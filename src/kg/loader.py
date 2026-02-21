"""
loader.py · TruPharma Knowledge Graph — Read-Only Query API
=============================================================
Loads the built KG SQLite database and provides structured query methods.
Used by the RAG pipeline / drug profile builder at runtime.

Graceful degradation: if the KG file doesn't exist, load_kg() returns None
and the app continues without KG data.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_KG_PATH = str(_PROJECT_ROOT / "data" / "kg" / "trupharma_kg.db")


class KnowledgeGraph:
    """
    Read-only wrapper around the KG SQLite database.
    Provides structured queries for drug identity, interactions,
    co-reported drugs, reactions, and ingredients.
    """

    def __init__(self, db_path: str):
        # Open in read-only mode via URI
        uri = f"file:{db_path}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ──────────────────────────────────────────────────────────
    #  Internal helpers
    # ──────────────────────────────────────────────────────────

    def _find_drug_id(self, name_or_rxcui: str) -> Optional[str]:
        """
        Resolve a name or RxCUI to the Drug node id.
        Uses the drug_aliases table for O(1) lookup, with fallback
        to linear scan for databases built before the alias table existed.
        """
        q = name_or_rxcui.strip()
        if not q:
            return None

        q_lower = q.lower()

        # Fast path: alias table lookup
        try:
            row = self._conn.execute(
                "SELECT node_id FROM drug_aliases WHERE alias = ?",
                (q_lower,)
            ).fetchone()
            if row:
                return row["node_id"]
        except Exception:
            pass  # Table may not exist in older DBs

        # Fallback: direct id match
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE type='Drug' AND id = ?",
            (q_lower,)
        ).fetchone()
        if row:
            return row["id"]

        # Also try the raw value (RxCUI might be numeric string)
        row = self._conn.execute(
            "SELECT id FROM nodes WHERE type='Drug' AND id = ?",
            (q,)
        ).fetchone()
        if row:
            return row["id"]

        # Slowest fallback: scan props
        rows = self._conn.execute(
            "SELECT id, props FROM nodes WHERE type='Drug'"
        ).fetchall()
        for r in rows:
            try:
                props = json.loads(r["props"]) if r["props"] else {}
            except (json.JSONDecodeError, TypeError):
                continue
            if props.get("rxcui") == q:
                return r["id"]
            gn = (props.get("generic_name") or "").lower()
            if gn == q_lower:
                return r["id"]
            brands = [b.lower() for b in props.get("brand_names", []) if b]
            if q_lower in brands:
                return r["id"]

        return None

    def _parse_props(self, props_str: Optional[str]) -> dict:
        if not props_str:
            return {}
        try:
            return json.loads(props_str)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _get_edges(
        self, node_id: str, edge_type: str, direction: str = "outgoing"
    ) -> List[dict]:
        """Get edges of a specific type, outgoing or incoming."""
        if direction == "outgoing":
            rows = self._conn.execute(
                "SELECT src, dst, props FROM edges WHERE src = ? AND type = ?",
                (node_id, edge_type),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT src, dst, props FROM edges WHERE dst = ? AND type = ?",
                (node_id, edge_type),
            ).fetchall()

        results = []
        for r in rows:
            props = self._parse_props(r["props"])
            results.append({
                "src": r["src"],
                "dst": r["dst"],
                **props,
            })
        return results

    def _get_node(self, node_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT id, type, props FROM nodes WHERE id = ?",
            (node_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "type": row["type"],
            **self._parse_props(row["props"]),
        }

    # ──────────────────────────────────────────────────────────
    #  Public query methods
    # ──────────────────────────────────────────────────────────

    def get_drug_identity(self, name_or_rxcui: str) -> Optional[dict]:
        """
        Look up a drug by name or RxCUI.
        Returns: {id, type, generic_name, brand_names, rxcui, ...} or None.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return None
        return self._get_node(drug_id)

    def get_interactions(self, name_or_rxcui: str) -> List[dict]:
        """
        Get drug–drug interactions (INTERACTS_WITH edges).
        Returns list of dicts with the interacting drug info.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return []

        edges = self._get_edges(drug_id, "INTERACTS_WITH", "outgoing")
        results = []
        for e in edges:
            target = self._get_node(e["dst"])
            results.append({
                "drug_id": e["dst"],
                "drug_name": (target or {}).get("generic_name", e["dst"]),
                "source": e.get("source", "unknown"),
                "description": e.get("description", ""),
            })
        return results

    def get_co_reported(self, name_or_rxcui: str) -> List[dict]:
        """
        Get drugs co-reported in FAERS adverse event reports.
        Returns list of dicts with co-reported drug info + report count.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return []

        edges = self._get_edges(drug_id, "CO_REPORTED_WITH", "outgoing")
        results = []
        for e in edges:
            target = self._get_node(e["dst"])
            results.append({
                "drug_id": e["dst"],
                "drug_name": (target or {}).get("generic_name", e["dst"]),
                "report_count": e.get("report_count", 0),
                "source": e.get("source", "faers"),
            })
        # Sort by report count descending
        results.sort(key=lambda x: x.get("report_count", 0), reverse=True)
        return results

    def get_drug_reactions(self, name_or_rxcui: str) -> List[dict]:
        """
        Get adverse reactions linked to this drug from FAERS.
        Returns list of dicts with reaction name + report count.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return []

        edges = self._get_edges(drug_id, "DRUG_CAUSES_REACTION", "outgoing")
        results = []
        for e in edges:
            target = self._get_node(e["dst"])
            results.append({
                "reaction": (target or {}).get("reactionmeddrapt", e["dst"]),
                "report_count": e.get("report_count", 0),
                "source": e.get("source", "faers"),
            })
        # Sort by report count descending
        results.sort(key=lambda x: x.get("report_count", 0), reverse=True)
        return results

    def get_ingredients(self, name_or_rxcui: str) -> List[dict]:
        """
        Get active ingredients for a drug from NDC.
        Returns list of dicts with ingredient name + strength.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return []

        edges = self._get_edges(drug_id, "HAS_ACTIVE_INGREDIENT", "outgoing")
        results = []
        for e in edges:
            target = self._get_node(e["dst"])
            results.append({
                "ingredient": (target or {}).get("name", e["dst"]),
                "strength": e.get("strength", ""),
                "source": e.get("source", "ndc"),
            })
        return results

    def get_label_reactions(self, name_or_rxcui: str) -> List[dict]:
        """
        Get adverse reactions that the official label warns about.
        Returns list of dicts with reaction name.
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return []

        edges = self._get_edges(drug_id, "LABEL_WARNS_REACTION", "outgoing")
        results = []
        for e in edges:
            target = self._get_node(e["dst"])
            results.append({
                "reaction": (target or {}).get("reactionmeddrapt", e["dst"]),
                "source": "label",
            })
        return results

    def get_disparity_analysis(self, name_or_rxcui: str) -> Optional[dict]:
        """
        Compare FAERS-reported reactions vs label-warned reactions.
        Returns:
          - confirmed_risks: in FAERS AND on label
          - emerging_signals: in FAERS but NOT on label (high risk)
          - unconfirmed_warnings: on label but rarely/not in FAERS
        """
        drug_id = self._find_drug_id(name_or_rxcui)
        if not drug_id:
            return None

        faers_reactions = self.get_drug_reactions(name_or_rxcui)
        label_reactions = self.get_label_reactions(name_or_rxcui)

        if not faers_reactions and not label_reactions:
            return None

        faers_terms = {r["reaction"].lower() for r in faers_reactions}
        label_terms = {r["reaction"].lower() for r in label_reactions}

        confirmed = faers_terms & label_terms
        emerging = faers_terms - label_terms
        unconfirmed = label_terms - faers_terms

        # Enrich with report counts from FAERS data
        faers_lookup = {r["reaction"].lower(): r for r in faers_reactions}

        return {
            "confirmed_risks": [
                {"reaction": t, "report_count": faers_lookup.get(t, {}).get("report_count", 0)}
                for t in sorted(confirmed)
            ],
            "emerging_signals": [
                {"reaction": t, "report_count": faers_lookup.get(t, {}).get("report_count", 0)}
                for t in sorted(emerging, key=lambda x: faers_lookup.get(x, {}).get("report_count", 0), reverse=True)
            ],
            "unconfirmed_warnings": [
                {"reaction": t} for t in sorted(unconfirmed)
            ],
            "disparity_score": len(emerging) / max(len(faers_terms), 1),
        }

    def get_summary(self, name_or_rxcui: str) -> Optional[dict]:
        """
        Get a comprehensive summary of all KG data for a drug.
        Returns a combined dict or None if drug not found.
        """
        identity = self.get_drug_identity(name_or_rxcui)
        if not identity:
            return None

        return {
            "identity": identity,
            "interactions": self.get_interactions(name_or_rxcui),
            "co_reported": self.get_co_reported(name_or_rxcui),
            "reactions": self.get_drug_reactions(name_or_rxcui),
            "label_reactions": self.get_label_reactions(name_or_rxcui),
            "ingredients": self.get_ingredients(name_or_rxcui),
            "disparity": self.get_disparity_analysis(name_or_rxcui),
        }


# ──────────────────────────────────────────────────────────────
#  Module-level loader (with graceful degradation)
# ──────────────────────────────────────────────────────────────

_KG_INSTANCE: Optional[KnowledgeGraph] = None
_KG_LOADED: bool = False


def load_kg(path: str = _DEFAULT_KG_PATH) -> Optional[KnowledgeGraph]:
    """
    Load the Knowledge Graph from a SQLite file.
    Returns None if the file doesn't exist (graceful degradation).
    The result is cached: subsequent calls return the same instance.
    """
    global _KG_INSTANCE, _KG_LOADED

    if _KG_LOADED:
        return _KG_INSTANCE

    _KG_LOADED = True

    if not os.path.exists(path):
        _KG_INSTANCE = None
        return None

    try:
        _KG_INSTANCE = KnowledgeGraph(path)
    except Exception:
        _KG_INSTANCE = None

    return _KG_INSTANCE
