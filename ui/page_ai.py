"""AI Insights & Recommendations page.

The paid AI call (Gemini or OpenAI) happens only after an explicit button click, only when the server-side
configuration enables it and the request passes `access_check`, and at most once per evidence
fingerprint per session (plus per-session and per-day caps).
"""

from __future__ import annotations

import json
import os

import streamlit as st

from src.ai_recommendations import (
    AISettings, RateLimiter, access_check, build_ai_payload, client_identity, generate_ai_recommendations,
    get_api_key, load_ai_settings, payload_fingerprint,
)
from ui import components as C
from ui import state as S


def read_secrets() -> dict:
    try:
        return st.secrets.to_dict()
    except Exception:  # noqa: BLE001 - no secrets file configured
        return {}


@st.cache_resource
def _rate_limiter(settings: AISettings) -> RateLimiter:
    """Process-wide limiter shared by all sessions and tabs (holds hashed visitor IDs + timestamps only)."""
    return RateLimiter(settings)


def _request_identity() -> tuple[str | None, bool, str | None, str]:
    host = ip = fwd = None
    try:
        host = st.context.headers.get("Host")
        fwd = st.context.headers.get("X-Forwarded-For")
        ip = st.context.ip_address
    except Exception:  # noqa: BLE001
        pass
    logged_in, email = False, None
    try:
        logged_in = bool(st.user.is_logged_in)
        email = st.user.get("email") if logged_in else None
    except Exception:  # noqa: BLE001 - auth not configured
        pass
    return host, logged_in, email, client_identity(email, ip, fwd)


