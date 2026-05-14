# 接口说明

本文档说明本服务对外提供的 HTTP API。

## 基本信息

- 服务地址：`http://43.133.171.134:39008`
- 所有响应均为 JSON。

## 认证

- 无内置认证。

## 通用说明

### description 字段格式

```json
{
  "description": {
    "product_details": { // 商品详情
      "brand": "string", // 品牌
      "product_name": "string", // 商品名
      "model_number": "string", // 型号
      "target": "string", // 对象
      "color": "string", // 颜色
      "size": "string", // 尺寸
      "weight": "string", // 重量
      "condition": "string" // 成色
    },
    "product_intro": "string", // 商品介绍
    "recommendation": "string", // 推荐语
    "search_keywords": ["string"] // 搜索关键词
  }
}
```

规则：
- `product_details` 必须包含 8 个字段；未知字段用空字符串。
- `search_keywords` 为字符串数组。
- 文本内容使用客户端 `language` 指定的语言。

### 价格预测
- 当前图片识别主接口已不再返回价格相关字段；价格由用户手动填写。上述字段仅适用于仍使用旧同步识别结果的兼容说明。

## 接口列表

### GET /config
返回 API 配置页面。

### GET /api/v1/config
读取当前可在页面上管理的运行时配置。

#### 响应（200）

```json
{
  "VISION_MODEL": "...",
  "CATEGORY_MODEL": "...",
  "PRODUCT_DATA_MODEL": "google/gemini-2.5-flash",
  "SHOWCASE_MODEL": "google/gemini-3.1-flash-image-preview",
  "LOG_LLM_RAW": false,
  "LOG_REQUESTS": true,
  "ENABLE_DEBUG": true,
  "IMAGE_COMPRESSION_THRESHOLD_MB": 1,
  "REQUEST_TIMEOUT": 60,
  "VISION_FALLBACK_MODELS": ["..."],
  "CATEGORY_FALLBACK_MODELS": ["..."],
  "MODEL_CALL_MAX_RETRIES": 3,
  "MODEL_CALL_TOTAL_BUDGET_SECONDS": 120
}
```

字段说明：
- `PRODUCT_DATA_MODEL`：商品数据生成模型，用于多图生成标题、描述和品牌信息，默认 `google/gemini-2.5-flash`。
- `SHOWCASE_MODEL`：`POST /api/v1/showcase/generate` 在请求未传 `model` 时使用的默认图生图模型。

### PUT /api/v1/config
保存配置并立即生效。

#### 请求（application/json）

```json
{
  "VISION_MODEL": "openai/gpt-4.1-mini",
  "CATEGORY_MODEL": "openai/gpt-4.1-mini",
  "PRODUCT_DATA_MODEL": "google/gemini-2.5-flash",
  "SHOWCASE_MODEL": "google/gemini-3.1-flash-image-preview",
  "LOG_REQUESTS": true
}
```

说明：
- 只允许更新白名单字段；未知字段返回 `400`。
- 带 `Origin` 请求头的跨站写入会返回 `403`；配置页面本身使用同源请求。
- 保存会同步写入 `.env`，因此服务重启后仍然保持相同配置。
- 保存后当前进程内的 `settings` 会更新，后续请求立即使用新配置。
- 修改 `SHOWCASE_MODEL` 后，进程内的 showcase 客户端 / 服务实例会同步更新，下一次 `POST /api/v1/showcase/generate` 立即使用新默认值。

### POST /api/v1/mercari/image/analyze
上传并解析商品图片。接口会并行启动“第一张图快分类”和“多图商品数据生成”两条链路，优先返回分类结果和轮询 `job_id`。

#### 请求（multipart/form-data）
字段：
- `image_list`（文件，必填，可多次上传）：商品图片列表（支持多张，如正面/背面/包装/标签；JPG/PNG/GIF 等）。
- `language`（字符串，可选）：`ja` / `en` / `zh`，默认 `ja`。
- `debug`（字符串，可选）：`true` / `1` / `yes` 等，默认 `false`。
- `vision_model`（字符串，可选）：视觉模型覆盖。
- `category_model`（字符串，可选）：分类模型覆盖。

