"""
label_edges.py · Build drug–drug interaction edges from openFDA labels
=======================================================================
Step 3 of the KG build pipeline.
- For each Drug node, fetches label records.
- Extracts interacting drugs from drug_interactions_table (structured)
  or drug_interactions (prose via Gemini API, with regex fallback).
- Creates INTERACTS_WITH edges with source: "label".
"""

import json
import os
import re
import sqlite3
import time
import warnings
from typing import Dict, List, Optional, Set

from src.kg.schema import upsert_edge, get_all_drug_names


# ──────────────────────────────────────────────────────────────
#  Gemini-based interaction extraction (primary for prose)
# ──────────────────────────────────────────────────────────────

_GEMINI_PROMPT_TEMPLATE = """You are a pharmacology expert. Extract ALL drug names mentioned as interacting with the target drug from the following drug interaction text.

Target drug: {drug_name}

Drug interaction text:
\"\"\"
{text}
\"\"\"

Return ONLY a JSON array of drug names (generic names preferred). Example: ["warfarin", "aspirin", "methotrexate"]
If no interacting drugs are found, return an empty array: []
Do NOT include the target drug itself. Do NOT include drug classes (like "NSAIDs") — only specific drug names."""


def _extract_via_gemini(
    text: str,
    drug_name: str,
    api_key: str,
) -> List[str]:
    """
    Use Gemini to extract interacting drug names from prose text.
    Returns a list of lowercased drug names.
    """
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.0-flash")

        prompt = _GEMINI_PROMPT_TEMPLATE.format(
            drug_name=drug_name,
            text=text[:3000],  # Cap text length to avoid token limits
        )
        resp = model.generate_content(prompt)
        if not resp or not resp.text:
            return []

        # Parse the JSON response
        raw = resp.text.strip()
        # Handle markdown code blocks
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        names = json.loads(raw)
        if isinstance(names, list):
            return [n.strip().lower() for n in names if isinstance(n, str) and n.strip()]
    except Exception as e:
        warnings.warn(f"Gemini extraction failed: {e}")

    return []


# ──────────────────────────────────────────────────────────────
#  Regex fallback (for when Gemini is unavailable)
# ──────────────────────────────────────────────────────────────

def _extract_drug_names_from_prose(
    text: str,
    known_drugs: Set[str],
) -> List[str]:
    """
    Fallback: extract drug names from prose using a dictionary
    of known drugs already in the KG.
    """
    if not text or not known_drugs:
        return []

    text_lower = text.lower()
    found = []

    # Sort by length descending so longer names match first
    for drug_name in sorted(known_drugs, key=len, reverse=True):
        if len(drug_name) < 3:
            continue
        pattern = r'\b' + re.escape(drug_name) + r'\b'
        if re.search(pattern, text_lower):
            found.append(drug_name)

    return found


def _extract_from_interaction_table(
    table_data: list,
    known_drugs: Set[str],
) -> List[str]:
    """
    Extract drug names from the structured drug_interactions_table field.
    """
    found = []
    if not table_data:
        return found

    for entry in table_data:
        if isinstance(entry, dict):
            for val in entry.values():
                if isinstance(val, str):
                    names = _extract_drug_names_from_prose(val, known_drugs)
                    found.extend(names)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, str):
                            names = _extract_drug_names_from_prose(item, known_drugs)
                            found.extend(names)
        elif isinstance(entry, str):
            names = _extract_drug_names_from_prose(entry, known_drugs)
            found.extend(names)

    return list(set(found))


# ──────────────────────────────────────────────────────────────
#  Resolve extracted name to a node ID
# ──────────────────────────────────────────────────────────────

def _resolve_to_node_id(name: str, conn: sqlite3.Connection) -> Optional[str]:
    """
    Given a drug name, find the matching node ID in the KG.
    """
    # Direct id match
    row = conn.execute(
        "SELECT id FROM nodes WHERE type='Drug' AND id = ?",
        (name.lower(),)
    ).fetchone()
    if row:
        return row[0]

    # Search in props
    rows = conn.execute(
        "SELECT id, props FROM nodes WHERE type='Drug'"
    ).fetchall()
    for r in rows:
        try:
            props = json.loads(r[1]) if r[1] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        gn = (props.get("generic_name") or "").lower()
        if gn == name.lower():
            return r[0]
        brands = [b.lower() for b in props.get("brand_names", []) if b]
        if name.lower() in brands:
            return r[0]

    return None


