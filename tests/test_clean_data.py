"""Tests for the cleaning and validation layer.

Covers the provided 2025-26 dataset and synthetic "future upload" scenarios.
Run with:  python -m pytest
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.clean_data import CleaningConfig, clean_survey, load_survey_file, make_public_dataset
from src.data_validation import detect_pii, parse_rating, redact_pii
from src.schema_mapper import LIKERT_FIELDS, TEXT_FIELDS, map_columns

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
# Checksum recorded during Stage 1 EDA (outputs/data_profile.md).
ORIGINAL_SHA256 = "56f42d5524a2f6f8c8b42d2ed9fcd03cd67f1925f1164301249b1228a8fd3020"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def raw_df() -> pd.DataFrame:
    df, sheet, _ = load_survey_file(DATA)
    assert sheet == "Survey Data"
    return df


@pytest.fixture(scope="module")
def result(raw_df):
    return clean_survey(raw_df)


@pytest.fixture(scope="module")
def cleaned(result):
    return result[0]


@pytest.fixture(scope="module")
def report(result):
    return result[1]


@pytest.fixture(scope="module")
def headers(raw_df) -> dict[str, str]:
    """Canonical field -> original header in the provided file."""
    return {m.field: m.source for m in map_columns(raw_df.columns).with_status("matched")}


def synthetic(n: int = 4, **overrides) -> pd.DataFrame:
    """Small future-style upload with renamed/simplified headers."""
    base = {
        "Response ID": [f"r{i}" for i in range(1, n + 1)],
        "Completion time": ["2026-11-02 10:00:00"] * n,
        "Start time": ["2026-11-02 09:58:00"] * n,
        "Role": ["Student"] * n,
        "Training date": ["2026-11-01"] * n,
        "County": ["Howard"] * n,
        "I can identify mental health challenges and crises.": ["Agree"] * n,
        "I can describe 988 and how it works.": ["Strongly agree"] * n,
        "I know when to use 988.": ["Agree"] * n,
        "Before the training: I could identify mental health challenges and crises.": ["Disagree"] * n,
        "Before the training: I could describe 988 and how it works.": ["Disagree"] * n,
        "Before the training: I knew when to use 988.": ["Neither agree nor disagree"] * n,
        "Likelihood to recommend": [9] * n,
    }
    base.update(overrides)
    return pd.DataFrame(base)


# ---------------------------------------------------------------------------
# Source data integrity and record retention
# ---------------------------------------------------------------------------

def test_original_spreadsheet_unchanged(raw_df):
    before = sha256(DATA)
    clean_survey(raw_df)
    assert sha256(DATA) == before == ORIGINAL_SHA256


def test_input_dataframe_not_modified(raw_df):
    snapshot = raw_df.copy(deep=True)
    clean_survey(raw_df)
    pd.testing.assert_frame_equal(raw_df, snapshot)


def test_all_records_retained(raw_df, cleaned, report):
    assert len(cleaned) == len(raw_df) == 1299
    assert set(cleaned["response_id"]) == {str(int(v)) for v in raw_df["Id"]}
    assert report["rows"] == {"input": 1299, "output": 1299, "retained_all": True}
    assert report["invariant_violations"] == []


def test_cleaning_is_deterministic(raw_df, cleaned, report):
    again, report2 = clean_survey(raw_df)
    pd.testing.assert_frame_equal(cleaned, again)
    assert json.dumps(report, default=str, sort_keys=True) == json.dumps(report2, default=str, sort_keys=True)


def test_no_file_side_effects(raw_df, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    clean_survey(raw_df)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Agreement-scale mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,score", [
    ("Strongly disagree", 1), ("Disagree", 2), ("Neither agree nor disagree", 3),
    ("Neither agree or disagree", 3), ("Agree", 4), ("Strongly agree", 5),
    ("Strongly agree\xa0", 5), ("  strongly AGREE ", 5), ("Agree‎", 4),
])
def test_valid_likert_labels_map(label, score):
    df = synthetic(1, **{"I can identify mental health challenges and crises.": [label]})
    out, _ = clean_survey(df)
    assert out.loc[0, "post_identify_mh"] == score
    assert out.loc[0, "post_identify_mh_raw"] == label  # original text preserved
    assert not out.loc[0, "flag_offscale_label"]


@pytest.mark.parametrize("label", ["Very agree", "Satisfied", "Totally", "5"])
def test_unknown_likert_labels_flagged_not_guessed(label):
    # 1 off-scale value in 5 keeps the column above the 80% value-check threshold
    df = synthetic(5, **{"I can identify mental health challenges and crises.": [label] + ["Agree"] * 4})
    out, rep = clean_survey(df)
    assert pd.isna(out.loc[0, "post_identify_mh"])
    assert out.loc[0, "flag_offscale_label"]
    assert out.loc[0, "post_identify_mh_raw"] == label
    assert rep["unexpected_categories"]["post_identify_mh"] == {label: 1}


def test_real_experience_label_normalization(cleaned, report):
    # Approved 2026-11-06: "Very agree" -> 5 and "Satisfied" -> 4 on the three experience items only.
    assert int(cleaned["flag_experience_label_normalized"].sum()) == 24
    assert int(cleaned["flag_offscale_label"].sum()) == 0
    assert report["standardization"]["experience_labels_normalized"]["sat_presenter_engaged"] ==         {"very agree": 13, "satisfied": 10}
    assert [int(cleaned[f].notna().sum()) for f in ("sat_presenter_engaged", "sat_presenter_knowledge",
                                                    "sat_well_organized")] == [1148, 1145, 1141]
    norm = cleaned["flag_experience_label_normalized"]
    raw = cleaned.loc[norm, "sat_presenter_engaged_raw"]
    assert set(raw.dropna()) <= {"Very agree", "Satisfied", "Strongly agree "}   # raw text preserved
    assert (cleaned.loc[cleaned["sat_presenter_engaged_raw"] == "Very agree", "sat_presenter_engaged"] == 5).all()
    assert (cleaned.loc[cleaned["sat_presenter_engaged_raw"] == "Satisfied", "sat_presenter_engaged"] == 4).all()
    # wording variant "or" normalized, not flagged
    assert report["standardization"]["likert_wording_variant"]["sat_presenter_engaged"] == 98


def test_approved_label_override_is_applied_only_when_configured():
    df = synthetic(5, **{"I can identify mental health challenges and crises.": ["Very agree"] + ["Agree"] * 4})
    default, _ = clean_survey(df)
    approved, _ = clean_survey(df, CleaningConfig(likert_label_overrides={"Very agree": 5}))
    assert pd.isna(default.loc[0, "post_identify_mh"])
    assert approved.loc[0, "post_identify_mh"] == 5


# ---------------------------------------------------------------------------
# Missing vs invalid
# ---------------------------------------------------------------------------

def test_missing_answers_stay_missing(raw_df, cleaned, headers):
    for f in LIKERT_FIELDS:
        raw_missing = raw_df[headers[f]].isna().to_numpy()
        assert cleaned.loc[raw_missing, f].isna().all(), f
        assert not cleaned.loc[raw_missing, "flag_offscale_label"].any() or f.startswith("sat")
    rec_missing = raw_df[headers["likelihood_call_988"]].isna().to_numpy()
    assert cleaned.loc[rec_missing, "likelihood_call_988"].isna().all()
    assert int(cleaned["likelihood_call_988"].notna().sum()) == 293


@pytest.mark.parametrize("value,expected", [
    (7, (7.0, "valid")), (7.0, (7.0, "valid")), ("10", (10.0, "valid")), (0, (0.0, "valid")),
    (None, (None, "missing")), (np.nan, (None, "missing")), ("", (None, "missing")),
    (11, (None, "invalid")), (-1, (None, "invalid")), (7.5, (None, "invalid")),
    ("ten", (None, "invalid")), ("10 - extremely likely", (None, "invalid")), (True, (None, "invalid")),
])
def test_parse_rating(value, expected):
    v, status = parse_rating(value)
    assert status == expected[1]
    assert (np.isnan(v) if expected[0] is None else v == expected[0])


def test_invalid_rating_flagged_and_distinct_from_missing():
    df = synthetic(6, **{"Likelihood to recommend": [11, None, "8", 9, 10, 0]})
    out, rep = clean_survey(df)
    assert out["likelihood_recommend"].isna().tolist() == [True, True, False, False, False, False]
    assert out["flag_invalid_rating"].tolist() == [True, False, False, False, False, False]
    assert rep["invalid_values"]["likelihood_recommend"] == {"invalid": 1, "missing": 1}


def test_dates_invalid_vs_missing_vs_inconsistent():
    dates = ["2026-11-01", "not a date", None, "2026-11-05", "10/30/2026", "2026-10-31"]
    out, rep = clean_survey(synthetic(6, **{"Training date": dates}))
    assert out["flag_training_date_invalid"].tolist() == [False, True, False, False, False, False]
    assert out["flag_training_date_missing"].tolist() == [False, False, True, False, False, False]
    assert out["flag_training_date_after_completion"].tolist() == [False, False, False, True, False, False]
    assert out["training_date"].iloc[4] == pd.Timestamp("2026-10-30")
    # completion date is never substituted for a missing/invalid training date
    # a training date after the survey submission is not usable for training-date trends (row is kept)
    assert out["training_date_valid"].tolist() == [True, False, False, False, True, True]
    assert out["training_month"].tolist()[:4] == ["2026-11", None, None, None]
    assert out.loc[1:2, "session_key"].str.startswith("unknown date").all()
    assert "training_date_effective" not in out
    assert out["training_date_raw"].iloc[1] == "not a date"


def test_real_date_checks(report, cleaned):
    assert report["invalid_values"]["training_date"] == {"invalid": 0, "missing": 3}
    assert report["dates"]["training_date"]["after_completion"] == 1
    assert int(cleaned["flag_training_date_gap_gt_30d"].sum()) == 14


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected,primary", [
    ("Student", {"student"}, "student"),
    ("Caregiver/parent;", {"caregiver_parent"}, "caregiver_parent"),
    ("Student;Caregiver/parent;", {"student", "caregiver_parent"}, "multiple"),
    ("Caregiver/parent;Student", {"student", "caregiver_parent"}, "multiple"),
    ("School staff member;Caregiver/parent;Other (e.g., community member)",
     {"school_staff", "caregiver_parent", "other_community"}, "multiple"),
])
def test_roles_parsed(raw, expected, primary):
    out, _ = clean_survey(synthetic(1, Role=[raw]))
    got = {c.removeprefix("role_") for c in ["role_student", "role_school_staff", "role_caregiver_parent",
                                               "role_other_community"] if out.loc[0, c]}
    assert got == expected
    assert out.loc[0, "role_primary"] == primary
    assert out.loc[0, "participant_role_raw"] == raw


def test_role_order_does_not_matter():
    a, _ = clean_survey(synthetic(1, Role=["Caregiver/parent;Student"]))
    b, _ = clean_survey(synthetic(1, Role=["Student;Caregiver/parent;"]))
    assert a.loc[0, "participant_role"] == b.loc[0, "participant_role"] == "student;caregiver_parent"


def test_unknown_role_kept_and_configurable():
    out, rep = clean_survey(synthetic(1, Role=["Nurse;Student"]))
    assert out.loc[0, "role_student"]
    assert out.loc[0, "role_unrecognized"] == "Nurse"
    assert out.loc[0, "flag_role_unrecognized"]
    assert rep["unexpected_categories"]["participant_role"] == {"Nurse": 1}
    configured, _ = clean_survey(synthetic(1, Role=["Nurse"]), CleaningConfig(role_aliases={"nurse": "health_staff"}))
    assert configured.loc[0, "role_health_staff"]
    assert configured.loc[0, "role_primary"] == "health_staff"


def test_real_role_counts(report):
    assert report["roles"]["membership_counts"] == {
        "student": 1080, "school_staff": 68, "caregiver_parent": 71, "other_community": 95}
    assert report["roles"]["multi_select_rows"] == 14
    assert report["roles"]["trailing_semicolon_removed"] == 100


# ---------------------------------------------------------------------------
# Counties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,county,state,status,partner", [
    ("Montgomery\xa0", "Montgomery", "MD", "recognized", None),
    ("Montgomery County ", "Montgomery", "MD", "recognized", None),
    ("Montgomery County with NAMI MC", "Montgomery", "MD", "recognized", "NAMI MC"),
    ("Queen Anne's County ", "Queen Anne's", "MD", "recognized", None),
    ("Prince Georges", "Prince George's", "MD", "recognized", None),
    ("Fairfax county ", "Fairfax", "VA", "recognized", None),
    ("Baltimore City", "Baltimore City", "MD", "recognized", None),
    ("Baltimore County", "Baltimore County", "MD", "recognized", None),
    ("Baltimore", "Baltimore", None, "ambiguous", None),
    ("Harris", "Harris", None, "unrecognized", None),
])
def test_county_mapping(raw, county, state, status, partner):
    out, _ = clean_survey(synthetic(1, County=[raw]))
    row = out.loc[0]
    assert row["county"] == county
    assert (row["county_state"] if pd.notna(row["county_state"]) else None) == state
    assert row["county_status"] == status
    assert (row["county_partner"] if pd.notna(row["county_partner"]) else None) == partner
    assert row["county_raw"] == raw
    assert row["flag_county_needs_review"] == (status != "recognized")


def test_real_county_counts(cleaned):
    counts = cleaned["county"].value_counts().to_dict()
    assert counts == {"Anne Arundel": 937, "Prince George's": 309, "Montgomery": 34, "Queen Anne's": 12,
                      "Fairfax": 6, "Harris": 1}
    assert int(cleaned["flag_out_of_service_area"].sum()) == 6
    assert cleaned.loc[cleaned["county"] == "Harris", "in_service_area"].isna().all()


# ---------------------------------------------------------------------------
# Schema robustness
# ---------------------------------------------------------------------------

def test_column_order_does_not_matter(raw_df, cleaned):
    rng = np.random.default_rng(0)
    shuffled = raw_df[list(rng.permutation(raw_df.columns))]
    out, _ = clean_survey(shuffled)
    pd.testing.assert_frame_equal(out, cleaned)


def test_extra_columns_preserved_or_dropped(raw_df, cleaned):
    df = raw_df.copy()
    df["Session code"] = "S1"
    df["Email"] = "someone@example.org"
    out, rep = clean_survey(df)
    assert len(out) == len(raw_df)
    assert "extra__Session code" in out
    assert not any("Email" in c for c in out.columns)
    assert rep["columns"]["dropped_personal_data"] == ["Email"]
    assert "Session code" in rep["columns"]["preserved_as_extra"]
    pd.testing.assert_series_equal(out["post_describe_988"], cleaned["post_describe_988"])
    assert "extra__Session code" not in make_public_dataset(out)


def test_missing_optional_columns(raw_df, headers):
    drop = [headers[f] for f in ("likelihood_call_988", "sat_presenter_engaged", "sat_presenter_knowledge",
                                 "sat_well_organized", "language", "start_time")]
    out, rep = clean_survey(raw_df.drop(columns=drop))
    assert len(out) == 1299
    assert rep["status"] != "blocked"
    assert not rep["analyses"]["intent_to_call_988"]["available"]
    assert not rep["analyses"]["training_experience"]["available"]
    assert rep["analyses"]["confidence_change"]["available"]
    assert "flag_fast_completion" not in out
    assert "likelihood_call_988" in rep["fields"]["missing_optional"]


def test_available_pairs_analyzable_when_one_pair_missing(raw_df, headers, cleaned):
    out, rep = clean_survey(raw_df.drop(columns=[headers["pre_describe_988"]]))
    pairs = rep["confidence_pairs"]
    assert not pairs["describe_988"]["analyzable"]
    assert pairs["identify_mh"]["analyzable"] and pairs["know_when_988"]["analyzable"]
    assert pairs["identify_mh"]["both_valid"] == 1299
    assert rep["analyses"]["confidence_change"]["available"]
    assert "change_describe_988" not in out
    pd.testing.assert_series_equal(out["change_identify_mh"], cleaned["change_identify_mh"])
    assert out["post_describe_988"].notna().all()  # post item alone is still kept


def test_renamed_future_upload_maps():
    out, rep = clean_survey(synthetic(4))
    assert rep["fields"]["missing_core"] == []
    assert rep["status"] in ("ok", "warnings")
    assert out["change_identify_mh"].tolist() == [2, 2, 2, 2]
    assert out["county"].iloc[0] == "Howard"


def test_ambiguous_mapping_not_silently_accepted():
    df = synthetic(2, **{"Date of training": ["2026-10-01", "2026-10-02"]})
    out, rep = clean_survey(df)
    amb = {a["source"] for a in rep["columns"]["ambiguous"]}
    assert amb == {"Training date", "Date of training"}
    assert "training_date" in rep["fields"]["missing_core"]
    assert "training_date" not in out
    assert {"extra__Training date", "extra__Date of training"} <= set(out.columns)
    resolved, rep2 = clean_survey(df, CleaningConfig(column_overrides={"Date of training": "training_date"}))
    assert resolved["training_date"].iloc[0] == pd.Timestamp("2026-10-01")
    assert "training_date" not in rep2["fields"]["missing_core"]


def test_fuzzy_suggestion_not_applied():
    m = map_columns(["Countyy name of training"])
    assert m.matches[0].status in ("matched", "suggestion", "unmatched")
    m = map_columns(["Complition tme"])
    assert m.matches[0].status == "suggestion" and m.matches[0].field is None
    assert m.matches[0].candidates == ["completion_time"]


def test_value_check_rejects_misfit_column():
    # Header says Likert, values are numeric codes: do not guess direction.
    df = synthetic(3, **{"I can identify mental health challenges and crises.": [1, 5, 4]})
    out, rep = clean_survey(df)
    assert "post_identify_mh" not in out
    assert rep["columns"]["rejected_by_value_check"][0]["candidates"] == ["post_identify_mh"]


def test_unrelated_upload_is_blocked_not_crashing():
    out, rep = clean_survey(pd.DataFrame({"foo": [1, 2], "bar": ["x", "y"]}))
    assert rep["status"] == "blocked"
    assert len(out) == 2


def test_csv_round_trip(raw_df, cleaned, tmp_path):
    p = tmp_path / "upload.csv"
    raw_df.to_csv(p, index=False)
    df, sheet, _ = load_survey_file(p)
    out, _ = clean_survey(df)
    for col in LIKERT_FIELDS + ["county", "participant_role", "likelihood_recommend"]:
        assert out[col].astype(str).tolist() == cleaned[col].astype(str).tolist(), col


# ---------------------------------------------------------------------------
# Duplicates and suspicious patterns
# ---------------------------------------------------------------------------

def test_real_suspicious_counts(report, cleaned):
    assert report["duplicates"]["possible_duplicate_rows"] == 12
    assert report["duplicates"]["groups"] == 6
    sp = report["suspicious_patterns"]
    assert sp["fast_completion"] == 216
    assert sp["reverse_scale_suspect"] == 17
    assert sp["straightline"] == 163
    assert sp["post_below_pre"] == 108
    assert len(cleaned) == 1299  # flagged rows retained


def test_duplicates_flagged_not_removed():
    df = synthetic(3)
    df.loc[2, "County"] = "Montgomery"
    out, _ = clean_survey(df)
    assert out["flag_possible_duplicate"].tolist() == [True, True, False]
    assert len(out) == 3


def test_survey_versions_inferred(report):
    versions = {s["version"] for s in report["survey_versions"]["signatures"]}
    assert "experience+recommend" in versions
    assert "recommend+call_988" in versions  # Oct 2026 sessions without the experience block


# ---------------------------------------------------------------------------
# Privacy
# ---------------------------------------------------------------------------

def test_pii_detection_and_redaction():
    assert detect_pii("Call me at 301-555-0123") == ["phone"]
    assert "email" in detect_pii("write to a.b@example.com")
    assert detect_pii("Ms. Smith was great") == ["titled_name"]
    assert detect_pii("988 is the number to call") == []
    assert redact_pii("email a.b@example.com now") == "email [redacted] now"


def test_public_dataset_has_no_free_text(cleaned):
    public = make_public_dataset(cleaned)
    texts = {t for f in TEXT_FIELDS for t in cleaned[f].dropna() if len(t) >= 12}
    assert not (set(public.columns) & set(TEXT_FIELDS))
    assert not any(c.endswith("_raw") or c.startswith("extra__") for c in public.columns)
    for c in public.select_dtypes(include="object").columns:
        assert not set(public[c].dropna()) & texts, c
    assert public["fb_most_valuable_provided"].sum() == 1063


def test_no_sensitive_data_in_logs_or_report(raw_df, headers, caplog):
    header_text = " ".join(map(str, raw_df.columns)).lower()
    # Skip comments that merely repeat words from the question headers (e.g. "when to use 988").
    comments = [str(v) for f in TEXT_FIELDS for v in raw_df[headers[f]].dropna()
                if len(str(v)) >= 12 and str(v).strip().lower() not in header_text]
    assert comments
    with caplog.at_level(logging.DEBUG):
        _, rep = clean_survey(raw_df)
    blob = caplog.text + json.dumps(rep, default=str)
    leaked = [c for c in comments if c in blob]
    assert leaked == []


# ---------------------------------------------------------------------------
# Stage 3 cleaning rules
# ---------------------------------------------------------------------------

def test_real_missing_training_dates_not_filled(raw_df, cleaned, headers):
    missing = raw_df[headers["training_date"]].isna().to_numpy()
    assert missing.sum() == 3
    assert cleaned.loc[missing, "training_date"].isna().all()
    assert cleaned.loc[missing, "training_month"].isna().all()
    assert not cleaned.loc[missing, "training_date_valid"].any()


def test_value_check_separates_missing_from_invalid():
    from src.data_validation import check_column_values
    s = pd.Series(["Agree", None, "", "Very agree", "Disagree", np.nan, "Agree", "Agree", "Agree"])
    chk = check_column_values("post_identify_mh", s)
    assert chk["missing"] == 3 and chk["nonmissing"] == 6
    assert chk["valid"] == 5 and chk["invalid"] == 1
    assert chk["valid_share"] == round(5 / 6, 3)
    # a mostly blank column with valid non-missing answers is still compatible
    sparse = pd.Series(["Agree"] + [None] * 50)
    assert check_column_values("post_identify_mh", sparse)["ok"]


def test_confirmed_mapping_round_trip():
    cfg = CleaningConfig().confirm_mapping("Date of training", "training_date")
    restored = CleaningConfig.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert restored.column_overrides == {"Date of training": "training_date"}
    df = synthetic(2, **{"Date of training": ["2026-10-01", "2026-10-02"]})
    out, rep = clean_survey(df, restored)
    assert out["training_date"].iloc[0] == pd.Timestamp("2026-10-01")
    with pytest.raises(ValueError):
        CleaningConfig.from_dict({"not_a_setting": 1})


def test_partner_names_not_in_public_export_by_default(cleaned):
    assert "county_partner" not in make_public_dataset(cleaned)
    assert "county_partner" in make_public_dataset(cleaned, include_partner=True)
