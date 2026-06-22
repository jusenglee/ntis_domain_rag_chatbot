from __future__ import annotations

import os
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from apps.platform.settings import get_ctx_token_budget, get_model_max_output_tokens


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


def _normalize_tag(tag: Any) -> str:
    """Normalize raw perf tag values for aggregation matching."""
    return str(tag or "").strip().upper()


def _is_metric_tag_match(metric: str, tag: str) -> bool:
    """Return whether a perf tag contributes to the requested aggregation metric."""
    tag_norm = _normalize_tag(tag)
    if metric == "paper_count":
        return "PAPER" in tag_norm
    if metric == "patent_count":
        return "IPR" in tag_norm or "PATENT" in tag_norm
    if metric == "report_count":
        return "RSCH_RPT" in tag_norm or "REPORT" in tag_norm
    if metric == "perf_total_count":
        return bool(tag_norm)
    return False


def _pick_payload_value(payload_get_fn: Callable[[Dict[str, Any], str], Any], payload: Dict[str, Any], *keys: str) -> str:
    """Pick the first non-empty payload field from dotted or flat candidate keys."""
    for key in keys:
        value = payload_get_fn(payload, key)
        if value is None and "." not in key:
            value = payload.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _build_project_perf_aggregation(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get_fn: Callable[[Dict[str, Any], str], Any],
    stats_metric: str,
) -> Optional[Dict[str, Any]]:
    """Aggregate perf hits by project or project-group for comparison queries."""
    docs = list(reranked or [])
    if not docs:
        return None

    candidate_docs = min(len(docs), max(1, int(hinted_limit or 0), int(policy_limit or 0), 20))
    ids_map = getattr(intent, "ids_map", {}) or {}
    group_by = "project_group" if bool(ids_map.get("pjt_no")) else "project"
    threshold = getattr(intent, "min_metric_count", None)
    top_k = max(1, int(getattr(intent, "top_k", 1) or 1))
    groups: dict[str, dict[str, Any]] = {}

    for point in docs[:candidate_docs]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
        pjt_no = _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")
        group_key = pjt_no if group_by == "project_group" else (pjt_id or pjt_no)
        if not group_key:
            continue
        tag = _pick_payload_value(payload_get_fn, payload, "tag", "meta_basic.tag", "meta_detail.tag")
        if not _is_metric_tag_match(stats_metric, tag):
            continue
        project_title = _pick_payload_value(
            payload_get_fn,
            payload,
            "meta_basic.kor_pjt_nm",
            "meta_detail.kor_pjt_nm",
            "meta_basic.eng_pjt_nm",
            "meta_detail.eng_pjt_nm",
            "kor_pjt_nm",
            "eng_pjt_nm",
            "title_text",
        )
        item = groups.setdefault(
            group_key,
            {
                "group_key": group_key,
                "pjt_id": pjt_id or None,
                "pjt_no": pjt_no or None,
                "project_title": project_title or group_key,
                "metric_value": 0,
                "supporting_perf_count": 0,
            },
        )
        item["metric_value"] += 1
        item["supporting_perf_count"] += 1
        if not item.get("project_title") and project_title:
            item["project_title"] = project_title
        if not item.get("pjt_id") and pjt_id:
            item["pjt_id"] = pjt_id
        if not item.get("pjt_no") and pjt_no:
            item["pjt_no"] = pjt_no

    rank_items = sorted(groups.values(), key=lambda item: (-int(item["metric_value"]), str(item.get("project_title") or item["group_key"])))
    if threshold is not None:
        rank_items = [item for item in rank_items if int(item.get("metric_value") or 0) >= int(threshold)]
    rank_items = rank_items[:top_k]

    return {
        "status": "ok" if rank_items else "empty_result",
        "metric": stats_metric,
        "group_by": group_by,
        "candidate_docs": candidate_docs,
        "threshold": threshold,
        "sort_order": "metric_desc_title_asc",
        "rank_items": rank_items,
    }


def _build_people_superlative_aggregation(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get_fn: Callable[[Dict[str, Any], str], Any],
) -> Optional[Dict[str, Any]]:
    """Keep legacy researcher participation ranking for project_participation_count."""
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
    for person_key, count in sorted(counts.items(), key=lambda item: (-item[1], names.get(item[0], item[0])))[:top_k]:
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
        "status": "ok",
        "metric": "project_participation_count",
        "candidate_docs": candidate_docs,
        "window_years": getattr(intent, "window_years", None),
        "rank_items": rank_items,
    }


