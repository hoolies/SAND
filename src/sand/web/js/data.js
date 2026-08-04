import {
  apiGet, apiJson, apiForm, apiFormWithProgress, apiDelete, downloadExport, readJsonSafe, detailMessage,
} from "./api.js";
import {
  SAND_TYPES, state, els, setError, setErrorFrom, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, syncActiveDatasetBadges, updateLockedUi, isDatasetLocked,
} from "./state.js";

export let afterSchemaLoad = null;
export function setAfterSchemaLoad(fn) { afterSchemaLoad = fn; }

const PEEK_PAGE = 50;
let peekOffset = 0;
let activeUpload = null;

function setUploadBusy(busy) {
  if (els.uploadBtn) els.uploadBtn.disabled = busy;
  if (els.addBtn) els.addBtn.disabled = busy;
  if (els.uploadCancelBtn) {
    els.uploadCancelBtn.style.display = busy ? "inline-block" : "none";
    els.uploadCancelBtn.disabled = !busy;
  }
}

function cancelActiveUpload() {
  if (activeUpload?.abort) activeUpload.abort();
  activeUpload = null;
  setUploadBusy(false);
  els.uploadStatus.textContent = "";
  setError("Upload cancelled.");
}

async function checkpointBeforeDestructive() {
  const id = els.dataset.value;
  if (!id) return;
  try {
    await apiJson(`/datasets/${encodeURIComponent(id)}/checkpoint`, "POST", {});
  } catch (err) {
    if (!isDatasetLocked()) throw err;
  }
}

async function confirmDestructive(action, target) {
  const msg = `This will checkpoint then permanently ${action} "${target}". Continue?`;
  if (!confirm(msg)) return false;
  try {
    await checkpointBeforeDestructive();
  } catch (err) {
    setErrorFrom(err);
    return false;
  }
  return true;
}

function assignFilesToInput(files) {
  if (!els.file || !files?.length) return;
  const dt = new DataTransfer();
  Array.from(files).forEach((f) => dt.items.add(f));
  els.file.files = dt.files;
  els.file.dispatchEvent(new Event("change", { bubbles: true }));
}

function wireUploadDropzone() {
  const zone = els.uploadDropzone;
  if (!zone) return;
  zone.addEventListener("dragover", (e) => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", (e) => {
    if (!zone.contains(e.relatedTarget)) zone.classList.remove("dragover");
  });
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const files = Array.from(e.dataTransfer?.files || []).filter((f) => {
      const name = f.name.toLowerCase();
      return name.endsWith(".csv") || name.endsWith(".xlsx") || name.endsWith(".parquet");
    });
    if (!files.length) {
      setError("Drop CSV, XLSX, or Parquet files only.");
      return;
    }
    assignFilesToInput(files);
  });
}


async function refreshDatasets(selectId) {
  setError("");
  const payload = await apiGet("/datasets");
  const data = Array.isArray(payload) ? payload : (payload.datasets || []);
  state.datasets = data;
  const emptyHint = document.getElementById("empty-hint");
  const orphanBox = document.getElementById("orphan-box");
  const orphanHint = document.getElementById("orphan-hint");
  const orphanActions = document.getElementById("orphan-actions");
  const diskEl = els.diskUsage || document.getElementById("disk-usage");
  if (diskEl) {
    if (payload.disk_usage_bytes != null) {
      const used = payload.disk_usage_bytes;
      const budget = payload.disk_budget_bytes;
      const usedMb = (used / (1024 * 1024)).toFixed(1);
      diskEl.textContent = budget
        ? `Disk: ${usedMb} MB used of ${(budget / (1024 * 1024)).toFixed(0)} MB budget`
        : `Disk: ${usedMb} MB used`;
      diskEl.className = payload.disk_warning ? "warn-box" : "hint";
      if (payload.disk_warning) diskEl.textContent = payload.disk_warning;
    } else {
      diskEl.textContent = "";
    }
  }
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
      await apiDelete(`/datasets/orphans/${encodeURIComponent(o.stem)}`);
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
  updateLockedUi();
}

async function loadSchema() {
  const id = els.dataset.value;
  if (!id) return;
  const data = await apiGet(`/datasets/${encodeURIComponent(id)}/schema`);
  state.schema = data.schema || {};
  state.tables = data.tables || [];
  state.lineage = data.lineage || {};
  els.datasetId.value = id;

  renderTableList();
  updateLockedUi();
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
  syncActiveDatasetBadges();
  if (typeof afterSchemaLoad === "function") await afterSchemaLoad();
}


