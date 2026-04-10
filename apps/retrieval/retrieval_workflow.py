from __future__ import annotations



import asyncio

from dataclasses import dataclass, replace as dc_replace

from typing import Any, Dict, Optional



from apps.evidence.canonical_context import build_prev_context_canonical_text
from apps.evidence.canonical_evidence import build_canonical_evidence
from apps.conversation.followup_anchor import is_child_anchor_source, parse_display_limit
from apps.evidence.detail_contract import (
    build_detail_answer_context,
    build_detail_prompt_context,
    compute_detail_coverage,
    coverage_satisfies_fields,
    extract_requested_fields,
    make_entity_cache_key,
)
from apps.evidence.result_set import ResultItem, RetrievalBundle

from apps.conversation.view_state import (
    DETAIL_CACHE_SCHEMA_VERSION,
    DetailCacheEntry,
    FocusEntity,
    append_recent_mentions,
    build_display_snapshot,
    focus_entity_from_detail,
    get_active_subject_entity,
    index_focus_subjects,
    index_snapshot_subjects,
    recent_mention_from_display_item,
    recent_mention_from_focus_entity,
    set_active_child_anchor_scope,
    set_active_focus_scope,
    set_active_result_scope,
)
from apps.retrieval.rag_retriever import CustomRAGRetriever, has_active_anchor_seed, resolve_rag_queries

from apps.api.contracts.workflow_models import KnowledgeSufficiency
from apps.api.runtime_helpers import log_event, logger, measure_latency
from apps.api.streaming.contracts import AnswerArtifact

from apps.conversation.entity_reference import ClarificationRequest, ResolvedEntityRef
from apps.conversation.fact_followup_resolver import resolve_followup_from_facts

from apps.conversation.followup_resolution import build_followup_clarification_message, build_followup_clarification_payload, resolve_entity_ref_from_strategy_meta, should_short_circuit_followup_clarification
from apps.conversation.raw_payload_store import sync_active_anchor_record, upsert_raw_payload_records
from apps.chat.llm_json import sanitize_llm_json
from apps.chat.llm_runtime import build_llm
from apps.planner.planner_contract import StrategyViolation
from apps.platform.settings import MAX_TOP_K_SIZE
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import Tool





def _get_normalized_intent(state: Any) -> Any:

    """workflow state에서 normalized_intent만 안전하게 꺼낸다."""

    payload = getattr(state, "intent_payload", None)

    return getattr(payload, "normalized_intent", None) if payload else None





def _get_strategy(state: Any) -> Any:

    """workflow state에서 현재 strategy 객체를 반환한다."""

    return getattr(state, "strategy", None)





def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:

    """여러 source를 순서대로 보며 key에 해당하는 첫 non-None 값을 고른다."""

    for source in sources:

        if source is None:

            continue

        if isinstance(source, dict):

            value = source.get(key)

        else:

            value = getattr(source, key, None)

        if value is not None:

            return value

    return default





_DISPLAY_LIMIT_SENTINEL = 10**9
_DETAIL_SYNTHETIC_TITLE_PRIMARY_TYPES = {
    "aggregation",
    "series",
    "pattern_analysis",
    "reverse_trace",
    "multi_hop_bundle",
}




def _coerce_positive_int(value: Any) -> Optional[int]:
    try:

        number = int(value)

    except Exception:

        return None

    return number if number >= 1 else None


