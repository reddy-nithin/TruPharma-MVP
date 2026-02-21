"""
faers_edges.py · Build FAERS co-reported & drug–reaction edges
================================================================
Step 4 of the KG build pipeline.
- For each Drug node, queries FAERS count endpoints.
- Creates CO_REPORTED_WITH edges (drug–drug from same adverse event reports).
- Creates Reaction nodes + DRUG_CAUSES_REACTION edges.
"""

import json
import ssl
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from src.kg.schema import upsert_node, upsert_edge, resolve_alias


# ──────────────────────────────────────────────────────────────
#  SSL / HTTP (reuse pattern)
# ──────────────────────────────────────────────────────────────

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

_FAERS_BASE = "https://api.fda.gov/drug/event.json"
_UA = "TruPharma/2.0"
_TIMEOUT = 15


def _api_get(url: str) -> dict:
    """GET JSON. Returns {} on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, OSError):
        return {}


# ──────────────────────────────────────────────────────────────
#  FAERS count queries
# ──────────────────────────────────────────────────────────────

def _build_search(generic_name: str, rxcui: Optional[str] = None) -> str:
    """Build FAERS search clause."""
    name = generic_name.strip().lower()
    clauses = [f'patient.drug.openfda.generic_name:"{name}"']
    if rxcui:
        clauses.append(f'patient.drug.openfda.rxcui:"{rxcui}"')
    return "+OR+".join(clauses)


def _fetch_co_reported_drugs(search: str, limit: int = 20) -> List[dict]:
    """
    Get drugs most frequently co-reported with the target drug in FAERS.
    Uses count on patient.drug.medicinalproduct.exact.
    """
    url = (
        f"{_FAERS_BASE}?search={search}"
        f"&count=patient.drug.medicinalproduct.exact&limit={limit}"
    )
    data = _api_get(url)
    return [
        {"term": r.get("term", ""), "count": r.get("count", 0)}
        for r in data.get("results", [])
    ]


def _fetch_top_reactions(search: str, limit: int = 25) -> List[dict]:
    """
    Get top adverse reactions reported for the target drug.
    Uses count on patient.reaction.reactionmeddrapt.exact.
    """
    url = (
        f"{_FAERS_BASE}?search={search}"
        f"&count=patient.reaction.reactionmeddrapt.exact&limit={limit}"
    )
    data = _api_get(url)
    return [
        {"term": r.get("term", ""), "count": r.get("count", 0)}
        for r in data.get("results", [])
    ]


# ──────────────────────────────────────────────────────────────
#  Resolve co-reported drug name to node ID
# ──────────────────────────────────────────────────────────────

def _find_drug_node(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """
    Try to match a co-reported drug name to an existing Drug node.
    """
    name_lower = name.strip().lower()
    if not name_lower:
        return None

    # Direct id match
    row = conn.execute(
        "SELECT id FROM nodes WHERE type='Drug' AND id = ?",
        (name_lower,)
    ).fetchone()
    if row:
        return row[0]

    # Search by generic_name or brand_names in props
    rows = conn.execute(
        "SELECT id, props FROM nodes WHERE type='Drug'"
    ).fetchall()
    for r in rows:
        try:
            props = json.loads(r[1]) if r[1] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        gn = (props.get("generic_name") or "").lower()
        if gn == name_lower:
            return r[0]
        brands = [b.lower() for b in props.get("brand_names", []) if b]
        if name_lower in brands:
            return r[0]

    return None


# ──────────────────────────────────────────────────────────────
#  Main builder
# ──────────────────────────────────────────────────────────────

def build_faers_edges(
    conn: sqlite3.Connection,
    drugs: List[Dict],
    sleep_s: float = 0.3,
    max_co_reported: int = 50,
    max_reactions: int = 20,
) -> None:
    """
    For each drug, query FAERS count endpoints and create:
      - CO_REPORTED_WITH edges (drug pairs from same FAERS reports)
      - Reaction nodes + DRUG_CAUSES_REACTION edges
    """
    co_reported_count = 0
    reaction_edge_count = 0
    reaction_node_count = 0
    failed = 0

    for i, drug in enumerate(drugs):
        node_id = drug["node_id"]
        generic = drug["generic_name"]
        rxcui = drug.get("rxcui")

        search = _build_search(generic, rxcui)

        # ── Co-reported drugs ──────────────────────────────────
        try:
            co_drugs = _fetch_co_reported_drugs(search, limit=max_co_reported)
        except Exception:
            co_drugs = []
            failed += 1

        for cd in co_drugs:
            term = cd.get("term", "").strip()
            count = cd.get("count", 0)
            if not term:
                continue

            # Skip if it's the same drug
            if term.lower() == generic.lower():
                continue

            target_id = _find_drug_node(conn, term)

            # If not found, create a stub Drug node
            if not target_id:
                stub_id = term.strip().lower()
                if stub_id == node_id or stub_id == generic.lower():
                    continue
                upsert_node(conn, stub_id, "Drug", {
                    "generic_name": term.strip(),
                    "stub": True,
                })
                target_id = stub_id

            if target_id and target_id != node_id:
                upsert_edge(conn, node_id, target_id, "CO_REPORTED_WITH", {
                    "source": "faers",
                    "report_count": count,
                })
                co_reported_count += 1

        time.sleep(sleep_s)

        # ── Drug → Reaction edges ──────────────────────────────
        try:
            reactions = _fetch_top_reactions(search, limit=max_reactions)
        except Exception:
            reactions = []
            failed += 1

        for rx in reactions:
            term = rx.get("term", "").strip()
            count = rx.get("count", 0)
            if not term:
                continue

            reaction_id = f"reaction:{term.lower()}"

            # Check if this Reaction node is new
            existing = conn.execute(
                "SELECT id FROM nodes WHERE id = ?", (reaction_id,)
            ).fetchone()
            if not existing:
                upsert_node(conn, reaction_id, "Reaction", {
                    "reactionmeddrapt": term,
                })
                reaction_node_count += 1

            upsert_edge(conn, node_id, reaction_id, "DRUG_CAUSES_REACTION", {
                "source": "faers",
                "report_count": count,
            })
            reaction_edge_count += 1

        time.sleep(sleep_s)

        if (i + 1) % 50 == 0:
            print(
                f"  [FAERS] Processed {i + 1}/{len(drugs)} drugs "
                f"({co_reported_count} co-reported, {reaction_edge_count} reaction edges)"
            )
            conn.commit()

    conn.commit()
    print(
        f"  [FAERS] Done. {co_reported_count} co-reported edges, "
        f"{reaction_node_count} Reaction nodes, {reaction_edge_count} reaction edges, "
        f"{failed} failed."
    )
