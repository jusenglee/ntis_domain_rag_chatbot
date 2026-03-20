from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.api.services.conversation_store import (
    build_save_history_payload,
    load_conversation_memory_from_store,
    save_conversation_memory,
)


class FakeKV:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value


def test_build_save_history_payload_includes_canonical_context():
    state = SimpleNamespace(
        conversation_id="cid",
        chat_history=[],
        messages=[SimpleNamespace(content="answer")],
        context=[{"title": "doc"}],
        canonical_evidence=[{"identity": "1711015550"}],
        render_profile={"name": "summary", "context_kind": "project"},
        request_started_at=1.0,
    )

    payload = build_save_history_payload(state, max_history_turns=5)

    assert payload["canonical_evidence"] == [{"identity": "1711015550"}]
    assert payload["render_profile"]["name"] == "summary"


def test_save_and_load_conversation_memory_roundtrip_preserves_canonical_context():
    kv = FakeKV()

    async def scenario():
        await save_conversation_memory(
            kv_store=kv,
            conversation_id="cid",
            history=[],
            canonical_evidence=[{"identity": "1711015550", "ids": {"pjt_id": "1711015550"}}],
            render_profile={"name": "detail", "context_kind": "project"},
                history_ttl_seconds=60,
        )
        return await load_conversation_memory_from_store(
            "cid",
            kv_store=kv,
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
            truncate_text=lambda value: value,
        )

    history, canonical_evidence, render_profile = asyncio.run(scenario())

    assert history == []
    assert canonical_evidence[0]["ids"]["pjt_id"] == "1711015550"
    assert render_profile["name"] == "detail"



def test_save_conversation_memory_persists_only_canonical_snapshot():
    kv = FakeKV()

    async def scenario():
        await save_conversation_memory(
            kv_store=kv,
            conversation_id="cid",
            history=[],
            canonical_evidence=[{"identity": "1711015550"}],
            render_profile={"name": "summary", "context_kind": "project"},
            history_ttl_seconds=60,
        )
        return dict(kv.data)

    saved = asyncio.run(scenario())
    assert "conversation:cid:last_context" not in saved
    assert "conversation:cid:last_fallback_context" not in saved
