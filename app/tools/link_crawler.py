"""BFS crawler for documents linked inside a tender PDF.

GeM-style tenders cite annexures (ATC, SLA, corrigenda) behind "Click here
to view the file" links or embedded file attachments. This tool walks those
links breadth-first from the main PDF — remote https URLs, local file://
URLs, and embedded attachments — downloads the PDFs it can reach, and
records a manifest of what was fetched vs what stayed out of reach (notably
login-walled links, which the issues detector then reports as missing
attachments instead of failing silently).

Safety: visited-set (normalized URLs), depth cap, document cap, per-file
size cap, zip-slip-proof filenames, PDF-magic check on downloads.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import pymupdf as fitz

from app.core.sanitize import sanitize_text
from app.services.observability import observability

logger = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF"
_FILENAME_BAD_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(name: str, fallback: str) -> str:
    name = (name or "").strip().replace("\\", "/").split("/")[-1].strip()
    name = _FILENAME_BAD_CHARS_RE.sub("_", name).strip("._")
    if not name.lower().endswith(".pdf"):
        name = (name or fallback) + ".pdf"
    return name[:120] or fallback


def extract_pdf_links(pdf_path: str) -> dict[str, list[str]]:
    """ URIs, local file refs, and embedded attachment names in a PDF.

    - URI annotations (http/https): "uris".
    - GoTo-remote annotations (PyMuPDF normalizes file:// targets into these
      with a "file" key): "files", resolved against the source PDF's folder.
    - Embedded file attachments: "embedded".
    """
    uris: list[str] = []
    files: list[str] = []
    embedded: list[str] = []
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return {"uris": [], "files": [], "embedded": []}
    try:
        base_dir = os.path.dirname(os.path.abspath(pdf_path))
        for page in doc:
            try:
                links = page.get_links() or []
            except Exception:
                logger.debug("link page scan skipped", exc_info=True)
                continue
            for link in links:
                if not isinstance(link, dict):
                    continue
                uri = link.get("uri")
                if uri and isinstance(uri, str) and uri.strip():
                    uris.append(uri.strip())
                    continue
                target = link.get("file")
                if target and isinstance(target, str) and target.strip():
                    target = target.strip()
                    if not os.path.isabs(target):
                        target = os.path.join(base_dir, target)
                    # PyMuPDF returns "/C:/..." for drive paths — normalize.
                    if re.match(r"^/[A-Za-z]:/", target):
                        target = target[1:]
                    files.append(os.path.normpath(target))
        try:
            embedded = [str(n) for n in (doc.embfile_names() or [])]
        except Exception:
            embedded = []
    finally:
        doc.close()
    # Dedupe, preserving order.
    return {
        "uris": list(dict.fromkeys(uris)),
        "files": list(dict.fromkeys(files)),
        "embedded": list(dict.fromkeys(embedded)),
    }


def _normalize_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        return urllib.parse.urlunsplit(
            (parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "")
        )
    except Exception:
        return url.strip()


def _looks_like_web_page(first: bytes) -> bool:
    """True when leading bytes are markup, not a document (PDF/office)."""
    head = first.lstrip()[:32].lower()
    return head.startswith(b"<") and (
        b"html" in head or b"doctype" in head or b"head" in head
    )


def _download_url(
    url: str, dest_path: str, timeout_s: int, max_bytes: int
) -> tuple[str, str]:
    """Fetch a URL to dest_path. Returns (ok, reason). PDF-magic checked.

    Web pages are never crawled: obvious page URLs, HTML content-types, and
    markup bodies are all rejected BEFORE the body is downloaded, so GeM
    viewer/login pages cost one header round-trip instead of a full fetch.
    Office documents (Word/Excel) are reported distinctly — annexure
    extraction is PDF-only by design.
    """
    path = urllib.parse.urlsplit(url).path.lower()
    if path.endswith((".html", ".htm", ".php", ".aspx", ".jsp", ".do")):
        return "skipped", "web page, skipped (page extension)"
    req = urllib.request.Request(
        url, headers={"User-Agent": "TenderOps-link-crawler/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            length = resp.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                return "failed", f"remote file too large ({length} bytes)"
            if ("html" in ctype or "text/" in ctype) and not path.endswith(".pdf"):
                # Header says web page: skip WITHOUT downloading the body.
                return "skipped", f"web page, skipped (content-type {ctype or 'unknown'})"
            with open(dest_path, "wb") as f:
                remaining = max_bytes + 1
                first = True
                while True:
                    chunk = resp.read(min(65536, remaining))
                    if not chunk:
                        break
                    if first:
                        # Sniff the leading bytes: HTML masquerading as a PDF
                        # (or generic content-type) aborts before the download.
                        first = False
                        if _looks_like_web_page(chunk):
                            return "skipped", "web page, skipped (markup body)"
                        if chunk.lstrip().startswith(b"PK"):
                            return "skipped", "office document, skipped (PDF-only)"
                    remaining -= len(chunk)
                    if remaining <= 0:
                        return "failed", "remote file too large"
                    f.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "failed", f"login-walled (HTTP {e.code})"
        return "failed", f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001 — network is untrusted
        return "failed", f"download failed: {str(e)[:120]}"
    try:
        with open(dest_path, "rb") as f:
            if f.read(4) != _PDF_MAGIC:
                return "failed", "not a PDF (bad magic)"
    except OSError as e:
        return "failed", f"unreadable download: {e}"
    return "fetched", "fetched"


def _copy_local_file(url: str, dest_path: str, max_bytes: int) -> tuple[str, str]:
    """Resolve file:// URLs against the local filesystem (offline-friendly)."""
    try:
        raw_path = urllib.parse.urlsplit(url).path
        # Windows file:///C:/... yields "/C:/..." — strip the leading slash.
        if re.match(r"^/[A-Za-z]:/", raw_path):
            raw_path = raw_path[1:]
        local = urllib.request.url2pathname(raw_path)
        if not os.path.isfile(local):
            return "failed", "local file not found"
        if os.path.getsize(local) > max_bytes:
            return "failed", "local file too large"
        shutil.copyfile(local, dest_path)
        with open(dest_path, "rb") as f:
            if f.read(4) != _PDF_MAGIC:
                return "failed", "not a PDF (bad magic)"
    except Exception as e:  # noqa: BLE001
        return "failed", f"local copy failed: {str(e)[:120]}"
    return "fetched", "fetched"


