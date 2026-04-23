from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from apps.platform.rag_constants import COL_PERF, COL_PROJECT, TAG_PJT_INFO, TAG_PJT_MP, TAG_PJT_ORG
from apps.platform.rag_types import RagResult
from apps.platform.settings import RAG_COLLECTION_ALLOWLIST
from loguru import logger
from apps.retrieval.rag_executor_support import get_meta, payload_title
from apps.retrieval.rag_hydration_runtime import hydrate_points_payload
from apps.retrieval.rag_join_runtime import build_join_hop_context, compose_join_context
from apps.retrieval.rag_postprocess_policy import hydrate_reranked_payloads, prepare_title_post_rerank
from apps.retrieval.rag_rank_runtime import PERF_TAGS_NORM, PROJECT_TAGS_NORM, dedup_by_doc_id, hit_key, normalize_tag_value, resolve_collection
from apps.retrieval.rag_rerank_support import build_final_rerank, soft_title_contains
from apps.retrieval.rag_runtime_observability import clip_text, log_kv, log_section, log_top_points as log_top_points_runtime, resolve_env_topn, timing_put
from apps.retrieval.rag_runtime_safety import build_multi_hop_bundle_payload, build_pattern_analysis_payload, build_people_superlative_aggregation, build_project_series_payload
from apps.retrieval.retrieval import _payload_get
from apps.retrieval.result_contract import enforce_reranked_contract

from apps.evidence.context_build_policy import build_context_bundle, build_context_with_output_type

