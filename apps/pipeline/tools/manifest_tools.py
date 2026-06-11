"""Phase 1 — manifest 슬롯 조회 도구.

PlannerAgent가 직전 turn의 published_manifest를 인용해 단일 항목 detail이나 부분집합 필터를
실행할 때 사용. session_state.published_manifest의 items에서 식별자를 결정적으로 추출한다.

도구:
    - manifest.get_item   : 1-indexed rank → {target, identifiers}
    - manifest.filter     : 전체 items → {target, identifiers(axis별 list)}
"""

from __future__ import annotations

from typing import Any, Dict, List

from apps.pipeline.tools.contracts import ToolContext, ToolEntry, ToolSpec


# ============================================================================
# manifest.get_item
# ============================================================================

MANIFEST_GET_ITEM_SPEC = ToolSpec(
    name="manifest.get_item",
    description=(
        "직전 발행된 manifest의 1-indexed rank에 해당하는 항목을 조회. "
        "사용자가 'N번 항목'을 가리키는데 세션에 식별자가 해소되지 않은 경우에만 호출 → "
        "결과 identifiers를 search.detail의 identifiers 인자로 넘겨 단건 조회."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rank": {"type": "integer", "minimum": 1},
        },
        "required": ["rank"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string", "enum": ["project", "perf", "people", "org"]},
            "identifiers": {"type": "object", "description": "axis별 string list"},
            "title": {"type": "string"},
            "doc_type": {"type": ["string", "null"]},
        },
    },
    cost_hint="fast",
    preconditions=["manifest_required"],
)


async def manifest_get_item_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    rank = args.get("rank")
    try:
        rank_int = int(rank)
    except (TypeError, ValueError):
        raise ValueError(f"manifest.get_item: 'rank' must be int, got {rank!r}")
    if rank_int < 1:
        raise ValueError(f"manifest.get_item: 'rank' must be >= 1, got {rank_int}")

    session = ctx.session_state
    if session is None or not _has_manifest(session):
        raise RuntimeError("manifest.get_item: session has no published_manifest")

    snapshot = session.published_manifest.snapshot
    items = list(snapshot.items or [])
    if rank_int > len(items):
        raise ValueError(
            f"manifest.get_item: rank={rank_int} out of range (manifest has {len(items)} items)"
        )

    item = items[rank_int - 1]
    target = _infer_target_from_item(item, default=snapshot.context_kind)
    identifiers = _identifiers_from_item(item)
    return {
        "target": target,
        "identifiers": identifiers,
        "title": item.title_text or "",
        "doc_type": item.doc_type,
        "entity_kind": item.entity_kind,
        "display_rank": item.display_rank,
    }


# ============================================================================
# manifest.filter
# ============================================================================

MANIFEST_FILTER_SPEC = ToolSpec(
    name="manifest.filter",
    description=(
        "직전 manifest의 모든 항목 식별자를 axis별로 수집. 사용자가 'X 관련만 골라줘' 같은 "
        "manifest 부분집합 의도일 때 PlannerAgent가 호출 → 결과 identifiers를 "
        "search.detail의 identifiers 인자로 넘겨 재조회 후 AnswerAgent가 자연어 reranking."
    ),
    input_schema={
        "type": "object",
        "properties": {},
    },
    output_schema={
        "type": "object",
        "properties": {
            "target": {"type": "string"},
            "identifiers": {"type": "object", "description": "axis별 dedup된 list"},
            "item_count": {"type": "integer"},
        },
    },
    cost_hint="fast",
    preconditions=["manifest_required"],
)


async def manifest_filter_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    session = ctx.session_state
    if session is None or not _has_manifest(session):
        raise RuntimeError("manifest.filter: session has no published_manifest")

    snapshot = session.published_manifest.snapshot
    items = list(snapshot.items or [])
    if not items:
        return {
            "target": snapshot.context_kind,
            "identifiers": {},
            "item_count": 0,
        }

    target = snapshot.context_kind or _infer_target_from_item(items[0], default="project")
    aggregated: Dict[str, List[str]] = {
        "pjt_id": [], "pjt_no": [], "rst_id": [], "person_no": [], "org_id": [],
    }
    seen: Dict[str, set] = {k: set() for k in aggregated}
    for item in items:
        for axis in aggregated.keys():
            value = (getattr(item, axis, None) or "").strip()
            if value and value not in seen[axis]:
                aggregated[axis].append(value)
                seen[axis].add(value)
    # 빈 axis는 dict에서 제거 — Planner prompt에 깔끔히 노출
    identifiers = {k: v for k, v in aggregated.items() if v}
    return {
        "target": target,
        "identifiers": identifiers,
        "item_count": len(items),
    }


# ============================================================================
# Helpers
# ============================================================================

_PERF_TAGS = {
    "IRD_NAI_RI_PAPER", "IRD_NAI_RI_IPR", "IRD_NAI_RI_SW", "IRD_NAI_RI_NVR",
    "IRD_NAI_RI_ORGSM_INFO", "IRD_NAI_RI_ORGSM_RESOURCE", "IRD_NAI_RI_COMPOUND",
    "IRD_NAI_RI_RSCH_RPT", "IRD_NAI_RI_FCLT_EQUIP", "IRD_NAI_RI_TECH_INFO",
}
_PROJECT_TAGS = {"IRD_NAI_PJT_INFO"}


def _has_manifest(session: Any) -> bool:
    pm = getattr(session, "published_manifest", None)
    if pm is None:
        return False
    snapshot = getattr(pm, "snapshot", None)
    return snapshot is not None and bool(getattr(snapshot, "items", None))


def _infer_target_from_item(item: Any, default: str = "project") -> str:
    doc_type = (getattr(item, "doc_type", "") or "").strip()
    if doc_type in _PERF_TAGS:
        return "perf"
    if doc_type in _PROJECT_TAGS:
        return "project"
    entity_kind = (getattr(item, "entity_kind", "") or "").strip().lower()
    if entity_kind in {"project", "perf", "people", "org"}:
        return entity_kind
    return default or "project"


def _identifiers_from_item(item: Any) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
        value = (getattr(item, axis, None) or "").strip()
        if value:
            out[axis] = [value]
    return out


# ============================================================================
# Registry entries
# ============================================================================

def manifest_tool_entries() -> List[ToolEntry]:
    return [
        ToolEntry(spec=MANIFEST_GET_ITEM_SPEC, handler=manifest_get_item_handler),
        ToolEntry(spec=MANIFEST_FILTER_SPEC, handler=manifest_filter_handler),
    ]
