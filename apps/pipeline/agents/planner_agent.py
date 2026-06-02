"""Phase 2 — PlannerAgent (ReAct 패턴 LLM driver).

PlannerAgent의 책임:
    - tool catalog + 사용자 질문 + 누적 observations를 보고 **다음 한 step**을 결정.
    - LLM 호출 1회로 PlannerStep 1개 발행.
    - 종료 결정도 LLM이 함 (action=answer/clarify). max_steps는 시스템 안전망.

설계 노트:
    - decide_next는 PlanState 1개를 받아 PlannerStep 1개를 반환 — loop 자체는 호출자(Phase 3
      LangGraph)가 운영.
    - parse 실패 / LLM 예외 → PlannerStep(action="answer", reason="...")로 안전 fallback
      (loop을 무한으로 만들지 않음).
    - prompt에 tool catalog는 ToolSpec.name/description/input_schema(축약)만 노출. JSON Schema
      전체는 too verbose라 description + input properties 이름·타입만.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.pipeline.agents.planner_contracts import PlannerStep, PlanState
from apps.pipeline.tools.contracts import Observation, ToolSpec


_DEFAULT_MAX_STEPS = 8


def _planner_thinking_enabled() -> bool:
    """Solar 102B의 thinking 모드를 PlannerAgent 호출에 활성화할지 — 기본 True.

    판단 품질이 중요한 호출(능동 도구 선택·결과 적합성 판단)에만 thinking 모드를 켜고,
    DialogueAgent의 분류·NER에는 끈다 (latency 비용 절약 + 분류 작업은 thinking 효과 미미).

    환경변수 RAG_PLANNER_THINKING_ENABLED으로 즉시 토글 — 운영 회귀 시 false로 우회.
    """
    raw = os.environ.get("RAG_PLANNER_THINKING_ENABLED", "true").strip().lower()
    return raw not in {"", "0", "false", "no", "off"}


class PlannerAgent:
    """LLM-driven ReAct planner. 한 step씩 결정한다."""

    def __init__(self, *, llm: Any, max_steps: int = _DEFAULT_MAX_STEPS) -> None:
        self._llm = llm
        self._max_steps = max_steps
        self._thinking_enabled = _planner_thinking_enabled()

    @property
    def max_steps(self) -> int:
        return self._max_steps

    async def decide_next(
        self,
        *,
        plan_state: PlanState,
        tool_specs: List[ToolSpec],
        request_id: str = "",
        conversation_id: str = "",
        session_summary: Optional[Dict[str, Any]] = None,
    ) -> PlannerStep:
        """현재 PlanState를 보고 다음 PlannerStep 결정. LLM 호출 1회.

        Args:
            plan_state: 누적 step/observation.
            tool_specs: PlannerAgent가 호출 가능한 도구 목록 (ToolExecutor.specs()).
            session_summary: 직전 turn 상태 요약 (subject/manifest/focused_detail 존재 여부 등).

        Returns:
            PlannerStep — call_tool / answer / clarify 중 하나.
        """
        if plan_state.step_no >= self._max_steps:
            return PlannerStep(
                action="answer",
                reason=f"max_steps_exceeded(step_no={plan_state.step_no})",
                confidence=0.3,
            )

        # ── Pass 1 — action + tool 선택 (args 제외) ──
        try:
            pass1 = await self._pass1_decide_action(
                plan_state=plan_state,
                tool_specs=tool_specs,
                session_summary=session_summary or {},
                request_id=request_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[PlannerAgent] pass1 llm_invoke failed: {exc} — defaulting to answer")
            return PlannerStep(
                action="answer",
                reason=f"llm_failure:{exc}",
                confidence=0.1,
            )

        # answer / clarify는 Pass 2 불필요 — 즉시 종료
        if pass1.action in ("answer", "clarify"):
            return pass1

        # ── Pass 2 — 선택된 도구의 args 구성 ──
        tool_spec = next((s for s in tool_specs if s.name == pass1.tool), None)
        if tool_spec is None:
            logger.warning(
                f"[PlannerAgent] pass1 selected unknown tool={pass1.tool!r} — defaulting to answer"
            )
            return PlannerStep(
                action="answer",
                reason=f"pass1_unknown_tool:{pass1.tool!r}",
                confidence=0.1,
            )

        try:
            args = await self._pass2_compose_args(
                tool_spec=tool_spec,
                plan_state=plan_state,
                session_summary=session_summary or {},
                request_id=request_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[PlannerAgent] pass2 llm_invoke failed tool={pass1.tool!r} err={exc} — empty args"
            )
            args = {}

        return PlannerStep(
            action="call_tool",
            tool=pass1.tool,
            args=args,
            reason=pass1.reason,
            confidence=pass1.confidence,
        )

    # ------------------------------------------------------------------
    # Pass 1 — action + tool 선택 (LLM 호출 1회)
    # ------------------------------------------------------------------

    async def _pass1_decide_action(
        self,
        *,
        plan_state: PlanState,
        tool_specs: List[ToolSpec],
        session_summary: Dict[str, Any],
        request_id: str,
        conversation_id: str,
    ) -> PlannerStep:
        system_prompt = _build_pass1_action_prompt(tool_specs=tool_specs)
        user_payload = _build_planner_user_payload(
            plan_state=plan_state,
            session_summary=session_summary,
        )
        # step이 쌓일수록 thinking 강도를 낮춰 타임아웃 방지.
        # step 1: medium (첫 판단 — 가장 중요)
        # step 2+: low (observation 보고 판단 — 컨텍스트 큼)
        obs_count = len(plan_state.observations)
        effort = "low" if obs_count >= 1 else "medium"
        thinking_kwargs: Dict[str, Any] = {}
        if self._thinking_enabled:
            thinking_kwargs = {
                "disable_thinking": False,
                "reasoning_effort": effort,
                "include_reasoning": False,
            }
        logger.info(
            f"[agentic_trace] [Planner Pass1] LLM_invoke_start "
            f"step={plan_state.step_no + 1} "
            f"observations_acc={obs_count}(누적 도구 결과 건수) "
            f"thinking={'on' if self._thinking_enabled else 'off'}(생각모드) "
            f"effort={effort}(추론 강도) "
            f"role=action_and_tool_selection(액션·도구 선택)"
        )
        response = await self._llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            request_id=request_id,
            conversation_id=conversation_id,
            temperature=0.0,
            top_p=1.0,
            max_tokens=4096 if self._thinking_enabled else 384,
            **thinking_kwargs,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[agentic_trace] [Planner Pass1] parse_failure(JSON 파싱 실패) "
                f"fallback=answer(안전 종료) raw_preview={raw[:120]!r}"
            )
            return PlannerStep(
                action="answer",
                reason="planner_parse_failure",
                confidence=0.1,
            )
        step = _build_step_from_llm(parsed, tool_specs=tool_specs)
        # action 한국어 라벨
        action_kr = {
            "call_tool": "도구 호출",
            "answer": "답변 진입",
            "clarify": "사용자 되묻기",
        }.get(step.action, "알 수 없음")
        logger.info(
            f"[agentic_trace] [Planner Pass1] decision_ready "
            f"action={step.action}({action_kr}) "
            f"tool={step.tool!r}(선택 도구) "
            f"reason={step.reason[:80]!r}(이유) "
            f"confidence={step.confidence:.2f}(신뢰도)"
        )
        return step

    # ------------------------------------------------------------------
    # Pass 2 — 선택된 도구의 args 구성 (LLM 호출 1회)
    # ------------------------------------------------------------------

    async def _pass2_compose_args(
        self,
        *,
        tool_spec: ToolSpec,
        plan_state: PlanState,
        session_summary: Dict[str, Any],
        request_id: str,
        conversation_id: str,
    ) -> Dict[str, Any]:
        system_prompt = _build_pass2_args_prompt(tool_spec=tool_spec)
        user_payload = _build_planner_user_payload(
            plan_state=plan_state,
            session_summary=session_summary,
        )
        logger.info(
            f"[agentic_trace] [Planner Pass2] LLM_invoke_start "
            f"tool={tool_spec.name!r}(args 채울 도구) "
            f"role=args_composition_with_NER(args 구성+NER) "
            f"thinking=off(생각모드 끔, 비용 절약)"
        )
        # Pass 2는 NER + schema 채움 — thinking 효과 미미, 비용만 ↑. 끄는 게 효율적.
        response = await self._llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            request_id=request_id,
            conversation_id=conversation_id,
            temperature=0.0,
            top_p=1.0,
            max_tokens=512,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[agentic_trace] [Planner Pass2] parse_failure(args JSON 파싱 실패) "
                f"tool={tool_spec.name!r} fallback=empty_args(빈 args로 진행) "
                f"raw_preview={raw[:120]!r}"
            )
            return {}
        # Pass 2 schema: {"args": {...}} 또는 직접 args dict 둘 다 수용
        if "args" in parsed and isinstance(parsed["args"], dict):
            args_out = parsed["args"]
        else:
            args_out = {k: v for k, v in parsed.items() if k not in _SCHEMA_KEYS}
        # 핵심 args 값을 미리보기 (긴 값은 60자로 자름)
        preview_pairs: List[str] = []
        for k, v in list(args_out.items())[:5]:
            v_str = str(v)
            if len(v_str) > 60:
                v_str = v_str[:60] + "..."
            preview_pairs.append(f"{k}={v_str}")
        preview = ", ".join(preview_pairs) if preview_pairs else "empty(빈 args)"
        logger.info(
            f"[agentic_trace] [Planner Pass2] args_ready(args 구성 완료) "
            f"tool={tool_spec.name!r} args_preview={{{preview}}}"
        )
        return args_out


# ============================================================================
# Prompt builders
# ============================================================================

_PLANNER_SYSTEM_HEADER = (
    "당신은 **사용자와 자율적으로 상호작용하는 대화 에이전트**입니다.\n"
    "당신은 단일 도메인 챗봇이 아니라, 사용자 발화를 이해하고 필요한 도구를 골라 답하는 자율 판단자입니다.\n"
    "\n"
    "[현재 가용 도구 환경 — MCP 스타일 외부 도구]\n"
    "현 시점에 연결된 외부 도구는 **NTIS(국가과학기술지식정보서비스) Qdrant 벡터 DB** 하나입니다.\n"
    "  - 이 DB는 국가 R&D 과제·성과·참여인력 활동 정보를 보유.\n"
    "  - search.* / lookup.* / manifest.* 도구는 모두 이 NTIS DB를 가져다 쓰는 인터페이스.\n"
    "  - 향후 다른 외부 도구(다른 DB·API·계산기 등)가 catalog에 추가될 수 있으며, 그때도 동일한 판단 흐름.\n"
    "**NTIS는 '필요할 때 가져다 쓰는 참고 도서관'일 뿐, 챗봇의 정체성이 아닙니다.**\n"
    "사용자 질문이 NTIS 데이터로 풀리지 않아도 — direct_answer로 직접 답하거나, unsupported로 정직 거절하거나,\n"
    "clarify로 되물을 수 있습니다. 모든 발화에 NTIS 검색을 강제하지 마세요.\n"
    "\n"
    "사용자 질문에 답하기 위해 **단 한 step만** 결정합니다. ReAct 패턴:\n"
    "  - 현재까지의 observations(이전 step 도구 결과)를 보고\n"
    "  - 다음에 어떤 도구를 호출할지 또는 답변/되묻기로 진입할지 결정\n\n"
    "도구 호출 시: action='call_tool', tool, args를 채웁니다.\n"
    "답변 가능하면: action='answer' (텍스트는 후속 AnswerAgent가 evidence 기반으로 생성하므로 보통 비웁니다).\n"
    "정보 부족: action='clarify', clarification_question 채웁니다.\n\n"
    "[출력 schema — 단일 JSON 객체]\n"
    "{\n"
    '  "action": "call_tool"|"answer"|"clarify",\n'
    '  "tool": "<도구 이름>"|null,\n'
    '  "args": {<도구별 args>},\n'
    '  "answer_text": null,\n'
    '  "clarification_question": "<되묻기 문구>"|null,\n'
    '  "reason": "<짧은 판정 이유>",\n'
    '  "confidence": 0.0~1.0\n'
    "}\n\n"
    "[NTIS R&D 데이터 — 가용 범위]\n"
    "현재 연결된 외부 도구는 NTIS(국가과학기술지식정보서비스) R&D 데이터베이스 하나다.\n"
    "  - 국가 R&D 과제: 사업명·기간·수행기관·연구진·총사업비·목표·요약\n"
    "  - 성과: 논문·특허·SW·보고서·장비·생명자원·신품종 등\n"
    "  - 참여인력 활동내역, 참여기관\n"
    "search 도구는 query만 넣으면 컬렉션·전략·필터를 내부 자동 결정한다. "
    "target/collection/perf_type 같은 DB 내부 개념을 Planner가 알 필요 없다.\n\n"
    "[자율 판단 원칙 — 탐색 우선]\n"
    "1. **인사·잡담·간단 메타 응답** → response.direct_answer (검색 불필요).\n"
    "2. **그 외 모든 질문** → 먼저 search 도구로 시도한다.\n"
    "   subject·도메인 힌트·식별자가 없어도 무방. query 텍스트만으로 검색 가능.\n"
    "   '이 질문이 NTIS에 있을까?'를 사전에 판단하지 말고 결과로 확인한다.\n"
    "3. **결과 적합성**: 검색 결과를 보고 판단한다.\n"
    "   - 충분히 적합 → action='answer'로 종료.\n"
    "   - 0건 또는 무관 → 다른 query·도구·args로 재시도.\n"
    "   - 재시도 후에도 무관 → response.unsupported.\n"
    "4. **response.unsupported는 마지막 수단** — 검색을 시도한 뒤에만 사용한다.\n"
    "   사전 판단으로 거절하지 않는다. 단, 아래는 즉시 거절 가능:\n"
    "   - 주가·날씨·실시간 뉴스·금융 데이터 (NTIS와 무관한 외부 사실)\n"
    "   - 사용자 주입 사실 ('기억해둬', '~라고 알아둬') — 저장 능력 없음\n"
    "5. 같은 도구를 같은 args로 두 번 호출하지 마세요 (loop guard로 차단됨).\n"
    "6. observations에서 status='error'가 보이면 다른 도구·args로 재시도.\n"
    "7. 검증이 필요한 인물·기관 이름은 lookup.* 도구로 NTIS 존재 확인 후 search.*에 전달.\n"
    "8. manifest_rank·focused_detail 직전 turn 인용은 manifest.* 도구로 식별자 매핑 후 exact_lookup.\n\n"
    "[dialogue_kind 제약 — DialogueAgent 분류 결과 준수]\n"
    "session.dialogue_kind가 있으면 DialogueAgent가 이미 의도를 분류한 결과다.\n"
    "이 값이 다음 중 하나이면 search / search.detail / search.stats / lookup.* / manifest.* 도구를 최소 1회 호출하기 전에\n"
    "response.unsupported 호출을 금지한다:\n"
    "  ask_search, ask_detail, stats, compare, refine_previous,\n"
    "  ask_similar, ask_meta, ask_children\n"
    "검색 결과가 0건이거나 완전히 무관한 경우에만 그때 response.unsupported를 사용한다.\n"
    "session.dialogue_kind가 없거나 direct_answer / clarification이면 자율 판단한다.\n\n"
    "[절대 규칙]\n"
    "- 응답은 반드시 단일 JSON 객체. 마크다운/주석/추가 텍스트 금지.\n"
    "- 도구 이름·args는 아래 catalog와 정확히 일치해야 합니다.\n"
    "- 자율 판단 — NTIS 검색을 강제하지 말고 자기 능력·도구 가용성 인지 후 도구 선택.\n"
    "- **action 필드에는 도구 이름을 절대 직접 박지 마세요.** action은 항상 'call_tool'·'answer'·'clarify' 중 하나.\n"
    "  도구 이름은 tool 필드에 들어갑니다. args는 args 필드 dict에.\n"
    "  올바른 예 (response.unsupported 호출):\n"
    '    {"action":"call_tool","tool":"response.unsupported","args":{"reason":"NTIS DB에 인사 정보 없음","examples":["..."]},"reason":"out of available tools","confidence":0.9}\n'
    "  잘못된 예 (action에 도구 이름 박음, 절대 금지):\n"
    '    {"action":"response.unsupported","reason":"...","examples":[...]}  ← 시스템이 정정하지만 비효율.\n'
)


def _build_planner_system_prompt(*, tool_specs: List[ToolSpec]) -> str:
    """**Deprecated 호환 wrapper** — 회귀 가드(prompt 문자열 매칭) 통과용.

    2026-05-27: 실제 Planner는 2-pass로 분리됨:
        - `_build_pass1_action_prompt`: action+tool 선택 (catalog 슬림, args 명세 제외).
        - `_build_pass2_args_prompt`: 도구별 args schema만 노출.
    이 wrapper는 둘을 합쳐 회귀 가드용 문자열만 보존한다.
    """
    pass1 = _build_pass1_action_prompt(tool_specs=tool_specs)
    sample_tool = next(iter(tool_specs), None)
    pass2 = _build_pass2_args_prompt(tool_spec=sample_tool) if sample_tool else ""
    return pass1 + "\n\n# === Pass 2 (도구별 args 구성) — 호환 view ===\n\n" + pass2


def _build_pass1_action_prompt(*, tool_specs: List[ToolSpec]) -> str:
    """Pass 1 prompt — action + tool 선택만. args 명세 일체 노출 안 함.

    LLM 책임을 줄여 schema 위반·NER spillover를 차단한다.
    """
    catalog_lines: List[str] = ["[도구 catalog — 이름·설명만]"]
    for spec in tool_specs:
        precon = f"  (전제: {', '.join(spec.preconditions)})" if spec.preconditions else ""
        catalog_lines.append(
            f"  - {spec.name} (cost={spec.cost_hint}){precon}\n"
            f"      {spec.description}"
        )
    catalog = "\n".join(catalog_lines)
    return _PLANNER_SYSTEM_HEADER + "\n" + catalog + (
        "\n\n"
        "[Pass 1 출력 schema — 단일 JSON 객체, args 필드 없음]\n"
        "{\n"
        '  "action": "call_tool"|"answer"|"clarify",\n'
        '  "tool":   "<도구 이름>"|null,           // action=call_tool일 때 필수, 그 외 null\n'
        '  "answer_text": null,                    // 보통 비움 (후속 단이 채움)\n'
        '  "clarification_question": null,         // action=clarify일 때만\n'
        '  "reason": "<짧은 판정 이유>",\n'
        '  "confidence": 0.0~1.0\n'
        "}\n"
        "**args는 이 단에서 채우지 않습니다.** 도구를 선택한 뒤 다음 단이 그 도구의 schema에 따라 args를 채웁니다.\n"
    )


def _build_pass2_args_prompt(*, tool_spec: ToolSpec) -> str:
    """Pass 2 prompt — 선택된 도구의 args schema만 노출. kind별 특화.

    Pass 1이 도구 이름 결정 후 이 prompt로 args를 채운다. NER·필터 추출이 이 단에서 일어남.
    LLM에 노출되는 catalog가 1개 도구만 → schema 정확도 ↑.
    """
    input_props = (tool_spec.input_schema or {}).get("properties") or {}
    required = (tool_spec.input_schema or {}).get("required") or []

    lines: List[str] = []
    lines.append(
        f"당신은 NTIS RAG 챗봇의 **도구 args 구성기**입니다. 선택된 도구의 args만 JSON으로 출력합니다."
    )
    lines.append("")
    lines.append(f"[선택된 도구: {tool_spec.name}]")
    lines.append(f"  설명: {tool_spec.description}")
    if tool_spec.preconditions:
        lines.append(f"  전제: {', '.join(tool_spec.preconditions)}")
    lines.append("")
    lines.append("[args schema]")
    for prop_name, prop_meta in input_props.items():
        if not isinstance(prop_meta, dict):
            continue
        prop_type = prop_meta.get("type", "")
        req_mark = " (필수)" if prop_name in required else ""
        desc = prop_meta.get("description") or ""
        enum_vals = prop_meta.get("enum") or []
        line = f"  - {prop_name}: {prop_type}{req_mark}"
        if enum_vals:
            line += f" ({' | '.join(str(v) for v in enum_vals)})"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    lines.append("")
    lines.append("[출력 schema — 단일 JSON 객체]")
    lines.append("{")
    lines.append('  "args": {<도구 schema에 맞춘 args>}')
    lines.append("}")
    lines.append("")
    lines.append("[NER 규칙]")
    lines.append("- 사용자 발화의 인물·기관·연도·식별자는 args의 적절한 필드에 채웁니다.")
    lines.append("- session.entity_resolution에 확정된 식별자(subject.person_no/org_id, identifiers.*)가 있으면, "
                 "raw 이름·텍스트 대신 그 확정값을 해당 args 필드(person_no/org_id/pjt_id 등)에 우선 사용합니다.")
    lines.append("- 기술 약어·일반명사(LLM, AI, 빅데이터, ML 등)는 subject_name·perf_type 같은 식별자 필드에 박지 마세요.")
    lines.append("- 사용자가 명시 안 한 값은 null·빈 배열로 둡니다.")
    lines.append("")
    lines.append("[절대 규칙]")
    lines.append("- 응답은 반드시 단일 JSON 객체로 {\"args\": {...}}.")
    lines.append("- 마크다운/주석/추가 텍스트 금지.")
    lines.append("- args 외 필드 출력 금지.")
    return "\n".join(lines)


def _build_planner_user_payload(
    *,
    plan_state: PlanState,
    session_summary: Dict[str, Any],
) -> str:
    payload: Dict[str, Any] = {
        "question": plan_state.question,
        "step_no": plan_state.step_no,
        "session": session_summary,
    }
    if plan_state.decisions:
        payload["previous_decisions"] = [
            {
                "step": i + 1,
                "action": d.action,
                "tool": d.tool,
                "reason": d.reason[:150],
            }
            for i, d in enumerate(plan_state.decisions)
        ]
    if plan_state.observations:
        payload["previous_observations"] = [
            _summarize_observation(o) for o in plan_state.observations
        ]
    return json.dumps(payload, ensure_ascii=False)


def _summarize_observation(obs: Observation) -> Dict[str, Any]:
    """관측 결과를 prompt에 노출할 압축 형태로 변환 — 큰 evidence는 핵심만."""
    summary: Dict[str, Any] = {
        "tool": obs.tool,
        "status": obs.status,
        "latency_ms": round(obs.latency_ms, 1),
    }
    if obs.status == "error":
        summary["error_code"] = obs.error_code
        summary["error_message"] = (obs.error_message or "")[:200]
        return summary
    res = obs.result or {}
    # 검색 도구 결과
    if "evidences" in res:
        evs = res.get("evidences") or []
        summary["evidence_count"] = len(evs)
        summary["search_status"] = res.get("status")
        summary["total_hits"] = res.get("total_hits", 0)
        # 처음 3건의 title + identity만
        summary["evidences_preview"] = [
            {
                "rank": ev.get("snapshot_rank"),
                "title": (ev.get("title") or "")[:80],
                "identity": ev.get("identity"),
            }
            for ev in evs[:3]
        ]
        # 검색 실행 컨텍스트 — Planner가 0건 원인 진단 및 재시도 전략 결정에 활용
        if "applied_context" in res:
            ac = res["applied_context"]
            summary["searched_collections"] = ac.get("collections_searched", [])
            if ac.get("multi_collection"):
                summary["multi_collection_search"] = True
            if ac.get("subject_anchor"):
                summary["subject_anchor_used"] = ac["subject_anchor"]
            if ac.get("filters_applied"):
                summary["filters_applied"] = ac["filters_applied"]
        return summary
    # lookup 도구 결과
    if "hits" in res:
        hits = res.get("hits") or []
        summary["hit_count"] = res.get("hit_count", len(hits))
        summary["hits_preview"] = [
            {k: v for k, v in hit.items() if k in {"person_no", "org_id", "display_name", "affiliation"}}
            for hit in hits[:3]
        ]
        return summary
    # manifest 도구 결과
    if "identifiers" in res:
        summary["target"] = res.get("target")
        summary["identifiers"] = res.get("identifiers")
        if "item_count" in res:
            summary["item_count"] = res.get("item_count")
        if "title" in res:
            summary["title"] = (res.get("title") or "")[:80]
        return summary
    # generic fallback — 키 노출만
    summary["result_keys"] = list(res.keys())[:10]
    return summary


# ============================================================================
# JSON / Step parsing
# ============================================================================

def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_stripped, re.DOTALL)
    candidates: List[str] = []
    if fence:
        candidates.append(fence.group(1))
    candidates.append(raw_stripped)
    # 본문에서 첫 { ... } 추출
    brace = re.search(r"\{.*\}", raw_stripped, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


_SCHEMA_KEYS = {
    "action", "tool", "args",
    "answer_text", "clarification_question", "clarification_options",
    "reason", "confidence",
}


def _coerce_action_field(
    parsed: Dict[str, Any], tool_specs: Optional[List[ToolSpec]],
) -> Dict[str, Any]:
    """LLM이 schema를 부분 위반해도 정정.

    관찰된 패턴 (2026-05-27 운영 로그):
        LLM이 {"action": "response.unsupported", "reason": "...", "examples": [...]}로 출력
        → schema 위반. 우리는 action ∈ {call_tool, answer, clarify} + tool 별도.

    정정 규칙:
        1. parsed["action"]이 도구 카탈로그 이름과 정확 일치하면 action="call_tool",
           tool=<원래 action>으로 자동 변환.
        2. args 필드가 비어 있으면 parsed에서 schema 외 키(reason/confidence/tool/args/answer_text/
           clarification_* 제외)를 모아 args로 채움. 단 reason은 PlannerStep.reason에도 유지.
    """
    action_raw = str(parsed.get("action") or "").strip()
    if not action_raw:
        return parsed
    tool_names = {s.name for s in (tool_specs or [])}
    if action_raw in tool_names:
        # 도구 이름이 action에 박힘 — 정정
        new_parsed = dict(parsed)
        new_parsed["action"] = "call_tool"
        new_parsed.setdefault("tool", action_raw)
        # args가 비어 있으면 schema 외 키들을 args로 흡수
        existing_args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        if not existing_args:
            extra = {
                k: v for k, v in parsed.items()
                if k not in _SCHEMA_KEYS and v is not None
            }
            if extra:
                new_parsed["args"] = extra
        logger.info(
            f"[PlannerAgent] coerced_action_to_call_tool tool={action_raw!r} "
            f"args_keys={list((new_parsed.get('args') or {}).keys())}"
        )
        return new_parsed
    return parsed


def _build_step_from_llm(
    parsed: Dict[str, Any], tool_specs: Optional[List[ToolSpec]] = None,
) -> PlannerStep:
    """LLM dict → PlannerStep. 누락·이상값은 안전 기본값으로.

    2026-05-27: tool_specs를 받아 LLM의 schema 부분 위반(action에 도구 이름 직접 박기)을 정정.
    """
    parsed = _coerce_action_field(parsed, tool_specs)
    action_raw = str(parsed.get("action") or "").strip().lower()
    if action_raw not in {"call_tool", "answer", "clarify"}:
        # 안전 기본값 — answer로 종료
        return PlannerStep(
            action="answer",
            reason=f"unknown_action:{action_raw!r}",
            confidence=0.2,
        )
    action: Any = action_raw

    tool = parsed.get("tool")
    tool = str(tool).strip() if tool else None
    args = parsed.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    clarification_question = parsed.get("clarification_question")
    clarification_question = (
        str(clarification_question).strip() if clarification_question else None
    )
    answer_text = parsed.get("answer_text")
    answer_text = str(answer_text).strip() if answer_text else None

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    reason = str(parsed.get("reason") or "")[:200]

    # action별 필수 필드 안전망
    if action == "call_tool" and not tool:
        return PlannerStep(
            action="answer", reason="missing_tool_in_call_tool", confidence=0.2,
        )
    if action == "clarify" and not clarification_question:
        clarification_question = "질문 의도를 좀 더 구체적으로 알려주실 수 있나요?"

    return PlannerStep(
        action=action,
        tool=tool,
        args=args,
        answer_text=answer_text,
        clarification_question=clarification_question,
        reason=reason,
        confidence=confidence,
    )
