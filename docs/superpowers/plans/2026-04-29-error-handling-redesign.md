# Error Handling Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad-hoc error handling in the LLM pipeline with a unified retry + fallback layer that distinguishes request failures from JSON parse failures, walks a configurable fallback model list, and surfaces structured per-attempt details in the HTTP error response.

**Architecture:** A new `ResilientLLMCaller` mediator (`app/llm/resilient.py`) wraps single-shot calls to `OpenRouterClient.chat`. It performs primary-model retries with exponential backoff (0.2 / 0.4 / 0.8s, capped at 1.5s and clamped by remaining stage budget), then walks `fallback_models` (one attempt each, no inter-model backoff). Both `LLMRequestError` and `LLMParseError` feed the same retry/fallback loop. A new `parse_llm_json` (`app/llm/json_parser.py`) tolerates plain JSON, ```` ```json … ``` ````/```` ``` … ``` ```` markdown fences, and prose surrounding `{…}` or `[…]`. On exhaustion the caller raises `LLMAllAttemptsFailedError(stage, attempts)`; `main.py` serialises it into HTTP 502 with a structured `detail` dict that the frontend renders as a collapsible attempt list.

**Tech Stack:** Python 3.11, FastAPI, requests, stdlib `unittest`, vanilla JS in `web/index.html`. Test runner: `uv run python -m unittest tests.<module> -v`. No new dependencies.

**Spec reference:** `docs/superpowers/specs/2026-04-29-error-handling-redesign-design.md` (commit f7a4692).

**Implementation note for AttemptRecord placement:** The spec puts `AttemptRecord` in `app/llm/resilient.py` and uses `List[AttemptRecord]` in `app/errors.py`. To honour that without circular imports, `app/errors.py` adds `from __future__ import annotations` so the type hint becomes a string at runtime; the dataclass stays in `resilient.py` and is only imported under `TYPE_CHECKING`.

---

## File Map

**New:**
- `app/llm/json_parser.py` — `parse_llm_json` (`LLMParseError` lives in `app/errors.py`).
- `app/llm/resilient.py` — `AttemptRecord` dataclass and `ResilientCaller`.
- `tests/test_json_parser.py`
- `tests/test_resilient_caller.py`
- `tests/test_service_error_paths.py`
- `tests/test_main_error_response.py`

**Modified:**
- `app/errors.py` — add `LLMParseError`, `LLMAllAttemptsFailedError`.
- `app/llm/client.py` — add optional `timeout` arg to `chat`.
- `app/constants.py` — add `DEFAULT_FALLBACK_MODELS`.
- `app/config.py` — add `_env_str_list`, new settings fields, remove deprecated category-retry fields.
- `app/runtime_config.py` — add `multiline_str` field type, swap deprecated fields for the new ones.
- `app/utils.py` — `safe_json_loads` becomes a thin delegate to `parse_llm_json`.
- `app/service.py` — three `_call_*` methods delegate to `ResilientCaller`; `_choose_categories` drops its in-method retry block; `analyze`/`analyze_title` accumulate attempts and surface them under `_debug.attempts` when `debug=true`.
- `main.py` — catch `LLMAllAttemptsFailedError`, return 502 with structured `detail`.
- `web/index.html` — structured error renderer + `payload.detail` fallback bug fix.
- `web/config.html` — render `multiline_str` as textarea, swap deprecated cards for new ones.
- `tests/test_runtime_config.py` — replace fixtures referencing deprecated fields; add new-field round-trips.
- `tests/test_openrouter_client.py` — add `chat(timeout=...)` override case.
- `tests/test_service_rakuten_id.py` — replace any reference to deprecated category-retry settings (verify and patch in Task 6).
- `README.md` — update env var table; mark deprecated fields.
- `API.md` — describe structured error body schema.

---

## Task 1: Robust JSON parser (`parse_llm_json` + `LLMParseError`)

**Files:**
- Create: `app/llm/json_parser.py`
- Create: `tests/test_json_parser.py`
- Modify: `app/errors.py` (add `LLMParseError` only — `LLMAllAttemptsFailedError` comes in Task 2)

- [ ] **Step 1.1: Add `LLMParseError` to `app/errors.py`**

Replace the entire current contents of `app/errors.py` with:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .llm.resilient import AttemptRecord  # pragma: no cover


class BadRequestError(Exception):
    pass


class LLMRequestError(Exception):
    pass


class LLMParseError(Exception):
    """Raised by parse_llm_json when LLM output cannot be coerced into JSON."""
```

(`LLMAllAttemptsFailedError` is added in Task 2; the `TYPE_CHECKING` import block is reserved for it now to keep diffs small.)

- [ ] **Step 1.2: Write the failing tests for `parse_llm_json`**

Create `tests/test_json_parser.py`:

```python
import unittest

from app.errors import LLMParseError
from app.llm.json_parser import parse_llm_json


class ParseLLMJsonTest(unittest.TestCase):
    def test_plain_json_object(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})

    def test_json_object_in_json_fence(self):
        raw = "Here you go:\n```json\n{\"a\": 1, \"b\": [2, 3]}\n```"
        self.assertEqual(parse_llm_json(raw), {"a": 1, "b": [2, 3]})

    def test_json_object_in_unlabeled_fence(self):
        raw = "```\n{\"x\": \"y\"}\n```"
        self.assertEqual(parse_llm_json(raw), {"x": "y"})

    def test_json_object_with_surrounding_prose(self):
        raw = "Sure! The answer is { \"answer\": 42 } - hope it helps."
        self.assertEqual(parse_llm_json(raw), {"answer": 42})

    def test_bare_array(self):
        self.assertEqual(parse_llm_json("[1, 2, 3]"), [1, 2, 3])

    def test_unicode_preserved(self):
        self.assertEqual(parse_llm_json('{"name": "東京"}'), {"name": "東京"})

    def test_empty_string_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("   \n\t  ")

    def test_pure_prose_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("I'm sorry, I can't help with that.")

    def test_trailing_comma_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json('{"a": 1,}')

    def test_excerpt_in_message_capped_at_200_chars(self):
        long_blob = "noise " * 200
        with self.assertRaises(LLMParseError) as ctx:
            parse_llm_json(long_blob)
        msg = str(ctx.exception)
        self.assertIn("excerpt=", msg)
        # 200 chars + ellipsis somewhere in the message
        self.assertIn("…", msg)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 1.3: Run the tests and confirm they fail**

```bash
uv run python -m unittest tests.test_json_parser -v
```

Expected: ImportError / ModuleNotFoundError on `app.llm.json_parser`.

- [ ] **Step 1.4: Implement `parse_llm_json`**

Create `app/llm/json_parser.py`:

```python
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from ..errors import LLMParseError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_llm_json(raw: str) -> Any:
    """Parse LLM output as JSON, tolerating common framing variants.

    Strategy (first match wins):
      1) json.loads on the stripped text.
      2) Strip ```json ... ``` or ``` ... ``` fence and parse the inside.
      3) Substring from first '{' to last '}'.
      4) Substring from first '[' to last ']'.
    Raises LLMParseError with a 200-char excerpt on failure.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise LLMParseError("LLM returned empty content.")

    stripped = raw.strip()
    candidates: List[str] = [stripped]

    fence = _FENCE_RE.search(stripped)
    if fence:
        candidates.append(fence.group(1).strip())

    if "{" in stripped and "}" in stripped:
        candidates.append(stripped[stripped.find("{"): stripped.rfind("}") + 1])

    if "[" in stripped and "]" in stripped:
        candidates.append(stripped[stripped.find("["): stripped.rfind("]") + 1])

    last_err: Optional[json.JSONDecodeError] = None
    for c in candidates:
        if not c:
            continue
        try:
            return json.loads(c)
        except json.JSONDecodeError as exc:
            last_err = exc

    excerpt = (raw[:200] + "…") if len(raw) > 200 else raw
    reason = str(last_err) if last_err else "no JSON candidate found"
    raise LLMParseError(f"JSON decode failed: {reason}. excerpt={excerpt!r}")
```

- [ ] **Step 1.5: Run tests and confirm they pass**

```bash
uv run python -m unittest tests.test_json_parser -v
```

Expected: 11 tests OK.

- [ ] **Step 1.6: Commit**

```bash
git add app/errors.py app/llm/json_parser.py tests/test_json_parser.py
git commit -m "feat(llm): add robust parse_llm_json with markdown/prose tolerance"
```

---

## Task 2: `AttemptRecord` + `LLMAllAttemptsFailedError`

**Files:**
- Modify: `app/errors.py`
- Create: `app/llm/resilient.py` (skeleton holding `AttemptRecord`; `ResilientCaller` is added in Task 7)

- [ ] **Step 2.1: Write failing tests for the new exception**

Create `tests/test_errors.py`:

```python
import unittest

from app.errors import LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord


class LLMAllAttemptsFailedErrorTest(unittest.TestCase):
    def test_summary_includes_stage_and_count(self):
        attempts = [
            AttemptRecord(model="m1", attempt=1, attempt_global=1,
                          error_kind="request_failed", message="boom",
                          latency_ms=12.0, status_code=503),
            AttemptRecord(model="m1", attempt=2, attempt_global=2,
                          error_kind="parse_failed", message="bad json",
                          latency_ms=8.0, status_code=200),
        ]
        exc = LLMAllAttemptsFailedError(stage="vision", attempts=attempts)
        self.assertEqual(exc.stage, "vision")
        self.assertIs(exc.attempts, attempts)
        self.assertIn("vision", str(exc))
        self.assertIn("2", str(exc))

    def test_empty_attempts_still_summarised(self):
        exc = LLMAllAttemptsFailedError(stage="category", attempts=[])
        self.assertEqual(exc.attempts, [])
        self.assertIn("category", str(exc))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2.2: Run tests and confirm they fail**

```bash
uv run python -m unittest tests.test_errors -v
```

Expected: ImportError on `app.llm.resilient` (module not yet created) AND on `LLMAllAttemptsFailedError`.

- [ ] **Step 2.3: Add `AttemptRecord` to `app/llm/resilient.py`**

Create `app/llm/resilient.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AttemptRecord:
    model: str
    attempt: int            # per-model attempt index, starting at 1
    attempt_global: int     # global attempt index across all models, starting at 1
    error_kind: str         # "request_failed" | "parse_failed" | "budget_exhausted" | "ok"
    message: str
    latency_ms: float
    status_code: Optional[int] = None
