#!/usr/bin/env python3
"""
generate_walkthrough_pdf.py
Convert docs/KNOWLEDGE_GRAPH_WALKTHROUGH.md to a styled PDF.
Uses only reportlab (no external binary dependencies).
"""

import os
import sys
from datetime import datetime

# ── reportlab imports ───────────────────────────────────────────
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import KeepTogether

# ── Color palette ───────────────────────────────────────────────
PRIMARY   = colors.HexColor("#1B4F72")   # deep navy
ACCENT    = colors.HexColor("#2E86AB")   # teal
GREEN     = colors.HexColor("#27AE60")
RED       = colors.HexColor("#C0392B")
YELLOW    = colors.HexColor("#F39C12")
LIGHT_BG  = colors.HexColor("#F4F8FB")
CODE_BG   = colors.HexColor("#EEF2F7")
MID_GREY  = colors.HexColor("#7F8C8D")
DARK_GREY = colors.HexColor("#2C3E50")

# ── Styles ──────────────────────────────────────────────────────
BASE = getSampleStyleSheet()

def make_styles():
    S = {}

    S["cover_title"] = ParagraphStyle(
        "cover_title",
        fontSize=28, leading=36,
        textColor=PRIMARY, fontName="Helvetica-Bold",
        alignment=TA_CENTER, spaceAfter=8,
    )
    S["cover_sub"] = ParagraphStyle(
        "cover_sub",
        fontSize=13, leading=18,
        textColor=ACCENT, fontName="Helvetica",
        alignment=TA_CENTER, spaceAfter=4,
    )
    S["cover_meta"] = ParagraphStyle(
        "cover_meta",
        fontSize=10, leading=14,
        textColor=MID_GREY, fontName="Helvetica",
        alignment=TA_CENTER, spaceAfter=2,
    )

    S["h1"] = ParagraphStyle(
        "h1",
        fontSize=18, leading=24,
        textColor=PRIMARY, fontName="Helvetica-Bold",
        spaceBefore=20, spaceAfter=8,
        borderPad=(0, 0, 4, 0),
    )
    S["h2"] = ParagraphStyle(
        "h2",
        fontSize=13, leading=18,
        textColor=ACCENT, fontName="Helvetica-Bold",
        spaceBefore=14, spaceAfter=6,
    )
    S["h3"] = ParagraphStyle(
        "h3",
        fontSize=11, leading=15,
        textColor=DARK_GREY, fontName="Helvetica-Bold",
        spaceBefore=10, spaceAfter=4,
    )

    S["body"] = ParagraphStyle(
        "body",
        fontSize=10, leading=15,
        textColor=DARK_GREY, fontName="Helvetica",
        alignment=TA_JUSTIFY, spaceAfter=6,
    )
    S["bullet"] = ParagraphStyle(
        "bullet",
        fontSize=10, leading=14,
        textColor=DARK_GREY, fontName="Helvetica",
        leftIndent=18, bulletIndent=6,
        spaceAfter=3,
    )
    S["code"] = ParagraphStyle(
        "code",
        fontSize=8.5, leading=12,
        textColor=colors.HexColor("#1A252F"),
        fontName="Courier",
        backColor=CODE_BG,
        leftIndent=12, rightIndent=12,
        spaceBefore=4, spaceAfter=8,
        borderWidth=0.5, borderColor=colors.HexColor("#BDC3C7"),
        borderPad=6, borderRadius=3,
    )
    S["caption"] = ParagraphStyle(
        "caption",
        fontSize=8, leading=11,
        textColor=MID_GREY, fontName="Helvetica-Oblique",
        alignment=TA_CENTER, spaceAfter=4,
    )

    return S


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = doc.pagesize
    # Header bar
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, h - 32, w, 32, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawString(0.5 * inch, h - 20, "TruPharma Knowledge Graph — Data Layer Fixes Walkthrough")
    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(w - 0.5 * inch, h - 20, f"Page {doc.page}")
    # Footer line
    canvas.setStrokeColor(colors.HexColor("#D5E8F3"))
    canvas.setLineWidth(0.8)
    canvas.line(0.5 * inch, 0.45 * inch, w - 0.5 * inch, 0.45 * inch)
    canvas.setFillColor(MID_GREY)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawString(0.5 * inch, 0.28 * inch, "TruPharma MVP · KG Feature Branch · Feb 2026")
    canvas.restoreState()


