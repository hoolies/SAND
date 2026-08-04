import {
  apiGet, apiJson, apiForm, apiDelete, downloadExport,
} from "./api.js";
import {
  state, els, setError, fillSelect, columnsFor, renderPreviewTable,
  escapeHtml, requireDataset, syncActiveDatasetBadges,
} from "./state.js";

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
}


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

export function wireChatTab() {
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
    } catch (err) {
      setError(err.message || String(err));
    }
  });
  els.exportChatCsvBtn.addEventListener("click", () => exportChatResult("csv"));
  els.exportChatXlsxBtn.addEventListener("click", () => exportChatResult("xlsx"));
}

export { loadChatHistory, refreshAskColumnSelects, wireChatTab };
