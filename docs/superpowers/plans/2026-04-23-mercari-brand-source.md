# Mercari Brand Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the default brand source to a normalized Mercari brand CSV while preserving the API response contract.

**Architecture:** Add an offline normalization script that rewrites `data/mercari_brand.csv` into the schema expected by `BrandStore`, using `data/rdx_brand.csv` to backfill cross-platform ids. Keep runtime changes limited to configuration and brand CSV loading so the API response shape remains unchanged.

**Tech Stack:** Python 3.11, csv, unittest, FastAPI service config

---

### Task 1: Lock Down Brand Loading Behavior

**Files:**
- Create: `tests/test_brand_store.py`
- Test: `tests/test_brand_store.py`

- [ ] **Step 1: Write the failing test**

```python
def test_brand_store_reads_normalized_mercari_csv():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_brand_store -v`
Expected: FAIL because the test module does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create a focused unit test module that verifies:
- `BrandStore` can read a normalized Mercari CSV with the new headers
- `meru_brand_id` remains populated from `meru_id`
- missing platform ids are returned as empty strings

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_brand_store -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_brand_store.py
git commit -m "test: cover normalized mercari brand loading"
```

### Task 2: Lock Down Normalization Rules

**Files:**
- Create: `tests/test_build_mercari_brand_csv.py`
- Create: `scripts/build_mercari_brand_csv.py`
- Test: `tests/test_build_mercari_brand_csv.py`

- [ ] **Step 1: Write the failing test**

```python
def test_normalize_mercari_brand_csv_backfills_platform_ids():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_build_mercari_brand_csv -v`
Expected: FAIL because the script module or functions do not exist yet.

- [ ] **Step 3: Write minimal implementation**

Implement script helpers that:
- read Mercari input in raw or normalized format
- read RDX mappings by `meru_id`
- normalize headers and values
- write the final UTF-8 CSV

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_build_mercari_brand_csv -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/build_mercari_brand_csv.py tests/test_build_mercari_brand_csv.py
git commit -m "feat: normalize mercari brand csv"
```

### Task 3: Switch Runtime Default And Docs

**Files:**
- Modify: `app/config.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing test**

```python
def test_settings_default_brand_csv_path_is_mercari_brand():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_brand_store -v`
Expected: FAIL because the default path still points to `data/rdx_brand.csv`.

- [ ] **Step 3: Write minimal implementation**

Point the default `BRAND_CSV_PATH` at `data/mercari_brand.csv` and update documentation accordingly.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_brand_store -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/config.py README.md
git commit -m "chore: switch default brand csv to mercari"
```

### Task 4: Regenerate Dataset And Verify End To End

**Files:**
- Modify: `data/mercari_brand.csv`

- [ ] **Step 1: Run the normalization script**

Run: `python scripts/build_mercari_brand_csv.py`
Expected: `data/mercari_brand.csv` is rewritten as UTF-8 with normalized headers and platform id columns.

- [ ] **Step 2: Verify the resulting header and sample rows**

Run: `python - <<'PY'\n...\nPY`
Expected: The header is normalized, `meru_id` equals `id`, and mapped ids are filled when present.

- [ ] **Step 3: Run the full test suite**

Run: `python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add data/mercari_brand.csv
git commit -m "data: regenerate mercari brand csv"
```
