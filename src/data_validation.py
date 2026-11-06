"""Validation helpers and the structured data-quality report.

Everything here works on in-memory objects and has no file or network side
effects. The quality report is a JSON-serializable dict with aggregate counts
only: it never contains free-text comments or other participant-level values.
"""

from __future__ import annotations

import re
from datetime import date, datetime

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schema_mapper import (
    FIELD_ORDER, LIKERT_SCORES, PRE_POST_PAIRS, RATING_FIELDS, SAT_FIELDS, SCHEMA, SCHEMA_BY_NAME,
    TEXT_FIELDS, SchemaMapping, clean_label,
)

REPORT_VERSION = "1.0"

# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

_RATING_RE = re.compile(r"[+-]?\d+(\.0+)?")


def parse_rating(value: object, lo: int = 0, hi: int = 10) -> tuple[float, str]:
    """Return (value, status) with status in {'valid', 'missing', 'invalid'}.

    Accepts integers (or integral floats / numeric strings) in [lo, hi] only.
    Text such as '10 - extremely likely' is *invalid*, not guessed.
    """
    if isinstance(value, (bool, np.bool_)):
        return np.nan, "invalid"
    if isinstance(value, (int, float, np.integer, np.floating)):
        if np.isnan(value):
            return np.nan, "missing"
        x = float(value)
    else:
        s = clean_label(value)
        if s is None:
            return np.nan, "missing"
        if not _RATING_RE.fullmatch(s):
            return np.nan, "invalid"
        x = float(s)
    if x != int(x) or not lo <= x <= hi:
        return np.nan, "invalid"
    return x, "valid"


_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def parse_datetime_value(value: object) -> tuple[pd.Timestamp, str]:
    """Return (timestamp, status) with status in {'valid', 'missing', 'invalid'}.

    Handles datetime objects, ISO strings, US-style m/d/y strings and Excel
    serial numbers. Timezones are dropped (exports are in local time).
    """
    if isinstance(value, (pd.Timestamp, datetime, date)):
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return pd.NaT, "missing"
        return (ts.tz_localize(None) if ts.tzinfo else ts), "valid"
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)):
        if np.isnan(value):
            return pd.NaT, "missing"
        if 20000 <= value <= 80000:  # Excel serial date (1954-2119)
            return _EXCEL_EPOCH + pd.to_timedelta(float(value), unit="D"), "valid"
        return pd.NaT, "invalid"
    s = clean_label(value)
    if s is None:
        return pd.NaT, "missing"
    try:
        ts = pd.Timestamp(s)
    except (ValueError, TypeError, OverflowError):
        return pd.NaT, "invalid"
    if pd.isna(ts):
        return pd.NaT, "invalid"
    return (ts.tz_convert(None) if ts.tzinfo else ts), "valid"


def parse_datetime_series(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    parsed = [parse_datetime_value(v) for v in s]
    values = pd.Series([p[0] for p in parsed], index=s.index, dtype="datetime64[ns]")
    status = pd.Series([p[1] for p in parsed], index=s.index, dtype="object")
    return values, status


# ---------------------------------------------------------------------------
# Privacy: PII detection in free text
# ---------------------------------------------------------------------------

PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone": re.compile(r"(?<!\d)(\+?1[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}(?!\d)"),
    "url": re.compile(r"https?://\S+|www\.\S+", re.I),
    "social_handle": re.compile(r"(?<![\w.])@[A-Za-z_][\w.]{2,}"),
    "titled_name": re.compile(r"\b(Mr|Mrs|Ms|Miss|Mx|Dr|Coach|Professor|Prof)\.?\s+[A-Z][a-z]+"),
    "street_address": re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
        r"Court|Ct|Way)\b"),
}


def detect_pii(text: object) -> list[str]:
    """Return the PII pattern types found in `text` (empty list if none)."""
    s = clean_label(text)
    if s is None:
        return []
    return [name for name, pat in PII_PATTERNS.items() if pat.search(s)]


def redact_pii(text: object, placeholder: str = "[redacted]") -> str | None:
    s = clean_label(text)
    if s is None:
        return None
    for pat in PII_PATTERNS.values():
        s = pat.sub(placeholder, s)
    return s


# ---------------------------------------------------------------------------
# Mapped-column value checks
# ---------------------------------------------------------------------------

VALUE_CHECK_MIN_SHARE = 0.8


