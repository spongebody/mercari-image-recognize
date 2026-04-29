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
- 图片识别阶段会优先读取图片中清晰可见的实际标价。
- `tax_excluded` 表示图片里直接读到的商品价格；`tax_included` 表示图片里直接读到的税后价格，没有则为 `null`。
- 如果识别到 `tax_excluded`，`prices` 返回空数组；如果没有明显标价，则 `tax_excluded` / `tax_included` 为 `null`，`prices` 返回 3 个日元参考价格。
- 价格仅作为 LLM 初步预测，不使用在线搜索，也不再调用独立价格模型。

## 接口列表

### GET /config
返回 API 配置页面。

说明：
- 该页面由当前 FastAPI 服务直接提供，不需要额外运行前端服务。
- 页面保存配置后会写入项目根目录 `.env`，并立即影响后续 API 请求。
- 页面不暴露 `OPENROUTER_API_KEY`。

### GET /api/v1/config
读取当前可在页面上管理的运行时配置。

#### 响应（200）

```json
{
  "VISION_MODEL": "...",
  "CATEGORY_MODEL": "...",
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
- `SHOWCASE_MODEL`：`POST /api/v1/showcase/generate` 在请求未传 `model` 时使用的默认图生图模型。

### PUT /api/v1/config
保存配置并立即生效。

#### 请求（application/json）

```json
{
  "VISION_MODEL": "openai/gpt-4.1-mini",
  "CATEGORY_MODEL": "openai/gpt-4.1-mini",
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
上传并解析商品图片。

#### 请求（multipart/form-data）
字段：
- `image_list`（文件，必填，可多次上传）：商品图片列表（支持多张，如正面/背面/包装/标签；JPG/PNG/GIF 等）。
- `language`（字符串，可选）：`ja` / `en` / `zh`，默认 `ja`。
- `debug`（字符串，可选）：`true` / `1` / `yes` 等，默认 `false`。
- `category_count`（整数，可选）：返回类别数量（1-3），默认 `1`。
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
  "tax_excluded": 980,
  "tax_included": 1078,
  "prices": [],
  "categories": [
    {
      "id": "123",
      "rakuten_id": "123",
      "name": "カテゴリ/パス",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "..."
    }
  ],
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
    "total_ms": 2345.67,
    "vision_ms": 1234.56,
    "category_ms": 789.01
  },
  "image_processing": [
    {
      "index": 1,
      "filename": "front.jpg",
      "compressed": true,
      "original_bytes": 2457600,
      "processed_bytes": 524288
    }
  ],
  "best_target_path": "...",
  "best_category_id": "...",
  "rakuten_id": "...",
  "meru_id": "...",
  "rakuma_id": "...",
  "zenplus_id": "...",
  "alternatives": [
    {
      "target_path": "...",
      "category_id": "...",
      "rakuten_id": "...",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "..."
    }
  ],
  "_debug": {
    "ai_raw": {"...": "..."},
    "group_name": "...",
    "llm_category_raw": {"...": "..."}
  }
}
```

说明：
- `_debug` 仅在 `debug=true` 且服务端允许调试时返回。
- `tax_excluded` / `tax_included` 只表示图片中直接读到的价格。
- `prices` 为视觉模型给出的初步价格预测；当 `tax_excluded` 有值时必须为空数组。
- `best_target_path` / `alternatives` 在成功匹配分类路径时返回。
- `timings.total_ms` 表示服务端分析处理总耗时；`timings.vision_ms` 表示图片识别 LLM 调用耗时；`timings.category_ms` 表示分类 LLM 调用耗时，单位均为毫秒。
- `image_processing` 表示后端图片预处理结果；当单张图片超过服务端 `IMAGE_COMPRESSION_THRESHOLD_MB` 阈值并成功压缩时，`compressed=true` 且包含压缩前后的字节数。

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
  - `stage` ∈ `"vision"`, `"category"`, `"title_category"`。
  - `kind` 当前固定为 `"all_attempts_failed"`，保留供未来扩展。
  - `attempts[].error_kind` ∈ `"request_failed"`, `"parse_failed"`, `"budget_exhausted"`。
  - `attempts[].status_code` 在 `parse_failed` 时为 200；`request_failed` 时尽力从上游消息中解析整数 HTTP 状态码，否则 `null`。
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
  "alternatives": [
    {
      "target_path": "...",
      "category_id": "...",
      "rakuten_id": "...",
      "meru_id": "...",
      "rakuma_id": "...",
      "zenplus_id": "..."
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
