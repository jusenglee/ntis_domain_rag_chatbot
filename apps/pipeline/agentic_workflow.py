"""단일 Agentic 파이프라인 — Cyclic LangGraph (ADR-0020/0022/0023).

PlannerAgent + ToolExecutor 기반 동적 그래프. 정적 그래프는 폐기됨(ADR-0020).

흐름:
    load_session
      → dialogue_agent  (의도 분류 — direct_answer/clarification은 Planner 없이 즉답 종료)
      → entity_resolver  (person_no/org_id 등 식별자 해소 — 모호 시 clarification 즉답,
                          ask_meta/ask_children은 fast-path 즉답)
      → planner_loop  (LLM이 다음 step 결정 — 충분성 판단의 단일 권한)
        → tool_executor  (call_tool인 경우 → Observation 누적)
            → emit_tool_response  (마지막 obs가 response.* terminal → Publication layer 발행)
            → planner_loop        (그 외 → Planner가 answer/추가도구/unsupported 결정)
        → answer_curator  (answer인 경우 → 누적 observation의 evidence를 EvidenceBundle로, evidence-only)
          → answer_agent → critic_agent → save_session
        → emit_agentic_clarify  (clarify인 경우 → 즉답 종료)
          → save_session

종료 조건:
    - PlannerStep.action ∈ {"answer", "clarify"}
    - plan_state.step_no >= planner.max_steps  (Planner 내부에서 answer로 강제 종료)
    - 동일 ToolCall 반복(loop guard) — answer로 강제 종료

ADR-0023: AdequacyGate(LLM judge) 제거 — 검색 성공 후에도 insufficient 과판정으로
재검색 churn을 유발하던 제2 심판을 폐기하고, 충분성 판단을 Planner로 일원화.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from apps.api.streaming.contracts import AnswerArtifact
from apps.api.streaming.emitter import StreamEvent
from apps.pipeline.agent_state import AgentPipelineState
from apps.pipeline.agent_workflow import (
    AgentPipelineDeps,
    node_emit_clarification,
    node_emit_children_list,
    node_emit_direct_answer,
    node_emit_internal_error,
    node_emit_meta_answer,
    node_load_session,
    node_save_session,
    _make_node_answer,
    _make_node_critic,
    _make_node_dialogue,
    _make_node_entity_resolver,
)
from apps.pipeline.log_helpers import short_id
from apps.pipeline.agents.contracts import (
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
            termination_reason = "answer"
            if step.reason.startswith("max_steps_exceeded"):
                termination_reason = "max_steps"
            elif _planner_answer_is_internal_error(step=step, plan_state=plan_state):
                termination_reason = "error"
            next_state = next_state.with_termination(termination_reason)
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


def _planner_answer_is_internal_error(*, step: PlannerStep, plan_state: PlanState) -> bool:
    """Planner failure must not be published as no_result.

    단, 누적 observation에 성공 검색이 있으면 internal error로 단정하지 않는다 —
    answer_curator가 그 evidence로 답변할 수 있는데 '내부 오류'로 턴을 죽이는 것이
    더 나쁜 결과다 (예: 검색 성공 후 Pass1이 미등록 도구 이름을 내는 경우).
    """
    reason = step.reason or ""
    internal_prefixes = (
        "llm_failure",
        "planner_parse_failure",
        "pass1_unknown_tool",
        "missing_tool_in_call_tool",
    )
    has_ok_obs = any(obs.status == "ok" for obs in plan_state.observations)
    if reason.startswith(internal_prefixes):
        return not has_ok_obs
    if plan_state.observations and not has_ok_obs:
        return True
    return False


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

        # context에 현재 turn 식별자 + session + 라우팅 컨텍스트 주입.
        # entity_resolution / dialogue_kind는 SearchRouter가 컬렉션·전략을 자동 결정할 때 사용.
        # Planner는 이 정보를 args로 전달할 필요 없다 (ADR-0022).
        ctx = deps.tool_executor.context
        ctx.request_id = state.request_id
        ctx.turn_id = state.turn_id
        ctx.conversation_id = state.conversation_id
        ctx.session_state = state.session_state
        ctx.entity_resolution = state.entity_resolution
        ctx.dialogue_kind = state.dialogue_intent.kind if state.dialogue_intent else ""

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
# Node: answer_curator — 누적 observations에서 EvidenceBundle 빌드
# ============================================================================
#
# ADR-0023: AdequacyGate(LLM judge) 제거. 충분성 판단은 Planner 단일 권한.
# ADR-0024: answer_curator는 evidence-only. response.* terminal tool 발행은
#           emit_tool_response(Publication layer)로 분리. tool_executor 라우팅:
#           response.* 종결 → emit_tool_response, 그 외 → planner_loop.

async def node_answer_curator(state: AgentPipelineState) -> Dict[str, Any]:
    """누적 observations에서 답변용 evidence만 선택해 EvidenceBundle을 만든다 (ADR-0024).

    책임 경계 (evidence-only):
        O search.* observations → CanonicalEvidence 선택 → EvidenceBundle (+ SearchResult)
        X 직접응답(response.*) 발행 — emit_tool_response 노드가 담당 (Publication layer)
        X final_answer_text / AnswerArtifact / stream 발행 — Publication layer 전담

    이 노드는 항상 answer_agent로 흐른다. evidence가 0건이면 empty bundle →
    AnswerAgent가 결정적 no_result 메시지를 생성한다.
    """
    plan_state = state.plan_state
    if plan_state is None:
        bundle = EvidenceBundle(view="empty", items=[], groups=[])
        return {"evidence_bundle": bundle}

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


def _select_terminal_response_text(plan_state: "PlanState") -> tuple[Optional[str], Optional[str]]:
    """response.* terminal tool observation → (final_text, kind).

    response.direct_answer / response.unsupported는 evidence가 아니라 **terminal intent
    tool**이다. handler가 result.final_text에 최종 텍스트를 이미 확정해 둔다.
    emit_tool_response(Publication layer)가 이 헬퍼로 텍스트를 꺼내 발행한다.

    PlannerStep.answer_text는 발행하지 않는다 (Planner는 publication authority 아님,
    grounding/citation 검증 우회 — ADR-0024). action=answer는 evidence 기반 흐름으로 간다.
    """
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


# ============================================================================
# Node: emit_tool_response — response.* terminal tool 결과 발행 (Publication layer)
# ============================================================================

async def node_emit_tool_response(state: AgentPipelineState) -> Dict[str, Any]:
    """response.direct_answer / response.unsupported observation을 사용자에게 발행한다.

    ADR-0024: 이전엔 answer_curator가 이 발행까지 했으나(계층 위반), Publication layer
    전담 노드로 분리. final_text는 도구 handler가 이미 확정했으므로 AnswerAgent/Critic을
    거치지 않는다 (이미 최종 텍스트인 응답의 publication path).
    """
    plan_state = state.plan_state
    text, kind = _select_terminal_response_text(plan_state) if plan_state else (None, None)
    if not text:
        # 방어 — 라우터가 response.* ok observation을 확인하고 보냈으므로 도달하면 배선 오류.
        logger.error(
            f"[agentic_trace] req={short_id(state.request_id)} "
            f"[emit_tool_response] terminal_text_missing(배선 오류 — response.* 결과 없음)"
        )
        text = "요청을 처리하지 못했습니다. 다시 시도해 주세요."
        kind = "direct_answer"

    if state.stream_emitter is not None:
        await state.stream_emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=state.request_id,
                content=text,
                model_key=f"agentic_{kind or 'direct_answer'}",
            )
        )
    artifact = AnswerArtifact(
        text=text,
        answer_kind="direct_answer" if kind != "unsupported" else "clarification",
        references=[],
        source_refs=[],
        meta={
            "kind": f"agentic_{kind}",
            "step_count": plan_state.step_no if plan_state else 0,
            "termination_reason": plan_state.termination_reason if plan_state else None,
        },
    )
    kind_kr = {
        "direct_answer": "직접 응답·인사·간단 안내",
        "unsupported": "정직 거절·NTIS 범위 외",
    }.get(kind or "", "알 수 없음")
    logger.info(
        f"[agentic_trace] req={short_id(state.request_id)} "
        f"[emit_tool_response] terminal_response_published "
        f"kind={kind!r}({kind_kr}) chars={len(text)}(답변 길이) "
        f"skip=AnswerAgent+Critic(이미 최종 텍스트)"
    )
    # emit_direct_answer와 동일 패턴 — evidence_bundle 미설정 → save_session이 view=None 처리.
    return {"answer_artifact": artifact, "final_answer_text": text}


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
    # ADR-0022: 새 도구명 + 구 도구명 모두 인식 (하위 호환)
    _EXACT_TOOLS = {"search.detail", "search.exact_lookup"}
    _AGG_TOOLS = {"search.stats", "search.aggregate"}
    _HYBRID_TOOLS = {"search", "search.hybrid"}
    for obs in observations:
        if obs.status != "ok":
            continue
        if obs.tool in _EXACT_TOOLS:
            last_exact = obs
        elif obs.tool in _AGG_TOOLS:
            last_agg = obs
        elif obs.tool in _HYBRID_TOOLS:
            last_hybrid = obs

    if last_exact is not None:
        evs = _renumber_evidences(_deserialize_evidences(last_exact.result.get("evidences") or []))
        if evs:
            view = "single_detail" if len(evs) == 1 else "list_compact"
            return evs, view, last_exact
        # ok-but-empty detail(잘못된 식별자 등)은 무시 — 이전 성공 검색 결과로 fallback.
        # 그대로 쓰면 view=single_detail(0건)이 앞선 목록 evidence를 가린다.
    if last_agg is not None:
        evs = _renumber_evidences(_deserialize_evidences(last_agg.result.get("evidences") or []))
        return evs, "stats_summary", last_agg
    if last_hybrid is not None:
        evs = _renumber_evidences(_deserialize_evidences(last_hybrid.result.get("evidences") or []))
        # 마지막 검색이 0건이면 이전 OK 검색 중 가장 많은 hits를 가진 것으로 fallback.
        # 예: step1=10건(관련), step2=0건(재시도 실패) → step1 결과 사용.
        if not evs:
            best_fallback = max(
                (
                    obs for obs in observations
                    if obs.status == "ok"
                    and obs.tool in _HYBRID_TOOLS
                    and obs is not last_hybrid
                    and (obs.result.get("total_hits") or 0) > 0
                ),
                key=lambda o: o.result.get("total_hits", 0),
                default=None,
            )
            if best_fallback is not None:
                evs = _renumber_evidences(
                    _deserialize_evidences(best_fallback.result.get("evidences") or [])
                )
                last_hybrid = best_fallback
        view = "single_detail" if len(evs) == 1 else "list_compact"
        return evs, view, last_hybrid
    return [], "empty", None


def _renumber_evidences(evs: List[CanonicalEvidence]) -> List[CanonicalEvidence]:
    """snapshot_rank를 1..N으로 재부여 — AnswerAgent 인용 범위 위반 방지.

    Static 그래프의 EvidenceCuratorAgent는 display_rank를 1..N으로 보장했다.
    Agentic 경로는 raw search 결과를 그대로 쓰므로 snapshot_rank가 비연속
    (예: 1,4,7,12,13)일 수 있다. CriticAgent는 max_rank=len(items)로 검증하므로
    [12] 인용이 10건 중 12번 → out_of_range 오류가 발생한다.
    """
    return [
        ev.model_copy(update={"snapshot_rank": i})
        for i, ev in enumerate(evs, start=1)
    ]


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
    """entity_resolver 출력 → planner_loop / fast-path / emit_clarification 분기.

    ask_meta는 검색 없이 세션 캐시(manifest/focused_detail)로 즉답.
    ask_children은 focused_detail 캐시가 실제로 그 항목을 가리킬 때만 즉답 —
    캐시가 없거나 사용자가 다른 manifest 항목을 인용했으면 planner_loop로 보내
    EntityResolver가 해소한 식별자(rst_id 등)로 search.detail이 본문을 가져와 답한다.
    그 외는 PlannerAgent가 동적으로 도구 전략을 결정하는 planner_loop로 진입.
    """
    resolution = state.entity_resolution
    if resolution is None:
        return "emit_internal_error"
    if resolution.clarification_needed:
        return "emit_clarification"
    intent = state.dialogue_intent
    if intent is not None and intent.kind == "ask_meta":
        return "emit_meta_answer"
    if intent is not None and intent.kind == "ask_children":
        if _children_fast_path_available(state):
            return "emit_children_list"
        # 해소된 식별자가 있을 때만 planner_loop — search.detail로 그 항목을 가져올 수 있다.
        ids = resolution.identifiers
        if ids is not None and ids.has_any():
            return "planner_loop"
        # 사용자가 N번을 인용했는데 해소도 실패하고 식별자도 없음 — emit_children_list로
        # 보내면 focused_detail(다른 항목) 캐시로 오답을 확신 발행하므로 되묻기가 정직하다.
        if intent.manifest_rank is not None:
            return "emit_clarification"
        # 인용 없는 명단 요청 + 빈 세션 — 기존 emit_children_list의 안내 메시지가 정답.
        return "emit_children_list"
    return "planner_loop"


def _children_fast_path_available(state: AgentPipelineState) -> bool:
    """ask_children을 focused_detail 캐시로 즉답 가능한지 판정.

    조건 (모두 만족):
        - session_state.focused_detail 존재 (child_entities 캐시 보유 슬롯)
        - 사용자가 manifest_rank를 인용했다면(intent 기준 — 해소 실패해도 인용은 인용),
          해소된 식별자가 focused_detail anchor와 같은 항목이어야 함. 다른 항목이거나
          해소 실패면 캐시 즉답은 오답이므로 라우터가 planner_loop/되묻기로 처리한다.

    항목 동일성 판정은 양쪽 모두 값이 있는 가장 구체적인 축 하나로 결정한다 —
    perf 항목은 부모 과제의 pjt_id를 함께 들고 다닐 수 있어, any-axis 겹침 판정은
    같은 과제의 다른 성과를 같은 항목으로 오판한다 (rst_id가 진실원).
    """
    session = state.session_state
    if session is None or not session.has_focused_detail():
        return False
    intent = state.dialogue_intent
    cited_rank = intent.manifest_rank if intent is not None else None
    resolution = state.entity_resolution
    resolved_rank = resolution.manifest_rank if resolution is not None else None
    if cited_rank is None and resolved_rank is None:
        return True  # "이 항목" anaphora — focused_detail 자신을 가리킴
    if resolved_rank is None:
        # 인용은 있으나 해소 실패(manifest 부재/범위 초과) — 캐시가 그 항목이라는 보장 없음.
        return False
    ids = resolution.identifiers
    if ids is None:
        return False
    anchor = session.focused_detail.anchor
    for axis in ("rst_id", "pjt_id", "pjt_no", "person_no", "org_id"):
        values = getattr(ids, axis, None) or []
        anchor_val = (getattr(anchor, axis, None) or "").strip()
        if anchor_val and values:
            return anchor_val in values
    return False


def _route_after_planner(state: AgentPipelineState) -> str:
    """planner_loop 출력 → 다음 노드 분기."""
    plan_state = state.plan_state
    if plan_state is None:
        return "answer_curator"  # safety fallback
    if plan_state.terminated:
        reason = plan_state.termination_reason
        if reason == "clarify":
            return "emit_agentic_clarify"
        if reason == "error":
            return "emit_internal_error"
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
    """tool_executor → emit_tool_response / planner_loop / answer_curator 결정적 분기.

    충분성 판단은 Planner 단일 권한 (ADR-0023). 이 라우터는 결정적 규칙만 적용:
        - plan_state None / step_no>=50 → answer_curator (안전판)
        - 마지막 observation이 response.direct_answer/response.unsupported + ok
          → emit_tool_response (Publication layer가 terminal 텍스트 발행 — ADR-0024)
        - 그 외(검색 결과·error 포함) → planner_loop
          (Planner가 observation+applied_context 보고 answer/추가도구/unsupported 결정)
    """
    plan_state = state.plan_state
    if plan_state is None or plan_state.step_no >= 50:
        return "answer_curator"
    last_obs = plan_state.last_observation()
    if (
        last_obs is not None
        and last_obs.status == "ok"
        and last_obs.tool in {"response.direct_answer", "response.unsupported"}
    ):
        # ADR-0024: terminal intent tool 결과는 Publication layer 노드가 발행 (curator 아님).
        return "emit_tool_response"
    return "planner_loop"


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
    # DialogueAgent 분류 결과 — Planner가 dialogue_kind를 제약 조건으로 활용한다.
    # ask_search 계열이면 search.* 시도 전 response.unsupported 금지 (ADR-0025 Issue 1).
    if state.dialogue_intent is not None:
        summary["dialogue_kind"] = state.dialogue_intent.kind
    return summary


# ============================================================================
# Graph builder
# ============================================================================

def build_agentic_pipeline_graph(deps: AgentPipelineDeps):
    """단일 Agentic 파이프라인 cyclic LangGraph 빌더 (ADR-0020/0023).

    진입 단:
        load_session → dialogue_agent → entity_resolver → planner_loop
        - dialogue_agent: direct_answer/clarification은 Planner 없이 즉답 종료.
        - entity_resolver: person_no/org_id 등 식별자 해소(모호 시 clarification).
          ask_meta/ask_children은 fast-path 즉답.

    동적 루프(agentic):
        planner_loop ↔ tool_executor (action=call_tool 동안 반복)
        tool_executor → emit_tool_response(response.* terminal) / planner_loop(그 외).
        answer는 answer_curator(evidence-only) → answer_agent → critic_agent → save_session.
        clarify는 emit_agentic_clarify로 즉답 종료.

    충분성 판단은 Planner 단일 권한 (ADR-0023: AdequacyGate 제거).
    publication 계층 분리 (ADR-0024): curator=evidence, emit_*=최종 텍스트 발행.
    종료: planner가 answer/clarify 결정, max_steps, duplicate_call 가드.
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
    # ADR-0023: adequacy_gate 노드 제거. 충분성 판단은 Planner 단일 권한.
    graph.add_node("answer_curator", node_answer_curator)
    graph.add_node("answer_agent", _make_node_answer(deps))
    graph.add_node("critic_agent", _make_node_critic(deps))
    graph.add_node("emit_agentic_clarify", node_emit_agentic_clarify)
    graph.add_node("emit_clarification", node_emit_clarification)
    graph.add_node("emit_internal_error", node_emit_internal_error)
    graph.add_node("emit_direct_answer", node_emit_direct_answer)
    # ADR-0024: response.* terminal tool 결과 발행 (Publication layer, curator에서 분리).
    graph.add_node("emit_tool_response", node_emit_tool_response)
    # ask_meta / ask_children fast-path — 검색 없이 세션 캐시(manifest/focused_detail)로 즉답.
    graph.add_node("emit_meta_answer", node_emit_meta_answer)
    graph.add_node("emit_children_list", node_emit_children_list)
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
    # 엔티티 해소 단: ask_meta/ask_children은 fast-path, 모호성은 되묻기, 그 외는 planner_loop.
    graph.add_conditional_edges(
        "entity_resolver", _route_after_entity_resolver,
        {
            "planner_loop": "planner_loop",
            "emit_meta_answer": "emit_meta_answer",
            "emit_children_list": "emit_children_list",
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
            "emit_internal_error": "emit_internal_error",
        },
    )
    # tool_executor → emit_tool_response(response.* 종결) / planner_loop(그 외) / answer_curator(안전판).
    graph.add_conditional_edges(
        "tool_executor", _route_after_tool_executor,
        {
            "emit_tool_response": "emit_tool_response",
            "planner_loop": "planner_loop",
            "answer_curator": "answer_curator",
        },
    )

    # ADR-0024: answer_curator는 evidence-only — 항상 answer_agent로 (직접응답 발행 분기 제거).
    graph.add_edge("answer_curator", "answer_agent")
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
    graph.add_edge("emit_tool_response", "save_session")
    graph.add_edge("emit_meta_answer", "save_session")
    graph.add_edge("emit_children_list", "save_session")
    graph.add_edge("emit_internal_error", "save_session")
    graph.add_edge("save_session", END)

    return graph.compile()
