"""Architecture drift detector: Graph snapshots across commits."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5,
                             check=False)
        return out.stdout.strip() or "worktree"
    except Exception:
        return "worktree"


def main() -> None:
    from app.services import entire_memory as mem

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--save", default="", help="Save current snapshot as LABEL")
    p.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="Diff two saved snapshot labels")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    if args.save:
        out = mem.save_snapshot(args.save or _head())
        print(json.dumps(out, indent=1, default=str) if args.json
              else f"Saved {out['label']}: {out['files']} files -> {out['path']}")
    elif args.diff:
        from pathlib import Path

        def _load(label: str) -> dict:
            return json.loads(
                (Path(mem._snap_dir()) / f"{label}.json").read_text())

        out = mem.graph_diff(_load(args.diff[0]), _load(args.diff[1]))
        if args.json:
            print(json.dumps(out, indent=1, default=str))
        else:
            print(f"Changed files: {len(out['changed_files'])}")
            for f in out["changed_files"][:20]:
                print(f"- {f}")
            print(f"New deps: {len(out['new_dependencies'])}, "
                  f"removed: {len(out['removed_dependencies'])}")
            for d in out["new_dependencies"][:10]:
                print(f"+ {d['file']} imports {d['added_import']}")
    else:
        snap = mem.graph_snapshot(".")
        print(f"Snapshot: {snap['files']} files, "
              f"{sum(len(v) for v in snap['imports'].values())} local edges")


if __name__ == "__main__":
    main()
