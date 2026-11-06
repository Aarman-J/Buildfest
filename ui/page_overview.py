"""Executive Overview: the main business question answered in about 30 seconds."""

from __future__ import annotations

import streamlit as st

from ui import charts as CH
from ui import components as C
from ui import transforms as X

SHORT_QUESTION = ("How can EveryMind use its 988 training feedback to evaluate training effectiveness, find gaps across "
                  "communities, and keep improving future trainings?")


def _go(state, page: str) -> None:
    state["nav"] = page


def render(state, ready: bool) -> None:
    if not ready:
        C.hero("EveryMind · Understanding 988 training", "Turn training feedback into better trainings",
               SHORT_QUESTION, ["Upload a survey", "Automatic cleaning", "Privacy-safe", "Evidence-based actions"])
        st.button("Upload a survey to begin →", type="primary", on_click=_go, args=(state, "Upload & Data Quality"))
        return

    results, report = state["results"], state["report"]
    k = X.overview_kpis(results, report)
    chips = [f"{k['responses']:,} survey responses", "Small groups protected", "No comments shown"]
    if state.get("demo_loaded"):
        chips.insert(0, "Demo data · BuildFest 2025–26 sample")
    C.hero("Executive overview", "Is EveryMind's 988 training working, and what should change next?",
           SHORT_QUESTION, chips)

    c1, c2, c3, c4 = st.columns(4)
    C.kpi(c1, f"{k['responses']:,}", "Survey responses", f"{k['paired_n']:,} answered both before & after", "🧾")
    if k["largest_gain"]:
        C.kpi(c2, f"+{k['largest_gain']['pp']:.0f} points", "Largest confidence increase",
              f"{k['largest_gain']['label']} (percentage points)", "🚀")
    if k["largest_gap"]:
        g = k["largest_gap"]
        C.kpi(c3, f"{g['pct']}%", "Largest remaining gap", f"{g['label']}: {g['k']} people not yet confident", "🎯")
    if k["recommend"]:
        C.kpi(c4, f"{k['recommend']['mean']:.1f}/10", "Would recommend the training",
              f"{k['recommend']['promoters']}% gave 9–10", "⭐")

    left, right = st.columns([3, 2], gap="medium")
    with left:
        st.plotly_chart(CH.before_after(X.q1_before_after(results["q1"]),
                                        "% agreeing they can do each learning goal", height=300), key="ov_ba")
        up = sum(1 for o in k["objectives"] if o["change"] > 0)
        C.takeaway(f"**Agreement rose on {up} of {len(k['objectives'])} learning goals**, but participants recalled "
                   "their 'before' ratings after the training, so this is perceived, not proven, change.")
    with right:
        C.section("Key takeaways")
        for icon, title, text in X.overview_takeaways(results, report):
            st.markdown(f"**{icon} {title}**  \n{text}")

    priorities = results["recommendations"].get("priorities", [])
    if priorities:
        C.section("Priority actions", "Each links evidence → action → who → how progress is measured")
        for i in range(0, len(priorities), 2):
            cols = st.columns(2, gap="medium")
            for col, p in zip(cols, priorities[i:i + 2]):
                with col:
                    C.priority_card(p, compact=True)
            st.markdown("")
    b1, b2 = st.columns(2)
    b1.button("Explore the findings →", type="primary", on_click=_go, args=(state, "Explore Findings"),
              width="stretch")
    b2.button("Full recommendations & AI insights →", on_click=_go, args=(state, "AI Insights & Recommendations"),
              width="stretch")
