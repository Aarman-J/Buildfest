# Cleaning Plan

**Status:** implemented in Stage 2. See `docs/cleaning_decisions.md` for the final rules, counts and pending approvals. Where the two differ, `cleaning_decisions.md` takes precedence. For example, `Very agree`/`Satisfied` are currently scored NA, an upload is blocked only when no survey content is recognized, and fuzzy matches are suggestions only. The evidence for each item is in `outputs/data_profile.md`; counts refer to the 2025-26 file (1,299 rows).

Principles:
- The raw file in `data/` is never modified.
- No rows are deleted. Problem rows are **flagged**, and each analysis decides how to treat flags.
- Missing values are never imputed.
- Every transformation keeps the raw value alongside the cleaned one, and is logged to `docs/` with the number of rows it affected.

## A. Safe to automate

These changes are reversible and lossless. They fix formatting, not meaning.

| # | Operation | Evidence | Justification |
|---|---|---|---|
| A1 | Normalize headers (NFKC, NBSP to space, strip invisible marks, collapse whitespace) before matching | 6 of 20 headers contain NBSP or trailing spaces | Without it, header matching fails on characters people can't see. |
| A2 | Rename columns to canonical names using high-confidence matches | All 20 columns matched with high confidence | Downstream code becomes independent of header wording. |
| A3 | Trim and normalize whitespace in every categorical value | `Strongly agree\xa0` (566-678 per item); `Montgomery\xa0` (28); trailing spaces in county names | These are formatting artifacts, not different answers. |
| A4 | Strip U+200E from `language`, then map to `en`/`es` | All 1,299 values carry the mark | Added by the form platform; carries no meaning. |
| A5 | Map `Neither agree or disagree` to `Neither agree nor disagree` | 63-98 per experience item | The same scale point with different wording. |
| A6 | Code the agreement scale 1-5 (keep the label as an ordered category too) | Every on-scale label | Needed for pre/post change and averages. |
| A7 | Split `participant_role` on `;`, drop empty tokens, one-hot encode, derive `role_primary` | 100 rows end in a trailing `;`; 14 rows are multi-select; token order varies | `Caregiver/parent;` and `Caregiver/parent` are the same answer. One-hot keeps multi-select information. |
| A8 | County: remove the word "County", fix case and whitespace, move ` with ...` text to `county_partner` | 15 raw labels collapse to 6 | `Montgomery`, `Montgomery County ` and `Montgomery\xa0` are the same county. |
| A9 | Parse `start_time` and `completion_time` to datetime; parse `training_date` to date | 0 unparseable values | Needed for time trends. |
| A10 | Cast `likelihood_recommend` and `likelihood_call_988` to nullable integers | All values are integers 0-10 | Type safety. Missing values stay missing. |
| A11 | Convert empty or whitespace-only text to missing | 1 case in `fb_improvements` | Consistent handling of missing values. |
| A12 | Derive `completion_seconds`, `change_<item>`, `session_key`, `survey_version` | n/a | Pure derivations; no information is lost. |
| A13 | Drop columns that look like personal data (email, name, phone) at ingestion, if present | None in the current file | Privacy by default for future uploads. |

## B. Automate as flags only (no values changed)

