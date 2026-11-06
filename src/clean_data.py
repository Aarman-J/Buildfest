"""Reusable cleaning pipeline for EveryMind 988 training surveys.

Library use (e.g. from Streamlit):

    from src.clean_data import load_survey_file, clean_survey, make_public_dataset
    raw_df, sheet, sheets = load_survey_file(uploaded_file)
    cleaned_df, quality_report = clean_survey(raw_df)
    public_df = make_public_dataset(cleaned_df)

`clean_survey` is pure: it never modifies `raw_df`, never reads or writes files,
and never sends data anywhere. No rows are dropped and no missing answers are
imputed; problems become `flag_*` columns plus aggregate counts in the report.

Command line (writes the privacy-safe export and the report to outputs/):

    python src/clean_data.py [--input data/....xlsx] [--outdir outputs]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import BinaryIO

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_validation import (
    build_quality_report, check_column_values, detect_pii, parse_datetime_series, parse_rating,
    quality_report_to_markdown,
)
from src.schema_mapper import (
    FIELD_ORDER, LIKERT_FIELDS, LIKERT_LABELS, LIKERT_SCORES, LIKERT_SYNONYMS, POST_FIELDS, PRE_FIELDS,
    PRE_POST_PAIRS, RATING_FIELDS, ROLE_CODES, ROLE_ORDER, SAT_FIELDS, TEXT_FIELDS, SchemaMapping,
    clean_label, map_columns, norm_header, strip_accents,
)

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT_ROOT / "data" / "BuildFest_EveryMind_Survey_Data_FULL.xlsx"
DEFAULT_OUTDIR = PROJECT_ROOT / "outputs"

# ---------------------------------------------------------------------------
# County reference
# ---------------------------------------------------------------------------

MD_JURISDICTIONS = [
    "Allegany", "Anne Arundel", "Baltimore City", "Baltimore County", "Calvert", "Caroline", "Carroll", "Cecil",
    "Charles", "Dorchester", "Frederick", "Garrett", "Harford", "Howard", "Kent", "Montgomery",
    "Prince George's", "Queen Anne's", "St. Mary's", "Somerset", "Talbot", "Washington", "Wicomico",
    "Worcester",
]
AMBIGUOUS = "__ambiguous__"
ALT_SCALE_MIN_SHARE = 0.2  # share of a session using off-scale labels before it is labelled "alt scale"


def county_key(text: str) -> str:
    """Lowercase, accent/punctuation-light key with the word 'county' removed."""
    s = strip_accents(text.lower()).replace("’", "'")
    s = re.sub(r"\bcounty\b|\bco\.?(?=\s|$)", " ", s)
    s = re.sub(r"[^a-z0-9' ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _build_county_aliases() -> dict[str, tuple[str, str | None]]:
    """Explicit mapping: county_key(label) -> (canonical county, state). Keys checked as written."""
    m: dict[str, tuple[str, str | None]] = {}
    for name in MD_JURISDICTIONS:
        if name.startswith("Baltimore"):
            continue
        m[county_key(name)] = (name, "MD")
    m.update({
        # Baltimore City vs Baltimore County must not be merged; bare "Baltimore" is ambiguous.
        "baltimore city": ("Baltimore City", "MD"),
        "city of baltimore": ("Baltimore City", "MD"),
        "baltimore": (AMBIGUOUS, None),
        # Common abbreviations / spellings
        "prince georges": ("Prince George's", "MD"), "prince george": ("Prince George's", "MD"),
        "pg": ("Prince George's", "MD"), "pgc": ("Prince George's", "MD"),
        "aa": ("Anne Arundel", "MD"), "moco": ("Montgomery", "MD"),
        "queen annes": ("Queen Anne's", "MD"), "st marys": ("St. Mary's", "MD"),
        "saint mary's": ("St. Mary's", "MD"), "saint marys": ("St. Mary's", "MD"), "st mary's": ("St. Mary's", "MD"),
        # Non-Maryland jurisdictions with a single unambiguous match
        "fairfax": ("Fairfax", "VA"),
        "loudoun": ("Loudoun", "VA"),
        "arlington": ("Arlington", "VA"),
        "prince william": ("Prince William", "VA"),
        "washington dc": ("District of Columbia", "DC"), "dc": ("District of Columbia", "DC"),
        "district of columbia": ("District of Columbia", "DC"),
        # "Washington" alone could be Washington County MD or Washington DC
        "washington": (AMBIGUOUS, None),
    })
    return m


COUNTY_ALIASES = _build_county_aliases()
# "Baltimore County" must be checked before the word "county" is stripped.
COUNTY_ALIASES_LITERAL = {"baltimore county": ("Baltimore County", "MD"),
                          "washington county": ("Washington", "MD")}

PARTNER_SPLIT = re.compile(r"\s+(?:in partnership with|with|w/)\s+|\s*\(|\s+-\s+", re.I)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Approved normalization (2026-11-06) of two non-standard labels used by a variant form, applied ONLY to the
# three training-experience items (never to the six confidence items). Evidence: 23 of the 24 rows using them
# answer consistently within a single label, and their recommend scores are clearly positive (mean 9.1 / 8.6).
# Both map to "agree" (>= 4), so % agree is unaffected by the exact level; means/"strongly agree" shares are.
# Raw text is kept in <field>_raw and rows are flagged `flag_experience_label_normalized`.
EXPERIENCE_LABEL_MAP = {"very agree": 5, "satisfied": 4}


@dataclass
class CleaningConfig:
    fast_completion_seconds: float = 30
    long_gap_days: int = 30
    min_session_size: int = 5          # sessions smaller than this get low-confidence version labels
    min_group_size: int = 10           # groups smaller than this trigger small-n warnings
    service_area_states: tuple = ("MD",)
    # User-approved additions. Keys are compared case-insensitively.
    likert_label_overrides: dict[str, int] = field(default_factory=dict)  # applies to ALL Likert items
    experience_label_map: dict[str, int] = field(default_factory=lambda: dict(EXPERIENCE_LABEL_MAP))
    role_aliases: dict[str, str] = field(default_factory=dict)            # e.g. {"teacher": "school_staff"}
    county_aliases: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    column_aliases: dict[str, list[str]] = field(default_factory=dict)
    column_overrides: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """JSON-compatible form, e.g. to save user-confirmed mappings between uploads."""
        d = asdict(self)
        d["service_area_states"] = list(self.service_area_states)
        d["county_aliases"] = {k: list(v) for k, v in self.county_aliases.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CleaningConfig":
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"Unknown CleaningConfig keys: {sorted(unknown)}")
        d = dict(d)
        if "service_area_states" in d:
            d["service_area_states"] = tuple(d["service_area_states"])
        if "county_aliases" in d:
            d["county_aliases"] = {k: tuple(v) for k, v in d["county_aliases"].items()}
        return cls(**d)

    def confirm_mapping(self, header: str, canonical_field: str | None) -> "CleaningConfig":
        """Return a copy with a manually confirmed header mapping (None = ignore column)."""
        d = self.to_dict()
        d["column_overrides"] = {**d["column_overrides"], header: canonical_field}
        return CleaningConfig.from_dict(d)


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_survey_file(source: str | Path | BinaryIO, filename: str | None = None,
                     sheet: str | None = None) -> tuple[pd.DataFrame, str, list[str]]:
    """Read an uploaded Excel/CSV file into a raw DataFrame (all values as objects).

    `source` may be a path or a file-like object (e.g. Streamlit UploadedFile).
    For workbooks, the sheet matching the most canonical fields is used unless
    `sheet` is given. Returns (raw_df, chosen_sheet, all_sheet_names).
    """
    name = filename or getattr(source, "name", None) or str(source)
    if str(name).lower().endswith((".csv", ".txt")):
        data = source.read() if hasattr(source, "read") else Path(source).read_bytes()
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                df = pd.read_csv(io.BytesIO(data), dtype=object, encoding=enc)
                return df, "csv", ["csv"]
            except UnicodeDecodeError:
                continue
        raise ValueError("Could not decode CSV file")
    sheets = pd.read_excel(source, sheet_name=None, dtype=object)
    if not sheets:
        raise ValueError("Workbook has no sheets")
    if sheet is None:
        def score(s):
            return (len(map_columns(sheets[s].columns).field_positions()), len(sheets[s]))
        sheet = max(sheets, key=score)
    return sheets[sheet], sheet, list(sheets)


# ---------------------------------------------------------------------------
# Field cleaners. Each returns new columns plus aggregate stats.
# ---------------------------------------------------------------------------

def _cmap(s: pd.Series, func) -> pd.Series:
    """Element-wise map that keeps None as None (object dtype; no pandas string inference)."""
    return pd.Series([func(v) for v in s], index=s.index, dtype="object")


def _raw_text(s: pd.Series) -> pd.Series:
    """Original value as text for audit (missing stays missing)."""
    return _cmap(s, lambda v: None if clean_label(v) is None else str(v))


def _clean_likert(s: pd.Series, scores: dict[str, int]):
    cleaned = _cmap(s, clean_label)
    key = _cmap(cleaned, lambda v: v.lower() if v else None)
    score = pd.Series([scores.get(k) if k else np.nan for k in key], index=s.index, dtype="float64")
    present = cleaned.notna()
    off = present & score.isna()
    stats = {
        "hidden_chars_removed": int((present & (_cmap(s, str) != cleaned)).sum()),
        "wording_variant": int(key.isin(LIKERT_SYNONYMS).sum()),
        "case_variant": int((present & score.notna() & ~key.isin(LIKERT_SYNONYMS)
                             & (cleaned != _cmap(score, lambda x: LIKERT_LABELS.get(int(x)) if pd.notna(x) else None))).sum()),
        "user_override_applied": int(key.isin({k for k in scores if k not in LIKERT_SCORES}).sum()),
        "offscale": cleaned[off].value_counts().to_dict(),
    }
    cols = {
        "score": pd.array(score.tolist(), dtype="Int64"),
        "label": _cmap(score, lambda x: LIKERT_LABELS.get(int(x)) if pd.notna(x) else None),
        "raw": _raw_text(s),
        "offscale": off,
    }
    return cols, stats


def _clean_rating(s: pd.Series):
    parsed = [parse_rating(v) for v in s]
    vals = pd.array([p[0] if p[1] == "valid" else None for p in parsed], dtype="Int64")
    status = pd.Series([p[1] for p in parsed], index=s.index)
    return vals, status


def _clean_roles(s: pd.Series, role_map: dict[str, str], codes: list[str]):
    raw_clean = _cmap(s, clean_label)
    n = len(s)
    member = {c: [None] * n for c in codes}
    canonical, unrecognized, counts, primary = [None] * n, [None] * n, [0] * n, [None] * n
    unknown_tokens: dict[str, int] = {}
    for i, v in enumerate(raw_clean):
        if v is None:
            continue
        tokens = [clean_label(t) for t in v.split(";")]
        tokens = [t for t in tokens if t]
        found = sorted({role_map[t.lower()] for t in tokens if t.lower() in role_map},
                       key=lambda c: codes.index(c))
        unknown = [t for t in tokens if t.lower() not in role_map]
        for t in unknown:
            unknown_tokens[t] = unknown_tokens.get(t, 0) + 1
        for c in codes:
            member[c][i] = c in found
        counts[i] = len(found) + len(unknown)
        canonical[i] = ";".join(found + (["unrecognized"] if unknown else [])) or None
        unrecognized[i] = "; ".join(unknown) or None
        primary[i] = (found[0] if len(found) == 1 and not unknown
                      else "multiple" if counts[i] > 1 else "unrecognized")
    cols = {f"role_{c}": pd.array(member[c], dtype="boolean") for c in codes}
    cols.update({
        "participant_role": pd.Series(canonical, dtype="object"),
        "role_primary": pd.Series(primary, dtype="object"),
        "role_count": pd.array([c if raw_clean.iloc[i] is not None else None for i, c in enumerate(counts)],
                               dtype="Int64"),
        "role_unrecognized": pd.Series(unrecognized, dtype="object"),
        "participant_role_raw": _raw_text(s),
    })
    raw_str = raw_clean.dropna()
    stats = {
        "trailing_semicolon_removed": int(raw_str.str.endswith(";").sum()),
        "multi_select_rows": int(sum(1 for i, c in enumerate(counts) if c > 1)),
        "rows_with_unrecognized_role": int(sum(u is not None for u in unrecognized)),
        "unrecognized_tokens": {k[:60]: v for k, v in sorted(unknown_tokens.items(), key=lambda x: -x[1])},
        "membership_counts": {c: int(sum(bool(x) for x in member[c])) for c in codes},
    }
    return cols, stats


def _clean_county(s: pd.Series, aliases: dict, service_states: tuple):
    cleaned = _cmap(s, clean_label)
    county, state, partner, status = [], [], [], []
    for v in cleaned:
        if v is None:
            county.append(None); state.append(None); partner.append(None); status.append("missing")
            continue
        parts = PARTNER_SPLIT.split(v, maxsplit=1)
        place = parts[0].strip()
        extra = parts[1].strip(" )") if len(parts) > 1 else None
        literal = re.sub(r"\s+", " ", strip_accents(place.lower()).replace("’", "'")).strip(" .")
        key = county_key(place)
        hit = COUNTY_ALIASES_LITERAL.get(literal) or aliases.get(key)
        if hit is None and extra:  # e.g. "Prince George's - PGCPS": try the full text too
            hit = aliases.get(county_key(v))
        if hit is None:
            label = " ".join(w.capitalize() if w.islower() else w for w in key.split()) or place
            county.append(label); state.append(None); status.append("unrecognized")
        elif hit[0] == AMBIGUOUS:
            county.append(place.title()); state.append(None); status.append("ambiguous")
        else:
            county.append(hit[0]); state.append(hit[1]); status.append("recognized")
        partner.append(extra or None)
    in_area = [None if st != "recognized" else (sta in service_states)
               for st, sta in zip(status, state)]
    cols = {
        "county": pd.Series(county, dtype="object"),
        "county_state": pd.Series(state, dtype="object"),
        "county_partner": pd.Series(partner, dtype="object"),
        "county_status": pd.Series(status, dtype="object"),
        "in_service_area": pd.array(in_area, dtype="boolean"),
        "county_raw": _raw_text(s),
    }
    df = pd.DataFrame({"raw": _cmap(s, lambda v: repr(v) if clean_label(v) else None),
                       "county": cols["county"], "status": cols["county_status"], "state": cols["county_state"]})
    table = (df.dropna(subset=["county"]).groupby(["county", "state", "status"], dropna=False)
             .agg(rows=("raw", "size"), raw_labels=("raw", "nunique")).reset_index()
             .sort_values("rows", ascending=False))
    table["state"] = table["state"].astype("object").where(table["state"].notna(), "unknown")
    stats = {
        "table": table.to_dict("records"),
        "rows_by_county": {k: int(v) for k, v in df["county"].value_counts().items()},
        "raw_labels": int(_cmap(s, lambda v: str(v) if clean_label(v) else None).nunique()),
        "canonical_labels": int(df["county"].nunique()),
        "rows_relabelled": int((cleaned.notna() & (cleaned != cols["county"])).sum()),
        "needs_review_rows": int(pd.Series(status).isin(["unrecognized", "ambiguous"]).sum()),
        "partner_rows": int(pd.Series(partner).notna().sum()),
        "status_counts": pd.Series(status).value_counts().to_dict(),
    }
    return cols, stats


def _language_code(label: str | None) -> str | None:
    if label is None:
        return None
    k = strip_accents(label.lower())
    if k.startswith(("english", "ingles")) or k in ("en", "en-us"):
        return "en"
    if k.startswith(("espanol", "spanish")) or k in ("es", "es-mx", "es-us"):
        return "es"
    return "other"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def clean_survey(raw_df: pd.DataFrame, config: CleaningConfig | None = None) -> tuple[pd.DataFrame, dict]:
    """Clean an uploaded survey DataFrame.

    Returns (cleaned_df, quality_report). `cleaned_df` is the protected working
    representation: it keeps raw values (`*_raw`), free text and unrecognized
    columns (`extra__*`). Use `make_public_dataset` before showing or exporting.
    """
    cfg = config or CleaningConfig()
    if not isinstance(raw_df, pd.DataFrame):
        raise TypeError("clean_survey expects a pandas DataFrame")
    n = len(raw_df)
    raw = raw_df.reset_index(drop=True)  # new object; raw_df itself is never touched

    mapping = map_columns(list(raw.columns), aliases=cfg.column_aliases, overrides=cfg.column_overrides)
    likert_scores = {**LIKERT_SCORES, **{k.lower().strip(): int(v) for k, v in cfg.likert_label_overrides.items()}}
    exp_map = {k.lower().strip(): int(v) for k, v in (cfg.experience_label_map or {}).items()}
    experience_scores = {**exp_map, **likert_scores}   # standard labels always win

    def scores_for(f: str) -> dict[str, int]:
        return experience_scores if f in SAT_FIELDS else likert_scores

    # Value-based sanity check of automatic matches (overrides are trusted).
    value_checks = {}
    for m in mapping.with_status("matched"):
        if m.method == "override":
            continue
        chk = check_column_values(m.field, raw.iloc[:, m.position], scores_for(m.field))
        value_checks[m.field] = chk
        if not chk["ok"]:
            mapping.reject(m.position, f"only {chk['valid_share']:.0%} of values fit {m.field}; confirm mapping")
    pos = mapping.field_positions()
    log.info("Mapped %d of %d columns to canonical fields", len(pos), raw.shape[1])

    def src(f):
        return raw.iloc[:, pos[f]] if f in pos else None

    out: dict[str, object] = {}
    stats: dict = {"standardization": {}, "unexpected_categories": {}, "invalid_values": {}, "dates": {}}
    std = stats["standardization"]

    # --- identifiers -------------------------------------------------------
    if "response_id" in pos:
        rid = _cmap(src("response_id"), lambda v: None if clean_label(v) is None else
                                     (str(int(v)) if isinstance(v, float) and v.is_integer() else clean_label(v)))
        generated = rid.isna()
        rid = rid.where(~generated, pd.Series([f"row-{i + 1}" for i in range(n)], index=rid.index))
    else:
        generated = pd.Series(True, index=raw.index)
        rid = pd.Series([f"row-{i + 1}" for i in range(n)])
    out["response_id"] = rid.astype("object")
    out["response_id_generated"] = generated.astype(bool)
    out["flag_duplicate_response_id"] = rid.duplicated(keep=False) & ~generated

    # --- timestamps --------------------------------------------------------
    st = ct = None
    for f in ("start_time", "completion_time"):
        if f in pos:
            vals, status = parse_datetime_series(src(f))
            out[f], out[f"{f}_raw"] = vals, _raw_text(src(f))
            stats["invalid_values"][f] = {"invalid": int((status == "invalid").sum()),
                                          "missing": int((status == "missing").sum())}
            if f == "start_time":
                st = vals
            else:
                ct = vals
    completion_date = ct.dt.normalize() if ct is not None else pd.Series(pd.NaT, index=raw.index)
    out["completion_date"] = completion_date
    if st is not None and ct is not None:
        secs = (ct - st).dt.total_seconds()
        out["completion_seconds"] = secs
        out["flag_completion_before_start"] = (secs < 0).fillna(False)
        out["flag_fast_completion"] = ((secs >= 0) & (secs < cfg.fast_completion_seconds)).fillna(False)
        stats["dates"]["completion_seconds"] = {
            "median": None if secs.dropna().empty else float(secs.median()),
            "under_threshold": int(out["flag_fast_completion"].sum()),
            "threshold_seconds": cfg.fast_completion_seconds,
            "over_1h": int((secs > 3600).sum()),
        }

    # --- training date -----------------------------------------------------
    if "training_date" in pos:
        td, td_status = parse_datetime_series(src("training_date"))
        td = td.dt.normalize()
        out["training_date"], out["training_date_raw"] = td, _raw_text(src("training_date"))
        out["flag_training_date_missing"] = td_status == "missing"
        out["flag_training_date_invalid"] = td_status == "invalid"
        # 988 launched 2022-07-16; earlier dates cannot be a 988 training.
        out["flag_training_date_implausible"] = (td < pd.Timestamp("2022-07-16")).fillna(False)
        gap = (completion_date - td).dt.days
        out["flag_training_date_after_completion"] = (gap < 0).fillna(False)
        out["flag_training_date_gap_gt_30d"] = (gap > cfg.long_gap_days).fillna(False)
        stats["invalid_values"]["training_date"] = {"invalid": int((td_status == "invalid").sum()),
                                                    "missing": int((td_status == "missing").sum())}
        stats["dates"]["training_date"] = {
            "min": None if td.dropna().empty else str(td.min().date()),
            "max": None if td.dropna().empty else str(td.max().date()),
            "after_completion": int(out["flag_training_date_after_completion"].sum()),
            f"gap_over_{cfg.long_gap_days}_days": int(out["flag_training_date_gap_gt_30d"].sum()),
            "before_988_launch": int(out["flag_training_date_implausible"].sum()),
            "median_days_to_completion": None if gap.dropna().empty else float(gap.median()),
        }
        # Dates before the 988 launch or after the survey was submitted are not usable for training-date trends
        # (the record itself is kept for every other analysis).
        usable = td.notna() & ~out["flag_training_date_implausible"] & ~out["flag_training_date_after_completion"]
    else:
        td = pd.Series(pd.NaT, index=raw.index, dtype="datetime64[ns]")
        usable = pd.Series(False, index=raw.index)
    # Training-date analyses use only respondent-reported, plausible dates.
    # The completion date is NOT substituted (approved Stage 3 rule).
    out["training_date_valid"] = usable.astype(bool)
    out["training_month"] = _cmap(td.where(usable), lambda v: v.strftime("%Y-%m") if pd.notna(v) else None)

    # --- language ----------------------------------------------------------
    if "language" in pos:
        lab = _cmap(src("language"), clean_label)
        out["language"] = _cmap(lab, _language_code)
        out["language_label"] = lab.astype("object")
        out["language_raw"] = _raw_text(src("language"))
        std["language_invisible_marks_removed"] = int((lab.notna() & (_cmap(src("language"), str) != lab)).sum())
        stats["languages"] = {k: int(v) for k, v in out["language"].value_counts().items()}
        other = lab[out["language"] == "other"].value_counts()
        if len(other):
            stats["unexpected_categories"]["language"] = {k[:60]: int(v) for k, v in other.items()}

    # --- roles -------------------------------------------------------------
    if "participant_role" in pos:
        role_map = {**ROLE_CODES, **{k.lower().strip(): v for k, v in cfg.role_aliases.items()}}
        codes = ROLE_ORDER + sorted(set(role_map.values()) - set(ROLE_ORDER))
        cols, rstats = _clean_roles(src("participant_role"), role_map, codes)
        out.update(cols)
        out["flag_role_unrecognized"] = cols["role_unrecognized"].notna()
        stats["roles"] = rstats
        std["role_trailing_semicolon_removed"] = rstats["trailing_semicolon_removed"]
        if rstats["unrecognized_tokens"]:
            stats["unexpected_categories"]["participant_role"] = rstats["unrecognized_tokens"]

    # --- county ------------------------------------------------------------
    if "county" in pos:
        aliases = {**COUNTY_ALIASES, **{county_key(k): tuple(v) for k, v in cfg.county_aliases.items()}}
        cols, cstats = _clean_county(src("county"), aliases, cfg.service_area_states)
        out.update(cols)
        out["flag_county_needs_review"] = cols["county_status"].isin(["unrecognized", "ambiguous"])
        out["flag_out_of_service_area"] = pd.Series(cols["in_service_area"]).eq(False).fillna(False)
        stats["counties"] = cstats
        std["county_labels_collapsed"] = f"{cstats['raw_labels']} raw labels -> {cstats['canonical_labels']} counties"
        std["county_rows_relabelled"] = cstats["rows_relabelled"]

    # --- Likert items ------------------------------------------------------
    offscale_any = pd.Series(False, index=raw.index)
    exp_normalized = pd.Series(False, index=raw.index)
    exp_counts: dict[str, dict[str, int]] = {}
    lik_std = {}
    for f in LIKERT_FIELDS:
        if f not in pos:
            continue
        cols, lstats = _clean_likert(src(f), scores_for(f))
        if f in SAT_FIELDS and exp_map:
            key = _cmap(src(f), lambda v: (clean_label(v) or "").lower() or None)
            hit = key.isin(set(exp_map) - set(likert_scores))
            exp_normalized |= hit
            counts = key[hit].value_counts()
            if len(counts):
                exp_counts[f] = {str(k): int(v) for k, v in counts.items()}
            lstats["user_override_applied"] -= int(hit.sum())
        out[f], out[f"{f}_label"], out[f"{f}_raw"] = cols["score"], cols["label"], cols["raw"]
        offscale_any |= cols["offscale"]
        lik_std[f] = {k: v for k, v in lstats.items() if k != "offscale"}
        if lstats["offscale"]:
            stats["unexpected_categories"][f] = {str(k)[:60]: int(v) for k, v in lstats["offscale"].items()}
    if lik_std:
        out["flag_offscale_label"] = offscale_any
        out["flag_experience_label_normalized"] = exp_normalized
        if exp_counts:
            std["experience_labels_normalized"] = exp_counts
        for k in ("hidden_chars_removed", "wording_variant", "case_variant", "user_override_applied"):
            std[f"likert_{k}"] = {f: v[k] for f, v in lik_std.items() if v[k]}

    # --- 0-10 ratings ------------------------------------------------------
    invalid_rating = pd.Series(False, index=raw.index)
    for f in RATING_FIELDS:
        if f not in pos:
            continue
        vals, status = _clean_rating(src(f))
        out[f], out[f"{f}_raw"] = vals, _raw_text(src(f))
        invalid_rating |= status == "invalid"
        stats["invalid_values"][f] = {"invalid": int((status == "invalid").sum()),
                                      "missing": int((status == "missing").sum())}
    if any(f in pos for f in RATING_FIELDS):
        out["flag_invalid_rating"] = invalid_rating

    # --- free text ---------------------------------------------------------
    pii_any = pd.Series(False, index=raw.index)
    pii_types: dict[str, int] = {}
    for f in TEXT_FIELDS:
        if f not in pos:
            continue
        txt = _cmap(src(f), clean_label)
        found = _cmap(txt, detect_pii)
        out[f] = txt
        out[f"{f}_pii"] = found.map(bool)
        pii_any |= out[f"{f}_pii"]
        for types in found:
            for t in types:
                pii_types[t] = pii_types.get(t, 0) + 1
    if any(f in pos for f in TEXT_FIELDS):
        out["flag_text_pii"] = pii_any
    stats["pii_types"] = pii_types

    cleaned = pd.DataFrame(out, index=raw.index)

    # --- derived outcome fields -------------------------------------------
    for pre, post in PRE_POST_PAIRS:
        if pre in cleaned and post in cleaned:
            cleaned[f"change_{post.removeprefix('post_')}"] = cleaned[post] - cleaned[pre]
    _flag_patterns(cleaned)
    _session_and_versions(cleaned, pos, cfg, stats)
    _flag_duplicates(cleaned, stats)

    # --- unrecognized columns kept separately -----------------------------
    extras = []
    for m in mapping.matches:
        if m.status in ("unmatched", "ambiguous", "suggestion", "rejected"):
            name = f"extra__{m.source}"
            while name in cleaned:
                name += "_"
            cleaned[name] = raw.iloc[:, m.position].astype("object").values
            extras.append(m.source)
    stats["extra_columns"] = extras

    flag_cols = [c for c in cleaned.columns if c.startswith("flag_")]
    cleaned["n_flags"] = cleaned[flag_cols].fillna(False).astype(bool).sum(axis=1).astype("Int64")
    cleaned = cleaned[_column_order(cleaned)]

    report = build_quality_report(raw, cleaned, mapping, stats, cfg)
    report["columns"]["value_checks"] = value_checks
    log.info("Cleaned %d rows; status=%s; %d warnings", n, report["status"], len(report["warnings"]))
    return cleaned, report


def _flag_patterns(df: pd.DataFrame) -> None:
    post = [f for f in POST_FIELDS if f in df]
    pre = [f for f in PRE_FIELDS if f in df]
    if post:
        p = df[post]
        n_post = p.notna().sum(axis=1)
        all_sd = (n_post >= 2) & (p.eq(1) | p.isna()).all(axis=1)
        rec = df["likelihood_recommend"] if "likelihood_recommend" in df else pd.Series(pd.NA, index=df.index)
        df["flag_reverse_scale_suspect"] = (all_sd & rec.ge(9)).fillna(False).astype(bool)
    items = pre + post
    if len(items) >= 4:
        x = df[items]
        df["flag_straightline"] = (x.notna().sum(axis=1) >= 4) & (x.nunique(axis=1) == 1)
    changes = [c for c in df.columns if c.startswith("change_")]
    if changes:
        df["flag_post_below_pre"] = df[changes].lt(0).fillna(False).any(axis=1)


def _session_and_versions(df: pd.DataFrame, pos: dict, cfg: CleaningConfig, stats: dict) -> None:
    """Infer the question set ("survey version") per session. Heuristic, never certain."""
    valid_date = df["training_date"].where(df["training_date_valid"]) if "training_date" in df         else pd.Series(pd.NaT, index=df.index)
    date_part = valid_date.dt.strftime("%Y-%m-%d").fillna("unknown date")
    county_part = df["county"].fillna("unknown county") if "county" in df else "unknown county"
    df["session_key"] = (date_part + " | " + county_part).astype("object")

    blocks = {
        "experience": [f for f in SAT_FIELDS if f in pos],
        "recommend": ["likelihood_recommend"] if "likelihood_recommend" in pos else [],
        "call_988": ["likelihood_call_988"] if "likelihood_call_988" in pos else [],
    }
    answered = pd.DataFrame(index=df.index)
    for b, fields in blocks.items():
        raw_cols = [f"{f}_raw" for f in fields]
        answered[b] = df[raw_cols].notna().any(axis=1) if raw_cols else False
    alt = df["flag_offscale_label"] if "flag_offscale_label" in df else pd.Series(False, index=df.index)
    g = answered.groupby(df["session_key"])
    share = g.mean()
    size = g.size()
    alt_share = alt.groupby(df["session_key"]).mean()

    def label(key):
        present = [b for b in blocks if share.loc[key, b] >= 0.5]
        lab = "+".join(present) if present else "core only"
        return lab + (" (alt scale)" if alt_share.loc[key] >= ALT_SCALE_MIN_SHARE else "")

    sess_label = {k: label(k) for k in share.index}
    df["survey_version"] = df["session_key"].map(sess_label).astype("object")
    df["survey_version_confidence"] = df["session_key"].map(
        lambda k: "low" if size.loc[k] < cfg.min_session_size else "moderate").astype("object")

    sig = (pd.DataFrame({"version": df["survey_version"], "session": df["session_key"],
                         "date": valid_date})
           .groupby("version")
           .agg(sessions=("session", "nunique"), rows=("session", "size"),
                first_date=("date", "min"), last_date=("date", "max"))
           .reset_index().sort_values("first_date"))
    for c in ("first_date", "last_date"):
        sig[c] = sig[c].dt.strftime("%Y-%m-%d")
    stats["survey_versions"] = {
        "signatures": sig.to_dict("records"),
        "blocks_checked": {b: f for b, f in blocks.items()},
        "note": ("Versions are inferred per session (training date + county) from which optional question "
                 "blocks at least half the session answered. 'alt scale' = at least 20% of the session used answer "
                 "labels outside the agreement scale. This is a heuristic: confirm with EveryMind's form history."),
        "columns_absent_from_upload": [b for b, f in blocks.items() if not f],
    }


def _flag_duplicates(df: pd.DataFrame, stats: dict) -> None:
    content = [c for c in df.columns if (c.endswith("_raw") and not c.startswith(("start_time", "completion_time")))
               or c in TEXT_FIELDS]
    if not content:
        df["flag_possible_duplicate"] = False
        df["duplicate_group"] = pd.array([None] * len(df), dtype="Int64")
        return
    key = df[content + ["session_key"]].astype(str)
    is_dup = key.duplicated(keep=False)
    group_ids = key[is_dup].apply(tuple, axis=1)
    order = {k: i + 1 for i, k in enumerate(dict.fromkeys(group_ids))}
    df["flag_possible_duplicate"] = is_dup
    df["duplicate_group"] = pd.array([order.get(t) for t in key.apply(tuple, axis=1)] if is_dup.any()
                                     else [None] * len(df), dtype="Int64")
    df.loc[~is_dup, "duplicate_group"] = pd.NA
    text_cols = [c for c in TEXT_FIELDS if c in df]
    with_text = 0
    if text_cols and is_dup.any():
        has_text = df.loc[is_dup, text_cols].notna().any(axis=1)
        with_text = int(df.loc[is_dup][has_text]["duplicate_group"].nunique())
    stats["duplicate_groups_with_text"] = with_text


def _column_order(df: pd.DataFrame) -> list[str]:
    first = ["response_id", "response_id_generated", "session_key", "survey_version", "survey_version_confidence",
             "training_date", "training_date_valid", "training_month", "completion_date",
             "start_time", "completion_time", "completion_seconds", "language", "language_label",
             "participant_role", "role_primary", "role_count"]
    first += [c for c in df.columns if c.startswith("role_") and c not in first and c != "role_unrecognized"]
    first += ["role_unrecognized", "county", "county_state", "county_status", "county_partner", "in_service_area"]
    for f in LIKERT_FIELDS:
        first += [f, f"{f}_label"]
    first += [c for c in df.columns if c.startswith("change_")]
    first += RATING_FIELDS
    for f in TEXT_FIELDS:
        first += [f, f"{f}_pii"]
    first = [c for c in first if c in df]
    flags = sorted(c for c in df.columns if c.startswith("flag_")) + ["n_flags", "duplicate_group"]
    raws = sorted(c for c in df.columns if c.endswith("_raw"))
    extras = [c for c in df.columns if c.startswith("extra__")]
    seen = set(first + flags + raws + extras)
    rest = [c for c in df.columns if c not in seen]
    return first + rest + [c for c in flags if c in df] + raws + extras


# ---------------------------------------------------------------------------
# Privacy-safe representation
# ---------------------------------------------------------------------------

PUBLIC_DROP_EXACT = {"start_time", "completion_time", "role_unrecognized", "county_partner", *TEXT_FIELDS}
# Quasi-identifiers removed from any row-level export: IDs, exact dates/durations, session keys, duplicate links.
ROW_IDENTIFIERS = {"response_id", "response_id_generated", "training_date", "completion_date", "completion_seconds",
                   "session_key", "duplicate_group"}


def make_public_dataset(cleaned: pd.DataFrame, include_partner: bool = False) -> pd.DataFrame:
    """Row-level copy with direct and indirect identifiers removed. NOT for public download.

    Removes free text, raw audit values, exact timestamps and dates, response IDs, session keys, duplicate
    links and extra columns; keeps codes/scores, training month and flags. Removing identifiers does not
    guarantee anonymity (small groups can still be singled out), so the dashboard only offers aggregate
    downloads. Free-text fields are replaced by `<field>_provided` booleans.
    """
    drop = set(PUBLIC_DROP_EXACT) | ROW_IDENTIFIERS
    drop -= {"county_partner"} if include_partner else set()
    keep = [c for c in cleaned.columns
            if c not in drop and not c.endswith("_raw") and not c.startswith("extra__")]
    public = cleaned[keep].copy()
    for f in TEXT_FIELDS:
        if f in cleaned:
            public[f"{f}_provided"] = cleaned[f].notna()
    return public


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    return str(o)


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean an EveryMind survey export")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--sheet", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    before = _sha256(args.input)
    raw_df, sheet, _ = load_survey_file(args.input, sheet=args.sheet)
    cleaned, report = clean_survey(raw_df)
    if _sha256(args.input) != before:
        sys.exit("ERROR: input file changed during cleaning")

    args.outdir.mkdir(parents=True, exist_ok=True)
    make_public_dataset(cleaned).to_csv(args.outdir / "cleaned_survey.csv", index=False, encoding="utf-8")
    md = quality_report_to_markdown(report, title="Data Quality Report: EveryMind 988 Training Survey")
    header = (f"_Source: `{args.input.name}` (sheet `{sheet}`), SHA-256 `{before}` (unchanged). "
              "Generated by `src/clean_data.py`._\n\n")
    (args.outdir / "data_quality_report.md").write_text(md.replace("\n", "\n" + header, 1), encoding="utf-8")
    (args.outdir / "data_quality_report.json").write_text(
        json.dumps(report, indent=2, default=_json_default), encoding="utf-8")
    print(f"status={report['status']} rows_in={report['rows']['input']} rows_out={report['rows']['output']}")
    print(f"wrote {args.outdir / 'cleaned_survey.csv'} (privacy-safe), data_quality_report.md/.json")


if __name__ == "__main__":
    main()
