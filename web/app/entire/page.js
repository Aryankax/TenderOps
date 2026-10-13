"use client";

import { useState } from "react";
import {
  briefEntire,
  demoEntire,
  fetchEntireStatus,
  listEntireCheckpoints,
  reviewEntire,
  scoutEntire,
  searchEntire,
} from "../../lib/entire";

export default function EntirePage() {
  const [status, setStatus] = useState(null);
  const [checkpoints, setCheckpoints] = useState([]);
  const [query, setQuery] = useState("");
  const [output, setOutput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function run(fn, format) {
    setBusy(true);
    setError("");
    try {
      const out = await fn();
      setOutput(format ? format(out) : JSON.stringify(out, null, 1));
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-6xl px-4 pb-16">
      <header className="py-6">
        <h1 className="text-xl font-bold tracking-tight">Entire Memory</h1>
        <p className="text-xs text-zinc-500">
          Checkpoints, agentic search, Graph and Brain for TenderOps (Neathacks 2026)
        </p>
      </header>
      {error && <div className="card mb-4 border-red-500/40 text-red-200">⚠️ {error}</div>}
      <div className="mb-4 flex flex-wrap gap-2">
        <button className="btn-ghost" disabled={busy}
          onClick={() => run(fetchEntireStatus)}>Status</button>
        <button className="btn-ghost" disabled={busy}
          onClick={() => run(listEntireCheckpoints, (o) => {
            setCheckpoints(o.checkpoints || []);
            return JSON.stringify(o, null, 1);
          })}>Checkpoints</button>
        <input className="input max-w-[280px]" value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search past runs / code…" />
        <button className="btn-ghost" disabled={busy || !query}
          onClick={() => run(() => searchEntire(query))}>Search</button>
        <button className="btn-ghost" disabled={busy || !query}
          onClick={() => run(() => briefEntire(query), (o) => o.briefing_md)}>Brief</button>
        <button className="btn-ghost" disabled={busy || !query}
          onClick={() => run(() => reviewEntire(query, [], []),
            (o) => `${o.verdict}\n${(o.flags || []).join("\n")}`)}>Review</button>
        <button className="btn-ghost" disabled={busy}
          onClick={() => run(() => scoutEntire(checkpoints[0]?.id || ""))}>Scout</button>
        {checkpoints[0] && (
          <button className="btn-ghost" disabled={busy}
            onClick={() => run(() => demoEntire(checkpoints[0].id), (o) => o.demo_md)}>
            Demo: {checkpoints[0].id}
          </button>
        )}
      </div>
      {status && <div className="card mb-4 text-xs text-zinc-400">{JSON.stringify(status)}</div>}
      <pre className="card scroll-slim max-h-[60vh] overflow-auto whitespace-pre-wrap text-xs text-zinc-300">
        {output || "Run an action above — results land here."}
      </pre>
    </div>
  );
}
