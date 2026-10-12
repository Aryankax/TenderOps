"""Export the static Swagger/OpenAPI spec (openapi.json).

Regenerate after changing endpoints or models:
    uv run python scripts/export_openapi.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "openapi.json"

spec = app.openapi()
OUT.write_text(json.dumps(spec, indent=2), encoding="utf-8")
print(f"wrote {OUT} ({len(spec['paths'])} paths)")
