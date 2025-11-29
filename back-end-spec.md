# Mercari 商品图片识别接口技术方案（v1）

## 1. 目标与整体概览

### 1.1 业务目标

实现一个 HTTP 接口服务，用户上传 Mercari 商品图片后，接口自动识别并返回用于商品上架所需的关键信息：

* 商品标题：`title`
* 商品描述：`description`
* 三个可能的价格（单位：日元，整数）：`prices[3]`
* 三个可能的分类（细分类，带 `category_id + category_name`）：`categories[3]`
* 品牌名称（仅当存在于品牌数据集中，否则返回空字符串）：`brand_name`

### 1.2 技术方案核心要点

1. 使用 OpenRouter 提供的多模态 LLM（支持图片输入）从图片中识别商品信息。
2. 使用同样通过 OpenRouter 调用的文本 LLM，根据识别出的商品信息和某个一级分类下的所有类目条目，从中选出 Top3 细分类。
3. 品牌名称通过 LLM 初步识别后，在本地品牌数据集中验证，仅当匹配成功才返回。
4. 不使用向量数据库及相似度检索。类目 Top3 全部交给第二次 LLM 调用来完成。
5. 所有 LLM 输出必须为严格 JSON，后端负责解析、校验、兜底。

---

## 2. 外部接口设计

### 2.1 HTTP Endpoint

* URL：`POST /api/v1/mercari/image/analyze`
* Method：`POST`
* 协议：HTTP/HTTPS（推荐 HTTPS）
* 鉴权：建议使用一个简单的 API Key 机制或由上层网关统一处理。此文档不展开。

### 2.2 请求参数

#### 2.2.1 Content-Type：multipart/form-data（推荐）

字段约定：

* `image`（必填）：商品图片文件

  * 类型限制：`image/jpeg`、`image/png`、`image/webp` 等常见格式。具体格式由服务端配置。
  * 当前版本只支持单张图片。
* `language`（可选）：用于控制标题和描述的语言

  * 字符串枚举：`"ja"`、`"en"`、`"zh"`，默认 `"ja"`。
  * 当前建议使用 `"ja"`。
* `debug`（可选）：是否返回调试信息

  * 可选值：`"true"` 或 `"false"`，默认 `"false"`。
  * 若为 `"true"`，响应中会附带 `_debug` 字段，包含 LLM 原始输出，用于排查问题。

> 注：后续可扩展支持 JSON 请求（例如传 `image_url`），当前版本可以先不支持。

### 2.3 响应 JSON 格式

成功时返回：

```json
{
  "title": "Nintendo Switch Lite ターコイズ 本体",
  "description": "状態は良好で、付属品は本体のみです。動作確認済みです。",
  "prices": [8500, 9000, 9500],
  "categories": [
    {
      "id": "003",
      "name": "CD・DVD・ブルーレイ > CD > アニメ"
    },
    {
      "id": "002",
      "name": "CD・DVD・ブルーレイ > CD > その他"
    },
    {
      "id": "001",
      "name": "CD・DVD・ブルーレイ > CD > K-POP・アジア"
    }
  ],
  "brand_name": "Nintendo"
}
```

字段说明：

* `title`：字符串，商品标题。
* `description`：字符串，商品描述。
* `prices`：长度为 3 的数组，每个元素为整数，单位日元，从小到大排序。
* `categories`：数组，最多 3 个元素，每个元素有：

  * `id`：字符串，与类目数据集中的 `category_id` 一致。
  * `name`：字符串，与类目数据集中的 `category_name` 一致，即完整路径。
* `brand_name`：字符串，如果品牌经验证不在品牌数据集中则返回空字符串。

当 `debug=true` 时，响应中可以多一个 `_debug` 字段：

```json
"_debug": {
  "ai_raw": { },
  "group_name": "CD・DVD・ブルーレイ",
  "llm_category_raw": { }
}
```

---

## 3. 数据源设计

### 3.1 品牌数据集

品牌数据 CSV 格式示例：

```csv
id,name,name_jp,name_en
225nDaWCk4MpMbnFP6a5An,Xmiss,キスミス,Xmiss
227vuRYYdqkMVDwJuJQd5B,GRANDPHASE,グランフェイズ,GRANDPHASE
22AkY5Vrv76PsWr8q6MCu7,Lara Guidotti,ラーラグィドッティ,Lara Guidotti
22AqCQCGPsmBv3vzFMSgvJ,PinkishBeaute,ピンキッシュボーテ,PinkishBeaute
22JwnhFS88dB2xLGX3uNwj,LaCheteau,ラシュトー,LaCheteau
```

