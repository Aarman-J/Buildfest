"""Transparent, rule-based theme classification for written feedback.

Design
------
* Themes are fixed keyword/regex dictionaries (THEMES below), derived from the
  most frequent words and phrases in the 2025-26 comments (English and Spanish).
  No theme is generated automatically and no comment text is ever quoted
  in outputs.
* A comment can match several themes. Comments matching none are labelled
  `other_unclassified` (if substantive) so they stay visible.
* Each comment also gets one response type:
    unclear                -> non-answers ("idk", "n/a", "."), no letters, or very
                              short text with no theme
    improvement_suggestion -> a suggestion theme or suggestion cue ("should", "add",
                              "more ...") is present
    praise                 -> praise words and no suggestion
    neutral                -> everything else (e.g. "nothing", questions, factual
                              statements of what was learned)
* Classification is deterministic and versioned (THEME_VERSION). Human
  validation: `validation_sample` draws a reproducible random sample (PII
  redacted) for reviewers to label; `score_validation` measures agreement.
  Keyword lists should be revised when precision for a theme falls below 0.8.

Only aggregate counts leave this module through `theme_summary`.
"""

from __future__ import annotations

import re

import pandas as pd

if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data_validation import redact_pii
from src.schema_mapper import clean_label, strip_accents

THEME_VERSION = "2026.11-2"

PRAISE = r"\b(great|good|amazing|awesome|excellent|perfect|loved?|enjoyed|helpful|informative|fun|nice|" \
         r"interesting|fantastic|wonderful|good job|well done|great job|thank(s| you)?|bien|excelente|" \
         r"muy bueno|perfecto|me gusto)\b"
SUGGESTION_CUES = r"\b(should|could|would be (better|nice|good)|maybe|add|include|need(s)? (more|to)|more|less|" \
                  r"shorter|longer|wish|instead|improve by|deberian|podrian|mas)\b"
# Non-answers. "nothing"/"none"/"no" are *answers* (handled by themes), not unclear.
NON_ANSWER = re.compile(r"^(idk|i don'?t know|n/?a|na|no se|ok|okay|not sure|no comments?|.)$")
NOTHING = r"^(nothing|none|no|nope|nada|not really|nothing really|no thanks?|no improvements?)\b"
# Questions are often typed without "?"; a leading question word counts.
QUESTION_PAT = r"\?|^(how|what|why|who|when|where|do|does|did|can|is|are|will|would|if|could|should i)\b"

