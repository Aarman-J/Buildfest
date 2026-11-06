# Cleaning Decisions

**Stage 2 record, updated in Stage 3.** Lists what the cleaning layer (`src/clean_data.py`, `src/schema_mapper.py`, `src/data_validation.py`) does, why, how many rows it affects in the 2025-26 file (1,299 rows), and which decisions are still **pending approval**. Evidence is in `outputs/data_profile.md` and `outputs/data_quality_report.md`.

Ground rules (all implemented and tested):
- The raw file is never modified (SHA-256 `56f42d55...3020`, checked by the CLI and by tests).
- `clean_survey(raw_df)` is pure: it doesn't modify the input, doesn't touch files and makes no network calls.
- No rows are dropped. Every input row comes out exactly once.
- Nothing is imputed. Missing stays missing; unusable values become `NA` and their original text is kept in `<field>_raw`.
- Problems become `flag_*` columns. No row is excluded without explicit approval.

## 1. Schema mapping

| ID | Decision | Detail |
|---|---|---|
| M1 | Matching order: user override, then exact canonical name, then documented alias, then keyword rules, then fuzzy *suggestion* | Headers are compared after NFKC normalization, removal of NBSP and invisible marks, whitespace collapse, lowercasing and accent stripping. Column order doesn't matter. |
| M2 | Fuzzy matches are never applied | The similarity score (0.6 or higher) is reported as a suggestion. The column is kept as `extra__<header>`. |
| M3 | Conflicts are never resolved silently | If two columns match one field, or one header matches several fields, nothing is applied until the user sets `column_overrides`. |
| M4 | Value check after header match | At least 80% of non-missing values must fit the field type (Likert label, 0-10 integer, or date). Otherwise the match is rejected and the column is kept as an extra. User overrides skip this check. |
| M5 | "Required" now means "needed for an analysis", not "needed to upload" | Each analysis declares the fields it needs (`data_validation.ANALYSES`). An upload is **blocked** only if no outcome or experience question is recognized at all. Missing pairs or optional blocks disable only the analyses that need them. |
| M6 | Unrecognized columns are kept; columns that look like personal data are dropped | Unmatched columns are kept as `extra__*` (working data only). Unmatched headers such as Email, Name, Phone or Address are not loaded, and only the header name is reported. |
| M7 | Aliases live in code | `schema_mapper.DEFAULT_ALIASES` holds the exact 2025-26 headers plus common variants. More can be passed at runtime with `CleaningConfig.column_aliases`. This replaces the YAML file proposed in Stage 1, so no extra dependency is needed. |

## 2. Value normalization (automatic, lossless)

| ID | Operation | Rows / values affected (2025-26) |
|---|---|---|
| N1 | Remove invisible characters and extra spaces from every text value | Likert: 566 / 678 / 675 `Strongly agree\xa0` values across the three experience items. Language: all 1,299 (U+200E). |
| N2 | `Neither agree or disagree` becomes `Neither agree nor disagree` | 98 / 76 / 63 across the three experience items |
| N3 | Agreement scale scored 1-5; `<field>_label` holds the canonical text and `<field>_raw` the original | All 9 Likert items |
| N4 | Case-insensitive label matching | 0 needed in this file; supported for future uploads |
| N5 | Language mapped to `en` / `es` / `other`; `language_label` keeps the readable label | 1,280 `en`, 19 `es` |
| N6 | Roles: split on `;`, drop empty tokens, map to codes, one boolean column per code, `participant_role` = codes in a fixed order, `role_primary` = the single role or `multiple` | 100 trailing `;` removed; 14 multi-select rows |
| N7 | County: explicit mapping table (24 Maryland jurisdictions, common abbreviations, a few neighbouring DC/VA jurisdictions); partner text after `with` / `(` / ` - ` goes to `county_partner` | 15 raw labels become 6 counties; 17 rows relabelled; 1 partner (`NAMI MC`) |
| N8 | Dates parsed (ISO, US m/d/y, Excel serial, datetime objects); training date kept at day precision | 0 invalid |
| N9 | 0-10 ratings: whole numbers 0-10 only. Text like `"10 - extremely likely"` counts as *invalid*, not a guess | 0 invalid; 33 / 1,006 missing |
| N10 | Free text: whitespace cleaned; empty strings become missing | 1 whitespace-only improvement comment becomes missing |
| N11 | Response ID kept as text; generated as `row-N` only when absent, with `response_id_generated` set | 0 generated |

## 3. Derived fields

| Field | Definition |
|---|---|
| `change_<item>` | Post score minus pre score (-4 to +4). Present only when both items of the pair were mapped. |
| `completion_seconds`, `completion_date` | From start and completion timestamps |
| `training_date_valid`, `training_month` | `training_date_valid` is true when the reported training date parses and is on or after the 988 launch (2022-07-16). `training_month` (YYYY-MM) is set only for valid dates. **Stage 3:** the completion date is never substituted; the former `training_date_effective`/`training_date_source` fallback was removed. |
| `session_key` | Valid training date plus county (`unknown date` when the training date is missing or invalid). A stand-in for a session ID, since none exists. |
| `survey_version`, `survey_version_confidence` | See section 5 |
| `n_flags` | Number of `flag_*` columns set on the row |

## 4. Flags (rows kept)