字段说明：

* `id`：品牌唯一 ID。
* `name`：品牌通用名称。
* `name_jp`：日文名称。
* `name_en`：英文名称。

总数据量约 52,681 条。

**加载与索引要求：**

* 服务启动时读取整份 CSV 至内存。
* 为品牌匹配建立以下索引：

  * `normalized(name)` → 品牌行
  * `normalized(name_jp)` → 品牌行
  * `normalized(name_en)` → 品牌行

其中 `normalized` 需要做的归一化包括：

* 去除前后空格。
* 转为小写。
* 全角半角统一（可选）。
* 去掉常见符号（如 `®`、`©` 等）。
* 多个空白字符合并为一个空格。

提供一个统一的品牌匹配逻辑：

* 输入：LLM 返回的 `brand_name`（原始字符串）。
* 先归一化后做精确匹配。
* 如有需要，可以在精确匹配失败时做阈值较高的模糊匹配（避免误匹配）。
* 若匹配成功，返回品牌行；否则返回空（表示品牌不在数据集）。

最终接口返回 `brand_name` 时，使用品牌数据集中的官方名称，例如 `name` 字段。

---

### 3.2 类目数据集

类目数据 CSV 最终格式约定为：

```csv
category_id,category_name,group_name
001,CD・DVD・ブルーレイ > CD > K-POP・アジア,CD・DVD・ブルーレイ
002,CD・DVD・ブルーレイ > CD > その他,CD・DVD・ブルーレイ
003,CD・DVD・ブルーレイ > CD > アニメ,CD・DVD・ブルーレイ
004,DIY・工具 > ドライバー・レンチ > スパナ・レンチ > T形レンチ,DIY・工具
005,DIY・工具 > ドライバー・レンチ > ソケット > その他,DIY・工具
```

字段说明：

* `category_id`：细分类的唯一 ID。全局唯一且稳定。
* `category_name`：完整类目路径，使用 `>` 连接各级名称。
* `group_name`：一级分类名称。

一级分类列表必须限制在以下 22 个值中之一：

1. キッチン・日用品・その他
2. ゲーム・おもちゃ・グッズ
3. スポーツ
4. ファッション
5. 車・バイク・自転車
6. ホビー・楽器・アート
7. アウトドア・釣り・旅行用品
8. ハンドメイド・手芸
9. DIY・工具
10. ベビー・キッズ
11. 家具・インテリア
12. ペット用品
13. ダイエット・健康
14. コスメ・美容
15. スマホ・タブレット・パソコン
16. テレビ・オーディオ・カメラ
17. フラワー・ガーデニング
18. 生活家電・空調
19. チケット
20. 本・雑誌・漫画
21. CD・DVD・ブルーレイ
22. 食品・飲料・酒

**加载与索引要求：**

* 服务启动时读取整份 CSV 至内存。

* 按 `group_name` 建立分组索引：

  * `group_name` → 该一级分类下所有条目列表，每个条目包含：

    * `category_id`
    * `category_name`
    * `group_name`

* 建立 `(group_name, category_name)` → `category_id` 的映射，用于在 LLM 返回类目路径后查回对应 ID。

---

## 4. 系统模块与职责

从实现角度划分，系统可以包含以下模块（不要求必须按模块部署，仅作职责划分）：

1. **API 接口模块**

   * 负责 HTTP 请求接收、参数校验、文件读取、响应返回。
   * 与业务模块交互，不直接操作 LLM 或数据集。

2. **Vision LLM 调用模块**

   * 封装与 OpenRouter 多模态模型的调用逻辑。
   * 输入：图片（转为 base64 的 data URL）以及固定 Prompt。
   * 输出：`title`, `description`, `prices`, `top_level_category`, `brand_name`。

3. **品牌匹配模块**

   * 加载品牌数据集。
   * 提供 `match_brand(raw_name)` 接口，将 LLM 的品牌识别结果映射到数据集中的官方名称。

4. **类目数据模块**

   * 加载类目数据集。
   * 提供：

     * `get_categories_by_group(group_name)` → 列表
     * `find_category_id(group_name, category_name)` → `category_id` 或空。

5. **Category LLM 调用模块（文本 LLM）**

   * 输入：商品信息（title、description、brand、group_name）、指定 group_name 下所有 `category_name`。
   * 使用固定 Prompt 让 LLM 选择 Top1 + 2 个备选（总计最多 Top3）。
   * 输出：最佳类目路径和备选列表。

