"""Phase 3 — Cyclic LangGraph (agentic loop).

기존 정적 그래프와 별개로 PlannerAgent + ToolExecutor 기반 동적 그래프를 빌드한다.

흐름:
    load_session
      → dialogue_agent  (의도 분류 — direct_answer/clarification은 Planner 없이 즉답 종료)
      → entity_resolver  (person_no/org_id 등 식별자 해소 — 모호 시 clarification 즉답)
      → planner_loop  (LLM이 다음 step 결정)
        → tool_executor  (call_tool인 경우 → Observation 누적 → planner로 복귀)
        → answer_curator  (answer인 경우 → 누적 observation의 evidence를 EvidenceBundle로)
          → answer_agent  (기존 그대로)
            → critic_agent  (기존 그대로)
              → save_session
        → emit_agentic_clarify  (clarify인 경우 → 즉답 종료)
          → save_session

종료 조건:
    - PlannerStep.action ∈ {"answer", "clarify"}
    - plan_state.step_no >= planner.max_steps  (Planner 내부에서 answer로 강제 종료)
    - 동일 ToolCall 반복(loop guard) — answer로 강제 종료

기존 정적 그래프는 그대로 둔다. runtime이 환경변수 RAG_AGENTIC_MODE로 토글.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from apps.api.streaming.contracts import AnswerArtifact
from apps.api.streaming.emitter import StreamEvent
from apps.pipeline.agent_state import AgentPipelineState
from apps.pipeline.agent_workflow import (
    AgentPipelineDeps,
    node_emit_clarification,
    node_emit_direct_answer,
    node_emit_internal_error,
    node_load_session,
    node_save_session,
    _make_node_answer,
    _make_node_critic,
    _make_node_dialogue,
    _make_node_entity_resolver,
)
from apps.pipeline.log_helpers import short_id
from apps.pipeline.agents.contracts import (
    AnswerDraft,
    DialogueIntent,
    EvidenceBundle,
)
from apps.pipeline.agents.planner_contracts import (
    PlannerStep,
    PlanState,
)
from apps.pipeline.contracts import (
    CanonicalEvidence,
    SearchResult,
)
from apps.pipeline.tools.contracts import Observation, ToolCall


try:
    from langgraph.graph import END, START, StateGraph
except ModuleNotFoundError:  # pragma: no cover — 환경 검증용
    END = "__end__"
    START = "__start__"
    StateGraph = None  # type: ignore[assignment]


# ============================================================================
# Node: planner_loop — PlannerAgent.decide_next 호출 후 plan_state 갱신
# ============================================================================

def _make_node_planner(deps: AgentPipelineDeps):
    async def node_planner(state: AgentPipelineState) -> Dict[str, Any]:
        if deps.planner_agent is None or deps.tool_executor is None:
            raise RuntimeError(
                "node_planner: deps.planner_agent / deps.tool_executor 미주입 — "
                "agentic mode 진입 전에 ToolExecutor + PlannerAgent를 deps에 주입해야 한다."
            )
        plan_state = state.plan_state or PlanState(question=state.question or "")
        if plan_state.terminated:
            # 이미 종료된 상태 — 그대로 흘려보냄
            return {"plan_state": plan_state}

        t0 = time.perf_counter()
        tool_specs = deps.tool_executor.specs()
        session_summary = _summarize_session(state)
        step = await deps.planner_agent.decide_next(
            plan_state=plan_state,
            tool_specs=tool_specs,
            request_id=state.request_id,
            conversation_id=state.conversation_id,
            session_summary=session_summary,
        )

        rid = short_id(state.request_id)
        # Loop guard — 동일 호출 직전 반복 감지
        if step.action == "call_tool" and plan_state.decisions:
            new_sig = plan_state.signature_of(step)
            prev_sig = plan_state.signature_of(plan_state.decisions[-1])
            if new_sig == prev_sig:
                logger.warning(
                    f"[agentic_trace] req={rid} step={plan_state.step_no + 1} "
                    f"[planner_loop] guard=duplicate_call(중복 호출 감지) "
                    f"tool={step.tool!r} action_forced=answer(강제 종료, 무한 루프 방지)"
                )
                terminated = plan_state.with_termination("duplicate_call")
                return {"plan_state": terminated}

        latency = (time.perf_counter() - t0) * 1000.0
        next_state = plan_state.with_decision(step)
        # 즉시 종료 액션은 terminated 플래그도 같이 세팅 (라우터가 분기)
        if step.action == "answer":
            next_state = next_state.with_termination("answer")
        elif step.action == "clarify":
            next_state = next_state.with_termination("clarify")
        args_keys = list((step.args or {}).keys()) if step.action == "call_tool" else []
        action_kr = {
            "call_tool": "도구 호출",
            "answer": "답변 진입",
            "clarify": "사용자 되묻기",
        }.get(step.action, "unknown(알 수 없음)")
        logger.info(
            f"[agentic_trace] req={rid} step={next_state.step_no} "
            f"[planner_loop] decision_emitted "
            f"action={step.action}({action_kr}) tool={step.tool!r} args_keys={args_keys} "
            f"reason={step.reason[:80]!r}(이유) confidence={step.confidence:.2f}(신뢰도) "
            f"latency_ms={latency:.1f}(소요시간)"
        )
        # 종료 시 plan summary
        if next_state.terminated:
            _emit_plan_summary(request_id=rid, plan_state=next_state)
        return {
            "plan_state": next_state,
            "latencies": {f"planner_step_{next_state.step_no}": latency / 1000.0},
        }

    return node_planner


# ============================================================================
# Node: tool_executor — 마지막 decision의 ToolCall 실행 → plan_state 갱신
# ============================================================================

def _make_node_tool_executor(deps: AgentPipelineDeps):
    async def node_tool_executor(state: AgentPipelineState) -> Dict[str, Any]:
        if deps.tool_executor is None:
            raise RuntimeError("node_tool_executor: deps.tool_executor 미주입")
        plan_state = state.plan_state
        if plan_state is None or plan_state.last_decision() is None:
            raise RuntimeError("node_tool_executor: plan_state.last_decision()이 None")
        decision = plan_state.last_decision()
        if decision.action != "call_tool" or not decision.tool:
            raise RuntimeError(
                f"node_tool_executor: last decision is not call_tool "
                f"(action={decision.action}, tool={decision.tool!r})"
            )

        # context에 현재 turn 식별자 + session 주입 (도구가 ctx.session_state 등을 사용)
        ctx = deps.tool_executor.context
        ctx.request_id = state.request_id
        ctx.turn_id = state.turn_id
        ctx.conversation_id = state.conversation_id
        ctx.session_state = state.session_state

        call = ToolCall(tool=decision.tool, args=dict(decision.args or {}), reason=decision.reason)
        t0 = time.perf_counter()
        logger.info(
            f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
            f"[tool_executor] invoke_start "
            f"tool={call.tool!r}(호출 도구) args_keys={list(call.args.keys())}(인자 키)"
        )
        obs = await deps.tool_executor.execute(call)
        latency = (time.perf_counter() - t0) * 1000.0
        result_summary = _summarize_obs_for_trace(obs)
        status_kr = "성공" if obs.status == "ok" else f"실패({obs.error_code})"
        logger.info(
            f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
            f"[tool_executor] observation_ready "
            f"tool={obs.tool!r} status={obs.status}({status_kr}) "
            f"{result_summary} latency_ms={obs.latency_ms:.1f}(소요시간)"
        )
        return {
            "plan_state": plan_state.with_observation(obs),
            "latencies": {f"tool_step_{plan_state.step_no}": latency / 1000.0},
        }

    return node_tool_executor


# ============================================================================
# Node: adequacy_gate — Planner와 분리된 LLM judge (c1)
# ============================================================================

def _make_node_adequacy_gate(deps: AgentPipelineDeps):
    async def node_adequacy_gate(state: AgentPipelineState) -> Dict[str, Any]:
        """tool_executor 후 'answer 가능한가' 판정. 결과를 state.diagnostics에 기록.

        adequacy_gate 미주입 시 자동 통과 (insufficient로 설정 → planner_loop 복귀, 기존 흐름).
        주입 시 LLM judge로 verdict 결정 → 라우터가 answer_curator/planner_loop 분기.
        """
        if deps.adequacy_gate is None:
            # 미주입 — 결정적 default(insufficient)로 통과해 planner_loop 복귀
            verdict_dict = {
                "verdict": "insufficient", "reason": "gate_not_injected",
                "confidence": 1.0, "source": "deterministic",
            }
            return {
                "diagnostics": {**(state.diagnostics or {}), "last_adequacy_verdict": verdict_dict},
            }
        plan_state = state.plan_state
        if plan_state is None:
            return {
                "diagnostics": {
                    **(state.diagnostics or {}),
                    "last_adequacy_verdict": {
                        "verdict": "insufficient", "reason": "no_plan_state",
                        "confidence": 1.0, "source": "deterministic",
                    },
                },
            }
        t0 = time.perf_counter()
        verdict = await deps.adequacy_gate.check(
            question=state.question or "",
            plan_state=plan_state,
            request_id=state.request_id,
            conversation_id=state.conversation_id,
        )
        latency = (time.perf_counter() - t0) * 1000.0
        verdict_kr = {
            "adequate": "충분, 답변 가능",
            "insufficient": "부족, 추가 도구 필요",
            "error": "판정 오류",
        }.get(verdict.verdict, "알 수 없음")
        source_kr = {
            "deterministic": "결정적 규칙",
            "llm_judge": "LLM 판정",
            "fallback": "안전 fallback",
        }.get(verdict.source, "알 수 없음")
        logger.info(
            f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
            f"[adequacy_gate] verdict_ready "
            f"verdict={verdict.verdict}({verdict_kr}) "
            f"source={verdict.source}({source_kr}) "
            f"reason={verdict.reason[:80]!r}(이유) latency_ms={latency:.1f}(소요시간)"
        )
        return {
            "diagnostics": {
                **(state.diagnostics or {}),
                "last_adequacy_verdict": {
                    "verdict": verdict.verdict, "reason": verdict.reason,
                    "confidence": verdict.confidence, "source": verdict.source,
                },
            },
            "latencies": {f"adequacy_step_{plan_state.step_no}": latency / 1000.0},
        }

    return node_adequacy_gate


# ============================================================================
# Node: answer_curator — 누적 observations에서 EvidenceBundle 빌드
# ============================================================================

async def node_answer_curator(state: AgentPipelineState) -> Dict[str, Any]:
    """누적 observations에서 답변 흐름을 결정.

    2026-05-27 Step 4 확장:
        - **response.* 도구 결과** (`final_text` 보유) 또는 **Planner step.answer_text** 가 있으면
          AnswerAgent/Critic을 건너뛰고 그 텍스트를 final_answer_text로 직접 적용 (능동 응답).
        - 그 외엔 누적 search.* observations에서 evidence 선택 후 EvidenceBundle 빌드 → AnswerAgent 흐름.
        - 검색 observation도 없고 직접 응답도 없으면 표준 거절 텍스트로 최종 답변 (empty bundle 회피).
    """
    plan_state = state.plan_state
    if plan_state is None:
        bundle = EvidenceBundle(view="empty", items=[], groups=[])
        return {"evidence_bundle": bundle}

    # 1. response.* 도구 또는 step.answer_text를 직접 답변으로 흐름.
    direct_text, direct_kind = _select_direct_response_text(plan_state)
    if direct_text:
        if state.stream_emitter is not None:
            await state.stream_emitter.publish(
                StreamEvent(
                    kind="answer.chunk",
                    request_id=state.request_id,
                    content=direct_text,
                    model_key=f"agentic_{direct_kind or 'direct_answer'}",
                )
            )
        artifact = AnswerArtifact(
            text=direct_text,
            answer_kind="direct_answer" if direct_kind != "unsupported" else "clarification",
            references=[],
            source_refs=[],
            meta={
                "kind": f"agentic_{direct_kind}",
                "step_count": plan_state.step_no,
                "termination_reason": plan_state.termination_reason,
            },
        )
        kind_kr = {
            "direct_answer": "직접 응답·인사·간단 안내",
            "unsupported": "정직 거절·NTIS 범위 외",
        }.get(direct_kind or "", "알 수 없음")
        logger.info(
            f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
            f"[answer_curator] direct_response_selected "
            f"kind={direct_kind!r}({kind_kr}) chars={len(direct_text)}(답변 길이) "
            f"skip=AnswerAgent+Critic(생성·검증 건너뜀)"
        )
        # AnswerAgent/Critic을 건너뛰기 위해 evidence_bundle을 empty + final_answer_text 직접 채움.
        bundle = EvidenceBundle(
            view="empty", items=[], groups=[],
            diagnostics={"agentic_direct_response": True, "kind": direct_kind},
        )
        return {
            "evidence_bundle": bundle,
            "final_answer_text": direct_text,
            "answer_artifact": artifact,
            "dialogue_intent": state.dialogue_intent or DialogueIntent(
                kind="direct_answer", target_hint=None, query=state.question or "",
                direct_text=direct_text, reason="agentic_direct", confidence=0.9,
            ),
        }

    evidences, view, source_obs = _select_evidences_for_answer(plan_state.observations)
    bundle = EvidenceBundle(
        view=view,
        items=evidences,
        groups=[],
        total_unique=len(evidences),
        diagnostics={
            "agentic_step_count": plan_state.step_no,
            "tool_observations": len(plan_state.observations),
            "evidence_source_tool": source_obs.tool if source_obs else None,
            "evidence_source_call_id": source_obs.call_id if source_obs else None,
        },
    )
    # SearchResult도 채워둔다 (downstream 호환 — CriticAgent 일부 진단)
    sr = SearchResult(
        status="single" if evidences else "empty",
        evidences=evidences,
        total_hits=len(evidences),
        diagnostics={"agentic_mode": True},
    )
    view_kr = {
        "list_compact": "목록",
        "single_detail": "단건 상세",
        "subject_activity": "인물·기관 활동",
        "stats_summary": "통계 집계",
        "empty": "빈 결과",
    }.get(view, "알 수 없음")
    logger.info(
        f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
        f"[answer_curator] evidence_based "
        f"view={view}({view_kr}) evidence_n={len(evidences)}(근거 건수) "
        f"source_tool={source_obs.tool if source_obs else None!r}(근거 도구) "
        f"next=AnswerAgent(LLM 답변 생성으로 진입)"
    )
    # Phase 1에서 load_session→dialogue_agent가 항상 먼저 실행돼 dialogue_intent를 세팅한다.
    # 합성(synthetic) ask_search 의도로 덮어쓰지 않고 실제 분류 결과를 그대로 사용한다 —
    # subject_name·action_hint·length_hint 등 실제 신호가 AnswerAgent prompt로 정상 전달된다.
    intent = state.dialogue_intent
    if intent is None:
        # 방어 — agentic 모드에선 dialogue_agent가 항상 먼저 실행되므로 도달 시 그래프 배선 오류.
        logger.error(
            f"[agentic_trace] req={short_id(state.request_id)} step={plan_state.step_no} "
            f"[answer_curator] dialogue_intent_missing(배선 오류 — dialogue_agent 미실행) "
            f"fallback=synthetic_ask_search"
        )
        intent = DialogueIntent(
            kind="ask_search", target_hint=None, query=state.question or "",
            reason="agentic_fallback_intent", confidence=0.5,
        )
    return {
        "evidence_bundle": bundle,
        "search_result": sr,
        "dialogue_intent": intent,
    }


def _select_direct_response_text(plan_state: "PlanState") -> tuple[Optional[str], Optional[str]]:
    """response.* 도구 또는 Planner step.answer_text → (final_text, kind).

    우선순위:
        1. plan_state.decisions의 마지막 PlannerStep이 action='answer' + answer_text 채워짐 → 그 텍스트.
        2. plan_state.observations에 status='ok'인 response.* 마지막 observation의 result.final_text.
        3. 없으면 (None, None).
    """
    # 1) 마지막 decision의 answer_text
    last_decision = plan_state.last_decision() if plan_state.decisions else None
    if last_decision is not None and last_decision.action == "answer":
        if last_decision.answer_text:
            return last_decision.answer_text, "direct_answer"
    # 2) response.* 마지막 ok observation
    last_response_obs: Optional[Observation] = None
    for obs in plan_state.observations:
        if obs.status != "ok":
            continue
        if obs.tool in {"response.direct_answer", "response.unsupported"}:
            last_response_obs = obs
    if last_response_obs is not None:
        res = last_response_obs.result or {}
        text = str(res.get("final_text") or "").strip()
        if text:
            kind = str(res.get("kind") or "direct_answer")
            return text, kind
    return None, None


def _select_evidences_for_answer(
    observations: List[Observation],
) -> tuple[List[CanonicalEvidence], str, Optional[Observation]]:
    """누적 observations에서 답변에 쓸 evidence 추출.

    우선순위:
        1. status='ok'인 마지막 search.exact_lookup → single_detail view
        2. status='ok'인 마지막 search.aggregate → stats_summary view
        3. status='ok'인 마지막 search.hybrid → list_compact view (또는 evidence 1건이면 single_detail)
        4. 그 외 → empty view
    """
    last_exact: Optional[Observation] = None
    last_agg: Optional[Observation] = None
    last_hybrid: Optional[Observation] = None
    for obs in observations:
        if obs.status != "ok":
            continue
        if obs.tool == "search.exact_lookup":
            last_exact = obs
        elif obs.tool == "search.aggregate":
            last_agg = obs
        elif obs.tool == "search.hybrid":
            last_hybrid = obs

    if last_exact is not None:
        evs = _deserialize_evidences(last_exact.result.get("evidences") or [])
        view = "single_detail" if len(evs) <= 1 else "list_compact"
        return evs, view, last_exact
    if last_agg is not None:
        evs = _deserialize_evidences(last_agg.result.get("evidences") or [])
        return evs, "stats_summary", last_agg
    if last_hybrid is not None:
        evs = _deserialize_evidences(last_hybrid.result.get("evidences") or [])
        view = "single_detail" if len(evs) == 1 else "list_compact"
        return evs, view, last_hybrid
    return [], "empty", None


def _deserialize_evidences(raw_list: List[Dict[str, Any]]) -> List[CanonicalEvidence]:
    """tool result의 evidences(dict) → CanonicalEvidence."""
    out: List[CanonicalEvidence] = []
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        try:
            ev = CanonicalEvidence(**raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[answer_curator] evidence_deserialize_failed idx={i} err={exc}")
            continue
        out.append(ev)
    return out


# ============================================================================
# Node: emit_agentic_clarify — clarify 결정 즉답
# ============================================================================

async def node_emit_agentic_clarify(state: AgentPipelineState) -> Dict[str, Any]:
    plan_state = state.plan_state
    step = plan_state.last_decision() if plan_state else None
    text = (
        step.clarification_question
        if step and step.clarification_question
        else "질문 의도를 좀 더 구체적으로 알려주실 수 있나요?"
    )
    if state.stream_emitter is not None and text:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key="agentic_clarify",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="clarification",
        references=[],
        source_refs=[],
        meta={"kind": "agentic_clarify", "termination_reason": "clarify"},
    )
    logger.info(
        f"[agentic_trace] req={short_id(state.request_id)} "
        f"[emit_agentic_clarify] clarify_dispatched(되묻기 발행) "
        f"text_preview={text[:80]!r}(질문 미리보기) chars={len(text)}(길이)"
    )
    return {"answer_artifact": artifact, "final_answer_text": text}


# ============================================================================
# Routers
# ============================================================================

def _route_after_dialogue(state: AgentPipelineState) -> str:
    """dialogue_agent 출력 → 다음 노드 분기 (정적 그래프 route_after_dialogue와 동일 의미).

    direct_answer / clarification은 Planner를 거치지 않고 즉답 종료한다.
    그 외(ask_search/ask_detail/stats/ask_meta/ask_children/…)는 entity_resolver로 진행해
    식별자(person_no/org_id 등)를 해소한 뒤 planner_loop가 도구 전략을 결정한다.
    """
    intent = state.dialogue_intent
    if intent is None:
        return "emit_internal_error"
    if intent.kind == "direct_answer":
        return "emit_direct_answer"
    if intent.kind == "clarification":
        return "emit_clarification"
    return "entity_resolver"


def _route_after_entity_resolver(state: AgentPipelineState) -> str:
    """entity_resolver 출력 → planner_loop / emit_clarification 분기.

    정적 그래프의 route_after_entity_resolver는 search_planner로 진입하지만, agentic 모드는
    PlannerAgent가 검색 전략을 동적으로 결정하므로 planner_loop로 진입한다.
    동명이인 등 모호성으로 clarification_needed=True면 즉답 되묻기로 종료.
    """
    resolution = state.entity_resolution
    if resolution is None:
        return "emit_internal_error"
    if resolution.clarification_needed:
        return "emit_clarification"
    return "planner_loop"


def _route_after_planner(state: AgentPipelineState) -> str:
    """planner_loop 출력 → 다음 노드 분기."""
    plan_state = state.plan_state
    if plan_state is None:
        return "answer_curator"  # safety fallback
    if plan_state.terminated:
        reason = plan_state.termination_reason
        if reason == "clarify":
            return "emit_agentic_clarify"
        # answer / max_steps / duplicate_call / error → answer 흐름
        return "answer_curator"
    last = plan_state.last_decision()
    if last is None:
        return "answer_curator"
    if last.action == "call_tool":
        return "tool_executor"
    if last.action == "clarify":
        return "emit_agentic_clarify"
    return "answer_curator"


def _route_after_tool_executor(state: AgentPipelineState) -> str:
    """tool_executor → adequacy_gate (or planner_loop if gate 미주입).

    2026-05-27 (c1): adequacy_gate가 deps에 주입돼 있으면 거기서 답 가능 여부 판정.
    """
    # 안전 가드 — 비정상 상태면 종료로 흐름
    plan_state = state.plan_state
    if plan_state is None or plan_state.step_no >= 50:
        return "answer_curator"
    return "adequacy_gate"


def _route_after_adequacy_gate(state: AgentPipelineState) -> str:
    """adequacy_gate → planner_loop / answer_curator 분기.

    diagnostics["last_adequacy_verdict"]를 보고:
        adequate    → answer_curator (Planner를 더 호출하지 않고 답변 흐름 진입)
        insufficient→ planner_loop (다음 step 결정)
        그 외       → planner_loop (안전 fallback)
    """
    diag = (state.diagnostics or {}).get("last_adequacy_verdict") or {}
    verdict = diag.get("verdict") if isinstance(diag, dict) else None
    if verdict == "adequate":
        return "answer_curator"
    return "planner_loop"


def _route_after_answer_curator(state: AgentPipelineState) -> str:
    """answer_curator 출력 → 다음 노드 분기.

    Step 4 (2026-05-27): answer_curator가 response.* 도구 결과를 final_answer_text로 직접 채웠으면
    AnswerAgent/Critic을 건너뛰고 save_session으로 곧장 (능동 직접 응답).
    그 외(evidence 기반)는 기존 answer_agent → critic_agent 흐름.
    """
    if state.final_answer_text and state.answer_artifact is not None:
        return "save_session"
    return "answer_agent"


def _route_after_agentic_critic(state: AgentPipelineState) -> str:
    """Step 2 (2026-05-27): Critic 결정을 정적 그래프와 동일 패턴으로 라우팅.

    이전엔 critic_agent → save_session 직결이라 repair_answer/clarify/internal_error 분기가
    무시되고 모든 답변이 publish_with_warning까지 흘러갔다. 능동 에이전트가 답변 검증 결과를
    실제로 반영하려면 이 라우팅이 필수다.

    분기:
        publish        → save_session
        repair_answer  → answer_agent (1회만 — state.repair_attempted 가드로 무한 루프 차단)
        clarify        → emit_clarification (CriticAgent의 clarification_question 사용)
        internal_error → emit_internal_error
        그 외 / None   → emit_internal_error (안전)
    """
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
# 세션 요약 (Planner prompt에 노출되는 직전 turn 컨텍스트)
# ============================================================================

def _summarize_obs_for_trace(obs: Observation) -> str:
    """tool_executor trace 로그에 노출할 결과 핵심 메트릭 1줄 (k=v 공백 구분)."""
    if obs.status == "error":
        msg = (obs.error_message or "")[:80]
        return f"error_code={obs.error_code!r} msg={msg!r}"
    res = obs.result or {}
    if "evidences" in res:
        evs = res.get("evidences") or []
        return (
            f"search_status={res.get('status')!r} evidence_n={len(evs)} "
            f"total_hits={res.get('total_hits', 0)}"
        )
    if "hits" in res:
        return f"hit_count={res.get('hit_count', 0)}"
    if "identifiers" in res:
        return f"target={res.get('target')!r} item_count={res.get('item_count', 1)}"
    return f"result_keys={list(res.keys())[:6]}"


def _emit_plan_summary(*, request_id: str, plan_state: PlanState) -> None:
    """plan 종료 시 한 줄 요약 — 운영 trace 끝에 step 시퀀스 압축 노출."""
    sequence_parts: List[str] = []
    for i, decision in enumerate(plan_state.decisions, start=1):
        if decision.action == "call_tool":
            sequence_parts.append(f"#{i}:{decision.tool}")
        else:
            sequence_parts.append(f"#{i}:{decision.action}")
    tool_call_count = sum(1 for d in plan_state.decisions if d.action == "call_tool")
    ok_obs = sum(1 for o in plan_state.observations if o.status == "ok")
    err_obs = sum(1 for o in plan_state.observations if o.status == "error")
    termination_kr = {
        "answer": "정상 답변",
        "clarify": "사용자 되묻기",
        "max_steps": "최대 step 초과",
        "duplicate_call": "중복 호출 감지",
        "error": "오류",
    }.get(plan_state.termination_reason or "", "알 수 없음")
    logger.info(
        f"[agentic_trace] req={request_id} [plan_summary] termination_reached "
        f"total_steps={plan_state.step_no}(총 step 수) "
        f"termination={plan_state.termination_reason!r}({termination_kr}) "
        f"tool_calls={tool_call_count}(도구 호출 건수) "
        f"obs_ok={ok_obs}(성공) obs_err={err_obs}(실패) "
        f"sequence=[{' → '.join(sequence_parts)}](실행 시퀀스)"
    )


def _summarize_session(state: AgentPipelineState) -> Dict[str, Any]:
    session = state.session_state
    summary: Dict[str, Any] = {
        "has_subject": session.has_subject() if session else False,
        "has_manifest": session.has_manifest() if session else False,
        "has_focused_detail": session.has_focused_detail() if session else False,
    }
    if session and session.has_subject():
        sub = session.current_subject
        summary["subject"] = {
            "name": sub.subject_name,
            "kind": sub.subject_kind,
            "identity_status": sub.identity_status,
        }
    if session and session.has_focused_detail():
        anchor = session.focused_detail.anchor
        summary["focused_detail"] = {
            "kind": anchor.kind,
            "title": session.focused_detail.title,
        }
    if session and session.has_manifest():
        snap = session.published_manifest.snapshot
        summary["manifest"] = {
            "context_kind": snap.context_kind,
            "item_count": len(snap.items or []),
        }
    # EntityResolver 해소 결과 노출 — Planner가 raw 이름 대신 확정 식별자(person_no/org_id 등)를
    # 도구 args에 쓰도록 한다. payload["session"]에 통째로 직렬화되어 양 pass prompt에 노출된다.
    if state.entity_resolution is not None:
        res = state.entity_resolution
        er: Dict[str, Any] = {"resolution_source": res.resolution_source}
        if res.subject is not None:
            er["subject"] = {
                "kind": res.subject.kind,
                "display_name": res.subject.display_name,
                "person_no": res.subject.person_no,
                "org_id": res.subject.org_id,
                "identity_status": res.subject.identity_status,
            }
        id_map: Dict[str, List[str]] = {}
        ids = res.identifiers
        if ids is not None:
            for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
                values = getattr(ids, axis, None) or []
                if values:
                    id_map[axis] = list(values)
        if id_map:
            er["identifiers"] = id_map
        if res.forced_target:
            er["forced_target"] = res.forced_target
        if res.manifest_rank:
            er["manifest_rank"] = res.manifest_rank
            er["manifest_resolved_target"] = res.manifest_resolved_target
        summary["entity_resolution"] = er
    return summary


# ============================================================================
# Graph builder
# ============================================================================

def build_agentic_pipeline_graph(deps: AgentPipelineDeps):
    """RAG_AGENTIC_MODE=true 때 사용되는 cyclic LangGraph 빌더.

    진입 단(정적 그래프와 공유):
        load_session → dialogue_agent → entity_resolver → planner_loop
        - dialogue_agent: direct_answer/clarification은 Planner 없이 즉답 종료.
        - entity_resolver: person_no/org_id 등 식별자 해소(모호 시 clarification).

    동적 루프(agentic):
        planner_loop ↔ tool_executor (action=call_tool 동안 반복, 사이에 adequacy_gate)
        → answer_curator → answer_agent → critic_agent → save_session
        clarify는 emit_agentic_clarify로 즉답 종료.

    종료: planner가 answer/clarify 결정 → answer_curator 또는 emit_agentic_clarify로 분기.
    """
    if StateGraph is None:
        raise RuntimeError("langgraph not available — install langgraph to use agentic mode")
    if deps.planner_agent is None or deps.tool_executor is None:
        raise RuntimeError(
            "build_agentic_pipeline_graph: deps.planner_agent / deps.tool_executor 모두 필요"
        )

    graph = StateGraph(AgentPipelineState)
    graph.add_node("load_session", node_load_session)
    # 판단(DialogueAgent) → 엔티티 해소(EntityResolver) 단을 planner_loop 앞에 배치.
    # direct_answer/clarification은 여기서 단락 종료, 그 외만 식별자 해소 후 planner_loop 진입.
    graph.add_node("dialogue_agent", _make_node_dialogue(deps))
    graph.add_node("entity_resolver", _make_node_entity_resolver(deps))
    graph.add_node("planner_loop", _make_node_planner(deps))
    graph.add_node("tool_executor", _make_node_tool_executor(deps))
    # Phase 5 (c1): adequacy_gate가 Planner와 결과 적합성 판단을 분리.
    graph.add_node("adequacy_gate", _make_node_adequacy_gate(deps))
    graph.add_node("answer_curator", node_answer_curator)
    graph.add_node("answer_agent", _make_node_answer(deps))
    graph.add_node("critic_agent", _make_node_critic(deps))
    graph.add_node("emit_agentic_clarify", node_emit_agentic_clarify)
    # Step 2: 정적 그래프의 emit_clarification / emit_internal_error 재사용 — critic 결정 분기.
    graph.add_node("emit_clarification", node_emit_clarification)
    graph.add_node("emit_internal_error", node_emit_internal_error)
    # dialogue_agent가 direct_answer(인사/잡담)로 분류 시 즉답 (정적 그래프 node_emit_direct_answer 재사용).
    graph.add_node("emit_direct_answer", node_emit_direct_answer)
    graph.add_node("save_session", node_save_session)

    graph.add_edge(START, "load_session")
    graph.add_edge("load_session", "dialogue_agent")

    # 판단 단: direct_answer/clarification은 즉답 종료, 그 외는 entity_resolver로.
    graph.add_conditional_edges(
        "dialogue_agent", _route_after_dialogue,
        {
            "entity_resolver": "entity_resolver",
            "emit_direct_answer": "emit_direct_answer",
            "emit_clarification": "emit_clarification",
            "emit_internal_error": "emit_internal_error",
        },
    )
    # 엔티티 해소 단: 모호성(동명이인 등)은 되묻기, 그 외는 planner_loop로 진입.
    graph.add_conditional_edges(
        "entity_resolver", _route_after_entity_resolver,
        {
            "planner_loop": "planner_loop",
            "emit_clarification": "emit_clarification",
            "emit_internal_error": "emit_internal_error",
        },
    )

    graph.add_conditional_edges(
        "planner_loop", _route_after_planner,
        {
            "tool_executor": "tool_executor",
            "answer_curator": "answer_curator",
            "emit_agentic_clarify": "emit_agentic_clarify",
        },
    )
    # tool_executor → adequacy_gate (gate 미주입이어도 노드 자체는 안전 통과)
    graph.add_conditional_edges(
        "tool_executor", _route_after_tool_executor,
        {"adequacy_gate": "adequacy_gate", "answer_curator": "answer_curator"},
    )
    # adequacy_gate → planner_loop (insufficient) / answer_curator (adequate)
    graph.add_conditional_edges(
        "adequacy_gate", _route_after_adequacy_gate,
        {"planner_loop": "planner_loop", "answer_curator": "answer_curator"},
    )

    # Step 4: answer_curator → answer_agent 직결 제거. direct_response면 save_session 곧장.
    graph.add_conditional_edges(
        "answer_curator", _route_after_answer_curator,
        {"save_session": "save_session", "answer_agent": "answer_agent"},
    )
    graph.add_edge("answer_agent", "critic_agent")
    # Step 2: critic_agent → save_session 직결 제거. publish/repair/clarify/internal_error 분기.
    graph.add_conditional_edges(
        "critic_agent", _route_after_agentic_critic,
        {
            "save_session": "save_session",
            "answer_agent": "answer_agent",  # repair_answer 1회 — answer_agent 내부 repair_attempted 가드로 무한 루프 차단
            "emit_clarification": "emit_clarification",
            "emit_internal_error": "emit_internal_error",
        },
    )
    graph.add_edge("emit_agentic_clarify", "save_session")
    graph.add_edge("emit_clarification", "save_session")
    graph.add_edge("emit_direct_answer", "save_session")
    graph.add_edge("emit_internal_error", "save_session")
    graph.add_edge("save_session", END)

    return graph.compile()
