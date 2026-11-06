# EveryMind 988 Training Insights

A Streamlit dashboard for the University of Maryland BuildFest 2026 Nonprofit Data Challenge. EveryMind staff upload their *Understanding 988* training feedback survey as an Excel or CSV file. The dashboard validates and cleans it in memory, then answers:

> **Main question:** How can EveryMind use its 988 training feedback data to develop an actionable solution for evaluating training effectiveness, identifying gaps across communities, and continuously improving future trainings?

| Page | What it shows |
|---|---|
| Executive Overview | Main question, 4 headline numbers, one before/after chart, key takeaways, 4 priority actions |
| Explore Findings | Tabs for Q1 (effectiveness), Q2 (experiences and relationships) and Q3 (community differences), with technical detail under "Advanced details" |
| AI Insights & Recommendations | 4 verified priority recommendations, all underlying rules, optional AI-drafted suggestions (advisory, validated) |
| Upload & Data Quality | Upload, column mapping, automatic cleaning, quality warnings, aggregate-only downloads |

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

1. Go to **Upload & Data Quality** and upload an `.xlsx` or `.csv` export (one row per response). If the challenge file is present at `data/BuildFest_EveryMind_Survey_Data_FULL.xlsx`, a **Load sample** button appears.
2. Review the column mapping:
   - Recognized questions, aliases, reordered columns and extra columns are handled automatically.
   - Uncertain matches (ambiguous, suggested or values that don't fit) are **never applied silently**. Choose the right field or leave the column unmapped.
   - Columns that look like personal data, such as Email or Name, are not loaded.
3. Click **Run cleaning & analysis**. No rows are deleted. Problems are flagged, and the page explains which analyses can't run because of missing questions.
4. Results update automatically when no column needs confirmation. Browse **Explore Findings** (Q1–Q3 tabs) and download aggregate reports at the bottom of the upload page. A new upload clears all previous results, including AI drafts.

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

## Deploying to Streamlit Community Cloud

1. Push this repository to GitHub. `.gitignore` excludes `secrets.toml`, `outputs/` and any other data files. Only the challenge sample spreadsheet is committed.
2. Go to https://share.streamlit.io, click **Create app**, choose this repo, branch `main`, main file `app.py`. Under **Advanced settings**, choose Python 3.12.
3. In **Advanced settings → Secrets**, paste the contents of [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) with your real key. This is server-side and never visible to visitors.
4. Click **Deploy**. The BuildFest 2025–26 sample (`data/BuildFest_EveryMind_Survey_Data_FULL.xlsx`) is committed, so the hosted app auto-loads it as a demo. Visitors can still upload their own survey on *Upload & Data Quality*. Set `[demo] autoload = false` to turn this off.

**AI access on the public site.** `access = "public"` lets any visitor click *Generate AI Recommendations*. Spending is bounded server-side:
- 3 calls per visitor per hour and 6 per day, with a 30 s cooldown
- 10 calls per hour and 30 per day for the whole site
- 1 call at a time, and a capped output size

At Gemini Flash prices that's only a few cents a day at the maximum. Counters reset if the app restarts, so also set a hard cap at the provider: in Google Cloud Console → *APIs & Services → Generative Language API → Quotas*, lower "requests per day". For a private demo, use `access = "allowlist"` with Streamlit sign-in instead, or set `enabled = false`.

To change secrets later, open the app → **⋮ → Settings → Secrets**. The app restarts automatically.

## Limitations and privacy

- **Not causal.** "Before" ratings are recalled after training, so changes are self-reported and not proof of effect. Confidence is self-assessed, not tested knowledge.
- **Mixed survey versions.** Some questions were asked only in some sessions; for example, likelihood to call 988 was answered by about a fifth of respondents. Results apply only to those asked.
- **Audience mix.** County differences can reflect who attended, for example students vs. adults. Groups with fewer than 10 respondents are suppressed, and groups with fewer than 30 are not compared. A small group doesn't mean poor access.
- **Provisional themes.** Feedback themes come from keyword rules that haven't been validated by people, and comments are never displayed.
- **Privacy-safe exports.** Exports exclude comments, raw text, exact timestamps, partner names and extra columns. The AI receives only aggregate results.
- **Documentation.** Details are in `docs/cleaning_decisions.md`, `docs/analysis_methods.md` and `docs/ai_recommendations.md`.
