"""Regression test scout: behaviors needing tests from a checkpoint."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    from app.services import entire_memory as mem

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", default="")
    p.add_argument("--latest", action="store_true")
    p.add_argument("--files", nargs="*", default=[])
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    ckpt_id = args.checkpoint
    if args.latest and not ckpt_id:
        items = mem.list_checkpoints(limit=1)
        ckpt_id = items[0]["id"] if items else ""
    out = mem.scout_tests(ckpt_id, args.files or None)
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        if not out["suggestions"]:
            print("No suggestions — record a checkpoint with changed files first.")
        for s in out["suggestions"]:
            print(f"- {s['file']}: {s['suggested_test'][:140]}")
            print(f"  why: {s['rationale'][:160]}")


if __name__ == "__main__":
    main()
