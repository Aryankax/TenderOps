"""Checkpoint-to-demo generator: checkpoint -> shareable walkthrough."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    from app.services import entire_memory as mem

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", default="", help="Checkpoint id")
    p.add_argument("--tender-id", default="", help="Use latest for this tender")
    p.add_argument("--latest", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--out", default="", help="Write markdown to file")
    args = p.parse_args()
    ckpt_id = args.checkpoint
    if not ckpt_id and (args.tender_id or args.latest):
        items = mem.list_checkpoints(args.tender_id or None, limit=1)
        if not items:
            print("No checkpoints recorded yet.")
            return
        ckpt_id = items[0]["id"]
    if not ckpt_id:
        p.error("pass --checkpoint, --tender-id, or --latest")
    out = mem.checkpoint_demo(ckpt_id)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out["demo_md"])
        print(f"Wrote {args.out}")
    elif args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(out["demo_md"])


if __name__ == "__main__":
    main()
