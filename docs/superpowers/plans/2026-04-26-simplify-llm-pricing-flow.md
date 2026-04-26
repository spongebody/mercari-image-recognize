# Simplify LLM Pricing Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce image analysis to one vision prompt that includes price prediction, followed by the existing category prompt, with no online or dedicated pricing paths.

**Architecture:** `MercariAnalyzer.analyze` always calls the vision LLM once with the price-enabled prompt, normalizes `prices`, then runs the existing category selection flow. Dedicated price clients, online model routing, search prompts, price strategy branching, and UI strategy controls are removed.

**Tech Stack:** FastAPI, Python dataclasses, OpenRouter chat completions, vanilla HTML/JS test UI, pytest/unittest.

---

### Task 1: Lock Analyzer Behavior With Tests

**Files:**
- Modify: `tests/test_service_rakuten_id.py`
- Test: `tests/test_service_rakuten_id.py`

- [ ] **Step 1: Add a fake chat client that records calls**

```python
class RecordingChatClient(FakeChatClient):
    def __init__(self, payload):
        super().__init__(payload)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return super().chat(**kwargs)
```

- [x] **Step 2: Add a regression test for the single vision pricing path**

```python
def test_analyze_uses_single_vision_call_for_prices(self):
    settings = SimpleNamespace(
        vision_model="vision-test",
        category_model="category-test",
        log_llm_raw=False,
        category_llm_retry_enabled=False,
        category_llm_max_retries=0,
    )
    vision_client = RecordingChatClient(
        {
            "title": "シャツ",
            "description": "メンズシャツ",
            "prices": [1000, 1500, 2000],
            "top_level_category": "メンズファッション",
            "brand_name": "",
        }
    )
    analyzer = MercariAnalyzer(
        settings=settings,
        brand_store=FakeBrandStore(),
        category_store=FakeCategoryStore(),
        vision_client=vision_client,
        category_client=FakeChatClient({"best_target_path": "メンズファッション/トップス"}),
    )

    result = analyzer.analyze(
        images=[(b"image-bytes", "image/png")],
        language="ja",
        category_limit=1,
    )

    self.assertEqual(result["prices"], [1000, 1500, 2000])
    self.assertEqual(len(vision_client.calls), 1)
```

- [x] **Step 3: Run the focused test**

Run: `pytest tests/test_service_rakuten_id.py -v`

Expected before implementation: FAIL while the old branch can bypass inline vision prices.

### Task 2: Simplify Backend Wiring

**Files:**
- Modify: `app/service.py`
- Modify: `main.py`
- Modify: `app/config.py`

- [x] **Step 1: Remove unused price prompt imports and analyzer branch fields**

In `app/service.py`, keep only `VISION_SYSTEM_PROMPT_WITH_PRICE`, `VISION_USER_PROMPT_WITH_PRICE`, and the category/title prompts. Remove `PRICE_*`, search prompt imports, `force_online`, and `price_mode`.

- [x] **Step 2: Make `analyze` use one vision pricing path**

`analyze` should call:

```python
ai_raw, ai_full = self._call_vision_llm(
    data_urls,
    language,
    model_override=vision_model_override,
)
```

Then keep debug focused on the raw vision response and category choice.

- [x] **Step 3: Remove dedicated price prediction method**

Delete `_predict_price_with_model` from `app/service.py` and remove dedicated price-client wiring from tests and `main.py`.

- [x] **Step 4: Remove API price model routing**

In `main.py`, remove `price_model` from `analyze_image`, stop passing `price_model_override`, remove `price_model` and `vision_model_online` from `/health`, and simplify client creation if safe.

- [x] **Step 5: Remove online/default config fields**

In `app/config.py`, delete `vision_model_online`, `price_model`, and `__post_init__`.

### Task 3: Delete Unused Prompts And UI Controls

**Files:**
- Modify: `app/llm/prompts.py`
- Modify: `web/index.html`

- [x] **Step 1: Remove obsolete prompt constants**

Delete `VISION_SYSTEM_PROMPT`, `VISION_USER_PROMPT_TEMPLATE`, `VISION_SYSTEM_PROMPT_WITH_SEARCH`, `VISION_USER_PROMPT_TEMPLATE_WITH_WITH_SEARCH`, `PRICE_SYSTEM_PROMPT`, and `PRICE_USER_PROMPT_TEMPLATE`.

- [x] **Step 2: Simplify UI price controls**

Remove the `price-strategy` select options for `vision_online` and `dedicated`; stop appending `price_strategy` and `price_model` in the request. Keep rendering `prices` but remove citation display copy if no longer returned.

### Task 4: Update Docs And Verify

**Files:**
- Modify: `README.md`
- Modify: `API.md`
- Modify: `scripts/perf_test.py`

- [x] **Step 1: Update docs**

Document that image analysis always uses one vision LLM prompt with inline price prediction and one category prompt. Remove `price_strategy`, `price_model`, `VISION_MODEL_ONLINE`, and `PRICE_MODEL` references.

- [x] **Step 2: Remove obsolete perf test field**

Delete `"price_strategy": "vision"` from `scripts/perf_test.py`.

- [x] **Step 3: Run verification**

Run:

```bash
pytest -q
python -m compileall app main.py
```

Expected: all tests pass and Python files compile.

## Self-Review

- Spec coverage: The plan removes online pricing, dedicated pricing, separate price prompts, price model configuration, API/UI strategy controls, and docs references while preserving image recognition with prices plus category classification.
- Placeholder scan: No placeholders remain.
- Type consistency: Existing public constructor compatibility is retained during edits unless all call sites are updated together.
