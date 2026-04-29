# Mercari Image Analyzer

## Quick start

1) Install dependencies:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Configure `.env` (loaded automatically by `python-dotenv` in `app/config.py`).

3) Run the API:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The same FastAPI process also serves the runtime configuration page at `/config`.
Changes saved from that page are written to `.env` and applied to subsequent API
requests immediately.

## Image analysis flow

Image analysis uses one vision LLM call followed by one category selection call:

1. `VISION_SYSTEM_PROMPT_WITH_PRICE` + `VISION_USER_PROMPT_WITH_PRICE`
   - Generates title, structured description, brand, top-level category, direct image price fields, and optional preliminary JPY price predictions.
   - If a visible product price is found in the image, `tax_excluded` / `tax_included` are returned and `prices` is empty.
   - If no visible product price is found, direct price fields are `null` and `prices` contains 3 inferred condition-based values.
   - Pricing is image-only. The app does not use online search or a separate price model.
2. `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE`
   - Chooses the best category path from local CSV-backed candidates under the detected top-level category.

The image analysis response includes backend timings in milliseconds: `timings.total_ms` for service-side analysis time, `timings.vision_ms` for the image recognition call, and `timings.category_ms` for the category selection call. The test page displays these backend timings instead of browser-measured request time.

## Prompt overview

All prompts live in `app/llm/prompts.py`.

- Image recognition with pricing: `VISION_SYSTEM_PROMPT_WITH_PRICE` + `VISION_USER_PROMPT_WITH_PRICE`
  - Generates title, description, top-level category, brand, visible image prices, and fallback inferred price points.
- Title-only category: `PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT` + `PRODUCT_TITLE_CATEGORY_USER_PROMPT`
  - Classifies a title to a top-level category.
- Category selection: `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE`
  - Chooses best category path and up to 2 alternatives from the candidate list.

## Environment variables

Booleans accept `1`, `true`, `yes`, or `on`.

- `OPENROUTER_API_KEY` (required): OpenRouter API key.
- `VISION_MODEL` (default: empty): model for image understanding.
- `CATEGORY_MODEL` (default: empty): model for category selection.
- `BRAND_CSV_PATH` (default: `data/mercari_brand.csv`): brand data CSV path.
- `CATEGORY_CSV_PATH` (default: `data/category_rakuten.csv`): category data CSV path.
- `OPENROUTER_BASE_URL` (default: `https://openrouter.ai/api/v1/chat/completions`): API base URL.
- `OPENROUTER_REFERER` (default: empty): optional referer header.
- `OPENROUTER_APP_NAME` (default: `mercari-image-backend`): app name header.
- `REQUEST_TIMEOUT` (default: `60`): per-call OpenRouter request timeout in seconds.
- `ENABLE_DEBUG` (default: `true`): allow debug response fields.
- `MAX_IMAGE_BYTES` (default: `5242880`): max upload size in bytes.
- `IMAGE_COMPRESSION_THRESHOLD_MB` (default: `1`): backend compresses any single uploaded image larger than this size in MB before sending it to the vision model. Set `0` to disable backend compression.
- `LOG_LLM_RAW` (default: `false`): write raw LLM logs under `logs/`.
- `LOG_REQUESTS` (default: `true`): write request logs under `logs/requests/`.
- `LOG_REQUESTS_RETENTION_DAYS` (default: `7`): request log retention days.
- `LOG_REQUESTS_MAX_FILES` (default: `1000`): cap on request log files.
- `VISION_FALLBACK_MODELS` (default: built-in 7-model list): comma-separated OpenRouter model
  ids tried in order if the primary `VISION_MODEL` exhausts its retries.
- `CATEGORY_FALLBACK_MODELS` (default: built-in 7-model list): comma-separated OpenRouter
  model ids tried in order if the primary `CATEGORY_MODEL` exhausts its retries.
- `MODEL_CALL_MAX_RETRIES` (default: `3`): number of retries on the primary model before the
  caller starts walking the fallback list. The total primary attempt count is
  `MODEL_CALL_MAX_RETRIES + 1`. Applies to vision, category, and title-category stages.
- `MODEL_CALL_TOTAL_BUDGET_SECONDS` (default: `120`): hard cap on the total wall-clock time
  one stage may spend across all retries and fallbacks. Each per-attempt timeout is reduced
  to `min(REQUEST_TIMEOUT, remaining budget)`. The vision stage and the category stage have
  independent budgets.
- `REASONING_ENABLED` (optional): enable or disable reasoning globally for all OpenRouter requests.
- `REASONING_EFFORT` (optional): reasoning effort level, one of `minimal`, `low`, `medium`, `high`, `xhigh`, `none`.
- `REASONING_MAX_TOKENS` (optional): reasoning token budget hint sent globally to OpenRouter.
- `REASONING_SUMMARY` (optional): reasoning summary level, one of `auto`, `concise`, `detailed`.

Every LLM stage retries the primary model up to `MODEL_CALL_MAX_RETRIES + 1` times with
exponential backoff (0.2s, 0.4s, 0.8s, capped at 1.5s and clamped by remaining budget),
then walks the fallback list (one attempt each, no inter-model backoff). Both OpenRouter
request errors and JSON parse failures feed the same retry/fallback loop. Once every
attempt has failed the stage raises an error that the API surfaces as a structured 502
response (see `API.md`). The deprecated `CATEGORY_LLM_RETRY_ENABLED` /
`CATEGORY_LLM_MAX_RETRIES` settings have been removed.

## Runtime configuration page

Open `http://<host>:<port>/config` to edit the common API settings without
manually changing `.env`. The page can update:

- `VISION_MODEL`
- `CATEGORY_MODEL`
- `SHOWCASE_MODEL`
- `LOG_LLM_RAW`
- `LOG_REQUESTS`
- `ENABLE_DEBUG`
- `IMAGE_COMPRESSION_THRESHOLD_MB`
- `REQUEST_TIMEOUT`
- `MODEL_CALL_MAX_RETRIES`
- `MODEL_CALL_TOTAL_BUDGET_SECONDS`
- `VISION_FALLBACK_MODELS`
- `CATEGORY_FALLBACK_MODELS`

`OPENROUTER_API_KEY` is intentionally not exposed on the page.

Runtime updates are applied inside the current API process. The provided systemd
command runs a single uvicorn worker, which matches this behavior. If you deploy
multiple workers later, restart or reload all workers after changing config.
