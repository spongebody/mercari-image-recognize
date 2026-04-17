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

### 价格策略（price_strategy）
- `vision`（默认）：由视觉模型直接生成价格。
- `vision_online`：视觉模型在提示中进行搜索参考定价。
- `dedicated`：独立价格模型；失败时 `prices` 为空。

## 接口列表

### POST /api/v1/mercari/image/analyze
上传并解析商品图片。

#### 请求（multipart/form-data）
字段：
- `image_list`（文件，必填，可多次上传）：商品图片列表（支持多张，如正面/背面/包装/标签；JPG/PNG/GIF 等）。
- `language`（字符串，可选）：`ja` / `en` / `zh`，默认 `ja`。
- `debug`（字符串，可选）：`true` / `1` / `yes` 等，默认 `false`。
- `category_count`（整数，可选）：返回类别数量（1-3），默认 `1`。
- `price_strategy`（字符串，可选）：`vision` / `vision_online` / `dedicated`，默认 `vision`。
- `vision_model`（字符串，可选）：视觉模型覆盖。
- `category_model`（字符串，可选）：分类模型覆盖。
- `price_model`（字符串，可选）：价格模型覆盖。

说明：
- `image_list` 是**文件列表字段**
- 传值方式：`multipart/form-data` 中**同名字段重复出现**，每个字段为一个文件。
- 支持多张图片同时分析，优先从正反面、标签、包装细节中提取型号、品牌、颜色、尺寸、重量、成色等信息。

示例：

```bash
curl -X POST "http://localhost:8000/api/v1/mercari/image/analyze" \
  -F "image_list=@/path/to/item_front.jpg" \
  -F "image_list=@/path/to/item_back.jpg" \
  -F "language=ja" \
  -F "price_strategy=vision"
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
  "prices": [1000, 2000, 3000],
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
  "price_citations": [
    {"url": "...", "title": "...", "content": "..."}
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
    "llm_category_raw": {"...": "..."},
    "price_raw": {"...": "..."},
    "price_strategy": "vision",
    "price_citations": [],
    "price_error": null,
    "price_source": "vision"
  }
}
```

说明：
- `_debug` 仅在 `debug=true` 且服务端允许调试时返回。
- `prices` 在定价失败或禁用时可能为空。
- `best_target_path` / `alternatives` 在成功匹配分类路径时返回。

#### 错误
- `400`：请求无效（图片格式/参数错误、解析失败等）。
- `502`：LLM 请求失败。
- `500`：内部错误。

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
- `400`：请求无效
- `502`：LLM 请求失败
- `500`：内部错误

### GET /health
健康检查。

#### 响应（200）

```json
{
  "status": "ok",
  "models": {
    "vision_model": "...",
    "vision_model_online": "...",
    "category_model": "...",
    "price_model": "..."
  }
}
```
