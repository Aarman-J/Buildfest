"""Run the full analysis engine and render example summaries.

Library use (e.g. from Streamlit):

    from src.run_analysis import run_all
    results = run_all(cleaned_df, quality_report)        # JSON-compatible dict
    md = render_summary(results, "q1")                   # optional Markdown

Command line (cleans the original file in memory, writes aggregate outputs only):

    python src/run_analysis.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis_utils import ANALYSIS_VERSION, AnalysisConfig, to_jsonable
from src.analyze_q1 import run_q1
from src.analyze_q2 import run_q2
from src.analyze_q3 import run_q3
from src.recommendations import generate_recommendations

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_all(cleaned: pd.DataFrame, quality_report: dict | None = None,
            cfg: AnalysisConfig | None = None) -> dict:
    """Run Q1-Q3 and recommendations. Pure: no I/O; output is aggregate-only."""
    cfg = cfg or AnalysisConfig()
    q1, q2, q3 = run_q1(cleaned, cfg), run_q2(cleaned, cfg), run_q3(cleaned, cfg)
    recs = generate_recommendations(q1, q2, q3, quality_report)
    return to_jsonable({"analysis_version": ANALYSIS_VERSION, "config": cfg.to_dict(), "n_rows": len(cleaned),
                        "q1": q1, "q2": q2, "q3": q3, "recommendations": recs})


# ---------------------------------------------------------------------------
# Markdown summaries (generated from results; no hard-coded findings)
# ---------------------------------------------------------------------------

def _table(rows: list[dict]) -> str:
    if not rows:
        return "_none_\n"
    cols = list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    lines += ["| " + " | ".join("" if r.get(c) is None else str(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _ci(s: dict | None) -> str:
    if not s or s.get("pct") is None:
        return "n/a"
    return f"{s['pct']}% [{s['ci_low']}-{s['ci_high']}]"


def summary_q1(q1: dict) -> str:
    L = [f"# Q1 summary: {q1['title']}\n", f"> {q1['question']}\n", f"**Rows analyzed:** {q1['n_rows']}. "
         f"**Caveat:** {q1['caveat']}\n"]
    if q1["objectives_missing"]:
        L.append(f"Pairs not available in this upload: {', '.join(q1['objectives_missing'])}.\n")
    L.append("## Confidence before and after (paired respondents)\n")
    L.append("Agree = 'Agree' or 'Strongly agree'. Brackets show 95% Wilson intervals.\n")
    L.append(_table([{
        "Objective": o["label"], "Paired n": o["coverage"]["paired_n"],
        "Agree before (recalled)": _ci(o.get("pre_agree")), "Agree after": _ci(o.get("post_agree")),
        "Change (pp)": o.get("agree_pp_change"),
        "Improved": _ci(o["change"]["improved"]) if o.get("change") else None,
        "Unchanged": _ci(o["change"]["unchanged"]) if o.get("change") else None,
        "Declined": _ci(o["change"]["declined"]) if o.get("change") else None,
    } for o in q1["objectives"]]))
    L.append("## Remaining gaps and ceiling effects\n")
    L.append(_table([{
        "Objective": o["label"],
        "Not agreeing after": _ci(o["remaining_gap"]["not_agree_after"]) if o.get("remaining_gap") else None,
        "of which neutral": o["remaining_gap"]["neutral_after"]["pct"] if o.get("remaining_gap") else None,
        "of which disagree": o["remaining_gap"]["disagree_after"]["pct"] if o.get("remaining_gap") else None,
        "Strongly agree before (ceiling)": o["ceiling"]["pre_strongly_agree"]["pct"] if o.get("ceiling") else None,
        "Moved into agree (of those not agreeing before)": _ci(o["transitions"]["moved_into_agree"])
        if o.get("transitions") else None,
    } for o in q1["objectives"]]))
    t = q1["trend"]
    L.append("## Trend by training month\n")
    if not t.get("available"):
        L.append(f"Not available: {t.get('reason', 'no valid training dates')}\n")
    else:
        L.append(f"{t['note']} Rows without a valid training date: {t['rows_without_valid_training_date']}. "
                 "Months with suppressed values have fewer than the minimum respondents.\n")
        keys = [o["key"] for o in q1["objectives"]]
        L.append(_table([{"Month": m["month"], "n": m["n_rows"], "Sessions": m.get("n_sessions"),
                          **{f"{k} after %": m.get(f"{k}_post_agree_pct") for k in keys},
                          **{f"{k} change pp": m.get(f"{k}_agree_pp_change") for k in keys},
                          "call_988 answered %": m.get("likelihood_call_988_valid_pct")} for m in t["months"]]))
    s = q1.get("sensitivity")
    if s:
        L.append("## Sensitivity to flagged responses\n")
        L.append(s["note"] + "\n")
        keys = [o["key"] for o in q1["objectives"]]
        L.append(_table([{"Scenario": r["scenario"], "Rows": r["rows"],
                          **{f"{k} change pp": r.get(f"{k}_agree_pp_change") for k in keys},
                          "Max shift pp": r.get("max_shift_pp")} for r in s["scenarios"]]))
        L.append(f"Robust (data-quality exclusions shift results < 5 pp): **{s['robust']}** "
                 f"(max shift {s['max_shift_pp']} pp).\n")
    return "\n".join(L)


def summary_q2(q2: dict) -> str:
    L = [f"# Q2 summary: {q2['title']}\n", f"> {q2['question']}\n", f"**Rows analyzed:** {q2['n_rows']}. "
         f"{q2['caveat']}\n", "## Training experience\n"]
    L.append(_table([{"Item": e["label"], "Valid n": e["coverage"]["valid"],
                      "Missing": e["coverage"]["missing"], "Off-scale/unusable": e["coverage"]["unusable"],
                      "Agree": _ci(e.get("agree")), "Strongly agree": _ci(e.get("strongly_agree")),
                      "Mean (1-5)": e.get("mean")} for e in q2["experience"] if e["coverage"]["collected"]]))
    r = q2["recommendation"]
    if r.get("summary"):
        L.append("## Likelihood to recommend (0-10)\n")
        L.append(f"n = {r['summary']['n']}; mean {r['summary']['mean']}, median {r['summary']['median']}. "
                 f"Promoters (9-10) {_ci(r['promoters_9_10'])}, passives (7-8) {_ci(r['passives_7_8'])}, "
                 f"detractors (0-6) {_ci(r['detractors_0_6'])}; NPS {r['nps']}.\n")
    c = q2["call_988"]
    L.append("## Likelihood to call 988 (0-10)\n")
    L.append(f"**{c['caveat']}**\n")
    if c.get("summary"):
        L.append(f"Answered by {c['coverage']['valid']} of {c['coverage']['rows']} respondents "
                 f"({c['coverage']['valid_pct']}%). Among them: mean {c['summary']['mean']}, median "
                 f"{c['summary']['median']}; 9-10 {_ci(c['very_likely_9_10'])}.\n")
        if c.get("coverage_by_survey_version"):
            L.append(_table(c["coverage_by_survey_version"]))
    L.append("## Relationships (Spearman, exploratory)\n")
    L.append(_table([{"Family": x["family"], "X": x["x"], "Y": x["y"], "n": x["n"], "rho": x["rho"],
                      "p": x["p_value"], "Strength": x["strength"]} for x in q2["relationships"]]))
    s = q2["satisfaction_vs_gap"]
    L.append("## Satisfaction vs remaining confidence gaps\n")
    L.append(s["note"] + " Definitions: " + "; ".join(f"{k} = {v}" for k, v in s["definitions"].items()) + "\n")
    L.append(_table([{"Objective": x["objective"], "Group": x["group"], "Group n": x["group_n"],
                      "Not agreeing after (group)": _ci(x.get("group_not_agree_after")),
                      "Not agreeing after (others)": _ci(x.get("others_not_agree_after"))}
                     for x in s["rows"]]))
    L.append("## Written feedback themes (counts only)\n")
    for f, fb in q2["feedback"].items():
        if not fb.get("available"):
            continue
        L.append(f"### {f}\n\n{fb['comments']} comments ({fb['comment_rate_pct']}% of rows). Theme version "
                 f"{fb['theme_version']}. {fb['method']}\n")
        L.append(_table([{"Type": t["type"], "n": t["n"], "% of comments": t["pct_of_comments"]}
                         for t in fb["response_types"]]))
        L.append(_table([{"Theme": t["label"], "n": t["n"], "% of comments": t["pct_of_comments"]}
                         for t in fb["themes"] if t["n"]]))
    return "\n".join(L)


def summary_q3(q3: dict) -> str:
    L = [f"# Q3 summary: {q3['title']}\n", f"> {q3['question']}\n", f"**Rows analyzed:** {q3['n_rows']}.\n",
         "## Minimum-group-size policy\n", q3["policy"]["description"] + "\n", "## Guardrails\n"]
    L += [f"- {g}" for g in q3["guardrails"]]
    L.append("")
    for dim, g in q3["groupings"].items():
        if not g.get("available"):
            continue
        L.append(f"## By {dim.replace('_', ' ')}\n")
        if g["overlapping"]:
            L.append(f"Overlapping groups: {g['membership_total']} memberships from {g['rows_total']} respondents.\n")
        rows = []
        for r in g["groups"]:
            o = r.get("objectives", {})
            row = {"Group": r["label"], "n": r["n_display"], "Sessions": r.get("n_sessions"),
                   "Comparable": r["comparable"]}
            for k, v in o.items():
                row[f"{k} before %"] = (v.get("pre_agree") or {}).get("pct")
                row[f"{k} after %"] = (v.get("post_agree") or {}).get("pct")
            row["Experience mean"] = (r.get("experience") or {}).get("mean")
            row["Recommend mean"] = (r.get("recommend") or {}).get("mean")
            row["call_988 n"] = (r.get("call_988") or {}).get("valid_n_display")
            rows.append(row)
        L.append(_table(rows))
        sig = g["comparison"]["signals"]
        L.append(f"Signals ({g['comparison']['rule']}):\n")
        L.append(_table([{k: s[k] for k in ("objective", "label", "metric", "group_value", "group_n", "rest_value",
                                            "rest_n", "difference_pp")} for s in sig]))
        if g["comparison"]["tests"]:
            L.append(_table([{k: t[k] for k in ("objective", "test", "h", "p_value")}
                             for t in g["comparison"]["tests"]]))
    c = q3["composition_check"]
    if c.get("available"):
        L.append("## Does audience mix explain county differences?\n")
        L.append(c["method"] + "\n")
        L.append(_table(c["county_role_mix"]))
        L.append(_table([{k: r.get(k) for k in ("objective", "county", "paired_n", "crude_pre_agree_pct",
                                                "standardized_pre_agree_pct", "crude_post_agree_pct",
                                                "standardized_post_agree_pct", "composition_may_matter")}
                         for r in c["standardized"]]))
        if c["not_standardizable"]:
            L.append(f"Not standardizable (too few respondents in a role stratum): "
                     f"{', '.join(c['not_standardizable'])}.\n")
    return "\n".join(L)


def summary_recommendations(recs: dict) -> str:
    L = ["# Recommendations\n", recs["note"] + "\n", "Categories: " +
         "; ".join(f"**{k}** = {v}" for k, v in recs["categories"].items()) + "\n"]
    for r in recs["recommendations"]:
        L.append(f"## {r['id']} [{r['priority']}] {r['category']} ({r['rule']})\n")
        L.append(f"- **Finding:** {r['finding']}")
        L.append(f"- **Evidence:** `{json.dumps(r['evidence'], default=str)[:600]}`")
        L.append(f"- **Action:** {r['action']}")
        L.append(f"- **Implementation:** {r['implementation']}")
        L.append(f"- **Success metric:** {r['success_metric']}")
        if [c for c in r["caveats"] if c]:
            L.append(f"- **Caveats:** {' '.join(c for c in r['caveats'] if c)}")
        L.append("")
    return "\n".join(L)


def render_summary(results: dict, section: str) -> str:
    return {"q1": lambda: summary_q1(results["q1"]), "q2": lambda: summary_q2(results["q2"]),
            "q3": lambda: summary_q3(results["q3"]),
            "recommendations": lambda: summary_recommendations(results["recommendations"])}[section]()


def main() -> None:
    from src.clean_data import DEFAULT_INPUT, clean_survey, load_survey_file

    ap = argparse.ArgumentParser(description="Run the EveryMind analysis engine")
    ap.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    ap.add_argument("--outdir", type=Path, default=PROJECT_ROOT / "outputs")
    args = ap.parse_args()
    raw, _, _ = load_survey_file(args.input)
    cleaned, report = clean_survey(raw)
    results = run_all(cleaned, report)
    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir / "analysis_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    header = f"_Generated by `src/run_analysis.py` from `{args.input.name}` (analysis v{ANALYSIS_VERSION})._\n\n"
    for sec, name in (("q1", "q1_summary.md"), ("q2", "q2_summary.md"), ("q3", "q3_summary.md"),
                      ("recommendations", "recommendations.md")):
        (args.outdir / name).write_text(header + render_summary(results, sec), encoding="utf-8")
    print(f"rows={len(cleaned)} recommendations={len(results['recommendations']['recommendations'])}")
    print(f"wrote analysis_results.json, q1/q2/q3 summaries, recommendations.md to {args.outdir}")


if __name__ == "__main__":
    main()
