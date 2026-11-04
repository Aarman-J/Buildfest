"""Rule-based, evidence-linked recommendations.

Each rule reads the structured results of `run_q1`, `run_q2`, `run_q3` (and,
optionally, the cleaning quality report) and emits a recommendation only when
its documented threshold is met *in the data supplied*. Nothing is hard-coded
from the 2025-26 dataset.

Every recommendation has:
    finding -> evidence -> action -> implementation -> success_metric
plus `category` (verified finding / exploratory association / data-quality
warning / follow-up investigation), `priority`, the `rule` id and caveats.
No rule makes a causal claim.
"""

from __future__ import annotations

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis_utils import DATA_QUALITY, EXPLORATORY, FOLLOW_UP, OBJECTIVES, VERIFIED

# Thresholds (documented in docs/analysis_methods.md)
GAP_MIN_PCT = 5.0              # % not agreeing after training that warrants content reinforcement
CEILING_PRE_AGREE_PCT = 70.0   # recalled-before agreement above which measurable change is limited
ASSOC_MIN_RHO = 0.3            # |rho| for an exploratory association worth acting on
EXPERIENCE_ITEM_MIN_AGREE = 90.0
SAT_NOT_CONFIDENT_MIN_N = 10
COVERAGE_MIN_PCT = 50.0        # optional item answered by fewer rows than this -> coverage warning
DETRACTOR_MIN_PCT = 25.0
FEEDBACK_THEME_MIN_PCT = 5.0   # suggestion theme share of improvement comments
QUESTIONS_THEME_MIN_PCT = 15.0
RECOMMEND_GAP_POINTS = 1.5     # difference in mean 0-10 rating between comparable role groups
FAST_COMPLETION_MIN_PCT = 10.0
REVERSE_SCALE_MIN_PCT = 1.0
MONTH_DROP_PP = 15.0           # month post-agree below overall by this much -> follow-up

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
LABEL = {o["key"]: o["label"] for o in OBJECTIVES}


def _rec(rule, category, priority, finding, evidence, action, implementation, metric, caveats=()):
    return {"rule": rule, "category": category, "priority": priority, "finding": finding, "evidence": evidence,
            "action": action, "implementation": implementation, "success_metric": metric,
            "caveats": list(caveats)}


def _fmt(s: dict) -> str:
    return f"{s['pct']}% ({s['k']} of {s['n']})"


