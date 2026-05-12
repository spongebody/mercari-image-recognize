# Mercari Image Analyzer

Mercari Image Analyzer 是一个基于 FastAPI + OpenRouter 的图片识别、商品信息生成、类目匹配和商品图生成服务。后端入口是 `main.py`，核心业务编排在 `app/service.py`。

## 启动方式

### 1. 安装依赖

任选一种方式。

使用 `uv`：

```bash
uv sync
```

使用 Python venv + pip：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell 激活虚拟环境时使用：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，至少填写：

```dotenv
OPENROUTER_API_KEY=...
VISION_MODEL=openai/gpt-4o-mini
CATEGORY_MODEL=openai/gpt-4o-mini
```

`.env` 会在 `app/config.py` 中通过 `python-dotenv` 自动加载。

### 3. 启动 API

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

如果使用 `uv`：

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. 同时启动 API 和本地前端

`run.sh` 会同时启动：

- API: `http://localhost:8000`
- UI: `http://localhost:8002`

```bash
./run.sh
```

可通过环境变量覆盖端口：

```bash
API_PORT=8010 UI_PORT=8012 ./run.sh
```

前端静态页面也可以单独打开 `web/index.html`，或用任意静态文件服务器托管 `web/`。

### 5. 配置页和健康检查

- 配置页：`GET /config`
- 读取运行时配置：`GET /api/v1/config`
- 更新运行时配置：`PUT /api/v1/config`
- 健康检查：`GET /health`

配置页保存的内容会写回 `.env`，并尽量同步到当前进程内的客户端实例。当前代码按单进程使用设计；多 worker 部署时，修改配置后需要重启或重载所有 worker。

## API 服务链路

### POST `/api/v1/mercari/image/analyze`

这是主图片识别接口，接收 `multipart/form-data`：

- `image_list`: 1 张或多张图片，字段名固定为 `image_list`
- `language`: `ja` / `en` / `zh`，默认 `ja`
- `debug`: `true` 时返回调试信息，前提是 `ENABLE_DEBUG=true`
- `vision_model`: 可选，覆盖本次快速识别模型
- `category_model`: 可选，覆盖本次类目选择模型

完整链路：

1. `main.py` 校验上传文件：文件存在、MIME 类型在 `app/constants.py` 的 `ALLOWED_MIME_TYPES` 中、大小不超过 `MAX_IMAGE_BYTES`。
2. `app/image_processing.py::compress_image_if_needed` 根据 `IMAGE_COMPRESSION_THRESHOLD_MB` 对大图压缩，避免直接把过大的原图发给模型。
3. `main.py` 创建 `job_id`，把商品信息生成任务提交到 `ThreadPoolExecutor`：
   - 主任务调用 `MercariAnalyzer.generate_product_data`
   - 如果 `PRODUCT_DATA_FALLBACK_MODEL` 非空，同时提交 fallback 商品信息任务
4. 同一请求内立即执行快速分类链路 `MercariAnalyzer.classify_first_image_categories`：
   - 只使用第一张图
   - 使用 `FAST_CLASSIFICATION_SYSTEM_PROMPT` + `FAST_CLASSIFICATION_USER_PROMPT`
   - 通过 `VISION_MODEL` 或请求里的 `vision_model` 调 OpenRouter
   - 得到 `title`、`simple_description`、`top_level_category`
5. `app/service.py` 将模型返回的顶级类目映射到 `TOP_LEVEL_CATEGORIES`，再用 `CategoryStore.get_categories_by_group` 从分类 CSV 取候选路径。
6. 类目选择链路调用 OpenRouter：
   - prompt 来自 `CATEGORY_SYSTEM_PROMPT` + `CATEGORY_USER_PROMPT_TEMPLATE`
   - 模型来自 `CATEGORY_MODEL` 或请求里的 `category_model`
   - 从候选路径中选出最多 3 个分类
7. `main.py` 将分类结果和商品信息 future 存入 `AnalysisJobStore`。
8. 如果商品信息已经可用，直接返回 `status=completed`；否则返回 `status=product_pending` 和 `job_id`。
9. 客户端用 `GET /api/v1/mercari/image/analyze/{job_id}` 轮询商品信息结果。

商品信息生成链路：

