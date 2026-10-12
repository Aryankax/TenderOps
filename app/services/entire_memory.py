"""Entire Continuous Memory for TenderOps (offline-first).

Bridges TenderOps pipeline runs to Entire concepts:

- **Checkpoints**: every supervised run records request + agent decisions +
  changed files + validation, linked to the git commit (Entire links commits
  to session context the same way when the CLI is installed).
- **Agentic search**: keyword-overlap search over past checkpoints, with a
  passthrough to `entire search` when the CLI exists.
- **Entire Graph**: `EntireGraph` shells out to `entire-graph search` when
  available, else falls back to AST + text search over `app/`.
- **Entire Brain**: durable facts in `.entire/brain/facts.md` + session
  carryover file, so context survives across agents.

All helpers are best-effort and never raise for missing binaries: the local
store in `settings.ENTIRE_MEMORY_DIR` is the source of truth.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from app.core.config import settings

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "by", "as", "at", "from", "that",
        "this", "it", "its", "into", "over", "under", "tender", "bid",
        "requirement", "compliance", "evidence",
        # Generic build verbs carry no review signal on their own.
        "build", "wire", "wiring", "implement", "create", "make", "update",
        "fix", "add", "adding", "refactor", "change", "using", "use",
        # Project name, not a requirement.
        "tenderops",
    ]
)


def _mem_dir() -> Path:
    d = Path(getattr(settings, "ENTIRE_MEMORY_DIR", "./data/entire_memory"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _store_path() -> Path:
    return _mem_dir() / "checkpoints.json"


def _snap_dir() -> Path:
    d = _mem_dir() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_all() -> list[dict]:
    p = _store_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_all(items: list[dict]) -> None:
    _store_path().write_text(json.dumps(items, indent=1), encoding="utf-8")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 2}


def git_info() -> dict[str, str]:
    """Best-effort git context (empty strings when git is missing)."""

    def _run(args: list[str]) -> str:
        try:
            out = subprocess.run(
                ["git", *args], capture_output=True, text=True, timeout=5,
                check=False,
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:
            return ""

    return {
        "git_commit": _run(["rev-parse", "HEAD"]),
        "branch": _run(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def cli_status() -> dict:
    """Which Entire binaries are installed (CLI / graph / brain)."""
    return {
        "entire_cli": shutil.which("entire") is not None,
        "entire_graph": shutil.which("entire-graph") is not None,
        "entire_brain": shutil.which("entire-brain") is not None,
        "store": str(_store_path()),
        "checkpoints": len(_load_all()),
    }


def entire_url_for(checkpoint_id: str, git_commit: str = "") -> str:
    """Shareable Entire checkpoint URL template for the submission checklist."""
    owner_repo = os.environ.get("ENTIRE_OWNER_REPO", "OWNER/REPO")
    sha = git_commit or "FULL_COMMIT_SHA"
    return f"https://entire.io/gh/{owner_repo}/commit/{sha}#{checkpoint_id}"


def record_checkpoint(
    tender_id: str,
    request: str = "",
    decisions: list[str] | None = None,
    changed_files: list[str] | None = None,
    validation: dict | None = None,
    run_id: str = "",
    git_commit: str = "",
    branch: str = "",
) -> dict:
    """Persist one Entire-style checkpoint for a tender run."""
    items = _load_all()
    info = git_info()
    ckpt_id = f"ckpt_{uuid.uuid4().hex[:12]}"
    now = time.time()
    commit = git_commit or info["git_commit"]
    entry = {
        "id": ckpt_id,
        "tender_id": tender_id,
        "run_id": run_id or f"run-{ckpt_id[5:]}",
        "request": request,
        "decisions": list(decisions or []),
        "changed_files": list(changed_files or []),
        "validation": dict(validation or {}),
        "git_commit": commit,
        "branch": branch or info["branch"],
        "created_at": now,
        "entire_url": entire_url_for(ckpt_id, commit),
    }
    items.append(entry)
    _save_all(items)
    return entry


def list_checkpoints(tender_id: str | None = None, limit: int = 50) -> list[dict]:
    items = _load_all()
    if tender_id:
        items = [c for c in items if c.get("tender_id") == tender_id]
    items.sort(key=lambda c: c.get("created_at", 0), reverse=True)
    return items[: max(1, limit)]


def get_checkpoint(checkpoint_id: str) -> dict | None:
    return next((c for c in _load_all() if c.get("id") == checkpoint_id), None)


def search_checkpoints(query: str, top_k: int = 5) -> list[dict]:
    """Keyword-overlap search over past checkpoints (offline agentic search).

    Falls back to literal substring matches when there is no token overlap.
    If the `entire` CLI exists, its output is attached as `entire_cli_hint`
    on the top hit (informational only, never required).
    """
    items = _load_all()
    if not items:
        return []
    qtok = _tokens(query)
    scored: list[tuple[float, dict]] = []
    for c in items:
        hay = " ".join(
            [c.get("tender_id", ""), c.get("request", ""), *c.get("decisions", [])]
        )
        overlap = len(qtok & _tokens(hay))
        score = overlap / max(1, len(qtok))
        if not qtok or query.lower() in hay.lower():
            score = max(score, 0.5)
        if score > 0:
            scored.append((score, c))
    scored.sort(key=lambda s: (s[0], s[1].get("created_at", 0)), reverse=True)
    out = [dict(c, search_score=round(s, 3)) for s, c in scored[: max(1, top_k)]]
    hint = _entire_cli_search(query)
    if hint and out:
        out[0] = {**out[0], "entire_cli_hint": hint[:500]}
    return out


def _entire_cli_search(query: str) -> str:
    if shutil.which("entire") is None:
        return ""
    try:
        out = subprocess.run(
            ["entire", "search", query, "--limit", "3"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- Graph ---
class EntireGraph:
    """Code map: Entire Graph when installed, local AST fallback otherwise."""

    repo_root: Path = Path(".")

    @classmethod
    def locate(cls, query: str, top_k: int = 5) -> list[dict]:
        """Where is `query` implemented? Graph first, local fallback second."""
        if shutil.which("entire-graph") is not None:
            try:
                out = subprocess.run(
                    ["entire-graph", "search", query, "--limit", str(top_k)],
                    capture_output=True, text=True, timeout=15,
                    cwd=str(cls.repo_root), check=False,
                )
                if out.returncode == 0 and out.stdout.strip():
                    return [
                        {"source": "entire-graph", "hit": line.strip()}
                        for line in out.stdout.strip().splitlines()[:top_k]
                        if line.strip()
                    ]
            except Exception:  # noqa: S110 — graph optional, fallback below
                pass
        return [
            {**hit, "source": "local-fallback"}
            for hit in _local_locate(query, top_k, cls.repo_root)
        ]

    @classmethod
    def impact(cls, file_path: str) -> dict:
        """What depends on `file_path`? (reverse edges of the import graph)."""
        snap = graph_snapshot(cls.repo_root)
        dependents = sorted(
            {f for f, imports in snap["imports"].items() if file_path in imports}
        )
        return {"file": file_path, "dependents": dependents,
                "count": len(dependents)}

    @classmethod
    def stats(cls) -> dict:
        """Token-saving estimate in the spirit of `entire graph stats`."""
        n_files = sum(1 for _ in Path(cls.repo_root, "app").rglob("*.py"))
        return {
            "indexed_files": n_files,
            "graph_available": shutil.which("entire-graph") is not None,
            "note": "Graph answers point at exact files instead of full-tree greps; "
            "the hack-page benchmark reports up to 71% token savings.",
        }


def _local_locate(query: str, top_k: int, repo_root: Path) -> list[dict]:
    hits = _local_locate_phrase(query, top_k, repo_root)
    if hits:
        return hits
    # Phrase matched nothing: fall back to per-term search so a long
    # briefing query still surfaces the most relevant files.
    seen: dict[tuple[str, int], dict] = {}
    for term in sorted(_tokens(query)):
        for h in _local_locate_phrase(term, top_k, repo_root):
            seen.setdefault((h["file"], h["line"]), h)
            if len(seen) >= top_k:
                return list(seen.values())[:top_k]
    return list(seen.values())[:top_k]


def _local_locate_phrase(query: str, top_k: int, repo_root: Path) -> list[dict]:
    q = query.lower()
    hits: list[dict] = []
    for path in sorted(Path(repo_root, "app").rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: S112 — unreadable file, skip it
            continue
        if q in path.as_posix().lower():
            hits.append({"file": path.as_posix(), "line": 1,
                         "preview": f"path match: {path.name}"})
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if q in line.lower():
                hits.append({"file": path.as_posix(), "line": i,
                             "preview": line.strip()[:200]})
                break
        if len(hits) >= top_k * 3:
            break
    # Prefer symbol definitions (def/class) mentioning the query.
    def _rank(h: dict) -> int:
        return 0 if re.search(r"(def |class ).*", h["preview"]) else 1

    hits.sort(key=_rank)
    return hits[:top_k]


def graph_snapshot(repo_root: Path | str = ".") -> dict:
    """File -> local-imports map + content hashes (for drift detection)."""
    root = Path(repo_root)
    imports: dict[str, list[str]] = {}
    hashes: dict[str, str] = {}
    for path in sorted(Path(root, "app").rglob("*.py")):
        rel = path.as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: S112 — unreadable file, skip it
            continue
        hashes[rel] = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        try:
            tree = ast.parse(text)
        except SyntaxError:
            imports[rel] = []
            continue
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        local = sorted(
            m for m in mods if m.startswith("app.")
        )
        imports[rel] = [m.replace(".", "/") + ".py" for m in local]
    return {"imports": imports, "hashes": hashes,
            "files": len(imports), "created_at": time.time()}


def save_snapshot(label: str, repo_root: Path | str = ".") -> dict:
    snap = graph_snapshot(repo_root)
    path = _snap_dir() / f"{label}.json"
    path.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return {"label": label, "path": str(path), **snap}


def graph_diff(before: dict, after: dict) -> dict:
    """Compare two Graph snapshots: new deps, removed deps, changed files."""
    b_imp, a_imp = before.get("imports", {}), after.get("imports", {})
    b_h, a_h = before.get("hashes", {}), after.get("hashes", {})
    new_deps, removed_deps = [], []
    for f, deps in a_imp.items():
        for d in deps:
            if d not in b_imp.get(f, []):
                new_deps.append({"file": f, "added_import": d})
    for f, deps in b_imp.items():
        for d in deps:
            if d not in a_imp.get(f, []):
                removed_deps.append({"file": f, "removed_import": d})
    changed = sorted({f for f in a_h if b_h.get(f) != a_h.get(f)})
    return {"new_dependencies": new_deps, "removed_dependencies": removed_deps,
            "changed_files": changed,
            "coupling_delta": len(new_deps) - len(removed_deps)}


# ---------------------------------------------------------------- Brain ---
class EntireBrain:
    """Durable facts + session carryover (repo-local Brain fallback)."""

    @staticmethod
    def facts_path() -> Path:
        return Path(".entire/brain/facts.md")

    @classmethod
    def read_facts(cls) -> str:
        p = cls.facts_path()
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    @classmethod
    def append_fact(cls, fact: str) -> str:
        p = cls.facts_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = f"- {fact.strip()}\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
        return line


# ------------------------------------------------------- Showcase tools ---
def build_briefing(query: str, top_k: int = 3) -> dict:
    """'Before you build' assistant: past attempts + reusable code."""
    past = search_checkpoints(query, top_k=top_k)
    code = EntireGraph.locate(query, top_k=top_k)
    rejected = [
        d for c in past for d in c.get("decisions", [])
        if re.search(r"reject|avoid|don.?t|skip|fail", d, re.IGNORECASE)
    ][:top_k]
    lines = [f"# Build briefing: {query}", ""]
    lines.append(f"Past attempts found: {len(past)}.")
    for c in past:
        lines.append(
            f"- {c['id']} tender={c.get('tender_id')} "
            f"score={c.get('search_score')} request={c.get('request','')[:120]}"
        )
    if rejected:
        lines.append("")
        lines.append("Rejected approaches to avoid repeating:")
        lines.extend(f"- {r[:200]}" for r in rejected)
    lines.append("")
    lines.append("Reusable code (Graph):")
    for h in code:
        if "file" in h:
            lines.append(
                f"- {h['file']}:{h.get('line', 1)} {h.get('preview','')[:120]}"
            )
        else:
            lines.append(f"- {h.get('hit','')[:160]}")
    lines.append("")
    lines.append("Suggested first step: reuse the smallest match above; "
                 "record a checkpoint after the run.")
    return {"query": query, "past_attempts": past, "reusable_code": code,
            "rejected_approaches": rejected, "briefing_md": "\n".join(lines)}


def review_change(request: str, changed_files: list[str],
                  decisions: list[str], checkpoint_id: str = "") -> dict:
    """Reviewer that checks intent: request vs. committed change."""
    flags: list[str] = []
    req_tok = _tokens(request)
    change_tok = _tokens(" ".join([*changed_files, *decisions]))

    def _covered(term: str) -> bool:
        return any(c.startswith(term) or term.startswith(c)
                   for c in change_tok)

    missed = sorted(t for t in req_tok if not _covered(t))
    if missed:
        flags.append(f"requirements with no evidence in change: {', '.join(missed[:8])}")
    if changed_files and not decisions:
        flags.append("change has files but no recorded decisions — link the agent transcript.")
    has_test = any("test" in f.lower() for f in changed_files)
    has_test = has_test or any("test" in c for c in change_tok)
    if changed_files and not has_test:
        flags.append("no test evidence referenced — add or cite tests.")
    past = search_checkpoints(request, top_k=3) if request else []
    verdict = "PASS" if not flags else "NEEDS_ATTENTION"
    return {"verdict": verdict, "flags": flags,
            "missed_terms": missed[:12],
            "changed_files": changed_files,
            "checkpoint_id": checkpoint_id,
            "related_checkpoints": [c["id"] for c in past]}


def checkpoint_demo(checkpoint_id: str) -> dict:
    """Checkpoint-to-demo generator: problem, decisions, change, validation."""
    c = get_checkpoint(checkpoint_id)
    if c is None:
        raise KeyError(f"Unknown checkpoint {checkpoint_id}.")
    md = [
        f"# Demo: {c.get('tender_id')} ({c['id']})", "",
        f"Problem: {c.get('request') or 'Tender run recorded as Entire checkpoint.'}",
        "",
        "Key decisions:",
        *([f"- {d}" for d in c.get("decisions", [])] or ["- (no decisions recorded)"]),
        "",
        "Implementation:",
        *([f"- `{f}`" for f in c.get("changed_files", [])] or ["- (no files recorded)"]),
        "",
        f"Validation: `{json.dumps(c.get('validation', {}))[:400]}`",
        "",
        f"Evidence: Entire checkpoint `{c['id']}` commit `{c.get('git_commit','?')[:12]}`",
        f"Link: {c.get('entire_url','')}",
    ]
    return {"checkpoint": c, "demo_md": "\n".join(md)}


def scout_tests(checkpoint_id: str = "", changed_files: list[str] | None = None) -> dict:
    """Regression test scout: behaviors needing tests from change + Graph."""
    files = list(changed_files or [])
    c = get_checkpoint(checkpoint_id) if checkpoint_id else None
    if c is not None:
        files.extend(f for f in c.get("changed_files", []) if f not in files)
    suggestions: list[dict] = []
    for f in files[:10]:
        impact = EntireGraph.impact(f)
        suggestions.append({
            "file": f,
            "suggested_test": f"tests/test_entire_memory.py-style regression for {Path(f).stem}",
            "dependents": impact["dependents"][:5],
            "rationale": f"{impact['count']} dependent(s) — change ripples here.",
        })
    if c is not None:
        for d in c.get("decisions", [])[:5]:
            if re.search(r"edge|uncertain|assum|review", d, re.IGNORECASE):
                suggestions.append({
                    "file": "(discussed edge case)",
                    "suggested_test": d[:160],
                    "dependents": [],
                    "rationale": "Agent discussed but may not have covered it.",
                })
    return {"checkpoint_id": checkpoint_id, "suggestions": suggestions}
