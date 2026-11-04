# EveryMind 988 Training Insights

A Streamlit dashboard for the University of Maryland BuildFest 2026 Nonprofit Data Challenge. EveryMind staff upload their *Understanding 988* training feedback survey as an Excel or CSV file. The dashboard validates and cleans it in memory, then answers:

> **Main question:** How can EveryMind use its 988 training feedback data to develop an actionable solution for evaluating training effectiveness, identifying gaps across communities, and continuously improving future trainings?

| Page | What it shows |
|---|---|
| Executive Overview | Headline results, remaining gaps, community notes, data limitations and top evidence-based recommendations |
| Data Upload & Quality | Upload, column mapping and confirmation, cleaning, quality warnings, privacy-safe download |
| Q1 · Training Effectiveness | Before (recalled) vs. after confidence, change, remaining gaps, monthly trends, uncertainty and sensitivity |
| Q2 · Trends & Relationships | Presenter and organization ratings, recommendation, likelihood to call 988, correlations, provisional feedback themes |
| Q3 · Community Differences | County, role (overlapping) and language comparisons, with small-group suppression and an audience-mix check |
| AI Insights & Recommendations | Rule-based recommendations, plus optional AI-drafted suggestions (advisory, validated) |
| Exports & Reports | Cleaned data, tables, executive summary, data-quality report, recommendations |

## Project structure

```
app.py                     Streamlit entry point
ui/                        Interface only (pages, charts, transforms, session state)
src/                       Tested engine: schema mapping, cleaning, validation, Q1-Q3 analysis,
                           feedback themes, rule-based recommendations, AI module (ai_recommendations.py)
tests/                     pytest suite (cleaning, analysis, AI, dashboard)
docs/                      Schema, cleaning decisions, analysis methods, AI design
.streamlit/config.toml     Theme and safe server defaults (secrets.toml is NOT committed)
```

## Local installation

Requires Python 3.10 or later.

```bash
cd EveryMind-BuildFest
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
```

## Running the dashboard

```bash
streamlit run app.py
```

Open http://localhost:8501. Streamlit reads `.streamlit/secrets.toml` and `.streamlit/config.toml` from the **folder you run the command in**. If your secrets file is in a parent folder, either run `streamlit run EveryMind-BuildFest/app.py` from that folder, or move the secrets file into `EveryMind-BuildFest/.streamlit/`.

## Uploading survey data

1. Go to **Data Upload & Quality** and upload an `.xlsx` or `.csv` export (one row per response). If the challenge file is present at `data/BuildFest_EveryMind_Survey_Data_FULL.xlsx`, a **Load sample** button appears.
2. Review the column mapping:
   - Recognized questions, aliases, reordered columns and extra columns are handled automatically.
   - Uncertain matches (ambiguous, suggested or values that don't fit) are **never applied silently**. Choose the right field or leave the column unmapped.
   - Columns that look like personal data, such as Email or Name, are not loaded.
3. Click **Run cleaning & analysis**. No rows are deleted. Problems are flagged, and the page explains which analyses can't run because of missing questions.
4. Browse the Q1, Q2 and Q3 pages. Use **Exports & Reports** for downloads. A new upload clears all previous results, including AI drafts.

Uploads are kept only in the browser session's memory and are never written to disk.

## Optional AI recommendations (Gemini or OpenAI)

AI recommendations use **Google Gemini** (`gemini-3.5-flash`) by default, or OpenAI `gpt-5-mini` if you set `provider = "openai"`. They receive only aggregate, verified results, every recommendation must cite evidence IDs, and suggestions that fail validation are rejected. The feature is **disabled by default** because it incurs API charges. See [docs/ai_recommendations.md](docs/ai_recommendations.md).

**Local demo:** add this to `.streamlit/secrets.toml` (never commit this file):

```toml
GEMINI_API_KEY = "..."         # or OPENAI_API_KEY with provider = "openai"

[ai]
enabled = true
access = "local"      # only requests to localhost may trigger paid calls
provider = "gemini"
```

Instead of the `[ai]` table you can set environment variables, for example `EVERYMIND_AI_ENABLED=true` and `EVERYMIND_AI_ACCESS=local`.

**Deployment:** keep `enabled = false`, unless you configure Streamlit authentication (`[auth]` with an OIDC provider; see the Streamlit docs for `st.login`) together with:

```toml
[ai]
enabled = true
access = "allowlist"
allowed_emails = ["staff.member@everymind.org"]
max_calls_per_session = 3
max_calls_per_user_hour = 3   # per visitor (signed-in email, else IP)
max_calls_per_user_day = 6
cooldown_seconds = 30
max_calls_per_hour = 10       # whole server
max_calls_per_day = 30
```

Rate limits are enforced on the server for each visitor and for the whole server, so reloading the page or opening new tabs doesn't reset them.

Additional server-side controls include caching by evidence fingerprint, request caps, an output-token limit and a timeout.

## Running tests

```bash
python -m pytest
```

There are 155 tests: cleaning, analysis, AI (with the OpenAI client mocked, so there are no charges), and dashboard tests, including a headless run of every page. Most tests use the challenge dataset in `data/`, so they need that file locally.

Command-line pipelines:
- `python src/clean_data.py` cleans the data
- `python src/run_analysis.py` produces the analysis summaries
- `python src/explore_data.py` produces the EDA profile

## Deploying to Streamlit Community Cloud (not done yet)

1. Push the repository to GitHub. `.gitignore` excludes `secrets.toml`, `data/`, `outputs/` and spreadsheets, so participant data is not uploaded.
2. On share.streamlit.io, create an app with the entry point `app.py` and a Python version of 3.11 or later.
3. Under *Advanced settings → Secrets*, paste the secrets. Leave `[ai] enabled = false` unless sign-in and an allowlist are configured.
4. Optionally, restrict who can view the app in the Community Cloud sharing settings.
5. After deploying, upload a file and check every page. The sample button won't appear, because `data/` isn't deployed.

## Limitations and privacy

- **Not causal.** "Before" ratings are recalled after training, so changes are self-reported and not proof of effect. Confidence is self-assessed, not tested knowledge.
- **Mixed survey versions.** Some questions were asked only in some sessions; for example, likelihood to call 988 was answered by about a fifth of respondents. Results apply only to those asked.
- **Audience mix.** County differences can reflect who attended, for example students vs. adults. Groups with fewer than 10 respondents are suppressed, and groups with fewer than 30 are not compared. A small group doesn't mean poor access.
- **Provisional themes.** Feedback themes come from keyword rules that haven't been validated by people, and comments are never displayed.
- **Privacy-safe exports.** Exports exclude comments, raw text, exact timestamps, partner names and extra columns. The AI receives only aggregate results.
- **Documentation.** Details are in `docs/cleaning_decisions.md`, `docs/analysis_methods.md` and `docs/ai_recommendations.md`.
