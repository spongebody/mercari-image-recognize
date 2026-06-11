# 模型测试页重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `/evaluations` 页面从 5-tab 结构重构为「单页测试台 + 新建抽屉 + 独立对比视图」，删除分析功能，补齐耗时统计与失败明细。

**Architecture:** 后端只做三处小改动（summary 增加耗时均值、新增 errors 接口、删除 analysis）；前端三个文件（HTML/CSS/JS）整体重写，保持 vanilla JS + 服务端渲染架构不变，复用现有 API。

**Tech Stack:** FastAPI + 文件存储（后端），vanilla JS / CSS（前端，无构建步骤），pytest（`uv run pytest`）。

**Spec:** `docs/superpowers/specs/2026-06-11-model-testing-redesign-design.md`

---

## 全局注意事项

1. **测试命令**：本项目用 uv 管理，跑测试一律 `uv run pytest <path> -v`。
2. **不要误删同名概念**：`main.py` 里的 `analysis_job_store`、`_merge_analysis_payload`、`poll_image_analysis` 属于**图片识别异步任务**功能，与本次要删除的「评测分析（evaluation analysis）」无关，**严禁改动**。本次只删：`PUT /api/v1/evaluations/{run_id}/analysis` 路由（main.py:914-922）、`EvaluationRunStore.save_analysis`（runs.py:254-269）、`read_run` 返回值里的 `analysis` 字段（runs.py:126-136）。
3. **前端三个文件在 Task 4-6 中整体重写**，期间页面处于中间态，**Task 6 结束才一起 commit**。
4. 静态资源带版本号查询串，本次统一用 `?v=20260611`。
5. 行内代码注释风格：现有代码用简短英文注释解释「为什么」，保持一致。

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `app/evaluation/image_model_evaluation.py` | 修改 | `_summary_bucket()` 增加三个耗时均值字段 |
| `app/evaluation/runs.py` | 修改 | 新增 `read_errors()`；删除 `save_analysis()`；`read_run()` 去掉 analysis |
| `main.py` | 修改 | 新增 `GET .../errors` 路由；删除 `PUT .../analysis` 路由 |
| `tests/test_image_model_evaluation.py` | 修改 | 耗时均值测试 |
| `tests/test_evaluation_runs.py` | 修改 | read_errors / read_run 测试 |
| `tests/test_evaluations_api.py` | 修改 | errors 端点测试、analysis 端点已移除测试 |
| `web/evaluations.html` | 重写 | 双视图骨架 + 抽屉 + 错误弹层 + lightbox |
| `web/assets/evaluations.css` | 重写 | 统计卡、抽屉、sticky 工具条、对比表样式 |
| `web/assets/evaluations.js` | 重写 | 状态分支渲染、键盘流、dirty 跟踪、对比表 |

---

### Task 1: summary 增加耗时均值

