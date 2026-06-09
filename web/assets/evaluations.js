function escapeHtml(s) {
        return String(s == null ? "" : s)
          .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      }

      // ---------- Evaluation SOP ----------
      const evaluationState = {
        runs: [],
        activeRunId: "",
        activeDetail: null,
        rows: [],
        poller: null,
        activeTab: "setup",
      };
      // Defaults pulled from saved config so this standalone page can prefill models.
      let configDefaults = {};
      const evaluationFile = document.getElementById("evaluation-file");
      const evaluationVisionModel = document.getElementById("evaluation-vision-model");
      const evaluationCategoryModel = document.getElementById("evaluation-category-model");
      const evaluationProductModel = document.getElementById("evaluation-product-model");
      const evaluationReasoning = document.getElementById("evaluation-reasoning");
      const evaluationLimit = document.getElementById("evaluation-limit");
      const evaluationLanguage = document.getElementById("evaluation-language");
      const evaluationMessage = document.getElementById("evaluations-message");
      const evaluationCreateBtn = document.getElementById("evaluation-create-btn");
      const evaluationRefreshBtn = document.getElementById("evaluation-refresh-btn");
      const evaluationRunList = document.getElementById("evaluation-run-list");
      const evaluationActiveTitle = document.getElementById("evaluation-active-title");
      const evaluationActiveStatus = document.getElementById("evaluation-active-status");
      const evaluationActiveMeta = document.getElementById("evaluation-active-meta");
      const evaluationMetrics = document.getElementById("evaluation-metrics");
      const evaluationDownloadLink = document.getElementById("evaluation-download-link");
      const evaluationArchiveBtn = document.getElementById("evaluation-archive-btn");
      const evaluationSaveReviewBtn = document.getElementById("evaluation-save-review-btn");
      const evaluationResultsHost = document.getElementById("evaluation-results-host");
      const evaluationSaveAnalysisBtn = document.getElementById("evaluation-save-analysis-btn");
      const evaluationCloneBtn = document.getElementById("evaluation-clone-btn");
      const evaluationAnalysisNotes = document.getElementById("evaluation-analysis-notes");
      const evaluationActions = document.getElementById("evaluation-actions");
      const evaluationNextRun = document.getElementById("evaluation-next-run");

      function showEvaluationMessage(text, type) {
        evaluationMessage.textContent = text;
        evaluationMessage.className = `message show ${type}`;
      }

      function clearEvaluationMessage() {
        evaluationMessage.className = "message";
        evaluationMessage.textContent = "";
      }

      async function loadConfigDefaults() {
        try {
          const resp = await fetch("/api/v1/config");
          if (resp.ok) configDefaults = await resp.json();
        } catch (err) {
          /* fall back to placeholders */
        }
        applyConfigDefaultsToEvaluation();
      }

      function applyConfigDefaultsToEvaluation() {
        evaluationVisionModel.value = evaluationVisionModel.value || configDefaults.VISION_MODEL || "openai/gpt-4o-mini";
        evaluationCategoryModel.value = evaluationCategoryModel.value || configDefaults.CATEGORY_MODEL || "openai/gpt-4o-mini";
        evaluationProductModel.value = evaluationProductModel.value || configDefaults.PRODUCT_DATA_MODEL || "openai/gpt-4o-mini";
      }

      async function evaluationJson(url, options) {
        const resp = await fetch(url, options);
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(data.detail || "请求失败");
        return data;
      }

      function formatPercent(value) {
        const num = Number(value);
        if (!Number.isFinite(num)) return "-";
        return `${Math.round(num * 1000) / 10}%`;
      }

      function formatSeconds(value) {
        const num = Number(value);
        if (!Number.isFinite(num) || num <= 0) return "-";
        return `${Math.round(num)}s`;
      }

      function summaryOverall() {
        return (evaluationState.activeDetail && evaluationState.activeDetail.summary && evaluationState.activeDetail.summary.overall) || {};
      }

      function renderEvaluationMetrics() {
        const detail = evaluationState.activeDetail || {};
        const status = detail.status || {};
        const overall = summaryOverall();
        const metrics = [
          ["进度", `${status.completed || 0}/${status.total || 0}`],
          ["失败", String(status.failed || 0)],
          ["分类准确率", formatPercent(overall.categoryAccuracy)],
          ["品牌准确率", formatPercent(overall.brandAccuracy)],
          ["复核后分类", formatPercent(overall.categoryReviewedAccuracy)],
          ["复核后品牌", formatPercent(overall.brandReviewedAccuracy)],
          ["待分类校验", String(overall.categoryPendingReview ?? "-")],
          ["待品牌校验", String(overall.brandPendingReview ?? "-")],
          ["ETA", formatSeconds(status.etaSeconds)],
        ];
        evaluationMetrics.innerHTML = metrics.map(([label, value]) =>
          `<div class="metric"><div class="metric-label">${escapeHtml(label)}</div><div class="metric-value">${escapeHtml(value)}</div></div>`
        ).join("");
      }

      function renderEvaluationList() {
        if (!evaluationState.runs.length) {
          evaluationRunList.textContent = "暂无测试记录";
          return;
        }
        evaluationRunList.innerHTML = evaluationState.runs.map((run) => {
          const active = run.runId === evaluationState.activeRunId ? " active" : "";
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
        evaluationRunList.querySelectorAll("[data-run-id]").forEach((el) => {
          el.addEventListener("click", () => selectEvaluationRun(el.getAttribute("data-run-id")));
        });
        evaluationRunList.querySelectorAll("[data-clone-id]").forEach((el) => {
          el.addEventListener("click", (ev) => {
            ev.stopPropagation();
            cloneRunConfig(el.getAttribute("data-clone-id"));
          });
        });
      }

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

      function parseAnalysisSection(markdown, title) {
        const pattern = new RegExp(`## ${title}\\n\\n([\\s\\S]*?)(?=\\n\\n## |$)`);
        const match = String(markdown || "").match(pattern);
        return match ? match[1].trim() : "";
      }

      function renderEvaluationDetail() {
        const detail = evaluationState.activeDetail;
        const run = detail && detail.run ? detail.run : {};
        const status = detail && detail.status ? detail.status : {};
        const isArchived = status.status === "archived" || run.archived;
        const isComplete = status.status === "completed" || isArchived;
        evaluationActiveTitle.textContent = evaluationState.activeRunId || "未选择测试";
        evaluationActiveStatus.textContent = status.status || "idle";
        evaluationActiveMeta.textContent = evaluationState.activeRunId
          ? `${run.visionModel || "-"} / ${run.categoryModel || "-"} / ${run.productDataModel || "-"} · reasoning=${run.reasoningEffort || "none"} · ${status.message || ""}`
          : "上传测试数据后会在这里展示实时进度和统计。";
        evaluationDownloadLink.hidden = !isComplete;
        evaluationDownloadLink.href = evaluationState.activeRunId
          ? `/api/v1/evaluations/${encodeURIComponent(evaluationState.activeRunId)}/results.csv`
          : "#";
        evaluationArchiveBtn.disabled = !evaluationState.activeRunId || isArchived;
        evaluationSaveReviewBtn.disabled = !isComplete || isArchived || !evaluationState.rows.length;
        evaluationSaveAnalysisBtn.disabled = !evaluationState.activeRunId || isArchived;
        evaluationCloneBtn.disabled = !evaluationState.activeRunId;
        if (detail && detail.analysis) {
          evaluationAnalysisNotes.value = parseAnalysisSection(detail.analysis, "可优化点") || detail.analysis;
          evaluationActions.value = parseAnalysisSection(detail.analysis, "优化动作");
          evaluationNextRun.value = parseAnalysisSection(detail.analysis, "下一轮测试建议");
        } else if (!evaluationState.activeRunId) {
          evaluationAnalysisNotes.value = "";
          evaluationActions.value = "";
          evaluationNextRun.value = "";
        }
        renderEvaluationMetrics();
      }

      async function loadEvaluations() {
        const data = await evaluationJson("/api/v1/evaluations");
        evaluationState.runs = data.runs || [];
        if (!evaluationState.activeRunId && evaluationState.runs[0]) {
          evaluationState.activeRunId = evaluationState.runs[0].runId;
        }
        renderEvaluationList();
        if (evaluationState.activeRunId) {
          await loadEvaluationDetail(evaluationState.activeRunId);
        }
      }

      async function selectEvaluationRun(runId) {
        evaluationState.activeRunId = runId;
        evaluationState.rows = [];
        renderEvaluationList();
        evaluationResultsHost.textContent = "正在读取结果...";
        await loadEvaluationDetail(runId);
      }

      function ensureEvaluationPolling(status) {
        const running = status === "pending" || status === "running";
        if (running && !evaluationState.poller) {
          evaluationState.poller = window.setInterval(() => {
            if (evaluationState.activeRunId) {
              loadEvaluationDetail(evaluationState.activeRunId).catch((err) => showEvaluationMessage(String(err.message || err), "error"));
              loadEvaluations().catch(() => {});
            }
          }, 2500);
        }
        if (!running && evaluationState.poller) {
          window.clearInterval(evaluationState.poller);
          evaluationState.poller = null;
        }
      }

      async function loadEvaluationDetail(runId) {
        const detail = await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(runId)}`);
        evaluationState.activeDetail = detail;
        renderEvaluationDetail();
        const status = detail.status && detail.status.status;
        ensureEvaluationPolling(status);
        if (status === "completed" || status === "archived") {
          await loadEvaluationResults(runId);
        } else {
          evaluationState.rows = [];
          evaluationResultsHost.textContent = "测试运行中，完成后展示结果。";
        }
      }

      function reviewSelect(value, rowIndex, key) {
        const current = String(value || "");
        const options = [
          ["", "待校验"],
          ["OK", "正确"],
          ["ACCEPTABLE", "可接受"],
          ["NG", "错误"],
        ];
        return `<select data-row="${rowIndex}" data-review-key="${key}">` +
          options.map(([val, label]) => `<option value="${val}"${current === val ? " selected" : ""}>${label}</option>`).join("") +
          `</select>`;
      }

      function renderEvaluationResults() {
        if (!evaluationState.rows.length) {
          evaluationResultsHost.textContent = "暂无结果。";
          evaluationSaveReviewBtn.disabled = true;
          return;
        }
        evaluationResultsHost.innerHTML =
          `<div class="results-table-wrap"><table class="results-table">` +
          `<thead><tr>` +
          `<th>商品</th><th>原分类</th><th>AI分类</th><th>AI分类Path</th><th>置信度</th>` +
          `<th>原品牌</th><th>AI品牌</th><th>AI标题</th><th>分类校验</th><th>品牌校验</th><th>备注</th>` +
          `</tr></thead><tbody>` +
          evaluationState.rows.map((row, index) => (
            `<tr>` +
            `<td class="clip">${escapeHtml(row.itemName || "")}</td>` +
            `<td>${escapeHtml(row.genreId || "")}</td>` +
            `<td>${escapeHtml(row.aiCategory || "")}</td>` +
            `<td class="clip">${escapeHtml(row.aiCategoryPath || "")}</td>` +
            `<td>${escapeHtml(row.aiCategoryConfidence || "")}</td>` +
            `<td>${escapeHtml(row.brand || "")}</td>` +
            `<td>${escapeHtml(row.aiBrand || "")}</td>` +
            `<td class="clip">${escapeHtml(row.aiTitle || "")}</td>` +
            `<td>${reviewSelect(row.customerCategoryCheck, index, "customerCategoryCheck")}</td>` +
            `<td>${reviewSelect(row.customerBrandCheck, index, "customerBrandCheck")}</td>` +
            `<td><textarea data-row="${index}" data-review-key="customerNotes">${escapeHtml(row.customerNotes || "")}</textarea></td>` +
            `</tr>`
          )).join("") +
          `</tbody></table></div>`;
        renderEvaluationDetail();
      }

      async function loadEvaluationResults(runId) {
        const data = await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(runId)}/results`);
        evaluationState.rows = data.rows || [];
        renderEvaluationResults();
      }

      async function createEvaluation() {
        clearEvaluationMessage();
        applyConfigDefaultsToEvaluation();
        if (!evaluationFile.files || !evaluationFile.files[0]) {
          showEvaluationMessage("请先选择测试数据文件。", "error");
          return;
        }
        const form = new FormData();
        form.append("file", evaluationFile.files[0]);
        form.append("visionModel", evaluationVisionModel.value.trim());
        form.append("categoryModel", evaluationCategoryModel.value.trim());
        form.append("productDataModel", evaluationProductModel.value.trim());
        form.append("reasoningEffort", evaluationReasoning.value);
        form.append("language", evaluationLanguage.value);
        form.append("limit", String(Number(evaluationLimit.value || 0)));
        evaluationCreateBtn.disabled = true;
        evaluationCreateBtn.textContent = "提交中...";
        try {
          const data = await evaluationJson("/api/v1/evaluations", { method: "POST", body: form });
          evaluationState.activeRunId = data.runId;
          showEvaluationMessage(`已创建测试：${data.runId}`, "success");
          setActiveTab("monitor");
          await loadEvaluations();
        } catch (err) {
          showEvaluationMessage(String(err.message || err), "error");
        } finally {
          evaluationCreateBtn.disabled = false;
          evaluationCreateBtn.textContent = "开始测试";
        }
      }

      async function saveEvaluationReview() {
        const updates = evaluationState.rows.map((row, index) => {
          const categoryEl = evaluationResultsHost.querySelector(`[data-row="${index}"][data-review-key="customerCategoryCheck"]`);
          const brandEl = evaluationResultsHost.querySelector(`[data-row="${index}"][data-review-key="customerBrandCheck"]`);
          const notesEl = evaluationResultsHost.querySelector(`[data-row="${index}"][data-review-key="customerNotes"]`);
          return {
            rowIndex: index,
            customerCategoryCheck: categoryEl ? categoryEl.value : row.customerCategoryCheck,
            customerBrandCheck: brandEl ? brandEl.value : row.customerBrandCheck,
            customerNotes: notesEl ? notesEl.value : row.customerNotes,
          };
        });
        evaluationSaveReviewBtn.disabled = true;
        try {
          await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(evaluationState.activeRunId)}/review`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ rows: updates }),
          });
          showEvaluationMessage("客服校验已保存，统计已刷新。", "success");
          await loadEvaluationDetail(evaluationState.activeRunId);
        } catch (err) {
          showEvaluationMessage(String(err.message || err), "error");
        } finally {
          renderEvaluationDetail();
        }
      }

      async function saveEvaluationAnalysis() {
        evaluationSaveAnalysisBtn.disabled = true;
        try {
          await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(evaluationState.activeRunId)}/analysis`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              analysisNotes: evaluationAnalysisNotes.value,
              optimizationActions: evaluationActions.value,
              nextRunSuggestion: evaluationNextRun.value,
            }),
          });
          showEvaluationMessage("分析已保存。", "success");
          await loadEvaluationDetail(evaluationState.activeRunId);
        } catch (err) {
          showEvaluationMessage(String(err.message || err), "error");
        } finally {
          renderEvaluationDetail();
        }
      }

      async function archiveEvaluation() {
        if (!evaluationState.activeRunId || !window.confirm("归档后将锁定客服校验和分析，确认归档？")) return;
        evaluationArchiveBtn.disabled = true;
        try {
          await evaluationJson(`/api/v1/evaluations/${encodeURIComponent(evaluationState.activeRunId)}/archive`, { method: "POST" });
          showEvaluationMessage("测试已归档。", "success");
          await loadEvaluations();
        } catch (err) {
          showEvaluationMessage(String(err.message || err), "error");
        } finally {
          renderEvaluationDetail();
        }
      }

      function setActiveTab(tab) {
        evaluationState.activeTab = tab;
        document.querySelectorAll("#evaluation-tabs .tab").forEach((btn) => {
          const isActive = btn.getAttribute("data-tab") === tab;
          btn.classList.toggle("active", isActive);
          btn.setAttribute("aria-selected", isActive ? "true" : "false");
        });
        document.querySelectorAll("[data-panel]").forEach((panel) => {
          panel.classList.toggle("active", panel.getAttribute("data-panel") === tab);
        });
        if (tab === "compare" && typeof renderCompare === "function") renderCompare();
      }

      document.querySelectorAll("#evaluation-tabs .tab").forEach((btn) => {
        btn.addEventListener("click", () => setActiveTab(btn.getAttribute("data-tab")));
      });
      evaluationCreateBtn.addEventListener("click", createEvaluation);
      evaluationRefreshBtn.addEventListener("click", () => {
        loadEvaluations().catch((err) => showEvaluationMessage(String(err.message || err), "error"));
      });
      evaluationSaveReviewBtn.addEventListener("click", saveEvaluationReview);
      evaluationSaveAnalysisBtn.addEventListener("click", saveEvaluationAnalysis);
      evaluationArchiveBtn.addEventListener("click", archiveEvaluation);
      evaluationCloneBtn.addEventListener("click", () => {
        if (evaluationState.activeRunId) cloneRunConfig(evaluationState.activeRunId);
      });

      // ---------- Shell + sidebar ----------
      Shell.mount({
        page: "evaluations",
        defaultRoute: "evaluations",
        brand: { logo: "M", text: "Mercari 识别" },
        sidebar: () => [{ id: "evaluations", label: "模型测试" }],
        onRouteChange: () => {
          Shell.setHeader({ title: "模型测试", crumb: "" });
        },
      });

      // ---------- Init ----------
      loadConfigDefaults();
      loadEvaluations().catch((err) => showEvaluationMessage(String(err.message || err), "error"));
