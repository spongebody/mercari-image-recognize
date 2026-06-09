# 模型测试 (Model Testing) UI/Flow Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the `/evaluations` ("模型测试") page into a phased-tab workflow (发起 → 监控 → 校验 → 分析 + 多轮对比) with image thumbnails, config reuse, multi-run comparison, and a polished review experience — frontend only, no backend changes.

**Architecture:** Extract the current single-file `web/evaluations.html` (810 lines, inline CSS+JS) into three focused files following the existing `shell.css`/`shell.js` shared-asset pattern: `evaluations.html` (markup + tab shells), `web/assets/evaluations.css` (styles), `web/assets/evaluations.js` (state + per-tab render logic). All new features reuse existing API endpoints unchanged.

**Tech Stack:** Vanilla JS (no framework/build step), CSS with custom properties, FastAPI backend (untouched). Verification via the Playwright MCP browser tools against a locally-run server with seeded runs in `logs/image_model_tests/`.

---

## Design reference

Spec: `docs/superpowers/specs/2026-06-09-model-testing-ui-redesign-design.md`

### Existing API (all reused as-is, no changes)
- `GET /api/v1/config` → model defaults
- `POST /api/v1/evaluations` (multipart) → create run
- `GET /api/v1/evaluations` → `{runs:[{runId,status,visionModel,categoryModel,productDataModel,reasoningEffort,language,createdAt,archived,...}]}`
- `GET /api/v1/evaluations/{id}` → `{run, status, summary:{overall,byModel}, analysis}`
- `GET /api/v1/evaluations/{id}/results` → `{rows:[...]}` (19 RESULT_FIELDS incl. `image`, `genreId`, `aiCategory`, `brand`, `aiBrand`, `customerCategoryCheck`, ...)
- `GET /api/v1/evaluations/{id}/results.csv`
- `PUT /api/v1/evaluations/{id}/review` (body `{rows:[{rowIndex,customerCategoryCheck,customerBrandCheck,customerNotes}]}`)
- `PUT /api/v1/evaluations/{id}/analysis` (body `{analysisNotes,optimizationActions,nextRunSuggestion}`)
- `POST /api/v1/evaluations/{id}/archive`

### Per-row correctness semantics (mirror backend exactly — `app/evaluation/image_model_evaluation.py:284-296`)
JS helpers to add (used by review filter + diff coloring):
```js
// mirrors _clean(genreId) === _clean(aiCategory)
function isCategoryCorrect(row) {
  return String(row.genreId ?? "").trim() === String(row.aiCategory ?? "").trim();
}
// mirrors normalize_brand_for_compare: NFKC, strip ®™©, lowercase, keep only letters/digits
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
// mirrors _review_check_is_positive
function reviewIsPositive(value) {
  return ["OK", "ACCEPTABLE"].includes(String(value ?? "").trim().toUpperCase());
}
```

### How to run + auth (for every verification step)
The page redirects to `/login` unless `LOGS_PASSWORD` is set and a valid session cookie is present. Start the server with a known password:
```bash
cd /Users/youbo/gala/labs/mercari-image-recognize
LOGS_USER=admin LOGS_PASSWORD=devpass uv run uvicorn main:app --host 127.0.0.1 --port 8000
# (or: source .venv/bin/activate && LOGS_USER=admin LOGS_PASSWORD=devpass uvicorn main:app --port 8000)
```
Browser-verify recipe (Playwright MCP tools) used in each task:
1. `browser_navigate` → `http://127.0.0.1:8000/login`
2. `browser_fill_form` username=`admin`, password=`devpass`; submit (or `browser_evaluate` a `fetch("/api/v1/console/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:"admin",password:"devpass"})})` then navigate).
3. `browser_navigate` → `http://127.0.0.1:8000/evaluations`
4. `browser_snapshot` / `browser_take_screenshot` to confirm expectations.

Seeded completed runs already exist (e.g. `2026-06-02-09-30`) so 校验/对比 tabs have real data without running inference.

---

## File Structure

- **Create** `web/assets/evaluations.css` — all page styles (lifted from inline `<style>`), extended with tab + review-state + thumbnail + compare styles.
- **Create** `web/assets/evaluations.js` — all page logic (lifted from inline `<script>`), reorganized into sections: state, helpers, setup, monitor, review, analysis, compare, init.
- **Modify** `web/evaluations.html` — reduce to `<head>` (links to the two assets + shell.css), tab nav, and per-tab panel containers; remove inline `<style>`/`<script>` bodies.

No other files change. Backend and Python tests untouched.

---

### Task 1: Extract inline CSS/JS into shared asset files (pure refactor, zero behavior change)

**Files:**
- Create: `web/assets/evaluations.css`
- Create: `web/assets/evaluations.js`
- Modify: `web/evaluations.html`

- [ ] **Step 1: Create `web/assets/evaluations.css`**

Move the entire contents of the current `<style>` block in `web/evaluations.html` (lines 7–316, i.e. everything between `<style>` and `</style>`, excluding those tags) verbatim into `web/assets/evaluations.css`. No edits to the rules in this task.

- [ ] **Step 2: Create `web/assets/evaluations.js`**

