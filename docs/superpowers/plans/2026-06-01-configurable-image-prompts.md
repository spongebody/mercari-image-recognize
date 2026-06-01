# Configurable Image-Recognition Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 8 image-recognition / taxonomy prompts (system + user = 16 entries) editable from the config page, loaded at LLM-call time so edits apply with no service restart, with byte-identical behavior when no override exists.

**Architecture:** A new `app/llm/prompt_store.py` holds a registry whose defaults are the existing `app/llm/prompts.py` constants, plus a sparse in-memory override layer persisted to `data/prompt_overrides.json`. `app/service.py` reads prompts via `prompt_store.get(KEY)` / `prompt_store.render_system(KEY)` at call time instead of importing constants. Three auth-gated endpoints (`GET/PUT /api/v1/prompts`, `POST /api/v1/prompts/reset`) mirror the existing `/api/v1/config` mechanism, and `web/config.html` gains a prompts panel. The 3 system prompts that embed the live category list switch from import-time concatenation to a `[[TOP_LEVEL_CATEGORY_OPTIONS]]` sentinel rendered via `str.replace()` (never `.format()`, which would break literal JSON braces).

**Tech Stack:** Python 3.11, FastAPI, dataclasses, `unittest` + `fastapi.testclient.TestClient`, vanilla JS in `web/config.html`. Run tests with `.venv/bin/python -m pytest`.

---

## File Structure

- **Create** `app/llm/prompt_store.py` — registry, override layer, validation, persistence, rendering.
- **Modify** `app/llm/prompts.py` — 3 category system prompts use the sentinel token instead of concatenation.
- **Modify** `app/service.py` — replace 16 direct constant references with `prompt_store` lookups; fix imports.
- **Modify** `main.py` — wire prompt store; add 3 endpoints; extract a shared cross-origin guard.
- **Modify** `web/config.html` — add the "提示词配置" panel + JS.
- **Modify** `.gitignore` — ignore `data/prompt_overrides.json`.
- **Create** `tests/data/prompt_category_golden.json` — frozen snapshot of the 3 category system prompts (regression guard).
- **Create** `tests/test_prompt_store.py` — unit tests for the store.
- **Create** `tests/test_prompts_api.py` — API tests.

---

## Task 1: Freeze the category-prompt golden snapshot

This captures the CURRENT rendered value of the 3 concatenated system prompts BEFORE we refactor, so a later test can prove the sentinel rendering is byte-identical.

**Files:**
- Create: `tests/data/prompt_category_golden.json`

- [ ] **Step 1: Create the `tests/data` directory**

Run:
```bash
mkdir -p tests/data
```

- [ ] **Step 2: Capture the current constant values into the golden file**

Run this exactly (it reads the CURRENT, pre-refactor constants):
```bash
.venv/bin/python -c "
import json
from app.llm.prompts import (
    FAST_CLASSIFICATION_SYSTEM_PROMPT,
    TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT,
    PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT,
)
golden = {
    'FAST_CLASSIFICATION_SYSTEM_PROMPT': FAST_CLASSIFICATION_SYSTEM_PROMPT,
    'TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT': TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT,
    'PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT': PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT,
}
with open('tests/data/prompt_category_golden.json', 'w', encoding='utf-8') as f:
    json.dump(golden, f, ensure_ascii=False, indent=2)
print('wrote', len(golden), 'entries')
"
```
Expected output: `wrote 3 entries`

- [ ] **Step 3: Verify the golden file contains the rendered category list**

Run:
```bash
.venv/bin/python -c "
import json
g = json.load(open('tests/data/prompt_category_golden.json', encoding='utf-8'))
assert '1. ' in g['FAST_CLASSIFICATION_SYSTEM_PROMPT'], 'category list missing'
assert '[[TOP_LEVEL_CATEGORY_OPTIONS]]' not in g['FAST_CLASSIFICATION_SYSTEM_PROMPT'], 'should be pre-refactor'
print('golden OK')
"
```
Expected output: `golden OK`

- [ ] **Step 4: Commit**

```bash
git add tests/data/prompt_category_golden.json
git commit -m "test: freeze category system-prompt golden snapshot"
```

---

## Task 2: Refactor the 3 category system prompts to use the sentinel token

Swap import-time concatenation for a literal `[[TOP_LEVEL_CATEGORY_OPTIONS]]` token placed at the exact position the list currently occupies. `TOP_LEVEL_CATEGORY_OPTIONS` stays defined in `prompts.py` (it is reused by `prompt_store`).

**Files:**
- Modify: `app/llm/prompts.py`

- [ ] **Step 1: Refactor `PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT`**

