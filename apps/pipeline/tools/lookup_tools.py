"""Phase 1 — 도메인 lookup 도구.

Phase B에서 EntityResolverAgent 내부에 둔 이름 lookup을 도구로도 노출.
PlannerAgent가 "subject가 진짜 NTIS 인물·기관인가?"를 검증하거나, 사용자 질문에서
인물·기관 식별자가 필요할 때 호출.

도구:
    - lookup.person_by_name : 이름 → person_no/소속
    - lookup.org_by_name    : 이름 → org_id/org_code
"""

from __future__ import annotations

from typing import Any, Dict, List

from apps.pipeline.retrieval.name_lookup import lookup_org_by_name, lookup_person_by_name
from apps.pipeline.tools.contracts import ToolContext, ToolEntry, ToolSpec


# ============================================================================
# lookup.person_by_name
# ============================================================================

LOOKUP_PERSON_SPEC = ToolSpec(
    name="lookup.person_by_name",
    description=(
        "NTIS payload의 prtcp_mp_hm_nm_list/prtcp_mp[].hm_nm에서 정확 이름 매칭으로 person_no를 조회. "
        "LLM의 subject NER 검증(0건이면 약어/일반명사로 판단), 동명이인 후보 노출에 사용."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "검증할 사람 이름"},
            "max_hits": {"type": "integer", "default": 10},
        },
        "required": ["name"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "hits": {
                "type": "array",
                "description": "[{person_no, display_name, affiliation?, source_collection}]",
            },
            "hit_count": {"type": "integer"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)


async def lookup_person_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("lookup.person_by_name: 'name' is required")
    if ctx.qdrant_client is None:
        raise RuntimeError("lookup.person_by_name: ctx.qdrant_client is None")
    max_hits = int(args.get("max_hits") or 10)

    hits = lookup_person_by_name(
        qdrant_client=ctx.qdrant_client, name=name, max_hits=max_hits,
    )
    return {
        "hits": [
            {
                "person_no": h.person_no,
                "display_name": h.display_name,
                "affiliation": h.affiliation,
                "source_collection": h.source_collection,
            }
            for h in hits
        ],
        "hit_count": len(hits),
    }


# ============================================================================
# lookup.org_by_name
# ============================================================================

LOOKUP_ORG_SPEC = ToolSpec(
    name="lookup.org_by_name",
    description=(
        "NTIS payload의 prtcp_org[].org_nm에서 정확 기관명 매칭으로 org_id/org_code 조회. "
        "기관 anchor 검증·후보 노출에 사용."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "검증할 기관명"},
            "max_hits": {"type": "integer", "default": 10},
        },
        "required": ["name"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "hits": {
                "type": "array",
                "description": "[{org_id?, org_code?, display_name, source_collection}]",
            },
            "hit_count": {"type": "integer"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)


async def lookup_org_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ValueError("lookup.org_by_name: 'name' is required")
    if ctx.qdrant_client is None:
        raise RuntimeError("lookup.org_by_name: ctx.qdrant_client is None")
    max_hits = int(args.get("max_hits") or 10)

    hits = lookup_org_by_name(
        qdrant_client=ctx.qdrant_client, name=name, max_hits=max_hits,
    )
    return {
        "hits": [
            {
                "org_id": h.org_id,
                "org_code": h.org_code,
                "display_name": h.display_name,
                "source_collection": h.source_collection,
            }
            for h in hits
        ],
        "hit_count": len(hits),
    }


# ============================================================================
# Registry entries
# ============================================================================

def lookup_tool_entries() -> List[ToolEntry]:
    return [
        ToolEntry(spec=LOOKUP_PERSON_SPEC, handler=lookup_person_handler),
        ToolEntry(spec=LOOKUP_ORG_SPEC, handler=lookup_org_handler),
    ]
