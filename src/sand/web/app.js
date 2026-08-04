const SAND_TYPES = ["integer", "float", "boolean", "datetime", "date", "string", "unknown"];

const state = {
  datasets: [],
  schema: {},
  tables: [],
  currentTable: null,
  typesPlan: null,
  lastJoinSql: null,
  lastChatSql: null,
};

const els = {
  error: document.getElementById("error"),
  activeDatasetLabel: document.getElementById("active-dataset-label"),

  // Data tab
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

  // Join tab
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

  // Chat tab
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
};

// ---------- shared helpers ----------

function setError(msg) {
  els.error.textContent = msg || "";
}

function detailMessage(data) {
  if (!data) return "Request failed";
  if (typeof data.detail === "string") return data.detail;
  if (data.detail && typeof data.detail === "object") {
    const msg = data.detail.message || JSON.stringify(data.detail);
    if (data.detail.offline_actions && data.detail.offline_actions.length) {
      return `${msg} Offline asks: ${data.detail.offline_actions.join(", ")}.`;
    }
    return msg;
  }
  return "Request failed";
}

async function readJsonSafe(res) {
  try {
    return await res.json();
  } catch (_err) {
    return null;
  }
}

async function apiGet(path) {
  const res = await fetch(path);
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

async function apiJson(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

async function apiForm(path, formData) {
  const res = await fetch(path, { method: "POST", body: formData });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function downloadExport(fmt, body, filename) {
  const res = await fetch(`/export/${fmt}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await readJsonSafe(res);
    throw new Error(detailMessage(data));
  }
  const blob = await res.blob();
  downloadBlob(blob, filename);
}

function fillSelect(sel, values, preferred) {
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

function columnsFor(table) {
  return (state.schema[table] || []).map((c) => c.name);
}

function renderPreviewTable(rows, columns) {
  if (!rows || !rows.length) return "<p class='hint'>No rows returned.</p>";
  const cols = columns || Object.keys(rows[0]);
  const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
  const body = rows
    .slice(0, 50)
    .map((r) => `<tr>${cols.map((c) => `<td>${r[c] == null ? "" : escapeHtml(r[c])}</td>`).join("")}</tr>`)
    .join("");
  return `<table class="preview"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function requireDataset() {
  if (!els.dataset.value) {
    setError("Select or create a dataset on the Data tab first.");
    return null;
  }
  return els.dataset.value;
}

function syncActiveDatasetBadges() {
  const id = els.dataset.value || "none";
  els.activeDatasetLabel.textContent = id;
  els.joinActiveDataset.textContent = els.dataset.value || "none — pick one on the Data tab";
  els.chatActiveDataset.textContent = els.dataset.value || "none — pick one on the Data tab";
}

// ---------- datasets / schema ----------

async function refreshDatasets(selectId) {
  setError("");
  const payload = await apiGet("/datasets");
  const data = Array.isArray(payload) ? payload : (payload.datasets || []);
  state.datasets = data;
  const emptyHint = document.getElementById("empty-hint");
  const orphanBox = document.getElementById("orphan-box");
  const orphanHint = document.getElementById("orphan-hint");
  const orphanActions = document.getElementById("orphan-actions");
  if (emptyHint) {
    if (payload.empty) {
      emptyHint.style.display = "block";
      emptyHint.textContent = payload.hint || "No datasets yet — upload files or Load sample shop.";
    } else {
      emptyHint.style.display = "none";
    }
  }
  if (orphanBox && orphanHint && orphanActions) {
    const orphans = payload.orphans || [];
    if (orphans.length) {
      orphanBox.style.display = "block";
      orphanHint.textContent =
        `Found legacy SQLite file(s). Re-ingest into DuckDB if needed, or delete the leftovers:`;
      orphanActions.innerHTML = "";
      orphans.forEach((o) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "secondary small";
        btn.textContent = `Delete ${o.stem}.db`;
        btn.addEventListener("click", async () => {
          if (!confirm(`Delete legacy ${o.stem}.db?`)) return;
          try {
            const res = await fetch(`/datasets/orphans/${encodeURIComponent(o.stem)}`, { method: "DELETE" });
            const data = await readJsonSafe(res);
            if (!res.ok) throw new Error(detailMessage(data));
            await refreshDatasets(els.dataset.value);
          } catch (err) {
            setError(err.message || String(err));
          }
        });
        orphanActions.appendChild(btn);
      });
    } else {
      orphanBox.style.display = "none";
    }
  }
  els.dataset.innerHTML = "";
  data.forEach((d) => {
    const opt = document.createElement("option");
    opt.value = d.id;
    const locked = (d.tables || []).includes("(locked)");
    opt.textContent = `${d.id} (${locked ? "locked" : d.tables.length + " tables"})`;
    els.dataset.appendChild(opt);
  });
  if (selectId && data.some((d) => d.id === selectId)) els.dataset.value = selectId;
  if (els.dataset.value) {
    await loadSchema();
  } else {
    state.schema = {};
    state.tables = [];
    renderTableList();
    clearTableContext();
    fillSelect(els.joinLeft, []);
    fillSelect(els.joinRight, []);
    els.recipeList.innerHTML = "";
    els.recipeEmpty.style.display = "block";
    els.chatLog.innerHTML = "";
    fillSelect(els.chatTable, []);
  }
  syncActiveDatasetBadges();
}

async function loadSchema() {
  const id = els.dataset.value;
  if (!id) return;
  const data = await apiGet(`/datasets/${encodeURIComponent(id)}/schema`);
  state.schema = data.schema || {};
  state.tables = data.tables || [];
  els.datasetId.value = id;

  renderTableList();
  if (state.currentTable && !state.tables.includes(state.currentTable)) {
    clearTableContext();
  }

  const prevL = els.joinLeft.value;
  const prevR = els.joinRight.value;
  fillSelect(els.joinLeft, state.tables, prevL);
  fillSelect(els.joinRight, state.tables, prevR);
  if (state.tables.length > 1 && els.joinLeft.value === els.joinRight.value) {
    els.joinRight.value = state.tables.find((t) => t !== els.joinLeft.value) || state.tables[1];
  }
  if (!els.joinLeft.value && state.tables[0]) els.joinLeft.value = state.tables[0];
  if (!els.joinRight.value && state.tables[1]) els.joinRight.value = state.tables[1];
  await onJoinTablesChanged();

  fillSelect(els.chatTable, ["", ...state.tables], els.chatTable.value);
  els.chatTable.options[0].textContent = "(auto — first table)";
  refreshAskColumnSelects();

  syncActiveDatasetBadges();
  await Promise.all([loadRecipes(), loadChatHistory()]);
}

function refreshAskColumnSelects() {
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
}

function renderTableList() {
  els.tableList.innerHTML = "";
  if (!state.tables.length) {
    els.tableList.innerHTML = "<li class='hint' style='cursor:default;'>No tables yet — upload a spreadsheet.</li>";
    return;
  }
  state.tables.forEach((t) => {
    const cols = columnsFor(t);
    const li = document.createElement("li");
    li.className = t === state.currentTable ? "active" : "";
    li.innerHTML = `<span>${escapeHtml(t)}</span><span class="cols-hint">${cols.length} cols</span>`;
    li.addEventListener("click", () => selectTable(t));
    els.tableList.appendChild(li);
  });
}

function clearTableContext() {
  state.currentTable = null;
  state.typesPlan = null;
  els.tableContextEmpty.style.display = "block";
  els.tableContext.style.display = "none";
  els.typesEditor.style.display = "none";
  renderTableList();
}

async function selectTable(table) {
  setError("");
  state.currentTable = table;
  state.typesPlan = null;
  els.typesEditor.style.display = "none";
  els.tableContextEmpty.style.display = "none";
  els.tableContext.style.display = "block";
  els.tableContextName.textContent = table;
  renderTableList();
  els.profileView.innerHTML = "<p class='hint'>Loading profile…</p>";
  try {
    await loadProfile(table);
  } catch (err) {
    setError(err.message || String(err));
    els.profileView.innerHTML = "";
  }
}

async function loadProfile(table) {
  const id = els.dataset.value;
  const data = await apiGet(`/datasets/${encodeURIComponent(id)}/profile/${encodeURIComponent(table)}`);
  const rows = data.profile || [];
  const samples = data.samples || {};
  let html = "";
  if (!rows.length) {
    html = "<p class='hint'>No columns found.</p>";
  } else {
    html += "<table class='profile-table'><thead><tr><th>Column</th><th>Type</th><th>Rows</th><th>Nulls</th><th>Null %</th><th>Distinct</th><th>Min</th><th>Max</th><th>Sample values</th></tr></thead><tbody>";
    rows.forEach((r) => {
      const sample = (samples[r.column] || []).map((v) => `<span>${escapeHtml(v)}</span>`).join("");
      html += `<tr>
        <td><strong>${escapeHtml(r.column)}</strong></td>
        <td><span class="badge">${escapeHtml(r.type)}</span></td>
        <td>${r.rows}</td>
        <td>${r.nulls}</td>
        <td>${r.null_pct}%</td>
        <td>${r.distinct}</td>
        <td>${r.min == null ? "" : escapeHtml(r.min)}</td>
        <td>${r.max == null ? "" : escapeHtml(r.max)}</td>
        <td><div class="sample-list">${sample || "<span>—</span>"}</div></td>
      </tr>`;
    });
    html += "</tbody></table>";
  }
  els.profileView.innerHTML = html;
}

// ---------- table actions ----------

els.renameTableBtn.addEventListener("click", async () => {
  if (!state.currentTable) return;
  const newName = prompt(`Rename table "${state.currentTable}" to:`, state.currentTable);
  if (!newName || newName.trim() === state.currentTable) return;
  setError("");
  try {
    const data = await apiJson(
      `/datasets/${encodeURIComponent(els.dataset.value)}/tables/${encodeURIComponent(state.currentTable)}/rename`,
      "POST",
      { new_name: newName.trim() }
    );
    await loadSchema();
    await selectTable(data.new);
  } catch (err) {
    setError(err.message || String(err));
  }
});

els.dropTableBtn.addEventListener("click", async () => {
  if (!state.currentTable) return;
  if (!confirm(`Drop table "${state.currentTable}"? This cannot be undone.`)) return;
  setError("");
  try {
    await fetch(`/datasets/${encodeURIComponent(els.dataset.value)}/tables/${encodeURIComponent(state.currentTable)}`, {
      method: "DELETE",
    }).then(async (res) => {
      const data = await readJsonSafe(res);
      if (!res.ok) throw new Error(detailMessage(data));
    });
    clearTableContext();
    await loadSchema();
  } catch (err) {
    setError(err.message || String(err));
  }
});

els.exportTableCsvBtn.addEventListener("click", () => exportTable("csv"));
els.exportTableXlsxBtn.addEventListener("click", () => exportTable("xlsx"));

async function exportTable(fmt) {
  if (!state.currentTable) return;
  setError("");
  try {
    await downloadExport(
      fmt,
      { dataset_id: els.dataset.value, table: state.currentTable },
      `${state.currentTable}.${fmt}`
    );
  } catch (err) {
    setError(err.message || String(err));
  }
}

els.exportDbBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  setError("");
  try {
    await downloadExport("db", { dataset_id: id }, `${id}.duckdb`);
  } catch (err) {
    setError(err.message || String(err));
  }
});

els.duplicateBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  const newId = prompt(`Duplicate "${id}" as new dataset id:`, `${id}_copy`);
  if (!newId) return;
  setError("");
  try {
    const data = await apiJson(`/datasets/${encodeURIComponent(id)}/duplicate?new_id=${encodeURIComponent(newId.trim())}`, "POST");
    await refreshDatasets(data.dataset_id);
  } catch (err) {
    setError(err.message || String(err));
  }
});

