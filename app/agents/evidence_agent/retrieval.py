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

import os
import re
from typing import Any

import chromadb

from app.core.config import settings
from app.services.observability import observability
from app.tools.document_parser import DocumentParser

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


def _content_tokens(text: str) -> set:
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


def _get_collection(company_profile_id: str):
    client = chromadb.PersistentClient(path=settings.EVIDENCE_CHROMA_DIR)
    return client.get_or_create_collection(
        name=f"company_evidence_{company_profile_id}",
        metadata={"hnsw:space": "cosine"},
    )


def index_company_docs(
    docs: list[dict[str, str]], company_profile_id: str = "sample"
) -> dict[str, Any]:
    """Parse PDFs and (re)build the company evidence collection.

    docs: [{"path": ..., "name": ...}]. Returns {"chunks": n, "documents": m}.
    """
    collection = _get_collection(company_profile_id)
    try:
        existing = collection.get(limit=1)
        if existing and existing.get("ids"):
            collection.delete(ids=collection.get()["ids"])
    except Exception as e:
        observability.log_event("evidence_index_clear_skipped", reason=str(e)[:200])

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


def query_evidence(
    query_text: str,
    company_profile_id: str = "sample",
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """Return [{document_name, page_number, excerpt, score}] above threshold."""
    collection = _get_collection(company_profile_id)
    res = collection.query(
        query_texts=[query_text[:2000]],
        n_results=top_k or settings.EVIDENCE_TOP_K,
    )
    hits: list[dict[str, Any]] = []
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    for text, meta, dist in zip(docs, metas, dists):
        score = max(0.0, 1.0 - float(dist))
        if score < settings.EVIDENCE_MIN_SCORE:
            continue
        excerpt = str(text)[:1500]
        if not _tokens_overlap(query_text, excerpt):
            continue
        hits.append(
            {
                "document_name": (meta or {}).get("document_name", "unknown"),
                "page_number": int((meta or {}).get("page_number", 1)),
                "excerpt": excerpt,
                "score": round(score, 3),
                "supports": "supports",
            }
        )
    return hits


def reset_index(company_profile_id: str = "sample") -> None:
    client = chromadb.PersistentClient(path=settings.EVIDENCE_CHROMA_DIR)
    try:
        client.delete_collection(name=f"company_evidence_{company_profile_id}")
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
