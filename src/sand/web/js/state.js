/** Shared DOM/state for the SAND UI. */

export const SAND_TYPES = ["integer", "float", "boolean", "datetime", "date", "string", "unknown"];

export const state = {
  datasets: [],
  schema: {},
  tables: [],
  currentTable: null,
  typesPlan: null,
  lastJoinSql: null,
  lastChatSql: null,
};

export const els = {};

export function bindElements() {
  Object.assign(els, {
    error: document.getElementById("error"),
    activeDatasetLabel: document.getElementById("active-dataset-label"),
    datasetId: document.getElementById("dataset-id"),
    file: document.getElementById("file"),
    fileList: document.getElementById("file-list"),
    replace: document.getElementById("replace"),
    uploadBtn: document.getElementById("upload-btn"),
    addBtn: document.getElementById("add-btn"),
    sampleBtn: document.getElementById("sample-btn"),
    uploadStatus: document.getElementById("upload-status"),
    dataset: document.getElementById("dataset"),
    refreshBtn: document.getElementById("refresh-btn"),
    duplicateBtn: document.getElementById("duplicate-btn"),
    deleteDatasetBtn: document.getElementById("delete-dataset-btn"),
    exportDbBtn: document.getElementById("export-db-btn"),
    checkpointBtn: document.getElementById("checkpoint-btn"),
    diskUsage: document.getElementById("disk-usage"),
    tableList: document.getElementById("table-list"),
    tableContextEmpty: document.getElementById("table-context-empty"),
    tableContext: document.getElementById("table-context"),
    tableContextName: document.getElementById("table-context-name"),
    renameTableBtn: document.getElementById("rename-table-btn"),
    dropTableBtn: document.getElementById("drop-table-btn"),
    exportTableCsvBtn: document.getElementById("export-table-csv-btn"),
    exportTableXlsxBtn: document.getElementById("export-table-xlsx-btn"),
    reviewTypesBtn: document.getElementById("review-types-btn"),
    profileView: document.getElementById("profile-view"),
    typesEditor: document.getElementById("types-editor"),
    typesTableHolder: document.getElementById("types-table-holder"),
    applyTypesBtn: document.getElementById("apply-types-btn"),
    cancelTypesBtn: document.getElementById("cancel-types-btn"),
    typesStatus: document.getElementById("types-status"),
    joinActiveDataset: document.getElementById("join-active-dataset"),
    joinLeft: document.getElementById("join-left"),
    joinRight: document.getElementById("join-right"),
    joinSuggestions: document.getElementById("join-suggestions"),
    keyRows: document.getElementById("key-rows"),
    addKeyBtn: document.getElementById("add-key-btn"),
    sharedHint: document.getElementById("shared-hint"),
    joinHow: document.getElementById("join-how"),
    joinAs: document.getElementById("join-as"),
    joinRecipeName: document.getElementById("join-recipe-name"),
    estimateBtn: document.getElementById("estimate-btn"),
    joinBtn: document.getElementById("join-btn"),
    saveRecipeBtn: document.getElementById("save-recipe-btn"),
    joinStatus: document.getElementById("join-status"),
    estimateResult: document.getElementById("estimate-result"),
    recipeList: document.getElementById("recipe-list"),
    recipeEmpty: document.getElementById("recipe-empty"),
    joinPreview: document.getElementById("join-preview"),
    joinChart: document.getElementById("join-chart"),
    exportJoinCsvBtn: document.getElementById("export-join-csv-btn"),
    exportJoinXlsxBtn: document.getElementById("export-join-xlsx-btn"),
    chatActiveDataset: document.getElementById("chat-active-dataset"),
    chatTable: document.getElementById("chat-table"),
    askProfileBtn: document.getElementById("ask-profile-btn"),
    askMissingBtn: document.getElementById("ask-missing-btn"),
    chartType: document.getElementById("chart-type"),
    chatLog: document.getElementById("chat-log"),
    prompt: document.getElementById("prompt"),
    askBtn: document.getElementById("ask-btn"),
    clearHistoryBtn: document.getElementById("clear-history-btn"),
    previewNotice: document.getElementById("preview-notice"),
    chart: document.getElementById("chart"),
    exportChatCsvBtn: document.getElementById("export-chat-csv-btn"),
    exportChatXlsxBtn: document.getElementById("export-chat-xlsx-btn"),
    apiToken: document.getElementById("api-token"),
    saveTokenBtn: document.getElementById("save-token-btn"),
  });
}

export function setError(msg) {
  if (els.error) els.error.textContent = msg || "";
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

export function renderPreviewTable(rows, columns) {
  if (!rows || !rows.length) return "<p class='hint'>No rows</p>";
  const cols = columns && columns.length ? columns : Object.keys(rows[0] || {});
  const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows
    .slice(0, 50)
    .map((r) => `<tr>${cols.map((c) => `<td>${escapeHtml(r[c] ?? "")}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

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
}
