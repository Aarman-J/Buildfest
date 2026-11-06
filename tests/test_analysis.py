"""Tests for the analysis engine (Q1-Q3, feedback themes, recommendations).

Synthetic uploads use canonical column names and go through the real cleaning
pipeline, so expected values can be computed by hand. Headline results on the
original file are re-derived independently from the raw spreadsheet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.analysis_utils import AnalysisConfig, spearman, wilson_ci
from src.analyze_q1 import run_q1
from src.analyze_q2 import run_q2
from src.analyze_q3 import run_q3
from src.clean_data import clean_survey, load_survey_file
from src.feedback_themes import classify, score_validation, theme_summary, validation_sample
from src.recommendations import generate_recommendations
from src.run_analysis import render_summary, run_all

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
LBL = {1: "Strongly disagree", 2: "Disagree", 3: "Neither agree nor disagree", 4: "Agree", 5: "Strongly agree"}


# ---------------------------------------------------------------------------
# Synthetic upload helper
# ---------------------------------------------------------------------------

def upload(n: int, **cols) -> pd.DataFrame:
    """Raw upload with canonical headers. Likert columns may be given as ints (converted to labels)."""
    base = {
        "response_id": [f"s{i}" for i in range(n)],
        "start_time": ["2026-03-02 10:00:00"] * n,
        "completion_time": ["2026-03-02 10:03:00"] * n,
        "training_date": ["2026-03-01"] * n,
        "county": ["Howard"] * n,
        "participant_role": ["Student"] * n,
        "language": ["English (United States)"] * n,
        "pre_identify_mh": [3] * n, "post_identify_mh": [4] * n,
        "pre_describe_988": [2] * n, "post_describe_988": [5] * n,
        "pre_know_when_988": [3] * n, "post_know_when_988": [4] * n,
        "sat_presenter_engaged": [5] * n, "sat_presenter_knowledge": [5] * n, "sat_well_organized": [4] * n,
        "likelihood_recommend": [9] * n, "likelihood_call_988": [8] * n,
    }
    base.update(cols)
    df = pd.DataFrame(base)
    for c in df.columns:
        if (c.startswith(("pre_", "post_", "sat_"))):
            df[c] = df[c].map(lambda v: LBL.get(v, v) if not (isinstance(v, float) and np.isnan(v)) else v)
    return df


def cleaned(n: int, **cols) -> pd.DataFrame:
    return clean_survey(upload(n, **cols))[0]


@pytest.fixture(scope="module")
def real():
    raw, _, _ = load_survey_file(DATA)
    c, report = clean_survey(raw)
    return raw, c, report


@pytest.fixture(scope="module")
def real_results(real):
    _, c, report = real
    return run_all(c, report)


# ---------------------------------------------------------------------------
# Q1: denominators and paired calculations
# ---------------------------------------------------------------------------

def test_paired_calculations_by_hand():
    pre = [1, 2, 3, 4, 5, 5, 4, 2, 3, 3, 4, 1]
    post = [4, 2, 5, 4, 5, 3, 5, 4, 3, 4, 2, 5]
    q1 = run_q1(cleaned(12, pre_identify_mh=pre, post_identify_mh=post), AnalysisConfig(min_cell=5))
    o = next(x for x in q1["objectives"] if x["key"] == "identify_mh")
    assert o["coverage"]["paired_n"] == 12
    pre_agree = sum(p >= 4 for p in pre)
    post_agree = sum(p >= 4 for p in post)
    assert o["pre_agree"]["k"] == pre_agree and o["post_agree"]["k"] == post_agree
    assert o["agree_pp_change"] == round(round(100 * post_agree / 12, 1) - round(100 * pre_agree / 12, 1), 1)
    ch = [b - a for a, b in zip(pre, post)]
    assert o["change"]["improved"]["k"] == sum(c > 0 for c in ch)
    assert o["change"]["unchanged"]["k"] == sum(c == 0 for c in ch)
    assert o["change"]["declined"]["k"] == sum(c < 0 for c in ch)
    assert o["remaining_gap"]["not_agree_after"]["k"] == sum(p < 4 for p in post)
    assert o["ceiling"]["pre_strongly_agree"]["k"] == pre.count(5)
    assert sum(d["n"] for d in o["post_distribution"]) == 12
    # transition matrix cells add up to paired n
    t = next(x for x in q1["transitions"] if x["key"] == "identify_mh")
    assert sum(c["n"] for c in t["cells"]) == 12


def test_missing_and_offscale_excluded_from_denominators():
    n = 20
    pre = [3] * n
    post = [5] * 15 + [np.nan] * 3 + ["Very agree"] * 2   # 2 off-scale = 10% (passes value check)
    q1 = run_q1(cleaned(n, pre_identify_mh=pre, post_identify_mh=post))
    o = next(x for x in q1["objectives"] if x["key"] == "identify_mh")
    assert o["coverage"]["paired_n"] == 15
    assert o["coverage"]["post"]["missing"] == 3 and o["coverage"]["post"]["unusable"] == 2
    assert o["post_agree"]["n"] == 15 and o["post_agree"]["pct"] == 100.0
    # missing is not counted as "not agreeing"
    assert o["remaining_gap"]["not_agree_after"]["k"] == 0


def test_partial_pairs_handled():
    df = upload(15).drop(columns=["pre_describe_988"])
    c, _ = clean_survey(df)
    q1 = run_q1(c)
    keys = [o["key"] for o in q1["objectives"]]
    assert keys == ["identify_mh", "know_when_988"]
    assert q1["objectives_missing"] == ["describe_988"]
    q3 = run_q3(c)
    grp = q3["groupings"]["county"]["groups"][0]
    assert grp["objectives"]["describe_988"]["paired_n"] is None          # post-only reported
    assert grp["objectives"]["describe_988"]["post_agree"]["pct"] == 100.0


def test_trend_uses_training_date_only():
    dates = ["2026-01-15"] * 12 + ["2026-02-10"] * 12 + [None] * 6
    c = cleaned(30, training_date=dates, completion_time=["2026-03-01 10:00:00"] * 30)
    t = run_q1(c)["trend"]
    assert [m["month"] for m in t["months"]] == ["2026-01", "2026-02"]
    assert t["rows_without_valid_training_date"] == 6
    assert sum(m["n_rows"] for m in t["months"]) == t["rows_with_valid_training_date"] == 24
    assert "2026-03" not in [m["month"] for m in t["months"]]  # completion month never used


def test_small_month_suppressed():
    dates = ["2026-01-15"] * 25 + ["2026-02-10"] * 5
    t = run_q1(cleaned(30, training_date=dates))["trend"]
    feb = next(m for m in t["months"] if m["month"] == "2026-02")
    assert feb["suppressed"] and feb["identify_mh_post_agree_pct"] is None


def test_wilson_ci():
    lo, hi = wilson_ci(50, 100)
    assert lo == pytest.approx(40.4, abs=0.1) and hi == pytest.approx(59.6, abs=0.1)
    assert wilson_ci(0, 0) == (None, None)


# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------

def test_spearman_reports_pairwise_n():
    x = pd.Series([1, 2, 3, 4, 5, np.nan, 7, 8, 9, 10] * 4)
    y = pd.Series([2, 1, 4, 3, 6, 5, np.nan, 9, 8, 10] * 4)
    r = spearman(x, y, min_n=10)
    assert r["n"] == 32
    assert r["rho"] is not None and r["rho"] > 0.8
    assert spearman(x[:5], y[:5], min_n=10)["rho"] is None


def test_q2_handles_absent_optional_questions():
    df = upload(40).drop(columns=["sat_presenter_engaged", "sat_presenter_knowledge", "sat_well_organized",
                                  "likelihood_call_988"])
    c, rep = clean_survey(df)
    q2 = run_q2(c)
    assert all(not e["coverage"]["collected"] for e in q2["experience"])
    assert not q2["call_988"]["coverage"]["collected"]
    assert all(r["family"] not in ("experience_vs_post_confidence", "post_confidence_vs_call_988")
               for r in q2["relationships"])
    recs = generate_recommendations(run_q1(c), q2, run_q3(c), rep)
    assert not any(r["rule"] == "Q2_CALL_988_COVERAGE" for r in recs["recommendations"])


def test_call_988_coverage_and_caveat():
    call = [8] * 10 + [np.nan] * 30
    q2 = run_q2(cleaned(40, likelihood_call_988=call))
    c = q2["call_988"]
    assert c["coverage"]["valid"] == 10 and c["summary"]["n"] == 10
    assert "must not be generalized" in c["caveat"]


def test_recommendation_nps():
    rec = [10] * 10 + [8] * 5 + [3] * 5
    r = run_q2(cleaned(20, likelihood_recommend=rec))["recommendation"]
    assert r["promoters_9_10"]["k"] == 10 and r["detractors_0_6"]["k"] == 5
    assert r["nps"] == 25.0


def test_satisfaction_vs_gap():
    post = [5] * 20 + [3] * 20
    eng = [5] * 30 + [2] * 10
    q2 = run_q2(cleaned(40, post_identify_mh=post, sat_presenter_engaged=eng, sat_presenter_knowledge=eng,
                        sat_well_organized=eng), AnalysisConfig(min_cell=5))
    row = next(r for r in q2["satisfaction_vs_gap"]["rows"]
               if r["objective"] == "identify_mh" and r["group"] == "highly_satisfied")
    assert row["group_n"] == 30 and row["satisfied_but_not_confident_n"] == 10


# ---------------------------------------------------------------------------
# Q3: groups, overlap, small-group policy, composition
# ---------------------------------------------------------------------------

def test_overlapping_roles_counted_in_each_group():
    roles = ["Student"] * 30 + ["Student;Caregiver/parent"] * 12 + ["Caregiver/parent"] * 20
    q3 = run_q3(cleaned(62, participant_role=roles))
    g = {r["key"]: r for r in q3["groupings"]["role"]["groups"]}
    assert g["student"]["n_rows"] == 42 and g["caregiver_parent"]["n_rows"] == 32
    assert q3["groupings"]["role"]["membership_total"] == 74 > 62
    assert q3["groupings"]["role"]["comparison"]["tests"] == []          # no tests on overlapping groups
    prim = {r["key"]: r for r in q3["groupings"]["role_primary"]["groups"]}
    assert prim["multiple"]["n_rows"] == 12
    assert sum(r["n_rows"] or 0 for r in prim.values()) == 62


def test_small_group_policy():
    counties = ["Howard"] * 40 + ["Kent"] * 15 + ["Cecil"] * 4
    q3 = run_q3(cleaned(59, county=counties))
    g = {r["key"]: r for r in q3["groupings"]["county"]["groups"]}
    assert g["Cecil"]["suppressed"] and g["Cecil"]["n_display"] == "<10" and g["Cecil"]["n_rows"] is None
    assert "objectives" not in g["Cecil"]
    assert not g["Kent"]["suppressed"] and not g["Kent"]["comparable"]
    assert g["Howard"]["comparable"]
    assert all(s["group"] == "Howard" for s in q3["groupings"]["county"]["comparison"]["all_comparisons"]) \
        or q3["groupings"]["county"]["comparison"]["all_comparisons"] == []
    assert {"Kent", "Cecil"} <= {f["group"] for f in q3["small_group_follow_ups"]}


def test_composition_explains_county_difference():
    # Students: post agree 50%; non-students: 100%. County A mostly students, B mostly adults.
    roles = ["Student"] * 40 + ["School staff member"] * 10 + ["Student"] * 10 + ["School staff member"] * 40
    post = ([5] * 20 + [2] * 20 + [5] * 10) + ([5] * 5 + [2] * 5 + [5] * 40)
    counties = ["Howard"] * 50 + ["Kent"] * 50
    q3 = run_q3(cleaned(100, participant_role=roles, post_identify_mh=post, county=counties))
    rows = {r["county"]: r for r in q3["composition_check"]["standardized"] if r["objective"] == "identify_mh"}
    assert rows["Howard"]["crude_post_agree_pct"] == 60.0 and rows["Kent"]["crude_post_agree_pct"] == 90.0
    assert rows["Howard"]["standardized_post_agree_pct"] == rows["Kent"]["standardized_post_agree_pct"] == 75.0
    assert rows["Howard"]["composition_may_matter"] and rows["Kent"]["composition_may_matter"]


def test_unknown_categories_do_not_break_grouping():
    q3 = run_q3(cleaned(30, county=["Harris"] * 15 + ["Baltimore"] * 15, participant_role=["Nurse"] * 30,
                        language=["Klingon"] * 30))
    keys = {r["key"] for r in q3["groupings"]["county"]["groups"]}
    assert keys == {"Harris", "Baltimore"}
    assert [r["key"] for r in q3["groupings"]["language"]["groups"]] == ["other"]
    assert [r["key"] for r in q3["groupings"]["role_primary"]["groups"]] == ["unrecognized"]


# ---------------------------------------------------------------------------
# Feedback themes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,field,themes,rtype", [
    ("That anyone can call 988 24/7", "fb_most_valuable", {"who_can_call", "availability_privacy"}, "neutral"),
    ("idk", "fb_improvements", set(), "unclear"),
    ("nothing", "fb_improvements", {"no_change_needed"}, "neutral"),
    ("More videos and activities please", "fb_improvements", {"media_visuals", "more_interactive"},
     "improvement_suggestion"),
    ("It was great!", "fb_improvements", {"general_praise"}, "praise"),
    ("how many people call 988 every day", "fb_improvements", {"questions_about_988"}, "neutral"),
])
def test_classify(text, field, themes, rtype):
    r = classify(text, field)
    assert set(r["themes"]) == themes and r["response_type"] == rtype


def test_theme_summary_counts_only_and_validation_workflow():
    texts = ["More videos please", "nothing", "It was great", "how does it work?", None] * 4
    c = cleaned(20, fb_improvements=texts)
    s = theme_summary(c, "fb_improvements")
    assert s["comments"] == 16
    assert "More videos please" not in json.dumps(s)
    sample = validation_sample(c, "fb_improvements", n=10, seed=1)
    assert len(sample) == 10 and set(sample["human_type"]) == {""}
    sample["human_type"] = sample["machine_type"]
    sample["human_themes"] = sample["machine_themes"]
    score = score_validation(sample)
    assert score["reviewed"] == 10 and score["type_accuracy"] == 1.0


# ---------------------------------------------------------------------------
# Recommendations adapt to the data
# ---------------------------------------------------------------------------

def test_recommendations_adapt_to_data():
    good = cleaned(60, post_identify_mh=[5] * 60, post_describe_988=[5] * 60, post_know_when_988=[5] * 60)
    q = (run_q1(good), run_q2(good), run_q3(good))
    rules = {r["rule"] for r in generate_recommendations(*q)["recommendations"]}
    assert "Q1_REMAINING_GAP" not in rules and "Q2_CALL_988_COVERAGE" not in rules
    weak = cleaned(60, post_know_when_988=[3] * 30 + [4] * 30, likelihood_call_988=[7] * 10 + [np.nan] * 50)
    recs = generate_recommendations(run_q1(weak), run_q2(weak), run_q3(weak))["recommendations"]
    gap = next(r for r in recs if r["rule"] == "Q1_REMAINING_GAP")
    assert gap["evidence"]["objective"] == "know_when_988" and gap["evidence"]["not_agree_after"]["pct"] == 50.0
    assert any(r["rule"] == "Q2_CALL_988_COVERAGE" for r in recs)
    for r in recs:
        assert {"finding", "evidence", "action", "implementation", "success_metric", "category"} <= set(r)


def test_no_hardcoded_dataset_values_in_source():
    src = ROOT / "src"
    forbidden = re.compile(r"\b(1299|1,299|Anne Arundel|Prince George's|89\.9|44\.0)\b")
    for f in ["analysis_utils.py", "analyze_q1.py", "analyze_q2.py", "analyze_q3.py", "recommendations.py",
              "run_analysis.py"]:
        text = (src / f).read_text(encoding="utf-8")
        assert not forbidden.search(text), f


# ---------------------------------------------------------------------------
# Whole-engine properties on the original data
# ---------------------------------------------------------------------------

def test_deterministic(real, real_results):
    _, c, report = real
    assert json.dumps(run_all(c, report), sort_keys=True) == json.dumps(real_results, sort_keys=True)


def test_results_json_serializable_and_no_pii(real, real_results):
    raw, c, _ = real
    blob = json.dumps(real_results)
    assert "response_id" not in blob
    header_text = " ".join(map(str, raw.columns)).lower()
    # Distinctive comments only: short generic phrases ("Anyone can call") legitimately appear in theme labels.
    comments = [t for f in ("fb_most_valuable", "fb_improvements") for t in c[f].dropna()
                if len(t) >= 25 and len(t.split()) >= 5 and t.lower() not in header_text]
    assert comments and not [t for t in comments if t in blob]
    for sec in ("q1", "q2", "q3", "recommendations"):
        md = render_summary(real_results, sec)
        assert not [t for t in comments if t in md]


def test_engine_does_not_modify_cleaned(real):
    _, c, report = real
    snap = c.copy(deep=True)
    run_all(c, report)
    pd.testing.assert_frame_equal(c, snap)


def test_sensitivity_excludes_flagged_rows(real, real_results):
    _, c, _ = real
    rows = {s["scenario"]: s for s in real_results["q1"]["sensitivity"]["scenarios"]}
    assert rows["all_responses"]["rows"] == len(c)
    assert rows["exclude_reverse_scale_suspects"]["excluded_rows"] == int(c["flag_reverse_scale_suspect"].sum())
    g = c["duplicate_group"].dropna()
    assert rows["exclude_possible_duplicates"]["excluded_rows"] == int(len(g) - g.nunique())


# ---------------------------------------------------------------------------
# Independent verification against the raw spreadsheet (no cleaning code)
# ---------------------------------------------------------------------------

def _raw_scores(raw: pd.DataFrame, col_idx: int, experience: bool = False) -> pd.Series:
    scale = {"strongly disagree": 1, "disagree": 2, "neither agree nor disagree": 3,
             "neither agree or disagree": 3, "agree": 4, "strongly agree": 5}
    if experience:  # approved normalization for the three experience items only
        scale = {**scale, "very agree": 5, "satisfied": 4}
    s = raw.iloc[:, col_idx].map(lambda v: scale.get(str(v).replace("\xa0", " ").strip().lower())
                                 if pd.notna(v) else None)
    return pd.to_numeric(s, errors="coerce")


def test_headlines_match_raw_spreadsheet(real_results):
    raw = pd.read_excel(DATA)  # independent read, positional columns as documented in data_profile.md
    pairs = {"identify_mh": (10, 7), "describe_988": (11, 8), "know_when_988": (12, 9)}
    q1 = {o["key"]: o for o in real_results["q1"]["objectives"]}
    for key, (pre_i, post_i) in pairs.items():
        pre, post = _raw_scores(raw, pre_i), _raw_scores(raw, post_i)
        both = pre.notna() & post.notna()
        n = int(both.sum())
        pre_pct = round(100 * (pre[both] >= 4).mean(), 1)
        post_pct = round(100 * (post[both] >= 4).mean(), 1)
        assert q1[key]["coverage"]["paired_n"] == n
        assert q1[key]["pre_agree"]["pct"] == pre_pct
        assert q1[key]["post_agree"]["pct"] == post_pct
        assert q1[key]["agree_pp_change"] == round(post_pct - pre_pct, 1)
        assert q1[key]["change"]["improved"]["k"] == int((post[both] > pre[both]).sum())
    q2 = real_results["q2"]
    for e, idx in zip(q2["experience"], (13, 14, 15)):
        s = _raw_scores(raw, idx, experience=True).dropna()
        assert e["coverage"]["valid"] == len(s)
        assert e["agree"]["pct"] == round(100 * (s >= 4).mean(), 1)
    rec = raw.iloc[:, 16].dropna()
    assert q2["recommendation"]["summary"]["n"] == len(rec)
    assert q2["recommendation"]["summary"]["mean"] == round(rec.mean(), 3)
    assert q2["call_988"]["coverage"]["valid"] == int(raw.iloc[:, 17].notna().sum())
    # role overlap: membership counts from raw tokens
    tokens = raw.iloc[:, 4].dropna().str.split(";").map(lambda t: {x.strip() for x in t if x.strip()})
    roles = {r["key"]: r["n_rows"] for r in real_results["q3"]["groupings"]["role"]["groups"]}
    assert roles["student"] == int(tokens.map(lambda t: "Student" in t).sum())
    assert roles["caregiver_parent"] == int(tokens.map(lambda t: "Caregiver/parent" in t).sum())
