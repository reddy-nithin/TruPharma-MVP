"""
schema.py · TruPharma Knowledge Graph — SQLite Schema
======================================================
Creates the nodes/edges tables, indexes, and provides upsert helpers.

Tables:
    nodes(id TEXT PK, type TEXT, props TEXT)
    edges(src TEXT, dst TEXT, type TEXT, props TEXT, PK(src, dst, type))
"""

import json
import os
import sqlite3
from typing import Any, Dict, Optional


# ══════════════════════════════════════════════════════════════
#  Database initialization
# ══════════════════════════════════════════════════════════════

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id    TEXT PRIMARY KEY,
    type  TEXT NOT NULL,
    props TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    src   TEXT NOT NULL,
    dst   TEXT NOT NULL,
    type  TEXT NOT NULL,
    props TEXT DEFAULT '{}',
    PRIMARY KEY (src, dst, type)
);

CREATE TABLE IF NOT EXISTS drug_aliases (
    alias   TEXT PRIMARY KEY,
    node_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_edges_src_type ON edges(src, type);
CREATE INDEX IF NOT EXISTS idx_edges_dst_type ON edges(dst, type);
"""


def init_db(path: str = "data/kg/trupharma_kg.db") -> sqlite3.Connection:
    """
    Create (or open) the KG SQLite database and ensure schema exists.
    Returns an open connection ready for inserts.
    """
    # Ensure parent directory exists
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


# ══════════════════════════════════════════════════════════════
#  Upsert helpers
# ══════════════════════════════════════════════════════════════

def upsert_node(
    conn: sqlite3.Connection,
    node_id: str,
    node_type: str,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Insert or update a node.  On conflict (same id), update type and props.
    """
    props_json = json.dumps(props or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO nodes (id, type, props)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            type  = excluded.type,
            props = excluded.props
        """,
        (node_id, node_type, props_json),
    )


def upsert_edge(
    conn: sqlite3.Connection,
    src: str,
    dst: str,
    edge_type: str,
    props: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Insert or update an edge.  On conflict (same src+dst+type), update props.
    """
    props_json = json.dumps(props or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO edges (src, dst, type, props)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(src, dst, type) DO UPDATE SET
            props = excluded.props
        """,
        (src, dst, edge_type, props_json),
    )


# ══════════════════════════════════════════════════════════════
#  Quick helpers
# ══════════════════════════════════════════════════════════════

def count_nodes(conn: sqlite3.Connection, node_type: Optional[str] = None) -> int:
    """Count nodes, optionally filtered by type."""
    if node_type:
        row = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE type = ?", (node_type,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
    return row[0] if row else 0


def count_edges(conn: sqlite3.Connection, edge_type: Optional[str] = None) -> int:
    """Count edges, optionally filtered by type."""
    if edge_type:
        row = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE type = ?", (edge_type,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    return row[0] if row else 0


def get_all_drug_names(conn: sqlite3.Connection) -> set:
    """Return a set of all known drug names (generic + brands) for dictionary matching."""
    names = set()
    rows = conn.execute(
        "SELECT id, props FROM nodes WHERE type = 'Drug'"
    ).fetchall()
    for row in rows:
        names.add(row[0].lower())
        try:
            props = json.loads(row[1]) if row[1] else {}
            gn = props.get("generic_name", "")
            if gn:
                names.add(gn.lower())
            for bn in props.get("brand_names", []):
                if bn:
                    names.add(bn.lower())
        except (json.JSONDecodeError, TypeError):
            pass
    return names


def populate_aliases(conn: sqlite3.Connection) -> int:
    """
    Populate the drug_aliases lookup table from existing Drug nodes.
    Maps generic names, brand names, RxCUI, and node IDs to their node_id.
    Returns number of aliases inserted.
    """
    rows = conn.execute(
        "SELECT id, props FROM nodes WHERE type = 'Drug'"
    ).fetchall()

    count = 0
    for row in rows:
        node_id = row[0]
        try:
            props = json.loads(row[1]) if row[1] else {}
        except (json.JSONDecodeError, TypeError):
            props = {}

        # Collect all aliases for this node
        aliases = set()
        aliases.add(node_id.lower())

        gn = props.get("generic_name", "")
        if gn:
            aliases.add(gn.lower())

        rxcui = props.get("rxcui", "")
        if rxcui:
            aliases.add(str(rxcui))

        for bn in props.get("brand_names", []):
            if bn:
                aliases.add(bn.lower())

        for alias in aliases:
            conn.execute(
                "INSERT OR IGNORE INTO drug_aliases (alias, node_id) VALUES (?, ?)",
                (alias, node_id),
            )
            count += 1

    conn.commit()
    return count


def rebuild_aliases(conn: sqlite3.Connection) -> int:
    """Clear and repopulate the drug_aliases table."""
    conn.execute("DELETE FROM drug_aliases")
    conn.commit()
    return populate_aliases(conn)


def resolve_alias(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Fast O(1) lookup of a drug name/brand/rxcui to its node_id."""
    row = conn.execute(
        "SELECT node_id FROM drug_aliases WHERE alias = ?",
        (name.strip().lower(),)
    ).fetchone()
    return row[0] if row else None
