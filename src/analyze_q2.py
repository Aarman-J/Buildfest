"""Supporting Question 2: trends, relationships and differences in experiences and outcomes.

"What trends, relationships, or differences in the survey data provide insights
into participants' training experiences and outcomes?"

Associations are Spearman rank correlations on pairwise-complete rows with the
actual n reported. They are exploratory (no causal claims, no multiple-testing
correction). `likelihood_call_988` is only asked in some survey versions, so its
results describe the respondents who answered it, not all participants. Missing answers are answer
coverage, not confirmed question exposure.
"""

from __future__ import annotations

import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis_utils import (
    AGREE_MIN, EXPLORATORY, SAT_LABELS, VERIFIED, AnalysisConfig, apply_exclusions, available_objectives,
    available_post_objectives, describe_numeric, has, item_coverage, likert_distribution, num, pct,
    rating_distribution, result, sat_composite, share, spearman, suppress,
)
from src.feedback_themes import theme_summary
from src.schema_mapper import SAT_FIELDS, TEXT_FIELDS

QUESTION = ("What trends, relationships, or differences in the survey data provide insights into participants' "
            "training experiences and outcomes?")


def experience_items(df: pd.DataFrame, cfg: AnalysisConfig) -> list[dict]:
    out = []
    for f in SAT_FIELDS:
        cov = item_coverage(df, f)
        row = {"field": f, "label": SAT_LABELS[f], "coverage": cov, "suppressed": suppress(cov["valid"], cfg)}
        if cov["collected"] and not row["suppressed"]:
            v = df[f].dropna().astype(int)
            row.update({"distribution": likert_distribution(v), "agree": share(v >= AGREE_MIN),
                        "strongly_agree": share(v == 5), "mean": num(v.mean()), "evidence_type": VERIFIED})
        out.append(row)
    return out


