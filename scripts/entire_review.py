"""Reviewer that checks intent: request vs. committed change."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    from app.services import entire_memory as mem

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--request", default="", help="Original request text")
    p.add_argument("--files", nargs="*", default=[], help="Changed files")
    p.add_argument("--decisions", nargs="*", default=[], help="Recorded decisions")
    p.add_argument("--checkpoint", default="", help="Checkpoint id for context")
    p.add_argument("--latest", action="store_true",
                   help="Review the most recent checkpoint")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    request, files, decisions = args.request, args.files, args.decisions
    if args.latest:
        latest = mem.list_checkpoints(limit=1)
        if not latest:
            print(json.dumps({"verdict": "NO_CHECKPOINTS"}) if args.json
                  else "No checkpoints recorded yet.")
            return
        c = latest[0]
        request = request or c.get("request", "")
        files = files or c.get("changed_files", [])
        decisions = decisions or c.get("decisions", [])
        args.checkpoint = args.checkpoint or c["id"]
    out = mem.review_change(request, files, decisions, args.checkpoint)
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(f"Verdict: {out['verdict']}")
        for flag in out["flags"]:
            print(f"- {flag}")
        if out["related_checkpoints"]:
            print("Related:", ", ".join(out["related_checkpoints"]))


if __name__ == "__main__":
    main()
