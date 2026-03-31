from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from apps.api.contracts.answer_groundedness import (
    AnswerGroundednessVerdict,
    build_groundedness_snapshot_from_canonical_evidence,
)
from apps.api.services.canonical_context import render_canonical_evidence_text
from apps.api.streaming.contracts import AnswerArtifact
from apps.core.canonical_evidence import build_canonical_evidence


def _coerce_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _coerce_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _resolve_llm_request_overrides(state: Any) -> dict[str, Any]:
    overrides = getattr(state, "request_overrides", None) or {}
    if not isinstance(overrides, dict):
        return {}

    llm_overrides: dict[str, Any] = {}
    temperature = _coerce_float(overrides.get("temperature"))
    top_p = _coerce_float(overrides.get("top_p"))
    max_tokens = _coerce_int(overrides.get("max_tokens"))
    top_k = _coerce_int(overrides.get("top_k"))

    if temperature is not None:
        llm_overrides["temperature"] = temperature
    if top_p is not None:
        llm_overrides["top_p"] = top_p
    if max_tokens is not None:
        llm_overrides["max_tokens_hint"] = max_tokens
    if top_k is not None:
        llm_overrides["top_k"] = top_k
    return llm_overrides


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """여러 객체나 dict에서 같은 속성의 첫 non-None 값을 찾는다."""
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


def _resolve_groundedness_visible_count(state: Any) -> Optional[int]:
    render_profile = getattr(state, "render_profile", None) or {}
    question_analysis = getattr(state, "question_analysis", None)
    output_name = str(
        _pick_attr(render_profile, question_analysis, key="name", default=None)
        or _pick_attr(question_analysis, key="output_type", default=None)
        or ""
    ).strip().lower()
    if output_name not in {"list", "relation", "comparison", "series", "stats"}:
        return None
    view_state = getattr(state, "view_state", None)
    latest_display_snapshot = getattr(view_state, "latest_display_snapshot", None)
    try:
        value = getattr(latest_display_snapshot, "visible_count", None)
        return int(value) if value is not None else None
    except Exception:
        return None


