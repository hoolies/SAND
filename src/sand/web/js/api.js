/** Shared HTTP helpers + optional API token (localStorage). */

const TOKEN_KEY = "sand_api_token";

export function getApiToken() {
  return (localStorage.getItem(TOKEN_KEY) || "").trim();
}

export function setApiToken(token) {
  const t = (token || "").trim();
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getApiToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
    headers["X-SAND-Token"] = token;
  }
  return headers;
}

export function detailMessage(data) {
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

export async function readJsonSafe(res) {
  try {
    return await res.json();
  } catch (_err) {
    return null;
  }
}

export async function apiGet(path) {
  const res = await fetch(path, { headers: authHeaders() });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

export async function apiJson(path, method, body) {
  const res = await fetch(path, {
    method,
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

export async function apiForm(path, formData) {
  const res = await fetch(path, { method: "POST", headers: authHeaders(), body: formData });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

export async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE", headers: authHeaders() });
  const data = await readJsonSafe(res);
  if (!res.ok) throw new Error(detailMessage(data));
  return data;
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export async function downloadExport(fmt, body, filename) {
  const res = await fetch(`/export/${fmt}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await readJsonSafe(res);
    throw new Error(detailMessage(data));
  }
  const blob = await res.blob();
  downloadBlob(blob, filename);
}
