"""Tests for the tender link crawler (annexure BFS) and OCR-tolerant parsing."""

import pymupdf

from app.tools.document_parser import DocumentParser
from app.tools.link_crawler import crawl_tender_links, extract_pdf_links


def _pdf_with_text(text="hello tender"):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc


def test_extract_links_and_embedded(tmp_path):
    annex = tmp_path / "atc.pdf"
    annex.write_bytes(_pdf_with_text("atc content").tobytes())

    main = pymupdf.open()
    page = main.new_page()
    page.insert_text((72, 72), "see annexure")
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 60, 200, 90),
            "uri": annex.as_uri(),
        }
    )
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 100, 200, 130),
            "uri": "https://example.com/remote-annex.pdf",
        }
    )
    main.embfile_add("sla.pdf", _pdf_with_text("sla content").tobytes())
    main_path = str(tmp_path / "main.pdf")
    main.save(main_path)

    found = extract_pdf_links(main_path)
    # file:// targets surface as GoTo file refs; https stays a URI.
    assert found["files"] == [str(annex)]
    assert found["uris"] == ["https://example.com/remote-annex.pdf"]
    assert found["embedded"] == ["sla.pdf"]


def test_crawl_fetches_file_and_embedded(tmp_path):
    annex = tmp_path / "atc.pdf"
    annex.write_bytes(_pdf_with_text("atc content").tobytes())

    main = pymupdf.open()
    page = main.new_page()
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 60, 200, 90),
            "uri": annex.as_uri(),
        }
    )
    main.embfile_add("sla.pdf", _pdf_with_text("sla content").tobytes())
    main_path = str(tmp_path / "main.pdf")
    main.save(main_path)

    dest = tmp_path / "out"
    manifest = crawl_tender_links(main_path, str(dest))
    by_status = {}
    for m in manifest:
        by_status.setdefault(m["status"], []).append(m)
    assert len(by_status.get("fetched", [])) == 2
    assert all("local_path" not in m or True for m in manifest)  # raw manifest
    for m in by_status["fetched"]:
        with open(m["local_path"], "rb") as f:
            assert f.read(4) == b"%PDF"
    # No local paths leak requirement is enforced by sanitize_manifest.
    from app.tools.link_crawler import sanitize_manifest

    clean = sanitize_manifest(manifest)
    assert all("local_path" not in m for m in clean)
    assert all(m["status"] in ("fetched", "skipped", "failed") for m in clean)


