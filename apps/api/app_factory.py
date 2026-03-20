"""FastAPI app factory and workflow assembly for the NTIS RAG application."""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI
from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.tools import Tool
from langgraph.graph import END, StateGraph

from apps.api.contracts.runtime_contracts import (
    friendly_strategy_violation_message,
    sanitize_ids_map_semantics,
    validate_project_key_env_contract,
)
from apps.api.contracts.workflow_models import (
    AgentState,
    KnowledgeSufficiency,
    PLANNER_SCHEMA_VERSION,
    QuestionAnalysis,
    RuleDecision,
    measure_latency as measure_latency_impl,
)
from apps.api.routes import RouteDeps, register_routes
from apps.api.runtime import AppRuntimeConfig, initialize_app_runtime, shutdown_app_runtime
from apps.api.services.answer_generation import build_answer_context, generate_answer, merge_answers
from apps.api.services.answer_merge import select_final_answer
from apps.api.services.context_renderer import refine_documents_rule_based, split_sentences
from apps.api.services.conversation_store import (
    build_save_history_payload,
    load_conversation_memory_from_store,
    save_conversation_memory,
)
from apps.api.services.llm_json import sanitize_llm_json
from apps.api.services.llm_runtime import build_llm, get_llm_cache, load_system_prompt
from apps.api.services.planner_runtime import run_stagewise_question_analysis
from apps.api.services.planner_service import (
    apply_planner_strategy,
    apply_question_analysis_v3,
    collect_researcher_name_terms,
    merge_planner_hints,
    normalize_hint_terms,
)
from apps.api.services.query_analysis import run_question_analysis
from apps.api.services.rag_retriever import CustomRAGRetriever, is_hit_source, resolve_rag_queries
from apps.api.services.request_facade import build_intent_payload as build_intent_payload_impl
from apps.api.services.retrieval_workflow import node_knowledge_sufficiency, node_rag_search
from apps.api.services.runtime_helpers import (
    compute_total_ms_from_start,
    derive_stream_error_code,
    extract_contract_failure_details,
    extract_stream_chunk_text_and_field,
    has_superlative_cue,
    is_debug_logging_enabled,
    mask_query_for_log,
    select_max_tokens_hint,
    setup_file_logging,
    sha256_file,
    truncate_text,
)
from apps.api.services.workflow_builder import WorkflowNodes, build_request_workflow
from apps.api.services.workflow_nodes import (
    node_analyze_question,
    node_direct_answer,
    node_join_answers,
    node_load_memory,
    node_rule_precheck,
    node_save_history,
)
from apps.core.llm_streaming import run_llm_streaming
from apps.core.metrics import (
    PROMETHEUS_TIMEOUT as METRICS_PROMETHEUS_TIMEOUT,
    STREAM_INTERVAL_SECONDS as METRICS_STREAM_INTERVAL_SECONDS,
    collect_snapshot as collect_metrics_snapshot,
)
from apps.api.rag_mapper.rag_mapper import RagMapper
from apps.core.log_keys import CHANGED_BY_PLANNER_MERGE
from apps.core.pipeline_steps import build_changed_fields, normalize_intent
from apps.core.planner_contract import StrategyViolation
from apps.core.query_intent import (
    _cheap_precheck,
    classify_query as classify_query_intent,
    extract_perf_types,
    extract_title_terms,
    extract_years,
    normalize_org_terms,
)
from apps.core.rag_runtime_observability import get_code_fingerprint_fields, set_log_context
from apps.core.rag_store import build_rag_objects
from apps.core.retrieval import ensure_keyword_index, ensure_text_index, warmup_sparse_encoder
from apps.core.schemas import IntentPayloadV3, PlannerStage1Decision, PlannerStage2Slots
from apps.core.settings import (
    MAX_CONTEXT_CHARS,
    MAX_DOC_SENTENCES,
    MAX_DOC_TOKENS,
    MAX_TOP_K_SIZE,
    REDIS_TTL,
    REDIS_URL,
    SOLAR_VLLM_CONFIG,
)


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

