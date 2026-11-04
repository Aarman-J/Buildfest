"""Q2 · Trends and relationships page."""

from __future__ import annotations

import streamlit as st

from ui import charts as CH
from ui import components as C
from ui import transforms as X

PROVISIONAL = ("**Provisional:** theme counts come from documented keyword rules and have not yet been validated by "
               "human reviewers. Use them as a guide to read further, not as final figures. Individual comments are "
               "never shown in this dashboard.")


def render(state) -> None:
    C.header("Question 2 of 3", "Trends & Relationships", "q2")
    q2 = state["results"]["q2"]
    tabs = st.tabs(["Training experience", "Likelihood to recommend", "Likelihood to call 988", "Relationships",
                    "Written feedback"])

    with tabs[0]:
        summ = X.q2_experience_summary(q2)
        if summ.empty:
            st.info("The presenter and organization questions were not collected in this dataset.")
        else:
            st.plotly_chart(CH.likert_stacked(X.q2_experience_dist(q2), "Presenter and organization ratings"),
                            key="q2_lik")
            st.dataframe(summ, hide_index=True)
            st.caption("Percentages use valid answers to each item. 'Unusable' answers used labels outside the "
                       "agreement scale (e.g. 'Very agree', 'Satisfied') and are not scored.")

    with tabs[1]:
        r = q2["recommendation"]
        if not r.get("summary"):
            st.info("Likelihood to recommend was not collected (or too few answers).")
        else:
            c = st.columns(4)
            C.kpi(c[0], f"{r['summary']['mean']}", "Mean (0–10)", f"median {r['summary']['median']}, n = {r['summary']['n']:,}")
            C.kpi(c[1], f"{r['promoters_9_10']['pct']}%", "Gave 9–10")
            C.kpi(c[2], f"{r['detractors_0_6']['pct']}%", "Gave 0–6")
            C.kpi(c[3], f"{r['nps']}", "Net Promoter Score", "% 9–10 minus % 0–6 (secondary view)")
            st.plotly_chart(CH.rating_bars(X.rating_dist(r), "Likelihood to recommend the training"), key="q2_rec")
            st.caption(f"{r['coverage']['missing']} respondents did not answer this question.")

    with tabs[2]:
        cc = q2["call_988"]
        if not cc["coverage"]["collected"]:
            st.info("This dataset does not include the likelihood-to-call-988 question.")
        else:
            cov = cc["coverage"]
            st.warning(f"**Asked of only some participants.** {cov['valid']:,} of {cov['rows']:,} respondents "
                       f"({cov['valid_pct']}%) answered, because the question appears only in some survey versions. "
                       "These results describe those respondents only and must not be generalized to all participants.")
            if cc.get("summary"):
                c = st.columns(3)
                C.kpi(c[0], f"{cc['summary']['mean']}", "Mean (0–10)", f"n = {cc['summary']['n']:,}")
                C.kpi(c[1], f"{cc['very_likely_9_10']['pct']}%", "Answered 9–10",
                      f"95% CI {cc['very_likely_9_10']['ci_low']}–{cc['very_likely_9_10']['ci_high']}")
                C.kpi(c[2], f"{cc['unlikely_0_4']['pct']}%", "Answered 0–4")
                st.plotly_chart(CH.rating_bars(X.rating_dist(cc), "Likelihood to call 988 (respondents who were asked)"),
                                key="q2_call")
            if cc.get("coverage_by_survey_version"):
                with st.expander("Who was asked? Answer rate by inferred survey version"):
                    st.dataframe(cc["coverage_by_survey_version"], hide_index=True)

    with tabs[3]:
        st.markdown("Spearman rank correlations measure whether two ratings tend to rise together (ρ from −1 to 1). "
                    "They are **exploratory associations, not causes**: both ratings come from the same form. "
                    "Each bar shows the number of respondents with both answers (n).")
        rel = X.q2_relationships(q2)
        if rel.empty:
            st.info("Not enough paired answers to compute relationships.")
        else:
            fams = list(dict.fromkeys(rel["family_key"]))
            pick = st.multiselect("Relationships to show", fams, default=[f for f in fams if f in (
                "experience_vs_post_confidence", "experience_vs_confidence_change", "post_confidence_vs_call_988")]
                or fams[:2], format_func=X.FAMILY_LABELS.get, key="q2_fams")
            st.plotly_chart(CH.correlation_bars(X.q2_relationships(q2, pick), "Associations between measures"),
                            key="q2_corr")
            st.caption("Guide: |ρ| < 0.1 negligible · 0.1–0.3 weak · 0.3–0.5 moderate · ≥ 0.5 strong. Relationships "
                       "involving likelihood to call 988 use only respondents who were asked that question.")
        sg = X.q2_sat_gap(q2)
        if not sg.empty:
            st.markdown("#### Strong satisfaction does not guarantee confidence")
            grp = st.radio("Satisfied group", list(dict.fromkeys(sg["group"])), horizontal=True, key="q2_satgrp",
                           format_func={"highly_satisfied": "Highly satisfied (experience mean ≥ 4.5)",
                                        "promoters": "Would recommend (9–10)"}.get)
            d = sg[sg["group"] == grp]
            st.plotly_chart(CH.grouped_share(d, "% not agreeing after training", "objective", "who",
                                             {"In group": "#1D3F6E", "Everyone else": "#A3ACB9"}), key="q2_satgap")

    with tabs[4]:
        st.warning(PROVISIONAL)
        field = st.radio("Question", ["fb_most_valuable", "fb_improvements"], horizontal=True, key="q2_fb",
                         format_func={"fb_most_valuable": "Most valuable thing learned",
                                      "fb_improvements": "How to improve the training"}.get)
        themes, types, fb = X.q2_themes(q2, field)
        if themes.empty:
            st.info("No comments for this question in this dataset.")
        else:
            st.markdown(f"{fb['comments']:,} comments ({fb['comment_rate_pct']}% of respondents). "
                        f"Theme rules version {fb['theme_version']}.")
            c1, c2 = st.columns([3, 2])
            c1.plotly_chart(CH.theme_bars(themes, "Themes (PROVISIONAL)"), key="q2_themes")
            types = types.rename(columns={"type": "Type", "n": "Comments", "pct_of_comments": "% of comments"})
            types["Type"] = types["Type"].map({"praise": "Praise", "improvement_suggestion": "Improvement suggestion",
                                               "neutral": "Neutral / factual", "unclear": "Unclear / non-answer"})
            c2.markdown("**Response types (PROVISIONAL)**")
            c2.dataframe(types, hide_index=True)
            c2.caption("Human validation: reviewers label a reproducible, redacted sample "
                       "(`feedback_themes.validation_sample`) and agreement is scored with `score_validation`. "
                       "See docs/analysis_methods.md.")
