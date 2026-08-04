import {
  apiGet, apiJson, apiDelete, downloadExport, newQueryId,
} from "./api.js";
import {
  state, els, setError, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, isDatasetLocked,
} from "./state.js";
import { loadSchema } from "./data.js";

let extraStepIndex = 0;
let activeAbort = null;
let activeQueryId = null;

function setJoinBusy(busy) {
  if (els.joinBtn) els.joinBtn.disabled = busy || isDatasetLocked();
  if (els.estimateBtn) els.estimateBtn.disabled = busy || isDatasetLocked();
  if (els.joinCancelBtn) els.joinCancelBtn.style.display = busy ? "inline-block" : "none";
  if (els.joinCancelBtn) els.joinCancelBtn.disabled = !busy;
}

async function cancelActiveJoin() {
  const id = els.dataset?.value;
  if (activeAbort) activeAbort.abort();
  if (id && activeQueryId) {
    try {
      await apiJson("/chat/cancel", "POST", { dataset_id: id, query_id: activeQueryId });
    } catch (_err) {
      /* best-effort */
    }
  }
  setJoinBusy(false);
  setError("Join cancelled.");
}

async function withCancellableJoin(fn) {
  if (activeAbort) activeAbort.abort();
  activeAbort = new AbortController();
  activeQueryId = newQueryId();
  setJoinBusy(true);
  setError("");
  try {
    return await fn(activeAbort.signal, activeQueryId);
  } catch (err) {
    if (err && err.name === "AbortError") {
      setError("Join cancelled.");
      return null;
    }
    throw err;
  } finally {
    activeAbort = null;
    activeQueryId = null;
    setJoinBusy(false);
  }
}

function addKeyRow(leftCol, rightCol, container, leftIsPrev) {
  const host = container || els.keyRows;
  const row = document.createElement("div");
  row.className = "key-row";
  const left = leftIsPrev
    ? document.createElement("input")
    : document.createElement("select");
  const right = document.createElement("select");
  if (leftIsPrev) {
    left.type = "text";
    left.className = "key-left";
    left.placeholder = "column from previous result";
    if (leftCol) left.value = leftCol;
  } else {
    left.className = "key-left";
    fillSelect(left, columnsFor(els.joinLeft.value), leftCol);
  }
  right.className = "key-right";
  const rightTable = container
    ? container.closest(".join-step-panel")?.querySelector(".step-right")?.value
    : els.joinRight.value;
  fillSelect(right, columnsFor(rightTable || els.joinRight.value), rightCol);
  const eq = document.createElement("div");
  eq.className = "equals";
  eq.textContent = "=";
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "secondary";
  remove.textContent = "×";
  remove.addEventListener("click", () => {
    if (host.children.length > 1) row.remove();
  });
  row.append(left, eq, right, remove);
  host.appendChild(row);
}

