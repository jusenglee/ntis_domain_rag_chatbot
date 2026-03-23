from __future__ import annotations

import asyncio
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
from apps.api.services.view_state import DetailCacheEntry, build_display_snapshot, focus_entity_from_detail
from apps.core.followup_resolution import build_followup_clarification_message, should_short_circuit_followup_clarification


def _get_normalized_intent(state: Any) -> Any:
    """workflow state에서 normalized_intent만 안전하게 꺼낸다."""
    payload = getattr(state, "intent_payload", None)
    return getattr(payload, "normalized_intent", None) if payload else None


def _get_strategy(state: Any) -> Any:
    """workflow state에 실린 strategy 객체를 반환한다."""
    return getattr(state, "strategy", None)


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """여러 source를 순서대로 보며 key에 해당하는 첫 비None 값을 고른다."""
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


def _resolve_display_request(question_analysis: Any, *, docs_count: int) -> int:
    requested = _coerce_positive_int(getattr(question_analysis, "display_limit", None))
    if requested is None:
        requested = _coerce_positive_int(getattr(question_analysis, "limit", None)) or 1
    return min(requested, max(1, int(docs_count or 0) or 1))


def _extract_explicit_count(question: Any) -> Optional[int]:
    count = parse_display_limit(str(question or ""), default=_DISPLAY_LIMIT_SENTINEL)
    return None if count == _DISPLAY_LIMIT_SENTINEL else int(count)


def _normalize_display_payloads(
    *,
    docs: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    base_route: str,
    output_type: str,
    log_event: Any,
    request_id: str,
    conversation_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Display snapshot에 들어갈 documents와 canonical evidence 길이를 맞춘다."""
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

    if len(normalized_docs) != len(normalized_canonical):
        aligned_count = min(len(normalized_docs), len(normalized_canonical))
        log_event(
            "RAG.DISPLAY_INPUT_MISMATCH",
            request_id=request_id,
            conversation_id=conversation_id,
            docs_count=len(normalized_docs),
            canonical_count=len(normalized_canonical),
            aligned_count=aligned_count,
            base_route=base_route,
            output_type=output_type,
        )
        normalized_docs = normalized_docs[:aligned_count]
        normalized_canonical = normalized_canonical[:aligned_count]

    return normalized_docs, normalized_canonical


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

    planner/strategy 신호가 이미 충분히 강하면 LLM 판단을 건너뛰고 즉시 high로 고정해 불필요한 우회를 줄인다.
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
        result = knowledge_sufficiency_cls(
            requires_new_knowledge="low",
            search_intent="followup clarification short-circuit",
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
        return {"knowledge_sufficiency": result, "no_result_message": no_result_message}
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
        "당신은 지식 검색 필요성을 판단하는 분석기입니다.\n"
        "이 시스템에서 사용하는 용어는 모두 국내 연구개발(R&D) 행정 및 제도 맥락으로 해석합니다.\n"
        "[이전 대화]와 [참고 문서]를 기반으로, [현재 질문]에 답하기 위해 새로운 검색이 필요한지 판단하세요.\n\n"
        "판단 기준:\n"
        "1. requires_new_knowledge:\n"
        "   - low: [참고 문서]만으로 충분히 답변 가능\n"
        "   - medium: [참고 문서]로 일부 답변 가능하나 보강 필요\n"
        "   - high: [참고 문서]로 답변 불가하거나 새로운 정보 요청\n\n"
        "2. search_intent: 검색이 필요한 경우, 무엇을 찾아야 하는지 설명\n"
        "3. retrieval_query:\n"
        "   - search_intent 기반 벡터 검색에 최적화된 질의형 쿼리\n"
        "   - 키워드 또는 짧은 구문 형태\n"
        "   - 핵심 개념 5개 이내\n"
        "   - 최대 120자 이내\n"
        "4. confidence: 판단 신뢰도(0.0~1.0)\n\n"
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
                search_intent="일반 검색",
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

    retriever 결과에서 문서, canonical_evidence, render_profile만 꺼내 workflow state로 넘겨 후속 답변 생성이 raw payload에 직접 의존하지 않게 한다.
    """
    ks = state.knowledge_sufficiency
    qa = state.question_analysis
    query_intent = _get_normalized_intent(state)
    view_state = getattr(state, "view_state", None)

    try:
        output_type = str(_pick_attr(query_intent, qa, key="output_type", default="summary") or "summary").strip().lower()
        latest_focus_entity = getattr(view_state, "latest_focus_entity", None)
        if output_type == "detail" and latest_focus_entity is not None:
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

        raw_query, planner_query, search_query, query_confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries_fn(
            state=state,
            qa=qa,
            ks=ks,
            min_confidence=0.55,
        )
        explicit_count = _extract_explicit_count(getattr(state, "question", ""))
        search_num = _resolve_retrieval_budget(qa, max_top_k_size=max_top_k_size)
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
        )

        retriever = custom_rag_retriever_cls(
            top_k=search_num,
            model_name="gemma_triton_0",
            intent_payload=state.intent_payload,
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
        raw_result_count = int(retrieve_result.get("raw_result_count") or len(docs)) if isinstance(retrieve_result, dict) else len(docs)

        context_kind = str((render_profile or {}).get("context_kind") or _pick_attr(query_intent, qa, key="base_route", default="project") or "project").strip().lower()
        list_like_output = output_type in {"list", "relation", "comparison", "series", "stats"}
        if list_like_output and docs:
            docs, canonical_evidence = _normalize_display_payloads(
                docs=docs,
                canonical_evidence=canonical_evidence,
                base_route=context_kind or "project",
                output_type=output_type,
                log_event=log_event,
                request_id=state.request_id,
                conversation_id=state.conversation_id,
            )
        display_limit = _resolve_display_request(qa, docs_count=len(docs))
        if list_like_output and docs and view_state is not None:
            docs_count_before_snapshot = len(docs)
            canonical_count_before_snapshot = len(canonical_evidence)
            snapshot = build_display_snapshot(
                conversation_id=state.conversation_id,
                turn_id=state.request_id,
                context_kind=context_kind or "project",
                requested_count=display_limit,
                documents=docs,
                canonical_evidence=canonical_evidence,
                raw_count=raw_result_count,
            )
            view_state.latest_display_snapshot = snapshot
            view_state.raw_candidates_cache[snapshot.view_id] = [dict(item) for item in docs[: min(len(docs), 20)] if isinstance(item, dict)]
            docs = docs[: snapshot.visible_count]
            canonical_evidence = canonical_evidence[: snapshot.visible_count]
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
                    source=focus_entity.source,
                    pjt_id=focus_entity.pjt_id,
                    pjt_no=focus_entity.pjt_no,
                )
                coverage = compute_detail_coverage(docs[0])
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