def recommendation(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    cov = item_coverage(df, "likelihood_recommend")
    out = {"coverage": cov, "suppressed": suppress(cov["valid"], cfg)}
    if not cov["collected"] or out["suppressed"]:
        return out
    v = df["likelihood_recommend"].dropna().astype(int)
    prom, pas, det = share(v >= 9), share(v.between(7, 8)), share(v <= 6)
    out.update({
        "distribution": rating_distribution(v), "summary": describe_numeric(v),
        "promoters_9_10": prom, "passives_7_8": pas, "detractors_0_6": det,
        "nps": round(prom["pct"] - det["pct"], 1),
        "nps_note": "NPS = % promoters (9-10) minus % detractors (0-6), range -100 to 100. Shown as a secondary "
                    "view; the full 0-10 distribution is the primary result.",
        "evidence_type": VERIFIED,
    })
    return out


def call_988(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    cov = item_coverage(df, "likelihood_call_988")
    out = {"coverage": cov, "suppressed": suppress(cov["valid"], cfg),
           "caveat": "Answer coverage only: missing answers may mean the question was not shown on some survey "
                     "forms or was skipped (inferred survey versions are not confirmed form history). Results "
                     "describe respondents who answered; they must not be generalized to all participants."}
    if not cov["collected"]:
        return out
    if "survey_version" in df:
        by_v = (df.assign(_valid=df["likelihood_call_988"].notna())
                .groupby("survey_version")["_valid"].agg(["size", "sum"]).reset_index())
        out["coverage_by_survey_version"] = [
            {"survey_version": r["survey_version"], "rows": int(r["size"]), "valid": int(r["sum"]),
             "valid_pct": pct(int(r["sum"]), int(r["size"]))} for _, r in by_v.iterrows()]
    if out["suppressed"]:
        return out
    v = df["likelihood_call_988"].dropna().astype(int)
    out.update({"distribution": rating_distribution(v), "summary": describe_numeric(v),
                "very_likely_9_10": share(v >= 9), "unlikely_0_4": share(v <= 4),
                "evidence_type": VERIFIED})
    return out


def relationships(df: pd.DataFrame, cfg: AnalysisConfig) -> list[dict]:
    """Spearman correlations between experience, confidence and intent measures."""
    comp = sat_composite(df)
    objs = available_objectives(df)
    posts = available_post_objectives(df)
    specs: list[tuple[str, str, pd.Series, str, pd.Series, str]] = []
    if comp.notna().any():
        for o in posts:
            specs.append(("experience_vs_post_confidence", "Training experience (mean of 3 items)", comp,
                          f"Post: {o['label']}", df[o["post"]], o["key"]))
        for o in objs:
            specs.append(("experience_vs_confidence_change", "Training experience (mean of 3 items)", comp,
                          f"Change: {o['label']}", df[o["post"]] - df[o["pre"]], o["key"]))
        if has(df, "likelihood_recommend"):
            specs.append(("experience_vs_recommend", "Training experience (mean of 3 items)", comp,
                          "Likelihood to recommend", df["likelihood_recommend"], "recommend"))
    for f in [f for f in SAT_FIELDS if f in df]:
        for o in posts:
            specs.append(("experience_item_vs_post_confidence", SAT_LABELS[f], df[f],
                          f"Post: {o['label']}", df[o["post"]], o["key"]))
    if has(df, "likelihood_recommend"):
        for o in objs:
            specs.append(("recommend_vs_confidence_change", "Likelihood to recommend", df["likelihood_recommend"],
                          f"Change: {o['label']}", df[o["post"]] - df[o["pre"]], o["key"]))
    if has(df, "likelihood_call_988"):
        for o in posts:
            specs.append(("post_confidence_vs_call_988", f"Post: {o['label']}", df[o["post"]],
                          "Likelihood to call 988", df["likelihood_call_988"], o["key"]))
        for o in objs:
            specs.append(("confidence_change_vs_call_988", f"Change: {o['label']}", df[o["post"]] - df[o["pre"]],
                          "Likelihood to call 988", df["likelihood_call_988"], o["key"]))
    out = []
    for family, xl, x, yl, y, key in specs:
        r = spearman(x, y, cfg.min_compare)
        out.append({"family": family, "x": xl, "y": yl, "objective": key, **r,
                    "significant": r["p_value"] is not None and r["p_value"] < cfg.alpha,
                    "evidence_type": EXPLORATORY})
    return out


def satisfaction_vs_gap(df: pd.DataFrame, cfg: AnalysisConfig) -> dict:
    """Do highly satisfied respondents still report confidence gaps?"""
    groups = {}
    comp = sat_composite(df)
    if comp.notna().any():
        groups["highly_satisfied"] = ("Mean experience rating >= 4.5", comp >= 4.5, comp.notna())
    if has(df, "likelihood_recommend"):
        rec = df["likelihood_recommend"]
        groups["promoters"] = ("Likelihood to recommend 9-10", rec >= 9, rec.notna())
    out = {"definitions": {k: v[0] for k, v in groups.items()}, "rows": []}
    for o in available_post_objectives(df):
        post = df[o["post"]]
        for g, (_, in_group, valid) in groups.items():
            base = valid & post.notna()
            ing = base & in_group.fillna(False).astype(bool)
            outg = base & ~in_group.fillna(False).astype(bool)
            row = {"objective": o["key"], "label": o["label"], "group": g,
                   "group_n": int(ing.sum()), "others_n": int(outg.sum())}
            if suppress(int(ing.sum()), cfg) or suppress(int(outg.sum()), cfg):
                row.update({"suppressed": True})
            else:
                gi, go = share(post[ing] < AGREE_MIN), share(post[outg] < AGREE_MIN)
                row.update({"suppressed": False, "group_not_agree_after": gi, "others_not_agree_after": go,
                            "satisfied_but_not_confident_n": gi["k"],
                            "satisfied_but_not_confident_pct_of_all_valid": pct(gi["k"], int(base.sum()))})
            out["rows"].append(row)
    out["evidence_type"] = EXPLORATORY
    out["note"] = ("Shows respondents who rate the training highly but still do not agree they can perform an "
                   "objective. High satisfaction alone is not evidence of learning.")
    return out


def experience_trend(df: pd.DataFrame, cfg: AnalysisConfig) -> list[dict]:
    """Experience and recommendation by training month (valid training dates only)."""
    if "training_month" not in df:
        return []
    rows = []
    comp = sat_composite(df)
    for m, g in df[df["training_month"].notna()].groupby("training_month", sort=True):
        c = comp.loc[g.index].dropna()
        rec = g["likelihood_recommend"].dropna() if "likelihood_recommend" in g else pd.Series(dtype=float)
        rows.append({"month": m, "n_rows": int(len(g)), "suppressed": suppress(len(g), cfg),
                     "experience_valid_n": int(len(c)),
                     "experience_mean": None if suppress(len(c), cfg) else num(c.mean()),
                     "experience_agree_pct": None if suppress(len(c), cfg) else pct(int((c >= AGREE_MIN).sum()), len(c)),
                     "recommend_valid_n": int(len(rec)),
                     "recommend_mean": None if suppress(len(rec), cfg) else num(rec.astype(float).mean())})
    return rows


def run_q2(df: pd.DataFrame, cfg: AnalysisConfig | None = None) -> dict:
    cfg = cfg or AnalysisConfig()
    work, excl = apply_exclusions(df, cfg.exclude_flags)
    rels = relationships(work, cfg)
    notable = sorted([r for r in rels if r["rho"] is not None and abs(r["rho"]) >= 0.3],
                     key=lambda r: -abs(r["rho"]))
    return result(
        "q2", "Experiences, relationships and feedback", QUESTION, len(work), cfg,
        exclusions=excl,
        experience=experience_items(work, cfg),
        experience_composite={**describe_numeric(sat_composite(work)),
                              "definition": "Mean of the available presenter/organization items (1-5) per respondent"},
        recommendation=recommendation(work, cfg),
        call_988=call_988(work, cfg),
        relationships=rels,
        notable_relationships=notable,
        satisfaction_vs_gap=satisfaction_vs_gap(work, cfg),
        experience_trend=experience_trend(work, cfg),
        feedback={f: theme_summary(work, f, cfg.min_cell) for f in TEXT_FIELDS},
        caveat="Correlations are exploratory, computed on respondents with both measures, and do not show cause.",
    )
