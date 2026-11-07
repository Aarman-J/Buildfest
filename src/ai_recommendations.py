"""Optional AI-drafted recommendations (Google Gemini or OpenAI), grounded in verified aggregate results.

This module is deliberately separate from the deterministic statistics engine.
It never sees participant-level data: `build_ai_payload` takes the aggregate
`run_all` results, keeps only adequately sized groups, and assigns every
finding an evidence ID. The model must cite those IDs; `validate_ai_output`
rejects any recommendation with unknown IDs, missing fields, statistics that
do not appear in the cited evidence, or causal claims.

Nothing here reads Streamlit state, so it can be unit-tested with a mock client.
See docs/ai_recommendations.md.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, ValidationError

PROMPT_VERSION = "2026.11-2"
DEFAULT_PROVIDER = "gemini"
DEFAULT_MODELS = {"gemini": "gemini-3.5-flash", "openai": "gpt-5-mini"}
DEFAULT_MODEL = DEFAULT_MODELS[DEFAULT_PROVIDER]
PROVIDERS = tuple(DEFAULT_MODELS)
# Tried in order when the main model is overloaded (503), timing out or rate-limited.
DEFAULT_FALLBACKS = {"gemini": ("gemini-3.1-flash-lite", "gemini-flash-latest"), "openai": ()}

# ---------------------------------------------------------------------------
# Settings and access control (server-side)
# ---------------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AISettings:
    enabled: bool = False                 # paid feature is OFF unless explicitly enabled server-side
    access: str = "allowlist"             # "local" (localhost) | "allowlist" (signed-in emails) | "public" (anyone, rate-limited)
    allowed_emails: tuple = ()
    provider: str = DEFAULT_PROVIDER      # "gemini" | "openai"
    model: str = DEFAULT_MODEL
    fallback_models: tuple = ()          # tried in order if the model is busy/timing out
    max_recommendations: int = 6
    max_output_tokens: int = 6000         # includes reasoning tokens for reasoning models
    reasoning_effort: str = "low"
    timeout_seconds: float = 45.0
    max_retries: int = 1
    max_calls_per_session: int = 3
    max_calls_per_day: int = 30           # whole server (all visitors), rolling 24 h
    max_calls_per_hour: int = 10          # whole server, rolling 60 min
    max_calls_per_user_hour: int = 3      # per visitor (signed-in email, else IP address)
    max_calls_per_user_day: int = 6
    cooldown_seconds: int = 30            # minimum gap between two calls from the same visitor
    max_concurrent: int = 1               # paid requests in flight at once, whole server


def load_ai_settings(secrets: Mapping | None, env: Mapping | None) -> AISettings:
    """Read the [ai] table from Streamlit secrets, falling back to EVERYMIND_AI_* environment variables."""
    secrets = secrets or {}
    env = env or {}
    sec = dict(secrets.get("ai", {}) or {})

    def get(key, default):
        if key in sec:
            return sec[key]
        ev = env.get(f"EVERYMIND_AI_{key.upper()}")
        return ev if ev is not None else default

    def as_bool(v):
        return v if isinstance(v, bool) else str(v).strip().lower() in _TRUE

    emails = get("allowed_emails", ())
    if isinstance(emails, str):
        emails = [e for e in re.split(r"[,\s]+", emails) if e]
    access = str(get("access", "allowlist")).strip().lower()
    provider = str(get("provider", DEFAULT_PROVIDER)).strip().lower()
    provider = provider if provider in PROVIDERS else DEFAULT_PROVIDER
    return AISettings(
        provider=provider,
        enabled=as_bool(get("enabled", False)),
        access=access if access in ("local", "allowlist", "public") else "allowlist",
        allowed_emails=tuple(sorted(e.strip().lower() for e in emails)),
        model=str(get("model", "") or DEFAULT_MODELS[provider]),
        fallback_models=tuple(get("fallback_models", DEFAULT_FALLBACKS[provider]) or ()),
        max_recommendations=max(1, min(int(get("max_recommendations", 6)), 10)),
        max_output_tokens=max(1000, min(int(get("max_output_tokens", 6000)), 16000)),
        reasoning_effort=str(get("reasoning_effort", "low")),
        timeout_seconds=float(get("timeout_seconds", 45)),
        max_retries=int(get("max_retries", 1)),
        max_calls_per_session=int(get("max_calls_per_session", 3)),
        max_calls_per_day=int(get("max_calls_per_day", 30)),
        max_calls_per_hour=int(get("max_calls_per_hour", 10)),
        max_calls_per_user_hour=int(get("max_calls_per_user_hour", 3)),
        max_calls_per_user_day=int(get("max_calls_per_user_day", 6)),
        cooldown_seconds=int(get("cooldown_seconds", 30)),
        max_concurrent=max(1, int(get("max_concurrent", 1))),
    )


KEY_NAMES = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "openai": ("OPENAI_API_KEY",)}


def get_api_key(secrets: Mapping | None, env: Mapping | None, provider: str = "openai") -> str | None:
    """API key for `provider` from secrets (top level or [<provider>].api_key) or the environment.

    Gemini: GEMINI_API_KEY / GOOGLE_API_KEY. OpenAI: OPENAI_API_KEY. Never log the result.
    """
    secrets, env = secrets or {}, env or {}
    names = KEY_NAMES.get(provider, ())
    key = next((secrets.get(n) for n in names if secrets.get(n)), None) \
        or (secrets.get(provider, {}) or {}).get("api_key") \
        or next((env.get(n) for n in names if env.get(n)), None)
    key = str(key).strip() if key else None
    return key or None


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def access_check(settings: AISettings, host: str | None, is_logged_in: bool,
                 email: str | None) -> tuple[bool, str]:
    """Server-side decision on whether this request may trigger a paid API call."""
    if not settings.enabled:
        return False, ("AI recommendations are turned off on this server. An administrator can enable them in the "
                       "server configuration (see README).")
    if settings.access == "public":
        return True, "Open to all visitors, limited by server-side rate limits."
    if settings.access == "local":
        h = (host or "").split(":")[0].lower() if host and not host.startswith("[") else (host or "").lower()
        if h in LOCAL_HOSTS or (host or "").startswith("[::1]"):
            return True, "Enabled for local demonstration."
        return False, "AI recommendations are limited to local use on this server."
    if not is_logged_in:
        return False, "Sign in with an authorized account to generate AI recommendations."
    if not email or email.lower() not in settings.allowed_emails:
        return False, "Your account is not authorized to generate AI recommendations."
    return True, "Authorized account."


class UsageLimiter:
    """Thread-safe per-day call counter (shared by all sessions in one server process)."""

    def __init__(self, max_per_day: int, today: Callable[[], date] = date.today):
        self.max_per_day = max_per_day
        self._today = today
        self._day = today()
        self._count = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._today() != self._day:
                self._day, self._count = self._today(), 0
            if self._count >= self.max_per_day:
                return False
            self._count += 1
            return True

    @property
    def used_today(self) -> int:
        return self._count


def _is_private(ip: str) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a.is_link_local


def client_identity(email: str | None, ip: str | None, forwarded_for: str | None = None) -> str:
    """Rate-limit bucket for a visitor: signed-in email, else client IP. Hashed so raw IPs/emails are not stored.

    Visitors whose address cannot be determined share one 'unknown' bucket (stricter, not looser).
    """
    email, ip, forwarded_for = (v if isinstance(v, str) else None for v in (email, ip, forwarded_for))
    if email:
        raw = "email:" + email.strip().lower()
    else:
        # Prefer the server-observed client IP; the forwarded header is client-controllable, so it is
        # only a fallback. If either is spoofed, server-wide caps still apply.
        ip = (ip or "").strip()
        fwd = (forwarded_for or "").split(",")[0].strip()
        # Behind a hosting proxy (e.g. Streamlit Community Cloud) the observed IP is the proxy's private
        # address, shared by everyone; then the forwarded client address is the only per-visitor signal.
        addr = fwd if (fwd and (not ip or _is_private(ip))) else (ip or fwd)
        raw = "ip:" + addr if addr else "unknown"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RateLimiter:
    """Process-wide sliding-window limiter for paid AI calls (thread-safe).

    Enforces, in order: one-at-a-time concurrency, per-visitor cooldown, per-visitor hourly and
    daily caps, and server-wide hourly and daily caps. Reloading the page or opening new tabs
    does not reset anything because state is keyed by visitor, not by browser session.
    Call `acquire(identity)`; if it returns ok, call `release()` when the request finishes.
    """

    HOUR, DAY = 3600.0, 86400.0

    def __init__(self, settings: AISettings, clock: Callable[[], float] | None = None):
        import time
        self.s = settings
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._calls: list[tuple[float, str]] = []     # (timestamp, identity), last 24 h
        self._in_flight = 0

    def _prune(self, now: float) -> None:
        self._calls = [(t, i) for t, i in self._calls if now - t < self.DAY]

    def acquire(self, identity: str) -> tuple[bool, str, int]:
        """Return (ok, message, retry_after_seconds). Counts the call only if ok."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            mine = [t for t, i in self._calls if i == identity]
            mine_hour = [t for t in mine if now - t < self.HOUR]
            all_hour = [t for t, _ in self._calls if now - t < self.HOUR]

            def wait_until(times: list[float], window: float, limit: int) -> int:
                # seconds until the oldest call that keeps us at the limit leaves the window
                if limit <= 0 or len(times) < limit:
                    return 0
                return int(window - (now - sorted(times)[-limit])) + 1

            checks = [
                (self._in_flight >= self.s.max_concurrent, "Another AI request is running. Try again in a moment.", 10),
                (bool(mine) and now - max(mine) < self.s.cooldown_seconds,
                 "Please wait before requesting again.",
                 int(self.s.cooldown_seconds - (now - max(mine))) + 1 if mine else 0),
                (len(mine_hour) >= self.s.max_calls_per_user_hour, "Hourly AI limit reached for your account/network.",
                 wait_until(mine_hour, self.HOUR, self.s.max_calls_per_user_hour) if mine_hour else 0),
                (len(mine) >= self.s.max_calls_per_user_day, "Daily AI limit reached for your account/network.",
                 wait_until(mine, self.DAY, self.s.max_calls_per_user_day) if mine else 0),
                (len(all_hour) >= self.s.max_calls_per_hour, "This server's hourly AI limit has been reached.",
                 wait_until(all_hour, self.HOUR, self.s.max_calls_per_hour) if all_hour else 0),
                (len(self._calls) >= self.s.max_calls_per_day, "This server's daily AI limit has been reached.",
                 wait_until([t for t, _ in self._calls], self.DAY, self.s.max_calls_per_day) if self._calls else 0),
            ]
            for blocked, msg, retry in checks:
                if blocked:
                    return False, msg, max(1, retry)
            self._calls.append((now, identity))
            self._in_flight += 1
            return True, "ok", 0

    def release(self) -> None:
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)

    def usage(self, identity: str) -> dict:
        with self._lock:
            now = self._clock()
            self._prune(now)
            mine = [t for t, i in self._calls if i == identity]
            return {"user_hour": sum(now - t < self.HOUR for t in mine), "user_day": len(mine),
                    "server_hour": sum(now - t < self.HOUR for t, _ in self._calls),
                    "server_day": len(self._calls), "in_flight": self._in_flight}