def check_column_values(field_name: str, s: pd.Series, likert_scores: dict[str, int] | None = None) -> dict:
    """Sanity-check that a column's values fit the field it was mapped to.

    Missing (blank) values are counted separately and never count against the
    column: the compatibility share is valid / non-missing. Returns
    {'ok', 'rows', 'missing', 'nonmissing', 'valid', 'invalid', 'valid_share'}.
    Columns with no non-missing values pass (nothing contradicts the header).
    """
    f = SCHEMA_BY_NAME[field_name]
    present = [v for v in s if clean_label(v) is not None]
    base = {"rows": int(len(s)), "missing": int(len(s) - len(present)), "nonmissing": len(present)}
    if not present or f.dtype in ("string", "category", "multi-select", "free_text"):
        return {"ok": True, **base, "valid": None, "invalid": None, "valid_share": None}
    if f.dtype == "likert_agree_5":
        scores = likert_scores or LIKERT_SCORES
        good = sum((clean_label(v) or "").lower() in scores for v in present)
    elif f.dtype == "int_0_10":
        good = sum(parse_rating(v)[1] == "valid" for v in present)
    else:  # date / datetime
        good = sum(parse_datetime_value(v)[1] == "valid" for v in present)
    share = good / len(present)
    return {"ok": share >= VALUE_CHECK_MIN_SHARE, **base, "valid": int(good), "invalid": int(len(present) - good),
            "valid_share": round(share, 3)}


# ---------------------------------------------------------------------------
# Analysis readiness
# ---------------------------------------------------------------------------
# An analysis is available when at least one field group in `any_of` is fully present.

ANALYSES: dict[str, dict] = {
    "confidence_change": {
        "label": "Retrospective pre/post confidence change",
        "any_of": [[pre, post] for pre, post in PRE_POST_PAIRS], "core": True},
    "post_confidence": {
        "label": "Post-training confidence levels",
        "any_of": [[post] for _, post in PRE_POST_PAIRS], "core": True},
    "training_experience": {
        "label": "Presenter and organization ratings",
        "any_of": [[f] for f in SAT_FIELDS], "core": True},
    "recommendation": {"label": "Likelihood to recommend (0-10)", "any_of": [["likelihood_recommend"]], "core": True},
    "intent_to_call_988": {"label": "Likelihood to call 988 (0-10)", "any_of": [["likelihood_call_988"]], "core": True},
    "trends_over_time": {"label": "Trends over time", "any_of": [["training_date"], ["completion_time"]]},
    "county_comparison": {"label": "Comparison across counties", "any_of": [["county"]]},
    "role_comparison": {"label": "Comparison across participant roles", "any_of": [["participant_role"]]},
    "language_comparison": {"label": "Comparison across survey languages", "any_of": [["language"]]},
    "feedback_themes": {"label": "Written feedback themes", "any_of": [[f] for f in TEXT_FIELDS]},
    "completion_quality": {"label": "Completion-time quality checks", "any_of": [["start_time", "completion_time"]]},
}


def analysis_availability(present_fields: list[str]) -> dict[str, dict]:
    present = set(present_fields)
    out = {}
    for name, spec in ANALYSES.items():
        groups = [g for g in spec["any_of"] if set(g) <= present]
        needed = sorted({f for g in spec["any_of"] for f in g} - present)
        out[name] = {
            "label": spec["label"],
            "available": bool(groups),
            "core": spec.get("core", False),
            "using": sorted({f for g in groups for f in g}),
            "missing_fields": needed,
        }
    return out


# ---------------------------------------------------------------------------
# Invariants on cleaned output (used by the pipeline and tests)
# ---------------------------------------------------------------------------

def validate_cleaned(raw_df: pd.DataFrame, cleaned: pd.DataFrame) -> list[str]:
    """Return a list of violated invariants (empty list = all good)."""
    problems = []
    if len(cleaned) != len(raw_df):
        problems.append(f"row count changed: {len(raw_df)} -> {len(cleaned)}")
    if "response_id" in cleaned and cleaned["response_id"].isna().any():
        problems.append("response_id has missing values")
    for f in SCHEMA:
        if f.name not in cleaned:
            continue
        s = cleaned[f.name].dropna()
        if f.dtype == "likert_agree_5" and not s.between(1, 5).all():
            problems.append(f"{f.name} has scores outside 1-5")
        if f.dtype == "int_0_10" and not s.between(0, 10).all():
            problems.append(f"{f.name} has ratings outside 0-10")
    return problems


# ---------------------------------------------------------------------------
# Quality report
# ---------------------------------------------------------------------------

def _pct(n: int, d: int) -> float:
    return round(100 * n / d, 1) if d else 0.0