# field -> {theme: (label, regex)}. Patterns run on lowercased, accent-stripped text.
# Keyword lists come from frequent words/phrases in the 2025-26 comments (see docs/analysis_methods.md).
THEMES: dict[str, dict[str, tuple[str, str]]] = {
    "fb_most_valuable": {
        "who_can_call": ("Anyone can call, for many reasons (not only suicide/crisis)",
                         r"\b(anyone|anybody|everyone|any ?one|any age|any reason|anything|not (just|only) for|"
                         r"don'?t have to be|doesn'?t have to be|for (any|all)|cualquier(a)?|todos|even (if|minors|"
                         r"when|for|though)|minors|bad day|hard day|just (stressed|sad)|not (a|in) crisis|"
                         r"(different|many|all) (reasons|situations|kinds|ways)|situations?|reasons? to call|"
                         r"drugs?|sexual orientation|lgbt\w*)\b"),
        "when_to_use": ("When to use 988", r"\b(when to|when (you|someone|a person)|in a crisis|cuando|why to)\b"),
        "how_988_works": ("What 988 is and how it works",
                          r"\b(how (it|988) works|what 988|988 is|988 does|how to (call|use|reach|contact)|"
                          r"texting|text|chat|what happens|como funciona|line|number|the way 988|logistics|"
                          r"what it is|languages?|llamada|mensaje)\b"),
        "availability_privacy": ("Available 24/7, free, confidential, trustworthy",
                                 r"\b(24 ?/? ?7|24 hours|anytime|any time|always|whenever|available|open|free|"
                                 r"confidential\w*|anonymous|privacy|private|trust ?worthy|trust|todo el tiempo|"
                                 r"siempre|does not need your|no information)\b"),
        "difference_from_911": ("Difference from 911 / police", r"\b(911|police|difference between|instead of)\b"),
        "recognizing_signs": ("Recognizing mental health signs",
                              r"\b(signs?|warning|recogni[sz]e|identify|symptoms?|mental health|mental crisis|"
                              r"salud mental)\b"),
        "suicide_prevention": ("Suicide and suicide prevention",
                               r"\b(suicid\w*|kill (yourself|themsel\w*)|save lives)\b"),
        "help_seeking": ("Help-seeking, listening and support",
                         r"\b(help|talk|reach out|support|not alone|alone|okay to|ok to|ask for|someone to|listen\w*|"
                         r"empath\w*|ayud\w*|apud\w*|hablar|apoyo|escuch\w*|contar con|comunicar\w*)\b"),
        "other_resources": ("Other resources (warmline, counselors, trusted adults)",
                            r"\b(warm ?line|resources?|counsel(or|ors|ing)|trusted adult|therap\w*|recursos?)\b"),
        "facts_statistics": ("Facts and statistics",
                             r"(\d+ ?%|percent|statistic\w*|every \d+|\d{2,}[,.]?\d* (people|students|died|deaths)|"
                             r"died|deaths?|estadistica)"),
        "awareness_of_988": ("Learning that 988 exists / to call 988",
                             r"(^(to )?call 988$|\b(learned about 988|who to call|existence|exists|never heard|existe|"
                             r"there is a (number|line|hotline)|useful|importan\w*)\b)"),
        "everything": ("Everything / all of it", r"\b(everything|all of it|todo)\b"),
        "nothing_noted": ("Nothing / none noted", NOTHING),
        "general_praise": ("General praise", PRAISE),
    },
    "fb_improvements": {
        "no_change_needed": ("No change needed / no questions",
                             NOTHING + r"|\b(nothing|none|no (changes?|improvements?|questions)|it'?s (good|fine|"
                             r"great|perfect)|keep (it|doing)|nada|nothing to improve|not much to improve|"
                             r"don'?t (need|have) (to|any))\b"),
        "more_interactive": ("More interactive / discussion",
                             r"\b(interactive|activit(y|ies)|games?|more engag\w*|engage more|hands[- ]on|kahoot|"
                             r"more (class )?participat\w*|participating more|boring|fun(ner)?|dinamic\w*|"
                             r"discussions?|preguntar)\b"),
        "examples_scenarios": ("More examples, scenarios or real stories",
                               r"\b(examples?|scenarios?|role ?play\w*|real[- ]life|stor(y|ies)|demonstrat\w*|"
                               r"experiences?|ejemplos?|received services|testimon\w*|guest speakers?)\b"),
        "media_visuals": ("Videos, visuals, slides, handouts",
                          r"\b(videos?|visuals?|slides?|power ?point|ppt|pictures?|images?|ilustracion\w*|"
                          r"handouts?|guide|brief note|note)\b"),
        "length_pacing": ("Length and pacing",
                          r"\b(shorter|longer|too long|too short|more time|less time|faster|slower|pace|rushed?|"
                          r"time)\b"),
        "more_depth": ("More information / clearer explanation",
                       r"\b(more (about|info\w*|details?|depth|on)|learn more|in depth|deeper|explain\w* (more|"
                       r"better|it better)|how (it|988) works|mas informacion|about (the|988))\b"),
        "format_setting": ("Format or setting (online, outdoors, recording)",
                           r"\b(online|virtual|in person|outside|outdoors?|recording|recorded)\b"),
        "questions_about_988": ("Questions or curiosity about 988",
                                QUESTION_PAT + r"|\b(how many|what happens|who (answers|picks up|is on)|how often|"
                                r"do they|does it|is it|are they|what if|why do|cuantas|que pasa)\b"),
        "careers_in_988": ("Interest in 988 / crisis-counselor careers",
                           r"\b(jobs?|hired|hiring|careers?|work (there|for|at) 988|volunteer\w*|how are counselors "
                           r"trained|education do you need)\b"),
        "reach_more_people": ("Reach more people / spread awareness",
                              r"\b(more (people|schools|students)|everyone should|spread|advertis\w*|awareness|"
                              r"promot\w*|share (it|this)|compart\w*|otros|escuelas|eventos?|other (students|"
                              r"people|schools))\b"),
        "incentives_food": ("Food or incentives", r"\b(food|snacks?|candy|prizes?|pizza|comida)\b"),
        "general_praise": ("General praise", PRAISE),
        "delivery_audio": ("Presenter delivery / audio",
                           r"\b(louder|speak|mic(rophone)?|audio|sound|hear|presenter|speaker|voice)\b"),
    },
}
SUGGESTION_THEMES = {"more_interactive", "examples_scenarios", "media_visuals", "length_pacing", "more_depth",
                     "reach_more_people", "incentives_food", "delivery_audio", "format_setting"}
