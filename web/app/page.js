"use client";

import { useEffect, useMemo, useState } from "react";
import AuthScreen from "../components/AuthScreen";
import BidPackage from "../components/BidPackage";
import BlockerWizard from "../components/BlockerWizard";
import Requirements from "../components/Requirements";
import StageTracker from "../components/StageTracker";
import TenderDetails from "../components/TenderDetails";
import { complianceTip, tip } from "../lib/glossary";
import { buildRefMap, say } from "../lib/refs";
import {
  createCompany,
  fetchMe,
  getJob,
  getToken,
  isUnauthorized,
  listCompanies,
  listCompanyDocs,
  logout,
  runTenderOnly,
  startJob,
  startResumeJob,
  uploadCompanyDocs,
  waitForJob,
} from "../lib/api";

const TABS = ["Home", "Run", "Dashboard", "Requirements", "Blockers", "Evidence"];

export default function Home() {
  const [tab, setTab] = useState("Home");
  const [user, setUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [companies, setCompanies] = useState([]);
  const [companyId, setCompanyId] = useState("");
  const [newCompanyName, setNewCompanyName] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reqView, setReqView] = useState({ key: 0, onlyReview: false, unmappedOnly: false });
  const [tenderFile, setTenderFile] = useState(null);
  const [fetchLinks, setFetchLinks] = useState(true);
  const [tenderOnly, setTenderOnly] = useState(false);
  const [tracking, setTracking] = useState(false);
  const [trackId, setTrackId] = useState(null);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState(null);
  // Recency of the shown result: {jobId, updatedAt}. Refresh only adopts a
  // job result NEWER than what's displayed — stale polls can never resurrect
  // an old paused state over a completed one.
  const [resultMeta, setResultMeta] = useState(null);
  const [indexMsg, setIndexMsg] = useState("");
  const [backendUp, setBackendUp] = useState(null);
  const [runKey, setRunKey] = useState(0);
  const [resumeNote, setResumeNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch("/backend/health")
      .then((r) => { if (!cancelled) setBackendUp(r.ok); })
      .catch(() => { if (!cancelled) setBackendUp(false); });
    return () => { cancelled = true; };
  }, []);

  // Per-user caches: a new account on the same browser must NEVER inherit
  // the previous user's tender state (that was the phantom "paused" bug).
  const resultKeyFor = (u) => `tenderops_result_${u}`;
  const companyKeyFor = (u) => `tenderops_company_${u}`;

  function loadUserCache(username, ownedSlugs) {
    try {
      const cached = localStorage.getItem(resultKeyFor(username));
      setResult(cached ? JSON.parse(cached) : null);
    } catch { setResult(null); }
    try {
      const saved = localStorage.getItem(companyKeyFor(username));
      if (saved && ownedSlugs.includes(saved)) setCompanyId(saved);
      else if (ownedSlugs.length) setCompanyId(ownedSlugs[0]);
      else setCompanyId("");
    } catch { setCompanyId(ownedSlugs.length ? ownedSlugs[0] : ""); }
    setResultMeta(null);
    setJobId("");
    setJob(null);
  }

  function cacheResult(out) {
    try {
      if (user) localStorage.setItem(resultKeyFor(user.username), JSON.stringify(out));
    } catch { /* too big — skip cache */ }
  }

  // Auth bootstrap: validate any saved token, then load owned companies.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getToken()) { if (!cancelled) setAuthChecked(true); return; }
      try {
        const me = await fetchMe();
        if (cancelled) return;
        setUser(me.user);
        const owned = (me.companies || []).map((c) => c.slug);
        setCompanies(me.companies || []);
        loadUserCache(me.user.username, owned);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setAuthChecked(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    try {
      if (user && companyId) localStorage.setItem(companyKeyFor(user.username), companyId);
    } catch { /* ignore */ }
  }, [companyId]);

  async function refreshCompanies(selectSlug) {
    try {
      const out = await listCompanies();
      setCompanies(out.companies || []);
      if (selectSlug) setCompanyId(selectSlug);
      else if (!companyId && (out.companies || []).length) setCompanyId(out.companies[0].slug);
    } catch (e) {
      if (isUnauthorized(e)) setUser(null);
    }
  }

  async function doLogout() {
    await logout();
    setUser(null);
    setCompanies([]);
    setCompanyId("");
    setResult(null);
    setResultMeta(null);
    setJobId("");
    setJob(null);
    setNotice("");
    setError("");
    setTab("Home");
  }

  function handleAuthFail(e) {
    if (isUnauthorized(e)) {
      setUser(null);
      setError("Session expired — please log in again.");
      return true;
    }
    return false;
  }

  const analysis = result?.analysis || {};
  const evidence = result?.evidence || {};
  const compliance = result?.compliance || {};
  const refMap = useMemo(
    () => buildRefMap(analysis.requirements || [], analysis.issues || []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [result]
  );
  const evById = useMemo(() => {
    const m = {};
    for (const i of evidence.items || []) m[i.requirement_id] = i;
    return m;
  }, [result]); // eslint-disable-line react-hooks/exhaustive-deps
  const compById = useMemo(() => {
    const m = {};
    for (const i of compliance.items || []) m[i.requirement_id] = i;
    return m;
  }, [result]); // eslint-disable-line react-hooks/exhaustive-deps

  // Route by PIPELINE outcome, not job status: a DONE job whose pipeline
  // is still PAUSED_ON_BLOCKERS goes to Blockers, never "back" to a
  // dashboard that then looks blocked. Returns a notice for the banner.
  function routeAfterPipeline(out) {
    const blockers = out?.compliance?.blockers?.length || 0;
    const paused = out?.pipeline_status === "PAUSED_ON_BLOCKERS" || blockers > 0;
    if (paused) {
      setTab("Blockers");
      return `Pipeline paused on ${blockers} blocker(s) — upload fixes or waive below to continue.`;
    }
    setTab("Dashboard");
    return "";
  }

  function trackResult(out, meta) {
    setResult(out);
    setResultMeta(meta || null);
    cacheResult(out);
  }

  async function doRun() {
    if (!tenderFile) return;
    if (!companyId) { setError("Create or select a company first (Evidence tab)."); return; }
    setBusy(true);
    setError("");
    setNotice("");
    setTracking(true);
    setTrackId(null);
    setRunKey((k) => k + 1);
    try {
      if (tenderOnly) {
        const out = await runTenderOnly(tenderFile, { fetchLinks });
        // Tender-only responses carry their own tender_id inside analysis.
        setTrackId(out.tender_id || out.analysis?.tender_id || null);
        trackResult(out, { jobId: "tender-only", updatedAt: new Date().toISOString() });
        setTab("Dashboard");
      } else {
        // Background job, not the sync /pipeline/run endpoint: the POST
        // returns in ms with a job_id and the worker runs P1→P4 while we
        // poll. No long-lived HTTP connection, so the dev proxy never
        // times out (previously: socket hang up / ECONNRESET on big runs).
        const { job_id } = await startJob(tenderFile, { fetchLinks, companyId });
        setJobId(job_id);
        const finished = await waitForJob(job_id, {
          timeout: 1800000,
          onUpdate: (j) => setJob(j),
        });
        setJob(finished);
        if (finished.result) {
          setTrackId(finished.result.tender_id || null);
          trackResult(finished.result, { jobId: job_id, updatedAt: finished.updated_at || "" });
          setNotice(routeAfterPipeline(finished.result));
        } else {
          setTab("Dashboard");
        }
      }
    } catch (e) {
      if (!handleAuthFail(e)) setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function pollJob() {
    if (!jobId) return;
    try {
      const j = await getJob(jobId);
      setJob(j);
      if (j.status === "DONE" && j.result) {
        // Adopt only newer results: Refresh must never clobber a newer
        // completed state with this job's older snapshot.
        const incoming = j.updated_at || "";
        const current = resultMeta?.updatedAt || "";
        const sameJob = resultMeta?.jobId === jobId;
        if (!result || !resultMeta || (!sameJob && incoming && incoming > current)) {
          trackResult(j.result, { jobId, updatedAt: incoming });
          setNotice(routeAfterPipeline(j.result));
        } else if (sameJob) {
          setNotice("");
        }
      }
      setError("");
    } catch (e) {
      if (!handleAuthFail(e)) setError(String(e.message || e));
    }
  }

  const status = result?.pipeline_status || analysis.analysis_status || "?";
  const statusColor =
    status === "COMPLETED" || status === "SUCCESS" ? "text-emerald-300"
    : status.startsWith("PAUSE") ? "text-amber-300" : "text-zinc-300";

  if (!authChecked) {
    return (
      <div className="mx-auto max-w-6xl px-4 pb-16">
        <div className="card mt-10 text-center text-zinc-400">Loading TenderOps…</div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="mx-auto max-w-6xl px-4 pb-16">
        <AuthScreen onAuth={async (u) => {
          setUser(u);
          try {
            const me = await fetchMe();
            const owned = (me.companies || []).map((c) => c.slug);
            setCompanies(me.companies || []);
            loadUserCache(u.username, owned);
          } catch {
            setCompanies([]);
            loadUserCache(u.username, []);
          }
          setTab("Home");
        }} />
      </div>
    );
  }

  const activeCompany = companies.find((c) => c.slug === companyId);

  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <header className="flex flex-wrap items-center gap-3 py-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-500 font-black text-zinc-950">T</div>
        <div className="mr-auto">
          <h1 className="text-xl font-bold tracking-tight">TenderOps</h1>
          <p className="text-xs text-zinc-500">Agentic tender analysis &amp; bid preparation, end to end</p>
        </div>
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          Company
          <select className="input max-w-[180px]" value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
            title="Evidence is searched strictly inside this company's private index">
            <option value="" disabled>Select…</option>
            {companies.map((c) => (
              <option key={c.slug} value={c.slug}>{c.name}</option>
            ))}
          </select>
        </label>
        <span className="badge" title="Logged in">👤 {user.username}</span>
        <button className="btn-ghost text-xs" onClick={doLogout}>Log out</button>
        <nav className="flex gap-1 rounded-xl border border-white/10 bg-zinc-900/70 p-1">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-sm ${tab === t ? "bg-emerald-500 text-zinc-950 font-semibold" : "text-zinc-400 hover:text-zinc-100"}`}>
              {t}
              {t === "Blockers" && (compliance.blockers || []).length > 0 && (
                <span className="ml-1 rounded-full bg-amber-400 px-1.5 text-[11px] font-bold text-zinc-950">
                  {(compliance.blockers || []).length}
                </span>
              )}
            </button>
          ))}
        </nav>
      </header>

      {error && <div className="card mb-4 border-red-500/40 text-red-200">⚠️ {error}</div>}
      {notice && <div className="card mb-4 border-cyan-500/40 text-cyan-100">ℹ️ {notice}</div>}
      {result && (
        <div className="card mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-emerald-500/30">
          <div className="mr-auto min-w-[200px]">
            <div className="text-[11px] uppercase tracking-wide text-zinc-500">Working on tender</div>
            <div className="font-semibold leading-tight">
              {analysis.metadata?.tender_title || result.tender_id}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-zinc-400">
              {analysis.metadata?.tender_reference && <span>Ref: <code className="badge">{analysis.metadata.tender_reference}</code></span>}
              {analysis.metadata?.issuing_authority && <span>🏛 {analysis.metadata.issuing_authority}</span>}
              {analysis.metadata?.submission_deadline && <span>⏰ Due {analysis.metadata.submission_deadline}</span>}
              {analysis.metadata?.emd_amount && <span>💰 EMD {analysis.metadata.emd_amount}</span>}
            </div>
          </div>
          <span className={`text-sm font-bold ${statusColor}`}>{status}</span>
          <code className="badge">{result.tender_id}</code>
          {activeCompany && <span className="badge" title="Company workspace">🏢 {activeCompany.name}</span>}
        </div>
      )}
      {backendUp === false && (
        <div className="card mb-4 border-red-500/40 text-red-200">
          ⚠️ Backend unreachable at <code>127.0.0.1:8000</code> — start it first:{" "}
          <code className="badge">uv run uvicorn app.main:app --reload</code>{" "}
          (without <code>--reload</code> for real multi-minute runs — a reload
          kills in-flight requests; or use “Run async” below). Set{" "}
          <code className="badge">BACKEND_URL</code> if it lives elsewhere.
        </div>
      )}

      {tab === "Home" && (
        <div className="space-y-6">
          <div className="card overflow-hidden">
            <div className="grid items-center gap-6 md:grid-cols-2">
              <div>
                <div className="mb-2 inline-block rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-300">
                  Agentic bid preparation · P1 → P4
                </div>
                <h2 className="text-3xl font-black leading-tight tracking-tight">
                  From tender PDF to <span className="text-emerald-400">submission-ready bid</span>, supervised end to end.
                </h2>
                <p className="mt-3 text-sm leading-relaxed text-zinc-400">
                  TenderOps reads Government e-Marketplace tenders the way a bid manager would:
                  it extracts every requirement, proves each one against <em>your</em> company's
                  private document index, assesses compliance, pauses on blockers for human fixes,
                  then drafts grounded responses and verifies them with an independent checker —
                  never the drafter itself.
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <button className="btn-primary" onClick={() => setTab("Run")}>Analyze a tender</button>
                  <button className="btn-ghost" onClick={() => setTab("Evidence")}>Set up company documents</button>
                </div>
                {result && (
                  <button className="mt-3 text-sm text-cyan-300 underline" onClick={() => setTab("Dashboard")}>
                    Continue working on {analysis.metadata?.tender_title || result.tender_id} →
                  </button>
                )}
              </div>
              <div className="grid gap-2">
                {[
                  ["P1 · Tender Analyst", "Extracts metadata, requirements with thresholds, and cross-chunk issues from the tender PDF and its annexures."],
                  ["P2 · Evidence Agent", "Maps every requirement against your company's private index — cosine recall plus deterministic numeric verification."],
                  ["P3 · Compliance", "Deterministic rules engine: compliant / non-compliant / pending / needs-review, with auditable blockers and human waivers."],
                  ["P4 · Bid prep + Verify", "Drafts grounded responses, then an independent verifier checks them. Bounded fix loop, risks always flagged."],
                ].map(([title, body]) => (
                  <div key={title} className="rounded-xl border border-white/10 bg-white/5 p-3">
                    <div className="text-sm font-semibold text-emerald-300">{title}</div>
                    <div className="mt-0.5 text-xs leading-relaxed text-zinc-400">{body}</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            {[
              ["🔒 Private by design", "Login, per-user company workspaces, and strict isolation: your documents and runs are never visible to other accounts."],
              ["🎯 Fix what blocks you", "Upload a missing certificate against its exact blocker — only that requirement is re-checked, only against your file."],
              ["📡 Always observable", "Every agent step, retry, provider fallback and waiver streams live. Switch tabs freely — the timeline persists."],
            ].map(([title, body]) => (
              <div key={title} className="card">
                <div className="font-semibold">{title}</div>
                <div className="mt-1 text-sm text-zinc-400">{body}</div>
              </div>
            ))}
          </div>
          <div className="card">
            <h3 className="mb-2 font-semibold">How a run flows</h3>
            <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-300">
              <li>Create your account, then a <button className="underline" onClick={() => setTab("Evidence")}>company workspace</button> and upload its documents (turnover, experience, certificates).</li>
              <li><button className="underline" onClick={() => setTab("Run")}>Run the pipeline</button> on a tender PDF — it executes in the background while you watch the live tracker.</li>
              <li>If it pauses, open <button className="underline" onClick={() => setTab("Blockers")}>Blockers</button>: attach each missing document to its blocker, or waive with a recorded override — then resume.</li>
              <li>When unblocked, P4 drafts the bid package and verifies it. Inspect everything on the <button className="underline" onClick={() => setTab("Dashboard")}>Dashboard</button>.</li>
            </ol>
          </div>
        </div>
      )}

      {tab === "Run" && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card">
            <h2 className="mb-3 font-semibold">Analyze a tender</h2>
            <input type="file" accept=".pdf" className="input mb-3"
              onChange={(e) => setTenderFile(e.target.files?.[0] || null)} />
            <p className="mb-3 text-xs text-zinc-500">
              Evidence will be searched in <code className="badge">{activeCompany?.name || companyId || "—"}</code>
              {companies.length === 0 && <> — <button className="underline" onClick={() => setTab("Evidence")}>create a company first</button></>}
            </p>
            <label className="mb-3 flex items-center gap-2 text-sm text-zinc-300">
              <input type="checkbox" checked={tenderOnly} onChange={(e) => setTenderOnly(e.target.checked)} />
              Tender only (P1, skip evidence + compliance)
            </label>
            <label className="mb-4 flex items-center gap-2 text-sm text-zinc-300">
              <input type="checkbox" checked={fetchLinks} onChange={(e) => setFetchLinks(e.target.checked)} />
              Follow embedded links / annexures
            </label>
            <div className="flex gap-2">
              <button className="btn-primary" disabled={!tenderFile || busy} onClick={doRun}>
                {busy ? "Agents running in background…" : "Run pipeline"}
              </button>
            </div>
            <p className="mt-2 text-xs text-zinc-500">
              Runs in the background — safe to switch tabs; the tracker shows
              live progress and results land on the Dashboard automatically.
            </p>
            {(jobId || job) && (
              <div className="mt-3 flex items-center gap-2 text-sm">
                <code className="badge" title={tip("job_vs_pipeline")}>{jobId}</code>
                <span className="text-zinc-400" title={job?.status ? tip(`job_${job.status}`) : tip("job_QUEUED")}>{job?.status || "QUEUED"}</span>
                <button className="btn-ghost" onClick={pollJob} title="Re-read this job. Only a result newer than what's shown will replace it.">Refresh</button>
              </div>
            )}
          </div>
          <div>
            <StageTracker tenderId={trackId} active={busy || tracking} sessionKey={runKey} />
            {!busy && !tracking && (
              <div className="card text-sm text-zinc-500">
                Start a run to watch P1 → P2 → P3 → P4 light up live, with every
                tool call, retry, and provider fallback streamed below.
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "Dashboard" && (
        !result ? <div className="card text-zinc-400">Run an analysis or load a saved JSON first.</div> : (
          <div className="space-y-4">
            <div className="card flex flex-wrap items-center gap-3">
              <div className="mr-auto">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">Pipeline outcome</div>
                <span className={`text-lg font-bold ${statusColor}`}
                  title={String(status).startsWith("PAUSE") ? tip("pipeline_PAUSED_ON_BLOCKERS") : tip("pipeline_COMPLETED")}>{status}</span>
                <div className="text-xs text-zinc-500">
                  {String(status).startsWith("PAUSE")
                    ? "Blocked — resolve or waive blockers to continue."
                    : "Done — bid package below (if produced)."}
                </div>
              </div>
              <code className="badge">{result.tender_id}</code>
              {job && (
                <span className="badge" title={tip("job_vs_pipeline")}>
                  job {job.job_id || jobId}: {job.status} — {tip(`job_${job.status}`)}
                </span>
              )}
              {result.checkpoint_path && <code className="badge">checkpoint saved</code>}
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              {[
                ["Requirements", (analysis.requirements || []).length, "Bidder obligations extracted from the tender — click to inspect each one.", () => { setReqView({ key: Date.now(), onlyReview: false, unmappedOnly: false }); setTab("Requirements"); }],
                ["Need review", analysis.requirements_needing_review || 0, "Items where extraction or mapping was uncertain — a human should verify these.", () => { setReqView({ key: Date.now(), onlyReview: true, unmappedOnly: false }); setTab("Requirements"); }],
                ["Issues", (analysis.issues || []).length, "Tender-level findings: missing attachments and contradictions between clauses.", () => setTab("Blockers")],
                ["Blockers", (compliance.blockers || []).length, "Open findings stopping submission. Upload fixes or waive them on the Blockers tab.", () => setTab("Blockers")],
                ["Unmapped", (evidence.unmapped_requirements || []).length, tip("unmapped"), () => { setReqView({ key: Date.now(), onlyReview: false, unmappedOnly: true }); setTab("Requirements"); }],
              ].map(([label, v, meaning, go]) => (
                <button key={label} onClick={go} title={`${meaning} Click to open.`}
                  className="card py-3 text-center transition-colors hover:border-emerald-400/50 hover:bg-white/10">
                  <div className="text-2xl font-bold">{v}</div>
                  <div className="text-xs text-zinc-500">{label} <span className="text-zinc-600">→</span></div>
                </button>
              ))}
            </div>
            {compliance.summary && (
              <div className="card">
                <h3 className="mb-2 font-semibold">Compliance — {compliance.decision}</h3>
                <div className="flex h-3 overflow-hidden rounded-full bg-white/10">
                  {Object.entries({
                    compliant: "bg-emerald-500", non_compliant: "bg-red-500",
                    pending: "bg-amber-400", needs_review: "bg-cyan-400",
                  }).map(([k, cls]) => {
                    const total = Math.max(1, (analysis.requirements || []).length);
                    return <div key={k} className={cls} style={{ width: `${((compliance.summary[k] || 0) / total) * 100}%` }} title={`${complianceTip(k.toUpperCase())} Count: ${compliance.summary[k] || 0}`} />;
                  })}
                </div>
                <div className="mt-2 text-xs text-zinc-400">
                  {["compliant", "non_compliant", "pending", "needs_review"].map((k) => `${k}: ${compliance.summary[k] || 0}`).join(" · ")}
                </div>
              </div>
            )}
            <div className="grid gap-4 md:grid-cols-2">
              <details className="card" open>
                <summary className="cursor-pointer font-semibold">Metadata</summary>
                <pre className="scroll-slim mt-2 max-h-64 overflow-auto text-xs text-zinc-400">{JSON.stringify(analysis.metadata || {}, null, 1)}</pre>
              </details>
              <details className="card" open>
                <summary className="cursor-pointer font-semibold">Completeness &amp; warnings</summary>
                <pre className="scroll-slim mt-2 max-h-32 overflow-auto text-xs text-zinc-400">{JSON.stringify(analysis.completeness || {}, null, 1)}</pre>
                {(analysis.warnings || []).slice(0, 8).map((w, i) => (
                  <div key={i} className="mt-1 text-xs text-amber-200/90">⚠️ {String(w).slice(0, 220)}</div>
                ))}
              </details>
            </div>
            <BidPackage pkg={result.package} verification={result.verification} refMap={refMap} />
          </div>
        )
      )}

      {tab === "Requirements" && (
        !result ? <div className="card text-zinc-400">Run an analysis or load a saved JSON first.</div> : (
          <div className="space-y-4">
            <TenderDetails metadata={analysis.metadata} tenderId={result.tender_id} />
            <Requirements
              key={reqView.key}
              requirements={analysis.requirements || []}
              evidenceById={evById}
              complianceById={compById}
              refMap={refMap}
              initialOnlyReview={reqView.onlyReview}
              unmappedOnly={reqView.unmappedOnly}
              unmappedIds={evidence.unmapped_requirements || []}
            />
          </div>
        )
      )}

      {tab === "Blockers" && (
        !result ? <div className="card text-zinc-400">Run an analysis or load a saved JSON first.</div> : (
          <div className="space-y-4">
            <BlockerWizard
              blockers={compliance.blockers || []}
              requirements={analysis.requirements || []}
              refMap={refMap}
              busy={busy}
              companyId={companyId}
              statusNote={resumeNote}
              evidenceById={evById}
              complianceById={compById}
              onResume={async ({ files, zipFile, waive, fixMap }) => {
                setBusy(true);
                setError("");
                setNotice("");
                setResumeNote("");
                // Background resume: the POST returns in ms with a job_id;
                // each blocker is re-checked ONLY against its own fix files
                // while we poll. No long-lived HTTP connection, so the proxy
                // never times out.
                setTrackId(result.tender_id);
                setTracking(true);
                setRunKey((k) => k + 1);
                try {
                  const { job_id } = await startResumeJob(result.tender_id, {
                    files, zipFile, waive, companyId, fixMap,
                  });
                  setResumeNote(`Resume job ${job_id}: QUEUED…`);
                  const finished = await waitForJob(job_id, {
                    onUpdate: (j) => setResumeNote(
                      `Resume job ${job_id}: ${j.status}… fix files re-checked per blocker, tracker below shows each verdict.`
                    ),
                  });
                  const out = finished.result || {};
                  const merged = { ...result, ...out, tender_id: out.tender_id || result.tender_id };
                  // A still-paused resume carries no package: never wipe one
                  // already held (e.g. loaded from saved JSON) with nulls.
                  if (out.package == null) merged.package = result.package;
                  if (out.verification == null) merged.verification = result.verification;
                  trackResult(merged, { jobId: job_id, updatedAt: finished.updated_at || "" });
                  setResumeNote(`Resume job ${job_id}: DONE — compliance reassessed.`);
                  setNotice(routeAfterPipeline(merged));
                } catch (e) {
                  if (!handleAuthFail(e)) setError(String(e.message || e));
                } finally {
                  setBusy(false);
                }
              }}
            />
            <StageTracker tenderId={result.tender_id} active={busy} sessionKey={runKey} />
            <div className="card">
              <h3 className="mb-2 font-semibold">Tender issues ({(analysis.issues || []).length})</h3>
              {(analysis.issues || []).map((issue) => (
                <details key={issue.issue_id} className="mb-2 rounded-xl border border-white/10 p-3">
                  <summary className="cursor-pointer text-sm">
                    <code className="badge">{issue.issue_id}</code> {issue.issue_type} ({issue.severity})
                  </summary>
                  <p className="mt-2 text-sm text-zinc-300">{say(issue.description, refMap)}</p>
                </details>
              ))}
            </div>
          </div>
        )
      )}

      {tab === "Evidence" && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="card">
            <h3 className="mb-2 font-semibold">Company workspace</h3>
            {companies.length === 0 ? (
              <p className="mb-3 text-sm text-amber-200/90">
                No company yet — create one to hold its private document index.
              </p>
            ) : (
              <p className="mb-3 text-sm text-zinc-400">
                Acting as <code className="badge">{activeCompany?.name || companyId}</code> —
                uploads land only in this company's private index.
              </p>
            )}
            <div className="flex gap-2">
              <input className="input" value={newCompanyName}
                onChange={(e) => setNewCompanyName(e.target.value)}
                placeholder="New company name (e.g. Acme Pvt Ltd)" />
              <button className="btn-primary whitespace-nowrap" disabled={busy || newCompanyName.trim().length < 2}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  try {
                    const c = await createCompany(newCompanyName.trim());
                    setNewCompanyName("");
                    await refreshCompanies(c.slug);
                    setIndexMsg(`Company “${c.name}” created — upload its documents below.`);
                  } catch (e) {
                    if (!handleAuthFail(e)) setError(String(e.message || e));
                  } finally {
                    setBusy(false);
                  }
                }}>
                Create
              </button>
            </div>
            {companies.length > 0 && (
              <select className="input mt-3" value={companyId} onChange={(e) => setCompanyId(e.target.value)}>
                {companies.map((c) => <option key={c.slug} value={c.slug}>{c.name}</option>)}
              </select>
            )}
          </div>
          <div className="card">
            <CompanyDocs companyId={companyId} refreshKey={indexMsg} onAuthFail={handleAuthFail} />
          </div>
          <div className="card">
            <CompanyUploader companyId={companyId} setIndexMsg={setIndexMsg} setBusy={setBusy} busy={busy} onAuthFail={handleAuthFail} />
            {indexMsg && <div className="mt-2 text-sm text-zinc-300">{indexMsg}</div>}
          </div>
          <div className="card">
            <h3 className="mb-2 font-semibold">Load saved result JSON</h3>
            <input type="file" accept=".json" className="input" onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              try {
                const raw = JSON.parse(await f.text());
                setResult(raw.analysis ? raw : { analysis: raw });
                setTab("Dashboard");
              } catch (err) {
                setError(`Invalid JSON: ${err.message}`);
              }
            }} />
            <p className="mt-2 text-xs text-zinc-500">Open any saved pipeline/analysis response for offline inspection.</p>
          </div>
        </div>
      )}
    </div>
  );

  function CompanyDocs({ companyId, refreshKey, onAuthFail }) {
    const [inventory, setInventory] = useState(null);
    const [loading, setLoading] = useState(false);
    useEffect(() => {
      if (!companyId) { setInventory(null); return; }
      let cancelled = false;
      setLoading(true);
      listCompanyDocs(companyId)
        .then((inv) => { if (!cancelled) setInventory(inv); })
        .catch((e) => {
          if (cancelled) return;
          if (onAuthFail && onAuthFail(e)) return;
          setInventory({ files: [], documents: 0, chunks: 0, error: true });
        })
        .finally(() => { if (!cancelled) setLoading(false); });
      return () => { cancelled = true; };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [companyId, refreshKey]);
    return (
      <div>
        <h3 className="mb-2 font-semibold">
          Documents{companyId ? <> for <code className="badge">{companyId}</code></> : ""}
        </h3>
        {loading && <div className="text-sm text-zinc-500">Reading index…</div>}
        {!loading && (!inventory || inventory.files?.length === 0) && (
          <div className="text-sm text-zinc-500">
            Nothing indexed for this company yet — upload PDFs or a zip on the left.
          </div>
        )}
        {(inventory?.files || []).map((f) => (
          <div key={f.name} className="mb-2 rounded-xl border border-white/10 bg-white/5 p-3">
            <div className="font-mono text-sm">📄 {f.name}</div>
            <div className="text-xs text-zinc-400">
              {f.chunks} chunk(s) · pages {Array.isArray(f.pages) ? f.pages.join(", ") : "—"}
            </div>
          </div>
        ))}
        {inventory && inventory.files?.length > 0 && (
          <div className="mt-1 text-xs text-zinc-500">
            {inventory.documents} document(s), {inventory.chunks} chunks total.
          </div>
        )}
      </div>
    );
  }

  function CompanyUploader({ companyId, setIndexMsg, setBusy, busy, onAuthFail }) {
    const [files, setFiles] = useState([]);
    const [zip, setZip] = useState(null);
    return (
      <div className="space-y-2">
        <h3 className="font-semibold">Add documents</h3>
        <p className="text-xs text-zinc-500">Appended to the index (never wiped). Scanned PDFs, certificates, statements.</p>
        <input type="file" accept=".pdf" multiple className="input"
          onChange={(e) => setFiles([...(e.target.files || [])])} />
        <input type="file" accept=".zip" className="input"
          onChange={(e) => setZip(e.target.files?.[0] || null)} />
        <button
          className="btn-primary" disabled={busy || !companyId || (!files.length && !zip)}
          onClick={async () => {
            setBusy(true);
            try {
              const out = await uploadCompanyDocs(companyId, { files, zipFile: zip });
              setFiles([]); setZip(null);
              setIndexMsg(`Indexed ${out.documents} document(s), ${out.chunks} chunks for ${out.company_profile_id}.`);
            } catch (e) {
              if (onAuthFail && onAuthFail(e)) return;
              setIndexMsg(`Failed: ${e.message}`);
            } finally {
              setBusy(false);
            }
          }}
        >
          Upload to {companyId || "company"}
        </button>
      </div>
    );
  }
}
