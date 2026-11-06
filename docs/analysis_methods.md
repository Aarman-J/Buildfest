# Analysis Methods, Assumptions, Limitations and Validation

**Stage 3 record.** This document describes the reusable analysis engine. The code is in `src/analysis_utils.py`, `src/analyze_q1.py`, `src/analyze_q2.py`, `src/analyze_q3.py`, `src/feedback_themes.py`, `src/recommendations.py` and `src/run_analysis.py`. Example outputs generated from the 2025-26 file are in `outputs/analysis_results.json`, `outputs/q1_summary.md`, `outputs/q2_summary.md`, `outputs/q3_summary.md` and `outputs/recommendations.md`.

## 1. Architecture

```
upload (xlsx/csv) -> load_survey_file -> clean_survey -> cleaned_df, quality_report
                                                   |
                         run_all(cleaned_df, quality_report, AnalysisConfig)
                           |-- run_q1 -> effectiveness, transitions, monthly trend, sensitivity
                           |-- run_q2 -> experience, recommend, call-988, correlations, satisfaction vs gaps, themes
                           |-- run_q3 -> county / role (overlapping) / primary role / language, composition check
                           '-- generate_recommendations(q1, q2, q3, quality_report)
```

- **Pure functions.** Every `run_*` function takes the cleaned DataFrame, never modifies it, does no I/O, and returns a JSON-compatible dict (numbers are Python `int`/`float`, NaN becomes `None`, tables are lists of records). Streamlit can call `run_all` directly.
- **Aggregate-only output.** Results contain no response IDs, no free text and no raw values. Free text is used only to count themes.
- **Graceful degradation.** A missing optional column or incomplete pair disables only the analyses that need it. Those analyses report `available: False` or appear in `objectives_missing`, and the engine never crashes.
- **Configuration** (`AnalysisConfig`):

| Setting | Default | Purpose |
|---|---|---|
| `min_cell` | 10 | Suppress groups smaller than this |
| `min_compare` | 30 | Minimum group size for comparisons and tests |
| `alpha` | 0.05 | Significance threshold for exploratory tests |
| `exclude_flags` | none | Rows to exclude in a sensitivity run |
| `meaningful_pp` | 10 | Smallest group difference (percentage points) treated as a signal |

## 2. Conventions used in every analysis

| Topic | Rule |
|---|---|
| Agreement | Scores 1-5. "Agree" means Agree or Strongly agree (score of 4 or more). |
| Denominators | Valid responses to the item(s) used. Pre/post figures use **paired** respondents (valid answers to both items). Every percentage is shown with its n. |
| Missing vs unusable | Blank answers count as *missing*. Off-scale or invalid answers count as *unusable*. Both are reported, and neither is counted as disagreement. |
| Intervals | 95% Wilson score intervals for proportions. |
| Training dates | Monthly trends use the respondent-reported training date only (`training_month`). Rows without a valid training date are counted, not reassigned. Completion dates are **never** substituted. |
| Flags | Flagged rows (reversed scale, fast completion, possible duplicate, straight-lining) are **kept** in every main result. `sensitivity` shows results without them. |
| Evidence labels | `verified_finding` (descriptive, adequate n), `exploratory_association`, `data_quality_warning`, `follow_up_investigation` |
| Causality | "Before" ratings are recalled after the training (retrospective pre/post). Changes are *self-reported perceived change*. No result is presented as causal. |

## 3. Q1: training effectiveness

For each available objective (`identify_mh`, `describe_988`, `know_when_988`):

- **Coverage:** valid pre, valid post, paired n, rows with only one of the two.
- **Distributions:** full 1-5 distributions before and after (paired), plus a 5×5 transition matrix for heatmap or Sankey charts.
- **Agreement:** % agreeing before and after, with the change in percentage points.
- **Individual change:** % improved, unchanged and declined, plus mean and median change. A sign test (improved vs declined) is reported as exploratory.
- **Remaining gap:** % not agreeing after training, split into neutral and disagree.
- **Ceiling:** % at "Strongly agree" before, and % improving among those who had room to improve.
- **Transitions:** % moving into agreement (among those not agreeing before) and % moving out.
- **Trend by training month:** n, number of sessions, before/after agreement, change, % not agreeing, and the answer rate of each optional item. Months below `min_cell` are suppressed; months below `min_compare` are marked.
- **Sensitivity:** headline figures recomputed with each flag set excluded. Robustness is judged on the data-quality exclusions only (reversed scale, fast completion, possible duplicates), with a threshold of less than 5 pp shift. Excluding straight-liners is shown for information only, because their change is zero by definition.

- **Agreement categories (added 2026-11-06):** each paired respondent falls into exactly one category:
  - reached agreement (below Agree before → Agree or above after)
  - maintained agreement
  - remained below agreement
  - moved below agreement

  The **reached-agreement rate** is reached ÷ started below Agree, and is "Not applicable" when nobody started below. This differs from numeric change: Disagree → Neutral is an improvement that doesn't reach agreement, and Strongly agree → Agree is a decline that maintains it.