**Files:**
- Modify: `app/evaluation/image_model_evaluation.py`（`_summary_bucket()` 在 298-342 行附近）
- Test: `tests/test_image_model_evaluation.py`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_image_model_evaluation.py` 文件末尾追加（该文件已 import `summarize_rows`；若没有，在文件头部补 `from app.evaluation.image_model_evaluation import summarize_rows`）：

```python
def test_summary_includes_average_durations():
    rows = [
        {
            "genreId": "1", "aiCategory": "1", "brand": "nike", "aiBrand": "nike",
            "categoryDurationS": "1.0", "productDataDurationS": "2.0", "totalDurationS": "3.0",
        },
        {
            "genreId": "2", "aiCategory": "2", "brand": "nike", "aiBrand": "nike",
            # productDataDurationS 为空串（行失败时的留空格式），不应计入均值
            "categoryDurationS": "2.0", "productDataDurationS": "", "totalDurationS": "5.0",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["overall"]["avgTotalDurationS"] == 4.0
    assert summary["overall"]["avgCategoryDurationS"] == 1.5
    assert summary["overall"]["avgProductDataDurationS"] == 2.0


def test_summary_average_durations_none_when_all_missing():
    rows = [{"genreId": "1", "aiCategory": "1", "brand": "", "aiBrand": ""}]

    summary = summarize_rows(rows)

    assert summary["overall"]["avgTotalDurationS"] is None
    assert summary["overall"]["avgCategoryDurationS"] is None
    assert summary["overall"]["avgProductDataDurationS"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_image_model_evaluation.py -v -k "average_durations"`
Expected: 2 FAILED with `KeyError: 'avgTotalDurationS'`

- [ ] **Step 3: Implement**

在 `app/evaluation/image_model_evaluation.py` 中，`_review_check_is_positive`（294 行）之后、`_summary_bucket` 之前插入：

```python
def _duration_seconds(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _average_duration(rows: Sequence[Dict[str, Any]], field: str) -> Optional[float]:
    values = [
        seconds
        for seconds in (_duration_seconds(row.get(field)) for row in rows)
        if seconds is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 3)
```

然后在 `_summary_bucket()` 的返回字典里（`"brandPendingReview": brand_pending_review,` 之后）追加三个键：

```python
        "avgTotalDurationS": _average_duration(rows, "totalDurationS"),
        "avgCategoryDurationS": _average_duration(rows, "categoryDurationS"),
        "avgProductDataDurationS": _average_duration(rows, "productDataDurationS"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_image_model_evaluation.py tests/test_evaluation_runs.py -v`
Expected: 全部 PASS（含原有测试，确认没破坏 byModel 分组等行为）

- [ ] **Step 5: Commit**

```bash
git add app/evaluation/image_model_evaluation.py tests/test_image_model_evaluation.py
git commit -m "feat(evaluation): add average stage durations to summary buckets

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: 失败明细接口 `GET /api/v1/evaluations/{run_id}/errors`

**Files:**
- Modify: `app/evaluation/runs.py`（`read_results` 之后，约 219 行）
- Modify: `main.py`（`download_evaluation_results` 路由之后，约 902 行）
- Test: `tests/test_evaluation_runs.py`, `tests/test_evaluations_api.py`

- [ ] **Step 1: Write the failing store tests**

在 `tests/test_evaluation_runs.py` 末尾追加（确认文件头已有 `from app.evaluation.runs import EvaluationRunStore`，没有则补上）：

```python
def test_read_errors_parses_jsonl_and_skips_bad_lines(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-00"
    run_dir.mkdir(parents=True)
    (run_dir / "errors.jsonl").write_text(
        '{"caseIndex": 1, "itemName": "a", "error": "boom"}\n'
        "not-json\n"
        '{"caseIndex": 2, "itemName": "b", "error": "bang"}\n',
        encoding="utf-8",
    )

    errors = store.read_errors("2026-06-11-10-00")

    assert errors == [
        {"caseIndex": 1, "itemName": "a", "error": "boom"},
        {"caseIndex": 2, "itemName": "b", "error": "bang"},
    ]


def test_read_errors_returns_empty_when_file_missing(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-01"
    run_dir.mkdir(parents=True)

    assert store.read_errors("2026-06-11-10-01") == []


def test_read_errors_respects_limit(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-02"
    run_dir.mkdir(parents=True)
    lines = "".join(f'{{"caseIndex": {i}, "error": "e{i}"}}\n' for i in range(30))
    (run_dir / "errors.jsonl").write_text(lines, encoding="utf-8")

    assert len(store.read_errors("2026-06-11-10-02")) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluation_runs.py -v -k "read_errors"`
Expected: 3 FAILED with `AttributeError: 'EvaluationRunStore' object has no attribute 'read_errors'`

- [ ] **Step 3: Implement the store method**

在 `app/evaluation/runs.py` 的 `read_results`（215-218 行）之后插入：

```python
    def read_errors(self, run_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        path = self.run_path(run_id) / "errors.jsonl"
        if not path.exists():
            return []
        errors: List[Dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    errors.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # keep serving partial diagnostics over failing hard
                if len(errors) >= limit:
                    break
        return errors
```

- [ ] **Step 4: Run store tests**

Run: `uv run pytest tests/test_evaluation_runs.py -v -k "read_errors"`
Expected: 3 PASS

- [ ] **Step 5: Write the failing API tests**

在 `tests/test_evaluations_api.py` 末尾追加（文件头已有 `from fastapi.testclient import TestClient` 和 `import main`）：

```python
def test_read_evaluation_errors_endpoint(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-09-00"
    run_dir.mkdir(parents=True)
    (run_dir / "errors.jsonl").write_text(
        '{"caseIndex": 1, "itemName": "x", "error": "boom"}\n', encoding="utf-8"
    )
    monkeypatch.setattr(main, "evaluation_store", store)
    client = TestClient(main.app)

    response = client.get("/api/v1/evaluations/2026-06-11-09-00/errors")

    assert response.status_code == 200
    assert response.json() == {
        "errors": [{"caseIndex": 1, "itemName": "x", "error": "boom"}]
    }


def test_read_evaluation_errors_404_for_unknown_run(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    monkeypatch.setattr(main, "evaluation_store", EvaluationRunStore(tmp_path))
    client = TestClient(main.app)

    assert client.get("/api/v1/evaluations/nope/errors").status_code == 404
```

- [ ] **Step 6: Run API tests to verify they fail**

Run: `uv run pytest tests/test_evaluations_api.py -v -k "errors"`
Expected: 第一个 404（路由不存在时落到 `GET /{run_id}` 也匹配不上两段路径，返回 404）→ FAILED；第二个可能误 PASS，没关系。

- [ ] **Step 7: Implement the route**

在 `main.py` 的 `download_evaluation_results`（896-901 行）之后插入：

```python
@app.get("/api/v1/evaluations/{run_id}/errors")
def read_evaluation_errors(run_id: str) -> Dict[str, Any]:
    try:
        return {"errors": evaluation_store.read_errors(run_id)}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Evaluation run not found.") from exc
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_evaluations_api.py tests/test_evaluation_runs.py -v`
Expected: 全部 PASS

- [ ] **Step 9: Commit**

```bash
git add app/evaluation/runs.py main.py tests/test_evaluation_runs.py tests/test_evaluations_api.py
git commit -m "feat(evaluation): expose run error details via /errors endpoint

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: 彻底删除评测分析功能（后端）

**Files:**
- Modify: `app/evaluation/runs.py`（删除 `save_analysis` 254-269 行；修改 `read_run` 119-136 行）
- Modify: `main.py`（删除 `save_evaluation_analysis` 路由 914-922 行）
- Test: `tests/test_evaluation_runs.py`, `tests/test_evaluations_api.py`

- [ ] **Step 1: Write the failing tests**

在 `tests/test_evaluation_runs.py` 末尾追加（确认文件头已有 `from app.evaluation.runs import EvaluationRunConfig, EvaluationRunStore`，没有则补全）：

```python
def test_read_run_omits_analysis_field(tmp_path):
    store = EvaluationRunStore(tmp_path)
    input_path = tmp_path / "input.tsv"
    input_path.write_text(
        "itemName\tgenreId\timage\tbrand\nitem\t100\thttp://example.com/a.jpg\tnike\n",
        encoding="utf-8",
    )
    run = store.create_run(
        input_path=input_path,
        config=EvaluationRunConfig(
            visionModel="v", categoryModel="c", productDataModel="p"
        ),
    )

    data = store.read_run(run.runId)

    assert "analysis" not in data
    assert not hasattr(store, "save_analysis")
```

在 `tests/test_evaluations_api.py` 末尾追加：

```python
def test_analysis_endpoint_removed():
    client = TestClient(main.app)

    response = client.put("/api/v1/evaluations/some-run/analysis", json={})

    assert response.status_code in (404, 405)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_evaluation_runs.py::test_read_run_omits_analysis_field tests/test_evaluations_api.py::test_analysis_endpoint_removed -v`
Expected: `test_read_run_omits_analysis_field` FAILED（`"analysis" in data`）；`test_analysis_endpoint_removed` FAILED（现在返回 404 之外的状态——实际是 404? 注意：当前路由存在，run 不存在时也返回 404，所以该测试此刻可能误 PASS。以删除代码后两个测试都 PASS 且全量套件通过为准）。

- [ ] **Step 3: Delete the code**

1. `app/evaluation/runs.py`：整段删除 `save_analysis` 方法（254-269 行）。
2. `app/evaluation/runs.py` 的 `read_run`：删除 `analysis = (...)` 赋值与返回字典里的 `"analysis": analysis,` 行，改为：

```python
    def read_run(self, run_id: str) -> Dict[str, Any]:
        path = self.run_path(run_id)
        summary = (
            self._read_json(path / "summary.json")
            if (path / "summary.json").exists()
            else {}
        )
        return {
            "run": self._read_json(path / "run_config.json"),
            "status": self._read_json(path / "status.json"),
            "summary": summary,
        }
```

3. `main.py`：整段删除 `save_evaluation_analysis` 路由函数（914-922 行，含装饰器）。

- [ ] **Step 4: Verify no dangling references**

Run: `grep -n "save_analysis\|save_evaluation_analysis" main.py app/evaluation/*.py tests/*.py`
Expected: 仅 `tests/test_evaluation_runs.py` 里 `not hasattr(store, "save_analysis")` 一处（断言本身）。

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -x -q`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add app/evaluation/runs.py main.py tests/test_evaluation_runs.py tests/test_evaluations_api.py
git commit -m "refactor(evaluation): remove unused analysis notes feature

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: 重写 `web/evaluations.html`

**Files:**
- Rewrite: `web/evaluations.html`

- [ ] **Step 1: Replace the file with the following complete content**（本任务不 commit，Task 6 末尾统一提交）

```html
<!DOCTYPE html>
<html lang="zh">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>模型测试</title>
    <link rel="stylesheet" href="/assets/evaluations.css?v=20260611" />
    <link rel="stylesheet" href="/assets/shell.css?v=20260611" />
  </head>
  <body>
    <div class="page">
      <div class="evaluation-layout">
        <aside class="stack">
          <section class="card stack">
            <div class="entry-head">
              <span class="entry-title">测试记录</span>
              <button class="secondary mini" id="refresh-btn" type="button">刷新</button>
            </div>
            <div class="run-list" id="run-list">正在读取...</div>
            <div class="sidebar-actions">
              <button id="new-run-btn" type="button">＋ 新建测试</button>
              <button class="secondary" id="compare-toggle-btn" type="button">⇄ 多轮对比</button>
            </div>
          </section>
        </aside>

        <main class="stack">
          <div class="message" id="message"></div>

          <section id="view-run" class="view active">
            <div class="stack">
              <div class="card stack">
                <div class="entry-head">
                  <div class="run-title-group">
                    <span class="entry-title" id="run-title">未选择测试</span>
                    <span class="pill idle" id="run-status">idle</span>
                  </div>
                  <div class="compact-actions">
                    <button class="secondary" id="reuse-btn" type="button" disabled>复用配置</button>
                    <a class="export-link" id="export-link-top" href="#" hidden>导出 CSV</a>
                    <button class="secondary" id="archive-btn" type="button" hidden>归档</button>
                  </div>
                </div>
                <div class="hint" id="run-meta">从左侧选择一条测试记录，或点击「＋ 新建测试」。</div>

                <div id="progress-block" hidden>
                  <div class="progress-num" id="progress-num">0 / 0</div>
                  <div class="progress-track"><div class="progress-fill" id="progress-fill"></div></div>
                  <div class="hint" id="progress-meta"></div>
                </div>

                <div class="stat-row" id="stat-row" hidden></div>
              </div>

              <div class="card stack" id="review-card" hidden>
                <div class="review-toolbar">
                  <div class="filter-bar" id="filter-bar"></div>
                  <div class="batch-actions" id="batch-actions">
                    <span class="batch-count" id="batch-count">已选 0</span>
                    <select id="batch-field">
                      <option value="customerCategoryCheck">分类校验</option>
                      <option value="customerBrandCheck">品牌校验</option>
                    </select>
                    <button class="secondary batch-btn" type="button" data-batch="OK">批量正确</button>
                    <button class="secondary batch-btn" type="button" data-batch="ACCEPTABLE">批量可接受</button>
                    <button class="secondary batch-btn" type="button" data-batch="NG">批量错误</button>
                  </div>
                  <div class="review-save">
                    <span class="dirty-count" id="dirty-count" hidden></span>
                    <a class="export-link" id="export-link" href="#" hidden>导出 CSV</a>
                    <button id="save-review-btn" type="button" disabled>保存校验</button>
                  </div>
                </div>
                <div class="hint">键盘：↑/↓ 选行，Space 勾选，1/2/3 标分类（正确/可接受/错误），q/w/e 标品牌；打标后自动跳到下一行，大图打开时快捷键同样生效。</div>
                <div id="results-host" class="hint">测试完成后展示 AI 结果和校验字段。</div>
              </div>
            </div>
          </section>

          <section id="view-compare" class="view">
            <div class="card stack">
              <div class="entry-head"><span class="entry-title">多轮对比</span></div>
              <div class="hint">点选要对比的轮次（默认预选最近两轮已完成的测试）。配置差异蓝色高亮，指标最优值绿色高亮。</div>
              <div class="compare-picker" id="compare-picker"></div>
              <div id="compare-host" class="hint">请选择至少一轮已完成的测试。</div>
            </div>
          </section>
        </main>
      </div>
    </div>

    <div class="drawer-backdrop" id="drawer-backdrop" hidden></div>
    <aside class="drawer" id="drawer" hidden aria-label="新建测试">
      <div class="entry-head">
        <span class="entry-title">新建测试</span>
        <button class="secondary mini" id="drawer-close-btn" type="button" aria-label="关闭">×</button>
      </div>
      <div class="stack">
        <div>
          <label for="f-file">测试数据</label>
          <input id="f-file" type="file" accept=".csv,.tsv,text/csv,text/tab-separated-values" />
          <div class="hint">TSV/CSV 需包含 itemName、genreId、image、brand 四个字段。</div>
        </div>
        <div>
          <label for="f-vision">图片识别模型</label>
          <input id="f-vision" type="text" placeholder="openai/gpt-4o-mini" />
        </div>
        <div>
          <label for="f-category">分类模型</label>
          <input id="f-category" type="text" placeholder="openai/gpt-4o-mini" />
        </div>
        <div>
          <label for="f-product">商品数据模型</label>
          <input id="f-product" type="text" placeholder="openai/gpt-4o-mini" />
        </div>
        <div class="controls-row">
          <div>
            <label for="f-reasoning">推理程度</label>
            <select id="f-reasoning">
              <option value="none">none</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
            </select>
          </div>
          <div>
            <label for="f-limit">数量限制</label>
            <input id="f-limit" type="number" min="0" step="1" value="0" />
          </div>
          <div>
            <label for="f-language">语言</label>
            <select id="f-language">
              <option value="ja">ja</option>
              <option value="zh">zh</option>
              <option value="en">en</option>
            </select>
          </div>
        </div>
        <div class="message" id="drawer-message"></div>
        <button id="create-btn" type="button">开始测试</button>
      </div>
    </aside>

    <div class="modal-backdrop" id="errors-backdrop" hidden>
      <div class="modal" role="dialog" aria-modal="true" aria-label="失败明细">
        <div class="entry-head">
          <span class="entry-title">失败明细（前 20 条）</span>
          <button class="secondary mini" id="errors-close-btn" type="button" aria-label="关闭">×</button>
        </div>
        <div id="errors-host" class="errors-host"></div>
      </div>
    </div>

    <div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="图片预览" hidden>
      <button class="lightbox-close" id="lightbox-close" type="button" aria-label="关闭">×</button>
      <button class="lightbox-nav lightbox-prev" id="lightbox-prev" type="button" aria-label="上一张">‹</button>
      <img class="lightbox-img" id="lightbox-img" src="" alt="" />
      <button class="lightbox-nav lightbox-next" id="lightbox-next" type="button" aria-label="下一张">›</button>
    </div>

    <script src="/assets/shell.js?v=20260611"></script>
    <script src="/assets/evaluations.js?v=20260611"></script>
  </body>
</html>
```

---

### Task 5: 重写 `web/assets/evaluations.css`

**Files:**
- Rewrite: `web/assets/evaluations.css`

- [ ] **Step 1: Replace the file with the following complete content**（不 commit）

```css
:root {
  --bg: #f7f8fa;
  --card: #ffffff;
  --text: #1c1f25;
  --muted: #6b7280;
  --accent: #2f80ed;
  --border: #e5e7eb;
  --danger: #e11d48;
  --success: #16a34a;
  --warn: #d97706;
}

* { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Inter", "Noto Sans SC", "Noto Sans JP", system-ui, -apple-system, sans-serif;
  min-height: 100vh;
}

.page { max-width: 1180px; margin: 0 auto; padding: 0; }

.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 18px;
  box-shadow: 0 6px 20px rgba(15, 23, 42, 0.05);
}

.controls-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
  gap: 12px;
}

label {
  display: block;
  font-size: 13px;
  font-weight: 700;
  color: var(--muted);
  margin-bottom: 6px;
}

input[type="text"], input[type="number"], input[type="file"], select, textarea {
  width: 100%;
  padding: 12px 16px;
  border: 2px solid var(--border);
  border-radius: 10px;
  background: #fdfdfd;
  font-size: 14px;
  transition: all 0.2s ease;
  font-family: inherit;
}

input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--accent);
  background-color: white;
  box-shadow: 0 0 0 3px rgba(47, 128, 237, 0.1);
}

textarea { resize: vertical; line-height: 1.55; }

.hint { margin-top: 6px; color: var(--muted); font-size: 12px; line-height: 1.5; }

button {
  width: 100%;
  border: none;
  background: linear-gradient(135deg, var(--accent), #1d4ed8);
  color: white;
  padding: 14px 16px;
  border-radius: 12px;
  font-size: 15px;
  font-weight: 700;
  cursor: pointer;
  transition: transform 0.08s ease, box-shadow 0.08s ease;
  box-shadow: 0 10px 25px rgba(47, 128, 237, 0.2);
}

button.secondary {
  background: white;
  color: var(--text);
  border: 2px solid var(--border);
  box-shadow: none;
}

button:disabled { opacity: 0.7; cursor: not-allowed; }

button:not(:disabled):hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(47, 128, 237, 0.28);
}

button.secondary:not(:disabled):hover { box-shadow: none; }

.mini { width: auto; padding: 4px 10px; font-size: 12px; border-radius: 8px; }
.mini:not(:disabled):hover { transform: none; }

.message { display: none; border-radius: 10px; padding: 12px 14px; font-size: 14px; line-height: 1.5; }
.message.show { display: block; }
.message.success { background: #dcfce7; color: #166534; border: 1px solid #86efac; }
.message.error { background: #fee2e2; color: #991b1b; border: 1px solid #fecdd3; }

.pill {
  background: #e0e7ff;
  color: #1e3a8a;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.pill.completed { background: #dcfce7; color: #166534; }
.pill.running, .pill.pending { background: #dbeafe; color: #1e40af; }
.pill.archived { background: #e5e7eb; color: #374151; }
.pill.failed { background: #fee2e2; color: #991b1b; }
.pill.idle { background: #f3f4f6; color: #6b7280; }

.entry-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.entry-title { font-size: 15px; font-weight: 700; color: var(--text); }
.run-title-group { display: flex; align-items: center; gap: 10px; min-width: 0; }
.run-title-group .entry-title { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

.evaluation-layout {
  display: grid;
  grid-template-columns: minmax(240px, 300px) 1fr;
  gap: 16px;
  align-items: start;
}

.stack { display: grid; gap: 14px; }
.compact-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.compact-actions button { width: auto; padding: 10px 16px; font-size: 13px; }

.view { display: none; }
.view.active { display: block; }

.run-list { display: grid; gap: 8px; max-height: 480px; overflow: auto; }
.run-item {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #fff;
  padding: 10px 12px;
  cursor: pointer;
  transition: border-color 0.12s ease, background 0.12s ease;
}
.run-item.active { background: #eff6ff; box-shadow: inset 3px 0 0 var(--accent); }
.run-id { font-size: 13px; font-weight: 800; color: var(--text); }
.run-meta { margin-top: 4px; color: var(--muted); font-size: 12px; line-height: 1.45; }
.run-clone {
  width: auto;
  margin-top: 8px;
  padding: 5px 10px;
  font-size: 11px;
  font-weight: 700;
  background: #fff;
  color: var(--accent);
  border: 1px dashed var(--accent);
  border-radius: 7px;
  box-shadow: none;
  cursor: pointer;
}
.run-clone:not(:disabled):hover { transform: none; box-shadow: none; background: #eff6ff; }

.sidebar-actions { display: grid; gap: 8px; margin-top: 4px; }
.sidebar-actions button { padding: 11px 14px; font-size: 14px; }
.sidebar-actions .secondary.on { border-color: var(--accent); color: var(--accent); background: #eff6ff; }

.progress-num { font-size: 28px; font-weight: 800; font-variant-numeric: tabular-nums; }
.progress-track { height: 8px; background: #e5e7eb; border-radius: 999px; overflow: hidden; margin: 8px 0 4px; }
.progress-fill { height: 100%; width: 0; background: linear-gradient(135deg, var(--accent), #1d4ed8); border-radius: 999px; transition: width 0.4s ease; }

.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }
.stat { border: 1px solid #eef2f7; border-radius: 10px; padding: 10px 12px; background: #fff; }
.stat-label { color: var(--muted); font-size: 11px; margin-bottom: 4px; }
.stat-value { font-size: 22px; font-weight: 800; letter-spacing: -0.01em; font-variant-numeric: tabular-nums; }
.stat-sub { margin-top: 3px; color: var(--muted); font-size: 11px; }
.stat-warn .stat-value { color: var(--warn); }
.stat-bad .stat-value { color: var(--danger); }
.stat-click { cursor: pointer; transition: border-color 0.12s ease; }
.stat-click:hover { border-color: var(--danger); }

.review-toolbar {
  position: sticky;
  top: calc(var(--shell-topbar-h, 56px) + 6px);
  z-index: 5;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  background: var(--card);
  padding: 6px 0;
}
.review-save { margin-left: auto; display: flex; align-items: center; gap: 10px; }
.review-save button { width: auto; padding: 10px 18px; font-size: 13px; }
.dirty-count { font-size: 12px; font-weight: 700; color: var(--warn); white-space: nowrap; }

.export-link {
  display: inline-block;
  padding: 9px 14px;
  border: 2px solid var(--border);
  border-radius: 10px;
  background: #fff;
  color: var(--text);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
  white-space: nowrap;
}
.export-link:hover { border-color: var(--accent); color: var(--accent); }

.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { width: auto; padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 700; border: 1px solid var(--border); background: #fff; color: var(--muted); box-shadow: none; cursor: pointer; transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease; }
.chip:not(:disabled):hover { transform: none; box-shadow: none; }
.chip.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.chip.chip-bad.active { background: var(--danger); border-color: var(--danger); }
.chip.chip-pending.active { background: var(--warn); border-color: var(--warn); }

.batch-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.batch-count { font-size: 12px; color: var(--muted); font-weight: 700; }
.batch-btn { width: auto; padding: 7px 12px; font-size: 12px; }
#batch-field { width: auto; padding: 7px 10px; font-size: 12px; }

.results-table-wrap {
  width: 100%;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 10px;
  max-height: calc(100vh - 340px);
}
table.results-table { width: 100%; border-collapse: collapse; min-width: 920px; background: #fff; }
.results-table th, .results-table td {
  border-bottom: 1px solid var(--border);
  padding: 8px 10px;
  text-align: left;
  vertical-align: top;
  font-size: 12px;
}
.results-table th {
  position: sticky;
  top: 0;
  background: #f8fafc;
  z-index: 1;
  color: var(--muted);
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.02em;
  font-size: 11px;
}
.results-table select, .results-table textarea { min-width: 96px; padding: 8px 10px; border-width: 1px; border-radius: 8px; font-size: 12px; }
.results-table textarea { min-width: 160px; min-height: 44px; }
.results-table tr.row-bad td { background: #fef2f2; }
.results-table tr.row-focus td { box-shadow: inset 3px 0 0 var(--accent); background: #f0f7ff; }

.clip { max-width: 220px; white-space: normal; word-break: break-word; }

.diff .orig { color: var(--muted); }
.diff.ok .ai { color: var(--success); font-weight: 700; }
.diff.bad .ai { color: var(--danger); font-weight: 700; }
.diff.warn .ai { color: #b45309; font-weight: 700; }

.caret { width: auto; padding: 2px 7px; font-size: 12px; line-height: 1.2; background: #fff; color: var(--muted); border: 1px solid var(--border); border-radius: 6px; box-shadow: none; }
.caret:not(:disabled):hover { transform: none; box-shadow: none; border-color: var(--accent); color: var(--accent); }
.detail-row td { background: #f8fafc; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px 16px; font-size: 12px; }
.detail-label { display: block; color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.03em; margin-bottom: 2px; }

.thumb { width: 46px; height: 46px; object-fit: cover; border-radius: 6px; border: 1px solid var(--border); cursor: zoom-in; background: #f1f5f9; }
.thumb-empty { display: inline-flex; align-items: center; justify-content: center; color: var(--muted); font-size: 11px; cursor: default; }

.compare-picker { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }
.compare-pill { width: auto; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--border); background: #fff; color: var(--muted); font-size: 12px; font-weight: 700; box-shadow: none; }
.compare-pill:not(:disabled):hover { transform: none; box-shadow: none; border-color: var(--accent); }
.compare-pill.on { border-color: var(--accent); background: #eff6ff; color: var(--accent); }
.compare-table { min-width: auto; }
.compare-table .compare-label { font-weight: 800; color: var(--muted); }
.compare-table td.compare-best { background: #dcfce7; color: #166534; font-weight: 800; }
.compare-table td.compare-diff { background: #eff6ff; color: #1d4ed8; font-weight: 700; }
.compare-table tr.compare-section td { background: #f8fafc; color: var(--muted); font-size: 11px; font-weight: 700; }

.drawer-backdrop, .modal-backdrop { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); z-index: 60; }
.drawer-backdrop[hidden], .modal-backdrop[hidden] { display: none; }
.drawer {
  position: fixed;
  top: 0; right: 0; bottom: 0;
  width: min(420px, 92vw);
  background: #fff;
  z-index: 61;
  padding: 18px;
  overflow-y: auto;
  box-shadow: -18px 0 48px rgba(15, 23, 42, 0.18);
}
.drawer[hidden] { display: none; }

.modal-backdrop { display: flex; align-items: center; justify-content: center; }
.modal { width: min(640px, 92vw); max-height: 80vh; overflow-y: auto; background: #fff; border-radius: 14px; padding: 18px; }
.errors-host { display: grid; gap: 8px; }
.error-item { border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 12px; }
.error-text { color: var(--danger); margin-top: 4px; word-break: break-word; }

.lightbox { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.82); display: flex; align-items: center; justify-content: center; z-index: 70; }
.lightbox[hidden] { display: none; }
.lightbox-img { max-width: 86vw; max-height: 86vh; border-radius: 10px; box-shadow: 0 24px 60px rgba(0, 0, 0, 0.4); }
.lightbox-close { position: absolute; top: 18px; right: 22px; width: auto; background: transparent; box-shadow: none; font-size: 30px; line-height: 1; color: #fff; }
.lightbox-nav { position: absolute; top: 50%; transform: translateY(-50%); width: auto; background: rgba(255, 255, 255, 0.15); box-shadow: none; font-size: 28px; padding: 6px 14px; }
.lightbox-prev { left: 18px; }
.lightbox-next { right: 18px; }
.lightbox-close:hover { transform: none; box-shadow: none; background: rgba(255, 255, 255, 0.28); }
.lightbox-nav:hover { transform: translateY(-50%); box-shadow: none; background: rgba(255, 255, 255, 0.28); }

@media (max-width: 860px) {
  .evaluation-layout { grid-template-columns: 1fr; }
  .review-toolbar { position: static; }
  .results-table-wrap { max-height: none; }
}
```

---

### Task 6: 重写 `web/assets/evaluations.js`

**Files:**
- Rewrite: `web/assets/evaluations.js`

设计要点（实现见下方完整代码）：
- `state.rows` 是校验数据的唯一事实来源：所有编辑（下拉、备注、批量、快捷键）先写入 `state.rows[i]` 并加入 `state.dirty`，保存时只提交 dirty 行。
- 批量操作作用于 `state.selectedRows` 全集（含被过滤隐藏的行）——旧版只改可见行的 DOM，是已知缺陷，此处修复。
- 快捷键在 lightbox 打开时同样生效（作用于打开图片的那一行，不自动跳行）；表格内打标后自动跳到当前过滤器下的下一行。
- `loadDetail` 有 stale-response 守卫（快速切换 run 时丢弃过期响应，对应既有 commit f76d47f 修过的问题，不能回退）。
- 对比视图缓存 run 详情；保存校验/归档后删除对应缓存。

- [ ] **Step 1: Replace the file with the following complete content**

```javascript
// ---------- generic helpers ----------
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function firstImageUrl(image) {
  return String(image || "").split("|").map((s) => s.trim()).filter(Boolean)[0] || "";
}

// ---------- correctness helpers (mirror backend image_model_evaluation.py) ----------
function isCategoryCorrect(row) {
  return String(row.genreId ?? "").trim() === String(row.aiCategory ?? "").trim();
}
function normalizeBrand(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .replace(/[®™©]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]/gu, "");
}
function isBrandCorrect(row) {
  const expected = normalizeBrand(row.brand);
  const actual = normalizeBrand(row.aiBrand);
  return Boolean(expected && actual && expected === actual);
}

// ---------- state ----------
const state = {
  runs: [],
  view: "run", // "run" | "compare"
  activeRunId: "",
  detail: null,
  rows: [],
  dirty: new Set(),        // row indices with unsaved review edits
  selectedRows: new Set(),
  expandedRows: new Set(),
  focusIndex: -1,          // original row index, not visible position
  filter: "all",
  poller: null,
  lightbox: { urls: [], index: 0, rowIndex: -1 },
  compareIds: new Set(),
  compareDetails: {},
};
let configDefaults = {};

const el = (id) => document.getElementById(id);

// ---------- messages / api ----------
function showMessage(text, type, host) {
  const node = host || el("message");
  node.textContent = text;
  node.className = `message show ${type}`;
}
function clearMessage(host) {
  const node = host || el("message");
  node.className = "message";
  node.textContent = "";
}

async function api(url, options) {
  const resp = await fetch(url, options);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || "请求失败");
  return data;
}

async function loadConfigDefaults() {
  try {
    const resp = await fetch("/api/v1/config");
    if (resp.ok) configDefaults = await resp.json();
  } catch (err) { /* fall back to placeholders */ }
}

// ---------- formatters ----------
function fmtPct(value) {
  const num = Number(value);
  if (value === null || value === undefined || value === "" || !Number.isFinite(num)) return "-";
  return `${Math.round(num * 1000) / 10}%`;
}
function fmtDur(value) {
  if (value === null || value === undefined || value === "") return "--";
  const num = Number(value);
  if (!Number.isFinite(num) || num < 0) return "--";
  return `${Math.round(num * 10) / 10}s`;
}

function isArchivedActive() {
  const d = state.detail || {};
  return ((d.status || {}).status === "archived") || Boolean((d.run || {}).archived);
}

// ---------- sidebar ----------
function renderRunList() {
  const host = el("run-list");
  if (!state.runs.length) { host.textContent = "暂无测试记录"; return; }
  host.innerHTML = state.runs.map((run) => {
    const active = run.runId === state.activeRunId && state.view === "run" ? " active" : "";
    const status = run.status || (run.archived ? "archived" : "pending");
    return (
      `<div class="run-item${active}" data-run-id="${escapeHtml(run.runId)}">` +
      `<div class="run-id">${escapeHtml(run.runId)}</div>` +
      `<div class="run-meta">${escapeHtml(status)} · ${escapeHtml(run.reasoningEffort || "none")}</div>` +
      `<div class="run-meta">${escapeHtml(run.visionModel || "")}</div>` +
      `<button class="run-clone" type="button" data-clone-id="${escapeHtml(run.runId)}">复用配置</button>` +
      `</div>`
    );
  }).join("");
  host.querySelectorAll("[data-run-id]").forEach((node) => {
    node.addEventListener("click", () => selectRun(node.getAttribute("data-run-id")));
  });
  host.querySelectorAll("[data-clone-id]").forEach((node) => {
    node.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const run = state.runs.find((r) => r.runId === node.getAttribute("data-clone-id"));
      openDrawer(run || null);
    });
  });
}

async function refreshRuns() {
  const data = await api("/api/v1/evaluations");
  state.runs = data.runs || [];
  renderRunList();
}

// ---------- views ----------
function setView(view) {
  state.view = view;
  el("view-run").classList.toggle("active", view === "run");
  el("view-compare").classList.toggle("active", view === "compare");
  el("compare-toggle-btn").classList.toggle("on", view === "compare");
  renderRunList();
  if (view === "compare") enterCompare();
}

async function selectRun(runId) {
  if (state.dirty.size && !window.confirm("有未保存的校验修改，切换后将丢失，确认切换？")) return;
  state.dirty = new Set();
  state.activeRunId = runId;
  state.rows = [];
  state.filter = "all";
  state.selectedRows = new Set();
  state.expandedRows = new Set();
  state.focusIndex = -1;
  state.detail = null; // avoid flashing the previous run's metrics while loading
  setView("run");
  renderRunDetail();
  el("results-host").textContent = "正在读取结果...";
  try {
    await loadDetail(runId);
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

// ---------- run detail ----------
function ensurePolling(status) {
  const running = status === "pending" || status === "running";
  if (running && !state.poller) {
    state.poller = window.setInterval(() => {
      if (state.activeRunId) {
        loadDetail(state.activeRunId).catch(() => {});
        refreshRuns().catch(() => {});
      }
    }, 2500);
  }
  if (!running && state.poller) {
    window.clearInterval(state.poller);
    state.poller = null;
  }
}

async function loadDetail(runId) {
  const detail = await api(`/api/v1/evaluations/${encodeURIComponent(runId)}`);
  if (runId !== state.activeRunId) return; // stale response after a quick run switch
  state.detail = detail;
  renderRunDetail();
  const status = detail.status && detail.status.status;
  ensurePolling(status);
  if (status === "completed" || status === "archived") {
    await loadResults(runId);
  } else {
    state.rows = [];
  }
}

async function loadResults(runId) {
  const data = await api(`/api/v1/evaluations/${encodeURIComponent(runId)}/results`);
  if (runId !== state.activeRunId) return;
  if (!state.dirty.size) state.rows = data.rows || []; // never clobber unsaved edits
  renderReview();
}

function renderRunDetail() {
  const detail = state.detail || {};
  const run = detail.run || {};
  const status = detail.status || {};
  const st = status.status || "idle";
  const isArchived = st === "archived" || Boolean(run.archived);
  const isComplete = st === "completed" || isArchived;
  const isRunning = st === "pending" || st === "running";

  el("run-title").textContent = state.activeRunId || "未选择测试";
  const pill = el("run-status");
  pill.textContent = st;
  pill.className = `pill ${st}`;

  el("run-meta").textContent = state.activeRunId
    ? `${run.visionModel || "-"} · ${run.categoryModel || "-"} · ${run.productDataModel || "-"}` +
      ` · effort: ${run.reasoningEffort || "none"} · ${run.language || "ja"}` +
      (Number(run.limit) > 0 ? ` · 限 ${run.limit} 条` : "")
    : "从左侧选择一条测试记录，或点击「＋ 新建测试」。";

  el("reuse-btn").disabled = !state.activeRunId;
  const exportTop = el("export-link-top");
  exportTop.hidden = !isComplete;
  exportTop.href = state.activeRunId
    ? `/api/v1/evaluations/${encodeURIComponent(state.activeRunId)}/results.csv`
    : "#";
  el("archive-btn").hidden = !isComplete || isArchived;

  const progress = el("progress-block");
  progress.hidden = !isRunning;
  if (isRunning) {
    const done = Number(status.completed || 0);
    const total = Number(status.total || 0);
    el("progress-num").textContent = `${done} / ${total}`;
    el("progress-fill").style.width = total ? `${Math.round((done / total) * 100)}%` : "0%";
    const eta = Number(status.etaSeconds || 0);
    el("progress-meta").textContent =
      `失败 ${status.failed || 0} · ${eta > 0 ? `预计剩余 ${Math.round(eta)}s` : "计算中..."} · ${status.message || ""}`;
  }

  renderStats(isComplete);
  el("review-card").hidden = !isComplete;
}

function renderStats(isComplete) {
  const host = el("stat-row");
  host.hidden = !isComplete;
  if (!isComplete) { host.innerHTML = ""; return; }
  const detail = state.detail || {};
  const status = detail.status || {};
  const overall = (detail.summary && detail.summary.overall) || {};
  const pendingCat = Number(overall.categoryPendingReview || 0);
  const pendingBrand = Number(overall.brandPendingReview || 0);
  const failed = Number(status.failed || 0);
  const cards = [
    { label: "分类准确率", value: fmtPct(overall.categoryAccuracy), sub: `复核后 ${fmtPct(overall.categoryReviewedAccuracy)}`, cls: "" },
    { label: "品牌准确率", value: fmtPct(overall.brandAccuracy), sub: `复核后 ${fmtPct(overall.brandReviewedAccuracy)}`, cls: "" },
    { label: "平均耗时 / 条", value: fmtDur(overall.avgTotalDurationS), sub: `分类 ${fmtDur(overall.avgCategoryDurationS)} · 商品数据 ${fmtDur(overall.avgProductDataDurationS)}`, cls: "" },
    { label: "待校验", value: String(pendingCat + pendingBrand), sub: `分类 ${pendingCat} · 品牌 ${pendingBrand}`, cls: pendingCat + pendingBrand > 0 ? "stat-warn" : "" },
    { label: "失败", value: String(failed), sub: `共 ${status.total || 0} 条`, cls: failed > 0 ? "stat-bad stat-click" : "" },
  ];
  host.innerHTML = cards.map((c, i) =>
    `<div class="stat ${c.cls}"${i === 4 && failed > 0 ? ' id="stat-failed" role="button" tabindex="0"' : ""}>` +
    `<div class="stat-label">${escapeHtml(c.label)}</div>` +
    `<div class="stat-value">${escapeHtml(c.value)}</div>` +
    `<div class="stat-sub">${escapeHtml(c.sub)}</div>` +
    `</div>`
  ).join("");
  const failedCard = el("stat-failed");
  if (failedCard) failedCard.addEventListener("click", openErrors);
}

async function openErrors() {
  try {
    const data = await api(`/api/v1/evaluations/${encodeURIComponent(state.activeRunId)}/errors`);
    const errors = data.errors || [];
    el("errors-host").innerHTML = errors.length
      ? errors.map((e) =>
          `<div class="error-item"><b>#${escapeHtml(String(e.caseIndex ?? "?"))}</b> ${escapeHtml(e.itemName || "")}` +
          `<div class="error-text">${escapeHtml(e.error || "")}</div></div>`
        ).join("")
      : "<div class='hint'>无失败明细。</div>";
    el("errors-backdrop").hidden = false;
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

// ---------- review ----------
function rowMatchesFilter(row, filter) {
  switch (filter) {
    case "categoryWrong": return !isCategoryCorrect(row);
    case "brandWrong": return !isBrandCorrect(row);
    case "pending":
      return (!isCategoryCorrect(row) && !String(row.customerCategoryCheck || "").trim())
          || (!isBrandCorrect(row) && !String(row.customerBrandCheck || "").trim());
    default: return true;
  }
}

function visibleIndices() {
  const out = [];
  state.rows.forEach((row, i) => { if (rowMatchesFilter(row, state.filter)) out.push(i); });
  return out;
}

function renderFilterBar() {
  const rows = state.rows;
  const counts = {
    all: rows.length,
    categoryWrong: rows.filter((r) => !isCategoryCorrect(r)).length,
    brandWrong: rows.filter((r) => !isBrandCorrect(r)).length,
    pending: rows.filter((r) => rowMatchesFilter(r, "pending")).length,
  };
  const chips = [
    ["all", "全部", counts.all, "chip-all"],
    ["categoryWrong", "分类错", counts.categoryWrong, "chip-bad"],
    ["brandWrong", "品牌错", counts.brandWrong, "chip-bad"],
    ["pending", "待校验", counts.pending, "chip-pending"],
  ];
  const host = el("filter-bar");
  host.innerHTML = rows.length
    ? chips.map(([key, label, n, cls]) =>
        `<button class="chip ${cls}${state.filter === key ? " active" : ""}" data-filter="${key}" type="button">${label} ${n}</button>`
      ).join("")
    : "";
  host.querySelectorAll("[data-filter]").forEach((node) => {
    node.addEventListener("click", () => {
      state.filter = node.getAttribute("data-filter");
      state.focusIndex = -1;
      renderReview();
    });
  });
}

function renderDirtyBar() {
  const n = state.dirty.size;
  const span = el("dirty-count");
  span.hidden = n === 0;
  span.textContent = `未保存 ${n} 条修改`;
  el("save-review-btn").disabled = isArchivedActive() || n === 0;
  const link = el("export-link");
  link.hidden = !state.rows.length;
  link.href = state.activeRunId
    ? `/api/v1/evaluations/${encodeURIComponent(state.activeRunId)}/results.csv`
    : "#";
}

function reviewSelect(value, rowIndex, key, disabled) {
  const current = String(value || "");
  const options = [["", "待校验"], ["OK", "正确"], ["ACCEPTABLE", "可接受"], ["NG", "错误"]];
  return `<select data-row="${rowIndex}" data-review-key="${key}"${disabled ? " disabled" : ""}>` +
    options.map(([val, label]) => `<option value="${val}"${current === val ? " selected" : ""}>${label}</option>`).join("") +
    `</select>`;
}

function renderReview() {
  renderFilterBar();
  renderDirtyBar();
  const host = el("results-host");
  const archived = isArchivedActive();
  el("batch-actions").style.display = archived ? "none" : "";
  if (!state.rows.length) { host.textContent = "暂无结果。"; return; }
  const vis = visibleIndices();
  if (!vis.length) { host.innerHTML = "<div class='hint'>当前筛选无匹配条目。</div>"; return; }

  const dis = archived;
  const body = vis.map((i) => {
    const row = state.rows[i];
    const catCls = isCategoryCorrect(row) ? "ok" : "bad";
    const brandCls = isBrandCorrect(row) ? "ok" : (String(row.aiBrand || "").trim() ? "warn" : "bad");
    const classes = [];
    if (!isCategoryCorrect(row) && !isBrandCorrect(row)) classes.push("row-bad");
    if (i === state.focusIndex) classes.push("row-focus");
    const url = firstImageUrl(row.image);
    const thumb = url
      ? `<img class="thumb" src="${escapeHtml(url)}" loading="lazy" alt="" data-full="${escapeHtml(row.image)}" data-thumb-row="${i}" />`
      : `<span class="thumb thumb-empty">无图</span>`;
    let html =
      `<tr${classes.length ? ` class="${classes.join(" ")}"` : ""} data-row-index="${i}">` +
      `<td><input type="checkbox" class="row-select" data-row="${i}" ${state.selectedRows.has(i) ? "checked" : ""}${dis ? " disabled" : ""} /></td>` +
      `<td><button class="caret" type="button" data-expand="${i}">${state.expandedRows.has(i) ? "▾" : "▸"}</button></td>` +
      `<td>${thumb}</td>` +
      `<td class="clip">${escapeHtml(row.itemName || "")}</td>` +
      `<td class="diff ${catCls}"><span class="orig">${escapeHtml(row.genreId || "")}</span> → <span class="ai">${escapeHtml(row.aiCategory || "")}</span></td>` +
      `<td class="diff ${brandCls}"><span class="orig">${escapeHtml(row.brand || "")}</span> → <span class="ai">${escapeHtml(row.aiBrand || "")}</span></td>` +
      `<td>${reviewSelect(row.customerCategoryCheck, i, "customerCategoryCheck", dis)}</td>` +
      `<td>${reviewSelect(row.customerBrandCheck, i, "customerBrandCheck", dis)}</td>` +
      `<td><textarea data-row="${i}" data-review-key="customerNotes"${dis ? " disabled" : ""}>${escapeHtml(row.customerNotes || "")}</textarea></td>` +
      `</tr>`;
    if (state.expandedRows.has(i)) {
      html +=
        `<tr class="detail-row" data-detail-for="${i}"><td></td><td></td><td colspan="7">` +
        `<div class="detail-grid">` +
        `<div><span class="detail-label">AI 分类 Path</span>${escapeHtml(row.aiCategoryPath || "-")}</div>` +
        `<div><span class="detail-label">置信度</span>${escapeHtml(row.aiCategoryConfidence || "-")}</div>` +
        `<div><span class="detail-label">AI 标题</span>${escapeHtml(row.aiTitle || "-")}</div>` +
        `</div></td></tr>`;
    }
    return html;
  }).join("");

  host.innerHTML =
    `<div class="results-table-wrap"><table class="results-table"><thead><tr>` +
    `<th><input type="checkbox" id="select-all"${dis ? " disabled" : ""} /></th>` +
    `<th></th><th>图片</th><th>商品</th><th>分类 (原→AI)</th><th>品牌 (原→AI)</th>` +
    `<th>分类校验</th><th>品牌校验</th><th>备注</th>` +
    `</tr></thead><tbody>${body}</tbody></table></div>`;

  bindReviewEvents(host);
  updateBatchCount();
}

function bindReviewEvents(host) {
  host.querySelectorAll("img.thumb").forEach((img) => {
    img.addEventListener("click", () => {
      openLightbox(img.getAttribute("data-full"), 0, Number(img.getAttribute("data-thumb-row")));
    });
  });
  host.querySelectorAll("[data-expand]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = Number(btn.getAttribute("data-expand"));
      if (state.expandedRows.has(i)) state.expandedRows.delete(i);
      else state.expandedRows.add(i);
      renderReview();
    });
  });
  host.querySelectorAll(".row-select").forEach((cb) => {
    cb.addEventListener("change", () => {
      const i = Number(cb.getAttribute("data-row"));
      if (cb.checked) state.selectedRows.add(i); else state.selectedRows.delete(i);
      updateBatchCount();
    });
  });
  const selectAll = el("select-all");
  if (selectAll) selectAll.addEventListener("change", () => {
    host.querySelectorAll(".row-select").forEach((cb) => {
      cb.checked = selectAll.checked;
      const i = Number(cb.getAttribute("data-row"));
      if (selectAll.checked) state.selectedRows.add(i); else state.selectedRows.delete(i);
    });
    updateBatchCount();
  });
  host.querySelectorAll("select[data-review-key], textarea[data-review-key]").forEach((node) => {
    node.addEventListener("change", () => {
      markEdit(Number(node.getAttribute("data-row")), node.getAttribute("data-review-key"), node.value);
    });
  });
  host.querySelectorAll("tbody tr[data-row-index]").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      const tag = (ev.target.tagName || "").toLowerCase();
      if (["input", "select", "textarea", "button", "img", "a", "option"].includes(tag)) return;
      state.focusIndex = Number(tr.getAttribute("data-row-index"));
      refreshFocusClass();
    });
  });
}

