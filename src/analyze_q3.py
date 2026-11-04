"""Supporting Question 3: differences across communities and participant populations.

"How do training experiences and outcomes differ across communities and
participant populations, and what might these differences reveal about
opportunities to improve accessibility, equity, and community impact?"

Groupings: county, participant role (multi-select, overlapping), primary role
(exclusive), and survey language.

Minimum-group-size policy (AnalysisConfig):
  * n < min_cell (default 10): statistics suppressed, n shown as "<10".
  * min_cell <= n < min_compare (default 30): descriptive results shown, but the
    group is excluded from comparisons, signals and tests.
  * n >= min_compare: included in comparisons.
A small or absent group is a data-collection fact, not evidence of poor access.
"""

from __future__ import annotations

import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis_utils import (
    AGREE_MIN, EXPLORATORY, FOLLOW_UP, VERIFIED, AnalysisConfig, apply_exclusions, available_objectives,
    available_post_objectives, has, num, pct, result, sat_composite, share, wilson_ci,
)
from src.analyze_q1 import paired
from src.schema_mapper import ROLE_ORDER

QUESTION = ("How do training experiences and outcomes differ across communities and participant populations, and "
            "what might these differences reveal about opportunities to improve accessibility, equity, and "
            "community impact?")
ROLE_LABELS = {"student": "Student", "school_staff": "School staff", "caregiver_parent": "Caregiver/parent",
               "other_community": "Other / community member", "multiple": "Multiple roles",
               "unrecognized": "Unrecognized role"}
LANG_LABELS = {"en": "English", "es": "Spanish", "other": "Other language"}
GUARDRAILS = [
    "Group sizes reflect who was trained and surveyed, not population need or access. A small or missing group "
    "is not evidence of poor access.",
    "Groups differ in session, presenter, setting and audience, so differences are descriptive and not causal.",
    "Roles are multi-select: a respondent can appear in several role groups, so role groups overlap and are "
    "not compared with significance tests (primary role is used for that).",
    "'Before' ratings are recalled after training.",
]


def _display_n(n: int, cfg: AnalysisConfig) -> str:
    return f"<{cfg.min_cell}" if n < cfg.min_cell else str(n)