def build_people_superlative_aggregation(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get: Callable[[Dict[str, Any], str], Any],
) -> Optional[Dict[str, Any]]:
    """Build either legacy people ranking or project/perf comparison aggregation."""
    payload_get_fn = payload_get
    wants_rank = bool(getattr(intent, "wants_rank", False))
    stats_metric = str(getattr(intent, "stats_metric", "project_participation_count") or "project_participation_count")
    output_type = str(getattr(intent, "output_type", "") or "").strip().lower()
    supported_project_metrics = {"paper_count", "patent_count", "report_count", "perf_total_count"}

    if stats_metric in supported_project_metrics or output_type == "comparison":
        metric = stats_metric if stats_metric in supported_project_metrics else "perf_total_count"
        return _build_project_perf_aggregation(
            reranked=reranked,
            intent=intent,
            hinted_limit=hinted_limit,
            policy_limit=policy_limit,
            payload_get_fn=payload_get_fn,
            stats_metric=metric,
        )

    if not wants_rank and stats_metric != "project_participation_count":
        return None
    return _build_people_superlative_aggregation(
        reranked=reranked,
        intent=intent,
        hinted_limit=hinted_limit,
        policy_limit=policy_limit,
        payload_get_fn=payload_get_fn,
    )


def _infer_series_doc_kind(payload: Dict[str, Any], payload_get_fn: Callable[[Dict[str, Any], str], Any]) -> str:
    """Infer whether a payload behaves like a project or perf document."""
    collection = _pick_payload_value(payload_get_fn, payload, "_collection").lower()
    tag = _normalize_tag(_pick_payload_value(payload_get_fn, payload, "tag", "meta_basic.tag", "meta_detail.tag"))
    if collection.startswith("ntis_project_v1") or "PJT_INFO" in tag:
        return "project"
    if collection.startswith("ntis_perf_v1") or tag:
        return "perf"
    if _pick_payload_value(payload_get_fn, payload, "kor_pjt_nm", "meta_basic.kor_pjt_nm", "meta_detail.kor_pjt_nm"):
        return "project"
    return "unknown"


def _perf_type_from_tag(tag: str) -> str:
    """Map raw perf tag values into a compact perf type label."""
    tag_norm = _normalize_tag(tag)
    if "PAPER" in tag_norm:
        return "paper"
    if "IPR" in tag_norm or "PATENT" in tag_norm:
        return "patent"
    if "RSCH_RPT" in tag_norm or "REPORT" in tag_norm:
        return "report"
    return tag_norm.lower() or "perf"