```

`ResilientCaller` is added in Task 7.

- [ ] **Step 2.4: Add `LLMAllAttemptsFailedError` to `app/errors.py`**

Replace the entire `app/errors.py` with:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .llm.resilient import AttemptRecord  # pragma: no cover


class BadRequestError(Exception):
    pass


class LLMRequestError(Exception):
    pass


class LLMParseError(Exception):
    """Raised by parse_llm_json when LLM output cannot be coerced into JSON."""


class LLMAllAttemptsFailedError(Exception):
    """Raised when every retry + fallback attempt for a stage has failed."""

    def __init__(self, stage: str, attempts: "List[AttemptRecord]") -> None:
        self.stage = stage
        self.attempts = list(attempts)
        super().__init__(f"{stage}: {len(self.attempts)} attempts failed.")
```

- [ ] **Step 2.5: Run tests and confirm they pass**

```bash
uv run python -m unittest tests.test_errors -v
```

Expected: 2 tests OK.

- [ ] **Step 2.6: Commit**

```bash
git add app/errors.py app/llm/resilient.py tests/test_errors.py
git commit -m "feat(errors): add AttemptRecord + LLMAllAttemptsFailedError"
```

---

## Task 3: `OpenRouterClient.chat` accepts a per-call `timeout` override

**Files:**
- Modify: `app/llm/client.py:27-79`
- Modify: `tests/test_openrouter_client.py`

- [ ] **Step 3.1: Add a failing test for the per-call timeout override**

Append to `tests/test_openrouter_client.py` (inside the existing `OpenRouterClientReasoningTest` class):

```python
    def test_chat_timeout_override(self):
        client = OpenRouterClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            timeout=30,
        )
        captured = {}

        def fake_post(url, headers, data, timeout):
            captured["timeout"] = timeout
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        client.session.post = fake_post

        client.chat(
            model="google/gemini-3.1-pro-preview",
            messages=[{"role": "user", "content": "hi"}],
            timeout=5,
        )
        self.assertEqual(captured["timeout"], 5)

        client.chat(
            model="google/gemini-3.1-pro-preview",
            messages=[{"role": "user", "content": "hi"}],
        )
        self.assertEqual(captured["timeout"], 30)
```

- [ ] **Step 3.2: Run and confirm failure**

```bash
uv run python -m unittest tests.test_openrouter_client -v
```

Expected: TypeError on unexpected keyword argument `timeout`.

- [ ] **Step 3.3: Add the `timeout` parameter to `chat`**

Replace `app/llm/client.py` `chat` signature and the `requests.post` call. The complete updated file:

```python
import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..errors import LLMRequestError


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int,
        referer: str = "",
        app_name: str = "",
        reasoning: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.referer = referer
        self.app_name = app_name
        self.reasoning = dict(reasoning) if reasoning else None
        self.session = requests.Session()

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: Optional[float] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        if not self.api_key:
            raise LLMRequestError("OPENROUTER_API_KEY is not configured.")
        if not model:
            raise LLMRequestError("Model name is empty.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.app_name:
            headers["X-Title"] = self.app_name

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.reasoning is not None:
            payload["reasoning"] = dict(self.reasoning)

        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            response = self.session.post(
                self.base_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=effective_timeout,
            )
        except requests.RequestException as exc:
            raise LLMRequestError(f"OpenRouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMRequestError(
                f"OpenRouter returned {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMRequestError(f"Failed to parse OpenRouter response: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("OpenRouter response is missing content.") from exc

        return content, data
```

- [ ] **Step 3.4: Run and confirm tests pass**

```bash
uv run python -m unittest tests.test_openrouter_client -v
```

Expected: all reasoning tests + new override test OK.

- [ ] **Step 3.5: Commit**

```bash
git add app/llm/client.py tests/test_openrouter_client.py
git commit -m "feat(llm): per-call timeout override on OpenRouterClient.chat"
```

---

## Task 4: Default fallback models constant

**Files:**
- Modify: `app/constants.py:48-49`

- [ ] **Step 4.1: Append `DEFAULT_FALLBACK_MODELS` to `app/constants.py`**

Add at the bottom of `app/constants.py` (after `PRICE_MAX = 1_000_000`):

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

- [ ] **Step 4.2: Smoke import**

```bash
uv run python -c "from app.constants import DEFAULT_FALLBACK_MODELS; assert len(DEFAULT_FALLBACK_MODELS) == 7; print('ok')"
```

Expected: `ok`.

- [ ] **Step 4.3: Commit**

```bash
git add app/constants.py
git commit -m "feat(constants): add DEFAULT_FALLBACK_MODELS for resilient caller"
```

---

