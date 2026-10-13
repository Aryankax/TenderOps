"""Company evidence index built on Chroma.

Page-level chunks: each chunk carries its exact document + page so every
hit preserves source refs for the audit trail. Default ONNX MiniLM
embeddings run locally — no API budget burned on indexing.

Two-stage filtering: cosine similarity proposes, a deterministic
token-overlap gate disposes junk (with 5 tiny docs in the index, cosine
distances compress into a narrow band and absolute scores are meaningless
— but an unrelated excerpt shares no content tokens with the query).
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any

from app.core.config import settings
from app.services.observability import observability
from app.tools.document_parser import DocumentParser


def _require_chromadb():
    """Import chromadb lazily so the API boots without the optional dep.

    Raises a clear actionable error only when the evidence store is
    actually used.
    """
    try:
        import chromadb
    except ImportError as e:
        raise RuntimeError(
            "Evidence store unavailable: the 'chromadb' package is not "
            "installed. Run 'uv add chromadb' to enable the evidence agent."
        ) from e
    return chromadb


_OVERLAP_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "for",
        "and",
        "to",
        "in",
        "on",
        "by",
        "bidder",
        "bid",
        "bids",
        "requirement",
        "requirements",
        "details",
        "detail",
        "clause",
        "service",
        "services",
        "tender",
        "gem",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "has",
        "have",
        "will",
        "shall",
        "must",
        "should",
        "year",
        "years",
        "per",
        "inr",
        "rs",
        "rupee",
        "rupees",
        "lakh",
        "lakhs",
        "lac",
        "crore",
        "crores",
    }
)


def _content_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if t not in _OVERLAP_STOPWORDS and len(t) > 2
    }


def _tokens_overlap(query: str, excerpt: str) -> bool:
    """True when query and excerpt share a content token.

    Substring match (len>=5) absorbs inflections: 'complet' links
    'completed' and 'completion' without a stemmer.
    """
    q, e = _content_tokens(query), _content_tokens(excerpt)
    if q & e:
        return True
    for a in q:
        if len(a) < 5:
            continue
        for b in e:
            if len(b) >= 5 and (a in b or b in a):
                return True
    return False


def sanitize_company_id(company_profile_id: str | None) -> str:
    """Company namespace for the evidence index (X vs Z never mix).

    Collection names become f"company_evidence_{id}", so the id is strictly
    alphanumerics/dash/underscore. Applied at every collection entry point.
    """
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(company_profile_id or "").strip())
    return clean[:64] or "sample"


def _get_collection(company_profile_id: str):
    chromadb = _require_chromadb()
    client = chromadb.PersistentClient(path=settings.EVIDENCE_CHROMA_DIR)
    return client.get_or_create_collection(
        name=f"company_evidence_{sanitize_company_id(company_profile_id)}",
        metadata={"hnsw:space": "cosine"},
    )


def index_company_docs(
    docs: list[dict[str, str]],
    company_profile_id: str = "sample",
    replace: bool = True,
) -> dict[str, Any]:
    """Parse PDFs and build the company evidence collection.

    docs: [{"path": ..., "name": ...}]. Returns {"chunks": n, "documents": m}.
    replace=False appends to the existing collection (resume flow) instead
    of wiping it first.
    """
    collection = _get_collection(company_profile_id)
    if replace:
        try:
            existing = collection.get(limit=1)
            if existing and existing.get("ids"):
                collection.delete(ids=collection.get()["ids"])
        except Exception as e:
            observability.log_event("evidence_index_clear_skipped", reason=str(e)[:200])
    else:
        # Appending — but the same filename may already be indexed (resume
        # retried, or fix re-uploaded): Chroma rejects duplicate chunk IDs, so
        # drop this document's stale chunks first, then add the fresh parse.
        try:
            names = list({str(doc.get("name", "")) for doc in docs if doc.get("name")})
            if names:
                name_filter: dict[str, Any] = {"document_name": {"$in": names}}
                stale = collection.get(where=name_filter, include=[])
                if stale and stale.get("ids"):
                    collection.delete(ids=stale["ids"])
        except Exception as e:
            observability.log_event(
                "evidence_index_reindex_skipped", reason=str(e)[:200]
            )

    ids, documents, metadatas = [], [], []
    doc_count = 0
    for doc in docs:
        try:
            result = DocumentParser.parse_pdf(doc["path"])
        except Exception as e:
            observability.log_error(
                "evidence_index_parse_failed",
                e,
                document=doc.get("name"),
            )
            continue
        doc_count += 1
        for page in result.pages:
            if page.extraction_status != "SUCCESS" or not page.text.strip():
                continue
            chunk_id = f"{doc['name']}::p{page.page_number}"
            ids.append(chunk_id)
            documents.append(page.text[:8000])
            metadatas.append(
                {"document_name": doc["name"], "page_number": page.page_number}
            )
    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
    observability.log_event(
        "evidence_index_built",
        company_profile_id=company_profile_id,
        documents=doc_count,
        chunks=len(ids),
    )
    return {"chunks": len(ids), "documents": doc_count}


def _coerce_page(value: Any) -> int:
    """Chroma metadata values are untyped — coerce page numbers safely."""
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (ValueError, TypeError):
        return 1


def list_indexed_documents(
    company_profile_id: str = "sample",
) -> dict[str, Any]:
    """Inventory of a company's index: per-document chunks + pages."""
    collection = _get_collection(company_profile_id)
    try:
        stored = collection.get(include=["metadatas"])
    except Exception:
        return {
            "company_profile_id": sanitize_company_id(company_profile_id),
            "documents": 0,
            "chunks": 0,
            "files": [],
        }
    by_doc: dict[str, dict[str, Any]] = {}
    for meta in stored.get("metadatas") or []:
        name = str((meta or {}).get("document_name", "unknown"))
        entry = by_doc.setdefault(name, {"name": name, "chunks": 0, "pages": set()})
        entry["chunks"] += 1
        with contextlib.suppress(ValueError, TypeError):
            entry["pages"].add(_coerce_page((meta or {}).get("page_number", 0)))
    files: list[dict[str, Any]] = [
        {"name": name, "chunks": e["chunks"], "pages": sorted(e["pages"])}
        for name, e in sorted(by_doc.items())
    ]
    return {
        "company_profile_id": sanitize_company_id(company_profile_id),
        "documents": len(files),
        "chunks": sum(f["chunks"] for f in files),
        "files": files,
    }


