"""Canonical schema for EveryMind 988 training surveys and header-to-field mapping.

This module is the single source of truth for canonical field names, types,
answer vocabularies and column aliases. `explore_data.py`, `clean_data.py` and
`data_validation.py` all import from here.

Matching order for each uploaded column (first rule giving exactly one field wins):
    1. user override        (config: column_overrides)
    2. exact canonical name (e.g. "post_describe_988" or "post describe 988")
    3. documented alias     (DEFAULT_ALIASES + config: column_aliases)
    4. keyword rules        (all keyword groups present, no exclusion keyword)
    5. fuzzy similarity     -> suggestion only, never applied automatically

If two columns claim the same field, neither is applied (unless exactly one of
them is a user override). Unapplied columns are kept separately, never dropped
silently.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from typing import Iterable, Mapping

# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

INVISIBLE = dict.fromkeys(map(ord, "‎‏​‌‍⁠﻿"), None)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)  # NaN / NaT
    except (TypeError, ValueError):
        return False


def clean_label(value: object) -> str | None:
    """Strip invisible marks, NBSP and surplus whitespace. Keeps case. Empty -> None."""
    if _is_missing(value):
        return None
    s = unicodedata.normalize("NFKC", str(value)).translate(INVISIBLE).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_text(value: object) -> str:
    """clean_label + lowercase; missing -> ''."""
    return (clean_label(value) or "").lower()


def strip_accents(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def norm_header(h: object) -> str:
    return strip_accents(norm_text(h)).replace("’", "'")


def has_hidden_chars(value: object) -> bool:
    s = str(value)
    return any(ch in s for ch in "\xa0‎‏​﻿") or s != s.strip() or "  " in s


# ---------------------------------------------------------------------------
# Answer vocabularies
# ---------------------------------------------------------------------------

AGREE_5 = ["Strongly disagree", "Disagree", "Neither agree nor disagree", "Agree", "Strongly agree"]
LIKERT_LABELS = {i + 1: label for i, label in enumerate(AGREE_5)}
# Lowercased, whitespace-normalized label -> score. Only documented equivalents.
LIKERT_SCORES = {label.lower(): i + 1 for i, label in enumerate(AGREE_5)}
LIKERT_SYNONYMS = {"neither agree or disagree": "Neither agree nor disagree"}
LIKERT_SCORES.update({k: LIKERT_SCORES[v.lower()] for k, v in LIKERT_SYNONYMS.items()})

# Lowercased role token -> role code. Extend via CleaningConfig.role_aliases.
ROLE_CODES = {
    "student": "student",
    "school staff member": "school_staff",
    "caregiver/parent": "caregiver_parent",
    "parent/caregiver": "caregiver_parent",
    "other (e.g., community member)": "other_community",
}
ROLE_ORDER = ["student", "school_staff", "caregiver_parent", "other_community"]

# ---------------------------------------------------------------------------
# Canonical schema
# ---------------------------------------------------------------------------

PRE = ["before", "think back", "antes"]


@dataclass(frozen=True)
class CanonicalField:
    name: str
    category: str
    dtype: str
    required: bool  # "core": absence disables core analyses (see data_validation.ANALYSES)
    description: str
    groups: tuple = ()
    exclude: tuple = ()
    business_questions: str = ""


def _f(name, category, dtype, required, description, groups, exclude=(), bq=""):
    return CanonicalField(name, category, dtype, required, description,
                          tuple(tuple(g) for g in groups), tuple(exclude), bq)


SCHEMA: list[CanonicalField] = [
    _f("response_id", "metadata", "string", True,
       "Unique survey response identifier assigned by the form platform.",
       [["id", "response id", "respondent id"]], exclude=["session", "training", "county"],
       bq="Record integrity"),
    _f("start_time", "metadata", "datetime", False,
       "Timestamp the respondent opened the survey.",
       [["start time", "started", "start date", "hora de inicio"]],
       bq="SQ1 (time trends), data quality (completion duration)"),
    _f("completion_time", "metadata", "datetime", True,
       "Timestamp the respondent submitted the survey.",
       [["completion time", "completed", "submitted", "end time", "hora de finalizacion"]],
       bq="SQ1 (time trends), fallback when training date is missing"),
    _f("language", "demographics", "category", False,
       "Language/locale the survey form was completed in.",
       [["language", "idioma"]], bq="SQ3 (accessibility, language equity)"),
    _f("participant_role", "demographics", "multi-select", True,
       "Self-reported participant role(s); semicolon-delimited multi-select.",
       [["are you a", "role", "participant type", "usted es"]],
       bq="SQ3 (differences across populations)"),
    _f("training_date", "metadata", "date", True,
       "Date of the training session as entered by the respondent.",
       [["date of your training", "training date", "date of training", "fecha"]],
       bq="SQ1 (monitor over time), session grouping"),
    _f("county", "demographics", "category", True,
       "County the training is affiliated with.",
       [["county", "condado", "jurisdiction"]], bq="SQ3 (gaps across communities)"),
    _f("post_identify_mh", "training_outcomes", "likert_agree_5", True,
       "Current agreement: 'I can identify mental health challenges and crises.'",
       [["identify", "identificar"], ["mental health", "salud mental"]], exclude=PRE,
       bq="SQ1, SQ2 (post-training confidence; paired with pre)"),
    _f("post_describe_988", "training_outcomes", "likert_agree_5", True,
       "Current agreement: 'I can describe 988 and how it works.'",
       [["describe", "describir"], ["988"]], exclude=PRE,
       bq="SQ1, SQ2 (post-training confidence; paired with pre)"),
    _f("post_know_when_988", "training_outcomes", "likert_agree_5", True,
       "Current agreement: 'I know when to use 988.'",
       [["know when", "when to use", "cuando usar"], ["988"]], exclude=PRE + ["knew when"],
       bq="SQ1, SQ2 (post-training confidence; paired with pre)"),
    _f("pre_identify_mh", "training_outcomes", "likert_agree_5", True,
       "Recalled pre-training agreement: 'I could identify mental health challenges and crises.'",
       [PRE, ["identify", "identificar"]], bq="SQ1, SQ2 (retrospective pre/post change)"),
    _f("pre_describe_988", "training_outcomes", "likert_agree_5", True,
       "Recalled pre-training agreement: 'I could describe 988 and how it works.'",
       [PRE, ["describe", "describir"]], bq="SQ1, SQ2 (retrospective pre/post change)"),
    _f("pre_know_when_988", "training_outcomes", "likert_agree_5", True,
       "Recalled pre-training agreement: 'I knew when to use 988.'",
       [PRE, ["knew when", "know when", "when to use", "cuando usar"]],
       bq="SQ1, SQ2 (retrospective pre/post change)"),
    _f("sat_presenter_engaged", "training_experience", "likert_agree_5", False,
       "Agreement: 'The presenter(s) engaged the participants.'",
       [["engaged", "involucr"]], bq="SQ1, SQ2 (delivery quality)"),
    _f("sat_presenter_knowledge", "training_experience", "likert_agree_5", False,
       "Agreement: 'The presenter(s) knew the subject matter well.'",
       [["subject matter", "knew the subject", "conocia"]], bq="SQ1, SQ2 (delivery quality)"),
    _f("sat_well_organized", "training_experience", "likert_agree_5", False,
       "Agreement: 'The training was well organized.'",
       [["organized", "organizad"]], bq="SQ1, SQ2 (delivery quality)"),
    _f("likelihood_recommend", "training_experience", "int_0_10", False,
       "0-10 likelihood to recommend the training (NPS-style).",
       [["recommend", "recomendar"]], bq="SQ1, SQ2 (overall satisfaction / NPS)"),
    _f("likelihood_call_988", "behavioral_intent", "int_0_10", False,
       "0-10 likelihood to call 988 for self or others in a crisis.",
       [["likely", "probable"], ["call 988", "llamar al 988", "call"]], exclude=["recommend"],
       bq="SQ1, SQ2 (behavioral intent outcome)"),
    _f("fb_most_valuable", "written_feedback", "free_text", False,
       "Open text: most valuable thing learned.",
       [["most valuable", "mas valioso", "learned"]], bq="SQ2 (qualitative themes)"),
    _f("fb_improvements", "written_feedback", "free_text", False,
       "Open text: how to improve the training.",
       [["improve", "mejorar", "suggestion"]], bq="SQ1, SQ3 (improvement opportunities)"),
]

SCHEMA_BY_NAME = {f.name: f for f in SCHEMA}
FIELD_ORDER = [f.name for f in SCHEMA]
PRE_POST_PAIRS = [
    ("pre_identify_mh", "post_identify_mh"),
    ("pre_describe_988", "post_describe_988"),
    ("pre_know_when_988", "post_know_when_988"),
]
PAIR_NAMES = {post: post.removeprefix("post_") for _, post in PRE_POST_PAIRS}
POST_FIELDS = [post for _, post in PRE_POST_PAIRS]
PRE_FIELDS = [pre for pre, _ in PRE_POST_PAIRS]
SAT_FIELDS = ["sat_presenter_engaged", "sat_presenter_knowledge", "sat_well_organized"]
LIKERT_FIELDS = [f.name for f in SCHEMA if f.dtype == "likert_agree_5"]
RATING_FIELDS = [f.name for f in SCHEMA if f.dtype == "int_0_10"]
TEXT_FIELDS = [f.name for f in SCHEMA if f.dtype == "free_text"]

# Documented header aliases (compared after norm_header). Includes the exact
# 2025-26 Microsoft Forms headers so that export keeps mapping even if keyword
# rules change.
DEFAULT_ALIASES: dict[str, list[str]] = {
    "response_id": ["id", "response id", "respondent id", "submission id", "record id"],
    "start_time": ["start time", "start date", "started", "started at", "hora de inicio"],
    "completion_time": ["completion time", "completed", "completed at", "submitted", "submission time",
                        "end time", "end date", "timestamp", "hora de finalizacion"],
    "language": ["language", "survey language", "idioma"],
    "participant_role": ["are you a....?", "are you a...?", "are you a?", "role", "participant role",
                         "participant type", "i am a", "usted es"],
    "training_date": ["please enter the date of your training.", "training date", "date of training",
                      "session date", "fecha de la capacitacion"],
    "county": ["which county is this training affiliated with?", "county", "training county",
               "jurisdiction", "condado"],
    "post_identify_mh": ["please tell us how much you disagree or agree with the statements..i can identify "
                         "mental health challenges and crises."],
    "post_describe_988": ["please tell us how much you disagree or agree with the statements..i can describe "
                          "988 and how it works."],
    "post_know_when_988": ["please tell us how much you disagree or agree with the statements..i know when "
                           "to use 988."],
    "pre_identify_mh": ["now think back to before you took part in the understanding 988 training. .i could "
                        "identify mental health challenges and crises."],
    "pre_describe_988": ["now think back to before you took part in the understanding 988 training. .i could "
                         "describe 988 and how it works."],
    "pre_know_when_988": ["now think back to before you took part in the understanding 988 training. .i knew "
                          "when to use 988."],
    "sat_presenter_engaged": ["how much do you agree or disagree with the following aspects of the training?."
                              "the presenter(s) engaged the participants."],
    "sat_presenter_knowledge": ["how much do you agree or disagree with the following aspects of the training?."
                                "the presenter(s) knew the subject matter well."],
    "sat_well_organized": ["how much do you agree or disagree with the following aspects of the training?."
                           "the training was well organized."],
    "likelihood_recommend": ["how likely are you to recommend the understanding 988 training to a friend or "
                             "colleague?", "likelihood to recommend", "nps", "recommend score"],
    "likelihood_call_988": ["how likely are you to call 988 if you or someone else is experiencing a mental "
                            "health challenge or crisis?", "likelihood to call 988"],
    "fb_most_valuable": ["what was the most valuable thing you learned during the training?"],
    "fb_improvements": ["how can we improve this training in the future?"],
}

# Unmatched columns whose header looks like personal data are dropped at ingestion.
PII_HEADER_PATTERN = re.compile(
    r"\b(e-?mail|name|first name|last name|full name|phone|telephone|mobile|cell|address|ip address|"
    r"correo|nombre|telefono|direccion)\b"
)

FUZZY_THRESHOLD = 0.6

# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def _keyword_hit(header: str, kw: str) -> bool:
    kw = strip_accents(kw)
    if len(kw) <= 3:  # short tokens like "id" must be whole words
        return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", header) is not None
    return kw in header


def rule_match(header: str, f: CanonicalField) -> bool:
    if not f.groups or any(_keyword_hit(header, kw) for kw in f.exclude):
        return False
    return all(any(_keyword_hit(header, kw) for kw in grp) for grp in f.groups)


@dataclass
class ColumnMatch:
    """Outcome for one uploaded column.

    status: matched | ambiguous | suggestion | unmatched | ignored | rejected | pii_dropped
    Only `matched` columns are used as canonical fields.
    """
    position: int
    source: str
    field: str | None = None
    method: str = "none"  # override | exact | alias | keyword | fuzzy | none
    status: str = "unmatched"
    candidates: list[str] = dc_field(default_factory=list)
    score: float | None = None
    note: str = ""


@dataclass
class SchemaMapping:
    matches: list[ColumnMatch]

    def field_positions(self) -> dict[str, int]:
        return {m.field: m.position for m in self.matches if m.status == "matched"}

    def with_status(self, *statuses: str) -> list[ColumnMatch]:
        return [m for m in self.matches if m.status in statuses]

    def present_fields(self) -> list[str]:
        pos = self.field_positions()
        return [f for f in FIELD_ORDER if f in pos]

    def missing_fields(self, required: bool | None = None) -> list[str]:
        pos = self.field_positions()
        return [f.name for f in SCHEMA if f.name not in pos and (required is None or f.required == required)]

    def reject(self, position: int, note: str) -> None:
        for m in self.matches:
            if m.position == position:
                m.candidates = [m.field] if m.field else m.candidates
                m.field, m.status, m.note = None, "rejected", note

    def to_records(self) -> list[dict]:
        return [asdict(m) for m in self.matches]


def _alias_index(extra: Mapping[str, Iterable[str]] | None) -> dict[str, set[str]]:
    idx: dict[str, set[str]] = {}
    for source in (DEFAULT_ALIASES, extra or {}):
        for fname, names in source.items():
            if fname not in SCHEMA_BY_NAME:
                raise ValueError(f"Alias given for unknown canonical field: {fname!r}")
            for n in names:
                idx.setdefault(norm_header(n), set()).add(fname)
    return idx


def _fuzzy_suggestion(h: str, alias_idx: dict[str, set[str]]) -> tuple[str | None, float]:
    best, best_score = None, 0.0
    targets = [(a, fs) for a, fs in alias_idx.items()]
    targets += [(f.name.replace("_", " "), {f.name}) for f in SCHEMA]
    targets += [(norm_header(f.description), {f.name}) for f in SCHEMA]
    for text, fields in targets:
        if len(fields) != 1:
            continue
        score = difflib.SequenceMatcher(None, h, text).ratio()
        if score > best_score:
            best, best_score = next(iter(fields)), score
    return (best, best_score) if best_score >= FUZZY_THRESHOLD else (None, best_score)


def map_columns(columns: Iterable[object],
                aliases: Mapping[str, Iterable[str]] | None = None,
                overrides: Mapping[str, str | None] | None = None) -> SchemaMapping:
    """Map uploaded headers (in any order) to canonical fields.

    overrides: {uploaded header: canonical field name, or None to ignore the column}.
    Header comparison is case/whitespace/accent-insensitive.
    """
    alias_idx = _alias_index(aliases)
    ov = {norm_header(k): v for k, v in (overrides or {}).items()}
    for target in ov.values():
        if target is not None and target not in SCHEMA_BY_NAME:
            raise ValueError(f"Override targets unknown canonical field: {target!r}")

    matches: list[ColumnMatch] = []
    for pos, col in enumerate(columns):
        h = norm_header(col)
        m = ColumnMatch(position=pos, source=str(col))
        if h in ov:
            m.method = "override"
            if ov[h] is None:
                m.status, m.note = "ignored", "excluded by user override"
            else:
                m.field, m.status, m.candidates = ov[h], "matched", [ov[h]]
        else:
            exact = [f.name for f in SCHEMA if h in (f.name, f.name.replace("_", " "))]
            if exact:
                cands, method = exact, "exact"
            elif h in alias_idx:
                cands, method = sorted(alias_idx[h]), "alias"
            else:
                cands, method = [f.name for f in SCHEMA if rule_match(h, f)], "keyword"
            if len(cands) == 1:
                m.field, m.method, m.status, m.candidates = cands[0], method, "matched", cands
            elif len(cands) > 1:
                m.method, m.status, m.candidates = method, "ambiguous", cands
                m.note = "header matches several canonical fields"
            else:
                best, score = _fuzzy_suggestion(h, alias_idx)
                m.score = round(score, 2)
                if best:
                    m.method, m.status, m.candidates = "fuzzy", "suggestion", [best]
                    m.note = "similar to a known field; confirm before use"
                elif PII_HEADER_PATTERN.search(h):
                    m.status, m.note = "pii_dropped", "header looks like personal data; column not loaded"
        matches.append(m)

    claims: dict[str, list[ColumnMatch]] = {}
    for m in matches:
        if m.status == "matched":
            claims.setdefault(m.field, []).append(m)
    for fname, ms in claims.items():
        if len(ms) < 2:
            continue
        overrides_ = [m for m in ms if m.method == "override"]
        keep = overrides_[0] if len(overrides_) == 1 else None
        for m in ms:
            if m is keep:
                continue
            m.field, m.status, m.candidates = None, "ambiguous", [fname]
            m.note = ("field already assigned by user override" if keep
                      else "several columns match this field; confirm which one to use")
    return SchemaMapping(matches)


def match_columns(columns: Iterable[object]) -> dict[str, dict]:
    """Compact {header: {field, method, confidence, candidates}} view (used by explore_data)."""
    out = {}
    for m in map_columns(columns).matches:
        if m.status == "matched":
            confidence = "high"
        elif m.status == "suggestion":
            confidence = f"suggest {m.candidates[0]} ({m.score:.2f})"
        else:
            confidence = m.status
        out[m.source] = {"field": m.field, "method": m.method, "confidence": confidence,
                         "candidates": m.candidates}
    return out