PayloadGet = Callable[[Dict[str, Any], str], Any]
HitKey = Callable[[Any], Tuple[str, str]]
TimingPut = Callable[[str, Any], None]


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _get_attr(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _hydrate_points(points: Sequence[Any], *, qdr: Any, chunk_size: int = 128) -> None:
    hydrate_points_payload(
        qdr,
        list(points or []),
        normalize_tag_value=normalize_tag_value,
        project_tags_norm=PROJECT_TAGS_NORM,
        perf_tags_norm=PERF_TAGS_NORM,
        col_project=COL_PROJECT,
        col_perf=COL_PERF,
        tag_pjt_info=TAG_PJT_INFO,
        tag_pjt_mp=TAG_PJT_MP,
        tag_pjt_org=TAG_PJT_ORG,
        rag_collection_allowlist=RAG_COLLECTION_ALLOWLIST,
        chunk_size=chunk_size,
    )


def _log_top_points(title: str, points: Sequence[Any], *, topn: int = None, level: str = "info", tier: str = "debug") -> None:
    log_top_points_runtime(
        title,
        list(points or []),
        get_meta=get_meta,
        resolve_collection=resolve_collection,
        topn=topn,
        level=level,
        tier=tier,
        logger_obj=logger,
    )


def _has_explicit_identifiers(intent: Any) -> bool:
    ids_map = getattr(intent, "ids_map", None)
    if isinstance(ids_map, dict) and any(values for values in ids_map.values() if values):
        return True
    ids_flat = getattr(intent, "ids_flat", None)
    if isinstance(ids_flat, list) and len(ids_flat) > 0:
        return True
    if bool(getattr(intent, "is_exact_key_query", False)):
        return True
    legacy = getattr(intent, "ids", None)
    return isinstance(legacy, dict) and any(values for values in legacy.values() if values)


def _render_series_context(series: Optional[Dict[str, Any]]) -> str:
    """Render a compact text view for series payloads."""
    if not isinstance(series, dict):
        return ""
    projects = list(series.get("instance_projects") or [])
    buckets = list(series.get("year_buckets") or [])
    if not projects and not buckets:
        return ""
    lines = [f"- series_key_kind: {series.get('series_key_kind') or 'unknown'}"]
    if series.get("series_key"):
        lines.append(f"- series_key: {series.get('series_key')}")
    if series.get("relation_hint"):
        lines.append(f"- relation_hint: {series.get('relation_hint')}")
    for item in projects[:8]:
        title = str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip()
        year = str(item.get("year") or "-")
        pjt_id = str(item.get("pjt_id") or "").strip()
        pjt_no = str(item.get("pjt_no") or "").strip()
        suffix = ", ".join(part for part in [f"year={year}" if year else "", f"pjt_id={pjt_id}" if pjt_id else "", f"pjt_no={pjt_no}" if pjt_no else ""] if part)
        lines.append(f"- project: {title}" + (f" ({suffix})" if suffix else ""))
    for bucket in buckets[:8]:
        lines.append(
            f"- bucket {bucket.get('year')}: projects={bucket.get('project_count', 0)}, papers={bucket.get('paper_count', 0)}, patents={bucket.get('patent_count', 0)}, reports={bucket.get('report_count', 0)}"
        )
    return "\n".join(lines)
def _render_pattern_analysis_context(pattern_analysis: Optional[Dict[str, Any]]) -> str:
    """Render a compact text view for pattern-analysis payloads."""
    if not isinstance(pattern_analysis, dict):
        return ""
    items = list(pattern_analysis.get("items") or [])
    if not items:
        return ""
    kind = str(pattern_analysis.get("pattern_kind") or "pattern_analysis")
    lines = [f"- pattern_kind: {kind}"]
    for item in items[:8]:
        if kind == "coauthor_org_repeat":
            lines.append(f"- org: {item.get('org_name')} | repeated_authors={item.get('repeated_author_count')} | authors={', '.join(item.get('author_names') or [])}")
            if item.get("supporting_perf_titles"):
                lines.append(f"- perf_titles: {', '.join(item.get('supporting_perf_titles') or [])}")
            continue
        if kind == "perf_mix_gap":
            lines.append(f"- project: {item.get('project_title') or item.get('group_key')} | paper_count={item.get('paper_count', 0)} | patent_count={item.get('patent_count', 0)} | report_count={item.get('report_count', 0)} | gap={item.get('gap_kind')}")
            continue
        if kind == "series_member_change":
            lines.append(f"- year={item.get('year')} | project={item.get('project_title')} | added={', '.join(item.get('added_members') or []) or '-'} | removed={', '.join(item.get('removed_members') or []) or '-'} | member_count={item.get('member_count', 0)}")
            continue
        lines.append(f"- item: {item}")
    return "\n".join(lines)



def _render_multi_hop_bundle_context(bundle: Optional[Dict[str, Any]]) -> str:
    """Render a compact text view for multi-hop bundle payloads."""
    if not isinstance(bundle, dict):
        return ""
    projects = list(bundle.get("projects") or [])
    bundles = list(bundle.get("bundles") or [])
    if not projects and not bundles:
        return ""
    lines = [f"- bundle_kind: {bundle.get('bundle_kind') or 'project_outputs'}"]
    if bundle.get("guidance_message"):
        lines.append(f"- guidance: {bundle.get('guidance_message')}")
    for project in projects[:6]:
        title = str(project.get("project_title") or project.get("pjt_id") or project.get("pjt_no") or "project").strip()
        suffix = ", ".join(part for part in [f"pjt_id={project.get('pjt_id')}" if project.get("pjt_id") else "", f"pjt_no={project.get('pjt_no')}" if project.get("pjt_no") else "", f"year={project.get('year')}" if project.get("year") else ""] if part)
        lines.append(f"- project: {title}" + (f" ({suffix})" if suffix else ""))
    for entry in bundles[:6]:
        lines.append(f"- target: {entry.get('target_kind')} | item_count={entry.get('item_count', 0)} | selection_policy={entry.get('selection_policy')}")
        for item in list(entry.get("items") or [])[:4]:
            if entry.get("target_kind") in {"paper", "patent", "report", "representative_perf"}:
                lines.append(f"- item: {item.get('perf_title')} ({item.get('perf_type')})")
            elif entry.get("target_kind") == "participant_org":
                lines.append(f"- item: {item.get('org_name')}" + (f" ({item.get('role')})" if item.get('role') else ""))
            elif entry.get("target_kind") == "researcher":
                lines.append(f"- item: {item.get('researcher_name')}" + (f" ({item.get('affiliation_org_name')})" if item.get('affiliation_org_name') else ""))
            else:
                lines.append(f"- item: {item}")
    return "\n".join(lines)

def _render_reverse_trace_context(reverse_trace: Optional[Dict[str, Any]]) -> str:
    """Render a compact text view for perf -> project -> perf reverse traces."""
    if not isinstance(reverse_trace, dict):
        return ""
    origin_perf = list(reverse_trace.get("origin_perf") or [])
    origin_projects = list(reverse_trace.get("origin_projects") or [])
    followup_perf = list(reverse_trace.get("followup_perf") or [])
    if not origin_perf and not origin_projects and not followup_perf:
        return ""
    lines = ["- relation_chain: perf -> project -> perf"]
    for item in origin_perf[:4]:
        title = str(item.get("perf_title") or item.get("title") or item.get("doc_id") or "perf").strip()
        perf_type = str(item.get("perf_type") or "unknown").strip()
        lines.append(f"- origin_perf: {title} ({perf_type})")
    for item in origin_projects[:6]:
        title = str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip()
        suffix = ", ".join(part for part in [f"pjt_id={item.get('pjt_id')}" if item.get("pjt_id") else "", f"pjt_no={item.get('pjt_no')}" if item.get("pjt_no") else ""] if part)
        lines.append(f"- origin_project: {title}" + (f" ({suffix})" if suffix else ""))
    for item in followup_perf[:8]:
        title = str(item.get("perf_title") or item.get("title") or item.get("doc_id") or "perf").strip()
        perf_type = str(item.get("perf_type") or "unknown").strip()
        year = str(item.get("published_year") or "").strip()
        lines.append(f"- followup_perf: {title} ({perf_type}" + (f", year={year}" if year else "") + ")")
    return "\n".join(lines)

@dataclass(frozen=True)
class ResultAssemblyRequest:
    """RAG 결과 조립에 필요한 요청 컨텍스트를 묶어 두는 구조체다.
    base route, mode, relation, query terms, source hits가 한 곳에 있어 context builder와 join assembler가 같은 입력을 공유한다.
    """
    base_route: str
    mode: str
    action: str
    output_type: Optional[str]
    query_text: str
    people_terms: Optional[List[str]]
    people_ids: Optional[List[str]]
    org_terms: Optional[List[str]]
    people_org_terms: Optional[List[str]]
    org_role: Optional[str]
    stack: str
    plan_mode: str
    relation: Any
    keywords: List[str]
    sources: Sequence[Any]


@dataclass(frozen=True)
class ResultAssemblyPolicy:
    """result assembly에서 쓸 rerank/title/context 정책 설정을 묶어 둔다.
    preset과 title match 정책을 함께 들고 다니며 조립 단계에서 타이틀 강화가 어떻게 적용되는지 고정한다.
    """
    rerank_spec: Optional[Dict[str, Any]]
    preset: Any
    ctx_hard_limit: int
    hinted_limit: int
    title_match_mode: str
    title_match_mode_contains: str
    title_terms: List[str]
    lookup_title_filter_policy: str


@dataclass(frozen=True)
class ResultAssemblyState:
    """Result assembly가 공유하는 실제 runtime state만 보관한다."""

    timings: Dict[str, Any]
    t_all0: float
    qdr: Any
    intent_payload: Any


def collect_filter_probe_terms(*, people_terms: Optional[List[str]], people_ids: Optional[List[str]], mode: str) -> List[str]:
    """필터 효과를 사후 점검할 때 사용할 probe 키워드를 골라낸다.
    lookup/join에서만 최소 한 개 term을 유지해 raw nested payload에 실제 매칭 흔적이 있는지 확인한다.
    """
    probe_terms = [str(term).strip() for term in (people_terms or []) if str(term).strip()]
    if not probe_terms and mode in ("lookup", "join"):
        if bool(people_terms) and not bool(people_ids):
            probe_terms = [str(term).strip() for term in (people_terms or []) if str(term).strip()][:1]
    return probe_terms


def collect_filter_probe_docs(
    reranked: Sequence[Any],
    *,
    payload_get: PayloadGet,
    probe_terms: List[str],
    mode: str,
) -> Optional[Dict[str, Any]]:
    """reranked top-N 문서에서 참여인력 nested payload 매칭 증거를 수집한다.
    filter 결과가 실제 raw payload 기준으로 확인되는지 사후 진단할 수 있게 preview 문서와 match count를 만든다.
    """
    if not probe_terms or mode not in ("lookup", "join") or not reranked:
        return None

    inspect_topn = min(max(1, int(os.getenv("RAG_FILTER_PROBE_TOPN", "10"))), len(reranked))
    matched = 0
    raw_inspected = 0
    probe_docs: List[Dict[str, Any]] = []

    for point in reranked[:inspect_topn]:
        payload = getattr(point, "payload", None) or {}
        doc_id = (
            payload_get(payload, "doc_id")
            or payload_get(payload, "id")
            or payload_get(payload, "meta_basic.doc_id")
            or payload_get(payload, "meta_basic.pjt_id")
            or payload_get(payload, "meta_detail.pjt_id")
        )
        title = (
            payload_get(payload, "meta_basic.kor_pjt_nm")
            or payload_get(payload, "meta_basic.title")
            or payload_get(payload, "title")
        )
        members_raw = payload.get("prtcp_mp") if isinstance(payload, dict) else None
        raw_members_available = isinstance(members_raw, list)
        members_preview: List[Dict[str, Optional[str]]] = []
        names: List[str] = []
        if raw_members_available:
            raw_inspected += 1
            for member in members_raw:
                if not isinstance(member, dict):
                    continue
                hm_nm = str(member.get("hm_nm") or "").strip() or None
                blng_org_nm = str(member.get("blng_org_nm") or "").strip() or None
                hm_id = str(member.get("hm_id") or "").strip() or None
                members_preview.append(
                    {
                        "hm_nm": hm_nm,
                        "blng_org_nm": blng_org_nm,
                        "hm_id": hm_id,
                    }
                )
                if hm_nm:
                    names.append(hm_nm)
        probe_docs.append(
            {
                "doc_id": str(doc_id or "").strip() or None,
                "title": str(title or "").strip() or None,
                "raw_nested_available": raw_members_available,
                "prtcp_mp_preview": members_preview,
            }
        )
        if any(term.lower() in name.lower() for term in probe_terms for name in names):
            matched += 1

    if raw_inspected == 0:
        return {
            "status": "unknown",
            "reason": "raw_nested_unavailable",
            "inspect_topn": inspect_topn,
            "matched": 0,
            "probe_docs": probe_docs,
        }

    return {
        "status": "confirmed_match" if matched > 0 else "confirmed_miss",
        "reason": None,
        "inspect_topn": inspect_topn,
        "matched": matched,
        "probe_docs": probe_docs,
    }


def _payload_list_texts(payload: Dict[str, Any], key: str) -> List[str]:
    values: List[str] = []
    current = payload.get(key)
    if isinstance(current, list):
        for item in current:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                values.append(text)
    elif current is not None:
        text = str(current).strip()
        if text:
            values.append(text)
    return values


def _match_any_term(value: str, terms: Sequence[str]) -> bool:
    target = str(value or "").strip().lower()
    if not target:
        return False
    return any(str(term or "").strip().lower() in target for term in terms if str(term or "").strip())


def _match_same_member_constraints(
    members_raw: Any,
    *,
    people_terms: Sequence[str],
    people_ids: Sequence[str],
    affiliation_org_terms: Sequence[str],
) -> bool:
    if not isinstance(members_raw, list):
        return False
    normalized_people = [str(term).strip() for term in people_terms if str(term).strip()]
    normalized_ids = [str(term).strip() for term in people_ids if str(term).strip()]
    normalized_affiliations = [str(term).strip() for term in affiliation_org_terms if str(term).strip()]
    for member in members_raw:
        if not isinstance(member, dict):
            continue
        hm_nm = str(member.get("hm_nm") or "").strip()
        hm_id = str(member.get("hm_id") or "").strip()
        blng_org_nm = str(member.get("blng_org_nm") or "").strip()
        person_ok = True
        affiliation_ok = True
        if normalized_people:
            person_ok = _match_any_term(hm_nm, normalized_people)
        if normalized_ids:
            person_ok = person_ok and hm_id in normalized_ids
        if normalized_affiliations:
            affiliation_ok = _match_any_term(blng_org_nm, normalized_affiliations)
        if person_ok and affiliation_ok:
            return True
    return False


def _point_satisfies_structured_constraint(
    point: Any,
    *,
    people_terms: Sequence[str],
    people_ids: Sequence[str],
    org_terms: Sequence[str],
    people_org_terms: Sequence[str],
    org_role: Optional[str],
) -> bool:
    payload = getattr(point, "payload", None) or {}
    role = str(org_role or "").strip().lower()
    normalized_people = [str(term).strip() for term in people_terms if str(term).strip()]
    normalized_ids = [str(term).strip() for term in people_ids if str(term).strip()]
    normalized_orgs = [str(term).strip() for term in org_terms if str(term).strip()]
    normalized_people_orgs = [str(term).strip() for term in people_org_terms if str(term).strip()]

    if normalized_people or normalized_ids or normalized_people_orgs:
        if _match_same_member_constraints(
            payload.get("prtcp_mp"),
            people_terms=normalized_people,
            people_ids=normalized_ids,
            affiliation_org_terms=normalized_people_orgs,
        ):
            people_axis_ok = True
        else:
            people_axis_ok = not (normalized_people or normalized_ids or normalized_people_orgs)
        if not people_axis_ok:
            return False

    if normalized_orgs:
        if role in {"affiliation", "affiliation_org"}:
            return _match_same_member_constraints(
                payload.get("prtcp_mp"),
                people_terms=[],
                people_ids=[],
                affiliation_org_terms=normalized_orgs,
            )
        if role in {"participant", "participant_org"}:
            participant_orgs = [
                str((item or {}).get("org_nm") or "").strip()
                for item in (payload.get("prtcp_org") or [])
                if isinstance(item, dict)
            ]
            return any(_match_any_term(value, normalized_orgs) for value in participant_orgs)
        top_org = str(payload.get("org_nm") or "").strip()
        if role in {"lead", "performer", "performing", "lead_org"}:
            return _match_any_term(top_org, normalized_orgs)
        participant_orgs = [
            str((item or {}).get("org_nm") or "").strip()
            for item in (payload.get("prtcp_org") or [])
            if isinstance(item, dict)
        ]
        return _match_any_term(top_org, normalized_orgs) or any(_match_any_term(value, normalized_orgs) for value in participant_orgs)
    return True


def apply_structured_result_constraint(
    reranked: Sequence[Any],
    *,
    people_terms: Sequence[str],
    people_ids: Sequence[str],
    org_terms: Sequence[str],
    people_org_terms: Sequence[str],
    org_role: Optional[str],
) -> tuple[List[Any], Dict[str, Any]]:
    constrained = list(reranked or [])
    constraints_present = bool(people_terms or people_ids or org_terms or people_org_terms)
    if not constraints_present:
        return constrained, {"applied": False, "input_count": len(constrained), "output_count": len(constrained), "dropped_count": 0}

    filtered = [
        point
        for point in constrained
        if _point_satisfies_structured_constraint(
            point,
            people_terms=people_terms,
            people_ids=people_ids,
            org_terms=org_terms,
            people_org_terms=people_org_terms,
            org_role=org_role,
        )
    ]
    return filtered, {
        "applied": True,
        "input_count": len(constrained),
        "output_count": len(filtered),
        "dropped_count": max(0, len(constrained) - len(filtered)),
    }


def collect_merged_hits(sources: Sequence[Any], *, hit_key: HitKey) -> List[Any]:
    """여러 source에서 들어온 hit를 key 기준으로 중복 제거해 합친다.
    source별 히트를 다른 순서로 받더라도 같은 문서를 두 번 집계하지 않게 한다.
    """
    merged_hits: List[Any] = []
    seen: set[Tuple[str, str]] = set()
    for source in sources:
        for hit in (getattr(source, "points", None) or []):
            key = hit_key(hit)
            if key in seen:
                continue
            seen.add(key)
            merged_hits.append(hit)
    return merged_hits


def resolve_effective_min_reranked(
    *,
    intent: Any,
    mode: str,
    base_route: str,
    preset_min_reranked: int,
    hinted_limit: int = 0,
) -> tuple[int, str]:
    """현재 요청에 적용할 실효 `min_reranked` 값과 clamp 사유를 계산한다.
    hinted limit·lookup id query 같은 예외 경로에서 rerank 하한선을 낮춰 가시 결과가 사라지지 않게 한다.
    """
    effective_min_reranked = max(0, int(preset_min_reranked or 0))
    clamp_reasons: list[str] = []

    hinted_limit_val = max(0, int(hinted_limit or 0))
    if hinted_limit_val > 0:
        effective_min_reranked = min(effective_min_reranked, hinted_limit_val)
        clamp_reasons.append("hinted_limit")

    if str(mode).strip().lower() != "lookup":
        return effective_min_reranked, ",".join(clamp_reasons) if clamp_reasons else "none"

    if str(base_route or "").strip().lower() not in ("", "project", "perf"):
        return effective_min_reranked, ",".join(clamp_reasons) if clamp_reasons else "none"

    if _has_explicit_identifiers(intent):
        id_lookup_min_reranked = max(0, int(os.getenv("RAG_MIN_RERANKED_LOOKUP_ID", "1")))
        effective_min_reranked = min(effective_min_reranked, id_lookup_min_reranked)
        clamp_reasons.append("lookup_id_query")
    return effective_min_reranked, ",".join(clamp_reasons) if clamp_reasons else "none"


def assemble_rag_result(
    *,
    reranked: Sequence[Any],
    sources: Sequence[Any],
    min_ctx_items: int,
    preset_max_ctx_items: int,
    ctx_hard_limit: int,
    action: str,
    base_route: str,
    output_type: Optional[str],
    mode: str,
    query_text: str,
    people_terms: Optional[List[str]],
    person_ids: Optional[List[str]],
    people_org_terms: Optional[List[str]],
    org_terms: Optional[List[str]],
    org_role: Optional[str],
    timings: Dict[str, Any],
    t_all0: float,
    stack: str,
    plan_mode: str,
    relation: Any,
    keywords: List[str],
    aggregation: Optional[Dict[str, Any]],
    series: Optional[Dict[str, Any]],
    pattern_analysis: Optional[Dict[str, Any]],
    multi_hop_bundle: Optional[Dict[str, Any]],
    contract_fail_reason: Any,
    intent_payload: Any = None,
) -> RagResult:
    """base retrieval/rerank 결과를 context bundle·docs·aggregations·meta가 들어있는 RagResult로 조립한다.
    title post-rerank, contract enforcement, filter probe, aggregation, context rendering을 연결하는 핵심 결과 조립 관문이다.
    """
    strategy_meta = dict(getattr(intent_payload, "strategy_meta", None) or {})
    t0 = time.time()
    context_bundle = build_context_bundle(
        list(reranked or []),
        min_ctx_items=min_ctx_items,
        preset_max_ctx_items=int(preset_max_ctx_items),
        ctx_hard_limit=ctx_hard_limit,
        action=action,
        base_route=base_route,
        output_type=output_type,
        mode=mode,
        query_text=query_text,
        people_terms=people_terms,
        person_ids=person_ids,
        org_terms=org_terms,
        people_org_terms=people_org_terms,
        org_role=org_role,
        turn_id=str(strategy_meta.get("turn_id") or "").strip() or None,
    )
    context = context_bundle["context"]
    if str(output_type or "").strip().lower() == "series" and isinstance(series, dict) and str(series.get("status") or "").strip().lower() == "ok":
        context = _render_series_context(series) or context
    elif isinstance(pattern_analysis, dict) and str(pattern_analysis.get("status") or "").strip().lower() == "ok":
        context = _render_pattern_analysis_context(pattern_analysis) or context
    elif isinstance(multi_hop_bundle, dict) and str(multi_hop_bundle.get("status") or "").strip().lower() in {"ok", "partial"}:
        context = _render_multi_hop_bundle_context(multi_hop_bundle) or context
    refs = context_bundle["refs"]
    ctx_fieldset = context_bundle["fieldset"]
    render_profile = context_bundle.get("render_profile")
    canonical_evidence = context_bundle.get("canonical_evidence")
    prompt_units = context_bundle.get("prompt_units") or []
    used_tokens = int(context_bundle.get("used_tokens") or 0)
    dropped_by_floor = int(context_bundle.get("dropped_by_floor") or 0)
    dropped_by_budget = int(context_bundle.get("dropped_by_budget") or 0)
    compressed_count = int(context_bundle.get("compressed_count") or 0)
    lineages = list(context_bundle.get("lineages") or [])

    timing_put(timings, "phase.build_context", time.time() - t0)
    timing_put(timings, "phase.total", time.time() - t_all0)

    ctx_max_items = int(context_bundle["max_items"])
    kept_ctx = int(context_bundle["kept_ctx"])
    discarded_ctx = int(context_bundle["discarded_ctx"])

    log_kv(
        "RAG.CONTEXT",
        reranked_total=len(reranked or []),
        min_ctx_items=min_ctx_items,
        kept_ctx=kept_ctx,
        discarded_ctx=discarded_ctx,
        used_tokens=used_tokens,
        dropped_by_floor=dropped_by_floor,
        dropped_by_budget=dropped_by_budget,
        compressed_count=compressed_count,
        contract_fail_reason=contract_fail_reason,
        execution_mode=mode,
        execution_base_route=base_route,
        execution_output_type=output_type,
        execution_relation=relation,
        strategy_source="execution_request",
        tier="normal",
    )
    log_kv(
        "RAG.CTX",
        tier="debug",
        ctx_len=len(context or ""),
        refs=len(refs or []),
        max_items=int(ctx_max_items),
        used_tokens=used_tokens,
        contract_fail_reason=contract_fail_reason,
        execution_mode=mode,
        execution_base_route=base_route,
        output_type=output_type,
        execution_relation=relation,
        fieldset_keys=list(ctx_fieldset or []),
        render_profile=render_profile,
        strategy_source="execution_request",
    )

    merged_hits = collect_merged_hits(sources, hit_key=hit_key)
    logger.info(
        f"execution_mode={mode} planner_mode_hint={plan_mode} "
        f"base={base_route} action={action} rel={relation} "
        f"kw_det={timings.get('phase.kw_det',0):.4f}s, search={timings.get('phase.dense_search',0):.4f}s, "
        f"rrf={timings.get('phase.rrf_merge',0):.4f}s, rerank={timings.get('phase.final_rerank',0):.4f}s, "
        f"ctx={timings.get('phase.build_context',0):.4f}s, total={timings.get('phase.total',0):.4f}s"
    )
    return RagResult(
        stack=stack,
        keywords=keywords,
        hits=merged_hits,
        reranked_hits=list(reranked or []),
        context=context,
        refs=refs,
        timings=timings,
        aggregation=aggregation,
        series=series,
        multi_hop_bundle=multi_hop_bundle,
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        prompt_units=prompt_units,
        used_tokens=used_tokens,
        kept_ctx=kept_ctx,
        discarded_ctx=discarded_ctx,
        dropped_by_floor=dropped_by_floor,
        dropped_by_budget=dropped_by_budget,
        compressed_count=compressed_count,
        lineages=lineages,
        anchor_hit=bool(context_bundle.get("anchor_hit")),
        followup_resolved_by_facts=bool(context_bundle.get("followup_resolved_by_facts")),
    )


class SearchLookupResultOrchestrator:
    """`SearchLookupResultOrchestrator`는 현재 모듈의 책임을 표현하는 타입 또는 헬퍼 클래스입니다.

책임:
- 현재 레이어는 planner/contract/runtime 경계를 넘어 의미를 임의 보정하지 않고, 필요한 검증과 조립만 수행해야 합니다.\n    """
    def __init__(
        self,
        *,
        merged_rrf: Sequence[Any],
        query_intent: Any,
        keywords: List[str],
        lex_w_eff: float,
        request: ResultAssemblyRequest,
        policy: ResultAssemblyPolicy,
        state: ResultAssemblyState,
    ) -> None:
        """join result assembly에 필요한 runtime callback과 공유 설정을 인스턴스에 붙인다.
        join hop context 조립과 base result assembly가 같은 자원을 공유할 수 있게 초기 상태를 정리한다.
        """
        self.merged_rrf = merged_rrf
        self.query_intent = query_intent
        self.keywords = keywords
        self.lex_w_eff = lex_w_eff
        self.request = request
        self.policy = policy
        self.state = state

    def run(self) -> RagResult:
        """join hop 결과를 받아 relation 전용 RagResult로 조립한다.
        hop1/hop2 context, join keys used, strategy summary를 합친 다음 base assembler가 내놓는 shape와 맞춰 반환한다.
        """
        record_timing = lambda key, value: timing_put(self.state.timings, key, value)
        final_rerank = build_final_rerank(
            payload_get=_payload_get,
            get_meta=get_meta,
            payload_title=payload_title,
            clip_text=clip_text,
            log_kv=log_kv,
            log_section=log_section,
        )
        title_post_filter = prepare_title_post_rerank(
            self.merged_rrf,
            plan_mode=self.request.plan_mode,
            title_match_mode=self.policy.title_match_mode,
            title_match_mode_contains=self.policy.title_match_mode_contains,
            title_terms=self.policy.title_terms,
            lookup_title_filter_policy=self.policy.lookup_title_filter_policy,
            soft_title_contains=soft_title_contains,
            log_kv=log_kv,
            timing_put=record_timing,
        )
        title_soft_terms_for_rerank = list(title_post_filter.get("title_soft_terms_for_rerank", []) or [])
        title_soft_boost = float(title_post_filter.get("title_soft_boost", 0.0) or 0.0)

        t0 = time.time()
        final_keep = int((self.policy.rerank_spec or {}).get("final_keep", 80))
        reranked = final_rerank(
            self.merged_rrf,
            it=self.query_intent,
            kws=self.keywords,
            lex_w=self.lex_w_eff,
            base_route=self.request.base_route,
            mode=self.request.mode,
            keep=final_keep,
            tag_boost=float(getattr(self.policy.preset, "tag_boost", 0.0)),
            tag_mismatch_penalty=float(getattr(self.policy.preset, "tag_mismatch_penalty", 0.0)),
            title_soft_terms=title_soft_terms_for_rerank,
            title_soft_boost=title_soft_boost,
        )
        reranked = dedup_by_doc_id(reranked)
        if len(reranked) > self.policy.ctx_hard_limit:
            reranked = reranked[: self.policy.ctx_hard_limit]
        record_timing("phase.final_rerank", time.time() - t0)

        aggregation = build_people_superlative_aggregation(
            reranked=reranked,
            intent=self.query_intent,
            hinted_limit=self.policy.hinted_limit,
            policy_limit=int(getattr(self.policy.preset, "max_ctx_items", 10) or 10),
            payload_get=_payload_get,
        )
        series = build_project_series_payload(
            reranked=reranked,
            intent=self.query_intent,
            hinted_limit=self.policy.hinted_limit,
            policy_limit=int(getattr(self.policy.preset, "max_ctx_items", 10) or 10),
            payload_get=_payload_get,
        )
        if aggregation:
            record_timing("info.aggregation_candidate_docs", int(aggregation.get("candidate_docs", 0) or 0))
            record_timing("info.aggregation_rank_items", len(aggregation.get("rank_items", []) or []))
            record_timing("info.aggregation_metric", str(aggregation.get("metric") or ""))
            record_timing("info.aggregation_group_by", str(aggregation.get("group_by") or ""))
            record_timing("info.aggregation_threshold", aggregation.get("threshold"))
            record_timing("info.aggregation_result_count", len(aggregation.get("rank_items", []) or []))
            status = str(aggregation.get("status") or "").strip().lower()
            if status in {"unsupported", "empty_result"}:
                record_timing("info.failed_step", f"aggregation_{status}")
        if series:
            record_timing("info.series_result_count", len(series.get("instance_projects", []) or []))
            record_timing("info.series_bucket_count", len(series.get("year_buckets", []) or []))
            status = str(series.get("status") or "").strip().lower()
            if status in {"unsupported", "empty_result"}:
                record_timing("info.failed_step", f"series_{status}")

        pattern_analysis = build_pattern_analysis_payload(
            reranked=reranked,
            intent=self.query_intent,
            hinted_limit=self.policy.hinted_limit,
            policy_limit=int(getattr(self.policy.preset, "max_ctx_items", 10) or 10),
            payload_get=_payload_get,
            aggregation=aggregation,
            series=series,
        )
        if pattern_analysis:
            items = list(pattern_analysis.get("items") or [])
            record_timing("info.pattern_kind", str(pattern_analysis.get("pattern_kind") or ""))
            record_timing("info.pattern_result_count", len(items))
            record_timing("info.pattern_subject_count", int(pattern_analysis.get("subject_count") or len(items) or 0))
            record_timing("info.pattern_support_doc_count", int(pattern_analysis.get("support_doc_count") or 0))
            status = str(pattern_analysis.get("status") or "").strip().lower()
            if status in {"unsupported", "insufficient_evidence", "empty_result"}:
                record_timing("info.failed_step", f"pattern_{status}")

        result_topn_normal = resolve_env_topn(
            "RAG_LOG_TOPN_NORMAL",
            default=3,
            fallback_keys=["RAG_LOG_TOPN_FINAL"],
        )
        result_topn_debug = resolve_env_topn("RAG_LOG_TOPN_DEBUG", default=12)
        _log_top_points("RAG.RESULT.TOP", reranked, topn=result_topn_normal, tier="normal")
        _log_top_points("RAG.RESULT.TOP.DEBUG", reranked, topn=result_topn_debug, tier="debug")

        min_ctx_items = max(1, min(2, int(os.getenv("RAG_MIN_CTX_ITEMS", "2"))))
        min_reranked = max(0, int(getattr(self.policy.preset, "min_reranked", 0) or 0))
        effective_min_reranked, min_reranked_clamp_reason = resolve_effective_min_reranked(
            intent=self.query_intent,
            mode=self.request.mode,
            base_route=self.request.base_route,
            preset_min_reranked=min_reranked,
            hinted_limit=self.policy.hinted_limit,
        )
        record_timing("info.contract_min_reranked", int(min_reranked))
        record_timing("info.contract_effective_min_reranked", int(effective_min_reranked))
        record_timing("info.contract_min_reranked_clamp_reason", min_reranked_clamp_reason)
        log_kv(
            "RAG.CONTRACT.MIN_RERANKED",
            tier="debug",
            mode=self.request.mode,
            base_route=self.request.base_route,
            preset_min_reranked=int(min_reranked),
            hinted_limit=int(max(0, int(self.policy.hinted_limit or 0))),
            effective_min_reranked=int(effective_min_reranked),
            clamp_reason=min_reranked_clamp_reason,
        )
        min_final_avg = float(os.getenv("RAG_FALLBACK_MIN_FINAL_AVG", "0"))
        min_final_max = float(os.getenv("RAG_FALLBACK_MIN_FINAL_MAX", "0"))
        score_topn = max(1, int(os.getenv("RAG_FALLBACK_SCORE_TOPN", "5")))

        contract_fail_reason = enforce_reranked_contract(
            reranked=reranked,
            min_reranked=effective_min_reranked,
            min_final_avg=min_final_avg,
            min_final_max=min_final_max,
            score_topn=score_topn,
            timing_put=record_timing,
            mode=self.request.mode,
        )

        hydrate_reranked_payloads(
            reranked=reranked,
            qdr=self.state.qdr,
            ctx_hard_limit=self.policy.ctx_hard_limit,
            min_ctx_items=min_ctx_items,
            preset_max_ctx_items=int(self.policy.preset.max_ctx_items),
            intent_payload=self.state.intent_payload,
            hinted_limit=self.policy.hinted_limit,
            coerce_int=_coerce_int,
            get_attr=_get_attr,
            hydrate_points_payload=lambda points: _hydrate_points(points, qdr=self.state.qdr),
            timing_put=record_timing,
            mode=self.request.mode,
            output_type=self.request.output_type,
            base_route=self.request.base_route,
        )
        reranked, structured_constraint = apply_structured_result_constraint(
            reranked,
            people_terms=list(self.request.people_terms or []),
            people_ids=list(self.request.people_ids or []),
            org_terms=list(self.request.org_terms or []),
            people_org_terms=list(self.request.people_org_terms or []),
            org_role=self.request.org_role,
        )
        log_kv(
            "RAG.STRUCTURED_RESULT_CONSTRAINT",
            tier="debug",
            applied=int(bool(structured_constraint.get("applied"))),
            input_count=int(structured_constraint.get("input_count", 0)),
            output_count=int(structured_constraint.get("output_count", 0)),
            dropped_count=int(structured_constraint.get("dropped_count", 0)),
            base_route=self.request.base_route,
            org_role=self.request.org_role,
            people_terms=list(self.request.people_terms or []),
            people_ids=list(self.request.people_ids or []),
            org_terms=list(self.request.org_terms or []),
            people_org_terms=list(self.request.people_org_terms or []),
        )
        if structured_constraint.get("applied") and not reranked:
            contract_fail_reason = contract_fail_reason or "structured_post_filter_empty"
            record_timing("info.contract_fail_reason", contract_fail_reason)

        multi_hop_bundle = build_multi_hop_bundle_payload(
            reranked=reranked,
            intent=self.query_intent,
            hinted_limit=self.policy.hinted_limit,
            policy_limit=int(getattr(self.policy.preset, "max_ctx_items", 10) or 10),
            payload_get=_payload_get,
        )
        if multi_hop_bundle:
            bundles = list(multi_hop_bundle.get("bundles") or [])
            record_timing("info.bundle_kind", str(multi_hop_bundle.get("bundle_kind") or ""))
            record_timing("info.bundle_target_count", len(bundles))
            record_timing("info.bundle_project_count", len(multi_hop_bundle.get("projects", []) or []))
            record_timing("info.bundle_item_count", sum(int(entry.get("item_count") or 0) for entry in bundles))
            record_timing("info.guidance_required", int(bool(multi_hop_bundle.get("guidance_message") or multi_hop_bundle.get("ambiguities"))))
            status = str(multi_hop_bundle.get("status") or "").strip().lower()
            if status in {"unsupported", "empty_result", "partial"}:
                record_timing("info.failed_step", f"bundle_{status}")

        probe_terms = collect_filter_probe_terms(
            people_terms=self.request.people_terms,
            people_ids=self.request.people_ids,
            mode=self.request.mode,
        )
        probe_result = collect_filter_probe_docs(
            reranked,
            payload_get=_payload_get,
            probe_terms=probe_terms,
            mode=self.request.mode,
        )
        if probe_result and str(probe_result.get("status") or "").strip().lower() == "confirmed_miss":
            log_kv(
                "FILTER_MISS_SUSPECTED",
                level="warning",
                mode=self.request.mode,
                filter="participant_researcher_name",
                values=probe_terms,
                topN=int(probe_result.get("inspect_topn", 0)),
                matched=int(probe_result.get("matched", 0)),
                reason=probe_result.get("reason"),
                probe_docs=probe_result.get("probe_docs", []),
                tier="debug",
            )

        return assemble_rag_result(
            reranked=reranked,
            sources=self.request.sources,
            min_ctx_items=min_ctx_items,
            preset_max_ctx_items=int(self.policy.preset.max_ctx_items),
            ctx_hard_limit=self.policy.ctx_hard_limit,
            action=self.request.action,
            base_route=self.request.base_route,
            output_type=self.request.output_type,
            mode=self.request.mode,
            query_text=self.request.query_text,
            people_terms=self.request.people_terms,
            person_ids=self.request.people_ids,
            org_terms=self.request.org_terms,
            people_org_terms=self.request.people_org_terms,
            org_role=self.request.org_role,
            timings=self.state.timings,
            t_all0=self.state.t_all0,
            stack=self.request.stack,
            plan_mode=self.request.plan_mode,
            relation=self.request.relation,
            keywords=self.request.keywords,
            aggregation=aggregation,
            series=series,
            pattern_analysis=pattern_analysis,
            multi_hop_bundle=multi_hop_bundle,
            contract_fail_reason=contract_fail_reason,
            intent_payload=self.state.intent_payload,
        )


def finalize_rag_result(
    *,
    merged_rrf: Sequence[Any],
    query_intent: Any,
    keywords: List[str],
    lex_w_eff: float,
    base_route: str,
    mode: str,
    rerank_spec: Optional[Dict[str, Any]],
    preset: Any,
    ctx_hard_limit: int,
    hinted_limit: int,
    timings: Dict[str, Any],
    qdr: Any,
    intent_payload: Any,
    people_terms: Optional[List[str]],
    people_ids: Optional[List[str]],
    people_org_terms: Optional[List[str]],
    query_text: str,
    org_terms: Optional[List[str]],
    org_role: Optional[str],
    sources: Sequence[Any],
    action: str,
    output_type: Optional[str],
    stack: str,
    plan_mode: str,
    relation: Any,
    t_all0: float,
    title_match_mode: str,
    title_match_mode_contains: str,
    title_terms: List[str],
    lookup_title_filter_policy: str,
) -> RagResult:
    """request/policy/runtime을 받아 base RAG 결과 조립 전체를 실행한다.
    ResultAssemblyRequest·Policy·Runtime를 입력으로 받는 파사드로, 실제 조립 함수의 긴 인자 목록을 증발시키지 않게 한다.
    """
    request = ResultAssemblyRequest(
        base_route=base_route,
        mode=mode,
        action=action,
        output_type=output_type,
        query_text=query_text,
        people_terms=people_terms,
        people_ids=people_ids,
        org_terms=org_terms,
        people_org_terms=people_org_terms,
        org_role=org_role,
        stack=stack,
        plan_mode=plan_mode,
        relation=relation,
        keywords=keywords,
        sources=sources,
    )
    policy = ResultAssemblyPolicy(
        rerank_spec=rerank_spec,
        preset=preset,
        ctx_hard_limit=ctx_hard_limit,
        hinted_limit=hinted_limit,
        title_match_mode=title_match_mode,
        title_match_mode_contains=title_match_mode_contains,
        title_terms=title_terms,
        lookup_title_filter_policy=lookup_title_filter_policy,
            )
    state = ResultAssemblyState(
        timings=timings,
        t_all0=t_all0,
        qdr=qdr,
        intent_payload=intent_payload,
    )
    return SearchLookupResultOrchestrator(
        merged_rrf=merged_rrf,
        query_intent=query_intent,
        keywords=keywords,
        lex_w_eff=lex_w_eff,
        request=request,
        policy=policy,
        state=state,
    ).run()


def assemble_join_rag_result(
    *,
    hop1_points: Sequence[Any],
    hop1_kind: str,
    hop1_query_text: str,
    hop1_max_items: int,
    hop2_reranked: Sequence[Any],
    hop2_label: str,
    effective_join_mode: str,
    join_pjt_ids: List[str],
    join_pjt_nos: List[str],
    preset_max_ctx_items: int,
    ctx_hard_limit: int,
    action: str,
    hop2_kind: str,
    output_type: Optional[str],
    mode: str,
    query_text: str,
    people_terms: Optional[List[str]],
    person_ids: Optional[List[str]],
    people_org_terms: Optional[List[str]],
    org_terms: Optional[List[str]],
    org_role: Optional[str],
    timings: Dict[str, Any],
    t_all0: float,
    stack: str,
    keywords: List[str],
    hits: Sequence[Any],
    aggregation: Optional[Dict[str, Any]] = None,
    series: Optional[Dict[str, Any]] = None,
    reverse_trace: Optional[Dict[str, Any]] = None,
    pattern_analysis: Optional[Dict[str, Any]] = None,
    multi_hop_bundle: Optional[Dict[str, Any]] = None,
    debug_meta: Optional[Dict[str, Any]] = None,
) -> RagResult:
    """JOIN 경로의 hop 결과를 relation 특화 RagResult로 조립한다.
    join context 번들, canonical relation summary, strategy meta를 포함해 base result와는 다른 relation view를 만든다.
    """
    min_ctx_items = max(1, min(2, int(os.getenv("RAG_MIN_CTX_ITEMS", "2"))))
    hop1_ctx, hop1_refs = build_join_hop_context(
        context_builder=build_context_with_output_type,
        points=list(hop1_points or []),
        action=action,
        base_route=hop1_kind,
        mode=mode,
        output_type=output_type,
        max_items=hop1_max_items,
        query_text=hop1_query_text,
        people_terms=list(people_terms or []),
        person_ids=list(person_ids or []),
        org_role=org_role,
    )

    t0 = time.time()
    context_bundle = build_context_bundle(
        list(hop2_reranked or []),
        min_ctx_items=min_ctx_items,
        preset_max_ctx_items=int(preset_max_ctx_items),
        ctx_hard_limit=ctx_hard_limit,
        action=action,
        base_route=hop2_kind,
        output_type=output_type,
        mode=mode,
        query_text=query_text,
        people_terms=people_terms,
        person_ids=person_ids,
        people_org_terms=people_org_terms,
        org_terms=org_terms,
        org_role=org_role,
    )
    hop2_ctx = context_bundle["context"]
    if str(output_type or "").strip().lower() == "series" and isinstance(series, dict) and str(series.get("status") or "").strip().lower() == "ok":
        hop2_ctx = _render_series_context(series) or hop2_ctx
    elif isinstance(reverse_trace, dict) and str(reverse_trace.get("status") or "").strip().lower() in {"ok", "partial"}:
        hop2_ctx = _render_reverse_trace_context(reverse_trace) or hop2_ctx
    elif isinstance(multi_hop_bundle, dict) and str(multi_hop_bundle.get("status") or "").strip().lower() in {"ok", "partial"}:
        hop2_ctx = _render_multi_hop_bundle_context(multi_hop_bundle) or hop2_ctx
    hop2_refs = context_bundle["refs"]
    render_profile = context_bundle.get("render_profile")
    canonical_evidence = context_bundle.get("canonical_evidence")
    prompt_units = context_bundle.get("prompt_units") or []
    used_tokens = int(context_bundle.get("used_tokens") or 0)
    dropped_by_floor = int(context_bundle.get("dropped_by_floor") or 0)
    dropped_by_budget = int(context_bundle.get("dropped_by_budget") or 0)
    compressed_count = int(context_bundle.get("compressed_count") or 0)
    lineages = list(context_bundle.get("lineages") or [])

    context = compose_join_context(
        hop1_ctx=hop1_ctx,
        hop2_ctx=hop2_ctx,
        hop2_label=hop2_label,
        effective_join_mode=effective_join_mode,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
    )
    refs = list(hop1_refs or []) + list(hop2_refs or [])

    timing_put(timings, "phase.build_context", time.time() - t0)
    timing_put(timings, "phase.total", time.time() - t_all0)

    log_kv(
        "RAG.CONTEXT",
        reranked_total=len(hop2_reranked or []),
        min_ctx_items=min_ctx_items,
        kept_ctx=int(context_bundle["kept_ctx"]),
        discarded_ctx=int(context_bundle["discarded_ctx"]),
        used_tokens=used_tokens,
        dropped_by_floor=dropped_by_floor,
        dropped_by_budget=dropped_by_budget,
        compressed_count=compressed_count,
        context_shape="join",
        execution_mode=mode,
        execution_base_route=hop2_kind,
        execution_output_type=output_type,
        strategy_source="execution_request",
        tier="normal",
    )
    log_kv(
        "RAG.CTX",
        tier="debug",
        ctx_len=len(context or ""),
        refs=len(refs or []),
        max_items=int(context_bundle["max_items"]),
        used_tokens=used_tokens,
        execution_mode=mode,
        execution_base_route=hop2_kind,
        output_type=output_type,
        fieldset_keys=list(context_bundle["fieldset"] or []),
        render_profile=render_profile,
        context_shape="join",
        strategy_source="execution_request",
    )

    return RagResult(
        stack=stack,
        keywords=keywords,
        hits=list(hits or []),
        reranked_hits=list(hop2_reranked or []),
        context=context,
        refs=refs,
        timings=timings,
        aggregation=aggregation,
        series=series,
        reverse_trace=reverse_trace,
        pattern_analysis=pattern_analysis,
        multi_hop_bundle=multi_hop_bundle,
        debug_meta=debug_meta,
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        prompt_units=prompt_units,
        used_tokens=used_tokens,
        kept_ctx=int(context_bundle.get("kept_ctx") or 0),
        discarded_ctx=int(context_bundle.get("discarded_ctx") or 0),
        dropped_by_floor=dropped_by_floor,
        dropped_by_budget=dropped_by_budget,
        compressed_count=compressed_count,
        lineages=lineages,
    )