## 4. Q2: experiences, relationships and feedback

- **Experience items:** coverage (valid, missing, unusable), full distribution, % agree, % strongly agree, mean.
- **Experience composite:** the mean of the available experience items per respondent.
- **Recommendation:** full 0-10 distribution, mean, median and quartiles. Promoters/passives/detractors and NPS are a secondary view.
- **Likelihood to call 988:** coverage overall and per inferred survey version, and statistics **among those asked and answering only**. The caveat travels with the result.
- **Relationships:** Spearman rank correlation on pairwise-complete rows, always reported with n, rho, p and a strength label (|rho| < 0.1 negligible, < 0.3 weak, < 0.5 moderate, otherwise strong). The pairs tested are:
  - experience vs post confidence
  - experience vs change
  - experience vs recommend
  - each experience item vs post confidence
  - recommend vs change
  - post confidence and change vs call-988

  Correlations need n ≥ `min_compare` and are exploratory, with no multiple-testing correction.
- **Satisfaction vs gaps:** among *highly satisfied* respondents (experience composite ≥ 4.5) and *promoters* (9-10), the % still not agreeing after training, compared with everyone else.
- **Experience trend** by training month.

### Written-feedback themes (`src/feedback_themes.py`)

1. **Dictionary.** Fixed keyword/regex dictionaries per question (`THEMES`, version `THEME_VERSION`). The keywords came from the most frequent words and two-word phrases in the 2025-26 comments, English and Spanish. They were refined on random samples of unclassified comments, read during development only. No theme is generated automatically.
2. **Multiple themes** per comment are allowed. A substantive comment with no match becomes `other_unclassified`.
3. **Response type** (exactly one per comment):

| Type | Rule |
|---|---|
| `unclear` | Non-answers ("idk", "n/a", a single character), no letters, or 2 words or fewer with no theme |
| `improvement_suggestion` | A suggestion theme or suggestion cue word, except plain questions and "nothing to improve" |
| `praise` | Praise words with no suggestion |
| `neutral` | Everything else: "nothing", questions, statements of what was learned |

4. **Privacy.** Only counts leave the module. No quotations are produced.
5. **Human validation** (still to be done; see section 8):
   1. Draw a sample with `validation_sample(cleaned, field, n=50, seed=2026)`. It is reproducible, PII-redacted, and must be kept private.
   2. Two reviewers each fill in `human_themes` and `human_type` independently, then resolve disagreements.
   3. Run `score_validation(sample)` to get per-theme precision and recall, type accuracy and Cohen's kappa.
   4. Revise the keywords of any theme with precision below 0.8, bump `THEME_VERSION`, and record the result here.

Current coverage on the 2025-26 file:
- **fb_most_valuable:** 1,063 comments; 18% `other_unclassified`.
- **fb_improvements:** 651 comments; 16% `other_unclassified`.

A development spot-check found misclassifications (for example, praise that mentions engagement). This is why human validation is required before theme counts are used in external reporting.

## 5. Q3: community and population differences

- **Groupings:**
  - **County:** with status, state and service-area metadata.
  - **Role:** one group per role; multi-select, so groups **overlap**.
  - **Primary role:** exclusive; "multiple" is its own group.
  - **Language.**
- **Group profile:** n, sessions, and for each objective paired n, before/after agreement (with CIs), change, % improved and declined, % not agreeing after, and distributions. Also experience (composite), recommend, call-988 (with n), item missingness, survey-version mix and role mix. If a group has a post item without its pre item, post-only results are still shown.
- **Minimum-group-size policy:**
  - n < `min_cell`: statistics suppressed, n shown as "<10".
  - `min_cell` ≤ n < `min_compare`: descriptive only.
  - n ≥ `min_compare`: compared.
  - Small groups appear as follow-up items with the explicit note that low counts are not evidence of poor access.
- **Comparisons:** each comparable group is compared against the rest of the sample on post agreement and on change in agreement. A **signal** requires:
  - an absolute difference ≥ `meaningful_pp`,
  - n ≥ `min_compare` on both sides,
  - and, for proportions, non-overlapping Wilson intervals.

  Kruskal-Wallis tests on change scores are run only for non-overlapping groupings (county, primary role, language), and are exploratory.
- **Students vs other participants (added 2026-11-06):** membership-based. Students = anyone who selected Student, including multi-role respondents; others = did not select Student. This comparison is descriptive only.
- **Exploratory label:** comparable groups with 30-49 respondents are labelled exploratory.
- **Composition check** (strata now use Student *membership*): each county's recalled-before and after agreement rates are directly standardized to the overall student/non-student mix. Counties without at least `min_cell` respondents in each stratum are listed as *not standardizable*, meaning their differences cannot be separated from audience mix.