els.deleteDatasetBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  if (!confirm(`Delete dataset "${id}" and all its tables? This cannot be undone.`)) return;
  setError("");
  try {
    const res = await fetch(`/datasets/${encodeURIComponent(id)}`, { method: "DELETE" });
    const data = await readJsonSafe(res);
    if (!res.ok) throw new Error(detailMessage(data));
    clearTableContext();
    await refreshDatasets();
  } catch (err) {
    setError(err.message || String(err));
  }
});

// ---------- types review ----------

els.reviewTypesBtn.addEventListener("click", async () => {
  if (!state.currentTable) return;
  setError("");
  els.typesStatus.textContent = "";
  try {
    const data = await apiGet(`/datasets/${encodeURIComponent(els.dataset.value)}/types/${encodeURIComponent(state.currentTable)}`);
    state.typesPlan = data.plan;
    renderTypesEditor(data.plan);
    els.typesEditor.style.display = "block";
  } catch (err) {
    setError(err.message || String(err));
  }
});

function renderTypesEditor(plan) {
  let html = "<table class='type-table'><thead><tr><th>Column</th><th>Inferred</th><th>Type override</th><th>Nulls</th><th>Distinct</th><th>Samples</th></tr></thead><tbody>";
  plan.columns.forEach((col) => {
    const current = col.override || col.inferred;
    const options = SAND_TYPES.map((t) => `<option value="${t}" ${t === current ? "selected" : ""}>${t}</option>`).join("");
    const samples = (col.sample_values || []).slice(0, 4).map((v) => escapeHtml(v)).join(", ");
    html += `<tr data-column="${escapeHtml(col.name)}">
      <td><strong>${escapeHtml(col.name)}</strong></td>
      <td><span class="badge">${escapeHtml(col.inferred)}</span></td>
      <td><select class="type-override">${options}</select></td>
      <td>${col.null_count}</td>
      <td>${col.distinct_count}</td>
      <td class="samples">${samples || "—"}</td>
    </tr>`;
  });
  html += "</tbody></table>";
  els.typesTableHolder.innerHTML = html;
}

