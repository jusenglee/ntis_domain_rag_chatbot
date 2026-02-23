# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List

def promote_mode_from_search_hits(
        *,
        current_mode: str,
        search_hits: List[Any],
        ids_map: Dict[str, List[str]],
        planner_strategy: Any,
) -> Dict[str, Any]:
    """검색 hit 기반 mode 승격 정책(기본 비활성).

    회귀 방지를 위해 기본 동작은 입력 mode를 그대로 반환한다.
    """
    mode_now = str(current_mode or "").strip().lower() or "search"
    merged_ids_map: Dict[str, List[str]] = dict(ids_map or {}) if isinstance(ids_map, dict) else {}

    return {
        "mode": mode_now,
        "kind": None,
        "reason": "promotion_disabled_passthrough",
        "ids_map": merged_ids_map,
        "planner_mode": None,
        "planner_action": None,
        "planner_relation": None,
        "query_text": None,
        "allowed": {
            "policy": "disabled_passthrough",
            "lookup": False,
            "join": False,
        },
        "signals": {
            "has_pjt_id": int(bool((merged_ids_map.get("pjt_id") or []))),
            "has_pjt_no": int(bool((merged_ids_map.get("pjt_no") or []))),
            "has_history_nuance": 0,
            "has_detail_nuance": 0,
            "wants_project_perf_relation": 0,
        },
    }