| # | Flag | Rule | Count (2025-26) | Why it is not automatic removal |
|---|---|---|---|---|
| B1 | `flag_fast_completion` | `completion_seconds` < 30 | 216 (16.6%) | Short mobile completions in group sessions can be genuine. Sensitivity analyses should run with and without these rows. |
| B2 | `flag_reverse_scale_suspect` | All 3 post items `Strongly disagree` **and** `likelihood_recommend` of 9 or more | 17 (31 rows have all post items at Strongly disagree) | This looks like scale misreading, but it can't be confirmed. Recoding would be guesswork. |
| B3 | `flag_straightline` | Same label on all 6 pre/post items | 163 (12.5%) | Most of these are `Strongly agree` throughout, which is plausible. |
| B4 | `flag_post_below_pre` | Any post item lower than its pre item | 108 (8.3%) | Can be real, e.g. someone who realized they knew less than they thought. |
| B5 | `flag_training_date_after_completion` | `training_date` later than the completion date | 1 | Probably a typo, but which part is wrong is unknown. |
| B6 | `flag_training_date_gap_gt_30d` | completion date minus `training_date` > 30 days | 14 | Could be a late response or a mistyped date. |
| B7 | `flag_training_date_missing` | `training_date` is missing | 3 | Use the completion date for trends, and mark it as estimated. |
| B8 | `flag_offscale_label` | Experience item labelled `Very agree` / `Satisfied` | 24 rows | The value is held as NA until a mapping is confirmed (see C1). |
| B9 | `in_service_area = False` | County not on the configured list | 7 (Fairfax 6, Harris 1) | Kept, and excluded from county comparisons by default. |
| B11 | `flag_possible_duplicate` | Identical on every non-ID, non-timestamp column within the same `session_key` | 12 rows (6 pairs) | Classmates can give identical answers. Matching free text is stronger evidence, but neither is proof. |
| B10 | `flag_text_pii` | Regex finds a phone number, email or URL in free text | 3 phone-like strings | Redact at display time; the stored text keeps the original for authorized review. |

## C. Needs human review or EveryMind confirmation

| # | Issue | Options | Recommendation |
|---|---|---|---|
| C1 | `Very agree` and `Satisfied` labels (24 rows, mostly April 2026) | (a) map `Very agree` to 5 and `Satisfied` to 4; (b) treat as missing | Ask EveryMind which form produced them and what its scale was. Until then use (b), and report how results change under (a). |
| C2 | Suspected reversed scale (B2) | Recode, exclude, or keep | Keep, and report results with and without these rows. Ask EveryMind whether the form's option order changed. |
| C3 | Fairfax (VA) and Harris (TX) | Real out-of-state sessions or entry errors | Confirm with EveryMind; this decides the service-area list. |
| C4 | `Montgomery County with NAMI MC` | Partner-led session or a free-text county | Confirm whether partner-delivered sessions should be compared separately. |
| C5 | Survey versions (A/B/C in the schema spec) | n/a | Confirm the form history and dates with EveryMind. Without that, versions are inferred from the data. |
| C6 | Missing training dates (3) and the date after completion (1) | Infer from the session or leave as is | Leave as is, and use the completion date for trends. |
| C7 | 12 rows (6 pairs) with identical answers on all non-ID/time columns | Duplicate submissions or classmates answering alike | Each pair comes from the same session, three pairs have adjacent or near-adjacent IDs (573/575, 1065/1066, 1146/1147), and two pairs share identical free text. Double submission is plausible. Flag (B11) and review; do not drop automatically. |
| C8 | Free-text PII | n/a | Confirm the redaction policy and who may see raw comments. |
| C9 | Confounding of role and county (Anne Arundel is mostly students) | n/a | Analysis-stage decision: stratify by role when comparing counties. |

## D. Explicitly out of scope for cleaning

- Imputing any missing Likert, rating or text answer.
- Deleting rows for any flag.
- Making text-based inferences that change structured fields (e.g. guessing a role from a comment).
- Combining `likelihood_call_988` across versions as if every respondent had been asked it.

## E. Validation tests to write (Stage 2, `tests/`)

- The raw file checksum is unchanged after the pipeline runs.
- Row count in equals row count out, and `response_id` stays unique.
- Every required canonical field is present and its type is correct.
- Likert codes are within 1-5 or NA; 0-10 ratings are within 0-10 or NA.
- No off-scale label is silently mapped (B8 count equals NA introduced in C1).
- Each role one-hot row has at least one true value.
- County values are on the reference list or `in_service_area` is false.
- Re-running the pipeline on its own output gives the same result (idempotence).
- Synthetic uploads with reordered columns, renamed headers, an extra column, a missing optional field and NBSP-polluted labels all map correctly. A missing required field must block the upload.