els.cancelTypesBtn.addEventListener("click", () => {
  els.typesEditor.style.display = "none";
  els.typesStatus.textContent = "";
});

els.applyTypesBtn.addEventListener("click", async () => {
  if (!state.currentTable || !state.typesPlan) return;
  const columns = [];
  els.typesTableHolder.querySelectorAll("tr[data-column]").forEach((tr) => {
    const name = tr.dataset.column;
    const type = tr.querySelector(".type-override").value;
    columns.push({ name, type });
  });
  setError("");
  els.typesStatus.textContent = "Applying…";
  els.applyTypesBtn.disabled = true;
  try {
    const data = await apiJson(
      `/datasets/${encodeURIComponent(els.dataset.value)}/types/${encodeURIComponent(state.currentTable)}`,
      "POST",
      { columns }
    );
    els.typesStatus.textContent = `Applied types to ${data.row_count} rows.`;
    await loadProfile(state.currentTable);
    await loadSchema();
  } catch (err) {
    setError(err.message || String(err));
    els.typesStatus.textContent = "";
  } finally {
    els.applyTypesBtn.disabled = false;
  }
});

// ---------- upload ----------

els.file.addEventListener("change", () => {
  els.fileList.innerHTML = Array.from(els.file.files)
    .map((f) => `<li>${escapeHtml(f.name)} <span class="hint">(${Math.round(f.size / 1024)} KB)</span></li>`)
    .join("");
});

