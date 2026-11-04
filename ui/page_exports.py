"""Exports page: everything is generated in memory from the current dataset."""

from __future__ import annotations

import json

import streamlit as st

from src.data_validation import quality_report_to_markdown
from ui import components as C
from ui import state as S
from ui import transforms as X


def render(state) -> None:
    C.header("Share", "Exports & Reports")
    results, report = state["results"], state["report"]
    label = state.get("upload_name", "uploaded survey")
    st.markdown(f"All files below are generated from the **currently loaded dataset** (`{label}`, "
                f"{results['n_rows']:,} responses) at download time. None contain written comments, response-level "
                "raw text, exact timestamps or partner names.")
    did = state["dataset_id"]

    st.subheader("Data")
    c1, c2 = st.columns(2)
    c1.download_button("Privacy-safe cleaned dataset (CSV)", S.public_csv(state), f"everymind_cleaned_{did}.csv",
                       "text/csv", width="stretch")
    c2.download_button("Key analysis tables (ZIP of CSVs)", X.tables_zip(results, report),
                       f"everymind_tables_{did}.zip", "application/zip", width="stretch")

    st.subheader("Reports")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Executive summary (Markdown)", X.executive_summary_md(results, report, label),
                       f"executive_summary_{did}.md", "text/markdown", width="stretch")
    c2.download_button("Data-quality report (Markdown)", quality_report_to_markdown(report),
                       f"data_quality_{did}.md", "text/markdown", width="stretch")
    c3.download_button("Data-quality report (JSON)", X.report_json_bytes(report), f"data_quality_{did}.json",
                       "application/json", width="stretch")
    c1, c2 = st.columns(2)
    c1.download_button("All analysis results (JSON, aggregate)", json.dumps(results, indent=2),
                       f"analysis_results_{did}.json", "application/json", width="stretch")

    st.subheader("Recommendations")
    c1, c2 = st.columns(2)
    c1.download_button("Rule-based recommendations (verified, Markdown)", X.rule_recommendations_md(results),
                       f"recommendations_rule_based_{did}.md", "text/markdown", width="stretch")
    ai = S.latest_ai_result(state)
    if ai:
        c2.download_button("AI-drafted recommendations (ADVISORY, Markdown)", X.ai_recommendations_md(ai),
                           f"recommendations_AI_ADVISORY_{did}.md", "text/markdown", width="stretch")
    else:
        c2.caption("No AI-drafted recommendations for this dataset yet.")
    st.caption("AI-drafted recommendations are labelled separately and are advisory; the rule-based recommendations "
               "and statistical results are the verified outputs.")