function markEdit(i, key, value) {
  state.rows[i][key] = value;
  state.dirty.add(i);
  renderDirtyBar();
  renderFilterBar(); // pending counts may have changed
}

function updateBatchCount() {
  el("batch-count").textContent = `已选 ${state.selectedRows.size}`;
  const selectAll = el("select-all");
  if (!selectAll) return;
  const boxes = el("results-host").querySelectorAll(".row-select");
  const checked = [...boxes].filter((cb) => cb.checked).length;
  selectAll.checked = boxes.length > 0 && checked === boxes.length;
  selectAll.indeterminate = checked > 0 && checked < boxes.length;
}

function applyBatch(verdict) {
  if (isArchivedActive() || !state.selectedRows.size) return;
  const field = el("batch-field").value;
  state.selectedRows.forEach((i) => {
    state.rows[i][field] = verdict;
    state.dirty.add(i);
  });
  renderReview();
}

function refreshFocusClass() {
  el("results-host").querySelectorAll("tr[data-row-index]").forEach((tr) => {
    tr.classList.toggle("row-focus", Number(tr.getAttribute("data-row-index")) === state.focusIndex);
  });
}

function scrollFocusIntoView() {
  const tr = el("results-host").querySelector(`tr[data-row-index="${state.focusIndex}"]`);
  if (tr) tr.scrollIntoView({ block: "nearest" });
}