def build_project_series_payload(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get: Callable[[Dict[str, Any], str], Any],
) -> Optional[Dict[str, Any]]:
    """Build a project-series payload over project-group or year-window evidence."""
    payload_get_fn = payload_get
    output_type = str(getattr(intent, "output_type", "") or "").strip().lower()
    ids_map = getattr(intent, "ids_map", {}) or {}
    years = [str(v).strip() for v in (getattr(intent, "years", []) or []) if str(v).strip()]
    if output_type != "series" and not ids_map.get("pjt_no") and not years:
        return None

    docs = list(reranked or [])
    if not docs:
        return None

    candidate_docs = min(len(docs), max(1, int(hinted_limit or 0), int(policy_limit or 0), 20))
    series_key_kind = "pjt_no" if ids_map.get("pjt_no") else "year_window"
    series_key = str((ids_map.get("pjt_no") or [None])[0] or "").strip() if series_key_kind == "pjt_no" else f"{getattr(intent, 'year_from', None) or (years[0] if years else '')}:{getattr(intent, 'year_to', None) or (years[-1] if years else '')}"

    projects: Dict[str, Dict[str, Any]] = {}
    linked_perf: List[Dict[str, Any]] = []

    for point in docs[:candidate_docs]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        kind = _infer_series_doc_kind(payload, payload_get_fn)
        pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
        pjt_no = _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")
        if series_key_kind == "pjt_no" and series_key and pjt_no and pjt_no != series_key:
            continue
        if kind == "project":
            project_key = pjt_id or pjt_no
            if not project_key:
                continue
            year = _pick_payload_value(payload_get_fn, payload, "stan_yr", "meta_basic.stan_yr", "meta_detail.stan_yr", "dt1")[:4]
            item = projects.setdefault(project_key, {
                "pjt_id": pjt_id or None,
                "pjt_no": pjt_no or None,
                "project_title": _pick_payload_value(payload_get_fn, payload, "meta_basic.kor_pjt_nm", "meta_detail.kor_pjt_nm", "kor_pjt_nm", "title_text") or project_key,
                "year": year or None,
                "lead_org_name": _pick_payload_value(payload_get_fn, payload, "org_nm", "meta_basic.org_nm", "meta_detail.org_nm") or None,
            })
            if not item.get("year") and year:
                item["year"] = year
            if not item.get("lead_org_name"):
                item["lead_org_name"] = _pick_payload_value(payload_get_fn, payload, "org_nm", "meta_basic.org_nm", "meta_detail.org_nm") or None
            continue

    project_year = {str(item.get("pjt_id") or key): str(item.get("year") or "") for key, item in projects.items() if item.get("pjt_id") or item.get("year")}
    seen_perf: set[tuple[str, str, str]] = set()
    for point in docs[:candidate_docs]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        if _infer_series_doc_kind(payload, payload_get_fn) != "perf":
            continue
        pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
        pjt_no = _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")
        if series_key_kind == "pjt_no":
            matches = bool(series_key and pjt_no == series_key) or bool(pjt_id and pjt_id in project_year)
        else:
            matches = bool(pjt_id and pjt_id in project_year)
        if not matches:
            continue
        tag = _pick_payload_value(payload_get_fn, payload, "tag", "meta_basic.tag", "meta_detail.tag")
        perf_title = _pick_payload_value(payload_get_fn, payload, "title_text", "title1", "meta_basic.title", "meta_detail.title") or "performance"
        published_year = _pick_payload_value(payload_get_fn, payload, "dt1", "meta_basic.dt1", "meta_detail.dt1", "stan_yr")[:4]
        dedup_key = (pjt_id or pjt_no or "", perf_title, tag)
        if dedup_key in seen_perf:
            continue
        seen_perf.add(dedup_key)
        linked_perf.append({
            "pjt_id": pjt_id or None,
            "perf_type": _perf_type_from_tag(tag),
            "perf_title": perf_title,
            "published_year": published_year or None,
        })

    if not projects:
        return {
            "status": "empty_result",
            "series_key_kind": series_key_kind,
            "series_key": series_key or None,
            "relation_hint": "_".join(getattr(intent, "relation", ()) or ()) or None,
            "instance_projects": [],
            "linked_perf": [],
            "year_buckets": [],
            "candidate_docs": candidate_docs,
        }

    year_buckets: Dict[str, Dict[str, Any]] = {}
    for item in projects.values():
        year = str(item.get("year") or "unknown")
        bucket = year_buckets.setdefault(year, {"year": year, "project_count": 0, "paper_count": 0, "patent_count": 0, "report_count": 0})
        bucket["project_count"] += 1
    for perf in linked_perf:
        bucket_year = str(project_year.get(str(perf.get("pjt_id") or "")) or perf.get("published_year") or "unknown")
        bucket = year_buckets.setdefault(bucket_year, {"year": bucket_year, "project_count": 0, "paper_count": 0, "patent_count": 0, "report_count": 0})
        perf_type = str(perf.get("perf_type") or "")
        if perf_type == "paper":
            bucket["paper_count"] += 1
        elif perf_type == "patent":
            bucket["patent_count"] += 1
        elif perf_type == "report":
            bucket["report_count"] += 1

    instance_projects = sorted(projects.values(), key=lambda item: (str(item.get("year") or "9999"), str(item.get("project_title") or "")))
    buckets = sorted(year_buckets.values(), key=lambda item: str(item.get("year") or "9999"))
    return {
        "status": "ok",
        "series_key_kind": series_key_kind,
        "series_key": series_key or None,
        "relation_hint": "_".join(getattr(intent, "relation", ()) or ()) or None,
        "instance_projects": instance_projects,
        "linked_perf": linked_perf,
        "year_buckets": buckets,
        "candidate_docs": candidate_docs,
    }


def _normalize_member_name(value: Any) -> str:
    """Return a trimmed member name for pattern calculations."""
    return str(value or "").strip()