def _first_text_value(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _build_fresh_retrieval_query(
    *,
    question: str,
    resolved_entity_ref: ResolvedEntityRef | None,
    focus_entity: Any = None,
    latest_focus_entity: Any = None,
) -> str:
    raw_question = str(question or "").strip()
    focus_entity = focus_entity or latest_focus_entity
    anchor_query = None
    if isinstance(resolved_entity_ref, ResolvedEntityRef):
        primary_seed = _pick_primary_seed_map(
            dict(resolved_entity_ref.seed_map or {}),
            getattr(resolved_entity_ref, "entity_kind", None),
        )
        if primary_seed:
            anchor_query = _first_text_value(*next(iter(primary_seed.values()), []))
    anchor_query = _first_text_value(
        anchor_query,
        getattr(focus_entity, "title_text", None),
        getattr(focus_entity, "pjt_id", None),
        getattr(focus_entity, "pjt_no", None),
        getattr(focus_entity, "rst_id", None),
        getattr(focus_entity, "person_no", None),
        getattr(focus_entity, "org_id", None),
        getattr(focus_entity, "org_code", None),
        getattr(focus_entity, "biz_no", None),
    )
    if not anchor_query:
        return raw_question
    if not raw_question:
        return anchor_query
    if anchor_query.lower() in raw_question.lower():
        return raw_question
    return f"{anchor_query} {raw_question}".strip()


def _should_probe_llm_freshness_for_detail_followup(
    *,
    state: Any,
    query_intent: Any,
    qa: Any,
    strategy_meta: dict[str, Any],
) -> bool:
    output_type = str(_pick_attr(query_intent, qa, key="output_type", default="summary") or "summary").strip().lower()
    followup_resolution_status = str(strategy_meta.get("followup_resolution_status") or "").strip().lower()
    if output_type != "detail":
        return False
    if not has_active_anchor_seed(state):
        return False
    return bool(
        strategy_meta.get("explicit_followup")
        or strategy_meta.get("anchor_source")
        or followup_resolution_status == "resolved"
    )


def _detail_uses_synthetic_title(*sources: Any) -> bool:
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_type = str(source.get("source_type") or source.get("doc_type") or "").strip().lower()
        if source_type in _DETAIL_SYNTHETIC_TITLE_PRIMARY_TYPES:
            return True
    return False


def _resolve_detail_coverage_title(
    document: Optional[dict[str, Any]],
    canonical_item: Optional[dict[str, Any]],
    *,
    anchor_title: Optional[str] = None,
) -> Optional[str]:
    doc = document if isinstance(document, dict) else {}
    canonical = canonical_item if isinstance(canonical_item, dict) else {}
    if _detail_uses_synthetic_title(doc, canonical):
        return None

    facts = canonical.get("facts") or {}
    meta_basic = doc.get("meta_basic") or {}
    meta_detail = doc.get("meta_detail") or {}
    return _first_text_value(
        doc.get("title1"),
        doc.get("kor_pjt_nm"),
        meta_basic.get("kor_pjt_nm"),
        meta_detail.get("kor_pjt_nm"),
        facts.get("title"),
        anchor_title,
        doc.get("title_text"),
        doc.get("title2"),
        doc.get("eng_pjt_nm"),
        meta_basic.get("eng_pjt_nm"),
        meta_detail.get("eng_pjt_nm"),
        doc.get("title"),
    )




def _resolve_retrieval_budget(question_analysis: Any, *, max_top_k_size: int) -> int:
    """Use the assembled question-analysis limit as the retrieval budget source of truth."""
    planner_limit = _coerce_positive_int(getattr(question_analysis, "limit", None))
    return min(planner_limit or max_top_k_size, max_top_k_size)


def _resolve_runtime_top_k(question_analysis: Any, *, max_top_k_size: int, exact_detail_lookup: bool) -> int:
    if exact_detail_lookup:
        return 1
    retrieval_budget = _resolve_retrieval_budget(question_analysis, max_top_k_size=max_top_k_size)
    visible_limit = _resolve_display_request(question_analysis)
    overfetch_budget = max(visible_limit, visible_limit * 3)
    return min(max(retrieval_budget, overfetch_budget), max_top_k_size)




def _resolve_display_request(question_analysis: Any) -> int:

    requested = _coerce_positive_int(getattr(question_analysis, "display_limit", None))

    if requested is None:

        requested = _coerce_positive_int(getattr(question_analysis, "limit", None)) or 1

    return requested





def _extract_explicit_count(question: Any) -> Optional[int]:

    count = parse_display_limit(str(question or ""), default=_DISPLAY_LIMIT_SENTINEL)

    return None if count == _DISPLAY_LIMIT_SENTINEL else int(count)





def _classify_docs_kind(docs: list[dict[str, Any]]) -> str:

    if not docs:

        return "item_list"

    source_types = {str(item.get("source_type") or "").strip().lower() for item in docs if isinstance(item, dict)}

    source_types.discard("")

    item_like_types = {"hit", "canonical_item", "item", "document"}

    if source_types and source_types.issubset(item_like_types):

        return "item_list"

    if "aggregation" in source_types:

        return "collection_wrapper"

    return "item_list"





def _build_retrieval_bundle(
    *,
    docs: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    render_profile: dict[str, Any],
    raw_count: int,
    clarification: dict[str, Any] | None = None,
    no_result_message: str | None = None,
    answer_context_text: str = "",
    debug_answer_context_text: str = "",
    context_source: str = "pipeline_context",
) -> RetrievalBundle:
    items: list[ResultItem] = []

    max_len = max(len(docs or []), len(canonical_evidence or []))

    for index in range(max_len):

        display = docs[index] if index < len(docs) and isinstance(docs[index], dict) else {}

        canonical = canonical_evidence[index] if index < len(canonical_evidence) and isinstance(canonical_evidence[index], dict) else {}

        items.append(ResultItem(canonical=dict(canonical), display=dict(display), raw_hit=dict(display)))

    return RetrievalBundle(
        items=items,
        render_profile=dict(render_profile or {}),
        raw_count=int(raw_count or 0),
        context_kind=str((render_profile or {}).get("context_kind") or "project").strip().lower() or "project",
        clarification=clarification,
        no_result_message=no_result_message,
        answer_context_text=str(answer_context_text or ""),
        debug_answer_context_text=str(debug_answer_context_text or ""),
        context_source=str(context_source or "pipeline_context"),
    )




def _build_answer_artifact(*, text: str, answer_kind: str, answer_source: str, clarification: ClarificationRequest | None = None) -> AnswerArtifact:
    return AnswerArtifact(
        text=str(text or ""),
        answer_kind=answer_kind,
        stream_metrics={"content_chars": len(str(text or "")), "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        clarification=clarification,
        meta={"answer_source": answer_source},
    )


def _pick_primary_seed_map(seed_map: dict[str, list[str]], preferred_entity_kind: str | None) -> dict[str, list[str]]:
    kind = str(preferred_entity_kind or "").strip().lower()
    if kind == "perf":
        order = ("rst_id", "doi", "issn", "pjt_id", "pjt_no", "person_no", "org_id", "org_code", "biz_no")
    elif kind == "project":
        order = ("pjt_id", "pjt_no", "rst_id", "doi", "issn", "person_no", "org_id", "org_code", "biz_no")
    elif kind == "people":
        order = ("person_no", "pjt_id", "pjt_no", "rst_id", "org_id", "org_code", "biz_no", "doi", "issn")
    elif kind == "org":
        order = ("org_id", "org_code", "biz_no", "pjt_id", "pjt_no", "rst_id", "person_no", "doi", "issn")
    else:
        order = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")

    for key in order:
        values = seed_map.get(key) or []
        normalized = [str(v).strip() for v in values if str(v).strip()]
        if normalized:
            return {key: [normalized[0]]}
    return {}


def _resolve_detail_entity_ref(
    *,
    strategy_meta: dict[str, Any],
    focus_entity: Any,
    ids_map: dict[str, list[str]] | None = None,
    preferred_entity_kind: str | None = None,
) -> ResolvedEntityRef | ClarificationRequest | None:
    resolved = resolve_entity_ref_from_strategy_meta(strategy_meta)
    if isinstance(resolved, ClarificationRequest):
        return resolved
    normalized_ids = dict(ids_map or {})
    primary_ids = _pick_primary_seed_map(normalized_ids, preferred_entity_kind)
    if primary_ids:
        key = next(iter(primary_ids.keys()))
        entity_kind = (
            "perf" if key in {"rst_id", "doi", "issn"}
            else ("people" if key == "person_no" else ("org" if key in {"org_id", "org_code", "biz_no"} else "project"))
        )
        return ResolvedEntityRef(entity_kind=entity_kind, seed_map=primary_ids, source="explicit_id")
    if isinstance(resolved, ResolvedEntityRef):
        primary_seed = _pick_primary_seed_map(dict(resolved.seed_map or {}), preferred_entity_kind)
        if primary_seed:
            key = next(iter(primary_seed.keys()))
            entity_kind = (
                "perf" if key in {"rst_id", "doi", "issn"}
                else ("people" if key == "person_no" else ("org" if key in {"org_id", "org_code", "biz_no"} else "project"))
            )
            return ResolvedEntityRef(
                entity_kind=entity_kind,
                seed_map=primary_seed,
                source=resolved.source,
                display_view_id=resolved.display_view_id,
                display_rank=resolved.display_rank,
                anchor_fields=dict(resolved.anchor_fields or {}),
            )
    anchor_source = str((strategy_meta or {}).get("anchor_source") or "").strip().lower()
    if anchor_source != "detail_lookup":
        return None
    if focus_entity is None:
        return None
    seed_map: dict[str, list[str]] = {}
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        value = getattr(focus_entity, key, None)
        if str(value or "").strip():
            seed_map[key] = [str(value).strip()]
            break
    if not seed_map:
        return None
    kind = str(getattr(focus_entity, "kind", "") or "project").strip().lower() or "project"
    return ResolvedEntityRef(
        entity_kind=kind if kind in {"project", "perf", "people", "org"} else "project",
        seed_map=seed_map,
        source="detail_lookup",
        display_view_id=getattr(focus_entity, "view_id", None),
        display_rank=getattr(focus_entity, "display_rank", None),
        anchor_fields={"title_text": getattr(focus_entity, "title_text", None)},
    )




def _build_display_docs_from_canonical(canonical_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    synthetic_docs: list[dict[str, Any]] = []
    for index, item in enumerate(canonical_evidence, start=1):
        if not isinstance(item, dict):
            continue
        ids = item.get("ids") or {}

        facts = item.get("facts") or {}

        synthetic_docs.append(

            {

                "title": facts.get("title"),

                "title_text": facts.get("title"),

                "source_index": index,

                "source_type": "canonical_item",

                "doc_id": ids.get("doc_id"),

                "pjt_id": ids.get("pjt_id"),

                "pjt_no": ids.get("pjt_no"),

            }
        )
    return synthetic_docs


def _subject_identity(kind: str, *, display_name: str | None, ids_map: dict[str, list[str]]) -> str:
    if kind == "people" and ids_map.get("person_no"):
        return f"people:person_no:{ids_map['person_no'][0]}"
    if kind == "org":
        if ids_map.get("org_id"):
            return f"org:org_id:{ids_map['org_id'][0]}"
        if ids_map.get("org_code"):
            return f"org:org_code:{ids_map['org_code'][0]}"
        if ids_map.get("biz_no"):
            return f"org:biz_no:{ids_map['biz_no'][0]}"
    if kind == "perf":
        if ids_map.get("rst_id"):
            return f"perf:rst_id:{ids_map['rst_id'][0]}"
        if ids_map.get("doi"):
            return f"perf:doi:{ids_map['doi'][0]}"
        if ids_map.get("issn"):
            return f"perf:issn:{ids_map['issn'][0]}"
    return f"{kind}:name:{str(display_name or '').strip().lower()}"


def _append_unique_subject_candidate(
    candidates: list[dict[str, Any]],
    seen: set[str],
    *,
    kind: str,
    display_name: str | None,
    ids_map: dict[str, list[str]],
    item: Any,
    source: str,
) -> None:
    display = str(display_name or "").strip()
    normalized_ids = {
        key: [str(value).strip() for value in values if str(value).strip()]
        for key, values in (ids_map or {}).items()
        if any(str(value).strip() for value in values)
    }
    if not display and not normalized_ids:
        return
    identity = _subject_identity(kind, display_name=display, ids_map=normalized_ids)
    if identity in seen:
        return
    seen.add(identity)
    candidates.append(
        {
            "kind": kind,
            "display_name": display or None,
            "ids_map": normalized_ids,
            "display_rank": getattr(item, "display_rank", None),
            "item_pjt_id": getattr(item, "pjt_id", None),
            "item_pjt_no": getattr(item, "pjt_no", None),
            "item_rst_id": getattr(item, "rst_id", None),
            "item_title": getattr(item, "title_text", None),
            "source": source,
        }
    )


def _collect_subject_candidates_from_item(item: Any, *, subject_kind: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    child_refs = list(getattr(item, "child_refs", []) or [])

    if subject_kind == "people":
        person_no = str(getattr(item, "person_no", None) or "").strip()
        if person_no:
            researchers = [str(value).strip() for value in (getattr(item, "researchers", []) or []) if str(value).strip()]
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="people",
                display_name=(researchers[0] if len(set(researchers)) == 1 else None),
                ids_map={"person_no": [person_no]},
                item=item,
                source="item_person_no",
            )
        for ref in child_refs:
            if str(getattr(ref, "kind", "") or "").strip().lower() != "people":
                continue
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="people",
                display_name=str(getattr(ref, "display_name", "") or "").strip() or None,
                ids_map=dict(getattr(ref, "ids_map", {}) or {}),
                item=item,
                source="child_ref",
            )
        researchers = [str(value).strip() for value in (getattr(item, "researchers", []) or []) if str(value).strip()]
        if len(set(researchers)) == 1:
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="people",
                display_name=researchers[0],
                ids_map={},
                item=item,
                source="researchers",
            )
        return candidates

    if subject_kind == "org":
        org_ids_map = {
            "org_id": [str(getattr(item, "org_id", None) or "").strip()] if str(getattr(item, "org_id", None) or "").strip() else [],
            "org_code": [str(getattr(item, "org_code", None) or "").strip()] if str(getattr(item, "org_code", None) or "").strip() else [],
            "biz_no": [str(getattr(item, "biz_no", None) or "").strip()] if str(getattr(item, "biz_no", None) or "").strip() else [],
        }
        if any(org_ids_map.values()):
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="org",
                display_name=str(getattr(item, "lead_org", None) or "").strip() or None,
                ids_map=org_ids_map,
                item=item,
                source="item_org_id",
            )
        for ref in child_refs:
            if str(getattr(ref, "kind", "") or "").strip().lower() != "org":
                continue
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="org",
                display_name=str(getattr(ref, "display_name", "") or "").strip() or None,
                ids_map=dict(getattr(ref, "ids_map", {}) or {}),
                item=item,
                source="child_ref",
            )
        lead_org = str(getattr(item, "lead_org", None) or "").strip()
        if lead_org:
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="org",
                display_name=lead_org,
                ids_map={},
                item=item,
                source="lead_org",
            )
        participant_org = [str(value).strip() for value in (getattr(item, "participant_org", []) or []) if str(value).strip()]
        if len(set(participant_org)) == 1:
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="org",
                display_name=participant_org[0],
                ids_map={},
                item=item,
                source="participant_org",
            )
        return candidates

    if subject_kind == "perf":
        perf_ids_map = {
            "rst_id": [str(getattr(item, "rst_id", None) or "").strip()] if str(getattr(item, "rst_id", None) or "").strip() else [],
            "doi": [str(getattr(item, "doi", None) or "").strip()] if str(getattr(item, "doi", None) or "").strip() else [],
            "issn": [str(getattr(item, "issn", None) or "").strip()] if str(getattr(item, "issn", None) or "").strip() else [],
        }
        if any(perf_ids_map.values()):
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="perf",
                display_name=str(getattr(item, "title_text", "") or "").strip() or None,
                ids_map=perf_ids_map,
                item=item,
                source="item_perf_id",
            )
        for ref in child_refs:
            if str(getattr(ref, "kind", "") or "").strip().lower() != "perf":
                continue
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="perf",
                display_name=str(getattr(ref, "display_name", "") or "").strip() or None,
                ids_map=dict(getattr(ref, "ids_map", {}) or {}),
                item=item,
                source="child_ref",
            )
        title_text = str(getattr(item, "title_text", "") or "").strip()
        if title_text:
            _append_unique_subject_candidate(
                candidates,
                seen,
                kind="perf",
                display_name=title_text,
                ids_map={},
                item=item,
                source="title_text",
            )
        return candidates

    return candidates