def rules_q1(q1: dict) -> list[dict]:
    out = []
    objs = [o for o in q1.get("objectives", []) if not o.get("suppressed")]
    if not objs:
        return out
    gaps = sorted(objs, key=lambda o: -o["remaining_gap"]["not_agree_after"]["pct"])
    top = gaps[0]
    g = top["remaining_gap"]["not_agree_after"]
    if g["pct"] >= GAP_MIN_PCT:
        out.append(_rec(
            "Q1_REMAINING_GAP", VERIFIED, "high",
            f"After training, {_fmt(g)} of respondents did not agree they can '{top['label'].lower()}'; "
            f"this is the largest remaining confidence gap of the {len(objs)} objectives.",
            {"objective": top["key"], "not_agree_after": g,
             "neutral_after": top["remaining_gap"]["neutral_after"],
             "all_objectives": {o["key"]: o["remaining_gap"]["not_agree_after"]["pct"] for o in objs}},
            f"Reinforce the '{top['label']}' segment of the training.",
            "Add a short scenario-based practice and a 1-question check for understanding at the end of that "
            "segment; review the segment with presenters.",
            f"Share not agreeing after training for this objective falls below its current {g['pct']}% in the "
            "next reporting period (same survey items, same denominator rules).",
            ["Self-reported confidence, not tested knowledge."]))
    gain = max(objs, key=lambda o: o["agree_pp_change"])
    out.append(_rec(
        "Q1_LARGEST_GAIN", VERIFIED, "low",
        f"The largest self-reported increase in agreement is for '{gain['label'].lower()}': "
        f"{gain['pre_agree']['pct']}% recalled agreeing before vs {gain['post_agree']['pct']}% after "
        f"(+{gain['agree_pp_change']} pp, paired n = {gain['coverage']['paired_n']}).",
        {"objective": gain["key"], "pre_agree": gain["pre_agree"], "post_agree": gain["post_agree"],
         "improved": gain["change"]["improved"]},
        "Keep this content stable and use this item as a core monitoring indicator.",
        "Track this objective's before/after gap on the monthly dashboard view.",
        "Gap stays at or above its current level across months with adequate n.",
        [q1.get("caveat", "")]))
    for o in objs:
        if o["pre_agree"]["pct"] >= CEILING_PRE_AGREE_PCT:
            out.append(_rec(
                "Q1_CEILING", DATA_QUALITY, "medium",
                f"{o['pre_agree']['pct']}% already recalled agreeing before training that they can "
                f"'{o['label'].lower()}', and {o['ceiling']['pre_strongly_agree']['pct']}% chose the top category, "
                "so the measurable change for this objective is limited.",
                {"objective": o["key"], "pre_agree": o["pre_agree"], "pre_strongly_agree": o["ceiling"]["pre_strongly_agree"]},
                "Strengthen how this objective is measured.",
                "Add one or two knowledge/scenario questions (correct/incorrect) or a true pre-survey in a sample of "
                "sessions to complement the retrospective rating.",
                "Knowledge-check items collected in at least one session per county per quarter.",
                ["Retrospective ratings are subject to recall bias."]))
    sens = q1.get("sensitivity") or {}
    if sens and not sens.get("robust", True):
        out.append(_rec(
            "Q1_SENSITIVITY", DATA_QUALITY, "high",
            f"Headline pre/post figures shift by up to {sens['max_shift_pp']} pp when suspicious responses are "
            "excluded.", {"max_shift_pp": sens["max_shift_pp"], "scenarios": sens["robustness_scenarios"]},
            "Report headline results with and without flagged responses.",
            "Show the sensitivity table next to headline figures in the dashboard.",
            "Decision makers see both figures for each headline result."))
    trend = q1.get("trend", {})
    months = [m for m in trend.get("months", []) if not m.get("suppressed")]
    for m in months:  # one recommendation per month, listing every objective that dipped
        dips = []
        for o in objs:
            value = m.get(o["key"] + "_post_agree_pct")
            overall = o["post_agree"]["pct"]
            if value is not None and overall - value >= MONTH_DROP_PP:
                dips.append({"objective": o["key"], "label": o["label"], "month_value": value,
                             "overall_value": overall, "paired_n": m[o["key"] + "_paired_n"]})
        if not dips:
            continue
        out.append(_rec(
            "Q1_MONTH_DIP", FOLLOW_UP, "medium",
            f"In training month {m['month']} (n = {m['n_rows']}, {m.get('n_sessions')} session(s)), post-training "
            "agreement was lower than overall: "
            + "; ".join(f"'{d['label'].lower()}' {d['month_value']}% vs {d['overall_value']}%" for d in dips) + ".",
            {"month": m["month"], "n_rows": m["n_rows"], "n_sessions": m.get("n_sessions"), "dips": dips},
            "Review the sessions delivered that month.",
            "Check session records (presenter, audience, setting, survey version) for that month before "
            "drawing conclusions.",
            "Explanation documented; month-level results return within the overall range.",
            ["Few sessions per month: one session can drive a monthly result."]
            + (["Month has fewer respondents than the comparison minimum."] if m.get("below_min_compare") else [])))
    return out