6. **业务编排模块**

   * 串联上述模块：

     1. 调用 Vision LLM 得到初步商品信息。
     2. 品牌匹配。
     3. 通过 `top_level_category` 映射到 `group_name`。
     4. 根据 `group_name` 获取所有类目条目。
     5. 调用 Category LLM 选择 Top3 类目路径。
     6. 将这些类目路径映射回 `category_id` + `category_name`。
     7. 清洗价格后，组装最终 JSON 响应。

---

## 5. 业务流程详解

### 5.1 Step 1：接收图片与基础校验

1. 接收 multipart/form-data 请求，解析出：

   * `image` 文件
   * `language`（如未提供，默认 `"ja"`）
   * `debug`（如未提供，默认 `false`）
2. 校验图片类型：只接受配置允许的 MIME 类型。
3. 可选：校验图片大小（例如不超过 5MB），必要时进行压缩。
4. 将图片文件内容读取成二进制数组，交给业务编排模块。

### 5.2 Step 2：调用 Vision LLM 识别商品信息

#### 5.2.1 图片编码方式

将图片二进制转为 base64，然后构造 data URL：

* 格式示例：`data:image/jpeg;base64,xxxxx...`

之后在 OpenRouter 请求的 message 中作为 `image_url` 字段传递。

#### 5.2.2 Vision LLM System Prompt（完整）

```text
You are an assistant helping sellers list items on Mercari Japan.

Given ONE product image, your task is:

1. Infer what the product is (type), its condition, important attributes, and any visible details.
2. Generate a short, clear, and buyer-friendly title suitable for a Mercari Japan listing.
3. Generate a concise description in Japanese, mentioning condition, included accessories, and any important notes.
4. Propose 3 reasonable used-item prices in Japanese Yen (integers). 
   - Prices must be sorted from low to high.
   - Prices should reflect typical second-hand prices on Mercari, not official retail prices.
5. Choose the single best matching top-level category from the following list (return exactly one of these strings):
    1. キッチン・日用品・その他
    2. ゲーム・おもちゃ・グッズ
    3. スポーツ
    4. ファッション
    5. 車・バイク・自転車
    6. ホビー・楽器・アート
    7. アウトドア・釣り・旅行用品
    8. ハンドメイド・手芸
    9. DIY・工具
    10. ベビー・キッズ
    11. 家具・インテリア
    12. ペット用品
    13. ダイエット・健康
    14. コスメ・美容
    15. スマホ・タブレット・パソコン
    16. テレビ・オーディオ・カメラ
    17. フラワー・ガーデニング
    18. 生活家電・空調
    19. チケット
    20. 本・雑誌・漫画
    21. CD・DVD・ブルーレイ
    22. 食品・飲料・酒

6. If you can clearly identify a brand name printed on the item or its packaging, 
   return that brand name exactly as printed (for example "Nintendo", "Sony", "UNIQLO").
   If you are not sure or no brand is visible, return an empty string "".

IMPORTANT:
- The title and description must be in Japanese.
- Prices must be integers in Japanese Yen.
- The top_level_category must be exactly one of the provided strings.
- If you are not sure about the brand, do NOT guess; just return an empty string.

You must respond with pure JSON only, without any explanations, without markdown, and without comments.

The JSON schema is:

{
  "title": "string",
  "description": "string",
  "prices": [number, number, number],
  "top_level_category": "string",
  "brand_name": "string"
}
```

#### 5.2.3 Vision LLM User Prompt（示例）

```text
Look at this product image and fill in all JSON fields according to the instructions.

Language for title and description: Japanese.
Prices must be in JPY and integers, sorted from low to high.
If you are not sure about the brand, set "brand_name" to "".
```

图片通过 `image_url` 与该 User 文本一起传入。

#### 5.2.4 Vision LLM 输出处理

1. 从 OpenRouter 响应中提取 `choices[0].message.content`。
2. 该内容为 JSON 字符串，直接使用 JSON 解析。
3. 对解析出的字段做基本校验：

   * `title`、`description`：去除首尾空格。
   * `prices`：保证为数组：

     * 将每个元素转换为整数。
     * 过滤掉不在合理范围的价格（例如小于 100 或大于 1,000,000）。
     * 不足 3 个时可以用最后一个价格重复补足，始终维持长度为 3。
     * 最终按从小到大排序。
   * `top_level_category`：保留原值，用于后续映射到 `group_name`。
   * `brand_name`：保留原值，用于后续品牌匹配。

---

### 5.3 Step 3：品牌匹配

