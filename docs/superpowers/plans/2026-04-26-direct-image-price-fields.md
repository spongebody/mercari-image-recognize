# Direct Image Price Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct image price extraction fields while preserving inferred price predictions when no explicit price is visible.

**Architecture:** The single vision prompt returns both explicit price fields and inferred `prices`. `MercariAnalyzer.analyze` normalizes direct prices, enforces mutual exclusion by clearing `prices` when `tax_excluded ` is present, and returns the two new fields in the image-analysis response. UI and docs display the direct prices separately from inferred predictions.

**Tech Stack:** FastAPI service layer, OpenRouter prompt strings, vanilla HTML/JS test UI, pytest/unittest.

---

### Task 1: Add Service-Level Regression Tests

**Files:**
- Modify: `tests/test_service_rakuten_id.py`

- [x] Add a test where the vision payload contains `"tax_excluded ": 980`, `"tax_included": 1078`, and `"prices": [1000, 1500, 2000]`; assert the response returns direct prices and clears `prices`.
- [x] Add a test where direct prices are empty; assert `tax_excluded ` and `tax_included` are `None` and inferred `prices` are preserved.
- [x] Run focused tests and confirm the new direct-price test fails before implementation.
- [x] Add review follow-up tests for tax-only direct prices and combined price strings.

### Task 2: Normalize And Return Direct Price Fields

**Files:**
- Modify: `app/service.py`

- [x] Add a helper to coerce direct price values into integers or `None`, accepting numeric strings like `"¥980"` and `"税込 1,078円"`.
- [x] In `analyze`, read `tax_excluded ` and `tax_included` from `ai_raw`.
- [x] If `tax_excluded ` is present, set inferred `prices` to `[]`; otherwise preserve `normalize_price_list(ai_raw.get("prices", []))`.
- [x] Return both new fields in the result.
- [x] Ignore tax-included direct price when `tax_excluded ` is absent.
- [x] Parse the first discrete price token from combined direct-price strings.

### Task 3: Update Prompt Contract

**Files:**
- Modify: `app/llm/prompts.py`

- [x] Update `VISION_SYSTEM_PROMPT_WITH_PRICE` to prioritize reading visible price tags/labels.
- [x] Add JSON schema fields `tax_excluded ` and `tax_included`.
- [x] State the mutual exclusion rule: visible `tax_excluded ` means `prices` must be `[]`; no visible price means direct price fields are `null` and `prices` contains inferred values.
- [x] Update `VISION_USER_PROMPT_WITH_PRICE` with the same concise instruction.
- [x] Update the prompt schema to allow `prices` to be either `[]` or `[number, number, number]`.

### Task 4: Update UI And Docs

**Files:**
- Modify: `web/index.html`
- Modify: `README.md`
- Modify: `API.md`

- [x] Add labels and rendering for direct image price and tax-included direct image price.
- [x] Document the new fields and mutual exclusion behavior.

### Task 5: Verify

- [x] Run `PYTHONPATH=. uv run pytest -q`.
- [x] Run `PYTHONPATH=. uv run python -m compileall app main.py`.