Move the entire contents of the current inline `<script>` block in `web/evaluations.html` (the second `<script>`, lines 426–807, excluding the `<script>`/`</script>` tags) verbatim into `web/assets/evaluations.js`. No edits to the logic in this task.

- [ ] **Step 3: Rewrite `web/evaluations.html` head + script refs**

Replace the inline `<style>...</style>` block with a stylesheet link, and replace the inline logic `<script>...</script>` with a `src` reference. Keep `shell.css` and `shell.js`. The `<head>` becomes:
```html
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>模型测试</title>
    <link rel="stylesheet" href="/assets/evaluations.css" />
    <link rel="stylesheet" href="/assets/shell.css" />
  </head>
```
Keep the `<body>` markup unchanged for now. At the end of `<body>`, keep `<script src="/assets/shell.js"></script>` then add `<script src="/assets/evaluations.js"></script>` (in that order — `evaluations.js` uses the global `Shell`).

- [ ] **Step 4: Verify assets are served**

Static assets under `/assets/` are already served (shell.css/shell.js load from there). Start the server (see "How to run" above) and confirm both new files return 200:
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/assets/evaluations.css
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/assets/evaluations.js
```
Expected: `200` for both.

- [ ] **Step 5: Browser-verify no behavior change**

Run the browser-verify recipe → `/evaluations`. `browser_snapshot` should show the SAME page as before: left upload form + 测试记录 list, right 状态/客服校验/分析与迭代 cards. Selecting a seeded run (e.g. `2026-06-02-09-30`) still loads metrics + results table. Confirm no console errors via `browser_console_messages`.

- [ ] **Step 6: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "refactor: extract evaluations page CSS/JS into shared assets"
```

---

### Task 2: Phased tab navigation + restructure sections into panels

**Files:**
- Modify: `web/evaluations.html`
- Modify: `web/assets/evaluations.css`
- Modify: `web/assets/evaluations.js`

- [ ] **Step 1: Add tab markup + panel containers in `web/evaluations.html`**

Replace the `.evaluation-layout` body so the left rail (run list) stays persistent and the right side becomes a tab bar + 5 panels. New right-side structure:
```html
        <main class="stack">
          <nav class="tabbar" id="evaluation-tabs">
            <button class="tab active" data-tab="setup">① 发起</button>
            <button class="tab" data-tab="monitor">② 监控</button>
            <button class="tab" data-tab="review">③ 校验</button>
            <button class="tab" data-tab="analysis">④ 分析</button>
            <button class="tab tab-compare" data-tab="compare">⇄ 多轮对比</button>
          </nav>

          <section class="tab-panel active" data-panel="setup"><!-- move 上传/模型/配置 card here --></section>
          <section class="tab-panel" data-panel="monitor"><!-- move 状态 card (title/status/meta/metrics/actions) here --></section>
          <section class="tab-panel" data-panel="review"><!-- move 客服校验 card here --></section>
          <section class="tab-panel" data-panel="analysis"><!-- move 分析与迭代 card here --></section>
          <section class="tab-panel" data-panel="compare"><!-- new: built in Task 7 --></section>
        </main>
```
Move the existing four cards (upload/form, status+metrics, review, analysis) into the matching `setup`/`monitor`/`review`/`analysis` panels respectively — keep all their inner markup and element IDs intact (the JS references them by ID). Move the left `<aside>` run-list card out of `setup` so it stays in the persistent left rail. The `compare` panel is an empty `<section>` placeholder for Task 7.

- [ ] **Step 2: Add tab styles to `web/assets/evaluations.css`**
```css
.tabbar { display:flex; gap:8px; flex-wrap:wrap; }
.tab {
  width:auto; padding:9px 16px; border-radius:10px; font-size:13px; font-weight:700;
  background:#fff; color:var(--muted); border:1px solid var(--border);
  box-shadow:none; cursor:pointer; transition:background .12s ease, color .12s ease, border-color .12s ease;
}
.tab:not(:disabled):hover { transform:none; box-shadow:none; border-color:var(--accent); }
.tab.active { background:var(--accent); color:#fff; border-color:var(--accent); }
.tab.tab-compare { margin-left:auto; border-style:dashed; }
.tab.tab-compare.active { border-style:solid; }
.tab-panel { display:none; }
.tab-panel.active { display:block; }
```
(The `.tab` rule overrides the global `button` width/gradient via higher specificity; verify visually.)

- [ ] **Step 3: Add tab-switching logic to `web/assets/evaluations.js`**

Add to `evaluationState`: `activeTab: "setup"`. Add near the other element refs and init:
```js
function setActiveTab(tab) {
  evaluationState.activeTab = tab;
  document.querySelectorAll("#evaluation-tabs .tab").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-tab") === tab);
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("active", panel.getAttribute("data-panel") === tab);
  });
  if (tab === "compare") renderCompare(); // defined in Task 7; guard below
}
document.querySelectorAll("#evaluation-tabs .tab").forEach((btn) => {
  btn.addEventListener("click", () => setActiveTab(btn.getAttribute("data-tab")));
});
```
Guard the compare call so Tasks before 7 don't error: `if (tab === "compare" && typeof renderCompare === "function") renderCompare();`

