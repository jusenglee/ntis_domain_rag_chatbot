from __future__ import annotations

import os
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from apps.core.settings import get_ctx_token_budget, get_model_max_output_tokens


def get_ctx_hard_limit(*, model_name: str) -> int:
    """모델별 context budget을 바탕으로 runtime hard limit을 계산한다.

    환경변수 override가 있으면 그 값을 우선하고, 없으면 문서 수당 토큰 추정치를 이용해 안전한 상한을 유도한다.
    """
    raw = str(os.getenv("RAG_CTX_HARD_LIMIT", "")).strip()
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            pass
    budget = get_ctx_token_budget(model_name, max_output_tokens=get_model_max_output_tokens(model_name))
    est_per_doc = max(128, int(os.getenv("RAG_CTX_DOC_TOKEN_ESTIMATE", "900")))
    max_cap = max(8, int(os.getenv("RAG_CTX_HARD_LIMIT_MAX", "80")))
    derived = max(8, min(max_cap, budget // est_per_doc))
    return int(derived)


def debug_force_join_keys_enabled() -> bool:
    """JOIN key 강제 주입 디버그 플래그가 켜졌는지 읽는다."""
    return str(os.getenv("RAG_DEBUG_FORCE_JOIN_KEYS", "0")).strip().lower() in ("1", "true", "yes", "y")


def with_org_must_gate(
    base_filter: Any,
    *,
    col: str,
    org_role: Optional[str],
    org_filter: Any,
    participant_org_filter: Any,
    col_project: str,
    and_filter_fn: Callable[[Any, Any], Any],
) -> Any:
    """project 컬렉션에만 기관 must gate를 덧씌운다.

    기관 역할이 participant인지에 따라 participant_org_filter를 우선하고, 그렇지 않으면 일반 org filter를 사용한다.
    """
    if col != col_project:
        return base_filter

    role = str(org_role or "").strip().lower() or None
    gate = None
    if role == "participant":
        gate = participant_org_filter or org_filter
    elif org_filter is not None:
        gate = org_filter
    elif participant_org_filter is not None:
        gate = participant_org_filter

    if gate is None:
        return base_filter
    return and_filter_fn(base_filter, gate)


def build_people_superlative_aggregation(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get_fn: Callable[[Dict[str, Any], str], Any],
) -> Optional[Dict[str, Any]]:
    """재랭크된 문서 창에서 참여연구자 등장 횟수를 집계해 랭킹형 통계 payload를 만든다.

    실제 count는 문서 창 기준이므로 절대적 실적 수치가 아니라 retrieval 결과에 대한 요약이라는 점을 유지한다.
    """
    wants_rank = bool(getattr(intent, "wants_rank", False))
    stats_metric = str(getattr(intent, "stats_metric", "project_participation_count") or "project_participation_count")
    if not wants_rank and stats_metric != "project_participation_count":
        return None

    docs = list(reranked or [])
    if not docs:
        return None

    candidate_docs = min(len(docs), max(1, int(hinted_limit or 0), int(policy_limit or 0), 20))
    counts: Counter[str] = Counter()
    names: Dict[str, str] = {}
    ids: Dict[str, str] = {}
    orgs: Dict[str, str] = {}

    for point in docs[:candidate_docs]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        hm_names = payload_get_fn(payload, "prtcp_mp[].hm_nm") or payload_get_fn(payload, "prtcp_mp.hm_nm") or []
        hm_ids = payload_get_fn(payload, "prtcp_mp[].hm_id") or payload_get_fn(payload, "prtcp_mp.hm_id") or []
        org_names = payload_get_fn(payload, "prtcp_mp[].blng_org_nm") or payload_get_fn(payload, "prtcp_mp.blng_org_nm") or []

        if not isinstance(hm_names, list):
            hm_names = [hm_names] if str(hm_names or "").strip() else []
        if not isinstance(hm_ids, list):
            hm_ids = [hm_ids] if str(hm_ids or "").strip() else []
        if not isinstance(org_names, list):
            org_names = [org_names] if str(org_names or "").strip() else []

        member_count = max(len(hm_names), len(hm_ids), len(org_names))
        for idx in range(member_count):
            hm_nm = str(hm_names[idx] if idx < len(hm_names) else "").strip()
            hm_id = str(hm_ids[idx] if idx < len(hm_ids) else "").strip()
            org_nm = str(org_names[idx] if idx < len(org_names) else "").strip()
            person_key = hm_id or hm_nm
            if not person_key:
                continue
            counts[person_key] += 1
            if hm_nm:
                names[person_key] = hm_nm
            if hm_id:
                ids[person_key] = hm_id
            if org_nm and person_key not in orgs:
                orgs[person_key] = org_nm

    if not counts:
        return None

    top_k = max(1, int(getattr(intent, "top_k", 1) or 1))
    rank_items: List[Dict[str, Any]] = []
    for person_key, count in sorted(counts.items(), key=lambda item: (-item[1], names.get(item[0], item[0])) )[:top_k]:
        rank_items.append(
            {
                "person_key": person_key,
                "hm_id": ids.get(person_key),
                "hm_nm": names.get(person_key),
                "org_nm": orgs.get(person_key),
                "project_participation_count": int(count),
                "document_count": int(count),
            }
        )

    return {
        "metric": stats_metric,
        "candidate_docs": candidate_docs,
        "window_years": getattr(intent, "window_years", None),
        "rank_items": rank_items,
    }