def _promote_unique_list_subject_anchor(*, snapshot: Any, parent_focus: Any) -> tuple[FocusEntity | None, dict[str, Any]]:
    subject_kind = str(getattr(snapshot, "context_kind", "") or "").strip().lower()
    if subject_kind not in {"people", "org", "perf"}:
        return None, {"subject_kind": subject_kind or None, "candidate_count": 0, "source": None}

    candidates: list[dict[str, Any]] = []
    for item in list(getattr(snapshot, "items", []) or []):
        candidates.extend(_collect_subject_candidates_from_item(item, subject_kind=subject_kind))

    unique_candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        identity = _subject_identity(
            subject_kind,
            display_name=candidate.get("display_name"),
            ids_map=dict(candidate.get("ids_map") or {}),
        )
        if identity in seen:
            continue
        seen.add(identity)
        unique_candidates.append(candidate)

    if len(unique_candidates) != 1:
        return None, {
            "subject_kind": subject_kind,
            "candidate_count": len(unique_candidates),
            "source": None,
        }

    candidate = unique_candidates[0]
    ids_map = dict(candidate.get("ids_map") or {})
    anchor = FocusEntity(
        kind=subject_kind,
        source="child_list_unique_subject",
        view_id=getattr(snapshot, "view_id", None),
        display_rank=candidate.get("display_rank"),
        pjt_id=_first_text_value(getattr(parent_focus, "pjt_id", None), candidate.get("item_pjt_id")),
        pjt_no=_first_text_value(getattr(parent_focus, "pjt_no", None), candidate.get("item_pjt_no")),
        rst_id=(ids_map.get("rst_id") or [candidate.get("item_rst_id"), None])[0],
        person_no=(ids_map.get("person_no") or [None])[0],
        org_id=(ids_map.get("org_id") or [None])[0],
        org_code=(ids_map.get("org_code") or [None])[0],
        biz_no=(ids_map.get("biz_no") or [None])[0],
        doi=(ids_map.get("doi") or [None])[0],
        issn=(ids_map.get("issn") or [None])[0],
        title_text=str(candidate.get("display_name") or candidate.get("item_title") or "").strip() or None,
        lead_org=getattr(parent_focus, "lead_org", None),
    )
    return anchor, {
        "subject_kind": subject_kind,
        "candidate_count": len(unique_candidates),
        "source": candidate.get("source"),
        "title_text": getattr(anchor, "title_text", None),
        "identity_key": _subject_identity(subject_kind, display_name=getattr(anchor, "title_text", None), ids_map=ids_map),
    }




@dataclass(frozen=True)

class DisplayPayloadBundle:

    snapshot_documents: list[dict[str, Any]]

    snapshot_canonical_evidence: list[dict[str, Any]]

    docs_count: int

    canonical_count: int

    docs_kind: str

    canonical_kind: str

    display_source: str





def _is_equivalent_focus_entity(current: Any, incoming: Any) -> bool:

    keys = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn", "doc_id")

    current_values = tuple(str(getattr(current, key, "") or "").strip() for key in keys)

    incoming_values = tuple(str(getattr(incoming, key, "") or "").strip() for key in keys)

    return any(current_values) and current_values == incoming_values


def _log_scope_transition(*, state: Any, view_state: Any) -> None:
    active_scope = getattr(view_state, "active_scope", None)
    parent_chain = getattr(active_scope, "parent_chain", None) or []
    if not parent_chain:
        return
    entry = parent_chain[-1]
    if not isinstance(entry, dict):
        return
    log_event(
        "SCOPE.TRANSITION",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        scope_kind=entry.get("scope_kind"),
        view_id=entry.get("view_id"),
        entity_kind=entry.get("entity_kind"),
        anchor_kind=entry.get("anchor_kind"),
        reason=entry.get("reason"),
    )


def _build_detail_coverage_input(
    document: Optional[dict[str, Any]],
    canonical_item: Optional[dict[str, Any]],
    *,
    anchor_title: Optional[str] = None,
) -> dict[str, Any]:
    merged = dict(document or {}) if isinstance(document, dict) else {}
    if not isinstance(canonical_item, dict):
        return merged


    ids = dict(canonical_item.get("ids") or {})

    facts = dict(canonical_item.get("facts") or {})

    roles = dict(canonical_item.get("roles") or {})



    if ids and not isinstance(merged.get("ids"), dict):

        merged["ids"] = ids

    elif ids:

        merged["ids"] = {**ids, **dict(merged.get("ids") or {})}



    if facts and not isinstance(merged.get("facts"), dict):

        merged["facts"] = facts

    elif facts:

        merged["facts"] = {**facts, **dict(merged.get("facts") or {})}



    if roles and not isinstance(merged.get("roles"), dict):

        merged["roles"] = roles

    elif roles:

        merged["roles"] = {**roles, **dict(merged.get("roles") or {})}



    resolved_title = _resolve_detail_coverage_title(merged, canonical_item, anchor_title=anchor_title)
    if resolved_title:
        merged["title"] = resolved_title
        merged["title_text"] = resolved_title
    elif facts.get("title") and not str(merged.get("title") or merged.get("title_text") or "").strip():
        merged["title"] = facts.get("title")
        merged["title_text"] = facts.get("title")
    if facts.get("year") and not str(merged.get("stan_yr") or "").strip():

        merged["stan_yr"] = facts.get("year")

    if ids.get("pjt_id") and not str(merged.get("pjt_id") or "").strip():

        merged["pjt_id"] = ids.get("pjt_id")

    if ids.get("pjt_no") and not str(merged.get("pjt_no") or "").strip():

        merged["pjt_no"] = ids.get("pjt_no")

    if ids.get("rst_id") and not str(merged.get("rst_id") or "").strip():

        merged["rst_id"] = ids.get("rst_id")

    if ids.get("person_no") and not str(merged.get("person_no") or merged.get("hm_id") or "").strip():

        merged["person_no"] = ids.get("person_no")

    if ids.get("org_id") and not str(merged.get("org_id") or "").strip():

        merged["org_id"] = ids.get("org_id")

    if ids.get("org_code") and not str(merged.get("org_code") or merged.get("org_cd") or "").strip():

        merged["org_code"] = ids.get("org_code")

    if ids.get("biz_no") and not str(merged.get("biz_no") or merged.get("org_no") or "").strip():

        merged["biz_no"] = ids.get("biz_no")

    if ids.get("doi") and not str(merged.get("doi") or "").strip():

        merged["doi"] = ids.get("doi")

    if ids.get("issn") and not str(merged.get("issn") or "").strip():

        merged["issn"] = ids.get("issn")

    lead_org = ((roles.get("lead_org_name") or [None])[0]) if isinstance(roles.get("lead_org_name"), list) else None

    if lead_org and not str(merged.get("org_nm") or "").strip():

        merged["org_nm"] = lead_org

    if (roles.get("participant_org_name") or []) and not isinstance(merged.get("prtcp_org"), list):

        merged["prtcp_org"] = [{"org_nm": value} for value in (roles.get("participant_org_name") or []) if str(value).strip()]

    if (roles.get("participant_researcher_name") or []) and not isinstance(merged.get("prtcp_mp"), list):

        merged["prtcp_mp"] = [{"hm_nm": value} for value in (roles.get("participant_researcher_name") or []) if str(value).strip()]

    if isinstance(merged.get("prtcp_mp"), list) and (roles.get("people_affiliation_org_name") or []):

        for item, affiliation in zip(merged.get("prtcp_mp") or [], roles.get("people_affiliation_org_name") or []):

            if isinstance(item, dict) and affiliation and not str(item.get("blng_org_nm") or "").strip():

                item["blng_org_nm"] = affiliation

    return merged



def _detail_anchor_active(*, query_intent: Any) -> bool:

    ids_map = getattr(query_intent, "ids_map", None) or {}

    if isinstance(query_intent, dict):

        ids_map = query_intent.get("ids_map") or {}

    return any(

        ids_map.get(key)

        for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")

    )





