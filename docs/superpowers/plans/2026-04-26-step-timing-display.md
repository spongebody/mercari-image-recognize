# Step Timing Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show total request time plus separate image recognition and category classification times in image analysis results.

**Architecture:** Measure service-side total duration plus phase durations inside `MercariAnalyzer.analyze()` around the existing vision and category LLM calls, then return them in a `timings` object. Render backend timing values in the result card instead of browser-measured request time.

**Tech Stack:** Python `unittest`, FastAPI service layer, vanilla JavaScript in `web/index.html`.

---

### Task 1: Backend Timing Fields

**Files:**
- Modify: `tests/test_service_rakuten_id.py`
- Modify: `app/service.py`

- [ ] **Step 1: Write the failing test**

Add a service test that patches `app.service.time.monotonic` and asserts `analyze()` returns:

```python
result["timings"] == {
    "total_ms": 600.0,
    "vision_ms": 125.0,
    "category_ms": 250.0,
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_service_rakuten_id.RakutenIdResponseTest.test_analyze_includes_phase_timings`

Expected: FAIL because `timings` is missing.

- [ ] **Step 3: Write minimal implementation**

Wrap the full analysis flow, `_call_vision_llm()`, and `_choose_categories()` in `time.monotonic()` measurements. Include `category_ms: 0.0` when no category call happens.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_service_rakuten_id.RakutenIdResponseTest.test_analyze_includes_phase_timings`

Expected: PASS.

### Task 2: Result Card Display

**Files:**
- Modify: `web/index.html`

- [ ] **Step 1: Add render helper**

Add a small JavaScript helper that formats phase timings from `payload.timings`.

- [ ] **Step 2: Render timing stats**

Render `timings.total_ms`, `timings.vision_ms`, and `timings.category_ms` when backend timing values exist.

- [ ] **Step 3: Verify manually in static code**

Confirm existing result card rendering still accepts responses without `payload.timings`.

### Task 3: Documentation and Verification

**Files:**
- Modify: `API.md`
- Modify: `README.md`

- [ ] **Step 1: Document response fields**

Document `timings.total_ms`, `timings.vision_ms`, and `timings.category_ms`.

- [ ] **Step 2: Run focused tests**

Run: `python -m unittest tests.test_service_rakuten_id`

Expected: PASS.
