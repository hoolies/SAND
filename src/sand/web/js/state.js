/** Shared DOM/state for the SAND UI. */

export const SAND_TYPES = ["integer", "float", "boolean", "datetime", "date", "string", "unknown"];

export const state = {
  datasets: [],
  schema: {},
  tables: [],
  lineage: {},
  currentTable: null,
  typesPlan: null,
  lastJoinSql: null,
  lastChatSql: null,
  lastSqlSql: null,
  selectedSheets: [],
};

export const els = {};

const previewStore = new Map();
let previewSeq = 0;

export function bindElements() {
  Object.assign(els, {
    error: document.getElementById("error"),
    activeDatasetLabel: document.getElementById("active-dataset-label"),
    datasetId: document.getElementById("dataset-id"),
    uploadDropzone: document.getElementById("upload-dropzone"),
    file: document.getElementById("file"),
    fileList: document.getElementById("file-list"),
    sheetPicker: document.getElementById("sheet-picker"),
    replace: document.getElementById("replace"),
    uploadBtn: document.getElementById("upload-btn"),
    addBtn: document.getElementById("add-btn"),
    sampleBtn: document.getElementById("sample-btn"),
    uploadStatus: document.getElementById("upload-status"),
    importDuckdbFile: document.getElementById("import-duckdb-file"),
    importDatasetId: document.getElementById("import-dataset-id"),
    importDuckdbBtn: document.getElementById("import-duckdb-btn"),
    lockedBox: document.getElementById("locked-box"),
    lockedHint: document.getElementById("locked-hint"),
    lockedRetryBtn: document.getElementById("locked-retry-btn"),
    dataset: document.getElementById("dataset"),
    refreshBtn: document.getElementById("refresh-btn"),
    duplicateBtn: document.getElementById("duplicate-btn"),
    renameDatasetBtn: document.getElementById("rename-dataset-btn"),
    deleteDatasetBtn: document.getElementById("delete-dataset-btn"),
    exportDbBtn: document.getElementById("export-db-btn"),
    checkpointBtn: document.getElementById("checkpoint-btn"),
    diskUsage: document.getElementById("disk-usage"),
    limitsHint: document.getElementById("limits-hint"),
    tableList: document.getElementById("table-list"),
    tableContextEmpty: document.getElementById("table-context-empty"),
    tableContext: document.getElementById("table-context"),
    tableContextName: document.getElementById("table-context-name"),
    renameTableBtn: document.getElementById("rename-table-btn"),
    dropTableBtn: document.getElementById("drop-table-btn"),
    exportTableCsvBtn: document.getElementById("export-table-csv-btn"),
    exportTableXlsxBtn: document.getElementById("export-table-xlsx-btn"),
    exportTableParquetBtn: document.getElementById("export-table-parquet-btn"),
    reviewTypesBtn: document.getElementById("review-types-btn"),
    profileView: document.getElementById("profile-view"),
    tableRowsPeek: document.getElementById("table-rows-peek"),
    tableRowsPeekBody: document.getElementById("table-rows-peek-body"),
    tableRowsPeekMeta: document.getElementById("table-rows-peek-meta"),
    peekPrevBtn: document.getElementById("peek-prev-btn"),
    peekNextBtn: document.getElementById("peek-next-btn"),
    typesEditor: document.getElementById("types-editor"),
    typesTableHolder: document.getElementById("types-table-holder"),
    applyTypesBtn: document.getElementById("apply-types-btn"),
    cancelTypesBtn: document.getElementById("cancel-types-btn"),
    typesStatus: document.getElementById("types-status"),
    joinActiveDataset: document.getElementById("join-active-dataset"),
    joinLeft: document.getElementById("join-left"),
    joinRight: document.getElementById("join-right"),
    joinSteps: document.getElementById("join-steps"),
    addJoinStepBtn: document.getElementById("add-join-step-btn"),
    joinSuggestions: document.getElementById("join-suggestions"),
    keyRows: document.getElementById("key-rows"),
    addKeyBtn: document.getElementById("add-key-btn"),
    sharedHint: document.getElementById("shared-hint"),
    joinHow: document.getElementById("join-how"),
    joinAs: document.getElementById("join-as"),
    joinRecipeName: document.getElementById("join-recipe-name"),
    estimateBtn: document.getElementById("estimate-btn"),
    joinBtn: document.getElementById("join-btn"),
    joinStatus: document.getElementById("join-status"),
    estimateResult: document.getElementById("estimate-result"),
    recipeList: document.getElementById("recipe-list"),
    recipeEmpty: document.getElementById("recipe-empty"),
    joinPreview: document.getElementById("join-preview"),
    joinChart: document.getElementById("join-chart"),
    exportJoinCsvBtn: document.getElementById("export-join-csv-btn"),
    exportJoinXlsxBtn: document.getElementById("export-join-xlsx-btn"),
    exportJoinParquetBtn: document.getElementById("export-join-parquet-btn"),
    sqlActiveDataset: document.getElementById("sql-active-dataset"),
    sqlInput: document.getElementById("sql-input"),
    sqlChartType: document.getElementById("sql-chart-type"),
    sqlRunPreviewBtn: document.getElementById("sql-run-preview-btn"),
    sqlRunFullBtn: document.getElementById("sql-run-full-btn"),
    sqlCancelBtn: document.getElementById("sql-cancel-btn"),
    sqlPreviewNotice: document.getElementById("sql-preview-notice"),
    sqlPreview: document.getElementById("sql-preview"),
    sqlChart: document.getElementById("sql-chart"),
    exportSqlCsvBtn: document.getElementById("export-sql-csv-btn"),
    exportSqlXlsxBtn: document.getElementById("export-sql-xlsx-btn"),
    exportSqlParquetBtn: document.getElementById("export-sql-parquet-btn"),
    chatActiveDataset: document.getElementById("chat-active-dataset"),
    chatTable: document.getElementById("chat-table"),
    askProfileBtn: document.getElementById("ask-profile-btn"),
    askMissingBtn: document.getElementById("ask-missing-btn"),
    askFilterBtn: document.getElementById("ask-filter-btn"),
    filterColumn: document.getElementById("filter-column"),
    filterOp: document.getElementById("filter-op"),
    filterValue: document.getElementById("filter-value"),
    filterApplyBtn: document.getElementById("filter-apply-btn"),
    chartType: document.getElementById("chart-type"),
    chatLog: document.getElementById("chat-log"),
    lastSqlPanel: document.getElementById("last-sql-panel"),
    lastSqlInput: document.getElementById("last-sql-input"),
    runLastSqlBtn: document.getElementById("run-last-sql-btn"),
    copyLastSqlBtn: document.getElementById("copy-last-sql-btn"),
    prompt: document.getElementById("prompt"),
    askBtn: document.getElementById("ask-btn"),
    cancelQueryBtn: document.getElementById("cancel-query-btn"),
    clearHistoryBtn: document.getElementById("clear-history-btn"),
    previewNotice: document.getElementById("preview-notice"),
    chart: document.getElementById("chart"),
    exportChatCsvBtn: document.getElementById("export-chat-csv-btn"),
    exportChatXlsxBtn: document.getElementById("export-chat-xlsx-btn"),
    exportChatParquetBtn: document.getElementById("export-chat-parquet-btn"),
    apiToken: document.getElementById("api-token"),
    saveTokenBtn: document.getElementById("save-token-btn"),
    themeToggle: document.getElementById("theme-toggle"),
  });
}

