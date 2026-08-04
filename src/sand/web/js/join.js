import {
  apiGet, apiJson, apiForm, apiDelete, downloadExport,
} from "./api.js";
import {
  state, els, setError, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, syncActiveDatasetBadges,
} from "./state.js";
import { loadSchema } from "./data.js";


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
      await apiDelete(`/query/join/recipes/${encodeURIComponent(id)}/${encodeURIComponent(name)}`);
    await loadRecipes();
  } catch (err) {
    setError(err.message || String(err));
  }
}


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

export function wireJoinTab() {
  els.joinLeft.addEventListener("change", onJoinTablesChanged);
  els.joinRight.addEventListener("change", onJoinTablesChanged);
  els.addKeyBtn.addEventListener("click", () => addKeyRow());
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
  els.exportJoinCsvBtn.addEventListener("click", () => exportJoinResult("csv"));
  els.exportJoinXlsxBtn.addEventListener("click", () => exportJoinResult("xlsx"));
}

export { onJoinTablesChanged, loadRecipes, wireJoinTab };