def query_evidence(
    query_text: str,
    company_profile_id: str = "sample",
    top_k: int | None = None,
    document_name: str | None = None,
    bypass_gates: bool = False,
) -> list[dict[str, Any]]:
    """Return [{document_name, page_number, excerpt, score}] above threshold.

    document_name constrains the search to one file (Chroma where-filter) —
    used to pull fuller context from an already-selected document.
    bypass_gates returns the top chunks unfiltered: last resort for scoped
    fix-file re-checks so the mapping LLM actually reads the file instead of
    reporting "missing" on an empty hit list. Confidence stays honest —
    low scores still down-weight the verdict downstream.
    """
    if not query_text or not query_text.strip():
        return []
    collection = _get_collection(company_profile_id)
    kwargs: dict[str, Any] = {
        "query_texts": [query_text[:2000]],
        "n_results": top_k or settings.EVIDENCE_TOP_K,
    }
    if document_name:
        kwargs["where"] = {"document_name": document_name}
    res = collection.query(**kwargs)
    hits: list[dict[str, Any]] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        score = max(0.0, 1.0 - float(dist))
        excerpt = str(text)[:1500]
        if not bypass_gates:
            if score < settings.EVIDENCE_MIN_SCORE:
                continue
            if not _tokens_overlap(query_text, excerpt):
                continue
        hits.append(
            {
                "document_name": str((meta or {}).get("document_name") or "unknown"),
                "page_number": _coerce_page((meta or {}).get("page_number")),
                "excerpt": excerpt,
                "score": round(score, 3),
                "supports": "supports",
            }
        )
    return hits


def reset_index(company_profile_id: str = "sample") -> None:
    chromadb = _require_chromadb()
    client = chromadb.PersistentClient(path=settings.EVIDENCE_CHROMA_DIR)
    try:
        name = f"company_evidence_{sanitize_company_id(company_profile_id)}"
        client.delete_collection(name=name)
    except Exception as e:
        observability.log_event("evidence_index_reset_skipped", reason=str(e)[:200])
    # Best effort: remove empty persist dir.
    try:
        if os.path.isdir(settings.EVIDENCE_CHROMA_DIR) and not os.listdir(
            settings.EVIDENCE_CHROMA_DIR
        ):
            os.rmdir(settings.EVIDENCE_CHROMA_DIR)
    except OSError:
        pass