logger = setup_file_logging(logger_name="Chatbot_Server")
measure_latency = partial(measure_latency_impl, logger_obj=logger)

TEMPLATE_INDEX_PATH = Path("templates/index.html")
MAX_HISTORY_TURNS = 10
HISTORY_PREVIEW_LIMIT = 100
SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "4096"))
FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "4096"))
SOLAR_DEADLINE_MS = int(os.getenv("SOLAR_DEADLINE_MS", "9000"))
SOLAR_TTFT_DEADLINE_MS = int(os.getenv("SOLAR_TTFT_DEADLINE_MS", str(SOLAR_DEADLINE_MS)))
SOLAR_GEN_DEADLINE_MS = int(os.getenv("SOLAR_GEN_DEADLINE_MS", "15000"))
SOLAR_STREAM_MAX_CHARS = int(os.getenv("SOLAR_STREAM_MAX_CHARS", "10000"))
DUAL_MODEL_MERGE_POLICY = os.getenv("DUAL_MODEL_MERGE_POLICY", "solar_first").strip().lower()
DUAL_MODEL_FALLBACK_MESSAGE = "The generated answer was empty. Please try again."
SOLAR_MIN_ANSWER_CHARS = int(os.getenv("SOLAR_MIN_ANSWER_CHARS", "60"))
MAX_FIELD_SENTENCES = int(os.getenv("MAX_FIELD_SENTENCES", "3"))
MAX_FIELD_TOKENS = int(os.getenv("MAX_FIELD_TOKENS", "120"))
SOLAR_MAX_DOC_SENTENCES = int(os.getenv("SOLAR_MAX_DOC_SENTENCES", str(MAX_DOC_SENTENCES)))
SOLAR_MAX_DOC_TOKENS = int(os.getenv("SOLAR_MAX_DOC_TOKENS", str(MAX_DOC_TOKENS)))
SOLAR_MAX_CONTEXT_CHARS = int(os.getenv("SOLAR_MAX_CONTEXT_CHARS", str(MAX_CONTEXT_CHARS)))
PRIORITY_CONTEXT_FIELDS = tuple(
    field.strip()
    for field in os.getenv(
        "PRIORITY_CONTEXT_FIELDS",
        "title,title_text,title1,title2,pjt_id,pjt_no,project_id,project_no,ntis_task_id,task_id",
    ).split(",")
    if field.strip()
)
PLANNER_DISABLE_THINKING = os.getenv("PLANNER_DISABLE_THINKING", "true").strip().lower() in {"1", "true", "yes", "on"}
PLANNER_STAGE1_PROMPT_VERSION = os.getenv("PLANNER_STAGE1_PROMPT_VERSION", "v1").strip()
PLANNER_STAGE2_PROMPT_VERSION = os.getenv("PLANNER_STAGE2_PROMPT_VERSION", "v1").strip()
PLANNER_TEMPERATURE = float(os.getenv("PLANNER_TEMPERATURE", "0.0"))
PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS = {
    "pjt_id",
    "pjt_no",
    "doi",
    "issn",
    "rst_id",
    "paper_id",
}
RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT = os.getenv("RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def log_section(title: str, content: str) -> None:
    """디버그 로깅이 켜졌을 때만 구간 헤더가 붙은 로그 블록을 남긴다.
    크게 쓰는 trace 로그를 눈에 띄게 구분해 planner·retrieval·merge 단계를 한눈에 보게 한다.
    """
    if not is_debug_logging_enabled():
        return
    header = f"\n\033[96m{'=' * 10} [{title}] {'=' * 10}\033[0m"
    footer = f"\033[96m{'=' * 30}\033[0m\n"
    logger.info("%s\n%s\n%s", header, content, footer)


_APP_MAIN_FILE_PATH = Path(__file__).resolve()
_CODE_FINGERPRINT_FIELDS: Dict[str, str] = {
    "app_main_sha256": sha256_file(_APP_MAIN_FILE_PATH),
    **get_code_fingerprint_fields(),
}


def _log_event(name: str, **fields: Any) -> None:
    """운영 이벤트를 code fingerprint와 함께 구조화해 기록한다.
    policy mode, request/conversation id, stage meta를 한 형식으로 남기는 중앙 옵스 로깅 입구다.
    """
    payload = {"event": name, **_CODE_FINGERPRINT_FIELDS}
    if fields.get("policy_mode") is None:
        payload["policy_mode"] = "strict" if str(os.getenv("RAG_STRICT_STRATEGY_CONSISTENCY", "1")).strip().lower() in ("1", "true", "yes", "y") else "compat"
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = value
    logger.info("[OPS] %s", json.dumps(payload, ensure_ascii=False, default=str))


def _state_log_summary_fields(state: Any, total_ms: Optional[int] = None) -> Dict[str, Any]:
    """workflow state에서 요약 로그에 실 필드만 추출한다.
    planner truth과 execution strategy truth를 분리해 기록함으로써, 어느 단계에서 전략이 고정되었는지 나중에 바로 추적할 수 있게 한다.
    """
    question_analysis = getattr(state, "question_analysis", None)
    strategy = getattr(state, "strategy", None)
    intent_payload = getattr(state, "intent_payload", None)
    context = getattr(state, "context", None) or []
    merge_debug = getattr(state, "merge_debug", None) or {}
    timings = getattr(state, "timings", None) or {}
    target_cols = getattr(strategy, "target_collections", None)
    if target_cols is None:
        target_cols = getattr(question_analysis, "target_cols", None)
    return {
        "request_id": getattr(state, "request_id", None),
        "conversation_id": getattr(state, "conversation_id", None),
        "stage": "summary",
        "strategy_source": "execution_strategy" if strategy is not None else "assembled_question_analysis_only",
        "mode": getattr(strategy, "mode", None) or getattr(question_analysis, "mode", None),
        "relation": getattr(strategy, "relation", None) or getattr(question_analysis, "relation", None),
        "target_cols": list(target_cols) if isinstance(target_cols, (list, tuple)) else target_cols,
        "join_key_mode": getattr(strategy, "join_key_mode", None),
        "question_analysis_mode": getattr(question_analysis, "mode", None),
        "question_analysis_relation": getattr(question_analysis, "relation", None),
        "question_analysis_join_key_mode": getattr(question_analysis, "join_key_mode", None),
        "execution_mode": getattr(strategy, "mode", None),
        "execution_relation": getattr(strategy, "relation", None),
        "execution_join_key_mode": getattr(strategy, "join_key_mode", None),
        "join_key_source": getattr(strategy, "join_key_source", None),
        "join_compile_selection": getattr(strategy, "join_compile_selection", None),
        "hop2_key_strategy": getattr(strategy, "hop2_key_strategy", None),
        "resolved_runtime_key_kind": getattr(strategy, "resolved_runtime_key_kind", None),
        "join_keys_used_count": getattr(strategy, "join_keys_used_count", None),
        "query_graph_kind": getattr(strategy, "query_graph_kind", None),
        "anchor_summary": getattr(strategy, "anchor_summary", None),
        "anchor_resolution_status": getattr(strategy, "anchor_resolution_status", None),
        "ambiguity_codes": list(getattr(strategy, "ambiguity_codes", tuple()) or []),
        "resolved_researcher_count": getattr(strategy, "resolved_researcher_count", None),
        "resolved_org_count": getattr(strategy, "resolved_org_count", None),
        "aggregation_kind": getattr(strategy, "aggregation_kind", None),
        "aggregation_metric": timings.get("info.aggregation_metric") or None,
        "aggregation_group_by": timings.get("info.aggregation_group_by") or None,
        "aggregation_threshold": timings.get("info.aggregation_threshold"),
        "aggregation_result_count": timings.get("info.aggregation_result_count"),
        "failed_step": timings.get("info.failed_step") or None,
        "series_kind": getattr(strategy, "series_kind", None),
        "series_result_count": timings.get("info.series_result_count"),
        "series_bucket_count": timings.get("info.series_bucket_count"),
        "pattern_kind": getattr(strategy, "pattern_kind", None) or timings.get("info.pattern_kind") or None,
        "bundle_kind": getattr(strategy, "bundle_kind", None) or timings.get("info.bundle_kind") or None,
        "bundle_target_count": timings.get("info.bundle_target_count"),
        "bundle_project_count": timings.get("info.bundle_project_count"),
        "bundle_item_count": timings.get("info.bundle_item_count"),
        "guidance_required": getattr(strategy, "guidance_required", None) if strategy is not None else timings.get("info.guidance_required"),
        "pattern_result_count": timings.get("info.pattern_result_count"),
        "pattern_subject_count": timings.get("info.pattern_subject_count"),
        "pattern_support_doc_count": timings.get("info.pattern_support_doc_count"),
        "reverse_trace_enabled": getattr(strategy, "reverse_trace_enabled", None),
        "reverse_trace_hop_count": timings.get("info.reverse_trace_hop_count") or getattr(strategy, "reverse_trace_hop_count", None),
        "origin_project_count": timings.get("info.origin_project_count"),
        "followup_perf_count": timings.get("info.followup_perf_count"),
        "planner_mode": getattr(question_analysis, "mode", None),
        "planner_relation": getattr(question_analysis, "relation", None),
        "planner_target_cols": getattr(question_analysis, "target_cols", None),
        "intent_payload_version": getattr(intent_payload, "intent_payload_version", None),
        "strategy_version": getattr(question_analysis, "strategy_version", None),
        "project_key_policy": timings.get("info.project_key_policy") or getattr(strategy, "project_key_policy", None),
        "join_resolution_policy": timings.get("info.join_resolution_policy") or getattr(strategy, "join_resolution_policy", None),
        "resolved_join_key_mode": timings.get("info.resolved_join_key_mode") or None,
        "dual_branch_used": timings.get("info.dual_branch_used"),
        "candidate_project_key_count": timings.get("info.candidate_project_key_count") or ((getattr(intent_payload, "strategy_meta", None) or {}).get("candidate_project_key_count") if intent_payload is not None else None),
        "candidate_perf_key_count": timings.get("info.candidate_perf_key_count") or ((getattr(intent_payload, "strategy_meta", None) or {}).get("candidate_perf_key_count") if intent_payload is not None else None),
        "docs_found": len(context),
        "selected_model": merge_debug.get("selected_model"),
        "rendered_context_used": int(bool(getattr(state, "rendered_context_used", False))),
        "contract_fail_reason": timings.get("info.contract_fail_reason") or None,
        "empty_result_policy": timings.get("info.empty_result_policy") or None,
        "reranked_count": timings.get("info.reranked_count"),
        "degraded": int(bool(getattr(state, "degraded", False))),
        "total_ms": total_ms,
    }


APP_RUNTIME_CONFIG = AppRuntimeConfig(
    redis_url=REDIS_URL,
    planner_stagewise_enabled=True,
    planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
    planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
    ensure_payload_index_on_boot=RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT,
    sparse_warmup_on_boot=os.getenv("RAG_FASTEMBED_WARMUP_ON_BOOT", "true").strip().lower() in {"1", "true", "yes", "on"},
    metrics_timeout_seconds=METRICS_PROMETHEUS_TIMEOUT,
    payload_keyword_index_targets={
        "ntis_project_v1": ["pjt_id", "pjt_no"],
        "ntis_perf_v1": ["pjt_id", "pjt_no"],
    },
    payload_text_index_targets={
        "ntis_project_v1": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm"],
        "ntis_perf_v1": ["org_nm", "prtcp_org[].org_nm", "prtcp_mp[].blng_org_nm"],
    },
)


async def _run_question_analysis(
    *,
    question: str,
    conversation_id: str,
    chat_history: list[Any],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]] | None = None,
    request_id: Optional[str] = None,
    normalized_intent_base: Any = None,
) -> QuestionAnalysis:
    """request facade, stagewise planner, planner merge를 연결해 최종 question analysis를 만든다.
    cheap precheck로 끝낼지, stage 1/2 planner를 탈지, merge 결과가 어떤 필드를 바꿈는지를 이 코루틴이 중앙에서 다룬다.
    """
    return await run_question_analysis(
        question=question,
        conversation_id=conversation_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence or [],
        request_id=request_id,
        normalized_intent_base=normalized_intent_base,
        classify_query_intent=classify_query_intent,
        normalize_intent=normalize_intent,
        run_stagewise_question_analysis=run_stagewise_question_analysis,
        build_llm=lambda model_name: build_llm(model_name=model_name, solar_vllm_config=SOLAR_VLLM_CONFIG),
        planner_stage1_decision_cls=PlannerStage1Decision,
        planner_stage2_slots_cls=PlannerStage2Slots,
        question_analysis_cls=QuestionAnalysis,
        load_prompt_file=load_system_prompt,
        sanitize_llm_json=lambda msg: sanitize_llm_json(msg, logger=logger),
        sanitize_ids_map_semantics=sanitize_ids_map_semantics,
        log_event=_log_event,
        planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        planner_disable_thinking=PLANNER_DISABLE_THINKING,
        planner_temperature=PLANNER_TEMPERATURE,
        planner_schema_version=PLANNER_SCHEMA_VERSION,
        planner_stage2_regate_seed_allowed_keys=PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
        max_top_k_size=MAX_TOP_K_SIZE,
    )


