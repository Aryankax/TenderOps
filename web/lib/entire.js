// Entire Continuous Memory client (same-origin /backend rewrite, like api.js).

import { getToken } from "./api";

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function raiseForStatus(resp) {
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      try { detail = await resp.text(); } catch { /* keep */ }
    }
    throw new Error(`API ${resp.status}: ${detail}`);
  }
  return resp.json();
}

function get(url) {
  return fetch(url, { headers: { ...authHeaders() } }).then(raiseForStatus);
}

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(body),
  }).then(raiseForStatus);
}

export const fetchEntireStatus = () => get(`/backend/api/entire/status`);
export const listEntireCheckpoints = (tenderId) =>
  get(`/backend/api/entire/checkpoints${tenderId ? `?tender_id=${encodeURIComponent(tenderId)}` : ""}`);
export const searchEntire = (q) =>
  get(`/backend/api/entire/search?q=${encodeURIComponent(q)}`);
export const briefEntire = (query) => post(`/backend/api/entire/brief`, { query });
export const reviewEntire = (request, changed_files, decisions) =>
  post(`/backend/api/entire/review`, { request, changed_files, decisions });
export const scoutEntire = (checkpoint_id) =>
  get(`/backend/api/entire/scout${checkpoint_id ? `?checkpoint_id=${encodeURIComponent(checkpoint_id)}` : ""}`);
export const demoEntire = (checkpoint_id) =>
  get(`/backend/api/entire/checkpoints/${encodeURIComponent(checkpoint_id)}/demo`);
export const graphSearch = (q) =>
  get(`/backend/api/entire/graph/search?q=${encodeURIComponent(q)}`);
