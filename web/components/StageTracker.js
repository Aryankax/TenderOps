"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { subscribeEvents } from "../lib/api";
import { eventMatchesTender, eventStore } from "../lib/eventStore";

const STAGES = [
  { id: "P1", label: "Tender Analyst", nodes: new Set([
    "extract_metadata", "extract_requirements", "detect_issues",
    "assemble_final", "orchestrator_p1", "processing_chunk",
  ])},
  { id: "P2", label: "Evidence Agent", nodes: new Set([
    "retrieve_evidence", "retrieve_scoped", "retrieve_scoped_fallback",
    "map_evidence", "orchestrator_p2",
  ])},
  { id: "P3", label: "Compliance", nodes: new Set([
    "compliance_check", "assess_blockers", "pause_checkpoint",
    "finalize_result", "orchestrator_p3", "pipeline_paused",
    "resume_index_appended", "resume_blocker_recheck", "resume_blocker_skipped",
    "resume_fix_unmatched", "blockers_waived", "blockers_waive_unmatched",
  ])},
  { id: "P4", label: "Bid prep + Verify", nodes: new Set([
    "draft_responses", "orchestrator_p4_draft", "verify_package",
    "orchestrator_p4_verify", "pipeline_completed",
    "resume_p4_started", "resume_p4_completed", "resume_p4_failed",
    "bidprep_started", "bidprep_completed", "verification_started",
    "verification_completed",
  ])},
];

const TOOL_EVENTS = new Set([
  "processing_chunk", "rate_limit_backoff", "provider_fallback",
  "structured_output_failed", "mapping_failed", "draft_failed",
  "retrieval_failed", "evidence_index_built", "checkpoint_saved",
  "job_completed", "job_failed", "linkcrawl_child_scan_skipped",
  "resume_index_appended", "resume_blocker_recheck", "resume_blocker_skipped",
  "resume_fix_unmatched", "retrieve_scoped", "retrieve_scoped_fallback",
  "evidence_mapping_started", "blockers_waived", "blockers_waive_unmatched",
  "resume_p4_started", "resume_p4_completed", "resume_p4_failed",
  "bidprep_started", "bidprep_completed", "verification_started",
  "verification_completed",
]);

function stageOf(event) {
  const node = event.data?.node || event.name || "";
  for (const stage of STAGES) {
    if (stage.nodes.has(node) || stage.nodes.has(event.name)) return stage.id;
  }
  return null;
}

function shortEvent(event) {
  const d = event.data || {};
  if (event.name === "resume_blocker_recheck") {
    const outcomes = d.outcomes ? Object.entries(d.outcomes).map(([r, s]) => `${r}:${s}`).join(", ") : "";
    return { node: `↻ ${d.blocker_id} ${d.phase || ""}`, extra: `${(d.files || []).join(", ")} → ${outcomes}`, kind: event.kind };
  }
  if (event.name === "resume_index_appended") {
    return { node: "fix docs indexed", extra: `${d.documents || 0} doc(s), ${d.chunks || 0} chunks`, kind: event.kind };
  }
  if (event.name === "retrieve_scoped") {
    return { node: "scoped re-check", extra: (d.documents || []).join(", "), kind: event.kind };
  }
  if (event.name === "retrieve_scoped_fallback") {
    return { node: "↻ reading full fix file", extra: `${d.requirement_id || ""} ← ${(d.documents || []).join(", ")} (${d.fallback_chunks || 0} chunks)`, kind: event.kind };
  }
  if (event.name === "blockers_waived") {
    return { node: "waivers applied", extra: (d.blocker_ids || []).join(", "), kind: event.kind };
  }
  if (event.name === "resume_p4_started") {
    return { node: "▶ drafting bid package", extra: `${d.draftable || 0} draftable`, kind: event.kind };
  }
  if (event.name === "resume_p4_completed") {
    return { node: "✓ bid package verified", extra: d.passed ? "PASSED" : "FAILED", kind: event.kind };
  }
  const node = d.node || event.name;
  const extra =
    d.chunk_index !== undefined ? `chunk ${d.chunk_index}` :
    d.method ? `${d.method}${d.attempt ? ` #${d.attempt}` : ""}` :
    d.provider ? `→ ${d.provider}` :
    d.requirement_id ? d.requirement_id :
    d.requirements ? `${d.requirements} req(s)` :
    d.blockers !== undefined ? `${d.blockers} blockers` :
    d.decision ? d.decision : "";
  return { node, extra, kind: event.kind };
}

