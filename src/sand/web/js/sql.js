import { apiJson, downloadExport, newQueryId } from "./api.js";
import {
  state, els, setError, renderPreviewTable, escapeHtml, requireDataset, isDatasetLocked,
} from "./state.js";

let activeAbort = null;
let activeQueryId = null;
let lastSqlResult = null;

function setBusy(busy) {
  if (els.sqlRunPreviewBtn) els.sqlRunPreviewBtn.disabled = busy || isDatasetLocked();
  if (els.sqlRunFullBtn) els.sqlRunFullBtn.disabled = busy || isDatasetLocked();
  if (els.sqlCancelBtn) els.sqlCancelBtn.style.display = busy ? "inline-block" : "none";
  if (els.sqlCancelBtn) els.sqlCancelBtn.disabled = !busy;
}

async function cancelActiveQuery() {
  const id = els.dataset?.value;
  if (activeAbort) activeAbort.abort();
  if (id && activeQueryId) {
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

function showPreviewNotice(data) {
  if (!els.sqlPreviewNotice) return;
  if (data.is_preview) {
    const shown = data.row_count;
    const total = data.full_row_count != null ? data.full_row_count : "?";
    const sample = data.chart_sample_rows || 5000;
    els.sqlPreviewNotice.innerHTML =
      `Evaluated with LIMIT ${data.evaluated_limit} (${shown} of ${total} rows). ` +
      `<button type="button" class="link" id="sql-run-full-btn">Run full query</button>` +
      `<div class="hint" style="margin-top:0.4rem;">` +
      `Charts still sample at most ${sample.toLocaleString()} rows after a full run. ` +
      `Use Export CSV/XLSX/Parquet for the complete result.` +
      `</div>`;
    els.sqlPreviewNotice.classList.add("visible");
    document.getElementById("sql-run-full-btn")?.addEventListener("click", () => runSql(true));
    return;
  }
  if (data.chart_capped) {
    const shown = data.row_count;
    const total = data.full_row_count != null ? data.full_row_count : "?";
    const sample = data.chart_sample_rows || 5000;
    els.sqlPreviewNotice.innerHTML =
      `Chart shows ${shown.toLocaleString()} of ~${typeof total === "number" ? total.toLocaleString() : total} rows ` +
      `(sample cap ${sample.toLocaleString()}). Export for the full result.`;
    els.sqlPreviewNotice.classList.add("visible");
    return;
  }
  els.sqlPreviewNotice.classList.remove("visible");
}

function applySqlResult(data) {
  lastSqlResult = data;
  state.lastSqlSql = data.sql;
  if (els.sqlPreview) {
    const rows = Array.isArray(data.preview) ? data.preview : (data.preview?.rows || data.rows || []);
    els.sqlPreview.innerHTML = renderPreviewTable(rows, data.columns);
  }
  if (data.chart && data.chart.figure && els.sqlChart) {
    Plotly.newPlot(els.sqlChart, data.chart.figure.data, data.chart.figure.layout, { responsive: true });
  } else if (els.sqlChart) {
    els.sqlChart.innerHTML = "";
  }
  showPreviewNotice(data);
}

async function runSql(runFull) {
  const id = requireDataset();
  if (!id || isDatasetLocked()) return;
  const sql = (els.sqlInput?.value || "").trim();
  if (!sql) {
    setError("Enter SQL first.");
    return;
  }
  try {
    const body = { dataset_id: id, sql, run_full: !!runFull };
    if (els.sqlChartType?.value) body.chart_type = els.sqlChartType.value;
    const data = await withCancellable((signal, queryId) =>
      apiJson("/query/sql", "POST", body, { signal, queryId }),
    );
    if (!data) return;
    applySqlResult(data);
  } catch (err) {
    setError(err.message || String(err));
  }
}

async function exportSqlResult(fmt) {
  const id = requireDataset();
  if (!id) return;
  const sql = state.lastSqlSql || (els.sqlInput?.value || "").trim();
  if (!sql) {
    setError("Run SQL first, then export.");
    return;
  }
  setError("");
  try {
    await downloadExport(fmt, { dataset_id: id, sql }, `sql_result.${fmt}`);
  } catch (err) {
    setError(err.message || String(err));
  }
}

export function refreshSqlTab() {
  if (els.sqlActiveDataset) {
    els.sqlActiveDataset.textContent = els.dataset?.value || "none — pick one on the Data tab";
  }
  const locked = isDatasetLocked();
  if (els.sqlRunPreviewBtn) els.sqlRunPreviewBtn.disabled = locked;
  if (els.sqlRunFullBtn) els.sqlRunFullBtn.disabled = locked;
  if (els.sqlInput && locked) {
    els.sqlInput.placeholder = "Dataset is locked — close other apps using the file, then Retry on Data tab.";
  } else if (els.sqlInput) {
    els.sqlInput.placeholder = "SELECT region, SUM(amount) AS total FROM sales GROUP BY region";
  }
}

export function wireSqlTab() {
  els.sqlRunPreviewBtn?.addEventListener("click", () => runSql(false));
  els.sqlRunFullBtn?.addEventListener("click", () => runSql(true));
  els.sqlCancelBtn?.addEventListener("click", () => cancelActiveQuery());
  els.exportSqlCsvBtn?.addEventListener("click", () => exportSqlResult("csv"));
  els.exportSqlXlsxBtn?.addEventListener("click", () => exportSqlResult("xlsx"));
  els.exportSqlParquetBtn?.addEventListener("click", () => exportSqlResult("parquet"));
}
