// Backend client. Same-origin via next.config.js rewrite (/backend -> :8000),
// so no CORS and no env config needed. Every request carries the Bearer
// token (login first); the SSE stream passes it as ?access_token= because
// EventSource cannot set headers.

const TOKEN_KEY = "tenderops_token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode */ }
}

export function isUnauthorized(err) {
  return String(err?.message || err).includes("API 401");
}

function authHeaders() {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

async function apiFetch(url, opts = {}) {
  return fetch(url, {
    ...opts,
    headers: { ...authHeaders(), ...(opts.headers || {}) },
  });
}

async function raiseForStatus(resp) {
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      const body = await resp.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      try {
        detail = await resp.text();
      } catch {
        /* keep statusText */
      }
    }
    throw new Error(`API ${resp.status}: ${detail}`);
  }
  return resp.json();
}

// -- auth ---------------------------------------------------------------

export async function signup(username, password) {
  const resp = await fetch(`/backend/api/auth/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const out = await raiseForStatus(resp);
  setToken(out.token);
  return out;
}

export async function login(username, password) {
  const resp = await fetch(`/backend/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const out = await raiseForStatus(resp);
  setToken(out.token);
  return out;
}

export async function logout() {
  try {
    await apiFetch(`/backend/api/auth/logout`, { method: "POST" });
  } catch { /* token may already be dead */ }
  setToken("");
}

export async function fetchMe() {
  const resp = await apiFetch(`/backend/api/auth/me`);
  return raiseForStatus(resp);
}

// -- companies ----------------------------------------------------------

export async function listCompanies() {
  const resp = await apiFetch(`/backend/api/companies`);
  return raiseForStatus(resp);
}

export async function createCompany(name) {
  const resp = await apiFetch(`/backend/api/companies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return raiseForStatus(resp);
}

export async function listCompanyDocs(slug) {
  const resp = await apiFetch(
    `/backend/api/companies/${encodeURIComponent(slug)}/documents`
  );
  return raiseForStatus(resp);
}

export async function uploadCompanyDocs(slug, { files = [], zipFile = null } = {}) {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  if (zipFile) form.append("zip_file", zipFile);
  const resp = await apiFetch(
    `/backend/api/companies/${encodeURIComponent(slug)}/documents`,
    { method: "POST", body: form }
  );
  return raiseForStatus(resp);
}

// -- pipeline -----------------------------------------------------------

export async function runPipeline(file, { fetchLinks = true, companyId = "sample" } = {}) {
  const form = new FormData();
  form.append("file", file);
  const resp = await apiFetch(
    `/backend/api/pipeline/run?fetch_links=${fetchLinks}&company_id=${encodeURIComponent(companyId)}`,
    { method: "POST", body: form }
  );
  return raiseForStatus(resp);
}

export async function runTenderOnly(file, { fetchLinks = true } = {}) {
  const form = new FormData();
  form.append("file", file);
  const resp = await apiFetch(
    `/backend/api/tender-analysis?fetch_links=${fetchLinks}`,
    { method: "POST", body: form }
  );
  const analysis = await raiseForStatus(resp);
  return { analysis };
}

export async function startJob(file, { fetchLinks = true, companyId = "sample" } = {}) {
  const form = new FormData();
  form.append("file", file);
  const resp = await apiFetch(
    `/backend/api/jobs?fetch_links=${fetchLinks}&company_id=${encodeURIComponent(companyId)}`,
    { method: "POST", body: form }
  );
  return raiseForStatus(resp);
}

export async function getJob(jobId) {
  const resp = await apiFetch(`/backend/api/jobs/${encodeURIComponent(jobId)}`);
  return raiseForStatus(resp);
}

export async function resumePipeline(tenderId, { files = [], zipFile = null, waive = [], companyId = "sample", fixMap = null } = {}) {
  const form = new FormData();
  form.append("tender_id", tenderId);
  form.append("company_id", companyId);
  for (const f of files) form.append("files", f);
  if (zipFile) form.append("zip_file", zipFile);
  for (const id of waive) form.append("waive", id);
  if (fixMap && Object.keys(fixMap).length) form.append("fix_map", JSON.stringify(fixMap));
  const resp = await apiFetch(`/backend/api/pipeline/resume`, { method: "POST", body: form });
  return raiseForStatus(resp);
}

/** Background resume: returns {job_id} immediately; poll getJob() until DONE. */
export async function startResumeJob(tenderId, { files = [], zipFile = null, waive = [], companyId = "sample", fixMap = null } = {}) {
  const form = new FormData();
  form.append("tender_id", tenderId);
  form.append("company_id", companyId);
  for (const f of files) form.append("files", f);
  if (zipFile) form.append("zip_file", zipFile);
  for (const id of waive) form.append("waive", id);
  if (fixMap && Object.keys(fixMap).length) form.append("fix_map", JSON.stringify(fixMap));
  const resp = await apiFetch(`/backend/api/pipeline/resume-job`, { method: "POST", body: form });
  return raiseForStatus(resp);
}

/** Poll a job until DONE/FAILED (throws on failure/timeout). Returns the job. */
export async function waitForJob(jobId, { interval = 3000, timeout = 600000, onUpdate = null } = {}) {
  const start = Date.now();
  for (;;) {
    const job = await getJob(jobId);
    if (onUpdate) { try { onUpdate(job); } catch { /* display-only */ } }
    if (job.status === "DONE") return job;
    if (job.status === "FAILED") throw new Error(job.error || "Background job failed.");
    if (Date.now() - start > timeout) throw new Error(`Job ${jobId} timed out after ${Math.round(timeout / 60000)} min — it may still finish; refresh by job ID.`);
    await new Promise((r) => setTimeout(r, interval));
  }
}

export async function rebuildIndex({ files = [], zipFile = null, companyId = "sample" } = {}) {
  const form = new FormData();
  form.append("company_id", companyId);
  for (const f of files) form.append("files", f);
  if (zipFile) form.append("zip_file", zipFile);
  const resp = await apiFetch(`/backend/api/pipeline/evidence-index`, {
    method: "POST",
    body: form,
  });
  return raiseForStatus(resp);
}

/** Live agent event stream (SSE). Returns a cleanup function. */
export function subscribeEvents({ tenderId, after = 0, onEvent, onError }) {
  const params = new URLSearchParams();
  if (tenderId) params.set("tender_id", tenderId);
  if (after) params.set("after", String(after));
  const token = getToken();
  if (token) params.set("access_token", token);
  const q = params.toString() ? `?${params.toString()}` : "";
  const src = new EventSource(`/backend/api/events/stream${q}`);
  let lastSeq = after || 0;
  src.onmessage = (msg) => {
    try {
      const event = JSON.parse(msg.data);
      lastSeq = event.seq || lastSeq;
      onEvent && onEvent(event);
    } catch {
      /* ignore malformed frames */
    }
  };
  src.onerror = () => {
    onError && onError(lastSeq);
  };
  return () => src.close();
}

export async function listIndexedDocs(companyId = "sample") {
  const resp = await apiFetch(
    `/backend/api/pipeline/evidence-index?company_id=${encodeURIComponent(companyId)}`
  );
  return raiseForStatus(resp);
}
