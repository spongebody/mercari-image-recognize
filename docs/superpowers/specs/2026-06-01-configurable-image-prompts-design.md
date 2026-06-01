# 可配置图片识别提示词 (Configurable Image-Recognition Prompts)

**Date:** 2026-06-01
**Status:** Approved design, ready for implementation plan
**Scope (MVP):** Make all 8 image-recognition / taxonomy prompts editable from the config page, loaded dynamically at request time so edits apply with **no service restart**, without changing behavior when no override exists.

---

## 1. Problem & Goals

Prompts currently live as module-level string constants in `app/llm/prompts.py` and are imported by name into `app/service.py` at module load. Tuning a prompt requires a code edit and a restart.

Goals:

1. **Dynamic, real-time loading** — prompts are looked up at LLM-call time from a runtime store; edits via the config page take effect on the next request, no restart.
2. **Non-invasive** — when no override is set, the rendered prompt string is **byte-for-byte identical** to today's behavior. The existing chain and logic are untouched on the no-override path.
3. **MVP scope** — the 8 image-recognition / taxonomy prompts (system + user each = 16 editable entries):
   - `FAST_CLASSIFICATION` (system + user)
   - `TITLE_IMAGE_FALLBACK` (system + user)
   - `PRICE_ONLY` (system + user)
   - `PRODUCT_DATA` (system + user)
   - `PRODUCT_DATA_REGENERATION` (system + user)
   - `PRODUCT_DATA_FALLBACK` (system + user)
   - `PRODUCT_TITLE_CATEGORY` (system + user)
   - `CATEGORY` (system + user)

Non-goals (deferred): version history / rollback, per-environment prompt sets, DB-backed storage, editing non–image-recognition prompts (e.g. showcase prompts).

---

## 2. Current Chain (as built)

- `app/llm/prompts.py` defines the constants. Three **system** prompts concatenate the live `TOP_LEVEL_CATEGORY_OPTIONS` (derived from `app/constants.py::TOP_LEVEL_CATEGORIES`) at import time:
  `FAST_CLASSIFICATION_SYSTEM_PROMPT`, `TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT`, `PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT`.
- `app/service.py` imports all 16 constants (lines ~18–34) and references them directly at the call sites in `MercariAnalyzer`.
- **System prompts are used raw** (no `.format()`). Several contain literal JSON `{ }` braces in their schema blocks — so they must never be passed through `str.format()`.
- **User prompts are rendered with `.format(**kwargs)`** at the call site.
- An existing runtime-config pattern (`app/runtime_config.py` + `GET/PUT /api/v1/config` + `web/config.html`) mutates a shared `settings` object in place and persists to `.env`; `MercariAnalyzer` holds a reference to the same object, so changes apply live. The prompt feature mirrors this pattern but uses a JSON store (prompts are large multi-line text unsuited to `.env`).

### Required tokens / placeholders per prompt

| Key | Role | Render | Required tokens |
|---|---|---|---|
| `FAST_CLASSIFICATION` system | system | raw + replace | `[[TOP_LEVEL_CATEGORY_OPTIONS]]` |
| `FAST_CLASSIFICATION` user | user | `.format` | `{language_label}` |
| `TITLE_IMAGE_FALLBACK` system | system | raw + replace | `[[TOP_LEVEL_CATEGORY_OPTIONS]]` |
| `TITLE_IMAGE_FALLBACK` user | user | `.format` | `{language_label}` |
| `PRICE_ONLY` system | system | raw | — |
| `PRICE_ONLY` user | user | raw (no kwargs today) | — |
| `PRODUCT_DATA` system | system | raw | — |
| `PRODUCT_DATA` user | user | `.format` | `{language_label}` |
| `PRODUCT_DATA_REGENERATION` system | system | raw | — |
| `PRODUCT_DATA_REGENERATION` user | user | `.format` | `{language_label}`, `{user_notes}`, `{original_product_data_json}` |
| `PRODUCT_DATA_FALLBACK` system | system | raw | — |
| `PRODUCT_DATA_FALLBACK` user | user | `.format` | `{language_label}` |
| `PRODUCT_TITLE_CATEGORY` system | system | raw + replace | `[[TOP_LEVEL_CATEGORY_OPTIONS]]` |
| `PRODUCT_TITLE_CATEGORY` user | user | `.format` | `{title}`, `{language_label}` |
| `CATEGORY` system | system | raw | — |
| `CATEGORY` user | user | `.format` | `{title}`, `{description}`, `{brand}`, `{group_name}`, `{candidate_paths}` |

> Note: `PRICE_ONLY_USER_PROMPT` is currently passed without `.format()`; it has no placeholders, so it stays raw. Its `render_mode` is `format` only if a kwargs call is added; for MVP keep it as-is (no required tokens, format-safe since it has no braces).

---

## 3. Category-Options Handling (correctness-critical)

To keep `TOP_LEVEL_CATEGORY_OPTIONS` authoritative (sourced from `constants.py`) while letting the surrounding text be edited, the 3 category system prompts switch from import-time concatenation to a **sentinel token** `[[TOP_LEVEL_CATEGORY_OPTIONS]]` rendered via `str.replace()` (NOT `.format()`, to avoid breaking literal JSON braces).

- The editable default text for these 3 prompts contains `[[TOP_LEVEL_CATEGORY_OPTIONS]]` at the position where the options list currently appears.
- At the call site: `prompt_store.get(KEY).replace("[[TOP_LEVEL_CATEGORY_OPTIONS]]", TOP_LEVEL_CATEGORY_OPTIONS)`.
- A regression test asserts the rendered default equals the current concatenated constant exactly.