1. `MercariAnalyzer.generate_product_data` 使用全部图片。
2. 主模型使用 `PRODUCT_DATA_SYSTEM_PROMPT` + `PRODUCT_DATA_USER_PROMPT`。
3. fallback 模型使用 `PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT` + `PRODUCT_DATA_FALLBACK_USER_PROMPT`。
4. OpenRouter 返回 JSON 后，`app/llm/json_parser.py` 解析，`app/service.py` 规范化标题、描述、品牌等字段。
5. 品牌识别结果会通过 `BrandStore.match` 在品牌 CSV 中匹配，输出 `brand_name` 和 `brand_id_obj`。
6. 轮询时，`main.py::_resolve_product_source` 根据 `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS` 决定用主模型结果还是 fallback 结果，并返回 `product_data_source=primary|fallback`。

请求 OpenRouter 时统一经过：

- `app/llm/client.py::OpenRouterClient`: 构造请求、Headers、timeout、reasoning 参数。
- `app/llm/resilient.py::ResilientCaller`: 负责主模型重试、fallback 模型链、总耗时预算、JSON 解析失败重试。
- `app/llm/json_parser.py`: 从模型文本中提取 JSON。

### GET `/api/v1/mercari/image/analyze/{job_id}`

轮询主识别接口生成的后台商品信息任务。

- `product_pending`: 商品信息任务仍未选出可用结果。
- `completed`: 分类结果和商品信息都已合并。
- `404`: 当前进程内没有这个 job。job 只保存在内存中，默认 TTL 为 `AnalysisJobStore(ttl_seconds=1800)`。

### POST `/api/v1/mercari/product-data/regenerate`

商品数据重新生成接口，接收 `multipart/form-data`：

- `image_list`: 1 张或多张商品图片，字段名固定为 `image_list`，上传校验和压缩逻辑与 `/api/v1/mercari/image/analyze` 相同。
- `language`: `ja` / `en` / `zh`，默认 `ja`。
- `original_product_data`: 可选，原始商品数据 JSON 字符串。
- `user_notes`: 可选，用户补充信息，例如成色、关键词、同款判断、材质描述。
- `debug`: 可选，`true` 时返回调试信息，前提是 `ENABLE_DEBUG=true`。

完整链路：

1. `main.py` 使用与图片识别接口相同的上传校验和 `compress_image_if_needed` 压缩逻辑。
2. `main.py` 解析 `original_product_data`，要求它是 JSON object；非法 JSON 返回 `400`。
3. `MercariAnalyzer.regenerate_product_data` 使用全部图片。
4. prompt 来自 `PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT` + `PRODUCT_DATA_REGENERATION_USER_PROMPT`。
5. 生成优先级为：用户补充信息 > 原始商品数据 > 图片深度分析。
6. OpenRouter 返回 JSON 后，`app/llm/json_parser.py` 解析，`app/service.py` 规范化标题、描述、品牌等字段，并复用标题至少 80 字符的兜底逻辑。
7. 品牌识别结果会通过 `BrandStore.match` 在品牌 CSV 中匹配，输出 `brand_name` 和 `brand_id_obj`。
8. 接口同步返回重新生成的商品数据，不重新分类，也不返回价格字段。

### POST `/api/v1/mercari/title/analyze`

标题分类接口，接收 JSON：

```json
{
  "title": "商品标题",
  "image_url": "https://example.com/fallback.jpg",
  "language": "ja"
}
```

完整链路：

1. `main.py` 校验语言和标题。
2. `MercariAnalyzer.analyze_title` 调用 `PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT` + `PRODUCT_TITLE_CATEGORY_USER_PROMPT`，先只根据标题判断顶级类目。
3. 如果标题能得到有效顶级类目，则复用 `_choose_categories` 从分类 CSV 候选中选出目标路径。
4. 如果标题分类失败且提供了 `image_url`，`app/utils.py::fetch_image_from_url` 下载图片，再走旧版图片识别链路 `_classify_image_to_paths`。
5. fallback 图片识别使用 `VISION_SYSTEM_PROMPT_WITH_PRICE` + `VISION_USER_PROMPT_WITH_PRICE`，再进入类目选择链路。

### POST `/api/v1/showcase/generate`

商品图生成接口，接收单张图片和可选提示词：

- `file`: 图片文件
- `prompt_hint`: 可选，追加到默认商品图 prompt
- `model`: 可选，覆盖本次图生图模型

完整链路：

1. `main.py` 校验上传图片。
2. `app/showcase/service.py::ShowcaseService.generate_showcase` 生成 `request_id`，根据配置决定是否保存输入图。
3. `app/showcase/prompt.py::build_showcase_prompt` 构造商品图生成 prompt。
4. `app/showcase/openrouter_image_client.py` 调 OpenRouter 图生图接口，支持重试和 `SHOWCASE_FALLBACK_MODELS`。
5. 根据 `SHOWCASE_RETAIN_OUTPUT_FILES` 决定是否保存输出图到 `SHOWCASE_STORAGE_ROOT`。
6. `app/showcase/archive.py` 总是把请求记录写到 `logs/showcase/`。

