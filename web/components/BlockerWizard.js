"use client";

import { useMemo, useState } from "react";
import { blockerTip, tip } from "../lib/glossary";
import { say } from "../lib/refs";

/**
 * Step-through blocker resolution: for each blocker choose upload-fix /
 * waive / leave, attaching fix document(s) directly on the blocker card.
 * On resume, every attached file (per-blocker + extras) is appended to the
 * company's evidence index and affected checks are re-run.
 */
export default function BlockerWizard({ blockers, requirements, refMap, onResume, busy, companyId = "sample", statusNote = "", evidenceById = {}, complianceById = {} }) {
  const [step, setStep] = useState(0);
  const [decisions, setDecisions] = useState({}); // blocker_id -> {action, files:[]}
  const [extraFiles, setExtraFiles] = useState([]);
  const [extraZip, setExtraZip] = useState(null);

  const titles = useMemo(() => {
    const map = {};
    for (const r of requirements || []) map[r.requirement_id] = r.title;
    return map;
  }, [requirements]);

  const reqById = useMemo(() => {
    const map = {};
    for (const r of requirements || []) map[r.requirement_id] = r;
    return map;
  }, [requirements]);
  const [showFull, setShowFull] = useState(false);

  if (!blockers || blockers.length === 0) {
    return <div className="card text-emerald-300">No blockers — nothing to resolve. ✅</div>;
  }

  const done = step >= blockers.length;
  const current = !done ? blockers[step] : null;
  const decide = (id, action) =>
    setDecisions((d) => ({ ...d, [id]: { files: [], ...(d[id] || {}), action } }));
  const attachFiles = (id, fileList) => {
    const picked = [...(fileList || [])];
    if (!picked.length) return;
    setDecisions((d) => ({
      ...d,
      [id]: { files: [], ...(d[id] || {}), action: "fix", files: [...((d[id] || {}).files || []), ...picked] },
    }));
  };
  const removeFile = (id, idx) =>
    setDecisions((d) => ({
      ...d,
      [id]: { ...(d[id] || {}), files: ((d[id] || {}).files || []).filter((_, i) => i !== idx) },
    }));

  const waiveIds = blockers.filter((b) => decisions[b.blocker_id]?.action === "waive").map((b) => b.blocker_id);
  const fixBlockers = blockers.filter((b) => decisions[b.blocker_id]?.action === "fix");
  const perBlockerFiles = fixBlockers.flatMap((b) => (decisions[b.blocker_id]?.files || []).map((f) => ({ blocker: b.blocker_id, file: f })));
  const allFixFiles = [...perBlockerFiles.map((x) => x.file), ...extraFiles];

  return (
    <div className="card">
      <div className="mb-1 flex items-center justify-between">
        <h3 className="font-semibold">
          Resolve blockers {done ? "— summary" : `(${step + 1} of ${blockers.length})`}
        </h3>
        {!done && (
          <div className="flex gap-2">
            {step > 0 && <button className="btn-ghost" onClick={() => setStep(step - 1)}>← Back</button>}
            <button className="btn-ghost" onClick={() => setStep(step + 1)}>
              {decisions[current.blocker_id] ? "Next →" : "Skip →"}
            </button>
          </div>
        )}
      </div>
      {!done && (
        <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-white/10">
          <div className="h-full bg-emerald-500 transition-all" style={{ width: `${(step / blockers.length) * 100}%` }} />
        </div>
      )}

      {!done ? (
        <div key={current.blocker_id}>
          <div className="mb-2 flex items-center gap-2">
            <span className="text-xl" title={current.severity === "high" ? tip("severity_high") : tip("severity_medium")}>{current.severity === "high" ? "🔴" : "🟡"}</span>
            <code className="badge" title="Blocker code. Numbers restart every evaluation — waivers match the finding, not the number.">{current.blocker_id}</code>
            <span className="badge" title={blockerTip(current.blocker_type)}>{current.blocker_type}</span>
            {current.requires_human_review && <span className="badge" title={tip("needs_review")}>human review</span>}
          </div>
          <p className="mb-2">{say(current.description, refMap)}</p>
          <p className="mb-1 text-sm text-zinc-400">
            Affected: {(current.affected_requirement_ids || [])
              .map((id) => titles[id] ? `${titles[id]} [${id}]` : id)
              .join("; ") || "—"}
          </p>
          <p className="mb-2 text-sm text-cyan-200/90">Fix: {say(current.resolution_hint, refMap)}</p>
          <button className="btn-ghost mb-4 text-xs" onClick={() => setShowFull(!showFull)}>
            {showFull ? "▾ Hide full notes" : "▸ Show full notes (requirements + evidence + compliance)"}
          </button>
          {showFull && (
            <div className="mb-4 space-y-2 rounded-xl border border-white/10 bg-black/30 p-3">
              {(current.affected_requirement_ids || []).length === 0 && (
                <div className="text-xs text-zinc-500">
                  Tender-level finding — no single requirement scope. See tender issues below for the full text.
                </div>
              )}
              {(current.affected_requirement_ids || []).map((id) => {
                const req = reqById[id] || {};
                const ev = evidenceById[id];
                const comp = complianceById[id];
                return (
                  <details key={id} className="rounded-lg bg-white/5 p-2 text-sm" open>
                    <summary className="cursor-pointer font-medium">
                      {req.title || id} <code className="badge">{id}</code>
                    </summary>
                    <div className="mt-2 space-y-1.5 text-[13px]">
                      {req.normalized_description && (
                        <div><span className="text-zinc-500">Requirement: </span>{req.normalized_description}</div>
                      )}
                      {req.original_text && (
                        <div><span className="text-zinc-500">Tender says: </span><span className="text-zinc-300">{req.original_text.slice(0, 600)}</span></div>
                      )}
                      {(req.required_evidence || []).length > 0 && (
                        <div><span className="text-zinc-500">Documents needed: </span>{req.required_evidence.join("; ")}</div>
                      )}
                      {ev && (
                        <div className="rounded-lg bg-white/5 p-2">
                          <div>Evidence: <code>{ev.status}</code> (confidence {ev.confidence ?? "—"})</div>
                          {ev.computed_value != null && (
                            <div>Company figure: <code>{ev.computed_value} {ev.computed_unit || ""}</code></div>
                          )}
                          {ev.notes && <div className="mt-1 text-zinc-300">{say(ev.notes, refMap)}</div>}
                          {(ev.hits || []).slice(0, 3).map((h, i) => (
                            <div key={i} className="mt-1 font-mono text-[11px] text-cyan-300/80">
                              📄 {h.document_name} p.{h.page_number} (score {h.score})
                            </div>
                          ))}
                        </div>
                      )}
                      {comp && (
                        <div>
                          Compliance: <code>{comp.status}</code>
                          {(comp.reasons || []).map((r, i) => (
                            <div key={i} className="mt-0.5 text-zinc-300">• {say(r, refMap)}</div>
                          ))}
                        </div>
                      )}
                      {(req.source_references || []).slice(0, 2).map((s, i) => (
                        <div key={i} className="font-mono text-[11px] text-zinc-500">
                          Source: {s.document_name} p.{s.page_number} — {s.section_heading || ""}
                        </div>
                      ))}
                    </div>
                  </details>
                );
              })}
            </div>
          )}
          <div className="grid gap-2 md:grid-cols-3">
            {[
              ["fix", "📎 Upload fix", "Attach doc(s) below, applied on resume"],
              ["waive", "✅ Waive", "Human override — recorded, stays visible"],
              ["leave", "⏸ Leave", "Keeps blocking; run stays paused"],
            ].map(([action, label, hint]) => (
              <button
                key={action}
                onClick={() => decide(current.blocker_id, action)}
                className={`rounded-xl border p-3 text-left transition-colors ${
                  decisions[current.blocker_id]?.action === action
                    ? "border-emerald-400 bg-emerald-400/10"
                    : "border-white/10 bg-white/5 hover:border-white/30"
                }`}
              >
                <div className="font-medium">{label}</div>
                <div className="text-xs text-zinc-400">{hint}</div>
              </button>
            ))}
          </div>

          <div className="mt-3 rounded-xl border border-white/10 bg-white/5 p-3">
            <label className="mb-1 block text-sm font-medium text-zinc-200">
              📎 Fix document(s) for {current.blocker_id} (PDFs)
            </label>
            <p className="mb-2 text-xs text-zinc-500">
              Upload the missing certificate / statement that resolves this blocker.
              Attached files are indexed under <code className="badge">{companyId}</code> and
              re-checked on resume. Attaching auto-marks this blocker as “Upload fix”.
            </p>
            <input
              type="file" accept=".pdf" multiple className="input"
              onChange={(e) => { attachFiles(current.blocker_id, e.target.files); e.target.value = ""; }}
            />
            {((decisions[current.blocker_id] || {}).files || []).length > 0 && (
              <ul className="mt-2 space-y-1 text-xs text-zinc-300">
                {(decisions[current.blocker_id].files || []).map((f, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className="font-mono">📄 {f.name}</span>
                    <span className="text-zinc-500">{(f.size / 1024).toFixed(0)} KB</span>
                    <button className="btn-ghost px-2 py-0.5 text-xs" onClick={() => removeFile(current.blocker_id, i)}>✕</button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : (
        <div>
          <div className="mb-3 text-sm text-zinc-300">
            {fixBlockers.length} to fix with uploads · {waiveIds.length} waived ·{" "}
            {blockers.length - fixBlockers.length - waiveIds.length} left blocking
          </div>
          {perBlockerFiles.length > 0 && (
            <ul className="mb-3 space-y-1 text-xs text-zinc-300">
              {perBlockerFiles.map(({ blocker, file }, i) => (
                <li key={i} className="font-mono">📄 {file.name} <span className="text-zinc-500">→ {blocker}</span></li>
              ))}
            </ul>
          )}
          <div className="mb-3 grid gap-3 md:grid-cols-2">
            <div>
              <label className="text-sm text-zinc-400">Extra fix documents (PDFs, optional)</label>
              <input type="file" accept=".pdf" multiple className="input"
                onChange={(e) => setExtraFiles([...(e.target.files || [])])} />
              {extraFiles.length > 0 && <div className="text-xs text-zinc-500">{extraFiles.length} extra file(s) staged</div>}
            </div>
            <div>
              <label className="text-sm text-zinc-400">Or a zip of PDFs</label>
              <input type="file" accept=".zip" className="input"
                onChange={(e) => setExtraZip(e.target.files?.[0] || null)} />
            </div>
          </div>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() => {
              // Per-blocker mapping: each blocker's requirements are
              // re-checked ONLY against its own attached files.
              const fixMap = {};
              for (const b of fixBlockers) {
                const names = ((decisions[b.blocker_id] || {}).files || []).map((f) => f.name);
                if (names.length) fixMap[b.blocker_id] = names;
              }
              onResume({ files: allFixFiles, zipFile: extraZip, waive: waiveIds, fixMap });
            }}
          >
            {busy ? "Resume running in background… watch the tracker below" : `Resume pipeline with fixes (${allFixFiles.length} file(s)${extraZip ? " + zip" : ""})`}
          </button>
          {statusNote && <div className="mt-2 font-mono text-xs text-cyan-200/90">{statusNote}</div>}
        </div>
      )}
    </div>
  );
}