---

## 4. Components

### 4.1 New: `app/llm/prompt_store.py`

```python
@dataclass(frozen=True)
class PromptDef:
    key: str                 # e.g. "FAST_CLASSIFICATION_SYSTEM_PROMPT"
    label: str               # human label, e.g. "快速分类 · System"
    stage: str               # grouping, e.g. "fast_classification"
    role: str                # "system" | "user"
    default_text: str        # sourced from prompts.py constants
    required_tokens: tuple[str, ...]
    render_mode: str         # "raw" | "replace" | "format"
```

- `PROMPT_REGISTRY: tuple[PromptDef, ...]` — the 16 entries, defaults referencing the existing `prompts.py` constants (single source of truth).
- Module-level state: `_overrides: dict[str, str]`, a `threading.Lock`, and `OVERRIDES_PATH = BASE_DIR / "data" / "prompt_overrides.json"`.
- `load_overrides()` — read JSON at startup into `_overrides` (missing/corrupt file → empty, log a warning, fall back to defaults).
- `get(key) -> str` — return `_overrides.get(key, default_text)`. In-memory only; no I/O.
- `list_prompts() -> list[dict]` — registry + effective value + `is_overridden`.
- `update(updates: dict[str, str]) -> list[dict]` — validate every entry, then under the lock replace in-memory values and persist JSON atomically (temp file + `os.replace`). Returns the new list.
- `reset(keys: list[str] | None) -> list[dict]` — drop the given keys (or all) from `_overrides`, persist.
- `validate(key, text)` — `key` must be in registry; every `required_token` must be present; for `render_mode == "format"`, a dry-run `text.format(**{p: "x" for required placeholders})` (using a defaultdict-like stand-in for all `{...}` names) must not raise. Raises `ValueError` with a clear message on failure.

### 4.2 Changed: `app/service.py`

- Remove the 16 direct constant imports; import `from .llm import prompt_store` (and keep `TOP_LEVEL_CATEGORY_OPTIONS` import for the replace step, or expose a helper).
- Replace each constant reference with `prompt_store.get("<KEY>")`, preserving the existing `.format(...)` / `.replace(...)` rendering at the call site. System prompts that need options use `.replace("[[TOP_LEVEL_CATEGORY_OPTIONS]]", TOP_LEVEL_CATEGORY_OPTIONS)`.
- No change to control flow, fallbacks, retries, recording, or merging.

### 4.3 Changed: `main.py`

- `from app.llm import prompt_store`; call `prompt_store.load_overrides()` at startup (lifespan or module init, same place `settings` is built).
- Three endpoints (auth + cross-origin guard mirror `/api/v1/config`):
  - `GET /api/v1/prompts` → `prompt_store.list_prompts()` (public read, like `GET /api/v1/config`).
  - `PUT /api/v1/prompts` (auth + same-origin) → `prompt_store.update(payload)`; `ValueError` → HTTP 400.
  - `POST /api/v1/prompts/reset` (auth + same-origin) → `prompt_store.reset(payload.get("keys"))`.

### 4.4 Changed: `web/config.html`

- New collapsible "提示词配置 (Prompts)" panel below the existing settings.
- 8 prompts grouped by stage; each shows system + user `<textarea>`, a hint line listing `required_tokens`, an "已修改" badge when `is_overridden`, and per-prompt **保存 / 恢复默认** buttons.
- Reuses the page's existing auth header / fetch helpers. Save → `PUT`; Reset → `POST /reset`; inline success/error.

---

## 5. Data Flow

- **Startup:** `prompt_store.load_overrides()` reads `data/prompt_overrides.json` → memory.
- **Read (per LLM call):** `service.py` → `prompt_store.get(KEY)` → render → model. In-memory dict lookup, no I/O.
- **Write (`PUT`):** validate → lock → update memory + atomic JSON write → effective on next request, no restart.
- **Reset:** remove key(s) from overrides → revert to code default.

`data/prompt_overrides.json` is sparse — only edited prompts are stored. Add to `.gitignore` if runtime edits should not be committed (decision: yes, ignore it, consistent with `.env` being runtime state).

---

## 6. Error Handling

- Invalid `PUT` (unknown key, missing required token, format dry-run failure) → HTTP 400 with the offending key and reason; **no partial apply** (validate all before mutating).
- Corrupt/unreadable override file at startup → log warning, treat as empty (all defaults). Service never fails to boot because of a bad override file.
- Atomic persist (temp + `os.replace`) so a crash mid-write cannot corrupt the file.

---

## 7. Testing

- **Unit (`prompt_store`):** default returned with no override; override returned when set; `update` rejects missing token / bad format; `reset` reverts; `load_overrides` tolerates missing/corrupt file.
- **Regression:** rendered defaults for the 3 category prompts (after `[[TOP_LEVEL_CATEGORY_OPTIONS]]` replace) equal the current concatenated constants exactly; the other 13 defaults equal their constants verbatim.
- **API:** `GET` shape; `PUT` happy path + 400 cases + auth/cross-origin rejection; `reset` happy path.
- **Service regression:** existing service/pipeline tests pass unchanged (no-override path identical).

---

## 8. Out of Scope

Version history, rollback UI, multi-profile prompt sets, DB storage, non–image-recognition prompts. The JSON store leaves room to add history later without an API break.
