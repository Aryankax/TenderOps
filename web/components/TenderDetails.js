"use client";

// Full tender identity card: every basic detail before the requirements.
// Metadata-only (dates, refs, estimates live here — never as requirements).
const FIELDS = [
  ["tender_title", "Tender title"],
  ["tender_reference", "Reference"],
  ["issuing_authority", "Issuing authority"],
  ["submission_deadline", "Submission deadline"],
  ["bid_opening_date", "Bid opening"],
  ["pre_bid_meeting_date", "Pre-bid meeting"],
  ["clarification_deadline", "Clarification deadline"],
  ["emd_amount", "EMD amount"],
  ["tender_fee", "Tender fee"],
  ["currency", "Currency"],
  ["contract_duration", "Contract duration"],
  ["delivery_deadline", "Delivery deadline"],
];

export default function TenderDetails({ metadata = {}, tenderId }) {
  const rows = FIELDS.filter(([k]) => metadata[k]);
  const extras = Object.entries(metadata.other_metadata || {}).filter(
    ([, v]) => v !== null && v !== "" && v !== undefined
  );
  return (
    <div className="card">
      <h3 className="mb-1 font-semibold">Tender details</h3>
      {tenderId && <div className="mb-3"><code className="badge">{tenderId}</code></div>}
      {rows.length === 0 && extras.length === 0 && (
        <div className="text-sm text-zinc-500">No tender details extracted.</div>
      )}
      <dl className="grid gap-x-6 gap-y-2 md:grid-cols-2">
        {rows.map(([k, label]) => (
          <div key={k} className="flex flex-col border-b border-white/5 pb-1.5">
            <dt className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</dt>
            <dd className="text-sm text-zinc-100">{String(metadata[k])}</dd>
          </div>
        ))}
      </dl>
      {extras.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-sm text-zinc-400">
            More details ({extras.length})
          </summary>
          <dl className="mt-2 grid gap-x-6 gap-y-2 md:grid-cols-2">
            {extras.map(([k, v]) => (
              <div key={k} className="flex flex-col border-b border-white/5 pb-1.5">
                <dt className="text-[11px] uppercase tracking-wide text-zinc-500">
                  {k.replace(/_/g, " ")}
                </dt>
                <dd className="max-w-full overflow-hidden text-ellipsis text-sm text-zinc-300">
                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  );
}
