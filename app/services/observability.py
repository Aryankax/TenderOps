"""Observability adapter for neatlogs.

Also keeps an in-memory ring buffer of recent events so the UI can stream
a live agent timeline (stage -> agent -> tool) over SSE without touching
the tracing backend.
"""

import itertools
import logging
import threading
import time
from collections import deque

logger = logging.getLogger("tenderops")
logger.setLevel(logging.INFO)
ch = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
ch.setFormatter(formatter)
logger.addHandler(ch)

_EVENT_BUFFER: deque = deque(maxlen=500)
_EVENT_LOCK = threading.Lock()
_EVENT_SEQ = itertools.count(1)


def _record(kind: str, name: str, payload: dict) -> None:
    try:
        safe = jsonable(payload)
    except Exception:
        safe = {"unserializable": True}
    with _EVENT_LOCK:
        _EVENT_BUFFER.append(
            {
                "seq": next(_EVENT_SEQ),
                "ts": time.time(),
                "kind": kind,  # event | error
                "name": name,
                "data": safe,
            }
        )


def jsonable(value):
    """Best-effort JSON-safe conversion for event payloads."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return str(value)[:500]


def read_events(after_seq: int = 0, tender_id: str | None = None) -> list[dict]:
    """Snapshot of buffered events (newest last), optionally filtered."""
    with _EVENT_LOCK:
        buffered = list(_EVENT_BUFFER)
    out = []
    for event in buffered:
        if event["seq"] <= after_seq:
            continue
        if tender_id:
            data = event.get("data") or {}
            if data.get("tender_id") != tender_id and data.get("run_id") != tender_id:
                continue
        out.append(event)
    return out


class Observability:
    @staticmethod
    def log_event(event_name: str, **kwargs):
        """Log an observability event."""
        # TODO: Implement actual neatlogs integration once API is verified.
        logger.info(f"Event: {event_name} | {kwargs}")
        _record("event", event_name, kwargs)

    @staticmethod
    def log_error(error_name: str, error: Exception, **kwargs):
        logger.error(f"Error: {error_name} | {error} | {kwargs}")
        payload = dict(kwargs)
        payload["error"] = str(error)[:500]
        _record("error", error_name, payload)


observability = Observability()
