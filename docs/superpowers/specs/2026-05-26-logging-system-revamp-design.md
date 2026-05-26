# 日志系统改造设计

**日期**：2026-05-26
**作者**：youbo（设计），Claude（结对）
**状态**：草案，待 review

## 1. 背景与目标

当前 mercari-image-recognize 服务有两套互不关联的日志机制：

- **HTTP 请求日志**（`app/request_logging.py` + `main.py:444-480`）：每请求一个 JSON 写到 `logs/requests/`，已实现 7 天 / 1000 文件清理。
- **LLM 阶段日志**（`app/service.py::_log_raw` 第 1039 行）：每次 LLM 调用拆成 3 个文件写到 `logs/`，stage 包括 `fast_vision / category / title_category / product_data / product_data_fallback / product_data_regeneration / title_image_fallback`。**无清理机制**，目前已堆积 350+ 文件。

Showcase 服务（`app/showcase/service.py`）是独立第三套：自己的 `OpenRouterImageClient`、自己的 `request_id` 格式、自己的归档（`archive_writer`），日志只走 stdlib `logger` 到 console，不落盘。

### 1.1 用户痛点

1. 无法通过 Web 页面查看日志，每次排查必须 SSH 进服务器。
2. 日志信息不完整：HTTP 请求与背后的 LLM 调用之间**没有关联键**，只能靠时间戳猜测。Token / cost 信息散落在 raw response 文件里没有聚合。Showcase 完全不落盘。
3. 失败 / 异常时无法通过"请求时间 + 请求内容"快速定位日志。

### 1.2 目标

- 通过 Web 界面查看所有日志，默认保存一周，可配置。
- 完整保存：HTTP 请求 / 响应 / 上传原图 / LLM prompt / LLM raw response / token / cost。
- 通过时间、状态、文本内容快速定位单次请求 → 关联的所有 LLM 调用。

### 1.3 非目标

- 不做运营级的 token / cost 大盘（前期只在请求维度聚合，UI 顶部一条小统计即可）。
- 不引入外部日志服务（Loki / ELK）。
- 不做分布式追踪（单服务足够）。
- 不为多 worker / 多机器部署优化（单进程 SQLite 足够）。

## 2. 关键决策（已与用户确认）

| 决策 | 选择 |
|---|---|
| 存储后端 | SQLite（元数据/索引） + 文件系统（prompt 全文 / raw response / 原图） |
| 图片保留 | 存原图，按 request_id 归档 |
| 访问控制 | 简单密码（Basic Auth），同时覆盖 `/config` 页 |
| 保留策略 | 天数（默认 7）+ 总容量上限（默认 5 GiB）双底线 |
| UI 主线 | 时间线浏览 + 状态筛选（失败/慢） + 文本搜索 |
| 异步请求呈现 | 按 `job_id` 聚合 POST 与后续 poll GET |
| Showcase | 接入新系统，复用同一 `request_id` |
| 索引 schema | 双表（`requests` + `llm_calls`） |
| 旧文件 | 单次切换，直接删除旧日志 |
| UI 详情布局 | 列表 + 内联展开 |
| 文本搜索默认范围 | `error + body_summary`，可勾选包含 `prompt / response` |
| 日志失败可见性 | stderr + dead letter 文件（不抛异常给业务） |
| 清理任务 | 在主进程 FastAPI startup 注册定时器（默认 1 小时跑一次） |

## 3. 模块结构与数据流

### 3.1 新增模块

```
app/observability/
  __init__.py
  store.py         # SQLite 连接、schema、迁移、insert/query API
  context.py       # request_id contextvar 与 set/get/reset 辅助
  recorder.py      # 高层 API：start_request / finalize_request / record_llm_stage / record_showcase
  retention.py     # 后台清理：天数 + 总容量双底线
  auth.py          # Basic Auth dependency（用于 /logs、/config、PUT /api/v1/config）
  api.py           # /api/v1/logs/* 查询与文件下载端点
  paths.py         # request_id → 文件目录约定
```

