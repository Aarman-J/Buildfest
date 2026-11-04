"""Tests for the AI recommendation layer. The OpenAI client is always mocked (no paid calls)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.ai_recommendations import (
    AIRecommendationSet, AISettings, UsageLimiter, access_check, assert_payload_safe, build_ai_payload,
    generate_ai_recommendations, get_api_key, load_ai_settings, payload_fingerprint, validate_ai_output,
)
from src.clean_data import clean_survey, load_survey_file
from src.run_analysis import run_all

DATA = Path(__file__).resolve().parents[1] / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
ON = AISettings(enabled=True, access="local", max_recommendations=3, provider="openai", model="gpt-5-mini")
GEM = AISettings(enabled=True, access="local", max_recommendations=3, provider="gemini", model="gemini-3.5-flash")


@pytest.fixture(scope="module")
def real():
    raw, _, _ = load_survey_file(DATA)
    cleaned, report = clean_survey(raw)
    results = run_all(cleaned, report)
    return raw, cleaned, report, results, build_ai_payload(results, report)


def good_rec(refs, **kw):
    base = {"title": "Strengthen the segment on recognizing crises", "priority": "high", "evidence_refs": refs,
            "issue": "Some respondents still did not agree they can identify challenges after training.",
            "action": "Add a short practice scenario.", "implementation_steps": ["Draft scenario", "Pilot it"],
            "responsible_role": "Training Lead", "success_indicator": "Share not agreeing after training declines.",
            "limitations": "Self-reported confidence."}
    base.update(kw)
    return base


class FakeResponses:
    def __init__(self, parsed=None, exc=None, status="completed"):
        self.parsed, self.exc, self.status, self.calls = parsed, exc, status, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(status=self.status, output_parsed=self.parsed,
                               incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                               usage=SimpleNamespace(input_tokens=10, output_tokens=20, total_tokens=30))


class FakeClient:
    def __init__(self, **kw):
        self.responses = FakeResponses(**kw)


# ---------------------------------------------------------------------------
# Payload privacy
# ---------------------------------------------------------------------------

def test_payload_is_aggregate_and_privacy_safe(real):
    raw, cleaned, report, results, payload = real
    comments = [t for f in ("fb_most_valuable", "fb_improvements") for t in cleaned[f].dropna()
                if len(t) >= 25 and len(t.split()) >= 5]
    assert_payload_safe(payload, comments, results["config"]["min_compare"])
    blob = json.dumps(payload)
    assert "response_id" not in blob and "_raw" not in blob
    assert not any(t in blob for t in comments)
    # groups below the comparison minimum are not named or described
    for small in ("Harris", "Fairfax", "Queen Anne"):
        assert small not in blob
    ids = [e["id"] for e in payload["evidence"]]
    assert len(ids) == len(set(ids)) and len(ids) > 10
    assert all(set(b) == {"id", "rule", "priority", "category", "action"} for b in payload["baseline_recommendations"])


def test_assert_payload_safe_catches_violations(real):
    *_, payload = real
    bad = json.loads(json.dumps(payload))
    bad["evidence"].append({"id": "Q3.county.tiny", "category": "verified_finding", "statement": "x",
                            "values": {"n": 4}})
    with pytest.raises(ValueError):
        assert_payload_safe(bad)
    bad2 = {"evidence": [], "rows": [{"response_id": "1"}]}
    with pytest.raises(ValueError):
        assert_payload_safe(bad2)
    with pytest.raises(ValueError):
        assert_payload_safe({"evidence": [{"id": "x", "statement": "a secret comment here", "values": {}}]},
                            ["a secret comment here"])


def test_fingerprint_tracks_dataset(real):
    raw, cleaned, report, results, payload = real
    assert payload_fingerprint(payload, "gpt-5-mini") == payload_fingerprint(build_ai_payload(results, report),
                                                                              "gpt-5-mini")
    other, rep2 = clean_survey(raw.iloc[:600])
    assert payload_fingerprint(build_ai_payload(run_all(other, rep2), rep2), "gpt-5-mini") != \
        payload_fingerprint(payload, "gpt-5-mini")


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

def test_valid_recommendation_accepted(real):
    *_, payload = real
    ev = payload["evidence"][2]  # remaining gap for first objective
    pct = ev["values"]["not_agree_after_pct"]
    data = {"recommendations": [good_rec([ev["id"]], issue=f"{pct}% did not agree after training.")],
            "overall_note": "Use with the rule-based list."}
    acc, rej, note = validate_ai_output(data, payload, 3)
    assert len(acc) == 1 and not rej
    assert acc[0]["evidence"][0]["id"] == ev["id"] and note


@pytest.mark.parametrize("mutate,reason", [
    (lambda e: good_rec(["Q9.does_not_exist"]), "unknown evidence"),
    (lambda e: good_rec([]), "no evidence"),
    (lambda e: good_rec([e]), None),
    (lambda e: good_rec([e], issue="Exactly 73.4% of participants were confused."), "statistics not found"),
    (lambda e: good_rec([e], action="Keep it: the training caused the increase."), "causal"),
    (lambda e: good_rec([e], issue="This proves the training works."), "causal"),
    (lambda e: good_rec([e], implementation_steps=[]), "implementation_steps"),
    (lambda e: good_rec([e], responsible_role="  "), "empty field"),
])
def test_invalid_recommendations_rejected(real, mutate, reason):
    *_, payload = real
    eid = payload["evidence"][0]["id"]
    acc, rej, _ = validate_ai_output({"recommendations": [mutate(eid)], "overall_note": ""}, payload, 3)
    if reason is None:
        assert acc and not rej
    else:
        assert not acc and reason in " ".join(rej[0]["reasons"])


def test_limit_on_number_of_recommendations(real):
    *_, payload = real
    eid = payload["evidence"][0]["id"]
    acc, rej, _ = validate_ai_output({"recommendations": [good_rec([eid])] * 5, "overall_note": ""}, payload, 3)
    assert len(acc) == 3 and len(rej) == 2


def test_malformed_output_raises(real):
    *_, payload = real
    with pytest.raises(Exception):
        validate_ai_output({"recommendations": [{"title": "x"}], "overall_note": ""}, payload, 3)
    with pytest.raises(Exception):
        validate_ai_output({"recommendations": [good_rec(["a"], extra_field=1)], "overall_note": ""}, payload, 3)


# ---------------------------------------------------------------------------
# Generation: mocked client
# ---------------------------------------------------------------------------

def test_generate_success_and_request_shape(real):
    *_, payload = real
    eid = payload["evidence"][0]["id"]
    parsed = AIRecommendationSet.model_validate({"recommendations": [good_rec([eid]), good_rec(["bogus"])],
                                                 "overall_note": "note"})
    client = FakeClient(parsed=parsed)
    res = generate_ai_recommendations(payload, ON, "sk-test", client=client, dataset_id="d1")
    assert res.status == "partial" and len(res.recommendations) == 1 and len(res.rejected) == 1
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5-mini" and call["text_format"] is AIRecommendationSet
    assert call["max_output_tokens"] == ON.max_output_tokens and call["store"] is False
    assert call["timeout"] == ON.timeout_seconds
    assert json.loads(call["input"]) == payload
    assert res.dataset_id == "d1" and res.fingerprint == payload_fingerprint(payload, "gpt-5-mini")


def test_no_call_when_disabled(real):
    *_, payload = real
    client = FakeClient(parsed=None)
    res = generate_ai_recommendations(payload, AISettings(enabled=False), "sk-test", client=client)
    assert res.status == "disabled" and client.responses.calls == []


def test_missing_api_key_no_call(real, monkeypatch):
    *_, payload = real
    import openai

    def boom(*a, **k):
        raise AssertionError("OpenAI client must not be created without a key")
    monkeypatch.setattr(openai, "OpenAI", boom)
    res = generate_ai_recommendations(payload, ON, None)
    assert res.status == "error" and "No OpenAI API key" in res.message


class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    pass


@pytest.mark.parametrize("exc,text", [(APITimeoutError("t"), "did not respond in time"),
                                      (RateLimitError("r"), "rate-limited"),
                                      (RuntimeError("x"), "failed (RuntimeError)")])
def test_api_failures_are_friendly(real, exc, text):
    *_, payload = real
    res = generate_ai_recommendations(payload, ON, "sk-test", client=FakeClient(exc=exc))
    assert res.status == "error" and text in res.message and not res.recommendations


def test_incomplete_and_unparsed_responses(real):
    *_, payload = real
    res = generate_ai_recommendations(payload, ON, "sk-test", client=FakeClient(parsed=None, status="incomplete"))
    assert res.status == "error" and "incomplete" in res.message
    res = generate_ai_recommendations(payload, ON, "sk-test", client=FakeClient(parsed=None))
    assert res.status == "error"
    bad = AIRecommendationSet.model_validate({"recommendations": [good_rec(["nope"])], "overall_note": ""})
    res = generate_ai_recommendations(payload, ON, "sk-test", client=FakeClient(parsed=bad))
    assert res.status == "error" and res.rejected


def test_api_key_never_in_result(real):
    *_, payload = real
    secret = "sk-SECRET-123456"
    res = generate_ai_recommendations(payload, ON, secret, client=FakeClient(exc=RuntimeError(secret)))
    assert secret not in json.dumps(res.to_dict())


# ---------------------------------------------------------------------------
# Settings, access control and limits
# ---------------------------------------------------------------------------

def test_settings_default_disabled():
    s = load_ai_settings({}, {})
    assert not s.enabled and s.access == "allowlist" and s.provider == "gemini" and s.model == "gemini-3.5-flash"
    s = load_ai_settings({"OPENAI_API_KEY": "x"}, {})
    assert not s.enabled  # a key alone does not enable the paid feature


def test_settings_from_secrets_and_env():
    s = load_ai_settings({"ai": {"enabled": True, "access": "allowlist", "allowed_emails": ["A@x.org"],
                                 "max_recommendations": 50}}, {})
    assert s.enabled and s.allowed_emails == ("a@x.org",) and s.max_recommendations == 10
    s = load_ai_settings({}, {"EVERYMIND_AI_ENABLED": "true", "EVERYMIND_AI_ACCESS": "local"})
    assert s.enabled and s.access == "local"
    assert load_ai_settings({}, {"EVERYMIND_AI_ACCESS": "anyone"}).access == "allowlist"


def test_get_api_key():
    assert get_api_key({"OPENAI_API_KEY": " k1 "}, {}) == "k1"
    assert get_api_key({"openai": {"api_key": "k2"}}, {}) == "k2"
    assert get_api_key({}, {"OPENAI_API_KEY": "k3"}) == "k3"
    assert get_api_key({}, {}) is None


@pytest.mark.parametrize("settings,host,logged,email,ok", [
    (AISettings(enabled=False, access="local"), "localhost:8501", False, None, False),
    (AISettings(enabled=True, access="local"), "localhost:8501", False, None, True),
    (AISettings(enabled=True, access="local"), "127.0.0.1:8501", False, None, True),
    (AISettings(enabled=True, access="local"), "everymind.streamlit.app", False, None, False),
    (AISettings(enabled=True, access="local"), None, False, None, False),
    (AISettings(enabled=True, access="allowlist", allowed_emails=("a@x.org",)), "app", False, None, False),
    (AISettings(enabled=True, access="allowlist", allowed_emails=("a@x.org",)), "app", True, "b@x.org", False),
    (AISettings(enabled=True, access="allowlist", allowed_emails=("a@x.org",)), "app", True, "A@x.org", True),
])
def test_access_check(settings, host, logged, email, ok):
    assert access_check(settings, host, logged, email)[0] is ok


def test_usage_limiter_daily_reset():
    day = [date(2026, 11, 1)]
    lim = UsageLimiter(2, today=lambda: day[0])
    assert lim.try_acquire() and lim.try_acquire() and not lim.try_acquire()
    day[0] = date(2026, 11, 2)
    assert lim.try_acquire()


# ---------------------------------------------------------------------------
# Rate limiting (fake clock: no waiting)
# ---------------------------------------------------------------------------

from src.ai_recommendations import RateLimiter, client_identity  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def limiter(**kw):
    clock = Clock()
    s = AISettings(enabled=True, **{"cooldown_seconds": 30, "max_calls_per_user_hour": 3, "max_calls_per_user_day": 6,
                                    "max_calls_per_hour": 10, "max_calls_per_day": 30, **kw})
    return RateLimiter(s, clock=clock), clock


def call(lim, who):
    ok, msg, retry = lim.acquire(who)
    if ok:
        lim.release()
    return ok, msg, retry


def test_cooldown_blocks_rapid_repeats():
    lim, clock = limiter()
    assert call(lim, "a")[0]
    ok, msg, retry = call(lim, "a")
    assert not ok and "wait" in msg.lower() and 1 <= retry <= 31
    assert call(lim, "b")[0]                  # other visitors unaffected
    clock.t += 31
    assert call(lim, "a")[0]


def test_per_visitor_hourly_and_daily_caps():
    lim, clock = limiter()
    for _ in range(3):
        assert call(lim, "a")[0]
        clock.t += 31
    ok, msg, retry = call(lim, "a")
    assert not ok and "Hourly" in msg and retry > 0
    clock.t += 3600
    for _ in range(3):
        assert call(lim, "a")[0]
        clock.t += 31
    clock.t += 3600
    ok, msg, _ = call(lim, "a")
    assert not ok and "Daily" in msg
    clock.t += 86400
    assert call(lim, "a")[0]


def test_server_wide_cap_cannot_be_bypassed_with_new_identities():
    lim, clock = limiter(max_calls_per_hour=5)
    for i in range(5):
        assert call(lim, f"visitor{i}")[0]
    ok, msg, _ = call(lim, "brand-new-visitor")
    assert not ok and "server" in msg.lower()


def test_concurrency_one_at_a_time():
    lim, clock = limiter()
    ok, _, _ = lim.acquire("a")
    assert ok
    ok2, msg, _ = lim.acquire("b")
    assert not ok2 and "running" in msg
    lim.release()
    assert lim.acquire("b")[0]


def test_blocked_attempts_are_not_counted():
    lim, clock = limiter()
    assert call(lim, "a")[0]
    for _ in range(20):
        assert not call(lim, "a")[0]       # hammering during cooldown
    assert lim.usage("a")["user_day"] == 1


def test_client_identity_hashing():
    a = client_identity(None, "1.2.3.4")
    assert a == client_identity(None, "1.2.3.4") != client_identity(None, "5.6.7.8")
    assert "1.2.3.4" not in a
    assert client_identity("X@y.org", "1.2.3.4") == client_identity("x@y.org", "9.9.9.9")
    assert client_identity(None, "1.2.3.4", "6.6.6.6") == a      # spoofable header ignored when IP known
    # behind a proxy (private observed IP) the forwarded client address identifies the visitor
    assert client_identity(None, "10.0.0.5", "8.8.8.8, 10.0.0.1") == client_identity(None, "8.8.8.8")
    assert client_identity(None, "10.0.0.5", "8.8.8.8") != client_identity(None, "10.0.0.5", "9.9.9.9")
    assert client_identity(None, None, None) == client_identity(None, "", "")


def test_rate_limit_settings_loaded():
    s = load_ai_settings({"ai": {"cooldown_seconds": 60, "max_calls_per_user_hour": 1}}, {})
    assert s.cooldown_seconds == 60 and s.max_calls_per_user_hour == 1 and s.max_concurrent == 1



# ---------------------------------------------------------------------------
# Gemini provider (mocked client)
# ---------------------------------------------------------------------------

class FakeGeminiModels:
    def __init__(self, text=None, exc=None, finish="FinishReason.STOP"):
        self.text, self.exc, self.finish, self.calls = text, exc, finish, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text, candidates=[SimpleNamespace(finish_reason=self.finish)],
                               usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=50,
                                                              thoughts_token_count=20, total_token_count=170))


class FakeGemini:
    def __init__(self, **kw):
        self.models = FakeGeminiModels(**kw)


def test_gemini_success_and_request_shape(real):
    *_, payload = real
    eid = payload["evidence"][0]["id"]
    text = json.dumps({"recommendations": [good_rec([eid])], "overall_note": "note"})
    client = FakeGemini(text=text)
    res = generate_ai_recommendations(payload, GEM, "gm-test", client=client, dataset_id="d")
    assert res.status == "ok" and res.model == "gemini-3.5-flash" and len(res.recommendations) == 1
    call = client.models.calls[0]
    assert call["model"] == "gemini-3.5-flash" and json.loads(call["contents"]) == payload
    cfg = call["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema == AIRecommendationSet.model_json_schema()
    assert cfg.max_output_tokens == GEM.max_output_tokens and "evidence" in cfg.system_instruction
    assert res.usage["total_tokens"] == 170


def test_gemini_invalid_refs_rejected(real):
    *_, payload = real
    text = json.dumps({"recommendations": [good_rec(["made.up.id"])], "overall_note": ""})
    res = generate_ai_recommendations(payload, GEM, "gm-test", client=FakeGemini(text=text))
    assert res.status == "error" and res.rejected and "unknown evidence" in res.rejected[0]["reasons"][0]


@pytest.mark.parametrize("kw,text", [
    ({"text": None, "finish": "FinishReason.MAX_TOKENS"}, "incomplete"),
    ({"text": "{not json", "finish": "FinishReason.STOP"}, "did not match"),
    ({"text": json.dumps({"recommendations": [{"title": "x"}], "overall_note": ""})}, "did not match"),
    ({"text": None, "finish": "FinishReason.SAFETY"}, "incomplete"),
])
def test_gemini_bad_responses(real, kw, text):
    *_, payload = real
    res = generate_ai_recommendations(payload, GEM, "gm-test", client=FakeGemini(**kw))
    assert res.status == "error" and text in res.message and not res.recommendations


class FakeAPIError(Exception):
    def __init__(self, code):
        super().__init__(f"error {code} with key gm-SECRET")
        self.code = code


@pytest.mark.parametrize("code,text", [(429, "rate-limited"), (401, "rejected the configured API key"),
                                       (404, "not available"), (503, "busy right now"), (500, "temporary problem")])
def test_gemini_http_errors_are_friendly(real, code, text):
    *_, payload = real
    res = generate_ai_recommendations(payload, GEM, "gm-SECRET", client=FakeGemini(exc=FakeAPIError(code)))
    assert res.status == "error" and text in res.message
    assert "gm-SECRET" not in json.dumps(res.to_dict())


def test_gemini_missing_key_and_disabled(real):
    *_, payload = real
    res = generate_ai_recommendations(payload, GEM, None)
    assert res.status == "error" and "No Gemini API key" in res.message
    client = FakeGemini(text="{}")
    off = AISettings(enabled=False, provider="gemini")
    assert generate_ai_recommendations(payload, off, "k", client=client).status == "disabled"
    assert client.models.calls == []


def test_provider_settings_and_keys():
    s = load_ai_settings({"ai": {"provider": "openai"}}, {})
    assert s.provider == "openai" and s.model == "gpt-5-mini"
    s = load_ai_settings({"ai": {"provider": "gemini", "model": "gemini-2.5-flash"}}, {})
    assert s.model == "gemini-2.5-flash"
    assert load_ai_settings({"ai": {"provider": "something"}}, {}).provider == "gemini"
    assert get_api_key({"GEMINI_API_KEY": "g1", "OPENAI_API_KEY": "o1"}, {}, "gemini") == "g1"
    assert get_api_key({"GOOGLE_API_KEY": "g2"}, {}, "gemini") == "g2"
    assert get_api_key({"gemini": {"api_key": "g3"}}, {}, "gemini") == "g3"
    assert get_api_key({}, {"GEMINI_API_KEY": "g4"}, "gemini") == "g4"
    assert get_api_key({"OPENAI_API_KEY": "o1"}, {}, "gemini") is None



class SeqGeminiModels:
    """Fails with the given codes in order, then succeeds."""
    def __init__(self, codes, text):
        self.codes, self.text, self.calls = list(codes), text, []

    def generate_content(self, **kw):
        self.calls.append(kw["model"])
        if self.codes:
            raise FakeAPIError(self.codes.pop(0))
        return SimpleNamespace(text=self.text, candidates=[SimpleNamespace(finish_reason="FinishReason.STOP")],
                               usage_metadata=None)


def test_gemini_falls_back_when_model_busy(real):
    *_, payload = real
    eid = payload["evidence"][0]["id"]
    text = json.dumps({"recommendations": [good_rec([eid])], "overall_note": ""})
    s = replace(GEM, fallback_models=("lite-model", "latest-model"))
    client = SimpleNamespace(models=SeqGeminiModels([503], text))
    res = generate_ai_recommendations(payload, s, "k", client=client)
    assert res.status == "ok" and res.model == "lite-model"
    assert client.models.calls == ["gemini-3.5-flash", "lite-model"]


def test_gemini_all_models_busy(real):
    *_, payload = real
    s = replace(GEM, fallback_models=("lite-model",))
    client = SimpleNamespace(models=SeqGeminiModels([503, 504], "{}"))
    res = generate_ai_recommendations(payload, s, "k", client=client)
    assert res.status == "error" and "tried gemini-3.5-flash, lite-model" in res.message


def test_non_retryable_error_does_not_fall_back(real):
    *_, payload = real
    s = replace(GEM, fallback_models=("lite-model",))
    client = SimpleNamespace(models=SeqGeminiModels([401], "{}"))
    res = generate_ai_recommendations(payload, s, "k", client=client)
    assert res.status == "error" and client.models.calls == ["gemini-3.5-flash"]


def test_default_gemini_fallbacks():
    s = load_ai_settings({"ai": {"provider": "gemini"}}, {})
    assert s.fallback_models and s.model not in s.fallback_models



def test_public_access_mode():
    s = load_ai_settings({"ai": {"enabled": True, "access": "public"}}, {})
    assert s.access == "public"
    assert access_check(s, "everymind.streamlit.app", False, None)[0]
    assert not access_check(replace(s, enabled=False), "everymind.streamlit.app", False, None)[0]