# ──────────────────────────────────────────────────────────────
#  Main builder
# ──────────────────────────────────────────────────────────────

def build_label_interaction_edges(
    conn: sqlite3.Connection,
    drugs: List[Dict],
    sleep_s: float = 0.25,
    gemini_api_key: Optional[str] = None,
) -> None:
    """
    For each drug, fetch openFDA label records and extract drug–drug
    interactions.  Creates INTERACTS_WITH edges with source: "label".

    Uses Gemini API for prose extraction when available (better recall),
    falls back to regex + drug dictionary matching.
    """
    from src.ingestion.openfda_client import fetch_openfda_records

    # Resolve Gemini API key
    api_key = gemini_api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    use_gemini = bool(api_key)

    # Build dictionary of all known drug names for regex fallback
    known_drugs = get_all_drug_names(conn)

    if use_gemini:
        print(f"  [Labels] Using Gemini API for interaction extraction (drug dict: {len(known_drugs)} names)")
    else:
        print(f"  [Labels] No Gemini API key found — using regex fallback (drug dict: {len(known_drugs)} names)")
        print(f"  [Labels] Set GEMINI_API_KEY env var for better extraction recall")

    edge_count = 0
    failed = 0
    gemini_calls = 0

    for i, drug in enumerate(drugs):
        node_id = drug["node_id"]
        generic = drug["generic_name"]
        rxcui = drug.get("rxcui")

        # Self-names to exclude
        self_names = {generic.lower()}
        for bn in drug.get("brand_names", []):
            if bn:
                self_names.add(bn.lower())
        if rxcui:
            self_names.add(rxcui)

        try:
            search = f'openfda.generic_name:"{generic}"'
            records = fetch_openfda_records(search=search, limit=3)

            if not records and rxcui:
                search = f'openfda.rxcui:"{rxcui}"'
                records = fetch_openfda_records(search=search, limit=3)
        except Exception as e:
            print(f"  [Labels] Error fetching labels for '{generic}': {e}")
            failed += 1
            time.sleep(sleep_s)
            continue

        if not records:
            time.sleep(sleep_s)
            continue

        # Extract interaction drug names from label fields
        interacting_names: Set[str] = set()

        for rec in records:
            # Prefer structured table (always use regex for this)
            table = rec.get("drug_interactions_table")
            if table and isinstance(table, list):
                names = _extract_from_interaction_table(table, known_drugs)
                interacting_names.update(names)

            # Prose text: use Gemini if available, else regex
            prose = rec.get("drug_interactions")
            if prose:
                if isinstance(prose, list):
                    prose = " ".join(prose)

                if use_gemini and len(prose) > 50:
                    gemini_names = _extract_via_gemini(prose, generic, api_key)
                    interacting_names.update(gemini_names)
                    gemini_calls += 1
                    # Also supplement with regex to catch KG-known names Gemini may miss
                    regex_names = _extract_drug_names_from_prose(prose, known_drugs)
                    interacting_names.update(regex_names)
                else:
                    names = _extract_drug_names_from_prose(prose, known_drugs)
                    interacting_names.update(names)

        # Remove self-references
        interacting_names -= self_names

        # Create edges
        for int_name in interacting_names:
            target_id = _resolve_to_node_id(int_name, conn)
            if target_id and target_id != node_id:
                upsert_edge(conn, node_id, target_id, "INTERACTS_WITH", {
                    "source": "label",
                })
                upsert_edge(conn, target_id, node_id, "INTERACTS_WITH", {
                    "source": "label",
                })
                edge_count += 1

        if (i + 1) % 50 == 0:
            print(f"  [Labels] Processed {i + 1}/{len(drugs)} drugs ({edge_count} interaction pairs, {gemini_calls} Gemini calls)")
            conn.commit()

        time.sleep(sleep_s)

    conn.commit()
    print(f"  [Labels] Done. {edge_count} interaction pairs, {gemini_calls} Gemini calls, {failed} failed.")
