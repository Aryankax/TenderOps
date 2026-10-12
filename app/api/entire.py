"""Entire Continuous Memory API (Neathacks 2026).

Checkpoints + agentic search + Graph + Brain, offline-first: every endpoint
works without the Entire CLI; when the binaries exist their results are
attached alongside the local ones.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.models.entire import BriefIn, CheckpointIn, CheckpointOut, ReviewIn
from app.services import entire_memory as mem

router = APIRouter(prefix="/api/entire", tags=["Entire"])


@router.get("/status", summary="Entire integration status")
async def entire_status(user: dict = Depends(get_current_user)):
    _ = user
    return {"enabled": True, **mem.cli_status()}


@router.post("/checkpoints", response_model=CheckpointOut,
             summary="Record an Entire checkpoint")
async def create_checkpoint(body: CheckpointIn,
                            user: dict = Depends(get_current_user)):
    _ = user
    return mem.record_checkpoint(
        tender_id=body.tender_id, request=body.request,
        decisions=body.decisions, changed_files=body.changed_files,
        validation=body.validation, run_id=body.run_id,
        git_commit=body.git_commit, branch=body.branch,
    )


@router.get("/checkpoints", summary="List Entire checkpoints")
async def list_checkpoints(tender_id: str | None = None,
                           limit: int = Query(default=50, le=200),
                           user: dict = Depends(get_current_user)):
    _ = user
    return {"checkpoints": mem.list_checkpoints(tender_id, limit)}


@router.get("/checkpoints/{checkpoint_id}", summary="Get one checkpoint")
async def get_checkpoint(checkpoint_id: str,
                         user: dict = Depends(get_current_user)):
    _ = user
    c = mem.get_checkpoint(checkpoint_id)
    if c is None:
        raise HTTPException(status_code=404,
                            detail=f"Unknown checkpoint {checkpoint_id}.")
    return c


@router.get("/checkpoints/{checkpoint_id}/demo", summary="Checkpoint demo page")
async def checkpoint_demo(checkpoint_id: str,
                          user: dict = Depends(get_current_user)):
    _ = user
    try:
        return mem.checkpoint_demo(checkpoint_id)
    except KeyError:
        raise HTTPException(status_code=404,
                            detail=f"Unknown checkpoint {checkpoint_id}.")


@router.get("/search", summary="Agentic search over checkpoints + code")
async def entire_search(q: str, top_k: int = Query(default=5, le=20),
                        user: dict = Depends(get_current_user)):
    _ = user
    return {"query": q,
            "checkpoints": mem.search_checkpoints(q, top_k=top_k),
            "code": mem.EntireGraph.locate(q, top_k=top_k)}


@router.post("/brief", summary="'Before you build' briefing")
async def entire_brief(body: BriefIn, user: dict = Depends(get_current_user)):
    _ = user
    return mem.build_briefing(body.query, top_k=body.top_k)


@router.post("/review", summary="Reviewer: intent vs. change")
async def entire_review(body: ReviewIn, user: dict = Depends(get_current_user)):
    _ = user
    return mem.review_change(body.request, body.changed_files,
                             body.decisions, body.checkpoint_id)


@router.get("/scout", summary="Regression test scout")
async def entire_scout(checkpoint_id: str = "",
                       user: dict = Depends(get_current_user)):
    _ = user
    c = mem.get_checkpoint(checkpoint_id) if checkpoint_id else None
    if checkpoint_id and c is None:
        raise HTTPException(status_code=404,
                            detail=f"Unknown checkpoint {checkpoint_id}.")
    return mem.scout_tests(checkpoint_id)


@router.get("/graph/search", summary="Entire Graph code search")
async def graph_search(q: str, top_k: int = Query(default=5, le=20),
                       user: dict = Depends(get_current_user)):
    _ = user
    return {"query": q, "hits": mem.EntireGraph.locate(q, top_k=top_k)}


@router.get("/graph/stats", summary="Entire Graph savings stats")
async def graph_stats(user: dict = Depends(get_current_user)):
    _ = user
    return mem.EntireGraph.stats()


@router.get("/graph/impact", summary="Change-impact for one file")
async def graph_impact(file: str, user: dict = Depends(get_current_user)):
    _ = user
    return mem.EntireGraph.impact(file)


@router.get("/brain/facts", summary="Read Brain carryover facts")
async def brain_facts(user: dict = Depends(get_current_user)):
    _ = user
    return {"facts": mem.EntireBrain.read_facts()}
