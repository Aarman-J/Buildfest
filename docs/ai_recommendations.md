# AI-Drafted Recommendations: Design, Privacy and Cost Controls

The AI feature is **optional and advisory**. It drafts extra recommendations from verified aggregate results. The deterministic statistics engine (`src/analyze_q*.py`) and the rule-based recommendations (`src/recommendations.py`) remain the verified outputs. They work without AI and are always shown alongside it.

The code is in `src/ai_recommendations.py`, which has no Streamlit dependency and is fully unit-tested with a mocked client, and in `ui/page_ai.py`, which holds the button, caching, access check and display.

## 1. Model and API

| Setting | Value |
|---|---|
| Provider / model | **Gemini `gemini-3.5-flash`** (default, `provider = "gemini"`) or OpenAI `gpt-5-mini` (`provider = "openai"`). `[ai] model` overrides the model. `gemini-3.8-flash` was tested and timed out on the server side (504), so it is not the default. |
| API | Gemini: `google-genai` SDK, `client.models.generate_content` with `response_mime_type="application/json"` and `response_json_schema` from the Pydantic model, then `model_validate_json`. OpenAI: `client.responses.parse(..., text_format=...)`. |
| Output format | Pydantic model `AIRecommendationSet` as a strict JSON schema (`text_format`), with extra fields forbidden |
| Reasoning effort | `low` |
| Storage | OpenAI: `store=False`. Gemini: no storage flag. On the free tier Google may use prompts to improve its products, which is acceptable here because only aggregate results are sent. Use a paid key if that matters. |
| Timeout and retries | Client timeout plus a per-request `timeout`; `max_retries` defaults to 1 |
| Output cap | `max_output_tokens` (default 6,000, which includes reasoning tokens) |

## 2. What is sent and what is excluded

`build_ai_payload(results, quality_report)` builds a compact JSON summary of about 30 KB. It contains:

- **context:** the business question, the definition of "agree", the retrospective-rating caveat and the small-group policy.
- **evidence:** a list of findings, each with a unique ID (for example `Q1.describe_988.remaining_gap`), an evidence category, a one-sentence statement and the numbers behind it. The findings cover:
  - Q1 before/after results, individual change, remaining gaps, ceiling effects, trend coverage and sensitivity
  - Q2 experience items, recommendation, call-988 coverage and results, the strongest correlation in each family, satisfaction vs. gaps, and provisional theme percentages
  - Q3 results for **groups with at least `min_compare` (30) respondents only**, signals and the audience-mix check
  - data-quality counts
- **baseline_recommendations:** the ID, rule, priority, category and action of each rule-based recommendation, so the model can build on them instead of repeating them.

**Never sent:**
- individual responses or rows
- response IDs and timestamps
- free-text comments, raw values, extra or unrecognized columns
- the names or statistics of groups with fewer than 30 respondents (only how many such groups exist)
- the API key, which goes only in the request's authorization header