async function uploadFiles({ append }) {
  setError("");
  els.uploadStatus.textContent = "";
  if (!els.file.files.length) {
    setError("Choose one or more spreadsheet files first.");
    return;
  }
  const ds = els.datasetId.value.trim() || (append ? els.dataset.value : "");
  if (append && !ds) {
    setError("Select or name a dataset to add files into.");
    return;
  }
  const replace = els.replace.checked;

  try {
    if (append) {
      els.uploadStatus.textContent = "Adding…";
      let lastDatasetId = ds;
      let addedCount = 0;
      for (const f of Array.from(els.file.files)) {
        const form = new FormData();
        form.append("file", f);
        form.append("replace", replace ? "true" : "false");
        const data = await apiForm(`/datasets/${encodeURIComponent(ds)}/tables`, form);
        lastDatasetId = data.dataset_id;
        addedCount += data.tables.length;
      }
      els.uploadStatus.textContent = `Added ${addedCount} table(s) to ${lastDatasetId}`;
      els.file.value = "";
      els.fileList.innerHTML = "";
      await refreshDatasets(lastDatasetId);
      return;
    }

    const form = new FormData();
    if (ds) form.append("dataset_id", ds);
    form.append("replace", replace ? "true" : "false");
    Array.from(els.file.files).forEach((f) => form.append("files", f));
    els.uploadStatus.textContent = "Uploading…";
    const data = await apiForm("/datasets/upload", form);
    els.uploadStatus.textContent = `Loaded ${data.tables.length} table(s) into ${data.dataset_id}: ${data.tables.map((t) => t.name).join(", ")}`;
    els.datasetId.value = data.dataset_id;
    els.file.value = "";
    els.fileList.innerHTML = "";
    await refreshDatasets(data.dataset_id);
  } catch (err) {
    setError(err.message || String(err));
    els.uploadStatus.textContent = "";
  }
}

els.uploadBtn.addEventListener("click", () => uploadFiles({ append: false }));
els.addBtn.addEventListener("click", () => uploadFiles({ append: true }));

