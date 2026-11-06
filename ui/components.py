"""Reusable Streamlit components (HTML is escaped; styling lives in ui/theme.py)."""

from __future__ import annotations

import html
import json

import streamlit as st

BUSINESS_QUESTION = ("How can EveryMind use its 988 training feedback data to develop an actionable solution for "
                     "evaluating training effectiveness, identifying gaps across communities, and continuously "
                     "improving future trainings?")
QUESTIONS = {
    "q1": ("Supporting Question 1 · Training effectiveness",
           "How can EveryMind better evaluate training effectiveness, identify meaningful patterns in participant "
           "responses, and monitor outcomes over time?"),
    "q2": ("Supporting Question 2 · Trends and relationships",
           "What trends, relationships, or differences in the survey data provide insights into participants' "
           "training experiences and outcomes?"),
    "q3": ("Supporting Question 3 · Community differences",
           "How do training experiences and outcomes differ across communities and participant populations, and "
           "what might these differences reveal about opportunities to improve accessibility, equity, and "
           "community impact?"),
    "main": ("Main business question", BUSINESS_QUESTION),
}
RETRO_CAVEAT = ("**How to read this:** 'Before' ratings were recalled by participants *after* the training "
                "(retrospective pre/post). Changes show self-reported, perceived change – they are not proof that the "
                "training caused it. 'Agree' means *Agree* or *Strongly agree*.")
CATEGORY_LABEL = {"verified_finding": "Verified finding", "exploratory_association": "Exploratory association",
                  "data_quality_warning": "Data-quality warning", "follow_up_investigation": "Follow-up investigation"}

e = html.escape


def _html(s: str) -> None:
    st.markdown(s, unsafe_allow_html=True)


def hero(eyebrow: str, title: str, text: str, chips: list[str] = ()) -> None:
    chip_html = "".join(f'<span class="em-chip">{e(c)}</span>' for c in chips)
    _html(f'<div class="em-hero"><div class="eyebrow">{e(eyebrow)}</div><h1>{e(title)}</h1><p>{e(text)}</p>'
          f'<div class="em-chips">{chip_html}</div></div>')


def header(eyebrow: str, title: str, question_key: str | None = None) -> None:
    _html(f'<div class="em-header"><div class="eyebrow">{e(eyebrow)}</div><h1>{e(title)}</h1></div>')
    if question_key:
        question(question_key)


def section(title: str, sub: str = "") -> None:
    _html(f'<div class="em-section"><div class="h">{e(title)}</div><span>{e(sub)}</span></div>')


def question(key: str) -> None:
    label, text = QUESTIONS[key]
    _html(f'<div class="em-question"><div class="em-label">{e(label)}</div>{e(text)}</div>')


def kpi(col, value: str, label: str, sub: str = "", icon: str = "📊") -> None:
    col.markdown(f'<div class="em-kpi"><div class="top"><div class="ico">{icon}</div></div>'
                 f'<div class="v">{e(value)}</div><div class="l">{e(label)}</div><div class="s">{e(sub)}</div></div>',
                 unsafe_allow_html=True)


def _bar(pct: float, color: str) -> str:
    return f'<div class="em-bar"><div style="width:{max(0, min(100, pct or 0)):.1f}%;background:{color}"></div></div>'


def objective_card(col, o: dict) -> None:
    """Before -> after card for one learning objective (o from transforms.overview_kpis)."""
    col.markdown(
        f'<div class="em-card em-obj"><div class="name">{e(o["label"])}</div>'
        f'<div class="nums"><span class="before">{o["pre"]:.0f}%</span><span class="arrow">→</span>'
        f'<span class="after">{o["post"]:.0f}%</span>'
        f'<span class="em-pill up">{o["change"]:+.0f} pp</span></div>'
        f'<div class="em-bar-label"><span>Before (recalled)</span><span>{o["pre"]}%</span></div>{_bar(o["pre"], "#CBD5E1")}'
        f'<div class="em-bar-label"><span>After training</span><span>{o["post"]}%</span></div>{_bar(o["post"], "#4F46E5")}'
        f'<div style="margin-top:.6rem"><span class="em-pill warn">{o["gap"]}% still not confident</span> '
        f'<span class="em-small">({o["gap_k"]} of {o["n"]:,})</span></div></div>', unsafe_allow_html=True)


def insight(col, icon: str, title: str, body: str) -> None:
    col.markdown(f'<div class="em-card"><div class="em-insight"><div class="ico">{icon}</div><div>'
                 f'<h4>{e(title)}</h4><p style="margin:0">{e(body)}</p></div></div></div>', unsafe_allow_html=True)


def bullet_card(title: str, items: list[str], icon: str = "") -> None:
    lis = "".join(f"<li>{e(i)}</li>" for i in items)
    _html(f'<div class="em-card"><h4>{icon} {e(title)}</h4><ul style="margin:.2rem 0 0 1.1rem;padding:0">{lis}</ul></div>')