1. 将 LLM 输出的 `brand_name` 作为原始输入。
2. 若为空字符串，直接判定为无品牌，最终输出 `brand_name=""`。
3. 若非空：

   * 使用品牌匹配模块进行归一化与查找。
   * 若在品牌数据集中存在对应项：

     * 建议最终输出使用品牌数据集中的 `name` 字段（或按业务约定使用 `name_jp` 等）。
   * 若不存在，则认为该品牌不在 Mercari 品牌库中：

     * 最终输出 `brand_name=""`。

---

### 5.4 Step 4：类目 Top3 匹配（文本 LLM）

#### 5.4.1 将 top_level_category 映射到 group_name

1. 从 Vision LLM 得到的 `top_level_category` 为字符串。
2. 通过预先定义的 22 个一级分类列表进行映射：

   * 若 `top_level_category` 已经是列表中的完全一致项，则直接作为 `group_name` 使用。
   * 若存在一些格式上的微小差异（如多空格），可以做轻量模糊匹配：

     * 限制匹配阈值较高，例如 90 以上。
3. 如果无法映射为合法的 `group_name`：

   * 当前版本可以直接返回空的 `categories` 数组，或者走后续兜底策略。
   * 建议日志记录，以便后续优化 Prompt 或映射逻辑。

#### 5.4.2 根据 group_name 获取所有候选类目

1. 调用类目数据模块的 `get_categories_by_group(group_name)`。
2. 得到该一级分类下所有条目，每个条目的结构包括：

   * `category_id`
   * `category_name`
   * `group_name`
3. 将这些条目的 `category_name` 提取出来组成候选路径列表，本次方案**不做过滤与预筛选**，全部传给 LLM。

#### 5.4.3 Category LLM System Prompt（完整）

```text
You are an e-commerce taxonomy specialist for the Japanese marketplace Mercari.

Task:
- You are given information about ONE product (title, description, brand, and its top-level category).
- You are also given a list of candidate Mercari category paths under that top-level category.
- Your job is to choose the single best target category path, and also 2 alternative candidate paths (top 3 in total, if available).

Instructions:
- Carefully understand what the product is, how it is used, who it is for, and any important attributes.
- Carefully read all candidate category paths.
- Choose only from the given candidate category paths. Do NOT invent new categories.
- Assign a confidence score in [0,1] to each chosen path.
- Confidence should reflect how likely it is that the product belongs to that category.
- If there are not enough good candidates, you may choose fewer than 3 paths.

Output format:
- Respond with pure JSON only, with no explanations, no markdown, and no comments.
- The JSON schema is:

{
  "best_target_path": "string",
  "confidence": 0.0,
  "reason": "string",
  "alternatives": [
    {
      "target_path": "string",
      "confidence": 0.0,
      "reason": "string"
    }
  ]
}

Notes:
- "best_target_path" must be exactly one of the candidate category paths.
- Each "target_path" in "alternatives" must also be exactly one of the candidate paths.
- You MUST NOT return any path that is not in the candidate list.
- The "reason" fields can be in Japanese or English.
- If you cannot find any reasonable category, you may return an empty alternatives list and set confidence to a low value.
```

#### 5.4.4 Category LLM User Prompt（模板，完整）

在调用时，服务端需将商品信息和候选路径插入下面模板：

```text
Product information:

- Title: {title}
- Description: {description}
- Brand (may be empty): {brand}
- Top-level category (group_name): {group_name}

Here is the list of candidate Mercari category paths under this top-level category.
Each line is one candidate path:

{candidate_paths_block}

Please choose:
- 1 best matching category path ("best_target_path"),
- and up to 2 alternative category paths ("alternatives"),
following the required JSON schema.

Important:
- Only use category paths from the candidate list.
- Do NOT invent new or modified category paths.
```

其中：

* `{title}`：使用 Vision LLM 输出并清洗后的标题。
* `{description}`：使用 Vision LLM 输出并清洗后的描述。
* `{brand}`：可以使用 LLM 原始 `brand_name` 或品牌匹配后最终的 `brand_name`。
* `{group_name}`：映射后的一级分类名称。
* `{candidate_paths_block}`：形如：

  ```text
  CD・DVD・ブルーレイ > CD > K-POP・アジア
  CD・DVD・ブルーレイ > CD > その他
  CD・DVD・ブルーレイ > CD > アニメ
  DIY・工具 > ドライバー・レンチ > スパナ・レンチ > T形レンチ
  DIY・工具 > ドライバー・レンチ > ソケット > その他
  ```

  实际内容根据 `group_name` 获取的条目列表生成，仅包含该一级分类下的所有 `category_name`。

