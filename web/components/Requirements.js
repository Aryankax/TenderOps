"use client";

import { useMemo, useState } from "react";
import { complianceTip, evidenceTip, mandatoryTip, semanticTip, tip } from "../lib/glossary";
import { say } from "../lib/refs";

function Badges({ req, evidence, compliance }) {
  const items = [
    { text: `\`${req.requirement_id}\``, title: tip("requirement_id") },
    { text: req.semantic_type || "?", title: semanticTip(req.semantic_type) },
    { text: `mandatory=${String(req.mandatory)}`, title: mandatoryTip(req.mandatory) },
  ];
  if (evidence) items.push({ text: `evidence:${evidence.status}`, title: evidenceTip(evidence.status) });
  if (compliance) items.push({ text: `compliance:${compliance.status}`, title: complianceTip(compliance.status) });
  if (req.needs_review) items.push({ text: "⚠️ review", title: tip("needs_review") });
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((b, i) => (
        <span key={i} className="badge" title={b.title}>{b.text}</span>
      ))}
    </span>
  );
}

function RequirementCard({ req, evidence, compliance, refMap }) {
  const [open, setOpen] = useState(false);
  const th = req.threshold || {};
  return (
    <div className="rounded-xl border border-white/10 bg-zinc-900/50">
      <button onClick={() => setOpen(!open)} className="w-full p-4 text-left">
        <div className="font-medium">{req.title || "(untitled)"}</div>
        <div className="mt-2"><Badges req={req} evidence={evidence} compliance={compliance} /></div>
        {req.normalized_description && (
          <div className="mt-2 text-sm text-zinc-400">{req.normalized_description}</div>
        )}
      </button>
      {open && (
        <div className="space-y-3 border-t border-white/10 p-4 text-sm">
          <div><span className="text-zinc-500">Original: </span>{(req.original_text || "").slice(0, 800)}</div>
          {(th.value != null || th.original_value) && (
            <div title={tip("threshold")}>
              Threshold: <code className="text-cyan-300">{th.operator} {th.value} {th.unit}</code>{" "}
              <span className="text-zinc-500">(verbatim: <em>{th.original_value}</em>)</span>
            </div>
          )}
          {req.applicability_condition && (
            <div title={mandatoryTip(null)}><span className="text-zinc-500">Condition: </span>{req.applicability_condition}</div>
          )}
          {(req.required_evidence || []).length > 0 && (
            <div title={tip("required_evidence")}><span className="text-zinc-500">Evidence required: </span>{req.required_evidence.join("; ")}</div>
          )}
          {req.review_reason && (
            <div className="rounded-lg bg-amber-500/10 p-2 text-amber-200" title={tip("needs_review")}>
              {say(req.review_reason, refMap)}
            </div>
          )}
          {evidence && (
            <div className="rounded-lg bg-white/5 p-3" title={evidenceTip(evidence.status)}>
              <div>
                Evidence: <code>{evidence.status}</code>{" "}
                <span title={tip("confidence")}>(confidence {evidence.confidence})</span>
              </div>
              {evidence.computed_value != null && (
                <div>Company figure: <code>{evidence.computed_value} {evidence.computed_unit || ""}</code></div>
              )}
              {evidence.notes && <div className="mt-1 text-zinc-300">{say(evidence.notes, refMap)}</div>}
              {(evidence.hits || []).map((h, i) => (
                <details key={i} className="mt-2">
                  <summary className="cursor-pointer text-cyan-300">
                    📄 {h.document_name} p.{h.page_number} (score {h.score})
                  </summary>
                  <div className="mt-1 max-h-40 overflow-y-auto scroll-slim text-xs text-zinc-400">
                    {(h.excerpt || "").slice(0, 1500)}
                  </div>
                </details>
              ))}
            </div>
          )}
          {(req.source_references || []).map((s, i) => (
            <div key={i} className="font-mono text-[11px] text-zinc-500">
              Source: {s.document_name} p.{s.page_number} — {s.section_heading || ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Requirements({ requirements, evidenceById, complianceById, refMap, initialOnlyReview = false, unmappedOnly = false, unmappedIds = [] }) {
  const [q, setQ] = useState("");
  const [onlyReview, setOnlyReview] = useState(initialOnlyReview);
  const [onlyUnmapped, setOnlyUnmapped] = useState(unmappedOnly);
  const [sem, setSem] = useState("(all)");

  const sems = useMemo(
    () => [...new Set(requirements.map((r) => String(r.semantic_type || "?")))].sort(),
    [requirements]
  );
  const filtered = requirements.filter((r) => {
    if (onlyReview && !r.needs_review) return false;
    if (onlyUnmapped && !unmappedIds.includes(r.requirement_id)) return false;
    if (sem !== "(all)" && String(r.semantic_type || "?") !== sem) return false;
    if (q && !`${r.title} ${r.original_text}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <input className="input max-w-xs" placeholder="Search title / text…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="input max-w-[220px]" value={sem} onChange={(e) => setSem(e.target.value)}>
          <option>(all)</option>
          {sems.map((s) => <option key={s}>{s}</option>)}
        </select>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input type="checkbox" checked={onlyReview} onChange={(e) => setOnlyReview(e.target.checked)} />
          Only needs_review
        </label>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input type="checkbox" checked={onlyUnmapped} onChange={(e) => setOnlyUnmapped(e.target.checked)} />
          Only unmapped
        </label>
        <span className="text-xs text-zinc-500">{filtered.length} / {requirements.length}</span>
      </div>
      {filtered.map((r) => (
        <RequirementCard
          key={r.requirement_id}
          req={r}
          evidence={evidenceById[r.requirement_id]}
          compliance={complianceById[r.requirement_id]}
          refMap={refMap}
        />
      ))}
    </div>
  );
}
