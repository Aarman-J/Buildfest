"""Shared conventions for the analysis engine (Q1-Q3, recommendations).

Every analysis function takes the *cleaned* DataFrame from
`src.clean_data.clean_survey` and returns JSON-compatible dicts/lists:
numbers are Python int/float (NaN -> None), tables are lists of records.

Conventions applied everywhere
------------------------------
* Agreement items are scored 1-5; "agree" means a score of 4 or 5.
* Denominators are the *valid* responses for the item(s) involved, never all
  rows. Every percentage is reported next to its n.
* Pre/post comparisons use only respondents with valid answers to *both*
  items of the pair (paired n).
* Missing and off-scale answers are excluded from denominators and reported
  as counts. Nothing is imputed.
* Groups with fewer than `min_cell` valid responses are suppressed (counts
  shown as "<min_cell", statistics as None). Groups below `min_compare`
  are shown but not used for comparisons or significance tests.
* Statistical tests are exploratory (no multiple-comparison correction), and
  "before" ratings are retrospective recall, so nothing here is causal.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.schema_mapper import LIKERT_LABELS, PRE_POST_PAIRS, SAT_FIELDS

ANALYSIS_VERSION = "1.0"

OBJECTIVES = [
    {"key": post.removeprefix("post_"), "pre": pre, "post": post, "label": label}
    for (pre, post), label in zip(PRE_POST_PAIRS, [
        "Identify mental health challenges and crises",
        "Describe 988 and how it works",
        "Know when to use 988",
    ])
]
SAT_LABELS = {
    "sat_presenter_engaged": "Presenter(s) engaged participants",
    "sat_presenter_knowledge": "Presenter(s) knew the subject matter",
    "sat_well_organized": "Training was well organized",
}
AGREE_MIN = 4
LIKERT_LEVELS = [1, 2, 3, 4, 5]

# Evidence categories used by every result and recommendation.
VERIFIED = "verified_finding"          # descriptive fact about this dataset, adequate n
EXPLORATORY = "exploratory_association"
DATA_QUALITY = "data_quality_warning"
FOLLOW_UP = "follow_up_investigation"


@dataclass
class AnalysisConfig:
    min_cell: int = 10        # suppress groups/months with fewer valid responses (privacy + stability)
    min_compare: int = 30     # minimum n per group to include in comparisons and tests
    exploratory_below: int = 50  # comparable groups with fewer respondents are labelled "exploratory"
    alpha: float = 0.05
    # Sensitivity analysis: rows with any of these flags set are excluded.
    exclude_flags: tuple = ()
    # Difference (percentage points) between a group and the rest that is worth surfacing
    # when the confidence intervals also do not overlap.
    meaningful_pp: float = 10.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["exclude_flags"] = list(self.exclude_flags)
        return d


SENSITIVITY_SCENARIOS = {
    "all_responses": (),
    "exclude_reverse_scale_suspects": ("flag_reverse_scale_suspect",),
    "exclude_fast_completions": ("flag_fast_completion",),
    "exclude_possible_duplicates": ("flag_possible_duplicate_repeat",),
    "exclude_straightliners": ("flag_straightline",),
    "exclude_all_suspicious": ("flag_reverse_scale_suspect", "flag_fast_completion",
                               "flag_possible_duplicate_repeat", "flag_straightline"),
}


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def has(df: pd.DataFrame, *cols: str) -> bool:
    return all(c in df.columns for c in cols)


def pct(k: float, n: float, digits: int = 1) -> float | None:
    return round(100.0 * k / n, digits) if n else None


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    """95% Wilson score interval for a proportion, in percent."""
    if not n:
        return None, None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1)


def share(mask: pd.Series, valid: pd.Series | None = None) -> dict:
    """{'k','n','pct','ci_low','ci_high'} for a boolean mask over valid rows."""
    if valid is not None:
        mask = mask[valid]
    k, n = int(mask.sum()), int(len(mask))
    lo, hi = wilson_ci(k, n)
    return {"k": k, "n": n, "pct": pct(k, n), "ci_low": lo, "ci_high": hi}


def num(x) -> float | int | None:
    """Plain Python number or None."""
    if x is None or (isinstance(x, float) and math.isnan(x)) or x is pd.NA or x is pd.NaT:
        return None
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if np.isnan(x) else round(float(x), 3)
    return x


def describe_numeric(s: pd.Series) -> dict:
    s = pd.to_numeric(s, errors="coerce").dropna().astype(float)
    if s.empty:
        return {"n": 0, "mean": None, "median": None, "sd": None, "q1": None, "q3": None}
    return {"n": int(len(s)), "mean": num(s.mean()), "median": num(s.median()),
            "sd": num(s.std(ddof=1)) if len(s) > 1 else None,
            "q1": num(s.quantile(0.25)), "q3": num(s.quantile(0.75))}


def likert_distribution(s: pd.Series) -> list[dict]:
    """Full 1-5 distribution over valid answers (denominator = valid n)."""
    v = s.dropna().astype(int)
    n = len(v)
    counts = v.value_counts()
    return [{"score": lvl, "label": LIKERT_LABELS[lvl], "n": int(counts.get(lvl, 0)),
             "pct": pct(int(counts.get(lvl, 0)), n)} for lvl in LIKERT_LEVELS]


def rating_distribution(s: pd.Series, lo: int = 0, hi: int = 10) -> list[dict]:
    v = s.dropna().astype(int)
    n = len(v)
    counts = v.value_counts()
    return [{"score": x, "n": int(counts.get(x, 0)), "pct": pct(int(counts.get(x, 0)), n)} for x in range(lo, hi + 1)]


def item_coverage(df: pd.DataFrame, field_name: str) -> dict:
    """Valid / missing / unusable (present but off-scale or invalid) counts for one field."""
    n = len(df)
    if field_name not in df:
        return {"field": field_name, "collected": False, "rows": n, "valid": 0, "missing": n, "unusable": 0}
    valid = int(df[field_name].notna().sum())
    raw = f"{field_name}_raw"
    blank = int(df[raw].isna().sum()) if raw in df else n - valid
    return {"field": field_name, "collected": True, "rows": n, "valid": valid, "valid_pct": pct(valid, n),
            "missing": blank, "unusable": n - valid - blank}


def spearman(x: pd.Series, y: pd.Series, min_n: int) -> dict:
    """Spearman rank correlation on pairwise-complete rows, with actual n."""
    from scipy.stats import spearmanr
    pair = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"),
                         "y": pd.to_numeric(y, errors="coerce")}).dropna()
    n = len(pair)
    if n < max(min_n, 3) or pair["x"].nunique() < 2 or pair["y"].nunique() < 2:
        return {"n": n, "rho": None, "p_value": None, "strength": "insufficient data"}
    res = spearmanr(pair["x"].astype(float), pair["y"].astype(float))
    rho = float(res.statistic)
    return {"n": n, "rho": round(rho, 3), "p_value": float(f"{res.pvalue:.3g}"), "strength": strength(rho)}


def strength(rho: float | None) -> str:
    if rho is None:
        return "insufficient data"
    a = abs(rho)
    return ("negligible" if a < 0.1 else "weak" if a < 0.3 else "moderate" if a < 0.5 else "strong")


def sat_composite(df: pd.DataFrame) -> pd.Series:
    """Mean of available experience items (1-5); NaN if none answered."""
    cols = [c for c in SAT_FIELDS if c in df]
    if not cols:
        return pd.Series(np.nan, index=df.index)
    return df[cols].astype("Float64").mean(axis=1, skipna=True).astype(float)


def available_objectives(df: pd.DataFrame) -> list[dict]:
    return [o for o in OBJECTIVES if has(df, o["pre"], o["post"])]


def available_post_objectives(df: pd.DataFrame) -> list[dict]:
    return [o for o in OBJECTIVES if has(df, o["post"])]


def suppress(n: int, cfg: AnalysisConfig) -> bool:
    return n < cfg.min_cell


def apply_exclusions(df: pd.DataFrame, flags: tuple) -> tuple[pd.DataFrame, dict]:
    """Drop rows with any of the given flags (sensitivity analysis only). Unknown flags are ignored."""
    work = add_duplicate_repeat_flag(df)
    present = [f for f in flags if f in work]
    if not present:
        return work, {"excluded_rows": 0, "flags_applied": [], "flags_unavailable": list(flags)}
    mask = work[present].fillna(False).astype(bool).any(axis=1)
    info = {"excluded_rows": int(mask.sum()), "flags_applied": present,
            "flags_unavailable": [f for f in flags if f not in work],
            "by_flag": {f: int(work[f].fillna(False).astype(bool).sum()) for f in present}}
    return work.loc[~mask], info


def add_duplicate_repeat_flag(df: pd.DataFrame) -> pd.DataFrame:
    """Mark all but the first row of each possible-duplicate group (for sensitivity runs only)."""
    if "duplicate_group" not in df or "flag_possible_duplicate_repeat" in df:
        return df
    out = df.copy()
    g = out["duplicate_group"]
    out["flag_possible_duplicate_repeat"] = g.notna() & g.duplicated(keep="first")
    return out


def records(df: pd.DataFrame) -> list[dict]:
    return [{k: num(v) for k, v in r.items()} for r in df.to_dict("records")]


def to_jsonable(obj):
    """Recursively convert numpy/pandas scalars and NaN to JSON-safe values."""
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp,)):
        return obj.isoformat()
    if isinstance(obj, (np.integer, np.floating, float)) or obj is pd.NA or obj is pd.NaT:
        return num(obj)
    return obj


def result(section: str, title: str, question: str, n_rows: int, cfg: AnalysisConfig, **content) -> dict:
    return to_jsonable({"section": section, "title": title, "question": question,
                        "analysis_version": ANALYSIS_VERSION, "n_rows": int(n_rows), "config": cfg.to_dict(),
                        **content})
