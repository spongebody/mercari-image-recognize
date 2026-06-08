# 图片识别测试评估 SOP 系统设计

**Date:** 2026-06-05
**Status:** Draft design, ready for implementation plan
**Scope (MVP):** 在现有 `/config` Shell 配置页内新增“测试评估”工作台，把测试数据上传、模型参数选择、后台测试、客服校验、人工分析和归档串成一个轻量 SOP 闭环。

---

## 1. 背景

当前项目已经具备三部分基础能力：

1. 主图片识别链路：`MercariAnalyzer` 支持快速分类、类目选择、商品数据生成和品牌库匹配。
2. 运行时配置页：`web/config.html` 已升级为 Shell 页面，左侧导航内有 `API 配置` 和 `提示词配置` 两个 route。
3. 离线评估脚本：`scripts/run_image_model_tests.py` 已能读取 TSV 测试集，按模型组合跑完整链路，并输出 `results.csv` 和 `summary.json`。

现在需要把临时 CLI 测试沉淀为配置页内可重复执行的 SOP 系统。MVP 不追求完整实验平台，只解决“怎么上传、怎么跑、怎么校验、怎么归档、怎么指导下一轮优化”。

---

## 2. 目标

MVP 目标：

1. 在 `/config` 页面新增第三个二级 route：`测试评估`。
2. 支持上传测试集文件，字段至少包含 `itemName`、`genreId`、`image`、`brand`。
3. 支持输入本轮模型配置：
   - `visionModel`
   - `categoryModel`
   - `productDataModel`
   - `reasoningEffort`
   - `limit`
4. 后端创建 evaluation run，并在后台执行测试。
5. 页面能实时查看运行进度、成功数、失败数、已用时间和 ETA。
6. 测试完成后展示/下载结果表。
7. 客服可以填写分类校验、品牌校验和备注。
8. 系统能重新计算“严格准确率”和“客服确认后准确率”。
9. 人工填写分析结论和优化动作。
10. 支持归档 run，归档后锁定校验和分析内容，防止误改。

---

## 3. 非目标

MVP 暂不做：

1. 数据库和复杂权限。
2. 多人协作审批流。
3. 复杂图表看板。
4. AI 自动分析优化建议。
5. 多 run 并发调度队列。
6. 在线编辑超大表格的高级功能。
7. 对历史 run 做版本 diff 和回滚。

这些能力可以在 SOP 稳定后逐步增加。

---

## 4. 当前页面集成方式

现有 `web/config.html` 使用：

- `Shell.mount({ page: "config", defaultRoute: "api", ... })`
- 当前 route 映射：
  - `api` -> `tab-api`
  - `prompts` -> `tab-prompts`
- Shell 侧边栏由 `sidebar: () => [...]` 返回。

MVP 将增加：

- `tab-evaluations`
- route id：`evaluations`
- sidebar label：`测试评估`
- header title：`测试评估`

不新增独立 HTML 页面，避免分散配置/提示词/测试能力。

---

## 5. 目录与归档结构

所有 run 继续写入文件系统：

```text
logs/image_model_tests/
  2026-06-05-14-30/
    input.csv
    run_config.json
    status.json
    results.csv
    summary.json
    customer_review.csv
    analysis.md
    errors.jsonl
```

目录名使用北京时间 `Asia/Shanghai`，格式 `yyyy-mm-dd-hh-mm`。如果同一分钟内重复创建 run，追加短后缀：

```text
2026-06-05-14-30
2026-06-05-14-30-2
2026-06-05-14-30-3
```

### `run_config.json`

```json
{
  "runId": "2026-06-05-14-30",
  "visionModel": "google/gemini-2.5-flash",
  "categoryModel": "google/gemini-2.5-flash",
  "productDataModel": "google/gemini-2.5-flash",
  "reasoningEffort": "none",
  "language": "ja",
  "limit": 0,
  "createdAt": "2026-06-05T14:30:00+08:00",
  "archived": false
}
```

### `status.json`

```json
{
  "runId": "2026-06-05-14-30",
  "status": "running",
  "total": 200,
  "completed": 37,
  "success": 37,
  "failed": 0,
  "startedAt": "2026-06-05T14:30:02+08:00",
  "updatedAt": "2026-06-05T14:35:12+08:00",
  "elapsedSeconds": 310.4,
  "etaSeconds": 1365.2,
  "message": "37/200 completed"
}
```

状态枚举：