## 6. Recommendation engine (`src/recommendations.py`)

Each rule fires only when its threshold is met in the uploaded data, and emits: finding → evidence (with numbers and n) → action → implementation → success metric, plus category, priority and caveats. Recommendations are sorted by priority, then category, then rule.

| Rule | Trigger (default threshold) | Category |
|---|---|---|
| Q1_REMAINING_GAP | Largest % not agreeing after ≥ 5% | verified |
| Q1_LARGEST_GAIN | Always (largest pp increase) | verified |
| Q1_CEILING | Recalled-before agreement ≥ 70% | data quality |
| Q1_SENSITIVITY | Data-quality exclusions shift results ≥ 5 pp | data quality |
| Q1_MONTH_DIP | Month post-agreement ≥ 15 pp below overall (month not suppressed) | follow-up |
| Q2_EXPERIENCE_CONFIDENCE | \|rho\| ≥ 0.3, n ≥ min_compare | exploratory |
| Q2_LOWEST_EXPERIENCE_ITEM | Lowest item < 90% agree and ≥ 2 pp below highest | verified |
| Q2_SATISFIED_NOT_CONFIDENT | ≥ 10 highly satisfied respondents not agreeing after | exploratory |
| Q2_CALL_988_COVERAGE | Call-988 answered by < 50% | data quality |
| Q2_DETRACTORS | Detractors (0-6) ≥ 25% | follow-up |
| Q2_QUESTIONS_IN_FEEDBACK | "Questions about 988" ≥ 15% of improvement comments | verified |
| Q2_FEEDBACK_SUGGESTIONS | Suggestion theme ≥ 5% of improvement comments and n ≥ 10 | exploratory |
| Q3_GROUP_DIFFERENCE | Any Q3 signal (one recommendation per dimension) | follow-up |
| Q3_SMALL_GROUPS | Any suppressed or descriptive-only group | follow-up |
| Q3_COMPOSITION | County student share differs ≥ 30 pp from overall | data quality |
| Q3_RECOMMEND_BY_ROLE | Mean recommend differs ≥ 1.5 points between comparable primary-role groups | exploratory |
| DQ_* | Off-scale labels, more than one survey version, training-date problems, fast completion ≥ 10%, reversed-scale suspects ≥ 1%, comments with possible personal information | data quality |

## 7. Validation performed

105 automated tests pass: 76 cleaning tests and 29 analysis tests.

The analysis tests check:
- **Paired calculations by hand:** agreement, change, improved/unchanged/declined counts, remaining gap, ceiling, and that the transition matrix adds up to the paired n.
- **Denominators:** missing and off-scale answers are excluded, and missing is never counted as "not agreeing".
- **Optional inputs:** incomplete pairs, absent optional questions and absent call-988 data are handled.
- **Trends:** they use training dates only; completion months never appear; small months are suppressed.
- **Groups:** overlapping roles count in every group they belong to; there are no tests on overlapping groups; the small-group policy is applied; unknown categories don't break grouping.
- **Composition check:** a synthetic case where the county difference is entirely due to role mix gives identical standardized rates (75% vs 75%).
- **Statistics helpers:** Spearman reports the pairwise n; Wilson intervals are correct.
- **Feedback:** theme and type classification, counts-only summaries, and the validation workflow.
- **Recommendations adapt to data:** no gap rule when everyone agrees; the gap rule picks the right objective.
- **No hard-coded results:** the source files are scanned for numbers and county names from the 2025-26 file.
- **Determinism and side effects:** results are identical across runs, and the input DataFrame is unchanged.
- **Privacy:** no response IDs and no distinctive comment text in the JSON results or the Markdown summaries.
- **Independent verification:** paired n, before/after agreement, pp change and improved counts for all three objectives, experience agreement, recommendation n and mean, call-988 n, and role membership counts are recomputed directly from the raw spreadsheet with plain pandas, without the cleaning code. All match.

## 8. Limitations and open items

- **No causal claims.** Retrospective pre/post ratings are open to recall bias and response-shift. Confidence is self-reported, not tested knowledge.
- **Ceiling effects.** Agreement before training is already high for "identify mental health challenges", which limits measurable change.
- **Mixed survey versions.** Survey versions are mixed, and call-988 is collected in about a fifth of rows. Its results describe only those who were asked.
- **County is entangled with audience and session.** County, audience type (students vs adults) and session are intertwined, and the dataset has no session or presenter identifier. Montgomery cannot be role-standardized.
- **Small groups.** Spanish-language (19), Queen Anne's (12), Fairfax (6) and Harris (1) are too small to compare.
- **Theme validation pending.** The theme classifier has not yet been validated by people, and name detection in comments is limited to titled names, such as "Ms. X".
- **Exploratory tests.** Tests are not corrected for multiple comparisons; with n ≈ 1,300, small effects reach p < 0.05, so focus on effect sizes and intervals.