def build_answer_context(
    *,
    answer_context_text: Optional[str] = None,
    docs_for_ctx: list[Any],
    canonical_evidence: Optional[list[dict[str, Any]]] = None,
    render_profile: Optional[dict[str, Any]] = None,
    normalized_intent: Any = None,
    strategy: Any = None,
    qa: Any,
    model_name: str,
    refine_documents_rule_based_fn: Any,
    priority_context_fields: tuple[str, ...],
    max_field_sentences: int,
    max_field_tokens: int,
    default_max_doc_sentences: int,
    default_max_doc_tokens: int,
    solar_max_doc_sentences: int,
    solar_max_doc_tokens: int,
    solar_max_context_chars: int,
    split_sentences_fn: Any,
    logger: Any,
) -> dict[str, Any]:
    """문서와 canonical evidence를 답변 생성용 context text로 정리한다.

    가능하면 canonical_evidence를 그대로 렌더링하고, 없을 때만 docs를 canonical 형태로 파생해
    raw payload 직접 참조를 줄인다.
    """
    is_solar = model_name == "solar_vllm_0"
    profile = dict(render_profile or {})
    if not profile:
        profile = {
            "name": str(_pick_attr(normalized_intent, qa, key="output_type", default="summary")).strip().lower() or "summary",
            "context_kind": str(
                _pick_attr(normalized_intent, strategy, qa, key="base_route")
                or _pick_attr(qa, key="head")
                or "project"
            ).strip().lower() or "project",
        }

    pipeline_context = str(answer_context_text or "").strip()
    if pipeline_context:
        context_text = pipeline_context
        if is_solar and solar_max_context_chars > 0:
            context_text = context_text[:solar_max_context_chars]
        context_sentences = len(split_sentences_fn(context_text)) if context_text and context_text != "NONE" else 0
        context_tokens_est = len(context_text.split()) if context_text and context_text != "NONE" else 0
        return {
            "context_text": context_text,
            "rendered_context_used": context_text != "NONE",
            "context_sentences": context_sentences,
            "context_tokens_est": context_tokens_est,
            "is_solar": is_solar,
            "context_source": "pipeline_context",
        }

    effective_canonical = list(canonical_evidence or [])
    context_source = "canonical_evidence"
    if not effective_canonical and docs_for_ctx:
        base_route = str(profile.get("context_kind") or _pick_attr(normalized_intent, strategy, qa, key="base_route") or _pick_attr(qa, key="head") or "project").strip().lower() or "project"
        output_type = str(profile.get("name") or _pick_attr(normalized_intent, qa, key="output_type", default="summary")).strip().lower() or "summary"
        derived: list[dict[str, Any]] = []
        for rank, item in enumerate(docs_for_ctx, start=1):
            if not isinstance(item, dict):
                continue
            derived.append(
                build_canonical_evidence(
                    item,
                    rank=rank,
                    base_route=base_route,
                    output_type=output_type,
                ).to_dict()
            )
        effective_canonical = derived
        context_source = "derived_canonical_evidence"

    context_text = render_canonical_evidence_text(
        effective_canonical,
        profile,
        max_chars=solar_max_context_chars if is_solar else 0,
    )
    rendered_context_used = bool(effective_canonical) and context_text != "NONE"
    if is_solar and solar_max_context_chars > 0:
        context_text = context_text[:solar_max_context_chars]

    context_sentences = len(split_sentences_fn(context_text)) if context_text and context_text != "NONE" else 0
    context_tokens_est = len(context_text.split()) if context_text and context_text != "NONE" else 0
    return {
        "context_text": context_text,
        "rendered_context_used": rendered_context_used,
        "context_sentences": context_sentences,
        "context_tokens_est": context_tokens_est,
        "is_solar": is_solar,
        "context_source": context_source,
    }


