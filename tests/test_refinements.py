"""Regression tests for the 2026-11-06 refinement (teammate review corrections + simplified dashboard).

Each test pins one confirmed correction. Expected values for the provided dataset were verified
independently from the raw workbook with plain pandas (no project code).
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.ai_recommendations import assert_payload_safe, build_ai_payload
from src.analysis_utils import AnalysisConfig
from src.analyze_q1 import run_q1
from src.analyze_q3 import run_q3
from src.clean_data import CleaningConfig, clean_survey, load_survey_file, make_public_dataset
from src.run_analysis import run_all
from ui import transforms as X

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
LBL = {1: "Strongly disagree", 2: "Disagree", 3: "Neither agree nor disagree", 4: "Agree", 5: "Strongly agree"}


def upload(n: int, **cols) -> pd.DataFrame:
    base = {
        "response_id": [f"s{i}" for i in range(n)], "start_time": ["2026-03-02 10:00:00"] * n,
        "completion_time": ["2026-03-02 10:03:00"] * n, "training_date": ["2026-03-01"] * n,
        "county": ["Howard"] * n, "participant_role": ["Student"] * n, "language": ["English (United States)"] * n,
        "pre_identify_mh": [3] * n, "post_identify_mh": [4] * n, "pre_describe_988": [2] * n,
        "post_describe_988": [5] * n, "pre_know_when_988": [3] * n, "post_know_when_988": [4] * n,
        "sat_presenter_engaged": [5] * n, "sat_presenter_knowledge": [5] * n, "sat_well_organized": [4] * n,
        "likelihood_recommend": [9] * n, "likelihood_call_988": [8] * n,
    }
    base.update(cols)
    df = pd.DataFrame(base)
    for c in df.columns:
        if c.startswith(("pre_", "post_", "sat_")):
            df[c] = df[c].map(lambda v: LBL.get(v, v) if not (isinstance(v, float) and np.isnan(v)) else v)
    return df


@pytest.fixture(scope="module")
def real():
    raw, _, _ = load_survey_file(DATA)
    cleaned, report = clean_survey(raw)
    return raw, cleaned, report, run_all(cleaned, report)


# 3.1 Training-experience label normalization --------------------------------------------------

def test_experience_mapping_only_on_experience_items():
    labels = ["Very agree", "Satisfied"] + ["Agree"] * 8
    out, rep = clean_survey(upload(10, sat_presenter_engaged=labels, post_identify_mh=labels))
    assert out["sat_presenter_engaged"].tolist()[:2] == [5, 4]
    assert out["sat_presenter_engaged_raw"].tolist()[:2] == ["Very agree", "Satisfied"]
    assert out["flag_experience_label_normalized"].tolist()[:3] == [True, True, False]
    # confidence items are never mapped: off-scale and missing, raw text kept
    assert out["post_identify_mh"].isna().tolist()[:3] == [True, True, False]
    assert out["flag_offscale_label"].tolist()[:2] == [True, True]
    assert rep["unexpected_categories"]["post_identify_mh"] == {"Very agree": 1, "Satisfied": 1}


def test_experience_mapping_can_be_disabled():
    out, _ = clean_survey(upload(10, sat_presenter_engaged=["Very agree"] + ["Agree"] * 9),
                          CleaningConfig(experience_label_map={}))
    assert pd.isna(out.loc[0, "sat_presenter_engaged"]) and out.loc[0, "flag_offscale_label"]


def test_real_experience_counts(real):
    *_, res = real
    assert [e["coverage"]["valid"] for e in res["q2"]["experience"]] == [1148, 1145, 1141]


# 3.2 Student membership --------------------------------------------------------------------------

def test_student_membership_includes_multi_role(real):
    _, cleaned, _, res = real
    groups = {g["key"]: g["n_rows"] for g in res["q3"]["audience"]["groups"]}
    assert groups == {"student": 1080, "non_student": 219}
    assert int((cleaned["role_primary"] == "student").sum()) == 1076          # exclusive view preserved
    mix = res["q3"]["composition_check"]["overall_mix_pct"]
    assert mix["student"] == round(100 * 1080 / 1299, 1)


def test_audience_comparison_counts_multi_role_students():
    roles = ["Student"] * 30 + ["Student;Caregiver/parent"] * 10 + ["Caregiver/parent"] * 35
    q3 = run_q3(clean_survey(upload(75, participant_role=roles))[0])
    groups = {g["key"]: g["n_rows"] for g in q3["audience"]["groups"]}
    assert groups == {"student": 40, "non_student": 35}


# 3.3 County classification ----------------------------------------------------------------------

def test_partner_label_county_kept_separately(real):
    _, cleaned, _, _ = real
    row = cleaned[cleaned["county_partner"].notna()]
    assert len(row) == 1 and row["county"].iloc[0] == "Montgomery" and row["county_partner"].iloc[0] == "NAMI MC"
    assert int((cleaned["county"] == "Montgomery").sum()) == 34
    harris = cleaned[cleaned["county"] == "Harris"]
    assert harris["county_state"].isna().all() and (harris["county_status"] == "unrecognized").all()


# 3.4 Training-date eligibility -------------------------------------------------------------------

def test_training_date_after_completion_excluded_from_trends(real):
    _, cleaned, _, res = real
    assert int(cleaned["training_date_valid"].sum()) == 1295
    late = cleaned["flag_training_date_after_completion"]
    assert int(late.sum()) == 1 and cleaned.loc[late, "training_month"].isna().all()
    assert res["q1"]["trend"]["rows_with_valid_training_date"] == 1295
    assert res["q1"]["objectives"][0]["coverage"]["paired_n"] == 1299       # still in other analyses


# 3.5 Calling-likelihood coverage wording --------------------------------------------------------

def test_call_coverage_is_described_as_answer_coverage(real):
    *_, res = real
    cov = res["q2"]["call_988"]["coverage"]
    assert (cov["rows"], cov["valid"], cov["missing"]) == (1299, 293, 1006)
    cav = res["q2"]["call_988"]["caveat"].lower()
    assert "answer coverage" in cav and "were asked" not in cav
    payload = build_ai_payload(res, real[2])
    stmt = next(e for e in payload["evidence"] if e["id"] == "Q2.call_988.coverage")["statement"]
    assert "not confirmed question exposure" in stmt


# 3.6 Public export privacy -----------------------------------------------------------------------

def test_row_level_helper_drops_identifiers(real):
    _, cleaned, _, _ = real
    pub = make_public_dataset(cleaned)
    for col in ("response_id", "training_date", "completion_date", "completion_seconds", "session_key",
                "duplicate_group", "start_time", "completion_time", "county_partner"):
        assert col not in pub.columns


def test_downloads_are_aggregate_only(real):
    raw, cleaned, report, res = real
    z = zipfile.ZipFile(io.BytesIO(X.tables_zip(res, report)))
    for name in z.namelist():
        data = z.read(name)
        if name.endswith(".csv") and data.strip():          # empty tables (nothing to report) are skipped
            df = pd.read_csv(io.BytesIO(data))
            assert "response_id" not in df.columns and len(df) < 200, name     # summaries, not records
    ids = set(cleaned["response_id"].astype(str))
    texts = [t for f in ("fb_most_valuable", "fb_improvements") for t in cleaned[f].dropna()
             if len(t) >= 25 and len(t.split()) >= 5]
    for blob in (X.executive_summary_md(res, report), X.priorities_md(res), json.dumps(res)):
        assert not [t for t in texts if t in blob]
    assert '"response_id"' not in json.dumps(res) and len(ids) == 1299


# Part 4: agreement categories ---------------------------------------------------------------------

def test_agreement_categories_by_hand():
    pre = [1, 2, 3, 4, 5, 5, 4, 2, 3, 4, 1, 5]
    post = [4, 3, 2, 4, 4, 3, 5, 5, 3, 2, 1, 5]
    q1 = run_q1(clean_survey(upload(12, pre_identify_mh=pre, post_identify_mh=post))[0], AnalysisConfig(min_cell=5))
    o = next(x for x in q1["objectives"] if x["key"] == "identify_mh")
    a = o["agreement_categories"]
    below = [p < 4 for p in pre]
    exp = {"reached": sum(b and q >= 4 for b, q in zip(below, post)),
           "maintained": sum(not b and q >= 4 for b, q in zip(below, post)),
           "remained_below": sum(b and q < 4 for b, q in zip(below, post)),
           "moved_below": sum(not b and q < 4 for b, q in zip(below, post))}
    assert {k: v["k"] for k, v in a.items()} == exp
    assert sum(v["k"] for v in a.values()) == 12
    rr = o["reached_agreement_rate"]
    assert rr["n"] == sum(below) and rr["k"] == exp["reached"]
    # Disagree -> Neutral improved numerically but did NOT reach agreement
    assert o["change"]["improved"]["k"] > exp["reached"]


def test_reached_rate_not_applicable_when_nobody_starts_below():
    q1 = run_q1(clean_survey(upload(12, pre_identify_mh=[4] * 12, post_identify_mh=[5] * 12))[0],
                AnalysisConfig(min_cell=5))
    o = next(x for x in q1["objectives"] if x["key"] == "identify_mh")
    assert o["reached_agreement_rate"] is None
    assert X.q1_reached_rates(q1, ["identify_mh"]).iloc[0]["Reached-agreement rate"] == "Not applicable"


def test_real_reached_agreement_matches_reference(real):
    *_, res = real
    got = {o["key"]: (o["reached_agreement_rate"]["k"], o["reached_agreement_rate"]["n"],
                      o["reached_agreement_rate"]["pct"]) for o in res["q1"]["objectives"]}
    assert got == {"identify_mh": (264, 361, 73.1), "describe_988": (602, 695, 86.6),
                   "know_when_988": (501, 576, 87.0)}


# Q3 sample-size labels -----------------------------------------------------------------------------

def test_exploratory_label_and_comparison_threshold(real):
    *_, res = real
    q3 = res["q3"]
    groups = {g["key"]: g for g in q3["groupings"]["county"]["groups"]}
    assert groups["Montgomery"]["exploratory"] and not groups["Anne Arundel"]["exploratory"]
    cmp = X.q3_compare(q3, "county", "identify_mh")
    assert all("Queen Anne" not in g and "Fairfax" not in g and "Harris" not in g for g in cmp["group"])
    assert any("exploratory" in g for g in cmp["group"])


# Part 6: consolidated priorities adapt to the data -------------------------------------------------

def test_priorities_on_real_data(real):
    *_, res = real
    pr = {p["key"]: p for p in res["recommendations"]["priorities"]}
    assert set(pr) == {"A", "B", "C", "D"}
    for p in pr.values():
        for f in ("finding", "evidence", "action", "implementation", "responsible_role", "success_metric",
                  "limitation", "linked_rules"):
            assert p[f], (p["key"], f)
    assert "131 of 1299" in pr["A"]["evidence"][0]
    assert "293 of 1299" in pr["C"]["evidence"][0]


def test_priorities_not_triggered_without_evidence():
    roles = ["Student"] * 40 + ["School staff member"] * 40
    df = upload(80, participant_role=roles, post_identify_mh=[5] * 80, post_describe_988=[5] * 80,
                post_know_when_988=[5] * 80, likelihood_recommend=[9] * 80)
    cleaned, rep = clean_survey(df)
    keys = {p["key"] for p in run_all(cleaned, rep)["recommendations"]["priorities"]}
    assert "A" not in keys and "B" not in keys and "C" not in keys


# AI payload ----------------------------------------------------------------------------------------

def test_ai_payload_includes_new_evidence_and_stays_safe(real):
    _, cleaned, report, res = real
    payload = build_ai_payload(res, report)
    ids = {e["id"] for e in payload["evidence"]}
    assert {"Q1.identify_mh.agreement_categories", "Q3.audience.recommend"} <= ids
    mont = next(e for e in payload["evidence"] if e["id"] == "Q3.county.montgomery")
    assert "EXPLORATORY" in mont["statement"]
    texts = [t for f in ("fb_most_valuable", "fb_improvements") for t in cleaned[f].dropna()
             if len(t) >= 25 and len(t.split()) >= 5]
    assert_payload_safe(payload, texts)


# Upload replaces demonstration data ---------------------------------------------------------------

def test_explicit_upload_of_same_file_recomputes(real):
    from ui import state as S
    data = DATA.read_bytes()
    state: dict = {}
    S.register_upload(state, data, DATA.name)
    assert S.process(state)
    state["ai_results"] = {"fp": {"dataset_id": state["dataset_id"], "status": "ok", "fingerprint": "fp"}}
    assert not S.register_upload(state, data, DATA.name)                 # silent re-render: no reset
    assert S.register_upload(state, data, "my_upload.xlsx", force=True)   # explicit upload: fresh start
    assert "results" not in state and "ai_results" not in state and state["upload_name"] == "my_upload.xlsx"
    assert S.process(state) and state["results"]["n_rows"] == 1299
