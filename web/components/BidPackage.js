"use client";

import { useState } from "react";
import { tip } from "../lib/glossary";
import { say } from "../lib/refs";

// Full bid-package inspector: every draft (body + evidence), the submission
// checklist, the risk report, and the independent verification findings.
export default function BidPackage({ pkg, verification, refMap }) {
  const [openDraft, setOpenDraft] = useState(null);
  if (!pkg && !verification) return null;
  const drafts = pkg?.drafts || [];
  const checklist = pkg?.checklist || [];
  const risks = pkg?.risk_report || [];
  const findings = verification?.findings || [];

  return (
    <div className="card">
      <div className="mb-1 flex flex-wrap items-center gap-3">
        <h3 className="font-semibold">Bid package</h3>
        {verification && (
          <span className={`badge ${verification.passed ? "border-emerald-500/50 text-emerald-300" : "border-red-500/50 text-red-300"}`}
            title={verification.passed ? tip("verification_passed") : tip("verification_failed")}>
            verification {verification.passed ? "✅ PASSED" : "❌ FAILED"}
          </span>
        )}
        <span className="text-xs text-zinc-500">
          {drafts.length} drafts · {checklist.length} checklist · {risks.length} risks · {findings.length} findings
        </span>
      </div>
      <p className="mb-3 text-xs text-zinc-500">
        Drafted only for compliant requirements, each grounded in cited company evidence,
        then checked by an independent verifier — not the drafter.
      </p>

      {drafts.length > 0 && (
        <div className="mb-4">
          <h4 className="mb-2 text-sm font-semibold text-zinc-300">Response drafts ({drafts.length})</h4>
          <div className="space-y-2">
            {drafts.map((d) => {
              const open = openDraft === d.draft_id;
              return (
                <div key={d.draft_id} className="rounded-xl border border-white/10 bg-white/5">
                  <button onClick={() => setOpenDraft(open ? null : d.draft_id)} className="w-full p-3 text-left">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="badge">{d.draft_id}</code>
                      {d.requirement_id && <code className="badge">{d.requirement_id}</code>}
                      <span className="badge">{d.kind}</span>
                      {d.needs_human_signoff && <span className="badge border-amber-400/50 text-amber-200" title="The drafter flagged this for a human signature before submission.">⚠️ needs signoff</span>}
                    </div>
                    <div className="mt-1 text-sm font-medium">{d.title}</div>
                  </button>
                  {open && (
                    <div className="space-y-2 border-t border-white/10 p-3 text-sm">
                      <p className="whitespace-pre-wrap text-zinc-200">{d.body}</p>
                      {(d.claimed_value != null) && (
                        <div className="text-xs text-cyan-200">
                          Claimed figure: {d.claimed_value} {d.claimed_unit || ""}
                        </div>
                      )}
                      {(d.evidence_refs || []).length > 0 && (
                        <div className="text-xs text-zinc-400">
                          <span className="text-zinc-500">Grounded in: </span>
                          {(d.evidence_refs || []).map((r, i) => (
                            <span key={i} className="mr-2 font-mono">📄 {r.document_name} p.{r.page_number}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {checklist.length > 0 && (
        <div className="mb-4">
          <h4 className="mb-2 text-sm font-semibold text-zinc-300">Submission checklist ({checklist.length})</h4>
          <ol className="list-decimal space-y-1 pl-5 text-sm text-zinc-300">
            {checklist.map((c, i) => <li key={i}>{say(String(c), refMap)}</li>)}
          </ol>
        </div>
      )}

      {risks.length > 0 && (
        <div className="mb-4">
          <h4 className="mb-2 text-sm font-semibold text-zinc-300">Risk report ({risks.length})</h4>
          <ul className="space-y-1 text-sm text-amber-200/90">
            {risks.map((r, i) => <li key={i}>⚠️ {say(String(r), refMap)}</li>)}
          </ul>
        </div>
      )}

      {findings.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-semibold text-zinc-300">
            Verification findings ({findings.filter((f) => !f.passed).length} failing / {findings.length})
          </h4>
          <div className="space-y-2">
            {findings.map((f, i) => (
              <div key={i} className={`rounded-xl border p-3 text-sm ${f.passed ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span title={f.passed ? tip("verification_passed") : tip("verification_failed")}>{f.passed ? "✅" : "🛑"}</span>
                  <code className="badge" title="Which independent check ran.">{f.check}</code>
                  {f.requirement_id && <code className="badge" title={tip("requirement_id")}>{f.requirement_id}</code>}
                  {f.draft_id && <code className="badge">{f.draft_id}</code>}
                  <span className="badge" title={f.severity === "blocking" ? tip("severity_blocking") : tip("severity_warning")}>{f.severity}</span>
                </div>
                {f.detail && <p className="mt-1 text-zinc-300">{say(f.detail, refMap)}</p>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