@measure_latency("generate_answer_gemma")
async def node_generate_answer_gemma(state: AgentState) -> Dict[str, Any]:
    """Gemma 모델을 사용해 답변 생성 node를 위임 호출한다.
    dual-model workflow에서 모델 별 책임을 분리하기 위한 연결 헬퍼다.
    """
    return await _generate_answer(state, "gemma_triton_0", "answer_gemma")


@measure_latency("generate_answer_solar")
async def node_generate_answer_solar(state: AgentState) -> Dict[str, Any]:
    """Solar 모델을 사용해 답변 생성 node를 위임 호출한다.
    merge policy가 Solar 우선인지, fallback인지와 무관하게 같은 입출력 계약을 유지한다.
    """
    return await _generate_answer(state, "solar_vllm_0", "answer_solar")


async def _generate_answer(state: AgentState, model_name: str, final_field: str) -> Dict[str, Any]:
    """현재 state의 context·strategy·deadline 설정을 모아 실제 answer generation을 실행한다.
    rendered context 사용 여부, max token hint, stream timeout, LLM fallback 설정을 맞추는 실질 생성 관문이다.
    """
    return await generate_answer(
        state,
        model_name=model_name,
        final_field=final_field,
        build_llm_fn=lambda model_name: build_llm(model_name=model_name, solar_vllm_config=SOLAR_VLLM_CONFIG),
        build_answer_context_fn=lambda **kwargs: build_answer_context(
            **kwargs,
            refine_documents_rule_based_fn=refine_documents_rule_based,
            priority_context_fields=PRIORITY_CONTEXT_FIELDS,
            max_field_sentences=MAX_FIELD_SENTENCES,
            max_field_tokens=MAX_FIELD_TOKENS,
            default_max_doc_sentences=MAX_DOC_SENTENCES,
            default_max_doc_tokens=MAX_DOC_TOKENS,
            solar_max_doc_sentences=SOLAR_MAX_DOC_SENTENCES,
            solar_max_doc_tokens=SOLAR_MAX_DOC_TOKENS,
            solar_max_context_chars=SOLAR_MAX_CONTEXT_CHARS,
            split_sentences_fn=split_sentences,
            logger=logger,
        ),
        load_system_prompt_fn=load_system_prompt,
        system_prompt_path=Path("prompts/ntis_chatbot.md"),
        log_section_fn=log_section,
        select_max_tokens_hint_fn=lambda qa: select_max_tokens_hint(
            qa,
            short_answer_max_tokens_hint=SHORT_ANSWER_MAX_TOKENS_HINT,
            follow_up_max_tokens_hint=FOLLOW_UP_MAX_TOKENS_HINT,
        ),
        run_llm_streaming_fn=run_llm_streaming,
        log_event=_log_event,
        logger=logger,
        solar_ttft_deadline_ms=SOLAR_TTFT_DEADLINE_MS,
        solar_gen_deadline_ms=SOLAR_GEN_DEADLINE_MS,
        solar_stream_max_chars=SOLAR_STREAM_MAX_CHARS,
    )


