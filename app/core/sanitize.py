"""Shared text sanitization.

PyMuPDF extraction of PDFs with embedded non-Latin fonts (e.g. Hindi GeM
tenders) leaves raw C0 control characters (\\x01, \\x13, ...) in the page
text. When the LLM copies such text verbatim into a JSON string, the output
contains literal unescaped control bytes, which is illegal JSON — Groq's
strict validator rejects it with 400 json_validate_failed ("Failed to
generate JSON"). Sanitizing inputs (so the model never sees them) and
outputs (so API responses stay clean) removes this entire failure class.
"""

from __future__ import annotations

import re

# C0 controls except \t (0x09) and \n (0x0a), plus DEL. \r is normalized to
# \n separately. Form-feed / vertical-tab break JSON strings too.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufeff\u200b]")
_BLANK_LINES_RE = re.compile(r"\n{4,}")


def sanitize_text(text: str) -> str:
    """Strip control/ZW characters; normalize newlines. Never raises."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHAR_RE.sub("", text)
    return _BLANK_LINES_RE.sub("\n\n\n", text)
