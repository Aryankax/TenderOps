import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.api.deps import get_current_user
from app.services.observability import read_events

router = APIRouter(prefix="/api/events", tags=["Events"])


@router.get(
    "/stream",
    summary="Live agent event stream (SSE)",
    description=(
        "Server-sent events of agent activity: node starts/successes, "
        "retries, provider fallbacks, blockers. Filter by tender_id "
        "(matches tender_id or run_id). Replay recent history first, "
        "then stream live. Clients reconnect with ?after=<last seq>."
    ),
    responses={200: {"description": "text/event-stream"}},
)
async def stream_events(
    tender_id: str | None = Query(default=None),
    after: int = Query(default=0),
    user: dict[str, Any] = Depends(get_current_user),
):
    async def generate():
        seq = after
        # Replay anything already buffered so late joiners see the run start.
        for event in read_events(after_seq=seq, tender_id=tender_id):
            seq = event["seq"]
            yield f"data: {json.dumps(event)}\n\n"
        # Then follow live; heartbeat keeps proxies from closing idle runs.
        idle = 0
        while True:
            batch = read_events(after_seq=seq, tender_id=tender_id)
            for event in batch:
                seq = event["seq"]
                yield f"data: {json.dumps(event)}\n\n"
            idle = 0 if batch else idle + 1
            if idle >= 20:  # ~10s of silence: close, client reconnects
                break  # with ?after=<seq>; resume is lossless
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # Proxies (incl. Next.js dev rewrites) must not buffer or cache
            # the stream, or they log ECONNRESET-style errors on idle closes.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
