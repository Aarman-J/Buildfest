"""Q1 · Training effectiveness page."""

from __future__ import annotations

import streamlit as st

from ui import charts as CH
from ui import components as C
from ui import state as S
from ui import theme as T
from ui import transforms as X


def render(state) -> None:
    C.header("Question 1 of 3", "Training Effectiveness", "q1")
    st.markdown(C.RETRO_CAVEAT)
    full = state["results"]["q1"]
    if not full.get("available"):
        st.warning("No before/after confidence pairs were found in this dataset, so this section cannot run.")
        return
    if full.get("objectives_missing"):
        st.info("Not available in this upload (question pair incomplete): " + ", ".join(full["objectives_missing"]))

    opts = X.objective_options(full)
    labels = {k: v for k, v in opts}
    f1, f2 = st.columns([2, 3])
    chosen = f1.pills("Learning objectives", [k for k, _ in opts], selection_mode="multi",
                      default=[k for k, _ in opts], format_func=labels.get, key="q1_objs") or [k for k, _ in opts]
    months = X.available_months(full)
    undated = full["trend"].get("rows_without_valid_training_date", 0)
    period = None
    with f2:
        use_period = st.toggle("Limit to a training period", value=False, key="q1_period_on",
                               disabled=len(months) < 2,
                               help="Uses the training date reported by participants. Responses without a valid "
                                    "training date are excluded when a period is selected; survey completion "
                                    "dates are never used instead.")
        if use_period and len(months) >= 2:
            start, end = st.select_slider("Training months", options=months, value=(months[0], months[-1]),
                                          key="q1_period")
            period = X.months_in_range(months, start, end)
    q1 = S.q1_for_period(state, period)
    if period:
        st.caption(f"Showing {q1['n_rows']:,} responses with training dates {period[0]} to {period[-1]} "
                   f"({undated} responses without a valid training date are excluded).")
    else:
        st.caption(f"Showing all {q1['n_rows']:,} responses (including {undated} without a valid training date).")
    if not X.objective_options(q1):
        st.warning("Too few responses in this period to report results (fewer than 10 paired answers).")
        return

    tabs = st.tabs(["Before vs after", "Individual change", "Remaining gaps", "Over time", "Samples & uncertainty"])
    with tabs[0]:
        st.plotly_chart(CH.before_after(X.q1_before_after(q1, chosen), "% agreeing before (recalled) and after"),
                        key="q1_ba")
        st.plotly_chart(CH.pp_change(X.q1_pp_change(q1, chosen), "Change in % agreeing (percentage points)"),
                        key="q1_pp")
        st.caption("Whiskers: 95% confidence intervals (Wilson). Only respondents who answered both the before and "
                   "after question are included (paired n shown on hover).")
    with tabs[1]:
        st.plotly_chart(CH.stacked_share(X.q1_change(q1, chosen).rename(columns={"category": "cat"}),
                                         "Individual change between recalled-before and after ratings", "cat",
                                         T.CHANGE, ["Improved", "Unchanged", "Declined"]), key="q1_chg")
        st.caption("'Unchanged' includes people who were already at the top of the scale before (ceiling effect).")
        key = st.selectbox("Transition detail", chosen, format_func=labels.get, key="q1_tr_obj")
        pivot = X.q1_transition(q1, key)
        if pivot is not None:
            st.plotly_chart(CH.transition_heatmap(pivot, f"How answers moved: {labels[key]}"), key="q1_heat")
            st.caption("Each cell counts respondents with that before/after combination. Cells below the diagonal "
                       "are declines.")
    with tabs[2]:
        gaps = X.q1_gaps(q1, chosen)
        st.plotly_chart(CH.stacked_share(gaps.rename(columns={"component": "cat"}),
                                         "Respondents not agreeing after training (remaining gap)", "cat", T.GAP,
                                         list(T.GAP)), key="q1_gap")
        tbl = X.q1_summary_table(q1, chosen)[["Learning objective", "Paired n", "Not agreeing after %",
                                              "Agree before (recalled) %", "Strongly agree before %"]]
        st.dataframe(tbl, hide_index=True)
        st.caption("A high 'agree before' share limits how much improvement can be measured (ceiling effect). "
                   "Consider adding knowledge-check questions for these objectives.")
    with tabs[3]:
        t = q1["trend"]
        if not t.get("available"):
            st.info(t.get("reason", "No valid training dates in this dataset."))
        else:
            trend = X.q1_trend(q1, chosen)
            metric = st.radio("Measure", ["post_agree_pct", "change_pp", "not_agree_after_pct"], horizontal=True,
                              format_func={"post_agree_pct": "% agreeing after", "change_pp": "Change (pp)",
                                           "not_agree_after_pct": "% not agreeing after"}.get, key="q1_metric")
            ytitle = {"post_agree_pct": "% agreeing after", "change_pp": "Percentage points",
                      "not_agree_after_pct": "% not agreeing after"}[metric]
            st.plotly_chart(CH.trend_lines(trend, metric, "Outcomes by training month", ytitle), key="q1_trend")
            st.plotly_chart(CH.month_counts(X.q1_month_counts(q1), "Responses per training month"), key="q1_months")
            st.caption(f"{t['note']} Months with fewer than 10 responses are hidden (grey bars); open markers and gold "
                       "bars mark months with fewer than 30 responses, where one session can drive the result.")
            miss = trend.drop_duplicates("month")[["month", "n_rows", "experience_answered_pct", "call_988_answered_pct"]]
            with st.expander("Optional questions answered per month (survey-version changes)"):
                st.dataframe(miss.rename(columns={"month": "Month", "n_rows": "Responses",
                                                  "experience_answered_pct": "Experience items answered %",
                                                  "call_988_answered_pct": "Call-988 answered %"}), hide_index=True)
    with tabs[4]:
        st.dataframe(X.q1_summary_table(q1, chosen), hide_index=True)
        if full.get("sensitivity"):
            st.markdown("**Sensitivity check:** do flagged responses change the conclusions?")
            st.dataframe(X.sensitivity_table(full), hide_index=True)
            s = full["sensitivity"]
            (st.success if s["robust"] else st.warning)(
                f"Excluding possible reversed-scale, very fast and duplicate responses moves headline figures by at "
                f"most {s['max_shift_pp']} pp ({'robust' if s['robust'] else 'not robust'}, threshold 5 pp). "
                "All responses are kept in the main results.")
