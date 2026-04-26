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

## Image analysis flow

Image analysis uses one vision LLM call followed by one category selection call:

1. `VISION_SYSTEM_PROMPT_WITH_PRICE` + `VISION_USER_PROMPT_WITH_PRICE`
   - Generates title, structured description, brand, top-level category, direct image price fields, and optional preliminary JPY price predictions.
   - If a visible product price is found in the image, `tax_excluded` / `tax_included` are returned and `prices` is empty.
   - If no visible product price is found, direct price fields are `null` and `prices` contains 3 inferred condition-based values.
   - Pricing is image-only. The app does not use online search or a separate price model.
2. `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE`
   - Chooses the best category path from local CSV-backed candidates under the detected top-level category.

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
- `REQUEST_TIMEOUT` (default: `60`): OpenRouter request timeout in seconds.
- `ENABLE_DEBUG` (default: `true`): allow debug response fields.
- `MAX_IMAGE_BYTES` (default: `5242880`): max upload size in bytes.
- `LOG_LLM_RAW` (default: `false`): write raw LLM logs under `logs/`.
- `LOG_REQUESTS` (default: `true`): write request logs under `logs/requests/`.
- `LOG_REQUESTS_RETENTION_DAYS` (default: `7`): request log retention days.
- `LOG_REQUESTS_MAX_FILES` (default: `1000`): cap on request log files.
- `CATEGORY_LLM_RETRY_ENABLED` (default: `0`): enable retries for category selection calls.
- `CATEGORY_LLM_MAX_RETRIES` (default: `1`): number of additional attempts when retries are enabled.
- `REASONING_ENABLED` (optional): enable or disable reasoning globally for all OpenRouter requests.
- `REASONING_EFFORT` (optional): reasoning effort level, one of `minimal`, `low`, `medium`, `high`, `xhigh`, `none`.
- `REASONING_MAX_TOKENS` (optional): reasoning token budget hint sent globally to OpenRouter.
- `REASONING_SUMMARY` (optional): reasoning summary level, one of `auto`, `concise`, `detailed`.

Category retries apply only to the category selection step and cover OpenRouter request errors and JSON parsing failures, using exponential backoff (0.2s, 0.4s, 0.8s, capped).
