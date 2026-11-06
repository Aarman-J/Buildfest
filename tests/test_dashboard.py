"""Tests for the dashboard layer: session lifecycle, transforms, exports and an end-to-end app run."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from ui import charts as CH
from ui import state as S
from ui import transforms as X

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
TEXT_FIELDS = ("fb_most_valuable", "fb_improvements")


@pytest.fixture(scope="module")
def sample_bytes() -> bytes:
    return DATA.read_bytes()


@pytest.fixture(scope="module")
def loaded(sample_bytes):
    state: dict = {}
    assert S.register_upload(state, sample_bytes, DATA.name)
    assert S.process(state)
    return state


def comments(state) -> list[str]:
    c = state["cleaned"]
    return [t for f in TEXT_FIELDS for t in c[f].dropna() if len(t) >= 25 and len(t.split()) >= 5]


def synthetic_upload(n=40, **cols) -> bytes:
    base = {"response_id": range(n), "completion_time": ["2026-03-02 10:03:00"] * n,
            "training_date": ["2026-03-01"] * n, "county": ["Howard"] * n, "participant_role": ["Student"] * n,
            "pre_identify_mh": ["Disagree"] * n, "post_identify_mh": ["Agree"] * n,
            "likelihood_recommend": [9] * n}
    base.update(cols)
    return pd.DataFrame(base).to_csv(index=False).encode()


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def test_end_to_end_upload_processing(loaded):
    assert S.is_ready(loaded)
    assert loaded["results"]["n_rows"] == len(loaded["cleaned"]) == 1299
    assert loaded["report"]["rows"]["retained_all"]
    pub = loaded["public_df"]
    assert not set(TEXT_FIELDS) & set(pub.columns)
    assert not any(c.endswith("_raw") or c.startswith("extra__") for c in pub.columns)
    assert "county_partner" not in pub.columns and "completion_time" not in pub.columns


def test_csv_upload_path(sample_bytes):
    raw = pd.read_excel(io.BytesIO(sample_bytes))
    state: dict = {}
    S.register_upload(state, raw.to_csv(index=False).encode(), "export.csv")
    assert S.process(state)
    assert state["results"]["n_rows"] == 1299


def test_same_file_does_not_reset_but_new_file_does(sample_bytes):
    state: dict = {}
    S.register_upload(state, sample_bytes, "a.xlsx")
    S.process(state)
    state["ai_results"] = {"fp": {"dataset_id": state["dataset_id"], "status": "ok", "fingerprint": "fp"}}
    assert not S.register_upload(state, sample_bytes, "a.xlsx")
    assert S.is_ready(state) and state["ai_results"]
    assert S.register_upload(state, synthetic_upload(), "b.csv")
    for k in ("results", "cleaned", "report", "ai_results", "dataset_id", "period_cache"):
        assert k not in state
    assert not S.is_ready(state)


def test_failed_processing_clears_old_results(sample_bytes):
    state: dict = {}
    S.register_upload(state, sample_bytes, "a.xlsx")
    assert S.process(state)
    S.register_upload(state, pd.DataFrame({"foo": [1, 2], "bar": ["x", "y"]}).to_csv(index=False).encode(), "x.csv")
    assert not S.process(state)
    assert state.get("results") is None and state.get("error")
    assert not S.is_ready(state)


def test_unreadable_file_reports_error():
    state: dict = {}
    S.register_upload(state, b"\x00\x01not a spreadsheet", "broken.xlsx")
    assert "upload_error" in state and state.get("upload_raw") is None


def test_ambiguous_mapping_requires_confirmation_and_invalidates():
    data = synthetic_upload(**{"Date of training": ["2026-02-01"] * 40, "Training date": ["2026-02-02"] * 40})
    data = pd.read_csv(io.BytesIO(data)).drop(columns=["training_date"]).to_csv(index=False).encode()
    state: dict = {}
    S.register_upload(state, data, "amb.csv")
    need = X.needs_confirmation(S.mapping_preview(state))
    assert {r["source"] for r in need} == {"Date of training", "Training date"}
    assert S.process(state)
    assert "training_date" in state["report"]["fields"]["missing_core"]
    first_id = state["dataset_id"]
    state["overrides"] = {"Training date": "training_date"}
    assert not S.is_ready(state)          # mapping changed: old results are stale
    assert S.process(state)
    assert state["dataset_id"] != first_id
    assert state["cleaned"]["training_date"].iloc[0] == pd.Timestamp("2026-02-02")


def test_ai_results_tied_to_dataset(loaded):
    state = dict(loaded)
    state["ai_results"] = {}
    S.store_ai_result(state, {"fingerprint": "f1", "dataset_id": "other", "status": "ok"})
    assert state["ai_results"] == {}
    S.store_ai_result(state, {"fingerprint": "f1", "dataset_id": state["dataset_id"], "status": "ok"})
    assert S.ai_result_for_current(state, "f1") and S.latest_ai_result(state)
    state["dataset_id"] = "changed"
    assert S.ai_result_for_current(state, "f1") is None and S.latest_ai_result(state) is None


def test_period_filter_uses_training_months(loaded):
    months = X.available_months(loaded["results"]["q1"])
    period = tuple(months[-2:])
    q1 = S.q1_for_period(loaded, period)
    expected = int(loaded["cleaned"]["training_month"].isin(period).sum())
    assert q1["n_rows"] == expected
    assert S.q1_for_period(loaded, period) is q1          # cached
    assert S.q1_for_period(loaded, None) is loaded["results"]["q1"]


# ---------------------------------------------------------------------------
# Transforms used for rendering
# ---------------------------------------------------------------------------

def test_q1_rendering_inputs_match_engine(loaded):
    q1 = loaded["results"]["q1"]
    ba = X.q1_before_after(q1)
    assert len(ba) == 2 * len(q1["objectives"])
    for o in q1["objectives"]:
        after = ba[(ba["objective"] == o["key"]) & (ba["phase"] == "After")].iloc[0]
        assert after["pct"] == o["post_agree"]["pct"] and after["n"] == o["coverage"]["paired_n"]
    ch = X.q1_change(q1, ["describe_988"])
    assert set(ch["category"]) == {"Improved", "Unchanged", "Declined"}
    assert ch["pct"].sum() == pytest.approx(100, abs=0.2)
    pivot = X.q1_transition(q1, "describe_988")
    assert pivot.shape == (5, 5) and int(pivot.values.sum()) == q1["objectives"][1]["coverage"]["paired_n"]
    trend = X.q1_trend(q1)
    assert set(trend["month"]) == set(X.available_months(q1))
    assert trend.loc[trend["suppressed"], "post_agree_pct"].isna().all()


def test_q2_rendering_inputs(loaded):
    q2 = loaded["results"]["q2"]
    assert len(X.q2_experience_summary(q2)) == 3
    rd = X.rating_dist(q2["recommendation"])
    assert list(rd["score"]) == list(range(11)) and rd["n"].sum() == q2["recommendation"]["summary"]["n"]
    rel = X.q2_relationships(q2, ["post_confidence_vs_call_988"])
    assert (rel["n"] <= q2["call_988"]["coverage"]["valid"]).all()
    themes, types, fb = X.q2_themes(q2, "fb_improvements")
    assert not themes.empty and types["n"].sum() == fb["comments"]


def test_q3_small_group_suppression(loaded):
    q3 = loaded["results"]["q3"]
    table = X.q3_group_table(q3, "county")
    sup = table[table["Status"].str.startswith("Suppressed")]
    assert set(sup["Group"]) == {"Fairfax", "Harris"}
    assert (sup["Respondents"] == "<10").all()
    stat_cols = [c for c in table.columns if c.endswith("%") or "mean" in c]
    assert sup[stat_cols].isna().all().all()
    default = X.q3_outcomes(q3, "county", "describe_988")
    assert set(default["group"]) == {"Anne Arundel", "Montgomery", "Prince George's"}
    with_desc = X.q3_outcomes(q3, "county", "describe_988", include_descriptive=True)
    assert "Queen Anne's" in set(with_desc["group"]) and not {"Fairfax", "Harris"} & set(with_desc["group"])
    assert not {"Fairfax", "Harris"} & set(X.q3_gap(q3, "county", "describe_988", True)["group"])
    assert not {"Fairfax", "Harris"} & set(X.q3_missingness(q3, "county")["Group"])


def test_q3_overlapping_roles(loaded):
    g = loaded["results"]["q3"]["groupings"]["role"]
    assert g["overlapping"] and g["membership_total"] > g["rows_total"]


def test_missing_optional_fields_render():
    state: dict = {}
    S.register_upload(state, synthetic_upload(), "min.csv")
    assert S.process(state)
    res, rep = state["results"], state["report"]
    assert X.q2_experience_summary(res["q2"]).empty
    assert X.rating_dist(res["q2"]["call_988"]).empty
    k = X.overview_kpis(res, rep)
    assert k["call_988"] is False or k["call_988"] is None
    assert len(k["objectives"]) == 1
    for fig in (CH.likert_stacked(X.q2_experience_dist(res["q2"]), "t"),
                CH.rating_bars(X.rating_dist(res["q2"]["call_988"]), "t"),
                CH.before_after(X.q1_before_after(res["q1"]), "t")):
        assert fig is not None
    X.executive_summary_md(res, rep)
    X.tables_zip(res, rep)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def test_exports_are_privacy_safe(loaded):
    res, rep = loaded["results"], loaded["report"]
    texts = comments(loaded)
    csv = S.public_csv(loaded).decode("utf-8")
    md = X.executive_summary_md(res, rep, "sample")
    z = zipfile.ZipFile(io.BytesIO(X.tables_zip(res, rep)))
    zipped = " ".join(z.read(n).decode("utf-8") for n in z.namelist())
    for blob in (csv, md, zipped, json.dumps(res), X.rule_recommendations_md(res)):
        assert not [t for t in texts if t in blob]
    assert "response_id" not in zipped
    assert "Harris" not in md and "Fairfax" not in md


def test_ai_markdown_is_labelled_advisory():
    md = X.ai_recommendations_md({"model": "gpt-5-mini", "created_at": "now", "fingerprint": "f", "overall_note": "",
                                  "recommendations": [], "rejected": []})
    assert "ADVISORY" in md


# ---------------------------------------------------------------------------
# App smoke test (headless)
# ---------------------------------------------------------------------------

def test_app_runs_all_pages_without_ai_calls(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import openai

    monkeypatch.delenv("EVERYMIND_AI_ENABLED", raising=False)

    def no_paid_calls(*a, **k):
        raise AssertionError("No OpenAI client may be created while AI is disabled")
    monkeypatch.setattr(openai, "OpenAI", no_paid_calls)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    # Do not depend on the developer's local .streamlit/secrets.toml: run with AI explicitly disabled.
    at.secrets["ai"] = {"enabled": False}
    at.run()
    assert not at.exception
    at.radio(key="nav").set_value("Upload & Data Quality").run()
    [b for b in at.button if "sample" in b.label.lower()][0].click().run()
    assert not at.exception and "dataset_id" in at.session_state
    for page in ("Executive Overview", "Explore Findings", "AI Insights & Recommendations",
                 "Upload & Data Quality"):
        at.radio(key="nav").set_value(page).run()
        assert not at.exception, page
    at.radio(key="nav").set_value("AI Insights & Recommendations").run()
    assert not [b for b in at.button if "Generate AI" in b.label]