# ---------------------------------------------------------------------------
# Privacy-safe aggregate payload with evidence IDs
# ---------------------------------------------------------------------------

def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _ev(items: list, eid: str, category: str, statement: str, **values) -> None:
    items.append({"id": eid, "category": category, "statement": statement,
                  "values": {k: v for k, v in values.items() if v is not None}})


def build_ai_payload(results: Mapping, quality_report: Mapping | None = None) -> dict:
    """Compact, aggregate-only summary of verified results for the model.

    Excludes: raw rows, response IDs, comments, groups below `min_compare`
    (only their count is passed) and suppressed months.
    """
    q1, q2, q3 = results["q1"], results["q2"], results["q3"]
    cfg = results.get("config", {})
    min_compare = cfg.get("min_compare", 30)
    min_cell = cfg.get("min_cell", 10)
    ev: list[dict] = []

    # Q1
    for o in q1.get("objectives", []):
        if o.get("suppressed"):
            continue
        k = o["key"]
        _ev(ev, f"Q1.{k}.before_after", "verified_finding",
            f"'{o['label']}': {o['pre_agree']['pct']}% recalled agreeing before training and "
            f"{o['post_agree']['pct']}% agreed after (change {o['agree_pp_change']} pp; paired n = "
            f"{o['coverage']['paired_n']}).",
            paired_n=o["coverage"]["paired_n"], pre_agree_pct=o["pre_agree"]["pct"],
            post_agree_pct=o["post_agree"]["pct"], change_pp=o["agree_pp_change"],
            post_ci_low=o["post_agree"]["ci_low"], post_ci_high=o["post_agree"]["ci_high"])
        _ev(ev, f"Q1.{k}.individual_change", "verified_finding",
            f"'{o['label']}': {o['change']['improved']['pct']}% improved, {o['change']['unchanged']['pct']}% "
            f"unchanged, {o['change']['declined']['pct']}% declined (paired n = {o['coverage']['paired_n']}).",
            improved_pct=o["change"]["improved"]["pct"], unchanged_pct=o["change"]["unchanged"]["pct"],
            declined_pct=o["change"]["declined"]["pct"], paired_n=o["coverage"]["paired_n"])
        g = o["remaining_gap"]
        _ev(ev, f"Q1.{k}.remaining_gap", "verified_finding",
            f"'{o['label']}': {g['not_agree_after']['pct']}% ({g['not_agree_after']['k']} of "
            f"{g['not_agree_after']['n']}) did not agree after training ({g['neutral_after']['pct']}% neutral, "
            f"{g['disagree_after']['pct']}% disagree).",
            not_agree_after_pct=g["not_agree_after"]["pct"], not_agree_after_n=g["not_agree_after"]["k"],
            paired_n=g["not_agree_after"]["n"], neutral_pct=g["neutral_after"]["pct"],
            disagree_pct=g["disagree_after"]["pct"])
        ac, rr = o.get("agreement_categories"), o.get("reached_agreement_rate")
        if ac:
            _ev(ev, f"Q1.{k}.agreement_categories", "verified_finding",
                f"'{o['label']}' (paired n = {o['coverage']['paired_n']}): {ac['reached']['pct']}% reached agreement, "
                f"{ac['maintained']['pct']}% maintained agreement, {ac['remained_below']['pct']}% remained below "
                f"agreement, {ac['moved_below']['pct']}% moved below agreement."
                + (f" Of the {rr['n']} who started below Agree, {rr['pct']}% reached agreement." if rr else ""),
                reached_pct=ac["reached"]["pct"], maintained_pct=ac["maintained"]["pct"],
                remained_below_pct=ac["remained_below"]["pct"], moved_below_pct=ac["moved_below"]["pct"],
                reached_n=ac["reached"]["k"], started_below_n=rr["n"] if rr else None,
                reached_rate_pct=rr["pct"] if rr else None)
        _ev(ev, f"Q1.{k}.ceiling", "data_quality_warning",
            f"'{o['label']}': {o['ceiling']['pre_strongly_agree']['pct']}% chose 'Strongly agree' before training, "
            "so they cannot show improvement on this scale.",
            pre_strongly_agree_pct=o["ceiling"]["pre_strongly_agree"]["pct"],
            pre_agree_pct=o["pre_agree"]["pct"])
    t = q1.get("trend", {})
    if t.get("available"):
        months = [m for m in t["months"] if not m.get("suppressed")]
        _ev(ev, "Q1.trend.coverage", "verified_finding",
            f"Monthly trends cover {len(months)} months with at least {min_cell} respondents; "
            f"{t['rows_without_valid_training_date']} responses lack a valid training date and are excluded "
            "from monthly trends.",
            months_reported=len(months), rows_without_valid_training_date=t["rows_without_valid_training_date"])
    s = q1.get("sensitivity")
    if s:
        _ev(ev, "Q1.sensitivity", "verified_finding" if s["robust"] else "data_quality_warning",
            f"Excluding suspicious responses shifts headline figures by at most {s['max_shift_pp']} pp "
            f"({'robust' if s['robust'] else 'not robust'}).", max_shift_pp=s["max_shift_pp"])

    # Q2
    for e in q2.get("experience", []):
        if e.get("agree") is None:
            continue
        _ev(ev, f"Q2.experience.{e['field']}", "verified_finding",
            f"'{e['label']}': {e['agree']['pct']}% agreed ({e['agree']['k']} of {e['agree']['n']}); "
            f"{e['strongly_agree']['pct']}% strongly agreed.",
            agree_pct=e["agree"]["pct"], strongly_agree_pct=e["strongly_agree"]["pct"], valid_n=e["agree"]["n"],
            missing_n=e["coverage"]["missing"], unusable_n=e["coverage"]["unusable"])
    r = q2.get("recommendation", {})
    if r.get("summary"):
        _ev(ev, "Q2.recommend", "verified_finding",
            f"Likelihood to recommend (0-10): mean {r['summary']['mean']}, median {r['summary']['median']} "
            f"(n = {r['summary']['n']}); {r['promoters_9_10']['pct']}% gave 9-10 and {r['detractors_0_6']['pct']}% "
            "gave 0-6.",
            mean=r["summary"]["mean"], median=r["summary"]["median"], n=r["summary"]["n"],
            promoters_pct=r["promoters_9_10"]["pct"], detractors_pct=r["detractors_0_6"]["pct"], nps=r["nps"])
    c = q2.get("call_988", {})
    if c.get("coverage", {}).get("collected"):
        cov = c["coverage"]
        _ev(ev, "Q2.call_988.coverage", "data_quality_warning",
            f"Likelihood to call 988 was answered by {cov['valid']} of {cov['rows']} respondents "
            f"({cov['valid_pct']}%). This is answer coverage, not confirmed question exposure: missing answers "
            "may reflect forms that did not show the question or respondents who skipped it.",
            valid_n=cov["valid"], rows=cov["rows"], valid_pct=cov["valid_pct"])
        if c.get("summary"):
            _ev(ev, "Q2.call_988.results", "verified_finding",
                f"Among the {c['summary']['n']} respondents asked, mean likelihood to call 988 was "
                f"{c['summary']['mean']} (0-10) and {c['very_likely_9_10']['pct']}% answered 9-10. Not "
                "generalizable to respondents without an answer.",
                n=c["summary"]["n"], mean=c["summary"]["mean"], very_likely_pct=c["very_likely_9_10"]["pct"])
    rels = [x for x in q2.get("relationships", []) if x.get("rho") is not None and x["n"] >= min_compare]
    for fam in sorted({x["family"] for x in rels}):
        xs = [x for x in rels if x["family"] == fam]
        best = max(xs, key=lambda x: abs(x["rho"]))
        _ev(ev, f"Q2.relationship.{fam}", "exploratory_association",
            f"{best['x']} vs {best['y']}: Spearman rho = {best['rho']} ({best['strength']}, n = {best['n']}); "
            f"rho range in this family {min(x['rho'] for x in xs)} to {max(x['rho'] for x in xs)}. Association "
            "only.", rho=best["rho"], n=best["n"], rho_min=min(x["rho"] for x in xs),
            rho_max=max(x["rho"] for x in xs))
    for row in q2.get("satisfaction_vs_gap", {}).get("rows", []):
        if row.get("suppressed") or row["group"] != "highly_satisfied":
            continue
        gi = row["group_not_agree_after"]
        _ev(ev, f"Q2.satisfied_not_confident.{row['objective']}", "exploratory_association",
            f"Among highly satisfied respondents (n = {row['group_n']}), {gi['pct']}% ({gi['k']}) did not agree "
            f"after training on '{row['label']}', vs {row['others_not_agree_after']['pct']}% of others.",
            group_n=row["group_n"], group_not_agree_pct=gi["pct"], group_not_agree_n=gi["k"],
            others_not_agree_pct=row["others_not_agree_after"]["pct"])
    for f, fb in q2.get("feedback", {}).items():
        if not fb.get("available") or not fb.get("comments"):
            continue
        top = [t for t in fb["themes"] if t["n"] >= min_cell][:6]
        _ev(ev, f"Q2.themes.{f}", "exploratory_association",
            f"Keyword-based themes for '{f}' ({fb['comments']} comments): "
            + "; ".join(f"{t['label']} {t['pct_of_comments']}%" for t in top) + ". Not yet human-validated.",
            comments=fb["comments"], **{f"theme_{t['theme']}_pct": t["pct_of_comments"] for t in top})

    # Q3 (comparable groups only)
    suppressed_or_small = 0
    for dim, g in q3.get("groupings", {}).items():
        if not g.get("available"):
            continue
        for grp in g["groups"]:
            if not grp.get("comparable"):
                suppressed_or_small += 1
                continue
            objs = {k: v for k, v in grp.get("objectives", {}).items() if not v.get("suppressed") and v.get("paired_n")}
            parts = [f"{k}: before {v['pre_agree']['pct']}%, after {v['post_agree']['pct']}%" for k, v in objs.items()]
            vals = {"n": grp["n_rows"]}
            for k, v in objs.items():
                vals[f"{k}_pre_pct"], vals[f"{k}_post_pct"] = v["pre_agree"]["pct"], v["post_agree"]["pct"]
                vals[f"{k}_change_pp"] = v["agree_pp_change"]
            if grp.get("recommend", {}).get("mean") is not None:
                vals["recommend_mean"] = grp["recommend"]["mean"]
            if grp.get("experience", {}).get("mean") is not None:
                vals["experience_mean"] = grp["experience"]["mean"]
            overlap = " (roles overlap: respondents can be in several role groups)" if g.get("overlapping") else ""
            _ev(ev, f"Q3.{dim}.{_slug(grp['key'])}", "verified_finding",
                f"{dim.replace('_', ' ').title()} group '{grp['label']}' (n = {grp['n_rows']}){overlap}: "
                + "; ".join(parts) + (f"; mean recommend {vals['recommend_mean']}" if "recommend_mean" in vals else "")
                + (" EXPLORATORY: fewer than 50 respondents." if grp.get("exploratory") else ""),
                **vals)
        for sgl in g.get("comparison", {}).get("signals", []):
            _ev(ev, f"Q3.{dim}.signal.{_slug(sgl['group'])}.{sgl['objective']}.{sgl['metric']}", "follow_up_investigation",
                f"{sgl['label']} differs from other respondents on {sgl['metric']} for {sgl['objective']}: "
                f"{sgl['group_value']} vs {sgl['rest_value']} ({sgl['difference_pp']} pp). Descriptive only.",
                group_value=sgl["group_value"], rest_value=sgl["rest_value"], difference_pp=sgl["difference_pp"],
                group_n=sgl["group_n"], rest_n=sgl["rest_n"])
    aud = q3.get("audience", {})
    arec = aud.get("recommend") or {}
    if arec.get("student_mean") is not None and arec.get("others_mean") is not None:
        _ev(ev, "Q3.audience.recommend", "verified_finding",
            f"Mean likelihood to recommend (0-10): students {arec['student_mean']} (n = {arec['student_n']}) vs "
            f"other participants {arec['others_mean']} (n = {arec['others_n']}); overall {arec['overall_mean']}. "
            "Students = anyone who selected Student, including multi-role respondents. Descriptive only.",
            student_mean=arec["student_mean"], student_n=arec["student_n"], others_mean=arec["others_mean"],
            others_n=arec["others_n"], overall_mean=arec["overall_mean"])
    comp = q3.get("composition_check", {})
    if comp.get("available"):
        mix = [m for m in comp.get("county_role_mix", []) if m["n"] >= min_compare]
        _ev(ev, "Q3.composition", "data_quality_warning",
            "Student share differs by county (" + ", ".join(f"{m['county']} {m['student_pct']}%" for m in mix)
            + f"; overall {comp['overall_mix_pct'].get('student')}%). County differences may reflect audience mix."
            + (f" Not standardizable: {', '.join(comp['not_standardizable'])}." if comp.get("not_standardizable") else ""),
            overall_student_pct=comp["overall_mix_pct"].get("student"),
            **{f"{_slug(m['county'])}_student_pct": m["student_pct"] for m in mix})
    _ev(ev, "Q3.small_groups", "follow_up_investigation",
        f"{suppressed_or_small} groups had fewer than {min_compare} respondents and are excluded from comparison. "
        "Small counts reflect data collection, not access or outcomes.", groups_excluded=suppressed_or_small)

    # Data quality
    if quality_report:
        flags = quality_report.get("flags", {})
        n = quality_report.get("rows", {}).get("output", 0)
        sv = quality_report.get("survey_versions", {}).get("signatures", [])
        _ev(ev, "DQ.survey_versions", "data_quality_warning",
            f"{len(sv)} different question sets were detected across sessions.", question_sets=len(sv))
        for flag, label in (("flag_offscale_label", "used answer labels outside the agreement scale"),
                            ("flag_fast_completion", "completed the survey in under 30 seconds"),
                            ("flag_reverse_scale_suspect", "may have read the agreement scale in reverse"),
                            ("flag_possible_duplicate", "are possible duplicate submissions"),
                            ("flag_training_date_missing", "have no training date")):
            if flags.get(flag):
                _ev(ev, f"DQ.{flag.removeprefix('flag_')}", "data_quality_warning",
                    f"{flags[flag]} of {n} responses {label} (kept and flagged).", count=flags[flag], rows=n,
                    pct=round(100 * flags[flag] / n, 1) if n else None)

    # Baseline rule-based recommendations: identifiers and actions only (their finding text can mention
    # small groups; the numbers the model may use are all in `evidence`).
    baseline = [{"id": r["id"], "rule": r["rule"], "priority": r["priority"], "category": r["category"],
                 "action": r["action"]} for r in results.get("recommendations", {}).get("recommendations", [])]
    return {
        "prompt_version": PROMPT_VERSION,
        "context": {
            "organization": "EveryMind (nonprofit providing 'Understanding 988' training)",
            "business_question": ("How can EveryMind use its 988 training feedback data to develop an actionable "
                                  "solution for evaluating training effectiveness, identifying gaps across "
                                  "communities, and continuously improving future trainings?"),
            "total_responses": results.get("n_rows"),
            "agree_definition": "'Agree' = Agree or Strongly agree on a 5-point scale.",
            "before_ratings": "Recalled after training (retrospective); changes are self-reported, not causal.",
            "small_group_policy": f"Groups under {min_compare} respondents are not compared.",
        },
        "evidence": ev,
        "baseline_recommendations": baseline,
    }