export default function StageTracker({ tenderId, active, autoOpen = true, sessionKey = "default" }) {
  const boxRef = useRef(null);
  // Restore the shared buffer on remount (tab switch) instead of wiping it.
  const [events, setEvents] = useState(() => {
    eventStore.ensure(sessionKey);
    return [...eventStore.events];
  });

  useEffect(() => {
    if (!active) return;
    eventStore.ensure(sessionKey);
    setEvents([...eventStore.events]);
    const close = subscribeEvents({
      tenderId,
      after: eventStore.lastSeq,
      onEvent: (event) => {
        if (eventStore.append(event)) setEvents([...eventStore.events]);
      },
      onError: () => {
        // EventSource retries on its own; idle streams close by design
        // (~10s) and resubscribe with ?after=<seq> — resume is lossless.
      },
    });
    return close;
  }, [active, tenderId, sessionKey]);

  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current?.scrollHeight });
  }, [events.length]);

  const visible = useMemo(
    () => events.filter((e) => eventMatchesTender(e, tenderId)),
    [events, tenderId]
  );
  const { doneStages, liveStage } = useMemo(() => {
    const done = new Set();
    let live = null;
    for (const event of visible) {
      const stage = stageOf(event);
      if (stage) {
        live = stage;
        if (
          event.kind === "event" &&
          /success|completed|paused/i.test(event.name + JSON.stringify(event.data || {}))
        ) {
          done.add(stage);
        }
      }
    }
    return { doneStages: done, liveStage: live };
  }, [visible]);

  const toolEvents = useMemo(() => visible.filter((e) => TOOL_EVENTS.has(e.data?.node || e.name)), [visible]);

  if (!active && visible.length === 0) return null;

  return (
    <div className="card">
      <div className="mb-4 flex items-center gap-2">
        {STAGES.map((s, i) => {
          const done = doneStages.has(s.id);
          const live = liveStage === s.id && !done;
          return (
            <div key={s.id} className="flex flex-1 items-center gap-2 last:flex-none">
              <div
                className={`flex h-9 w-9 items-center justify-center rounded-full border text-xs font-bold ${
                  done
                    ? "border-emerald-500 bg-emerald-500/20 text-emerald-300"
                    : live
                    ? "stage-active border-emerald-400 bg-emerald-400/10 text-emerald-200"
                    : "border-white/15 bg-white/5 text-zinc-500"
                }`}
              >
                {done ? "✓" : s.id}
              </div>
              <div className="hidden text-xs text-zinc-400 md:block">{s.label}</div>
              {i < STAGES.length - 1 && <div className="h-px flex-1 bg-white/10" />}
            </div>
          );
        })}
      </div>
      <div ref={boxRef} className="scroll-slim max-h-44 space-y-1 overflow-y-auto font-mono text-[11px]">
        {toolEvents.length === 0 && (
          <div className="text-zinc-500">Waiting for agent activity…</div>
        )}
        {toolEvents.map((e, i) => {
          const s = shortEvent(e);
          const bad = e.kind === "error";
          return (
            <div key={e.seq || i} className={bad ? "text-red-300" : "text-zinc-400"}>
              <span className="text-zinc-600">
                {new Date((e.ts || 0) * 1000).toLocaleTimeString()}
              </span>{" "}
              {bad ? "✗" : "•"} {s.node}{" "}
              {s.extra && <span className="text-zinc-500">{s.extra}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