function applyVerdictToFocused(field, value) {
  if (state.focusIndex < 0 || isArchivedActive()) return;
  const prevVis = visibleIndices();
  const prevPos = prevVis.indexOf(state.focusIndex);
  state.rows[state.focusIndex][field] = value;
  state.dirty.add(state.focusIndex);
  // Auto-advance within the current filter. If the row just left the filter
  // (e.g. marked while on the pending chip), focus whatever took its place.
  const vis = visibleIndices();
  if (!vis.length) state.focusIndex = -1;
  else if (vis.includes(state.focusIndex)) {
    const p = vis.indexOf(state.focusIndex);
    state.focusIndex = vis[Math.min(p + 1, vis.length - 1)];
  } else {
    state.focusIndex = vis[Math.min(Math.max(prevPos, 0), vis.length - 1)];
  }
  renderReview();
  scrollFocusIntoView();
}

async function saveReview() {
  if (!state.dirty.size) return;
  const updates = [...state.dirty].map((i) => ({
    rowIndex: i,
    customerCategoryCheck: state.rows[i].customerCategoryCheck || "",
    customerBrandCheck: state.rows[i].customerBrandCheck || "",
    customerNotes: state.rows[i].customerNotes || "",
  }));
  const btn = el("save-review-btn");
  btn.disabled = true;
  btn.textContent = "保存中...";
  try {
    await api(`/api/v1/evaluations/${encodeURIComponent(state.activeRunId)}/review`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows: updates }),
    });
    state.dirty = new Set();
    delete state.compareDetails[state.activeRunId]; // summary changed, drop stale cache
    showMessage("校验已保存，统计已刷新。", "success");
    await loadDetail(state.activeRunId);
  } catch (err) {
    showMessage(String(err.message || err), "error");
  } finally {
    btn.textContent = "保存校验";
    renderDirtyBar();
  }
}

