"""Supporting Question 1: training effectiveness and monitoring over time.

"How can EveryMind better evaluate training effectiveness, identify meaningful
patterns in participant responses, and monitor outcomes over time?"

Uses the three retrospective pre/post confidence pairs. The "before" ratings
are recalled after the training, so changes are *self-reported perceived
change*, not causal evidence of training effect.
"""

from __future__ import annotations

import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis_utils import (
    AGREE_MIN, EXPLORATORY, OBJECTIVES, SENSITIVITY_SCENARIOS, VERIFIED, AnalysisConfig, apply_exclusions,
    available_objectives, describe_numeric, has, item_coverage, likert_distribution, num, pct, result, share,
    suppress,
)
from src.schema_mapper import LIKERT_LABELS, RATING_FIELDS, SAT_FIELDS

QUESTION = ("How can EveryMind better evaluate training effectiveness, identify meaningful patterns in participant "
            "responses, and monitor outcomes over time?")
ROBUSTNESS_SCENARIOS = ("exclude_reverse_scale_suspects", "exclude_fast_completions", "exclude_possible_duplicates")
ROBUST_PP = 5.0
CAVEAT = ("'Before' ratings were recalled after the training (retrospective pre/post). Differences describe "
          "self-reported perceived change and cannot prove that the training caused it.")


def paired(df: pd.DataFrame, obj: dict) -> pd.DataFrame:
    """Rows with valid answers to both items of the pair, as int scores."""
    p = df[[obj["pre"], obj["post"]]].dropna()
    return pd.DataFrame({"pre": p[obj["pre"]].astype(int), "post": p[obj["post"]].astype(int)}, index=p.index)


def objective_summary(df: pd.DataFrame, obj: dict, cfg: AnalysisConfig) -> dict:
    from scipy.stats import binomtest

    pre_valid = int(df[obj["pre"]].notna().sum())
    post_valid = int(df[obj["post"]].notna().sum())
    p = paired(df, obj)
    n = len(p)
    out = {
        "key": obj["key"], "label": obj["label"], "pre_field": obj["pre"], "post_field": obj["post"],
        "coverage": {"rows": len(df), "pre_valid": pre_valid, "post_valid": post_valid, "paired_n": n,
                     "unpaired": int((df[obj["pre"]].notna() ^ df[obj["post"]].notna()).sum()),
                     "pre": item_coverage(df, obj["pre"]), "post": item_coverage(df, obj["post"])},
        "suppressed": suppress(n, cfg),
    }
    if out["suppressed"]:
        return out
    change = p["post"] - p["pre"]
    pre_agree, post_agree = share(p["pre"] >= AGREE_MIN), share(p["post"] >= AGREE_MIN)
    improved, unchanged, declined = int((change > 0).sum()), int((change == 0).sum()), int((change < 0).sum())
    below_top = p["pre"] < 5
    not_agree_before = p["pre"] < AGREE_MIN
    test = binomtest(improved, improved + declined, 0.5) if improved + declined else None
    out.update({
        "pre_distribution": likert_distribution(p["pre"]),
        "post_distribution": likert_distribution(p["post"]),
        "pre_agree": pre_agree,
        "post_agree": post_agree,
        "agree_pp_change": round(post_agree["pct"] - pre_agree["pct"], 1),
        "pre_mean": num(p["pre"].mean()), "post_mean": num(p["post"].mean()),
        "change": {
            **describe_numeric(change),
            "improved": share(change > 0), "unchanged": share(change == 0), "declined": share(change < 0),
            "sign_test_p_value": float(f"{test.pvalue:.3g}") if test else None,
            "sign_test_note": "Exploratory: compares how many improved vs declined (ties ignored).",
        },
        "transitions": {
            "moved_into_agree": share(p["post"] >= AGREE_MIN, not_agree_before) if not_agree_before.any()
            else {"k": 0, "n": 0, "pct": None, "ci_low": None, "ci_high": None},
            "moved_out_of_agree": share(p["post"] < AGREE_MIN, ~not_agree_before) if (~not_agree_before).any()
            else {"k": 0, "n": 0, "pct": None, "ci_low": None, "ci_high": None},
        },
        "remaining_gap": {
            "not_agree_after": share(p["post"] < AGREE_MIN),
            "neutral_after": share(p["post"] == 3),
            "disagree_after": share(p["post"] <= 2),
        },
        "ceiling": {
            "pre_strongly_agree": share(p["pre"] == 5),
            "pre_agree_or_higher": pre_agree,
            "improved_among_room_to_improve": share(change > 0, below_top) if below_top.any()
            else {"k": 0, "n": 0, "pct": None, "ci_low": None, "ci_high": None},
            "note": "Respondents already at 'Strongly agree' before cannot show improvement on this scale.",
        },
        "evidence_type": VERIFIED,
    })
    return out


def transition_matrix(df: pd.DataFrame, obj: dict, cfg: AnalysisConfig) -> dict:
    """Pre x post counts for heatmap / Sankey charts. Cells below min_cell are reported but marked."""
    p = paired(df, obj)
    n = len(p)
    if suppress(n, cfg):
        return {"key": obj["key"], "paired_n": n, "suppressed": True, "cells": []}
    tab = pd.crosstab(p["pre"], p["post"]).reindex(index=range(1, 6), columns=range(1, 6), fill_value=0)
    cells = [{"pre_score": i, "pre_label": LIKERT_LABELS[i], "post_score": j, "post_label": LIKERT_LABELS[j],
              "n": int(tab.loc[i, j]), "pct_of_paired": pct(int(tab.loc[i, j]), n)}
             for i in tab.index for j in tab.columns]
    return {"key": obj["key"], "paired_n": n, "suppressed": False, "cells": cells}