@measure_latency("merge_answers")
async def node_merge_answers(state: AgentState) -> Dict[str, Any]:
    """dual-model 결과를 merge policy에 따라 하나의 답변으로 선정한다.
    빈 답변, 짧은 답변, 호출 실패 상태를 감안해 최종 사용자 visible answer를 고른다.
    """
    return await merge_answers(
        state,
        select_final_answer_fn=select_final_answer,
        log_event=_log_event,
        logger=logger,
        dual_model_merge_policy=DUAL_MODEL_MERGE_POLICY,
        dual_model_fallback_message=DUAL_MODEL_FALLBACK_MESSAGE,
        solar_min_answer_chars=SOLAR_MIN_ANSWER_CHARS,
    )


def build_advanced_workflow() -> Any:
    """memory, planner, retrieval, answer merge를 엮는 LangGraph workflow를 구성한다.
    node 연결 순서와 branch 조건을 이 함수에서 고정해 app 부트 시 재사용할 수 있는 그래프를 만든다.
    """
    load_memory_node = partial(
        node_load_memory,
        load_conversation_memory_fn=partial(
            load_conversation_memory_from_store,
            logger=logger,
            truncate_text=lambda value, limit=HISTORY_PREVIEW_LIMIT: truncate_text(value, limit),
        ),
        log_event=_log_event,
    )
    analyze_question_node = partial(
        node_analyze_question,
        build_intent_payload_fn=partial(
            build_intent_payload_impl,
            cheap_precheck=_cheap_precheck,
            has_superlative_cue=has_superlative_cue,
            extract_years=extract_years,
            extract_perf_types=extract_perf_types,
            extract_title_terms=extract_title_terms,
            classify_query_intent=classify_query_intent,
            normalize_intent=normalize_intent,
            run_question_analysis=_run_question_analysis,
            apply_question_analysis_v3=partial(
                apply_question_analysis_v3,
                merge_planner_hints=partial(
                    merge_planner_hints,
                    normalize_org_terms=normalize_org_terms,
                    normalize_hint_terms=normalize_hint_terms,
                    collect_researcher_name_terms=collect_researcher_name_terms,
                ),
                normalize_hint_terms=normalize_hint_terms,
                apply_planner_strategy_fn=partial(
                    apply_planner_strategy,
                    normalize_hint_terms=normalize_hint_terms,
                    log_event=_log_event,
                    build_changed_fields=build_changed_fields,
                    changed_by_planner_merge=CHANGED_BY_PLANNER_MERGE,
                    strategy_violation_cls=StrategyViolation,
                ),
            ),
            log_event=_log_event,
            intent_payload_cls=IntentPayloadV3,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version=PLANNER_STAGE1_PROMPT_VERSION,
            planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        ),
    )
    knowledge_sufficiency_node = measure_latency("knowledge_sufficiency")(
        partial(
            node_knowledge_sufficiency,
            build_llm_fn=lambda model_name: build_llm(model_name=model_name, solar_vllm_config=SOLAR_VLLM_CONFIG),
            pydantic_output_parser_cls=PydanticOutputParser,
            chat_prompt_template_cls=ChatPromptTemplate,
            knowledge_sufficiency_cls=KnowledgeSufficiency,
            sanitize_llm_json_fn=lambda msg: sanitize_llm_json(msg, logger=logger),
            refine_documents_rule_based_fn=refine_documents_rule_based,
            priority_context_fields=PRIORITY_CONTEXT_FIELDS,
            max_field_sentences=MAX_FIELD_SENTENCES,
            max_field_tokens=MAX_FIELD_TOKENS,
            default_max_doc_sentences=MAX_DOC_SENTENCES,
            default_max_doc_tokens=MAX_DOC_TOKENS,
            logger=logger,
            log_event=_log_event,
        )
    )
    rag_search_node = measure_latency("rag_search")(
        partial(
            node_rag_search,
            resolve_rag_queries_fn=resolve_rag_queries,
            custom_rag_retriever_cls=CustomRAGRetriever,
            tool_cls=Tool,
            max_top_k_size=MAX_TOP_K_SIZE,
            strategy_violation_cls=StrategyViolation,
            logger=logger,
            log_event=_log_event,
        )
    )
    save_history_node = partial(
        node_save_history,
        build_save_history_payload_fn=build_save_history_payload,
        save_conversation_memory_fn=save_conversation_memory,
        logger=logger,
        log_event=_log_event,
        state_log_summary_fields_fn=_state_log_summary_fields,
        compute_total_ms_from_start_fn=compute_total_ms_from_start,
        max_history_turns=MAX_HISTORY_TURNS,
        history_ttl_seconds=REDIS_TTL,
    )
    return build_request_workflow(
        agent_state_type=AgentState,
        state_graph_cls=StateGraph,
        end=END,
        nodes=WorkflowNodes(
            load_memory=load_memory_node,
            rule_precheck=partial(node_rule_precheck, rule_decision_cls=RuleDecision),
            analyze_question=analyze_question_node,
            judge_knowledge_sufficiency=knowledge_sufficiency_node,
            rag_search=rag_search_node,
            generate_answer_gemma=node_generate_answer_gemma,
            generate_answer_solar=node_generate_answer_solar,
            join_answers=node_join_answers,
            direct_answer=node_direct_answer,
            merge_answers=node_merge_answers,
            save_history=save_history_node,
        ),
    )