Replace this exact block:
```python
Given a product title, choose the single best matching top-level category from the following list (return exactly one of these strings):
""" + TOP_LEVEL_CATEGORY_OPTIONS + """

IMPORTANT:
- The top_level_category must be exactly one of the provided strings.
```
with:
```python
Given a product title, choose the single best matching top-level category from the following list (return exactly one of these strings):
[[TOP_LEVEL_CATEGORY_OPTIONS]]

IMPORTANT:
- The top_level_category must be exactly one of the provided strings.
```

- [ ] **Step 2: Refactor `TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT`**

Replace this exact block:
```python
3. top_level_category: the single best matching top-level category from this Rakuten-style taxonomy list (return exactly one of these strings):
""" + TOP_LEVEL_CATEGORY_OPTIONS + """

4. brand_name: if you can clearly identify a brand name printed on the item or its packaging,
```
with:
```python
3. top_level_category: the single best matching top-level category from this Rakuten-style taxonomy list (return exactly one of these strings):
[[TOP_LEVEL_CATEGORY_OPTIONS]]

4. brand_name: if you can clearly identify a brand name printed on the item or its packaging,
```

- [ ] **Step 3: Refactor `FAST_CLASSIFICATION_SYSTEM_PROMPT`**

Replace this exact block:
```python
- top_level_category: exactly one top-level category from this list
""" + TOP_LEVEL_CATEGORY_OPTIONS + """

Do not generate brand information, listing copy, detailed description sections, or any price fields.
```
with:
```python
- top_level_category: exactly one top-level category from this list
[[TOP_LEVEL_CATEGORY_OPTIONS]]

Do not generate brand information, listing copy, detailed description sections, or any price fields.
```

- [ ] **Step 4: Verify the constants now contain the sentinel and still import**

Run:
```bash
.venv/bin/python -c "
from app.llm.prompts import FAST_CLASSIFICATION_SYSTEM_PROMPT as a, TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT as b, PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT as c
for name, v in [('fast', a), ('title', b), ('product_title', c)]:
    assert '[[TOP_LEVEL_CATEGORY_OPTIONS]]' in v, name
    assert '    1. ' not in v, name + ' still has baked list'
print('sentinel OK')
"
```
Expected output: `sentinel OK`

- [ ] **Step 5: Verify byte-identical rendering against the golden snapshot**

Run:
```bash
.venv/bin/python -c "
import json
from app.llm.prompts import (TOP_LEVEL_CATEGORY_OPTIONS,
    FAST_CLASSIFICATION_SYSTEM_PROMPT, TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT, PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT)
g = json.load(open('tests/data/prompt_category_golden.json', encoding='utf-8'))
T = '[[TOP_LEVEL_CATEGORY_OPTIONS]]'
assert FAST_CLASSIFICATION_SYSTEM_PROMPT.replace(T, TOP_LEVEL_CATEGORY_OPTIONS) == g['FAST_CLASSIFICATION_SYSTEM_PROMPT']
assert TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT.replace(T, TOP_LEVEL_CATEGORY_OPTIONS) == g['TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT']
assert PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT.replace(T, TOP_LEVEL_CATEGORY_OPTIONS) == g['PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT']
print('byte-identical OK')
"
```
Expected output: `byte-identical OK`

- [ ] **Step 6: Commit**

```bash
git add app/llm/prompts.py
git commit -m "refactor: use category-options sentinel in system prompts"
```

---

## Task 3: Create `prompt_store.py` — registry, read, render, load

**Files:**
- Create: `app/llm/prompt_store.py`
- Test: `tests/test_prompt_store.py`

- [ ] **Step 1: Write `app/llm/prompt_store.py` (read/render/load only)**

