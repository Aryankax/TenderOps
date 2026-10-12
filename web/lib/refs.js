// Client-side mirror of backend requirement_refs (for old saved JSONs
// whose prose was never expanded). Idempotent: [bracketed] IDs are immune.

const ID_RE = /(?<!\[)\b(?:REQ-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*|R\d+(?:-\d+)?|ISSUE-\d+|BLK-\d+)\b(?!\])/g;

function clip(text, limit = 100) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (clean.length <= limit) return clean;
  const cut = clean.lastIndexOf(" ", limit);
  return clean.slice(0, cut > limit / 2 ? cut : limit).replace(/[ .,:;]+$/, "") + "…";
}

export function buildRefMap(requirements = [], issues = []) {
  const map = {};
  for (const req of requirements) {
    const rid = String(req.requirement_id || "").trim();
    if (!rid) continue;
    const title = String(req.title || "").trim() || rid;
    const desc = clip(req.normalized_description || "");
    map[rid] =
      desc && desc.toLowerCase() !== title.toLowerCase()
        ? `"${title}" [${rid}] — ${desc}`
        : `"${title}" [${rid}]`;
  }
  for (const issue of issues || []) {
    const iid = String(issue.issue_id || "").trim();
    if (!iid || map[iid]) continue;
    const label = String(issue.issue_type || "issue").replace(/_/g, " ").toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
    const desc = clip(issue.description || "", 80);
    map[iid] = `"${label}" [${iid}]${desc ? " — " + desc : ""}`;
  }
  return map;
}

export function say(text, refMap) {
  if (!text || !refMap) return text || "—";
  return String(text).replace(ID_RE, (m) => refMap[m] || m);
}
