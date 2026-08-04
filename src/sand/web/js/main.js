import { apiGet, apiJson, getApiToken, setApiToken } from "./api.js";
import { bindElements, els, fillSelect, setError, state } from "./state.js";
import { refreshDatasets, setAfterSchemaLoad, wireDataTab } from "./data.js";
import { loadRecipes, onJoinTablesChanged, wireJoinTab } from "./join.js";
import { loadChatHistory, refreshAskColumnSelects, wireChatTab } from "./chat.js";
import { wireSqlTab, refreshSqlTab } from "./sql.js";

const THEME_KEY = "sand_theme";
const DATASET_KEY = "sand_active_dataset";
const TAB_KEY = "sand_active_tab";
const VALID_TABS = new Set(["data", "join", "sql", "chat"]);
const THEMES = {
  dark: "tokyo-night-storm",
  light: "catppuccin-latte",
};

function preferredTheme() {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved === THEMES.dark || saved === THEMES.light) return saved;
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
    ? THEMES.light
    : THEMES.dark;
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
  if (els.themeToggle) {
    els.themeToggle.textContent = theme === THEMES.light ? "Tokyo Night" : "Catppuccin Latte";
    els.themeToggle.title =
      theme === THEMES.light
        ? "Switch to Tokyo Night Storm (dark)"
        : "Switch to Catppuccin Latte (light)";
  }
}

function switchTab(view) {
  if (!VALID_TABS.has(view)) return;
  document.querySelectorAll(".tab").forEach((b) => {
    const active = b.dataset.view === view;
    b.classList.toggle("active", active);
    b.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.remove("active");
    v.hidden = true;
  });
  const panel = document.getElementById(`view-${view}`);
  if (panel) {
    panel.classList.add("active");
    panel.hidden = false;
  }
  localStorage.setItem(TAB_KEY, view);
}

function activeTabView() {
  const active = document.querySelector(".tab.active");
  return active?.dataset?.view || "data";
}

function isTypingInField(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA";
}

function clickVisibleCancelButtons() {
  [els.sqlCancelBtn, els.cancelQueryBtn].forEach((btn) => {
    if (btn && btn.offsetParent !== null && !btn.disabled) btn.click();
  });
}

function wireKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    const active = document.activeElement;
    const view = activeTabView();

    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      if (view === "sql" || active?.id === "sql-input") {
        e.preventDefault();
        els.sqlRunPreviewBtn?.click();
        return;
      }
      if (view === "chat" || active?.id === "prompt") {
        e.preventDefault();
        els.askBtn?.click();
        return;
      }
      if (view === "join") {
        e.preventDefault();
        els.joinBtn?.click();
        return;
      }
    }

    if (e.key === "Escape") {
      clickVisibleCancelButtons();
      return;
    }

    if (isTypingInField(active)) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "1") switchTab("data");
    else if (e.key === "2") switchTab("join");
    else if (e.key === "3") switchTab("sql");
    else if (e.key === "4") switchTab("chat");
  });
}

bindElements();
applyTheme(preferredTheme());

setAfterSchemaLoad(async () => {
  await onJoinTablesChanged();
  fillSelect(els.chatTable, ["", ...state.tables], els.chatTable.value);
  if (els.chatTable.options[0]) els.chatTable.options[0].textContent = "(auto — first table)";
  refreshAskColumnSelects();
  refreshSqlTab();
  await Promise.all([loadRecipes(), loadChatHistory()]);
});

wireDataTab();
wireJoinTab();
wireChatTab();
wireSqlTab();

els.themeToggle?.addEventListener("click", () => {
  const current = document.documentElement.getAttribute("data-theme") || THEMES.dark;
  applyTheme(current === THEMES.light ? THEMES.dark : THEMES.light);
});

if (els.apiToken) {
  els.apiToken.value = getApiToken();
}

els.saveTokenBtn?.addEventListener("click", () => {
  setApiToken(els.apiToken?.value || "");
  setError("");
  const status = document.getElementById("token-status");
  if (status) status.textContent = getApiToken() ? "Token saved for this browser." : "Token cleared.";
  refreshDatasets(els.dataset?.value).catch((err) => setError(err.message || String(err)));
});

els.checkpointBtn?.addEventListener("click", async () => {
  try {
    const id = els.dataset.value;
    if (!id) throw new Error("Select a dataset first");
    await apiJson(`/datasets/${encodeURIComponent(id)}/checkpoint`, "POST", {});
    setError("");
    if (els.uploadStatus) els.uploadStatus.textContent = `Checkpointed ${id}.`;
  } catch (err) {
    setError(err.message || String(err));
  }
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.view));
});

wireKeyboardShortcuts();

const savedDataset = localStorage.getItem(DATASET_KEY);
const savedTab = localStorage.getItem(TAB_KEY);

refreshDatasets(savedDataset || undefined)
  .then(() => {
    if (els.dataset?.value) localStorage.setItem(DATASET_KEY, els.dataset.value);
    switchTab(savedTab && VALID_TABS.has(savedTab) ? savedTab : "data");
  })
  .catch((err) => setError(err.message || String(err)));

els.dataset?.addEventListener("change", () => {
  if (els.dataset.value) localStorage.setItem(DATASET_KEY, els.dataset.value);
});

apiGet("/health")
  .then((h) => {
    const hint = document.getElementById("llm-hint");
    const authHint = document.getElementById("auth-hint");
    const limitsEl = els.limitsHint || document.getElementById("limits-hint");
    if (limitsEl && h.limits) {
      const lim = h.limits;
      limitsEl.textContent =
        `Limits: chart sample ${Number(lim.chart_sample_rows || 0).toLocaleString()} rows · ` +
        `max result ${Number(lim.max_result_rows || 0).toLocaleString()} rows · ` +
        `query timeout ${lim.query_timeout_seconds}s`;
    }
    if (authHint) {
      if (h.auth_required) {
        authHint.style.display = "block";
        if (!getApiToken()) {
          authHint.textContent = "This server requires an API token. Enter it above, then Save token.";
        } else {
          authHint.textContent = "API token is set for this browser.";
        }
      } else {
        authHint.style.display = "none";
      }
    }
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
  })
  .catch(() => {});