```python
from __future__ import annotations

import json
import logging
import os
import string
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import BASE_DIR
from . import prompts as _p
from .prompts import TOP_LEVEL_CATEGORY_OPTIONS

logger = logging.getLogger(__name__)

CATEGORY_OPTIONS_TOKEN = "[[TOP_LEVEL_CATEGORY_OPTIONS]]"
OVERRIDES_PATH = BASE_DIR / "data" / "prompt_overrides.json"


@dataclass(frozen=True)
class PromptDef:
    key: str
    label: str
    stage: str
    role: str  # "system" | "user"
    default_text: str
    required_tokens: Tuple[str, ...]


PROMPT_REGISTRY: Tuple[PromptDef, ...] = (
    PromptDef("FAST_CLASSIFICATION_SYSTEM_PROMPT", "快速分类 · System", "fast_classification", "system", _p.FAST_CLASSIFICATION_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("FAST_CLASSIFICATION_USER_PROMPT", "快速分类 · User", "fast_classification", "user", _p.FAST_CLASSIFICATION_USER_PROMPT, ("{language_label}",)),
    PromptDef("TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT", "标题图片兜底 · System", "title_image_fallback", "system", _p.TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("TITLE_IMAGE_FALLBACK_USER_PROMPT", "标题图片兜底 · User", "title_image_fallback", "user", _p.TITLE_IMAGE_FALLBACK_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRICE_ONLY_SYSTEM_PROMPT", "价格提取 · System", "price_only", "system", _p.PRICE_ONLY_SYSTEM_PROMPT, ()),
    PromptDef("PRICE_ONLY_USER_PROMPT", "价格提取 · User", "price_only", "user", _p.PRICE_ONLY_USER_PROMPT, ()),
    PromptDef("PRODUCT_DATA_SYSTEM_PROMPT", "商品数据 · System", "product_data", "system", _p.PRODUCT_DATA_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_USER_PROMPT", "商品数据 · User", "product_data", "user", _p.PRODUCT_DATA_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT", "商品数据再生成 · System", "product_data_regeneration", "system", _p.PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_REGENERATION_USER_PROMPT", "商品数据再生成 · User", "product_data_regeneration", "user", _p.PRODUCT_DATA_REGENERATION_USER_PROMPT, ("{language_label}", "{user_notes}", "{original_product_data_json}")),
    PromptDef("PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT", "商品数据兜底 · System", "product_data_fallback", "system", _p.PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_FALLBACK_USER_PROMPT", "商品数据兜底 · User", "product_data_fallback", "user", _p.PRODUCT_DATA_FALLBACK_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT", "标题选类目 · System", "title_category", "system", _p.PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("PRODUCT_TITLE_CATEGORY_USER_PROMPT", "标题选类目 · User", "title_category", "user", _p.PRODUCT_TITLE_CATEGORY_USER_PROMPT, ("{title}", "{language_label}")),
    PromptDef("CATEGORY_SYSTEM_PROMPT", "类目匹配 · System", "category", "system", _p.CATEGORY_SYSTEM_PROMPT, ()),
    PromptDef("CATEGORY_USER_PROMPT_TEMPLATE", "类目匹配 · User", "category", "user", _p.CATEGORY_USER_PROMPT_TEMPLATE, ("{title}", "{description}", "{brand}", "{group_name}", "{candidate_paths}")),
)

_REGISTRY_BY_KEY: Dict[str, PromptDef] = {d.key: d for d in PROMPT_REGISTRY}
_overrides: Dict[str, str] = {}
_lock = threading.Lock()


def load_overrides() -> None:
    """Load the sparse override file into memory. Never raises."""
    global _overrides
    try:
        raw = OVERRIDES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        _overrides = {}
        return
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring unreadable prompt overrides at %s: %s", OVERRIDES_PATH, exc)
        _overrides = {}
        return
    if not isinstance(data, dict):
        _overrides = {}
        return
    _overrides = {
        key: value
        for key, value in data.items()
        if key in _REGISTRY_BY_KEY and isinstance(value, str)
    }


def get(key: str) -> str:
    """Effective unrendered template (override if set, else default)."""
    definition = _REGISTRY_BY_KEY.get(key)
    if definition is None:
        raise KeyError(key)
    return _overrides.get(key, definition.default_text)


def render_system(key: str) -> str:
    """get(key) with the category-options sentinel replaced by the live list.

    For system prompts without the sentinel this is a no-op, so it is safe to
    call uniformly for every system prompt.
    """
    return get(key).replace(CATEGORY_OPTIONS_TOKEN, TOP_LEVEL_CATEGORY_OPTIONS)


def is_overridden(key: str) -> bool:
    return key in _overrides


def list_prompts() -> List[dict]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "stage": d.stage,
            "role": d.role,
            "value": _overrides.get(d.key, d.default_text),
            "default": d.default_text,
            "required_tokens": list(d.required_tokens),
            "is_overridden": d.key in _overrides,
        }
        for d in PROMPT_REGISTRY
    ]


load_overrides()
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_prompt_store.py`:
```python
import json
import unittest
from pathlib import Path

from app.llm import prompt_store
from app.llm.prompts import TOP_LEVEL_CATEGORY_OPTIONS

GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "prompt_category_golden.json").read_text(encoding="utf-8")
)


class PromptStoreReadTest(unittest.TestCase):
    def setUp(self):
        prompt_store._overrides = {}

    def tearDown(self):
        prompt_store._overrides = {}

    def test_get_returns_default_when_no_override(self):
        from app.llm.prompts import PRODUCT_DATA_SYSTEM_PROMPT
        self.assertEqual(
            prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), PRODUCT_DATA_SYSTEM_PROMPT
        )

    def test_get_returns_override_when_set(self):
        prompt_store._overrides["PRODUCT_DATA_SYSTEM_PROMPT"] = "custom text"
        self.assertEqual(prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), "custom text")

    def test_get_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            prompt_store.get("NOPE")

    def test_render_system_replaces_category_token(self):
        rendered = prompt_store.render_system("FAST_CLASSIFICATION_SYSTEM_PROMPT")
        self.assertNotIn("[[TOP_LEVEL_CATEGORY_OPTIONS]]", rendered)
        self.assertIn(TOP_LEVEL_CATEGORY_OPTIONS, rendered)

    def test_render_system_no_op_without_token(self):
        from app.llm.prompts import PRICE_ONLY_SYSTEM_PROMPT
        self.assertEqual(
            prompt_store.render_system("PRICE_ONLY_SYSTEM_PROMPT"), PRICE_ONLY_SYSTEM_PROMPT
        )

    def test_category_prompts_render_byte_identical_to_golden(self):
        for key in (
            "FAST_CLASSIFICATION_SYSTEM_PROMPT",
            "TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT",
            "PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT",
        ):
            self.assertEqual(prompt_store.render_system(key), GOLDEN[key])

    def test_list_prompts_has_sixteen_entries(self):
        prompts = prompt_store.list_prompts()
        self.assertEqual(len(prompts), 16)
        keys = {p["key"] for p in prompts}
        self.assertIn("CATEGORY_USER_PROMPT_TEMPLATE", keys)
        self.assertFalse(any(p["is_overridden"] for p in prompts))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_prompt_store.py -v`
Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add app/llm/prompt_store.py tests/test_prompt_store.py
git commit -m "feat: add prompt_store registry with dynamic read/render"
```

---

## Task 4: Add validation, update, reset, and persistence to `prompt_store`

**Files:**
- Modify: `app/llm/prompt_store.py`
- Test: `tests/test_prompt_store.py`

- [ ] **Step 1: Write failing tests for validation/update/reset**

Append to `tests/test_prompt_store.py`:
```python
import tempfile
from unittest.mock import patch