def render(state) -> None:
    C.header("Act on the findings", "AI Insights & Recommendations", "main")
    results, report = state["results"], state["report"]
    secrets = read_secrets()
    settings: AISettings = load_ai_settings(secrets, os.environ)
    api_key = get_api_key(secrets, os.environ, settings.provider)
    provider = "Google Gemini" if settings.provider == "gemini" else "OpenAI"
    host, logged_in, email, visitor = _request_identity()
    allowed, reason = access_check(settings, host, logged_in, email)

    payload = build_ai_payload(results, report)
    fp = payload_fingerprint(payload, settings.model)

    priorities = results["recommendations"].get("priorities", [])
    rules = results["recommendations"]["recommendations"]
    C.section("1 · Priority recommendations", "Verified: computed by documented rules from this dataset")
    if priorities:
        for i in range(0, len(priorities), 2):
            cols = st.columns(2, gap="medium")
            for col, p in zip(cols, priorities[i:i + 2]):
                with col:
                    C.priority_card(p)
            st.markdown("")
    else:
        st.info("No priority rule was triggered by this dataset.")
    with st.expander(f"All {len(rules)} underlying rule-based recommendations (each linked to its evidence)"):
        for r in rules:
            C.rule_recommendation(r)

    C.section("2 · AI-drafted suggestions (optional)", f"Advisory: drafted by {provider}, then checked by Python")
    with st.container(border=True):
        st.markdown("The AI drafts **additional, advisory** recommendations from a compact summary of verified aggregate "
                    "results. It never receives individual responses, IDs or comments, and every suggestion must cite "
                    "evidence IDs from that summary. Suggestions that fail automated checks are rejected, not shown.")
        cached = S.ai_result_for_current(state, fp)
        calls = state.get("ai_calls", 0)
        if not allowed:
            st.info(reason + " The verified priority recommendations above are always available.",
                    icon=":material/lock:")
        elif not api_key:
            st.warning(f"AI generation is enabled, but no {provider} API key is configured on the server. Rule-based "
                       "recommendations above are still available.")
        else:
            st.caption(f"Model: `{settings.model}` ({provider}) · up to {settings.max_recommendations} recommendations · "
                       f"output limit {settings.max_output_tokens:,} tokens · timeout {settings.timeout_seconds:g}s · "
                       f"{calls}/{settings.max_calls_per_session} requests used this session.")
            has_good = cached is not None and cached["status"] in ("ok", "partial")
            label = "Regenerate AI recommendations" if has_good else "Generate AI Recommendations"
            disabled = state.get("ai_running", False) or calls >= settings.max_calls_per_session
            if calls >= settings.max_calls_per_session:
                st.caption("Session limit reached. Reload the page to start a new session.")
            confirm = True
            if has_good:
                confirm = st.checkbox("I want a new draft (uses another paid request)", key="ai_regen_ok")
            limiter = _rate_limiter(settings)
            u = limiter.usage(visitor)
            st.caption(f"Rate limits: you {u['user_hour']}/{settings.max_calls_per_user_hour} this hour, "
                       f"{u['user_day']}/{settings.max_calls_per_user_day} today · server "
                       f"{u['server_hour']}/{settings.max_calls_per_hour} this hour, "
                       f"{u['server_day']}/{settings.max_calls_per_day} today · "
                       f"{settings.cooldown_seconds}s between requests.")
            if st.button(label, type="primary" if not has_good else "secondary",
                         disabled=disabled or not confirm, key="ai_go"):
                ok, msg, retry = limiter.acquire(visitor)
                if not ok:
                    wait = f"{retry // 60} min" if retry >= 120 else f"{retry} s"
                    st.error(f"{msg} Try again in about {wait}. Rule-based recommendations remain available.")
                else:
                    state["ai_running"] = True
                    state["ai_calls"] = calls + 1
                    try:
                        with st.spinner("Drafting and validating recommendations…"):
                            res = generate_ai_recommendations(payload, settings, api_key,
                                                              dataset_id=state["dataset_id"]).to_dict()
                    finally:
                        state["ai_running"] = False
                        limiter.release()
                    S.store_ai_result(state, res)
                    cached = res
        if cached:
            if cached["status"] in ("ok", "partial"):
                st.success(cached["message"] + f" Generated {cached['created_at']} for this dataset.")
                if cached.get("overall_note"):
                    st.markdown(f"> {cached['overall_note']}")
                st.caption("AI-drafted recommendations are advisory and require human review. They are not verified "
                           "findings; check the cited evidence for each.")
                for i, r in enumerate(cached["recommendations"], 1):
                    C.ai_recommendation(i, r)
            else:
                st.error(cached["message"] + " The verified priority recommendations above are unaffected.")
            if cached.get("rejected"):
                with st.expander(f"{len(cached['rejected'])} AI suggestion(s) rejected by validation"):
                    for x in cached["rejected"]:
                        st.markdown(f"- **{x['title']}** – {'; '.join(x['reasons'])}")

    with st.expander("How the AI works: what is sent, how outputs are checked, cost and access controls"):
        st.markdown(f"""
- **Model:** `{settings.model}` ({provider}), JSON output constrained by a strict schema (Pydantic) and re-validated in Python.
- **Sent:** the aggregate evidence list below (percentages, counts, confidence intervals, correlation summaries,
  data-quality counts) plus the IDs and actions of the rule-based recommendations.
- **Never sent:** individual responses, response IDs, free-text comments, raw values, or any group with fewer than
  {results['config']['min_compare']} respondents.
- **Validation:** each suggestion must cite existing evidence IDs; percentages and counts in its title or issue must
  appear in the cited evidence; causal claims ("the training caused…", "proves") are rejected; at most
  {settings.max_recommendations} are accepted.
- **Cost controls:** server-side enable flag (off by default), access check, button-only generation, one request per
  evidence fingerprint unless you confirm a regeneration, and rate limits that survive page reloads:
  {settings.max_calls_per_user_hour}/hour and {settings.max_calls_per_user_day}/day per visitor (signed-in email or IP),
  {settings.max_calls_per_hour}/hour and {settings.max_calls_per_day}/day for the whole server,
  {settings.cooldown_seconds}s cooldown, {settings.max_concurrent} request at a time, plus
  {settings.max_calls_per_session} per browser session, an output-token cap and a timeout.
- **Status here:** {"enabled" if settings.enabled else "disabled"} · access mode `{settings.access}` · {reason}
""")
        st.caption(f"Evidence fingerprint: {fp}. Full documentation: docs/ai_recommendations.md")
        with st.expander("View the exact aggregate summary that would be sent"):
            st.code(json.dumps(payload, indent=2)[:60000], language="json")
