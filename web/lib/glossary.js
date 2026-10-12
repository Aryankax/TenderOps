// Plain-language meanings for every badge/status in the UI.
// Wire via title={...} so hovering any pill explains what it means
// and what its values mean.

const TIPS = {
  requirement_id: "Stable code for one extracted requirement. Evidence, compliance and blockers all reference it.",
  semantic_BIDDER_OBLIGATION: "Something the bidder must do or submit.",
  semantic_ELIGIBILITY_CONDITION: "A qualifying criterion (turnover, experience…) tested against company figures.",
  semantic_EXEMPTION: "Relaxation or waiver for some bidders (MSE/startup) — relief, not an obligation.",
  semantic_BUYER_DISCRETION: "The buyer's right (may extend, may relax). No bidder action needed.",
  semantic_PROHIBITION: "Something the bidder must NOT do. A violation rejects the bid.",
  semantic_RESTRICTION: "A limit on how the bidder may act.",
  semantic_CONTRACTUAL_TERM: "A contract condition (warranty, penalty, payment) binding after award.",
  semantic_INFORMATIONAL: "Reference info only — dates, refs and estimates live here, never as requirements.",
  mandatory_true: "Must be met. A gap here blocks the bid.",
  mandatory_false: "Optional. Gaps here never block.",
  mandatory_null: "Conditional — applies only when the stated condition holds.",
  evidence_satisfied: "Company documents prove this requirement.",
  evidence_partial: "Some evidence found, but gaps remain.",
  evidence_missing: "No supporting evidence found in the company index.",
  evidence_needs_review: "Ambiguous match — a human must decide.",
  compliance_COMPLIANT: "Evidence proves the requirement. Nothing to do.",
  compliance_NON_COMPLIANT: "Proven gap — raises a blocker.",
  compliance_PENDING: "Waiting on documents or info. Resumable, never a silent pass.",
  compliance_NEEDS_REVIEW: "Ambiguous — a human must decide. Escalates, never assumes.",
  confidence: "0–1: how certain the mapping is. Low scores mean weak or indirect evidence — inspect the hits.",
  needs_review: "Extraction or mapping was uncertain here. Verify this item before relying on it.",
  threshold: "The measurable floor/ceiling from the tender (e.g. at least 5 cr turnover). Company figures are tested against it.",
  required_evidence: "Documents the bidder must attach to prove this requirement.",
  blocker_MISSING_DOCUMENT: "A mandatory requirement has no supporting evidence. Upload the document, then resume.",
  blocker_FAILED_CHECK: "The company figure does not meet the threshold. Supply stronger evidence or concede.",
  blocker_EXPIRED_DOCUMENT: "The supporting document expired. Upload a renewed one, then resume.",
  blocker_CONTRADICTION: "Tender clauses contradict each other. Reconcile them before proceeding.",
  blocker_UNRESOLVED_REVIEW: "A human-review flag is still open on a mandatory requirement.",
  blocker_INCOMPLETE_EVIDENCE: "The evidence set is incomplete — see the notes, then resume.",
  severity_high: "Blocks submission until resolved or explicitly waived.",
  severity_medium: "Should be resolved, but can be waived with a recorded override.",
  verification_passed: "An independent verifier (not the drafter) confirmed the bid package.",
  verification_failed: "Independent verification found blocking problems — see findings.",
  severity_blocking: "Must be fixed before the bid goes out.",
  severity_warning: "Advisory — review, but it does not fail verification.",
  job_QUEUED: "Accepted and waiting for a worker thread.",
  job_RUNNING: "Worker is executing the pipeline right now.",
  job_DONE: "Worker finished. The RESULT below says whether the pipeline completed or paused on blockers.",
  job_FAILED: "Worker failed. Read the error, fix the cause, retry.",
  job_vs_pipeline: "Job state is the background worker (queued/running/done). Pipeline outcome is the bid result (completed/paused). A DONE job can carry a PAUSED pipeline — that is normal.",
  pipeline_PAUSED_ON_BLOCKERS: "Stopped safely on blockers. Upload fixes or waive, then resume.",
  pipeline_COMPLETED: "Ran to the end, including drafting and verification.",
  unmapped: "Requirements with no usable evidence (missing or needs review).",
  waived: "Human override recorded on the checkpoint. Auditable — never silent.",
};

export function tip(key) {
  return TIPS[key] || "";
}

export function semanticTip(value) {
  return tip(`semantic_${value}`) || `Clause kind: ${value}`;
}

export function evidenceTip(status) {
  return tip(`evidence_${status}`) || `Evidence: ${status}`;
}

export function complianceTip(status) {
  return tip(`compliance_${status}`) || `Compliance: ${status}`;
}

export function blockerTip(type) {
  return tip(`blocker_${type}`) || `Blocker: ${type}`;
}

export function mandatoryTip(value) {
  return tip(`mandatory_${String(value)}`);
}