function renderTableList() {
  els.tableList.innerHTML = "";
  if (!state.tables.length) {
    els.tableList.innerHTML = "<li class='hint' style='cursor:default;'>No tables yet — upload a spreadsheet.</li>";
    return;
  }
  state.tables.forEach((t) => {
    const cols = columnsFor(t);
    const lin = state.lineage[t] || {};
    const metaParts = [];
    if (lin.source_file) metaParts.push(escapeHtml(lin.source_file));
    if (lin.sheet_name) metaParts.push(`sheet: ${escapeHtml(lin.sheet_name)}`);
    if (lin.row_count != null) metaParts.push(`${lin.row_count} rows`);
    const meta = metaParts.length ? `<div class="recipe-meta">${metaParts.join(" · ")}</div>` : "";
    const li = document.createElement("li");
    li.className = t === state.currentTable ? "active" : "";
    li.innerHTML =
      `<span>${escapeHtml(t)}</span>` +
      `<span class="cols-hint">${cols.length} cols</span>${meta}`;
    li.addEventListener("click", () => selectTable(t));
    els.tableList.appendChild(li);
  });
}

function clearTableContext() {
  state.currentTable = null;
  state.typesPlan = null;
  peekOffset = 0;
  els.tableContextEmpty.style.display = "block";
  els.tableContext.style.display = "none";
  els.typesEditor.style.display = "none";
  if (els.tableRowsPeek) els.tableRowsPeek.style.display = "none";
  if (els.tableRowsPeekBody) els.tableRowsPeekBody.innerHTML = "";
  if (els.tableRowsPeekMeta) els.tableRowsPeekMeta.textContent = "";
  renderTableList();
}

