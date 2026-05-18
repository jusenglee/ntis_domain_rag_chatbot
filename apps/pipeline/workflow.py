"""3계층 파이프라인의 LangGraph 워크플로우 빌더.

ADR-0018 흐름:
    load_session
      └─ judgment_agent
            ├─ DirectAnswer       → emit_direct_answer
            ├─ Clarification      → emit_clarification
            └─ SearchTask
                  └─ search_agent
                        ├─ status=error     → emit_internal_error
                        ├─ status=empty     → generate_no_result → emit_answer
                        ├─ status=multiple  → refine_judgment (1회) → search_agent
                        └─ status=single    → generate_answer → final_guard
                                                  ├─ publish        → emit_answer
                                                  ├─ clarify        → emit_clarification
                                                  └─ internal_error → emit_internal_error
      └─ save_session

기존 apps.api.workflow_builder의 14개 노드를 8개로 축소한다.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger

from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact, StreamEvent
from apps.conversation.session_memory import (
    PublishedManifestContext,
    SessionMemory,
    SubjectQueryContext,
)
from apps.conversation.view_state import DisplayItem, DisplaySnapshot
from apps.pipeline.contracts import ReferenceManifest
from apps.pipeline.log_helpers import (
    evidence_titles_preview,
    preview,
    search_task_log,
    short_id,
    subject_log,
)
from apps.pipeline.session_store import load_pipeline_session, save_pipeline_session
from apps.pipeline.contracts import (
    JudgmentDecision,
    SearchResult,
    SearchResultStatus,
    SearchTask,
)
from apps.pipeline.final_guard import FinalGuard
from apps.pipeline.judgment_agent import JudgmentAgent
from apps.pipeline.llm_generator import LLMGenerator, generate_no_result_message
from apps.pipeline.search_agent import SearchAgent
from apps.pipeline.state import PipelineState


# ============================================================================
# Dependency container (workflow factory가 주입받음)
# ============================================================================

class PipelineDeps:
    """Workflow 노드들이 공유하는 의존성 묶음.

    runtime에서 한 번만 생성해 graph 빌드 시 캡처한다.
    """

    def __init__(
        self,
        *,
        judgment_agent: JudgmentAgent,
        search_agent: SearchAgent,
        llm_generator: LLMGenerator,
        final_guard: FinalGuard,
    ) -> None:
        self.judgment_agent = judgment_agent
        self.search_agent = search_agent
        self.llm_generator = llm_generator
        self.final_guard = final_guard


# ============================================================================
# Node implementations
# ============================================================================

async def node_load_session(state: PipelineState) -> Dict[str, Any]:
    """KV에서 SessionMemory 복원. 없으면 빈 객체."""
    memory = await load_pipeline_session(
        kv_store=state.kv_store,
        conversation_id=state.conversation_id,
    )
    cc = getattr(memory, "current_context", None)
    cc_type = getattr(cc, "context_type", "empty") if cc is not None else "empty"
    prev_subject = getattr(cc, "subject_name", None) if cc is not None else None
    logger.info(
        f"[load_session] req={short_id(state.request_id)} cid={short_id(state.conversation_id)} "
        f"context_type={cc_type} prev_subject={preview(prev_subject, limit=40)!r} "
        f"q_len={len(state.question or '')} q={preview(state.question, limit=80)!r}"
    )
    return {"session_memory": memory}


def _make_node_judgment(deps: PipelineDeps):
    async def node_judgment(state: PipelineState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        decision = await deps.judgment_agent.decide(
            question=state.question,
            session_memory=state.session_memory,
            request_id=state.request_id,
            turn_id=state.turn_id,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        update: Dict[str, Any] = {
            "judgment": decision,
            "latencies": {"judgment_agent": latency / 1000.0},
        }
        rid = short_id(state.request_id)
        if decision.search_task is not None:
            update["search_task"] = decision.search_task
            logger.info(
                f"[node_judgment] req={rid} kind=search latency_ms={latency:.1f} | "
                f"{search_task_log(decision.search_task)}"
            )
        elif decision.direct_answer is not None:
            logger.info(
                f"[node_judgment] req={rid} kind=direct_answer latency_ms={latency:.1f} "
                f"text={preview(decision.direct_answer.text, limit=80)!r}"
            )
        elif decision.clarification is not None:
            logger.info(
                f"[node_judgment] req={rid} kind=clarification latency_ms={latency:.1f} "
                f"reason={decision.clarification.reason!r} "
                f"question={preview(decision.clarification.question, limit=80)!r}"
            )
        else:
            logger.warning(f"[node_judgment] req={rid} kind=UNKNOWN latency_ms={latency:.1f}")
        return update

    return node_judgment


def _make_node_search(deps: PipelineDeps):
    async def node_search(state: PipelineState) -> Dict[str, Any]:
        rid = short_id(state.request_id)
        if state.search_task is None:
            logger.warning(f"[node_search] req={rid} no search_task; skipping")
            return {}
        t0 = time.perf_counter()
        result = await deps.search_agent.execute(state.search_task)
        latency = (time.perf_counter() - t0) * 1000.0
        if result.status == "error":
            logger.warning(
                f"[node_search] req={rid} status=error err_code={result.error_code!r} "
                f"err_detail={preview(result.error_detail, limit=120)!r} latency_ms={latency:.1f}"
            )
        else:
            logger.info(
                f"[node_search] req={rid} status={result.status} total_hits={result.total_hits} "
                f"evidence_n={len(result.evidences)} latency_ms={latency:.1f} "
                f"top={evidence_titles_preview(result.evidences, max_items=3)}"
            )
        return {
            "search_result": result,
            "latencies": {"search_agent": latency / 1000.0},
        }

    return node_search


def _make_node_refine_judgment(deps: PipelineDeps):
    """SearchResult=multiple일 때 1회만 refine을 시도.

    현재 evidences의 식별자/제목 단서를 SearchTask.identifiers에 주입해 다시 search.
    refine_attempted=True로 마킹해 무한 루프 방지.
    """
    async def node_refine_judgment(state: PipelineState) -> Dict[str, Any]:
        if state.search_task is None or state.search_result is None:
            return {"refine_attempted": True}

        # multiple evidences의 식별자를 OR로 묶어 정확 후보로 다시 시도
        ev_pjt_ids = []
        ev_rst_ids = []
        for ev in state.search_result.evidences:
            pid = ev.ids.get("pjt_id")
            if pid:
                ev_pjt_ids.append(pid)
            rid = ev.ids.get("rst_id")
            if rid:
                ev_rst_ids.append(rid)

        new_identifiers = state.search_task.identifiers.model_copy(
            update={
                "pjt_id": list(dict.fromkeys([*state.search_task.identifiers.pjt_id, *ev_pjt_ids])),
                "rst_id": list(dict.fromkeys([*state.search_task.identifiers.rst_id, *ev_rst_ids])),
            }
        )
        # detail로 강제 변환해 단일 결과 추구
        refined = state.search_task.model_copy(
            update={
                "identifiers": new_identifiers,
                "action": "detail",
                "strategy": "exact_lookup",
                "limit": 1,
                "display_limit": 1,
                "judgment_reason": state.search_task.judgment_reason + "|refined",
            }
        )
        result = await deps.search_agent.execute(refined)
        logger.info(
            f"[node_refine_judgment] req={short_id(state.request_id)} "
            f"injected_pjt_ids={ev_pjt_ids[:5]} injected_rst_ids={ev_rst_ids[:5]} "
            f"refined_status={result.status} hits={result.total_hits} "
            f"evidence_n={len(result.evidences)}"
        )
        return {
            "search_task": refined,
            "search_result": result,
            "refine_attempted": True,
        }

    return node_refine_judgment


def _make_node_generate(deps: PipelineDeps):
    async def node_generate(state: PipelineState) -> Dict[str, Any]:
        if state.search_task is None or state.search_result is None:
            return {}
        emitter = state.stream_emitter

        if state.search_result.status == "empty":
            generated = await generate_no_result_message(
                task=state.search_task,
                emitter=emitter,
            )
        else:
            generated = await deps.llm_generator.generate(
                task=state.search_task,
                result=state.search_result,
                emitter=emitter,
            )
        return {"generated": generated}

    return node_generate


def _make_node_final_guard(deps: PipelineDeps):
    async def node_final_guard(state: PipelineState) -> Dict[str, Any]:
        rid = short_id(state.request_id)
        if state.search_task is None or state.search_result is None or state.generated is None:
            logger.warning(
                f"[node_final_guard] req={rid} missing inputs; "
                f"task={state.search_task is not None} result={state.search_result is not None} "
                f"generated={state.generated is not None}"
            )
            return {}
        final = deps.final_guard.review(
            task=state.search_task,
            result=state.search_result,
            generated=state.generated,
        )
        update: Dict[str, Any] = {"final_answer": final}
        artifact = _make_answer_artifact(final=final, generated=state.generated)
        if artifact is not None:
            update["answer_artifact"] = artifact
            update["final_answer_text"] = artifact.text
        cited = final.diagnostics.get("cited_ranks") if final.diagnostics else None
        manifest_n = len(final.reference_manifest.items) if final.reference_manifest else 0
        out_of_range = final.diagnostics.get("out_of_range") if final.diagnostics else None
        logger.info(
            f"[node_final_guard] req={rid} decision={final.decision} reason={final.reasoning} "
            f"cited_ranks={cited} out_of_range={out_of_range} manifest_items={manifest_n} "
            f"answer_chars={len(final.text or '') if final.text else 0} "
            f"preview={preview(final.text, limit=80)!r}"
        )
        return update

    return node_final_guard


async def node_emit_direct_answer(state: PipelineState) -> Dict[str, Any]:
    decision = state.judgment
    text = decision.direct_answer.text if decision and decision.direct_answer else ""
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="judgment_direct",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="direct_answer",
        references=[],
        source_refs=[],
    )
    logger.info(
        f"[emit_direct_answer] req={short_id(state.request_id)} chars={len(text)} "
        f"preview={preview(text, limit=80)!r}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


async def node_emit_clarification(state: PipelineState) -> Dict[str, Any]:
    final = state.final_answer
    judgment = state.judgment
    clar = None
    source = "none"
    if final is not None and final.clarification is not None:
        clar = final.clarification
        source = "final_guard"
    elif judgment is not None and judgment.clarification is not None:
        clar = judgment.clarification
        source = "judgment"

    text = clar.question if clar else "추가 정보가 필요합니다."
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="clarification",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="clarification",
        references=[],
        source_refs=[],
    )
    logger.info(
        f"[emit_clarification] req={short_id(state.request_id)} source={source} "
        f"reason={(clar.reason if clar else None)!r} chars={len(text)} "
        f"preview={preview(text, limit=80)!r}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


async def node_emit_internal_error(state: PipelineState) -> Dict[str, Any]:
    text = "내부 오류로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요."
    if state.stream_emitter is not None:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="internal_error",
            )
        )
    reason = state.final_answer.reasoning if state.final_answer else "unknown"
    error_meta = ErrorArtifact(
        error_code="pipeline_internal_error",
        reason=reason,
        retryable=False,
    )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="error",
        error=error_meta,
        references=[],
        source_refs=[],
    )
    search_err = None
    if state.search_result is not None and state.search_result.status == "error":
        search_err = state.search_result.error_code
    logger.warning(
        f"[emit_internal_error] req={short_id(state.request_id)} reason={reason!r} "
        f"search_error={search_err!r} generated={state.generated is not None}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


def _manifest_to_display_snapshot(
    *,
    manifest: ReferenceManifest,
    state: PipelineState,
) -> Optional[DisplaySnapshot]:
    """FinalGuard의 ReferenceManifest를 SessionMemory에 저장 가능한 DisplaySnapshot으로 변환.

    각 ReferenceItem이 DisplayItem 1개로 매핑되고, id_axis에 따라 적절한 식별자 필드에 채워진다.
    Follow-up turn에서 JudgmentAgent가 이 snapshot을 보고 "N번 항목" / 사업명 인용을 해석할 수 있다.
    """
    if manifest is None or not manifest.items:
        return None
    items: List[DisplayItem] = []
    for ref in manifest.items:
        entity_kind = "project" if ref.id_axis in ("pjt_id", "pjt_no") else (
            "people" if ref.id_axis == "person_no" else (
                "org" if ref.id_axis == "org_id" else "perf"
            )
        )
        item_kwargs: Dict[str, Any] = {
            "display_rank": ref.published_rank,
            "entity_kind": entity_kind,
            "doc_type": ref.tag,
            "doc_id": ref.id,
            "title_text": ref.title,
        }
        # id_axis에 따라 적합한 필드에 id 박기
        if ref.id_axis == "pjt_id":
            item_kwargs["pjt_id"] = ref.id
        elif ref.id_axis == "pjt_no":
            item_kwargs["pjt_no"] = ref.id
        elif ref.id_axis == "rst_id":
            item_kwargs["rst_id"] = ref.id
        elif ref.id_axis == "person_no":
            item_kwargs["person_no"] = ref.id
        elif ref.id_axis == "org_id":
            item_kwargs["org_id"] = ref.id
        items.append(DisplayItem(**item_kwargs))

    return DisplaySnapshot(
        view_id=f"{state.conversation_id}:turn:{state.turn_id}",
        conversation_id=state.conversation_id,
        turn_id=state.turn_id,
        request_id=state.request_id,
        context_kind=state.search_task.target if state.search_task else "project",
        requested_count=state.search_task.display_limit if state.search_task else len(items),
        visible_count=len(items),
        raw_count=manifest.total_visible,
        items=items,
    )


async def node_save_session(state: PipelineState) -> Dict[str, Any]:
    """SessionMemory에 current_context를 commit 후 KV에 저장.

    publish + manifest items가 있으면 SubjectQueryContext.result_manifest 또는
    PublishedManifestContext로 commit한다 → 다음 turn의 ordinal/title follow-up이 가능.
    """
    memory = state.session_memory
    task = state.search_task
    final = state.final_answer
    publishing = final is not None and final.decision == "publish"

    # ReferenceManifest → DisplaySnapshot 변환 (publish + manifest 있을 때)
    manifest_snapshot: Optional[DisplaySnapshot] = None
    if publishing and final.reference_manifest and final.reference_manifest.items:
        try:
            manifest_snapshot = _manifest_to_display_snapshot(
                manifest=final.reference_manifest,
                state=state,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[save_session] manifest→snapshot skipped: {exc}")
            manifest_snapshot = None

    # 1) Subject continuity: publish + subject가 있으면 SubjectQueryContext에 manifest까지 commit
    if task and task.subject is not None and publishing:
        subject = task.subject
        ids_map: Dict[str, list[str]] = {}
        if subject.person_no:
            ids_map["person_no"] = [subject.person_no]
        if subject.org_id:
            ids_map["org_id"] = [subject.org_id]
        try:
            memory.current_context = SubjectQueryContext(
                subject_name=subject.display_name,
                subject_kind=subject.kind,
                subject_ids_map=ids_map,
                identity_status=subject.identity_status,
                result_kind=task.target,
                result_manifest=manifest_snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[save_session] current_context (subject) update skipped: {exc}")
    # 2) Subject 없이 list publish → PublishedManifestContext (ordinal/title follow-up 지원)
    elif publishing and manifest_snapshot is not None:
        try:
            memory.current_context = PublishedManifestContext(
                result_kind=task.target if task else "project",
                result_manifest=manifest_snapshot,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[save_session] current_context (manifest) update skipped: {exc}")

    # 2) KV가 있을 때만 직렬화 저장 (KV 없는 환경은 메모리만 갱신)
    saved = False
    if state.kv_store is not None and state.conversation_id:
        saved = await save_pipeline_session(
            kv_store=state.kv_store,
            conversation_id=state.conversation_id,
            memory=memory,
        )

    cc = getattr(memory, "current_context", None)
    cc_type = getattr(cc, "context_type", "empty") if cc is not None else "empty"
    committed_subject = getattr(cc, "subject_name", None) if cc is not None else None
    total = state.total_ms() if state.request_started_at else 0.0
    logger.info(
        f"[save_session] req={short_id(state.request_id)} cid={short_id(state.conversation_id)} "
        f"context_type={cc_type} subject={preview(committed_subject, limit=40)!r} "
        f"kv_saved={saved} total_ms={total:.1f}"
    )
    return {"session_memory": memory}


# ============================================================================
# Routing helpers
# ============================================================================

def route_after_judgment(state: PipelineState) -> str:
    decision = state.judgment
    if decision is None:
        return "emit_internal_error"
    if decision.direct_answer is not None:
        return "emit_direct_answer"
    if decision.clarification is not None:
        return "emit_clarification"
    return "search_agent"


def route_after_search(state: PipelineState) -> str:
    if state.search_result is None:
        return "emit_internal_error"
    status: SearchResultStatus = state.search_result.status
    if status == "error":
        return "emit_internal_error"
    if status == "multiple" and not state.refine_attempted:
        return "refine_judgment"
    # multiple이면서 이미 refine을 시도한 경우 final_guard가 clarify로 닫는다
    # single / empty / (refine 이후) multiple 모두 generate로 보내고 final_guard가 결정
    return "generate"


def route_after_refine(state: PipelineState) -> str:
    if state.search_result is None:
        return "emit_internal_error"
    if state.search_result.status == "error":
        return "emit_internal_error"
    return "generate"


def route_after_final_guard(state: PipelineState) -> str:
    final = state.final_answer
    if final is None:
        return "emit_internal_error"
    if final.decision == "publish":
        # final_guard에서 이미 answer_artifact를 만들어둠. save_session으로.
        return "save_session"
    if final.decision == "clarify":
        return "emit_clarification"
    return "emit_internal_error"


# ============================================================================
# Answer artifact builder
# ============================================================================

def _make_answer_artifact(
    *,
    final: Any,
    generated: Any,
) -> Optional[AnswerArtifact]:
    """FinalAnswer + GeneratedAnswer → AnswerArtifact (routes.py가 읽는 표준)."""
    if final.decision != "publish":
        return None
    refs: list[dict[str, Any]] = []
    source_refs: list[Any] = []
    if final.reference_manifest:
        for item in final.reference_manifest.items:
            refs.append(
                {
                    "rank": item.published_rank,
                    "id": item.id,
                    "tag": item.tag,
                    "title": item.title,
                }
            )
            source_refs.append(item)
    return AnswerArtifact(
        text=final.text or "",
        answer_kind="llm_streamed",
        stream_metrics=dict(generated.stream_metrics or {}),
        references=refs,
        source_refs=source_refs,
        meta={"final_guard_reasoning": final.reasoning},
    )


# ============================================================================
# Workflow builder (entry point)
# ============================================================================

def build_pipeline_graph(deps: PipelineDeps) -> Any:
    """LangGraph StateGraph를 구성해 컴파일된 graph를 돌려준다.

    Args:
        deps: workflow가 노드 내부에서 호출하는 컴포넌트 묶음.

    Returns:
        compiled LangGraph (ainvoke / astream 사용 가능).
    """
    from langgraph.graph import END, StateGraph

    graph = StateGraph(PipelineState)

    # 노드 등록
    graph.add_node("load_session", node_load_session)
    graph.add_node("judgment_agent", _make_node_judgment(deps))
    graph.add_node("search_agent", _make_node_search(deps))
    graph.add_node("refine_judgment", _make_node_refine_judgment(deps))
    graph.add_node("generate", _make_node_generate(deps))
    graph.add_node("final_guard", _make_node_final_guard(deps))
    graph.add_node("emit_direct_answer", node_emit_direct_answer)
    graph.add_node("emit_clarification", node_emit_clarification)
    graph.add_node("emit_internal_error", node_emit_internal_error)
    graph.add_node("save_session", node_save_session)

    # 엣지
    graph.set_entry_point("load_session")
    graph.add_edge("load_session", "judgment_agent")

    graph.add_conditional_edges(
        "judgment_agent",
        route_after_judgment,
        {
            "emit_direct_answer": "emit_direct_answer",
            "emit_clarification": "emit_clarification",
            "search_agent": "search_agent",
            "emit_internal_error": "emit_internal_error",
        },
    )

    graph.add_conditional_edges(
        "search_agent",
        route_after_search,
        {
            "generate": "generate",
            "refine_judgment": "refine_judgment",
            "emit_internal_error": "emit_internal_error",
        },
    )

    graph.add_conditional_edges(
        "refine_judgment",
        route_after_refine,
        {
            "generate": "generate",
            "emit_internal_error": "emit_internal_error",
        },
    )

    graph.add_edge("generate", "final_guard")

    graph.add_conditional_edges(
        "final_guard",
        route_after_final_guard,
        {
            "save_session": "save_session",
            "emit_clarification": "emit_clarification",
            "emit_internal_error": "emit_internal_error",
        },
    )

    # 종료 경로
    graph.add_edge("emit_direct_answer", "save_session")
    graph.add_edge("emit_clarification", "save_session")
    graph.add_edge("emit_internal_error", "save_session")
    graph.add_edge("save_session", END)

    return graph.compile()