def payload_fingerprint(payload: Mapping, model: str) -> str:
    blob = json.dumps({"payload": payload, "model": model, "prompt": PROMPT_VERSION}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

class AIRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    priority: Literal["high", "medium", "low"]
    evidence_refs: list[str]
    issue: str
    action: str
    implementation_steps: list[str]
    responsible_role: str
    success_indicator: str
    limitations: str


class AIRecommendationSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendations: list[AIRecommendation]
    overall_note: str


INSTRUCTIONS = """You draft program-improvement recommendations for EveryMind, a nonprofit that delivers
"Understanding 988" training. You receive ONLY aggregate, verified results from a statistical engine as JSON.

Rules (all mandatory):
1. Base every recommendation on the evidence list. Cite 1-4 evidence IDs in evidence_refs, copied exactly.
2. Use only numbers that appear in the evidence you cite. Do not calculate, estimate or invent statistics.
3. No causal claims. 'Before' ratings were recalled after training; describe changes as self-reported.
   Never say the training caused, proved or led to an outcome. Use "associated with", "reported".
4. Do not interpret groups that are not in the evidence. Small counts are not evidence of poor access.
5. Results on likelihood to call 988 apply only to respondents who answered it. Do not claim the others were
   not asked; survey versions are inferred, not verified.
6. Feedback themes are counted with keyword rules; treat their percentages as approximate.
7. Add value beyond baseline_recommendations; do not copy them.
8. Return at most {max_recs} recommendations, highest priority first.
9. Write in plain English for a nonprofit program manager. implementation_steps: 2-5 short concrete steps.
   responsible_role: a role such as Program Manager, Training Lead, Evaluation/Data Lead, Presenters,
   Community Partnerships Lead. success_indicator: one measurable indicator using the existing survey.
   limitations: the main uncertainty for this recommendation.
10. overall_note: one or two sentences on how to use these recommendations."""


# ---------------------------------------------------------------------------
# Validation of model output
# ---------------------------------------------------------------------------

CAUSAL = re.compile(
    r"\b(prove[sn]?|proved|caused|due to the training|because of the training|as a result of the training|"
    r"training (led|leads|resulted) to|resulted from the training|the training (increased|improved|raised|"
    r"boosted|caused))\b", re.I)
STAT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%|percent\b|pp\b|percentage points?\b)", re.I)
N_EQ = re.compile(r"\bn\s*=\s*(\d+)", re.I)
RHO = re.compile(r"\brho\s*=?\s*(-?\d*\.\d+)", re.I)