els.sampleBtn.addEventListener("click", async () => {
  setError("");
  els.uploadStatus.textContent = "Loading sample…";
  const dsId = els.datasetId.value.trim() || "shop";
  try {
    const data = await apiJson(`/datasets/samples/shop?dataset_id=${encodeURIComponent(dsId)}`, "POST");
    els.uploadStatus.textContent = `Loaded sample dataset "${data.dataset_id}" with tables: ${data.tables.map((t) => t.name).join(", ")}`;
    els.datasetId.value = data.dataset_id;
    await refreshDatasets(data.dataset_id);
  } catch (err) {
    setError(err.message || String(err));
    els.uploadStatus.textContent = "";
  }
});

els.refreshBtn.addEventListener("click", () => refreshDatasets(els.dataset.value));
els.dataset.addEventListener("change", () => {
  clearTableContext();
  state.lastJoinSql = null;
  state.lastChatSql = null;
  els.joinPreview.innerHTML = "<p class='hint'>Run a join to see rows here.</p>";
  els.joinChart.innerHTML = "";
  els.estimateResult.innerHTML = "";
  els.joinStatus.textContent = "";
  els.previewNotice.classList.remove("visible");
  els.chart.innerHTML = "";
  loadSchema();
});

// ---------- join tab ----------

function addKeyRow(leftCol, rightCol) {
  const row = document.createElement("div");
  row.className = "key-row";
  const left = document.createElement("select");
  const right = document.createElement("select");
  left.className = "key-left";
  right.className = "key-right";
  fillSelect(left, columnsFor(els.joinLeft.value), leftCol);
  fillSelect(right, columnsFor(els.joinRight.value), rightCol);
  const eq = document.createElement("div");
  eq.className = "equals";
  eq.textContent = "=";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    if (els.keyRows.children.length > 1) row.remove();
  });
  row.append(left, eq, right, remove);
  els.keyRows.appendChild(row);
}

function refreshKeyColumnOptions() {
  const leftCols = columnsFor(els.joinLeft.value);
  const rightCols = columnsFor(els.joinRight.value);
  els.keyRows.querySelectorAll(".key-row").forEach((row) => {
    const l = row.querySelector(".key-left");
    const r = row.querySelector(".key-right");
    fillSelect(l, leftCols, l.value);
    fillSelect(r, rightCols, r.value);
  });
  const shared = leftCols.filter((c) => rightCols.includes(c));
  els.sharedHint.textContent = shared.length
    ? `Shared columns: ${shared.join(", ")}`
    : "No shared column names — map left=right explicitly, or use a suggestion above.";
}

function resetKeyRows(leftCol, rightCol) {
  els.keyRows.innerHTML = "";
  const leftCols = columnsFor(els.joinLeft.value);
  const rightCols = columnsFor(els.joinRight.value);
  if (leftCol && rightCol) {
    addKeyRow(leftCol, rightCol);
  } else {
    const shared = leftCols.filter((c) => rightCols.includes(c));
    if (shared.length) addKeyRow(shared[0], shared[0]);
    else addKeyRow(leftCols[0], rightCols[0]);
  }
  refreshKeyColumnOptions();
}

function collectJoinKeys() {
  const keys = [];
  els.keyRows.querySelectorAll(".key-row").forEach((row) => {
    const left = row.querySelector(".key-left").value;
    const right = row.querySelector(".key-right").value;
    if (left && right) keys.push({ left, right });
  });
  return keys;
}

function renderJoinSuggestions(suggestions) {
  els.joinSuggestions.innerHTML = "";
  if (!suggestions.length) {
    els.joinSuggestions.innerHTML = "<span class='hint'>No obvious key matches found — map columns manually below.</span>";
    return;
  }
  suggestions.forEach((s) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.innerHTML = `${escapeHtml(s.left)} = ${escapeHtml(s.right)}<span class="score">${Math.round(s.score * 100)}%</span>`;
    chip.title = s.reason;
    chip.addEventListener("click", () => addKeyRow(s.left, s.right));
    els.joinSuggestions.appendChild(chip);
  });
}

async function onJoinTablesChanged() {
  const left = els.joinLeft.value;
  const right = els.joinRight.value;
  if (!left || !right || left === right || !els.dataset.value) {
    resetKeyRows();
    els.joinSuggestions.innerHTML = "";
    return;
  }
  try {
    const data = await apiJson("/query/join/suggest", "POST", { dataset_id: els.dataset.value, left, right });
    const suggestions = data.suggestions || [];
    renderJoinSuggestions(suggestions);
    if (suggestions.length) resetKeyRows(suggestions[0].left, suggestions[0].right);
    else resetKeyRows();
  } catch (err) {
    els.joinSuggestions.innerHTML = "";
    resetKeyRows();
  }
}