async function archiveRun() {
  if (!state.activeRunId || !window.confirm("归档后将锁定校验，确认归档？")) return;
  try {
    await api(`/api/v1/evaluations/${encodeURIComponent(state.activeRunId)}/archive`, { method: "POST" });
    delete state.compareDetails[state.activeRunId];
    showMessage("测试已归档。", "success");
    await refreshRuns();
    await loadDetail(state.activeRunId);
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
}

// ---------- drawer ----------
function openDrawer(prefillRun) {
  const src = prefillRun
    || state.runs.find((r) => r.runId === state.activeRunId)
    || state.runs[0]
    || {};
  el("f-vision").value = src.visionModel || configDefaults.VISION_MODEL || "openai/gpt-4o-mini";
  el("f-category").value = src.categoryModel || configDefaults.CATEGORY_MODEL || "openai/gpt-4o-mini";
  el("f-product").value = src.productDataModel || configDefaults.PRODUCT_DATA_MODEL || "openai/gpt-4o-mini";
  el("f-reasoning").value = src.reasoningEffort || "none";
  el("f-language").value = src.language || "ja";
  el("f-limit").value = String(src.limit ?? 0);
  clearMessage(el("drawer-message"));
  el("drawer-backdrop").hidden = false;
  el("drawer").hidden = false;
}

function closeDrawer() {
  el("drawer").hidden = true;
  el("drawer-backdrop").hidden = true;
}

async function createRun() {
  const fileInput = el("f-file");
  if (!fileInput.files || !fileInput.files[0]) {
    showMessage("请先选择测试数据文件。", "error", el("drawer-message"));
    return;
  }
  const form = new FormData();
  form.append("file", fileInput.files[0]);
  form.append("visionModel", el("f-vision").value.trim());
  form.append("categoryModel", el("f-category").value.trim());
  form.append("productDataModel", el("f-product").value.trim());
  form.append("reasoningEffort", el("f-reasoning").value);
  form.append("language", el("f-language").value);
  form.append("limit", String(Number(el("f-limit").value || 0)));
  const btn = el("create-btn");
  btn.disabled = true;
  btn.textContent = "提交中...";
  try {
    const data = await api("/api/v1/evaluations", { method: "POST", body: form });
    closeDrawer();
    fileInput.value = "";
    showMessage(`已创建测试：${data.runId}`, "success");
    await refreshRuns();
    await selectRun(data.runId);
  } catch (err) {
    showMessage(String(err.message || err), "error", el("drawer-message"));
  } finally {
    btn.disabled = false;
    btn.textContent = "开始测试";
  }
}

// ---------- compare ----------
function completedRunIds() {
  return state.runs.filter((r) => {
    const st = r.status || (r.archived ? "archived" : "");
    return st === "completed" || st === "archived";
  }).map((r) => r.runId);
}

async function enterCompare() {
  if (!state.compareIds.size) {
    completedRunIds().slice(0, 2).forEach((id) => state.compareIds.add(id));
  }
  renderComparePicker();
  await Promise.all([...state.compareIds].map(ensureCompareDetail));
  renderCompareTable();
}

function renderComparePicker() {
  const ids = completedRunIds();
  const host = el("compare-picker");
  host.innerHTML = ids.length
    ? ids.map((id) =>
        `<button class="compare-pill${state.compareIds.has(id) ? " on" : ""}" data-compare-id="${escapeHtml(id)}" type="button">${escapeHtml(id)}</button>`
      ).join("")
    : "<span class='hint'>暂无已完成的测试。</span>";
  host.querySelectorAll("[data-compare-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-compare-id");
      if (state.compareIds.has(id)) state.compareIds.delete(id);
      else { state.compareIds.add(id); await ensureCompareDetail(id); }
      renderComparePicker();
      renderCompareTable();
    });
  });
}

