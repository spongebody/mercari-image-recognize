# Error handling redesign for image recognition / category pipeline

Date: 2026-04-29

## Background

### Current pipeline

```
web/index.html  →  POST /api/v1/mercari/image/analyze   (or /title/analyze)
                →  main.analyze_image / main.analyze_title
                →  MercariAnalyzer.analyze (run_in_threadpool)
                     ├─ _call_vision_llm           → OpenRouterClient.chat (vision_model)
                     ├─ _call_title_category_llm   → OpenRouterClient.chat (category_model)
                     └─ _choose_categories         → OpenRouterClient.chat (category_model)
                                                     with optional retries
                                                     (CATEGORY_LLM_RETRY_ENABLED, default off)
```

`OpenRouterClient.chat` raises `LLMRequestError` on any HTTP / network / response-shape failure.
JSON parsing happens inside `_call_vision_llm` / `_call_title_category_llm` / `_choose_categories`
via `safe_json_loads`; on failure those methods raise `BadRequestError("Failed to parse ... JSON.")`.

`main.py` maps:

- `BadRequestError` → 400 (`detail: str`)
- `LLMRequestError` → 502 (`detail: str`)
- other `Exception` → 500

The web frontend reads `await resp.text()`, then `JSON.parse(text)`. On parse failure it sets
`payload = { error: "JSON parse failed", raw: text }`. The image-flow result renderer
(`formatResultData`) only checks `payload.error` and ignores `payload.detail`, so any backend
error returned via FastAPI's `HTTPException(detail=...)` is silently dropped on the image flow.

### Reported issues

1. The web page often shows "JSON parse failed" even though the network panel shows that the
   actual failure was an OpenRouter request error (5xx, timeout, missing API key, etc.). Users
   cannot tell which step failed: the request, or JSON parsing.
2. Retry is only available on the category step (and is disabled by default). Vision and
   title-category steps do not retry. The configured `REQUEST_TIMEOUT` is a per-call upper
   bound for `requests.post(..., timeout=...)` — it is NOT a delay between requests, and is
   easy to confuse with the retry backoff.
3. There is no fallback to alternate models when a chosen model is broken or unstable.

## Goals

- The user (and operators) can tell which stage failed and whether each failure was a request
  failure or a parse failure.
- Each stage automatically retries the primary model up to 3 times, then falls back through a
  configurable list of OpenRouter models (one attempt each), before returning a real error.
- Vision model and category model can configure their fallback lists independently. Default
  list is identical for both.
- JSON parsing is robust: tolerates plain JSON, ```` ```json ... ``` ```` markdown fences,
  generic ```` ``` ... ``` ```` fences, and leading/trailing prose around `{...}` or `[...]`.

## Non-goals