## 运行时依赖文件

核心图片识别/分类接口运行时依赖这些文件：

- `main.py`: FastAPI app、路由、上传校验、后台任务、配置页。
- `app/config.py`: 环境变量读取和默认配置。
- `app/runtime_config.py`: `/api/v1/config` 可修改配置字段及写回 `.env`。
- `app/constants.py`: 支持语言、MIME 类型、顶级类目、默认 fallback 模型。
- `app/image_processing.py`: 上传图片压缩。
- `app/jobs.py`: 图片识别后台任务内存存储。
- `app/service.py`: 商品识别、标题分类、品牌匹配、类目选择的核心编排。
- `app/utils.py`: 文本规范化、图片转 data URL、远程图片下载、价格辅助函数。
- `app/errors.py`: 业务和 LLM 错误类型。
- `app/llm/prompts.py`: 所有识别、商品信息、类目选择 prompt。
- `app/llm/client.py`: OpenRouter Chat Completions 请求客户端。
- `app/llm/resilient.py`: 主模型重试、fallback 链路、耗时预算。
- `app/llm/json_parser.py`: LLM JSON 提取和解析。
- `app/data/brands.py`: 加载和匹配品牌 CSV。
- `app/data/categories.py`: 加载和查找分类 CSV。
- `data/mercari_brand.csv`: 默认品牌数据，来自 `BRAND_CSV_PATH`。
- `data/category_rakuten.csv`: 默认分类数据，来自 `CATEGORY_CSV_PATH`。

商品图生成接口额外依赖：

- `app/showcase/service.py`
- `app/showcase/openrouter_image_client.py`
- `app/showcase/prompt.py`
- `app/showcase/storage.py`
- `app/showcase/archive.py`
- `storage/`: 保留输入/输出图片时使用。
- `logs/showcase/`: 写入每次生成请求的归档记录。

前端和配置页依赖：

- `web/index.html`: 本地测试 UI。
- `web/config.html`: `/config` 配置页面。

## data 目录说明

当前运行时只直接读取：

- `data/mercari_brand.csv`
- `data/category_rakuten.csv`

`data/` 根目录只保留以上两个运行时文件。其他数据源、测试素材、旧文件和备份文件统一放在 `data/others/`，相关脚本的默认路径也指向 `data/others/`：

- `data/others/rdx_category.csv`: `scripts/update_meru_id.py`、`scripts/build_rakuten_category_csv.py`、`scripts/extract_top_categories.py` 的输入或输出目标。
- `data/others/rakuten_to_mercari.csv`: `scripts/update_meru_id.py` 用来回填 `meru_id`。
- `data/others/Rakuten_ZenPlus_Catetory_Mapping.csv`: `scripts/update_meru_id.py` 用来回填 `zenplus_id`。
- `data/others/rdx_brand.csv`: `scripts/build_mercari_brand_csv.py` 用来回填跨平台品牌 ID。
- `data/others/title_test_cases.csv`: `scripts/run_title_tests.py` 和 `scripts/perf_test.py` 的标题用例。
- `data/others/lv.jpg`、`data/others/test.png`: `scripts/perf_test.py` 的默认测试图片。

服务运行时不会读取 `data/others/`；它只影响维护脚本、压测脚本或人工追溯旧数据。

## 配置变量

布尔值接受 `1`、`true`、`yes`、`on` 表示开启；`0`、`false`、`no`、`off` 表示关闭。列表类变量使用英文逗号分隔。

### OpenRouter 和模型

- `OPENROUTER_API_KEY`: OpenRouter API Key；识别和生成接口都需要。
- `OPENROUTER_BASE_URL`: OpenRouter Chat Completions 地址，默认 `https://openrouter.ai/api/v1/chat/completions`。
- `OPENROUTER_REFERER`: 可选，写入 `HTTP-Referer` 请求头。
- `OPENROUTER_APP_NAME`: 可选，写入 `X-Title` 请求头，默认 `mercari-image-backend`。
- `VISION_MODEL`: 图片快速识别和旧版图片 fallback 识别的主模型。
- `CATEGORY_MODEL`: 类目选择和标题顶级类目判断的主模型。
- `PRODUCT_DATA_MODEL`: 多图商品信息生成的主模型，默认 `google/gemini-2.5-flash`。
- `PRODUCT_DATA_FALLBACK_MODEL`: 与主商品信息任务并行运行的保底模型；留空可关闭并行保底。
- `PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS`: 主商品信息任务超过该秒数仍未返回时，轮询接口优先采用 fallback 结果，默认 `10`。
- `VISION_FALLBACK_MODELS`: `VISION_MODEL` 重试失败后按顺序尝试的模型链。
- `CATEGORY_FALLBACK_MODELS`: `CATEGORY_MODEL` 重试失败后按顺序尝试的模型链。
- `PRODUCT_DATA_FALLBACK_MODELS`: 商品信息主模型或显式 fallback 模型请求失败后继续尝试的模型链。
- `MODEL_CALL_MAX_RETRIES`: 主模型最大重试次数；总尝试次数为 `MODEL_CALL_MAX_RETRIES + 1`。
- `MODEL_CALL_TOTAL_BUDGET_SECONDS`: 单个 LLM 阶段跨重试和 fallback 的总耗时预算。
- `REQUEST_TIMEOUT`: 单次 OpenRouter 请求超时时间，单位秒。