async function ensureCompareDetail(id) {
  // Cached for the session; failures are NOT cached so a later click retries.
  if (state.compareDetails[id]) return;
  try {
    state.compareDetails[id] = await api(`/api/v1/evaluations/${encodeURIComponent(id)}`);
  } catch (err) {
    showMessage(`读取 ${id} 详情失败：${err.message || err}`, "error");
  }
}

const COMPARE_SECTIONS = [
  { title: "配置（蓝色 = 不同）", kind: "config", rows: [
    ["图片识别模型", (d) => (d.run || {}).visionModel || "-"],
    ["分类模型", (d) => (d.run || {}).categoryModel || "-"],
    ["商品数据模型", (d) => (d.run || {}).productDataModel || "-"],
    ["推理程度", (d) => (d.run || {}).reasoningEffort || "none"],
    ["语言", (d) => (d.run || {}).language || "-"],
    ["数据量", (d) => String(((d.summary || {}).overall || {}).total ?? (d.status || {}).total ?? "-")],
  ]},
  { title: "质量（绿色 = 最优）", kind: "metric", rows: [
    ["分类准确率", (o) => o.categoryAccuracy, "pct", "max"],
    ["品牌准确率", (o) => o.brandAccuracy, "pct", "max"],
    ["复核后分类", (o) => o.categoryReviewedAccuracy, "pct", "max"],
    ["复核后品牌", (o) => o.brandReviewedAccuracy, "pct", "max"],
    ["待分类校验", (o) => o.categoryPendingReview, "num", "min"],
    ["待品牌校验", (o) => o.brandPendingReview, "num", "min"],
  ]},
  { title: "耗时（绿色 = 最快）", kind: "metric", rows: [
    ["平均耗时 / 条", (o) => o.avgTotalDurationS, "dur", "min"],
    ["分类阶段", (o) => o.avgCategoryDurationS, "dur", "min"],
    ["商品数据阶段", (o) => o.avgProductDataDurationS, "dur", "min"],
  ]},
];

