# Refinement Record: Teammate Review, Analytical Corrections and Dashboard Simplification (2026-11-06)

**Scope:** incorporate validated corrections from a teammate's review and simplify the dashboard for a 2-minute demo.

The pre-refinement version is preserved as the local branch `backup/pre-refinement` and the tag `pre-refinement-2026-11-06`. Nothing has been pushed or deployed.

**Source material.** The teammate's comparison report, reference calculations, codebook and revised app files were **not available** on this machine; the only BuildFest PDF found was the official case brief. Each correction below was therefore verified independently against the raw workbook with plain pandas, using the figures quoted in the request. The verification script is reproduced by the tests in `tests/test_refinements.py`.

## 1. Corrections

| # | Issue | Verdict | Before | After |
|---|---|---|---|---|
| 3.1 | "Very agree" / "Satisfied" on the 3 training-experience items | **Implemented** as an approved, flagged normalization: Very agree → 5, Satisfied → 4, experience items only. The 6 confidence items are unchanged. Raw text is kept in `*_raw`; rows carry `flag_experience_label_normalized`. | Valid n: 1,125 / 1,123 / 1,118. Agree %: 87.9 / 90.1 / 91.6 | Valid n: 1,148 / 1,145 / 1,141. Agree %: 88.2 / 90.3 / 91.8 |
| 3.2 | Student membership | **Implemented.** The audience-adjustment check and the new students-vs-others comparison use *membership*: anyone who selected Student, including multi-role respondents. Primary-role tables (mutually exclusive) are kept. | 1,076 students (primary role); overall student share 82.8% | 1,080 students (membership); overall student share 83.1%. Prince George's student share: 58.3% → 59.5% |
| 3.3 | Montgomery / "NAMI MC" | **Not changed.** See section 3. | 34 | 34 (partner text in `county_partner`) |
| 3.4 | Training date after survey completion | **Implemented.** Excluded from training-date trends and from `training_month`; the record stays in all other analyses. Completion dates are still never substituted. | 1,296 usable training dates | 1,295 |
| 3.5 | Likelihood-to-call wording | **Implemented.** Everywhere the dashboard, reports, rules and AI payload now say "answered", not "asked": 293 of 1,299 answered and 1,006 are missing. Missing answers may mean the question wasn't shown or was skipped; inferred survey versions are labelled a heuristic. | "asked of only some participants" | "answered by 293 of 1,299; exposure not recorded" |
| 3.6 | Public export privacy | **Implemented.** The dashboard no longer offers any row-level download; all downloads are aggregate. `make_public_dataset` additionally drops response IDs, exact dates, completion seconds, session keys and duplicate links, and is documented as not for public release. | Row-level CSV download with `response_id`, day-level dates and `session_key` | Aggregate-only downloads (executive summary, priorities, summary tables, quality report, rule list, AI drafts, JSON results) |

The off-scale count dropped from 24 rows to 0, and the inferred question-set count changed from 6 to 4. That's because the alternate-label sessions are no longer a separate "alt scale" signature. Rule-based recommendations went from 22 to 21, because `DQ_OFFSCALE_LABELS` no longer triggers.

### Why the experience mapping is justified

There is no codebook. Evidence from the data:
- 23 of the 24 rows that use these labels answer all three items with a single consistent label.
- Mean recommend scores for these respondents are 9.1 (Very agree) and 8.6 (Satisfied), which is clearly the positive end of the scale.
- Both labels map to ≥ 4, so every "% agree" figure is the same under any agreement-side mapping. Only means and "% strongly agree" depend on the exact level.

One row mixes "Strongly agree" and "Very agree", so the exact level of "Very agree" is uncertain. The mapping can be turned off with `CleaningConfig(experience_label_map={})`.

## 2. Analytical additions