def create_app() -> FastAPI:
    """runtime 자원을 조립하고 route와 workflow를 등록한 FastAPI app을 만든다.
    RAG object, redis, LLM, route dependency, workflow node wiring이 부트스트랩 단계에서 한 번에 엮결된다.
    """
    @asynccontextmanager
    async def runtime_lifespan(app: FastAPI):
        """app lifespan 동안 runtime 자원을 초기화하고 종료 시 정리한다.
        warmup, payload index 확보, sparse encoder 준비, shutdown cleanup까지 서비스 생명주기 후처리를 담당한다.
        """
        try:
            await initialize_app_runtime(
                app,
                config=APP_RUNTIME_CONFIG,
                logger=logger,
                log_event=_log_event,
                validate_project_key_env_contract=lambda: validate_project_key_env_contract(log_info=logger.info),
                build_rag_objects=build_rag_objects,
                ensure_keyword_index=ensure_keyword_index,
                ensure_text_index=ensure_text_index,
                warmup_sparse_encoder=warmup_sparse_encoder,
                build_workflow=build_advanced_workflow,
            )
            yield
        finally:
            await shutdown_app_runtime(app, logger=logger, llm_cache=get_llm_cache())

    app = FastAPI(lifespan=runtime_lifespan)
    register_routes(
        app,
        RouteDeps(
            template_index_path=TEMPLATE_INDEX_PATH,
            logger=logger,
            log_event=_log_event,
            is_debug_logging_enabled=is_debug_logging_enabled,
            mask_query_for_log=mask_query_for_log,
            extract_stream_chunk_text_and_field=extract_stream_chunk_text_and_field,
            is_hit_source=is_hit_source,
            derive_stream_error_code=derive_stream_error_code,
            compute_total_ms_from_start=compute_total_ms_from_start,
            extract_contract_failure_details=extract_contract_failure_details,
            friendly_strategy_violation_message=friendly_strategy_violation_message,
            collect_metrics_snapshot=collect_metrics_snapshot,
            metrics_stream_interval_seconds=METRICS_STREAM_INTERVAL_SECONDS,
            set_log_context=set_log_context,
            rag_mapper=RagMapper,
            human_message=HumanMessage,
            strategy_violation=StrategyViolation,
        ),
    )
    return app