function refreshKeyColumnOptions() {
  const leftCols = columnsFor(els.joinLeft.value);
  const rightCols = columnsFor(els.joinRight.value);
  els.keyRows.querySelectorAll(".key-row").forEach((row) => {
    const l = row.querySelector(".key-left");
    const r = row.querySelector(".key-right");
    if (l.tagName === "SELECT") fillSelect(l, leftCols, l.value);
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

function collectJoinKeys(container) {
  const keys = [];
  const host = container || els.keyRows;
  host.querySelectorAll(".key-row").forEach((row) => {
    const leftEl = row.querySelector(".key-left");
    const right = row.querySelector(".key-right").value;
    const left = leftEl.tagName === "SELECT" ? leftEl.value : leftEl.value.trim();
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

function addJoinStepPanel() {
  if (!els.joinSteps) return;
  extraStepIndex += 1;
  const stepNum = extraStepIndex + 1;
  const panel = document.createElement("div");
  panel.className = "join-step-panel panel stack";
  panel.style.marginTop = "0.75rem";
  panel.dataset.step = String(extraStepIndex);
  panel.innerHTML = `
    <div class="btn-row" style="justify-content:space-between;">
      <strong>Step ${stepNum}</strong>
      <button type="button" class="secondary small remove-step-btn">Remove step</button>
    </div>
    <div>
      <label>Left</label>
      <p class="hint">previous result</p>
    </div>
    <div>
      <label>Right table</label>
      <select class="step-right"></select>
    </div>
    <div>
      <label>Join keys</label>
      <div class="step-key-rows"></div>
      <button type="button" class="secondary add-step-key-btn">Add key</button>
    </div>`;
  const rightSel = panel.querySelector(".step-right");
  fillSelect(rightSel, state.tables, state.tables[0]);
  const keyHost = panel.querySelector(".step-key-rows");
  addKeyRow("", "", keyHost, true);
  panel.querySelector(".add-step-key-btn").addEventListener("click", () => {
    addKeyRow("", "", keyHost, true);
  });
  rightSel.addEventListener("change", () => {
    keyHost.querySelectorAll(".key-row").forEach((row) => {
      const r = row.querySelector(".key-right");
      fillSelect(r, columnsFor(rightSel.value), r.value);
    });
  });
  panel.querySelector(".remove-step-btn").addEventListener("click", () => panel.remove());
  els.joinSteps.appendChild(panel);
}

function collectExtraSteps() {
  if (!els.joinSteps) return [];
  const steps = [];
  els.joinSteps.querySelectorAll(".join-step-panel").forEach((panel) => {
    const right = panel.querySelector(".step-right")?.value;
    const on = collectJoinKeys(panel.querySelector(".step-key-rows"));
    if (right && on.length) {
      steps.push({
        left: "__prev__",
        right,
        on,
        how: els.joinHow.value,
      });
    }
  });
  return steps;
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
  if (!estimate) {
    els.estimateResult.innerHTML = "";
    return;
  }
  if (Array.isArray(estimate.steps)) {
    const final = estimate.final_estimated_rows ?? "—";
    const warnHtml = estimate.warning ? `<div class="warn-box">⚠ ${escapeHtml(estimate.warning)}</div>` : "";
    const stepsHtml = estimate.steps.map((s, i) => `
      <div class="kv-grid" style="margin-top:0.5rem;">
        <div class="kv"><div class="label">Step ${i + 1} left</div><div class="value">${s.left_rows}</div></div>
        <div class="kv"><div class="label">Step ${i + 1} right</div><div class="value">${s.right_rows}</div></div>
        <div class="kv"><div class="label">Est. rows</div><div class="value">${s.estimated_rows ?? "—"}</div></div>
        <div class="kv"><div class="label">Multiplicity</div><div class="value">${escapeHtml(s.multiplicity)}</div></div>
      </div>
    `).join("");
    els.estimateResult.innerHTML = `
      <div class="kv-grid">
        <div class="kv"><div class="label">Final estimated rows</div><div class="value">${final}</div></div>
        <div class="kv"><div class="label">Steps</div><div class="value">${estimate.steps.length}</div></div>
      </div>
      ${stepsHtml}
      ${warnHtml}
    `;
    return;
  }
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
      let meta = "";
      if (r.plan) {
        const n = r.plan.steps?.length || 0;
        meta = `${n}-step plan`;
        if (r.plan.as_table) meta += ` → ${r.plan.as_table}`;
      } else if (r.spec) {
        const keys = (r.spec.on || []).map((k) => (typeof k === "string" ? k : `${k.left}=${k.right}`)).join(", ");
        meta = `${r.spec.left} ⋈ ${r.spec.right} on ${keys} (${r.spec.how})`;
      }
      li.innerHTML = `
        <div>
          <div><strong>${escapeHtml(r.name)}</strong></div>
          ${meta ? `<div class="recipe-meta">${escapeHtml(meta)}</div>` : ""}
        </div>
        <div class="recipe-actions">
          <button type="button" class="secondary small load-recipe">Load</button>
          <button type="button" class="secondary small run-recipe">Run</button>
          <button type="button" class="danger small" data-act="del">Delete</button>
        </div>`;
      li.querySelector(".load-recipe").addEventListener("click", () => loadRecipeIntoForm(r));
      li.querySelector(".run-recipe").addEventListener("click", () => runRecipe(r.name));
      li.querySelector('[data-act="del"]').addEventListener("click", () => deleteRecipe(r.name));
      els.recipeList.appendChild(li);
    });
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function deleteRecipe(name) {
  const id = requireDataset();
  if (!id) return;
  if (!confirm(`Delete recipe “${name}”?`)) return;
  try {
    await apiDelete(`/query/join/recipes/${encodeURIComponent(id)}/${encodeURIComponent(name)}`);
    await loadRecipes();
  } catch (err) {
    setError(err.message || String(err));
  }
}

function applyJoinKeys(on, container, leftIsPrev) {
  const host = container || els.keyRows;
  host.innerHTML = "";
  const keys = on || [];
  if (!keys.length) {
    addKeyRow("", "", host, leftIsPrev);
    return;
  }
  keys.forEach((k) => {
    if (typeof k === "string") {
      if (k.includes("=")) {
        const [l, r] = k.split("=", 2);
        addKeyRow(l.trim(), r.trim(), host, leftIsPrev);
      } else {
        addKeyRow(k, k, host, leftIsPrev);
      }
    } else {
      addKeyRow(k.left, k.right, host, leftIsPrev);
    }
  });
}

function loadRecipeIntoForm(recipe) {
  setError("");
  if (els.joinRecipeName) els.joinRecipeName.value = recipe.name || "";
  if (els.joinSteps) els.joinSteps.innerHTML = "";
  extraStepIndex = 0;

  const plan = recipe.plan;
  const spec = recipe.spec || (plan?.steps?.[0] || null);
  if (!spec && !plan) {
    setError("Recipe has no join/plan to load.");
    return;
  }
  const first = plan?.steps?.[0] || spec;
  if (els.joinLeft) els.joinLeft.value = first.left;
  if (els.joinRight) els.joinRight.value = first.right;
  if (els.joinHow) els.joinHow.value = first.how || "inner";
  applyJoinKeys(first.on, els.keyRows, false);
  refreshKeyColumnOptions();

  const rest = plan?.steps?.slice(1) || [];
  rest.forEach((step) => {
    addJoinStepPanel();
    const panels = els.joinSteps.querySelectorAll(".join-step-panel");
    const panel = panels[panels.length - 1];
    const rightSel = panel.querySelector(".step-right");
    if (rightSel) rightSel.value = step.right;
    applyJoinKeys(step.on, panel.querySelector(".step-key-rows"), true);
    rightSel?.dispatchEvent(new Event("change"));
  });

  const asTable = plan?.as_table || spec?.as_table || "";
  if (els.joinAs) els.joinAs.value = asTable;
  els.joinStatus.textContent = `Loaded recipe "${recipe.name}" into the form — edit and Run join to save changes.`;
  // Refresh suggestions without wiping loaded keys
  const left = els.joinLeft.value;
  const right = els.joinRight.value;
  if (left && right && left !== right && els.dataset.value) {
    apiJson("/query/join/suggest", "POST", { dataset_id: els.dataset.value, left, right })
      .then((data) => renderJoinSuggestions(data.suggestions || []))
      .catch(() => {});
  }
}

async function runRecipe(name) {
  const id = requireDataset();
  if (!id || isDatasetLocked()) return;
  try {
    const data = await withCancellableJoin((signal, queryId) =>
      apiJson("/query/join", "POST", { dataset_id: id, recipe_name: name }, { signal, queryId }),
    );
    if (!data) return;
    renderJoinResult(data);
    if (data.as_table) await loadSchema();
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
  els.addJoinStepBtn?.addEventListener("click", () => addJoinStepPanel());
  els.estimateBtn.addEventListener("click", async () => {
    const id = requireDataset();
    if (!id || isDatasetLocked()) return;
    const on = collectJoinKeys();
    if (!on.length) { setError("Add at least one join key."); return; }
    setError("");
    els.estimateBtn.disabled = true;
    try {
      const extraSteps = collectExtraSteps();
      let body;
      if (extraSteps.length) {
        body = {
          dataset_id: id,
          plan: {
            steps: [{ ...buildJoinSpec(), as_table: null, limit: null }, ...extraSteps],
            as_table: els.joinAs.value.trim() || null,
          },
        };
      } else {
        body = { dataset_id: id, join: buildJoinSpec() };
      }
      const data = await apiJson("/query/join/estimate", "POST", body);
      renderEstimate(data.estimate);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      els.estimateBtn.disabled = isDatasetLocked();
    }
  });
  els.joinBtn.addEventListener("click", async () => {
    const id = requireDataset();
    if (!id || isDatasetLocked()) return;
    if (state.tables.length < 2) { setError("Need at least two tables to join. Upload more spreadsheets."); return; }
    const on = collectJoinKeys();
    if (!on.length) { setError("Add at least one join key."); return; }

    const recipeName = (els.joinRecipeName?.value || "").trim();
    const asTable = els.joinAs.value.trim() || null;
    const extraSteps = collectExtraSteps();
    let body;
    if (extraSteps.length) {
      body = {
        dataset_id: id,
        plan: {
          steps: [{ ...buildJoinSpec(), as_table: null, limit: null }, ...extraSteps],
          as_table: asTable,
          limit: 500,
        },
      };
    } else {
      body = {
        dataset_id: id,
        join: { ...buildJoinSpec(), as_table: asTable, limit: 500 },
      };
    }
    if (recipeName) body.recipe_name = recipeName;
    if (asTable || recipeName) body.write = true;
    try {
      const data = await withCancellableJoin((signal, queryId) =>
        apiJson("/query/join", "POST", body, { signal, queryId }),
      );
      if (!data) return;
      renderJoinResult(data);
      if (recipeName) {
        els.joinStatus.textContent =
          (els.joinStatus.textContent || "") + ` · saved recipe "${recipeName}"`;
        await loadRecipes();
      }
      if (asTable) await loadSchema();
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  els.joinCancelBtn?.addEventListener("click", () => cancelActiveJoin());
  els.exportJoinCsvBtn.addEventListener("click", () => exportJoinResult("csv"));
  els.exportJoinXlsxBtn.addEventListener("click", () => exportJoinResult("xlsx"));
  els.exportJoinParquetBtn?.addEventListener("click", () => exportJoinResult("parquet"));
}

export { onJoinTablesChanged, loadRecipes, wireJoinTab };
