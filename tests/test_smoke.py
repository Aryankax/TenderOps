"""Smoke tests: app boots, routes serve, evidence dep degrades cleanly.

Fast and offline-safe: no LLM or network calls (validation fails before
any model invocation).
"""

from fastapi.testclient import TestClient


def _client():
    import app.main as m
    return TestClient(m.app, raise_server_exceptions=False)


def test_health_ok():
    c = _client()
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_routes_registered():
    c = _client()
    paths = set(c.get("/openapi.json").json()["paths"])
    assert "/api/tender-analysis" in paths
    assert "/api/pipeline/run" in paths
    assert "/api/pipeline/evidence-index" in paths


def test_tender_analysis_rejects_non_pdf(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    r = c.post("/api/tender-analysis", files={"file": ("x.txt", b"hi")},
               headers=headers)
    assert r.status_code == 400


def test_tender_analysis_requires_file(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    r = c.post("/api/tender-analysis", headers=headers)
    assert r.status_code == 422


def test_pipeline_requires_file(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    r = c.post("/api/pipeline/run", headers=headers)
    assert r.status_code == 422


def test_evidence_retrieval_imports_without_chromadb(monkeypatch):
    """retrieval.py must import even when chromadb is missing (lazy dep)."""
    import builtins
    import app.agents.evidence_agent.retrieval as ret

    real_import = builtins.__import__

    def no_chromadb(name, *args, **kwargs):
        if name == "chromadb":
            raise ImportError("No module named 'chromadb'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_chromadb)
    try:
        with __import__("pytest").raises(RuntimeError, match="uv add chromadb"):
            ret._require_chromadb()
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)


def _make_pdf_bytes(text="hello"):
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    return doc.tobytes()


def _make_zip_bytes():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("certs/turnover.pdf", _make_pdf_bytes("turnover 5 crore"))
        zf.writestr("experience.pdf", _make_pdf_bytes("experience certificate"))
        zf.writestr("notes.txt", b"not a pdf")
        zf.writestr("../../evil.pdf", _make_pdf_bytes("evil"))
    return buf.getvalue()


def test_evidence_index_zip_extracts_pdfs_one_by_one(monkeypatch, tmp_path):
    import app.api.pipeline as pipeline_mod
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    seen = {}

    def fake_index(saved, profile="sample"):
        seen["names"] = [s["name"] for s in saved]
        return {"chunks": len(saved), "documents": len(saved)}

    monkeypatch.setattr(
        pipeline_mod.retrieval, "index_company_docs", fake_index
    )
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    r = c.post(
        "/api/pipeline/evidence-index",
        files=[("zip_file", ("docs.zip", _make_zip_bytes(), "application/zip"))],
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["documents"] == 2
    assert sorted(seen["names"]) == ["experience.pdf", "turnover.pdf"]
    assert sorted(body["indexed_files"]) == ["experience.pdf", "turnover.pdf"]
    skipped = {s["name"]: s["reason"] for s in body["skipped"]}
    assert skipped.get("notes.txt") == "not a PDF"
    assert any("unsafe path" in reason for reason in skipped.values())


def test_evidence_index_rejects_bad_zip(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    assert (
        c
        .post(
            "/api/pipeline/evidence-index",
            files=[("zip_file", ("docs.zip", b"not a zip", "application/zip"))],
            headers=headers,
        )
        .status_code
        == 400
    )


def test_evidence_index_rejects_non_zip_extension(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    assert (
        c
        .post(
            "/api/pipeline/evidence-index",
            files=[("zip_file", ("docs.pdf", _make_pdf_bytes(), "application/pdf"))],
            headers=headers,
        )
        .status_code
        == 400
    )


def test_evidence_index_requires_input(monkeypatch, tmp_path):
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    r = c.post("/api/pipeline/evidence-index", headers=headers)
    assert r.status_code == 400
    assert "No usable PDFs" in r.json()["detail"]


def test_evidence_index_single_pdf_still_works(monkeypatch, tmp_path):
    import app.api.pipeline as pipeline_mod
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        pipeline_mod.retrieval,
        "index_company_docs",
        lambda saved, profile="sample": {"chunks": 1, "documents": 1},
    )
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    r = c.post(
        "/api/pipeline/evidence-index",
        files=[("files", ("a.pdf", _make_pdf_bytes(), "application/pdf"))],
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["indexed_files"] == ["a.pdf"]


def test_evidence_index_ignores_empty_swagger_boxes(monkeypatch, tmp_path):
    """Swagger UI submits untouched file boxes as plain "" text fields.

    zip + empty `files` box indexes from the zip; empty input alone is a
    400 business error, never a 422 validation error.
    """
    import io
    import zipfile
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.pdf", _make_pdf_bytes("doc a"))

    import app.api.pipeline as pipeline_mod

    orig = pipeline_mod.retrieval.index_company_docs
    pipeline_mod.retrieval.index_company_docs = (
        lambda saved, profile="sample": {"chunks": len(saved), "documents": len(saved)}
    )
    try:
        c = _client()
        headers, _ = login_headers(c)
        make_company(c, headers, name="sample")
        r = c.post(
            "/api/pipeline/evidence-index",
            data={"files": ""},
            files=[("zip_file", ("docs.zip", buf.getvalue(), "application/zip"))],
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["indexed_files"] == ["a.pdf"]

        r = c.post("/api/pipeline/evidence-index", data={"files": ""},
                   headers=headers)
        assert r.status_code == 400
        assert "No usable PDFs" in r.json()["detail"]
    finally:
        pipeline_mod.retrieval.index_company_docs = orig


def test_requirement_refs_expand_ids_to_titles():
    """Bare IDs in prose become title + description; structured fields and
    unknown IDs are untouched; expansion is idempotent."""
    from app.core.requirement_refs import build_ref_map, resolve_requirement_refs

    reqs = [
        {"requirement_id": "R4", "title": "Minimum Turnover",
         "normalized_description": "Eligibility: Minimum Turnover. Threshold: at least 10,000,000 INR."},
        {"requirement_id": "R1-2", "title": "Option clause",
         "normalized_description": None},
    ]
    ref_map = build_ref_map(reqs)
    out = resolve_requirement_refs(
        "R4 states 10M but R1-2 and REQ-9 conflict.", ref_map
    )
    assert '"Minimum Turnover" [R4]' in out
    assert "10,000,000" in out
    assert '"Option clause" [R1-2]' in out
    assert "REQ-9" in out  # unknown IDs pass through
    # Idempotent: expanded bracketed IDs are never re-expanded.
    assert resolve_requirement_refs(out, ref_map) == out
    # R1 must not match inside R10.
    solo = resolve_requirement_refs(
        "See R10 for details.", {"R1": '"One" [R1]', "R10": '"Ten" [R10]'}
    )
    assert solo == 'See "Ten" [R10] for details.'
    assert resolve_requirement_refs(None, ref_map) is None
    assert resolve_requirement_refs("plain text", {}) == "plain text"


def test_run_forwards_fetch_links_query_param(monkeypatch, tmp_path):
    """The UI checkbox rides as ?fetch_links=false — it must reach the
    orchestrator instead of silently defaulting to True."""
    import app.api.pipeline as pipeline_mod
    from helpers import isolate_auth, login_headers, make_company

    isolate_auth(monkeypatch, tmp_path)

    seen = {}

    def fake_run(pdf_path, document_name="", company_profile_id="sample",
                 fetch_links=True):
        seen["fetch_links"] = fetch_links
        return {
            "tender_id": "t1", "analysis": _minimal_analysis(),
            "evidence": _minimal_evidence(), "compliance": None,
            "package": None, "verification": None,
            "pipeline_status": "COMPLETED", "checkpoint_path": None,
        }

    monkeypatch.setattr(
        "app.orchestrator.service.OrchestratorService.run_tender",
        staticmethod(fake_run),
    )
    import pymupdf as fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "hi")
    pdf = doc.tobytes()
    c = _client()
    headers, _ = login_headers(c)
    make_company(c, headers, name="sample")
    r = c.post(
        "/api/pipeline/run?fetch_links=false",
        files={"file": ("t.pdf", pdf, "application/pdf")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert seen.get("fetch_links") is False


def _minimal_analysis():
    return {
        "tender_id": "t1", "analysis_status": "SUCCESS", "metadata": {},
        "requirements": [], "issues": [], "total_pages": 1,
        "processed_pages": 1, "unreadable_pages": 0,
        "requirements_needing_review": 0, "warnings": [],
        "failed_chunks": [], "linked_documents": [], "completeness": {},
        "analysis_version": "1.0",
    }


def _minimal_evidence():
    return {"tender_id": "t1", "company_profile_id": "sample", "items": [],
            "unmapped_requirements": [], "warnings": []}