async function loadTableRowsPeek(table, offset = 0) {
  if (!els.tableRowsPeek || !els.tableRowsPeekBody) return;
  const id = els.dataset.value;
  if (!id || !table) return;
  peekOffset = offset;
  els.tableRowsPeek.style.display = "block";
  els.tableRowsPeekBody.innerHTML = "<p class='hint'>Loading sample rows…</p>";
  if (els.tableRowsPeekMeta) els.tableRowsPeekMeta.textContent = "";
  try {
    const data = await apiGet(
      `/datasets/${encodeURIComponent(id)}/rows/${encodeURIComponent(table)}?limit=${PEEK_PAGE}&offset=${offset}`,
    );
    const rows = data.rows || [];
    const columns = data.columns || (rows[0] ? Object.keys(rows[0]) : []);
    els.tableRowsPeekBody.innerHTML = renderPreviewTable(rows, columns);
    const total = data.total_count;
    const start = offset + 1;
    const end = offset + rows.length;
    if (els.tableRowsPeekMeta) {
      els.tableRowsPeekMeta.textContent = rows.length
        ? (total != null
          ? `Rows ${start}–${end} of ${total}`
          : `Rows ${start}–${end}`)
        : "No rows in this table.";
    }
    if (els.peekPrevBtn) els.peekPrevBtn.disabled = offset <= 0;
    if (els.peekNextBtn) {
      els.peekNextBtn.disabled = rows.length < PEEK_PAGE || (total != null && end >= total);
    }
  } catch (err) {
    if (err.status === 404) {
      els.tableRowsPeekBody.innerHTML = "<p class='hint'>Sample rows not available yet.</p>";
      if (els.peekPrevBtn) els.peekPrevBtn.disabled = true;
      if (els.peekNextBtn) els.peekNextBtn.disabled = true;
      return;
    }
    els.tableRowsPeekBody.innerHTML = "";
    els.tableRowsPeek.style.display = "none";
    throw err;
  }
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
    await loadTableRowsPeek(table, 0);
  } catch (err) {
    setErrorFrom(err);
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





async function loadXlsxSheets() {
  if (!els.sheetPicker || !els.file?.files?.length) {
    if (els.sheetPicker) {
      els.sheetPicker.style.display = "none";
      els.sheetPicker.innerHTML = "<label>Excel sheets (optional — leave unchecked to ingest all)</label>";
    }
    state.selectedSheets = [];
    return;
  }
  const xlsxFiles = Array.from(els.file.files).filter((f) => f.name.toLowerCase().endsWith(".xlsx"));
  if (!xlsxFiles.length) {
    els.sheetPicker.style.display = "none";
    els.sheetPicker.innerHTML = "<label>Excel sheets (optional — leave unchecked to ingest all)</label>";
    state.selectedSheets = [];
    return;
  }
  els.sheetPicker.style.display = "block";
  els.sheetPicker.innerHTML = "<label>Excel sheets (optional — leave unchecked to ingest all)</label>";
  state.selectedSheets = [];
  for (const f of xlsxFiles) {
    const form = new FormData();
    form.append("file", f);
    try {
      const data = await apiForm("/datasets/xlsx/sheets", form);
      const group = document.createElement("div");
      group.className = "stack";
      group.innerHTML = `<strong class="hint">${escapeHtml(f.name)}</strong>`;
      (data.sheets || []).forEach((sheet) => {
        const id = `sheet-${escapeHtml(f.name)}-${escapeHtml(sheet)}`.replace(/[^a-zA-Z0-9_-]/g, "_");
        const label = document.createElement("label");
        label.className = "checkbox-row";
        label.innerHTML = `<input type="checkbox" data-sheet="${escapeHtml(sheet)}" id="${id}" /> ${escapeHtml(sheet)}`;
        group.appendChild(label);
      });
      els.sheetPicker.appendChild(group);
    } catch (err) {
      const errEl = document.createElement("p");
      errEl.className = "hint";
      errEl.textContent = `${f.name}: ${err.message || String(err)}`;
      els.sheetPicker.appendChild(errEl);
    }
  }
}

function collectSelectedSheets() {
  if (!els.sheetPicker) return [];
  const checked = els.sheetPicker.querySelectorAll('input[type="checkbox"][data-sheet]:checked');
  return Array.from(checked).map((cb) => cb.dataset.sheet);
}

function appendSheetsToForm(form) {
  const sheets = collectSelectedSheets();
  if (sheets.length) form.append("sheets", JSON.stringify(sheets));
}

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
  setUploadBusy(true);

  try {
    if (append) {
      els.uploadStatus.textContent = "Adding…";
      let lastDatasetId = ds;
      let addedCount = 0;
      const files = Array.from(els.file.files);
      for (let i = 0; i < files.length; i += 1) {
        const f = files[i];
        const form = new FormData();
        form.append("file", f);
        form.append("replace", replace ? "true" : "false");
        appendSheetsToForm(form);
        const req = apiFormWithProgress(
          `/datasets/${encodeURIComponent(ds)}/tables`,
          form,
          (pct) => { els.uploadStatus.textContent = `Adding… ${pct}%`; },
        );
        activeUpload = req;
        const data = await req;
        lastDatasetId = data.dataset_id;
        addedCount += data.tables.length;
      }
      els.uploadStatus.textContent = `Added ${addedCount} table(s) to ${lastDatasetId}`;
      els.file.value = "";
      els.fileList.innerHTML = "";
      if (els.sheetPicker) {
        els.sheetPicker.style.display = "none";
        els.sheetPicker.innerHTML = "<label>Excel sheets (optional — leave unchecked to ingest all)</label>";
      }
      await refreshDatasets(lastDatasetId);
      return;
    }

    const form = new FormData();
    if (ds) form.append("dataset_id", ds);
    form.append("replace", replace ? "true" : "false");
    Array.from(els.file.files).forEach((f) => form.append("files", f));
    appendSheetsToForm(form);
    els.uploadStatus.textContent = "Uploading… 0%";
    const req = apiFormWithProgress("/datasets/upload", form, (pct) => {
      els.uploadStatus.textContent = `Uploading… ${pct}%`;
    });
    activeUpload = req;
    const data = await req;
    els.uploadStatus.textContent = `Loaded ${data.tables.length} table(s) into ${data.dataset_id}: ${data.tables.map((t) => t.name).join(", ")}`;
    els.datasetId.value = data.dataset_id;
    els.file.value = "";
    els.fileList.innerHTML = "";
    if (els.sheetPicker) {
      els.sheetPicker.style.display = "none";
      els.sheetPicker.innerHTML = "<label>Excel sheets (optional — leave unchecked to ingest all)</label>";
    }
    await refreshDatasets(data.dataset_id);
  } catch (err) {
    setError(err.message || String(err));
    els.uploadStatus.textContent = "";
  } finally {
    activeUpload = null;
    setUploadBusy(false);
  }
}