function renderCompareTable() {
  const ids = completedRunIds().filter((id) => state.compareIds.has(id));
  const host = el("compare-host");
  if (!ids.length) {
    host.innerHTML = "<div class='hint'>请选择至少一轮已完成的测试。</div>";
    return;
  }
  const details = ids.map((id) => state.compareDetails[id] || {});
  const header = `<tr><th>指标</th>${ids.map((id) => `<th>${escapeHtml(id)}</th>`).join("")}</tr>`;
  const sections = COMPARE_SECTIONS.map((section) => {
    const head = `<tr class="compare-section"><td colspan="${ids.length + 1}">${escapeHtml(section.title)}</td></tr>`;
    const rows = section.rows.map((rowDef) => {
      if (section.kind === "config") {
        const [label, get] = rowDef;
        const values = details.map((d) => get(d));
        const differs = values.length > 1 && !values.every((v) => v === values[0]);
        const cells = values.map((v) => `<td${differs ? ' class="compare-diff"' : ""}>${escapeHtml(String(v))}</td>`).join("");
        return `<tr><td class="compare-label">${escapeHtml(label)}</td>${cells}</tr>`;
      }
      const [label, get, fmt, dir] = rowDef;
      const overalls = details.map((d) => ((d.summary || {}).overall) || {});
      const vals = overalls.map((o) => {
        const raw = get(o);
        const num = Number(raw);
        return raw === null || raw === undefined || raw === "" || !Number.isFinite(num) ? null : num;
      });
      const present = vals.filter((v) => v !== null);
      const best = present.length ? (dir === "max" ? Math.max(...present) : Math.min(...present)) : null;
      const cells = vals.map((v) => {
        const text = v === null ? "-" : fmt === "pct" ? fmtPct(v) : fmt === "dur" ? fmtDur(v) : String(v);
        const isBest = best !== null && v === best && present.length > 1;
        return `<td${isBest ? ' class="compare-best"' : ""}>${text}</td>`;
      }).join("");
      return `<tr><td class="compare-label">${escapeHtml(label)}</td>${cells}</tr>`;
    }).join("");
    return head + rows;
  }).join("");
  host.innerHTML = `<div class="results-table-wrap"><table class="results-table compare-table"><thead>${header}</thead><tbody>${sections}</tbody></table></div>`;
}

// ---------- lightbox ----------
function openLightbox(image, startIndex, rowIndex) {
  const urls = String(image || "").split("|").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  state.lightbox = { urls, index: startIndex || 0, rowIndex: rowIndex ?? -1 };
  if (state.lightbox.rowIndex >= 0) {
    state.focusIndex = state.lightbox.rowIndex;
    refreshFocusClass();
  }
  renderLightbox();
  el("lightbox").hidden = false;
}

function renderLightbox() {
  const { urls, index } = state.lightbox;
  el("lightbox-img").src = urls[index] || "";
  el("lightbox-prev").style.visibility = urls.length > 1 ? "visible" : "hidden";
  el("lightbox-next").style.visibility = urls.length > 1 ? "visible" : "hidden";
}

function closeLightbox() {
  el("lightbox").hidden = true;
  state.lightbox = { urls: [], index: 0, rowIndex: -1 };
  renderReview(); // reflect any verdicts marked while the lightbox was open
}

function stepLightbox(delta) {
  const { urls, index } = state.lightbox;
  if (!urls.length) return;
  state.lightbox.index = (index + delta + urls.length) % urls.length;
  renderLightbox();
}