def rules_q2(q2: dict, cfg_min_compare: int) -> list[dict]:
    out = []
    rels = [r for r in q2.get("relationships", []) if r["family"] == "experience_vs_post_confidence"
            and r["rho"] is not None and r["n"] >= cfg_min_compare]
    if rels:
        best = max(rels, key=lambda r: abs(r["rho"]))
        if abs(best["rho"]) >= ASSOC_MIN_RHO:
            out.append(_rec(
                "Q2_EXPERIENCE_CONFIDENCE", EXPLORATORY, "medium",
                f"Respondents who rated the training experience higher also tended to report higher confidence "
                f"after training (Spearman rho up to {best['rho']}, n = {best['n']}, {best['strength']}).",
                {"associations": [{k: r[k] for k in ("y", "rho", "p_value", "n")} for r in rels]},
                "Monitor presenter engagement and organization ratings alongside confidence outcomes.",
                "Show experience and outcome indicators side by side per session/month; use low experience ratings "
                "as an early prompt for presenter support.",
                "Session-level experience and confidence tracked together each quarter.",
                ["Association only: both are self-reported on the same form and may share response style."]))
    items = [e for e in q2.get("experience", []) if e.get("agree")]
    if len(items) >= 2:
        low = min(items, key=lambda e: e["agree"]["pct"])
        high = max(items, key=lambda e: e["agree"]["pct"])
        if low["agree"]["pct"] < EXPERIENCE_ITEM_MIN_AGREE and high["agree"]["pct"] - low["agree"]["pct"] >= 2:
            out.append(_rec(
                "Q2_LOWEST_EXPERIENCE_ITEM", VERIFIED, "medium",
                f"'{low['label']}' has the lowest agreement of the experience items: {_fmt(low['agree'])}, vs "
                f"{high['agree']['pct']}% for '{high['label']}'.",
                {"item": low["field"], "agree": low["agree"], "strongly_agree": low["strongly_agree"]},
                "Focus presenter development on this aspect of delivery.",
                "Share the item-level results with presenters; add interactive elements where this rating is lowest.",
                f"Agreement on this item reaches at least {EXPERIENCE_ITEM_MIN_AGREE:g}%."))
    svg = q2.get("satisfaction_vs_gap", {}).get("rows", [])
    hits = [r for r in svg if not r.get("suppressed") and r["group"] == "highly_satisfied"
            and r["satisfied_but_not_confident_n"] >= SAT_NOT_CONFIDENT_MIN_N]
    if hits:
        worst = max(hits, key=lambda r: r["group_not_agree_after"]["pct"])
        out.append(_rec(
            "Q2_SATISFIED_NOT_CONFIDENT", EXPLORATORY, "medium",
            f"Among highly satisfied respondents, {_fmt(worst['group_not_agree_after'])} still did not agree "
            f"after training that they can '{worst['label'].lower()}'.",
            {"rows": [{k: r[k] for k in ("objective", "group_n", "satisfied_but_not_confident_n")} for r in hits]},
            "Do not use satisfaction as a proxy for learning.",
            "Keep confidence (and any knowledge-check) items on every form; report them separately from satisfaction.",
            "Every report shows outcome items alongside satisfaction."))
    call = q2.get("call_988", {})
    cov = call.get("coverage", {})
    if cov.get("collected") and cov.get("valid_pct") is not None and cov["valid_pct"] < COVERAGE_MIN_PCT:
        out.append(_rec(
            "Q2_CALL_988_COVERAGE", DATA_QUALITY, "high",
            f"Likelihood to call 988 was answered by only {cov['valid_pct']}% of respondents "
            f"({cov['valid']} of {cov['rows']}), concentrated in some survey versions.",
            {"coverage": cov, "by_version": call.get("coverage_by_survey_version")},
            "Ask the behavioral-intent question on every survey form.",
            "Standardize the survey form so all sessions include this item.",
            "Item answered by at least 90% of respondents in the next period.",
            [call.get("caveat", "")]))
    rec = q2.get("recommendation", {})
    if rec.get("detractors_0_6") and rec["detractors_0_6"]["pct"] >= DETRACTOR_MIN_PCT:
        out.append(_rec(
            "Q2_DETRACTORS", FOLLOW_UP, "medium",
            f"{_fmt(rec['detractors_0_6'])} rated their likelihood to recommend at 0-6.",
            {"detractors": rec["detractors_0_6"], "promoters": rec["promoters_9_10"], "nps": rec.get("nps")},
            "Identify which audiences give low recommendation ratings (see Q3 groups) and why.",
            "Compare recommendation ratings by role and county; review improvement comments from those sessions.",
            "Share of 0-6 ratings decreases in the next period.",
            ["Students may interpret 'recommend to a friend' differently from adults."]))
    fb = q2.get("feedback", {}).get("fb_improvements", {})
    if fb.get("available") and fb.get("comments"):
        themes = {t["theme"]: t for t in fb["themes"]}
        q = themes.get("questions_about_988")
        if q and q["pct_of_comments"] and q["pct_of_comments"] >= QUESTIONS_THEME_MIN_PCT:
            out.append(_rec(
                "Q2_QUESTIONS_IN_FEEDBACK", VERIFIED, "medium",
                f"{q['pct_of_comments']}% of improvement comments ({q['n']} of {fb['comments']}) are questions "
                "about 988 rather than suggestions.",
                {"theme": q, "comments": fb["comments"], "theme_version": fb.get("theme_version")},
                "Give participants a dedicated way to ask questions.",
                "Add a Q&A segment or an FAQ handout covering the most common questions; consider a separate "
                "'What questions do you still have?' item on the form.",
                "Share of improvement comments that are questions decreases; FAQ in use.",
                ["Theme counts come from keyword rules pending human validation."]))
        sugg = [t for t in fb["themes"] if t["theme"] in (
            "more_interactive", "examples_scenarios", "media_visuals", "length_pacing", "more_depth",
            "delivery_audio", "format_setting", "incentives_food", "reach_more_people")
            and t["pct_of_comments"] and t["pct_of_comments"] >= FEEDBACK_THEME_MIN_PCT and t["n"] >= 10]
        if sugg:
            out.append(_rec(
                "Q2_FEEDBACK_SUGGESTIONS", EXPLORATORY, "low",
                "Most frequent improvement suggestions: "
                + "; ".join(f"{t['label']} ({t['n']}, {t['pct_of_comments']}%)" for t in sugg[:3]) + ".",
                {"themes": sugg, "comments": fb["comments"]},
                "Pilot the most-requested changes in a few sessions.",
                "Choose one suggestion theme, pilot it, and compare experience and confidence ratings with "
                "comparable sessions.",
                "Pilot sessions show equal or higher experience ratings and confidence.",
                ["Theme counts come from keyword rules pending human validation."]))
    return out