说明：
- `image_list` 是**文件列表字段**
- 传值方式：`multipart/form-data` 中**同名字段重复出现**，每个字段为一个文件。
- 支持多张图片同时分析，优先从正反面、标签、包装细节中提取型号、品牌、颜色、尺寸、重量、成色等信息。

示例：

```bash
curl -X POST "http://localhost:8000/api/v1/mercari/image/analyze" \
  -F "image_list=@/path/to/item_front.jpg" \
  -F "image_list=@/path/to/item_back.jpg" \
  -F "language=ja"
```

前端（FormData）示例：
```js
const formData = new FormData();
files.forEach((file) => {
  formData.append("image_list", file);
});
```

#### 响应（200）

```json
{
  "job_id": "8d65f5c2...",
  "status": "product_pending",
  "categories": [
    {
      "id": "123",
      "rakuten_id": "123",
      "name": "カテゴリ/パス",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "...",
      "meru_path": "...",
      "rakuma_path": "...",
      "zenplus_path": "...",
      "confidence": 0.92
    }
  ],
  "timings": {
    "total_ms": 900.12,
    "classification_ms": 900.12
  },
  "image_processing": [
    {
      "index": 1,
      "filename": "front.png",
      "compressed": true,
      "original_bytes": 2000000,
      "processed_bytes": 500000
    }
  ],
  "best_target_path": "...",
  "best_category_id": "...",
  "rakuten_id": "...",
  "meru_id": "...",
  "rakuma_id": "...",
  "zenplus_id": "...",
  "meru_path": "...",
  "rakuma_path": "...",
  "zenplus_path": "...",
  "alternatives": [
    {
      "target_path": "...",
      "category_id": "...",
      "rakuten_id": "...",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "...",
      "meru_path": "...",
      "rakuma_path": "...",
      "zenplus_path": "..."
    }
  ],
  "_debug": {
    "fast_ai_raw": {"...": "..."},
    "group_name": "...",
    "llm_category_raw": {"...": "..."}
  }
}
```

说明：
- `status=product_pending` 表示商品数据仍在后台生成；使用 `job_id` 调用轮询接口获取完整商品数据。
- 如果商品数据在首接口返回前已经完成，首接口会直接返回 `status=completed`，并合并 `title`、`description`、`brand_name`、`brand_id_obj`。
- `_debug` 仅在 `debug=true` 且服务端允许调试时返回。
- 主接口不再返回价格相关字段。
- `categories` 始终返回置信度从高到低的最多 3 个匹配分类（候选目录中可信匹配少于 3 时可能少于 3 个）；`best_target_path` / `alternatives` 在成功匹配分类路径时返回，同样按置信度降序。
- `timings.total_ms` 表示从请求开始到当前响应时刻的实际墙钟耗时；`timings.classification_ms` 表示首接口分类链路耗时，单位均为毫秒。由于分类与商品数据并行执行，完成态的 `total_ms ≈ max(classification_ms, product_data_ms)`，而非两者相加。
- `image_processing` 字段返回每张上传图片的压缩处理结果（是否压缩、原始/处理后字节数等）。

### GET /api/v1/mercari/image/analyze/{job_id}
轮询商品数据生成结果。

#### 响应（200，生成中）

```json
{
  "job_id": "8d65f5c2...",
  "status": "product_pending",
  "categories": [
    {
      "id": "123",
      "rakuten_id": "123",
      "name": "カテゴリ/パス",
      "confidence": 0.92
    }
  ],
  "best_target_path": "...",
  "best_category_id": "...",
  "rakuten_id": "..."
}
```

#### 响应（200，已完成）