async def generate_answer(
    state: Any,
    *,
    model_name: str,
    final_field: str,
    build_llm_fn: Any,
    build_answer_context_fn: Any,
    load_system_prompt_fn: Any,
    system_prompt_path: Path,
    log_section_fn: Any,
    select_max_tokens_hint_fn: Any,
    run_llm_streaming_fn: Any,
    log_event: Any,
    logger: Any,
    solar_ttft_deadline_ms: int,
    solar_gen_deadline_ms: int,
    solar_stream_max_chars: int,
) -> Dict[str, Any]:
    """모델별 system prompt, reference context, user question을 묶어 최종 답변을 생성한다.

    스트리밍 메트릭과 context 사용 여부를 함께 기록해 이후 병합 단계가 모델 상태를 근거 있게
    판단할 수 있도록 만든다.
    """
    def _build_short_circuit_artifact(*, content: str, answer_kind: str, answer_source: str) -> AnswerArtifact:
        return AnswerArtifact(
            text=content,
            answer_kind=answer_kind,
            stream_metrics={
                "content_chars": len(content),
                "stream_content_emitted_chunks": 1,
                "emitted_chars": len(content),
                "ttft_any_ms": 0.0,
                "ttft_content_ms": 0.0,
            },
            user_visible_final_required=True,
            meta={"answer_source": answer_source, "model_key": final_field.replace("answer_", "")},
        )

    answer_artifact = getattr(state, "answer_artifact", None)
    if isinstance(answer_artifact, AnswerArtifact) and answer_artifact.text:
        rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
        return {
            final_field: answer_artifact.text,
            f"{final_field}_meta": answer_artifact.to_meta_dict(),
            f"answer_artifact_{final_field.replace('answer_', '')}": answer_artifact,
            rendered_context_key: False,
            "stream_meta": {final_field: answer_artifact.to_meta_dict()},
        }

    no_result_message = str(getattr(state, "no_result_message", "") or "").strip()
    if no_result_message:
        log_event(
            "LLM.GENERATE",
            request_id=getattr(state, "request_id", None),
            conversation_id=getattr(state, "conversation_id", None),
            stage="generate_answer_no_result",
            model=model_name,
            ks_level="short_circuit",
            ctx_chars=0,
            ctx_sentences=0,
            ctx_tokens_est=0,
            context_source="no_result_message",
            emitted_chars=len(no_result_message),
        )
        rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
        no_result_artifact = _build_short_circuit_artifact(
            content=no_result_message,
            answer_source="no_result_message",
            answer_kind="no_result",
        )
        return {
            final_field: no_result_message,
            f"{final_field}_meta": no_result_artifact.to_meta_dict(),
            f"answer_artifact_{final_field.replace('answer_', '')}": no_result_artifact,
            rendered_context_key: False,
            "stream_meta": {final_field: no_result_artifact.to_meta_dict()},
        }

    llm = build_llm_fn(model_name=model_name)

    ks = getattr(state, "knowledge_sufficiency", None)
    qa = getattr(state, "question_analysis", None)
    intent_payload = getattr(state, "intent_payload", None)
    normalized_intent = getattr(intent_payload, "normalized_intent", None) if intent_payload else None
    strategy = getattr(state, "strategy", None)
    retrieval_bundle = getattr(state, "retrieval_bundle", None)
    answer_context_text = str(
        getattr(state, "answer_context_text", None)
        or getattr(retrieval_bundle, "answer_context_text", None)
        or ""
    ).strip()
    docs_for_ctx = getattr(state, "context", None) or getattr(state, "prev_context", None) or []
    canonical_evidence = getattr(state, "canonical_evidence", None) or []
    render_profile = getattr(state, "render_profile", None) or {}

    context_info = build_answer_context_fn(
        answer_context_text=answer_context_text,
        docs_for_ctx=docs_for_ctx,
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        normalized_intent=normalized_intent,
        strategy=strategy,
        qa=qa,
        model_name=model_name,
    )
    context_text = context_info["context_text"]
    rendered_context_used = context_info["rendered_context_used"]
    context_sentences = context_info["context_sentences"]
    context_tokens_est = context_info["context_tokens_est"]
    context_source = context_info.get("context_source", "canonical_evidence")

    system_prompt = await load_system_prompt_fn(system_prompt_path)

    messages_state = getattr(state, "messages", None) or []
    last_message = messages_state[-1] if messages_state else HumanMessage(content="")
    question_summary = str(
        _pick_attr(normalized_intent, qa, key="retrieval_query")
        or _pick_attr(qa, key="question_summary")
        or getattr(last_message, "content", "")
        or ""
    ).strip()
    human_prompt = (
        f"[질문 요약]\n{question_summary or '없음'}\n\n"
        f"[원본 질문]\n{getattr(last_message, 'content', '')}\n\n"
        f"[제공된 정보]\n{context_text}"
    )
    log_section_fn("Reference Context", context_text)

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]
    token_hint_source = qa
    strategy_mode = _pick_attr(strategy, key="mode")
    if strategy_mode:
        token_hint_source = type("TokenHintSource", (), {"mode": str(strategy_mode).strip().upper()})()
    max_tokens_hint = select_max_tokens_hint_fn(token_hint_source)
    llm_request_overrides = _resolve_llm_request_overrides(state)
    max_tokens_hint = int(llm_request_overrides.get("max_tokens_hint", max_tokens_hint))
    final_artifact = await run_llm_streaming_fn(
        llm,
        messages,
        emitter=getattr(state, "stream_emitter", None),
        model_key=final_field.replace("answer_", ""),
        max_tokens_hint=max_tokens_hint,
        request_id=getattr(state, "request_id", None),
        ttft_deadline_ms=solar_ttft_deadline_ms if model_name == "solar_vllm_0" else None,
        gen_deadline_ms=solar_gen_deadline_ms if model_name == "solar_vllm_0" else None,
        max_chars=solar_stream_max_chars if model_name == "solar_vllm_0" else None,
        astream_kwargs={key: value for key, value in llm_request_overrides.items() if key != "max_tokens_hint"},
    )
    if isinstance(final_artifact, tuple):
        final_answer, stream_metrics = final_artifact
        final_artifact = AnswerArtifact(
            text=str(final_answer or ""),
            answer_kind="llm_collected",
            stream_metrics=dict(stream_metrics or {}),
            user_visible_final_required=True,
            meta={"model_key": final_field.replace("answer_", "")},
        )
    final_answer = final_artifact.text
    stream_metrics = dict(final_artifact.stream_metrics or {})

    ttft_any_ms = stream_metrics.get("ttft_any_ms")
    ttft_content_ms = stream_metrics.get("ttft_content_ms")
    reasoning_chars = int(stream_metrics.get("reasoning_chars") or 0)
    content_chars = int(stream_metrics.get("content_chars") or 0)

    logger.info(
        "[stream_metrics] request_id=%s model=%s ttft_any_ms=%s ttft_content_ms=%s reasoning_chars=%s content_chars=%s",
        getattr(state, "request_id", None),
        model_name,
        ttft_any_ms,
        ttft_content_ms,
        reasoning_chars,
        content_chars,
    )

    if model_name == "solar_vllm_0":
        content_delay_ms: Optional[float] = None
        if ttft_any_ms is not None and ttft_content_ms is not None:
            content_delay_ms = round(ttft_content_ms - ttft_any_ms, 1)

        if ttft_any_ms is None:
            logger.warning(
                "[solar_stream_guard] request_id=%s category=stream_not_started_or_stalled ttft_any_ms=%s ttft_content_ms=%s ttft_deadline_exceeded=%s deadline_exceeded=%s",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                bool(stream_metrics.get("ttft_deadline_exceeded")),
                bool(stream_metrics.get("deadline_exceeded")),
            )
        elif ttft_content_ms is None or (content_delay_ms is not None and content_delay_ms >= 500):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=content_delayed ttft_any_ms=%s ttft_content_ms=%s content_delay_ms=%s reasoning_chars=%s content_chars=%s",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                content_delay_ms,
                reasoning_chars,
                content_chars,
            )
        elif stream_metrics.get("gen_deadline_exceeded"):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=gen_deadline_exceeded gen_deadline_ms=%s truncated_chars=%s emitted_chars=%s",
                getattr(state, "request_id", None),
                solar_gen_deadline_ms,
                len(final_answer),
                stream_metrics.get("emitted_chars"),
            )
            if stream_metrics.get("short_output_guard_triggered"):
                logger.warning(
                    "[solar_stream_guard] request_id=%s short_output_guard_triggered min_chars=%s emitted_chars=%s",
                    getattr(state, "request_id", None),
                    stream_metrics.get("short_output_guard_min_chars"),
                    stream_metrics.get("emitted_chars"),
                )
        elif stream_metrics.get("char_limited"):
            logger.warning(
                "[solar_stream_guard] request_id=%s category=char_limited max_chars=%s truncated_chars=%s",
                getattr(state, "request_id", None),
                solar_stream_max_chars,
                len(final_answer),
            )

    log_event(
        "LLM.GENERATE",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        stage="generate_answer",
        model=model_name,
        request_temperature=llm_request_overrides.get("temperature"),
        request_top_p=llm_request_overrides.get("top_p"),
        request_max_tokens=max_tokens_hint,
        request_top_k=llm_request_overrides.get("top_k"),
        ks_level=(getattr(ks, "requires_new_knowledge", None) if ks else "unknown"),
        ctx_chars=len(context_text),
        ctx_sentences=context_sentences,
        ctx_tokens_est=context_tokens_est,
        context_source=context_source,
        emitted_chars=len(final_answer or ""),
    )
    rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
    return {
        final_field: final_answer,
        f"{final_field}_meta": stream_metrics,
        f"answer_artifact_{final_field.replace('answer_', '')}": final_artifact,
        rendered_context_key: rendered_context_used,
        "stream_meta": {final_field: stream_metrics},
    }