def _normalize_display_payloads(

    *,

    docs: list[dict[str, Any]],

    canonical_evidence: list[dict[str, Any]],

    base_route: str,

    output_type: str,

    requested_count: int,

    explicit_count: Optional[int],


    request_id: str,

    conversation_id: str,

) -> DisplayPayloadBundle:

    """Normalize display inputs and choose the snapshot source of truth."""

    normalized_docs = [item for item in (docs or []) if isinstance(item, dict)]

    normalized_canonical = [item for item in (canonical_evidence or []) if isinstance(item, dict)]



    if len(normalized_canonical) < len(normalized_docs):

        canonical_before = len(normalized_canonical)

        for rank, item in enumerate(normalized_docs[canonical_before:], start=canonical_before + 1):

            normalized_canonical.append(

                build_canonical_evidence(

                    item,

                    rank=rank,

                    base_route=base_route,

                    output_type=output_type,

                ).to_dict()

            )

        derived_count = len(normalized_canonical) - canonical_before

        if derived_count > 0:

            log_event(

                "RAG.CANONICAL_EVIDENCE.DERIVED",

                request_id=request_id,

                conversation_id=conversation_id,

                docs_count=len(normalized_docs),

                canonical_count_before=canonical_before,

                canonical_count_after=len(normalized_canonical),

                derived_count=derived_count,

                base_route=base_route,

                output_type=output_type,

            )



    docs_kind = _classify_docs_kind(normalized_docs)

    canonical_kind = "item_list"

    fallback_threshold = int(explicit_count or 0) if explicit_count is not None else int(requested_count or 0)
    promoted_canonical_axis = False
    if (
        str(output_type or "").strip().lower() in {"list", "relation", "comparison", "series", "stats"}
        and normalized_canonical
        and docs_kind == "collection_wrapper"
        and (
            str(base_route or "").strip().lower() in {"people", "org"}
            or len(normalized_canonical) >= max(1, fallback_threshold)
        )
    ):
        normalized_docs = _build_display_docs_from_canonical(normalized_canonical)
        docs_kind = _classify_docs_kind(normalized_docs)
        promoted_canonical_axis = True
        log_event(

            "RAG.DISPLAY_CANONICAL_AXIS.PROMOTED",

            request_id=request_id,

            conversation_id=conversation_id,

            docs_count_before=len(docs or []),

            canonical_count=len(normalized_canonical),

            output_type=output_type,

        )



    docs_count = len(normalized_docs)

    canonical_count = len(normalized_canonical)

    display_source = "canonical_axis" if promoted_canonical_axis else "docs"



    if docs_count != canonical_count:

        aligned_count = min(docs_count, canonical_count)

        fallback_threshold = int(explicit_count or 0) if explicit_count is not None else int(requested_count or 0)

        prefer_canonical = (
            str(output_type or "").strip().lower() == "list"
            and canonical_count > docs_count
            and (
                str(base_route or "").strip().lower() in {"people", "org"}
                or canonical_count >= max(1, fallback_threshold)
            )
            and (docs_kind == "collection_wrapper" or docs_count < max(1, fallback_threshold))
        )
        if prefer_canonical:

            display_source = "synthetic_from_canonical"

            snapshot_documents = _build_display_docs_from_canonical(normalized_canonical)

            snapshot_canonical = normalized_canonical

        else:

            snapshot_documents = normalized_docs[:aligned_count]

            snapshot_canonical = normalized_canonical[:aligned_count]

        log_event(

            "RAG.DISPLAY_INPUT_MISMATCH",

            request_id=request_id,

            conversation_id=conversation_id,

            docs_count=docs_count,

            canonical_count=canonical_count,

            aligned_count=aligned_count,

            base_route=base_route,

            output_type=output_type,

            docs_kind=docs_kind,

            canonical_kind=canonical_kind,

            display_source=display_source,

        )

    else:

        snapshot_documents = normalized_docs

        snapshot_canonical = normalized_canonical



    return DisplayPayloadBundle(

        snapshot_documents=snapshot_documents,

        snapshot_canonical_evidence=snapshot_canonical,

        docs_count=docs_count,

        canonical_count=canonical_count,

        docs_kind=docs_kind,

        canonical_kind=canonical_kind,

        display_source=display_source,

    )





@measure_latency("knowledge_sufficiency")
async def node_knowledge_sufficiency(state: Any) -> Dict[str, Any]:

    """이전 문맥만으로 답할 수 있는지 판단하고, 필요하면 retrieval 의도를 만든다.



    planner/strategy 신호가 이미 충분히 강하면 LLM 판단을 건너뛰고 즉시 high로 고정해

    불안정한 우회를 줄인다.

    """

    history = state.chat_history[-6:]

    history_str = "\n".join([f"{type(message).__name__}: {message.content}" for message in history])



    qa = state.question_analysis

    query_intent = _get_normalized_intent(state)

    strategy = _get_strategy(state)

    intent_payload = getattr(state, "intent_payload", None)

    strategy_meta = dict(getattr(intent_payload, "strategy_meta", None) or {})

    if should_short_circuit_followup_clarification(strategy_meta):
        no_result_message = build_followup_clarification_message(strategy_meta)
        clarification = build_followup_clarification_payload(strategy_meta)
        log_event(
            "CLARIFICATION.ISSUED",
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            clarification_type=(clarification or {}).get("clarification_type") if isinstance(clarification, dict) else None,
            reason=dict(strategy_meta.get("clarification_payload") or {}).get("reason"),
        )
        result = KnowledgeSufficiency(
            requires_new_knowledge="low",
                search_intent="followup clarification required",
                retrieval_query=state.messages[-1].content,
            confidence=1.0,

        )

        log_event(

            "KS.RESULT",

            request_id=state.request_id,

            conversation_id=state.conversation_id,

            stage="knowledge_sufficiency",

            requires_new_knowledge=result.requires_new_knowledge,

            retrieval_query=result.retrieval_query,

            confidence=round(float(result.confidence), 2),

            followup_resolution_status=strategy_meta.get("followup_resolution_status"),

            early_exit_reason="followup_clarification",

        )

        return {"knowledge_sufficiency": result, "no_result_message": no_result_message, "clarification": clarification}

    retrieval_query = _pick_attr(query_intent, qa, key="retrieval_query", default=state.messages[-1].content)
    action = _pick_attr(query_intent, strategy, qa, key="action")
    detail_followup_freshness_probe = _should_probe_llm_freshness_for_detail_followup(
        state=state,
        query_intent=query_intent,
        qa=qa,
        strategy_meta=strategy_meta,
    )

    search_required_actions = {
        "list",
        "detail",
        "relation",
        "stats",

        "id_exact",

        "id_fuzzy",

        "topic",

        "content",

    }

    if (query_intent or strategy or qa) and not state.prev_context and not detail_followup_freshness_probe:
        result = KnowledgeSufficiency(
            requires_new_knowledge="high",
            search_intent="이전 문맥이 없어 새로운 검색이 필요합니다.",

            retrieval_query=retrieval_query,

            confidence=1.0,

        )

        log_event(

            "KS.RESULT",

            request_id=state.request_id,

            conversation_id=state.conversation_id,

            stage="knowledge_sufficiency",

            requires_new_knowledge=result.requires_new_knowledge,

            retrieval_query=result.retrieval_query,

            confidence=round(float(result.confidence), 2),

        )
        return {"knowledge_sufficiency": result}

    if action in search_required_actions and not detail_followup_freshness_probe:
        result = KnowledgeSufficiency(
            requires_new_knowledge="high",
            search_intent=f"query_intent action={action} requires retrieval",
            retrieval_query=retrieval_query,
            prefer_fresh_retrieval=False,
            confidence=1.0,
        )
        log_event(
            "KS.RESULT",
            request_id=state.request_id,
            conversation_id=state.conversation_id,

            stage="knowledge_sufficiency",
            requires_new_knowledge=result.requires_new_knowledge,
            retrieval_query=result.retrieval_query,
            prefer_fresh_retrieval=int(bool(getattr(result, "prefer_fresh_retrieval", False))),
            confidence=round(float(result.confidence), 2),
            early_exit_reason="search_required_action",
        )
        return {"knowledge_sufficiency": result}

    llm = build_llm(model_name="gemma_triton_0")
    parser = PydanticOutputParser(pydantic_object=KnowledgeSufficiency)


    render_profile = getattr(state, "render_profile", None) or {}

    base_route = str(

        _pick_attr(render_profile, query_intent, qa, key="context_kind")

        or _pick_attr(query_intent, qa, key="base_route")

        or _pick_attr(qa, key="head")

        or "project"

    ).strip().lower() or "project"

    output_type = str(

        _pick_attr(render_profile, query_intent, qa, key="name")

        or _pick_attr(query_intent, qa, key="output_type")

        or "summary"

    ).strip().lower() or "summary"

    prev_context_str = build_prev_context_canonical_text(

        state.prev_context,

        base_route=base_route,

        output_type=output_type,

        render_profile_name=output_type,

        render_profile_kind=base_route,

        max_chars=0,

    )



    system_prompt = (

        "당신은 추가 검색 필요성을 판단하는 분석기입니다.\n"

        "이 시스템에서 사용하는 용어는 모두 국내 연구개발(R&D) 행정 및 제도 맥락으로 해석합니다.\n"

        "[이전 대화]와 [참고 문서]를 기반으로, [현재 질문]에 답하기 위해 새로운 검색이 필요한지 판단하세요.\n\n"

        "판단 기준:\n"

        "1. requires_new_knowledge:\n"

        "   - low: [참고 문서]만으로 충분히 답할 수 있음\n"

        "   - medium: [참고 문서]로 일부 답은 가능하나 보강 검색이 필요함\n"

        "   - high: [참고 문서]로 답변이 부족하거나 새로운 정보가 필요함\n\n"

        "2. search_intent: 검색이 필요한 경우, 무엇을 찾아야 하는지 설명\n"

        "3. retrieval_query:\n"

        "   - search_intent 기반 벡터 검색에 적합한 질의형 쿼리\n"

        "   - 짧고 명확한 자연어 구문 형태\n"

        "   - 핵심 개념 5개 이내\n"

        "   - 최대 120자 이내\n"

        "4. confidence: 판단 신뢰도 (0.0~1.0)\n\n"
        "5. prefer_fresh_retrieval:\n"
        "   - true: 이전 문맥과 관련된 대상이라도, 현재 질문이 더 최신이거나 갱신된 상태를 다시 확인하려는 뜻이어서 기존 문맥/캐시만 재사용하면 오래된 답이 될 위험이 큼\n"
        "   - false: 검색이 필요하더라도 최신성/갱신성 때문에 fresh retrieval을 강제할 필요는 없음\n\n"
        "중요 규칙:\n"
        "- 단어 포함 여부를 기계적으로 따르지 말고, [현재 질문]의 의미가 [참고 문서]보다 더 새롭거나 갱신된 사실 확인을 요구하는지 판단하세요.\n"
        "- [참고 문서]가 같은 대상을 설명하더라도 사용자가 최신 상태, 현재 현황, 업데이트 여부, 새 결과 확인처럼 다시 조회해야 하는 의미를 담고 있으면 requires_new_knowledge=high, prefer_fresh_retrieval=true 로 판단하세요.\n"
        "- [참고 문서]만으로 충분하고 다시 조회할 필요가 없을 때만 prefer_fresh_retrieval=false 로 두세요.\n\n"
        "{format_instructions}"
    )


    prompt = ChatPromptTemplate.from_messages(

        [

            ("system", system_prompt),

            (

                "human",

                "[이전 대화]\n{history}\n\n[참고 문서]\n{prev_context}\n\n[현재 질문]\n{question}",

            ),

        ]

    )



    try:

        chain = prompt | llm | sanitize_llm_json | parser

        result = await chain.ainvoke(
            {
                "format_instructions": parser.get_format_instructions(),
                "history": history_str or "없음",
                "prev_context": prev_context_str or "없음",
                "question": state.messages[-1].content,
            }
        )
        if action in search_required_actions:
            result = KnowledgeSufficiency(
                requires_new_knowledge="high",
                search_intent=str(getattr(result, "search_intent", "") or f"query_intent action={action} requires retrieval"),
                retrieval_query=str(getattr(result, "retrieval_query", "") or retrieval_query or state.messages[-1].content),
                prefer_fresh_retrieval=bool(getattr(result, "prefer_fresh_retrieval", False)),
                confidence=float(getattr(result, "confidence", 0.0) or 0.0),
            )
        log_event(
            "KS.RESULT",
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            stage="knowledge_sufficiency",
            requires_new_knowledge=result.requires_new_knowledge,
            retrieval_query=result.retrieval_query,
            prefer_fresh_retrieval=int(bool(getattr(result, "prefer_fresh_retrieval", False))),
            confidence=round(float(result.confidence), 2),
            early_exit_reason=("search_required_action_llm_freshness_probe" if action in search_required_actions else None),
        )
        return {"knowledge_sufficiency": result}
    except Exception as exc:
        logger.error("Knowledge Sufficiency Error: %s", exc)
        return {
            "knowledge_sufficiency": KnowledgeSufficiency(
                requires_new_knowledge="high",
                search_intent="knowledge fallback",
                retrieval_query=state.messages[-1].content,
                prefer_fresh_retrieval=False,
                confidence=0.5,
            )
        }