```json
{
  "job_id": "8d65f5c2...",
  "status": "completed",
  "title": "...",
  "description": {
    "product_details": {
      "brand": "...",
      "product_name": "...",
      "model_number": "...",
      "target": "...",
      "color": "...",
      "size": "...",
      "weight": "...",
      "condition": "..."
    },
    "product_intro": "...",
    "recommendation": "...",
    "search_keywords": ["..."]
  },
  "categories": [
    {
      "id": "123",
      "rakuten_id": "123",
      "name": "カテゴリ/パス",
      "confidence": 0.92
    }
  ],
  "brand_name": "...",
  "tax_excluded": null,
  "tax_included": null,
  "prices": [1000, 1500, 2000],
  "brand_id_obj": {
    "rakuten_brand_id": "...",
    "yshop_brand_id": "...",
    "yauc_brand_id": "...",
    "meru_brand_id": "...",
    "ebay_brand_id": "...",
    "rakuma_brand_id": "...",
    "amazon_brand_id": "...",
    "qoo10_brand_id": "..."
  },
  "timings": {
    "total_ms": 1800.34,
    "classification_ms": 900.12,
    "product_data_ms": 1800.34
  },
  "product_data_source": "primary",
  "image_processing": [
    {
      "index": 1,
      "filename": "front.png",
      "compressed": true,
      "original_bytes": 2000000,
      "processed_bytes": 500000
    }
  ]
}
```

说明：
- `brand_name` / `brand_id_obj` 只在商品数据完成后返回。
- `tax_excluded`、`tax_included`、`prices` 始终存在。初次 `product_pending` 时默认返回 `null` / `null` / `[]`；完成后由商品数据生成链路返回价格。
- 如果图片中有明确可见的实际商品价格，`tax_excluded` 返回不含税价格整数日元，`tax_included` 返回含税价格整数日元或 `null`，并且 `prices` 为 `[]`。
- 如果没有明确可见的实际商品价格，`tax_excluded` 和 `tax_included` 为 `null`，`prices` 返回 `[poor, average, good]` 三个按成色升序的参考价格。
- 由于分类与商品数据在服务端并行执行，完成态 `timings.total_ms = max(classification_ms, product_data_ms)`，反映两个 LLM 链路重叠后的实际耗时；`classification_ms` 表示首接口分类耗时，`product_data_ms` 表示轮询侧商品数据生成耗时。
- `image_processing` 与首接口保持一致，标记每张图片是否被压缩。
- `product_data_source` 标识商品数据来自哪个 LLM：`"primary"` 表示主模型（`PRODUCT_DATA_MODEL`），`"fallback"` 表示保底小模型（`PRODUCT_DATA_FALLBACK_MODEL`）。当主模型超过 `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS` 仍未返回，或主模型失败时，保底模型结果会被采用；`PRODUCT_DATA_FALLBACK_MODEL` 留空可关闭保底。
- 内存任务默认只保存在当前 API 进程中；服务重启后未完成的 `job_id` 会失效。

#### 错误
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
  - `stage` ∈ `"fast_vision"`, `"product_data"`, `"product_data_regeneration"`, `"vision"`, `"category"`, `"title_category"`。
  - `kind` 当前固定为 `"all_attempts_failed"`，保留供未来扩展。
  - `attempts[].error_kind` ∈ `"request_failed"`, `"parse_failed"`, `"budget_exhausted"`。
  - `attempts[].status_code` 在 `parse_failed` 时为 200；`request_failed` 时尽力从上游消息中解析整数 HTTP 状态码，否则 `null`。
- `500`：内部错误，`detail` 为字符串。

### POST /api/v1/mercari/product-data/regenerate
根据商品图片、可选原始商品数据、可选用户补充信息，重新生成商品数据。适用于用户对首次生成结果不满意后补充成色、关键词、同款信息、材质说明等内容再生成。

#### 请求（multipart/form-data）
字段：
- `image_list`（文件，必填，可多次上传）：商品图片列表，校验和压缩逻辑与 `/api/v1/mercari/image/analyze` 相同。
- `language`（字符串，可选）：`ja` / `en` / `zh`，默认 `ja`。
- `original_product_data`（字符串，可选）：原始商品数据 JSON 字符串，必须是 JSON object。
- `user_notes`（字符串，可选）：用户补充信息。生成时优先级最高。
- `debug`（字符串，可选）：`true` / `1` / `yes` 等，默认 `false`。

优先级：
1. 如果提供 `user_notes`，模型优先使用用户补充信息。
2. 如果没有 `user_notes` 但有 `original_product_data`，模型基于原始商品数据和图片进行优化。
3. 如果两者都没有，模型深入分析图片并生成新的商品数据。

示例：

