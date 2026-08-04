import { apiGet, apiJson, getApiToken, setApiToken } from "./api.js";
import { bindElements, els, fillSelect, setError, state } from "./state.js";
import { refreshDatasets, setAfterSchemaLoad, wireDataTab } from "./data.js";
import { loadRecipes, onJoinTablesChanged, wireJoinTab } from "./join.js";
import { loadChatHistory, refreshAskColumnSelects, wireChatTab } from "./chat.js";

bindElements();

setAfterSchemaLoad(async () => {
  await onJoinTablesChanged();
  fillSelect(els.chatTable, ["", ...state.tables], els.chatTable.value);
  if (els.chatTable.options[0]) els.chatTable.options[0].textContent = "(auto — first table)";
  refreshAskColumnSelects();
  await Promise.all([loadRecipes(), loadChatHistory()]);
});

wireDataTab();
wireJoinTab();
wireChatTab();

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
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`view-${btn.dataset.view}`)?.classList.add("active");
  });
});

refreshDatasets().catch((err) => setError(err.message || String(err)));
apiGet("/health")
  .then((h) => {
    const hint = document.getElementById("llm-hint");
    const authHint = document.getElementById("auth-hint");
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