def _count(s: pd.Series) -> int:
    return int(s.fillna(False).astype(bool).sum())


def build_quality_report(raw_df: pd.DataFrame, cleaned: pd.DataFrame, mapping: SchemaMapping,
                         stats: dict, config) -> dict:
    n = len(cleaned)
    present = mapping.present_fields()
    analyses = analysis_availability(present)
    core_ok = any(a["available"] for a in analyses.values() if a["core"])

    matched = [{"source": m.source, "field": m.field, "method": m.method} for m in mapping.with_status("matched")]
    columns = {
        "input_count": int(raw_df.shape[1]),
        "recognized": matched,
        "unrecognized": [m.source for m in mapping.with_status("unmatched")],
        "ambiguous": [{"source": m.source, "candidates": m.candidates, "note": m.note}
                      for m in mapping.with_status("ambiguous")],
        "suggestions": [{"source": m.source, "suggested_field": m.candidates[0], "score": m.score}
                        for m in mapping.with_status("suggestion")],
        "rejected_by_value_check": [{"source": m.source, "candidates": m.candidates, "note": m.note}
                                    for m in mapping.with_status("rejected")],
        "ignored_by_user": [m.source for m in mapping.with_status("ignored")],
        "dropped_personal_data": [m.source for m in mapping.with_status("pii_dropped")],
        "preserved_as_extra": stats.get("extra_columns", []),
    }

    missingness = {}
    for f in present:
        col = f if f in cleaned else None
        if col is None:
            continue
        miss = int(cleaned[col].isna().sum())
        raw_col = f"{f}_raw" if f"{f}_raw" in cleaned else None
        blank_raw = int(cleaned[raw_col].isna().sum()) if raw_col else miss
        missingness[f] = {
            "missing": blank_raw, "missing_pct": _pct(blank_raw, n),
            # values that were present but could not be used (off-scale / invalid)
            "unusable": miss - blank_raw, "valid": n - miss, "valid_pct": _pct(n - miss, n),
        }

    pairs = {}
    for pre, post in PRE_POST_PAIRS:
        name = post.removeprefix("post_")
        has_both = pre in present and post in present
        both = int((cleaned[pre].notna() & cleaned[post].notna()).sum()) if has_both else 0
        pairs[name] = {
            "pre_field": pre, "post_field": post, "analyzable": has_both and both > 0,
            "pre_present": pre in present, "post_present": post in present,
            "pre_valid": int(cleaned[pre].notna().sum()) if pre in present else 0,
            "post_valid": int(cleaned[post].notna().sum()) if post in present else 0,
            "both_valid": both,
        }

    flag_cols = sorted(c for c in cleaned.columns if c.startswith("flag_"))
    flags = {c: _count(cleaned[c]) for c in flag_cols}

    dup = {
        "possible_duplicate_rows": flags.get("flag_possible_duplicate", 0),
        "groups": int(cleaned["duplicate_group"].nunique()) if "duplicate_group" in cleaned else 0,
        "groups_with_identical_free_text": stats.get("duplicate_groups_with_text", 0),
        "duplicate_response_ids": flags.get("flag_duplicate_response_id", 0),
    }

    suspicious_keys = ["flag_fast_completion", "flag_completion_before_start", "flag_reverse_scale_suspect",
                       "flag_straightline", "flag_post_below_pre"]
    suspicious = {k.removeprefix("flag_"): flags[k] for k in suspicious_keys if k in flags}

    privacy = {
        "text_fields": {f: {"responses": int(cleaned[f].notna().sum()),
                            "pii_flagged": _count(cleaned[f"{f}_pii"])}
                        for f in TEXT_FIELDS if f in cleaned},
        "pii_types_found": stats.get("pii_types", {}),
        "dropped_personal_data_columns": columns["dropped_personal_data"],
        "note": "Free text and raw values are kept only in the in-memory working data. "
                "Public exports drop them (see make_public_dataset).",
    }

    warnings = _warnings(cleaned, analyses, pairs, flags, stats, missingness, config, n)

    status = "blocked" if not core_ok or n == 0 else ("warnings" if warnings else "ok")
    return {
        "report_version": REPORT_VERSION,
        "status": status,
        "status_reason": (None if status != "blocked" else
                          "No survey outcome or experience questions were recognized; nothing can be analyzed. "
                          "Check the column mapping."),
        "rows": {"input": int(len(raw_df)), "output": int(n), "retained_all": int(len(raw_df)) == int(n)},
        "columns": columns,
        "fields": {
            "present": present,
            "missing_core": mapping.missing_fields(required=True),
            "missing_optional": mapping.missing_fields(required=False),
        },
        "analyses": analyses,
        "missingness": missingness,
        "standardization": stats.get("standardization", {}),
        "unexpected_categories": stats.get("unexpected_categories", {}),
        "invalid_values": stats.get("invalid_values", {}),
        "dates": stats.get("dates", {}),
        "counties": stats.get("counties", {}),
        "roles": stats.get("roles", {}),
        "duplicates": dup,
        "suspicious_patterns": suspicious,
        "flags": flags,
        "confidence_pairs": pairs,
        "survey_versions": stats.get("survey_versions", {}),
        "privacy": privacy,
        "warnings": warnings,
        "invariant_violations": validate_cleaned(raw_df, cleaned),
    }


