"""
ndc_edges.py · Build NDC ingredient & product edges
=====================================================
Step 2 of the KG build pipeline.
- For each Drug node, fetches NDC product metadata.
- Creates Ingredient nodes and HAS_ACTIVE_INGREDIENT edges.
- Creates HAS_PRODUCT edges with product metadata.
"""

import sqlite3
import time
from typing import Dict, List

from src.kg.schema import upsert_node, upsert_edge


def build_ndc_edges(
    conn: sqlite3.Connection,
    drugs: List[Dict],
    sleep_s: float = 0.25,
) -> None:
    """
    For each drug in the list, fetch NDC metadata and create:
      - Ingredient nodes
      - HAS_ACTIVE_INGREDIENT edges (Drug → Ingredient)
      - HAS_PRODUCT edges (Drug → product info in edge props)
    """
    from src.ingestion.ndc import fetch_ndc_metadata

    ingredient_count = 0
    product_count = 0
    failed = 0

    for i, drug in enumerate(drugs):
        node_id = drug["node_id"]
        generic = drug["generic_name"]
        rxcui = drug.get("rxcui")
        brand = (drug.get("brand_names") or [None])[0]

        try:
            ndc_meta = fetch_ndc_metadata(
                generic_name=generic,
                brand_name=brand,
                rxcui=rxcui,
            )
        except Exception as e:
            print(f"  [NDC] Error for '{generic}': {e}")
            failed += 1
            time.sleep(sleep_s)
            continue

        if not ndc_meta or not ndc_meta.get("active_ingredients"):
            time.sleep(sleep_s)
            continue

        # ── Active ingredients ─────────────────────────────────
        for ing in ndc_meta.get("active_ingredients", []):
            ing_name = ing.get("name", "").strip()
            if not ing_name:
                continue

            ing_id = ing_name.lower()
            upsert_node(conn, ing_id, "Ingredient", {"name": ing_name})
            upsert_edge(conn, node_id, ing_id, "HAS_ACTIVE_INGREDIENT", {
                "source": "ndc",
                "strength": ing.get("strength", ""),
            })
            ingredient_count += 1

        # ── Product metadata (stored as edge props) ────────────
        product_ndcs = ndc_meta.get("product_ndcs", [])
        if product_ndcs:
            # Store a summary in one HAS_PRODUCT edge per drug
            product_props = {
                "source": "ndc",
                "dosage_forms": ndc_meta.get("dosage_forms", []),
                "routes": ndc_meta.get("routes", []),
                "manufacturer": ndc_meta.get("manufacturer"),
                "marketing_category": ndc_meta.get("marketing_category"),
                "product_ndcs": product_ndcs[:10],  # cap to avoid bloat
            }
            # Use a synthetic product node id
            prod_id = f"product:{node_id}"
            upsert_node(conn, prod_id, "Product", {
                "drug_id": node_id,
                "generic_name": generic,
            })
            upsert_edge(conn, node_id, prod_id, "HAS_PRODUCT", product_props)
            product_count += 1

        # Progress
        if (i + 1) % 50 == 0:
            print(f"  [NDC] Processed {i + 1}/{len(drugs)} drugs")
            conn.commit()

        time.sleep(sleep_s)

    conn.commit()
    print(f"  [NDC] Done. {ingredient_count} ingredient edges, {product_count} product edges, {failed} failed.")