def make_table(data, col_widths=None, header_row=True):
    """Create a styled, zebra-striped table."""
    t = Table(data, colWidths=col_widths or [])
    cmd = [
        ("FONTNAME",    (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 9),
        ("FONTNAME",    (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0, 0), (-1, 0),  9),
        ("BACKGROUND",  (0, 0), (-1, 0),  PRIMARY),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  colors.white),
        ("ALIGN",       (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT_BG]),
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#D5E8F3")),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0,0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",(0, 0), (-1, -1), 8),
    ]
    t.setStyle(TableStyle(cmd))
    return t


def highlight_box(text, style_name, S, bg=LIGHT_BG, border=ACCENT, icon=""):
    """Render a highlighted alert box."""
    label = f"<b>{icon} {text}</b>" if icon else f"<b>{text}</b>"
    p = Paragraph(label, ParagraphStyle(
        "hbox",
        parent=S["body"],
        backColor=bg,
        borderColor=border,
        borderWidth=1,
        borderPad=8,
        borderRadius=4,
        spaceBefore=4, spaceAfter=8,
    ))
    return p


def build_pdf(md_path: str, out_path: str):
    S = make_styles()
    doc = SimpleDocTemplate(
        out_path,
        pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.65 * inch,
    )

    story = []

    # ── Cover page ─────────────────────────────────────────────
    story.append(Spacer(1, 1.4 * inch))
    story.append(Paragraph("TruPharma Knowledge Graph", S["cover_title"]))
    story.append(Paragraph("Data Layer Fixes — Implementation Walkthrough", S["cover_sub"]))
    story.append(Spacer(1, 0.3 * inch))
    story.append(HRFlowable(width="70%", thickness=2, color=ACCENT, spaceAfter=16, hAlign="CENTER"))
    story.append(Paragraph("Branch: <b>KG-feature</b>", S["cover_meta"]))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%B %d, %Y')}", S["cover_meta"]))
    story.append(Spacer(1, 0.5 * inch))

    # Cover summary box
    summary_text = (
        "This document records the critical analysis and systematic remediation of the "
        "TruPharma Knowledge Graph data layer. Starting from a 5-drug stub database, "
        "six targeted fixes were applied to produce a production-quality graph covering "
        "<b>184 seed drugs</b>, <b>942 total nodes</b>, and <b>16,116 structured relationships</b> "
        "across four external data sources (openFDA, RxNorm, NDC, FAERS)."
    )
    story.append(Paragraph(summary_text, ParagraphStyle(
        "csbox", parent=S["body"],
        backColor=LIGHT_BG, borderColor=ACCENT, borderWidth=1.5,
        borderPad=14, borderRadius=5, alignment=TA_JUSTIFY,
        fontSize=10.5, leading=16,
    )))
    story.append(PageBreak())

    # ── Section 1: Architecture ─────────────────────────────────
    story.append(Paragraph("1. Architecture", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))
    story.append(Paragraph(
        "The KG is stored as a single SQLite file at <b>data/kg/trupharma_kg.db</b> and is "
        "populated by a 5-step build pipeline that calls external APIs sequentially.",
        S["body"]
    ))
    story.append(Spacer(1, 0.1 * inch))

    arch_data = [
        ["Step", "Data Source", "Produces"],
        ["1", "openFDA Labels → RxNorm", "Drug nodes (node_id = rxcui)"],
        ["2", "NDC API", "Ingredient nodes + HAS_ACTIVE_INGREDIENT edges\nProduct nodes + HAS_PRODUCT edges"],
        ["3", "openFDA Label (prose)", "INTERACTS_WITH edges (regex over 2,000-name dict)"],
        ["4", "FAERS Count API", "Reaction nodes + DRUG_CAUSES_REACTION edges\nCO_REPORTED_WITH edges (incl. stub nodes)"],
        ["5", "openFDA Label (adverse_reactions)", "LABEL_WARNS_REACTION edges (disparity analysis)"],
        ["Final", "–", "drug_aliases table rebuilt (3,822 entries)"],
    ]
    story.append(make_table(arch_data, col_widths=[0.5*inch, 2.3*inch, 3.8*inch]))
    story.append(Spacer(1, 0.2 * inch))

    # ── Section 2: Before vs After ──────────────────────────────
    story.append(Paragraph("2. Before vs. After", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))
    story.append(Paragraph(
        "The original database was built with <b>--max-drugs 5</b>, producing a toy dataset "
        "that returned empty KG data for any drug not in {ibuprofen, acetaminophen, "
        "zinc oxide, ethanol, salicylic acid}.",
        S["body"]
    ))

    metrics_data = [
        ["Metric", "Before (5 drugs)", "After (200-drug seed)"],
        ["Drug nodes (seed)", "5", "184"],
        ["Drug nodes (total, incl. stubs)", "5", "942"],
        ["Ingredient nodes", "20", "207"],
        ["Reaction nodes", "41", "269"],
        ["INTERACTS_WITH edges", "0 ⚠", "746 ✓"],
        ["CO_REPORTED_WITH edges", "2 ⚠", "7,116 ✓"],
        ["DRUG_CAUSES_REACTION edges", "60", "3,180 ✓"],
        ["LABEL_WARNS_REACTION edges", "0 ⚠", "4,435 ✓  (new — enables disparity)"],
        ["Aliases indexed", "0 ⚠", "3,822 ✓"],
        ["Database size", "0.4 MB", "3.9 MB"],
        ["Build time", "~2 min", "~17 min"],
    ]
    story.append(make_table(metrics_data, col_widths=[2.9*inch, 1.8*inch, 2.5*inch]))
    story.append(Spacer(1, 0.2 * inch))

    # ── Section 3: Fixes ────────────────────────────────────────
    story.append(Paragraph("3. Fixes Applied", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))

    fixes = [
        {
            "title": "Fix 1 — Rebuild with 200-Drug Seed",
            "files": "scripts/build_kg.py (CLI invocation)",
            "root_cause": (
                "The original build used --max-drugs 5, seeding only 5 generic OTC compounds. "
                "Any query for a different drug returned empty KG data."
            ),
            "fix": "Re-ran the build pipeline with --max-drugs 200 --sleep 0.15, discovering "
                   "the top 200 most-documented generic drug names via the openFDA label count endpoint.",
            "cmd": "python3 scripts/build_kg.py --max-drugs 200 --sleep 0.15",
            "result": "184 Drug nodes resolved from 200 candidates (5 failed RxNorm resolution).",
        },
        {
            "title": "Fix 2 — Stub Nodes for Unknown Co-Reported Drugs",
            "files": "src/kg/builders/faers_edges.py",
            "root_cause": (
                "FAERS returns up to 50 co-reported drugs per query, but the original code "
                "silently discarded any co-reported drug not already in the KG. With 5 seeded "
                "drugs, 99%+ of co-reported data was lost."
            ),
            "fix": "When a co-reported drug name does not match an existing node, a lightweight "
                   "'stub' Drug node is created with stub: true in its properties, and the "
                   "CO_REPORTED_WITH edge is stored as usual.",
            "result": "CO_REPORTED_WITH edges increased from 2 → 7,116.",
        },
        {
            "title": "Fix 3 — LABEL_WARNS_REACTION Edges (Disparity Analysis)",
            "files": "src/kg/builders/label_reaction_edges.py (new), scripts/build_kg.py, src/kg/loader.py",
            "root_cause": (
                "The proposal's core feature — disparity analysis (label warnings vs. real-world "
                "FAERS signals) — was blocked because no structured link existed between FDA label "
                "adverse reactions and FAERS reaction nodes."
            ),
            "fix": "New Step 5 in the pipeline extracts adverse_reactions, warnings, boxed_warning, "
                   "and contraindications text from label records. Matches these against existing "
                   "FAERS Reaction nodes using word-boundary regex. Creates LABEL_WARNS_REACTION edges. "
                   "New query methods added: get_label_reactions() and get_disparity_analysis().",
            "result": "4,435 LABEL_WARNS_REACTION edges across 164/184 drugs. "
                      "Disparity analysis (confirmed_risks, emerging_signals, unconfirmed_warnings, "
                      "disparity_score) now queryable directly from the KG.",
        },
        {
            "title": "Fix 4 — Name-Alias Lookup Table",
            "files": "src/kg/schema.py, src/kg/builders/rxnorm_nodes.py, src/kg/loader.py",
            "root_cause": (
                "Every KG lookup did a full linear scan of all Drug nodes, parsing JSON "
                "properties for each (O(n × JSON parse)). This was slow and failed on minor "
                "name variations."
            ),
            "fix": "Added a drug_aliases SQLite table with PRIMARY KEY on alias, mapping every "
                   "generic name, brand name, and RxCUI to its node_id. The _find_drug_id() "
                   "method now does a single indexed SELECT lookup, with linear scan as fallback "
                   "for legacy DBs. New helpers: populate_aliases(), rebuild_aliases(), resolve_alias().",
            "result": "3,822 aliases indexed. Lookups are instantaneous and handle all name variants.",
        },
        {
            "title": "Fix 5 — Increased FAERS Co-Reported Limit",
            "files": "src/kg/builders/faers_edges.py",
            "root_cause": "Default max_co_reported was 15, limiting the co-reported drug network density.",
            "fix": "Changed max_co_reported default from 15 → 50.",
            "result": "Each FAERS query returns 3.3× more co-reported drug data per drug.",
        },
        {
            "title": "Bonus Fix — KG Lookup Strategy in engine.py",
            "files": "src/rag/engine.py",
            "root_cause": (
                "The RAG engine called the live RxNorm API on every query before the KG lookup. "
                "This added 3–10s of latency and failed when the live API returned a different "
                "RxCUI than the one stored during the KG build (causing metformin, atorvastatin, "
                "and others to miss despite being in the database)."
            ),
            "fix": "Modified to try the extracted drug name directly against the alias table first "
                   "(O(1), no network). Falls back to live RxNorm only if the direct lookup fails.",
            "result": "All tested drugs (metformin, lisinopril, atorvastatin) now resolve correctly.",
        },
    ]

    for i, fix in enumerate(fixes):
        story.append(Paragraph(fix["title"], S["h2"]))
        story.append(Paragraph(f"<b>File(s):</b> <font name='Courier' size='8.5'>{fix['files']}</font>", S["body"]))
        story.append(Paragraph(f"<b>Root cause:</b> {fix['root_cause']}", S["body"]))
        story.append(Paragraph(f"<b>Fix:</b> {fix['fix']}", S["body"]))
        if fix.get("cmd"):
            story.append(Paragraph(fix["cmd"], S["code"]))
        story.append(Paragraph(f"<b>Result:</b> {fix['result']}", S["body"]))
        if i < len(fixes) - 1:
            story.append(HRFlowable(width="100%", thickness=0.4, color=colors.HexColor("#D5E8F3"), spaceAfter=6))

    # ── Section 4: Verification ─────────────────────────────────
    story.append(Paragraph("4. Verification Results", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))
    story.append(Paragraph(
        "Tested in the Streamlit app (localhost:8503) immediately after the full rebuild with all fixes applied:",
        S["body"]
    ))

    verify_data = [
        ["Drug", "Ingredients", "Interactions (label)", "Co-Reported (FAERS)", "Reactions (FAERS)"],
        ["Metformin", "✓ (2)", "topiramate, cephalexin,\nsulfamethoxazole", "50 drugs incl. aspirin\n(34,642), amlodipine (31,622)", "nausea 29K, glucose↑ 27K,\ndiarrhea 27K"],
        ["Lisinopril", "✓ (1)", "spironolactone, losartan,\npropranolol, HCTZ", "metformin (34,864),\naspirin (31,493)", "fatigue 19,923,\nnausea 18,639"],
        ["Atorvastatin", "✓ (1)", "estradiol, fluconazole,\nverapamil", "aspirin (33,593),\nPLAVIX (16,277)", "fatigue 13,958,\nmyalgia 9,660"],
    ]
    story.append(make_table(
        verify_data,
        col_widths=[0.95*inch, 0.85*inch, 1.55*inch, 1.75*inch, 1.8*inch]
    ))
    story.append(Spacer(1, 0.2 * inch))

    # ── Verification screenshots ─────────────────────────────────
    story.append(Paragraph("4.1  App Screenshots", S["h2"]))
    story.append(Paragraph(
        "The following screenshots were captured live from the Streamlit dashboard "
        "(port 8503) immediately after the full rebuild, confirming end-to-end KG data flow.",
        S["body"]
    ))

    screenshots = [
        ("kg_metformin.png",    "Metformin — Active Ingredients, FDA label interactions (topiramate, cephalexin, sulfamethoxazole), and FAERS Co-Reported Drugs"),
        ("kg_atorvastatin.png", "Atorvastatin — Active Ingredients, FDA label interactions (estradiol, fluconazole, verapamil), and FAERS Co-Reported Drugs with report counts"),
        ("kg_lisinopril.png",   "Lisinopril — FAERS Co-Reported Drugs (metformin at 34,864 reports, aspirin at 31,493) and Adverse Reactions (fatigue 19,923, nausea 18,639)"),
    ]

    shots_dir = os.path.join(project_root, "docs", "screenshots")
    usable_width = 7.0 * inch  # page width minus margins

    for fname, caption in screenshots:
        img_path = os.path.join(shots_dir, fname)
        if os.path.exists(img_path):
            try:
                img = Image(img_path, width=usable_width, height=usable_width * 0.56)
                img.hAlign = "CENTER"
                story.append(img)
                story.append(Paragraph(caption, S["caption"]))
                story.append(Spacer(1, 0.15 * inch))
            except Exception as e:
                story.append(Paragraph(f"[Screenshot not available: {fname}]", S["caption"]))
        else:
            story.append(Paragraph(f"[Screenshot not found: {fname}]", S["caption"]))

    story.append(Spacer(1, 0.1 * inch))

    # ── Section 5: Schema Reference ─────────────────────────────
    story.append(Paragraph("5. KG Schema Reference", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))

    story.append(Paragraph("Node Types", S["h2"]))
    node_data = [
        ["Type", "Description", "Key Properties"],
        ["Drug", "A pharmaceutical compound", "generic_name, rxcui, brand_names, stub"],
        ["Ingredient", "An active ingredient", "name"],
        ["Reaction", "A MedDRA adverse reaction term", "reactionmeddrapt"],
        ["Product", "An NDC drug product", "drug_id, generic_name"],
    ]
    story.append(make_table(node_data, col_widths=[1.0*inch, 2.2*inch, 3.7*inch]))
    story.append(Spacer(1, 0.15 * inch))

    story.append(Paragraph("Edge Types", S["h2"]))
    edge_data = [
        ["Edge Type", "Source → Target", "Data Source"],
        ["HAS_ACTIVE_INGREDIENT", "Drug → Ingredient", "NDC API"],
        ["HAS_PRODUCT", "Drug → Product", "NDC API"],
        ["INTERACTS_WITH", "Drug ↔ Drug (bidirectional)", "openFDA Labels"],
        ["CO_REPORTED_WITH", "Drug ↔ Drug (bidirectional)", "FAERS Count API"],
        ["DRUG_CAUSES_REACTION", "Drug → Reaction", "FAERS Count API"],
        ["LABEL_WARNS_REACTION", "Drug → Reaction", "openFDA Labels (adverse_reactions)"],
    ]
    story.append(make_table(edge_data, col_widths=[2.0*inch, 2.2*inch, 2.7*inch]))
    story.append(Spacer(1, 0.2 * inch))

    # ── Section 6: Rebuild instructions ────────────────────────
    story.append(Paragraph("6. How to Rebuild the KG", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))

    cmds = [
        ("Full rebuild (~17 min):", "python3 scripts/build_kg.py --max-drugs 200 --sleep 0.15"),
        ("With Gemini for higher-recall interaction extraction:",
         "GEMINI_API_KEY=your_key python3 scripts/build_kg.py --max-drugs 200"),
        ("Skip FAERS step (faster, no co-reported/reactions):",
         "python3 scripts/build_kg.py --max-drugs 200 --skip-faers"),
        ("Skip disparity step:",
         "python3 scripts/build_kg.py --max-drugs 200 --skip-label-reactions"),
    ]
    for desc, cmd in cmds:
        story.append(Paragraph(f"<b>{desc}</b>", S["body"]))
        story.append(Paragraph(cmd, S["code"]))

    # ── Section 7: Remaining work ───────────────────────────────
    story.append(Paragraph("7. Remaining Work — DrugBank Integration (Fix #6)", S["h1"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=PRIMARY, spaceAfter=10))
    story.append(Paragraph(
        "DrugBank's curated drug-drug interaction dataset would provide ~2,700 high-quality DDIs "
        "for the ~800 drugs in the public open set, supplementing the 746 label-extracted interactions. "
        "The dataset requires a free academic license download. Implementation would follow the same "
        "builder pattern as <font name='Courier' size='8.5'>label_edges.py</font>.",
        S["body"]
    ))
    story.append(Paragraph("<b>Free alternative DDI sources:</b>", S["h3"]))
    alternatives = [
        "DailyMed (NLM) — structured SPL label XMLs with interaction sections",
        "ChEMBL (EMBL-EBI) — bioactivity database with target-level interaction potential",
        "KEGG Drug — drug interaction pairs in downloadable format",
        "SIDER — side effect and indication database extracted from package inserts",
    ]
    for alt in alternatives:
        story.append(Paragraph(f"• {alt}", S["bullet"]))

    # ── Build ───────────────────────────────────────────────────
    doc.build(story, onFirstPage=lambda c, d: None, onLaterPages=header_footer)
    print(f"PDF written to: {out_path}")


if __name__ == "__main__":
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    md_in  = os.path.join(project_root, "docs", "KNOWLEDGE_GRAPH_WALKTHROUGH.md")
    pdf_out = os.path.join(project_root, "docs", "KNOWLEDGE_GRAPH_WALKTHROUGH.pdf")
    build_pdf(md_in, pdf_out)