async def merge_answers(
    state: Any,
    *,
    select_final_answer_fn: Any,
    log_event: Any,
    logger: Any,
    dual_model_merge_policy: str,
    dual_model_fallback_message: str,
    solar_min_answer_chars: int,
) -> Dict[str, Any]:
    """Solar과 Gemma 결과 중 최종 답변을 선택하고 병합 메타를 기록한다.

    선택 정책은 별도 함수에 위임하고, 여기서는 선택 사유와 실패 징후를 workflow state에 보존한다.
    """
    has_docs_context = bool(getattr(state, "context", None) or getattr(state, "prev_context", None) or getattr(state, "canonical_evidence", None))
    rendered_context_used = bool(getattr(state, "rendered_context_used_gemma", False)) or bool(getattr(state, "rendered_context_used_solar", False))

    answer_gemma = (getattr(state, "answer_gemma", "") or "").strip()
    answer_gemma_meta = getattr(state, "answer_gemma_meta", None) or {}
    answer_solar_raw = getattr(state, "answer_solar", None) or ""
    answer_solar = answer_solar_raw.strip()
    solar_meta = getattr(state, "answer_solar_meta", None) or {}
    answer_artifact_gemma = getattr(state, "answer_artifact_gemma", None)
    answer_artifact_solar = getattr(state, "answer_artifact_solar", None)
    canonical_evidence = getattr(state, "canonical_evidence", None) or []
    groundedness_snapshot_model = build_groundedness_snapshot_from_canonical_evidence(
        canonical_evidence=canonical_evidence,
        visible_count=_resolve_groundedness_visible_count(state),
    )
    groundedness_snapshot = groundedness_snapshot_model.model_dump()
    selection = select_final_answer_fn(
        answer_solar=answer_solar,
        answer_gemma=answer_gemma,
        solar_meta=solar_meta,
        gemma_meta=answer_gemma_meta,
        policy=dual_model_merge_policy,
        fallback_message=dual_model_fallback_message,
        min_answer_chars=solar_min_answer_chars,
        evidence_snapshot=groundedness_snapshot,
    )
    solar_fail_reasons = list(selection["solar_fail_reasons"])
    solar_warning_reasons = list(selection["solar_warning_reasons"])
    solar_failed = bool(selection["solar_failed"])
    gemma_fail_reasons = list(selection.get("gemma_fail_reasons", []))
    gemma_warning_reasons = list(selection.get("gemma_warning_reasons", []))
    gemma_failed = bool(selection.get("gemma_failed", False))
    solar_groundedness = dict(selection.get("solar_groundedness") or {})
    gemma_groundedness = dict(selection.get("gemma_groundedness") or {})
    selected_model = str(selection["selected_model"])
    selected_answer = str(selection["selected_answer"])
    degraded = bool(getattr(state, "degraded", False)) or (selected_answer == dual_model_fallback_message)
    selected_meta = {}
    if selected_model == "solar":
        selected_meta = solar_meta
    elif selected_model == "gemma":
        selected_meta = answer_gemma_meta
    selected_answer_source = str(selected_meta.get("answer_source") or selected_model)
    selected_artifact = None
    if selected_model == "solar" and isinstance(answer_artifact_solar, AnswerArtifact):
        selected_artifact = answer_artifact_solar
    elif selected_model == "gemma" and isinstance(answer_artifact_gemma, AnswerArtifact):
        selected_artifact = answer_artifact_gemma
    elif selected_answer:
        selected_artifact = AnswerArtifact(
            text=selected_answer,
            answer_kind=("direct_answer" if selected_model == "fallback" else "llm_collected"),
            stream_metrics=dict(selected_meta or {}),
            user_visible_final_required=True,
            meta={"answer_source": selected_answer_source, "model_key": selected_model},
        )
    if selected_model == "solar":
        selected_groundedness = solar_groundedness
    elif selected_model == "gemma":
        selected_groundedness = gemma_groundedness
    else:
        selected_groundedness = AnswerGroundednessVerdict(
            status="skipped_fallback",
            reason_codes=[],
            unsupported_claims=[],
            insufficient_axes=[],
            checked_claims=0,
            snapshot_source=str(groundedness_snapshot.get("snapshot_source") or "none"),
            claims={},
        ).model_dump()
    if isinstance(selected_artifact, AnswerArtifact):
        enriched_meta = dict(selected_artifact.meta or {})
        enriched_meta.setdefault("answer_source", selected_answer_source)
        enriched_meta.setdefault("model_key", selected_model)
        enriched_meta["groundedness_status"] = selected_groundedness.get("status")
        enriched_meta["groundedness_reason_codes"] = list(selected_groundedness.get("reason_codes") or [])
        enriched_meta["groundedness_summary"] = selected_groundedness
        selected_artifact = AnswerArtifact(
            text=selected_artifact.text,
            answer_kind=selected_artifact.answer_kind,
            stream_metrics=dict(selected_artifact.stream_metrics or {}),
            user_visible_final_required=bool(selected_artifact.user_visible_final_required),
            references=list(selected_artifact.references or []),
            clarification=selected_artifact.clarification,
            error=selected_artifact.error,
            meta=enriched_meta,
        )

    merge_debug = {
        "policy": dual_model_merge_policy,
        "selected_model": selected_model,
        "selected_answer_source": selected_answer_source,
        "selection_reason": selection["selection_reason"],
        "solar_failed": solar_failed,
        "solar_fail_reasons": solar_fail_reasons,
        "solar_warning_reasons": solar_warning_reasons,
        "solar_meta": selection["solar_meta"],
        "gemma_failed": gemma_failed,
        "gemma_fail_reasons": gemma_fail_reasons,
        "gemma_warning_reasons": gemma_warning_reasons,
        "gemma_meta": selection.get("gemma_meta", {}),
        "solar_answer_chars": selection["solar_answer_chars"],
        "gemma_answer_chars": selection["gemma_answer_chars"],
        "min_chars_threshold": solar_min_answer_chars,
        "selected_answer_kind": (selected_artifact.answer_kind if isinstance(selected_artifact, AnswerArtifact) else None),
        "groundedness_snapshot": groundedness_snapshot,
        "solar_groundedness": solar_groundedness,
        "gemma_groundedness": gemma_groundedness,
        "selected_groundedness": selected_groundedness,
    }

    log_event(
        "LLM.RESULT",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        stage="merge_answers",
        selected_model=selected_model,
        solar_failed=int(solar_failed),
        solar_fail_reasons=solar_fail_reasons,
        gemma_failed=int(gemma_failed),
        gemma_fail_reasons=gemma_fail_reasons,
        gemma_answer_chars=len(answer_gemma),
        solar_answer_chars=len(answer_solar),
        groundedness_status=selected_groundedness.get("status"),
        groundedness_reason_codes=list(selected_groundedness.get("reason_codes") or []),
    )
    logger.info(
        "[merge_selection] request_id=%s selected_model=%s solar_fail_reasons=%s",
        getattr(state, "request_id", None),
        selected_model,
        solar_fail_reasons,
    )

    return {
        "messages": [AIMessage(content=selected_answer)],
        "final_answer_text": selected_answer,
        "final_answer_artifact": selected_artifact,
        "answer_solar_raw": answer_solar_raw,
        "merge_debug": merge_debug,
        "selected_answer_meta": (selected_artifact.to_meta_dict() if isinstance(selected_artifact, AnswerArtifact) else selected_meta),
        "answer_groundedness_snapshot": groundedness_snapshot_model,
        "answer_groundedness_verdict": AnswerGroundednessVerdict.model_validate(selected_groundedness),
        "rendered_context_used": rendered_context_used,
        "degraded": degraded,
    }