def rules_q3(q3: dict) -> list[dict]:
    out = []
    comp = q3.get("composition_check", {})
    confounded = set(comp.get("not_standardizable", [])) | {
        r["county"] for r in comp.get("standardized", []) if r.get("composition_may_matter")}
    for dim in ("county", "role_primary", "language"):
        g = q3.get("groupings", {}).get(dim, {})
        sigs = sorted(g.get("comparison", {}).get("signals", []), key=lambda s: -abs(s["difference_pp"]))
        if not sigs:
            continue
        caveats = ["Descriptive differences; sessions, presenters and audiences differ between groups."]
        flagged = sorted({s["group"] for s in sigs if dim == "county" and s["group"] in confounded})
        if flagged:
            caveats.append(f"Audience (role) mix differs for {', '.join(flagged)}; differences may reflect who "
                           "attended rather than the county.")
        if any(s["metric"] == "agree_pp_change" for s in sigs):
            caveats.append("Differences in change are partly driven by different recalled 'before' levels.")

        def describe(s):
            metric = "before-to-after change" if s["metric"] == "agree_pp_change" else "post-training agreement"
            return (f"{s['label']}: {metric} for '{LABEL.get(s['objective'], s['objective']).lower()}' "
                    f"{s['group_value']} vs {s['rest_value']} for others ({s['difference_pp']:+} pp)")
        out.append(_rec(
            "Q3_GROUP_DIFFERENCE", FOLLOW_UP, "medium",
            f"{len(sigs)} difference(s) by {dim.replace('_', ' ')} met the signal rule. Largest: "
            + "; ".join(describe(s) for s in sigs[:3]) + ".",
            {"dimension": dim, "signals": [{k: s[k] for k in ("objective", "group", "metric", "group_value",
                                                              "group_n", "rest_value", "rest_n", "difference_pp",
                                                              "ci_separated")} for s in sigs]},
            "Investigate before acting: check whether each difference is driven by recalled baseline, audience "
            "or specific sessions.",
            "Break the results down by session and role; review delivery notes for these sessions.",
            "Documented explanation; follow-up results by group with adequate n.", caveats))
    small = q3.get("small_group_follow_ups", [])
    if small:
        groups = sorted({f"{s['dimension']}: {s['group']}" for s in small})
        out.append(_rec(
            "Q3_SMALL_GROUPS", FOLLOW_UP, "medium",
            f"{len(groups)} groups have too few responses for comparison ({', '.join(groups)}).",
            {"groups": small, "policy": q3.get("policy")},
            "Increase survey responses from these groups before comparing them.",
            "Plan sessions and survey collection with these communities (e.g. Spanish-language sessions, partner "
            "counties); keep reporting their counts.",
            f"Each group reaches at least {q3.get('policy', {}).get('min_compare')} responses per reporting period.",
            ["Low counts reflect data collection, not access or outcomes."]))
    mix = comp.get("county_role_mix", [])
    overall_student = comp.get("overall_mix_pct", {}).get("student")
    if mix and overall_student is not None:
        skewed = [m for m in mix if m["student_pct"] is not None and abs(m["student_pct"] - overall_student) >= 30]
        if skewed:
            out.append(_rec(
                "Q3_COMPOSITION", DATA_QUALITY, "medium",
                "County audiences differ sharply in role mix ("
                + ", ".join(f"{m['county']}: {m['student_pct']}% students" for m in skewed)
                + f"; overall {overall_student}%).",
                {"county_role_mix": mix, "not_standardizable": comp.get("not_standardizable")},
                "Compare counties within the same audience type.",
                "Show county results split by student vs non-student; avoid ranking counties on crude totals.",
                "County comparisons in reports are role-stratified."))
    roles = q3.get("groupings", {}).get("role_primary", {}).get("groups", [])
    comparable = [r for r in roles if r.get("comparable") and r.get("recommend", {}).get("mean") is not None]
    if len(comparable) >= 2:
        lo = min(comparable, key=lambda r: r["recommend"]["mean"])
        hi = max(comparable, key=lambda r: r["recommend"]["mean"])
        if hi["recommend"]["mean"] - lo["recommend"]["mean"] >= RECOMMEND_GAP_POINTS:
            out.append(_rec(
                "Q3_RECOMMEND_BY_ROLE", EXPLORATORY, "medium",
                f"Mean likelihood to recommend is {lo['recommend']['mean']} among {lo['label']} respondents vs "
                f"{hi['recommend']['mean']} among {hi['label']} respondents (0-10).",
                {"groups": [{"group": r["key"], "n": r["recommend"]["valid_n"], "mean": r["recommend"]["mean"]}
                            for r in comparable]},
                f"Review how the training is tailored for {lo['label'].lower()} audiences.",
                "Gather targeted feedback (e.g. short student focus group) and test an adapted version.",
                f"Gap in mean recommendation between groups narrows below {RECOMMEND_GAP_POINTS} points.",
                ["Groups attend different sessions and settings; descriptive comparison only."]))
    return out


