"""
rxnorm_nodes.py · Build Drug nodes from openFDA + RxNorm
=========================================================
Step 1 of the KG build pipeline.
- Discovers top drugs via the openFDA label count API.
- Resolves each via RxNorm (reuses src.ingestion.rxnorm).
- Inserts Drug nodes into the KG SQLite database.
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import sqlite3
from typing import Dict, List

from src.kg.schema import upsert_node, rebuild_aliases

# ──────────────────────────────────────────────────────────────
#  SSL / HTTP helpers (reuse pattern from other ingestion modules)
# ──────────────────────────────────────────────────────────────

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

_LABEL_BASE = "https://api.fda.gov/drug/label.json"
_UA = "TruPharma/2.0"
_TIMEOUT = 20


def _api_get(url: str) -> dict:
    """GET JSON.  Returns {} on any failure."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError,
            json.JSONDecodeError, OSError):
        return {}


# ──────────────────────────────────────────────────────────────
#  Seed list: discover top drugs from openFDA label counts
# ──────────────────────────────────────────────────────────────

def _fetch_top_drug_names(max_drugs: int = 200) -> List[str]:
    """
    Use the openFDA label count endpoint to get the most common
    generic drug names across all drug labels.
    
    Returns a list of lowercase generic drug names.
    """
    # openFDA count endpoint returns up to 1000 results
    limit = min(max_drugs, 1000)
    url = (
        f"{_LABEL_BASE}?count=openfda.generic_name.exact&limit={limit}"
    )
    data = _api_get(url)
    results = data.get("results", [])
    
    names = []
    for r in results:
        term = r.get("term", "").strip()
        if term and len(term) > 1:
            names.append(term.lower())
    
    print(f"  [RxNorm] Discovered {len(names)} drug names from openFDA label counts")
    return names[:max_drugs]


# ──────────────────────────────────────────────────────────────
#  Main builder
# ──────────────────────────────────────────────────────────────

def build_drug_nodes(
    conn: sqlite3.Connection,
    max_drugs: int = 200,
    sleep_s: float = 0.15,
) -> List[Dict]:
    """
    Build Drug nodes from openFDA discovery + RxNorm resolution.
    
    Returns a list of drug dicts (for use by downstream builders):
        [{"node_id": ..., "generic_name": ..., "rxcui": ..., "brand_names": [...]}, ...]
    """
    # Import here to avoid circular import at module load
    from src.ingestion.rxnorm import resolve_drug_name

    seed_names = _fetch_top_drug_names(max_drugs)
    if not seed_names:
        print("  [RxNorm] WARNING: No drug names discovered. KG will be empty.")
        return []

    drugs: List[Dict] = []
    seen_ids: set = set()
    failed = 0

    for i, name in enumerate(seed_names):
        try:
            rxnorm = resolve_drug_name(name)
        except Exception as e:
            print(f"  [RxNorm] Error resolving '{name}': {e}")
            failed += 1
            continue

        rxcui = rxnorm.get("rxcui")
        generic = rxnorm.get("generic_name") or rxnorm.get("resolved_name") or name
        brands = rxnorm.get("brand_names", [])
        confidence = rxnorm.get("confidence", "none")

        # Skip unresolved drugs (no RxCUI and no confidence)
        if confidence == "none" and not rxcui:
            failed += 1
            continue

        # Node ID: prefer rxcui, fallback to lowercase generic name
        node_id = rxcui if rxcui else generic.lower()

        # Deduplicate
        if node_id in seen_ids:
            continue
        seen_ids.add(node_id)

        props = {
            "generic_name": generic,
            "brand_names": brands,
            "rxcui": rxcui,
            "confidence": confidence,
        }
        upsert_node(conn, node_id, "Drug", props)

        drugs.append({
            "node_id": node_id,
            "generic_name": generic,
            "rxcui": rxcui,
            "brand_names": brands,
        })

        # Progress every 50 drugs
        if (i + 1) % 50 == 0:
            print(f"  [RxNorm] Resolved {i + 1}/{len(seed_names)} drugs ({len(drugs)} unique nodes)")
            conn.commit()

        time.sleep(sleep_s)

    conn.commit()
    alias_count = rebuild_aliases(conn)
    print(f"  [RxNorm] Done. {len(drugs)} Drug nodes created, {failed} failed/skipped, {alias_count} aliases indexed.")
    return drugs
