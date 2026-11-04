"""EveryMind 988 Training Insights – Streamlit dashboard entry point.

Run locally:  streamlit run app.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui import components as C  # noqa: E402
from ui import page_ai, page_exports, page_overview, page_q1, page_q2, page_q3, page_upload  # noqa: E402
from ui import state as S  # noqa: E402
from ui.theme import CSS  # noqa: E402

PAGES = ["Executive Overview", "Data Upload & Quality", "Q1 · Training Effectiveness", "Q2 · Trends & Relationships",
         "Q3 · Community Differences", "AI Insights & Recommendations", "Exports & Reports"]
ICONS = {"Executive Overview": "🏠", "Data Upload & Quality": "📤", "Q1 · Training Effectiveness": "📈",
         "Q2 · Trends & Relationships": "🔗", "Q3 · Community Differences": "🗺️",
         "AI Insights & Recommendations": "✨", "Exports & Reports": "📦"}
SHORT = {"Executive Overview": "Overview", "Data Upload & Quality": "Upload & Data Quality",
         "Q1 · Training Effectiveness": "Q1 · Effectiveness", "Q2 · Trends & Relationships": "Q2 · Relationships",
         "Q3 · Community Differences": "Q3 · Communities", "AI Insights & Recommendations": "AI Recommendations",
         "Exports & Reports": "Exports & Reports"}
NEEDS_DATA = set(PAGES) - {"Executive Overview", "Data Upload & Quality"}

st.set_page_config(page_title="EveryMind 988 Training Insights", page_icon="💙", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
state = st.session_state


def demo_autoload_enabled() -> bool:
    """Auto-load the bundled sample on first visit (demo mode). Off with [demo] autoload = false."""
    try:
        val = st.secrets.get("demo", {}).get("autoload", None)
    except Exception:  # noqa: BLE001 - no secrets file
        val = None
    if val is None:
        val = os.environ.get("EVERYMIND_DEMO_AUTOLOAD", "true")
    return str(val).strip().lower() in ("1", "true", "yes", "on")


# Demo mode: if the challenge sample is present locally and nothing is loaded yet, load and analyze it so the
# dashboard is ready without any clicks. Only once per session; an upload replaces it.
if (not state.get("demo_tried") and state.get("upload_raw") is None and page_upload.SAMPLE.exists()
        and demo_autoload_enabled()):
    state["demo_tried"] = True
    with st.spinner("Loading the BuildFest 2025–26 sample dataset…"):
        page_upload.load_sample(state)
    state["demo_loaded"] = S.is_ready(state)

with st.sidebar:
    st.markdown('<div class="em-brand"><div class="logo">E</div><div><div class="name">EveryMind</div>'
                '<div class="sub">988 Training Insights</div></div></div>', unsafe_allow_html=True)
    page = st.radio("Navigate", PAGES, key="nav", label_visibility="collapsed",
                    format_func=lambda p: f"{ICONS[p]}  {SHORT[p]}")
    st.markdown("")
    ready = S.is_ready(state)
    if ready:
        r = state["report"]
        label = "BuildFest 2025–26 sample" if state.get("demo_loaded") and state.get("upload_name") == \
            page_upload.SAMPLE.name else state.get("upload_name", "")
        st.markdown(f'<div class="em-side-card"><span class="em-dot"></span><b>Dataset loaded</b><br>{C.e(label)}'
                    f'<br>{r["rows"]["output"]:,} responses</div>', unsafe_allow_html=True)
    elif state.get("upload_raw") is not None:
        st.markdown(f'<div class="em-side-card"><b>File loaded</b><br>{C.e(state.get("upload_name", ""))}<br>'
                    'Run cleaning on the upload page.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="em-side-card"><b>No dataset loaded</b><br>Upload a survey to begin.</div>',
                    unsafe_allow_html=True)
    st.markdown("")
    st.caption("🔒 Processed in memory for this session only. Comments are never displayed; groups under 10 "
               "respondents are suppressed.")
    st.caption("BuildFest 2026 · Nonprofit Data Challenge")

if page == "Executive Overview":
    page_overview.render(state, ready)
elif page == "Data Upload & Quality":
    page_upload.render(state)
elif page in NEEDS_DATA and not ready:
    C.header("EveryMind", page)
    C.need_data()
elif page == "Q1 · Training Effectiveness":
    page_q1.render(state)
elif page == "Q2 · Trends & Relationships":
    page_q2.render(state)
elif page == "Q3 · Community Differences":
    page_q3.render(state)
elif page == "AI Insights & Recommendations":
    page_ai.render(state)
elif page == "Exports & Reports":
    page_exports.render(state)