def tag(text: str, cls: str) -> str:
    return f'<span class="em-tag {cls}">{e(text)}</span>'


def rec_card(col, r: dict) -> None:
    """Compact card for a rule-based recommendation."""
    col.markdown(
        f'<div class="em-rec {e(r["priority"])}">'
        + tag(r["priority"].title() + " priority", r["priority"]) + tag(CATEGORY_LABEL.get(r["category"], r["category"]), "cat")
        + f'<div class="t">{e(r["action"])}</div><div class="f">{e(r["finding"])}</div>'
        f'<div class="row"><b>How:</b> {e(r["implementation"])}</div>'
        f'<div class="row"><b>Success measure:</b> {e(r["success_metric"])}</div></div>', unsafe_allow_html=True)


def rule_recommendation(r: dict, expanded: bool = False) -> None:
    head = f"{r['id']} · {r['action']}"
    with st.expander(head, expanded=expanded):
        _html(tag(r["priority"].title() + " priority", r["priority"]) +
              tag(CATEGORY_LABEL.get(r["category"], r["category"]), "cat") + tag("Rule-based", "low"))
        st.markdown(f"**Finding.** {r['finding']}")
        st.markdown(f"**Proposed action.** {r['action']}")
        st.markdown(f"**Suggested implementation.** {r['implementation']}")
        st.markdown(f"**Success measure.** {r['success_metric']}")
        cav = [c for c in r.get("caveats", []) if c]
        if cav:
            st.caption("Caveats: " + " ".join(cav))
        with st.popover("Supporting evidence (data)"):
            st.code(json.dumps(r["evidence"], indent=2, default=str)[:4000], language="json")
            st.caption(f"Rule: {r['rule']} (see docs/analysis_methods.md)")


def ai_recommendation(i: int, r: dict) -> None:
    with st.container(border=True):
        _html(tag("AI-drafted · advisory", "ai") + tag(r["priority"].title() + " priority", r["priority"]))
        st.markdown(f"#### AI-{i}. {r['title']}")
        st.markdown(f"**Issue.** {r['issue']}")
        st.markdown(f"**Proposed action.** {r['action']}")
        st.markdown("**Implementation steps.**\n" + "\n".join(f"{j}. {s}" for j, s in enumerate(r["implementation_steps"], 1)))
        c1, c2 = st.columns(2)
        c1.markdown(f"**Responsible role.** {r['responsible_role']}")
        c2.markdown(f"**Success indicator.** {r['success_indicator']}")
        st.markdown(f"**Limitations.** {r['limitations']}")
        with st.expander(f"Evidence cited ({len(r['evidence'])})"):
            for ev in r["evidence"]:
                st.markdown(f"- `{ev['id']}` · *{CATEGORY_LABEL.get(ev['category'], ev['category'])}* – {ev['statement']}")


def need_data() -> None:
    st.info("No dataset is loaded yet. Go to **Upload & Data Quality** to upload a survey export "
            "(or load the sample dataset if it is available).", icon=":material/upload_file:")


def small_print(text: str) -> None:
    _html(f'<div class="em-small">{e(text)}</div>')


def takeaway(text: str) -> None:
    """One-sentence takeaway under a chart. Text may contain **bold** markers only."""
    safe = e(text)
    parts = safe.split("**")
    html_text = "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))
    _html(f'<div class="em-takeaway">👉 {html_text}</div>')


PRIORITY_BADGE = {"A": "high", "B": "medium", "C": "high", "D": "medium"}


def priority_card(p: dict, compact: bool = False) -> None:
    """Consolidated recommendation: evidence -> action -> implementation -> role -> measure -> limitation."""
    cls = PRIORITY_BADGE.get(p["key"], "medium")
    ev = "".join(f"<li>{e(x)}</li>" for x in p["evidence"])
    body = (f'<div class="em-rec {cls}">' + tag(f"Priority {p['key']}", cls) + tag("Rule-based · verified data", "cat")
            + f'<div class="t">{e(p["title"])}</div><div class="f">{e(p["finding"])}</div>'
            f'<div class="row"><b>Evidence</b><ul style="margin:.15rem 0 .3rem 1.1rem;padding:0">{ev}</ul></div>'
            f'<div class="row"><b>Action:</b> {e(p["action"])}</div>')
    if not compact:
        steps = "".join(f"<li>{e(s)}</li>" for s in p["implementation"])
        body += (f'<div class="row"><b>How</b><ol style="margin:.15rem 0 .3rem 1.1rem;padding:0">{steps}</ol></div>'
                 f'<div class="row"><b>Who:</b> {e(p["responsible_role"])}</div>'
                 f'<div class="row"><b>Progress measure:</b> {e(p["success_metric"])}</div>'
                 f'<div class="row"><b>Limitation:</b> {e(p["limitation"])}</div>')
    else:
        body += (f'<div class="row"><b>Who:</b> {e(p["responsible_role"])}</div>'
                 f'<div class="row"><b>Progress measure:</b> {e(p["success_metric"])}</div>')
    _html(body + "</div>")