def build_pattern_analysis_payload(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get: Callable[[Dict[str, Any], str], Any],
    aggregation: Optional[Dict[str, Any]] = None,
    series: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Compute planner-selected pattern analysis from retrieval/runtime evidence only."""
    payload_get_fn = payload_get
    pattern_kind = str(getattr(intent, "pattern_kind", "") or "").strip().lower() or None
    if not pattern_kind:
        return None

    docs = list(reranked or [])
    candidate_docs = min(len(docs), max(1, int(hinted_limit or 0), int(policy_limit or 0), 20)) if docs else 0

    if pattern_kind == "coauthor_org_repeat":
        org_authors: Dict[str, set[str]] = {}
        org_titles: Dict[str, List[str]] = {}
        support_docs = 0
        for point in docs[:candidate_docs]:
            payload = getattr(point, "payload", None) or {}
            members = payload.get("prtcp_mp") if isinstance(payload, dict) else None
            if not isinstance(members, list) or not members:
                continue
            support_docs += 1
            perf_title = _pick_payload_value(payload_get_fn, payload, "title_text", "title", "meta_basic.title", "meta_detail.title") or "perf"
            local_orgs: Dict[str, set[str]] = {}
            for member in members:
                if not isinstance(member, dict):
                    continue
                org_name = str(member.get("blng_org_nm") or "").strip()
                author_name = _normalize_member_name(member.get("hm_nm"))
                if not org_name or not author_name:
                    continue
                local_orgs.setdefault(org_name, set()).add(author_name)
            for org_name, author_names in local_orgs.items():
                org_authors.setdefault(org_name, set()).update(author_names)
                org_titles.setdefault(org_name, []).append(str(perf_title).strip())
        items = []
        for org_name, author_names in sorted(org_authors.items(), key=lambda item: (-len(item[1]), item[0])):
            if len(author_names) < 2:
                continue
            titles = []
            seen_titles = set()
            for title in org_titles.get(org_name, []):
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                titles.append(title)
            items.append({
                "org_name": org_name,
                "repeated_author_count": len(author_names),
                "author_names": sorted(author_names),
                "supporting_perf_titles": titles[:6],
            })
        status = "ok" if items else ("insufficient_evidence" if support_docs else "empty_result")
        return {
            "status": status,
            "pattern_kind": pattern_kind,
            "candidate_docs": candidate_docs,
            "subject_count": len(items),
            "support_doc_count": support_docs,
            "items": items,
        }

    if pattern_kind == "perf_mix_gap":
        groups: Dict[str, Dict[str, Any]] = {}
        for point in docs[:candidate_docs]:
            payload = getattr(point, "payload", None) or {}
            if not isinstance(payload, dict):
                continue
            pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
            pjt_no = _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")
            group_key = pjt_id or pjt_no
            if not group_key:
                continue
            item = groups.setdefault(
                group_key,
                {
                    "group_key": group_key,
                    "pjt_id": pjt_id or None,
                    "pjt_no": pjt_no or None,
                    "project_title": _pick_payload_value(payload_get_fn, payload, "kor_pjt_nm", "meta_basic.kor_pjt_nm", "meta_detail.kor_pjt_nm", "title_text") or group_key,
                    "paper_count": 0,
                    "patent_count": 0,
                    "report_count": 0,
                },
            )
            tag = _pick_payload_value(payload_get_fn, payload, "tag", "meta_basic.tag", "meta_detail.tag")
            if _is_metric_tag_match("paper_count", tag):
                item["paper_count"] += 1
            if _is_metric_tag_match("patent_count", tag):
                item["patent_count"] += 1
            if _is_metric_tag_match("report_count", tag):
                item["report_count"] += 1
        items = []
        for item in sorted(groups.values(), key=lambda row: (-int(row.get("paper_count") or 0), str(row.get("project_title") or row.get("group_key") or ""))):
            if int(item.get("paper_count") or 0) <= 0 or int(item.get("patent_count") or 0) != 0:
                continue
            items.append({**item, "gap_kind": "paper_without_patent"})
        support_doc_count = sum(int(row.get("paper_count") or 0) + int(row.get("patent_count") or 0) + int(row.get("report_count") or 0) for row in groups.values())
        return {
            "status": "ok" if items else ("empty_result" if groups else "insufficient_evidence"),
            "pattern_kind": pattern_kind,
            "candidate_docs": candidate_docs,
            "subject_count": len(items),
            "support_doc_count": support_doc_count,
            "items": items,
        }

    if pattern_kind == "series_member_change":
        if not isinstance(series, dict) or not list(series.get("instance_projects") or []):
            return {
                "status": "unsupported",
                "pattern_kind": pattern_kind,
                "candidate_docs": candidate_docs,
                "subject_count": 0,
                "support_doc_count": 0,
                "items": [],
            }
        members_by_project: Dict[str, set[str]] = {}
        for point in docs[:candidate_docs]:
            payload = getattr(point, "payload", None) or {}
            pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
            if not pjt_id:
                continue
            members = payload.get("prtcp_mp") if isinstance(payload, dict) else None
            if not isinstance(members, list):
                continue
            bucket = members_by_project.setdefault(pjt_id, set())
            for member in members:
                if not isinstance(member, dict):
                    continue
                name = _normalize_member_name(member.get("hm_nm"))
                if name:
                    bucket.add(name)
        items = []
        previous_members: set[str] = set()
        support_docs = 0
        for project in list(series.get("instance_projects") or []):
            pjt_id = str(project.get("pjt_id") or "").strip()
            current_members = set(members_by_project.get(pjt_id, set()))
            if current_members:
                support_docs += 1
            if not current_members and not previous_members:
                continue
            items.append({
                "year": project.get("year"),
                "project_title": project.get("project_title"),
                "added_members": sorted(current_members - previous_members),
                "removed_members": sorted(previous_members - current_members),
                "member_count": len(current_members),
            })
            previous_members = current_members
        status = "ok" if items else ("insufficient_evidence" if support_docs == 0 else "empty_result")
        return {
            "status": status,
            "pattern_kind": pattern_kind,
            "candidate_docs": candidate_docs,
            "subject_count": len(items),
            "support_doc_count": support_docs,
            "items": items,
        }

    return {
        "status": "unsupported",
        "pattern_kind": pattern_kind,
        "candidate_docs": candidate_docs,
        "subject_count": 0,
        "support_doc_count": 0,
        "items": [],
    }



def build_multi_hop_bundle_payload(
    *,
    reranked: List[Any],
    intent: Any,
    hinted_limit: int,
    policy_limit: int,
    payload_get: Callable[[Dict[str, Any], str], Any],
    project_points: Optional[List[Any]] = None,
    perf_points: Optional[List[Any]] = None,
    resolved_anchors: Any = None,
) -> Optional[Dict[str, Any]]:
    """Build a planner-first multi-hop bundle payload from project/perf evidence only."""
    payload_get_fn = payload_get
    bundle_targets = [str(value).strip().lower() for value in (getattr(intent, "bundle_targets", None) or []) if str(value).strip()]
    if not bundle_targets:
        return None

    docs = list(reranked or [])
    project_docs = list(project_points or [])
    perf_docs = list(perf_points or [])
    if not project_docs or not perf_docs:
        for point in docs:
            payload = getattr(point, "payload", None) or {}
            if not isinstance(payload, dict):
                continue
            kind = _infer_series_doc_kind(payload, payload_get_fn)
            if kind == "project":
                project_docs.append(point)
            elif kind == "perf":
                perf_docs.append(point)

    candidate_docs = max(len(project_docs), len(perf_docs), min(len(docs), max(1, int(hinted_limit or 0), int(policy_limit or 0), 20)) if docs else 0)
    project_items: List[Dict[str, Any]] = []
    seen_projects: set[str] = set()
    for point in project_docs[:candidate_docs]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        pjt_id = _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id")
        pjt_no = _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no")
        project_key = pjt_id or pjt_no
        if not project_key or project_key in seen_projects:
            continue
        seen_projects.add(project_key)
        project_items.append({
            "pjt_id": pjt_id or None,
            "pjt_no": pjt_no or None,
            "project_title": _pick_payload_value(payload_get_fn, payload, "meta_basic.kor_pjt_nm", "meta_detail.kor_pjt_nm", "kor_pjt_nm", "title_text") or project_key,
            "lead_org_name": _pick_payload_value(payload_get_fn, payload, "org_nm", "meta_basic.org_nm", "meta_detail.org_nm") or None,
            "year": (_pick_payload_value(payload_get_fn, payload, "stan_yr", "meta_basic.stan_yr", "meta_detail.stan_yr", "dt1")[:4] or None),
        })

    bundle_entries: List[Dict[str, Any]] = []
    representative_only = bool(getattr(intent, "representative_only", False))
    for target in bundle_targets:
        items: List[Dict[str, Any]] = []
        selection_policy = "top_reranked"
        if target in {"paper", "patent", "report", "representative_perf"}:
            desired_type = None if target == "representative_perf" else target
            for point in perf_docs[:candidate_docs]:
                payload = getattr(point, "payload", None) or {}
                if not isinstance(payload, dict):
                    continue
                tag = _pick_payload_value(payload_get_fn, payload, "tag", "meta_basic.tag", "meta_detail.tag")
                perf_type = _perf_type_from_tag(tag)
                if desired_type and perf_type != desired_type:
                    continue
                items.append({
                    "perf_type": perf_type,
                    "perf_title": _pick_payload_value(payload_get_fn, payload, "title_text", "title", "meta_basic.title", "meta_detail.title") or "perf",
                    "published_year": (_pick_payload_value(payload_get_fn, payload, "pub_year", "year", "meta_basic.year", "meta_detail.year")[:4] or None),
                    "pjt_id": _pick_payload_value(payload_get_fn, payload, "pjt_id", "meta_basic.pjt_id", "meta_detail.pjt_id") or None,
                    "pjt_no": _pick_payload_value(payload_get_fn, payload, "pjt_no", "meta_basic.pjt_no", "meta_detail.pjt_no") or None,
                })
            if target == "representative_perf" or representative_only:
                items = items[:1]
                selection_policy = "reranked_top1"
        elif target == "participant_org":
            selection_policy = "project_payload_unique"
            seen = set()
            for point in project_docs[:candidate_docs]:
                payload = getattr(point, "payload", None) or {}
                orgs = payload.get("prtcp_org") if isinstance(payload, dict) else None
                if not isinstance(orgs, list):
                    continue
                for org in orgs:
                    if not isinstance(org, dict):
                        continue
                    org_nm = str(org.get("org_nm") or org.get("org_name") or org.get("name") or "").strip()
                    role = str(org.get("org_slct_nm") or org.get("role") or "").strip() or None
                    key = (org_nm, role)
                    if not org_nm or key in seen:
                        continue
                    seen.add(key)
                    items.append({"org_name": org_nm, "role": role})
        elif target == "researcher":
            selection_policy = "project_payload_unique"
            seen = set()
            for point in project_docs[:candidate_docs]:
                payload = getattr(point, "payload", None) or {}
                members = payload.get("prtcp_mp") if isinstance(payload, dict) else None
                if not isinstance(members, list):
                    continue
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    name = str(member.get("hm_nm") or member.get("name") or "").strip()
                    affiliation = str(member.get("blng_org_nm") or member.get("affiliation") or "").strip() or None
                    key = (name, affiliation)
                    if not name or key in seen:
                        continue
                    seen.add(key)
                    items.append({"researcher_name": name, "affiliation_org_name": affiliation})
        bundle_entries.append({
            "target_kind": target,
            "items": items[:6],
            "item_count": len(items),
            "selection_policy": selection_policy,
        })

    ambiguities = list(getattr(resolved_anchors, "ambiguities", tuple()) or tuple()) if resolved_anchors is not None else []
    guidance_required = bool(getattr(intent, "guidance_required", False)) or bool(ambiguities)
    guidance_message = None
    if guidance_required:
        guidance_message = "모호한 조건이 있어 일부 JOIN 결과만 보여줍니다. 필요하면 과제번호나 기관 역할을 더 구체적으로 지정해 주세요."

    bundle_item_count = sum(int(entry.get("item_count") or 0) for entry in bundle_entries)
    status = "ok" if project_items or bundle_item_count else "empty_result"
    if guidance_required and status == "ok":
        status = "partial"
    return {
        "status": status,
        "anchor_kind": str(getattr(intent, "base_route", "project") or "project"),
        "bundle_kind": str(getattr(intent, "bundle_kind", "") or "project_outputs"),
        "projects": project_items[:8],
        "bundles": bundle_entries,
        "ambiguities": ambiguities,
        "guidance_message": guidance_message,
        "candidate_docs": candidate_docs,
    }
