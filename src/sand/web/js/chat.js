import {
  apiGet, apiJson, apiDelete, downloadExport, newQueryId,
} from "./api.js";
import {
  state, els, setError, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, isDatasetLocked, showLastSqlPanel,
} from "./state.js";

let activeAbort = null;
let activeQueryId = null;

export function refreshAskColumnSelects() {
  const table = els.chatTable.value || state.tables[0];
  const cols = table ? columnsFor(table) : [];
  const askColumn = document.getElementById("ask-column");
  const askGroup = document.getElementById("ask-group");
  const askDate = document.getElementById("ask-date");
  if (!askColumn || !askGroup || !askDate) return;
  const keepC = askColumn.value;
  const keepG = askGroup.value;
  const keepD = askDate.value;
  fillSelect(askColumn, ["", ...cols], keepC);
  fillSelect(askGroup, ["", ...cols], keepG);
  fillSelect(askDate, ["", ...cols], keepD);
  askColumn.options[0].textContent = "(auto)";
  askGroup.options[0].textContent = "(auto)";
  askDate.options[0].textContent = "(auto)";
  if (els.filterColumn) {
    fillSelect(els.filterColumn, ["", ...cols], els.filterColumn.value);
    if (els.filterColumn.options[0]) els.filterColumn.options[0].textContent = "(pick column)";
  }
}


function addMessage(role, text, meta) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div>${text}</div>` + (meta ? `<div class="meta">${meta}</div>` : "");
  els.chatLog.appendChild(div);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return div;
}

function setBusy(busy) {
  if (els.askBtn) els.askBtn.disabled = busy || isDatasetLocked();
  if (els.cancelQueryBtn) els.cancelQueryBtn.style.display = busy ? "inline-block" : "none";
  if (els.cancelQueryBtn) els.cancelQueryBtn.disabled = !busy;
}

async function cancelActiveQuery() {
  const id = els.dataset?.value;
  if (activeAbort) activeAbort.abort();
  if (id) {
    try {
      await apiJson("/chat/cancel", "POST", { dataset_id: id, query_id: activeQueryId });
    } catch (_err) {
      /* best-effort */
    }
  }
  setBusy(false);
  setError("Query cancelled.");
}

async function withCancellable(fn) {
  if (activeAbort) activeAbort.abort();
  activeAbort = new AbortController();
  activeQueryId = newQueryId();
  setBusy(true);
  setError("");
  try {
    return await fn(activeAbort.signal, activeQueryId);
  } catch (err) {
    if (err && err.name === "AbortError") {
      setError("Query cancelled.");
      return null;
    }
    throw err;
  } finally {
    activeAbort = null;
    activeQueryId = null;
    setBusy(false);
  }
}

async function loadChatHistory() {
  const id = els.dataset.value;
  els.chatLog.innerHTML = "";
  els.previewNotice.classList.remove("visible");
  state.lastChatSql = null;
  showLastSqlPanel(null);
  if (!id) return;
  try {
    const data = await apiGet(`/chat/${encodeURIComponent(id)}/history`);
    (data.turns || []).forEach((turn) => {
      const meta = turn.sql ? `SQL: ${turn.sql}` : "";
      addMessage(turn.role === "user" ? "user" : "assistant", escapeHtml(turn.content), meta ? escapeHtml(meta) : "");
      if (turn.sql) state.lastChatSql = turn.sql;
    });
    showLastSqlPanel(state.lastChatSql);
  } catch (err) {
    setError(err.message || String(err));
  }
  await loadViews();
}

function showPreviewNotice(data) {
  if (data.is_preview) {
    const shown = data.row_count;
    const total = data.full_row_count != null ? data.full_row_count : "?";
    const sample = data.chart_sample_rows || 5000;
    els.previewNotice.innerHTML =
      `Evaluated with LIMIT ${data.evaluated_limit} (${shown} of ${total} rows). ` +
      `<button type="button" class="link" id="run-full-btn">Run full query</button>` +
      `<div class="hint" style="margin-top:0.4rem;">` +
      `Charts still sample at most ${sample.toLocaleString()} rows after a full run. ` +
      `Use Export CSV/XLSX/Parquet for the complete result.` +
      `</div>`;
    els.previewNotice.classList.add("visible");
    document.getElementById("run-full-btn").addEventListener("click", runFullQuery);
    return;
  }

  if (data.chart_capped) {
    const shown = data.row_count;
    const total = data.full_row_count != null ? data.full_row_count : "?";
    const sample = data.chart_sample_rows || 5000;
    els.previewNotice.innerHTML =
      `Chart shows ${shown.toLocaleString()} of ~${typeof total === "number" ? total.toLocaleString() : total} rows ` +
      `(sample cap ${sample.toLocaleString()}). ` +
      `Export CSV/XLSX/Parquet for the full result. ` +
      `Saved views can raise this cap (up to max result rows).`;
    els.previewNotice.classList.add("visible");
    return;
  }

  els.previewNotice.classList.remove("visible");
}

function applyChatResult(data, metaExtra) {
  const reason = (data.chart && data.chart.spec && data.chart.spec.reason) || "";
  const meta = metaExtra || `SQL: ${data.sql}\n${reason}`;
  addMessage("assistant", escapeHtml(data.summary), escapeHtml(meta));
  if (data.chart && data.chart.figure) {
    Plotly.newPlot(els.chart, data.chart.figure.data, data.chart.figure.layout, { responsive: true });
  }
  state.lastChatSql = data.sql;
  showLastSqlPanel(data.sql);
  showPreviewNotice(data);
}

async function runSqlFromChat(sql, runFull) {
  const id = requireDataset();
  if (!id || isDatasetLocked() || !sql) return;
  try {
    const body = { dataset_id: id, sql, run_full: !!runFull };
    if (els.chartType?.value) body.chart_type = els.chartType.value;
    const data = await withCancellable((signal, queryId) =>
      apiJson("/query/sql", "POST", body, { signal, queryId }),
    );
    if (!data) return;
    applyChatResult(data, `SQL (direct)\nSQL: ${data.sql}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function runFullQuery() {
  const sql = (els.lastSqlInput?.value || state.lastChatSql || "").trim();
  if (!sql) return;
  await runSqlFromChat(sql, true);
}


