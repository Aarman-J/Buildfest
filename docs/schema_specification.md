# Canonical Schema Specification

**Status:** implemented in Stage 2. **Source of truth in code:** `src/schema_mapper.py` (`SCHEMA`, `DEFAULT_ALIASES`, answer vocabularies). `explore_data.py`, `clean_data.py` and `data_validation.py` all import from it. Implementation decisions and pending approvals are in `docs/cleaning_decisions.md`.

The dashboard will accept future EveryMind survey exports (Excel or CSV) that may differ from the 2025-26 file. Every upload is mapped onto the canonical schema below before any cleaning or analysis. All analysis code refers only to canonical names, never to raw headers.

## 1. Canonical fields

| Canonical field | Type | Core | Category | Allowed values / format | Observed source header (2025-26) |
|---|---|---|---|---|---|
| `response_id` | string | **Yes** | metadata | unique per row | `Id` |
| `start_time` | datetime | No | metadata | ISO-8601 or Excel datetime | `Start time` |
| `completion_time` | datetime | **Yes** | metadata | ISO-8601 or Excel datetime | `Completion time` |
| `language` | category | No | demographics | `en`, `es`, other ISO 639-1 codes | `Language` |
| `participant_role` | multi-select | **Yes** | demographics | tokens: `student`, `school_staff`, `caregiver_parent`, `other_community` | `Are you a....?` |
| `training_date` | date | **Yes** | metadata | YYYY-MM-DD | `Please enter the date of your training.` |
| `county` | category | **Yes** | demographics | controlled list (see 4.3) | `Which county is this training affiliated with?` |
| `post_identify_mh` | likert_agree_5 | **Yes** | training outcomes | 1-5 (see 4.1) | `...statements..I can identify mental health challenges and crises.` |
| `post_describe_988` | likert_agree_5 | **Yes** | training outcomes | 1-5 | `...statements..I can describe 988 and how it works.` |
| `post_know_when_988` | likert_agree_5 | **Yes** | training outcomes | 1-5 | `...statements..I know when to use 988.` |
| `pre_identify_mh` | likert_agree_5 | **Yes** | training outcomes | 1-5 | `Now think back to before... I could identify...` |
| `pre_describe_988` | likert_agree_5 | **Yes** | training outcomes | 1-5 | `Now think back to before... I could describe 988...` |
| `pre_know_when_988` | likert_agree_5 | **Yes** | training outcomes | 1-5 | `Now think back to before... I knew when to use 988.` |
| `sat_presenter_engaged` | likert_agree_5 | No | training experience | 1-5 | `...aspects of the training?.The presenter(s) engaged the participants.` |
| `sat_presenter_knowledge` | likert_agree_5 | No | training experience | 1-5 | `...The presenter(s) knew the subject matter well.` |
| `sat_well_organized` | likert_agree_5 | No | training experience | 1-5 | `...The training was well organized.` |
| `likelihood_recommend` | int 0-10 | No | training experience | integer 0-10 | `How likely are you to recommend...` |
| `likelihood_call_988` | int 0-10 | No | behavioral intent | integer 0-10 | `How likely are you to call 988...` |
| `fb_most_valuable` | free text | No | written feedback | string (PII-screened) | `What was the most valuable thing you learned...` |
| `fb_improvements` | free text | No | written feedback | string (PII-screened) | `How can we improve this training in the future?` |

### Derived fields (created by the pipeline, never uploaded)