class PromptStoreWriteTest(unittest.TestCase):
    def setUp(self):
        prompt_store._overrides = {}
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "prompt_overrides.json"
        self._patch = patch.object(prompt_store, "OVERRIDES_PATH", self._path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()
        prompt_store._overrides = {}

    def test_update_persists_and_applies(self):
        result = prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "new system text"})
        self.assertEqual(prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), "new system text")
        saved = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(saved["PRODUCT_DATA_SYSTEM_PROMPT"], "new system text")
        entry = next(p for p in result if p["key"] == "PRODUCT_DATA_SYSTEM_PROMPT")
        self.assertTrue(entry["is_overridden"])

    def test_update_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"NOPE": "x"})

    def test_update_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "   "})

    def test_update_rejects_missing_category_token(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"FAST_CLASSIFICATION_SYSTEM_PROMPT": "no token here"})

    def test_update_rejects_user_prompt_missing_required_placeholder(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"PRODUCT_DATA_USER_PROMPT": "no placeholder"})

    def test_update_rejects_user_prompt_extra_placeholder(self):
        with self.assertRaises(ValueError):
            prompt_store.update(
                {"PRODUCT_DATA_USER_PROMPT": "lang {language_label} extra {oops}"}
            )

    def test_update_accepts_valid_user_prompt(self):
        prompt_store.update({"PRODUCT_DATA_USER_PROMPT": "Lang: {language_label}. Go."})
        self.assertEqual(
            prompt_store.get("PRODUCT_DATA_USER_PROMPT"), "Lang: {language_label}. Go."
        )

    def test_update_is_atomic_on_validation_failure(self):
        with self.assertRaises(ValueError):
            prompt_store.update(
                {"PRODUCT_DATA_SYSTEM_PROMPT": "valid", "NOPE": "bad"}
            )
        self.assertFalse(prompt_store.is_overridden("PRODUCT_DATA_SYSTEM_PROMPT"))

    def test_reset_specific_key(self):
        prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "x"})
        prompt_store.reset(["PRODUCT_DATA_SYSTEM_PROMPT"])
        self.assertFalse(prompt_store.is_overridden("PRODUCT_DATA_SYSTEM_PROMPT"))

    def test_reset_all(self):
        prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "x"})
        prompt_store.reset(None)
        self.assertEqual(prompt_store._overrides, {})

    def test_load_overrides_tolerates_missing_file(self):
        prompt_store.load_overrides()
        self.assertEqual(prompt_store._overrides, {})

    def test_load_overrides_tolerates_corrupt_file(self):
        self._path.write_text("{not json", encoding="utf-8")
        prompt_store.load_overrides()
        self.assertEqual(prompt_store._overrides, {})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prompt_store.py::PromptStoreWriteTest -v`
Expected: FAIL with `AttributeError: module 'app.llm.prompt_store' has no attribute 'update'`.

- [ ] **Step 3: Add validation/update/reset/persist to `app/llm/prompt_store.py`**

Insert these functions just above the final `load_overrides()` call at the bottom of the module:
```python
def _user_prompt_fields(text: str) -> set:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(text)
        if field_name
    }


