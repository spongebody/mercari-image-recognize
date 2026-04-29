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

Image analysis now runs a fast category path and a product-data path in parallel:

1. Fast classification path:
   - Uses only the first uploaded image with `FAST_CLASSIFICATION_SYSTEM_PROMPT` + `FAST_CLASSIFICATION_USER_PROMPT`.
   - Extracts only top-level category, title, and simple description.
   - Calls `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE` to choose up to 3 CSV-backed category paths with confidence scores.
   - The first API response returns these categories plus a `job_id` as soon as this path completes.
2. Product data path:
   - Uses all uploaded images with `PRODUCT_DATA_SYSTEM_PROMPT` + `PRODUCT_DATA_USER_PROMPT`.
   - Generates title, structured description, and brand information.
   - Does not generate `tax_excluded`, `tax_included`, or `prices`; price entry is handled manually by the client.
   - Uses `PRODUCT_DATA_MODEL` by default (`google/gemini-2.5-flash` unless configured).
   - In parallel, a smaller fallback model `PRODUCT_DATA_FALLBACK_MODEL` (default `openai/gpt-4o-mini`) runs the same prompt. If the primary model has not produced a result within `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS` (default `10`), the polling endpoint returns the fallback result so callers always get bounded-latency data. The completed payload includes `product_data_source` (`"primary"` or `"fallback"`) so clients can tell which model produced the data.

If the product-data path finishes before the first response is sent, the response is returned as `status=completed` with the full product data. Otherwise the client polls `GET /api/v1/mercari/image/analyze/{job_id}` until it returns `status=completed`. In-memory jobs live only in the current API process.

The image analysis response includes backend timings in milliseconds: `timings.classification_ms` for the first API response's category path, `timings.product_data_ms` for the polling-side product data generation path once complete, and `timings.total_ms` for the wall-clock duration. Because the two paths run in parallel, the completed `total_ms ≈ max(classification_ms, product_data_ms)` rather than their sum. The response also includes `image_processing`, which lists each uploaded image's compression status (whether it was compressed and the original/processed byte sizes).

## Prompt overview

All prompts live in `app/llm/prompts.py`.

- Fast image classification: `FAST_CLASSIFICATION_SYSTEM_PROMPT` + `FAST_CLASSIFICATION_USER_PROMPT`
  - Uses the first image to generate only the minimal classification evidence, without brand or price fields.
- Product data generation: `PRODUCT_DATA_SYSTEM_PROMPT` + `PRODUCT_DATA_USER_PROMPT`
  - Uses all images to generate title, description, and brand fields without price fields.
- Legacy/title fallback image recognition: `VISION_SYSTEM_PROMPT_WITH_PRICE` + `VISION_USER_PROMPT_WITH_PRICE`
  - Still used by title fallback image classification.
- Title-only category: `PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT` + `PRODUCT_TITLE_CATEGORY_USER_PROMPT`
  - Classifies a title to a top-level category.
- Category selection: `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE`
  - Chooses best category path and up to 2 alternatives from the candidate list.

## Environment variables

Booleans accept `1`, `true`, `yes`, or `on`.

- `OPENROUTER_API_KEY` (required): OpenRouter API key.
- `VISION_MODEL` (default: empty): model for image understanding.
- `CATEGORY_MODEL` (default: empty): model for category selection.
- `PRODUCT_DATA_MODEL` (default: `google/gemini-2.5-flash`): model for all-image product data generation.
- `PRODUCT_DATA_FALLBACK_MODEL` (default: `openai/gpt-4o-mini`): smaller model that runs in parallel with the primary product-data call. Set to empty to disable the fallback.
- `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS` (default: `10`): if the primary product-data call has not completed after this many seconds, the polling endpoint returns the fallback result instead. Accepts decimals (e.g., `7.5`).
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
- `PRODUCT_DATA_MODEL`
- `PRODUCT_DATA_FALLBACK_MODEL`
- `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS`
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
