"""Phase 1 — retrieval 도구 wrap.

기존 `SearchAgent.execute(task)`를 도구로 노출:
    - search.hybrid    : query + (subject) → hybrid_search 또는 subject_anchor
    - search.exact_lookup : identifiers → exact_lookup
    - search.aggregate : aggregate_by → 통계 집계

각 도구는 SearchTask를 빌드해 SearchAgent.execute로 위임. SearchAgent의 SearchResult를
ToolExecutor가 처리하기 쉽게 dict로 변환해 반환.

설계 노트:
    - args는 도구별로 강하게 typed 하지 않고 dict — Planner LLM이 input_schema를 보고 채움.
    - 도구 handler 내부에서 args 검증·기본값 fallback. 잘못된 args는 ValueError로 던지면
      ToolExecutor가 Observation(error_code="invalid_arg")로 감싼다.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from apps.pipeline.contracts import (
    FilterBundle,
    IdentifierBundle,
    SubjectAnchor,
    build_search_task,
)
from apps.pipeline.tools.contracts import ToolContext, ToolEntry, ToolSpec


# ============================================================================
# search.hybrid
# ============================================================================

SEARCH_HYBRID_SPEC = ToolSpec(
    name="search.hybrid",
    description=(
        "NTIS Qdrant 컬렉션에서 hybrid(dense+sparse) 검색. subject(person/org)가 주어지면 "
        "anchor를 강제 필터로 적용하고, 없으면 일반 hybrid 검색."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "자연어 검색 질의"},
            "target": {
                "type": "string",
                "enum": ["project", "perf", "people", "org", "support"],
                "description": "검색 대상 도메인 (collection 자동 결정)",
            },
            "subject": {
                "type": ["object", "null"],
                "description": "{kind:'people'|'org', display_name:..., person_no?:..., org_id?:...}",
            },
            "filters": {
                "type": ["object", "null"],
                "description": (
                    "{year_from?, year_to?, perf_type?:[PAPER|PATENT|SOFTWARE|...], "
                    "lead_org_name?:[...], participant_org_name?:[...], "
                    "participant_person_name?:[...] (subject 외 공동 참여자 인명 AND 매칭), "
                    "domain_keywords?:[...], "
                    "exclude_org_name?:[...], exclude_perf_type?:[...], exclude_person_name?:[...]}"
                ),
            },
            "limit": {"type": "integer", "default": 10},
            "sort_by": {
                "type": "string",
                "enum": ["relevance", "recent_desc", "recent_asc"],
                "default": "relevance",
            },
        },
        "required": ["query", "target"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["single", "multiple", "empty", "error"]},
            "evidences": {"type": "array", "description": "CanonicalEvidence dict 리스트"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="medium",
    preconditions=[],
)


async def search_hybrid_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("search.hybrid: 'query' is required")
    target = str(args.get("target") or "project").strip()
    if target not in {"project", "perf", "people", "org", "support"}:
        raise ValueError(
            f"search.hybrid: invalid 'target' = {target!r}. "
            "유효값: project | perf | people | org | support"
        )

    subject = _parse_subject(args.get("subject"))
    filters = _parse_filters(args.get("filters"))
    limit = int(args.get("limit") or 10)
    sort_by = str(args.get("sort_by") or "relevance")
    if sort_by not in {"relevance", "recent_desc", "recent_asc"}:
        sort_by = "relevance"

    task = build_search_task(
        action="list",
        target=target,  # type: ignore[arg-type]
        request_id=ctx.request_id,
        turn_id=ctx.turn_id,
        retrieval_query=query,
        subject=subject,
        filters=filters,
        limit=limit,
        display_limit=limit,
        sort_by=sort_by,  # type: ignore[arg-type]
        judgment_reason="tool:search.hybrid",
    )
    if ctx.search_agent is None:
        raise RuntimeError("search.hybrid: ctx.search_agent is None — wire via ToolContext")

    result = await ctx.search_agent.execute(task)
    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "diagnostics": dict(result.diagnostics or {}),
    }


# ============================================================================
# search.exact_lookup
# ============================================================================

SEARCH_EXACT_LOOKUP_SPEC = ToolSpec(
    name="search.exact_lookup",
    description=(
        "식별자(pjt_id/pjt_no/rst_id/person_no/org_id)로 NTIS payload 정확 매칭. "
        "단건 detail 조회·manifest 부분집합 재조회에 사용."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "identifiers": {
                "type": "object",
                "description": (
                    "{pjt_id?:[...], pjt_no?:[...], rst_id?:[...], person_no?:[...], org_id?:[...]} — "
                    "axis별 string list. 비어 있지 않은 axis 중 가장 우선순위 높은 것 사용."
                ),
            },
            "target": {
                "type": "string",
                "enum": ["project", "perf", "people", "org", "support"],
                "description": "타깃 도메인. 식별자 type으로 추론 가능 (rst_id→perf, pjt_id→project).",
            },
            "limit": {"type": "integer", "default": 10},
            "action": {
                "type": "string",
                "enum": ["list", "detail"],
                "default": "list",
                "description": "detail이면 단건 반환, list면 매칭 전체.",
            },
        },
        "required": ["identifiers"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "evidences": {"type": "array"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)


async def search_exact_lookup_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    raw_ids = args.get("identifiers") or {}
    if not isinstance(raw_ids, dict) or not any(raw_ids.values()):
        raise ValueError("search.exact_lookup: 'identifiers' must be non-empty dict")

    identifiers = IdentifierBundle(
        pjt_id=_clean_list(raw_ids.get("pjt_id")),
        pjt_no=_clean_list(raw_ids.get("pjt_no")),
        rst_id=_clean_list(raw_ids.get("rst_id")),
        person_no=_clean_list(raw_ids.get("person_no")),
        org_id=_clean_list(raw_ids.get("org_id")),
    )
    if not identifiers.has_any():
        raise ValueError("search.exact_lookup: identifiers all empty after cleanup")

    target = str(args.get("target") or _infer_target_from_ids(identifiers)).strip()
    if target not in {"project", "perf", "people", "org", "support"}:
        target = "project"
    limit = int(args.get("limit") or 10)
    action = str(args.get("action") or "list")
    if action not in {"list", "detail"}:
        action = "list"

    task = build_search_task(
        action=action,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        request_id=ctx.request_id,
        turn_id=ctx.turn_id,
        identifiers=identifiers,
        limit=limit,
        display_limit=limit,
        judgment_reason="tool:search.exact_lookup",
    )
    if ctx.search_agent is None:
        raise RuntimeError("search.exact_lookup: ctx.search_agent is None")

    result = await ctx.search_agent.execute(task)
    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "diagnostics": dict(result.diagnostics or {}),
    }


# ============================================================================
# search.aggregate
# ============================================================================

SEARCH_AGGREGATE_SPEC = ToolSpec(
    name="search.aggregate",
    description=(
        "NTIS payload를 stats 도구로 집계. aggregate_by 축(year/lead_org/tag/perf_type/"
        "participant_org/participant_person)으로 그룹 count 반환."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "aggregate_by": {
                "type": "string",
                "enum": ["year", "lead_org", "tag", "perf_type", "participant_org", "participant_person"],
            },
            "target": {"type": "string", "enum": ["project", "perf", "people", "org"]},
            "subject": {"type": ["object", "null"]},
            "filters": {"type": ["object", "null"]},
            "limit": {"type": "integer", "default": 30},
        },
        "required": ["aggregate_by", "target"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "evidences": {"type": "array", "description": "그룹별 count (CanonicalEvidence dict)"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="slow",
    preconditions=[],
)


async def search_aggregate_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    aggregate_by = str(args.get("aggregate_by") or "").strip()
    if aggregate_by not in {"year", "lead_org", "tag", "perf_type", "participant_org", "participant_person"}:
        raise ValueError(f"search.aggregate: invalid aggregate_by={aggregate_by!r}")
    target = str(args.get("target") or "project").strip()
    if target not in {"project", "perf", "people", "org"}:
        raise ValueError(f"search.aggregate: invalid target={target!r}")

    subject = _parse_subject(args.get("subject"))
    filters = _parse_filters(args.get("filters"))
    limit = int(args.get("limit") or 30)

    task = build_search_task(
        action="stats",
        target=target,  # type: ignore[arg-type]
        request_id=ctx.request_id,
        turn_id=ctx.turn_id,
        subject=subject,
        filters=filters,
        aggregate_by=aggregate_by,  # type: ignore[arg-type]
        limit=limit,
        display_limit=limit,
        judgment_reason="tool:search.aggregate",
    )
    if ctx.search_agent is None:
        raise RuntimeError("search.aggregate: ctx.search_agent is None")

    result = await ctx.search_agent.execute(task)
    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "diagnostics": dict(result.diagnostics or {}),
    }


# ============================================================================
# Helpers
# ============================================================================

def _clean_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        seen = set()
        for v in value:
            s = str(v).strip()
            if s and s not in seen:
                out.append(s)
                seen.add(s)
        return out
    return []


def _parse_subject(value: Any) -> Optional[SubjectAnchor]:
    if not isinstance(value, dict) or not value.get("display_name"):
        return None
    kind = str(value.get("kind") or "people").strip()
    if kind not in {"people", "org"}:
        kind = "people"
    return SubjectAnchor(
        kind=kind,  # type: ignore[arg-type]
        display_name=str(value.get("display_name")).strip(),
        person_no=str(value.get("person_no") or "").strip() or None,
        org_id=str(value.get("org_id") or "").strip() or None,
        org_code=str(value.get("org_code") or "").strip() or None,
        biz_no=str(value.get("biz_no") or "").strip() or None,
        affiliation_org_name=str(value.get("affiliation_org_name") or "").strip() or None,
        identity_status=(
            str(value.get("identity_status") or "ambiguous_name_only").strip()
            if value.get("identity_status") in {"ambiguous_name_only", "resolved_with_org", "resolved"}
            else "ambiguous_name_only"
        ),  # type: ignore[arg-type]
    )


def _parse_filters(value: Any) -> Optional[FilterBundle]:
    """tool args의 filters dict → FilterBundle.

    2026-05-27 정합화: 사용자 짚은 계약 불일치 — Phase 1에서 `coparticipants` 키를 받았으나
    `FilterBundle`은 `participant_person_name`이 정식 필드. 같은 키 별칭으로 받아 매핑하고
    `lead_org_name`/`participant_org_name`/`domain_keywords`도 노출.
    """
    if not isinstance(value, dict):
        return None
    # 공동 참여자 인명 — 별칭 호환 (coparticipants는 DialogueIntent 측 명명, 도구는 둘 다 수용).
    participant_persons = _clean_list(value.get("participant_person_name")) or _clean_list(
        value.get("coparticipants")
    )
    return FilterBundle(
        year_from=_to_int_or_none(value.get("year_from")),
        year_to=_to_int_or_none(value.get("year_to")),
        lead_org_name=_clean_list(value.get("lead_org_name")),
        participant_org_name=_clean_list(value.get("participant_org_name")),
        participant_person_name=participant_persons,
        perf_type=[s.upper() for s in _clean_list(value.get("perf_type"))],
        domain_keywords=_clean_list(value.get("domain_keywords")),
        exclude_org_name=_clean_list(value.get("exclude_org_name")),
        exclude_perf_type=[s.upper() for s in _clean_list(value.get("exclude_perf_type"))],
        exclude_person_name=_clean_list(value.get("exclude_person_name")),
    )


def _to_int_or_none(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    if v < 1900 or v > 2100:
        return None
    return v


def _infer_target_from_ids(ids: IdentifierBundle) -> str:
    if ids.rst_id:
        return "perf"
    if ids.person_no:
        return "people"
    if ids.org_id:
        return "org"
    return "project"


# ============================================================================
# ADR-0022: SearchAgent 자동 라우팅 도구 (Planner에 DB 스키마 노출 제거)
#
# 이전 도구(search.hybrid / search.exact_lookup / search.aggregate)는 Planner에
# target enum / perf_type enum / aggregate_by enum 같은 DB 내부 개념을 노출했다.
# Planner가 'target="research"' 같은 잘못된 값을 생성하는 근본 원인이었다.
#
# 새 도구는 query (+ 의미적 hint만) 받아 컬렉션·전략·필터를 SearchRouter가 자동 결정한다.
# Planner 인터페이스:
#   search(query, hint?)        — 일반 검색
#   search.detail(query?)       — 단건 상세 (세션 컨텍스트 자동 사용)
#   search.stats(query, axis?)  — 통계 집계
# ============================================================================


# ----------------------------------------------------------------------------
# SearchRouter — ctx에서 라우팅 컨텍스트 읽어 target / subject / filters 결정
# ----------------------------------------------------------------------------

# Target → Qdrant 컬렉션명 매핑 (Planner에 노출 안 함 — SearchRouter 내부 전용)
_TARGET_TO_COLLECTION: Dict[str, str] = {
    "project": "ntis_project_v1",
    "perf":    "ntis_perf_v1",
    "support": "ntis_supports",
    "people":  "ntis_project_v1",  # anchor 검색
    "org":     "ntis_project_v1",  # anchor 검색
}


def _is_multi_collection_candidate(ctx: ToolContext) -> bool:
    """forced_target/subject/identifiers가 모두 없을 때 project+perf 병렬 검색 후보.

    topic 쿼리("LLM 연구동향" 등)는 도메인 힌트가 없어 project만 검색하면 perf(논문/특허) 누락.
    이 경우 두 컬렉션을 병렬 검색해 결과를 통합한다.
    """
    er = getattr(ctx, "entity_resolution", None)
    if er is None:
        return True
    if getattr(er, "forced_target", None):
        return False
    if getattr(er, "subject", None):
        return False  # 인물/기관 anchor이 있으면 단일 컬렉션 집중
    ids = getattr(er, "identifiers", None)
    if ids is not None and ids.has_any():
        return False  # 식별자가 있으면 단일 컬렉션
    return True


def _build_applied_context(
    *,
    targets: List[str],
    subject: Optional["SubjectAnchor"],
    filters: Optional["FilterBundle"],
    sort_by: str,
    multi_collection: bool = False,
) -> Dict[str, Any]:
    """Planner observation에 노출할 검색 실행 컨텍스트 요약."""
    ctx_out: Dict[str, Any] = {
        "collections_searched": [_TARGET_TO_COLLECTION.get(t, t) for t in targets],
        "multi_collection": multi_collection,
        "sort_by": sort_by,
    }
    if subject:
        ctx_out["subject_anchor"] = getattr(subject, "display_name", None)
    if filters:
        applied: Dict[str, Any] = {}
        for field in ("year_from", "year_to", "perf_type", "lead_org_name",
                      "participant_person_name", "exclude_org_name", "exclude_perf_type"):
            val = getattr(filters, field, None)
            if val:
                applied[field] = val
        if applied:
            ctx_out["filters_applied"] = applied
    return ctx_out


async def _run_single_search(
    *,
    query: str,
    target: str,
    subject: Optional["SubjectAnchor"],
    filters: Optional["FilterBundle"],
    limit: int,
    sort_by: str,
    ctx: ToolContext,
    judgment_reason: str = "tool:search",
) -> "Any":
    """단일 컬렉션 hybrid search 실행."""
    task = build_search_task(
        action="list",
        target=target,  # type: ignore[arg-type]
        request_id=ctx.request_id,
        turn_id=ctx.turn_id,
        retrieval_query=query,
        subject=subject,
        filters=filters,
        limit=limit,
        display_limit=limit,
        sort_by=sort_by,  # type: ignore[arg-type]
        judgment_reason=judgment_reason,
    )
    return await ctx.search_agent.execute(task)


def _merge_multi_results(results: List[Any], limit: int) -> Any:
    """두 컬렉션 결과를 score 기준 병합·정렬·탈중복."""
    from apps.pipeline.contracts import SearchResult

    all_evidences = []
    seen_ids: set = set()
    total = 0

    for result in results:
        if result is None or result.status == "error":
            continue
        total += int(result.total_hits or 0)
        for ev in (result.evidences or []):
            # 식별자 기준 중복 제거
            dedup_key = (
                (ev.ids.get("pjt_id") or ev.ids.get("pjt_no") or "")
                + (ev.ids.get("rst_id") or "")
            ) if ev.ids else str(id(ev))
            if dedup_key and dedup_key in seen_ids:
                continue
            if dedup_key:
                seen_ids.add(dedup_key)
            all_evidences.append(ev)

    # score 내림차순 정렬
    all_evidences.sort(key=lambda e: getattr(e, "score", 0.0), reverse=True)
    top = all_evidences[:limit]

    if not top:
        return SearchResult(status="empty", evidences=[], total_hits=0)
    status = "single" if len(top) == 1 else "multiple"
    return SearchResult(status=status, evidences=top, total_hits=total)


def _route_from_ctx(ctx: ToolContext):
    """ToolContext의 entity_resolution + dialogue_kind → (target, subject, filters).

    Planner가 DB 스키마를 알 필요 없도록 라우팅 판단을 전담한다.
    우선순위:
        1. entity_resolution.forced_target → 해당 컬렉션
        2. entity_resolution.subject.kind=people/org → project (anchor 검색)
        3. entity_resolution.identifiers에 rst_id → perf
        4. 그 외 → project (가장 일반적인 R&D 쿼리 기본값)
    """
    er = getattr(ctx, "entity_resolution", None)

    # target 결정
    ids = getattr(er, "identifiers", None) if er is not None else None
    if er is not None and er.forced_target:
        target = er.forced_target
    elif ids is not None and getattr(ids, "rst_id", None):
        target = "perf"
    elif ids is not None and getattr(ids, "person_no", None):
        target = "people"
    elif ids is not None and getattr(ids, "org_id", None):
        target = "org"
    elif ids is not None and (getattr(ids, "pjt_id", None) or getattr(ids, "pjt_no", None)):
        target = "project"
    else:
        target = "project"

    # subject anchor
    subject: Optional[SubjectAnchor] = None
    if er is not None and er.subject is not None:
        sub = er.subject
        subject = SubjectAnchor(
            kind=sub.kind,  # type: ignore[arg-type]
            display_name=getattr(sub, "display_name", "") or getattr(sub, "subject_name", ""),
            person_no=getattr(sub, "person_no", None),
            org_id=getattr(sub, "org_id", None),
            org_code=getattr(sub, "org_code", None),
            biz_no=getattr(sub, "biz_no", None),
            affiliation_org_name=getattr(sub, "affiliation_org_name", None),
            identity_status=getattr(sub, "identity_status", "ambiguous_name_only"),  # type: ignore[arg-type]
        )

    # filters from EntityResolution
    filters: Optional[FilterBundle] = None
    if er is not None and er.filters is not None:
        filters = er.filters

    return target, subject, filters


def _infer_aggregate_by(axis: Optional[str]) -> str:
    """의미적 axis 힌트 → 내부 aggregate_by 값.

    Planner는 "year"·"org"·"type"·null만 사용한다.
    실제 DB 집계 축("lead_org", "perf_type" 등)은 여기서 매핑한다.
    """
    _MAP = {
        "year": "year",
        "org": "lead_org",
        "type": "tag",       # project은 tag, perf는 perf_type — 기본 tag 사용
        "participant_org": "participant_org",
        "participant_person": "participant_person",
        # 내부값 직접 전달 허용 (lookup.* 도구가 ctx 통해 사용하는 경로)
        "lead_org": "lead_org",
        "tag": "tag",
        "perf_type": "perf_type",
    }
    return _MAP.get(str(axis or "").strip().lower(), "year")


# ----------------------------------------------------------------------------
# 새 도구 1: search
# ----------------------------------------------------------------------------

SEARCH_SPEC = ToolSpec(
    name="search",
    description=(
        "NTIS R&D 데이터 검색. query만 입력하면 컬렉션·전략·필터를 자동 결정한다. "
        "일반 검색·인물 활동내역·기관 활동내역·주제 검색 등 모든 목록 조회에 사용."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "자연어 검색 질의"},
            "hint": {
                "type": ["string", "null"],
                "enum": ["recent", "brief", None],
                "description": "선택적 힌트: recent=최신순 정렬, brief=간결 결과, null=기본",
            },
            "limit": {"type": "integer", "description": "반환 건수 (기본 10)", "default": 10},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "evidences": {"type": "array"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="medium",
    preconditions=[],
)


async def search_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("search: 'query' is required")
    hint = str(args.get("hint") or "").strip().lower() or None
    limit = int(args.get("limit") or 10)
    sort_by = "recent_desc" if hint == "recent" else "relevance"

    if ctx.search_agent is None:
        raise RuntimeError("search: ctx.search_agent is None")

    target, subject, filters = _route_from_ctx(ctx)

    # 도메인 힌트 없음 → project + perf 병렬 검색 후 통합 (topic 쿼리 커버리지 향상)
    if _is_multi_collection_candidate(ctx):
        results = await asyncio.gather(
            _run_single_search(
                query=query, target="project", subject=subject, filters=filters,
                limit=limit, sort_by=sort_by, ctx=ctx, judgment_reason="tool:search/project",
            ),
            _run_single_search(
                query=query, target="perf", subject=subject, filters=filters,
                limit=limit, sort_by=sort_by, ctx=ctx, judgment_reason="tool:search/perf",
            ),
            return_exceptions=True,
        )
        valid = [r for r in results if not isinstance(r, Exception)]
        result = _merge_multi_results(valid, limit)
        applied_ctx = _build_applied_context(
            targets=["project", "perf"], subject=subject, filters=filters,
            sort_by=sort_by, multi_collection=True,
        )
    else:
        result = await _run_single_search(
            query=query, target=target, subject=subject, filters=filters,
            limit=limit, sort_by=sort_by, ctx=ctx,
        )
        applied_ctx = _build_applied_context(
            targets=[target], subject=subject, filters=filters, sort_by=sort_by,
        )

    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "applied_context": applied_ctx,
        "diagnostics": dict(result.diagnostics or {}),
    }


# ----------------------------------------------------------------------------
# 새 도구 2: search.detail
# ----------------------------------------------------------------------------

SEARCH_DETAIL_SPEC = ToolSpec(
    name="search.detail",
    description=(
        "세션 컨텍스트(EntityResolution 식별자 또는 manifest 인용)의 단건 상세 조회. "
        "사용자가 특정 과제·성과를 지목했을 때 사용. 식별자는 세션에서 자동 읽힌다."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": ["string", "null"],
                "description": "보조 검색어 (세션에 식별자가 없을 때 fallback용, 보통 null)",
            },
        },
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "evidences": {"type": "array"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)


async def search_detail_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    er = getattr(ctx, "entity_resolution", None)

    # 세션의 식별자 우선
    identifiers: Optional[IdentifierBundle] = None
    if er is not None and er.identifiers is not None and er.identifiers.has_any():
        identifiers = er.identifiers

    # 식별자 없으면 query fallback
    query = str(args.get("query") or "").strip() or None

    if identifiers is None and query is None:
        raise ValueError("search.detail: 세션에 식별자도 없고 query도 없음 — 상세 조회 불가")

    target, subject, filters = _route_from_ctx(ctx)

    if identifiers is not None:
        task = build_search_task(
            action="detail",
            target=target,  # type: ignore[arg-type]
            request_id=ctx.request_id,
            turn_id=ctx.turn_id,
            identifiers=identifiers,
            limit=1,
            display_limit=1,
            judgment_reason="tool:search.detail",
        )
    else:
        task = build_search_task(
            action="detail",
            target=target,  # type: ignore[arg-type]
            request_id=ctx.request_id,
            turn_id=ctx.turn_id,
            retrieval_query=query,
            subject=subject,
            filters=filters,
            limit=1,
            display_limit=1,
            judgment_reason="tool:search.detail(query_fallback)",
        )
    if ctx.search_agent is None:
        raise RuntimeError("search.detail: ctx.search_agent is None")

    result = await ctx.search_agent.execute(task)
    applied_ctx = _build_applied_context(
        targets=[target], subject=subject, filters=filters, sort_by="relevance",
    )
    if identifiers is not None:
        applied_ctx["identifier_used"] = True
    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "applied_context": applied_ctx,
        "diagnostics": dict(result.diagnostics or {}),
    }


# ----------------------------------------------------------------------------
# 새 도구 3: search.stats
# ----------------------------------------------------------------------------

SEARCH_STATS_SPEC = ToolSpec(
    name="search.stats",
    description=(
        "NTIS 데이터 통계·집계. '연도별', '기관별', '유형별' 등 집계 질문에 사용. "
        "axis는 의미 단위로만 지정 (year/org/type). 내부 집계 전략은 자동 결정."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "집계 기준 검색어"},
            "axis": {
                "type": ["string", "null"],
                "enum": ["year", "org", "type", None],
                "description": "집계 축: year=연도별, org=기관별, type=유형·분야별, null=자동(연도)",
            },
            "limit": {"type": "integer", "description": "그룹 반환 수 (기본 30)", "default": 30},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "evidences": {"type": "array"},
            "total_hits": {"type": "integer"},
        },
    },
    cost_hint="slow",
    preconditions=[],
)


async def search_stats_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ValueError("search.stats: 'query' is required")
    axis_hint = args.get("axis")
    aggregate_by = _infer_aggregate_by(axis_hint)
    limit = int(args.get("limit") or 30)

    target, subject, filters = _route_from_ctx(ctx)

    task = build_search_task(
        action="stats",
        target=target,  # type: ignore[arg-type]
        request_id=ctx.request_id,
        turn_id=ctx.turn_id,
        retrieval_query=query,
        subject=subject,
        filters=filters,
        aggregate_by=aggregate_by,  # type: ignore[arg-type]
        limit=limit,
        display_limit=limit,
        judgment_reason="tool:search.stats",
    )
    if ctx.search_agent is None:
        raise RuntimeError("search.stats: ctx.search_agent is None")

    result = await ctx.search_agent.execute(task)
    applied_ctx = _build_applied_context(
        targets=[target], subject=subject, filters=filters, sort_by="relevance",
    )
    applied_ctx["aggregate_by"] = aggregate_by
    return {
        "status": result.status,
        "evidences": [ev.model_dump() for ev in (result.evidences or [])],
        "total_hits": int(result.total_hits or 0),
        "applied_context": applied_ctx,
        "diagnostics": dict(result.diagnostics or {}),
    }


# ============================================================================
# Registry entries
# ============================================================================

def retrieval_tool_entries() -> List[ToolEntry]:
    """ADR-0022: 새 SearchRouter 기반 도구 3개.

    구 도구(search.hybrid / search.exact_lookup / search.aggregate)는 제거.
    Planner는 target/collection 같은 DB 스키마를 알 필요 없다.
    """
    return [
        ToolEntry(spec=SEARCH_SPEC, handler=search_handler),
        ToolEntry(spec=SEARCH_DETAIL_SPEC, handler=search_detail_handler),
        ToolEntry(spec=SEARCH_STATS_SPEC, handler=search_stats_handler),
    ]
