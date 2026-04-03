from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CollectionFilterPolicyContext:
    """컬렉션별 서버 필터 결정을 위해 필요한 상태를 한곳에 모은 입력 객체다."""
    mode: str
    base_route: str
    relation: Optional[Tuple[str, str]]
    relation_lookup_enforce: bool
    lookup_filter_enabled: bool
    title_filter_server_applied: bool
    planner_org_filter_present: bool
    org_role: Optional[str]
    org_terms: List[str]
    people_terms: List[str]
    people_ids: List[str]
    has_perf_ids: bool
    pjt_ids: List[str]
    pjt_nos: List[str]
    people_filter: Any
    participant_org_filter: Any
    org_filter: Any
    title_filter: Any
    project_tag_filter: Any
    perf_tag_filter: Any
    perf_type_filter: Any
    year_range_filter: Any
    perf_followup_filter: Any
    col_project: str
    col_perf: str


def resolve_collection_server_filter(
    *,
    col: str,
    context: CollectionFilterPolicyContext,
    get_relation_route: Callable[[Tuple[str, str]], Any],
    build_tag_only_filter: Callable[[List[str]], Any],
    and_filter: Callable[[Any, Any], Any],
    build_project_id_filter: Callable[[List[str], List[str]], Any],
    with_org_must_gate: Callable[[Any], Any],
    log_lookup_ids_empty: Callable[..., None],
    log_project_key_filter: Callable[..., None],
) -> Any:
    """현재 컬렉션에 적용할 서버 필터를 mode/base_route/relation 기준으로 결정한다.

    lookup, relation enforcement, key filter, 사람·기관·연도·성과 유형 필터를 순서 있게 합쳐 컬렉션마다 다른 의미를 보존한다.
    """
    lookup_has_ids = bool(context.pjt_ids or context.pjt_nos or context.has_perf_ids)
    lookup_has_name_filters = bool(context.people_terms or context.people_ids or context.org_terms)
    apply_name_filters = context.mode != "lookup" or lookup_has_ids or lookup_has_name_filters

    def _relation_lookup_filter_for_col() -> Any:
        """relation lookup 강제가 켜진 경우 relation route에 맞는 tag/name gate를 만든다."""
        if not context.relation_lookup_enforce or not context.relation:
            return None
        route = get_relation_route(context.relation)
        if route is None:
            return None
        if col == route.hop1_col:
            tag_filters_local = route.hop1_tag_filters
        elif col == route.hop2_col:
            tag_filters_local = route.hop2_tag_filters
        else:
            return None
        base_filter = build_tag_only_filter(tag_filters_local) if tag_filters_local else None
        relation_parts = set(route.relation)
        if col == context.col_project:
            if "people" in relation_parts and context.people_filter and apply_name_filters:
                base_filter = and_filter(base_filter, context.people_filter)
            if "org" in relation_parts and (context.participant_org_filter or context.org_filter) and apply_name_filters:
                base_filter = and_filter(base_filter, context.participant_org_filter or context.org_filter)
        return base_filter

    def _build_soft_filter_for_col() -> Any:
        """ID가 없을 때도 lookup에 보조적으로 붙일 soft filter를 컬렉션별로 조립한다."""
        base_filter = None
        if col == context.col_project:
            if context.title_filter_server_applied:
                base_filter = and_filter(base_filter, context.title_filter)
            if apply_name_filters and (context.people_filter or context.participant_org_filter or context.org_filter):
                tag_filter_local = build_tag_only_filter(["IRD_NAI_PJT_INFO"])
                base_filter = and_filter(base_filter, tag_filter_local)
            if apply_name_filters and context.people_filter:
                base_filter = and_filter(base_filter, context.people_filter)
            if apply_name_filters and (context.participant_org_filter or context.org_filter):
                base_filter = and_filter(base_filter, context.participant_org_filter or context.org_filter)
            if context.project_tag_filter:
                base_filter = and_filter(base_filter, context.project_tag_filter)
        elif col == context.col_perf:
            if context.title_filter_server_applied:
                base_filter = and_filter(base_filter, context.title_filter)
            if context.people_filter and apply_name_filters:
                base_filter = and_filter(base_filter, context.people_filter)
            if apply_name_filters and (context.participant_org_filter or context.org_filter):
                base_filter = and_filter(base_filter, context.participant_org_filter or context.org_filter)
            if context.perf_tag_filter:
                base_filter = and_filter(base_filter, context.perf_tag_filter)
        return base_filter

    def _apply_extra_filters(base_filter: Any) -> Any:
        """연도, perf type, perf followup 같은 공통 후처리 필터를 마지막에 덧붙인다."""
        relation_filter = _relation_lookup_filter_for_col()
        combined = and_filter(relation_filter, base_filter) if relation_filter else base_filter
        if col in (context.col_project, context.col_perf) and context.year_range_filter:
            combined = and_filter(combined, context.year_range_filter)
        if col == context.col_perf and context.perf_type_filter:
            combined = and_filter(combined, context.perf_type_filter)
        if col == context.col_perf and context.perf_followup_filter:
            combined = and_filter(combined, context.perf_followup_filter) if combined else context.perf_followup_filter
        return combined

    if context.mode != "lookup":
        return None

    base_filter_lookup = _build_soft_filter_for_col() if context.lookup_filter_enabled else None
    project_key_filter_type = "pjt_id" if context.pjt_ids else ("pjt_no" if context.pjt_nos else None)
    if context.relation and not (context.pjt_ids or context.pjt_nos or context.has_perf_ids):
        log_lookup_ids_empty(
            ids_map={"pjt_id": list(context.pjt_ids), "pjt_no": list(context.pjt_nos)},
            pjt_id=context.pjt_ids,
            pjt_no=context.pjt_nos,
            relation=context.relation,
            perf_ids=context.has_perf_ids,
            project_key_filter_type=project_key_filter_type,
        )

    pjt_filter = build_project_id_filter(context.pjt_ids, context.pjt_nos)
    if pjt_filter is not None:
        log_project_key_filter(
            project_key_filter_type=project_key_filter_type,
            pjt_id_count=len(context.pjt_ids),
            pjt_no_count=len(context.pjt_nos),
        )
        combined = and_filter(pjt_filter, base_filter_lookup) if base_filter_lookup else pjt_filter
        return _apply_extra_filters(with_org_must_gate(combined))

    if col == context.col_perf and context.perf_tag_filter:
        combined = and_filter(context.perf_tag_filter, base_filter_lookup) if base_filter_lookup else context.perf_tag_filter
        return _apply_extra_filters(with_org_must_gate(combined))

    if col == context.col_project and context.relation == ("people", "project") and context.people_filter and lookup_has_ids:
        tag_filter_local = build_tag_only_filter(["IRD_NAI_PJT_INFO"])
        combined = and_filter(and_filter(tag_filter_local, context.people_filter), base_filter_lookup) if base_filter_lookup else and_filter(tag_filter_local, context.people_filter)
        return _apply_extra_filters(with_org_must_gate(combined))

    if col == context.col_project and context.org_terms and context.base_route not in ("project", "org", "people") and lookup_has_ids:
        if context.org_role == "participant":
            combined = and_filter(context.participant_org_filter or context.org_filter, base_filter_lookup) if base_filter_lookup else (context.participant_org_filter or context.org_filter)
            return _apply_extra_filters(with_org_must_gate(combined))
        if context.org_filter:
            combined = and_filter(context.org_filter, base_filter_lookup) if base_filter_lookup else context.org_filter
            return _apply_extra_filters(with_org_must_gate(combined))

    if col == context.col_project:
        if context.base_route == "people":
            tag_filter_local = build_tag_only_filter(["IRD_NAI_PJT_INFO"])
            base_filter = tag_filter_local
            if context.people_filter:
                base_filter = and_filter(base_filter, context.people_filter)
            if context.participant_org_filter or context.org_filter:
                base_filter = and_filter(base_filter, context.participant_org_filter or context.org_filter)
            return _apply_extra_filters(with_org_must_gate(base_filter))
        if context.base_route == "org":
            tag_filter_local = build_tag_only_filter(["IRD_NAI_PJT_INFO"])
            base_filter = and_filter(tag_filter_local, context.participant_org_filter or context.org_filter) if (context.participant_org_filter or context.org_filter) else tag_filter_local
            return _apply_extra_filters(with_org_must_gate(base_filter))
        if context.base_route == "project":
            combined_filter = base_filter_lookup
            if combined_filter is None:
                combined_filter = build_tag_only_filter(["IRD_NAI_PJT_INFO"])
            return _apply_extra_filters(with_org_must_gate(combined_filter))

    if col == context.col_perf and context.base_route in {"perf", "people", "org"}:
        combined_filter = base_filter_lookup
        if context.people_filter:
            combined_filter = and_filter(combined_filter, context.people_filter) if combined_filter else context.people_filter
        if context.participant_org_filter or context.org_filter:
            combined_filter = and_filter(combined_filter, context.participant_org_filter or context.org_filter) if combined_filter else (context.participant_org_filter or context.org_filter)
        if context.perf_tag_filter:
            combined_filter = and_filter(combined_filter, context.perf_tag_filter) if combined_filter else context.perf_tag_filter
        if combined_filter is not None:
            return _apply_extra_filters(with_org_must_gate(combined_filter))
    return _apply_extra_filters(with_org_must_gate(base_filter_lookup))
