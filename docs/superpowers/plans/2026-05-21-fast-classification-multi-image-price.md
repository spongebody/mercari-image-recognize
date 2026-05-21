# Fast Classification Multi-Image Price Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return direct visible price fields in the first image-analysis response by having the fast classification chain inspect all uploaded images for prices.

**Architecture:** Extend the existing `fast_vision` call so it receives all uploaded images. Keep classification evidence anchored to the first image, extract only direct visible prices from all images, and preserve those fast direct prices during completed-response merges unless product data supplies direct prices.

**Tech Stack:** FastAPI, Python service layer, OpenRouter chat prompt strings, unittest/pytest.

---

## File Structure

- Modify `app/llm/prompts.py`: update the fast classification prompt contract to include `tax_excluded` and `tax_included`, while prohibiting inferred `prices`.
- Modify `app/service.py`: send all image data URLs to `_call_fast_classification_llm`, normalize direct price fields in `classify_first_image_categories`, and keep `prices` empty in the fast path.
- Modify `main.py`: preserve fast direct prices when product data completes without direct prices.
- Modify `tests/test_service_parallel_flow.py`: lock service-level fast prompt, multi-image submission, and fast direct price behavior.
- Modify `tests/test_image_analyze_jobs.py`: lock API first-response and merge behavior.
- Modify `README.md` and `API.md`: document that first responses can include fast direct prices from all uploaded images.

## Task 1: Fast Classification Service Behavior

**Files:**
- Modify: `tests/test_service_parallel_flow.py`
- Modify: `app/llm/prompts.py`
- Modify: `app/service.py`

- [ ] **Step 1: Update the existing fast classification test to expect all images and direct price fields**

In `tests/test_service_parallel_flow.py`, update `test_classify_first_image_categories_uses_first_image_and_returns_confidence`.

Use this vision payload:

```python
vision_client = RecordingChatClient(
    {
        "title": "Nike シャツ",
        "simple_description": "Nikeのメンズシャツ",
        "top_level_category": "メンズファッション",
        "tax_excluded": None,
        "tax_included": "税込 1,078円",
    }
)
```

Replace the prompt and result assertions in that test with:

```python
vision_content = vision_client.calls[0]["messages"][1]["content"]
image_parts = [part for part in vision_content if part["type"] == "image_url"]
prompt_text = vision_content[0]["text"]
system_prompt = vision_client.calls[0]["messages"][0]["content"]
category_prompt = category_client.calls[0]["messages"][1]["content"]
self.assertEqual(len(image_parts), 2)
self.assertNotIn("product_intro", prompt_text)
self.assertIn("tax_excluded", system_prompt)
self.assertIn("tax_included", system_prompt)
self.assertNotIn('"prices"', system_prompt)
self.assertNotIn("brand_name", prompt_text)
self.assertIn("- Brand (may be empty): \n", category_prompt)
self.assertEqual(result["status"], "product_pending")
self.assertNotIn("brand_name", result)
self.assertIsNone(result["tax_excluded"])
self.assertEqual(result["tax_included"], 1078)
self.assertEqual(result["prices"], [])
self.assertEqual(result["categories"][0]["confidence"], 0.91)
self.assertEqual(result["categories"][1]["confidence"], 0.53)
self.assertEqual(set(result["timings"].keys()), {"total_ms", "classification_ms"})
self.assertEqual(result["timings"]["total_ms"], result["timings"]["classification_ms"])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
PYTHONPATH=. uv run pytest tests/test_service_parallel_flow.py::ParallelFlowServiceTest::test_classify_first_image_categories_uses_first_image_and_returns_confidence -q
```

Expected: FAIL because the current fast classification call only sends one image and does not return direct price fields.

- [ ] **Step 3: Update the fast classification prompt contract**

In `app/llm/prompts.py`, replace the fast prompt section with:

```python
FAST_CLASSIFICATION_SYSTEM_PROMPT = """You are an assistant helping sellers quickly classify a product for a Japanese marketplace.

Use the first uploaded product image as the primary evidence for downstream category selection:
- title: a short product title in the requested language
- simple_description: one concise sentence describing what the product appears to be
- top_level_category: exactly one top-level category from this list
""" + TOP_LEVEL_CATEGORY_OPTIONS + """

Also inspect every uploaded product image for clearly visible actual product prices, such as a price tag, label, sticker, receipt, or packaging price:
- tax_excluded: the visible tax-excluded price as an integer JPY, or null
- tax_included: the visible tax-included price as an integer JPY, or null

If exactly one actual product price is visible, return it as tax_included and set tax_excluded to null.
If both tax-excluded and tax-included prices are clearly visible, return both.
If no actual product price is clearly visible, set both direct price fields to null.

Do not generate brand information, listing copy, detailed description sections, inferred reference prices, or a prices field.

You must respond with pure JSON only, without explanations, markdown, or comments.

The JSON schema is:

{
  "title": "string",
  "simple_description": "string",
  "top_level_category": "string",
  "tax_excluded": number or null,
  "tax_included": number or null
}
"""

FAST_CLASSIFICATION_USER_PROMPT = """Classify this product image set.

Language for title and simple_description: {language_label}.

Use the first image for category evidence. Inspect all images for visible actual product prices.

Return JSON only with title, simple_description, top_level_category, tax_excluded, and tax_included."""
```

- [ ] **Step 4: Update fast classification to send all images and return normalized direct prices**

In `app/service.py`, replace the first-image data URL block inside `classify_first_image_categories`:

```python
first_data_url = image_bytes_to_data_url(images[0][0], images[0][1])
ai_raw, vision_attempts = self._call_fast_classification_llm(
    first_data_url,
    language,
    model_override=vision_model_override,
)
```

with:

```python
data_urls = [
    image_bytes_to_data_url(image_bytes, mime_type)
    for image_bytes, mime_type in images
]
ai_raw, vision_attempts = self._call_fast_classification_llm(
    data_urls,
    language,
    model_override=vision_model_override,
)
```

After `group_name = _map_top_level_category(top_level_category)`, add:

```python
price_fields = _normalize_price_fields(ai_raw)
price_fields["prices"] = []
```

In the `result` dict, add:

```python
**price_fields,
```

so the result contains normalized `tax_excluded`, `tax_included`, and `prices`.

Change `_call_fast_classification_llm` signature from:

```python
def _call_fast_classification_llm(
    self,
    image_data_url: str,
    language: str,
    model_override: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
```

to:

```python
def _call_fast_classification_llm(
    self,
    image_data_urls: List[str],
    language: str,
    model_override: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
```

At the start of `_call_fast_classification_llm`, add:

```python
if not image_data_urls:
    raise BadRequestError("Image list is empty.")
```

Replace the user message content list with:

```python
image_payloads: List[Dict[str, Any]] = []
image_count = len(image_data_urls)
for index, url in enumerate(image_data_urls, start=1):
    role_text = (
        "Use this first image as the primary category evidence. "
        "Also inspect it for visible actual product prices."
        if index == 1
        else "Inspect this additional image for visible actual product prices."
    )
    image_payloads.append(
        {
            "type": "text",
            "text": f"Image {index} of {image_count}: {role_text}",
        }
    )
    image_payloads.append({"type": "image_url", "image_url": {"url": url}})
```

Then set the user content to:

```python
"content": [{"type": "text", "text": user_prompt}] + image_payloads,
```

- [ ] **Step 5: Run the focused test and verify it passes**

Run:

```bash
PYTHONPATH=. uv run pytest tests/test_service_parallel_flow.py::ParallelFlowServiceTest::test_classify_first_image_categories_uses_first_image_and_returns_confidence -q
```

Expected: PASS.

## Task 2: First Response And Completed Merge Behavior

**Files:**
- Modify: `tests/test_image_analyze_jobs.py`
- Modify: `main.py`

- [ ] **Step 1: Update the initial-response API test to expect fast direct price fields**