```text
pending
running
completed
failed
archived
```

`archived` 是最终锁定状态。归档后的 `run_config.json` 同步写 `archived: true`。

### `results.csv`

沿用现有评估脚本字段，并新增客服备注：

```text
itemName
genreId
image
brand
visionModel
categoryModel
productDataModel
reasoningEffort
aiCategory
aiCategoryPath
aiCategoryConfidence
aiBrand
aiTitle
categoryDurationS
productDataDurationS
totalDurationS
customerCategoryCheck
customerBrandCheck
customerNotes
```

客服字段允许值：

```text
空
OK
ACCEPTABLE
NG
```

含义：

- `OK`: AI 预测正确。
- `ACCEPTABLE`: 不完全匹配，但业务上可接受。
- `NG`: 错误。
- 空: 尚未校验。

### `summary.json`

保留现有严格统计，并增加客服确认后统计：

```json
{
  "overall": {
    "total": 200,
    "categoryCorrect": 68,
    "brandCorrect": 91,
    "categoryAccuracy": 0.34,
    "brandAccuracy": 0.455,
    "categoryReviewedCorrect": 110,
    "brandReviewedCorrect": 130,
    "categoryReviewedAccuracy": 0.55,
    "brandReviewedAccuracy": 0.65,
    "categoryPendingReview": 90,
    "brandPendingReview": 70
  }
}
```

客服确认后准确率规则：

- 严格匹配正确的行视为正确。
- `customerCategoryCheck` 为 `OK` 或 `ACCEPTABLE` 的行视为分类正确。
- `customerBrandCheck` 为 `OK` 或 `ACCEPTABLE` 的行视为品牌正确。
- `NG` 和空值不算正确。

### `analysis.md`

人工分析和优化动作：

```markdown
# Evaluation Analysis

## 可优化点

- ...

## 优化动作

- ...

## 下一轮测试建议

- ...
```

---

## 6. 后端模块设计

新增模块：

```text
app/evaluation/runs.py
```

职责：

1. 创建 run 目录。
2. 保存上传的 input 文件。
3. 写入 `run_config.json`。
4. 写入和读取 `status.json`。
5. 执行单个 run。
6. 读取 results/summary。
7. 保存客服校验字段。
8. 保存人工分析。
9. 归档 run。

现有 `app/evaluation/image_model_evaluation.py` 继续承担纯数据 helper：

- 加载测试用例。
- 构建模型组合。
- 构建结果行。
- 计算 summary。
- 写 `results.csv` / `summary.json`。

`runs.py` 不复制识别逻辑，仍复用：

- `MercariAnalyzer`
- `OpenRouterClient`
- `BrandStore`
- `CategoryStore`
- `fetch_image_from_url`
- `compress_image_if_needed`
- `build_result_row`
- `summarize_rows`

---

## 7. 后端 API 设计

新增接口，均挂在 `/api/v1/evaluations`。

### 创建并启动测试

```text
POST /api/v1/evaluations
Content-Type: multipart/form-data
```

字段：

```text
file
visionModel
categoryModel
productDataModel
reasoningEffort
language
limit
```

响应：

```json
{
  "runId": "2026-06-05-14-30",
  "status": "pending"
}
```

### 列出历史 run

```text
GET /api/v1/evaluations
```

响应：

```json
{
  "runs": [
    {
      "runId": "2026-06-05-14-30",
      "status": "completed",
      "createdAt": "2026-06-05T14:30:00+08:00",
      "visionModel": "google/gemini-2.5-flash",
      "categoryModel": "google/gemini-2.5-flash",
      "productDataModel": "google/gemini-2.5-flash",
      "reasoningEffort": "none",
      "total": 200,
      "completed": 200
    }
  ]
}
```

### 查看 run 状态和 summary

```text
GET /api/v1/evaluations/{run_id}
```

响应包含：

```json
{
  "run": {},
  "status": {},
  "summary": {}
}
```

`summary` 在测试未完成时可以为空对象。

### 读取结果

```text
GET /api/v1/evaluations/{run_id}/results
```

响应：

```json
{
  "rows": []
}
```

为避免 MVP 里做复杂分页，200 条内直接返回全部行。后续如数据量变大，再加 `offset/limit`。

### 下载结果 CSV

```text
GET /api/v1/evaluations/{run_id}/results.csv
```

返回 `FileResponse`。

### 保存客服校验

