"""
TruPharma GenAI Assistant  ·  Primary Demo
============================================
Streamlit app that connects to the openFDA RAG pipeline
for drug-label evidence retrieval and grounded answers.
"""

import sys
from pathlib import Path

# ── Ensure project root is importable ─────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import streamlit as st
from datetime import datetime
import time

from src.rag.engine import run_rag_query, read_logs

# ─── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Primary Demo | TruPharma RAG",
    page_icon="🩺",
    layout="wide",
)

# ─── Hide built-in page nav ──────────────────────────────────
st.markdown("""
<style>
div[data-testid="stSidebarNav"] { display: none !important; }
section[data-testid="stSidebar"] nav { display: none !important; }
section[data-testid="stSidebar"] ul[role="list"] { display: none !important; }
section[data-testid="stSidebar"] > div:first-child { padding-top: 0rem !important; }
/* Hide the auto-generated page nav links only, not collapse buttons */
section[data-testid="stSidebar"] ul[data-testid="stSidebarNavItems"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ─── App styling ──────────────────────────────────────────────
st.markdown("""<style>
.main-header-bar {
    background: linear-gradient(90deg, #F2994A, #EB5757);
    color: white; padding: 12px 16px; border-radius: 10px;
    font-weight: 600; margin-bottom: 14px;
}
.scenario-card {
    padding: 10px 12px; border-radius: 10px;
    margin-bottom: 8px; font-weight: 700; line-height: 1.2;
}
.primary-active {
    background-color: #E8F5E9; border-left: 6px solid #2E7D32;
}
.card {
    background: #FFFFFF; border: 1px solid #E5E7EB;
    border-radius: 14px; padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06); margin-bottom: 14px;
}
.card-title { font-weight: 800; font-size: 16px; margin-bottom: 8px; }
.card-title.response { color: #1f7a8c; }
.card-title.evidence { color: #d35400; }
.card-title.metrics  { color: #2e7d32; }
.card-title.logs     { color: #6b7280; }
.bullets { margin: 0; padding-left: 18px; }
.bullets li { margin: 6px 0; }
.pill-link {
    flex: 1; text-align: center; padding: 14px;
    border-radius: 14px; border: 1px solid #d1d5db;
    background: #ffffff; font-weight: 800; color: #111827;
    text-decoration: none !important;
    box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
/* Apply custom font but exclude Streamlit icon elements */
html, body,
p, h1, h2, h3, h4, h5, h6,
span, div, li, td, th, label, a,
input, textarea, select, button,
.stMarkdown, .stText, .stCaption,
[data-testid="stMetricValue"],
[data-testid="stMetricLabel"] {
    font-family: "Times New Roman", Times, serif !important;
    line-height: 1.4;
}
/* Restore Streamlit's icon font for Material Icons */
[data-testid="stIconMaterial"],
.material-symbols-rounded,
[data-testid="collapsedControl"] span,
span[class*="icon"] {
    font-family: "Material Symbols Rounded" !important;
}
</style>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  SESSION STATE
# ══════════════════════════════════════════════════════════════
if "active_panel" not in st.session_state:
    st.session_state.active_panel = "ALL"
if "result" not in st.session_state:
    st.session_state.result = None
if "logs" not in st.session_state:
    st.session_state.logs = []

def set_panel(name: str):
    st.session_state.active_panel = (
        "ALL" if st.session_state.active_panel == name else name
    )


# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════
st.sidebar.title("Scenario Mode")
st.sidebar.markdown(
    "<div class='scenario-card primary-active'>"
    "🟢 Primary Demo<br><small>Normal user workflow</small></div>",
    unsafe_allow_html=True,
)
if st.sidebar.button("⚠️ Go to Stress Test", key="go_stress"):
    st.switch_page("pages/stress_test.py")

st.sidebar.markdown("---")

# Example queries for convenience
st.sidebar.subheader("Example Queries")
EXAMPLES = [
    "-- Select an example --",
    "What are the drug interactions for ibuprofen?",
    "What is the recommended dosage for acetaminophen and are there any warnings?",
    "I am taking aspirin daily. What should I know about overdosage and when to stop use?",
    "What safety warnings exist for caffeine-containing products?",
    "What are the active ingredients in Tylenol and what are the drug interactions?",
    "What is the projected cost of antimicrobial resistance to GDP in 2050?",
]
example = st.sidebar.selectbox("Pick a sample question:", EXAMPLES, index=0)

st.sidebar.subheader("Query Input")
default_q = "" if example == EXAMPLES[0] else example
query_text = st.sidebar.text_area(
    "Enter your drug-label question:",
    value=default_q,
    placeholder="e.g. What are the side effects of ibuprofen?",
    height=100,
)

# ── Advanced settings (collapsible) ──
with st.sidebar.expander("Advanced Settings"):
    method = st.selectbox(
        "Retrieval method",
        ["hybrid", "dense", "sparse"],
        index=0,
    )
    top_k = st.slider("Top-K evidence", 3, 10, 5)
    gemini_key = st.text_input(
        "Google Gemini API key (optional)",
        type="password",
        help="If provided, answers are generated by Gemini 2.0 Flash. "
             "Otherwise, a rule-based extractive fallback is used.",
    )

run = st.sidebar.button("🔍 Run RAG Query", type="primary", width="stretch")

st.sidebar.markdown("---")
if st.sidebar.button("🔄 Reset Session"):
    st.session_state.clear()
    st.rerun()


# ══════════════════════════════════════════════════════════════
#  RUN LOGIC  (executes BEFORE rendering so state is updated)
# ══════════════════════════════════════════════════════════════
if run and query_text:
    with st.spinner("Fetching FDA drug labels and running RAG pipeline..."):
        result = run_rag_query(
            query_text,
            gemini_key=gemini_key,
            method=method,
            top_k=top_k,
            use_rerank=False,
        )
    st.session_state.result = result

    # Store for stress-test comparison page
    st.session_state.primary_last_run = {
        "query": query_text,
        "confidence": f"{result['confidence']:.0%}",
        "evidence_count": len(result["evidence"]),
    }

    st.session_state.logs.append(
        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]  "
        f"Query completed in {result['latency_ms']} ms  ·  "
        f"Evidence: {len(result['evidence'])}  ·  "
        f"Confidence: {result['confidence']:.0%}"
    )

elif run and not query_text:
    st.sidebar.warning("Please enter a query first.")


# ══════════════════════════════════════════════════════════════
#  MAIN HEADER
# ══════════════════════════════════════════════════════════════
st.markdown("## TruPharma GenAI Assistant")
st.markdown(
    "<div class='main-header-bar'>Prototype Primary Demo — Drug Label Evidence RAG</div>",
    unsafe_allow_html=True,
)

# ── Pill row ──
c1, c2, c3, c4, c5 = st.columns(5, gap="small")
c1.button("Response", width="stretch",
           type="primary" if st.session_state.active_panel == "Response" else "secondary",
           key="pill_response", on_click=set_panel, args=("Response",))
c2.button("Knowledge Graph", width="stretch",
           type="primary" if st.session_state.active_panel == "KG" else "secondary",
           key="pill_kg", on_click=set_panel, args=("KG",))
c3.button("Evidence / Artifacts", width="stretch",
           type="primary" if st.session_state.active_panel == "Evidence" else "secondary",
           key="pill_evidence", on_click=set_panel, args=("Evidence",))
c4.button("Metrics & Monitoring", width="stretch",
           type="primary" if st.session_state.active_panel == "Metrics" else "secondary",
           key="pill_metrics", on_click=set_panel, args=("Metrics",))
c5.button("Logs", width="stretch",
           type="primary" if st.session_state.active_panel == "Logs" else "secondary",
           key="pill_logs", on_click=set_panel, args=("Logs",))

st.caption("Click a pill to focus; click again to return to the full dashboard view.")


# ══════════════════════════════════════════════════════════════
#  RENDER HELPERS
# ══════════════════════════════════════════════════════════════

def render_response():
    st.markdown(
        "<div class='card'><div class='card-title response'>Response Panel</div>",
        unsafe_allow_html=True,
    )
    r = st.session_state.result
    if not r:
        st.info("Enter a drug-label question in the sidebar and click **Run RAG Query**.")
    else:
        st.markdown(f"**Confidence:** {r['confidence']:.0%}")
        llm_label = "Gemini 2.0 Flash" if r["llm_used"] else "Extractive fallback"
        st.markdown(f"**Generator:** {llm_label}")
        st.markdown("---")
        st.markdown(r["answer"])
    st.markdown("</div>", unsafe_allow_html=True)


def render_evidence():
    st.markdown(
        "<div class='card'><div class='card-title evidence'>Evidence / Artifacts</div>",
        unsafe_allow_html=True,
    )
    r = st.session_state.result
    if not r or not r["evidence"]:
        st.info("Evidence will appear here after running a query.")
    else:
        for i, ev in enumerate(r["evidence"], 1):
            with st.expander(f"Evidence {i}  ·  {ev['cite']}  ·  field: {ev['field']}"):
                st.markdown(f"**Document:** `{ev['doc_id']}`")
                st.markdown(f"**Field:** `{ev['field']}`")
                st.markdown("**Content:**")
                st.text(ev["content"][:600])
    st.markdown("</div>", unsafe_allow_html=True)


def render_kg():
    st.markdown(
        "<div class='card'><div class='card-title' style='color:#7c3aed;'>Knowledge Graph</div>",
        unsafe_allow_html=True,
    )
    r = st.session_state.result
    if not r:
        st.info("Run a query to see Knowledge Graph data.")
    elif not r.get("kg_available", False):
        st.warning("Knowledge Graph not available. "
                   "Build it with: `python3 scripts/build_kg.py`")
    else:
        interactions = r.get("kg_interactions", [])
        co_reported = r.get("kg_co_reported", [])
        reactions = r.get("kg_reactions", [])
        ingredients = r.get("kg_ingredients", [])

        any_data = interactions or co_reported or reactions or ingredients
        if not any_data:
            st.info("This drug is not in the Knowledge Graph seed list. "
                    "Try a more common drug, or rebuild with a larger seed.")
        else:
            if ingredients:
                st.markdown("**🧪 Active Ingredients**")
                for ing in ingredients:
                    strength = f" — {ing['strength']}" if ing.get('strength') else ""
                    st.markdown(f"- {ing['ingredient']}{strength}")

            if interactions:
                st.markdown("**⚡ Drug Interactions** (from FDA labels)")
                for ix in interactions[:10]:
                    desc = f": {ix['description']}" if ix.get('description') else ""
                    st.markdown(f"- **{ix['drug_name']}**{desc}")

            if co_reported:
                st.markdown("**🔗 Co-Reported Drugs** (from FAERS)")
                import pandas as pd
                df = pd.DataFrame([
                    {"Drug": cr["drug_name"], "Reports": cr.get("report_count", 0)}
                    for cr in co_reported[:10]
                ])
                st.dataframe(df, hide_index=True, width='stretch')

            if reactions:
                st.markdown("**⚠️ Adverse Reactions** (from FAERS)")
                import pandas as pd
                df = pd.DataFrame([
                    {"Reaction": rx["reaction"], "Reports": rx.get("report_count", 0)}
                    for rx in reactions[:10]
                ])
                st.dataframe(df, hide_index=True, width='stretch')

    st.markdown("</div>", unsafe_allow_html=True)


def render_metrics():
    st.markdown(
        "<div class='card'><div class='card-title metrics'>Metrics & Monitoring</div>",
        unsafe_allow_html=True,
    )
    r = st.session_state.result
    if r:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Latency", f"{r['latency_ms']:.0f} ms")
        m2.metric("Evidence Ct.", len(r["evidence"]))
        m3.metric("Confidence", f"{r['confidence']:.0%}")
        m4.metric("Records Fetched", r["num_records"])

        st.markdown(f"- **Retrieval method:** {r['method']}")
        st.markdown(f"- **LLM used:** {'Gemini 2.0 Flash' if r['llm_used'] else 'Extractive fallback'}")
        st.markdown(f"- **openFDA search:** `{r['search_query'][:120]}`")
        st.markdown(f"- **Errors / Fallbacks:** None")
    else:
        st.info("Run a query to see metrics.")
    st.markdown("</div>", unsafe_allow_html=True)


def render_logs():
    st.markdown(
        "<div class='card'><div class='card-title logs'>Logs</div>",
        unsafe_allow_html=True,
    )

    # Session logs (in-memory)
    st.markdown("**Session Log**")
    if not st.session_state.logs:
        st.write("No queries run yet this session.")
    else:
        for line in reversed(st.session_state.logs[-10:]):
            st.write(line)

    # CSV log (persistent)
    st.markdown("---")
    st.markdown("**Product Metrics CSV** (`logs/product_metrics.csv`)")
    csv_rows = read_logs(last_n=10)
    if csv_rows:
        import pandas as pd
        df = pd.DataFrame(csv_rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.write("No CSV log entries yet.")

    st.markdown("</div>", unsafe_allow_html=True)


def render_overall():
    left, right = st.columns([2.2, 1.2], gap="large")
    with left:
        render_response()
        render_kg()
    with right:
        render_evidence()
        render_metrics()
    render_logs()


# ══════════════════════════════════════════════════════════════
#  CONDITIONAL VIEWS
# ══════════════════════════════════════════════════════════════
active = st.session_state.active_panel

if active == "ALL":
    render_overall()
elif active == "Response":
    render_response()
elif active == "KG":
    render_kg()
elif active == "Evidence":
    render_evidence()
elif active == "Metrics":
    render_metrics()
elif active == "Logs":
    render_logs()