def _numbers_in(evidence_items: list[dict]) -> list[float]:
    nums = []
    for e in evidence_items:
        for v in e["values"].values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.append(float(v))
        nums += [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", e["statement"])]
    return nums


def _supported(x: float, nums: list[float], tol: float = 0.15) -> bool:
    return any(abs(x - n) <= tol or abs(abs(x) - abs(n)) <= tol for n in nums)


def validate_ai_output(data: Any, payload: Mapping, max_recs: int) -> tuple[list[dict], list[dict], str]:
    """Return (accepted, rejected, overall_note).

    Checks: schema (Pydantic, extra fields forbidden); evidence IDs exist; required
    text present; 1-6 implementation steps; statistics quoted in title/issue appear in
    the cited evidence; no causal language. At most `max_recs` are accepted.
    """
    if isinstance(data, AIRecommendationSet):
        parsed = data
    else:
        parsed = AIRecommendationSet.model_validate(data if isinstance(data, dict) else json.loads(data))
    by_id = {e["id"]: e for e in payload["evidence"]}
    accepted, rejected = [], []
    for i, rec in enumerate(parsed.recommendations):
        r = rec.model_dump()
        reasons = []
        if not r["evidence_refs"]:
            reasons.append("no evidence references")
        unknown = [x for x in r["evidence_refs"] if x not in by_id]
        if unknown:
            reasons.append(f"unknown evidence IDs: {', '.join(unknown[:5])}")
        for k in ("title", "issue", "action", "responsible_role", "success_indicator", "limitations"):
            if not str(r[k]).strip():
                reasons.append(f"empty field: {k}")
        steps = [s for s in r["implementation_steps"] if s.strip()]
        if not 1 <= len(steps) <= 6:
            reasons.append("implementation_steps must contain 1-6 items")
        if not unknown and r["evidence_refs"]:
            nums = _numbers_in([by_id[x] for x in r["evidence_refs"]])
            claims = [float(m.group(1)) for fld in ("title", "issue") for m in STAT.finditer(r[fld])]
            claims += [float(m.group(1)) for fld in ("title", "issue") for m in N_EQ.finditer(r[fld])]
            claims += [float(m.group(1)) for fld in ("title", "issue") for m in RHO.finditer(r[fld])]
            bad = [c for c in claims if not _supported(c, nums)]
            if bad:
                reasons.append(f"statistics not found in cited evidence: {', '.join(f'{b:g}' for b in bad[:5])}")
        text = " ".join(str(r[k]) for k in ("title", "issue", "action", "success_indicator"))
        text += " " + " ".join(r["implementation_steps"])
        if CAUSAL.search(text):
            reasons.append(f"unsupported causal claim ('{CAUSAL.search(text).group(0)}')")
        if reasons:
            rejected.append({"index": i, "title": r["title"][:120], "reasons": reasons})
        elif len(accepted) >= max_recs:
            rejected.append({"index": i, "title": r["title"][:120], "reasons": [f"over the limit of {max_recs}"]})
        else:
            r["implementation_steps"] = steps
            r["evidence"] = [{"id": x, "statement": by_id[x]["statement"], "category": by_id[x]["category"]}
                             for x in r["evidence_refs"]]
            accepted.append(r)
    return accepted, rejected, parsed.overall_note


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

@dataclass
class AIResult:
    status: Literal["ok", "partial", "error", "disabled"]
    message: str
    recommendations: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    overall_note: str = ""
    fingerprint: str = ""
    model: str = ""
    created_at: str = ""
    usage: dict = field(default_factory=dict)
    dataset_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def assert_payload_safe(payload: Mapping, forbidden_texts: list[str] = (), min_compare: int = 30) -> None:
    """Raise ValueError if the payload contains participant-level fields, comments or small groups."""
    blob = json.dumps(payload, default=str)
    for key in ("response_id", "fb_most_valuable_raw", "_raw", "extra__"):
        if f'"{key}' in blob:
            raise ValueError(f"payload contains forbidden field pattern {key!r}")
    for t in forbidden_texts:
        if t and t in blob:
            raise ValueError("payload contains participant comment text")
    for e in payload["evidence"]:
        if e["id"].startswith("Q3.") and "n" in e["values"] and e["values"]["n"] < min_compare:
            raise ValueError(f"payload contains a group below the comparison minimum: {e['id']}")


_ERROR_MESSAGES = {
    "APITimeoutError": "The AI service did not respond in time. Please try again later.",
    "AuthenticationError": "The AI service rejected the configured API key. Ask an administrator to check it.",
    "PermissionDeniedError": "The configured API key is not permitted to use this model.",
    "RateLimitError": "The AI service is rate-limited or out of quota. Please try again later.",
    "APIConnectionError": "Could not connect to the AI service. Check the network connection.",
    "NotFoundError": "The configured AI model is not available for this API key.",
    "BadRequestError": "The AI service rejected the request.",
    "LengthFinishReasonError": "The AI response hit the output-token limit before finishing.",
}
_HTTP_ERRORS = {400: "BadRequestError", 401: "AuthenticationError", 403: "PermissionDeniedError",
                404: "NotFoundError", 408: "APITimeoutError", 429: "RateLimitError"}


def _retryable(exc: Exception) -> bool:
    """Overload, rate-limit and timeout errors are worth retrying on another model."""
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return (isinstance(code, int) and code in (408, 429, 500, 502, 503, 504)) or "Timeout" in name \
        or name in ("APITimeoutError", "RateLimitError", "InternalServerError")


def _friendly(exc: Exception) -> str:
    """Plain-language message for any provider error (never includes the exception text, which may echo keys)."""
    name = type(exc).__name__
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _HTTP_ERRORS:
        name = _HTTP_ERRORS[code]
    elif code == 503:
        return "The AI model is busy right now (high demand at the provider). Please try again in a minute."
    elif isinstance(code, int) and code >= 500:
        return "The AI service had a temporary problem. Please try again later."
    elif "Timeout" in name:
        name = "APITimeoutError"
    elif "Connect" in name:
        name = "APIConnectionError"
    return _ERROR_MESSAGES.get(name, f"The AI request failed ({name}).")


def _call_openai(client, payload: Mapping, settings: AISettings, api_key: str | None):
    """Returns (parsed AIRecommendationSet | None, usage dict, incomplete reason | None)."""
    if client is None:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=settings.timeout_seconds, max_retries=settings.max_retries)
    resp = client.responses.parse(
        model=settings.model,
        instructions=INSTRUCTIONS.format(max_recs=settings.max_recommendations),
        input=json.dumps(payload, ensure_ascii=False),
        text_format=AIRecommendationSet,
        max_output_tokens=settings.max_output_tokens,
        reasoning={"effort": settings.reasoning_effort},
        store=False,
        timeout=settings.timeout_seconds,
    )
    u = getattr(resp, "usage", None)
    usage = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens", "total_tokens")} if u else {}
    if getattr(resp, "status", "completed") == "incomplete":
        return None, usage, getattr(getattr(resp, "incomplete_details", None), "reason", "unknown")
    return getattr(resp, "output_parsed", None), usage, None


