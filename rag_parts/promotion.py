# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List, Optional

from rag_parts.join import extract_pjt_ids, normalize_relation_hint


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def promote_mode_from_search_hits(
    *,
    current_mode: str,
    search_hits: List[Any],
    ids_map: Dict[str, List[str]],
    planner_strategy: Any,
) -> Dict[str, Any]:
    mode_now = str(current_mode or "").strip().lower() or "search"
    planner_mode = str(_get_attr(planner_strategy, "mode", "") or "").strip().lower() or None
    planner_action = str(_get_attr(planner_strategy, "action", "") or "").strip().lower() or None
    planner_relation = normalize_relation_hint(_get_attr(planner_strategy, "relation", None))
    planner_query = str(_get_attr(planner_strategy, "query_text", "") or "").strip()
    planner_limit = _get_attr(planner_strategy, "limit", 1)

    allowed = {
        "policy": "planner_allowed_promotion",
        "lookup": mode_now == "search" and (planner_mode in (None, "", "search")),
        "join": mode_now == "search"
        and (planner_mode in (None, "", "search"))
        and (planner_relation in (("project", "perf"), ("perf", "project")) or planner_action == "relation"),
    }

    base_ids_map: Dict[str, List[str]] = dict(ids_map or {}) if isinstance(ids_map, dict) else {}
    hit_pjt_ids = extract_pjt_ids(
        search_hits or [],
        max_ids=planner_limit,
    )
    merged_ids_map = dict(base_ids_map)
    if hit_pjt_ids:
        merged_ids_map["pjt_id"] = list(dict.fromkeys((merged_ids_map.get("pjt_id") or []) + list(hit_pjt_ids)))

    pjt_ids = [str(x).strip() for x in (merged_ids_map.get("pjt_id") or []) if str(x).strip()]
    pjt_nos = [str(x).strip() for x in (merged_ids_map.get("pjt_no") or []) if str(x).strip()]

    nuance_text = f"{planner_query} {planner_action or ''}".lower()
    has_history_nuance = any(tok in nuance_text for tok in ("이력", "누적", "전체", "내역", "현황", "전부"))
    has_detail_nuance = any(tok in nuance_text for tok in ("상세", "세부", "특정", "시행", "차수", "단일"))
    wants_project_perf_relation = planner_relation in (("project", "perf"), ("perf", "project")) or planner_action == "relation"

    promoted_mode = mode_now
    promoted_kind: Optional[str] = None
    reason: Optional[str] = None

    if allowed["join"] and wants_project_perf_relation and (pjt_ids or pjt_nos):
        promoted_mode = "join"
        promoted_kind = "join"
        reason = "relation_join_key"
    elif allowed["lookup"] and pjt_nos and has_history_nuance:
        promoted_mode = "lookup"
        promoted_kind = "group"
        reason = "pjt_no_history"
    elif allowed["lookup"] and pjt_ids and has_detail_nuance:
        promoted_mode = "lookup"
        promoted_kind = "instance"
        reason = "pjt_id_detail"

    return {
        "mode": promoted_mode,
        "kind": promoted_kind,
        "reason": reason,
        "ids_map": merged_ids_map,
        "planner_mode": planner_mode,
        "planner_action": planner_action,
        "planner_relation": planner_relation,
        "query_text": planner_query,
        "allowed": allowed,
        "signals": {
            "has_pjt_id": int(bool(pjt_ids)),
            "has_pjt_no": int(bool(pjt_nos)),
            "has_history_nuance": int(has_history_nuance),
            "has_detail_nuance": int(has_detail_nuance),
            "wants_project_perf_relation": int(wants_project_perf_relation),
        },
    }
