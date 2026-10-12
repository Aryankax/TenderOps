"""Before-you-build assistant: past attempts + reusable code briefing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    from app.services import entire_memory as mem

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query", required=True, help="What you plan to build")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    out = mem.build_briefing(args.query, top_k=args.top_k)
    if args.json:
        print(json.dumps(out, indent=1, default=str))
    else:
        print(out["briefing_md"])


if __name__ == "__main__":
    main()
