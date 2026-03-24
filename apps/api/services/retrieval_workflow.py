from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Optional

from apps.api.services.canonical_context import build_prev_context_canonical_text
from apps.core.canonical_evidence import build_canonical_evidence
from apps.api.services.followup_anchor import parse_display_limit
from apps.api.services.detail_contract import (
    compute_detail_coverage,
    coverage_satisfies_fields,
    extract_requested_fields,
    make_entity_cache_key,
    render_detail_answer,
)
from apps.api.services.view_state import DetailCacheEntry, FocusEntity, build_display_snapshot, focus_entity_from_detail
from apps.api.services.rag_retriever import has_active_anchor_seed, repair_query_for_resolved_anchor
from apps.api.services.request_facade import _apply_anchor_lock
from apps.core.followup_resolution import build_followup_clarification_message, build_followup_clarification_payload, should_short_circuit_followup_clarification


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


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number >= 1 else None


def _resolve_retrieval_budget(question_analysis: Any, *, max_top_k_size: int) -> int:
    """Use the assembled question-analysis limit as the retrieval budget source of truth."""
    planner_limit = _coerce_positive_int(getattr(question_analysis, "limit", None))
    return min(planner_limit or max_top_k_size, max_top_k_size)


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
    return "item_list" if not source_types or source_types == {"hit"} else "collection_wrapper"


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


def _build_focus_seed_map(focus_entity: Any) -> dict[str, list[str]]:
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        value = str(getattr(focus_entity, key, "") or "").strip()
        if value:
            return {key: [value]}
    return {}


