from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.api.services.workflow_nodes import node_load_memory


async def _load_memory(*args, **kwargs):
    return (
        [],
        [
            {
                "identity": "1711015550",
                "ids": {"pjt_id": "1711015550", "pjt_no": "PJT-2020-1234-5678"},
                "facts": {"title": "AI related project", "summary": "project summary", "year": "2024"},
                "roles": {"lead_org_name": ["ETRI"], "participant_org_name": ["KISTI"], "participant_researcher_name": ["Kim"]},
            }
        ],
        {"name": "summary", "context_kind": "project"},
    )


def test_node_load_memory_rehydrates_prev_context_from_canonical_snapshot():
    state = SimpleNamespace(
        conversation_id="cid",
        request_id="rid",
        messages=[SimpleNamespace(content="follow up")],
        kv_store=None,
    )

    result = asyncio.run(
        node_load_memory(
            state,
            load_conversation_memory_fn=_load_memory,
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert result["prev_context"][0]["pjt_id"] == "1711015550"
    assert result["prev_context"][0]["pjt_no"] == "PJT-2020-1234-5678"
    assert result["prev_context"][0]["org_nm"] == "ETRI"
    assert result["canonical_evidence"][0]["identity"] == "1711015550"
    assert result["render_profile"]["name"] == "summary"