### 3.2 一次 `POST /analyze` 的完整数据流

```
HTTP middleware
  ├─ request_id = uuid4().hex
  ├─ ctx.set_request_id(request_id)
  ├─ body = await request.body()  # rebuild request with replayable body
  ├─ recorder.start_request(request_id, method, path, headers, body)
  │     ├─ INSERT INTO requests (request_id, ts, endpoint, method, client_ip, ...)
  │     ├─ 落盘 logs/store/<YYYY-MM-DD>/<request_id>/request.json
  │     └─ 提取 multipart 上传图片 → image_<idx>.<ext>
  ├─ response = await call_next(request)
  │     └─ 业务逻辑 (service.py) 调用 LLM：
  │         recorder.record_llm_stage(stage, attempts, raw_response, parsed)
  │           ├─ 对每个 attempt INSERT 一行 llm_calls
  │           ├─ 从 raw_response.usage 提取 token，从 raw_response.cost 提取 cost
  │           ├─ 落盘 llm_<stage>_<attempt>_prompt.json
  │           │       llm_<stage>_<attempt>_response.json
  │           │       llm_<stage>_<attempt>_parsed.json
  │           └─ INSERT INTO llm_fts (prompt_text, response_text)
  ├─ 响应头注入 X-Request-Id: <request_id>
  └─ finally:
        recorder.finalize_request(request_id, response_status, duration_ms, error, response_body)
          ├─ UPDATE requests SET status_code, duration_ms, error, error_kind,
          │                       total_tokens=SUM(llm_calls.total_tokens),
          │                       total_cost_usd=SUM(llm_calls.cost_usd),
          │                       llm_call_count=COUNT(llm_calls),
          │                       job_id=<from response body if /analyze>
          └─ 落盘 response.json
        ctx.reset_request_id(token)
```

### 3.3 异步 executor 透传

`product_data_executor.submit(...)`：

```python
# 提交侧
request_id = ctx.get_request_id()
future = product_data_executor.submit(_generate_product_data_worker, request_id, ...)

# worker 入口
def _generate_product_data_worker(request_id, ...):
    token = ctx.set_request_id(request_id)
    try:
        ...  # 所有 record_llm_stage 自动挂同一 request_id
    finally:
        ctx.reset_request_id(token)
```

### 3.4 Job 关联

- `POST /analyze` 返回 `job_id` → `finalize_request` 从响应体里提取并写入 `requests.job_id`。
- 后续 `GET /analyze/{job_id}` 自身也是新 request_id，`finalize_request` 用一个轻量的 endpoint→job_id 提取器（按 path 模式 `/api/v1/mercari/image/analyze/{job_id}` 匹配）从 URL path 里取 `job_id` 写入同名列。提取器是显式注册的 dict，不是黑魔法。
- UI 按 `job_id` group：点 POST 行可展开"同 job 兄弟请求"（poll GETs + 后台任务里的 LLM 调用）。

### 3.5 Showcase 接入

`app/showcase/service.py::generate_showcase`：

- **退役** 自生成的 `request_id`（`20260526_223344_<uuid8>` 格式），改为 `ctx.get_request_id()`（中间件已注入）。
- **新增** 一次 `recorder.record_llm_stage(stage="showcase_generate", ...)`，把 `final_prompt`、`result.response_body`、`result.attempt_records` 入库。在 try/except 两条分支里都调。
- **保留** `archive_writer.write_record(...)` — 这是 showcase 的业务归档（含 input/output 图路径），不是日志，日志系统不替代它。

### 3.6 模块边界与单元职责