| Field | Definition |
|---|---|
| `role_student`, `role_school_staff`, `role_caregiver_parent`, `role_other_community` | Boolean one-hot from `participant_role`. A response can have several set to true. |
| `role_primary` | Single label for grouping: the selected role, or `multiple` when more than one is selected. |
| `county_partner` | Organization text removed from the county field (e.g. `NAMI MC`). |
| `in_service_area` | Boolean: the county is on EveryMind's configured service-area list. |
| `session_key` | `training_date` + `county`, which stands in for a session ID (none exists today). |
| `change_<item>` | `post_<item>` minus `pre_<item>` on the 1-5 scale. |
| `survey_version` | Inferred from which optional blocks are present (see 5). |
| `completion_seconds` | `completion_time` minus `start_time`. |
| `flag_*` | Quality flags such as `flag_fast_completion`, `flag_reverse_scale_suspect`, `flag_training_date_after_completion`, `flag_training_date_gap_gt_30d` and `flag_offscale_label`. |
| `source_file`, `upload_id`, `ingested_at` | Provenance, so several uploads can be combined and audited. *Not yet implemented; to be added by the dashboard layer.* |

## 2. Mandatory vs optional

- **Core (13 fields; shown as "Yes" in section 1):** these are needed by the core analyses. *Revised in Stage 2:* a missing core field **disables the analyses that need it** instead of blocking the upload. Each analysis declares its fields in `data_validation.ANALYSES`. For example, if one pre/post pair is missing, the other pairs are still analyzed. An upload is **blocked** only when no outcome or experience question is recognized at all. If `response_id` is absent, `row-N` IDs are generated and `response_id_generated` is set.
- **Optional (7 fields):** if missing, the related charts are hidden or marked "not collected in this upload". Optional fields are **never** filled with defaults.
- **Unknown extra columns:** kept as `extra__<header>` columns in the working data, listed in the quality report, ignored by analyses and excluded from public exports. Columns that look like personal data (`email`, `name`, `phone`, `address`, `ip`) are **dropped at ingestion** with a warning.

## 3. Column matching for new uploads

Matching runs in order. The first step that gives exactly one candidate wins. A user override in `CleaningConfig.column_overrides` always comes first:

1. **Normalize the header:** Unicode NFKC; strip U+200E/U+200F/U+200B/BOM; non-breaking space to space; collapse whitespace; lowercase; remove accents. This is needed because 6 of 20 current headers contain NBSP or trailing spaces.
2. **Exact match:** the header equals a canonical name, either as written or with spaces instead of underscores. This lets a re-uploaded cleaned export round-trip.
3. **Documented alias:** `schema_mapper.DEFAULT_ALIASES` (exact 2025-26 headers plus common variants) plus runtime additions through `CleaningConfig.column_aliases`. The dashboard can save headers users confirmed and pass them back in.
4. **Keyword rules:** each field lists keyword groups that must **all** appear (e.g. `post_describe_988` needs "describe" **and** "988"). Exclusion keywords prevent mix-ups: the post items exclude "before", "think back" and "antes" so they don't capture the retrospective-pre items. A few Spanish keywords are included.
5. **Fuzzy suggestion:** `difflib` similarity against field descriptions. A score of 0.6 or higher is offered as a *suggestion only*, never applied automatically.

**Conflict rule:** if two columns claim the same field, or one column matches two fields, neither is applied automatically. Both go to the confirmation screen. The exception is when exactly one of them is a user override.

**Value-based checks** run after header matching, as a sanity check:
- Likert fields must have at least 80% of values that map to the agree scale.
- 0-10 fields must have at least 80% whole numbers within 0-10.
- Dates must be at least 80% parseable.

*Implemented threshold: 80% for all types* (`data_validation.VALUE_CHECK_MIN_SHARE`). A lower bar than 95% lets columns with some invalid values still load, with those values flagged. A mapped column that fails is rejected (status `rejected`), kept as an extra column and sent back for confirmation. User overrides skip this check.

### Header variations to expect

| Field | Likely variants |
|---|---|
| `response_id` | `ID`, `Response ID`, `Respondent ID`, `#` |
| `completion_time` | `Completion time`, `Submitted`, `End time`, `Timestamp`, `Hora de finalización` |
| `training_date` | `Training date`, `Date of training`, `Session date`, `Fecha` |
| `county` | `County`, `Jurisdiction`, `Training county`, `Condado` |
| `participant_role` | `Are you a...?`, `Role`, `Participant type`, `I am a` |
| pre/post items | The question stem varies (`Please tell us...`, `Rate your agreement...`), and the statement may come before or after the stem, joined by `.` or `..` (the Microsoft Forms matrix export style). |
| `likelihood_*` | `Likelihood to recommend`, `NPS`, `How likely...` |

