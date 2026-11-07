"""Explore Findings: one tab per supporting question. Primary charts first; technical detail in expanders."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ui import charts as CH
from ui import components as C
from ui import state as S
from ui import theme as T
from ui import transforms as X



def render(state) -> None:
    C.header("Supporting questions", "Explore Findings")
    t1, t2, t3 = st.tabs(["Q1 · Training effectiveness", "Q2 · Experiences & relationships",
                          "Q3 · Community differences"])
    with t1:
        _q1(state)
    with t2:
        _q2(state)
    with t3:
        _q3(state)


# ---------------------------------------------------------------------------
# Q1
# ---------------------------------------------------------------------------

def _q1(state) -> None:
    C.question("q1")
    full = state["results"]["q1"]
    if not full.get("available"):
        st.warning("No before/after confidence questions were found in this dataset, so Q1 cannot run.")
        return
    st.caption("'Before' ratings were recalled by participants after the training, so changes are self-reported "
               "perceptions, not proof that the training caused them. 'Agree' = Agree or Strongly agree.")
    if full.get("objectives_missing"):
        st.info("Not available in this upload (question pair incomplete): " + ", ".join(full["objectives_missing"]))

    opts = X.objective_options(full)
    labels = dict(opts)
    months = X.available_months(full)
    chosen = st.pills("Learning goals", [k for k, _ in opts], selection_mode="multi", default=[k for k, _ in opts],
                      format_func=labels.get, key="q1_objs") or [k for k, _ in opts]
    period = None
    if st.toggle("Limit to a training period", value=False, key="q1_period_on", disabled=len(months) < 2,
                 help="Uses participant-reported training dates only; responses without a usable training date "
                      "are left out. Survey completion dates are never used instead."):
        start, end = st.select_slider("Training months", options=months, value=(months[0], months[-1]),
                                      key="q1_period")
        period = X.months_in_range(months, start, end)
    q1 = S.q1_for_period(state, period)
    undated = full["trend"].get("rows_without_valid_training_date", 0)
    st.caption(f"Showing {q1['n_rows']:,} responses"
               + (f" with training dates {period[0]} to {period[-1]}." if period
                  else f" ({undated} have no usable training date and appear only outside the monthly trend)."))
    if not X.objective_options(q1):
        st.warning("Fewer than 10 paired answers in this period; results are hidden.")
        return

    a, b = st.columns(2, gap="medium")
    with a:
        st.plotly_chart(CH.before_after(X.q1_before_after(q1, chosen), "1 · % agreeing before vs after",
                                        height=320), key="q1_ba")
        best = max((o for o in q1["objectives"] if o["key"] in chosen and not o.get("suppressed")),
                   key=lambda o: o["agree_pp_change"], default=None)
        if best:
            C.takeaway(f"**Biggest reported increase: {best['label'].lower()}**, {best['pre_agree']['pct']}% → "
                       f"{best['post_agree']['pct']}% ({best['agree_pp_change']:+} percentage points).")
    with b:
        gaps = X.q1_gaps(q1, chosen).rename(columns={"component": "cat"})
        st.plotly_chart(CH.stacked_share(gaps, "2 · Still not confident after training", "cat", T.GAP, list(T.GAP),
                                         height=320), key="q1_gap")
        worst = max((o for o in q1["objectives"] if o["key"] in chosen and not o.get("suppressed")),
                    key=lambda o: o["remaining_gap"]["not_agree_after"]["pct"], default=None)
        if worst:
            g = worst["remaining_gap"]["not_agree_after"]
            C.takeaway(f"**Largest gap: {worst['label'].lower()}**, {g['pct']}% ({g['k']} people) not yet "
                       "confident.")

    agree = X.q1_agreement(q1, chosen).rename(columns={"category": "cat"})
    st.plotly_chart(CH.stacked_share(agree, "3 · Where each participant ended up, relative to 'Agree'", "cat",
                                     T.AGREEMENT, list(T.AGREEMENT), height=300), key="q1_agree")
    rates = X.q1_reached_rates(q1, chosen)
    if not rates.empty:
        txt = "; ".join(f"{r['Learning objective'].lower()}: {r['Reached-agreement rate']} of {r['Started below Agree']}"
                        for _, r in rates.iterrows())
        C.takeaway(f"**Of people who started below 'Agree', how many reached it?** {txt}.")

    t = q1["trend"]
    if t.get("available") and t.get("months"):
        trend = X.q1_trend(q1, chosen)
        st.plotly_chart(CH.trend_lines(trend, "post_agree_pct", "4 · % agreeing after training, by training month",
                                       "% agreeing after"), key="q1_trend")
        st.plotly_chart(CH.month_counts(X.q1_month_counts(q1), "Responses per training month (sample size)"),
                        key="q1_months")
        C.takeaway("**Read months with few responses cautiously.** Grey bars (<10) are hidden; gold bars and open "
                   "markers (<30) can be driven by a single session.")

    with st.expander("Advanced details: numeric change, transitions, uncertainty and sensitivity"):
        st.plotly_chart(CH.pp_change(X.q1_pp_change(q1, chosen), "Change in % agreeing (percentage points)"),
                        key="q1_pp")
        st.plotly_chart(CH.stacked_share(X.q1_change(q1, chosen).rename(columns={"category": "cat"}),
                                         "Numeric rating change (any step up or down the 5-point scale)", "cat",
                                         T.CHANGE, ["Improved", "Unchanged", "Declined"]), key="q1_chg")
        st.caption("Numeric change differs from reaching 'Agree': Disagree → Neutral is an improvement that does not "
                   "reach agreement; Strongly agree → Agree is a decline that still maintains agreement.")
        st.dataframe(X.q1_reached_rates(q1, chosen), hide_index=True)
        key = st.selectbox("Answer-by-answer transitions for", chosen, format_func=labels.get, key="q1_tr_obj")
        pivot = X.q1_transition(q1, key)
        if pivot is not None:
            st.plotly_chart(CH.transition_heatmap(pivot, f"Before → after answers: {labels[key]}"), key="q1_heat")
        st.markdown("**Summary table with 95% confidence intervals**")
        st.dataframe(X.q1_summary_table(q1, chosen), hide_index=True)
        if full.get("sensitivity"):
            s = full["sensitivity"]
            st.markdown(f"**Sensitivity check:** excluding possibly reversed, very fast or duplicate responses moves "
                        f"headline figures by at most {s['max_shift_pp']} percentage points "
                        f"({'robust' if s['robust'] else 'not robust'}).")
            st.dataframe(X.sensitivity_table(full), hide_index=True)
        if t.get("available"):
            miss = X.q1_trend(q1, chosen).drop_duplicates("month")[["month", "n_rows", "experience_answered_pct",
                                                                    "call_988_answered_pct"]]
            st.markdown("**Optional questions answered per month**")
            st.dataframe(miss.rename(columns={"month": "Month", "n_rows": "Responses",
                                              "experience_answered_pct": "Experience items answered %",
                                              "call_988_answered_pct": "Call-988 answered %"}), hide_index=True)


# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------

def _q2(state) -> None:
    C.question("q2")
    q2, q3 = state["results"]["q2"], state["results"]["q3"]

    summ = X.q2_experience_summary(q2)
    if summ.empty:
        st.info("The presenter and organization questions were not collected in this dataset.")
    else:
        st.plotly_chart(CH.likert_stacked(X.q2_experience_dist(q2), "1 · How participants rated the training "
                                          "experience"), key="q2_lik")
        low = summ.sort_values("Agree %").iloc[0]
        C.takeaway(f"**Lowest-rated: {low['Item'].lower()}** ({low['Agree %']}% agree, n = {low['Valid n']:,}). "
                   "The other items are rated slightly higher.")
        norm = state["report"].get("standardization", {}).get("experience_labels_normalized")
        if norm:
            st.caption("A few answers used the labels 'Very agree' (counted as Strongly agree) or 'Satisfied' "
                       "(counted as Agree). Both count as agreeing either way; the original text is kept.")

    st.markdown("#### 2 · Would participants recommend the training?")
    r = q2.get("recommendation", {})
    aud = (q3.get("audience") or {}).get("recommend") or {}
    if r.get("summary"):
        c = st.columns(4)
        C.kpi(c[0], f"{r['summary']['mean']:.1f}/10", "Everyone", f"n = {r['summary']['n']:,}", "⭐")
        C.kpi(c[1], f"{r['promoters_9_10']['pct']}%", "Gave 9–10", "Very likely to recommend", "👍")
        if aud.get("student_mean") is not None:
            C.kpi(c[2], f"{aud['student_mean']:.1f}/10", "Students", f"n = {aud['student_n']:,}", "🎓")
        if aud.get("others_mean") is not None:
            C.kpi(c[3], f"{aud['others_mean']:.1f}/10", "Staff, caregivers & community", f"n = {aud['others_n']:,}", "🧑‍🏫")
        if aud.get("student_mean") is not None and aud.get("others_mean") is not None:
            d = aud["student_mean"] - aud["others_mean"]
            word = "less" if d <= -0.5 else "more" if d >= 0.5 else "about as"
            C.takeaway(f"**Students are {word} likely to recommend the training** ({aud['student_mean']:.1f} vs "
                       f"{aud['others_mean']:.1f} out of 10 for other participants). They also attend different "
                       "sessions, so this is a difference to investigate, not a cause.")
    else:
        st.info("Likelihood to recommend was not collected in this dataset.")

    st.markdown("#### 3 · How likely are participants to call 988?")
    cc = q2.get("call_988", {})
    cov = cc.get("coverage", {})
    if not cov.get("collected"):
        st.info("This dataset does not include the likelihood-to-call-988 question.")
    else:
        st.warning(f"**Only {cov['valid']:,} of {cov['rows']:,} respondents ({cov['valid_pct']}%) answered this "
                   "question.** Missing answers could mean the question wasn't shown on some survey forms or was "
                   "skipped; the data doesn't record which. Results describe only those who answered.")
        if cc.get("summary"):
            c = st.columns(3)
            C.kpi(c[0], f"{cc['summary']['mean']:.1f}/10", "Average (those who answered)",
                  f"n = {cc['summary']['n']:,}", "📞")
            C.kpi(c[1], f"{cc['very_likely_9_10']['pct']}%", "Answered 9–10", "Very likely to call", "✅")
            C.kpi(c[2], f"{cc['unlikely_0_4']['pct']}%", "Answered 0–4", "Unlikely to call", "⚠️")

    st.markdown("#### 4 · What participants wrote")
    field = st.segmented_control("Comment question", ["fb_improvements", "fb_most_valuable"],
                                 default="fb_improvements", key="q2_fb",
                                 format_func={"fb_most_valuable": "Most valuable thing learned",
                                              "fb_improvements": "How to improve the training"}.get) \
        or "fb_improvements"
    themes, types, fb = X.q2_themes(q2, field)
    if themes.empty:
        st.info("No comments for this question in this dataset.")
    else:
        top = themes[themes["theme"] != "other_unclassified"].sort_values("n", ascending=False).head(7)
        st.plotly_chart(CH.theme_bars(top, f"Most common themes · {fb['comments']:,} comments"),
                        key="q2_themes")
        lead = top.iloc[0]
        C.takeaway(f"**Most common theme: {lead['label'].lower()}** ({lead['pct_of_comments']:.0f}% of comments).")

    with st.expander("Advanced details: correlations, distributions and comment types"):
        st.markdown("**Associations between measures (Spearman ρ, exploratory, not causal)**")
        rel = X.q2_relationships(q2)
        if not rel.empty:
            fams = list(dict.fromkeys(rel["family_key"]))
            pick = st.multiselect("Relationships", fams, default=[f for f in fams if f in (
                "experience_vs_post_confidence", "post_confidence_vs_call_988")] or fams[:2],
                format_func=X.FAMILY_LABELS.get, key="q2_fams")
            st.plotly_chart(CH.correlation_bars(X.q2_relationships(q2, pick), "Associations between measures"),
                            key="q2_corr")
            st.caption("|ρ| < 0.1 negligible · 0.1–0.3 weak · 0.3–0.5 moderate · ≥ 0.5 strong. n = respondents with "
                       "both answers.")
        sg = X.q2_sat_gap(q2)
        if not sg.empty:
            d = sg[sg["group"] == "highly_satisfied"]
            st.plotly_chart(CH.grouped_share(d, "Highly satisfied participants can still lack confidence "
                                             "(% not agreeing after)", "objective", "who",
                                             {"In group": T.INDIGO, "Everyone else": T.SLATE}), key="q2_satgap")
        if r.get("summary"):
            st.plotly_chart(CH.rating_bars(X.rating_dist(r), "Likelihood to recommend: full 0–10 distribution"),
                            key="q2_rec")
        if cc.get("summary"):
            st.plotly_chart(CH.rating_bars(X.rating_dist(cc), "Likelihood to call 988: those who answered"),
                            key="q2_call")
        if cc.get("coverage_by_survey_version"):
            st.markdown("**Answer rate by inferred survey version** (a heuristic, not confirmed form history)")
            st.dataframe(cc["coverage_by_survey_version"], hide_index=True)
        if not summ.empty:
            st.dataframe(summ, hide_index=True)
        if not types.empty:
            t = types.rename(columns={"type": "Type", "n": "Comments", "pct_of_comments": "% of comments"})
            t["Type"] = t["Type"].map({"praise": "Praise", "improvement_suggestion": "Improvement suggestion",
                                       "neutral": "Neutral / factual", "unclear": "Unclear / non-answer"})
            st.markdown("**Comment types**")
            st.dataframe(t, hide_index=True)


# ---------------------------------------------------------------------------
# Q3
# ---------------------------------------------------------------------------

DIMS = {"county": "County", "role": "Participant role", "language": "Survey language"}


def _q3(state) -> None:
    C.question("q3")
    q3, q1 = state["results"]["q3"], state["results"]["q1"]
    dims = [d for d in DIMS if d in X.q3_dimensions(q3)]
    pol = q3["policy"]
    st.caption(f"Groups need at least {pol['min_compare']} responses to be compared ({pol['min_compare']}–"
               f"{pol['exploratory_below'] - 1} are labelled exploratory). Smaller groups are listed but not "
               "compared, and a small group is not evidence of poor access.")
    objs = X.objective_options(q1)
    if not dims or not objs:
        st.warning("No county, role or language columns (or no confidence questions) were found, so group "
                   "comparisons cannot run.")
        return
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    dim = c1.segmented_control("Compare by", dims, default=dims[0], format_func=DIMS.get, key="q3_dim") or dims[0]
    obj = c2.selectbox("Learning goal", [k for k, _ in objs], format_func=dict(objs).get, key="q3_obj")

    g = q3["groupings"][dim]
    if g.get("overlapping"):
        st.info(f"Participants could pick more than one role, so a person can appear in several groups "
                f"({g['membership_total']:,} memberships from {g['rows_total']:,} people).")
    cmp = X.q3_compare(q3, dim, obj)
    st.plotly_chart(CH.grouped_share(cmp, f"1 · Confident vs not yet confident: {dict(objs)[obj].lower()}",
                                     "group", "measure", T.COMPARE, height=360), key="q3_cmp")
    small = X.q3_not_compared(q3, dim)
    if small:
        st.caption("Too few responses to compare: " + ", ".join(small) + ".")
    conf = cmp[cmp["measure"] == "Confident after training"] if not cmp.empty else cmp
    if len(conf) >= 2:
        lo, hi = conf.loc[conf["pct"].idxmin()], conf.loc[conf["pct"].idxmax()]
        C.takeaway(f"**After training, the confident share ranges from {lo['pct']}% ({lo['group']}) to "
                   f"{hi['pct']}% ({hi['group']}).** Groups differ in session and audience, so treat gaps as "
                   "questions to investigate, not rankings.")

    st.markdown("#### 2 · Students vs other participants")
    aud = X.audience_frame(q3, obj)
    if aud.empty:
        st.info("Role information is needed for this comparison.")
    else:
        st.plotly_chart(CH.grouped_share(aud, f"Students vs staff, caregivers & community members (confidence: "
                                         f"{dict(objs)[obj].lower()})", "measure",
                                         "group", T.AUDIENCE, height=360), key="q3_aud")
        piv = aud.pivot_table(index="measure", columns="group", values="pct")
        if piv.shape[1] == 2:
            diff = (piv.iloc[:, 0] - piv.iloc[:, 1]).abs()
            m = diff.idxmax()
            C.takeaway(f"**Largest audience difference: {m.lower()}** ({piv.columns[0]} {piv.loc[m].iloc[0]}% vs "
                       f"{piv.columns[1]} {piv.loc[m].iloc[1]}%). Students = anyone who selected Student, "
                       "including multi-role respondents.")

    comp = q3.get("composition_check", {})
    if dim == "county" and comp.get("available"):
        big = [m for m in comp["county_role_mix"] if m["n"] >= pol["min_compare"]]
        st.warning("**County results reflect who attended.** Share of respondents who are students: "
                   + ", ".join(f"{m['county']} {m['student_pct']}%" for m in big)
                   + f" (overall {comp['overall_mix_pct'].get('student')}%). Don't rank counties on these numbers.")

    with st.expander("Advanced details: full group table, missing answers, audience adjustment"):
        st.dataframe(X.q3_group_table(q3, dim), hide_index=True)
        sig = X.q3_signals(q3, dim)
        if not sig.empty:
            st.markdown("**Differences flagged for follow-up** (descriptive; investigate before acting)")
            st.dataframe(sig, hide_index=True)
        st.markdown("**Missing answers and survey versions by group**")
        st.dataframe(X.q3_missingness(q3, dim), hide_index=True)
        if dim == "county" and comp.get("available"):
            st.plotly_chart(CH.composition_dumbbell(X.q3_composition(q3, obj),
                                                    "Observed vs adjusted to the overall student mix"), key="q3_comp")
            st.caption(comp["method"])
            if comp.get("not_standardizable"):
                st.caption("Cannot be adjusted (fewer than 10 in a role group): "
                           + ", ".join(comp["not_standardizable"]) + ".")
        if "role_primary" in X.q3_dimensions(q3):
            st.markdown("**Primary role (one group per person, for mutually exclusive comparisons)**")
            st.dataframe(X.q3_group_table(q3, "role_primary"), hide_index=True)
