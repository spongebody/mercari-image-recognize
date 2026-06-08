# 独立的尺寸识别接口 — 设计文档

日期：2026-06-08
状态：已确认设计，待写实现计划

## 背景与动机

当前商品尺寸（`product_size`）在第一阶段 fast vision 中提取（仅看首图），与分类结果一起返回。存在两个问题：

1. 尺寸信息大概率不在首图里（常在吊牌、包装、尺码表等后续图片上），首图提取命中率低。
2. 在 fast vision 提示词里加尺寸抽取会拖慢分类接口（`/api/v1/mercari/image/analyze`）的响应速度。

因此改为**独立接口**按需返回尺寸信息，参考现有的价格接口 `/api/v1/mercari/image/price`。独立接口会检查**所有**上传图片，且不影响分类接口的速度。

## 目标

- 把尺寸抽取从 fast vision / 分类链路中完全移除，恢复分类接口原有行为与速度。
- 新增独立的尺寸识别接口，检查全部图片，按需返回 `product_size`。
- 前端测试页提供独立的「识别尺寸」按钮（与价格按钮并列）。

## 非目标

- 不改动价格、商品数据、分类等既有链路的逻辑。
- 不新增 `SIZE_MODEL` 环境变量（尺寸接口复用 `vision_model`，支持请求级 `vision_model` 覆盖）。
- 尺寸仍为单一字符串，不做结构化拆分。

## 设计

### 1. 回退 fast vision 的尺寸逻辑

- `app/llm/prompts.py`：`FAST_CLASSIFICATION_SYSTEM_PROMPT` / `FAST_CLASSIFICATION_USER_PROMPT` 还原到加 `product_size` 之前的版本。
- `app/service.py`：`classify_first_image_categories` 移除 `product_size` 的提取（`ai_raw.get("product_size", ...)`）与返回字段。分类接口（含轮询）**不再出现** `product_size`。
- `tests/data/prompt_category_golden.json`：`FAST_CLASSIFICATION_SYSTEM_PROMPT` golden 快照还原。
- 前端的「📏 商品尺寸」渲染块与 i18n 标签 `fieldProductSize` **保留**——新尺寸接口的结果卡片复用同一个 `renderStructuredData`。

### 2. 新增 `SIZE_ONLY` 提示词（参考 `PRICE_ONLY`）

- `SIZE_ONLY_SYSTEM_PROMPT`：助手从所有上传图片中读取商品尺寸。规则：
  - **仅当**图片有明确可见的尺寸文字（吊牌/标签/包装/尺码表/标注的测量值，例 `"M"`、`"27cm"`、`"縦30×横20×高10cm"`）才返回，原样保留数字/单位/标签。
  - 逐张检查每一张图片；尺寸可能出现在任意一张上。
  - 不得根据外观、比例或参照物推断；无明确尺寸文字则返回 `null`。不确定时返回 `null`。
  - JSON schema：`{"product_size": "string or null"}`。
- `SIZE_ONLY_USER_PROMPT`：要求只返回 `product_size`，无明确信息时为 `null`。
- 在 `app/llm/prompt_store.py` 的 `PROMPT_REGISTRY` 注册两条（stage `size_only`，标签「尺寸提取 · System」「尺寸提取 · User」）。

### 3. service 层（镜像 price）

- `_call_size_only_llm(image_data_urls, model_override=None) -> (parsed, attempts)`：
  - stage=`size_only`；逐图加文本 "Image i of n: inspect this image for any clearly visible product size text."；`temperature=0.1`，小 `max_tokens`（约 200）。
  - 模型解析：`model_override or settings.vision_model`，fallback 用 `vision_fallback_models`。
  - 复用 `_record_stage` 记录可观测性。
- `extract_size(images, debug=False, model_override=None, started_at=None) -> dict`：
  - 校验非空；全部图片转 data url；调用 `_call_size_only_llm`。
  - 返回 `{"product_size": <str|None>, "timings": {"size_ms": <float>}}`，其中 `product_size = _clean_string(ai_raw.get("product_size","")) or None`。
  - debug 时附 `_debug = {"size_ai_raw": ai_raw, "attempts": {"size_only": [...]}}`。

### 4. 新接口 `POST /api/v1/mercari/image/size`（镜像 `/image/price`）

- 入参（multipart/form-data）：`image_list`（必填，可多张）、`debug`、`vision_model`。
- 处理：`_prepare_image_payloads` → `run_in_threadpool(analyzer.extract_size, ...)`。
- 错误：`BadRequestError→400`、`LLMAllAttemptsFailedError→502`（`_format_attempts_error`）、其它→500。
- debug 时附 `image_processing`；非 debug 时 `result.pop("timings", None)`，与 price 一致。
- 响应（200，默认）：`{"product_size": "40 to 45"}` 或 `{"product_size": null}`。
- 响应（200，debug）：附 `timings.size_ms`、`image_processing`、`_debug`。

### 5. 前端（`web/index.html`）

- 在「⚡ 快速识别价格」按钮旁加「📏 识别尺寸」按钮；i18n（zh：`📏 识别尺寸`，ja：`📏 サイズだけ高速取得`）。
- `buildSizeEndpoint()`（仿 `buildPriceEndpoint`，把 `/image/analyze` 替换为 `/image/size`）。
- `processSizeOnly()`（仿 `processPriceOnly`）：POST `image_list` + 可选 `vision_model`，结果走 `addResultCard` → `renderStructuredData`，自动展示「商品尺寸」。
- 复用既有 `pricing/processing` 互斥模式，新增 `sizing` 状态量避免重复点击。

### 6. 测试

- 新增 `tests/test_size_endpoint.py`，镜像 `tests/test_price_endpoint.py`：mock LLM 返回，断言有尺寸→`product_size` 为字符串、无尺寸→`null`、debug 字段、400/502 错误路径。
- 更新 `tests/test_prompt_store.py` 中 registry 数量断言（17 → 19）。
- 还原 fast classification golden 后，`test_prompt_store.py` 的 golden 测试仍通过。
- 全量 `pytest` 通过。

### 7. 文档

- `API.md`：从 `/image/analyze` 与轮询接口移除 `product_size` 字段与说明；新增 `POST /api/v1/mercari/image/size` 章节（仿 `/image/price`）。
- `README.md`：fast vision 输出去掉 `product_size`；链路说明/接口列表补充尺寸接口。

## 数据流

```
前端「识别尺寸」按钮
  → POST /api/v1/mercari/image/size (image_list=所有图片)
    → analyzer.extract_size
      → _call_size_only_llm (检查每张图)
        → vision LLM (SIZE_ONLY prompts)
      ← {product_size: <str|null>}
  ← {product_size: ...}  → renderStructuredData 展示「📏 商品尺寸」
```

分类接口 `/image/analyze` 与本接口完全解耦，互不影响速度。

## 验证

- 单元：`pytest`（含新 `test_size_endpoint.py`）。
- e2e：用真实图片（有尺寸：鞋盒「Sizes:40 to 45」；无尺寸：咖啡场景）打 `/image/size`，确认分别返回字符串与 `null`；并在测试页点「识别尺寸」按钮验证展示。