els.joinLeft.addEventListener("change", onJoinTablesChanged);
els.joinRight.addEventListener("change", onJoinTablesChanged);
els.addKeyBtn.addEventListener("click", () => addKeyRow());

function buildJoinSpec() {
  const on = collectJoinKeys();
  return {
    left: els.joinLeft.value,
    right: els.joinRight.value,
    on,
    how: els.joinHow.value,
  };
}

function renderEstimate(estimate) {
  const warnHtml = estimate.warning ? `<div class="warn-box">⚠ ${escapeHtml(estimate.warning)}</div>` : "";
  els.estimateResult.innerHTML = `
    <div class="kv-grid">
      <div class="kv"><div class="label">Left rows</div><div class="value">${estimate.left_rows}</div></div>
      <div class="kv"><div class="label">Right rows</div><div class="value">${estimate.right_rows}</div></div>
      <div class="kv"><div class="label">Estimated result rows</div><div class="value">${estimate.estimated_rows ?? "—"}</div></div>
      <div class="kv"><div class="label">Multiplicity</div><div class="value">${escapeHtml(estimate.multiplicity)}</div></div>
    </div>
    ${warnHtml}
  `;
}

els.estimateBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  const on = collectJoinKeys();
  if (!on.length) { setError("Add at least one join key."); return; }
  setError("");
  els.estimateBtn.disabled = true;
  try {
    const data = await apiJson("/query/join/estimate", "POST", { dataset_id: id, join: buildJoinSpec() });
    renderEstimate(data.estimate);
  } catch (err) {
    setError(err.message || String(err));
  } finally {
    els.estimateBtn.disabled = false;
  }
});

function renderJoinResult(data) {
  state.lastJoinSql = data.sql;
  els.joinStatus.textContent = `Joined ${data.row_count} rows` + (data.as_table ? ` → saved as "${data.as_table}"` : "");
  els.joinPreview.innerHTML = `<div class="sql-box">${escapeHtml(data.sql)}</div>` + renderPreviewTable(data.rows, data.columns);
  if (data.estimate) renderEstimate(data.estimate);
  els.joinChart.innerHTML = "";
  if (data.rows && data.rows.length) {
    const numeric = data.columns.filter((c) => typeof data.rows[0][c] === "number");
    const categorical = data.columns.filter((c) => typeof data.rows[0][c] !== "number");
    if (numeric.length && categorical.length) {
      Plotly.newPlot(els.joinChart, [{
        type: "bar",
        x: data.rows.slice(0, 30).map((r) => r[categorical[0]]),
        y: data.rows.slice(0, 30).map((r) => r[numeric[0]]),
      }], {
        title: `${numeric[0]} by ${categorical[0]}`,
        template: "plotly_white",
        margin: { t: 40, r: 20, b: 40, l: 40 },
      }, { responsive: true });
    }
  }
}

els.joinBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  if (state.tables.length < 2) { setError("Need at least two tables to join. Upload more spreadsheets."); return; }
  const on = collectJoinKeys();
  if (!on.length) { setError("Add at least one join key."); return; }

  const body = {
    dataset_id: id,
    join: { ...buildJoinSpec(), as_table: els.joinAs.value.trim() || null, limit: 500 },
  };
  setError("");
  els.joinBtn.disabled = true;
  try {
    const data = await apiJson("/query/join", "POST", body);
    renderJoinResult(data);
    if (body.join.as_table) await loadSchema();
  } catch (err) {
    setError(err.message || String(err));
  } finally {
    els.joinBtn.disabled = false;
  }
});

els.saveRecipeBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  const name = els.joinRecipeName.value.trim();
  if (!name) { setError("Give the recipe a name first."); return; }
  const on = collectJoinKeys();
  if (!on.length) { setError("Add at least one join key."); return; }
  setError("");
  try {
    await apiJson("/query/join/recipes", "POST", {
      dataset_id: id,
      name,
      join: { ...buildJoinSpec(), as_table: els.joinAs.value.trim() || null, limit: 500 },
    });
    els.joinStatus.textContent = `Saved recipe "${name}".`;
    await loadRecipes();
  } catch (err) {
    setError(err.message || String(err));
  }
});

