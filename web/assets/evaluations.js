// ---------- generic helpers ----------
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
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
  if (failedCard) {
    failedCard.addEventListener("click", openErrors);
    failedCard.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openErrors(); }
    });
  }
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
  el("save-review-btn").hidden = archived;
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
    const clamp = (text) => {
      const safe = escapeHtml(text || "");
      return `<div class="clamp2" title="${safe}">${safe}</div>`;
    };
    return (
      `<tr${classes.length ? ` class="${classes.join(" ")}"` : ""} data-row-index="${i}">` +
      `<td><input type="checkbox" class="row-select" data-row="${i}" ${state.selectedRows.has(i) ? "checked" : ""}${dis ? " disabled" : ""} /></td>` +
      `<td>${thumb}</td>` +
      `<td class="cell-name">${clamp(row.itemName)}</td>` +
      `<td class="diff ${catCls}"><span class="orig">${escapeHtml(row.genreId || "")}</span> → <span class="ai">${escapeHtml(row.aiCategory || "")}</span></td>` +
      `<td class="cell-path">${clamp(row.aiCategoryPath)}</td>` +
      `<td>${escapeHtml(row.aiCategoryConfidence || "")}</td>` +
      `<td class="diff ${brandCls}"><span class="orig">${escapeHtml(row.brand || "")}</span> → <span class="ai">${escapeHtml(row.aiBrand || "")}</span></td>` +
      `<td class="cell-title">${clamp(row.aiTitle)}</td>` +
      `<td>${reviewSelect(row.customerCategoryCheck, i, "customerCategoryCheck", dis)}</td>` +
      `<td>${reviewSelect(row.customerBrandCheck, i, "customerBrandCheck", dis)}</td>` +
      `<td><textarea data-row="${i}" data-review-key="customerNotes"${dis ? " disabled" : ""}>${escapeHtml(row.customerNotes || "")}</textarea></td>` +
      `</tr>`
    );
  }).join("");

  host.innerHTML =
    `<div class="results-table-wrap"><table class="results-table"><thead><tr>` +
    `<th><input type="checkbox" id="select-all"${dis ? " disabled" : ""} /></th>` +
    `<th>图片</th><th>商品</th><th>分类 (原→AI)</th><th>AI 分类 Path</th><th>置信度</th>` +
    `<th>品牌 (原→AI)</th><th>AI 标题</th><th>分类校验</th><th>品牌校验</th><th>备注</th>` +
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
  renderDropzone();
  el("drawer-backdrop").hidden = false;
  el("drawer").hidden = false;
}

function closeDrawer() {
  el("drawer").hidden = true;
  el("drawer-backdrop").hidden = true;
}

function renderDropzone() {
  const file = el("f-file").files && el("f-file").files[0];
  el("f-dropzone").classList.toggle("has-file", Boolean(file));
  el("dropzone-idle").hidden = Boolean(file);
  el("dropzone-file").hidden = !file;
  el("dropzone-filename").textContent = file
    ? `${file.name}（${Math.max(1, Math.ceil(file.size / 1024))} KB）`
    : "";
}

function setDropzoneFile(file) {
  const input = el("f-file");
  if (file) {
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
  } else {
    input.value = "";
  }
  renderDropzone();
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
    setDropzoneFile(null);
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
const dropzone = el("f-dropzone");
dropzone.addEventListener("click", (ev) => {
  if (ev.target.closest("#dropzone-clear")) return;
  el("f-file").click();
});
dropzone.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); el("f-file").click(); }
});
dropzone.addEventListener("dragover", (ev) => {
  ev.preventDefault();
  dropzone.classList.add("dragover");
});
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (ev) => {
  ev.preventDefault();
  dropzone.classList.remove("dragover");
  const file = ev.dataTransfer.files && ev.dataTransfer.files[0];
  if (!file) return;
  if (!/\.(csv|tsv)$/i.test(file.name)) {
    showMessage("仅支持 .csv / .tsv 文件。", "error", el("drawer-message"));
    return;
  }
  clearMessage(el("drawer-message"));
  setDropzoneFile(file);
});
el("f-file").addEventListener("change", renderDropzone);
el("dropzone-clear").addEventListener("click", (ev) => {
  ev.stopPropagation();
  setDropzoneFile(null);
});
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