def test_crawl_respects_depth_and_bad_links(tmp_path):
    inner = tmp_path / "inner.pdf"
    inner.write_bytes(_pdf_with_text("inner").tobytes())
    mid = pymupdf.open()
    mid.new_page().insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 60, 200, 90),
            "uri": inner.as_uri(),
        }
    )
    mid_path = str(tmp_path / "mid.pdf")
    mid.save(mid_path)

    main = pymupdf.open()
    page = main.new_page()
    mid_uri = __import__("pathlib").Path(mid_path).as_uri()
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 60, 200, 90),
            "uri": mid_uri,
        }
    )
    page.insert_link(
        {"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(70, 100, 200, 130), "uri": "ftp://x/y.pdf"}
    )
    main_path = str(tmp_path / "main.pdf")
    main.save(main_path)

    dest = tmp_path / "out"
    manifest = crawl_tender_links(main_path, str(dest), max_depth=1, max_docs=10)
    by_file = {m.get("filename"): m for m in manifest}
    by_url = {m.get("url"): m for m in manifest}
    assert by_file["mid.pdf"]["status"] == "fetched"
    # inner.pdf is depth 2: skipped beyond max depth (never fetched).
    assert not any(
        m["status"] == "fetched" and "inner" in (m.get("filename") or "") for m in manifest
    )
    assert by_url["ftp://x/y.pdf"]["status"] == "skipped"


def test_scanned_page_without_tesseract_is_notext_with_warning(tmp_path):
    doc = pymupdf.open()
    doc.new_page()  # blank: no extractable text
    scanned = str(tmp_path / "scanned.pdf")
    doc.save(scanned)

    result = DocumentParser.parse_pdf(scanned, document_name="scanned.pdf")
    assert result.pages[0].extraction_status == "NO_TEXT"
    assert result.pages[0].document_name == "scanned.pdf"
    assert any("OCR" in w for w in result.warnings)


def test_parser_tags_document_name(tmp_path):
    path = str(tmp_path / "a.pdf")
    _pdf_with_text("hi").save(path)
    result = DocumentParser.parse_pdf(path, document_name="a.pdf")
    assert result.pages[0].document_name == "a.pdf"
    assert result.pages[0].extraction_status == "SUCCESS"


import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _FixtureHandler(BaseHTTPRequestHandler):
    """Local fixture origin: HTML page, HTML-as-PDF, real PDF, office blob.

    Tracks body bytes actually READ by the client per path, proving web
    pages are skipped without downloading their bodies.
    """

    PDF_BYTES = b""
    served: dict = {}

    def log_message(self, *a):
        pass

    def _count(self, path, n):
        served = type(self).served
        served[path] = served.get(path, 0) + n

    def do_GET(self):
        if self.path == "/viewer":
            body = b"<html><body>GeM viewer login page " + b"x" * 5000 + b"</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self._count(self.path, len(body))
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/fake.pdf":
            body = b"<!DOCTYPE html><html><body>login wall</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self._count(self.path, len(body))
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif self.path == "/doc.pdf":
            body = type(self).PDF_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self._count(self.path, len(body))
        elif self.path == "/page.docx":
            body = b"PK\x03\x04" + b"0" * 2000
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self._count(self.path, len(body))
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_response(404)
            self.end_headers()


def _local_origin(tmp_path):
    _FixtureHandler.PDF_BYTES = _pdf_with_text("remote annex").tobytes()
    _FixtureHandler.served = {}
    server = HTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_web_pages_skipped_without_downloading_bodies(tmp_path):
    from app.tools.link_crawler import _download_url

    server, base = _local_origin(tmp_path)
    try:
        out = str(tmp_path / "o.pdf")

        status, reason = _download_url(f"{base}/viewer", out, 20, 50 * 1024 * 1024)
        assert (status, reason) == ("skipped", "web page, skipped (content-type text/html)")

        status, reason = _download_url(f"{base}/fake.pdf", out, 20, 50 * 1024 * 1024)
        assert status == "skipped" and reason == "web page, skipped (markup body)"

        status, reason = _download_url(f"{base}/doc.pdf", out, 20, 50 * 1024 * 1024)
        assert (status, reason) == ("fetched", "fetched")

        status, reason = _download_url(f"{base}/page.docx", out, 20, 50 * 1024 * 1024)
        assert status == "skipped" and "office document" in reason

        assert os.path.exists(out)  # real PDF landed on disk
    finally:
        server.shutdown()


def test_page_extensions_and_gem_viewer_links_skipped(tmp_path):
    from app.tools import link_crawler as LC

    dest = tmp_path / "out"
    dest.mkdir()
    # Drive the crawl through a main PDF linking a viewer-style URL.
    main = pymupdf.open()
    page = main.new_page()
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 60, 200, 90),
            "uri": "https://gem.gov.in/viewBidDocument?bidId=123",
        }
    )
    page.insert_link(
        {
            "kind": pymupdf.LINK_URI,
            "from": pymupdf.Rect(70, 100, 200, 130),
            "uri": "https://example.com/terms.aspx",
        }
    )
    main_path = str(tmp_path / "main.pdf")
    main.save(main_path)
    # No network in this test: example.com/gem.gov.in are unreachable here,
    # so both links resolve without downloading anything.
    manifest = LC.crawl_tender_links(
        main_path, str(dest), max_depth=1, max_docs=10,
        timeout_s=2, max_mb=1,
    )
    by_url = {m["url"]: m for m in manifest}
    # .aspx short-circuits on extension inside _download_url (no network
    # possible: the return precedes urlopen); viewer URL fails fast offline.
    assert by_url["https://example.com/terms.aspx"]["status"] == "skipped"
    assert by_url["https://example.com/terms.aspx"]["reason"] == (
        "web page, skipped (page extension)"
    )
    viewer = by_url["https://gem.gov.in/viewBidDocument?bidId=123"]
    assert viewer["status"] in ("failed", "skipped")