Columns may come in any order and any letter case. Matching does not depend on column position.

## 4. Value standardization

### 4.1 Agreement scale (automatic)

| Code | Canonical label | Accepted raw variants (after normalization) |
|---|---|---|
| 1 | Strongly disagree | `strongly disagree` |
| 2 | Disagree | `disagree` |
| 3 | Neither agree nor disagree | `neither agree nor disagree`, `neither agree or disagree` |
| 4 | Agree | `agree` |
| 5 | Strongly agree | `strongly agree` (including the `\xa0`-suffixed form) |

**Not mapped automatically:** `Very agree` and `Satisfied` (and any other unknown label). These are held as `NA` with `flag_offscale_label = True`, and their raw values are kept. A user-confirmed mapping can be saved for later uploads.

### 4.2 Participant role (automatic)

Split on `;`, trim, drop empty tokens, then map each token:

| Token | Code |
|---|---|
| `Student` | `student` |
| `School staff member` | `school_staff` |
| `Caregiver/parent` | `caregiver_parent` |
| `Other (e.g., community member)` | `other_community` |

Unknown tokens go to the confirmation screen.

### 4.3 County (partly automatic)

Normalize the text, remove the word `county`, and move any text after ` with `, `(` or ` - ` to `county_partner`. Then look it up in the explicit mapping `clean_data.COUNTY_ALIASES`. It covers Maryland's 24 jurisdictions, common abbreviations, and a few unambiguous neighbours (DC; Fairfax, Arlington, Loudoun and Prince William in VA). EveryMind can add more through `CleaningConfig.county_aliases`.

- `county_status = recognized`: canonical name and state set. `in_service_area` = the state is in `service_area_states` (default `MD`). Fairfax is recognized as VA, so `in_service_area = False`.
- `county_status = ambiguous`: labels such as `Baltimore` (City or County?) and `Washington` (County or DC?). No state is guessed; the row is flagged for review.
- `county_status = unrecognized`: not in the mapping (e.g. `Harris`, which exists in both TX and GA). The cleaned label is kept, no state is set, and the row is flagged.

**No county value is dropped. Unrecognized and ambiguous values are not recoded.**

### 4.4 Language (automatic)

Strip U+200E, then map `English (United States)` to `en` and `Español (México)` to `es`. Anything else keeps its original label.

## 5. Survey-version handling

The 2025-26 file mixes at least three question sets. The implemented inference labels each session by the blocks at least 50% of its rows answered, for example `experience+recommend`, `experience+recommend+call_988` and `recommend+call_988`. It adds `(alt scale)` when at least 20% of the session used off-scale labels. Sessions with fewer than 5 rows get low confidence. Current counts are in `docs/cleaning_decisions.md` section 5.

Requirements:
- `survey_version` is **inferred per session**, not per row, because individual respondents also skip items.
- Every metric reports its **n** and uses item-level denominators.
- Time-trend charts for optional items start from the first session that collected them.
- An upload whose items all map, but with a new combination of blocks, is accepted. The new combination is logged as a new version signature.

## 6. Ingestion contract (summary)

1. Read every sheet and pick the one with the most matched fields (the user can override the choice).
2. Match columns (section 3) and show the mapping with confidence levels. The user confirms anything marked `ambiguous`, `suggest` or `unmatched`.
3. Report which analyses are available (`report["analyses"]`). Block only if no outcome or experience question is recognized. Otherwise warn per analysis.
4. Standardize values (section 4), keeping each raw value next to its canonical value for audit.
5. Add flags without deleting any rows.
6. Keep the working result in memory. Export only `make_public_dataset(...)`. The dashboard may save confirmed mappings and pass them back as `column_aliases` or `column_overrides`.