export function setError(msg, requestId) {
  if (!els.error) return;
  if (!msg) {
    els.error.innerHTML = "";
    return;
  }
  let text = msg;
  const rid = requestId || null;
  if (rid) {
    const suffix = ` (request id: ${rid})`;
    if (text.endsWith(suffix)) text = text.slice(0, -suffix.length);
    els.error.innerHTML =
      `${escapeHtml(text)}<div class="hint" style="margin-top:0.35rem;">request id: ${escapeHtml(rid)}</div>`;
  } else {
    els.error.textContent = text;
  }
}

export function setErrorFrom(err) {
  if (!err) {
    setError("");
    return;
  }
  const msg = err.message || String(err);
  setError(msg, err.requestId || null);
}

export function isDatasetLocked() {
  return state.tables.includes("(locked)");
}

export function fillSelect(sel, values, preferred) {
  const current = preferred !== undefined ? preferred : sel.value;
  sel.innerHTML = "";
  values.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  });
  if (current && values.includes(current)) sel.value = current;
}

export function columnsFor(table) {
  return (state.schema[table] || []).map((c) => c.name);
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderTableBody(rows, columns, end) {
  const cols = columns && columns.length ? columns : Object.keys(rows[0] || {});
  return rows
    .slice(0, end)
    .map((r) => `<tr>${cols.map((c) => `<td>${escapeHtml(r[c] ?? "")}</td>`).join("")}</tr>`)
    .join("");
}

export function renderPreviewTable(rows, columns, options = {}) {
  if (!rows || !rows.length) return "<p class='hint'>No rows</p>";
  const pageSize = options.pageSize || 50;
  const cols = columns && columns.length ? columns : Object.keys(rows[0] || {});
  const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const total = rows.length;

  if (total <= pageSize && !options.page) {
    const body = renderTableBody(rows, cols, total);
    return `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  const visible = options.page ? Math.min(options.page * pageSize, total) : pageSize;
  const id = `preview-${++previewSeq}`;
  previewStore.set(id, { rows, columns: cols, visible, pageSize });
  const body = renderTableBody(rows, cols, visible);
  const footer = visible < total
    ? `<div class="preview-pager hint" style="margin-top:0.5rem;">` +
      `Showing 1–${visible} of ${total} ` +
      `<button type="button" class="secondary small" data-preview-more="${id}">Show more</button>` +
      `</div>`
    : `<div class="preview-pager hint" style="margin-top:0.5rem;">Showing 1–${total} of ${total}</div>`;
  return (
    `<div class="table-scroll" data-preview-id="${id}">` +
    `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>${footer}`
  );
}

document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-preview-more]");
  if (!btn) return;
  const id = btn.dataset.previewMore;
  const data = previewStore.get(id);
  if (!data) return;
  data.visible = Math.min(data.visible + data.pageSize, data.rows.length);
  const scroll = document.querySelector(`[data-preview-id="${id}"]`);
  if (!scroll) return;
  const tbody = scroll.querySelector("tbody");
  if (tbody) tbody.innerHTML = renderTableBody(data.rows, data.columns, data.visible);
  const pager = scroll.nextElementSibling;
  if (pager && pager.classList.contains("preview-pager")) {
    if (data.visible >= data.rows.length) {
      pager.innerHTML = `Showing 1–${data.rows.length} of ${data.rows.length}`;
    } else {
      pager.innerHTML =
        `Showing 1–${data.visible} of ${data.rows.length} ` +
        `<button type="button" class="secondary small" data-preview-more="${id}">Show more</button>`;
    }
  }
});

export function requireDataset() {
  const id = els.dataset?.value;
  if (!id) throw new Error("Select a dataset first");
  return id;
}

export function syncActiveDatasetBadges() {
  const id = els.dataset?.value || "none";
  if (els.activeDatasetLabel) els.activeDatasetLabel.textContent = id;
  if (els.joinActiveDataset) els.joinActiveDataset.textContent = id;
  if (els.chatActiveDataset) els.chatActiveDataset.textContent = id;
  if (els.sqlActiveDataset) els.sqlActiveDataset.textContent = id;
}

export function updateLockedUi() {
  const locked = isDatasetLocked();
  if (els.lockedBox) els.lockedBox.style.display = locked ? "block" : "none";
  if (els.lockedHint && locked) {
    const id = els.dataset?.value || "dataset";
    els.lockedHint.textContent =
      `Dataset "${id}" is locked — close CLI, Jupyter, or other apps using the DuckDB file, then Retry.`;
  }
  const disable = locked;
  [els.joinBtn, els.estimateBtn, els.askBtn, els.sqlRunPreviewBtn, els.sqlRunFullBtn,
    els.runLastSqlBtn, els.filterApplyBtn, els.askFilterBtn].forEach((el) => {
    if (el) el.disabled = disable;
  });
}

export function showLastSqlPanel(sql) {
  if (!els.lastSqlPanel || !els.lastSqlInput) return;
  if (!sql) {
    els.lastSqlPanel.style.display = "none";
    return;
  }
  els.lastSqlPanel.style.display = "block";
  els.lastSqlInput.value = sql;
}
