# EveryMind BuildFest — Project Instructions

## Project layout
- `data/` — original dataset (`BuildFest_EveryMind_Survey_Data_FULL.xlsx`). **Read-only. Never modify, overwrite, or save over any file in this folder.**
- `src/` — Python scripts (cleaning, analysis, charting).
- `outputs/` — all generated files: cleaned data, reports, charts.
- `tests/` — automated validation of the cleaned data and analysis.
- `docs/` — decisions, methods, and the AI usage log.

## Commands
- Install: `python -m pip install -r requirements.txt`
- EDA profile: `python src/explore_data.py`
- Clean + quality report: `python src/clean_data.py` (writes `outputs/cleaned_survey.csv`, `outputs/data_quality_report.md/.json`)
- Analysis + example summaries: `python src/run_analysis.py` (writes `outputs/analysis_results.json`, `q1/q2/q3_summary.md`, `recommendations.md`)
- Dashboard: `streamlit run app.py` (see README for secrets location and AI settings)
- Tests: `python -m pytest`

## Code map
- `src/schema_mapper.py`: canonical schema, aliases, answer vocabularies, column matching. Single source of truth.
- `src/clean_data.py`: `clean_survey(raw_df) -> (cleaned_df, report)` (pure, in-memory), `load_survey_file`, `make_public_dataset`.
- `src/data_validation.py`: value parsing, PII detection, analysis readiness, quality report and Markdown rendering.
- `src/analysis_utils.py`: shared conventions (denominators, Wilson CIs, suppression, Spearman, `AnalysisConfig`).
- `src/analyze_q1.py` / `analyze_q2.py` / `analyze_q3.py`: `run_q1/2/3(cleaned_df, cfg) -> dict` (JSON-compatible, aggregate only).
- `src/feedback_themes.py`: rule-based comment themes + human validation helpers. `src/recommendations.py`: rule-based recommendations.
- `src/run_analysis.py`: `run_all(cleaned_df, quality_report)` and Markdown summaries. Methods: `docs/analysis_methods.md`.
- `src/ai_recommendations.py`: optional OpenAI drafts (aggregate payload, strict schema, validation, access/cost controls). Docs: `docs/ai_recommendations.md`.
- `app.py` + `ui/`: Streamlit UI only. `ui/state.py` (per-session lifecycle), `ui/transforms.py` (pure reshaping, tested), `ui/charts.py`, `ui/page_*.py`.
- Import as `from src.clean_data import clean_survey` from the project root.
- Never read, print or commit `.streamlit/secrets.toml`; AI is off unless `[ai] enabled = true` server-side.

## Rules
- Load the raw data from `data/`; write every derived file to `outputs/`.
- Keep scripts reproducible: running them from scratch should regenerate everything in `outputs/`.
- Record each data-cleaning decision (what, why, rows affected) in `docs/`.
- Log notable AI-assisted work in `docs/` (what was asked, what was produced, what was checked).
- Never drop rows or impute answers in cleaning; flag instead. Pending approvals are listed in `docs/cleaning_decisions.md`.
- Never put free-text comments or `*_raw` values in public outputs, logs or the quality report; export via `make_public_dataset`.
