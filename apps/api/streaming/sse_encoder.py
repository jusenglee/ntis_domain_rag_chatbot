from __future__ import annotations

import json
from typing import Any

from apps.api.streaming.contracts import StreamEvent


def encode_sse_payload(tag: str, **payload: Any) -> str:
    body = {"tag": tag, **payload}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


def encode_stream_event(event: StreamEvent) -> str:
    payload = {
        "kind": event.kind,
        "request_id": event.request_id,
        "seq": event.seq,
        "model_key": event.model_key,
        "content": event.content,
        "meta": dict(event.meta or {}),
    }
    return encode_sse_payload("event", event=payload)