- Per-attempt different `temperature` / `max_tokens` (fallbacks reuse the primary's params).
- Differentiated retry by HTTP status (all `LLMRequestError`s are treated equally).
- Concurrent fan-out across multiple fallback models.
- Per-image / per-batch shared retry budget.
- Persisting attempts to an external monitoring system.

## Architecture

A new mediator layer **`ResilientLLMCaller`** (`app/llm/resilient.py`) absorbs the
"call LLM + parse JSON + retry + fallback" loop. All three stages
(`vision`, `category`, `title_category`) call into it. `OpenRouterClient.chat`
remains a single-shot HTTP wrapper.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  main.py (FastAPI)                                                           │
│   catches LLMAllAttemptsFailedError → 502 with structured detail dict        │
│   catches BadRequestError           → 400 with string detail                 │
│   catches Exception                 → 500 with "Internal server error."     │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  app/service.py  MercariAnalyzer                                             │
│   _call_vision_llm / _call_title_category_llm / _choose_categories           │
│     build messages, then delegate to ResilientCaller.call_and_parse(...)     │
│     existing in-method retry block in _choose_categories is removed          │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  app/llm/resilient.py (NEW)                                                  │
│   ResilientCaller.call_and_parse(stage, primary, fallbacks, ...)             │
│     - primary is called up to MODEL_CALL_MAX_RETRIES + 1 times (default 4:   │
│       one initial call plus three retries)                                   │
│     - exponential backoff between same-model retries (0.2/0.4/0.8s, ≤1.5s)   │
│     - on retries exhausted, walks fallback list (each model once, no backoff │
│       between model switches; primary is removed from fallbacks if listed)   │
│     - per-attempt timeout = min(REQUEST_TIMEOUT, remaining stage budget)     │
│     - aborts when remaining stage budget < 1s                                │
│     - parse_failed and request_failed both feed the same retry/fallback loop │
│     - on success returns (parsed_json, raw_response, attempts)               │
│     - on full exhaustion raises LLMAllAttemptsFailedError(stage, attempts)   │
└────────────────────────────────────┬─────────────────────────────────────────┘
                                     ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  app/llm/client.py  OpenRouterClient.chat                                    │
│   single-shot HTTP request; new optional `timeout` arg overrides self.timeout│
│   continues to raise LLMRequestError                                         │
│  app/llm/json_parser.py (NEW) parse_llm_json — robust JSON parser            │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Key design choices

- One independent budget per stage (`vision`, `category`, `title_category` each get
  `MODEL_CALL_TOTAL_BUDGET_SECONDS`). Stages do not share budget, so a slow vision call does
  not eat the category budget.
- Backoff applies only between consecutive attempts of the same model. Switching to the next
  fallback model resets and starts the new model immediately, with no wait.
- The current `CATEGORY_LLM_RETRY_ENABLED` and `CATEGORY_LLM_MAX_RETRIES` settings are
  superseded and removed from `runtime_config.CONFIG_FIELDS`. The settings are no longer read
  from `.env`. Old `.env` files containing them continue to load fine; the values are simply
  ignored. Documentation is updated accordingly.

## Components and file changes

### New files

#### `app/llm/json_parser.py`

```python
def parse_llm_json(raw: str) -> Any:
    """Robust LLM JSON parser. Tries (in order):
       1) json.loads on stripped text
       2) strip ```json ... ``` / ``` ... ``` markdown fence
       3) substring from first '{' to last '}'
       4) substring from first '[' to last ']'
       Raises LLMParseError with a 200-char excerpt on failure.
    """
```

`utils.safe_json_loads` is rewritten as a thin delegate to `parse_llm_json` so existing
imports keep working.

#### `app/llm/resilient.py`

```python
@dataclass
class AttemptRecord:
    model: str
    attempt: int                  # per-model attempt index (1..N)
    attempt_global: int           # global attempt index across all models (1..)
    error_kind: str               # "request_failed" | "parse_failed" | "budget_exhausted" | "ok"
    message: str
    latency_ms: float
    status_code: Optional[int]    # HTTP status from OpenRouter (best-effort)

class ResilientCaller:
    def __init__(self, client: OpenRouterClient, settings: Settings) -> None: ...

    def call_and_parse(
        self,
        *,
        stage: str,                    # "vision" | "category" | "title_category"
        primary_model: str,
        fallback_models: List[str],
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        max_retries: int,              # primary model retries, default 3
        total_budget_s: float,
        per_attempt_timeout_s: float,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        """Returns (parsed_json, raw_response, attempts).
           Raises LLMAllAttemptsFailedError(stage, attempts) on exhaustion.
        """
```

### Modified files

#### `app/errors.py`

Existing classes preserved. Two new classes:

```python
class LLMParseError(Exception):
    """raised by parse_llm_json on unrecoverable parse failure."""

class LLMAllAttemptsFailedError(Exception):
    def __init__(self, stage: str, attempts: List[AttemptRecord]):
        self.stage = stage
        self.attempts = attempts
        super().__init__(self._summary())

    def _summary(self) -> str: ...   # e.g. "vision: 8/8 attempts failed"
```

#### `app/llm/client.py`

`chat` gets an optional `timeout` parameter:

```python
def chat(
    self,
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.2,
    max_tokens: int = 1024,
    timeout: Optional[float] = None,
) -> Tuple[str, Dict[str, Any]]:
    effective_timeout = timeout if timeout is not None else self.timeout
    # ...self.session.post(..., timeout=effective_timeout)
```

`chat` continues to raise `LLMRequestError`. It does not parse business JSON — that remains
the caller's responsibility (now via `parse_llm_json`).

#### `app/config.py`

New `Settings` fields:

```python
vision_fallback_models: List[str]   = field(default_factory=lambda: _env_str_list(
    "VISION_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS))
category_fallback_models: List[str] = field(default_factory=lambda: _env_str_list(
    "CATEGORY_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS))
model_call_max_retries: int         = _env_int_min("MODEL_CALL_MAX_RETRIES", 3, 0)
model_call_total_budget_seconds: int = _env_int_min(
    "MODEL_CALL_TOTAL_BUDGET_SECONDS", 120, 1)
```

`_env_str_list(name, default)` reads a comma-separated env value and returns a list of
trimmed non-empty entries. If env var is unset or empty, it returns `list(default)`.

`DEFAULT_FALLBACK_MODELS` is added in `app/constants.py`:

```python
DEFAULT_FALLBACK_MODELS = (
    "google/gemini-3-flash-preview",
    "google/gemini-2.5-flash",
    "google/gemini-3.1-pro-preview",
    "openai/gpt-4o-mini",
    "openai/gpt-5-mini",
    "openai/gpt-5.4",
    "openai/gpt-5.5",
)
```

The deprecated fields `category_llm_retry_enabled` and `category_llm_max_retries` are
removed from the `Settings` dataclass.

#### `app/runtime_config.py`

`ConfigField.value_type` adds `"multiline_str"`. New helpers:

- `_parse_multiline_str(value)` — accepts either `list[str]` or `\n`-delimited string,
  returns `list[str]` after stripping and dropping empty entries.
- `_serialize_multiline_str(value)` — joins `list[str]` with `,` for `.env`.
- `get_public_config` returns `list[str]` for multiline_str fields (not the `,` form).

`CONFIG_FIELDS` changes:

- Remove: `CATEGORY_LLM_RETRY_ENABLED`, `CATEGORY_LLM_MAX_RETRIES`.
- Add:
  - `VISION_FALLBACK_MODELS` (multiline_str)
  - `CATEGORY_FALLBACK_MODELS` (multiline_str)
  - `MODEL_CALL_MAX_RETRIES` (int, min_value=0)
  - `MODEL_CALL_TOTAL_BUDGET_SECONDS` (int, min_value=1)

#### `app/service.py`

- `MercariAnalyzer.__init__` constructs a `ResilientCaller` per client (`vision_resilient`,
  `category_resilient`).
- `_call_vision_llm`: builds messages (unchanged), then calls
  `vision_resilient.call_and_parse(stage="vision", primary_model=..., fallback_models=...,
  ...)`. The existing `_log_raw("vision_content", content)` and
  `_log_raw("vision_raw_response", raw_response)` calls are kept and run after a successful
  attempt.
- `_call_title_category_llm`: same pattern with `stage="title_category"` and
  `category_resilient`.
- `_choose_categories`: removes the entire `attempts/sleep` block. Calls
  `category_resilient.call_and_parse(stage="category", ...)` once, then continues with the
  existing post-processing of `parsed`.
- `analyze` and `analyze_title` accumulate per-stage attempts in a local dict; when
  `debug=true` they are exposed in `result["_debug"]["attempts"] = {"vision": [...],
  "category": [...]}`.
- `analyze_title`'s existing `try/except` for `LLMRequestError` is changed to
  `LLMAllAttemptsFailedError`. The image-fallback flow is preserved unchanged.

#### `main.py`

```python
except BadRequestError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
except LLMAllAttemptsFailedError as exc:
    raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
except Exception as exc:
    raise HTTPException(status_code=500, detail="Internal server error.") from exc
```

`_format_attempts_error(exc)` returns a `dict` with the schema below.

#### `web/index.html`

- After `JSON.parse(text)`, when `!resp.ok` and `payload.detail` is an object with
  `kind === "all_attempts_failed"`, call `formatStructuredError(payload.detail)` to render:
  - One header line summarizing stage and attempt count.
  - A collapsed "show attempt details" toggle that lists each `AttemptRecord` with
    `[NN]  model  attempt N/M  ❌ kind  status_code  latency_ms`.
- Existing image-flow bug: `formatResultData` only reads `payload.error`. After this change,
  the error dispatch happens before `formatResultData`, so the bug is no longer reachable on
  the failure path. For belt-and-suspenders, `formatResultData` also gains a fallback that
  reads `payload.detail` (string form) when `payload.error` is missing.
- Title flow: replace `data.detail || data.error || text` with the same dispatch — if
  `data.detail` is an object, render structured; else render string.

#### `web/config.html`

- Render `multiline_str` fields as `<textarea>` rather than `<input type="text">`. The page
  currently fetches `/api/v1/config` (which already returns `list[str]` for these fields per
  the public-config schema) and joins with `\n` for display; on save it splits on `\n`,
  trims, drops empty lines, and PUTs `string[]`.
- Remove cards for `CATEGORY_LLM_RETRY_ENABLED` and `CATEGORY_LLM_MAX_RETRIES`.
- Add cards for `MODEL_CALL_MAX_RETRIES`, `MODEL_CALL_TOTAL_BUDGET_SECONDS`,
  `VISION_FALLBACK_MODELS`, `CATEGORY_FALLBACK_MODELS`.

## Algorithm: `ResilientCaller.call_and_parse`

```python
def call_and_parse(self, *, stage, primary_model, fallback_models,
                   messages, temperature, max_tokens,
                   max_retries, total_budget_s, per_attempt_timeout_s):

    if not primary_model:
        raise LLMAllAttemptsFailedError(stage, attempts=[])

    deadline   = time.monotonic() + total_budget_s
    attempts   = []
    backoff_s  = [0.2, 0.4, 0.8]
    global_idx = 0

    schedule = [(primary_model, max_retries + 1)] + [
        (m, 1) for m in fallback_models if m and m != primary_model
    ]

    for model, n_attempts in schedule:
        for attempt in range(1, n_attempts + 1):
            global_idx += 1
            remaining = deadline - time.monotonic()
            if remaining <= 1.0:
                attempts.append(AttemptRecord(
                    model=model, attempt=attempt, attempt_global=global_idx,
                    error_kind="budget_exhausted",
                    message=f"Stage budget exhausted before attempt (remaining {remaining:.2f}s).",
                    latency_ms=0.0, status_code=None,
                ))
                raise LLMAllAttemptsFailedError(stage, attempts)

            timeout = min(per_attempt_timeout_s, remaining)
            t0 = time.monotonic()
            try:
                content, raw = self.client.chat(
                    model=model, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                    timeout=timeout,
                )
            except LLMRequestError as exc:
                attempts.append(AttemptRecord(
                    model=model, attempt=attempt, attempt_global=global_idx,
                    error_kind="request_failed",
                    message=str(exc),
                    latency_ms=_ms_since(t0),
                    status_code=_extract_status_code(exc),
                ))
                if attempt < n_attempts:
                    _sleep_capped(backoff_s, attempt - 1, deadline)
                continue

            try:
                parsed = parse_llm_json(content)
                if not isinstance(parsed, dict):
                    raise LLMParseError("LLM did not return a JSON object.")
            except LLMParseError as exc:
                attempts.append(AttemptRecord(
                    model=model, attempt=attempt, attempt_global=global_idx,
                    error_kind="parse_failed",
                    message=str(exc),
                    latency_ms=_ms_since(t0),
                    status_code=200,
                ))
                if attempt < n_attempts:
                    _sleep_capped(backoff_s, attempt - 1, deadline)
                continue

            attempts.append(AttemptRecord(
                model=model, attempt=attempt, attempt_global=global_idx,
                error_kind="ok", message="",
                latency_ms=_ms_since(t0), status_code=200,
            ))
            return parsed, raw, attempts

    raise LLMAllAttemptsFailedError(stage, attempts)


def _sleep_capped(backoff_s, idx, deadline):
    delay = min(backoff_s[min(idx, len(backoff_s) - 1)], 1.5)
    delay = min(delay, max(0.0, deadline - time.monotonic() - 0.1))
    if delay > 0:
        time.sleep(delay)


def _extract_status_code(exc: LLMRequestError) -> Optional[int]:
    """Best-effort extraction of HTTP status from messages such as
       'OpenRouter returned 503: ...'. Returns None when no match.
    """
    m = re.match(r"OpenRouter returned (\d{3})", str(exc))
    return int(m.group(1)) if m else None
```

## Algorithm: `parse_llm_json`

```python
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)

def parse_llm_json(raw: str) -> Any:
    if not isinstance(raw, str) or not raw.strip():
        raise LLMParseError("LLM returned empty content.")

    s = raw.strip()
    candidates: List[str] = [s]

    fence = _FENCE_RE.search(s)
    if fence:
        candidates.append(fence.group(1).strip())

    if "{" in s and "}" in s:
        candidates.append(s[s.find("{"): s.rfind("}") + 1])

    if "[" in s and "]" in s:
        candidates.append(s[s.find("["): s.rfind("]") + 1])

    last_err: Optional[Exception] = None
    for c in candidates:
        if not c:
            continue
        try:
            return json.loads(c)
        except json.JSONDecodeError as exc:
            last_err = exc

    excerpt = (raw[:200] + "…") if len(raw) > 200 else raw
    raise LLMParseError(f"JSON decode failed: {last_err}. excerpt={excerpt!r}")
```

## Error response schema (HTTP)

When a stage exhausts all attempts, `main.py` returns HTTP 502 with body:

```json
{
  "detail": {
    "message": "vision 阶段全部尝试失败（8 次）",
    "stage": "vision",
    "kind": "all_attempts_failed",
    "attempts": [
      {
        "model": "google/gemini-3-flash-preview",
        "attempt": 1,
        "attempt_global": 1,
        "error_kind": "request_failed",
        "message": "OpenRouter returned 503: ...",
        "status_code": 503,
        "latency_ms": 12034.5
      },
      {
        "model": "google/gemini-3-flash-preview",
        "attempt": 2,
        "attempt_global": 2,
        "error_kind": "parse_failed",
        "message": "JSON decode failed: ...",
        "status_code": 200,
        "latency_ms": 8123.7
      }
    ]
  }
}
```

Field constraints:

- `stage ∈ {"vision", "category", "title_category"}`.
- `kind` is currently always `"all_attempts_failed"`. Reserved for future variants.
- `attempts[].error_kind ∈ {"request_failed", "parse_failed", "budget_exhausted", "ok"}`.
  In an error response, the array contains only failure entries (`"ok"` cannot appear since
  a single success returns 200).
- `attempts[].status_code` is an integer or `null`. For `parse_failed` it is always 200.
  For `request_failed` it is best-effort parsed from the `OpenRouterClient` error message.

When `debug=true` and the stage succeeds, the same shape (with `error_kind: "ok"` entries
permitted) is returned under `result._debug.attempts.<stage>`.

## HTTP status code mapping

| Trigger | Status | `detail` shape |
|---------|--------|---------------|
| `BadRequestError` (input validation: empty image, unsupported language, content-type, etc.) | 400 | string |
| `LLMAllAttemptsFailedError` (any stage) | 502 | dict (schema above) |
| Other uncaught `Exception` | 500 | `"Internal server error."` |

`title` flow keeps its existing internal "title classification failed → fall back to
image_url" behavior. The catch in `analyze_title` switches from `LLMRequestError` to
`LLMAllAttemptsFailedError`. Only when the image fallback also fails (or is unavailable)
does the request bubble up as 502.

## Edge cases

| # | Scenario | Behavior |
|---|----------|----------|
| 1 | `primary_model` appears inside `fallback_models` | Deduplicated; primary runs only its `max_retries+1` attempts |
| 2 | `fallback_models` is empty | Only the primary's retries run; on full failure raise |
| 3 | `primary_model` is empty string | Raise `LLMAllAttemptsFailedError` immediately with empty `attempts` |
| 4 | Budget already < 1s before first attempt | One `budget_exhausted` record, then raise |
| 5 | OpenRouter 200 but body not valid JSON (handled by client) | Client raises `LLMRequestError`; treated as `request_failed` |
| 6 | OpenRouter 200 + empty `content` string | `parse_llm_json` raises `LLMParseError("LLM returned empty content.")` → `parse_failed` |
| 7 | LLM returns ```` ```json [ ... ] ``` ```` (bare array) | `parse_llm_json` succeeds with a list; caller's `isinstance(parsed, dict)` check fails → `parse_failed` → continue |
| 8 | Same model fails 3 attempts (parse), fallback succeeds | Returned to user as success |
| 9 | All attempts succeed but stage took longer than budget | Success returned; budget only gates whether to start more attempts |
| 10 | Budget squeezes per-attempt timeout below ~1s | Treated as a normal `request_failed` (caused by `requests.Timeout`); next iteration's budget check stops the loop |
| 11 | `_log_raw` write failure | Swallowed (existing behavior) |
| 12 | Vision succeeds but category fully fails | 502 with `stage="category"`. Vision attempts are not in the response unless `debug=true` |
| 13 | Title flow fails fully and `image_url` provided | Existing fallback: run `_classify_image_to_paths`. If image fallback also fails, raise `LLMAllAttemptsFailedError(stage="vision")` |
| 14 | Image format/size invalid | `BadRequestError` → 400, unrelated to LLM chain |
| 15 | OpenRouter 401 (bad API key) | Surfaces as `request_failed` for every attempt; operators can read the attempts list and see "401" repeated |

## Logging

- Existing `_log_raw("vision_content", content)` / `_log_raw("vision_raw_response", raw)` runs
  once after a successful attempt for each stage. Unchanged.
- New aggregate log `_log_raw(f"{stage}_attempts", [asdict(a) for a in attempts])` is written
  on both success and failure (only when `LOG_LLM_RAW=1`).
- Failed attempts do not write per-attempt raw response logs; their messages are captured in
  the `attempts` list only.

## Testing

### `tests/test_json_parser.py` (new)

- Plain JSON object.
- JSON object wrapped in ```` ```json ... ``` ````.
- JSON object wrapped in ```` ``` ... ``` ```` (no language tag).
- JSON object surrounded by prose ("Here is the result: { ... }. Done.").
- Bare array (`[1, 2, 3]`).
- Empty string and whitespace-only — raises `LLMParseError`.
- Pure prose with no braces — raises `LLMParseError`.
- Trailing comma (invalid JSON) — raises `LLMParseError`.
- Unicode content preserved.
- Excerpt in the raised message is at most 200 chars + `…`.

### `tests/test_resilient_caller.py` (new)

Mock `OpenRouterClient.chat` with scripted side-effect sequences. Use `monkeypatch` on
`time.monotonic` and `time.sleep` to control budget and assert backoff.

- Primary-first-success: 1 entry, `error_kind="ok"`.
- Primary-second-success: 2 entries, first `request_failed`, second `ok`.
- Primary-3-fail-then-fb1-success: 4 entries, models switch correctly.
- All-fail: raises `LLMAllAttemptsFailedError`, attempts length = `(max_retries+1) +
  len(fallbacks_after_dedup)`, stage propagated. (E.g. with default settings where
  the primary `google/gemini-3-flash-preview` is also entry #1 of the default fallback
  list, dedup yields `4 + 6 = 10` attempts.)
- Parse failures: 3 same-model parse failures → fallback succeeds; `status_code=200` and
  `error_kind="parse_failed"` recorded.
- Budget exhausted before attempt N: last record has `error_kind="budget_exhausted"`, loop
  stops, exception raised.
- Backoff capped by budget: with remaining 0.3s the sleep call argument is ≤ 0.2s.
- Fallback contains primary: deduplicated, no double-runs.
- Empty fallback list: only primary retries.
- Empty primary string: raises immediately, `attempts == []`.
- Sleep is called between same-model retries but not when switching to a new model.
- `latency_ms` increases monotonically across the attempts list (under mocked monotonic
  clock).

### `tests/test_openrouter_client.py` (modify)

- Add a case that calls `chat(timeout=5)` and asserts the mocked `requests.post` is invoked
  with `timeout=5`, while `chat()` without `timeout` uses `self.timeout`.

### `tests/test_runtime_config.py` (modify)

- New `multiline_str` round-trip: PUT `["a/b", "c/d"]` → `.env` contains
  `VISION_FALLBACK_MODELS=a/b,c/d` → GET returns `["a/b", "c/d"]`.
- PUT a string with `\n` for a `multiline_str` field also accepted; empty lines dropped.
- PUT a removed field (`CATEGORY_LLM_RETRY_ENABLED`) returns 400 unsupported-field error.
- PUT new fields succeeds (`MODEL_CALL_MAX_RETRIES`, `MODEL_CALL_TOTAL_BUDGET_SECONDS`,
  `VISION_FALLBACK_MODELS`, `CATEGORY_FALLBACK_MODELS`).

### `tests/test_service_error_paths.py` (new)

- Vision link fully fails → `analyzer.analyze` raises `LLMAllAttemptsFailedError(stage="vision")`.
- Vision succeeds, category fully fails → raises `LLMAllAttemptsFailedError(stage="category")`.
- Title link fully fails + image_url provided + image fallback also fails → raises
  `LLMAllAttemptsFailedError(stage="vision")`.

### `tests/test_main_error_response.py` (new, FastAPI `TestClient`)

- Mock analyzer to raise `LLMAllAttemptsFailedError` → response 502, `body.detail` is a dict
  with the documented schema.
- Mock analyzer to raise `BadRequestError` → 400, `body.detail` is a string.

### Manual frontend smoke checks

- Unset `OPENROUTER_API_KEY` in `.env` → submit image → frontend shows the structured error
  with each attempt as `request_failed`.
- Set `VISION_MODEL=bogus/foo` → submit → page shows fallback chain progressing or fully
  failing depending on the configured fallbacks.
- Patch a prompt to force the model to wrap output in ```` ```json … ``` ```` → confirm
  `parse_llm_json` accepts it (no parse_failed in attempts).
- Use the config page to clear `VISION_FALLBACK_MODELS`, save, retry — verify only the
  primary is tried.
- Reload the config page after save to confirm round-trip rendering.

## Compatibility and rollout notes

1. Old `.env` files keep working. `CATEGORY_LLM_RETRY_ENABLED` /
   `CATEGORY_LLM_MAX_RETRIES` are not read; `runtime_config` rejects them on PUT. README and
   API.md are updated to mark these as removed.
2. `/api/v1/config` GET shape changes: drops 2 fields, adds 4. The only known consumer is
   `web/config.html`, which is updated together.
3. `/analyze` error body shape changes: `detail` becomes a dict on LLM exhaustion (string on
   `BadRequestError` and 500). API.md gains an "Error response" section describing both
   shapes. The single web client is updated to handle both forms.
4. New default behavior: every stage retries 3× and walks 7 fallback models. Worst-case
   single-stage time is bounded by `MODEL_CALL_TOTAL_BUDGET_SECONDS` (default 120s) rather
   than by the number of attempts. `REQUEST_TIMEOUT` (default 60s) keeps its meaning as the
   per-call upper bound.
5. No new dependencies. Pure stdlib + existing `requests` and `pytest`.

## Out of scope

- Per-model `temperature` / `max_tokens` overrides.
- Retry differentiation by HTTP status (e.g. 4xx no-retry).
- Concurrent fallback fan-out.
- Shared cross-image / cross-batch retry budget.
- Persisting attempts to an external monitoring system.
- Non-`web/index.html` clients (none exist in this repo).
