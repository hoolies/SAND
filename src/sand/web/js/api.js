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

export function newQueryId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return `q-${Date.now()}-${Math.random().toString(16).slice(2)}`;
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

function requestIdFrom(res) {
  return res?.headers?.get?.("X-Request-ID") || null;
}

export function apiError(res, data) {
  const err = new Error(detailMessage(data));
  err.status = res.status;
  const requestId = requestIdFrom(res);
  if (requestId) {
    err.requestId = requestId;
    err.message += ` (request id: ${requestId})`;
  }
  return err;
}

function xhrRequestId(xhr) {
  try {
    return xhr.getResponseHeader("X-Request-ID") || null;
  } catch (_err) {
    return null;
  }
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
  if (!res.ok) throw apiError(res, data);
  return data;
}

export async function apiJson(path, method, body, options = {}) {
  const queryId = options.queryId || null;
  const headers = authHeaders({ "Content-Type": "application/json" });
  if (queryId) headers["X-SAND-Query-Id"] = queryId;
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  });
  const data = await readJsonSafe(res);
  if (!res.ok) throw apiError(res, data);
  return data;
}

export async function apiForm(path, formData) {
  const res = await fetch(path, { method: "POST", headers: authHeaders(), body: formData });
  const data = await readJsonSafe(res);
  if (!res.ok) throw apiError(res, data);
  return data;
}

export function apiFormWithProgress(path, formData, onProgress) {
  const xhr = new XMLHttpRequest();
  const promise = new Promise((resolve, reject) => {
    xhr.open("POST", path);
    const headers = authHeaders();
    Object.entries(headers).forEach(([k, v]) => xhr.setRequestHeader(k, v));
    xhr.upload.addEventListener("progress", (ev) => {
      if (ev.lengthComputable && typeof onProgress === "function") {
        onProgress(Math.round((ev.loaded / ev.total) * 100));
      }
    });
    xhr.addEventListener("load", () => {
      let data = null;
      try {
        data = xhr.responseText ? JSON.parse(xhr.responseText) : null;
      } catch (_err) {
        data = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        const err = new Error(detailMessage(data));
        err.status = xhr.status;
        const requestId = xhrRequestId(xhr);
        if (requestId) {
          err.requestId = requestId;
          err.message += ` (request id: ${requestId})`;
        }
        reject(err);
      }
    });
    xhr.addEventListener("error", () => reject(new Error("Upload failed")));
    xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));
    xhr.send(formData);
  });
  promise.xhr = xhr;
  promise.abort = () => xhr.abort();
  return promise;
}

export async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE", headers: authHeaders() });
  const data = await readJsonSafe(res);
  if (!res.ok) throw apiError(res, data);
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
    throw apiError(res, data);
  }
  const blob = await res.blob();
  downloadBlob(blob, filename);
}