def _validate(key: str, text: str) -> None:
    definition = _REGISTRY_BY_KEY.get(key)
    if definition is None:
        raise ValueError(f"Unknown prompt key: {key}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    for token in definition.required_tokens:
        if token not in text:
            raise ValueError(f"{key} must contain the placeholder {token}.")
    if definition.role == "user":
        allowed = {tok.strip("{}") for tok in definition.required_tokens}
        try:
            fields = _user_prompt_fields(text)
        except ValueError as exc:
            raise ValueError(f"{key} has invalid template formatting: {exc}") from exc
        extra = fields - allowed
        if extra:
            raise ValueError(
                f"{key} contains unsupported placeholders: "
                + ", ".join("{" + name + "}" for name in sorted(extra))
            )


def _persist() -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OVERRIDES_PATH.with_name(OVERRIDES_PATH.name + ".tmp")
    tmp_path.write_text(
        json.dumps(_overrides, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, OVERRIDES_PATH)


def update(updates: Dict[str, str]) -> List[dict]:
    if not isinstance(updates, dict) or not updates:
        raise ValueError("No prompt updates provided.")
    for key, text in updates.items():
        _validate(key, text)
    with _lock:
        _overrides.update(updates)
        _persist()
    return list_prompts()


def reset(keys: Optional[List[str]]) -> List[dict]:
    if keys:
        unknown = [k for k in keys if k not in _REGISTRY_BY_KEY]
        if unknown:
            raise ValueError(f"Unknown prompt key(s): {', '.join(unknown)}")
    with _lock:
        if not keys:
            _overrides.clear()
        else:
            for key in keys:
                _overrides.pop(key, None)
        _persist()
    return list_prompts()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_prompt_store.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/llm/prompt_store.py tests/test_prompt_store.py
git commit -m "feat: add prompt override validation, update, reset, persistence"
```

---

## Task 5: Wire `service.py` to `prompt_store`

Replace the 16 direct constant references with call-time lookups. Rendering at each call site is unchanged except the source of the template string.

**Files:**
- Modify: `app/service.py` (import block ~18-34; call sites ~1041-1435)

- [ ] **Step 1: Replace the prompts import block**

Replace this exact block (lines ~18-34):
```python
from .llm.prompts import (
    CATEGORY_SYSTEM_PROMPT,
    CATEGORY_USER_PROMPT_TEMPLATE,
    FAST_CLASSIFICATION_SYSTEM_PROMPT,
    FAST_CLASSIFICATION_USER_PROMPT,
    PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT,
    PRODUCT_DATA_FALLBACK_USER_PROMPT,
    PRICE_ONLY_SYSTEM_PROMPT,
    PRICE_ONLY_USER_PROMPT,
    PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT,
    PRODUCT_DATA_REGENERATION_USER_PROMPT,
    PRODUCT_DATA_SYSTEM_PROMPT,
    PRODUCT_DATA_USER_PROMPT,
    PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT,
    PRODUCT_TITLE_CATEGORY_USER_PROMPT,
    TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT,
    TITLE_IMAGE_FALLBACK_USER_PROMPT,
)
```
with:
```python
from .llm import prompt_store
```

- [ ] **Step 2: Replace the title-image-fallback call site**

`_call_title_image_fallback_llm`: replace
```python
        user_prompt = TITLE_IMAGE_FALLBACK_USER_PROMPT.format(
            language_label=_language_label(language)
        )
```
with
```python
        user_prompt = prompt_store.get("TITLE_IMAGE_FALLBACK_USER_PROMPT").format(
            language_label=_language_label(language)
        )
```
and replace
```python
            {"role": "system", "content": TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT},
```
with
```python
            {"role": "system", "content": prompt_store.render_system("TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT")},
```

- [ ] **Step 3: Replace the fast-classification call site**

`_call_fast_classification_llm`: replace
```python
        user_prompt = FAST_CLASSIFICATION_USER_PROMPT.format(
            language_label=_language_label(language)
        )
```
with
```python
        user_prompt = prompt_store.get("FAST_CLASSIFICATION_USER_PROMPT").format(
            language_label=_language_label(language)
        )
```
and replace
```python
            {"role": "system", "content": FAST_CLASSIFICATION_SYSTEM_PROMPT},
```
with
```python
            {"role": "system", "content": prompt_store.render_system("FAST_CLASSIFICATION_SYSTEM_PROMPT")},
```

- [ ] **Step 4: Replace the price-only call site**

`_call_price_only_llm`: replace
```python
            {"role": "system", "content": PRICE_ONLY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [{"type": "text", "text": PRICE_ONLY_USER_PROMPT}] + image_payloads,
            },
```
with
```python
            {"role": "system", "content": prompt_store.render_system("PRICE_ONLY_SYSTEM_PROMPT")},
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt_store.get("PRICE_ONLY_USER_PROMPT")}] + image_payloads,
            },
