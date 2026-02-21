# TruPharma Knowledge Graph — Data Layer Fixes Walkthrough

**Author:** TruPharma Engineering  
**Date:** February 2026  
**Branch:** `KG-feature`

---

## Overview

This document records the critical analysis and systematic fix of the TruPharma Knowledge Graph (KG) data layer. Starting from an almost empty 5-drug database, we applied 6 targeted fixes to produce a production-quality KG covering 184 seed drugs with 16,116 structured relationships.

---

## Architecture

The KG is stored as a single SQLite file (`data/kg/trupharma_kg.db`). It integrates four external data sources through a 5-step build pipeline:

```
openFDA Label Count API
        │
        ▼
    RxNorm API          ──► Drug Nodes (node_id = rxcui)
        │
        ├──► NDC API         ──► Ingredient Nodes + HAS_ACTIVE_INGREDIENT edges
        │                        Product Nodes + HAS_PRODUCT edges
        │
        ├──► openFDA Label   ──► INTERACTS_WITH edges (regex over 2,000-name dict)
        │                        LABEL_WARNS_REACTION edges (adverse_reactions field)
        │
        └──► FAERS Count API ──► Reaction Nodes + DRUG_CAUSES_REACTION edges
                                 CO_REPORTED_WITH edges (stub nodes for unknown drugs)
```

The `drug_aliases` table maps every known name, brand name, and RxCUI to its node ID for O(1) lookups.

---

## Before vs. After

The original database was built with `--max-drugs 5`, producing a toy dataset.

| Metric | Before (5 drugs) | After (200 drugs) |
|--------|:----------------:|:-----------------:|
| Drug nodes (seed) | 5 | **184** |
| Drug nodes (total, incl. stubs) | 5 | **942** |
| Ingredient nodes | 20 | **207** |
| Reaction nodes | 41 | **269** |
| INTERACTS_WITH edges | **0** | **746** |
| CO_REPORTED_WITH edges | **2** | **7,116** |
| DRUG_CAUSES_REACTION edges | 60 | **3,180** |
| LABEL_WARNS_REACTION edges | **0** | **4,435** |
| Aliases indexed | 0 | **3,822** |
| Database size | 0.4 MB | **3.9 MB** |
| Build time | ~2 min | ~17 min |

---

## Fixes Applied

### Fix 1 — Rebuild with 200-Drug Seed

**Root cause:** The original build used `--max-drugs 5`, seeding only zinc oxide, ethanol, acetaminophen, ibuprofen, and salicylic acid. Any query for a different drug returned empty KG data.

**Command run:**
```bash
python3 scripts/build_kg.py --max-drugs 200 --sleep 0.15
```

This uses the openFDA label count endpoint to discover the top 200 most-documented generic drug names, then resolves each via RxNorm.

---

### Fix 2 — Stub Nodes for Unknown Co-Reported Drugs

**File:** `src/kg/builders/faers_edges.py`

**Root cause:** FAERS returns up to 50 co-reported drugs per query, but the original code silently discarded any drug not already in the KG. With only 5 seeded drugs, 99%+ of co-reported data was lost.

**Fix:** When a co-reported drug name does not match an existing node, a lightweight "stub" Drug node is created with `"stub": true` in its properties. The `CO_REPORTED_WITH` edge is then stored as normal.

**Result:** CO_REPORTED_WITH edges increased from **2 → 7,116**.

---

### Fix 3 — LABEL_WARNS_REACTION Edges (Disparity Analysis)

**File:** `src/kg/builders/label_reaction_edges.py` *(new)*

**Problem:** The proposal's core feature — disparity analysis (comparing label warnings vs. real-world FAERS signals) — was blocked because the KG had no structured link between FDA label adverse reactions and FAERS reaction nodes.

**Fix:** A new Step 5 in the build pipeline extracts `adverse_reactions`, `warnings`, `boxed_warning`, and `contraindications` text from label records. It matches these against existing FAERS Reaction nodes using word-boundary regex and creates `LABEL_WARNS_REACTION` edges.

**New query methods added to `loader.py`:**

| Method | Returns |
|--------|---------|
| `get_label_reactions(drug)` | Reactions explicitly warned on the label |
| `get_disparity_analysis(drug)` | `confirmed_risks`, `emerging_signals`, `unconfirmed_warnings`, `disparity_score` |

**Disparity categories:**
- **Confirmed risks:** Reaction appears in FAERS **and** on the label
- **Emerging signals:** Reaction appears in FAERS but **not** on the label (highest clinical interest)
- **Unconfirmed warnings:** On the label but not yet appearing in FAERS reports

**Result:** 4,435 LABEL_WARNS_REACTION edges across 164/184 drugs.

---

### Fix 4 — Name-Alias Lookup Table

**File:** `src/kg/schema.py`, `src/kg/loader.py`