| 模块 | 单一职责 | 依赖 |
|---|---|---|
| `store.py` | SQLite 连接管理、schema 初始化、纯数据 insert/query | stdlib |
| `context.py` | request_id contextvar 包装，可在线程边界传递 | stdlib |
| `recorder.py` | 业务接入点。组合 store + paths + context，吞所有异常 | store, context, paths |
| `paths.py` | request_id 到文件路径的映射（避免穿越） | stdlib |
| `retention.py` | 清理逻辑（天数 + 容量），可手动调用 | store, paths |
| `auth.py` | Basic Auth FastAPI dependency | fastapi |
| `api.py` | /api/v1/logs/* 路由 | store, auth, paths |

依赖方向是严格单向的；store / context / paths 不依赖业务模块，便于单测。

## 4. SQLite Schema

```sql
CREATE TABLE requests (
  request_id        TEXT PRIMARY KEY,
  timestamp_utc     TEXT NOT NULL,
  method            TEXT NOT NULL,
  endpoint          TEXT NOT NULL,
  status_code       INTEGER,
  duration_ms       REAL,
  error             TEXT,
  error_kind        TEXT,                       -- 'ok' | 'http_4xx' | 'http_5xx' | 'llm_failed' | 'exception'
  client_ip         TEXT,
  user_agent        TEXT,
  job_id            TEXT,
  language          TEXT,
  body_summary      TEXT,                       -- 紧凑文本摘要：filename 列表 / language / debug / 字段计数
  total_tokens      INTEGER,
  total_cost_usd    REAL,
  llm_call_count    INTEGER DEFAULT 0,
  has_image         INTEGER DEFAULT 0
);

CREATE INDEX idx_requests_ts          ON requests(timestamp_utc DESC);
CREATE INDEX idx_requests_status      ON requests(status_code);
CREATE INDEX idx_requests_endpoint    ON requests(endpoint, timestamp_utc DESC);
CREATE INDEX idx_requests_job_id      ON requests(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX idx_requests_error_kind  ON requests(error_kind) WHERE error_kind != 'ok';

CREATE TABLE llm_calls (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id        TEXT NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
  timestamp_utc     TEXT NOT NULL,
  stage             TEXT NOT NULL,
  attempt           INTEGER NOT NULL,
  model             TEXT NOT NULL,
  status            TEXT NOT NULL,              -- 'ok' | 'failed' | 'timeout'
  error_kind        TEXT,
  error_message     TEXT,
  latency_ms        REAL,
  http_status_code  INTEGER,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  total_tokens      INTEGER,
  cost_usd          REAL,
  prompt_file       TEXT,                       -- 相对 logs/store/ 的路径
  response_file     TEXT,
  parsed_file       TEXT
);

CREATE INDEX idx_llm_request    ON llm_calls(request_id);
CREATE INDEX idx_llm_ts         ON llm_calls(timestamp_utc DESC);
CREATE INDEX idx_llm_stage      ON llm_calls(stage, status);
CREATE INDEX idx_llm_failed     ON llm_calls(status) WHERE status != 'ok';

CREATE VIRTUAL TABLE requests_fts USING fts5(
  request_id UNINDEXED,
  endpoint,
  body_summary,
  error,
  content=''
);

CREATE VIRTUAL TABLE llm_fts USING fts5(
  request_id UNINDEXED,
  llm_call_id UNINDEXED,
  stage,
  model,
  error_message,
  prompt_text,
  response_text,
  content=''
);
```

**初始化 PRAGMA**：

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
```

**`error_kind` 取值规则**（由 `finalize_request` 推导）：

- `status_code` 2xx 且无 LLM 失败 → `ok`
- `status_code` 4xx → `http_4xx`
- `status_code` 5xx 且有异常 → `exception`
- `status_code` 5xx 且无异常但所有 LLM attempts 失败 → `llm_failed`
- 其它 5xx → `http_5xx`

## 5. 文件系统布局

```
logs/store/
  <YYYY-MM-DD>/
    <request_id>/                            # request_id 是 uuid4 hex（32 chars）
      request.json                           # 完整 headers + body（图片以 image_*.{ext} 引用）
      response.json                          # 完整响应 body（最多 2 MiB；超出只记 size + truncated 标志）
      image_<idx>.<ext>                      # 上传原图（保留 mime type 对应扩展名）
      llm_<stage>_<attempt>_prompt.json      # 完整 messages 数组（含 system / user content）
      llm_<stage>_<attempt>_response.json    # OpenRouter 完整 response（含 usage / cost_details）
      llm_<stage>_<attempt>_parsed.json      # 业务侧解析结果
  _dead_letter/
    <ts>.json                                # 日志写入失败时的 payload 兜底
```

**为什么按日期分目录**：让"删除某天之前"的清理动作可以走 `rm -rf logs/store/<old-date>/`，而不需要遍历所有 request_id 目录。

**路径穿越防护**：`paths.py::resolve_artifact_path(request_id, filename)` 拼好后必须 `Path.resolve()` 且 `is_relative_to(logs/store/<date>/<request_id>/)`，否则抛 `ValueError` 拒绝下载。

## 6. 写入路径改造

### 6.1 HTTP 中间件（替换 `main.py:444-480`）

```python
@app.middleware("http")
async def observe_request(request: Request, call_next):
    if not settings.log_requests:
        return await call_next(request)

    request_id = uuid.uuid4().hex
    token = ctx.set_request_id(request_id)
    start = time.monotonic()
    body = b""
    status = 500
    error = ""
    response_body_bytes = b""

    try:
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            request = _rebuild_request_with_body(request, body)
        try:
            recorder.start_request(request_id, request, body)
        except Exception:
            pass  # never block on logging

        response = await call_next(request)
        status = response.status_code
        response_body_bytes, response = await _capture_response_body(response, max_bytes=settings.log_response_max_bytes)
        response.headers["X-Request-Id"] = request_id  # _capture_response_body 返回的新 response 上设置
        return response
    except Exception as exc:
        error = repr(exc)
        raise
    finally:
        duration = (time.monotonic() - start) * 1000
        try:
            recorder.finalize_request(
                request_id=request_id,
                status=status,
                duration_ms=duration,
                error=error,
                response_body=response_body_bytes,
            )
        except Exception:
            pass
        ctx.reset_request_id(token)
```

### 6.2 `app/service.py` 改造

把现有 7 处 `_log_raw()` 调用合并为一次 `recorder.record_llm_stage()`：

```python
# 旧
self._log_raw("category_attempts", [a.__dict__ for a in attempts])
self._log_raw("category_parsed", parsed)
self._log_raw("category_raw_response", raw_response)

# 新
recorder.record_llm_stage(
    stage="category",
    attempts=attempts,            # List[AttemptRecord]
    raw_response=raw_response,    # dict (OpenRouter 完整响应)
    parsed=parsed,                # dict (业务解析后)
)
```

`record_llm_stage` 内部职责：
1. 对每个 attempt INSERT 一行 `llm_calls`，`model` 取 `attempt.model`（每个 attempt 可能用不同 fallback model），latency / error_kind / status_code 都来自 attempt。
2. 文件落盘策略：
   - `prompt_file`：每个 attempt 都写一份（同一 stage 内不同 attempt 的 messages 内容通常一致，但 model 不同；为简单起见每个 attempt 都落，磁盘开销可忽略）。
   - `response_file`：只在 attempt 拿到 HTTP 响应（不论成功失败）时写；网络层失败的 attempt 不写。
   - `parsed_file`：只在 attempt 成功且业务解析通过时写（即整个 stage 最终成功的那一行）。
3. Token / cost 从最终成功 attempt 的 `raw_response.usage` 与 `raw_response.cost` 或 `cost_details` 提取（OpenRouter 字段已存在，当前未入库），写入该行的 `prompt_tokens / completion_tokens / total_tokens / cost_usd`。失败 attempt 这几列为 NULL。
4. INSERT FTS（提取 prompt 文本字符串化 + response 文本字符串化），每个 attempt 一行 `llm_fts`。

删除 `_log_raw` 与 `self._logs_dir`。

### 6.3 异步 executor 透传

`service.py` 中 `product_data_executor.submit(...)` 改为显式传 `request_id`，worker 入口 `set_request_id(token); finally: reset`。

### 6.4 Showcase 改造（见 §3.5）

### 6.5 删除旧实现

- 删 `app/request_logging.py`
- 删 `service._log_raw`、`self._logs_dir`、`service.py` 内所有 `self._log_raw(...)` 调用点
- 删除磁盘上 `logs/requests/` 与 `logs/*.log` 旧文件（提供 `scripts/wipe_old_logs.py`）

## 7. 查询 API 与 Web 页

### 7.1 API（全部受 `require_logs_auth` 保护）

```
GET  /api/v1/logs/requests
       ?from=<iso>&to=<iso>&endpoint=<str>&status=<int>
       &error_kind=ok|http_4xx|http_5xx|llm_failed|exception
       &min_duration_ms=<int>&job_id=<str>
       &q=<fts query>&include_llm_text=true|false
       &cursor=<opaque>&limit=50

GET  /api/v1/logs/requests/{request_id}
       → {request, llm_calls, job_siblings}

GET  /api/v1/logs/requests/{request_id}/files/{filename}
       → stream，Content-Type 按扩展名；路径穿越防护

GET  /api/v1/logs/stats?from=<iso>&to=<iso>
       → {total, by_status, by_endpoint, by_error_kind, sum_tokens, sum_cost}

POST /api/v1/logs/prune
       → 手动触发清理
```

**翻页**：cursor 是 `base64(timestamp_utc || '|' || request_id)`，避免 OFFSET 在大表上的性能问题。

**文本搜索**：
- `include_llm_text=false`（默认）：只在 `requests_fts(endpoint, body_summary, error)` MATCH。
- `include_llm_text=true`：UNION `llm_fts(stage, model, error_message, prompt_text, response_text)`，按 request_id 去重回主表。

### 7.2 Web 页（`web/logs.html`）

风格参考 `web/config.html`。单页 + fetch，挂在 `GET /logs`（带 auth dep）。

```
┌─────────────────────────────────────────────────────────────┐
│ 日志查看                                       [刷新] [设置] │
├─────────────────────────────────────────────────────────────┤
│ 时间: [今天▼] [自定义]   端点: [全部▼]   状态: [全部▼]      │
│ ☐ 仅看失败  ☐ 慢请求(>5s)                                    │
│ 搜索: [_______________] ☐包含 prompt/response  [搜索]       │
├─────────────────────────────────────────────────────────────┤
│ 时间             端点          状态  耗时   token  错误      │
│ 12:34:56  /analyze       200   3.4s  4521         ▼         │
│   ├─ POST 主请求 [查看 request.json] [查看 response.json]    │
│   ├─ GET poll x3 [展开兄弟请求]                              │
│   └─ LLM stages:                                            │
│       fast_vision  ok    1.2s  1024  gpt-4o-mini [prompt][resp] │
│       category     ok    0.8s   512  gpt-4o-mini [prompt][resp] │
│       product_data ok    1.4s  2985  gpt-4o      [prompt][resp] │
│   原图: [thumb][thumb][thumb]                                │
│ 12:33:10  /analyze       502  12.1s    -    llm_failed  ▼   │
│ ...                                                          │
│                                              [加载更多]      │
└─────────────────────────────────────────────────────────────┘
```

**交互细节**：
- 默认 50 条，按 `timestamp_utc DESC`。
- 时间快捷：今天 / 24h / 7天 / 自定义。
- 列表行点击 → 内联展开详情面板。
- 同 job_id 的兄弟请求一键过滤（点 "展开兄弟请求" 即设置 `job_id=...`）。
- prompt / response 在 modal 中 pretty-print + 折叠；图片直接渲染。
- 复制 request_id 按钮。
- 顶部小条展示当前过滤范围下的：总数 / 失败率 / 累计 token / 累计 cost（调 `/stats`）。

### 7.3 认证

`auth.py::require_logs_auth(authorization: str = Header(None))`：

- 读 `settings.logs_password`；为空时返回 503（fail-closed，避免误开放）。
- 接受 `Authorization: Basic <base64>` 或 `Authorization: Bearer <token>`，用常量时间比较。
- 浏览器走 Basic Auth 弹窗，记住凭据后访问 `/logs` 与 `/api/v1/logs/*` 均自动带凭据。
- 也覆盖 `/config` 页与 `PUT /api/v1/config`。
- `/health` 与 `/api/v1/mercari/*` 不加 auth（这些是业务端点，由别处保护）。

## 8. 错误处理与可见性

**核心契约**：日志写入失败绝不影响主流程。延续现有 `_log_raw` / `write_request_log` 的 swallow 策略。

**改进点**（新 spec 与旧实现的差异）：

```python
def record_llm_stage(...) -> None:
    try:
        _record_llm_stage_impl(...)
    except Exception:
        _internal_logger.exception("observability.record_llm_stage failed")
        try:
            _write_dead_letter(payload)
        except Exception:
            pass
```

- 走 stdlib `logging` 到 stderr（运维能在 systemd journal 看到）。
- 写 dead letter 到 `logs/store/_dead_letter/<ts>.json`，运维可看到丢了什么。
- Dead letter 目录超过 1000 文件时停止写入（防止磁盘炸）。

**LLM 内部错误的捕获**：`AttemptRecord.error_kind / message` 已有，新增写入 `llm_calls.error_kind / error_message / http_status_code`。对于 Python 异常（非 OpenRouter 错误），在 caller 抛出处 `recorder.record_llm_stage` 仍会被调用一次（因为 `try/except LLMAllAttemptsFailedError` 已经在 service.py 里），传 `attempts=[...]` + `raw_response=None`。

## 9. 性能

- **同步写入**：HTTP middleware 写 SQLite + 文件均同步。SQLite WAL 下单条 INSERT < 1 ms；文件写 5–20 ms。LLM 调用动辄秒级，可忽略。
- **不引入后台写线程 / queue**：复杂度增长（崩溃丢数据、worker 死锁）不值得。
- **FTS 索引**：contentless 模式，typical prompt 几 KB，每日数百次调用一周累计 < 200 MB。
- **查询路径**：列表查询走复合索引；FTS 命中后返回 request_id 列表，回主表 LIMIT 50，ms 级。
- **响应 body 抓取上限 2 MiB**：超出只记元数据 + `truncated=true` 标志，避免吃内存。

## 10. 保留与清理

`retention.py::prune(retention_days, max_total_bytes, store_root, db)`：

1. 按时间删：`SELECT request_id, timestamp_utc FROM requests WHERE timestamp_utc < cutoff ORDER BY timestamp_utc`
2. 对选中的 request_id 集合：
   - `DELETE FROM requests_fts WHERE request_id IN (...)`（contentless FTS5 没有外键 cascade，必须显式删）
   - `DELETE FROM llm_fts WHERE request_id IN (...)`
   - `DELETE FROM requests WHERE request_id IN (...)`（`llm_calls` 通过 `ON DELETE CASCADE` 自动删）
3. 删对应 `logs/store/<YYYY-MM-DD>/<request_id>/`；如果某天目录下所有 request_id 都被清理，删空目录。
4. 如果磁盘总占用仍超 `max_total_bytes`，按 `timestamp_utc ASC` 继续删直至 < cap（同样按 §2 顺序删 FTS / requests / 文件）。
5. 写一行摘要到 stderr（含删除行数、释放字节数）。

**触发**：FastAPI startup 注册 `BackgroundTasks` + `asyncio` 定时器，每 `LOG_PRUNE_INTERVAL_MINUTES` 跑一次（默认 60）。另暴露 `POST /api/v1/logs/prune` 即时触发。

## 11. 配置项（接入 `app/runtime_config.py`，可通过 `/config` 页热更）

| Key | 默认 | 说明 |
|---|---|---|
| `LOG_REQUESTS` | `true` | 总开关（保留现有名称） |
| `LOG_RETENTION_DAYS` | `7` | 天数底线 |
| `LOG_MAX_TOTAL_BYTES` | `5_368_709_120` | 5 GiB 容量上限 |
| `LOG_PRUNE_INTERVAL_MINUTES` | `60` | 清理频率 |
| `LOG_RESPONSE_MAX_BYTES` | `2_097_152` | 响应 body 抓取上限 |
| `LOG_LLM_RAW` | **删除** | 现存项不再需要（新系统的 SQLite + 文件总是写；要全局关日志请用 `LOG_REQUESTS=false`） |
| `LOGS_PASSWORD` | `""` | 空字符串 = 整个 logs 模块返回 503 |

## 12. 测试计划（`tests/observability/`）

- `test_store.py`：schema 迁移幂等、insert/query 往返、并发写（两线程各 100 行不丢）、FTS 命中。
- `test_context.py`：contextvar 在线程池 executor 边界手动透传后正确隔离。
- `test_recorder.py`：record_llm_stage 把 attempts 拆成多行；token / cost 聚合到 requests；异常 swallow + dead letter 落盘。
- `test_retention.py`：天数触发；容量触发；SQLite 删除后文件目录消失；dead letter 写满后停写。
- `test_api.py`：列表过滤、cursor 翻页稳定（插入新行后老 cursor 仍能续翻）、FTS 查询、文件下载路径穿越（`../../../etc/passwd` 应 403）、无 `LOGS_PASSWORD` 时 503。
- `test_middleware.py`：注入 `X-Request-Id`、body 抓取、异常时仍写日志。
- `test_e2e_analyze.py`（集成）：mock OpenRouter，调一次 `/analyze`，断言 1 行 requests + 多行 llm_calls + 文件齐全 + job_id 关联后续 GET。
- `test_showcase_integration.py`：showcase 一次调用复用中间件 request_id，archive 仍按原逻辑写。
- 删除旧 `tests/test_request_logging*.py`（如有）。

## 13. 迁移与上线

1. 在分支上实现新模块 + UI + 测试。
2. 提供 `scripts/wipe_old_logs.py`：`rm -rf logs/*.log logs/requests/`。
3. 同一 commit / 同一 PR 内：
   - 引入 `app/observability/*`
   - 改造 `main.py` 中间件
   - 改造 `service.py` 7 处 `_log_raw` 调用 + executor 透传
   - 改造 `showcase/service.py` request_id 复用 + 新增 record_llm_stage
   - 删除 `app/request_logging.py` 与 `service._log_raw`
   - `app/config.py` 移除 `log_llm_raw`，新增 `log_retention_days / log_max_total_bytes / log_prune_interval_minutes / log_response_max_bytes / logs_password`（沿用现有 snake_case 命名）
   - 新增 `web/logs.html`，挂到 `/logs`
4. 上线步骤：
   - 部署新版本
   - 跑 `wipe_old_logs.py` 清理旧文件
   - 人工跑：`/analyze`、`/title/analyze`、`/showcase/generate` 各一次
   - 在 `/logs` 检查：能看到三条记录、X-Request-Id 在响应头里、点开能看到 LLM stages、prompt/response 可下载
   - 触发一次失败请求（如错误 API key），验证 `error_kind=llm_failed` 能筛出来
5. 文档：在 `README.md` 增补 `/logs` 入口与默认密码配置说明。

## 14. 未列入本期的明确事项

- 运营级 token / cost 大盘（图表化）
- 多 worker / 多机器部署的 SQLite 共享（如需可改 Postgres）
- 日志导出（CSV/JSON 批量）
- 报警 / webhook 接入
- 接入 OpenTelemetry / 分布式追踪

这些可在本期完成后单独立项。