#### 5.4.5 Category LLM 输出与验证

Category LLM 预期输出示例：

```json
{
  "best_target_path": "CD・DVD・ブルーレイ > CD > アニメ",
  "confidence": 0.93,
  "reason": "The product is clearly an anime CD.",
  "alternatives": [
    {
      "target_path": "CD・DVD・ブルーレイ > CD > その他",
      "confidence": 0.80,
      "reason": "Generic CD category could also apply."
    },
    {
      "target_path": "CD・DVD・ブルーレイ > CD > K-POP・アジア",
      "confidence": 0.50,
      "reason": "Less likely, only if the item is Asian pop rather than anime."
    }
  ]
}
```

服务端处理步骤：

1. 解析 JSON。

2. 构造一个有序路径列表：

   * 第一个为 `best_target_path`。
   * 后续依次为所有 `alternatives[*].target_path`。

3. 对于每一个路径：

   * 去除首尾空格。
   * 检查该路径是否在当前 `group_name` 下的候选列表中：

     * 使用 `(group_name, category_name)` 在类目数据模块中查找 `category_id`。
     * 如果查不到，表示该路径不来自原数据集，应丢弃。

4. 按顺序保留最多 3 个有效路径，形成最终的类目结果数组：

   * 每个元素为：

     * `id`：查到的 `category_id`。
     * `name`：该路径对应的 `category_name`。

5. 如果最终没有任何路径通过验证，则 `categories` 字段返回空数组。

---

### 5.5 Step 5：组装最终响应

综合所有步骤：

1. `title`：来自 Vision LLM，清洗后。
2. `description`：来自 Vision LLM，清洗后。
3. `prices`：来自 Vision LLM，经过整数化、过滤和排序后，长度固定为 3。
4. `categories`：来自 Category LLM 输出路径，经过验证并映射回 `category_id` 后：

   * 结构为数组，元素形如 `{ "id": "003", "name": "CD・DVD・ブルーレイ > CD > アニメ" }`。
   * 元素顺序即 LLM 认为的优先级顺序。
5. `brand_name`：品牌匹配成功则使用品牌数据集中的名称，否则为空字符串。

如启用 `debug`，附带 `_debug` 字段：

* `_debug.ai_raw`：Vision LLM 原始 JSON 输出。
* `_debug.group_name`：最终使用的一级分类名称。
* `_debug.llm_category_raw`：Category LLM 原始 JSON 输出。

---

## 6. 错误处理与异常情况

### 6.1 LLM 调用相关

* 网络异常/超时：

  * 记录日志（包括请求参数的摘要）。
  * 对外返回 502 或 504，根据实际情况选择。
* JSON 解析失败：

  * 可尝试简单清洗（去除多余前后文本、去除代码块标记等）。
  * 若仍失败，记录原始内容到日志。
  * 对外返回 500，并给出通用错误信息。

### 6.2 数据匹配相关

* 品牌匹配失败：`brand_name` 返回空字符串。
* 一级分类映射失败：可以返回空的 `categories` 数组。
* Category LLM 返回的路径全部无法在数据集中找到对应 `category_id`：

  * 返回空的 `categories` 数组。
  * 建议记录日志用于后续优化 Prompt 或数据清洗。

---

## 7. 配置项与部署建议

### 7.1 配置项（环境变量）

建议使用以下环境变量统一配置：

* `OPENROUTER_API_KEY`：OpenRouter 的访问令牌。
* `VISION_MODEL`：用于图片识别的模型名称。
* `CATEGORY_MODEL`：用于类目选择的文本模型名称。
* `BRAND_CSV_PATH`：品牌数据 CSV 路径。
* `CATEGORY_CSV_PATH`：类目数据 CSV 路径。
* `REQUEST_TIMEOUT`：调用 OpenRouter 的超时时间。
* `ENABLE_DEBUG`：是否允许客户端通过参数开启 debug。

### 7.2 部署建议

* 服务本身可以作为无状态服务部署，多实例水平扩展。
* 数据集（品牌 CSV、类目 CSV）在启动时加载进内存即可。
* 可在前面加 API Gateway 或负载均衡。
* 建议增加基础监控：

  * 请求成功率、平均延迟。
  * LLM 调用错误率。
  * 类目匹配失败次数，品牌匹配失败比例等。

## 8. 数据集存储位置
1. 品牌数据: data/brand.csv
2. 类别数据: data/category.csv