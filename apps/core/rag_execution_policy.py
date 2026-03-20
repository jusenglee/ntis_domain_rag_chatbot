from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


def resolve_join_execution_policy(
    *,
    relation: Optional[Tuple[str, str]],
    mode: str,
    action: Optional[str],
    join_key_mode: Optional[str],
    seed_join_pjt_ids: Optional[List[str]] = None,
    seed_join_pjt_nos: Optional[List[str]] = None,
    has_people_org_gate: bool = False,
) -> Dict[str, Any]:

    """JOIN 질의에서 hop1을 skip, lookup, search 중 무엇으로 실행할지 결정한다.

    seed key 존재 여부와 group expansion 정책, 사람/기관 gate 유무를 함께 보고 execution policy 메타를 만든다.
    """
    is_join_mode = bool(relation and mode == "join")
    if not is_join_mode:
        return {
            "policy_source": "execution_policy",
            "join_decision_source": "execution_policy",
            "hop1_strategy": None,
            "reason": None,
            "execution_policy_reason": None,
            "action": action,
            "seed_key_source": None,
            "seed_key_count": 0,
            "join_seed_presence": "none",
            "hop1_key_extraction_status": "not_applicable",
            "has_people_org_gate": int(bool(has_people_org_gate)),
        }

    seed_join_pjt_ids = [str(x).strip() for x in (seed_join_pjt_ids or []) if str(x).strip()]
    seed_join_pjt_nos = [str(x).strip() for x in (seed_join_pjt_nos or []) if str(x).strip()]

    group_resolve_project_ids = str(os.getenv("RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS", "1")).strip().lower() in (
        "1", "true", "yes", "y", "on",
    )
    group_resolve_max_ids = max(1, int(os.getenv("RAG_JOIN_GROUP_RESOLVE_MAX_IDS", "200")))
    group_resolve_topk = max(1, int(os.getenv("RAG_JOIN_GROUP_RESOLVE_TOPK", "400")))
    group_resolve_keep = max(1, int(os.getenv("RAG_JOIN_GROUP_RESOLVE_KEEP", "50")))

    if join_key_mode == "instance" and seed_join_pjt_ids:
        hop1_strategy = "skip"
        reason = "instance_seed_pjt_id"
        seed_key_source = "ids_map.pjt_id"
        seed_key_count = len(seed_join_pjt_ids)
        join_seed_presence = "pjt_id"
        hop1_key_extraction_status = "preseeded"
    elif join_key_mode == "group" and seed_join_pjt_nos:
        if group_resolve_project_ids:
            hop1_strategy = "lookup"
            reason = "group_seed_pjt_no_expand"
            hop1_key_extraction_status = "lookup_required"
        else:
            hop1_strategy = "skip"
            reason = "group_seed_pjt_no_only"
            hop1_key_extraction_status = "preseeded"
        seed_key_source = "ids_map.pjt_no"
        seed_key_count = len(seed_join_pjt_nos)
        join_seed_presence = "pjt_no"
    elif join_key_mode == "deferred":
        hop1_strategy = "lookup"
        reason = "ambiguous_project_key_discovery"
        seed_key_source = "candidate_keys.project_key"
        seed_key_count = 0
        join_seed_presence = "candidate_project_key"
        hop1_key_extraction_status = "lookup_required"
    elif has_people_org_gate:
        hop1_strategy = "lookup"
        reason = "people_org_gate_lookup"
        seed_key_source = "people_org_conditions"
        seed_key_count = 0
        join_seed_presence = "none"
        hop1_key_extraction_status = "lookup_required"
    else:
        hop1_strategy = "search"
        reason = "default_hop1_search"
        seed_key_source = None
        seed_key_count = 0
        join_seed_presence = "none"
        hop1_key_extraction_status = "search_required"

    return {
        "policy_source": "execution_policy",
        "join_decision_source": "execution_policy",
        "hop1_strategy": hop1_strategy,
        "reason": reason,
        "execution_policy_reason": reason,
        "action": action,
        "seed_key_source": seed_key_source,
        "seed_key_count": seed_key_count,
        "join_seed_presence": join_seed_presence,
        "hop1_key_extraction_status": hop1_key_extraction_status,
        "has_people_org_gate": int(bool(has_people_org_gate)),
        "group_resolve_project_ids": int(group_resolve_project_ids),
        "group_resolve_max_ids": group_resolve_max_ids,
        "group_resolve_topk": group_resolve_topk,
        "group_resolve_keep": group_resolve_keep,
    }