def group_profile(g: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    """Outcome profile for one group. Statistics are None when suppressed."""
    n = len(g)
    prof = {"n_rows": n if n >= cfg.min_cell else None, "n_display": _display_n(n, cfg),
            "suppressed": n < cfg.min_cell, "comparable": n >= cfg.min_compare,
            "n_sessions": int(g["session_key"].nunique()) if "session_key" in g and n >= cfg.min_cell else None}
    if prof["suppressed"]:
        return prof
    objs = {}
    for o in available_objectives(g):
        p = paired(g, o)
        k = len(p)
        if k < cfg.min_cell:
            objs[o["key"]] = {"paired_n": None, "paired_n_display": _display_n(k, cfg), "suppressed": True}
            continue
        ch = p["post"] - p["pre"]
        pre_a, post_a = share(p["pre"] >= AGREE_MIN), share(p["post"] >= AGREE_MIN)
        objs[o["key"]] = {
            "paired_n": k, "suppressed": False,
            "pre_agree": pre_a, "post_agree": post_a, "agree_pp_change": round(post_a["pct"] - pre_a["pct"], 1),
            "not_agree_after": share(p["post"] < AGREE_MIN), "improved": share(ch > 0),
            "declined": share(ch < 0), "mean_change": num(ch.mean()),
            "pre_distribution": {int(s): int(c) for s, c in p["pre"].value_counts().sort_index().items()},
            "post_distribution": {int(s): int(c) for s, c in p["post"].value_counts().sort_index().items()},
        }
    for o in available_post_objectives(g):
        if o["key"] not in objs:  # post item without its pre item: still report post confidence
            v = g[o["post"]].dropna()
            objs[o["key"]] = ({"paired_n": None, "post_only_n": len(v), "suppressed": False,
                               "post_agree": share(v >= AGREE_MIN), "not_agree_after": share(v < AGREE_MIN)}
                              if len(v) >= cfg.min_cell else {"post_only_n": None, "suppressed": True})
    prof["objectives"] = objs

    comp = sat_composite(g).dropna()
    exp = {"valid_n": len(comp) if len(comp) >= cfg.min_cell else None}
    if len(comp) >= cfg.min_cell:
        exp.update({"agree": share(comp >= AGREE_MIN), "mean": num(comp.mean())})
    prof["experience"] = exp
    for f, key in (("likelihood_recommend", "recommend"), ("likelihood_call_988", "call_988")):
        if f not in g:
            prof[key] = {"collected": False}
            continue
        v = g[f].dropna().astype(float)
        prof[key] = {"collected": True, "valid_n": len(v) if len(v) >= cfg.min_cell else None,
                     "valid_n_display": _display_n(len(v), cfg),
                     "mean": num(v.mean()) if len(v) >= cfg.min_cell else None,
                     "high_9_10": share(v >= 9) if len(v) >= cfg.min_cell else None}
    prof["missingness_pct"] = {
        "experience": pct(int(sat_composite(g).isna().sum()), n),
        **{f: (pct(int(g[f].isna().sum()), n) if f in g else None)
           for f in ("likelihood_recommend", "likelihood_call_988")},
        "training_date": pct(int((~g["training_date_valid"]).sum()), n) if "training_date_valid" in g else None,
    }
    if "survey_version" in g:
        vc = g["survey_version"].value_counts()
        prof["survey_versions"] = [{"version": v, "n_display": _display_n(int(c), cfg), "pct": pct(int(c), n)}
                                   for v, c in vc.items()]
    if "role_primary" in g:
        vc = g["role_primary"].value_counts()
        prof["role_mix_pct"] = {str(r): pct(int(c), n) for r, c in vc.items()}
    return prof


def _groups(df: pd.DataFrame, dimension: str) -> list[tuple[str, str, pd.Series, dict]]:
    """[(key, label, mask, meta)] for a grouping dimension."""
    out = []
    if dimension == "county" and "county" in df:
        for c in sorted(df["county"].dropna().unique(), key=str):
            m = df["county"] == c
            sub = df[m]
            meta = {"status": sub["county_status"].mode().iat[0] if "county_status" in sub else None,
                    "state": (sub["county_state"].dropna().mode().iat[0]
                              if "county_state" in sub and sub["county_state"].notna().any() else None),
                    "in_service_area": (bool(sub["in_service_area"].fillna(False).all())
                                        if "in_service_area" in sub and sub["in_service_area"].notna().any()
                                        else None)}
            out.append((str(c), str(c), m, meta))
        if df["county"].isna().any():
            out.append(("(missing)", "County not reported", df["county"].isna(), {"status": "missing"}))
    elif dimension == "role":
        codes = [c.removeprefix("role_") for c in df.columns
                 if c.startswith("role_") and c not in ("role_primary", "role_count", "role_unrecognized")]
        codes = [c for c in ROLE_ORDER if c in codes] + sorted(c for c in codes if c not in ROLE_ORDER)
        for c in codes:
            m = df[f"role_{c}"].fillna(False).astype(bool)
            out.append((c, ROLE_LABELS.get(c, c.replace("_", " ").title()), m, {"overlapping": True}))
    elif dimension == "role_primary" and "role_primary" in df:
        for c in [r for r in ROLE_ORDER + ["multiple", "unrecognized"] if r in set(df["role_primary"].dropna())]:
            out.append((c, ROLE_LABELS.get(c, c), df["role_primary"] == c, {}))
    elif dimension == "language" and "language" in df:
        for c in sorted(df["language"].dropna().unique(), key=str):
            out.append((str(c), LANG_LABELS.get(c, str(c)), df["language"] == c, {}))
    return out


def compare(df: pd.DataFrame, groups: list, cfg: AnalysisConfig, overlapping: bool) -> dict:
    """Each comparable group vs the rest of the sample; Kruskal-Wallis across groups if non-overlapping."""
    from scipy.stats import kruskal

    signals, tests = [], []
    comparable = [(k, lab, m) for k, lab, m, _ in groups if int(m.sum()) >= cfg.min_compare]
    for o in available_objectives(df):
        p_all = paired(df, o)
        for k, lab, m in comparable:
            p_in = p_all[m.loc[p_all.index]]
            p_out = p_all[~m.loc[p_all.index]]
            if len(p_in) < cfg.min_compare or len(p_out) < cfg.min_compare:
                continue
            for metric, fn in (("post_agree", lambda p: p["post"] >= AGREE_MIN),
                               ("agree_pp_change", None)):
                if metric == "post_agree":
                    a, b = share(fn(p_in)), share(fn(p_out))
                    diff = round(a["pct"] - b["pct"], 1)
                    separated = a["ci_high"] < b["ci_low"] or b["ci_high"] < a["ci_low"]
                else:
                    a_ch = (p_in["post"] >= AGREE_MIN).mean() - (p_in["pre"] >= AGREE_MIN).mean()
                    b_ch = (p_out["post"] >= AGREE_MIN).mean() - (p_out["pre"] >= AGREE_MIN).mean()
                    diff = round(100 * (a_ch - b_ch), 1)
                    a, b, separated = {"pct": round(100 * a_ch, 1), "n": len(p_in)}, \
                        {"pct": round(100 * b_ch, 1), "n": len(p_out)}, None
                signals.append({
                    "objective": o["key"], "group": k, "label": lab, "metric": metric,
                    "group_value": a["pct"], "group_n": a["n"], "rest_value": b["pct"], "rest_n": b["n"],
                    "difference_pp": diff, "ci_separated": separated,
                    "signal": bool(abs(diff) >= cfg.meaningful_pp and (separated is None or separated)),
                    "evidence_type": EXPLORATORY,
                })
        if not overlapping and len(comparable) >= 2:
            samples = [(p_all["post"] - p_all["pre"])[m.loc[p_all.index]] for _, _, m in comparable]
            samples = [s for s in samples if len(s) >= cfg.min_compare]
            if len(samples) >= 2 and pd.concat(samples).nunique() > 1:
                stat = kruskal(*samples)
                tests.append({"objective": o["key"], "test": "Kruskal-Wallis on change scores",
                              "groups": [k for k, _, _ in comparable], "h": round(float(stat.statistic), 3),
                              "p_value": float(f"{stat.pvalue:.3g}"), "evidence_type": EXPLORATORY})
    return {"signals": [s for s in signals if s["signal"]], "all_comparisons": signals, "tests": tests,
            "rule": f"Signal = |difference| >= {cfg.meaningful_pp} pp vs the rest of the sample, both sides "
                    f"n >= {cfg.min_compare}, and (for proportions) non-overlapping 95% Wilson intervals."}


def composition_check(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    """Is a county difference explained by its role mix? Crude vs role-standardized post-agree rates."""
    if not has(df, "county", "role_primary"):
        return {"available": False}
    strata = df["role_primary"].where(df["role_primary"] == "student", "non_student")
    overall_mix = strata.value_counts(normalize=True)
    mix_rows = []
    for c in sorted(df["county"].dropna().unique(), key=str):
        sub = df[df["county"] == c]
        n = len(sub)
        if n < cfg.min_cell:
            continue
        mix_rows.append({"county": c, "n": n, "student_pct": pct(int((strata[sub.index] == "student").sum()), n)})
    adj = []
    for o in available_objectives(df):
        p_all = paired(df, o)
        for c in [r["county"] for r in mix_rows]:
            in_c = df.loc[p_all.index, "county"] == c
            crude_n = int(in_c.sum())
            if crude_n < cfg.min_compare:
                continue
            row = {"objective": o["key"], "county": c, "paired_n": crude_n, "by_stratum": {}}
            ok = True
            rates = {}
            for s_name in overall_mix.index:
                m = in_c & (strata.loc[p_all.index] == s_name)
                k = int(m.sum())
                if k < cfg.min_cell:
                    ok = False
                    row["by_stratum"][s_name] = {"n": None, "n_display": _display_n(k, cfg)}
                    continue
                ps = p_all[m]
                rates[s_name] = {"pre": 100 * (ps["pre"] >= AGREE_MIN).mean(),
                                 "post": 100 * (ps["post"] >= AGREE_MIN).mean()}
                row["by_stratum"][s_name] = {"n": k, "pre_agree_pct": round(rates[s_name]["pre"], 1),
                                             "post_agree_pct": round(rates[s_name]["post"], 1)}
            pc = p_all[in_c]
            for metric in ("pre", "post"):
                crude = 100 * (pc[metric] >= AGREE_MIN).mean()
                std = sum(overall_mix[s] * rates[s][metric] for s in overall_mix.index) if ok else None
                row[f"crude_{metric}_agree_pct"] = round(crude, 1)
                row[f"standardized_{metric}_agree_pct"] = round(std, 1) if std is not None else None
            row["crude_pp_change"] = round(row["crude_post_agree_pct"] - row["crude_pre_agree_pct"], 1)
            row["standardized_pp_change"] = (round(row["standardized_post_agree_pct"]
                                                   - row["standardized_pre_agree_pct"], 1) if ok else None)
            shifts = [abs(row[f"standardized_{m}_agree_pct"] - row[f"crude_{m}_agree_pct"])
                      for m in ("pre", "post")] if ok else []
            row["max_composition_shift_pp"] = round(max(shifts), 1) if shifts else None
            row["composition_may_matter"] = (max(shifts) >= 5) if shifts else None
            row["standardization_possible"] = ok
            adj.append(row)
    return {"available": True, "strata": "student vs non-student (role_primary)",
            "overall_mix_pct": {k: round(100 * v, 1) for k, v in overall_mix.items()},
            "county_role_mix": mix_rows, "standardized": adj,
            "not_standardizable": sorted({r["county"] for r in adj if not r["standardization_possible"]}),
            "method": "Direct standardization of each county's recalled-before and current agree rates (paired "
                      "respondents) to the overall student/non-student mix. A county lacking >= min_cell "
                      "respondents in a stratum cannot be standardized: its difference from other counties cannot "
                      "be separated from its audience mix.",
            "evidence_type": EXPLORATORY}


def grouping(df: pd.DataFrame, dimension: str, cfg: AnalysisConfig) -> dict:
    groups = _groups(df, dimension)
    if not groups:
        return {"dimension": dimension, "available": False, "groups": []}
    overlapping = dimension == "role"
    rows = []
    for k, lab, m, meta in groups:
        rows.append({"key": k, "label": lab, **meta, **group_profile(df[m], cfg)})
    cmp = compare(df, groups, cfg, overlapping)
    return {"dimension": dimension, "available": True, "overlapping": overlapping,
            "membership_total": int(sum(int(m.sum()) for _, _, m, _ in groups)), "rows_total": len(df),
            "groups": rows, "comparison": cmp,
            "suppressed_groups": [r["key"] for r in rows if r["suppressed"]],
            "descriptive_only_groups": [r["key"] for r in rows if not r["suppressed"] and not r["comparable"]],
            "evidence_type": VERIFIED}


def run_q3(df: pd.DataFrame, cfg: AnalysisConfig | None = None) -> dict:
    cfg = cfg or AnalysisConfig()
    work, excl = apply_exclusions(df, cfg.exclude_flags)
    dims = {d: grouping(work, d, cfg) for d in ("county", "role", "role_primary", "language")}
    follow_ups = []
    for d, g in dims.items():
        for k in g.get("descriptive_only_groups", []) + g.get("suppressed_groups", []):
            follow_ups.append({"dimension": d, "group": k, "evidence_type": FOLLOW_UP,
                               "note": "Too few responses to compare. Collect more responses before drawing "
                                       "conclusions; do not interpret as low access or poor outcomes."})
    return result(
        "q3", "Community and population differences", QUESTION, len(work), cfg,
        exclusions=excl,
        policy={"min_cell": cfg.min_cell, "min_compare": cfg.min_compare, "meaningful_pp": cfg.meaningful_pp,
                "description": (
                    f"- n < {cfg.min_cell}: statistics suppressed and n shown as '<{cfg.min_cell}' (privacy and "
                    "stability).\n"
                    f"- {cfg.min_cell} <= n < {cfg.min_compare}: descriptive results shown; excluded from "
                    "comparisons, signals and tests.\n"
                    f"- n >= {cfg.min_compare}: included in comparisons.\n"
                    "- A small or absent group is a data-collection fact, not evidence of poor access.")},
        groupings=dims,
        composition_check=composition_check(work, cfg),
        small_group_follow_ups=follow_ups,
        guardrails=GUARDRAILS,
    )