def crawl_tender_links(
    pdf_path: str,
    dest_dir: str,
    *,
    max_depth: int = 2,
    max_docs: int = 10,
    timeout_s: int = 20,
    max_mb: int = 50,
) -> list[dict[str, Any]]:
    """BFS over a tender PDF's embedded links. Returns a fetch manifest.

    Each entry: {url, depth, via, status, reason, local_path?, filename?}.
    status is fetched | skipped | failed. Never raises for bad links —
    failures are manifest rows, so callers can report them as missing
    attachments instead of crashing the analysis.
    """
    os.makedirs(dest_dir, exist_ok=True)
    max_bytes = max_mb * 1024 * 1024
    manifest: list[dict[str, Any]] = []
    visited: set[str] = set()
    queue: list[tuple[str, int, str]] = []

    found = extract_pdf_links(pdf_path)
    try:
        doc = fitz.open(pdf_path)
        embedded_data = {name: doc.embfile_get(name) for name in found["embedded"]}
        doc.close()
    except Exception:
        embedded_data = {}

    for name in found["embedded"]:
        filename = _safe_filename(name, "attachment.pdf")
        dest = os.path.join(dest_dir, filename)
        try:
            with open(dest, "wb") as f:
                f.write(embedded_data[name])
            manifest.append(
                {
                    "url": f"embedded:{name}",
                    "depth": 1,
                    "via": "embedded-attachment",
                    "status": "fetched",
                    "reason": "embedded file extracted",
                    "local_path": dest,
                    "filename": filename,
                }
            )
        except Exception as e:  # noqa: BLE001
            manifest.append(
                {
                    "url": f"embedded:{name}",
                    "depth": 1,
                    "via": "embedded-attachment",
                    "status": "failed",
                    "reason": f"extract failed: {str(e)[:120]}",
                }
            )

    for uri in found["uris"]:
        queue.append((uri, 1, os.path.basename(pdf_path)))
    for local in found["files"]:
        try:
            queue.append((Path(local).as_uri(), 1, os.path.basename(pdf_path)))
        except Exception:
            logger.debug("local link skipped: %s", local, exc_info=True)
            continue

    docs_fetched = sum(1 for m in manifest if m["status"] == "fetched")
    while queue and docs_fetched < max_docs:
        url, depth, via = queue.pop(0)
        norm = _normalize_url(url)
        if norm in visited:
            continue
        visited.add(norm)
        scheme = urllib.parse.urlsplit(url).scheme.lower()
        filename = _safe_filename(
            os.path.basename(urllib.parse.urlsplit(url).path)
            or f"linked-doc-{len(manifest) + 1}",
            f"linked-doc-{len(manifest) + 1}.pdf",
        )
        dest = os.path.join(dest_dir, filename)
        if scheme in ("http", "https"):
            if depth > max_depth:
                manifest.append(
                    {
                        "url": url, "depth": depth, "via": via,
                        "status": "skipped", "reason": "beyond max depth",
                    }
                )
                continue
            status, reason = _download_url(url, dest, timeout_s, max_bytes)
        elif scheme == "file":
            status, reason = _copy_local_file(url, dest, max_bytes)
        else:
            manifest.append(
                {
                    "url": url, "depth": depth, "via": via,
                    "status": "skipped", "reason": f"unsupported scheme '{scheme or '?'}'",
                }
            )
            continue
        if status == "fetched":
            docs_fetched += 1
            manifest.append(
                {
                    "url": url, "depth": depth, "via": via,
                    "status": "fetched", "reason": reason,
                    "local_path": dest, "filename": filename,
                }
            )
            # BFS one level deeper: annexures link to further annexures.
            if depth < max_depth and docs_fetched < max_docs:
                try:
                    child_links = extract_pdf_links(dest)
                    for child in child_links["uris"]:
                        if _normalize_url(child) not in visited:
                            queue.append((child, depth + 1, filename))
                    for child_file in child_links["files"]:
                        try:
                            child_url = Path(child_file).as_uri()
                        except Exception:
                            logger.debug("child link skipped: %s", child_file, exc_info=True)
                            continue
                        if _normalize_url(child_url) not in visited:
                            queue.append((child_url, depth + 1, filename))
                except Exception as e:  # noqa: BLE001
                    observability.log_event(
                        "linkcrawl_child_scan_skipped", file=filename,
                        reason=str(e)[:120],
                    )
        elif status == "skipped":
            manifest.append(
                {
                    "url": url, "depth": depth, "via": via,
                    "status": "skipped", "reason": reason,
                }
            )
        else:
            manifest.append(
                {
                    "url": url, "depth": depth, "via": via,
                    "status": "failed", "reason": reason,
                }
            )
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except OSError:
                pass
    return manifest


def sanitize_manifest(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip local filesystem paths before persisting to results."""
    clean = []
    for entry in manifest:
        entry = dict(entry)
        entry.pop("local_path", None)
        if isinstance(entry.get("url"), str):
            entry["url"] = sanitize_text(entry["url"])[:500]
        clean.append(entry)
    return clean