// ---------- keyboard ----------
const VERDICT_KEYS = {
  "1": ["customerCategoryCheck", "OK"],
  "2": ["customerCategoryCheck", "ACCEPTABLE"],
  "3": ["customerCategoryCheck", "NG"],
  "q": ["customerBrandCheck", "OK"],
  "w": ["customerBrandCheck", "ACCEPTABLE"],
  "e": ["customerBrandCheck", "NG"],
};

document.addEventListener("keydown", (ev) => {
  if (!el("lightbox").hidden) {
    if (ev.key === "Escape") closeLightbox();
    else if (ev.key === "ArrowLeft") stepLightbox(-1);
    else if (ev.key === "ArrowRight") stepLightbox(1);
    else if (VERDICT_KEYS[ev.key] && state.lightbox.rowIndex >= 0 && !isArchivedActive()) {
      // Mark the row whose images are on screen; stay on it (no auto-advance).
      const [field, value] = VERDICT_KEYS[ev.key];
      state.rows[state.lightbox.rowIndex][field] = value;
      state.dirty.add(state.lightbox.rowIndex);
      renderDirtyBar();
    }
    return;
  }
  if (!el("errors-backdrop").hidden) {
    if (ev.key === "Escape") el("errors-backdrop").hidden = true;
    return;
  }
  if (!el("drawer").hidden) {
    if (ev.key === "Escape") closeDrawer();
    return;
  }
  if (state.view !== "run" || !state.rows.length || el("review-card").hidden) return;
  const tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "textarea" || tag === "select" || tag === "input") return;
  const vis = visibleIndices();
  if (!vis.length) return;
  if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    ev.preventDefault();
    const pos = vis.indexOf(state.focusIndex);
    if (ev.key === "ArrowDown") state.focusIndex = vis[Math.min(pos < 0 ? 0 : pos + 1, vis.length - 1)];
    else state.focusIndex = vis[Math.max(pos < 0 ? vis.length - 1 : pos - 1, 0)];
    refreshFocusClass();
    scrollFocusIntoView();
  } else if (ev.key === " ") {
    if (state.focusIndex < 0 || isArchivedActive()) return;
    ev.preventDefault();
    if (state.selectedRows.has(state.focusIndex)) state.selectedRows.delete(state.focusIndex);
    else state.selectedRows.add(state.focusIndex);
    renderReview();
  } else if (VERDICT_KEYS[ev.key]) {
    applyVerdictToFocused(...VERDICT_KEYS[ev.key]);
  }
});

// ---------- wiring ----------
el("refresh-btn").addEventListener("click", () => {
  refreshRuns().catch((err) => showMessage(String(err.message || err), "error"));
});
el("new-run-btn").addEventListener("click", () => openDrawer(null));
el("compare-toggle-btn").addEventListener("click", () => {
  setView(state.view === "compare" ? "run" : "compare");
});
el("reuse-btn").addEventListener("click", () => {
  openDrawer(state.runs.find((r) => r.runId === state.activeRunId) || null);
});
el("archive-btn").addEventListener("click", archiveRun);
el("save-review-btn").addEventListener("click", saveReview);
document.querySelectorAll("#batch-actions .batch-btn").forEach((btn) => {
  btn.addEventListener("click", () => applyBatch(btn.getAttribute("data-batch")));
});
el("drawer-close-btn").addEventListener("click", closeDrawer);
el("drawer-backdrop").addEventListener("click", closeDrawer);
el("create-btn").addEventListener("click", createRun);
el("errors-close-btn").addEventListener("click", () => { el("errors-backdrop").hidden = true; });
el("errors-backdrop").addEventListener("click", (ev) => {
  if (ev.target === el("errors-backdrop")) el("errors-backdrop").hidden = true;
});
el("lightbox-close").addEventListener("click", closeLightbox);
el("lightbox-prev").addEventListener("click", () => stepLightbox(-1));
el("lightbox-next").addEventListener("click", () => stepLightbox(1));
el("lightbox").addEventListener("click", (ev) => { if (ev.target === el("lightbox")) closeLightbox(); });
window.addEventListener("beforeunload", (ev) => {
  if (state.dirty.size) { ev.preventDefault(); ev.returnValue = ""; }
});

// ---------- shell + init ----------
Shell.mount({
  page: "evaluations",
  defaultRoute: "evaluations",
  brand: { logo: "M", text: "Mercari 识别" },
  sidebar: () => [{ id: "evaluations", label: "模型测试" }],
  onRouteChange: () => {
    Shell.setHeader({ title: "模型测试", crumb: "" });
  },
});

(async function init() {
  await loadConfigDefaults();
  try {
    await refreshRuns();
    if (state.runs[0]) await selectRun(state.runs[0].runId);
  } catch (err) {
    showMessage(String(err.message || err), "error");
  }
})();
```

- [ ] **Step 2: Syntax check**

Run: `node --check web/assets/evaluations.js`
Expected: 无输出（语法合法）

- [ ] **Step 3: Commit the three frontend files together**

```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat(console): rebuild model testing page as single-page workbench

- Single run view: header + stats row (incl. stage durations) + review table
- New-run form moved into a slide-in drawer prefilled from last run
- Compare view: config diff / quality / duration sections with best highlight
- Review: dirty tracking with sticky save bar, Space select, auto-advance,
  verdict shortcuts work inside the lightbox, expandable detail rows
- Removed the analysis tab (backend removed separately)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: 全量验证

**Files:** 无新文件；如有缺陷在本任务内修复并补 commit。

- [ ] **Step 1: Run the full backend suite**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS

- [ ] **Step 2: Start the server**

Run（后台运行）: `API_PORT=8008 ./run.sh`
然后 `grep -i "LOGS_PASSWORD" .env 2>/dev/null`——若设置了密码，浏览器流程需先在 `/login` 用该密码登录。

- [ ] **Step 3: Playwright walkthrough**

用 Playwright 浏览器工具访问 `http://localhost:8008/evaluations`，逐项核对：

1. **侧栏**：run 列表渲染；底部「＋ 新建测试」「⇄ 多轮对比」两个按钮；点 run 切换详情。
2. **测试台（已完成 run）**：头部 run ID + 状态 pill + 配置 meta；统计行 5 张卡（分类/品牌准确率含复核后副行、平均耗时含分类/商品数据副行、待校验、失败）；旧 run 无耗时数据时显示 `--`。
3. **校验区**：过滤 chips 计数正确；表格 9 列；点 ▸ 展开行内详情（Path/置信度/AI标题）；改一个下拉 → sticky 条出现「未保存 1 条修改」、保存按钮激活；保存后统计刷新、dirty 清零。
4. **键盘**：↑↓ 移动焦点行；Space 勾选；按 `1` 后焦点自动跳下一行；点缩略图开 lightbox 后按 `q` 仍生效，Esc 关闭后表格已反映该标注。
5. **未保存守卫**：有 dirty 时切换 run 弹确认。
6. **导出**：校验区与头部的「导出 CSV」均指向 `/results.csv` 并可下载。
7. **抽屉**：「＋ 新建测试」滑出抽屉且预填上一轮配置；run 条目上的「复用配置」预填对应 run；Esc/遮罩/× 均可关闭。（如不想真实跑模型，验证表单校验：不选文件直接提交 → 抽屉内报错。）
8. **多轮对比**：进入时预选最近两轮；表格三段（配置/质量/耗时）；不同模型的配置格蓝色，最优指标绿色；取消选择至 0 轮时显示提示。
9. **运行中状态**（若条件允许，用一份 2-3 行的小数据真实建一轮）：进度块显示 N/M 与进度条，完成后自动切到校验态。
10. **失败卡**（如有带 errors.jsonl 的 run）：失败卡红色可点，弹层列出错误。
11. **archived run**：下拉禁用、批量区隐藏、保存不可用、导出仍可用。
12. 浏览器 console 无报错；截图存档。

- [ ] **Step 4: Fix anything broken, then commit fixes**

发现问题就地修复，每个独立问题一个 commit（`fix(console): ...`），修完重跑 Step 1 与受影响的浏览器步骤。

- [ ] **Step 5: Final check**

Run: `git log --oneline -8 && git status`
Expected: Task 1-6 的 5 个 commit + 可能的 fix commits；工作区无本计划相关的未提交文件。

---

## Self-Review 记录

- Spec 覆盖：布局（Task 4-6）、统计含耗时（Task 1 + renderStats）、对比三段表（COMPARE_SECTIONS）、导出入口（export-link / export-link-top）、分析删除（Task 3）、errors 弹层（Task 2 + openErrors）、边界（`--` 显示、archived 只读、stale-response 守卫、unsaved 守卫）均有对应任务。
- 类型一致性：`avgTotalDurationS / avgCategoryDurationS / avgProductDataDurationS` 在后端返回、`renderStats`、`COMPARE_SECTIONS` 三处拼写一致；`read_errors(run_id, limit=20)` 与路由、测试一致；HTML id 与 JS `el()` 引用一一核对。
- 已知取舍：勾选集合 `selectedRows` 在批量操作时作用于全集（含被过滤行），与旧版「只改可见行」不同——这是修复而非回归，已在 Task 6 说明。