```

- [ ] **Step 5: Replace the product-data call site**

`_call_product_data_llm`: replace
```python
        if use_fallback_prompt:
            system_prompt = PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT
            user_prompt = PRODUCT_DATA_FALLBACK_USER_PROMPT.format(
                language_label=_language_label(language)
            )
            stage = "product_data_fallback"
        else:
            system_prompt = PRODUCT_DATA_SYSTEM_PROMPT
            user_prompt = PRODUCT_DATA_USER_PROMPT.format(
                language_label=_language_label(language)
            )
            stage = "product_data"
```
with
```python
        if use_fallback_prompt:
            system_prompt = prompt_store.render_system("PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT")
            user_prompt = prompt_store.get("PRODUCT_DATA_FALLBACK_USER_PROMPT").format(
                language_label=_language_label(language)
            )
            stage = "product_data_fallback"
        else:
            system_prompt = prompt_store.render_system("PRODUCT_DATA_SYSTEM_PROMPT")
            user_prompt = prompt_store.get("PRODUCT_DATA_USER_PROMPT").format(
                language_label=_language_label(language)
            )
            stage = "product_data"
```

- [ ] **Step 6: Replace the regeneration call site**

`_call_product_data_regeneration_llm`: replace
```python
        user_prompt = PRODUCT_DATA_REGENERATION_USER_PROMPT.format(
            language_label=_language_label(language),
            user_notes=_clean_string(user_notes) or "(none)",
            original_product_data_json=_product_data_context_json(original_product_data),
        )
```
with
```python
        user_prompt = prompt_store.get("PRODUCT_DATA_REGENERATION_USER_PROMPT").format(
            language_label=_language_label(language),
            user_notes=_clean_string(user_notes) or "(none)",
            original_product_data_json=_product_data_context_json(original_product_data),
        )
```
and replace
```python
            {"role": "system", "content": PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT},
```
with
```python
            {"role": "system", "content": prompt_store.render_system("PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT")},
```

- [ ] **Step 7: Replace the title-category call site**

`_call_title_category_llm`: replace
```python
        user_prompt = PRODUCT_TITLE_CATEGORY_USER_PROMPT.format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT},
```
with
```python
        user_prompt = prompt_store.get("PRODUCT_TITLE_CATEGORY_USER_PROMPT").format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": prompt_store.render_system("PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT")},