_COMPILED = {f: {t: re.compile(p) for t, (_, p) in d.items()} for f, d in THEMES.items()}


def _prep(text: object) -> str | None:
    s = clean_label(text)
    return strip_accents(s.lower()) if s else None


def classify(text: object, field_name: str) -> dict:
    """Return {'themes': [...], 'response_type': str} for one comment."""
    s = _prep(text)
    if s is None:
        return {"themes": [], "response_type": None}
    stripped = s.strip(" .!?")
    if NON_ANSWER.match(s) or NON_ANSWER.match(stripped) or not re.search(r"[a-z]", s):
        return {"themes": [], "response_type": "unclear"}
    themes = [t for t, pat in _COMPILED.get(field_name, {}).items() if pat.search(s)]
    words = len(re.findall(r"[a-z0-9']+", s))
    if not themes:
        if words <= 2:
            return {"themes": [], "response_type": "unclear"}
        themes = ["other_unclassified"]
    praise = re.search(PRAISE, s) is not None
    suggestion = bool(SUGGESTION_THEMES & set(themes)) or (
        field_name == "fb_improvements" and re.search(SUGGESTION_CUES, s) is not None
        and "no_change_needed" not in themes)
    if field_name == "fb_improvements" and "questions_about_988" in themes and not (
            SUGGESTION_THEMES & set(themes)):
        suggestion = False
    if suggestion:
        rtype = "improvement_suggestion"
    elif praise:
        rtype = "praise"
    else:
        rtype = "neutral"
    return {"themes": themes, "response_type": rtype}


def classify_series(s: pd.Series, field_name: str) -> pd.DataFrame:
    res = [classify(v, field_name) for v in s]
    return pd.DataFrame({"themes": [r["themes"] for r in res], "response_type": [r["response_type"] for r in res]},
                        index=s.index)