In `tests/test_image_analyze_jobs.py`, inside `test_initial_response_omits_brand_until_polling_completes`, add these fields to `analyzer.classify_first_image_categories.return_value`:

```python
"tax_excluded": None,
"tax_included": 1078,
"prices": [],
```

Replace the initial response price assertions with:

```python
self.assertIsNone(body["tax_excluded"])
self.assertEqual(body["tax_included"], 1078)
self.assertEqual(body["prices"], [])
```

Replace the pending poll price assertions with:

```python
self.assertIsNone(pending_response.json()["tax_excluded"])
self.assertEqual(pending_response.json()["tax_included"], 1078)
self.assertEqual(pending_response.json()["prices"], [])
```

Change the completed product future result price fields to simulate product data having no direct price:

```python
"tax_excluded": None,
"tax_included": None,
"prices": [1000, 1500, 2000],
```

Replace completed price assertions with:

```python
self.assertIsNone(completed["tax_excluded"])
self.assertEqual(completed["tax_included"], 1078)
self.assertEqual(completed["prices"], [1000, 1500, 2000])
```

- [ ] **Step 2: Add a completed-response test where product data direct price wins**

Add this test to `tests/test_image_analyze_jobs.py`:

```python
    def test_merge_prefers_product_data_direct_price_over_fast_price(self):
        classification = {
            "status": "product_pending",
            "categories": [],
            "tax_excluded": None,
            "tax_included": 1078,
            "prices": [],
            "timings": {"total_ms": 100.0, "classification_ms": 100.0},
        }
        product_data = {
            "title": "シャツ",
            "description": {
                "product_details": {
                    "brand": "",
                    "product_name": "シャツ",
                    "model_number": "",
                    "target": "",
                    "color": "",
                    "size": "",
                    "weight": "",
                    "condition": "",
                },
                "product_intro": "紹介文",
                "recommendation": "おすすめ",
                "search_keywords": ["シャツ"],
            },
            "brand_name": "",
            "brand_id_obj": {},
            "tax_excluded": 980,
            "tax_included": 1078,
            "prices": [],
            "timings": {"product_data_ms": 250.0},
        }

        merged = main._merge_analysis_payload(classification, product_data)

        self.assertEqual(merged["tax_excluded"], 980)
        self.assertEqual(merged["tax_included"], 1078)
        self.assertEqual(merged["prices"], [])
```

- [ ] **Step 3: Run the focused API tests and verify they fail**

Run:

```bash
PYTHONPATH=. uv run pytest tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_initial_response_omits_brand_until_polling_completes tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_merge_prefers_product_data_direct_price_over_fast_price -q
```

Expected: FAIL because completed merge currently lets product data `None` direct prices overwrite fast direct prices.

- [ ] **Step 4: Preserve fast direct prices during merge when product data has no direct price**

In `main.py`, add this helper near `_ensure_price_fields`:

```python
def _has_direct_price(payload: Dict[str, Any]) -> bool:
    return payload.get("tax_excluded") is not None or payload.get("tax_included") is not None
```

In `_merge_analysis_payload`, directly after:

```python
payload = dict(classification)
payload.update(product_data)
```

add:

```python
if _has_direct_price(classification) and not _has_direct_price(product_data):
    payload["tax_excluded"] = classification.get("tax_excluded")
    payload["tax_included"] = classification.get("tax_included")
```

Do not override `prices`; product data remains responsible for inferred reference prices.

- [ ] **Step 5: Run the focused API tests and verify they pass**

Run:

```bash
PYTHONPATH=. uv run pytest tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_initial_response_omits_brand_until_polling_completes tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_merge_prefers_product_data_direct_price_over_fast_price -q
```

Expected: PASS.

## Task 3: Documentation And Full Verification

**Files:**
- Modify: `README.md`
- Modify: `API.md`

- [ ] **Step 1: Update README fast-flow documentation**

In `README.md`, update the `POST /api/v1/mercari/image/analyze` flow so step 4 says:

```markdown
4. 同一请求内立即执行快速分类链路 `MercariAnalyzer.classify_first_image_categories`：
   - 使用第一张图作为标题、简述和顶级类目判断的主要依据
   - 同时检查所有上传图片中是否有清晰可见的实际价格标签、贴纸、票据或包装价格
   - 使用 `FAST_CLASSIFICATION_SYSTEM_PROMPT` + `FAST_CLASSIFICATION_USER_PROMPT`
   - 通过 `VISION_MODEL` 或请求里的 `vision_model` 调 OpenRouter
   - 得到 `title`、`simple_description`、`top_level_category`、`tax_excluded`、`tax_included`
```

Update the price-field paragraph to say:

```markdown
6. 价格字段始终返回：首接口会由快速分类链路基于所有上传图片抽取清晰可见的实际价格，返回 `tax_excluded` / `tax_included`；如果没有明确价格，则两者为 `null`。快速分类链路不推断 `prices`。商品信息完成后，如果商品信息链路识别到直接价格则以商品信息链路为准；如果商品信息链路没有直接价格但首接口已有直接价格，则保留首接口价格；如果没有明确价格，则商品信息链路可返回 3 个按成色升序的参考价格。
```

- [ ] **Step 2: Update API price-field documentation**

In `API.md`, replace the top-level price field note with:

```markdown
- 图片识别主接口会始终返回 `tax_excluded`、`tax_included`、`prices`。首个 `product_pending` 响应会由快速分类链路基于所有上传图片抽取清晰可见的实际价格；看不到明确实际价格时，`tax_excluded` / `tax_included` 为 `null`，`prices` 为 `[]`。后台商品数据完成后，直接价格以商品数据链路为准；如果商品数据没有直接价格但首响应已有直接价格，则保留首响应价格；参考价格 `prices` 仍只由商品数据链路推断。
```

In the image analyze response explanation, replace the current pending-price sentence with:

```markdown
- 首次响应如果仍为 `product_pending`，也可能包含快速分类链路从所有上传图片中抽取到的直接价格；快速分类链路不推断 `prices`，因此 `prices` 在首响应通常为 `[]`。
```

In the polling price notes, include:

```markdown
- 完成态合并时，如果商品数据链路返回直接价格，则覆盖首响应价格；如果商品数据链路没有直接价格但首响应已有直接价格，则保留首响应价格。
```

- [ ] **Step 3: Run the focused service and API tests**

Run:

```bash
PYTHONPATH=. uv run pytest tests/test_service_parallel_flow.py::ParallelFlowServiceTest::test_classify_first_image_categories_uses_first_image_and_returns_confidence tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_initial_response_omits_brand_until_polling_completes tests/test_image_analyze_jobs.py::ImageAnalyzeJobsTest::test_merge_prefers_product_data_direct_price_over_fast_price -q
```

Expected: PASS.

- [ ] **Step 4: Run the full test suite**

Run:

```bash
PYTHONPATH=. uv run pytest -q
```

Expected: PASS.

- [ ] **Step 5: Compile Python files**

Run:

```bash
PYTHONPATH=. uv run python -m compileall app main.py
```

Expected: command exits 0 and reports successful compilation.

- [ ] **Step 6: Review git diff**

Run:

```bash
git diff -- app/llm/prompts.py app/service.py main.py tests/test_service_parallel_flow.py tests/test_image_analyze_jobs.py README.md API.md
```

Expected: diff only contains the fast multi-image direct price behavior, tests, and docs.

## Self-Review

- Spec coverage: Task 1 implements all-image fast price extraction and prompt contract. Task 2 implements first-response and completed merge behavior. Task 3 documents the public contract and verifies the full project.
- Placeholder scan: No placeholder tasks or deferred behavior remain.
- Type consistency: The plan consistently uses `tax_excluded`, `tax_included`, and `prices`; `_call_fast_classification_llm` accepts `List[str]`; fast path returns direct price fields and forced empty `prices`.