def rules_quality(report: dict | None, n_rows: int) -> list[dict]:
    if not report:
        return []
    out = []
    flags = report.get("flags", {})
    off = flags.get("flag_offscale_label", 0)
    if off:
        out.append(_rec(
            "DQ_OFFSCALE_LABELS", DATA_QUALITY, "medium",
            f"{off} responses used answer labels outside the agreement scale; those answers are excluded.",
            {"unexpected_categories": report.get("unexpected_categories")},
            "Use one standard answer scale on every form and language version.",
            "Audit all form versions (including translations) against the standard 5-point scale.",
            "Zero off-scale labels in new uploads."))
    versions = report.get("survey_versions", {}).get("signatures", [])
    if len(versions) > 1:
        out.append(_rec(
            "DQ_SURVEY_VERSIONS", DATA_QUALITY, "high",
            f"{len(versions)} different question sets were detected across sessions.",
            {"signatures": versions},
            "Use a single standard survey form with a session identifier.",
            "Maintain one master form; add a session code field (or pre-filled link per session) so sessions and "
            "dates do not need to be inferred.",
            "One question set and a session code in all new uploads."))
    td = report.get("flags", {})
    excluded = td.get("flag_training_date_missing", 0) + td.get("flag_training_date_invalid", 0) \
        + td.get("flag_training_date_implausible", 0)
    inconsistent = td.get("flag_training_date_after_completion", 0)
    if excluded or inconsistent:
        out.append(_rec(
            "DQ_TRAINING_DATES", DATA_QUALITY, "low",
            f"{excluded} responses have a missing, invalid or implausible training date and are excluded from "
            f"trends by training month; {inconsistent} more have a training date after the survey was submitted "
            "(kept, flagged).",
            {k: td.get(k, 0) for k in ("flag_training_date_missing", "flag_training_date_invalid",
                                       "flag_training_date_after_completion", "flag_training_date_gap_gt_30d")},
            "Prefill or constrain the training date.",
            "Use a session-specific survey link with the date prefilled, or a date picker.",
            "No missing or inconsistent training dates in new uploads."))
    fast = flags.get("flag_fast_completion", 0)
    if n_rows and 100 * fast / n_rows >= FAST_COMPLETION_MIN_PCT:
        out.append(_rec(
            "DQ_FAST_COMPLETION", DATA_QUALITY, "low",
            f"{round(100 * fast / n_rows, 1)}% of surveys were completed in under 30 seconds.",
            {"fast_completions": fast, "rows": n_rows},
            "Give participants protected time to complete the survey at the end of the session.",
            "Allocate 3-5 minutes for the survey before closing the session.",
            "Share of very fast completions decreases.",
            ["Fast completion is a warning, not proof of low-quality answers; these responses are kept."]))
    rev = flags.get("flag_reverse_scale_suspect", 0)
    if n_rows and 100 * rev / n_rows >= REVERSE_SCALE_MIN_PCT:
        out.append(_rec(
            "DQ_REVERSE_SCALE", DATA_QUALITY, "low",
            f"{rev} respondents chose 'Strongly disagree' on all post items yet rated the training 9-10 for "
            "recommending it, suggesting some may have misread the scale direction.",
            {"suspects": rev, "rows": n_rows},
            "Make the scale direction unmistakable on the form.",
            "Label both ends of each scale clearly and keep the same order on every item and form version.",
            "Fewer contradictory response patterns in new uploads.",
            ["Kept in all results; see the sensitivity analysis."]))
    if flags.get("flag_text_pii"):
        out.append(_rec(
            "DQ_TEXT_PII", DATA_QUALITY, "low",
            f"{flags['flag_text_pii']} comments may contain personal information.",
            {"pii_flagged": flags["flag_text_pii"]},
            "Remind participants not to include names or contact details in comments.",
            "Add a one-line privacy note above the comment boxes.",
            "Fewer comments flagged for personal information."))
    return out


def generate_recommendations(q1: dict, q2: dict, q3: dict, quality_report: dict | None = None) -> dict:
    min_compare = q1.get("config", {}).get("min_compare", 30)
    recs = rules_q1(q1) + rules_q2(q2, min_compare) + rules_q3(q3) + rules_quality(quality_report, q1.get("n_rows", 0))
    recs.sort(key=lambda r: (PRIORITY_ORDER[r["priority"]], r["category"], r["rule"]))
    for i, r in enumerate(recs, 1):
        r["id"] = f"R{i:02d}"
    by_cat = {}
    for r in recs:
        by_cat.setdefault(r["category"], []).append(r["id"])
    return {"recommendations": recs, "by_category": by_cat,
            "categories": {VERIFIED: "Descriptive result for this dataset with adequate sample size",
                           EXPLORATORY: "Association or pattern; not causal, needs confirmation",
                           DATA_QUALITY: "Issue with data collection that limits interpretation",
                           FOLLOW_UP: "Question to investigate before acting"},
            "note": "Generated by transparent rules from the uploaded data (src/recommendations.py). "
                    "Thresholds are documented; no causal claims are made."}