def _warnings(cleaned, analyses, pairs, flags, stats, missingness, config, n) -> list[dict]:
    w: list[dict] = []

    def add(analysis, severity, message):
        w.append({"analysis": analysis, "severity": severity, "message": message})

    for name, a in analyses.items():
        if not a["available"] and a["core"]:
            add(name, "high", f"{a['label']} unavailable: missing {', '.join(a['missing_fields'])}.")
        elif not a["available"]:
            add(name, "medium", f"{a['label']} unavailable: missing {', '.join(a['missing_fields'])}.")
    for name, p in pairs.items():
        if analyses["confidence_change"]["available"] and not p["analyzable"]:
            add("confidence_change", "medium", f"Pair '{name}' is incomplete; other pairs are still analyzed.")
    for f, m in missingness.items():
        if f in RATING_FIELDS + SAT_FIELDS and 0 < m["valid_pct"] < 50:
            add(f, "medium", f"{f} has valid answers for only {m['valid_pct']}% of rows "
                             "(missing answers may reflect forms that did not show it, or skips; exposure is not recorded). "
                             "Report it with its own n.")
    unexpected = stats.get("unexpected_categories", {})
    likert_off = {f: v for f, v in unexpected.items() if f in SAT_FIELDS + [p for pr in PRE_POST_PAIRS for p in pr]}
    if likert_off:
        total = flags.get("flag_offscale_label", 0)
        add("training_experience", "medium",
            f"{total} rows contain labels outside the agreement scale; those answers are excluded (not guessed) "
            "until a mapping is approved.")
    if flags.get("flag_reverse_scale_suspect"):
        add("confidence_change", "medium",
            f"{flags['flag_reverse_scale_suspect']} rows may have read the agreement scale in reverse. Show results "
            "with and without them.")
    if flags.get("flag_fast_completion"):
        add("all", "low", f"{flags['flag_fast_completion']} responses completed in under "
                          f"{config.fast_completion_seconds} s; consider a sensitivity check.")
    if flags.get("flag_possible_duplicate"):
        add("all", "low", f"{flags['flag_possible_duplicate']} rows are possible duplicate submissions (kept).")
    county = stats.get("counties", {})
    if county.get("needs_review_rows"):
        add("county_comparison", "medium",
            f"{county['needs_review_rows']} rows have an unrecognized or ambiguous county; they are kept under "
            "their cleaned label and excluded from service-area totals.")
    if flags.get("flag_out_of_service_area"):
        add("county_comparison", "low",
            f"{flags['flag_out_of_service_area']} rows are from counties outside the configured service area "
            f"({', '.join(config.service_area_states)}); kept, but shown separately.")
    small = [k for k, v in county.get("rows_by_county", {}).items() if v < config.min_group_size]
    if small:
        add("county_comparison", "low", f"Counties with fewer than {config.min_group_size} responses: "
                                        f"{', '.join(small)}. Do not compare them statistically.")
    lang = stats.get("languages", {})
    small_lang = [k for k, v in lang.items() if v < config.min_group_size]
    if small_lang:
        add("language_comparison", "low", f"Language groups with fewer than {config.min_group_size} responses: "
                                          f"{', '.join(small_lang)}.")
    versions = stats.get("survey_versions", {}).get("signatures", [])
    if len(versions) > 1:
        add("trends_over_time", "medium",
            f"{len(versions)} different question sets were detected across sessions. Compare optional items only "
            "within sessions that asked them.")
    if flags.get("flag_text_pii"):
        add("feedback_themes", "high", f"{flags['flag_text_pii']} comments contain possible personal information. "
                                       "Redact before displaying any comment.")
    return w


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _md_table(rows: list[dict]) -> str:
    if not rows:
        return "_none_"
    cols = list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(out)