@measure_latency("rag_search")
async def node_rag_search(state: Any) -> Dict[str, Any]:

    """knowledge sufficiency 단계가 정한 query로 실제 RAG 검색을 수행한다.



    retriever 결과에서 문서, canonical_evidence, render_profile만 꺼내 workflow state로 넘겨

    후속 답변 생성이 raw payload에 직접 의존하지 않게 한다.

    """

    ks = state.knowledge_sufficiency

    qa = state.question_analysis
    query_intent = _get_normalized_intent(state)
    view_state = getattr(state, "view_state", None)
    turn_id = str(getattr(state, "turn_id", None) or getattr(state, "request_id", None) or "").strip()
    strategy_meta = dict(getattr(state.intent_payload, "strategy_meta", None) or {})
    raw_payload_memory = sync_active_anchor_record(getattr(state, "raw_payload_memory", None) or {}, view_state)
    prefer_fresh_retrieval = bool(
        getattr(ks, "prefer_fresh_retrieval", False)
        and str(getattr(ks, "requires_new_knowledge", "") or "").strip().lower() != "low"
    )

    try:
        output_type = str(_pick_attr(query_intent, qa, key="output_type", default="summary") or "summary").strip().lower()
        focus_entity_payload = strategy_meta.get("focus_entity") or {}
        if view_state is not None and isinstance(focus_entity_payload, dict) and focus_entity_payload:
            try:
                restored_focus_entity = FocusEntity.model_validate(focus_entity_payload)
                current_focus_entity = get_active_subject_entity(view_state)
                is_child_anchor = is_child_anchor_source(getattr(restored_focus_entity, "source", None))
                if is_child_anchor or current_focus_entity is None or _is_equivalent_focus_entity(current_focus_entity, restored_focus_entity):
                    if is_child_anchor:
                        view_state = set_active_child_anchor_scope(
                            view_state,
                            anchor=restored_focus_entity,
                            turn_id=turn_id,
                            reason="followup_anchor_restore",
                        )
                    else:
                        view_state = set_active_focus_scope(
                            view_state,
                            focus=restored_focus_entity,
                            turn_id=turn_id,
                            scope_kind="detail",
                            reason="followup_anchor_restore",
                        )
                    log_event(
                        "FOCUS.ENTITY.SET",
                        request_id=state.request_id,
                        conversation_id=state.conversation_id,
                        source="followup_anchor",
                        pjt_id=getattr(get_active_subject_entity(view_state), "pjt_id", None),
                        pjt_no=getattr(get_active_subject_entity(view_state), "pjt_no", None),
                        rst_id=getattr(get_active_subject_entity(view_state), "rst_id", None),
                        person_no=getattr(get_active_subject_entity(view_state), "person_no", None),
                        org_id=getattr(get_active_subject_entity(view_state), "org_id", None),
                        org_code=getattr(get_active_subject_entity(view_state), "org_code", None),
                        biz_no=getattr(get_active_subject_entity(view_state), "biz_no", None),
                    )
                    _log_scope_transition(state=state, view_state=view_state)
            except Exception:
                pass
        focus_entity = get_active_subject_entity(view_state)
        ids_map_for_detail = getattr(query_intent, "ids_map", None) or {}

        if isinstance(query_intent, dict):

            ids_map_for_detail = query_intent.get("ids_map") or {}

        preferred_entity_kind = str(
            getattr(query_intent, "context_owner_lock", None)
            or getattr(query_intent, "base_route", None)
            or getattr(qa, "base_route", None)
            or ""
        ).strip().lower() or None
        resolved_entity_ref = _resolve_detail_entity_ref(
            strategy_meta=strategy_meta,
            focus_entity=focus_entity,
            ids_map=ids_map_for_detail,
            preferred_entity_kind=preferred_entity_kind,
        )
        if isinstance(resolved_entity_ref, ClarificationRequest):
            clarification_payload = {
                "clarification_type": resolved_entity_ref.clarification_type,
                "status": "clarification_required",
                "candidates": list(resolved_entity_ref.candidates),
                "resume_token": dict(resolved_entity_ref.resume_token),
                "message": resolved_entity_ref.message,
            }
            log_event(
                "CLARIFICATION.ISSUED",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                clarification_type=resolved_entity_ref.clarification_type,
                reason=dict(strategy_meta.get("clarification_payload") or {}).get("reason"),
            )
            return {
                "clarification": clarification_payload,
                "no_result_message": resolved_entity_ref.message,
                "answer_artifact": _build_answer_artifact(
                    text=resolved_entity_ref.message,

                    answer_kind="clarification",

                    answer_source="followup_clarification",

                    clarification=resolved_entity_ref,

                ),

                "view_state": view_state,
                "raw_payload_memory": raw_payload_memory,

            }

        fact_followup = resolve_followup_from_facts(
            question=str(getattr(state, "question", "") or state.messages[-1].content or ""),
            view_state=view_state,
            raw_payload_memory=raw_payload_memory,
            base_route=str(_pick_attr(query_intent, qa, key="base_route", default="project") or "project"),
        )
        if fact_followup is not None and bool(
            strategy_meta.get("explicit_followup")
            or strategy_meta.get("anchor_source")
            or str(strategy_meta.get("followup_resolution_status") or "").strip().lower() == "resolved"
        ):
            artifact = fact_followup["answer_artifact"]
            log_event(
                "FOLLOWUP.FACT_RESOLVED",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                turn_id=turn_id,
                anchor_key=((fact_followup.get("raw_payload_record") or {}).get("anchor_key")),
            )
            return {
                "view_state": view_state,
                "raw_payload_memory": raw_payload_memory,
                "answer_artifact": artifact,
                "answer_context_text": "",
                "debug_answer_context_text": "",
                "anchor_hit": bool(fact_followup.get("anchor_hit")),
                "followup_resolved_by_facts": bool(fact_followup.get("followup_resolved_by_facts")),
            }

        detail_anchor_active = bool(output_type == "detail" and has_active_anchor_seed(state))
        if output_type == "detail" and not detail_anchor_active:
            if bool(
                strategy_meta.get("explicit_followup")
                or strategy_meta.get("anchor_source")
                or str(strategy_meta.get("followup_resolution_status") or "").strip().lower() == "resolved"

            ):

                log_event(

                    "RAG.DETAIL.ANCHOR.SEED_MISSING",

                    request_id=state.request_id,

                    conversation_id=state.conversation_id,
                    reason="detail follow-up signal exists but ids_map has no active anchor seed",
                )
        if output_type == "detail" and detail_anchor_active and prefer_fresh_retrieval:
            log_event(
                "RAG.DETAIL.FRESH_RETRIEVAL_BYPASS",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                prefer_fresh_retrieval=1,
                ks_search_intent=str(getattr(ks, "search_intent", "") or ""),
                anchor_source=strategy_meta.get("anchor_source"),
            )
        if output_type == "detail" and detail_anchor_active and isinstance(resolved_entity_ref, ResolvedEntityRef) and view_state is not None and not prefer_fresh_retrieval:
            requested_fields = extract_requested_fields(state.messages[-1].content)
            cache_anchor = focus_entity
            if cache_anchor is None:
                cache_anchor = FocusEntity(kind=resolved_entity_ref.entity_kind, source=resolved_entity_ref.source, **{k: (v[0] if isinstance(v, list) and v else None) for k, v in resolved_entity_ref.seed_map.items()})
            cache_key = make_entity_cache_key(cache_anchor)
            cache_entry = (getattr(view_state, "detail_cache", {}) or {}).get(cache_key)
            cache_schema_version = int(getattr(cache_entry, "schema_version", 0) or 0) if cache_entry is not None else 0
            if cache_entry is not None and cache_schema_version != DETAIL_CACHE_SCHEMA_VERSION:
                cache_entry = None
                log_event(
                    "DETAIL.CACHE.STALE_SCHEMA",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    entity_key=cache_key,
                    cache_schema_version=cache_schema_version,
                    expected_schema_version=DETAIL_CACHE_SCHEMA_VERSION,
                )
            if cache_entry and coverage_satisfies_fields(cache_entry.coverage, requested_fields):
                detail_prompt_context = build_detail_prompt_context(
                    cache_entry.coverage,
                    requested_fields=requested_fields,
                )
                detail_debug_context = build_detail_answer_context(
                    cache_entry.coverage,
                    requested_fields=requested_fields,
                )
                log_event(
                    "DETAIL.CACHE.HIT",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    entity_key=cache_key,
                    requested_fields=sorted(requested_fields),
                )
                retrieval_bundle = _build_retrieval_bundle(
                    docs=[],
                    canonical_evidence=[],
                    render_profile={"context_kind": getattr(cache_entry.anchor, "kind", "project"), "name": "detail"},
                    raw_count=1,
                    answer_context_text=detail_prompt_context,
                    debug_answer_context_text=detail_debug_context,
                    context_source="detail_contract_context",
                )
                return {
                    "context": [],
                    "canonical_evidence": [],
                    "retrieval_bundle": retrieval_bundle,
                    "answer_context_text": detail_prompt_context,
                    "debug_answer_context_text": detail_debug_context,
                    "resolved_retrieval_query": state.messages[-1].content,
                    "actual_retrieval_query": state.messages[-1].content,
                    "render_profile": {"context_kind": getattr(cache_entry.anchor, "kind", "project"), "name": "detail"},
                    "no_result_message": None,
                    "clarification": None,
                    "view_state": view_state,
                    "answer_artifact": None,
                    "raw_payload_memory": raw_payload_memory,
                    "anchor_hit": False,
                    "followup_resolved_by_facts": False,
                }
            log_event(
                "DETAIL.CACHE.MISS",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                entity_key=cache_key,

                requested_fields=sorted(requested_fields),

            )



        exact_detail_lookup = bool(
            output_type == "detail"
            and detail_anchor_active
            and not prefer_fresh_retrieval
            and isinstance(resolved_entity_ref, ResolvedEntityRef)
            and resolved_entity_ref.seed_map
        )
        focus_seed_map: dict[str, list[str]] = dict(resolved_entity_ref.seed_map) if isinstance(resolved_entity_ref, ResolvedEntityRef) else {}


        raw_query, planner_query, search_query, query_confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries(
            state=state,
            qa=qa,
            ks=ks,
            min_confidence=0.55,
        )
        resolved_retrieval_query = str(search_query or "").strip()
        query_resolution_reason = "raw_query_fallback_applied" if fallback_applied else "planner_query_retained"
        anchor_query_meta = {
            "anchor_present": bool(focus_seed_map),
            "anchor_source": (resolved_entity_ref.source if isinstance(resolved_entity_ref, ResolvedEntityRef) else None),
            "anchor_reference_kind": (resolved_entity_ref.reference_kind if isinstance(resolved_entity_ref, ResolvedEntityRef) else None),
            "anchor_entity_key": (str(next(iter(focus_seed_map.values()))[0]).strip() if focus_seed_map else None),
            "anchor_query_repaired": False,
            "anchor_repair_reason": None,
        }
        if prefer_fresh_retrieval:
            fresh_query_seed = _first_text_value(
                getattr(ks, "retrieval_query", None),
                resolved_retrieval_query,
                raw_query,
            ) or ""
            overridden_query = _build_fresh_retrieval_query(
                question=fresh_query_seed,
                resolved_entity_ref=(resolved_entity_ref if isinstance(resolved_entity_ref, ResolvedEntityRef) else None),
                focus_entity=focus_entity,
            )
            previous_query = resolved_retrieval_query
            resolved_retrieval_query = str(overridden_query or previous_query or "").strip()
            query_resolution_reason = "knowledge_sufficiency_llm_prefers_fresh_retrieval"
            anchor_query_meta["anchor_query_repaired"] = bool(resolved_retrieval_query and resolved_retrieval_query != previous_query)
            anchor_query_meta["anchor_repair_reason"] = (
                "llm_prefer_fresh_retrieval_anchor_preserved"
                if anchor_query_meta["anchor_query_repaired"]
                else "llm_prefer_fresh_retrieval"
            )
            log_event(
                "RAG.LLM_FRESH_RETRIEVAL",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                prefer_fresh_retrieval=1,
                ks_search_intent=str(getattr(ks, "search_intent", "") or ""),
                selected_search_query=resolved_retrieval_query,
                detail_anchor_active=int(detail_anchor_active),
                exact_detail_lookup_bypassed=int(bool(output_type == "detail" and detail_anchor_active)),
            )
        if output_type == "detail" and exact_detail_lookup and focus_seed_map:
            resolved_retrieval_query = str(next(iter(focus_seed_map.values()))[0]).strip()
        explicit_count = _extract_explicit_count(getattr(state, "question", ""))
        search_num = _resolve_runtime_top_k(
            qa,
            max_top_k_size=MAX_TOP_K_SIZE,
            exact_detail_lookup=exact_detail_lookup,
        )
        planner_limit = _coerce_positive_int(getattr(qa, "limit", None))

        planner_display_limit = _coerce_positive_int(getattr(qa, "display_limit", None))

        log_event(

            "RAG.RETRIEVAL_QUERY.RESOLUTION",

            request_id=state.request_id,

            conversation_id=state.conversation_id,
            raw_query=raw_query,
            planner_query=planner_query,
            selected_search_query=resolved_retrieval_query,
            confidence=query_confidence,
            drift_detected=drift_detected,
            drift_reasons=drift_reasons,
            fallback_applied=fallback_applied,
            prefer_fresh_retrieval=int(prefer_fresh_retrieval),
            query_resolution_reason=query_resolution_reason,
            anchor_present=anchor_query_meta.get("anchor_present"),
            anchor_source=anchor_query_meta.get("anchor_source"),
            anchor_reference_kind=anchor_query_meta.get("anchor_reference_kind"),
            anchor_entity_key=anchor_query_meta.get("anchor_entity_key"),
            anchor_query_repaired=anchor_query_meta.get("anchor_query_repaired"),
            anchor_repair_reason=anchor_query_meta.get("anchor_repair_reason"),
        )

        log_event(

            "RAG.COUNT_PIPELINE",

            request_id=state.request_id,

            conversation_id=state.conversation_id,

            explicit_count=explicit_count,
            planner_limit=planner_limit,
            planner_display_limit=planner_display_limit,
            runtime_top_k=search_num,
            retrieval_query=resolved_retrieval_query,
            raw_query=raw_query,
            planner_query=planner_query,
            drift_detected=drift_detected,
            fallback_applied=fallback_applied,
            prefer_fresh_retrieval=int(prefer_fresh_retrieval),
            query_resolution_reason=query_resolution_reason,
            anchor_present=anchor_query_meta.get("anchor_present"),
            anchor_source=anchor_query_meta.get("anchor_source"),
            anchor_reference_kind=anchor_query_meta.get("anchor_reference_kind"),
            anchor_entity_key=anchor_query_meta.get("anchor_entity_key"),
            anchor_query_repaired=anchor_query_meta.get("anchor_query_repaired"),
            anchor_repair_reason=anchor_query_meta.get("anchor_repair_reason"),
        )



        if exact_detail_lookup:

            log_event(

                "RAG.DETAIL.ANCHOR.EXACT_LOOKUP",

                request_id=state.request_id,
                conversation_id=state.conversation_id,
                anchor_seed_map=focus_seed_map,
                selected_search_query=resolved_retrieval_query,
            )
        retriever = CustomRAGRetriever(

            top_k=search_num,

            model_name="gemma_triton_0",

            intent_payload=state.intent_payload,

            request_overrides=getattr(state, "request_overrides", None) or {},

        )

        rag_tool = Tool(

            name="RAG_Search",

            description="Search the NTIS/IRIS knowledge base.",

            func=retriever.retrieve,

        )



        retrieve_result = await asyncio.to_thread(rag_tool.func, resolved_retrieval_query)
        actual_retrieval_query = str((retrieve_result or {}).get("actual_retrieval_query") or resolved_retrieval_query or "")
        log_event(
            "RAG.RETRIEVAL_QUERY.ACTUAL",
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            resolved_retrieval_query=resolved_retrieval_query,
            actual_retrieval_query=actual_retrieval_query,
        )
        if actual_retrieval_query != resolved_retrieval_query:
            log_event(
                "RAG.RETRIEVAL_QUERY.MISMATCH",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                resolved_retrieval_query=resolved_retrieval_query,
                actual_retrieval_query=actual_retrieval_query,
            )
        docs = retrieve_result.get("documents", []) if isinstance(retrieve_result, dict) else []

        canonical_evidence = retrieve_result.get("canonical_evidence", []) if isinstance(retrieve_result, dict) else []

        render_profile = retrieve_result.get("render_profile", {}) if isinstance(retrieve_result, dict) else {}
        no_result_message = retrieve_result.get("no_result_message") if isinstance(retrieve_result, dict) else None
        clarification = retrieve_result.get("clarification") if isinstance(retrieve_result, dict) else None
        answer_context_text = str(retrieve_result.get("answer_context_text") or "") if isinstance(retrieve_result, dict) else ""
        debug_answer_context_text = (
            str(retrieve_result.get("debug_answer_context_text") or answer_context_text)
            if isinstance(retrieve_result, dict)
            else answer_context_text
        )
        raw_result_count = int(retrieve_result.get("raw_result_count") or len(docs)) if isinstance(retrieve_result, dict) else len(docs)
        retrieval_bundle = _build_retrieval_bundle(
            docs=docs,
            canonical_evidence=canonical_evidence,
            render_profile=render_profile,
            raw_count=raw_result_count,
            clarification=clarification,
            no_result_message=no_result_message,
            answer_context_text=answer_context_text,
            debug_answer_context_text=debug_answer_context_text,
            context_source=("pipeline_context" if answer_context_text else "derived_canonical_evidence"),
        )


        context_kind = str((render_profile or {}).get("context_kind") or _pick_attr(query_intent, qa, key="base_route", default="project") or "project").strip().lower()

        list_like_output = output_type in {"list", "relation", "comparison", "series", "stats"}

        display_limit = _resolve_display_request(qa)

        requested_count_source = "question_analysis.display_limit" if _coerce_positive_int(getattr(qa, "display_limit", None)) is not None else "question_analysis.limit"

        if list_like_output and docs:

            display_bundle = _normalize_display_payloads(

                docs=docs,

                canonical_evidence=canonical_evidence,

                base_route=context_kind or "project",

                output_type=output_type,

                requested_count=display_limit,

                explicit_count=explicit_count,


                request_id=state.request_id,

                conversation_id=state.conversation_id,

            )

        else:

            display_bundle = DisplayPayloadBundle(

                snapshot_documents=docs,

                snapshot_canonical_evidence=canonical_evidence,

                docs_count=len(docs),

                canonical_count=len(canonical_evidence),

                docs_kind=_classify_docs_kind(docs),

                canonical_kind="item_list",

                display_source="docs",

            )

        if list_like_output and docs and view_state is not None:
            docs_count_before_snapshot = display_bundle.docs_count
            canonical_count_before_snapshot = display_bundle.canonical_count
            snapshot = build_display_snapshot(
                conversation_id=state.conversation_id,
                turn_id=turn_id,
                context_kind=context_kind or "project",
                requested_count=display_limit,
                items=retrieval_bundle.items,
                raw_count=raw_result_count,
            )
            scope_kind = "child" if str(strategy_meta.get("followup_reference_kind") or "").strip().lower() == "child_entity" else "list"
            view_state = set_active_result_scope(
                view_state,
                snapshot=snapshot,
                output_type=output_type,
                context_kind=context_kind or "project",
                turn_id=turn_id,
                scope_kind=scope_kind,
                reason="retrieval_list_result",
            )
            view_state = index_snapshot_subjects(
                view_state,
                snapshot=snapshot,
                turn_id=turn_id,
            )
            # --- recent_mentions: 목록 응답 시 상위 N개를 recent_mentions에 적재 ---
            _list_mentions = [
                recent_mention_from_display_item(
                    item,
                    source="list_snapshot",
                    turn_index=idx,
                )
                for idx, item in enumerate(snapshot.items[:12])
            ]
            if _list_mentions:
                view_state = append_recent_mentions(view_state, _list_mentions, max_items=12)
            view_state.last_query_contract = {
                "action": str(_pick_attr(query_intent, qa, key="action", default="") or ""),
                "output_type": output_type,
                "context_kind": context_kind or "project",
                "raw_query": raw_query,
                "planner_query": planner_query,
                "selected_search_query": resolved_retrieval_query,
                "actual_retrieval_query": actual_retrieval_query,
                "query_mismatch": bool(actual_retrieval_query != resolved_retrieval_query),
            }
            view_state.refinement_history.append({

                "turn_id": turn_id,

                "output_type": output_type,

                "context_kind": context_kind or "project",

                "view_id": snapshot.view_id,

            })

            if len(view_state.refinement_history) > 10:

                view_state.refinement_history = view_state.refinement_history[-10:]

            view_state.raw_candidates_cache[snapshot.view_id] = [dict(item) for item in display_bundle.snapshot_documents[: min(len(display_bundle.snapshot_documents), 20)] if isinstance(item, dict)]

            docs = display_bundle.snapshot_documents[: snapshot.visible_count]

            canonical_evidence = display_bundle.snapshot_canonical_evidence[: snapshot.visible_count]

            log_event(

                "DISPLAY.SNAPSHOT.BUILT",

                request_id=state.request_id,

                conversation_id=state.conversation_id,

                view_id=snapshot.view_id,

                requested_count=display_limit,

                docs_count=docs_count_before_snapshot,

                canonical_count=canonical_count_before_snapshot,

                visible_count=snapshot.visible_count,

                raw_count=snapshot.raw_count,

                docs_kind=display_bundle.docs_kind,

                canonical_kind=display_bundle.canonical_kind,

                display_source=display_bundle.display_source,

                requested_count_source=requested_count_source,

            )

            log_event(

                "RAG.COUNT_PIPELINE.RESULT",

                request_id=state.request_id,

                conversation_id=state.conversation_id,

                explicit_count=explicit_count,

                planner_limit=planner_limit,

                planner_display_limit=planner_display_limit,

                runtime_top_k=search_num,

                raw_query=raw_query,
                planner_query=planner_query,
                selected_search_query=resolved_retrieval_query,
                drift_detected=drift_detected,
                fallback_applied=fallback_applied,

                requested_count=display_limit,

                docs_count=docs_count_before_snapshot,

                canonical_count=canonical_count_before_snapshot,

                visible_count=snapshot.visible_count,

                raw_count=snapshot.raw_count,

                docs_kind=display_bundle.docs_kind,

                canonical_kind=display_bundle.canonical_kind,

                display_source=display_bundle.display_source,

                requested_count_source=requested_count_source,

            )

            log_event(
                "DISPLAY.SNAPSHOT.SAVED",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                view_id=snapshot.view_id,
            )
            promoted_child_anchor, promotion_meta = _promote_unique_list_subject_anchor(
                snapshot=snapshot,
                parent_focus=getattr(getattr(view_state, "active_scope", None), "focus", None),
            )
            if promoted_child_anchor is not None:
                view_state = set_active_child_anchor_scope(
                    view_state,
                    anchor=promoted_child_anchor,
                    turn_id=turn_id,
                    reason="result_list_unique_subject",
                )
                log_event(
                    "FOLLOWUP.CHILD_ANCHOR.PROMOTED",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    view_id=snapshot.view_id,
                    anchor_kind=getattr(promoted_child_anchor, "kind", None),
                    anchor_source=getattr(promoted_child_anchor, "source", None),
                    anchor_title=getattr(promoted_child_anchor, "title_text", None),
                    person_no=getattr(promoted_child_anchor, "person_no", None),
                    org_id=getattr(promoted_child_anchor, "org_id", None),
                    org_code=getattr(promoted_child_anchor, "org_code", None),
                    biz_no=getattr(promoted_child_anchor, "biz_no", None),
                    rst_id=getattr(promoted_child_anchor, "rst_id", None),
                    doi=getattr(promoted_child_anchor, "doi", None),
                    issn=getattr(promoted_child_anchor, "issn", None),
                    promotion_source=promotion_meta.get("source"),
                    candidate_count=promotion_meta.get("candidate_count"),
                    identity_key=promotion_meta.get("identity_key"),
                )
                # --- recent_mentions: child anchor 승격 시에도 적재 ---
                _child_mention = recent_mention_from_focus_entity(
                    promoted_child_anchor,
                    source="child_anchor",
                )
                view_state = append_recent_mentions(view_state, [_child_mention], max_items=12)
            _log_scope_transition(state=state, view_state=view_state)


        answer_artifact = None
        if output_type == "detail" and docs and view_state is not None:
            focus_entity = focus_entity_from_detail(
                context_kind=str((render_profile or {}).get("context_kind") or _pick_attr(query_intent, qa, key="base_route", default="project") or "project").strip().lower(),
                document=docs[0] if docs else None,
                canonical_item=canonical_evidence[0] if canonical_evidence else None,
                source="detail_lookup",
            )
            if focus_entity is not None:
                view_state = set_active_focus_scope(
                    view_state,
                    focus=focus_entity,
                    turn_id=turn_id,
                    scope_kind="detail",
                    reason="detail_lookup",
                    preserve_child_anchor=True,
                )
                view_state = index_focus_subjects(
                    view_state,
                    focus=focus_entity,
                    turn_id=turn_id,
                )
                # --- recent_mentions: 상세 응답 시 현재 focus 1건을 적재 ---
                _detail_mention = recent_mention_from_focus_entity(
                    focus_entity,
                    source="detail_focus",
                )
                view_state = append_recent_mentions(view_state, [_detail_mention], max_items=12)
                log_event(
                    "FOCUS.ENTITY.SET",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    source=getattr(focus_entity, "source", None),
                    pjt_id=getattr(focus_entity, "pjt_id", None),

                    pjt_no=getattr(focus_entity, "pjt_no", None),

                    rst_id=getattr(focus_entity, "rst_id", None),

                    person_no=getattr(focus_entity, "person_no", None),

                    org_id=getattr(focus_entity, "org_id", None),

                    org_code=getattr(focus_entity, "org_code", None),

                    biz_no=getattr(focus_entity, "biz_no", None),

                )

                coverage_input = _build_detail_coverage_input(
                    docs[0],
                    canonical_evidence[0] if canonical_evidence else None,
                    anchor_title=getattr(focus_entity, "title_text", None),
                )
                coverage = compute_detail_coverage(coverage_input, anchor=focus_entity)

                cache_key = make_entity_cache_key(focus_entity)

                requested_fields = extract_requested_fields(state.messages[-1].content)

                view_state.detail_cache[cache_key] = DetailCacheEntry(
                    entity_key=cache_key,
                    anchor=focus_entity,
                    coverage=coverage,
                    hydrated_fields=sorted(set(coverage.available_fields)),
                    source_turn_id=turn_id,
                    schema_version=DETAIL_CACHE_SCHEMA_VERSION,
                )
                _log_scope_transition(state=state, view_state=view_state)
                detail_prompt_context = build_detail_prompt_context(
                    coverage,
                    requested_fields=requested_fields,
                )
                detail_debug_context = build_detail_answer_context(
                    coverage,
                    requested_fields=requested_fields,
                )
                answer_context_text = detail_prompt_context
                debug_answer_context_text = detail_debug_context
                retrieval_bundle = _build_retrieval_bundle(
                    docs=docs,
                    canonical_evidence=canonical_evidence,
                    render_profile=render_profile,
                    raw_count=raw_result_count,
                    clarification=clarification,
                    no_result_message=no_result_message,
                    answer_context_text=detail_prompt_context,
                    debug_answer_context_text=detail_debug_context,
                    context_source="detail_contract_context",
                )
                log_event(
                    "DETAIL.COVERAGE",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,

                    entity_found=int(coverage.entity_found),

                    detail_level=coverage.detail_level,
                    available_fields=coverage.available_fields,
                    missing_fields=coverage.missing_fields,
                )

        if docs:
            raw_payload_memory = upsert_raw_payload_records(
                raw_payload_memory,
                conversation_id=state.conversation_id,
                turn_id=turn_id,
                entity_type=context_kind or "project",
                payloads=[dict(item) for item in docs[:4] if isinstance(item, dict)],
                activate_first=bool(output_type == "detail"),
            )
            raw_payload_memory = sync_active_anchor_record(raw_payload_memory, view_state)

        log_event(
            "RAG.RESULT",

            request_id=state.request_id,
            conversation_id=state.conversation_id,
            stage="rag_search",
            docs_found=len(docs),
            query_len=len(str(resolved_retrieval_query or "")),
            canonical_evidence_found=len(canonical_evidence),
        )


        return {
            "context": docs,
            "canonical_evidence": canonical_evidence,
            "retrieval_bundle": retrieval_bundle,
            "answer_context_text": answer_context_text,
            "debug_answer_context_text": debug_answer_context_text,
            "resolved_retrieval_query": resolved_retrieval_query,
            "actual_retrieval_query": actual_retrieval_query,
            "render_profile": render_profile,
            "no_result_message": no_result_message,
            "clarification": clarification,
            "view_state": view_state,
            "answer_artifact": answer_artifact,
            "raw_payload_memory": raw_payload_memory,
            "anchor_hit": False,
            "followup_resolved_by_facts": False,

        }

    except StrategyViolation:

        raise

    except Exception as exc:

        logger.error("RAG Error: %s", exc)

        log_event(

            "RAG.ERROR",

            request_id=state.request_id,

            conversation_id=state.conversation_id,

            stage="rag_search",

            error_type=type(exc).__name__,

            reason=str(exc),

        )

        raise