async function loadRecipes() {
  const id = els.dataset.value;
  els.recipeList.innerHTML = "";
  if (!id) { els.recipeEmpty.style.display = "block"; return; }
  try {
    const data = await apiGet(`/query/join/recipes/${encodeURIComponent(id)}`);
    const recipes = data.recipes || [];
    els.recipeEmpty.style.display = recipes.length ? "none" : "block";
    recipes.forEach((r) => {
      const li = document.createElement("li");
      const keys = r.spec.on.map((k) => (typeof k === "string" ? k : `${k.left}=${k.right}`)).join(", ");
      li.innerHTML = `
        <div>
          <div><strong>${escapeHtml(r.name)}</strong></div>
          <div class="recipe-meta">${escapeHtml(r.spec.left)} ⋈ ${escapeHtml(r.spec.right)} on ${escapeHtml(keys)} (${escapeHtml(r.spec.how)})</div>
        </div>
        <div class="recipe-actions">
          <button type="button" class="secondary small run-recipe">Run</button>
          <button type="button" class="danger small delete-recipe">Delete</button>
        </div>`;
      li.querySelector(".run-recipe").addEventListener("click", () => runRecipe(r.name));
      li.querySelector(".delete-recipe").addEventListener("click", () => deleteRecipe(r.name));
      els.recipeList.appendChild(li);
    });
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function runRecipe(name) {
  const id = requireDataset();
  if (!id) return;
  setError("");
  try {
    const data = await apiJson("/query/join", "POST", { dataset_id: id, recipe_name: name });
    renderJoinResult(data);
    if (data.as_table) await loadSchema();
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function deleteRecipe(name) {
  const id = requireDataset();
  if (!id) return;
  if (!confirm(`Delete recipe "${name}"?`)) return;
  setError("");
  try {
    const res = await fetch(`/query/join/recipes/${encodeURIComponent(id)}/${encodeURIComponent(name)}`, { method: "DELETE" });
    const data = await readJsonSafe(res);
    if (!res.ok) throw new Error(detailMessage(data));
    await loadRecipes();
  } catch (err) {
    setError(err.message || String(err));
  }
}

els.exportJoinCsvBtn.addEventListener("click", () => exportJoinResult("csv"));
els.exportJoinXlsxBtn.addEventListener("click", () => exportJoinResult("xlsx"));

async function exportJoinResult(fmt) {
  const id = requireDataset();
  if (!id) return;
  if (!state.lastJoinSql) { setError("Run a join first, then export."); return; }
  setError("");
  try {
    await downloadExport(fmt, { dataset_id: id, sql: state.lastJoinSql }, `join_result.${fmt}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

// ---------- chat tab ----------

function addMessage(role, text, meta) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div>${text}</div>` + (meta ? `<div class="meta">${meta}</div>` : "");
  els.chatLog.appendChild(div);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return div;
}

async function loadChatHistory() {
  const id = els.dataset.value;
  els.chatLog.innerHTML = "";
  els.previewNotice.classList.remove("visible");
  state.lastChatSql = null;
  if (!id) return;
  try {
    const data = await apiGet(`/chat/${encodeURIComponent(id)}/history`);
    (data.turns || []).forEach((turn) => {
      const meta = turn.sql ? `SQL: ${turn.sql}` : "";
      addMessage(turn.role === "user" ? "user" : "assistant", escapeHtml(turn.content), meta ? escapeHtml(meta) : "");
      if (turn.sql) state.lastChatSql = turn.sql;
    });
  } catch (err) {
    setError(err.message || String(err));
  }
}

function showPreviewNotice(data) {
  if (!data.is_preview) {
    els.previewNotice.classList.remove("visible");
    return;
  }
  const shown = data.row_count;
  const total = data.full_row_count != null ? data.full_row_count : "?";
  els.previewNotice.innerHTML =
    `Evaluated with LIMIT ${data.evaluated_limit} (${shown} of ${total} rows). ` +
    `<button type="button" class="link" id="run-full-btn">Run full query</button>`;
  els.previewNotice.classList.add("visible");
  document.getElementById("run-full-btn").addEventListener("click", runFullQuery);
}

async function runFullQuery() {
  const id = requireDataset();
  if (!id || !state.lastChatSql) return;
  setError("");
  try {
    const data = await apiJson("/chat", "POST", {
      dataset_id: id,
      run_full: true,
      sql: state.lastChatSql,
      message: "run full",
    });
    addMessage("assistant", escapeHtml(data.summary), `Full result: ${data.row_count} rows`);
    if (data.chart && data.chart.figure) {
      Plotly.newPlot(els.chart, data.chart.figure.data, data.chart.figure.layout, { responsive: true });
    }
    state.lastChatSql = data.sql;
    showPreviewNotice(data);
  } catch (err) {
    setError(err.message || String(err));
  }
}

els.askBtn.addEventListener("click", async () => {
  setError("");
  const message = els.prompt.value.trim();
  if (!message) return;
  const id = requireDataset();
  if (!id) return;
  addMessage("user", escapeHtml(message));
  els.prompt.value = "";
  els.askBtn.disabled = true;
  try {
    const body = { dataset_id: id, message };
    if (els.chartType.value) body.chart_type = els.chartType.value;
    const data = await apiJson("/chat", "POST", body);
    const reason = (data.chart && data.chart.spec && data.chart.spec.reason) || "";
    addMessage("assistant", escapeHtml(data.summary), escapeHtml(`SQL: ${data.sql}\n${reason}`));
    if (data.chart && data.chart.figure) {
      Plotly.newPlot(els.chart, data.chart.figure.data, data.chart.figure.layout, { responsive: true });
    }
    state.lastChatSql = data.sql;
    showPreviewNotice(data);
  } catch (err) {
    setError(err.message || String(err));
  } finally {
    els.askBtn.disabled = false;
  }
});

els.askProfileBtn.addEventListener("click", () => askCommon("profile"));
els.askMissingBtn.addEventListener("click", () => askCommon("missing"));
document.getElementById("ask-topn-btn").addEventListener("click", () => askCommon("top_n"));
document.getElementById("ask-groupby-btn").addEventListener("click", () => askCommon("groupby"));
document.getElementById("ask-ts-btn").addEventListener("click", () => askCommon("time_series"));
els.chatTable.addEventListener("change", refreshAskColumnSelects);

async function askCommon(action) {
  const id = requireDataset();
  if (!id) return;
  setError("");
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
  const labels = {
    profile: "Profile",
    missing: "Missing values",
    top_n: "Top-N",
    groupby: "Group-by",
    time_series: "Time series",
  };
  addMessage("user", `${labels[action] || action}${table ? ` — ${escapeHtml(table)}` : ""}`);
  try {
    const data = await apiJson("/chat/common-ask", "POST", { dataset_id: id, action, table, params });
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

els.clearHistoryBtn.addEventListener("click", async () => {
  const id = requireDataset();
  if (!id) return;
  if (!confirm("Clear chat history for this dataset?")) return;
  setError("");
  try {
    const res = await fetch(`/chat/${encodeURIComponent(id)}/history`, { method: "DELETE" });
    const data = await readJsonSafe(res);
    if (!res.ok) throw new Error(detailMessage(data));
    els.chatLog.innerHTML = "";
    els.previewNotice.classList.remove("visible");
    state.lastChatSql = null;
  } catch (err) {
    setError(err.message || String(err));
  }
});

els.exportChatCsvBtn.addEventListener("click", () => exportChatResult("csv"));
els.exportChatXlsxBtn.addEventListener("click", () => exportChatResult("xlsx"));

async function exportChatResult(fmt) {
  const id = requireDataset();
  if (!id) return;
  if (!state.lastChatSql) { setError("Ask a question first, then export the result."); return; }
  setError("");
  try {
    await downloadExport(fmt, { dataset_id: id, sql: state.lastChatSql }, `chat_result.${fmt}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

// ---------- tabs ----------

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`view-${tab.dataset.view}`).classList.add("active");
  });
});

// ---------- init ----------

refreshDatasets().catch((err) => setError(err.message || String(err)));
apiGet("/health").then((h) => {
  const hint = document.getElementById("llm-hint");
  if (!hint) return;
  if (h.llm_configured && h.llm_reachable !== false) {
    hint.style.display = "none";
  } else if (h.llm_configured && h.llm_reachable === false) {
    hint.style.display = "block";
    hint.textContent =
      "LLM is configured but unreachable — free-text Ask may fail. Use Offline asks, or check SAND_LLM_BASE_URL.";
  } else {
    hint.style.display = "block";
    hint.textContent =
      "No LLM configured — free-text Ask needs SAND_LLM_API_KEY. Use Offline asks above, or point SAND_LLM_BASE_URL at a local OpenAI-compatible server.";
  }
}).catch(() => {});
