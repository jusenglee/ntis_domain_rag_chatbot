"""Phase 10: 7-agent 재설계의 LangGraph 워크플로우 빌더.

흐름:
    load_session
      → dialogue_agent
            ├─ direct_answer    → emit_direct_answer    → save_session
            ├─ clarification    → emit_clarification    → save_session
            └─ search/refine/detail
                → entity_resolver
                    ├─ clarification_needed → emit_clarification → save_session
                    └─ otherwise
                        → search_planner
                              ├─ no plan       → emit_internal_error
                              └─ has plan
                                  → retrieval_agent
                                        ├─ status=error → emit_internal_error
                                        └─ otherwise
                                            → evidence_curator
                                                → answer_agent
                                                      → critic_agent
                                                            ├─ publish        → save_session
                                                            ├─ repair_answer  → answer_agent (repair_attempted=True)
                                                            │                    → critic_agent (1회 한도, 위반 시 clarify/error)
                                                            ├─ clarify        → emit_clarification → save_session
                                                            └─ internal_error → emit_internal_error  → save_session

SessionState 슬롯 (3 평행 슬롯) — load 시 SessionMemory → SessionState,
save 시 SessionState → SessionMemory 변환.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from apps.api.streaming.contracts import AnswerArtifact, ErrorArtifact, StreamEvent
from apps.api.streaming.model_keys import (
    PRIMARY_FRONTEND_KEY,
    SECONDARY_FRONTEND_KEY,
)
from apps.conversation.session_memory import (
    PublishedManifestContext,
    SessionMemory,
    SubjectQueryContext,
)
from apps.conversation.view_state import DisplayItem, DisplaySnapshot, FocusEntity
from apps.pipeline.agent_state import AgentPipelineState
from apps.pipeline.agents import (
    AnswerAgent,
    CriticAgent,
    DialogueAgent,
    EntityResolverAgent,
    EvidenceCuratorAgent,
    FocusedDetailSlot,
    ManifestSlot,
    RetrievalAgent,
    SearchPlannerAgent,
    SessionState,
    SessionStateAdapter,
    SubjectSlot,
)
from apps.pipeline.contracts import CanonicalEvidence, ReferenceManifest, SearchResult
from apps.pipeline.log_helpers import preview, short_id
from apps.pipeline.session_store import load_pipeline_session, save_pipeline_session


# ============================================================================
# Dependency container
# ============================================================================

class AgentPipelineDeps:
    """Agentic 파이프라인 노드가 사용하는 컴포넌트 묶음.

    ADR-0020: 정적 그래프 제거 후 agentic 전용 구조로 단순화.
    search_planner / retrieval_agent / evidence_curator는 정적 그래프 전용이었으나
    runtime 호환성을 위해 옵셔널로 유지 (None 허용, 실제 사용 안 함).
    """

    def __init__(
        self,
        *,
        dialogue_agent: DialogueAgent,
        entity_resolver: EntityResolverAgent,
        answer_agent: AnswerAgent,
        critic_agent: CriticAgent,
        answer_agent_secondary: Optional[AnswerAgent] = None,
        planner_agent: Any = None,
        tool_executor: Any = None,
        adequacy_gate: Any = None,
        # 하위 호환 — 정적 그래프 전용, 실제 사용 안 함
        search_planner: Any = None,
        retrieval_agent: Any = None,
        evidence_curator: Any = None,
    ) -> None:
        self.dialogue_agent = dialogue_agent
        self.entity_resolver = entity_resolver
        self.answer_agent = answer_agent
        self.critic_agent = critic_agent
        self.answer_agent_secondary = answer_agent_secondary
        self.planner_agent = planner_agent
        self.tool_executor = tool_executor
        self.adequacy_gate = adequacy_gate
        # 미사용 — 정적 그래프 삭제 후 제거 예정
        self.search_planner = search_planner
        self.retrieval_agent = retrieval_agent
        self.evidence_curator = evidence_curator


# ============================================================================
# Node implementations
# ============================================================================

async def node_load_session(state: AgentPipelineState) -> Dict[str, Any]:
    """KV에서 SessionMemory 복원 후 SessionState로 어댑팅.

    외부에서 state.session_state를 명시적으로 주입한 경우(테스트·재실행 시나리오)는
    KV 로드를 건너뛰고 그대로 사용한다. 운영 라우트(`routes.py`)는 session_state를
    빈 채로 전달하므로 KV 로드가 그대로 작동한다.
    """
    rid = short_id(state.request_id)
    if state.session_state and (
        state.session_state.has_subject()
        or state.session_state.has_manifest()
        or state.session_state.has_focused_detail()
    ):
        logger.info(
            f"[load_session] injected_state_used(이미 주입된 SessionState 사용) "
            f"req={rid} cid={short_id(state.conversation_id)} "
            f"has_subject={state.session_state.has_subject()}(주제 슬롯) "
            f"has_manifest={state.session_state.has_manifest()}(매니페스트 슬롯) "
            f"has_focused_detail={state.session_state.has_focused_detail()}(상세 슬롯)"
        )
        return {"session_state": state.session_state}

    memory = await load_pipeline_session(
        kv_store=state.kv_store,
        conversation_id=state.conversation_id,
    )
    session_state = SessionStateAdapter.from_session_memory(
        memory, conversation_id=state.conversation_id
    )
    logger.info(
        f"[load_session] kv_restored(KV에서 SessionState 복원) "
        f"req={rid} cid={short_id(state.conversation_id)} "
        f"has_subject={session_state.has_subject()}(주제 슬롯) "
        f"has_manifest={session_state.has_manifest()}(매니페스트 슬롯) "
        f"has_focused_detail={session_state.has_focused_detail()}(상세 슬롯) "
        f"q_len={len(state.question or '')}(질문 길이) "
        f"q={preview(state.question, limit=80)!r}(질문 미리보기)"
    )
    return {"session_memory": memory, "session_state": session_state}


def _make_node_dialogue(deps: AgentPipelineDeps):
    async def node_dialogue(state: AgentPipelineState) -> Dict[str, Any]:
        t0 = time.perf_counter()
        intent = await deps.dialogue_agent.decide(
            question=state.question,
            session=state.session_state,
            request_id=state.request_id,
            turn_id=state.turn_id,
            conversation_id=state.conversation_id,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        # 2026-05-26: 디버깅 가시성 — LLM이 채운 모든 핵심 신호를 한 줄에 노출.
        # 회귀 진단 시 어느 필드가 과추출됐는지(예: subject_name="LLM") 즉시 확인 가능.
        signals: List[str] = []
        if intent.subject_name:
            signals.append(f"subject={intent.subject_kind}:{intent.subject_name!r}")
        if intent.subject_affiliation_hint:
            signals.append(f"aff={intent.subject_affiliation_hint!r}")
        if intent.identifier_hints:
            signals.append(f"id_hints={ {k: len(v) for k, v in intent.identifier_hints.items() if v} }")
        if intent.year_from or intent.year_to:
            signals.append(f"year={intent.year_from}~{intent.year_to}")
        if intent.perf_type_hint:
            signals.append(f"perf_type={intent.perf_type_hint}")
        if intent.coparticipants:
            signals.append(f"coparticipants={intent.coparticipants}")
        if intent.exclude_org_name:
            signals.append(f"exclude_org={intent.exclude_org_name}")
        if intent.exclude_perf_type:
            signals.append(f"exclude_perf={intent.exclude_perf_type}")
        if intent.exclude_person_name:
            signals.append(f"exclude_person={intent.exclude_person_name}")
        if intent.sort_by and intent.sort_by != "relevance":
            signals.append(f"sort={intent.sort_by}")
        if intent.length_hint and intent.length_hint != "default":
            signals.append(f"length={intent.length_hint}")
        if intent.aggregate_hint:
            signals.append(f"agg={intent.aggregate_hint}")
        signals_text = " ".join(signals) if signals else "-"
        logger.info(
            f"[dialogue] intent_classified(의도 분류 완료) "
            f"req={short_id(state.request_id)} "
            f"kind={intent.kind}(의도 종류) "
            f"target_hint={intent.target_hint}(타깃 도메인) "
            f"action_hint={intent.action_hint}(액션 힌트) "
            f"manifest_rank={intent.manifest_rank}(직전 manifest 인용) "
            f"conf={intent.confidence:.2f}(신뢰도) "
            f"signals=[{signals_text}](추출된 신호) "
            f"q={preview(intent.query, limit=60)!r}(질의) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        return {
            "dialogue_intent": intent,
            "latencies": {"dialogue_agent": latency / 1000.0},
        }

    return node_dialogue


def _make_node_entity_resolver(deps: AgentPipelineDeps):
    async def node_entity_resolver(state: AgentPipelineState) -> Dict[str, Any]:
        if state.dialogue_intent is None:
            # 방어 — dialogue_agent가 intent를 세팅하지 않은 비정상 상태. entity_resolution이
            # None인 채 라우터에 도달해 emit_internal_error로 분기되므로 원인 추적용 로그 필수.
            logger.error(
                f"[entity_resolver] dialogue_intent_missing(판단 결과 없음 — 배선 오류) "
                f"req={short_id(state.request_id)} cid={short_id(state.conversation_id)} "
                f"→ entity_resolution=None → 라우터가 emit_internal_error로 분기"
            )
            return {}
        t0 = time.perf_counter()
        resolution = deps.entity_resolver.resolve(
            intent=state.dialogue_intent,
            session=state.session_state,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        # 2026-05-26: subject anchor·identifiers·filters를 디버깅 가시성 위해 노출.
        subj = resolution.subject
        subj_text = (
            f"{subj.kind}:{subj.display_name!r}(person_no={subj.person_no},org_id={subj.org_id},status={subj.identity_status})"
            if subj is not None else "none"
        )
        ids = resolution.identifiers
        id_counts: List[str] = []
        if ids:
            for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
                values = getattr(ids, axis, None) or []
                if values:
                    id_counts.append(f"{axis}={len(values)}")
        id_text = ",".join(id_counts) if id_counts else "empty"
        flt = resolution.filters
        flt_parts: List[str] = []
        if flt:
            if flt.year_from or flt.year_to:
                flt_parts.append(f"year={flt.year_from}~{flt.year_to}")
            if flt.perf_type:
                flt_parts.append(f"perf_type={flt.perf_type}")
            if flt.exclude_org_name:
                flt_parts.append(f"excl_org={flt.exclude_org_name}")
            if flt.exclude_perf_type:
                flt_parts.append(f"excl_perf={flt.exclude_perf_type}")
            if flt.exclude_person_name:
                flt_parts.append(f"excl_person={flt.exclude_person_name}")
        flt_text = " ".join(flt_parts) if flt_parts else "-"
        logger.info(
            f"[entity_resolver] resolution_ready(엔티티 해소 완료) "
            f"req={short_id(state.request_id)} "
            f"source={resolution.resolution_source}(해소 출처) "
            f"forced_target={resolution.forced_target}(강제 타깃) "
            f"manifest_rank={resolution.manifest_rank}(인용 번호) "
            f"clarify={resolution.clarification_needed}(되묻기 필요) "
            f"subject={subj_text}(주제) "
            f"ids=[{id_text}](식별자) "
            f"filters=[{flt_text}](필터) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        return {
            "entity_resolution": resolution,
            "latencies": {"entity_resolver": latency / 1000.0},
        }

    return node_entity_resolver


def _make_node_search_planner(deps: AgentPipelineDeps):
    async def node_search_planner(state: AgentPipelineState) -> Dict[str, Any]:
        if state.dialogue_intent is None or state.entity_resolution is None:
            return {}
        t0 = time.perf_counter()
        plan = deps.search_planner.plan(
            intent=state.dialogue_intent,
            resolution=state.entity_resolution,
            request_id=state.request_id,
            turn_id=state.turn_id,
            session=state.session_state,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        if plan is None:
            logger.info(
                f"[search_planner] no_plan(검색 계획 없음 — 즉답 분기 또는 검색 불필요) "
                f"req={short_id(state.request_id)} "
                f"intent_kind={state.dialogue_intent.kind}(의도 종류) "
                f"latency_ms={latency:.1f}(소요시간)"
            )
        else:
            logger.info(
                f"[search_planner] plan_ready(검색 계획 생성 완료) "
                f"req={short_id(state.request_id)} "
                f"tasks={len(plan.tasks)}(작업 수) "
                f"merge={plan.merge_strategy}(병합 전략) "
                f"reason={plan.plan_reason}(계획 사유) "
                f"latency_ms={latency:.1f}(소요시간)"
            )
            # 2026-05-26: 각 task의 action/target/strategy/collections/aggregate를 1줄씩 노출.
            rid = short_id(state.request_id)
            for i, task in enumerate(plan.tasks):
                task_subj = task.subject
                subj_text = (
                    f"{task_subj.kind}:{task_subj.display_name!r}"
                    if task_subj is not None else "none"
                )
                logger.info(
                    f"[search_planner] task_detail "
                    f"req={rid} task[{i}] "
                    f"action={task.action}(액션) target={task.target}(타깃) "
                    f"strategy={task.strategy}(전략) "
                    f"collections={list(task.collections)}(컬렉션) "
                    f"aggregate_by={task.aggregate_by}(집계 축) "
                    f"subject={subj_text}(주제 anchor) "
                    f"sort_by={task.sort_by}(정렬) "
                    f"q={preview(task.retrieval_query, limit=60)!r}(질의)"
                )
        return {
            "search_plan": plan,
            "latencies": {"search_planner": latency / 1000.0},
        }

    return node_search_planner


def _make_node_retrieval(deps: AgentPipelineDeps):
    async def node_retrieval(state: AgentPipelineState) -> Dict[str, Any]:
        if state.search_plan is None:
            return {}
        # P0-B 캐싱: 단일 task + 단일 식별자 + focused_detail 캐시 hit → SearchAgent skip.
        cached = _try_cached_detail(plan=state.search_plan, session_state=state.session_state)
        if cached is not None:
            logger.info(
                f"[retrieval] cache_hit(focused_detail 캐시 적중 — Qdrant 호출 skip) "
                f"req={short_id(state.request_id)} "
                f"identity={cached.evidences[0].identity if cached.evidences else 'n/a'}(증거 식별자) "
                f"skipped_qdrant_call=True(Qdrant 호출 생략)"
            )
            return {
                "search_result": cached,
                "latencies": {"retrieval_agent": 0.0},
            }
        t0 = time.perf_counter()
        result = await deps.retrieval_agent.execute(state.search_plan)
        latency = (time.perf_counter() - t0) * 1000.0
        # ask_similar: anchor 자신은 결과에서 제외 (focused_detail.cached_ids 일치 evidence 제거).
        if (
            state.search_plan is not None
            and state.search_plan.plan_reason == "ask_similar"
            and state.session_state is not None
            and state.session_state.has_focused_detail()
        ):
            result = _exclude_anchor_self(
                result=result, anchor_ids=state.session_state.focused_detail.cached_ids or {}
            )
        logger.info(
            f"[retrieval] result_ready(검색 결과 완료) "
            f"req={short_id(state.request_id)} "
            f"status={result.status}(결과 상태: single/multiple/empty/error) "
            f"evidence_n={len(result.evidences)}(증거 건수) "
            f"total_hits={result.total_hits}(전체 매칭 수) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        # 2026-05-26: 0건 진단 — filter가 너무 좁거나 anchor가 NTIS에 없을 때 무엇이 잘렸는지.
        if result.status == "empty" or len(result.evidences) == 0:
            tasks = list(state.search_plan.tasks) if state.search_plan else []
            diag_lines: List[str] = []
            for i, task in enumerate(tasks):
                ts = task.subject
                subj = (
                    f"{ts.kind}:{ts.display_name!r}(person_no={ts.person_no},org_id={ts.org_id})"
                    if ts is not None else "none"
                )
                flt = task.filters
                flt_parts: List[str] = []
                if flt:
                    if flt.year_from or flt.year_to:
                        flt_parts.append(f"year={flt.year_from}~{flt.year_to}")
                    if flt.perf_type:
                        flt_parts.append(f"perf_type={flt.perf_type}")
                    if flt.exclude_org_name:
                        flt_parts.append(f"excl_org={flt.exclude_org_name}")
                    if flt.exclude_perf_type:
                        flt_parts.append(f"excl_perf={flt.exclude_perf_type}")
                ids = task.identifiers
                id_counts = []
                if ids:
                    for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
                        values = getattr(ids, axis, None) or []
                        if values:
                            id_counts.append(f"{axis}={len(values)}")
                diag_lines.append(
                    f"  task[{i}] strategy={task.strategy} collections={list(task.collections)} "
                    f"subject={subj} filters=[{' '.join(flt_parts) or '-'}] "
                    f"ids=[{','.join(id_counts) or 'empty'}] "
                    f"q={preview(task.retrieval_query, limit=60)!r}"
                )
            logger.warning(
                f"[retrieval][EMPTY_DIAG] req={short_id(state.request_id)} "
                f"plan_reason={state.search_plan.plan_reason if state.search_plan else 'none'}\n"
                + "\n".join(diag_lines)
            )
        return {
            "search_result": result,
            "latencies": {"retrieval_agent": latency / 1000.0},
        }

    return node_retrieval


def _make_node_evidence_curator(deps: AgentPipelineDeps):
    async def node_evidence_curator(state: AgentPipelineState) -> Dict[str, Any]:
        if state.search_result is None or state.dialogue_intent is None:
            return {}
        t0 = time.perf_counter()
        bundle = deps.evidence_curator.curate(
            result=state.search_result,
            intent=state.dialogue_intent,
            resolution=state.entity_resolution,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        logger.info(
            f"[evidence_curator] curated(증거 묶음 완료) "
            f"req={short_id(state.request_id)} "
            f"view={bundle.view}(노출 뷰) items={len(bundle.items)}(증거 건수) "
            f"groups={len(bundle.groups)}(그룹 수) latency_ms={latency:.1f}(소요시간)"
        )
        return {
            "evidence_bundle": bundle,
            "latencies": {"evidence_curator": latency / 1000.0},
        }

    return node_evidence_curator


def _make_node_answer(deps: AgentPipelineDeps):
    async def node_answer(state: AgentPipelineState) -> Dict[str, Any]:
        """메인(Solar) 답변 + 선택적 보조(Gemma) 비교 답변을 생성한다.

        - 메인 답변(`deps.answer_agent`, Solar)은 패널 A 레인("solar")으로 스트리밍되고
          `state.answer_draft`로 저장돼 CriticAgent 검증·repair·references·세션 발행의 기준이 된다.
        - 보조 답변(`deps.answer_agent_secondary`, Gemma)이 주입돼 있고 결과가 비어있지 않으며
          repair 패스가 아니면, 동일 EvidenceBundle/Intent로 패널 B 레인("gemma")에 동시 스트리밍하고
          `state.secondary_answer_draft`로 보관한다 (검증 없이 원문 비교용).
        - repair 패스(`state.repair_attempted`)에서는 메인만 재생성한다 — 보조 패널 B는 1회차
          답변을 유지하고, 메인 패널 A에만 재작성 결과가 이어진다.
        - 보조 생성 실패는 메인 답변을 막지 않는다 (격리).
        """
        if state.evidence_bundle is None or state.dialogue_intent is None:
            return {}
        bundle = state.evidence_bundle
        intent = state.dialogue_intent
        emitter = state.stream_emitter

        repair_hint: Optional[str] = None
        if state.repair_attempted and state.guard_decision is not None:
            repair_hint = state.guard_decision.repair_hint

        # 이중 출력 활성 여부: 보조 에이전트 주입 + 비어있지 않은 결과(empty면 결정적 no_result 단일 메시지).
        dual_feature = deps.answer_agent_secondary is not None and bundle.view != "empty"
        run_secondary = dual_feature and not state.repair_attempted
        # dual이면 메인은 패널 A 레인("solar")로만 라우팅. 아니면(롤백/empty) 양 패널 fan-out 키.
        primary_stream_key = PRIMARY_FRONTEND_KEY if dual_feature else "solar_vllm_0"

        async def _gen_primary() -> Any:
            return await deps.answer_agent.generate(
                bundle=bundle,
                intent=intent,
                request_id=state.request_id,
                emitter=emitter,
                model_key=primary_stream_key,
                repair_hint=repair_hint,
            )

        async def _gen_secondary() -> Any:
            # 보조(Gemma) 실패는 무시 — 메인 답변/그래프를 막지 않는다.
            try:
                return await deps.answer_agent_secondary.generate(
                    bundle=bundle,
                    intent=intent,
                    request_id=state.request_id,
                    emitter=emitter,
                    model_key=SECONDARY_FRONTEND_KEY,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"[answer] secondary_failed(보조 Gemma 답변 생성 실패 — 무시) "
                    f"req={short_id(state.request_id)} error={exc}"
                )
                return None

        t0 = time.perf_counter()
        if run_secondary:
            draft, secondary_draft = await asyncio.gather(_gen_primary(), _gen_secondary())
        else:
            draft = await _gen_primary()
            secondary_draft = None
        latency = (time.perf_counter() - t0) * 1000.0

        sec_chars = len(secondary_draft.text) if secondary_draft is not None else None
        logger.info(
            f"[answer] draft_ready(답변 초안 완료) "
            f"req={short_id(state.request_id)} "
            f"template={draft.template}(템플릿) "
            f"chars={len(draft.text)}(메인 길이) "
            f"citations={len(draft.citations)}(인용 개수) "
            f"truncated={draft.truncated}(잘림 여부) "
            f"repair_hint={bool(repair_hint)}(재작성 힌트 적용) "
            f"dual={run_secondary}(이중 출력) "
            f"secondary_chars={sec_chars}(비교 Gemma 길이) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        update: Dict[str, Any] = {
            "answer_draft": draft,
            "latencies": {"answer_agent": latency / 1000.0},
        }
        # repair 패스에서는 secondary_draft를 덮어쓰지 않는다 (1회차 보조 답변 유지).
        if secondary_draft is not None:
            update["secondary_answer_draft"] = secondary_draft
        return update

    return node_answer


def _make_node_critic(deps: AgentPipelineDeps):
    async def node_critic(state: AgentPipelineState) -> Dict[str, Any]:
        if state.answer_draft is None or state.evidence_bundle is None or state.dialogue_intent is None:
            return {}
        t0 = time.perf_counter()
        decision = deps.critic_agent.critique(
            draft=state.answer_draft,
            bundle=state.evidence_bundle,
            intent=state.dialogue_intent,
            repair_attempted=state.repair_attempted,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        update: Dict[str, Any] = {
            "guard_decision": decision,
            "latencies": {"critic_agent": latency / 1000.0},
        }
        # repair_answer로 분기되면 다음 라운드에서 repair_attempted=True
        if decision.decision == "repair_answer":
            update["repair_attempted"] = True
        # publish 시 artifact 즉시 생성
        if decision.decision == "publish":
            artifact = _make_publish_artifact(
                decision=decision,
                draft=state.answer_draft,
                bundle=state.evidence_bundle,
            )
            update["answer_artifact"] = artifact
            update["final_answer_text"] = artifact.text
        decision_kr = {
            "publish": "발행 가능",
            "repair_answer": "재작성 요청",
            "clarify": "되묻기",
            "internal_error": "내부 오류",
        }.get(decision.decision, decision.decision)
        logger.info(
            f"[critic] decision_ready(검증 결정 완료) "
            f"req={short_id(state.request_id)} "
            f"decision={decision.decision}({decision_kr}) "
            f"reasoning={decision.reasoning!r}(판정 사유) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        return update

    return node_critic


# ============================================================================
# Terminal emit nodes
# ============================================================================

async def node_emit_direct_answer(state: AgentPipelineState) -> Dict[str, Any]:
    intent = state.dialogue_intent
    text = intent.direct_text if intent and intent.direct_text else ""
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="dialogue_direct",
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


async def node_emit_clarification(state: AgentPipelineState) -> Dict[str, Any]:
    """clarify 결정: DialogueAgent / EntityResolver / CriticAgent 중 어느 단계가
    클arification을 발행했는지에 따라 텍스트를 결정한다.
    """
    decision = state.guard_decision
    intent = state.dialogue_intent
    resolution = state.entity_resolution

    text: Optional[str] = None
    source = "none"
    if decision is not None and decision.decision == "clarify" and decision.clarification_question:
        text = decision.clarification_question
        source = "critic"
    elif resolution is not None and resolution.clarification_needed and resolution.clarification_reason:
        text = resolution.clarification_reason
        source = "entity_resolver"
    elif intent is not None and intent.kind == "clarification" and intent.clarification_question:
        text = intent.clarification_question
        source = "dialogue"

    if not text:
        text = "추가 정보가 필요합니다. 질문을 조금 더 구체적으로 알려주시겠어요?"

    if state.stream_emitter is not None:
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
        f"chars={len(text)} preview={preview(text, limit=80)!r}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


async def node_emit_children_list(state: AgentPipelineState) -> Dict[str, Any]:
    """ask_children 즉답 노드 — 검색 없이 focused_detail의 child_entities로 답변.

    조건:
        - session_state.focused_detail이 있음
        - focused_detail에 child_entities (참여연구자/참여기관/연계성과) 캐시됨

    답변 형식: 글머리표 + 이름·역할·소속. raw person_no/org_id는 노출하지 않음 (P1-2 정직성 유지).
    """
    rid = short_id(state.request_id)
    text = _build_children_list_text(
        session_state=state.session_state, intent=state.dialogue_intent
    )
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="children_list",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="direct_answer",
        references=[],
        source_refs=[],
        meta={"kind": "ask_children_short_circuit"},
    )
    logger.info(
        f"[emit_children_list] req={rid} chars={len(text)} preview={preview(text, limit=80)!r}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


async def node_emit_meta_answer(state: AgentPipelineState) -> Dict[str, Any]:
    """ask_meta 즉답 노드 — 검색 없이 manifest/focused_detail의 tag·axis로 분류 답변 생성.

    예: "이게 과제야 성과야?" → manifest item.tag(IRD_NAI_PJT_INFO vs IRD_NAI_RI_*)와
    id_axis(pjt_id vs rst_id), rst_id 접두어(CNL/PTR/SNW/REP/EQU/...)로 결정적 답변.

    LLM 호출 없음. 출력은 AnswerArtifact(answer_kind="direct_answer").
    """
    intent = state.dialogue_intent
    resolution = state.entity_resolution
    text = _build_meta_answer_text(intent=intent, resolution=resolution, session_state=state.session_state)
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="meta_classify",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="direct_answer",
        references=[],
        source_refs=[],
        meta={"kind": "ask_meta_short_circuit"},
    )
    logger.info(
        f"[emit_meta_answer] req={short_id(state.request_id)} "
        f"manifest_rank={resolution.manifest_rank if resolution else None} "
        f"target={resolution.manifest_resolved_target if resolution else None} "
        f"chars={len(text)} preview={preview(text, limit=80)!r}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


async def node_emit_internal_error(state: AgentPipelineState) -> Dict[str, Any]:
    reason = "unknown"
    error_code = "pipeline_internal_error"
    if state.guard_decision is not None and state.guard_decision.decision == "internal_error":
        reason = state.guard_decision.error_reason or state.guard_decision.reasoning or reason
        error_code = state.guard_decision.error_code or error_code
    elif state.search_result is not None and state.search_result.status == "error":
        reason = f"search_error:{state.search_result.error_code}"
        error_code = state.search_result.error_code or error_code
    # error_code별 사용자 친화 메시지 + retryable 결정
    text, retryable = _user_facing_error_message(error_code=error_code, reason=reason)
    if state.stream_emitter is not None:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="internal_error",
            )
        )
    error_meta = ErrorArtifact(error_code=error_code, reason=reason, retryable=retryable)
    artifact = AnswerArtifact(
        text=text,
        answer_kind="error",
        error=error_meta,
        references=[],
        source_refs=[],
    )
    logger.warning(
        f"[emit_internal_error] req={short_id(state.request_id)} reason={reason!r} "
        f"error_code={error_code!r} retryable={retryable}"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


def _user_facing_error_message(*, error_code: str, reason: str) -> tuple[str, bool]:
    """error_code별 사용자 친화 메시지 + 재시도 가능 여부.

    Returns:
        (text, retryable)
    """
    code = (error_code or "").lower()
    if "timeout" in code or "deadline" in code:
        return (
            "검색이 너무 오래 걸려 응답을 받지 못했습니다. "
            "조건을 좀 더 좁혀(예: 연도 범위 축소, 단일 키워드) 다시 시도해 주세요.",
            True,
        )
    if "qdrant" in code or "search_dispatch_failed" in code or "all_tasks_failed" in code:
        return (
            "검색 시스템이 일시적으로 응답하지 않습니다. 잠시 후 다시 시도해 주세요. "
            "문제가 지속되면 운영팀에 알려주세요.",
            True,
        )
    if "empty_generation" in code:
        return (
            "답변 모델이 응답을 생성하지 못했습니다. 질문을 조금 더 구체적으로 표현해 "
            "다시 시도해 주세요.",
            True,
        )
    if "llm" in code or "model" in code:
        return (
            "답변 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            True,
        )
    # 기본
    return (
        "내부 오류로 답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.",
        False,
    )


# ============================================================================
# Save session
# ============================================================================

async def node_save_session(state: AgentPipelineState) -> Dict[str, Any]:
    """SessionState를 갱신 후 SessionMemory로 직렬화해 KV에 저장.

    publish 시 view에 따라 슬롯 갱신 전략을 분기한다 (P0-2 회귀 대응):

    ┌────────────────────────┬──────────────┬───────────────┬───────────────┐
    │ view                   │ SubjectSlot  │ ManifestSlot  │ FocusedDetail │
    ├────────────────────────┼──────────────┼───────────────┼───────────────┤
    │ single_detail          │ 갱신*        │ 유지 (덮어쓰기 X) │ 갱신 (anchor) │
    │ subject_activity       │ 갱신*        │ 갱신          │ 클리어        │
    │ list_compact           │ 갱신*        │ 갱신          │ 클리어        │
    │ stats_summary          │ 갱신*        │ 갱신          │ 클리어        │
    │ comparison_table       │ 갱신*        │ 갱신          │ 클리어        │
    │ empty                  │ 유지         │ 유지          │ 유지          │
    └────────────────────────┴──────────────┴───────────────┴───────────────┘
    *SubjectSlot은 entity_resolution.subject가 있을 때만 갱신.

    핵심: detail 응답이 직전 turn의 list manifest를 단일 항목으로 덮어쓰면 사용자의
    "다른 번호" follow-up이 깨진다. 대신 focused_detail에 anchor만 저장한다.
    """
    state_obj: SessionState = state.session_state
    rid = short_id(state.request_id)
    publishing = state.guard_decision is not None and state.guard_decision.decision == "publish"
    view = state.evidence_bundle.view if state.evidence_bundle is not None else None

    if publishing:
        # 1) SubjectSlot 갱신 (entity_resolution.subject가 있을 때, view 무관)
        if state.entity_resolution is not None and state.entity_resolution.subject is not None:
            subj = state.entity_resolution.subject
            ids_map: Dict[str, List[str]] = {}
            if subj.person_no:
                ids_map["person_no"] = [subj.person_no]
            if subj.org_id:
                ids_map["org_id"] = [subj.org_id]
            state_obj = state_obj.with_subject(
                SubjectSlot(
                    subject_kind=subj.kind,
                    subject_name=subj.display_name,
                    subject_ids_map=ids_map,
                    identity_status=subj.identity_status,
                    affiliation_org_name=subj.affiliation_org_name,
                    last_turn_id=state.turn_id,
                )
            )

        manifest = state.guard_decision.reference_manifest

        # 2) view별 ManifestSlot / FocusedDetailSlot 갱신 분기
        if view == "single_detail" and manifest is not None and manifest.items:
            # detail은 manifest를 덮어쓰지 않고 focused_detail에 anchor + evidence 캐시 저장.
            anchor = _build_focus_entity_from_manifest(manifest=manifest, state=state)
            if anchor is not None:
                # evidence 본문 캐싱 (P0-B + ask_children 즉답).
                evidence = (
                    state.evidence_bundle.items[0]
                    if state.evidence_bundle and state.evidence_bundle.items
                    else None
                )
                slot_kwargs: Dict[str, Any] = {
                    "anchor": anchor,
                    "focused_turn_id": state.turn_id,
                }
                if evidence is not None:
                    slot_kwargs.update(
                        {
                            "title": evidence.title or None,
                            "summary": evidence.summary or None,
                            "facts": dict(evidence.facts or {}),
                            "roles": {k: list(v) for k, v in (evidence.roles or {}).items()},
                            "child_entities": [
                                dict(e) for e in (evidence.child_entities or []) if isinstance(e, dict)
                            ],
                            "cached_ids": dict(evidence.ids or {}),
                            "cached_tag": evidence.tag,
                            "cached_source_type": evidence.source_type,
                        }
                    )
                state_obj = state_obj.with_focused_detail(FocusedDetailSlot(**slot_kwargs))
        elif view in {"list_compact", "subject_activity", "stats_summary", "comparison_table"}:
            # 새 list publish — manifest 갱신 + 직전 focused_detail 클리어 (stale).
            if manifest is not None and manifest.items:
                snapshot = _manifest_to_display_snapshot(manifest=manifest, state=state)
                if snapshot is not None:
                    result_kind = _result_kind_from_resolution(state)
                    state_obj = state_obj.with_manifest(
                        ManifestSlot(
                            result_kind=result_kind,
                            snapshot=snapshot,
                            published_turn_id=state.turn_id,
                        )
                    )
            state_obj = state_obj.with_focused_detail(None)
        # view == "empty" 또는 None: 슬롯 모두 그대로 유지.

    # 3) SessionState → SessionMemory 반영
    memory = SessionStateAdapter.to_session_memory(state_obj, base=state.session_memory)

    # 4) KV 저장
    saved = False
    if state.kv_store is not None and state.conversation_id:
        saved = await save_pipeline_session(
            kv_store=state.kv_store,
            conversation_id=state.conversation_id,
            memory=memory,
        )

    total = state.total_ms() if state.request_started_at else 0.0
    logger.info(
        f"[save_session] persisted(KV 저장 완료) "
        f"req={rid} cid={short_id(state.conversation_id)} "
        f"publishing={publishing}(답변 발행 여부) "
        f"view={view}(노출 뷰) "
        f"has_subject={state_obj.has_subject()}(주제 슬롯) "
        f"has_manifest={state_obj.has_manifest()}(매니페스트 슬롯) "
        f"has_focused_detail={state_obj.has_focused_detail()}(상세 슬롯) "
        f"kv_saved={saved}(KV 저장 성공) "
        f"total_ms={total:.1f}(턴 총 소요시간)"
    )
    return {"session_memory": memory, "session_state": state_obj}


# ============================================================================
# Routing
# ============================================================================

def route_after_dialogue(state: AgentPipelineState) -> str:
    intent = state.dialogue_intent
    if intent is None:
        return "emit_internal_error"
    if intent.kind == "direct_answer":
        return "emit_direct_answer"
    if intent.kind == "clarification":
        return "emit_clarification"
    return "entity_resolver"


def route_after_entity_resolver(state: AgentPipelineState) -> str:
    resolution = state.entity_resolution
    if resolution is None:
        return "emit_internal_error"
    if resolution.clarification_needed:
        return "emit_clarification"
    return "search_planner"


def route_after_search_planner(state: AgentPipelineState) -> str:
    if state.search_plan is None:
        intent = state.dialogue_intent
        # ask_meta는 검색 없이 즉답 — manifest item의 tag/axis로 분류 답변 생성.
        if intent is not None and intent.kind == "ask_meta":
            return "emit_meta_answer"
        # ask_children도 즉답 — focused_detail.child_entities 캐시 활용.
        if intent is not None and intent.kind == "ask_children":
            return "emit_children_list"
        # 그 외 plan=None(DialogueAgent가 search/detail로 분류했지만 query 부재 등) → clarify.
        return "emit_clarification"
    return "retrieval_agent"


def route_after_retrieval(state: AgentPipelineState) -> str:
    if state.search_result is None:
        return "emit_internal_error"
    if state.search_result.status == "error":
        return "emit_internal_error"
    return "evidence_curator"


def route_after_critic(state: AgentPipelineState) -> str:
    decision = state.guard_decision
    if decision is None:
        return "emit_internal_error"
    if decision.decision == "publish":
        return "save_session"
    if decision.decision == "repair_answer":
        return "answer_agent"
    if decision.decision == "clarify":
        return "emit_clarification"
    return "emit_internal_error"


# ============================================================================
# Helpers
# ============================================================================

def _make_publish_artifact(
    *,
    decision: Any,
    draft: Any,
    bundle: Optional[Any] = None,
) -> AnswerArtifact:
    """ReferenceManifest + Bundle → AnswerArtifact.

    references[N]에 evidence.score를 첨부해 운영 UI가 신뢰도 표시 가능.
    score는 published_rank ↔ snapshot_rank 매핑으로 EvidenceBundle.items에서 추출.
    """
    # evidence rank → score 매핑 (published_rank == snapshot_rank by ADR-0017).
    score_by_rank: Dict[int, float] = {}
    if bundle is not None:
        for ev in (bundle.items or []):
            score_by_rank[ev.snapshot_rank] = float(ev.score or 0.0)

    refs: List[Dict[str, Any]] = []
    source_refs: List[Any] = []
    if decision.reference_manifest is not None:
        for item in decision.reference_manifest.items:
            ref_dict: Dict[str, Any] = {
                "rank": item.published_rank,
                "id": item.id,
                "tag": item.tag,
                "title": item.title,
            }
            score = score_by_rank.get(item.source_snapshot_rank)
            if score is not None:
                ref_dict["score"] = round(score, 4)
            refs.append(ref_dict)
            source_refs.append(item)
    answer_kind = "llm_streamed"
    if draft.template == "no_result":
        answer_kind = "no_result"
    return AnswerArtifact(
        text=decision.text or "",
        answer_kind=answer_kind,
        stream_metrics=dict(draft.stream_metrics or {}),
        references=refs,
        source_refs=source_refs,
        meta={"critic_reasoning": decision.reasoning},
    )


def _manifest_to_display_snapshot(
    *,
    manifest: ReferenceManifest,
    state: AgentPipelineState,
) -> Optional[DisplaySnapshot]:
    if manifest is None or not manifest.items:
        return None
    items: List[DisplayItem] = []
    for ref in manifest.items:
        if ref.id_axis in ("pjt_id", "pjt_no"):
            entity_kind = "project"
        elif ref.id_axis == "person_no":
            entity_kind = "people"
        elif ref.id_axis == "org_id":
            entity_kind = "org"
        else:
            entity_kind = "perf"
        kwargs: Dict[str, Any] = {
            "display_rank": ref.published_rank,
            "entity_kind": entity_kind,
            "doc_type": ref.tag,
            "doc_id": ref.id,
            "title_text": ref.title,
        }
        if ref.id_axis == "pjt_id":
            kwargs["pjt_id"] = ref.id
        elif ref.id_axis == "pjt_no":
            kwargs["pjt_no"] = ref.id
        elif ref.id_axis == "rst_id":
            kwargs["rst_id"] = ref.id
        elif ref.id_axis == "person_no":
            kwargs["person_no"] = ref.id
        elif ref.id_axis == "org_id":
            kwargs["org_id"] = ref.id
        items.append(DisplayItem(**kwargs))
    return DisplaySnapshot(
        view_id=f"{state.conversation_id}:turn:{state.turn_id}",
        conversation_id=state.conversation_id,
        turn_id=state.turn_id,
        request_id=state.request_id,
        context_kind=_result_kind_from_resolution(state),
        requested_count=len(items),
        visible_count=len(items),
        raw_count=manifest.total_visible,
        items=items,
    )


def _result_kind_from_resolution(state: AgentPipelineState) -> str:
    resolution = state.entity_resolution
    if resolution is not None and resolution.forced_target:
        return resolution.forced_target
    intent = state.dialogue_intent
    if intent is not None and intent.target_hint:
        return intent.target_hint
    return "project"


# ============================================================================
# ask_meta 즉답 헬퍼
# ============================================================================

# rst_id 접두어 → 사람-읽기 NTIS 성과 분류 (apps/api/rag_mapper/schema_types.py DataTag 매핑).
_RST_PREFIX_TO_LABEL: Dict[str, str] = {
    "CNL": "논문(Conventional Literature)",
    "JNL": "논문(Journal)",
    "PTR": "특허",
    "SNW": "소프트웨어",
    "REP": "연구보고서",
    "BIN": "생명정보",
    "COM": "화합물",
    "TAI": "기술요약",
    "NVR": "신품종",
    "EQU": "시설·장비",
    "BRS": "생물자원",
}

_TAG_TO_LABEL: Dict[str, str] = {
    "IRD_NAI_PJT_INFO": "과제(국가 R&D 사업)",
    "IRD_NAI_RI_PAPER": "성과 — 논문",
    "IRD_NAI_RI_IPR": "성과 — 특허",
    "IRD_NAI_RI_SW": "성과 — 소프트웨어",
    "IRD_NAI_RI_RSCH_RPT": "성과 — 연구보고서",
    "IRD_NAI_RI_FCLT_EQUIP": "성과 — 시설·장비",
    "IRD_NAI_RI_VARIETY": "성과 — 신품종",
}

_TARGET_TO_LABEL: Dict[str, str] = {
    "project": "과제(국가 R&D 사업)",
    "perf": "성과(논문/특허/SW/장비 등)",
    "people": "사람(연구자)",
    "org": "기관",
    "support": "지원·매뉴얼",
}


def _classify_rst_id(rst_id: str) -> Optional[str]:
    """rst_id 접두어로부터 사람-읽기 분류 반환. 접두어 매칭 실패 시 None."""
    if not rst_id or "-" not in rst_id:
        return None
    prefix = rst_id.split("-", 1)[0].upper()
    return _RST_PREFIX_TO_LABEL.get(prefix)


def _exclude_anchor_self(
    *,
    result: SearchResult,
    anchor_ids: Dict[str, str],
) -> SearchResult:
    """ask_similar 결과에서 anchor 자신과 동일한 식별자를 가진 evidence 제거.

    각 axis(pjt_id/rst_id/pjt_no/person_no/org_id)별로 anchor_ids 값과 일치하면 제외.
    snapshot_rank는 1..N으로 재부여.
    """
    if not result.evidences or not anchor_ids:
        return result
    keep: List[CanonicalEvidence] = []
    excluded = 0
    for ev in result.evidences:
        is_self = False
        for axis in ("pjt_id", "rst_id", "pjt_no", "person_no", "org_id"):
            anchor_val = (anchor_ids.get(axis) or "").strip()
            ev_val = (ev.ids.get(axis) or "").strip() if ev.ids else ""
            if anchor_val and ev_val and anchor_val == ev_val:
                is_self = True
                break
        if is_self:
            excluded += 1
            continue
        keep.append(ev)
    if not excluded:
        return result
    renumbered = [ev.model_copy(update={"snapshot_rank": i + 1}) for i, ev in enumerate(keep)]
    new_diag = dict(result.diagnostics or {})
    new_diag["ask_similar_excluded_self"] = excluded
    return result.model_copy(
        update={
            "evidences": renumbered,
            "total_hits": max(0, result.total_hits - excluded),
            "diagnostics": new_diag,
        }
    )


def _try_cached_detail(
    *,
    plan: Any,
    session_state: Optional[SessionState],
) -> Optional[SearchResult]:
    """P0-B 캐싱 — 단일 detail task가 focused_detail.cached_ids와 일치하면 캐시 SearchResult 생성.

    조건 (모두 만족):
        - session_state.focused_detail 있음
        - focused_detail.cached_ids 비어있지 않음 (evidence 본문 캐시됨)
        - plan.tasks 정확히 1개 + action="detail" + strategy="exact_lookup"
        - task의 identifiers와 cached_ids가 동일 axis/value 매칭

    Returns:
        cache hit이면 SearchResult(status="single", evidences=[cached]).
        miss면 None.
    """
    if session_state is None or not session_state.has_focused_detail():
        return None
    slot = session_state.focused_detail
    if not slot.cached_ids:
        return None
    if plan is None or len(plan.tasks) != 1:
        return None
    task = plan.tasks[0]
    if task.action != "detail" or task.strategy != "exact_lookup":
        return None

    # task의 identifiers와 cached_ids 매칭 — 어떤 axis든 한 개라도 일치하면 hit.
    matched_axis: Optional[str] = None
    for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
        task_values = getattr(task.identifiers, axis) or []
        cached_value = (slot.cached_ids.get(axis) or "").strip()
        if cached_value and cached_value in task_values:
            matched_axis = axis
            break
    if matched_axis is None:
        return None

    # 캐시된 evidence 재구성.
    cached_ev = CanonicalEvidence(
        identity=f"cached::{matched_axis}::{slot.cached_ids[matched_axis]}",
        source_type=slot.cached_source_type or (slot.anchor.kind if slot.anchor else "project"),
        tag=slot.cached_tag,
        ids=dict(slot.cached_ids),
        title=slot.title or (slot.anchor.title_text if slot.anchor else ""),
        summary=slot.summary or "",
        facts=dict(slot.facts or {}),
        roles={k: list(v) for k, v in (slot.roles or {}).items()},
        child_entities=list(slot.child_entities or []),
        snapshot_rank=1,
        score=1.0,
    )
    return SearchResult(
        status="single",
        evidences=[cached_ev],
        total_hits=1,
        diagnostics={
            "cache_hit": True,
            "cache_source": "focused_detail",
            "matched_axis": matched_axis,
        },
    )


def _verify_subject_in_children(
    *,
    subject_name: str,
    subject_kind: str,
    researchers: List[Dict[str, Any]],
    orgs: List[Dict[str, Any]],
    title: str,
) -> str:
    """focused_detail의 자식 엔티티에서 subject_name을 검색해 Yes/No+역할 답변 생성.

    인물(`subject_kind=people` 또는 미지정) 우선 검색. 그 후 기관(`subject_kind=org` 또는 미지정).
    매칭 안 되면 빈 문자열 반환 — 호출자가 명단 분기로 fallback.
    """
    name_norm = subject_name.strip()
    if not name_norm:
        return ""

    # 인물 매칭
    if subject_kind != "org":
        for entity in researchers:
            display = (entity.get("display_name") or "").strip()
            if not display:
                continue
            if display == name_norm or name_norm in display or display in name_norm:
                role = (entity.get("role") or "").strip()
                aff = (entity.get("affiliation") or "").strip()
                fragments = [f"네, '{title}'의 참여연구자에 **{display}**님이 포함되어 있습니다"]
                if role:
                    fragments.append(f"역할은 **{role}**")
                if aff:
                    fragments.append(f"소속은 {aff}")
                return ", ".join(fragments) + "."
        # 인물 미매칭 — 책임자/주연구자 정보를 안내
        top_level = [r for r in researchers if "top_level" in (r.get("parent_relation") or "").lower()]
        primary = (top_level or researchers)[:3]
        primary_text = ", ".join(
            f"{(p.get('display_name') or '').strip()}({(p.get('role') or '역할 미상').strip()})"
            for p in primary
            if (p.get("display_name") or "").strip()
        )
        if primary_text:
            return (
                f"아니요, '{title}'의 참여연구자 목록에 '{name_norm}'님은 없습니다. "
                f"확인된 참여연구자: {primary_text}."
            )

    # 기관 매칭
    if subject_kind != "people":
        for entity in orgs:
            display = (entity.get("display_name") or "").strip()
            if not display:
                continue
            if display == name_norm or name_norm in display or display in name_norm:
                role = (entity.get("role") or "").strip()
                role_label = "주관기관" if role == "lead_org" else (role or "참여기관")
                return f"네, '{title}'의 {role_label}에 **{display}**가 포함되어 있습니다."
        if orgs and subject_kind == "org":
            org_names = ", ".join(
                (e.get("display_name") or "").strip() for e in orgs[:3]
            )
            return (
                f"아니요, '{title}'의 참여기관 목록에 '{name_norm}'은 없습니다. "
                f"확인된 기관: {org_names}."
            )

    return ""


def _build_children_list_text(
    *,
    session_state: Optional[SessionState],
    intent: Optional[Any] = None,
) -> str:
    """focused_detail.child_entities를 사람-읽기 가능한 글머리표로 정리.

    intent.subject_name이 있으면 인물·기관 검증 답변 분기(2026-05-26):
        "김수빈 연구책임자?" → child_entities에서 매칭 검색 후 Yes/No + 역할/소속 답변.

    명단 요청(intent.subject_name 없음)이면 parent_relation별 분류 글머리표:
        - top_level_researcher / participant_researcher → [참여연구자]
        - lead_org / prtcp_org → [참여기관]
        - related_perf → [연계 성과]
    """
    if session_state is None or not session_state.has_focused_detail():
        return (
            "직전에 본 상세 항목 정보가 세션에 없어 참여자/참여기관을 즉답할 수 없습니다. "
            "먼저 특정 항목의 상세 정보를 요청해 주세요."
        )
    slot = session_state.focused_detail
    children = slot.child_entities or []
    title = slot.title or slot.anchor.title_text or "직전 항목"

    researchers: List[Dict[str, Any]] = []
    orgs: List[Dict[str, Any]] = []
    perfs: List[Dict[str, Any]] = []
    for entity in children:
        if not isinstance(entity, dict):
            continue
        name = (entity.get("display_name") or "").strip()
        if not name:
            continue
        kind = (entity.get("kind") or "").lower()
        relation = (entity.get("parent_relation") or "").lower()
        if kind == "people" or "researcher" in relation:
            researchers.append(entity)
        elif kind == "org" or "org" in relation:
            orgs.append(entity)
        elif kind == "perf" or "perf" in relation:
            perfs.append(entity)

    if not (researchers or orgs or perfs):
        return f"'{title}'의 참여자/참여기관 정보가 저장된 근거에 없습니다."

    # 검증 분기: subject_name이 있으면 child_entities + (focused_detail anchor 본문의)
    # participant_role_map에서 매칭 검색 후 Yes/No 답변.
    subject_name = ""
    subject_kind = ""
    if intent is not None:
        subject_name = (getattr(intent, "subject_name", "") or "").strip()
        subject_kind = (getattr(intent, "subject_kind", "") or "").strip()
    if subject_name:
        verification = _verify_subject_in_children(
            subject_name=subject_name,
            subject_kind=subject_kind,
            researchers=researchers,
            orgs=orgs,
            title=title,
        )
        if verification:
            return verification

    parts: List[str] = [f"'{title}'의 참여자·참여기관 정보입니다."]
    if researchers:
        parts.append(f"\n**참여연구자 ({len(researchers)}명)**")
        for e in researchers[:50]:
            line = f"- {e.get('display_name')}"
            role = (e.get("role") or "").strip()
            aff = (e.get("affiliation") or "").strip()
            if role:
                line += f" ({role})"
            if aff:
                line += f" — {aff}"
            parts.append(line)
    if orgs:
        parts.append(f"\n**참여기관 ({len(orgs)}곳)**")
        for e in orgs[:30]:
            line = f"- {e.get('display_name')}"
            role = (e.get("role") or "").strip()
            if role and role != "lead_org":
                line += f" ({role})"
            elif role == "lead_org":
                line += " (주관)"
            parts.append(line)
    if perfs:
        parts.append(f"\n**연계 성과 ({len(perfs)}건)**")
        for e in perfs[:30]:
            line = f"- {e.get('display_name')}"
            relation = (e.get("parent_relation") or "").strip()
            if relation:
                line += f" ({relation})"
            parts.append(line)
    return "\n".join(parts)


def _build_meta_answer_text(
    *,
    intent: Optional[Any],
    resolution: Optional[Any],
    session_state: Optional[SessionState],
) -> str:
    """ask_meta 답변 본문 구성. 우선순위:

    1. resolution.manifest_resolved_target / manifest_resolved_axis (EntityResolver가
       manifest_rank를 해소한 경우)
    2. focused_detail anchor (해소 실패 시 fallback)
    3. 둘 다 없으면 안내 메시지
    """
    rank: Optional[int] = (
        intent.manifest_rank if intent and intent.manifest_rank is not None else None
    )
    rank_label = f"{rank}번 항목은" if rank else "해당 항목은"

    # 1) resolution 기반 분류
    if resolution is not None:
        target = resolution.manifest_resolved_target
        axis = resolution.manifest_resolved_axis
        # rst_id 접두어 우선 — 가장 구체적인 정보
        for rst_value in (resolution.identifiers.rst_id or []):
            label = _classify_rst_id(rst_value)
            if label:
                return f"{rank_label} **성과** 유형이며 세부 종류는 **{label}**입니다."
        # tag 직접 매핑이 가능한지 진단에서 확인
        tag_label = None
        diag = getattr(resolution, "diagnostics", {}) or {}
        manifest_tag = diag.get("manifest_item_doc_type") or diag.get(
            "focused_detail_doc_type"
        )
        if manifest_tag:
            tag_label = _TAG_TO_LABEL.get(manifest_tag.strip())
        if tag_label:
            return f"{rank_label} **{tag_label}**입니다."
        # target 단위 분류
        if target:
            target_label = _TARGET_TO_LABEL.get(target)
            if target_label:
                return f"{rank_label} **{target_label}** 유형입니다."

    # 2) focused_detail anchor 기반 분류 (resolution이 manifest_rank 해소 못한 경우)
    if session_state is not None and session_state.has_focused_detail():
        anchor = session_state.focused_detail.anchor
        if anchor.rst_id:
            label = _classify_rst_id(anchor.rst_id)
            if label:
                return f"{rank_label} **성과** 유형이며 세부 종류는 **{label}**입니다."
        if anchor.doc_type:
            tag_label = _TAG_TO_LABEL.get(anchor.doc_type.strip())
            if tag_label:
                return f"{rank_label} **{tag_label}**입니다."
        if anchor.kind:
            target_label = _TARGET_TO_LABEL.get(anchor.kind)
            if target_label:
                return f"{rank_label} **{target_label}** 유형입니다."

    # 3) manifest 전체 메타 (2026-05-26 — manifest_rank=None + 직전 manifest 존재 시
    #    인명·유형·연도 분포를 한 문장으로 보고. 사용자 의도가 "전부 같은 사람이야?"/"전부
    #    같은 유형이야?" 같은 manifest 통계 질문일 때 답할 수 있게 한다).
    if (
        rank is None
        and session_state is not None
        and session_state.has_manifest()
        and getattr(session_state.published_manifest, "snapshot", None) is not None
    ):
        snapshot = session_state.published_manifest.snapshot
        items = list(snapshot.items or [])
        if items:
            text = _summarize_manifest_meta(items)
            if text:
                return text

    return (
        "현재 직전 검색 결과(또는 가장 최근 본 상세 항목)가 없어 분류를 즉답할 수 없습니다. "
        "어떤 항목의 유형을 알고 싶은지 다시 알려주실 수 있나요?"
    )


def _summarize_manifest_meta(items: List[Any]) -> str:
    """직전 manifest의 인명·유형·연도 분포를 한 문장으로 요약 (manifest 전체 메타 질문 답변).

    items: List[DisplayItem] (apps.conversation.view_state.DisplayItem)

    Returns:
        "직전 결과 N건은 [유형분포], 연도 [범위], [인명분포]입니다." 형식 한국어 문장.
        분류 가능한 시그널이 없으면 빈 문자열.
    """
    n = len(items)
    if n == 0:
        return ""

    # --- entity_kind / doc_type 분포 ---
    kind_counts: Dict[str, int] = {}
    for it in items:
        kind = (getattr(it, "entity_kind", "") or "").strip() or "unknown"
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kind_label_map = {"project": "과제", "perf": "성과", "people": "사람", "org": "기관", "support": "지원"}
    if len(kind_counts) == 1:
        only_kind = next(iter(kind_counts))
        kind_text = f"모두 {kind_label_map.get(only_kind, only_kind)}"
    else:
        parts = [
            f"{kind_label_map.get(k, k)} {c}건"
            for k, c in sorted(kind_counts.items(), key=lambda kv: -kv[1])
        ]
        kind_text = ", ".join(parts)

    # --- 연도 분포 ---
    years = [int(it.year) for it in items if getattr(it, "year", None) is not None]
    year_text = ""
    if years:
        y_min, y_max = min(years), max(years)
        year_text = f"{y_min}년" if y_min == y_max else f"{y_min}~{y_max}년"

    # --- 인명 분포 (person_no 우선, 없으면 researchers 첫 항목) ---
    person_keys: List[str] = []
    person_names: List[str] = []
    name_by_key: Dict[str, str] = {}
    for it in items:
        pid = (getattr(it, "person_no", "") or "").strip()
        names = list(getattr(it, "researchers", []) or [])
        first_name = (names[0] if names else "").strip()
        if pid:
            person_keys.append(pid)
            if pid not in name_by_key and first_name:
                name_by_key[pid] = first_name
        elif first_name:
            person_keys.append(f"name:{first_name}")
            name_by_key.setdefault(f"name:{first_name}", first_name)
        person_names.extend([n.strip() for n in names if n and n.strip()])
    unique_keys = sorted(set(person_keys))
    person_text = ""
    if person_keys:
        if len(unique_keys) == 1:
            only_name = name_by_key.get(unique_keys[0]) or "동일 인물"
            person_text = f"모두 {only_name}님의 활동"
        else:
            top_name_counts: Dict[str, int] = {}
            for k in person_keys:
                nm = name_by_key.get(k, "(이름 미상)")
                top_name_counts[nm] = top_name_counts.get(nm, 0) + 1
            top = sorted(top_name_counts.items(), key=lambda kv: -kv[1])[:3]
            person_text = "인물 분포 " + ", ".join(f"{nm}({c})" for nm, c in top)

    fragments = [f"직전 결과 {n}건은 {kind_text}"]
    if year_text:
        fragments.append(year_text)
    if person_text:
        fragments.append(person_text)
    return ", ".join(fragments) + "입니다."


def _build_focus_entity_from_manifest(
    *,
    manifest: ReferenceManifest,
    state: AgentPipelineState,
) -> Optional[FocusEntity]:
    """single_detail view의 단일 ReferenceItem → FocusEntity (focused_detail 슬롯용).

    EvidenceBundle.items[0]보다 ReferenceManifest.items[0]을 진실원으로 쓴다 — CriticAgent가
    이미 tag/id_axis 정합 검증을 마친 후이기 때문.
    """
    if manifest is None or not manifest.items:
        return None
    ref = manifest.items[0]
    if ref.id_axis in ("pjt_id", "pjt_no"):
        kind = "project"
    elif ref.id_axis == "person_no":
        kind = "people"
    elif ref.id_axis == "org_id":
        kind = "org"
    else:
        kind = "perf"
    kwargs: Dict[str, Any] = {
        "kind": kind,
        "source": "critic_publish",
        "view_id": f"{state.conversation_id}:turn:{state.turn_id}",
        "display_rank": ref.published_rank,
        "doc_type": ref.tag,
        "doc_id": ref.id,
        "title_text": ref.title,
    }
    if ref.id_axis == "pjt_id":
        kwargs["pjt_id"] = ref.id
    elif ref.id_axis == "pjt_no":
        kwargs["pjt_no"] = ref.id
    elif ref.id_axis == "rst_id":
        kwargs["rst_id"] = ref.id
    elif ref.id_axis == "person_no":
        kwargs["person_no"] = ref.id
    elif ref.id_axis == "org_id":
        kwargs["org_id"] = ref.id
    return FocusEntity(**kwargs)


# ============================================================================
# Workflow builder
# ============================================================================
# ADR-0020 Phase 5: build_agent_pipeline_graph (정적 7-agent 그래프) 삭제.
# 단일 agentic 파이프라인(agentic_workflow.build_agentic_pipeline_graph)으로 통합.
# 이 파일은 공유 노드(node_load_session, node_save_session, emit_*, _make_node_* 등)
# 만을 제공하는 모듈로 역할이 축소됐다.