def _call_gemini(client, payload: Mapping, settings: AISettings, api_key: str | None):
    """Gemini structured output: JSON mode constrained by the Pydantic model's JSON schema."""
    from google.genai import types
    if client is None:
        from google import genai
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
            timeout=int(settings.timeout_seconds * 1000),
            retry_options=types.HttpRetryOptions(attempts=1)))  # fallback chain handles retries
    level = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}.get(settings.reasoning_effort, "low")
    resp = client.models.generate_content(
        model=settings.model,
        contents=json.dumps(payload, ensure_ascii=False),
        config=types.GenerateContentConfig(
            system_instruction=INSTRUCTIONS.format(max_recs=settings.max_recommendations),
            response_mime_type="application/json",
            response_json_schema=AIRecommendationSet.model_json_schema(),
            max_output_tokens=settings.max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_level=level),
        ),
    )
    u = getattr(resp, "usage_metadata", None)
    usage = ({"input_tokens": getattr(u, "prompt_token_count", None),
              "output_tokens": getattr(u, "candidates_token_count", None),
              "thinking_tokens": getattr(u, "thoughts_token_count", None),
              "total_tokens": getattr(u, "total_token_count", None)} if u else {})
    cands = getattr(resp, "candidates", None) or []
    finish = str(getattr(cands[0], "finish_reason", "") or "") if cands else ""
    if "MAX_TOKENS" in finish:
        return None, usage, "max_output_tokens"
    if finish and "STOP" not in finish:
        return None, usage, finish.split(".")[-1].lower()
    text = getattr(resp, "text", None)
    if not text:
        return None, usage, None
    return AIRecommendationSet.model_validate_json(text), usage, None