## Task 5: Settings — new fields + remove deprecated ones

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_config.py`

- [ ] **Step 5.1: Add failing tests for the new settings**

Append to `tests/test_config.py` (inside `SettingsConfigTest`):

```python
    def test_default_fallback_models_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.vision_fallback_models[0], "google/gemini-3-flash-preview")
        self.assertEqual(len(settings.vision_fallback_models), 7)
        self.assertEqual(
            settings.vision_fallback_models,
            settings.category_fallback_models,
        )

    def test_fallback_models_from_env_csv(self):
        with patch.dict(
            "os.environ",
            {"VISION_FALLBACK_MODELS": "a/b , , c/d"},
            clear=True,
        ):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.vision_fallback_models, ["a/b", "c/d"])

    def test_model_call_budget_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.model_call_max_retries, 3)
        self.assertEqual(settings.model_call_total_budget_seconds, 120)

    def test_model_call_budget_from_env(self):
        with patch.dict(
            "os.environ",
            {"MODEL_CALL_MAX_RETRIES": "1", "MODEL_CALL_TOTAL_BUDGET_SECONDS": "60"},
            clear=True,
        ):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.model_call_max_retries, 1)
        self.assertEqual(settings.model_call_total_budget_seconds, 60)

    def test_deprecated_category_retry_fields_are_removed(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertFalse(hasattr(settings, "category_llm_retry_enabled"))
        self.assertFalse(hasattr(settings, "category_llm_max_retries"))
```

- [ ] **Step 5.2: Run and confirm failure**

```bash
uv run python -m unittest tests.test_config -v
```

Expected: AttributeError for `vision_fallback_models` and friends.

- [ ] **Step 5.3: Update `app/config.py`**

Replace the entire `app/config.py` with:

```python
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from dotenv import load_dotenv

from .constants import ALLOWED_MIME_TYPES, DEFAULT_FALLBACK_MODELS

# Load .env early so os.getenv can pick up values defined there.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_min(name: str, default: int, minimum: int) -> int:
    value = _env_int(name, default)
    return value if value >= minimum else default


def _env_optional_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return None


def _env_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _env_optional_enum(name: str, allowed: Set[str]) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    return cleaned if cleaned in allowed else None


def _env_str_list(name: str, default: Sequence[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    items = [item.strip() for item in raw.split(",")]
    items = [item for item in items if item]
    return items if items else list(default)


@dataclass
class Settings:
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    vision_model: str = os.getenv("VISION_MODEL", "")
    category_model: str = os.getenv("CATEGORY_MODEL", "")
    brand_csv_path: str = os.getenv("BRAND_CSV_PATH", "data/mercari_brand.csv")
    category_csv_path: str = os.getenv("CATEGORY_CSV_PATH", "data/category_rakuten.csv")
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    openrouter_referer: str = os.getenv("OPENROUTER_REFERER", "")
    openrouter_app_name: str = os.getenv("OPENROUTER_APP_NAME", "mercari-image-backend")
    request_timeout: int = _env_int_min("REQUEST_TIMEOUT", 60, 1)
    enable_debug_param: bool = _env_bool("ENABLE_DEBUG", True)
    max_image_bytes: int = _env_int("MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    image_compression_threshold_mb: int = _env_int("IMAGE_COMPRESSION_THRESHOLD_MB", 1)
    allowed_mime_types: Set[str] = field(default_factory=lambda: set(ALLOWED_MIME_TYPES))
    log_llm_raw: bool = _env_bool("LOG_LLM_RAW", False)
    log_requests: bool = _env_bool("LOG_REQUESTS", True)
    log_requests_retention_days: int = _env_int("LOG_REQUESTS_RETENTION_DAYS", 7)
    log_requests_max_files: int = _env_int("LOG_REQUESTS_MAX_FILES", 1000)

    vision_fallback_models: List[str] = field(
        default_factory=lambda: _env_str_list("VISION_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    )
    category_fallback_models: List[str] = field(
        default_factory=lambda: _env_str_list("CATEGORY_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    )
    model_call_max_retries: int = _env_int_min("MODEL_CALL_MAX_RETRIES", 3, 0)
    model_call_total_budget_seconds: int = _env_int_min(
        "MODEL_CALL_TOTAL_BUDGET_SECONDS", 120, 1
    )

    reasoning_enabled: Optional[bool] = field(default_factory=lambda: _env_optional_bool("REASONING_ENABLED"))
    reasoning_effort: Optional[str] = field(
        default_factory=lambda: _env_optional_enum(
            "REASONING_EFFORT",
            {"minimal", "low", "medium", "high", "xhigh", "none"},
        )
    )
    reasoning_max_tokens: Optional[int] = field(
        default_factory=lambda: _env_optional_int("REASONING_MAX_TOKENS")
    )
    reasoning_summary: Optional[str] = field(
        default_factory=lambda: _env_optional_enum(
            "REASONING_SUMMARY",
            {"auto", "concise", "detailed"},
        )
    )

    @property
    def reasoning(self) -> Optional[Dict[str, Any]]:
        reasoning: Dict[str, Any] = {}
        if self.reasoning_enabled is not None:
            reasoning["enabled"] = self.reasoning_enabled
        if self.reasoning_effort is not None:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_max_tokens is not None:
            reasoning["max_tokens"] = self.reasoning_max_tokens
        if self.reasoning_summary is not None:
            reasoning["summary"] = self.reasoning_summary
        return reasoning or None

    @property
    def image_compression_threshold_bytes(self) -> int:
        return max(0, int(self.image_compression_threshold_mb)) * 1024 * 1024


def load_settings() -> Settings:
    return Settings()
```

(Net change: removed `category_llm_retry_enabled` / `category_llm_max_retries`; added `_env_str_list`, `vision_fallback_models`, `category_fallback_models`, `model_call_max_retries`, `model_call_total_budget_seconds`.)

- [ ] **Step 5.4: Run config + reasoning tests and confirm pass**

```bash
uv run python -m unittest tests.test_config tests.test_openrouter_client -v
```

Expected: all tests OK (the reasoning test uses `Settings()` and must keep passing).

- [ ] **Step 5.5: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): add fallback models + retry/budget settings; drop CATEGORY_LLM_RETRY_*"
```

---

## Task 6: Runtime config — `multiline_str` field type + field swap

**Files:**
- Modify: `app/runtime_config.py`
- Modify: `tests/test_runtime_config.py`
- Verify: `tests/test_service_rakuten_id.py` (sanity check; patch only if it constructs `Settings` with the deprecated fields)

- [ ] **Step 6.1: Inspect dependent tests for deprecated fields**

```bash
uv run python -c "import re,pathlib; p=pathlib.Path('tests/test_service_rakuten_id.py'); print(p.read_text(encoding='utf-8'))"
```

If the output references `category_llm_retry_enabled` or `category_llm_max_retries`, plan to remove those references inside this same task; otherwise no change is needed.

- [ ] **Step 6.2: Write failing tests for the new runtime_config behaviour**

Replace the entire contents of `tests/test_runtime_config.py` with:

```python
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.runtime_config import get_public_config, update_runtime_config


def _fake_settings(**overrides):
    base = dict(
        vision_model="old-vision",
        category_model="old-category",
        log_llm_raw=False,
        log_requests=False,
        enable_debug_param=True,
        image_compression_threshold_mb=1,
        request_timeout=60,
        vision_fallback_models=["a/b"],
        category_fallback_models=["a/b"],
        model_call_max_retries=3,
        model_call_total_budget_seconds=120,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class RuntimeConfigTest(unittest.TestCase):
    def test_update_writes_env_and_updates_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=secret-key\nVISION_MODEL=old-vision\n",
                encoding="utf-8",
            )
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {
                    "VISION_MODEL": "new-vision",
                    "CATEGORY_MODEL": "new-category",
                    "LOG_REQUESTS": True,
                    "MODEL_CALL_MAX_RETRIES": 1,
                },
                env_path=env_path,
            )

            self.assertEqual(result["VISION_MODEL"], "new-vision")
            self.assertEqual(settings.category_model, "new-category")
            self.assertTrue(settings.log_requests)
            self.assertEqual(settings.model_call_max_retries, 1)

            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=secret-key", env_text)
            self.assertIn("VISION_MODEL=new-vision", env_text)
            self.assertIn("CATEGORY_MODEL=new-category", env_text)
            self.assertIn("LOG_REQUESTS=true", env_text)
            self.assertIn("MODEL_CALL_MAX_RETRIES=1", env_text)

    def test_get_public_config_returns_lists_for_fallbacks(self):
        settings = _fake_settings(
            vision_fallback_models=["m1", "m2"],
            category_fallback_models=["m3"],
            openrouter_api_key="secret",
        )

        result = get_public_config(settings)

        self.assertEqual(result["VISION_FALLBACK_MODELS"], ["m1", "m2"])
        self.assertEqual(result["CATEGORY_FALLBACK_MODELS"], ["m3"])
        self.assertNotIn("OPENROUTER_API_KEY", result)
        self.assertNotIn("CATEGORY_LLM_RETRY_ENABLED", result)
        self.assertNotIn("CATEGORY_LLM_MAX_RETRIES", result)

    def test_multiline_str_round_trip_from_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("VISION_FALLBACK_MODELS=z/z\n", encoding="utf-8")
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {"VISION_FALLBACK_MODELS": ["a/b", "c/d"]},
                env_path=env_path,
            )

            self.assertEqual(result["VISION_FALLBACK_MODELS"], ["a/b", "c/d"])
            self.assertEqual(settings.vision_fallback_models, ["a/b", "c/d"])
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("VISION_FALLBACK_MODELS=a/b,c/d", env_text)

    def test_multiline_str_round_trip_from_newline_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("", encoding="utf-8")
            settings = _fake_settings()

            update_runtime_config(
                settings,
                {"CATEGORY_FALLBACK_MODELS": "a/b\n\nc/d\n"},
                env_path=env_path,
            )

            self.assertEqual(settings.category_fallback_models, ["a/b", "c/d"])

    def test_deprecated_field_is_rejected(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"CATEGORY_LLM_RETRY_ENABLED": True},
            )

    def test_update_rejects_zero_request_timeout(self):
        with self.assertRaises(ValueError):
            update_runtime_config(_fake_settings(), {"REQUEST_TIMEOUT": 0})

    def test_multiline_str_value_must_be_list_or_string(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"VISION_FALLBACK_MODELS": 123},
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6.3: Run and confirm failure**

```bash
uv run python -m unittest tests.test_runtime_config -v
```

Expected: failures referencing missing `VISION_FALLBACK_MODELS`, etc.

- [ ] **Step 6.4: Replace `app/runtime_config.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from .config import BASE_DIR


@dataclass(frozen=True)
class ConfigField:
    env_name: str
    settings_attr: str
    value_type: str  # "str" | "int" | "bool" | "multiline_str"
    min_value: int | None = None


CONFIG_FIELDS = (
    ConfigField("VISION_MODEL", "vision_model", "str"),
    ConfigField("CATEGORY_MODEL", "category_model", "str"),
    ConfigField("LOG_LLM_RAW", "log_llm_raw", "bool"),
    ConfigField("LOG_REQUESTS", "log_requests", "bool"),
    ConfigField("ENABLE_DEBUG", "enable_debug_param", "bool"),
    ConfigField("IMAGE_COMPRESSION_THRESHOLD_MB", "image_compression_threshold_mb", "int"),
    ConfigField("REQUEST_TIMEOUT", "request_timeout", "int", min_value=1),
    ConfigField("VISION_FALLBACK_MODELS", "vision_fallback_models", "multiline_str"),
    ConfigField("CATEGORY_FALLBACK_MODELS", "category_fallback_models", "multiline_str"),
    ConfigField("MODEL_CALL_MAX_RETRIES", "model_call_max_retries", "int"),
    ConfigField(
        "MODEL_CALL_TOTAL_BUDGET_SECONDS",
        "model_call_total_budget_seconds",
        "int",
        min_value=1,
    ),
)
CONFIG_FIELD_BY_ENV = {field.env_name: field for field in CONFIG_FIELDS}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("Expected a boolean value.")


def _parse_int(value: Any, field: ConfigField) -> int:
    if isinstance(value, bool):
        raise ValueError("Expected an integer value.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected an integer value.") from exc
    if parsed < 0:
        raise ValueError("Expected a non-negative integer value.")
    if field.min_value is not None and parsed < field.min_value:
        raise ValueError(f"{field.env_name} must be at least {field.min_value}.")
    return parsed


def _parse_multiline_str(value: Any, field: ConfigField) -> List[str]:
    if isinstance(value, list):
        items = [str(v).strip() for v in value]
    elif isinstance(value, str):
        items = [piece.strip() for piece in value.replace("\r", "").split("\n")]
    else:
        raise ValueError(f"{field.env_name} must be a list of strings or a newline-delimited string.")
    items = [item for item in items if item]
    return items


def _parse_value(field: ConfigField, value: Any) -> Any:
    if field.value_type == "bool":
        return _parse_bool(value)
    if field.value_type == "int":
        return _parse_int(value, field)
    if field.value_type == "multiline_str":
        return _parse_multiline_str(value, field)
    if value is None:
        return ""
    parsed = str(value).strip()
    if "\n" in parsed or "\r" in parsed:
        raise ValueError(f"{field.env_name} must be a single-line value.")
    return parsed


def _serialize_value(field: ConfigField, value: Any) -> str:
    if field.value_type == "bool":
        return "true" if bool(value) else "false"
    if field.value_type == "multiline_str":
        return ",".join(value)
    return str(value)


def get_public_config(settings: Any) -> Dict[str, Any]:
    return {
        field.env_name: getattr(settings, field.settings_attr)
        for field in CONFIG_FIELDS
    }


def _set_env_lines(lines: Iterable[str], values: Dict[str, str]) -> str:
    remaining = dict(values)
    output = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line.rstrip("\n"))
    for key, value in remaining.items():
        output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def update_runtime_config(
    settings: Any,
    updates: Dict[str, Any],
    *,
    env_path: Path | None = None,
    on_applied: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    unknown = sorted(set(updates) - set(CONFIG_FIELD_BY_ENV))
    if unknown:
        raise ValueError(f"Unsupported config fields: {', '.join(unknown)}")

    parsed: Dict[ConfigField, Any] = {}
    serialized: Dict[str, str] = {}
    for env_name, value in updates.items():
        field = CONFIG_FIELD_BY_ENV[env_name]
        parsed_value = _parse_value(field, value)
        parsed[field] = parsed_value
        serialized[env_name] = _serialize_value(field, parsed_value)

    target_path = env_path or (BASE_DIR / ".env")
    existing_lines: List[str] = []
    if target_path.exists():
        existing_lines = target_path.read_text(encoding="utf-8").splitlines()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(_set_env_lines(existing_lines, serialized), encoding="utf-8")

    for field, value in parsed.items():
        setattr(settings, field.settings_attr, value)
    if on_applied:
        on_applied()

    return get_public_config(settings)
```

- [ ] **Step 6.5: If `tests/test_service_rakuten_id.py` referenced removed fields, patch it**

If Step 6.1 showed references, remove every `category_llm_retry_enabled=...` and `category_llm_max_retries=...` argument. Otherwise skip this step.

- [ ] **Step 6.6: Run and confirm pass**

```bash
uv run python -m unittest tests.test_runtime_config tests.test_config_api tests.test_service_rakuten_id -v
```

Expected: all tests OK.

- [ ] **Step 6.7: Commit**

```bash
git add app/runtime_config.py tests/test_runtime_config.py tests/test_service_rakuten_id.py
git commit -m "feat(runtime_config): multiline_str type; swap deprecated category-retry fields"
```

(If the rakuten test was untouched, drop it from `git add`.)

---

## Task 7: `ResilientCaller.call_and_parse`

**Files:**
- Modify: `app/llm/resilient.py` (extend the skeleton from Task 2)
- Create: `tests/test_resilient_caller.py`

- [ ] **Step 7.1: Write failing tests**

Create `tests/test_resilient_caller.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

from app.errors import LLMAllAttemptsFailedError, LLMRequestError
from app.llm.resilient import ResilientCaller, AttemptRecord


def _make_caller(client, *, max_retries=3, total_budget_s=120,
                 per_attempt_timeout_s=60):
    return ResilientCaller(
        client=client,
        max_retries=max_retries,
        total_budget_s=total_budget_s,
        per_attempt_timeout_s=per_attempt_timeout_s,
    )


def _ok_response(payload='{"ok": true}'):
    return (payload, {"choices": [{"message": {"content": payload}}]})


class ResilientCallerTest(unittest.TestCase):
    def setUp(self):
        # Force time.monotonic to advance deterministically: each call adds 0.001s
        self._t = [1000.0]

        def fake_monotonic():
            self._t[0] += 0.001
            return self._t[0]

        self._mono_patcher = patch("app.llm.resilient.time.monotonic", side_effect=fake_monotonic)
        self._mono = self._mono_patcher.start()
        self._sleep_patcher = patch("app.llm.resilient.time.sleep")
        self._sleep = self._sleep_patcher.start()

    def tearDown(self):
        self._mono_patcher.stop()
        self._sleep_patcher.stop()

    def test_primary_first_call_succeeds(self):
        client = MagicMock()
        client.chat.return_value = _ok_response()
        caller = _make_caller(client)

        parsed, raw, attempts = caller.call_and_parse(
            stage="vision",
            primary_model="m1",
            fallback_models=["fb1"],
            messages=[],
            temperature=0.1,
            max_tokens=10,
        )

        self.assertEqual(parsed, {"ok": True})
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].error_kind, "ok")
        self.assertEqual(attempts[0].model, "m1")
        client.chat.assert_called_once()

    def test_primary_retries_then_succeeds(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("OpenRouter returned 503: x"),
            _ok_response(),
        ]
        caller = _make_caller(client)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=[],
            messages=[], temperature=0.1, max_tokens=10,
        )

        self.assertEqual([a.error_kind for a in attempts], ["request_failed", "ok"])
        self.assertEqual(attempts[0].status_code, 503)
        self.assertEqual(self._sleep.call_count, 1)

    def test_primary_exhausted_fallback_succeeds(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("e1"),
            LLMRequestError("e2"),
            LLMRequestError("e3"),
            LLMRequestError("e4"),  # primary 4th attempt (initial + 3 retries)
            _ok_response(),         # fb1 succeeds
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1", "fb2"],
            messages=[], temperature=0.1, max_tokens=10,
        )

        models = [a.model for a in attempts]
        self.assertEqual(models, ["m1", "m1", "m1", "m1", "fb1"])
        self.assertEqual(attempts[-1].error_kind, "ok")

    def test_all_fail_raises(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("nope")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )

        self.assertEqual(ctx.exception.stage, "vision")
        # 4 primary + 1 fallback = 5
        self.assertEqual(len(ctx.exception.attempts), 5)
        self.assertTrue(all(a.error_kind == "request_failed" for a in ctx.exception.attempts))

    def test_parse_failed_triggers_retry_and_fallback(self):
        client = MagicMock()
        # Three malformed responses on m1, then valid on fb1
        client.chat.side_effect = [
            ("not json", {}),
            ("still not", {}),
            ("```\nnope\n```", {}),
            ("```\nnope\n```", {}),
            _ok_response(),
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1"],
            messages=[], temperature=0.1, max_tokens=10,
        )

        kinds = [a.error_kind for a in attempts]
        self.assertEqual(kinds[:4], ["parse_failed"] * 4)
        self.assertEqual(kinds[-1], "ok")
        self.assertEqual(attempts[0].status_code, 200)

    def test_non_dict_json_treated_as_parse_failed(self):
        client = MagicMock()
        # Bare array is valid JSON but not a dict
        client.chat.side_effect = [
            ("[1, 2, 3]", {}),
            _ok_response(),
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=[],
            messages=[], temperature=0.1, max_tokens=10,
        )
        self.assertEqual(attempts[0].error_kind, "parse_failed")
        self.assertEqual(attempts[1].error_kind, "ok")

    def test_budget_exhausted_before_attempt(self):
        # Make the deadline expire after the first attempt
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("e1")
        caller = _make_caller(client, max_retries=3, total_budget_s=0.1)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )
        kinds = [a.error_kind for a in ctx.exception.attempts]
        self.assertIn("budget_exhausted", kinds)

    def test_fallback_dedups_primary(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("nope")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1",
                fallback_models=["m1", "fb1"],  # m1 should be removed
                messages=[], temperature=0.1, max_tokens=10,
            )
        models = [a.model for a in ctx.exception.attempts]
        self.assertEqual(models.count("m1"), 4)  # primary's 4 attempts only
        self.assertEqual(models.count("fb1"), 1)

    def test_empty_fallback_list(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("e")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=[],
                messages=[], temperature=0.1, max_tokens=10,
            )
        self.assertEqual(len(ctx.exception.attempts), 4)

    def test_empty_primary_raises_immediately(self):
        client = MagicMock()
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )
        self.assertEqual(ctx.exception.attempts, [])
        client.chat.assert_not_called()

    def test_no_sleep_between_model_switch(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("e1"),
            LLMRequestError("e2"),
            LLMRequestError("e3"),
            LLMRequestError("e4"),  # primary exhausted
            _ok_response(),         # fb1
        ]
        caller = _make_caller(client, max_retries=3)
        caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1"],
            messages=[], temperature=0.1, max_tokens=10,
        )
        # 3 sleeps between primary's 4 attempts; none before the fallback
        self.assertEqual(self._sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 7.2: Run and confirm failure**

```bash
uv run python -m unittest tests.test_resilient_caller -v
```

Expected: ImportError on `ResilientCaller`.

- [ ] **Step 7.3: Implement `ResilientCaller`**

Replace `app/llm/resilient.py` (keeping `AttemptRecord`) with:

```python
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import LLMAllAttemptsFailedError, LLMParseError, LLMRequestError
from .client import OpenRouterClient
from .json_parser import parse_llm_json


_BACKOFF_S: Tuple[float, ...] = (0.2, 0.4, 0.8)
_BACKOFF_CAP_S: float = 1.5
_MIN_USEFUL_BUDGET_S: float = 1.0
_BACKOFF_HEADROOM_S: float = 0.1
_STATUS_RE = re.compile(r"OpenRouter returned (\d{3})")


@dataclass
class AttemptRecord:
    model: str
    attempt: int
    attempt_global: int
    error_kind: str
    message: str
    latency_ms: float
    status_code: Optional[int] = None


def _extract_status_code(exc: Exception) -> Optional[int]:
    m = _STATUS_RE.search(str(exc))
    return int(m.group(1)) if m else None


class ResilientCaller:
    """Wraps OpenRouterClient.chat with retry + fallback + JSON parsing."""

    def __init__(
        self,
        *,
        client: OpenRouterClient,
        max_retries: int,
        total_budget_s: float,
        per_attempt_timeout_s: float,
    ) -> None:
        self.client = client
        self.max_retries = max(0, int(max_retries))
        self.total_budget_s = float(total_budget_s)
        self.per_attempt_timeout_s = float(per_attempt_timeout_s)

    def call_and_parse(
        self,
        *,
        stage: str,
        primary_model: str,
        fallback_models: Sequence[str],
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        attempts: List[AttemptRecord] = []
        if not primary_model:
            raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

        deadline = time.monotonic() + self.total_budget_s
        global_idx = 0

        schedule: List[Tuple[str, int]] = [(primary_model, self.max_retries + 1)]
        for m in fallback_models:
            if m and m != primary_model:
                schedule.append((m, 1))

        for model, n_attempts in schedule:
            for attempt in range(1, n_attempts + 1):
                global_idx += 1
                remaining = deadline - time.monotonic()
                if remaining <= _MIN_USEFUL_BUDGET_S:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="budget_exhausted",
                            message=f"Stage budget exhausted before attempt (remaining {remaining:.2f}s).",
                            latency_ms=0.0,
                            status_code=None,
                        )
                    )
                    raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

                effective_timeout = min(self.per_attempt_timeout_s, remaining)
                t0 = time.monotonic()
                try:
                    content, raw_response = self.client.chat(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                    )
                except LLMRequestError as exc:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="request_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - t0) * 1000.0,
                            status_code=_extract_status_code(exc),
                        )
                    )
                    if attempt < n_attempts:
                        self._sleep_capped(attempt - 1, deadline)
                    continue

                try:
                    parsed = parse_llm_json(content)
                    if not isinstance(parsed, dict):
                        raise LLMParseError("LLM did not return a JSON object.")
                except LLMParseError as exc:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="parse_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - t0) * 1000.0,
                            status_code=200,
                        )
                    )
                    if attempt < n_attempts:
                        self._sleep_capped(attempt - 1, deadline)
                    continue

                attempts.append(
                    AttemptRecord(
                        model=model,
                        attempt=attempt,
                        attempt_global=global_idx,
                        error_kind="ok",
                        message="",
                        latency_ms=(time.monotonic() - t0) * 1000.0,
                        status_code=200,
                    )
                )
                return parsed, raw_response, attempts

        raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

    @staticmethod
    def _sleep_capped(idx: int, deadline: float) -> None:
        base = _BACKOFF_S[min(idx, len(_BACKOFF_S) - 1)]
        delay = min(base, _BACKOFF_CAP_S)
        budget_room = max(0.0, deadline - time.monotonic() - _BACKOFF_HEADROOM_S)
        delay = min(delay, budget_room)
        if delay > 0:
            time.sleep(delay)
```

- [ ] **Step 7.4: Run and confirm pass**

```bash
uv run python -m unittest tests.test_resilient_caller tests.test_errors -v
```

Expected: all tests OK.

- [ ] **Step 7.5: Commit**

```bash
git add app/llm/resilient.py tests/test_resilient_caller.py
git commit -m "feat(llm): ResilientCaller with retry, fallback, budget, parse handling"
```

---

## Task 8: `safe_json_loads` thin delegate

**Files:**
- Modify: `app/utils.py:110-124`

- [ ] **Step 8.1: Replace `safe_json_loads` body**

In `app/utils.py`, find the `safe_json_loads` function and replace it with:

```python
def safe_json_loads(raw: str) -> Any:
    """Backward-compatible alias for parse_llm_json (raises LLMParseError)."""
    from .llm.json_parser import parse_llm_json

    return parse_llm_json(raw)
```

The local import avoids a top-of-file circular import (`utils` is imported widely; `json_parser` imports from `errors`).

- [ ] **Step 8.2: Re-run all existing service-related tests**

```bash
uv run python -m unittest tests.test_service_rakuten_id tests.test_brand_store -v
```

Expected: all tests OK (the change is behavior-compatible for valid JSON; existing call sites that catch `Exception` continue to work).

- [ ] **Step 8.3: Commit**

```bash
git add app/utils.py
git commit -m "refactor(utils): safe_json_loads delegates to parse_llm_json"
```

---

## Task 9: Wire `ResilientCaller` into `_call_vision_llm`

**Files:**
- Modify: `app/service.py` (`__init__`, `_call_vision_llm`)

- [ ] **Step 9.1: Update `MercariAnalyzer.__init__` and `_call_vision_llm`**

In `app/service.py`, do three edits:

(a) Update the imports at the top of the file. Find this line:

```python
from .errors import BadRequestError, LLMRequestError
```

…and replace it with:

```python
from .errors import BadRequestError, LLMAllAttemptsFailedError, LLMRequestError
from .llm.resilient import ResilientCaller
```

(b) Replace the body of `MercariAnalyzer.__init__` to also build two callers:

```python
class MercariAnalyzer:
    def __init__(
        self,
        settings: Settings,
        brand_store: BrandStore,
        category_store: CategoryStore,
        vision_client: OpenRouterClient,
        category_client: OpenRouterClient,
    ):
        self.settings = settings
        self.brand_store = brand_store
        self.category_store = category_store
        self.vision_client = vision_client
        self.category_client = category_client
        self._logs_dir = Path(__file__).resolve().parent.parent / "logs"
        self.vision_caller = ResilientCaller(
            client=vision_client,
            max_retries=settings.model_call_max_retries,
            total_budget_s=settings.model_call_total_budget_seconds,
            per_attempt_timeout_s=settings.request_timeout,
        )
        self.category_caller = ResilientCaller(
            client=category_client,
            max_retries=settings.model_call_max_retries,
            total_budget_s=settings.model_call_total_budget_seconds,
            per_attempt_timeout_s=settings.request_timeout,
        )
```

(c) Replace `_call_vision_llm` (the existing implementation that builds messages + calls `chat` + parses with `safe_json_loads`) with:

```python
    def _call_vision_llm(
        self,
        image_data_urls: List[str],
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[Any]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        user_prompt = VISION_USER_PROMPT_WITH_PRICE.format(language_label=_language_label(language))
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image carefully. "
                        "Extract any unique evidence from it before merging with the other images."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT_WITH_PRICE},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
            },
        ]
        primary = model_override or self.settings.vision_model
        fallbacks = self.settings.vision_fallback_models

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="vision",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("vision_attempts", [a.__dict__ for a in exc.attempts])
            raise
        # _log_raw accepts either str or dict; logging `parsed` keeps the
        # post-parse JSON form (slightly more useful than the raw string).
        self._log_raw("vision_parsed", parsed)
        self._log_raw("vision_raw_response", raw_response)
        self._log_raw("vision_attempts", [a.__dict__ for a in attempts])
        return parsed, raw_response, attempts
```

> **Note on the return signature change:** the method now returns a 3-tuple including `attempts`. Callers in `analyze` are updated in Task 12. Tasks 10-11 also change to return attempts.

- [ ] **Step 9.2: Smoke import**

```bash
uv run python -c "from app.service import MercariAnalyzer; print('ok')"
```

Expected: `ok`.

- [ ] **Step 9.3: Commit**

```bash
git add app/service.py
git commit -m "refactor(service): vision call delegates to ResilientCaller"
```

(Tests for the full path are added in Task 12 once all three callers and the orchestration are in place.)

---

## Task 10: Wire `ResilientCaller` into `_call_title_category_llm`

**Files:**
- Modify: `app/service.py` (`_call_title_category_llm`)

- [ ] **Step 10.1: Replace `_call_title_category_llm`**

In `app/service.py`, replace the existing method with:

```python
    def _call_title_category_llm(
        self,
        title: str,
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[Any]]:
        user_prompt = PRODUCT_TITLE_CATEGORY_USER_PROMPT.format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        primary = model_override or self.settings.category_model
        fallbacks = self.settings.category_fallback_models

        try:
            parsed, raw_response, attempts = self.category_caller.call_and_parse(
                stage="title_category",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.3,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("title_category_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("title_category_parsed", parsed)
        self._log_raw("title_category_raw_response", raw_response)
        self._log_raw("title_category_attempts", [a.__dict__ for a in attempts])
        return parsed, attempts
```

- [ ] **Step 10.2: Smoke import**

```bash
uv run python -c "from app.service import MercariAnalyzer; print('ok')"
```

Expected: `ok`.

- [ ] **Step 10.3: Commit**

```bash
git add app/service.py
git commit -m "refactor(service): title-category call delegates to ResilientCaller"
```

---

## Task 11: Wire `ResilientCaller` into `_choose_categories`

**Files:**
- Modify: `app/service.py` (`_choose_categories`)

- [ ] **Step 11.1: Replace `_choose_categories`**

Replace the entire `_choose_categories` method (which currently contains the `attempts/sleep` loop) with:

```python
    def _choose_categories(
        self,
        title: str,
        description: str,
        brand_for_prompt: str,
        group_name: str,
        category_limit: int,
        model_override: Optional[str] = None,
    ) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]], List[Any]]:
        candidates = self.category_store.get_categories_by_group(group_name)
        if not candidates:
            return [], None, []

        candidate_paths = [item["name"] for item in candidates]
        candidate_block = "\n".join(candidate_paths)
        user_prompt = CATEGORY_USER_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            brand=brand_for_prompt,
            group_name=group_name,
            candidate_paths=candidate_block,
        )
        messages = [
            {"role": "system", "content": CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        primary = model_override or self.settings.category_model
        fallbacks = self.settings.category_fallback_models

        try:
            parsed, raw_response, attempts = self.category_caller.call_and_parse(
                stage="category",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.1,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("category_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("category_parsed", parsed)
        self._log_raw("category_raw_response", raw_response)
        self._log_raw("category_attempts", [a.__dict__ for a in attempts])

        ordered_paths: List[str] = []
        best = parsed.get("best_target_path")
        if isinstance(best, str) and best.strip():
            ordered_paths.append(best)

        alternatives = parsed.get("alternatives", [])
        if isinstance(alternatives, list):
            for alt in alternatives:
                if not isinstance(alt, dict):
                    continue
                path = alt.get("target_path")
                if isinstance(path, str) and path.strip():
                    ordered_paths.append(path)

        category_limit = max(1, min(category_limit, 3))
        seen = set()
        results: List[Dict[str, str]] = []
        for path in ordered_paths:
            path_clean = compress_whitespace(path)
            key = (group_name, path_clean)
            if key in seen:
                continue
            seen.add(key)
            match = self.category_store.find_category(group_name, path_clean)
            if match:
                results.append(
                    {
                        "id": match["id"],
                        "rakuten_id": match["id"],
                        "name": match["name"],
                        "meru_id": match.get("meru_id", ""),
                        "rakuma_id": match.get("rakuma_id", ""),
                        "zenplus_id": match.get("zenplus_id", ""),
                    }
                )
            if len(results) >= category_limit:
                break

        return results, parsed, attempts
```

> Note `parsed` is guaranteed to be a `dict` (caller raised `LLMParseError` otherwise), so the explicit `if parsed is None` block from the previous implementation is gone.

- [ ] **Step 11.2: Smoke import**

```bash
uv run python -c "from app.service import MercariAnalyzer; print('ok')"
```

Expected: `ok`.

- [ ] **Step 11.3: Commit**

```bash
git add app/service.py
git commit -m "refactor(service): category call delegates to ResilientCaller; drop in-method retries"
```

---

## Task 12: `analyze` / `analyze_title` — accumulate attempts, expose via `_debug`, update title fallback

**Files:**
- Modify: `app/service.py` (`analyze`, `analyze_title`, `_classify_image_to_paths`)
- Create: `tests/test_service_error_paths.py`

- [ ] **Step 12.1: Update the orchestrating methods**

In `app/service.py`:

(a) Replace `analyze` with:

```python
    def analyze(
        self,
        images: List[Tuple[bytes, str]],
        language: str,
        debug: bool = False,
        category_limit: int = 1,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
        image_processing: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        if not images:
            raise BadRequestError("Image list is required.")
        category_limit = max(1, min(int(category_limit), 3))
        total_started = time.monotonic()
        attempts_by_stage: Dict[str, List[Any]] = {}

        data_urls = [
            image_bytes_to_data_url(image_bytes, mime_type)
            for image_bytes, mime_type in images
        ]
        vision_started = time.monotonic()
        ai_raw, ai_full, vision_attempts = self._call_vision_llm(
            data_urls,
            language,
            model_override=vision_model_override,
        )
        attempts_by_stage["vision"] = vision_attempts
        vision_ms = round((time.monotonic() - vision_started) * 1000, 2)

        title = _clean_string(ai_raw.get("title", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        description_text = _description_to_text(description_struct)
        tax_excluded = _normalize_direct_price(ai_raw.get("tax_excluded"))
        tax_included = _normalize_direct_price(ai_raw.get("tax_included"))
        if tax_excluded is None:
            tax_included = None
        prices = [] if tax_excluded is not None else normalize_price_list(ai_raw.get("prices", []))
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        brand_match = self.brand_store.match(brand_raw)
        brand_name = brand_match["brand_name"] if brand_match else ""
        brand_id_obj = dict(brand_match["brand_id_obj"]) if brand_match else empty_brand_id_obj()

        group_name = _map_top_level_category(top_level_category)

        categories: List[Dict[str, str]] = []
        llm_category_raw: Optional[Dict[str, Any]] = None
        category_ms = 0.0

        if group_name:
            category_started = time.monotonic()
            categories, llm_category_raw, category_attempts = self._choose_categories(
                title=title or ai_raw.get("title", ""),
                description=description_text or _description_to_text(ai_raw.get("description", "")),
                brand_for_prompt=brand_raw or brand_name,
                group_name=group_name,
                category_limit=category_limit,
                model_override=category_model_override,
            )
            attempts_by_stage["category"] = category_attempts
            category_ms = round((time.monotonic() - category_started) * 1000, 2)

        result: Dict[str, Any] = {
            "title": title,
            "description": description_struct,
            "tax_excluded": tax_excluded,
            "tax_included": tax_included,
            "prices": prices,
            "categories": categories,
            "brand_name": brand_name,
            "brand_id_obj": brand_id_obj,
            "timings": {
                "total_ms": 0.0,
                "vision_ms": vision_ms,
                "category_ms": category_ms,
            },
        }
        if image_processing:
            result["image_processing"] = image_processing
        path_info = _paths_from_categories(categories, include_alternatives=False)
        if path_info:
            result.update(path_info)

        if debug:
            result["_debug"] = {
                "ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
                "attempts": {
                    stage: [a.__dict__ for a in attempts]
                    for stage, attempts in attempts_by_stage.items()
                },
            }

        result["timings"]["total_ms"] = round((time.monotonic() - total_started) * 1000, 2)
        return result
```

(b) Update `analyze_title` to catch `LLMAllAttemptsFailedError` (instead of `LLMRequestError`) and propagate properly:

```python
    def analyze_title(
        self,
        title: str,
        image_url: Optional[str],
        language: str,
        category_limit: int = 3,
        category_model_override: Optional[str] = None,
        vision_model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")

        title_clean = _clean_string(title)
        if not title_clean:
            raise BadRequestError("Title is required.")

        category_limit = max(1, min(int(category_limit), 3))
        categories: List[Dict[str, str]] = []
        title_error: Optional[Exception] = None

        try:
            title_payload, _title_attempts = self._call_title_category_llm(
                title=title_clean,
                language=language,
                model_override=category_model_override,
            )
            top_level_category = _clean_string(title_payload.get("top_level_category", ""))
            group_name = _map_top_level_category(top_level_category)
            if group_name:
                categories, _, _category_attempts = self._choose_categories(
                    title=title_clean,
                    description="",
                    brand_for_prompt="",
                    group_name=group_name,
                    category_limit=category_limit,
                    model_override=category_model_override,
                )
        except (BadRequestError, LLMAllAttemptsFailedError) as exc:
            title_error = exc

        paths_result = _paths_from_categories(categories)
        if paths_result:
            return paths_result

        if not image_url:
            if isinstance(title_error, LLMAllAttemptsFailedError):
                raise title_error
            if title_error is not None:
                raise BadRequestError(
                    f"Title classification failed; image_url is required for fallback. ({title_error})"
                ) from title_error
            raise BadRequestError("Title classification failed; image_url is required for fallback.")

        try:
            image_bytes, mime_type = fetch_image_from_url(
                image_url=image_url,
                timeout=self.settings.request_timeout,
                max_bytes=self.settings.max_image_bytes,
                allowed_mime_types=self.settings.allowed_mime_types,
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

        fallback_result = self._classify_image_to_paths(
            image_bytes=image_bytes,
            mime_type=mime_type,
            language=language,
            category_limit=category_limit,
            vision_model_override=vision_model_override,
            category_model_override=category_model_override,
        )
        if fallback_result:
            return fallback_result

        raise BadRequestError("Image recognition failed to return a category path.")
```

(c) Update `_classify_image_to_paths` to accept the new tuple shape:

```python
    def _classify_image_to_paths(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        category_limit: int = 3,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        ai_raw, _, _ = self._call_vision_llm(
            [data_url],
            language,
            model_override=vision_model_override,
        )

        title = _clean_string(ai_raw.get("title", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        description_text = _description_to_text(description_struct)
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        group_name = _map_top_level_category(top_level_category)
        if not group_name:
            return None

        categories, _, _ = self._choose_categories(
            title=title or ai_raw.get("title", ""),
            description=description_text or _description_to_text(ai_raw.get("description", "")),
            brand_for_prompt=brand_raw,
            group_name=group_name,
            category_limit=category_limit,
            model_override=category_model_override,
        )

        return _paths_from_categories(categories)
```

(d) The errors import was already updated in Task 9.1; no further change needed here. If `grep -n LLMRequestError app/service.py` shows zero remaining references, the engineer may drop `LLMRequestError` from the import for cleanliness.

- [ ] **Step 12.2: Add service-level error path tests**

Create `tests/test_service_error_paths.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

from app.errors import BadRequestError, LLMAllAttemptsFailedError, LLMRequestError
from app.service import MercariAnalyzer
from app.config import Settings
from app.data.brands import BrandStore
from app.data.categories import CategoryStore


def _build_analyzer(monkeypatched_clients):
    settings = Settings(
        openrouter_api_key="key",
        vision_model="m1",
        category_model="m1",
        vision_fallback_models=["fb1"],
        category_fallback_models=["fb1"],
        model_call_max_retries=0,        # 1 attempt per model; speed up test
        model_call_total_budget_seconds=10,
        request_timeout=10,
    )
    brand_store = MagicMock(spec=BrandStore)
    brand_store.match.return_value = None
    category_store = MagicMock(spec=CategoryStore)
    category_store.get_categories_by_group.return_value = []
    return MercariAnalyzer(
        settings=settings,
        brand_store=brand_store,
        category_store=category_store,
        vision_client=monkeypatched_clients["vision"],
        category_client=monkeypatched_clients["category"],
    )


class ServiceErrorPathsTest(unittest.TestCase):
    @patch("app.llm.resilient.time.sleep")
    def test_vision_full_failure_raises(self, _sleep):
        vision_client = MagicMock()
        vision_client.chat.side_effect = LLMRequestError("OpenRouter returned 503: x")
        category_client = MagicMock()
        analyzer = _build_analyzer({"vision": vision_client, "category": category_client})

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            analyzer.analyze(
                images=[(b"\x89PNG\r\n\x1a\n", "image/png")],
                language="en",
            )
        self.assertEqual(ctx.exception.stage, "vision")
        self.assertEqual(len(ctx.exception.attempts), 2)  # primary + 1 fallback

    @patch("app.llm.resilient.time.sleep")
    def test_vision_ok_category_full_failure_raises(self, _sleep):
        vision_client = MagicMock()
        ai_raw_payload = (
            '{"title":"x","description":"d","top_level_category":"花・ガーデン・DIY",'
            '"brand_name":"","tax_excluded":null,"tax_included":null,"prices":[]}'
        )
        vision_client.chat.return_value = (ai_raw_payload, {"choices": [{"message": {"content": ai_raw_payload}}]})
        category_client = MagicMock()
        category_client.chat.side_effect = LLMRequestError("OpenRouter returned 503: x")

        analyzer = _build_analyzer({"vision": vision_client, "category": category_client})
        # Make the category store return at least one candidate so _choose_categories runs the LLM call
        analyzer.category_store.get_categories_by_group.return_value = [{"name": "花・ガーデン・DIY/foo", "id": "1"}]

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            analyzer.analyze(
                images=[(b"\x89PNG\r\n\x1a\n", "image/png")],
                language="en",
            )
        self.assertEqual(ctx.exception.stage, "category")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 12.3: Run service tests and confirm pass**

```bash
uv run python -m unittest tests.test_service_error_paths tests.test_service_rakuten_id -v
```

Expected: all tests OK.

- [ ] **Step 12.4: Commit**

```bash
git add app/service.py tests/test_service_error_paths.py
git commit -m "feat(service): propagate LLMAllAttemptsFailedError; surface attempts in _debug"
```

---

## Task 13: `main.py` error response formatting

**Files:**
- Modify: `main.py:207-254`
- Create: `tests/test_main_error_response.py`

- [ ] **Step 13.1: Write failing tests for the structured error response**

Create `tests/test_main_error_response.py`:

```python
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.errors import BadRequestError, LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord
import main as main_module


class MainErrorResponseTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)

    @patch.object(main_module, "analyzer")
    def test_llm_all_attempts_failed_returns_structured_502(self, analyzer):
        attempts = [
            AttemptRecord(
                model="m1", attempt=1, attempt_global=1,
                error_kind="request_failed", message="OpenRouter returned 503: x",
                latency_ms=1234.5, status_code=503,
            ),
            AttemptRecord(
                model="m1", attempt=2, attempt_global=2,
                error_kind="parse_failed", message="JSON decode failed",
                latency_ms=42.0, status_code=200,
            ),
        ]
        analyzer.analyze.side_effect = LLMAllAttemptsFailedError("vision", attempts)

        resp = self.client.post(
            "/api/v1/mercari/image/analyze",
            files=[("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "en"},
        )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        detail = body["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["stage"], "vision")
        self.assertEqual(detail["kind"], "all_attempts_failed")
        self.assertEqual(len(detail["attempts"]), 2)
        self.assertEqual(detail["attempts"][0]["error_kind"], "request_failed")
        self.assertEqual(detail["attempts"][0]["status_code"], 503)
        self.assertEqual(detail["attempts"][1]["error_kind"], "parse_failed")

    @patch.object(main_module, "analyzer")
    def test_bad_request_returns_string_400(self, analyzer):
        analyzer.analyze.side_effect = BadRequestError("Image list is required.")

        resp = self.client.post(
            "/api/v1/mercari/image/analyze",
            files=[("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "en"},
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIsInstance(body["detail"], str)
        self.assertEqual(body["detail"], "Image list is required.")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 13.2: Run and confirm failure**

```bash
uv run python -m unittest tests.test_main_error_response -v
```

Expected: detail returned as string (`502: ...`) rather than dict.

- [ ] **Step 13.3: Update `main.py`**

(a) Add the new import at the top:

```python
from app.errors import BadRequestError, LLMAllAttemptsFailedError, LLMRequestError
```

(b) Add a helper near the top of `main.py` (just below imports):

```python
def _format_attempts_error(exc: LLMAllAttemptsFailedError) -> Dict[str, Any]:
    return {
        "message": f"{exc.stage} stage failed after {len(exc.attempts)} attempt(s).",
        "stage": exc.stage,
        "kind": "all_attempts_failed",
        "attempts": [
            {
                "model": a.model,
                "attempt": a.attempt,
                "attempt_global": a.attempt_global,
                "error_kind": a.error_kind,
                "message": a.message,
                "status_code": a.status_code,
                "latency_ms": a.latency_ms,
            }
            for a in exc.attempts
        ],
    }
```

(c) Replace the `try/except` block inside `analyze_image` with:

```python
    try:
        result = await run_in_threadpool(
            analyzer.analyze,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            category_limit=category_count,
            vision_model_override=vision_model,
            category_model_override=category_model,
            image_processing=image_processing,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
```

(d) Replace the `try/except` block inside `analyze_title` similarly:

```python
    try:
        result = await run_in_threadpool(
            analyzer.analyze_title,
            title=request.title,
            image_url=request.image_url,
            language=language,
        )
    except BadRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LLMAllAttemptsFailedError as exc:
        raise HTTPException(status_code=502, detail=_format_attempts_error(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Internal server error.") from exc
```

The legacy `except LLMRequestError` clause is removed in both — the resilient layer will never raise it through to `main.py`.

- [ ] **Step 13.4: Run and confirm pass**

```bash
uv run python -m unittest tests.test_main_error_response tests.test_config_api -v
```

Expected: all tests OK.

- [ ] **Step 13.5: Commit**

```bash
git add main.py tests/test_main_error_response.py
git commit -m "feat(api): structured error body for LLMAllAttemptsFailedError"
```

---

## Task 14: Frontend — image-flow structured error rendering + `payload.detail` fallback

**Files:**
- Modify: `web/index.html` (around lines 1478-1485, 1985-2050, 2118-2160)

- [ ] **Step 14.1: Add a `formatStructuredError` helper near `formatResultData`**

Open `web/index.html` and locate the `formatResultData` function (around line 1478). Insert the new helper **directly above** it:

```javascript
      function formatStructuredError(structured) {
        if (!structured || typeof structured !== "object") return "";
        const stage = String(structured.stage || "");
        const kind = String(structured.kind || "");
        const attempts = Array.isArray(structured.attempts) ? structured.attempts : [];
        const stageLabel = {
          vision: "图片识别",
          category: "类别选择",
          title_category: "标题分类",
        }[stage] || stage || "LLM";
        const headerMsg = structured.message || `${stageLabel} 失败：尝试 ${attempts.length} 次后均失败。`;

        const rows = attempts.map((a, idx) => {
          const errLabel = {
            request_failed: "请求失败",
            parse_failed: "解析失败",
            budget_exhausted: "预算耗尽",
            ok: "成功",
          }[a.error_kind] || a.error_kind || "未知";
          const status = a.status_code != null ? `HTTP ${a.status_code}` : "—";
          const latency = Number.isFinite(a.latency_ms) ? `${Math.round(a.latency_ms)}ms` : "—";
          const msg = String(a.message || "").replace(/[<>]/g, "");
          return `<div class="attempt-row">[${String(idx + 1).padStart(2, "0")}] <code>${a.model}</code> · attempt ${a.attempt} · <span class="attempt-kind ${a.error_kind}">${errLabel}</span> · ${status} · ${latency}<div class="attempt-msg">${msg}</div></div>`;
        }).join("");

        return `
          <div class="field-value structured-error">
            <div class="structured-error-header">❌ ${headerMsg}</div>
            <details class="structured-error-details">
              <summary>查看 ${attempts.length} 次尝试详情</summary>
              ${rows}
            </details>
          </div>
        `;
      }
```

- [ ] **Step 14.2: Update `formatResultData` to dispatch on structured detail**

In `formatResultData` (the function declared near line 1479), the current opening block is exactly:

```javascript
      function formatResultData(payload) {
        if (payload.error) {
          return `<div class="field-value" style="color: var(--danger);">❌ ${payload.error}</div>`;
        }

        const data = payload.data || payload;
        let html = '';
```

Replace **only those lines** (down to and including the `let html = '';` line) with:

```javascript
      function formatResultData(payload) {
        if (payload && payload.detail && typeof payload.detail === "object" && payload.detail.kind === "all_attempts_failed") {
          return formatStructuredError(payload.detail);
        }
        const errMsg = payload?.error || (typeof payload?.detail === "string" ? payload.detail : "");
        if (errMsg) {
          return `<div class="field-value" style="color: var(--danger);">❌ ${errMsg}</div>`;
        }

        const data = payload.data || payload;
        let html = '';
```

Everything below `let html = '';` stays untouched — do not modify the data rendering body of the function.

- [ ] **Step 14.3: Add minimal CSS for the structured error rendering**

In the `<style>` block (find an existing rule like `.preview-item.error` near line 218), append:

```css
      .structured-error { color: var(--danger); display: block; }
      .structured-error-header { font-weight: 600; margin-bottom: 6px; }
      .structured-error-details summary { cursor: pointer; font-size: 13px; color: var(--muted); margin-top: 4px; }
      .structured-error-details .attempt-row { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; padding: 6px 0; border-bottom: 1px dashed var(--border); }
      .structured-error-details .attempt-row:last-child { border-bottom: none; }
      .structured-error-details .attempt-msg { color: var(--muted); margin-top: 2px; white-space: pre-wrap; word-break: break-word; }
      .attempt-kind.request_failed { color: var(--danger); }
      .attempt-kind.parse_failed { color: #d97706; }
      .attempt-kind.budget_exhausted { color: var(--muted); }
      .attempt-kind.ok { color: var(--success); }
```

- [ ] **Step 14.4: Image submit handler — use `JSON.parse` result directly (no logic change required for image flow)**

The image submit handler around lines 1995-2050 already does:

```javascript
const text = await resp.text();
let payload = {};
try { payload = JSON.parse(text); } catch (err) { payload = { error: t("jsonParseFailed"), raw: text }; }
addResultCard(batchFileObj, payload, resp.ok);
```

This already feeds `payload` (which now contains `detail` as a dict for our error case) into `addResultCard` → `formatResultData`. No change needed in the submit handler itself; the dispatch we added in Step 14.2 handles it.

- [ ] **Step 14.5: Manual smoke check (cannot be automated)**

Start the API + UI:

```bash
uv run uvicorn main:app --port 8000 &
```

Open `http://localhost:8000/` (or use `web/index.html` directly in a browser pointed to the API endpoint). Then:

1. Set `OPENROUTER_API_KEY=""` in `.env`, restart the server, upload an image. Expect the result card to show "图片识别 失败：…" with a collapsible "查看 N 次尝试详情" listing each attempt's model, kind, status code.
2. Restore the API key, set `VISION_MODEL=bogus/foo`, restart, upload. Confirm the fallback chain progresses through models in order.

(If the manual check fails, debug and re-commit before moving on.)

- [ ] **Step 14.6: Commit**

```bash
git add web/index.html
git commit -m "feat(web): structured error rendering for LLM all-attempts-failed responses"
```

---

## Task 15: Frontend — title flow uses the same dispatch + `payload.detail` fallback bug fix

**Files:**
- Modify: `web/index.html` (around lines 2080-2110)

- [ ] **Step 15.1: Update `renderTitleResult` to dispatch on structured detail**

The current implementation of `renderTitleResult` (at line 1803 of `web/index.html`) is exactly:

```javascript
      function renderTitleResult(payload, success) {
        if (!payload) {
          titleResult.innerHTML = "";
          return;
        }
        if (!success) {
          const message = payload.error || payload.detail || t("titleError");
          titleResult.innerHTML = `<div class="field-value" style="color: var(--danger);">${escapeHtml(String(message))}</div>`;
          return;
        }
```

Replace **only the `if (!success) { ... }` block** with:

```javascript
        if (!success) {
          if (payload.detail && typeof payload.detail === "object" && payload.detail.kind === "all_attempts_failed") {
            titleResult.innerHTML = formatStructuredError(payload.detail);
          } else {
            const message = (typeof payload.detail === "string" && payload.detail)
              || payload.error || t("titleError");
            titleResult.innerHTML = `<div class="field-value" style="color: var(--danger);">${escapeHtml(String(message))}</div>`;
          }
          return;
        }
```

Leave everything below the `if (!success)` block (the success rendering at line 1813 onwards) untouched.

- [ ] **Step 15.2: Stop the title submit handler from collapsing structured detail into a string**

The current submit handler (around line 2098) does:

```javascript
          if (resp.ok) {
            renderTitleResult(data, true);
          } else {
            const message = data.detail || data.error || text || t("titleError");
            renderTitleResult({ error: message }, false);
          }
```

Replace those five lines with:

```javascript
          if (resp.ok) {
            renderTitleResult(data, true);
          } else {
            renderTitleResult(data, false);
          }
```

This passes the full response (including `data.detail` as a dict) to `renderTitleResult`, where the new dispatch from Step 15.1 takes over.

- [ ] **Step 15.3: Manual smoke check**

Restart server. Submit a title with a clearly broken model configuration (e.g. `CATEGORY_MODEL=bogus/foo`, no fallbacks reachable). Confirm the title result panel shows the structured error with attempts.

- [ ] **Step 15.4: Commit**

```bash
git add web/index.html
git commit -m "feat(web): title flow renders structured LLM error and surfaces detail string"
```

---

## Task 16: Config page — multiline textareas + new fields

**Files:**
- Modify: `web/config.html`

- [ ] **Step 16.1: Inspect existing field card layout**

```bash
uv run python -c "import pathlib; print(pathlib.Path('web/config.html').read_text(encoding='utf-8')[6000:12000])"
```

Expected: see the current cards for `CATEGORY_LLM_RETRY_ENABLED` / `CATEGORY_LLM_MAX_RETRIES` and the JS that builds the request payload. Note the pattern (key, label, type) used.

- [ ] **Step 16.2: Replace deprecated cards with new ones**

In `web/config.html`, locate the JS object/array that drives field rendering (search for `CATEGORY_LLM_RETRY_ENABLED`). Remove the two deprecated entries. Add four new entries with the field shapes below. Use the file's existing pattern for object/array definitions — do not invent new shapes. Example for an array-of-objects layout:

```javascript
        // Field definitions (shape: matches existing CONTROLS array in this file)
        // Remove:
        //   { key: "CATEGORY_LLM_RETRY_ENABLED", type: "bool", label: "Category retry enabled" },
        //   { key: "CATEGORY_LLM_MAX_RETRIES", type: "int", label: "Category max retries" },
        // Add:
        { key: "MODEL_CALL_MAX_RETRIES", type: "int", label: "模型调用重试次数（默认 3）" },
        { key: "MODEL_CALL_TOTAL_BUDGET_SECONDS", type: "int", label: "单阶段总预算（秒，默认 120）" },
        { key: "VISION_FALLBACK_MODELS", type: "multiline_str", label: "图片识别回退模型（一行一个）" },
        { key: "CATEGORY_FALLBACK_MODELS", type: "multiline_str", label: "分类回退模型（一行一个）" },
```

- [ ] **Step 16.3: Add `multiline_str` rendering and serialization**

Find the function that renders an input (search for the `case "bool"` or `if (field.type === "bool")` branch). Add a `multiline_str` branch:

(a) Rendering — produce a `<textarea>` with the array values joined on `\n`:

```javascript
        if (field.type === "multiline_str") {
          const value = Array.isArray(currentConfig[field.key]) ? currentConfig[field.key].join("\n") : "";
          return `
            <div class="card field">
              <label for="cfg-${field.key}">${field.label}</label>
              <textarea id="cfg-${field.key}" rows="6" data-key="${field.key}" data-type="multiline_str">${escapeHtml(value)}</textarea>
            </div>
          `;
        }
```

(b) Serialization — when collecting form values, when the input has `data-type="multiline_str"`, split the textarea value on `\n`, trim, drop empty lines, and add as an array:

```javascript
        if (el.dataset.type === "multiline_str") {
          const lines = String(el.value || "")
            .split(/\r?\n/)
            .map((s) => s.trim())
            .filter(Boolean);
          payload[el.dataset.key] = lines;
        } else if (el.dataset.type === "bool") {
          payload[el.dataset.key] = el.checked;
        } else if (el.dataset.type === "int") {
          payload[el.dataset.key] = Number(el.value);
        } else {
          payload[el.dataset.key] = el.value;
        }
```

> If `web/config.html` uses a different naming convention than `currentConfig` / `escapeHtml`, adapt to the names already in scope. Do **not** introduce a new escaping helper if one exists.

- [ ] **Step 16.4: Manual smoke check**

```bash
uv run uvicorn main:app --port 8000
```

Open `http://localhost:8000/config`:

1. The two old "Category retry" cards are gone.
2. The four new cards render. Textareas show the default 7 models pre-filled.
3. Edit `VISION_FALLBACK_MODELS`, save, reload — values round-trip via `.env`.
4. `.env` on disk shows `VISION_FALLBACK_MODELS=…,…` on one line.

- [ ] **Step 16.5: Commit**

```bash
git add web/config.html
git commit -m "feat(web): config page supports multiline_str; swap deprecated category-retry cards"
```

---

## Task 17: Documentation — README + API.md

**Files:**
- Modify: `README.md`
- Modify: `API.md`

- [ ] **Step 17.1: Update `README.md`**

Open `README.md`. In the "Environment variables" section:

(a) Remove the two bullets:
- `CATEGORY_LLM_RETRY_ENABLED`
- `CATEGORY_LLM_MAX_RETRIES`

(b) Remove the trailing paragraph: `Category retries apply only to the category selection step…`

(c) Add the four new bullets after `REQUEST_TIMEOUT`:

```markdown
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
```

(d) Add a new paragraph:

```markdown
Every LLM stage retries the primary model up to `MODEL_CALL_MAX_RETRIES + 1` times with
exponential backoff (0.2s, 0.4s, 0.8s, capped at 1.5s and clamped by remaining budget),
then walks the fallback list (one attempt each, no inter-model backoff). Both OpenRouter
request errors and JSON parse failures feed the same retry/fallback loop. Once every
attempt has failed the stage raises an error that the API surfaces as a structured 502
response (see `API.md`). The deprecated `CATEGORY_LLM_RETRY_ENABLED` /
`CATEGORY_LLM_MAX_RETRIES` settings have been removed.
```

(e) In the "Runtime configuration page" section, replace `CATEGORY_LLM_RETRY_ENABLED` and `CATEGORY_LLM_MAX_RETRIES` in the bullet list with:

```markdown
- `MODEL_CALL_MAX_RETRIES`
- `MODEL_CALL_TOTAL_BUDGET_SECONDS`
- `VISION_FALLBACK_MODELS`
- `CATEGORY_FALLBACK_MODELS`
```

- [ ] **Step 17.2: Update `API.md`**

Open `API.md`. After the "## 接口列表" section's `POST /api/v1/mercari/image/analyze` entry, locate the `#### 错误` block:

```
- `400`：请求无效（图片格式/参数错误、解析失败等）。
- `502`：LLM 请求失败。
- `500`：内部错误。
```

Replace it with:

```markdown
- `400`：请求无效（图片格式/参数错误、参数校验失败）。`detail` 为字符串。
- `502`：LLM 链路全部尝试失败。`detail` 为对象，结构如下：

  ```json
  {
    "detail": {
      "message": "vision stage failed after 8 attempt(s).",
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
          "model": "google/gemini-2.5-flash",
          "attempt": 1,
          "attempt_global": 5,
          "error_kind": "parse_failed",
          "message": "JSON decode failed: ...",
          "status_code": 200,
          "latency_ms": 8123.7
        }
      ]
    }
  }
  ```

  字段约束：
  - `stage` ∈ `"vision"`, `"category"`, `"title_category"`。
  - `kind` 当前固定为 `"all_attempts_failed"`，保留供未来扩展。
  - `attempts[].error_kind` ∈ `"request_failed"`, `"parse_failed"`, `"budget_exhausted"`。
  - `attempts[].status_code` 在 `parse_failed` 时为 200；`request_failed` 时尽力从上游消息中解析整数 HTTP 状态码，否则 `null`。
- `500`：内部错误，`detail` 为字符串。
```

Apply the same replacement under `POST /api/v1/mercari/title/analyze`'s `#### 错误` block.

- [ ] **Step 17.3: Run all tests one last time**

```bash
uv run python -m unittest discover -s tests -v
```

Expected: all suites OK.

- [ ] **Step 17.4: Commit**

```bash
git add README.md API.md
git commit -m "docs: error handling redesign — env vars and structured 502 schema"
```

---

## Final Verification Checklist

After all tasks land:

- [ ] `uv run python -m unittest discover -s tests -v` passes.
- [ ] `uv run uvicorn main:app --port 8000` boots, `/health` returns 200.
- [ ] `/config` page renders, shows the 4 new cards, hides the 2 removed ones, save round-trips through `.env`.
- [ ] Manual: invalid `OPENROUTER_API_KEY` → image upload renders structured error with attempts list.
- [ ] Manual: valid key, `VISION_MODEL=google/gemini-3-flash-preview` (real) → success.
- [ ] Manual: temporarily set `VISION_MODEL=bogus/foo` → fallback chain visibly progresses; result either succeeds via fallback or shows full attempts.
- [ ] `git log --oneline | head -20` shows the commits in the order Tasks 1-17 above.
