"""Pure transformations from engine results (dicts) to tidy DataFrames and report text.

No Streamlit imports: everything here is unit-tested. Inputs are the outputs of
`src.run_analysis.run_all` and `src.clean_data.clean_survey`; nothing is recomputed
statistically here, values are only reshaped for display.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Iterable

import pandas as pd

from src.analysis_utils import OBJECTIVES

OBJ_LABELS = {o["key"]: o["label"] for o in OBJECTIVES}
AGREE_NOTE = "Agree = 'Agree' or 'Strongly agree'."
DIM_LABELS = {"county": "County", "role": "Participant role (overlapping)", "role_primary": "Primary role",
              "language": "Survey language"}


def _pct(x):
    return None if x is None else float(x)


# ---------------------------------------------------------------------------
# Q1
# ---------------------------------------------------------------------------

def objective_options(q1: dict) -> list[tuple[str, str]]:
    return [(o["key"], o["label"]) for o in q1.get("objectives", []) if not o.get("suppressed")]


def _objs(q1: dict, keys: Iterable[str] | None):
    keys = set(keys) if keys is not None else None
    return [o for o in q1.get("objectives", []) if not o.get("suppressed") and (keys is None or o["key"] in keys)]


def q1_summary_table(q1: dict, keys=None) -> pd.DataFrame:
    rows = []
    for o in _objs(q1, keys):
        rows.append({
            "Learning objective": o["label"], "Paired n": o["coverage"]["paired_n"],
            "Agree before (recalled) %": o["pre_agree"]["pct"], "Agree after %": o["post_agree"]["pct"],
            "After 95% CI": f"{o['post_agree']['ci_low']}–{o['post_agree']['ci_high']}",
            "Change (pp)": o["agree_pp_change"], "Improved %": o["change"]["improved"]["pct"],
            "Unchanged %": o["change"]["unchanged"]["pct"], "Declined %": o["change"]["declined"]["pct"],
            "Not agreeing after %": o["remaining_gap"]["not_agree_after"]["pct"],
            "Strongly agree before %": o["ceiling"]["pre_strongly_agree"]["pct"],
        })
    return pd.DataFrame(rows)


def q1_before_after(q1: dict, keys=None) -> pd.DataFrame:
    rows = []
    for o in _objs(q1, keys):
        for phase, s in (("Before (recalled)", o["pre_agree"]), ("After", o["post_agree"])):
            rows.append({"objective": o["key"], "label": o["label"], "phase": phase, "pct": s["pct"],
                         "ci_low": s["ci_low"], "ci_high": s["ci_high"], "k": s["k"], "n": s["n"]})
    return pd.DataFrame(rows)


def q1_change(q1: dict, keys=None) -> pd.DataFrame:
    rows = []
    for o in _objs(q1, keys):
        for cat in ("improved", "unchanged", "declined"):
            s = o["change"][cat]
            rows.append({"objective": o["key"], "label": o["label"], "category": cat.title(), "pct": s["pct"],
                         "k": s["k"], "n": s["n"]})
    return pd.DataFrame(rows)


def q1_pp_change(q1: dict, keys=None) -> pd.DataFrame:
    return pd.DataFrame([{"objective": o["key"], "label": o["label"], "change_pp": o["agree_pp_change"],
                          "n": o["coverage"]["paired_n"]} for o in _objs(q1, keys)])


def q1_gaps(q1: dict, keys=None) -> pd.DataFrame:
    rows = []
    for o in _objs(q1, keys):
        g = o["remaining_gap"]
        for comp, s in (("Neutral after training", g["neutral_after"]), ("Disagree after training", g["disagree_after"])):
            rows.append({"objective": o["key"], "label": o["label"], "component": comp, "pct": s["pct"],
                         "k": s["k"], "n": s["n"], "total_gap_pct": g["not_agree_after"]["pct"]})
    return pd.DataFrame(rows)


def q1_transition(q1: dict, key: str) -> pd.DataFrame | None:
    t = next((t for t in q1.get("transitions", []) if t["key"] == key), None)
    if not t or t.get("suppressed") or not t["cells"]:
        return None
    df = pd.DataFrame(t["cells"])
    return df.pivot(index="pre_label", columns="post_label", values="n").reindex(
        index=[c["pre_label"] for c in t["cells"][::5]], columns=[c["post_label"] for c in t["cells"][:5]])


def available_months(q1: dict) -> list[str]:
    return [m["month"] for m in q1.get("trend", {}).get("months", [])]


def q1_trend(q1: dict, keys=None) -> pd.DataFrame:
    t = q1.get("trend", {})
    rows = []
    for m in t.get("months", []):
        for o in _objs(q1, keys):
            k = o["key"]
            rows.append({"month": m["month"], "objective": k, "label": o["label"], "n_rows": m["n_rows"],
                         "n_sessions": m.get("n_sessions"), "paired_n": m.get(f"{k}_paired_n"),
                         "suppressed": m["suppressed"] or m.get(f"{k}_post_agree_pct") is None,
                         "low_n": m.get("below_min_compare", False),
                         "pre_agree_pct": m.get(f"{k}_pre_agree_pct"), "post_agree_pct": m.get(f"{k}_post_agree_pct"),
                         "change_pp": m.get(f"{k}_agree_pp_change"),
                         "not_agree_after_pct": m.get(f"{k}_not_agree_after_pct"),
                         "call_988_answered_pct": m.get("likelihood_call_988_valid_pct"),
                         "experience_answered_pct": m.get("sat_presenter_engaged_valid_pct")})
    return pd.DataFrame(rows)


def q1_month_counts(q1: dict) -> pd.DataFrame:
    return pd.DataFrame([{"month": m["month"], "n_rows": m["n_rows"], "n_sessions": m.get("n_sessions"),
                          "suppressed": m["suppressed"], "low_n": m.get("below_min_compare", False)}
                         for m in q1.get("trend", {}).get("months", [])])


def months_in_range(months: list[str], start: str, end: str) -> tuple[str, ...]:
    return tuple(m for m in months if start <= m <= end)


def period_subset(cleaned: pd.DataFrame, months: tuple[str, ...] | None) -> pd.DataFrame:
    """Rows whose valid training month is in `months` (None = all rows, including undated)."""
    if months is None:
        return cleaned
    return cleaned[cleaned["training_month"].isin(months)]


def sensitivity_table(q1: dict) -> pd.DataFrame:
    s = q1.get("sensitivity") or {}
    keys = [o["key"] for o in _objs(q1, None)]
    names = {"all_responses": "All responses (main results)",
             "exclude_reverse_scale_suspects": "Without possible reversed-scale responses",
             "exclude_fast_completions": "Without surveys completed in < 30 s",
             "exclude_possible_duplicates": "Without repeat possible duplicates",
             "exclude_straightliners": "Without identical answers on all 6 items (info only)",
             "exclude_all_suspicious": "Without all of the above (info only)"}
    rows = []
    for r in s.get("scenarios", []):
        row = {"Scenario": names.get(r["scenario"], r["scenario"]), "Rows": r["rows"]}
        for k in keys:
            row[f"{k} change pp"] = r.get(f"{k}_agree_pp_change")
        row["Max shift pp"] = r.get("max_shift_pp")
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------

def q2_experience_summary(q2: dict) -> pd.DataFrame:
    rows = []
    for e in q2.get("experience", []):
        cov = e["coverage"]
        if not cov["collected"]:
            continue
        rows.append({"Item": e["label"], "Valid n": cov["valid"], "Missing": cov["missing"],
                     "Unusable (off-scale)": cov["unusable"],
                     "Agree %": (e.get("agree") or {}).get("pct"),
                     "Agree 95% CI": (f"{e['agree']['ci_low']}–{e['agree']['ci_high']}" if e.get("agree") else None),
                     "Strongly agree %": (e.get("strongly_agree") or {}).get("pct"), "Mean (1–5)": e.get("mean")})
    return pd.DataFrame(rows)


def q2_experience_dist(q2: dict) -> pd.DataFrame:
    rows = []
    for e in q2.get("experience", []):
        for d in e.get("distribution", []) or []:
            rows.append({"item": e["label"], "score": d["score"], "answer": d["label"], "n": d["n"], "pct": d["pct"],
                         "valid_n": e["coverage"]["valid"]})
    return pd.DataFrame(rows)


def rating_dist(section: dict) -> pd.DataFrame:
    rows = []
    for d in section.get("distribution", []) or []:
        band = "0-6" if d["score"] <= 6 else "7-8" if d["score"] <= 8 else "9-10"
        rows.append({"score": d["score"], "n": d["n"], "pct": d["pct"], "band": band})
    return pd.DataFrame(rows)


FAMILY_LABELS = {
    "experience_vs_post_confidence": "Training experience ↔ confidence after",
    "experience_vs_confidence_change": "Training experience ↔ confidence change",
    "experience_vs_recommend": "Training experience ↔ recommend",
    "experience_item_vs_post_confidence": "Each experience item ↔ confidence after",
    "recommend_vs_confidence_change": "Recommend ↔ confidence change",
    "post_confidence_vs_call_988": "Confidence after ↔ likelihood to call 988",
    "confidence_change_vs_call_988": "Confidence change ↔ likelihood to call 988",
}


def q2_relationships(q2: dict, families: Iterable[str] | None = None) -> pd.DataFrame:
    fams = set(families) if families is not None else None
    rows = [{"family": FAMILY_LABELS.get(r["family"], r["family"]), "family_key": r["family"],
             "pair": f"{r['x']} ↔ {r['y']}", "x": r["x"], "y": r["y"], "rho": r["rho"], "n": r["n"],
             "p_value": r["p_value"], "strength": r["strength"]}
            for r in q2.get("relationships", []) if fams is None or r["family"] in fams]
    return pd.DataFrame(rows)


def q2_sat_gap(q2: dict) -> pd.DataFrame:
    rows = []
    defs = q2.get("satisfaction_vs_gap", {}).get("definitions", {})
    for r in q2.get("satisfaction_vs_gap", {}).get("rows", []):
        if r.get("suppressed"):
            continue
        for who, s in (("In group", r["group_not_agree_after"]), ("Everyone else", r["others_not_agree_after"])):
            rows.append({"objective": r["label"], "group": r["group"], "definition": defs.get(r["group"], ""),
                         "who": who, "pct": s["pct"], "k": s["k"], "n": s["n"]})
    return pd.DataFrame(rows)


def q2_themes(q2: dict, field: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    fb = q2.get("feedback", {}).get(field, {})
    if not fb.get("available"):
        return pd.DataFrame(), pd.DataFrame(), fb
    themes = pd.DataFrame([t for t in fb["themes"] if t["n"] > 0])
    types = pd.DataFrame(fb["response_types"])
    return themes, types, fb


# ---------------------------------------------------------------------------
# Q3
# ---------------------------------------------------------------------------

def q3_dimensions(q3: dict) -> list[str]:
    return [d for d, g in q3.get("groupings", {}).items() if g.get("available")]


def q3_group_table(q3: dict, dim: str) -> pd.DataFrame:
    g = q3["groupings"][dim]
    rows = []
    for r in g["groups"]:
        status = ("Suppressed (<10)" if r["suppressed"] else
                  "Comparable" if r["comparable"] else "Descriptive only (<30)")
        row = {"Group": r["label"], "Respondents": r["n_display"], "Status": status,
               "Sessions": r.get("n_sessions")}
        for k, v in (r.get("objectives") or {}).items():
            if v.get("suppressed"):
                continue
            row[f"{k}: before %"] = (v.get("pre_agree") or {}).get("pct")
            row[f"{k}: after %"] = (v.get("post_agree") or {}).get("pct")
            row[f"{k}: not agreeing after %"] = (v.get("not_agree_after") or {}).get("pct")
        if not r["suppressed"]:
            row["Experience mean (1–5)"] = (r.get("experience") or {}).get("mean")
            row["Recommend mean (0–10)"] = (r.get("recommend") or {}).get("mean")
            row["Call-988 answers"] = (r.get("call_988") or {}).get("valid_n_display")
        rows.append(row)
    return pd.DataFrame(rows)


def q3_outcomes(q3: dict, dim: str, objective: str, include_descriptive: bool = False) -> pd.DataFrame:
    """Before/after agreement per group (never includes suppressed groups)."""
    rows = []
    for r in q3["groupings"][dim]["groups"]:
        if r["suppressed"] or (not r["comparable"] and not include_descriptive):
            continue
        v = (r.get("objectives") or {}).get(objective)
        if not v or v.get("suppressed") or not v.get("post_agree"):
            continue
        for phase, s in (("Before (recalled)", v.get("pre_agree")), ("After", v.get("post_agree"))):
            if s:
                rows.append({"group": r["label"], "phase": phase, "pct": s["pct"], "ci_low": s["ci_low"],
                             "ci_high": s["ci_high"], "n": s["n"], "comparable": r["comparable"]})
    return pd.DataFrame(rows)


def q3_gap(q3: dict, dim: str, objective: str, include_descriptive: bool = False) -> pd.DataFrame:
    rows = []
    for r in q3["groupings"][dim]["groups"]:
        if r["suppressed"] or (not r["comparable"] and not include_descriptive):
            continue
        v = (r.get("objectives") or {}).get(objective)
        if v and not v.get("suppressed") and v.get("not_agree_after"):
            s = v["not_agree_after"]
            rows.append({"group": r["label"], "pct": s["pct"], "ci_low": s["ci_low"], "ci_high": s["ci_high"],
                         "n": s["n"], "comparable": r["comparable"]})
    return pd.DataFrame(rows)


def q3_experience(q3: dict, dim: str, include_descriptive: bool = False) -> pd.DataFrame:
    rows = []
    for r in q3["groupings"][dim]["groups"]:
        if r["suppressed"] or (not r["comparable"] and not include_descriptive):
            continue
        e, rec = r.get("experience") or {}, r.get("recommend") or {}
        if e.get("agree"):
            rows.append({"group": r["label"], "measure": "Experience: agree (mean of 3 items ≥ 4)",
                         "pct": e["agree"]["pct"], "ci_low": e["agree"]["ci_low"], "ci_high": e["agree"]["ci_high"],
                         "n": e["agree"]["n"]})
        if rec.get("high_9_10"):
            s = rec["high_9_10"]
            rows.append({"group": r["label"], "measure": "Recommend: 9–10", "pct": s["pct"], "ci_low": s["ci_low"],
                         "ci_high": s["ci_high"], "n": s["n"]})
    return pd.DataFrame(rows)


def q3_missingness(q3: dict, dim: str) -> pd.DataFrame:
    rows = []
    for r in q3["groupings"][dim]["groups"]:
        if r["suppressed"]:
            continue
        m = r.get("missingness_pct", {})
        rows.append({"Group": r["label"], "Respondents": r["n_display"],
                     "Experience items missing %": m.get("experience"),
                     "Recommend missing %": m.get("likelihood_recommend"),
                     "Call-988 missing %": m.get("likelihood_call_988"),
                     "Training date missing/invalid %": m.get("training_date"),
                     "Survey versions": "; ".join(f"{v['version']} ({v['pct']}%)" for v in r.get("survey_versions", []))})
    return pd.DataFrame(rows)


def q3_signals(q3: dict, dim: str) -> pd.DataFrame:
    sig = q3["groupings"][dim].get("comparison", {}).get("signals", [])
    metric = {"agree_pp_change": "Change in agreement (pp)", "post_agree": "Agree after (%)"}
    return pd.DataFrame([{"Group": s["label"], "Objective": OBJ_LABELS.get(s["objective"], s["objective"]), "Measure": metric.get(s["metric"], s["metric"]),
                          "Group value": s["group_value"], "Group n": s["group_n"], "Everyone else": s["rest_value"],
                          "Others n": s["rest_n"], "Difference (pp)": s["difference_pp"]} for s in sig])


def q3_composition(q3: dict, objective: str) -> pd.DataFrame:
    c = q3.get("composition_check", {})
    rows = []
    for r in c.get("standardized", []):
        if r["objective"] != objective:
            continue
        for kind, key in (("As observed", "crude"), ("Adjusted to overall student mix", "standardized")):
            for phase in ("pre", "post"):
                v = r.get(f"{key}_{phase}_agree_pct")
                if v is not None:
                    rows.append({"county": r["county"], "version": kind,
                                 "phase": "Before (recalled)" if phase == "pre" else "After", "pct": v,
                                 "n": r["paired_n"]})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Data quality and mapping
# ---------------------------------------------------------------------------

STATUS_TEXT = {"matched": "Recognized", "ambiguous": "Ambiguous – needs confirmation",
               "suggestion": "Possible match – needs confirmation", "unmatched": "Not recognized (kept as extra)",
               "ignored": "Ignored by user", "rejected": "Values do not fit – needs confirmation",
               "pii_dropped": "Looks like personal data – not loaded"}


def mapping_table(records: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([{"Uploaded column": r["source"], "Status": STATUS_TEXT.get(r["status"], r["status"]),
                          "Mapped to": r["field"] or "", "Candidates": ", ".join(r["candidates"] or []),
                          "How": r["method"], "Note": r["note"]} for r in records])


def needs_confirmation(records: list[dict]) -> list[dict]:
    return [r for r in records if r["status"] in ("ambiguous", "suggestion", "rejected")]


def analyses_table(report: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Analysis": v["label"], "Available": "Yes" if v["available"] else "No",
                          "Missing fields": ", ".join(v["missing_fields"]) or "—"}
                         for v in report.get("analyses", {}).values()])


def missingness_table(report: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Field": k, "Valid": v["valid"], "Valid %": v["valid_pct"], "Blank": v["missing"],
                          "Present but unusable": v["unusable"]} for k, v in report.get("missingness", {}).items()])


FLAG_TEXT = {
    "flag_fast_completion": "Completed in under 30 seconds",
    "flag_reverse_scale_suspect": "Possible reversed scale",
    "flag_straightline": "Same answer on all 6 confidence items",
    "flag_post_below_pre": "A confidence answer lower after than before",
    "flag_possible_duplicate": "Possible duplicate submission",
    "flag_offscale_label": "Answer label outside the agreement scale",
    "flag_training_date_missing": "Training date missing",
    "flag_training_date_invalid": "Training date not a valid date",
    "flag_training_date_implausible": "Training date before 988 launch",
    "flag_training_date_after_completion": "Training date after survey submission",
    "flag_training_date_gap_gt_30d": "Survey > 30 days after training",
    "flag_county_needs_review": "County unrecognized or ambiguous",
    "flag_out_of_service_area": "County outside service area",
    "flag_text_pii": "Comment may contain personal information",
    "flag_invalid_rating": "0–10 rating not valid",
    "flag_role_unrecognized": "Unrecognized role",
    "flag_duplicate_response_id": "Repeated response ID",
    "flag_completion_before_start": "Completion before start time",
}


def flags_table(report: dict) -> pd.DataFrame:
    n = report["rows"]["output"] or 1
    return pd.DataFrame([{"Check": FLAG_TEXT.get(k, k), "Rows flagged": v, "% of rows": round(100 * v / n, 1)}
                         for k, v in report.get("flags", {}).items() if v]).sort_values(
        "Rows flagged", ascending=False) if any(report.get("flags", {}).values()) else pd.DataFrame()


def warnings_table(report: dict) -> pd.DataFrame:
    order = {"high": 0, "medium": 1, "low": 2}
    w = sorted(report.get("warnings", []), key=lambda x: order.get(x["severity"], 3))
    return pd.DataFrame([{"Severity": x["severity"].title(), "Affects": x["analysis"], "Message": x["message"]} for x in w])


def standardization_lines(report: dict) -> list[str]:
    labels = {
        "language_invisible_marks_removed": "Invisible characters removed from language labels",
        "role_trailing_semicolon_removed": "Trailing semicolons removed from roles",
        "county_labels_collapsed": "County spellings standardized",
        "county_rows_relabelled": "Rows with a standardized county name",
        "likert_hidden_chars_removed": "Hidden spaces removed from agreement answers",
        "likert_wording_variant": "'Neither agree or disagree' treated as 'Neither agree nor disagree'",
        "likert_case_variant": "Capitalization differences in agreement answers",
        "likert_user_override_applied": "User-approved label mappings applied",
    }
    out = []
    for k, v in report.get("standardization", {}).items():
        if not v:
            continue
        if isinstance(v, dict):
            v = sum(v.values())
        out.append(f"{labels.get(k, k)}: {v}")
    return out


# ---------------------------------------------------------------------------
# Overview, exports and report text
# ---------------------------------------------------------------------------

def overview_kpis(results: dict, report: dict) -> dict:
    q1, q2 = results["q1"], results["q2"]
    objs = _objs(q1, None)
    gap = max(objs, key=lambda o: o["remaining_gap"]["not_agree_after"]["pct"]) if objs else None
    gain = max(objs, key=lambda o: o["agree_pp_change"]) if objs else None
    exp = [e for e in q2.get("experience", []) if e.get("agree")]
    low_exp = min(exp, key=lambda e: e["agree"]["pct"]) if exp else None
    call = q2.get("call_988", {})
    rec = q2.get("recommendation", {})
    return {
        "responses": results["n_rows"],
        "paired_n": min((o["coverage"]["paired_n"] for o in objs), default=0),
        "objectives": [{"label": o["label"], "pre": o["pre_agree"]["pct"], "post": o["post_agree"]["pct"],
                        "change": o["agree_pp_change"], "gap": o["remaining_gap"]["not_agree_after"]["pct"],
                        "gap_k": o["remaining_gap"]["not_agree_after"]["k"], "n": o["coverage"]["paired_n"]}
                       for o in objs],
        "largest_gap": gap and {"label": gap["label"], "pct": gap["remaining_gap"]["not_agree_after"]["pct"],
                                "k": gap["remaining_gap"]["not_agree_after"]["k"], "n": gap["coverage"]["paired_n"]},
        "largest_gain": gain and {"label": gain["label"], "pp": gain["agree_pp_change"]},
        "lowest_experience": low_exp and {"label": low_exp["label"], "pct": low_exp["agree"]["pct"],
                                          "n": low_exp["agree"]["n"]},
        "recommend": rec.get("summary") and {"mean": rec["summary"]["mean"], "n": rec["summary"]["n"],
                                             "promoters": rec["promoters_9_10"]["pct"],
                                             "detractors": rec["detractors_0_6"]["pct"]},
        "call_988": call.get("coverage", {}).get("collected") and {
            "valid": call["coverage"]["valid"], "rows": call["coverage"]["rows"],
            "valid_pct": call["coverage"]["valid_pct"],
            "mean": (call.get("summary") or {}).get("mean")},
        "status": report.get("status"),
        "high_warnings": [w["message"] for w in report.get("warnings", []) if w["severity"] == "high"],
    }


def community_highlights(q3: dict) -> list[str]:
    out = []
    comp = q3.get("composition_check", {})
    min_compare = q3.get("policy", {}).get("min_compare", 30)
    big = [m for m in comp.get("county_role_mix", []) if m["n"] >= min_compare]
    if comp.get("available") and len(big) >= 2:
        mix = ", ".join(f"{m['county']} {m['student_pct']}%" for m in big)
        out.append(f"Student share of respondents differs by county ({mix}; overall "
                   f"{comp['overall_mix_pct'].get('student')}%), so county differences may reflect who attended.")
    for dim in ("county", "role_primary", "language"):
        g = q3.get("groupings", {}).get(dim, {})
        sig = sorted(g.get("comparison", {}).get("signals", []), key=lambda s: -abs(s["difference_pp"]))[:2]
        for s in sig:
            what = "change in agreement" if s["metric"] == "agree_pp_change" else "agreement after training"
            obj = OBJ_LABELS.get(s["objective"], s["objective"]).lower()
            out.append(f"{s['label']} ({DIM_LABELS.get(dim, dim).lower()}): {what} for '{obj}' "
                       f"{s['group_value']} vs {s['rest_value']} for others (n = {s['group_n']}). Descriptive only.")
    small = q3.get("small_group_follow_ups", [])
    if small:
        out.append(f"{len(small)} groups are too small to compare. This reflects data collection, not access.")
    return out


def tables_zip(results: dict, report: dict) -> bytes:
    """Key aggregate tables as CSV files in a zip (no participant-level data)."""
    q1, q2, q3 = results["q1"], results["q2"], results["q3"]
    tables = {
        "q1_objectives.csv": q1_summary_table(q1), "q1_trend_by_month.csv": q1_trend(q1),
        "q1_sensitivity.csv": sensitivity_table(q1), "q2_experience.csv": q2_experience_summary(q2),
        "q2_recommend_distribution.csv": rating_dist(q2.get("recommendation", {})),
        "q2_call_988_distribution.csv": rating_dist(q2.get("call_988", {})),
        "q2_relationships.csv": q2_relationships(q2), "q2_satisfaction_vs_gap.csv": q2_sat_gap(q2),
        "data_quality_flags.csv": flags_table(report), "data_quality_warnings.csv": warnings_table(report),
    }
    for f in ("fb_most_valuable", "fb_improvements"):
        themes, types, _ = q2_themes(q2, f)
        tables[f"q2_themes_{f}_PROVISIONAL.csv"] = themes
        tables[f"q2_response_types_{f}_PROVISIONAL.csv"] = types
    for dim in q3_dimensions(q3):
        tables[f"q3_groups_{dim}.csv"] = q3_group_table(q3, dim)
        tables[f"q3_signals_{dim}.csv"] = q3_signals(q3, dim)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, df in tables.items():
            z.writestr(name, df.to_csv(index=False))
        z.writestr("README.txt", "Aggregate tables from the EveryMind 988 training dashboard. Groups with fewer than "
                                 "10 respondents are suppressed. Theme counts are provisional (keyword-based).\n")
    return buf.getvalue()


def executive_summary_md(results: dict, report: dict, dataset_label: str = "") -> str:
    k = overview_kpis(results, report)
    recs = results["recommendations"]["recommendations"]
    L = ["# EveryMind 988 Training – Executive Summary", "",
         f"_Dataset: {dataset_label or 'uploaded survey'} · {k['responses']} responses · generated from verified "
         "aggregate results (no participant-level data)._", "",
         "**Business question:** How can EveryMind use its 988 training feedback data to develop an actionable "
         "solution for evaluating training effectiveness, identifying gaps across communities, and continuously "
         "improving future trainings?", "",
         "## Training effectiveness (Q1)", "",
         "'Before' ratings were recalled after the training, so changes are self-reported and not proof of cause. "
         + AGREE_NOTE, ""]
    for o in k["objectives"]:
        L.append(f"- **{o['label']}:** {o['pre']}% before → {o['post']}% after ({o['change']:+} pp); "
                 f"{o['gap']}% ({o['gap_k']} of {o['n']}) still not agreeing after training.")
    L += ["", "## Training experience (Q2)", ""]
    if k["lowest_experience"]:
        le = k["lowest_experience"]
        L.append(f"- Lowest-rated experience item: {le['label']} ({le['pct']}% agree, n = {le['n']}).")
    if k["recommend"]:
        r = k["recommend"]
        L.append(f"- Likelihood to recommend: mean {r['mean']} / 10 (n = {r['n']}); {r['promoters']}% gave 9–10, "
                 f"{r['detractors']}% gave 0–6.")
    if k["call_988"]:
        c = k["call_988"]
        L.append(f"- Likelihood to call 988 was asked of only {c['valid']} of {c['rows']} respondents "
                 f"({c['valid_pct']}%); results apply only to them.")
    L += ["", "## Communities and populations (Q3)", ""]
    L += [f"- {h}" for h in community_highlights(results["q3"])] or ["- No group differences met the signal rule."]
    L += ["", "## Data-quality limitations", ""]
    L += [f"- {w}" for w in k["high_warnings"]] or ["- No high-severity warnings."]
    L += ["", "## Top recommendations (rule-based, evidence-linked)", ""]
    for r in [r for r in recs if r["priority"] == "high"][:5] + [r for r in recs if r["priority"] == "medium"][:3]:
        L += [f"### {r['id']} · {r['priority'].title()} · {r['category'].replace('_', ' ')}",
              f"- **Finding:** {r['finding']}", f"- **Action:** {r['action']}",
              f"- **Implementation:** {r['implementation']}", f"- **Success measure:** {r['success_metric']}", ""]
    return "\n".join(L)


def rule_recommendations_md(results: dict) -> str:
    from src.run_analysis import summary_recommendations
    return summary_recommendations(results["recommendations"])


def ai_recommendations_md(ai: dict) -> str:
    L = ["# AI-drafted recommendations (ADVISORY – not verified findings)", "",
         f"_Model: {ai['model']} · generated {ai['created_at']} · evidence fingerprint {ai['fingerprint']}. "
         "Every recommendation cites evidence IDs from the verified aggregate summary and passed automated "
         "checks; it still requires human review._", ""]
    if ai.get("overall_note"):
        L += [f"> {ai['overall_note']}", ""]
    for i, r in enumerate(ai.get("recommendations", []), 1):
        L += [f"## AI-{i}. {r['title']} ({r['priority']})", f"- **Issue:** {r['issue']}", f"- **Action:** {r['action']}",
              "- **Implementation steps:**"] + [f"  {j}. {s}" for j, s in enumerate(r["implementation_steps"], 1)] + [
              f"- **Responsible role:** {r['responsible_role']}", f"- **Success indicator:** {r['success_indicator']}",
              f"- **Limitations:** {r['limitations']}",
              "- **Evidence:** " + "; ".join(f"`{e['id']}` {e['statement']}" for e in r["evidence"]), ""]
    if ai.get("rejected"):
        L += ["## Rejected by validation", ""] + [f"- {x['title']}: {'; '.join(x['reasons'])}" for x in ai["rejected"]]
    return "\n".join(L)


def report_json_bytes(report: dict) -> bytes:
    return json.dumps(report, indent=2, default=str).encode("utf-8")