### 数据文件

- `BRAND_CSV_PATH`: 品牌 CSV 路径，默认 `data/mercari_brand.csv`。
- `CATEGORY_CSV_PATH`: 分类 CSV 路径，默认 `data/category_rakuten.csv`。

`BRAND_CSV_PATH` 需要包含 `id,name,name_jp,name_en,rakuten_id,yshop_id,yauc_id,meru_id,ebay_id,rakuma_id,amazon_id,qoo10_id` 等字段。`CATEGORY_CSV_PATH` 需要包含 `category_id,path|category_name,group_name,meru_id,rakuma_id,zenplus_id` 等字段。

### 上传、调试和日志

- `MAX_IMAGE_BYTES`: 单张上传图片最大字节数，默认 `5242880`。
- `IMAGE_COMPRESSION_THRESHOLD_MB`: 单张图片超过该 MB 数后后端先压缩再发给视觉模型；设置为 `0` 可关闭压缩。
- `ENABLE_DEBUG`: 是否允许请求通过 `debug=true` 返回 `_debug` 字段。
- `LOG_LLM_RAW`: 是否把 LLM 原始请求结果和解析结果写入 `logs/`。
- `LOG_REQUESTS`: 是否记录 HTTP 请求日志。
- `LOG_REQUESTS_RETENTION_DAYS`: 请求日志保留天数。
- `LOG_REQUESTS_MAX_FILES`: 请求日志最多保留文件数。

### Reasoning 参数

这些变量会原样组装为 OpenRouter 的 `reasoning` 参数；留空则不发送对应字段。

- `REASONING_ENABLED`: 是否启用 reasoning。
- `REASONING_EFFORT`: reasoning 强度，可选 `minimal`、`low`、`medium`、`high`、`xhigh`、`none`。
- `REASONING_MAX_TOKENS`: reasoning token 预算。
- `REASONING_SUMMARY`: reasoning 摘要级别，可选 `auto`、`concise`、`detailed`。

### Showcase 商品图生成

- `SHOWCASE_MODEL`: `/api/v1/showcase/generate` 默认图生图模型。
- `SHOWCASE_STORAGE_ROOT`: 输入/输出图片保留目录，默认 `storage`。
- `SHOWCASE_RETAIN_INPUT_FILES`: 是否保留上传的输入图片。
- `SHOWCASE_RETAIN_OUTPUT_FILES`: 是否保留生成的输出图片。
- `SHOWCASE_REQUEST_TIMEOUT`: showcase 单次 OpenRouter 请求超时时间。
- `SHOWCASE_MAX_RETRIES`: 每个 showcase 模型的最大尝试次数，最小为 `1`。
- `SHOWCASE_FALLBACK_MODELS`: showcase 主模型耗尽重试后按顺序尝试的 fallback 模型。
- `SHOWCASE_TIMEZONE`: showcase `request_id`、归档目录和时间戳使用的时区，默认 `Asia/Shanghai`。

### run.sh 专用变量

- `API_HOST`: API 监听地址，默认 `0.0.0.0`。
- `API_PORT`: API 端口，默认 `8000`。
- `UI_HOST`: 本地 UI 静态服务器监听地址，默认 `0.0.0.0`。
- `UI_PORT`: 本地 UI 静态服务器端口，默认 `8002`。

### 已废弃变量

`.env.example` 中历史遗留的 `CATEGORY_LLM_RETRY_ENABLED` 和 `CATEGORY_LLM_MAX_RETRIES` 当前代码不再读取；重试策略由 `MODEL_CALL_MAX_RETRIES`、`MODEL_CALL_TOTAL_BUDGET_SECONDS` 和各类 fallback 模型链控制。