| Flag | Rule | 2025-26 count |
|---|---|---:|
| `flag_offscale_label` | Any Likert answer outside the agreement scale (score set to NA, raw kept) | 24 |
| `flag_fast_completion` | 0 ≤ completion time < 30 s (configurable) | 216 |
| `flag_completion_before_start` | Negative duration | 0 |
| `flag_reverse_scale_suspect` | At least 2 post items, all `Strongly disagree`, and recommend score of 9 or more | 17 |
| `flag_straightline` | At least 4 pre/post items answered, all identical | 163 |
| `flag_post_below_pre` | Any pre/post change below 0 | 108 |
| `flag_possible_duplicate` (+ `duplicate_group`) | Identical on every answer field (raw) within the same session | 12 rows / 6 groups (2 also share identical free text) |
| `flag_duplicate_response_id` | Response ID repeats | 0 |
| `flag_training_date_missing` / `_invalid` / `_implausible` | Blank / unparseable / before the 988 launch | 3 / 0 / 0 |
| `flag_training_date_after_completion` | Training date later than the completion date | 1 |
| `flag_training_date_gap_gt_30d` | Completion more than 30 days after training | 14 |
| `flag_invalid_rating` | Non-missing 0-10 value that is not a whole number from 0 to 10 | 0 |
| `flag_role_unrecognized` | A role token not in the role map | 0 |
| `flag_county_needs_review` | County unrecognized or ambiguous | 1 (Harris) |
| `flag_out_of_service_area` | Recognized county outside the configured states (default MD) | 6 (Fairfax, VA) |
| `flag_text_pii` | A comment matches the email, phone, URL, @handle, titled-name or street-address patterns | 4 comments (3 phone-like, 1 titled name) |

## 5. Survey-version inference (heuristic)

For each `session_key`, a question block counts as *asked* when at least 50% of the session's rows answered it. The blocks are experience (3 items), recommend, and call_988. A session is labelled `alt scale` when at least 20% of its rows use off-scale labels. Sessions with fewer than 5 rows get `survey_version_confidence = low`; all others get `moderate`, never "high".

| Inferred version | Sessions | Rows | Dates |
|---|---:|---:|---|
| experience+recommend | 35 | 980 | 2025-10-21 to 2026-10-09 |
| experience+recommend+call_988 | 12 | 162 | 2026-04-08 to 2026-08-27 |
| experience+recommend+call_988 (alt scale) | 3 | 17 | April 2026 |
| experience+recommend (alt scale) | 2 | 20 | April 2026 |
| recommend+call_988 | 2 | 119 | 2026-10-20 to 2026-10-21 |
| core only | 1 | 1 | 2026-07-08 (single respondent who skipped the optional items) |

## 6. Privacy

- **Working data** (`clean_survey` output, in memory only): includes free text, `*_raw` columns, exact timestamps and `extra__*` columns. It is for authorized staff processing only.
- **Public data** (`make_public_dataset`, written to `outputs/cleaned_survey.csv`) drops:
  - free text (each comment field is replaced by a `<field>_provided` boolean)
  - all `*_raw` columns
  - exact start and completion timestamps (date and duration are kept)
  - `role_unrecognized` and `county_partner`, since they may hold typed-in text
  - all `extra__*` columns
- The quality report and logs contain only aggregate counts and category labels. A test checks that no comment text appears in either.
- No network calls. Everything runs in memory, and only the CLI writes files.

## 7. Decisions confirmed in Stage 3

| # | Decision | Confirmed rule | Implementation |
|---|---|---|---|
| P1 | `Very agree` / `Satisfied` | **Revised 2026-11-06:** mapped Very agree → 5 and Satisfied → 4 on the three experience items only, flagged `flag_experience_label_normalized`, raw text kept. See `docs/refinement_2026-11-06.md` for the evidence. | `CleaningConfig.experience_label_map` (set `{}` to disable). |
| P2 | Possible reversed-scale responses (17) | **Retained**; sensitivity analysis provided | `analyze_q1.sensitivity` reports results with and without them |
| P3 | Uncertain county assignments (Fairfax VA, Harris) | **Kept and flagged** | `flag_county_needs_review`, `flag_out_of_service_area` |
| P4 | Bare `Montgomery` means Montgomery County, MD | Unchanged assumption; still worth confirming with EveryMind | `COUNTY_ALIASES` |
| P5 | Possible duplicates (6 pairs) | **Preserved** | Flagged; sensitivity scenario `exclude_possible_duplicates` drops the repeats only |
| P6 | Fast completion (< 30 s) | **Warning only**, never an exclusion | Flag; sensitivity scenario available |
| P7 | Training date fallback | **Removed.** Completion dates are not substituted in training-date analyses. Since 2026-11-06, dates after the survey submission are also excluded from trends (1,295 usable). | `training_date_valid`, `training_month`; tested |
| P8 | Identifying information and partner names | **Excluded from public exports.** Since 2026-11-06 the dashboard offers aggregate-only downloads; `make_public_dataset` also drops IDs, exact dates, session keys and duplicate links. | tested |
| P9 | Manually confirmed schema mappings | **Supported** for future uploads | `CleaningConfig.confirm_mapping()`, `to_dict()` / `from_dict()` for saving; tested |
| P10 | Category compatibility (value check) | Missing responses are separated from non-missing invalid responses; only non-missing values count toward the 80% compatibility share | `check_column_values` returns `missing`, `nonmissing`, `valid`, `invalid`; tested |