def _detail_anchor_active(*, query_intent: Any, strategy_meta: dict[str, Any], latest_focus_entity: Any) -> bool:
    ids_map = getattr(query_intent, "ids_map", None) or {}
    if isinstance(query_intent, dict):
        ids_map = query_intent.get("ids_map") or {}
    if any(ids_map.get(key) for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")):
        return True
    if latest_focus_entity is None:
        return False
    return bool(strategy_meta.get("explicit_followup") or strategy_meta.get("anchor_source") or strategy_meta.get("followup_resolution_status") == "resolved")


def _build_anchor_locked_payload(intent_payload: Any, focus_seed: dict[str, list[str]]) -> Any:
    if not focus_seed:
        return intent_payload
    patched_payload = deepcopy(intent_payload)
    normalized_intent = getattr(patched_payload, "normalized_intent", None)
    if normalized_intent is not None:
        setattr(patched_payload, "normalized_intent", _apply_anchor_lock(normalized_intent, focus_seed))
    return patched_payload


def _normalize_display_payloads(
    *,
    docs: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    base_route: str,
    output_type: str,
    requested_count: int,
    explicit_count: Optional[int],
    log_event: Any,
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

    docs_count = len(normalized_docs)
    canonical_count = len(normalized_canonical)
    docs_kind = _classify_docs_kind(normalized_docs)
    canonical_kind = "item_list"
    display_source = "docs"

    if docs_count != canonical_count:
        aligned_count = min(docs_count, canonical_count)
        fallback_threshold = int(explicit_count or 0) if explicit_count is not None else int(requested_count or 0)
        prefer_canonical = (
            str(output_type or "").strip().lower() == "list"
            and canonical_count > docs_count
            and canonical_count >= max(1, fallback_threshold)
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


async def node_knowledge_sufficiency(
    state: Any,
    *,
    build_llm_fn: Any,
    pydantic_output_parser_cls: Any,
    chat_prompt_template_cls: Any,
    knowledge_sufficiency_cls: Any,
    sanitize_llm_json_fn: Any,
    refine_documents_rule_based_fn: Any,
    priority_context_fields: tuple[str, ...],
    max_field_sentences: int,
    max_field_tokens: int,
    default_max_doc_sentences: int,
    default_max_doc_tokens: int,
    logger: Any,
    log_event: Any,
) -> Dict[str, Any]:
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
        result = knowledge_sufficiency_cls(
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

    if (query_intent or strategy or qa) and not state.prev_context:
        result = knowledge_sufficiency_cls(
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

    if action in search_required_actions:
        result = knowledge_sufficiency_cls(
            requires_new_knowledge="high",
            search_intent=f"query_intent action={action} requires retrieval",
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

    llm = build_llm_fn(model_name="gemma_triton_0")
    parser = pydantic_output_parser_cls(pydantic_object=knowledge_sufficiency_cls)

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
        "{format_instructions}"
    )

    prompt = chat_prompt_template_cls.from_messages(
        [
            ("system", system_prompt),
            (
                "human",
                "[이전 대화]\n{history}\n\n[참고 문서]\n{prev_context}\n\n[현재 질문]\n{question}",
            ),
        ]
    )

    try:
        chain = prompt | llm | sanitize_llm_json_fn | parser
        result = await chain.ainvoke(
            {
                "format_instructions": parser.get_format_instructions(),
                "history": history_str or "없음",
                "prev_context": prev_context_str or "없음",
                "question": state.messages[-1].content,
            }
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
    except Exception as exc:
        logger.error("Knowledge Sufficiency Error: %s", exc)
        return {
            "knowledge_sufficiency": knowledge_sufficiency_cls(
                requires_new_knowledge="high",
                search_intent="knowledge fallback",
                retrieval_query=state.messages[-1].content,
                confidence=0.5,
            )
        }


async def node_rag_search(
    state: Any,
    *,
    resolve_rag_queries_fn: Any,
    custom_rag_retriever_cls: Any,
    tool_cls: Any,
    max_top_k_size: int,
    strategy_violation_cls: type[Exception],
    logger: Any,
    log_event: Any,
) -> Dict[str, Any]:
    """knowledge sufficiency 단계가 정한 query로 실제 RAG 검색을 수행한다.

    retriever 결과에서 문서, canonical_evidence, render_profile만 꺼내 workflow state로 넘겨
    후속 답변 생성이 raw payload에 직접 의존하지 않게 한다.
    """
    ks = state.knowledge_sufficiency
    qa = state.question_analysis
    query_intent = _get_normalized_intent(state)
    view_state = getattr(state, "view_state", None)
    strategy_meta = dict(getattr(state.intent_payload, "strategy_meta", None) or {})

    try:
        output_type = str(_pick_attr(query_intent, qa, key="output_type", default="summary") or "summary").strip().lower()
        focus_entity_payload = strategy_meta.get("focus_entity") or {}
        if view_state is not None and isinstance(focus_entity_payload, dict) and focus_entity_payload:
            try:
                restored_focus_entity = FocusEntity.model_validate(focus_entity_payload)
                current_focus_entity = getattr(view_state, "latest_focus_entity", None)
                if current_focus_entity is None or _is_equivalent_focus_entity(current_focus_entity, restored_focus_entity):
                    view_state.latest_focus_entity = restored_focus_entity
                    log_event(
                        "FOCUS.ENTITY.SET",
                        request_id=state.request_id,
                        conversation_id=state.conversation_id,
                        source="followup_anchor",
                        pjt_id=getattr(view_state.latest_focus_entity, "pjt_id", None),
                        pjt_no=getattr(view_state.latest_focus_entity, "pjt_no", None),
                        rst_id=getattr(view_state.latest_focus_entity, "rst_id", None),
                        person_no=getattr(view_state.latest_focus_entity, "person_no", None),
                        org_id=getattr(view_state.latest_focus_entity, "org_id", None),
                        org_code=getattr(view_state.latest_focus_entity, "org_code", None),
                        biz_no=getattr(view_state.latest_focus_entity, "biz_no", None),
                    )
            except Exception:
                pass
        latest_focus_entity = getattr(view_state, "latest_focus_entity", None)
        detail_anchor_active = bool(output_type == "detail" and has_active_anchor_seed(state))
        if output_type == "detail" and latest_focus_entity is not None and detail_anchor_active:
            requested_fields = extract_requested_fields(state.messages[-1].content)
            cache_key = make_entity_cache_key(latest_focus_entity)
            cache_entry = (getattr(view_state, "detail_cache", {}) or {}).get(cache_key)
            if cache_entry and coverage_satisfies_fields(cache_entry.coverage, requested_fields):
                answer_text = render_detail_answer(cache_entry.coverage, requested_fields=requested_fields)
                log_event(
                    "DETAIL.CACHE.HIT",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    entity_key=cache_key,
                    requested_fields=sorted(requested_fields),
                )
                log_event(
                    "ANSWER.FALLBACK.APPLIED",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    reason="detail_cache_hit",
                )
                return {"detail_server_answer": answer_text, "view_state": view_state}
            log_event(
                "DETAIL.CACHE.MISS",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                entity_key=cache_key,
                requested_fields=sorted(requested_fields),
            )

        exact_detail_lookup = False
        focus_seed_map: dict[str, list[str]] = {}
        ids_map = getattr(query_intent, "ids_map", None) or {}
        if isinstance(query_intent, dict):
            ids_map = query_intent.get("ids_map") or {}
        if output_type == "detail" and detail_anchor_active:
            for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
                values = ids_map.get(key) or []
                if values:
                    focus_seed_map = {key: [str(values[0]).strip()]}
                    break
            if not focus_seed_map and latest_focus_entity is not None:
                focus_seed_map = _build_focus_seed_map(latest_focus_entity)
            exact_detail_lookup = bool(focus_seed_map)

        raw_query, planner_query, search_query, query_confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries_fn(
            state=state,
            qa=qa,
            ks=ks,
            min_confidence=0.55,
        )
        search_query, anchor_query_meta = repair_query_for_resolved_anchor(
            state=state,
            query=search_query,
        )
        if anchor_query_meta.get("anchor_query_repaired"):
            log_event(
                "RAG.RETRIEVAL_QUERY.ANCHOR_REWRITE",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                planner_query=planner_query,
                repaired_search_query=search_query,
                anchor_source=anchor_query_meta.get("anchor_source"),
                anchor_entity_key=anchor_query_meta.get("anchor_entity_key"),
                reason=anchor_query_meta.get("anchor_repair_reason"),
            )
        if output_type == "detail" and anchor_query_meta.get("anchor_present") and not anchor_query_meta.get("anchor_query_repaired"):
            log_event(
                "RAG.DETAIL.ANCHOR.NOT_APPLIED",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                raw_query=raw_query,
                planner_query=planner_query,
                selected_search_query=search_query,
                anchor_source=anchor_query_meta.get("anchor_source"),
                anchor_entity_key=anchor_query_meta.get("anchor_entity_key"),
            )
        explicit_count = _extract_explicit_count(getattr(state, "question", ""))
        search_num = 1 if exact_detail_lookup else _resolve_retrieval_budget(qa, max_top_k_size=max_top_k_size)
        planner_limit = _coerce_positive_int(getattr(qa, "limit", None))
        planner_display_limit = _coerce_positive_int(getattr(qa, "display_limit", None))
        log_event(
            "RAG.RETRIEVAL_QUERY.RESOLUTION",
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            raw_query=raw_query,
            planner_query=planner_query,
            selected_search_query=search_query,
            confidence=query_confidence,
            drift_detected=drift_detected,
            drift_reasons=drift_reasons,
            fallback_applied=fallback_applied,
            anchor_present=anchor_query_meta.get("anchor_present"),
            anchor_source=anchor_query_meta.get("anchor_source"),
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
            retrieval_query=search_query,
            raw_query=raw_query,
            planner_query=planner_query,
            drift_detected=drift_detected,
            fallback_applied=fallback_applied,
            anchor_present=anchor_query_meta.get("anchor_present"),
            anchor_source=anchor_query_meta.get("anchor_source"),
            anchor_entity_key=anchor_query_meta.get("anchor_entity_key"),
            anchor_query_repaired=anchor_query_meta.get("anchor_query_repaired"),
            anchor_repair_reason=anchor_query_meta.get("anchor_repair_reason"),
        )

        retriever_intent_payload = _build_anchor_locked_payload(state.intent_payload, focus_seed_map) if exact_detail_lookup else state.intent_payload
        if exact_detail_lookup:
            log_event(
                "RAG.DETAIL.ANCHOR.EXACT_LOOKUP",
                request_id=state.request_id,
                conversation_id=state.conversation_id,
                anchor_seed_map=focus_seed_map,
                selected_search_query=search_query,
            )
        retriever = custom_rag_retriever_cls(
            top_k=search_num,
            model_name="gemma_triton_0",
            intent_payload=retriever_intent_payload,
            request_overrides=getattr(state, "request_overrides", None) or {},
        )
        rag_tool = tool_cls(
            name="RAG_Search",
            description="Search the NTIS/IRIS knowledge base.",
            func=retriever.retrieve,
        )

        retrieve_result = await asyncio.to_thread(rag_tool.func, search_query)
        docs = retrieve_result.get("documents", []) if isinstance(retrieve_result, dict) else []
        canonical_evidence = retrieve_result.get("canonical_evidence", []) if isinstance(retrieve_result, dict) else []
        render_profile = retrieve_result.get("render_profile", {}) if isinstance(retrieve_result, dict) else {}
        no_result_message = retrieve_result.get("no_result_message") if isinstance(retrieve_result, dict) else None
        clarification = retrieve_result.get("clarification") if isinstance(retrieve_result, dict) else None
        raw_result_count = int(retrieve_result.get("raw_result_count") or len(docs)) if isinstance(retrieve_result, dict) else len(docs)

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
                log_event=log_event,
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
                turn_id=state.request_id,
                context_kind=context_kind or "project",
                requested_count=display_limit,
                documents=display_bundle.snapshot_documents,
                canonical_evidence=display_bundle.snapshot_canonical_evidence,
                raw_count=raw_result_count,
            )
            view_state.latest_display_snapshot = snapshot
            view_state.active_result_set_kind = output_type
            view_state.active_result_view_id = snapshot.view_id
            view_state.entity_scope = context_kind or "project"
            view_state.last_query_contract = {
                "action": str(_pick_attr(query_intent, qa, key="action", default="") or ""),
                "output_type": output_type,
                "context_kind": context_kind or "project",
                "raw_query": raw_query,
                "planner_query": planner_query,
                "selected_search_query": search_query,
            }
            view_state.refinement_history.append({
                "turn_id": state.request_id,
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
                selected_search_query=search_query,
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

        detail_server_answer = None
        if output_type == "detail" and docs and view_state is not None:
            focus_entity = focus_entity_from_detail(
                context_kind=str((render_profile or {}).get("context_kind") or _pick_attr(query_intent, qa, key="base_route", default="project") or "project").strip().lower(),
                document=docs[0] if docs else None,
                canonical_item=canonical_evidence[0] if canonical_evidence else None,
                source="detail_lookup",
            )
            if focus_entity is not None:
                view_state.latest_focus_entity = focus_entity
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
                coverage = compute_detail_coverage(docs[0], anchor=focus_entity)
                cache_key = make_entity_cache_key(focus_entity)
                requested_fields = extract_requested_fields(state.messages[-1].content)
                view_state.detail_cache[cache_key] = DetailCacheEntry(
                    entity_key=cache_key,
                    anchor=focus_entity,
                    coverage=coverage,
                    hydrated_fields=sorted(set(coverage.available_fields)),
                    source_turn_id=state.request_id,
                )
                detail_server_answer = render_detail_answer(coverage, requested_fields=requested_fields)
                log_event(
                    "DETAIL.COVERAGE",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    entity_found=int(coverage.entity_found),
                    detail_level=coverage.detail_level,
                    available_fields=coverage.available_fields,
                    missing_fields=coverage.missing_fields,
                )
                log_event(
                    "ANSWER.FALLBACK.APPLIED",
                    request_id=state.request_id,
                    conversation_id=state.conversation_id,
                    reason="detail_contract",
                )

        log_event(
            "RAG.RESULT",
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            stage="rag_search",
            docs_found=len(docs),
            query_len=len(str(search_query or "")),
            canonical_evidence_found=len(canonical_evidence),
        )

        return {
            "context": docs,
            "canonical_evidence": canonical_evidence,
            "render_profile": render_profile,
            "no_result_message": no_result_message,
            "clarification": clarification,
            "view_state": view_state,
            "detail_server_answer": detail_server_answer,
        }
    except strategy_violation_cls:
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