- [ ] **Step 4: Auto-advance to 监控 on successful create**

In `createEvaluation()`, after `showEvaluationMessage(\`已创建测试：${data.runId}\`, "success");` add `setActiveTab("monitor");`.

- [ ] **Step 5: Browser-verify**

Reload `/evaluations`. Expect: a tab bar with 5 tabs, 发起 active showing the upload form, others hidden. Click each tab → only its panel shows. Select a seeded completed run → switch to 监控 (metrics visible) and 校验 (results table visible). No console errors.

- [ ] **Step 6: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: phased tab navigation for model testing page"
```

---

### Task 3: Reuse-config (clone) from run history into the 发起 form

**Files:**
- Modify: `web/assets/evaluations.js`
- Modify: `web/assets/evaluations.css`

- [ ] **Step 1: Add a "复用配置" action to each run-list item**

In `renderEvaluationList()`, append a clone button inside each `.run-item` markup (after the existing `.run-meta` lines), and stop it from triggering run selection:
```js
`<button class="run-clone" type="button" data-clone-id="${escapeHtml(run.runId)}">复用配置</button>`
```
After building the list and wiring the `[data-run-id]` click handlers, wire the clone buttons:
```js
evaluationRunList.querySelectorAll("[data-clone-id]").forEach((el) => {
  el.addEventListener("click", (ev) => {
    ev.stopPropagation();
    cloneRunConfig(el.getAttribute("data-clone-id"));
  });
});
```

- [ ] **Step 2: Implement `cloneRunConfig`**

Uses `evaluationState.runs` (already loaded by `loadEvaluations`). Prefills the 发起 form fields (file still must be re-selected) and switches to the 发起 tab:
```js
function cloneRunConfig(runId) {
  const run = evaluationState.runs.find((r) => r.runId === runId);
  if (!run) return;
  evaluationVisionModel.value = run.visionModel || evaluationVisionModel.value;
  evaluationCategoryModel.value = run.categoryModel || evaluationCategoryModel.value;
  evaluationProductModel.value = run.productDataModel || evaluationProductModel.value;
  evaluationReasoning.value = run.reasoningEffort || "none";
  if (run.language) evaluationLanguage.value = run.language;
  setActiveTab("setup");
  showEvaluationMessage(`已复用 ${runId} 的配置，请选择数据文件后开始测试。`, "success");
}
```

- [ ] **Step 3: Add "带建议复用配置重测" CTA in the 分析 panel**

In `web/evaluations.html`, add inside the analysis panel's `.entry-head` a secondary button: `<button class="secondary" id="evaluation-clone-btn" type="button" disabled>复用配置重测</button>`. Wire in JS:
```js
const evaluationCloneBtn = document.getElementById("evaluation-clone-btn");
evaluationCloneBtn.addEventListener("click", () => {
  if (evaluationState.activeRunId) cloneRunConfig(evaluationState.activeRunId);
});
```
Enable/disable it in `renderEvaluationDetail()` alongside the other buttons: `evaluationCloneBtn.disabled = !evaluationState.activeRunId;`

- [ ] **Step 4: Style the clone button**
```css
.run-clone {
  width:auto; margin-top:8px; padding:5px 10px; font-size:11px; font-weight:700;
  background:#fff; color:var(--accent); border:1px dashed var(--accent);
  border-radius:7px; box-shadow:none; cursor:pointer;
}
.run-clone:not(:disabled):hover { transform:none; box-shadow:none; background:#eff6ff; }
```

- [ ] **Step 5: Browser-verify**

On `/evaluations` with seeded runs: each run item shows a 复用配置 button. Click it → switches to 发起 tab, model/reasoning/language fields populate from that run, success message shown, and clicking the run body still selects the run (clone click does not select). Select a run, go to 分析 → 复用配置重测 button enabled and works.

- [ ] **Step 6: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: reuse a past run's config into the setup form"
```

---

### Task 4: Image thumbnails + lightbox in the 校验 results table

**Files:**
- Modify: `web/assets/evaluations.js`
- Modify: `web/assets/evaluations.css`
- Modify: `web/evaluations.html`

- [ ] **Step 1: Add a thumbnail column to the results table**

In `renderEvaluationResults()`, add a leading `<th>图片</th>` before `<th>商品</th>` in the header row, and a leading cell in each row that renders the first image URL (rows' `image` may contain multiple `|`-separated URLs):
```js
// helper near the top of the file
function firstImageUrl(image) {
  return String(image || "").split("|").map((s) => s.trim()).filter(Boolean)[0] || "";
}
```
Row leading cell:
```js
`<td>${(() => {
  const url = firstImageUrl(row.image);
  return url
    ? `<img class="thumb" src="${escapeHtml(url)}" loading="lazy" alt="" data-full="${escapeHtml(row.image)}" />`
    : `<span class="thumb thumb-empty">无图</span>`;
})()}</td>`
```

- [ ] **Step 2: Add lightbox markup in `web/evaluations.html`**

Just before the closing `</body>` (after the script tags is fine, it's static markup; place it before scripts to be safe), add:
```html
<div class="lightbox" id="evaluation-lightbox" hidden>
  <button class="lightbox-close" id="lightbox-close" type="button" aria-label="关闭">×</button>
  <button class="lightbox-nav lightbox-prev" id="lightbox-prev" type="button" aria-label="上一张">‹</button>
  <img class="lightbox-img" id="lightbox-img" src="" alt="" />
  <button class="lightbox-nav lightbox-next" id="lightbox-next" type="button" aria-label="下一张">›</button>
</div>
```

- [ ] **Step 3: Implement lightbox logic in `web/assets/evaluations.js`**

Add state `lightbox: { urls: [], index: 0 }` to `evaluationState`, element refs, and:
```js
const lightboxEl = document.getElementById("evaluation-lightbox");
const lightboxImg = document.getElementById("lightbox-img");
function openLightbox(image, startIndex) {
  const urls = String(image || "").split("|").map((s) => s.trim()).filter(Boolean);
  if (!urls.length) return;
  evaluationState.lightbox = { urls, index: startIndex || 0 };
  renderLightbox();
  lightboxEl.hidden = false;
}
function renderLightbox() {
  const { urls, index } = evaluationState.lightbox;
  lightboxImg.src = urls[index] || "";
  document.getElementById("lightbox-prev").style.visibility = urls.length > 1 ? "visible" : "hidden";
  document.getElementById("lightbox-next").style.visibility = urls.length > 1 ? "visible" : "hidden";
}
function closeLightbox() { lightboxEl.hidden = true; evaluationState.lightbox = { urls: [], index: 0 }; }
function stepLightbox(delta) {
  const { urls, index } = evaluationState.lightbox;
  if (!urls.length) return;
  evaluationState.lightbox.index = (index + delta + urls.length) % urls.length;
  renderLightbox();
}
document.getElementById("lightbox-close").addEventListener("click", closeLightbox);
document.getElementById("lightbox-prev").addEventListener("click", () => stepLightbox(-1));
document.getElementById("lightbox-next").addEventListener("click", () => stepLightbox(1));
lightboxEl.addEventListener("click", (ev) => { if (ev.target === lightboxEl) closeLightbox(); });
document.addEventListener("keydown", (ev) => {
  if (lightboxEl.hidden) return;
  if (ev.key === "Escape") closeLightbox();
  else if (ev.key === "ArrowLeft") stepLightbox(-1);
  else if (ev.key === "ArrowRight") stepLightbox(1);
});
```
Wire thumbnail clicks (delegated) at the end of `renderEvaluationResults()`:
```js
evaluationResultsHost.querySelectorAll("img.thumb").forEach((img) => {
  img.addEventListener("click", () => openLightbox(img.getAttribute("data-full"), 0));
});
```

- [ ] **Step 4: Style thumbnails + lightbox**
```css
.thumb { width:46px; height:46px; object-fit:cover; border-radius:6px; border:1px solid var(--border); cursor:zoom-in; background:#f1f5f9; }
.thumb-empty { display:inline-flex; align-items:center; justify-content:center; color:var(--muted); font-size:11px; cursor:default; }
.lightbox { position:fixed; inset:0; background:rgba(15,23,42,.82); display:flex; align-items:center; justify-content:center; z-index:50; }
.lightbox[hidden] { display:none; }
.lightbox-img { max-width:86vw; max-height:86vh; border-radius:10px; box-shadow:0 24px 60px rgba(0,0,0,.4); }
.lightbox-close { position:absolute; top:18px; right:22px; width:auto; background:transparent; box-shadow:none; font-size:30px; line-height:1; color:#fff; }
.lightbox-nav { position:absolute; top:50%; transform:translateY(-50%); width:auto; background:rgba(255,255,255,.15); box-shadow:none; font-size:28px; padding:6px 14px; }
.lightbox-prev { left:18px; } .lightbox-next { right:18px; }
.lightbox-close:hover, .lightbox-nav:hover { transform:none; box-shadow:none; background:rgba(255,255,255,.28); }
.lightbox-nav { transform:translateY(-50%); }
```

- [ ] **Step 5: Browser-verify**

Select a seeded completed run → 校验 tab. Expect a 图片 column with thumbnails (or 无图). Click a thumbnail → lightbox opens with the full image; Esc/× closes; if the row has multiple `|`-separated images, ‹/› cycle them. No console errors.

- [ ] **Step 6: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: image thumbnails with lightbox in review table"
```

---

### Task 5: Review filters + 原→AI diff coloring

**Files:**
- Modify: `web/assets/evaluations.js`
- Modify: `web/assets/evaluations.css`
- Modify: `web/evaluations.html`

- [ ] **Step 1: Add the correctness helpers**

Add `isCategoryCorrect`, `normalizeBrand`, `isBrandCorrect`, `reviewIsPositive` (exact code in the "Design reference → Per-row correctness semantics" section above) near the top of `evaluations.js`.

- [ ] **Step 2: Add filter-chip markup in the 校验 panel**

In `web/evaluations.html`, inside the review card, above `#evaluation-results-host`, add:
```html
<div class="filter-bar" id="evaluation-filter-bar"></div>
```

- [ ] **Step 3: Add `reviewFilter` state + chip rendering**

Add `reviewFilter: "all"` to `evaluationState`. Add:
```js
const evaluationFilterBar = document.getElementById("evaluation-filter-bar");
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
function renderFilterBar() {
  const rows = evaluationState.rows;
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
  evaluationFilterBar.innerHTML = rows.length
    ? chips.map(([key, label, n, cls]) =>
        `<button class="chip ${cls}${evaluationState.reviewFilter === key ? " active" : ""}" data-filter="${key}">${label} ${n}</button>`
      ).join("")
    : "";
  evaluationFilterBar.querySelectorAll("[data-filter]").forEach((el) => {
    el.addEventListener("click", () => { evaluationState.reviewFilter = el.getAttribute("data-filter"); renderEvaluationResults(); });
  });
}
```

- [ ] **Step 4: Apply the filter + diff coloring in `renderEvaluationResults()`**

Compute the visible rows while preserving each row's original index (needed for review save, which keys on the full `evaluationState.rows` index):
```js
const visible = evaluationState.rows
  .map((row, index) => ({ row, index }))
  .filter(({ row }) => rowMatchesFilter(row, evaluationState.reviewFilter));
```
Build `<tbody>` from `visible` (use `{row, index}`; keep `index` for the `data-row` attributes so save still works). For the 分类/品牌 columns, render original→AI with state classes:
```js
const catCls = isCategoryCorrect(row) ? "ok" : "bad";
const brandCls = isBrandCorrect(row) ? "ok" : (String(row.aiBrand||"").trim() ? "warn" : "bad");
// category cell:
`<td class="diff ${catCls}"><span class="orig">${escapeHtml(row.genreId||"")}</span> → <span class="ai">${escapeHtml(row.aiCategory||"")}</span></td>`
// brand cell:
`<td class="diff ${brandCls}"><span class="orig">${escapeHtml(row.brand||"")}</span> → <span class="ai">${escapeHtml(row.aiBrand||"")}</span></td>`
```
Replace the previous separate 原分类/AI分类/原品牌/AI品牌 columns with these two combined diff columns (update the `<thead>` to `分类 (原→AI)` and `品牌 (原→AI)` accordingly; keep AI分类Path/置信度/AI标题 columns). Call `renderFilterBar()` at the start of `renderEvaluationResults()`. If `visible` is empty but rows exist, show a "当前筛选无匹配条目" message in the tbody area instead of the table.

- [ ] **Step 5: Style chips + diff cells**
```css
.filter-bar { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }
.chip { width:auto; padding:5px 12px; border-radius:999px; font-size:12px; font-weight:700; border:1px solid var(--border); background:#fff; color:var(--muted); box-shadow:none; cursor:pointer; }
.chip:not(:disabled):hover { transform:none; box-shadow:none; }
.chip.active { background:var(--accent); color:#fff; border-color:var(--accent); }
.chip.chip-bad.active { background:var(--danger); border-color:var(--danger); }
.chip.chip-pending.active { background:#d97706; border-color:#d97706; }
.diff .orig { color:var(--muted); }
.diff.ok .ai { color:var(--success); font-weight:700; }
.diff.bad .ai { color:var(--danger); font-weight:700; }
.diff.warn .ai { color:#b45309; font-weight:700; }
.results-table tr.row-bad td { background:#fef2f2; }
```
In Step 4 add `class="row-bad"` to a `<tr>` when neither category nor brand is correct (optional emphasis): `const rowCls = (!isCategoryCorrect(row) && !isBrandCorrect(row)) ? " row-bad" : "";`.

- [ ] **Step 6: Browser-verify**

Select a seeded completed run → 校验. Expect filter chips with counts; clicking 分类错/品牌错/待校验 narrows the table; 全部 restores. Category/brand cells show `原 → AI` with green (match) / red (mismatch) / amber (brand differs but present) coloring. Save 校验 still works (change a dropdown, save, metrics update) — confirm the saved row is the correct one even when filtered. No console errors.

- [ ] **Step 7: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: review filters and original-vs-AI diff coloring"
```

---

### Task 6: Batch marking + keyboard flow in 校验

**Files:**
- Modify: `web/assets/evaluations.js`
- Modify: `web/assets/evaluations.css`
- Modify: `web/evaluations.html`

- [ ] **Step 1: Add batch toolbar markup**

In `web/evaluations.html`, inside the review card `.entry-head`, add before 保存校验:
```html
<div class="batch-actions" id="evaluation-batch-actions">
  <span class="batch-count" id="evaluation-batch-count">已选 0</span>
  <button class="secondary batch-btn" type="button" data-batch="OK">批量正确</button>
  <button class="secondary batch-btn" type="button" data-batch="ACCEPTABLE">批量可接受</button>
  <button class="secondary batch-btn" type="button" data-batch="NG">批量错误</button>
  <select id="evaluation-batch-field"><option value="customerCategoryCheck">分类校验</option><option value="customerBrandCheck">品牌校验</option></select>
</div>
```

- [ ] **Step 2: Add row checkboxes + selection state**

Add `selectedRows: new Set()` to `evaluationState`. In `renderEvaluationResults()`, add a leading checkbox cell per row (before 图片) and a header "select all (visible)" checkbox:
```js
// header
`<th><input type="checkbox" id="evaluation-select-all" /></th>`
// row (uses original index)
`<td><input type="checkbox" class="row-select" data-row="${index}" ${evaluationState.selectedRows.has(index) ? "checked" : ""} /></td>`
```
After render, wire:
```js
evaluationResultsHost.querySelectorAll(".row-select").forEach((cb) => {
  cb.addEventListener("change", () => {
    const i = Number(cb.getAttribute("data-row"));
    if (cb.checked) evaluationState.selectedRows.add(i); else evaluationState.selectedRows.delete(i);
    updateBatchCount();
  });
});
const selectAll = document.getElementById("evaluation-select-all");
if (selectAll) selectAll.addEventListener("change", () => {
  evaluationResultsHost.querySelectorAll(".row-select").forEach((cb) => {
    cb.checked = selectAll.checked;
    const i = Number(cb.getAttribute("data-row"));
    if (selectAll.checked) evaluationState.selectedRows.add(i); else evaluationState.selectedRows.delete(i);
  });
  updateBatchCount();
});
updateBatchCount();
```

- [ ] **Step 3: Implement batch apply + count**
```js
const evaluationBatchCount = document.getElementById("evaluation-batch-count");
function updateBatchCount() { evaluationBatchCount.textContent = `已选 ${evaluationState.selectedRows.size}`; }
function applyBatch(value) {
  const field = document.getElementById("evaluation-batch-field").value;
  evaluationState.selectedRows.forEach((i) => {
    const el = evaluationResultsHost.querySelector(`[data-row="${i}"][data-review-key="${field}"]`);
    if (el) el.value = value;
  });
}
document.querySelectorAll("#evaluation-batch-actions .batch-btn").forEach((btn) => {
  btn.addEventListener("click", () => applyBatch(btn.getAttribute("data-batch")));
});
```
Clear selection when switching runs: in `selectEvaluationRun`, add `evaluationState.selectedRows = new Set();`.

- [ ] **Step 4: Keyboard flow for the focused row**

Add a "current row" highlight driven by ↑/↓, and 1/2/3 to set the current row's category check, q/w/e for brand check (only when focus is not in a textarea/select):
```js
function reviewKeyHandler(ev) {
  if (evaluationState.activeTab !== "review" || !evaluationState.rows.length) return;
  const tag = (ev.target.tagName || "").toLowerCase();
  if (tag === "textarea" || tag === "select" || tag === "input") return;
  const trs = Array.from(evaluationResultsHost.querySelectorAll("tbody tr[data-row-index]"));
  if (!trs.length) return;
  let cur = trs.findIndex((tr) => tr.classList.contains("row-focus"));
  const setVal = (field, value) => {
    if (cur < 0) return;
    const i = trs[cur].getAttribute("data-row-index");
    const el = evaluationResultsHost.querySelector(`[data-row="${i}"][data-review-key="${field}"]`);
    if (el) el.value = value;
  };
  const map = { "1": ["customerCategoryCheck","OK"], "2": ["customerCategoryCheck","ACCEPTABLE"], "3": ["customerCategoryCheck","NG"],
                "q": ["customerBrandCheck","OK"], "w": ["customerBrandCheck","ACCEPTABLE"], "e": ["customerBrandCheck","NG"] };
  if (ev.key === "ArrowDown") { cur = Math.min((cur < 0 ? -1 : cur) + 1, trs.length - 1); }
  else if (ev.key === "ArrowUp") { cur = Math.max((cur < 0 ? trs.length : cur) - 1, 0); }
  else if (map[ev.key]) { setVal(map[ev.key][0], map[ev.key][1]); return; }
  else return;
  ev.preventDefault();
  trs.forEach((tr) => tr.classList.remove("row-focus"));
  trs[cur].classList.add("row-focus");
  trs[cur].scrollIntoView({ block: "nearest" });
}
document.addEventListener("keydown", reviewKeyHandler);
```
In `renderEvaluationResults()` give each `<tr>` `data-row-index="${index}"`.

- [ ] **Step 5: Style batch toolbar + focus row + add a keyboard hint**
```css
.batch-actions { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.batch-count { font-size:12px; color:var(--muted); font-weight:700; }
.batch-btn { width:auto; padding:7px 12px; font-size:12px; }
#evaluation-batch-field { width:auto; padding:7px 10px; font-size:12px; }
.results-table tr.row-focus td { box-shadow: inset 3px 0 0 var(--accent); background:#f0f7ff; }
```
Add a hint under the filter bar in markup: `<div class="hint">键盘：↑/↓ 选行，1/2/3 标分类(正确/可接受/错误)，q/w/e 标品牌。</div>` (place inside the review panel near the filter bar).

- [ ] **Step 6: Browser-verify**

Select a seeded completed run → 校验. Check several rows → 已选 count updates; pick a field + 批量正确 → those rows' dropdowns set to 正确; 保存校验 persists. Header checkbox selects all visible. Click in the table area, press ↓ a few times → a row highlights and scrolls into view; press `1` → its 分类校验 becomes 正确; `q` → its 品牌校验 becomes 正确. Typing in a textarea does NOT trigger shortcuts. No console errors.

- [ ] **Step 7: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: batch marking and keyboard flow for review"
```

---

### Task 7: 多轮对比 (multi-run comparison) tab

**Files:**
- Modify: `web/evaluations.html`
- Modify: `web/assets/evaluations.js`
- Modify: `web/assets/evaluations.css`

- [ ] **Step 1: Build the compare panel markup**

Replace the empty compare panel with:
```html
<section class="tab-panel" data-panel="compare">
  <div class="card stack">
    <div class="entry-head"><span class="entry-title">多轮对比</span></div>
    <div class="hint">勾选要对比的运行，下表并排展示指标（同一行最优值高亮）。</div>
    <div class="compare-picker" id="evaluation-compare-picker"></div>
    <div class="compare-table-wrap" id="evaluation-compare-host" class="hint">请选择至少一个运行。</div>
  </div>
</section>
```

- [ ] **Step 2: Add compare state + picker rendering**

Add `compareRunIds: new Set()` and `compareDetails: {}` (cache by runId) to `evaluationState`. Implement:
```js
const evaluationComparePicker = document.getElementById("evaluation-compare-picker");
const evaluationCompareHost = document.getElementById("evaluation-compare-host");
function renderComparePicker() {
  evaluationComparePicker.innerHTML = evaluationState.runs.map((run) =>
    `<label class="compare-chip"><input type="checkbox" data-compare-id="${escapeHtml(run.runId)}" ${evaluationState.compareRunIds.has(run.runId) ? "checked" : ""}/> ${escapeHtml(run.runId)}</label>`
  ).join("") || "<span class='hint'>暂无运行</span>";
  evaluationComparePicker.querySelectorAll("[data-compare-id]").forEach((cb) => {
    cb.addEventListener("change", async () => {
      const id = cb.getAttribute("data-compare-id");
      if (cb.checked) { evaluationState.compareRunIds.add(id); await ensureCompareDetail(id); }
      else evaluationState.compareRunIds.delete(id);
      renderCompareTable();
    });
  });
}
async function ensureCompareDetail(id) {
  if (evaluationState.compareDetails[id]) return;
  try { evaluationState.compareDetails[id] = await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(id)}`); }
  catch (err) { evaluationState.compareDetails[id] = { error: String(err.message || err) }; }
}
```

- [ ] **Step 3: Render the side-by-side metrics table with best-value highlight**
```js
const COMPARE_METRICS = [
  ["分类准确率", (o) => o.categoryAccuracy, "pct", "max"],
  ["品牌准确率", (o) => o.brandAccuracy, "pct", "max"],
  ["复核后分类", (o) => o.categoryReviewedAccuracy, "pct", "max"],
  ["复核后品牌", (o) => o.brandReviewedAccuracy, "pct", "max"],
  ["待分类校验", (o) => o.categoryPendingReview, "num", "min"],
  ["待品牌校验", (o) => o.brandPendingReview, "num", "min"],
];
function renderCompareTable() {
  const ids = evaluationState.runs.map((r) => r.runId).filter((id) => evaluationState.compareRunIds.has(id));
  if (!ids.length) { evaluationCompareHost.innerHTML = "<div class='hint'>请选择至少一个运行。</div>"; return; }
  const overalls = ids.map((id) => (evaluationState.compareDetails[id]?.summary?.overall) || {});
  const header = `<tr><th>指标</th>${ids.map((id) => `<th>${escapeHtml(id)}</th>`).join("")}</tr>`;
  const body = COMPARE_METRICS.map(([label, get, fmt, dir]) => {
    const vals = overalls.map((o) => { const v = Number(get(o)); return Number.isFinite(v) ? v : null; });
    const present = vals.filter((v) => v !== null);
    const best = present.length ? (dir === "max" ? Math.max(...present) : Math.min(...present)) : null;
    const cells = vals.map((v) => {
      const text = v === null ? "-" : (fmt === "pct" ? formatPercent(v) : String(v));
      const isBest = best !== null && v === best && present.length > 1;
      return `<td class="${isBest ? "compare-best" : ""}">${text}</td>`;
    }).join("");
    return `<tr><td class="compare-label">${label}</td>${cells}</tr>`;
  }).join("");
  evaluationCompareHost.innerHTML = `<div class="compare-table-wrap"><table class="results-table compare-table"><thead>${header}</thead><tbody>${body}</tbody></table></div>`;
}
function renderCompare() { renderComparePicker(); renderCompareTable(); }
```
Remove the temporary `typeof renderCompare === "function"` guard in `setActiveTab` (the function now exists).

- [ ] **Step 4: Refresh picker when runs reload**

At the end of `renderEvaluationList()`, add: `if (evaluationState.activeTab === "compare") renderComparePicker();`

- [ ] **Step 5: Style compare**
```css
.compare-picker { display:flex; gap:10px; flex-wrap:wrap; margin:10px 0; }
.compare-chip { display:inline-flex; align-items:center; gap:6px; font-size:12px; font-weight:700; padding:6px 10px; border:1px solid var(--border); border-radius:8px; background:#fff; cursor:pointer; }
.compare-table .compare-label { font-weight:800; color:var(--muted); }
.compare-table td.compare-best { background:#dcfce7; color:#166534; font-weight:800; }
```

- [ ] **Step 6: Browser-verify**

Open 多轮对比 tab. Picker lists all runs. Check two seeded completed runs → a metrics table appears with one column per run; per-metric best value highlighted green (max for accuracies, min for pending). Unchecking removes its column. Switching to other tabs and back preserves selection. No console errors.

- [ ] **Step 7: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "feat: multi-run comparison tab"
```

---

### Task 8: Visual polish pass

**Files:**
- Modify: `web/assets/evaluations.css`

- [ ] **Step 1: Tighten cards, metrics, and run-list hierarchy**

Apply refinements (keep the existing blue accent and CSS variables; only adjust the listed rules):
```css
.card { box-shadow: 0 6px 20px rgba(15,23,42,.05); }            /* calmer than 0 14px 40px */
.metric { background:#fff; border-color:#eef2f7; }
.metric-value { font-size:22px; letter-spacing:-.01em; }
.metric-label { text-transform:uppercase; letter-spacing:.03em; font-size:11px; }
.run-item.active { box-shadow: inset 3px 0 0 var(--accent); }
.results-table th { font-size:11px; text-transform:uppercase; letter-spacing:.02em; }
.entry-title { font-size:15px; }
```
Add a status-pill color treatment so 监控 status reads at a glance:
```css
.pill.completed { background:#dcfce7; color:#166534; }
.pill.running, .pill.pending { background:#dbeafe; color:#1e40af; }
.pill.archived { background:#e5e7eb; color:#374151; }
```
In `renderEvaluationDetail()` (evaluations.js), set the pill class from status: `evaluationActiveStatus.className = \`pill ${status.status || "idle"}\`;` (replace the existing static `class="pill"` behavior).

- [ ] **Step 2: Browser-verify across tabs**

Walk all five tabs on a seeded run. Confirm: lighter card shadows, clearer metric labels, status pill colored by state (completed=green), active run item shows accent bar. Layout intact at desktop and at <860px (the existing single-column media query still applies). No console errors.

- [ ] **Step 3: Commit**
```bash
git add web/evaluations.html web/assets/evaluations.css web/assets/evaluations.js
git commit -m "style: visual polish for model testing page"
```

---

### Task 9: Full regression verification

**Files:** none (verification only)

- [ ] **Step 1: Backend tests still pass (no backend was changed — confirms nothing broke)**

Run:
```bash
cd /Users/youbo/gala/labs/mercari-image-recognize
uv run pytest tests/test_evaluations_api.py tests/test_evaluation_runs.py tests/test_image_model_evaluation.py -q
```
Expected: all pass (same as before the redesign).

- [ ] **Step 2: End-to-end browser walkthrough**

With the server running, via the Playwright recipe verify the full flow without regressions:
- 发起: form prefilled from `/api/v1/config`; 复用配置 fills models; (optionally create a tiny run if test data + API key available — otherwise skip live inference).
- 监控: selecting a running/seeded run shows metrics; completed run shows 下载结果 CSV link enabled.
- 校验: thumbnails + lightbox, filters, diff coloring, batch mark, keyboard flow, 保存校验 persists and refreshes metrics.
- 分析: notes load/save; 复用配置重测 works; 归档 locks the run (review/analysis buttons disable).
- 对比: two runs compared, best values highlighted.
Capture `browser_console_messages` → expect no errors across the walkthrough.

- [ ] **Step 3: Confirm auth contract unchanged**

`curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/evaluations` with no cookie → expect `307`/`302` redirect to `/login?next=/evaluations` (unchanged behavior).

- [ ] **Step 4: Final commit (if any verification fixups were needed)**
```bash
git add -A
git commit -m "test: verify model testing redesign end-to-end"
```
(If nothing changed in this task, skip the commit.)

---

## Self-Review

- **Spec coverage:** 分阶段Tab → Task 2. 复用配置 → Task 3. 缩略图+lightbox → Task 4. 筛选+原→AI对照着色 → Task 5. 批量+键盘流 → Task 6. 多轮对比 → Task 7. 视觉质感 → Task 8. 代码拆分(html/css/js) → Task 1. 浏览器验证+后端测试不回归 → Task 9. 鉴权契约不变 → Task 9 Step 3. All spec sections mapped.
- **Placeholders:** none — every code step includes real code; the only deferred reference (`renderCompare`) is explicitly guarded in Task 2 and the guard removed in Task 7.
- **Type/name consistency:** state keys (`activeTab`, `reviewFilter`, `selectedRows`, `lightbox`, `compareRunIds`, `compareDetails`), element IDs, and function names (`setActiveTab`, `cloneRunConfig`, `rowMatchesFilter`, `renderFilterBar`, `openLightbox`, `applyBatch`, `renderCompare`/`renderComparePicker`/`renderCompareTable`, `ensureCompareDetail`) are used consistently across tasks. Row save continues to key on the original `evaluationState.rows` index even when filtered (Task 5 Step 4 preserves `index`).