`assert_payload_safe()` checks the payload for these, and the tests confirm that no comment text, `response_id`, `_raw` fields or small-group names (for example Harris, Fairfax or Queen Anne's in the 2025-26 file) appear in it. In the dashboard, the *How the AI works* tab shows the exact payload.

## 3. Prompt

The instructions are the constant `INSTRUCTIONS` (version `PROMPT_VERSION`). In summary:

1. Every recommendation cites 1-4 evidence IDs, copied exactly.
2. Use only numbers from the cited evidence. Don't calculate or invent statistics.
3. Make no causal claims. "Before" ratings are retrospective. Use phrases such as "associated with" or "reported".
4. Don't interpret groups that are not in the evidence. Small counts are not evidence of poor access.
5. Call-988 results apply only to the respondents who were asked.
6. Feedback themes are provisional.
7. Add value beyond the baseline recommendations.
8. Return at most N recommendations, highest priority first.
9. Write in plain English, with 2-5 implementation steps, a responsible role, one measurable success indicator and one limitation.

The payload JSON is the user input.

## 4. Output validation (`validate_ai_output`)

Each recommendation must contain:
- **title**
- **priority:** high, medium or low
- **evidence_refs**
- **issue**
- **action**
- **implementation_steps**
- **responsible_role**
- **success_indicator**
- **limitations**

A recommendation is **rejected, not shown**, if any of the following is true:
- schema validation fails (a missing field, an extra field or a wrong type); in that case the whole response is rejected with a friendly error
- `evidence_refs` is empty or contains an ID that is not in the payload
- any required text field is empty, or there are not between 1 and 6 implementation steps
- a percentage, a percentage-point figure, `n =` or `rho` value quoted in the **title or issue** doesn't match (within ±0.15) a number in the cited evidence; targets in the success indicator are allowed
- the text contains causal language, for example "caused", "proves", "due to the training" or "the training increased"
- the limit on the number of recommendations has already been reached

Each accepted recommendation is shown with its cited evidence statements, labelled **AI-drafted · advisory**. Rejected suggestions are listed with their reasons.

## 5. Error handling

`generate_ai_recommendations` never raises. A timeout, authentication failure, rate limit, connection problem, missing model, rejected request, length cutoff, incomplete response, unparsed response or validation failure each becomes an `AIResult(status="error")` with a plain-language message, and nothing misleading is displayed. A missing API key returns an error **without creating a client**. The key is never logged, displayed or included in results, and the tests check this. In every case, the rule-based recommendations stay on screen.

## 6. Access control and cost protection

| Control | Implementation |
|---|---|
| Off by default | `[ai] enabled` defaults to `false`. An API key alone doesn't enable the feature. |
| Server-side access check | `access_check()`, evaluated on the server for each request. `access = "local"` serves only requests whose Host header is `localhost`, `127.0.0.1` or `::1`, for a local demo. `access = "allowlist"` (the default) requires Streamlit sign-in (`st.login`, configured in `[auth]`) **and** an email listed in `[ai] allowed_emails`. |
| Explicit trigger | Requests are sent only when someone clicks **Generate AI Recommendations**; nothing runs automatically. |
| Cache | Results are stored in the session, keyed by the evidence fingerprint (SHA-256 of the payload, model and prompt version) and tied to the `dataset_id`. A new upload or mapping invalidates them. |
| Repeated submissions | The button is disabled while a request is running. Regenerating needs an explicit checkbox. |
| Rate limits (`RateLimiter`, shared by every session in the server process) | Per visitor, keyed by a hashed signed-in email or else the hashed client IP: 3 per hour, 6 per day, and a 30 s cooldown. For the whole server: 10 per hour and 30 per day. Only 1 request runs at a time. Reloading the page or opening new tabs doesn't reset these limits. Blocked attempts aren't counted, and the user is told how long to wait. The client IP comes from Streamlit itself. `X-Forwarded-For` is used only as a fallback because it can be spoofed, and the server-wide caps still bound the total. |
| Other caps | Per-session calls (default 3), maximum recommendations (default 6, hard cap 10), output tokens (default 6,000, hard cap 16,000) and timeout (60 s). |
| Fallback | Rule-based recommendations are always available and are the primary output. |

A frontend password is **not** used as protection. On a public deployment, keep `enabled = false` unless sign-in and an allowlist are configured. Never use `access = "local"` on a public server: it doesn't protect anything there and will simply refuse all requests.

### Configuration (`.streamlit/secrets.toml`, never committed)

```toml
OPENAI_API_KEY = "sk-..."

[ai]
enabled = true            # default false
access = "local"          # local demo only; use "allowlist" for deployment
# allowed_emails = ["manager@everymind.org"]
# model = "gpt-5-mini"
# max_recommendations = 6
# max_output_tokens = 6000
# timeout_seconds = 60
# max_calls_per_session = 3
# max_calls_per_user_hour = 3      # per visitor (email or IP)
# max_calls_per_user_day = 6
# cooldown_seconds = 30
# max_calls_per_hour = 10          # whole server
# max_calls_per_day = 30
# max_concurrent = 1
```

The same settings can be given as environment variables, such as `EVERYMIND_AI_ENABLED=true` and `EVERYMIND_AI_ACCESS=local`. The key can be `OPENAI_API_KEY` at the top level, `[openai] api_key`, or the `OPENAI_API_KEY` environment variable.

## 7. Testing

`tests/test_ai.py` covers:
- payload privacy, and the safety checker catching each kind of violation
- the fingerprint changing with the dataset
- accepting a valid recommendation, and rejecting unknown or empty IDs, invented statistics, causal claims, empty fields, missing steps and recommendations over the limit
- malformed schemas
- the shape of the request: model, `text_format`, token cap, `store=False` and timeout
- no call when the feature is disabled, and no client created without a key
- friendly handling of timeouts, rate limits and generic errors, and of incomplete or unparsed responses
- the key never appearing in results
- settings defaults and precedence, the access-check matrix and the daily limiter

`tests/test_dashboard.py` runs the whole app headless with the OpenAI client patched to fail if it is ever created. It checks that every page renders and that no generate button appears while AI is disabled.