def generate_ai_recommendations(payload: Mapping, settings: AISettings, api_key: str | None,
                                client: Any = None, dataset_id: str = "") -> AIResult:
    """Call the configured provider once and return validated recommendations. Never raises for API problems."""
    fp = payload_fingerprint(payload, settings.model)
    base = {"fingerprint": fp, "model": settings.model, "dataset_id": dataset_id,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    if not settings.enabled:
        return AIResult("disabled", "AI recommendations are disabled on this server.", **base)
    provider_name = "Gemini" if settings.provider == "gemini" else "OpenAI"
    if not api_key and client is None:
        return AIResult("error", f"No {provider_name} API key is configured on the server.", **base)
    call = _call_gemini if settings.provider == "gemini" else _call_openai
    # Busy/overloaded models (503/504/429/timeouts) fall back to the next model in the chain.
    chain = [settings.model] + [m for m in settings.fallback_models if m != settings.model]
    tried: list[str] = []
    for model in chain:
        tried.append(model)
        try:
            parsed, usage, incomplete = call(client, payload, replace(settings, model=model), api_key)
            base["model"] = model
            break
        except ValidationError:
            return AIResult("error", "The AI response did not match the required structure.", **base)
        except Exception as exc:  # noqa: BLE001 - every API failure becomes a friendly message
            if _retryable(exc) and model != chain[-1]:
                continue
            msg = _friendly(exc)
            if len(tried) > 1:
                msg += f" (tried {', '.join(tried)})"
            return AIResult("error", msg, **base)
    if incomplete:
        return AIResult("error", f"The AI response was incomplete ({incomplete}). No recommendations were shown.",
                        usage=usage, **base)
    if parsed is None:
        return AIResult("error", "The AI response did not match the required structure.", usage=usage, **base)
    try:
        accepted, rejected, note = validate_ai_output(parsed, payload, settings.max_recommendations)
    except (ValidationError, ValueError, TypeError) as exc:
        return AIResult("error", f"The AI response failed validation ({type(exc).__name__}).", usage=usage, **base)
    if not accepted:
        return AIResult("error", "No AI recommendation passed validation.", rejected=rejected, usage=usage, **base)
    status = "partial" if rejected else "ok"
    msg = (f"{len(accepted)} recommendation(s) passed validation"
           + (f"; {len(rejected)} rejected." if rejected else "."))
    return AIResult(status, msg, recommendations=accepted, rejected=rejected, overall_note=note, usage=usage, **base)
