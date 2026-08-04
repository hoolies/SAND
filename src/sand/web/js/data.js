import {
  apiGet, apiJson, apiForm, apiDelete, downloadExport, readJsonSafe, detailMessage,
} from "./api.js";
import {
  SAND_TYPES, state, els, setError, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, syncActiveDatasetBadges,
} from "./state.js";

export let afterSchemaLoad = null;
export function setAfterSchemaLoad(fn) { afterSchemaLoad = fn; }


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
    if (!confirm(`Drop table "${state.currentTable}"? This cannot be undone.`)) return;
    setError("");
    try {
      await apiDelete(
        `/datasets/${encodeURIComponent(els.dataset.value)}/tables/${encodeURIComponent(state.currentTable)}`
      );
      clearTableContext();
      await loadSchema();
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  els.exportTableCsvBtn.addEventListener("click", () => exportTable("csv"));
  els.exportTableXlsxBtn.addEventListener("click", () => exportTable("xlsx"));
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
      await apiDelete(`/datasets/${encodeURIComponent(id)}`);
      clearTableContext();
      await refreshDatasets();
    } catch (err) {
      setError(err.message || String(err));
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
  els.file.addEventListener("change", () => {
    els.fileList.innerHTML = Array.from(els.file.files)
      .map((f) => `<li>${escapeHtml(f.name)} <span class="hint">(${Math.round(f.size / 1024)} KB)</span></li>`)
      .join("");
  });
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
}

export { refreshDatasets, loadSchema, clearTableContext, selectTable, wireDataTab };