async function askCommon(action) {
  const id = requireDataset();
  if (!id || isDatasetLocked()) return;
  const table = els.chatTable.value || undefined;
  const askColumn = document.getElementById("ask-column");
  const askGroup = document.getElementById("ask-group");
  const askDate = document.getElementById("ask-date");
  const params = {};
  if (action === "top_n" && askColumn && askColumn.value) params.column = askColumn.value;
  if (action === "groupby") {
    if (askGroup && askGroup.value) params.group_by = [askGroup.value];
    if (askColumn && askColumn.value) params.metric = askColumn.value;
  }
  if (action === "time_series") {
    if (askDate && askDate.value) params.date_column = askDate.value;
    if (askColumn && askColumn.value) params.metric = askColumn.value;
  }
  if (action === "filter") {
    const col = els.filterColumn?.value;
    const op = els.filterOp?.value || "eq";
    if (!col) {
      setError("Pick a column for filter.");
      return;
    }
    const filter = { column: col, op };
    const rawVal = (els.filterValue?.value || "").trim();
    if (op === "is_null" || op === "is_not_null") {
      /* no value */
    } else if (op === "between") {
      const parts = rawVal.split(",").map((s) => s.trim()).filter(Boolean);
      if (parts.length < 2) {
        setError("Between needs two comma-separated values.");
        return;
      }
      filter.value = parts;
    } else if (op === "in") {
      filter.value = rawVal.split(",").map((s) => s.trim()).filter(Boolean);
    } else if (!rawVal) {
      setError("Enter a filter value.");
      return;
    } else {
      filter.value = rawVal;
    }
    params.filters = [filter];
    params.limit = 100;
  }
  const labels = {
    profile: "Profile",
    missing: "Missing values",
    top_n: "Top-N",
    groupby: "Group-by",
    time_series: "Time series",
    filter: "Filter",
  };
  addMessage("user", `${labels[action] || action}${table ? ` — ${escapeHtml(table)}` : ""}`);
  try {
    const data = await withCancellable((signal, queryId) =>
      apiJson("/chat/common-ask", "POST", { dataset_id: id, action, table, params }, { signal, queryId }),
    );
    if (!data) return;
    const html = renderPreviewTable(data.rows, data.columns);
    addMessage("assistant", `${labels[action] || action} for <strong>${escapeHtml(data.table)}</strong> (${data.row_count || data.rows.length} rows)${html}`);
    if (data.rows && data.rows.length && data.columns) {
      const numeric = data.columns.filter((c) => typeof data.rows[0][c] === "number");
      const categorical = data.columns.filter((c) => typeof data.rows[0][c] !== "number");
      if (numeric.length && categorical.length) {
        Plotly.newPlot(els.chart, [{
          type: action === "time_series" ? "scatter" : "bar",
          mode: action === "time_series" ? "lines+markers" : undefined,
          x: data.rows.map((r) => r[categorical[0]]),
          y: data.rows.map((r) => r[numeric[0]]),
        }], {
          title: `${numeric[0]} by ${categorical[0]}`,
          margin: { t: 40, r: 20, b: 40, l: 40 },
        }, { responsive: true });
      }
    }
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function loadViews() {
  const list = document.getElementById("view-list");
  const empty = document.getElementById("view-empty");
  if (!list || !empty) return;
  list.innerHTML = "";
  const id = els.dataset.value;
  if (!id) {
    empty.style.display = "block";
    empty.textContent = "Select a dataset to see saved views.";
    return;
  }
  try {
    const data = await apiGet(`/chat/views/${encodeURIComponent(id)}`);
    const views = data.views || [];
    if (!views.length) {
      empty.style.display = "block";
      empty.textContent = "No saved views yet. Ask a question, then Save view.";
      return;
    }
    empty.style.display = "none";
    views.forEach((v) => {
      const li = document.createElement("li");
      const flags = [
        v.allow_over_cap ? "over-cap" : null,
        v.cache_enabled ? (v.has_cache ? "cached" : "cache on") : null,
      ].filter(Boolean).join(" · ");
      li.innerHTML =
        `<div><strong>${escapeHtml(v.name)}</strong>` +
        `<div class="recipe-meta">${escapeHtml(flags || "standard chart cap")}</div></div>` +
        `<div class="recipe-actions">` +
        `<button type="button" class="secondary small" data-act="run">Run</button>` +
        `<button type="button" class="secondary small" data-act="refresh">Refresh</button>` +
        `<button type="button" class="danger small" data-act="del">Delete</button>` +
        `</div>`;
      li.querySelector('[data-act="run"]').addEventListener("click", () => runView(v.name, false));
      li.querySelector('[data-act="refresh"]').addEventListener("click", () => runView(v.name, true));
      li.querySelector('[data-act="del"]').addEventListener("click", () => deleteView(v.name));
      list.appendChild(li);
    });
  } catch (err) {
    empty.style.display = "block";
    empty.textContent = err.message || String(err);
  }
}

async function saveCurrentView() {
  const id = requireDataset();
  if (!id) return;
  if (!state.lastChatSql) {
    setError("Ask a question first, then save that SQL as a view.");
    return;
  }
  const nameInput = document.getElementById("view-name");
  const name = (nameInput?.value || "").trim();
  if (!name) {
    setError("Enter a view name.");
    return;
  }
  const cacheEnabled = !!document.getElementById("view-cache")?.checked;
  const allowOverCap = !!document.getElementById("view-over-cap")?.checked;
  try {
    await apiJson("/chat/views", "POST", {
      dataset_id: id,
      name,
      sql: state.lastChatSql,
      chart_type: els.chartType.value || null,
      cache_enabled: cacheEnabled,
      allow_over_cap: allowOverCap,
    });
    if (nameInput) nameInput.value = "";
    setError("");
    await loadViews();
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function runView(name, refresh) {
  const id = requireDataset();
  if (!id) return;
  addMessage("user", `Run view: ${escapeHtml(name)}${refresh ? " (refresh cache)" : ""}`);
  try {
    const data = await withCancellable((signal, queryId) =>
      apiJson("/chat/views/run", "POST", {
        dataset_id: id,
        name,
        use_cache: !refresh,
        refresh_cache: !!refresh,
      }, { signal, queryId }),
    );
    if (!data) return;
    const cacheNote = data.from_cache ? " (from cache)" : (data.cache_updated ? " (cache updated)" : "");
    applyChatResult(data, `View ${name}${cacheNote}\nSQL: ${data.sql}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function deleteView(name) {
  const id = requireDataset();
  if (!id) return;
  if (!confirm(`Delete view “${name}”?`)) return;
  try {
    await apiDelete(`/chat/views/${encodeURIComponent(id)}/${encodeURIComponent(name)}`);
    await loadViews();
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function exportChatResult(fmt) {
  const id = requireDataset();
  if (!id) return;
  const sql = (els.lastSqlInput?.value || state.lastChatSql || "").trim();
  if (!sql) { setError("Ask a question first, then export the result."); return; }
  setError("");
  try {
    await downloadExport(fmt, { dataset_id: id, sql }, `chat_result.${fmt}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

export function wireChatTab() {
  els.askBtn.addEventListener("click", async () => {
    const message = els.prompt.value.trim();
    if (!message) return;
    const id = requireDataset();
    if (!id || isDatasetLocked()) return;
    addMessage("user", escapeHtml(message));
    els.prompt.value = "";
    try {
      const data = await withCancellable((signal, queryId) => {
        const body = { dataset_id: id, message };
        if (els.chartType.value) body.chart_type = els.chartType.value;
        return apiJson("/chat", "POST", body, { signal, queryId });
      });
      if (!data) return;
      applyChatResult(data);
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  els.cancelQueryBtn?.addEventListener("click", () => cancelActiveQuery());
  document.getElementById("save-view-btn")?.addEventListener("click", () => saveCurrentView());
  els.askProfileBtn.addEventListener("click", () => askCommon("profile"));
  els.askMissingBtn.addEventListener("click", () => askCommon("missing"));
  document.getElementById("ask-topn-btn").addEventListener("click", () => askCommon("top_n"));
  document.getElementById("ask-groupby-btn").addEventListener("click", () => askCommon("groupby"));
  document.getElementById("ask-ts-btn").addEventListener("click", () => askCommon("time_series"));
  els.askFilterBtn?.addEventListener("click", () => askCommon("filter"));
  els.filterApplyBtn?.addEventListener("click", () => askCommon("filter"));
  els.runLastSqlBtn?.addEventListener("click", () => {
    const sql = (els.lastSqlInput?.value || "").trim();
    if (!sql) return;
    runSqlFromChat(sql, false);
  });
  els.copyLastSqlBtn?.addEventListener("click", async () => {
    const sql = (els.lastSqlInput?.value || state.lastChatSql || "").trim();
    if (!sql) return;
    try {
      await navigator.clipboard.writeText(sql);
      setError("");
      els.copyLastSqlBtn.textContent = "Copied!";
      setTimeout(() => { els.copyLastSqlBtn.textContent = "Copy SQL"; }, 1500);
    } catch (err) {
      setError(err.message || "Copy failed");
    }
  });
  els.chatTable.addEventListener("change", refreshAskColumnSelects);
  els.clearHistoryBtn.addEventListener("click", async () => {
    const id = requireDataset();
    if (!id) return;
    if (!confirm("Clear chat history for this dataset?")) return;
    setError("");
    try {
      await apiDelete(`/chat/${encodeURIComponent(id)}/history`);
      els.chatLog.innerHTML = "";
      els.previewNotice.classList.remove("visible");
      state.lastChatSql = null;
      showLastSqlPanel(null);
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  els.exportChatCsvBtn.addEventListener("click", () => exportChatResult("csv"));
  els.exportChatXlsxBtn.addEventListener("click", () => exportChatResult("xlsx"));
  els.exportChatParquetBtn?.addEventListener("click", () => exportChatResult("parquet"));
}

export { loadChatHistory, refreshAskColumnSelects, wireChatTab, loadViews };