export function wireDataTab() {
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
    const table = state.currentTable;
    if (!(await confirmDestructive("delete table", table))) return;
    setError("");
    try {
      await apiDelete(
        `/datasets/${encodeURIComponent(els.dataset.value)}/tables/${encodeURIComponent(table)}`,
      );
      clearTableContext();
      await loadSchema();
    } catch (err) {
      setErrorFrom(err);
    }
  });
  els.exportTableCsvBtn.addEventListener("click", () => exportTable("csv"));
  els.exportTableXlsxBtn.addEventListener("click", () => exportTable("xlsx"));
  els.exportTableParquetBtn?.addEventListener("click", () => exportTable("parquet"));
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
  els.renameDatasetBtn?.addEventListener("click", async () => {
    const id = requireDataset();
    if (!id) return;
    const newId = prompt(`Rename dataset "${id}" to:`, id);
    if (!newId || newId.trim() === id) return;
    setError("");
    try {
      const data = await apiJson(
        `/datasets/${encodeURIComponent(id)}/rename`,
        "POST",
        { new_id: newId.trim() },
      );
      await refreshDatasets(data.new_id || data.dataset_id || newId.trim());
    } catch (err) {
      setErrorFrom(err);
    }
  });
  els.deleteDatasetBtn.addEventListener("click", async () => {
    const id = requireDataset();
    if (!id) return;
    if (!(await confirmDestructive("delete dataset", id))) return;
    setError("");
    try {
      await apiDelete(`/datasets/${encodeURIComponent(id)}`);
      clearTableContext();
      await refreshDatasets();
    } catch (err) {
      setErrorFrom(err);
    }
  });
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
  els.peekPrevBtn?.addEventListener("click", () => {
    if (!state.currentTable || peekOffset <= 0) return;
    loadTableRowsPeek(state.currentTable, Math.max(0, peekOffset - PEEK_PAGE)).catch(setErrorFrom);
  });
  els.peekNextBtn?.addEventListener("click", () => {
    if (!state.currentTable) return;
    loadTableRowsPeek(state.currentTable, peekOffset + PEEK_PAGE).catch(setErrorFrom);
  });
  wireUploadDropzone();
  els.file.addEventListener("change", () => {
    els.fileList.innerHTML = Array.from(els.file.files)
      .map((f) => `<li>${escapeHtml(f.name)} <span class="hint">(${Math.round(f.size / 1024)} KB)</span></li>`)
      .join("");
    loadXlsxSheets().catch((err) => setError(err.message || String(err)));
  });
  els.lockedRetryBtn?.addEventListener("click", () => refreshDatasets(els.dataset.value));
  els.importDuckdbBtn?.addEventListener("click", async () => {
    setError("");
    if (!els.importDuckdbFile?.files?.length) {
      setError("Choose a .duckdb file to import.");
      return;
    }
    const form = new FormData();
    form.append("file", els.importDuckdbFile.files[0]);
    const dsId = (els.importDatasetId?.value || "").trim();
    if (dsId) form.append("dataset_id", dsId);
    els.uploadStatus.textContent = "Importing…";
    setUploadBusy(true);
    try {
      const req = apiFormWithProgress("/datasets/import", form, (pct) => {
        els.uploadStatus.textContent = `Importing… ${pct}%`;
      });
      activeUpload = req;
      const data = await req;
      els.uploadStatus.textContent = `Imported ${data.tables.length} table(s) as ${data.dataset_id}`;
      els.importDuckdbFile.value = "";
      if (els.importDatasetId) els.importDatasetId.value = "";
      await refreshDatasets(data.dataset_id);
    } catch (err) {
      setError(err.message || String(err));
      els.uploadStatus.textContent = "";
    } finally {
      activeUpload = null;
      setUploadBusy(false);
    }
  });
  els.uploadBtn.addEventListener("click", () => uploadFiles({ append: false }));
  els.uploadCancelBtn?.addEventListener("click", () => cancelActiveUpload());
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
}

export { refreshDatasets, loadSchema, clearTableContext, selectTable, wireDataTab };