**Root cause:** Every KG lookup did a full linear scan of all Drug nodes, parsing JSON properties for each. This was O(n × JSON parse) per query and fragile — it failed on minor name variations.

**Fix:** Added a `drug_aliases` SQLite table:

```sql
CREATE TABLE IF NOT EXISTS drug_aliases (
    alias   TEXT PRIMARY KEY,
    node_id TEXT NOT NULL
);
```

Populated at build time with every generic name, brand name, and RxCUI for each Drug node (3,822 aliases total). The `_find_drug_id()` method now does a single `SELECT node_id FROM drug_aliases WHERE alias = ?` lookup.

**New schema helpers:**
- `populate_aliases(conn)` — fills alias table from Drug nodes
- `rebuild_aliases(conn)` — clears + repopulates (called at end of every build)
- `resolve_alias(conn, name)` — O(1) name-to-node-ID resolution

---

### Fix 5 — Increased FAERS Co-Reported Limit

**File:** `src/kg/builders/faers_edges.py`

Changed `max_co_reported` default from **15 → 50**. Each FAERS query now returns 3.3× more co-reported drug data, dramatically increasing the density of the CO_REPORTED_WITH edge set.

---

### Bonus Fix — KG Lookup Strategy in engine.py

**File:** `src/rag/engine.py`

**Problem found during verification:** The RAG engine called the live RxNorm API on every query to resolve a drug name before looking it up in the KG. This made lookups 3–10 seconds slower and caused failures when the live RxNorm response returned a different RxCUI than the one stored in the KG during the build.

**Fix:** Modified to try the raw extracted drug name directly against the alias table first (O(1), no network call). Falls back to live RxNorm only if the direct lookup fails.

```python
# Strategy 1: alias table (fast, reliable)
if kg.get_drug_identity(drug_name):
    lookup = drug_name
else:
    # Strategy 2: live RxNorm (fallback only)
    rxnorm = resolve_drug_name(drug_name)
    lookup = rxnorm.get("rxcui") or drug_name
```

---

## Verification Results

Tested in the Streamlit app after rebuild with all fixes applied:

| Drug | Ingredients | Interactions | Co-Reported | Reactions |
|------|:-----------:|:------------:|:-----------:|:---------:|
| **Metformin** | ✅ (2) | ✅ (topiramate, cephalexin, sulfamethoxazole) | ✅ (50+) | ✅ (nausea 29K, glucose increase 27K, diarrhea 27K) |
| **Lisinopril** | ✅ (1) | ✅ (spironolactone, losartan, propranolol, HCTZ) | ✅ (50+, incl. metformin 34,864) | ✅ (fatigue 19,923, nausea 18,639) |
| **Atorvastatin** | ✅ (1) | ✅ (estradiol, fluconazole, verapamil) | ✅ (50+) | ✅ (fatigue 13,958, myalgia 9,660) |

---

## How to Rebuild the KG

```bash
# From project root — full rebuild (~17 min):
python3 scripts/build_kg.py --max-drugs 200 --sleep 0.15

# With Gemini for higher-recall interaction extraction:
GEMINI_API_KEY=your_key python3 scripts/build_kg.py --max-drugs 200

# Partial rebuild (skip slow steps):
python3 scripts/build_kg.py --max-drugs 200 --skip-faers
python3 scripts/build_kg.py --max-drugs 200 --skip-label-reactions
```

---

## KG Schema Reference

| Node Type | Description | Key Properties |
|-----------|-------------|----------------|
| `Drug` | A pharmaceutical compound | `generic_name`, `rxcui`, `brand_names`, `stub` |
| `Ingredient` | An active ingredient | `name` |
| `Reaction` | A MedDRA adverse reaction term | `reactionmeddrapt` |
| `Product` | An NDC drug product | `drug_id`, `generic_name` |

| Edge Type | Source → Target | Data Source |
|-----------|-----------------|-------------|
| `HAS_ACTIVE_INGREDIENT` | Drug → Ingredient | NDC |
| `HAS_PRODUCT` | Drug → Product | NDC |
| `INTERACTS_WITH` | Drug ↔ Drug | openFDA Labels |
| `CO_REPORTED_WITH` | Drug ↔ Drug | FAERS |
| `DRUG_CAUSES_REACTION` | Drug → Reaction | FAERS |
| `LABEL_WARNS_REACTION` | Drug → Reaction | openFDA Labels |

---

## Remaining Work (Fix #6 — DrugBank)

DrugBank's curated drug-drug interaction dataset would provide ~2,700 high-quality DDIs for the ~800 drugs in the public open set, supplementing the 746 label-extracted interactions. The dataset requires a free academic license download. Implementation would follow the same builder pattern as `label_edges.py`.

Alternative free DDI sources: DailyMed (NLM), ChEMBL (EMBL-EBI), KEGG Drug, SIDER.