# ---------------------------------------------------------------------------
# relax_and_retry: 0건 결과 시 필터를 점진적으로 완화하여 재검색하는 노드
# ---------------------------------------------------------------------------

# 1차 완화 대상: 사람/기관 필터 (가장 제한적인 조건)
_RELAX_STAGE1_NI_FIELDS = {
    "people_terms": [],
    "org_terms": [],
    "participant_org_terms": [],
    "lead_org_terms": [],
    "people_affiliation_org_terms": [],
}
_RELAX_STAGE1_QA_FILTER_KEYS = frozenset({
    "participant_researcher_name",
    "org_name",
    "lead_org_name",
    "participant_org_name",
    "people_affiliation_org_name",
})

# 2차 완화 대상: 연도 필터까지 추가 제거
_RELAX_STAGE2_NI_FIELDS = {
    **_RELAX_STAGE1_NI_FIELDS,
    "years": [],
    "year_from": None,
    "year_to": None,
}
_RELAX_STAGE2_QA_FILTER_KEYS = _RELAX_STAGE1_QA_FILTER_KEYS | frozenset({
    "years",
    "year_range",
})


async def node_relax_and_retry(state: Any) -> Dict[str, Any]:
    """0건 결과 후 필터를 점진적으로 완화하여 재검색을 준비한다.

    LLM 호출 없이 rule-based로 NormalizedIntent와 QuestionAnalysis의
    필터 필드를 단계적으로 제거한다. 최대 2회 재시도를 지원하며,
    기존 Contract(mode/action/relation)은 절대 변경하지 않는다.
    """
    retry_count = (getattr(state, "search_retry_count", 0) or 0) + 1
    qa = state.question_analysis
    ip = state.intent_payload
    ni = ip.normalized_intent

    # 단계별 완화 대상 선택
    if retry_count <= 1:
        ni_updates = dict(_RELAX_STAGE1_NI_FIELDS)
        drop_keys = _RELAX_STAGE1_QA_FILTER_KEYS
    else:
        ni_updates = dict(_RELAX_STAGE2_NI_FIELDS)
        drop_keys = _RELAX_STAGE2_QA_FILTER_KEYS

    # --- NormalizedIntent 완화 (frozen dataclass → replace) ---
    # 실제 존재하는 필드만 교체 (NormalizedIntent에 없는 키 방어)
    safe_ni_updates = {k: v for k, v in ni_updates.items() if hasattr(ni, k)}
    new_ni = dc_replace(ni, **safe_ni_updates) if safe_ni_updates else ni

    # --- QuestionAnalysisV3 완화 (Pydantic → model_copy) ---
    relaxed_filters = {k: v for k, v in (qa.filters or {}).items() if k not in drop_keys}
    new_qa = qa.model_copy(update={"filters": relaxed_filters})

    # --- IntentPayloadV3 재조립 (frozen dataclass → replace) ---
    new_ip = dc_replace(ip, normalized_intent=new_ni, question_analysis=new_qa)

    # 완화된 필드 목록 로깅
    dropped_ni = sorted(k for k in safe_ni_updates if getattr(ni, k, None) != safe_ni_updates[k])
    dropped_qa = sorted(k for k in drop_keys if k in (qa.filters or {}))

    log_event(
        "RELAX.RETRY",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        retry_count=retry_count,
        dropped_ni_fields=dropped_ni,
        dropped_qa_filter_keys=dropped_qa,
        original_retrieval_query=getattr(qa, "retrieval_query", None),
        mode=getattr(qa, "mode", None),
        action=getattr(qa, "action", None),
    )

    return {
        "question_analysis": new_qa,
        "intent_payload": new_ip,
        "search_retry_count": retry_count,
        "context": [],
        "no_result_message": None,
    }