```text
PUT /api/v1/evaluations/{run_id}/review
Content-Type: application/json
```

请求：

```json
{
  "rows": [
    {
      "rowIndex": 0,
      "customerCategoryCheck": "OK",
      "customerBrandCheck": "ACCEPTABLE",
      "customerNotes": "临近类目，业务可接受"
    }
  ]
}
```

保存后：

1. 更新 `results.csv` 中对应行。
2. 重新计算并写入 `summary.json`。
3. 写 `customer_review.csv`，便于审计。

### 保存分析

```text
PUT /api/v1/evaluations/{run_id}/analysis
Content-Type: application/json
```

请求：

```json
{
  "analysisNotes": "...",
  "optimizationActions": "...",
  "nextRunSuggestion": "..."
}
```

后端写 `analysis.md`。

### 归档

```text
POST /api/v1/evaluations/{run_id}/archive
```

归档后：

- `status.json.status = archived`
- `run_config.json.archived = true`
- review/analysis API 返回 400，提示 run 已归档。

---

## 8. 前端 MVP 设计

在 `web/config.html` 新增 `tab-evaluations`，包含 4 个区块。

### 8.1 创建测试

控件：

```text
测试集文件
visionModel
categoryModel
productDataModel
reasoningEffort
language
limit
开始测试按钮
```

默认值可从 `/api/v1/config` 读取：

- `VISION_MODEL`
- `CATEGORY_MODEL`
- `PRODUCT_DATA_MODEL`
- `CLASSIFICATION_REASONING_ENABLED` 不直接映射；MVP 用 `reasoningEffort` 控制测试请求。

### 8.2 历史与进度

展示最近 run 列表，点击某个 run 后显示：

```text
状态
completed / total
success / failed
elapsed
eta
结果文件链接
```

running 状态下每 2 秒轮询一次。

### 8.3 结果与客服校验

表格列：

```text
itemName
genreId
brand
aiCategory
aiCategoryPath
aiCategoryConfidence
aiBrand
aiTitle
customerCategoryCheck
customerBrandCheck
customerNotes
```

MVP 筛选：

- 全部
- 待分类校验
- 待品牌校验
- 已校验

保存按钮一次保存当前页面所有编辑。

### 8.4 分析与归档

展示 summary 指标：

```text
分类严格准确率
品牌严格准确率
分类客服确认后准确率
品牌客服确认后准确率
```

人工输入：

```text
可优化点
优化动作
下一轮测试建议
```

按钮：

```text
保存分析
归档本次测试
```

---

## 9. 错误处理

1. 上传文件缺字段：创建 run 失败，返回 400。
2. OpenRouter 未配置 API key：创建 run 失败，返回 400。
3. 图片下载失败：该行写空 AI 字段，错误写 `errors.jsonl`，run 继续。
4. 单条模型失败：该行写空 AI 字段，错误写 `errors.jsonl`，run 继续。
5. 后台任务异常：`status.json.status = failed`，`message` 写异常摘要。
6. 归档后保存 review/analysis：返回 400。

---

## 10. 测试策略

### Unit

1. run id 生成使用北京时间。
2. 创建 run 会写入 `input.csv`、`run_config.json`、`status.json`。
3. review 保存会更新 `results.csv` 并重算 summary。
4. 归档后不允许保存 review。
5. summary 能计算客服确认后准确率。

### API

1. `POST /api/v1/evaluations` happy path 使用 dry-run 或 fake analyzer。
2. 上传缺字段文件返回 400。
3. `GET /api/v1/evaluations` 返回 run 列表。
4. `GET /api/v1/evaluations/{run_id}/results` 返回 rows。
5. `PUT /review` 更新校验字段。
6. `POST /archive` 锁定 run。

### Frontend smoke

1. `/config#evaluations` 能打开测试评估 tab。
2. 创建测试表单字段存在。
3. 历史 run 列表能渲染。
4. 结果表能显示客服字段。

---

## 11. 实施顺序

1. 后端 run store 和 summary/review helper。
2. 后端 API。
3. 配置页新增 `测试评估` route 和静态布局。
4. 前端接入创建 run、轮询状态、显示结果。
5. 前端接入客服校验、分析保存和归档。
6. 手动用 `--limit 1` 或小文件做端到端验证。

---

## 12. 开放点

MVP 默认一次只跑一个后台 evaluation run。后续如果客服/运营需要并行对比多个模型，可以在 run 创建接口上加并发限制和队列状态。