def trend_by_month(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    """Outcomes by *training* month. Rows without a valid training date are counted, not reassigned."""
    if "training_month" not in df:
        return {"available": False, "reason": "No training date column was mapped.", "months": []}
    valid = df["training_month"].notna()
    out = {"available": bool(valid.any()), "rows_with_valid_training_date": int(valid.sum()),
           "rows_without_valid_training_date": int((~valid).sum()),
           "note": "Months use the respondent-reported training date only. Survey completion dates are not "
                   "substituted for missing or invalid training dates.",
           "months": []}
    objs = available_objectives(df)
    for month, g in df[valid].groupby("training_month", sort=True):
        row = {"month": month, "n_rows": int(len(g)),
               "n_sessions": int(g["session_key"].nunique()) if "session_key" in g else None,
               "suppressed": suppress(len(g), cfg), "below_min_compare": len(g) < cfg.min_compare}
        for o in objs:
            p = paired(g, o)
            row[f"{o['key']}_paired_n"] = len(p)
            if row["suppressed"] or suppress(len(p), cfg):
                row[f"{o['key']}_pre_agree_pct"] = row[f"{o['key']}_post_agree_pct"] = None
                row[f"{o['key']}_agree_pp_change"] = row[f"{o['key']}_not_agree_after_pct"] = None
                continue
            pre_a, post_a = pct((p["pre"] >= AGREE_MIN).sum(), len(p)), pct((p["post"] >= AGREE_MIN).sum(), len(p))
            row[f"{o['key']}_pre_agree_pct"], row[f"{o['key']}_post_agree_pct"] = pre_a, post_a
            row[f"{o['key']}_agree_pp_change"] = round(post_a - pre_a, 1)
            row[f"{o['key']}_not_agree_after_pct"] = pct((p["post"] < AGREE_MIN).sum(), len(p))
        for f in SAT_FIELDS + RATING_FIELDS:
            row[f"{f}_valid_pct"] = (pct(int(g[f].notna().sum()), len(g)) if f in g else None)
        out["months"].append(row)
    return out


def sensitivity(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    """Recompute headline pre/post figures excluding flagged rows, one scenario at a time."""
    objs = available_objectives(df)
    rows = []
    for name, flags in SENSITIVITY_SCENARIOS.items():
        sub, info = apply_exclusions(df, flags)
        row = {"scenario": name, "rows": int(len(sub)), "excluded_rows": info["excluded_rows"],
               "flags_unavailable": info["flags_unavailable"]}
        for o in objs:
            p = paired(sub, o)
            n = len(p)
            row[f"{o['key']}_paired_n"] = n
            if suppress(n, cfg):
                row[f"{o['key']}_post_agree_pct"] = row[f"{o['key']}_agree_pp_change"] = None
                continue
            pre_a, post_a = pct((p["pre"] >= AGREE_MIN).sum(), n), pct((p["post"] >= AGREE_MIN).sum(), n)
            row[f"{o['key']}_post_agree_pct"] = post_a
            row[f"{o['key']}_agree_pp_change"] = round(post_a - pre_a, 1)
        rows.append(row)
    base = rows[0]
    shifts = {}
    for r in rows[1:]:
        s = 0.0
        for o in objs:
            for k in ("post_agree_pct", "agree_pp_change"):
                a, b = base.get(f"{o['key']}_{k}"), r.get(f"{o['key']}_{k}")
                if a is not None and b is not None:
                    s = max(s, abs(a - b))
        r["max_shift_pp"] = round(s, 1)
        shifts[r["scenario"]] = s
    quality = [s for s in ROBUSTNESS_SCENARIOS if s in shifts]
    max_q = max((shifts[s] for s in quality), default=0.0)
    return {"scenarios": rows, "robustness_scenarios": quality, "max_shift_pp": round(max_q, 1),
            "robust": max_q < ROBUST_PP,
            "note": f"Flagged rows are kept in all main results. Robustness is judged on data-quality exclusions "
                    f"({', '.join(quality)}): a shift under {ROBUST_PP:g} percentage points counts as robust. "
                    "Excluding straight-liners is shown for information only: they report zero change by "
                    "definition, so removing them raises change figures mechanically."}


def run_q1(df: pd.DataFrame, cfg: AnalysisConfig | None = None) -> dict:
    cfg = cfg or AnalysisConfig()
    work, excl = apply_exclusions(df, cfg.exclude_flags)
    objs = available_objectives(work)
    missing_pairs = [o["key"] for o in OBJECTIVES if o not in objs]
    summaries = [objective_summary(work, o, cfg) for o in objs]
    ranked = sorted([s for s in summaries if not s["suppressed"]],
                    key=lambda s: -s["remaining_gap"]["not_agree_after"]["pct"])
    return result(
        "q1", "Training effectiveness", QUESTION, len(work), cfg,
        available=bool(objs),
        caveat=CAVEAT,
        exclusions=excl,
        objectives_missing=missing_pairs,
        objectives=summaries,
        transitions=[transition_matrix(work, o, cfg) for o in objs],
        largest_remaining_gap=ranked[0]["key"] if ranked else None,
        largest_agree_gain=(max(ranked, key=lambda s: s["agree_pp_change"])["key"] if ranked else None),
        trend=trend_by_month(work, cfg),
        sensitivity=sensitivity(df, cfg) if not cfg.exclude_flags else None,
        evidence_type=VERIFIED,
        tests_evidence_type=EXPLORATORY,
    )