| Addition | Values on the provided data (verified independently) |
|---|---|
| Four exclusive agreement categories per objective (sum = paired n) | Identify: reached 264, maintained 904, remained below 97, moved below 34. Describe: 602 / 573 / 93 / 31. Know when: 501 / 698 / 75 / 25 |
| Reached-agreement rate = reached ÷ started below Agree ("Not applicable" if nobody started below) | 264/361 = 73.1% · 602/695 = 86.6% · 501/576 = 87.0% (matches the teammate's figures) |
| Students vs other participants (membership-based) | Recommend: 7.22 (n = 1,051) vs 9.38 (n = 215); overall 7.59. Positive experience: 86.3% vs 94.5% |
| Exploratory label for comparable groups with 30-49 respondents | Montgomery (34) is labelled exploratory everywhere, including the AI payload |
| Consolidated priorities A-D (computed, data-triggered) | All four trigger on the provided data. Each links evidence → action → implementation → responsible role → progress measure → limitation, plus the IDs of its underlying rules. No numeric targets |

## 3. Proposals not implemented

| Proposal | Reason |
|---|---|
| Montgomery 34 → 33 | The label is "Montgomery County with NAMI MC": it explicitly names the county, and 5 of the 6 other responses from the same training date (2025-12-03) chose Montgomery. Reclassifying it as unknown would discard supported information. The partner name is stored separately and is excluded from public outputs. This is a team decision if you prefer the stricter rule; it changes Montgomery's n from 34 to 33, which is still exploratory. |
| Comment-theme counts (91 contact-process, 49 practice, 30 pacing) | The teammate's codebook wasn't available, so the definitions can't be checked. Our documented themes are unchanged and still labelled provisional. |
| Teammate's revised application layout | Per instructions, our app remains the source of truth; only the four priority areas were adopted, as computed cards. |

## 4. Interface changes

**Seven pages became four:**

| Before | After |
|---|---|
| Executive Overview | **Executive Overview** |
| Q1, Q2 and Q3 pages | **Explore Findings**, with Q1, Q2 and Q3 tabs |
| AI Insights & Recommendations | **AI Insights & Recommendations** |
| Data Upload & Quality, Exports & Reports | **Upload & Data Quality** (downloads merged in) |

- **Overview:** 4 KPI cards ("percentage points" spelled out), one before/after chart, 4 computed takeaways and 4 priority cards.
- **Each Q tab:**
  - **Q1:** before/after, still-not-confident, agreement categories, monthly trend with sample sizes
  - **Q2:** experience ratings, recommendation (students vs others), call-988 with the coverage warning, provisional themes
  - **Q3:** comparable groups (n ≥ 30), students vs others, audience-mix warning

  Each primary chart has a one-sentence takeaway computed from the data. Heatmaps, correlations, distributions, sensitivity and audience-adjustment tables moved to **Advanced details**.
- **Recommendations:** the 4 priorities first, all rules in an expander, then the optional Gemini section with validation unchanged.
- **Upload:** an explicit upload always starts fresh, even for the same file as the demo, and runs automatically when no column needs confirmation. The quality details live in an expander, followed by aggregate downloads.
- **Mobile:** responsive CSS; tested at 390 px wide.

## 5. Release blockers and risks (action needed by the team)

1. **The raw workbook is in public Git history.** Commit `779348b` (pushed to the public GitHub repo) contains `data/BuildFest_EveryMind_Survey_Data_FULL.xlsx`, including written comments; 4 were flagged for possible personal information. Adding it to `.gitignore` or deleting it later does **not** remove it from history. Options:
   - make the repository private
   - rewrite history (for example `git filter-repo`) and force-push
   - keep it public only if the challenge rules explicitly allow publishing the dataset
2. **Anonymous judging.** The brief says submitted links must not reveal identities. The repository is under a personal account (`Aarman-J`) and commits carry a personal name. Consider a neutral account or organization, or submitting a ZIP.
3. **Public AI access.** The local secrets file currently sets `access = "public"`, and the earlier deployment guidance used the same. The code default remains off. The updated guidance is not to enable unrestricted generation for anonymous visitors: use `access = "allowlist"` with sign-in, or `enabled = false` on the public site, and demonstrate AI locally.
4. **Exposed API keys.** Both keys pasted in chat earlier should be rotated before submission.

## 6. Two-minute demo script (matches this version)

| Time | Screen | Say / do |
|---|---|---|
| 0:00-0:15 | Executive Overview | "EveryMind collects feedback from its 988 awareness training. Our dashboard turns it into evidence staff can act on." Point at the 4 cards. |
| 0:15-0:40 | Upload & Data Quality | Drag in the survey file. It's recognized (20 of 20 questions), cleaned and checked automatically: 1,299 rows kept, warnings listed. Open "Technical details" for one second. |
| 0:40-1:05 | Explore Findings → Q1 | Before vs after (largest gain +44 percentage points on explaining 988), the remaining gap (10.1% on identifying a crisis), the "reached agreement" chart. "These are recalled, self-reported ratings, not proof of cause." |
| 1:05-1:25 | Q2, then Q3 | Engagement is the lowest-rated item; students average 7.2/10 vs 9.4/10 for adults. In Q3, Montgomery is labelled exploratory and small counties aren't compared. |
| 1:25-1:45 | Recommendations & AI | The four priority cards (evidence → action → who → measure). If AI is enabled locally, show a validated Gemini draft with its cited evidence; otherwise explain that it's optional. |
| 1:45-2:00 | Upload page, downloads | Download the executive summary. "Each month, staff upload the new export and the results, warnings and priorities update automatically." |

## 7. Validation

- **Tests:** 198 automated tests pass. The 17 new tests in `tests/test_refinements.py` cover every correction plus the upload-replacement behavior.
- **Updated tests:** four existing tests were updated because they encoded the old, now-corrected behavior:
  - off-scale count
  - training date after completion
  - independent raw-workbook recalculation, now applying the approved experience mapping
  - the app smoke test, which now sets its own AI secrets instead of reading the developer's local file
- **Headless and browser runs:**
  - all four pages render without errors
  - a real browser upload of a reordered CSV with an extra column was processed automatically, and the sidebar, banners and results updated
  - checked at desktop (1440 px) and phone (390 px) widths