```

- [ ] **Step 8: Replace the category call site**

`_choose_categories`: replace
```python
        user_prompt = CATEGORY_USER_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            brand=brand_for_prompt,
            group_name=group_name,
            candidate_paths=candidate_block,
        )
        messages = [
            {"role": "system", "content": CATEGORY_SYSTEM_PROMPT},
```
with
```python
        user_prompt = prompt_store.get("CATEGORY_USER_PROMPT_TEMPLATE").format(
            title=title,
            description=description,
            brand=brand_for_prompt,
            group_name=group_name,
            candidate_paths=candidate_block,
        )
        messages = [
            {"role": "system", "content": prompt_store.render_system("CATEGORY_SYSTEM_PROMPT")},
```

- [ ] **Step 9: Verify no stale constant references remain**

Run:
```bash
grep -n "_SYSTEM_PROMPT\|_USER_PROMPT\|PROMPT_TEMPLATE" app/service.py
```
Expected: every match is a `prompt_store.get(...)` / `prompt_store.render_system(...)` string literal — no bare constant names.

- [ ] **Step 10: Run the service + existing test suite**

Run: `.venv/bin/python -m pytest tests/test_service_parallel_flow.py tests/test_product_data_fallback.py tests/test_price_endpoint.py tests/test_image_recognize_fixes.py -v`
Expected: all PASS (behavior unchanged on the no-override path).

- [ ] **Step 11: Commit**

```bash
git add app/service.py
git commit -m "refactor: load image-recognition prompts via prompt_store at call time"
```

---

## Task 6: Add the prompts API endpoints to `main.py`

**Files:**
- Modify: `main.py` (import near line 34; cross-origin guard near 586; new endpoints near 602)
- Test: `tests/test_prompts_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_prompts_api.py`:
```python
import base64
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


def _auth():
    creds = base64.b64encode(b"admin:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


class PromptsApiTest(unittest.TestCase):
    def setUp(self):
        self._env_patcher = patch.dict(os.environ, {"LOGS_PASSWORD": "testpass"})
        self._env_patcher.start()
        import app.config
        importlib.reload(app.config)
        importlib.reload(main)
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "prompt_overrides.json"
        self._path_patch = patch.object(main.prompt_store, "OVERRIDES_PATH", self._path)
        self._path_patch.start()
        main.prompt_store._overrides = {}

    def tearDown(self):
        main.prompt_store._overrides = {}
        self._path_patch.stop()
        self._tmp.cleanup()
        self._env_patcher.stop()

    def test_get_prompts_returns_registry(self):
        client = TestClient(main.app)
        resp = client.get("/api/v1/prompts")
        self.assertEqual(resp.status_code, 200)
        prompts = resp.json()["prompts"]
        self.assertEqual(len(prompts), 16)

    def test_put_prompt_updates_and_persists(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}. Go."},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            main.prompt_store.get("PRODUCT_DATA_USER_PROMPT"), "Lang {language_label}. Go."
        )
        self.assertTrue(self._path.exists())

    def test_put_invalid_prompt_returns_400(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "missing placeholder"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_requires_auth(self):
        client = TestClient(main.app)
        resp = client.put("/api/v1/prompts", json={"PRODUCT_DATA_USER_PROMPT": "x"})
        self.assertEqual(resp.status_code, 401)

    def test_put_rejects_cross_origin(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers={**_auth(), "Origin": "https://evil.example", "Host": "api.example"},
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}."},
        )
        self.assertEqual(resp.status_code, 403)

    def test_reset_reverts_override(self):
        client = TestClient(main.app)
        client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}. Go."},
        )
        resp = client.post(
            "/api/v1/prompts/reset",
            headers=_auth(),
            json={"keys": ["PRODUCT_DATA_USER_PROMPT"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(main.prompt_store.is_overridden("PRODUCT_DATA_USER_PROMPT"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_prompts_api.py -v`
Expected: FAIL (404 on the new routes / `AttributeError` on `main.prompt_store`).

- [ ] **Step 3: Import prompt_store in `main.py`**

Add near the other `app.` imports (after line ~34, next to `from app.runtime_config import ...`):
```python
from app.llm import prompt_store
```

- [ ] **Step 4: Extract a shared cross-origin guard and reuse it in `save_config`**

Replace the inline origin check in `save_config` (lines ~587-592):
```python
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlparse(origin).netloc
        request_host = request.headers.get("host", "")
        if origin_host != request_host:
            raise HTTPException(status_code=403, detail="Cross-origin config updates are not allowed.")
```
with a call to a new helper defined just above `save_config`:
```python
def _reject_cross_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlparse(origin).netloc
        request_host = request.headers.get("host", "")
        if origin_host != request_host:
            raise HTTPException(status_code=403, detail="Cross-origin updates are not allowed.")
```
and inside `save_config` use:
```python
    _reject_cross_origin(request)
```

- [ ] **Step 5: Add the three prompt endpoints after `save_config`**

```python
@app.get("/api/v1/prompts")
def read_prompts() -> Dict[str, Any]:
    return {"prompts": prompt_store.list_prompts()}


@app.put("/api/v1/prompts",
         dependencies=[Depends(require_logs_auth(settings.logs_password))])
def save_prompts(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    _reject_cross_origin(request)
    try:
        return {"prompts": prompt_store.update(payload)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/prompts/reset",
          dependencies=[Depends(require_logs_auth(settings.logs_password))])
def reset_prompts(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    _reject_cross_origin(request)
    try:
        return {"prompts": prompt_store.reset(payload.get("keys"))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 6: Run the API tests + the config API tests (guard the refactor)**

Run: `.venv/bin/python -m pytest tests/test_prompts_api.py tests/test_config_api.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_prompts_api.py
git commit -m "feat: add /api/v1/prompts read, update, and reset endpoints"
```

---

## Task 7: Add the prompts panel to `web/config.html`

The panel is built dynamically from the `GET /api/v1/prompts` response (no hardcoded 16 blocks). Auth is inherited from the already-authenticated page (browser resends Basic credentials on same-origin fetch).

**Files:**
- Modify: `web/config.html`

- [ ] **Step 1: Add the panel container in the HTML body**

Immediately after the closing `</section>` (or the closing element) of the existing config form — before the `<script>` block — insert:
```html
    <section class="card" id="prompts-section">
      <h2>提示词配置 (Prompts)</h2>
      <p class="hint">修改后点击「保存」立即生效，无需重启服务。占位符必须保留。</p>
      <div id="prompts-container">正在读取提示词...</div>
    </section>
```

- [ ] **Step 2: Add the prompts JS at the end of the existing `<script>` block**

Insert just before the final `loadConfig().catch(...)` call:
```javascript
      const promptsContainer = document.getElementById("prompts-container");

      function escapeHtml(s) {
        return String(s)
          .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      }

      function renderPrompts(prompts) {
        promptsContainer.innerHTML = "";
        prompts.forEach((p) => {
          const block = document.createElement("div");
          block.className = "prompt-block";
          const tokens = p.required_tokens.length
            ? `必需占位符：${p.required_tokens.map(escapeHtml).join(" ")}`
            : "无必需占位符";
          const badge = p.is_overridden ? ' <span class="badge">已修改</span>' : "";
          block.innerHTML =
            `<label>${escapeHtml(p.label)} <code>${escapeHtml(p.key)}</code>${badge}</label>` +
            `<div class="hint">${tokens}</div>` +
            `<textarea id="prompt-${p.key}" rows="10"></textarea>` +
            `<div class="prompt-actions">` +
            `<button type="button" data-save="${p.key}">保存</button>` +
            `<button type="button" data-reset="${p.key}">恢复默认</button>` +
            `</div>`;
          promptsContainer.appendChild(block);
          block.querySelector("textarea").value = p.value;
        });
        promptsContainer.querySelectorAll("[data-save]").forEach((btn) => {
          btn.addEventListener("click", () => savePrompt(btn.getAttribute("data-save")));
        });
        promptsContainer.querySelectorAll("[data-reset]").forEach((btn) => {
          btn.addEventListener("click", () => resetPrompt(btn.getAttribute("data-reset")));
        });
      }

      async function loadPrompts() {
        const resp = await fetch("/api/v1/prompts");
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "读取提示词失败");
        renderPrompts(data.prompts);
      }

      async function savePrompt(key) {
        const text = document.getElementById(`prompt-${key}`).value;
        try {
          const resp = await fetch("/api/v1/prompts", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [key]: text }),
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || "保存失败");
          renderPrompts(data.prompts);
          showMessage(`提示词 ${key} 已保存并立即生效。`, "success");
        } catch (err) {
          showMessage(String(err.message || err), "error");
        }
      }

      async function resetPrompt(key) {
        try {
          const resp = await fetch("/api/v1/prompts/reset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keys: [key] }),
          });
          const data = await resp.json();
          if (!resp.ok) throw new Error(data.detail || "恢复失败");
          renderPrompts(data.prompts);
          showMessage(`提示词 ${key} 已恢复默认。`, "success");
        } catch (err) {
          showMessage(String(err.message || err), "error");
        }
      }

      loadPrompts().catch((err) => {
        promptsContainer.textContent = "读取提示词失败：" + String(err.message || err);
      });
```

- [ ] **Step 3: Add minimal styling for the new elements**

Inside the page's existing `<style>` block, append:
```css
      .prompt-block { margin-bottom: 18px; padding-bottom: 12px; border-bottom: 1px solid #eee; }
      .prompt-block code { font-size: 12px; color: #666; }
      .prompt-block .badge { background: #f59e0b; color: #fff; border-radius: 4px; padding: 1px 6px; font-size: 11px; }
      .prompt-actions { margin-top: 6px; display: flex; gap: 8px; }
```

- [ ] **Step 4: Manually verify the page renders and round-trips**

Run the server: `.venv/bin/uvicorn main:app --port 8000` (ensure `LOGS_PASSWORD` is set in `.env`).
In a browser open `http://localhost:8000/config`, authenticate, scroll to "提示词配置". Confirm 16 textareas load, edit `PRODUCT_DATA_USER_PROMPT` keeping `{language_label}`, click 保存 → success message; remove the placeholder and save → error message; click 恢复默认 → reverts. Stop the server.

- [ ] **Step 5: Commit**

```bash
git add web/config.html
git commit -m "feat: add prompts config panel to config page"
```

---

## Task 8: Ignore the runtime override file and run the full suite

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the override file to `.gitignore`**

Append a line to `.gitignore`:
```
data/prompt_overrides.json
```

- [ ] **Step 2: Run the entire test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests PASS (no regressions).

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore runtime prompt_overrides.json"
```

---

## Self-Review Notes (verification checklist for the implementer)

- **Spec coverage:** Task 2/3 = sentinel + registry/defaults (spec §3, §4.1); Task 4 = validation/persistence (§4.1, §6); Task 5 = service wiring (§4.2); Task 6 = API (§4.3); Task 7 = UI (§4.4); Task 1 + Task 3 golden test = byte-identical no-override guarantee (§1 goal 2, §7). All 8 prompts (16 entries) are in `PROMPT_REGISTRY` (§1 scope).
- **No-override identity:** system prompts go through `render_system` (replace is a no-op without the sentinel); user prompts use `get(...).format(...)` exactly as before — output is unchanged when no override exists.
- **Placeholder safety:** system prompts are never `.format()`-ed (preserves literal JSON braces); user-prompt validation forbids extra `{...}` names, preventing a runtime `KeyError` in the pipeline.