```bash
curl -X POST "http://localhost:8000/api/v1/mercari/product-data/regenerate" \
  -F "image_list=@/path/to/item_front.jpg" \
  -F "image_list=@/path/to/item_back.jpg" \
  -F "language=ja" \
  -F 'original_product_data={"title":"古いタイトル","brand_name":"Nike"}' \
  -F "user_notes=成色は目立つ傷なし。明らか同款。"
```

#### 响应（200）

```json
{
  "title": "...",
  "description": {
    "product_details": {
      "brand": "...",
      "product_name": "...",
      "model_number": "...",
      "target": "...",
      "color": "...",
      "size": "...",
      "weight": "...",
      "condition": "..."
    },
    "product_intro": "...",
    "recommendation": "...",
    "search_keywords": ["..."]
  },
  "brand_name": "...",
  "brand_id_obj": {
    "rakuten_brand_id": "...",
    "yshop_brand_id": "...",
    "yauc_brand_id": "...",
    "meru_brand_id": "...",
    "ebay_brand_id": "...",
    "rakuma_brand_id": "...",
    "amazon_brand_id": "...",
    "qoo10_brand_id": "..."
  },
  "timings": {
    "product_data_ms": 1200.34
  }
}
```

说明：
- 标题会通过模型 prompt 和服务端兜底保证至少 80 个字符。
- 响应不包含价格字段，也不会重新分类。
- `brand_id_obj` 来自品牌 CSV 匹配；未匹配时各平台品牌 ID 为空字符串。

#### 错误
- `400`：请求无效（图片格式/大小、语言、`original_product_data` JSON 格式错误）。`detail` 为字符串。
- `502`：LLM 链路全部尝试失败。`detail` 为结构化对象，schema 与 image/analyze 接口一致；`stage` 为 `"product_data_regeneration"`。
- `500`：内部错误，`detail` 为字符串。

### POST /api/v1/mercari/title/analyze
根据标题分类。

#### 请求（application/json）

```json
{
  "title": "string",
  "image_url": "https://example.com/item.jpg",
  "language": "ja"
}
```

字段：
- `title`（字符串，必填）
- `image_url`（字符串，可选）：用于兜底分类
- `language`（字符串，可选）：`ja` / `en` / `zh`，默认 `ja`

#### 响应（200）

```json
{
  "best_target_path": "...",
  "best_category_id": "...",
  "rakuten_id": "...",
  "meru_id": "...",
  "rakuma_id": "...",
  "zenplus_id": "...",
  "meru_path": "...",
  "rakuma_path": "...",
  "zenplus_path": "...",
  "alternatives": [
    {
      "target_path": "...",
      "category_id": "...",
      "rakuten_id": "...",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "...",
      "meru_path": "...",
      "rakuma_path": "...",
      "zenplus_path": "..."
    }
  ]
}
```

#### 错误
- `400`：请求无效。`detail` 为字符串。
- `502`：LLM 链路全部尝试失败。`detail` 为结构化对象，schema 与 image/analyze 接口一致（见上文）；`stage` 取值为 `"title_category"`、`"category"` 或在图片兜底失败时为 `"vision"`。
- `500`：内部错误，`detail` 为字符串。

### POST /api/v1/showcase/generate
上传单张商品图，调用 OpenRouter 图生图模型生成电商效果展示图。

#### 请求（multipart/form-data）
字段：
- `file`（文件，必填）：单张商品图，`Content-Type` 必须为 `image/*`。
- `prompt_hint`（字符串，可选）：在内置 hero prompt 之后追加的补充提示词，例如风格、场景、光线等关键词。
- `model`（字符串，可选）：合成模型覆盖。留空使用服务端默认 `SHOWCASE_MODEL`（默认 `google/gemini-3.1-flash-image-preview`）。该模型必须支持 OpenRouter `chat/completions` 的 image+text → image modalities，例如 Gemini Flash Image 系列。

示例：
```bash
curl -X POST "http://localhost:8000/api/v1/showcase/generate" \
  -F "file=@/path/to/item.jpg" \
  -F "prompt_hint=sunlit Tokyo street, golden hour" \
  -F "model=google/gemini-3.1-flash-image-preview"
```

前端（FormData）示例：
```js
const formData = new FormData();
formData.append("file", file);
if (promptHint) formData.append("prompt_hint", promptHint);
if (modelOverride) formData.append("model", modelOverride);
```

