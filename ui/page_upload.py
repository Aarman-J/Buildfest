"""Upload & Data Quality page: upload, column mapping, cleaning, quality checks and aggregate downloads."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.data_validation import analysis_availability, quality_report_to_markdown
from src.schema_mapper import FIELD_ORDER, SCHEMA, SCHEMA_BY_NAME
from ui import components as C
from ui import state as S
from ui import transforms as X

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
KEEP = "— Leave unmapped (keep as extra column) —"
IGNORE = "Ignore this column"


def _field_label(f: str) -> str:
    return f"{f} – {SCHEMA_BY_NAME[f].description}" if f in SCHEMA_BY_NAME else f


def load_sample(state) -> None:
    S.register_upload(state, SAMPLE.read_bytes(), SAMPLE.name)
    S.process(state)


def render(state) -> None:
    C.header("Your data", "Upload & Data Quality")
    if state.get("demo_loaded") and state.get("upload_name") == SAMPLE.name:
        st.success("**Demo mode:** the BuildFest 2025–26 sample dataset was loaded and analyzed automatically. "
                   "Every page already shows results. Upload your own survey below to replace it.",
                   icon=":material/auto_awesome:")
    st.markdown("Upload a survey export (Excel or CSV). The dashboard recognizes the survey questions, asks you to "
                "confirm anything uncertain, then cleans and analyzes the data **in memory**. Nothing you upload is "
                "saved to disk, and no rows are deleted; problems are flagged instead.")

    c1, c2 = st.columns([3, 1], vertical_alignment="bottom")
    up = c1.file_uploader("Survey export (.xlsx or .csv)", type=["xlsx", "csv"], key="file_widget")
    if up is not None:
        data = up.getvalue()
        h = S.file_hash(data)
        if state.get("uploader_last") != h:
            state["uploader_last"] = h
            state["demo_loaded"] = False
            S.register_upload(state, data, up.name, force=True)
            st.toast("New file loaded. Results from any previous file were cleared.")
            # Automatic processing when every column was recognized without ambiguity.
            if state.get("upload_raw") is not None and not X.needs_confirmation(S.mapping_preview(state)):
                with st.spinner("Recognizing columns, cleaning, validating and analyzing…"):
                    S.process(state)
            st.rerun()   # redraw sidebar and banners for the new dataset
    if SAMPLE.exists():
        c2.button("Load BuildFest 2025–26 sample", on_click=load_sample, args=(state,), width="stretch",
                  help="Local copy of the challenge dataset (not included in public deployments).")

    if state.get("upload_error"):
        st.error(state["upload_error"])
        return
    if state.get("upload_raw") is None:
        st.info("Waiting for a file. Expected: one row per survey response, with the 988 training questions as "
                "columns. Column order, extra columns and minor wording differences are fine.")
        return

    raw: pd.DataFrame = state["upload_raw"]
    st.markdown(f"**File:** `{state['upload_name']}` · **Sheet:** `{state['upload_sheet']}` · "
                f"**{len(raw):,} rows × {raw.shape[1]} columns**")
    sheets = state.get("upload_sheets") or []
    if len(sheets) > 1:
        sel = st.selectbox("Worksheet", sheets, index=sheets.index(state["upload_sheet"]),
                           help="The sheet that matched the most survey questions was chosen automatically.")
        if sel != state["upload_sheet"]:
            S.select_sheet(state, sel)
            st.rerun()

    # ---- Step 1: schema -------------------------------------------------------------------
    st.subheader("1 · Check the column mapping")
    records = S.mapping_preview(state)
    report = state.get("report") if S.is_ready(state) or state.get("error") else None
    rejected = [{"source": r["source"], "status": "rejected", "field": None, "candidates": r["candidates"],
                 "method": "value check", "note": r["note"]}
                for r in (report or {}).get("columns", {}).get("rejected_by_value_check", [])
                if r["source"] not in (state.get("overrides") or {})]
    present = sorted({r["field"] for r in records if r["status"] == "matched"} -
                     {r["candidates"][0] for r in rejected if r["candidates"]})
    n_need = len(X.needs_confirmation(records)) + len(rejected)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Recognized questions", f"{len(present)} of {len(SCHEMA)}")
    m2.metric("Need your confirmation", n_need)
    m3.metric("Unrecognized (kept as extra)", sum(r["status"] == "unmatched" for r in records))
    m4.metric("Personal-data columns not loaded", sum(r["status"] == "pii_dropped" for r in records))

    with st.expander("Column-by-column mapping", expanded=n_need > 0):
        st.dataframe(X.mapping_table(records + rejected), hide_index=True)

    missing = [f for f in FIELD_ORDER if f not in present]
    avail = analysis_availability(present)
    a1, a2 = st.columns(2)
    with a1:
        n_ok = sum(v["available"] for v in avail.values())
        st.markdown(f"**{n_ok} of {len(avail)} analyses can run with these columns**")
        for v in avail.values():
            if not v["available"]:
                st.markdown(f"⛔ {v['label']} – missing: {', '.join(v['missing_fields'])}")
        with st.expander("List all analyses"):
            for v in avail.values():
                st.markdown(("✅ " if v["available"] else "⛔ ") + v["label"])
    with a2:
        st.markdown("**Survey fields not found**")
        if missing:
            for f in missing:
                st.markdown(f"- `{f}` {'(core)' if SCHEMA_BY_NAME[f].required else '(optional)'}")
        else:
            st.markdown("All expected fields were found.")

    confirm = X.needs_confirmation(records) + rejected
    others = [r for r in records if r["status"] == "unmatched"]
    if confirm or others:
        with st.form("mapping_form"):
            st.markdown("**Confirm uncertain columns.** Nothing is mapped automatically when the match is uncertain. "
                        "Choose the matching survey field, or leave it unmapped.")
            choices = {}
            taken = set(present)
            for r in confirm + others:
                opts = [KEEP, IGNORE] + [f for f in r["candidates"] if f] + \
                       [f for f in FIELD_ORDER if f not in taken and f not in r["candidates"]]
                label = f"`{r['source'][:90]}` – {X.STATUS_TEXT.get(r['status'], r['status'])}"
                if r.get("note"):
                    label += f" ({r['note']})"
                choices[r["source"]] = st.selectbox(label, opts, index=0, format_func=_field_label,
                                                    key=f"map_choice_{r['source']}")
            if st.form_submit_button("Apply mapping choices"):
                ov = dict(state.get("overrides") or {})
                for src, ch in choices.items():
                    if ch == KEEP:
                        ov.pop(src, None)
                    else:
                        ov[src] = None if ch == IGNORE else ch
                state["overrides"] = ov
                st.rerun()
    if state.get("overrides"):
        st.caption("Confirmed mappings: " + "; ".join(f"`{k}` → {v or 'ignored'}" for k, v in state["overrides"].items()))

    # ---- Step 2: run ------------------------------------------------------------------------
    st.subheader("2 · Clean and analyze")
    stale = state.get("results") is not None and not S.is_ready(state)
    if stale:
        st.warning("The mapping changed since the last run. Run again to update all results.")
    if st.button("Run cleaning & analysis", type="primary", disabled=S.is_ready(state)):
        with st.spinner("Cleaning, validating and analyzing…"):
            S.process(state)
        st.rerun()
    if state.get("error"):
        st.error(state["error"])
        return
    if not S.is_ready(state):
        return

    # ---- Step 3: quality summary ------------------------------------------------------------
    report = state["report"]
    st.subheader("3 · Data-quality check")
    k1, k2, k3, k4 = st.columns(4)
    C.kpi(k1, f"{report['rows']['input']:,}", "Rows uploaded", "", "📥")
    C.kpi(k2, f"{report['rows']['output']:,}", "Rows kept", "No rows are ever deleted", "✅")
    C.kpi(k3, str(len(report["fields"]["present"])), "Questions recognized", "", "🧭")
    C.kpi(k4, str(sum(1 for w in report["warnings"] if w["severity"] in ("high", "medium"))), "Warnings to note",
          "Listed below", "⚠️")
    st.markdown("")
    warn = X.warnings_table(report)
    top = warn[warn["Severity"].isin(["High", "Medium"])].head(6)
    for _, w in top.iterrows():
        st.markdown(f"- **{w['Severity']}:** {w['Message']}")
    st.success("Results on every page now reflect this file. Go to **Explore Findings** to see them.",
               icon=":material/check_circle:")
    with st.expander("Technical details: flags, answers per question, standardization, unexpected labels"):
        t1, t2, t3, t4, t5 = st.tabs(["All warnings", "Flagged responses", "Answers per question",
                                      "Standardization", "Unexpected answers"])
        with t1:
            st.dataframe(warn, hide_index=True)
        with t2:
            st.caption("Flags never remove rows. Analyses keep flagged responses; Q1 includes a sensitivity check.")
            st.dataframe(X.flags_table(report), hide_index=True)
        with t3:
            st.caption("Each percentage uses the *valid* answers to that question. 'Present but unusable' = an answer "
                       "outside the expected scale (kept for audit, not scored).")
            st.dataframe(X.missingness_table(report), hide_index=True)
            st.dataframe(X.analyses_table(report), hide_index=True)
        with t4:
            for line in X.standardization_lines(report):
                st.markdown(f"- {line}")
        with t5:
            st.caption("Labels that don't match the expected answer options are kept as raw text and treated as "
                       "missing until a mapping is approved. They are never guessed.")
            unexpected = report.get("unexpected_categories", {})
            if unexpected:
                st.dataframe(pd.DataFrame([{"Field": f, "Label": k, "Count": v} for f, d in unexpected.items()
                                           for k, v in d.items()]), hide_index=True)
            else:
                st.markdown("None.")

    # ---- Step 4: downloads (aggregate only) -------------------------------------------------
    st.subheader("4 · Download reports")
    st.caption("All downloads are aggregate summaries of the file above, generated in memory. They contain no "
               "individual responses, response IDs, exact dates, written comments or partner names.")
    _downloads(state)


def _downloads(state) -> None:
    results, report, did = state["results"], state["report"], state["dataset_id"]
    label = state.get("upload_name", "uploaded survey")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Executive summary (Markdown)", X.executive_summary_md(results, report, label),
                       f"executive_summary_{did}.md", "text/markdown", width="stretch")
    c2.download_button("Priority recommendations (Markdown)", X.priorities_md(results),
                       f"priority_recommendations_{did}.md", "text/markdown", width="stretch")
    c3.download_button("Summary tables (ZIP of CSVs)", X.tables_zip(results, report), f"summary_tables_{did}.zip",
                       "application/zip", width="stretch")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Data-quality report (Markdown)", quality_report_to_markdown(report),
                       f"data_quality_{did}.md", "text/markdown", width="stretch")
    c2.download_button("All rule-based recommendations (Markdown)", X.rule_recommendations_md(results),
                       f"all_rule_recommendations_{did}.md", "text/markdown", width="stretch")
    ai = S.latest_ai_result(state)
    if ai:
        c3.download_button("AI-drafted recommendations (ADVISORY)", X.ai_recommendations_md(ai),
                           f"ai_recommendations_ADVISORY_{did}.md", "text/markdown", width="stretch")
    with st.expander("Machine-readable versions (JSON)"):
        st.download_button("Aggregate analysis results (JSON)", json.dumps(results, indent=2),
                           f"analysis_results_{did}.json", "application/json")
        st.download_button("Data-quality report (JSON)", X.report_json_bytes(report), f"data_quality_{did}.json",
                           "application/json")
