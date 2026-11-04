"""Q3 · Community differences page."""

from __future__ import annotations

import streamlit as st

from ui import charts as CH
from ui import components as C
from ui import theme as T
from ui import transforms as X


def render(state) -> None:
    C.header("Question 3 of 3", "Community Differences", "q3")
    q3 = state["results"]["q3"]
    dims = X.q3_dimensions(q3)
    if not dims:
        st.warning("No county, role or language columns were found, so group comparisons cannot run.")
        return
    pol = q3["policy"]
    with st.expander("How groups are compared (read first)", expanded=False):
        st.markdown(f"- Groups with **fewer than {pol['min_cell']}** respondents are shown only as "
                    f"'<{pol['min_cell']}' with no detailed results (privacy and reliability).\n"
                    f"- Groups with **{pol['min_cell']}–{pol['min_compare'] - 1}** respondents are descriptive only: "
                    "they are not compared or interpreted.\n"
                    f"- A difference is highlighted only if it is at least {pol['meaningful_pp']:g} percentage points, "
                    f"both sides have ≥ {pol['min_compare']} respondents, and confidence intervals do not overlap.")
        for g in q3["guardrails"]:
            st.markdown(f"- {g}")

    dim = st.segmented_control("Compare by", dims, default=dims[0], format_func=X.DIM_LABELS.get, key="q3_dim") or dims[0]
    objs = [(k, lab) for k, lab in X.objective_options(state["results"]["q1"])]
    c2, c3 = st.columns([3, 2], vertical_alignment="bottom")
    obj = c2.selectbox("Learning objective", [k for k, _ in objs], format_func=dict(objs).get, key="q3_obj") \
        if objs else None
    incl = c3.toggle("Show descriptive-only groups (10–29)", value=False, key="q3_incl",
                     help="Shown for context only. Too small for comparison or interpretation.")
    g = q3["groupings"][dim]
    if g.get("overlapping"):
        st.info(f"Roles are multi-select: {g['membership_total']:,} role memberships come from {g['rows_total']:,} "
                "respondents, so a person can appear in several groups. Use **Primary role** for non-overlapping groups.")
    if dim == "county":
        st.info("County results are entangled with **who attended** (e.g. mostly students in one county, mostly adults "
                "in another) and with individual sessions. See the *Audience mix* tab before drawing conclusions.")
    if incl:
        st.caption("⚠️ Descriptive-only groups are included in charts below; do not compare them with other groups.")

    tabs = st.tabs(["Confidence", "Remaining gaps", "Training experience", "Group table", "Missingness & versions"]
                   + (["Audience mix (county)"] if dim == "county" else []))
    with tabs[0]:
        if obj:
            st.plotly_chart(CH.grouped_share(X.q3_outcomes(q3, dim, obj, incl), f"% agreeing: {dict(objs)[obj]}",
                                             "group", "phase", T.BEFORE_AFTER), key="q3_conf")
            st.caption("'Before' ratings were recalled after training. Differences in *change* are often driven by "
                       "different recalled starting points rather than different end points.")
        sig = X.q3_signals(q3, dim)
        st.markdown("**Differences meeting the signal rule** (descriptive; investigate before acting)")
        if sig.empty:
            st.markdown("None for this grouping.")
        else:
            st.dataframe(sig, hide_index=True)
    with tabs[1]:
        if obj:
            st.plotly_chart(CH.grouped_share(X.q3_gap(q3, dim, obj, incl).assign(series="Not agreeing after"),
                                             f"% not agreeing after training: {dict(objs)[obj]}", "group", "series",
                                             {"Not agreeing after": T.AMBER}), key="q3_gap")
    with tabs[2]:
        exp = X.q3_experience(q3, dim, incl)
        if exp.empty:
            st.info("Training-experience results are not available for groups of adequate size.")
        else:
            st.plotly_chart(CH.grouped_share(exp, "Training experience and recommendation by group", "group",
                                             "measure"), key="q3_exp")
    with tabs[3]:
        st.dataframe(X.q3_group_table(q3, dim), hide_index=True)
        st.caption("Suppressed groups show only '<10'. A small group reflects how many people were surveyed, not "
                   "community need or access.")
    with tabs[4]:
        st.dataframe(X.q3_missingness(q3, dim), hide_index=True)
        st.caption("Groups surveyed with different form versions can have very different answer rates for optional "
                   "questions; compare those questions only where they were asked.")
    if dim == "county":
        with tabs[5]:
            comp = q3["composition_check"]
            if not comp.get("available"):
                st.info("Role information is needed for this check.")
            else:
                big = [m for m in comp["county_role_mix"] if m["n"] >= pol["min_compare"]]
                st.markdown("**Share of respondents who are students**, overall "
                            f"{comp['overall_mix_pct'].get('student')}%: "
                            + ", ".join(f"{m['county']} {m['student_pct']}%" for m in big))
                if obj:
                    st.plotly_chart(CH.composition_dumbbell(X.q3_composition(q3, obj),
                                                            "Observed vs adjusted to the overall student mix"),
                                    key="q3_comp")
                st.caption(comp["method"])
                if comp.get("not_standardizable"):
                    st.warning("Cannot be adjusted (too few respondents in a role group): "
                               + ", ".join(comp["not_standardizable"]) + ". Their differences cannot be separated "
                               "from their audience mix.")