#### 响应（200，生成成功）

```json
{
  "request_id": "20260429_205755_b9855253",
  "status": "succeeded",
  "model": "google/gemini-3.1-flash-image-preview",
  "model_override": null,
  "prompt_hint": "sunlit Tokyo street, golden hour",
  "final_prompt": "Create a realistic, high-conversion e-commerce hero image ...",
  "image_base64": "<标准 base64 字符串>",
  "image_mime_type": "image/jpeg",
  "input_path": null,
  "output_path": null,
  "latency_ms": 24751,
  "created_at": "2026-04-29T20:57:55.671897+08:00"
}
```

字段说明：
- `request_id`：本次请求的全局唯一 ID（格式 `YYYYMMDD_HHMMSS_<8位随机>`），同时也是归档 JSON 文件名。
- `model`：本次实际使用的模型 ID。当 `model` 表单字段被设置时等于该值，否则等于服务端默认。
- `model_override`：调用方传入的 `model` 参数（trim 后），未传或全空白时为 `null`。
- `prompt_hint`：调用方传入的提示词补充原值。
- `final_prompt`：内置 hero prompt 与 `prompt_hint` 拼接后实际发给模型的完整提示词。
- `image_base64`：直接 base64 编码后的图片字节。建议在前端通过 `data:${image_mime_type};base64,${image_base64}` 形成 data URL 渲染或下载。
- `image_mime_type`：从上游响应解析得到的 MIME 类型，常见为 `image/jpeg` 或 `image/png`。
- `input_path` / `output_path`：仅当服务端开启 `SHOWCASE_RETAIN_INPUT_FILES` / `SHOWCASE_RETAIN_OUTPUT_FILES` 时返回相对项目根目录的落盘路径；默认两个开关均关，因此通常为 `null`。
- `latency_ms`：本次端到端处理总耗时（毫秒），含上传读取、上游调用、归档落盘。
- `created_at`：使用 `SHOWCASE_TIMEZONE`（默认 `Asia/Shanghai`）的 ISO 时间戳。

附加说明：
- 默认配置下，每次请求会向 `logs/showcase/YYYYMMDD/<request_id>.json` 写一份完整归档（含 `final_prompt`、`upstream_status_code`、`retry_count`、`error_*` 等字段，但不写 `image_base64`，以避免日志膨胀）。
- 端到端耗时取决于上游模型，单次约 15–30 秒。客户端建议设置 ≥ 60 秒的请求超时。

#### 错误
- `400`：请求无效。`detail` 为字符串：
  - `"Only image uploads are supported."`：缺少 `file` 字段或 `Content-Type` 非 `image/*`。
  - `"Uploaded file is empty."`：上传文件字节数为 0。
  - `"Failed to read uploaded file."`：服务端读取上传流失败。
- `422`：FastAPI 表单校验失败（例如完全没传 `file` 字段）。
- `502`：合成上游调用失败。响应体直接返回失败结构（不包在 `detail` 里），格式如下：

  ```json
  {
    "request_id": "20260429_205755_b9855253",
    "status": "failed",
    "model": "google/gemini-3.1-flash-image-preview",
    "model_override": null,
    "error_code": "upstream_generation_failed",
    "error_message": "OpenRouter returned status 500: ...",
    "latency_ms": 24123,
    "created_at": "2026-04-29T20:57:55.671897+08:00"
  }
  ```

  `error_code` 枚举：
  - `missing_api_key`：服务端未配置 `OPENROUTER_API_KEY`。
  - `missing_model`：服务端未配置 `SHOWCASE_MODEL` 且请求未传 `model`。
  - `upstream_generation_failed`：上游 4xx/5xx、网络异常、超出最大重试次数，或响应中没有可解析的图片字段。`error_message` 含原始上游消息片段。

  注意：与 `/api/v1/mercari/image/analyze` 不同，本接口的 502 直接以失败 schema 作为响应体顶层，不嵌在 `detail` 字段里。

### GET /health
健康检查。

#### 响应（200）

```json
{
  "status": "ok",
  "models": {
    "vision_model": "...",
    "category_model": "...",
    "showcase_model": "..."
  }
}
```