def theme_summary(df: pd.DataFrame, field_name: str, min_cell: int = 10) -> dict:
    """Aggregate theme and response-type counts (no text). Denominator = non-empty comments."""
    labels = {**{t: lab for t, (lab, _) in THEMES.get(field_name, {}).items()},
              "other_unclassified": "Other / unclassified"}
    if field_name not in df:
        return {"field": field_name, "available": False}
    s = df[field_name]
    c = classify_series(s[s.notna()], field_name)
    n = len(c)
    themes = pd.Series([t for ts in c["themes"] for t in ts], dtype="object").value_counts()
    types = c["response_type"].value_counts()
    rows = [{"theme": t, "label": labels.get(t, t), "n": int(themes.get(t, 0)),
             "pct_of_comments": round(100 * themes.get(t, 0) / n, 1) if n else None}
            for t in labels]
    rows.sort(key=lambda r: -r["n"])
    by_lang = {}
    if "language" in df:
        lang = df.loc[c.index, "language"]
        unclassified = c["themes"].map(lambda ts: ts == ["other_unclassified"])
        for code, g in unclassified.groupby(lang):
            by_lang[str(code)] = {"comments": int(len(g)), "unclassified": int(g.sum())}
    return {
        "field": field_name, "available": True, "theme_version": THEME_VERSION,
        "rows": int(len(df)), "comments": n, "comment_rate_pct": round(100 * n / len(df), 1) if len(df) else None,
        "themes": rows,
        "response_types": [{"type": t, "n": int(types.get(t, 0)),
                            "pct_of_comments": round(100 * types.get(t, 0) / n, 1) if n else None}
                           for t in ("praise", "improvement_suggestion", "neutral", "unclear")],
        "multi_theme_comments": int((c["themes"].map(len) > 1).sum()),
        "small_count_themes": [r["theme"] for r in rows if 0 < r["n"] < min_cell],
        "unclassified_by_language": by_lang,
        "method": "Keyword rules (feedback_themes.THEMES). Multiple themes per comment allowed. Counts only; "
                  "no comment text is reported. Pending human validation (see validation_sample).",
    }


# ---------------------------------------------------------------------------
# Human validation workflow (protected data: never publish the sample)
# ---------------------------------------------------------------------------

def validation_sample(df: pd.DataFrame, field_name: str, n: int = 50, seed: int = 2026) -> pd.DataFrame:
    """Reproducible random sample for human review. Text is PII-redacted; keep the file private."""
    s = df[field_name].dropna() if field_name in df else pd.Series(dtype="object")
    sample = s.sample(n=min(n, len(s)), random_state=seed) if len(s) else s
    c = classify_series(sample, field_name)
    return pd.DataFrame({
        "response_id": df.loc[sample.index, "response_id"].values if "response_id" in df else sample.index,
        "field": field_name,
        "text_redacted": sample.map(redact_pii).values,
        "machine_themes": c["themes"].map(lambda ts: ";".join(ts)).values,
        "machine_type": c["response_type"].values,
        "human_themes": "", "human_type": "", "reviewer": "", "notes": "",
    })


def score_validation(sample: pd.DataFrame) -> dict:
    """Agreement between machine and human labels on a completed validation sample."""
    done = sample[sample["human_type"].astype(str).str.len() > 0]
    if done.empty:
        return {"reviewed": 0}
    split = lambda x: set(t for t in str(x).split(";") if t)  # noqa: E731
    themes = sorted(set().union(*done["machine_themes"].map(split), *done["human_themes"].map(split)))
    per_theme = []
    for t in themes:
        m = done["machine_themes"].map(lambda x: t in split(x))
        h = done["human_themes"].map(lambda x: t in split(x))
        tp, fp, fn = int((m & h).sum()), int((m & ~h).sum()), int((~m & h).sum())
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        per_theme.append({"theme": t, "tp": tp, "fp": fp, "fn": fn,
                          "precision": round(prec, 3) if prec is not None else None,
                          "recall": round(rec, 3) if rec is not None else None})
    agree = (done["machine_type"] == done["human_type"]).mean()
    # Cohen's kappa for response type
    cats = sorted(set(done["machine_type"]) | set(done["human_type"]))
    pe = sum((done["machine_type"] == k).mean() * (done["human_type"] == k).mean() for k in cats)
    kappa = (agree - pe) / (1 - pe) if pe < 1 else None
    return {"reviewed": int(len(done)), "type_accuracy": round(float(agree), 3),
            "type_kappa": round(float(kappa), 3) if kappa is not None else None, "per_theme": per_theme,
            "revise_themes": [r["theme"] for r in per_theme if r["precision"] is not None and r["precision"] < 0.8]}