def quality_report_to_markdown(report: dict, title: str = "Data Quality Report") -> str:
    r = report
    L = [f"# {title}\n",
         f"**Status:** `{r['status']}`" + (f" ({r['status_reason']})" if r.get("status_reason") else ""),
         f"\n**Rows:** {r['rows']['input']} in, {r['rows']['output']} out "
         f"({'all retained' if r['rows']['retained_all'] else 'ROW COUNT CHANGED'}).\n"]
    if r["invariant_violations"]:
        L.append("**Invariant violations:** " + "; ".join(r["invariant_violations"]) + "\n")

    L.append("## Warnings\n")
    L.append(_md_table(r["warnings"]))

    c = r["columns"]
    L.append("\n## Columns\n")
    L.append(f"{c['input_count']} input columns; {len(c['recognized'])} recognized.\n")
    L.append(_md_table(c["recognized"]))
    for key, label in [("ambiguous", "Ambiguous (not applied; confirm)"),
                       ("suggestions", "Suggestions (not applied)"),
                       ("rejected_by_value_check", "Rejected because values do not fit the field"),
                       ("unrecognized", "Unrecognized (kept as extra columns)"),
                       ("dropped_personal_data", "Dropped: header looks like personal data"),
                       ("ignored_by_user", "Ignored by user override")]:
        if c[key]:
            L.append(f"\n**{label}:**\n")
            L.append(_md_table(c[key]) if isinstance(c[key][0], dict) else "\n".join(f"- `{x}`" for x in c[key]))
    L.append(f"\nMissing core fields: {', '.join(r['fields']['missing_core']) or 'none'}  ")
    L.append(f"Missing optional fields: {', '.join(r['fields']['missing_optional']) or 'none'}\n")

    L.append("## Analysis readiness\n")
    L.append(_md_table([{"analysis": k, "available": v["available"], "core": v["core"],
                         "missing_fields": ", ".join(v["missing_fields"]) or "-"} for k, v in r["analyses"].items()]))

    L.append("\n## Confidence pairs\n")
    L.append(_md_table([{"pair": k, **{x: v[x] for x in ("analyzable", "pre_valid", "post_valid", "both_valid")}}
                        for k, v in r["confidence_pairs"].items()]))

    L.append("\n## Missingness (after cleaning)\n")
    L.append("`missing` = blank in the upload; `unusable` = present but off-scale or invalid (kept in `_raw`).\n")
    L.append(_md_table([{"field": k, **v} for k, v in r["missingness"].items()]))

    L.append("\n## Standardization performed\n")
    for k, v in r["standardization"].items():
        L.append(f"- **{k}**: {v}")

    L.append("\n## Unexpected categories (kept raw, scored as missing)\n")
    if r["unexpected_categories"]:
        for f, v in r["unexpected_categories"].items():
            L.append(f"- **{f}**: " + ", ".join(f"`{k}` ({n})" for k, n in v.items()))
    else:
        L.append("_none_")

    L.append("\n## Invalid values\n")
    L.append(_md_table([{"field": k, **v} for k, v in r["invalid_values"].items()]))
    if r["dates"]:
        L.append("\n**Date checks:**\n")
        for k, v in r["dates"].items():
            L.append(f"- {k}: {v}")

    if r["counties"]:
        L.append("\n## Counties\n")
        L.append(_md_table(r["counties"].get("table", [])))
        L.append(f"\nRows needing county review: {r['counties'].get('needs_review_rows', 0)}")
    if r["roles"]:
        L.append("\n## Participant roles\n")
        for k, v in r["roles"].items():
            L.append(f"- {k}: {v}")

    L.append("\n## Duplicates and suspicious patterns (flagged, all rows kept)\n")
    for k, v in {**r["duplicates"], **r["suspicious_patterns"]}.items():
        L.append(f"- {k}: {v}")

    sv = r["survey_versions"]
    if sv:
        L.append("\n## Inferred survey versions\n")
        L.append(sv.get("note", ""))
        L.append("")
        L.append(_md_table(sv.get("signatures", [])))

    p = r["privacy"]
    L.append("\n## Privacy\n")
    L.append(p["note"] + "\n")
    for f, v in p["text_fields"].items():
        L.append(f"- {f}: {v['responses']} responses, {v['pii_flagged']} flagged for possible personal information")
    if p["pii_types_found"]:
        L.append(f"- PII pattern types found: {p['pii_types_found']}")
    L.append("")
    return "\n".join(L)
