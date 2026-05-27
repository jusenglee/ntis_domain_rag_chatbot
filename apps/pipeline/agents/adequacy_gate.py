"""Phase 5 (c1) — Adequacy Gate.

Planner가 도구 선택에 집중할 수 있도록 '누적 observation이 사용자 질문에 답할 수 있는가'를
별도 LLM judge로 분리. tool_executor → adequacy_gate → planner_loop(or answer_curator).

설계 원칙:
    - 결정적 단계 우선: response.* obs는 자동 adequate(이미 답 있음), 마지막 obs status=error는
      자동 insufficient(재시도 필요). 그 외엔 LLM judge.
    - LLM 부담 최소화: 도구 결과 요약만 전달, 원본 evidence 안 보냄. judge prompt 1KB 이내.
    - 안전 fallback: judge 실패 시 'insufficient' → Planner 복귀로 안전.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from apps.pipeline.agents.planner_contracts import PlanState
from apps.pipeline.tools.contracts import Observation


def _adequacy_thinking_enabled() -> bool:
    """Adequacy Gate LLM judge에 thinking 모드 — 기본 false (작은 판정, 비용 절약).
    필요 시 RAG_ADEQUACY_THINKING_ENABLED=true로 토글."""
    raw = os.environ.get("RAG_ADEQUACY_THINKING_ENABLED", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ============================================================================
# AdequacyVerdict
# ============================================================================

AdequacyVerdictKind = Literal["adequate", "insufficient", "error"]


class AdequacyVerdict(BaseModel):
    """Adequacy Gate 판정 결과."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: AdequacyVerdictKind
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # 결정적/LLM 분기 구분 — 운영 모니터링용
    source: Literal["deterministic", "llm_judge", "fallback"] = "llm_judge"


# ============================================================================
# AdequacyGate
# ============================================================================

_ADEQUACY_SYSTEM_PROMPT = (
    "당신은 자율 대화 에이전트의 **Adequacy Gate(결과 적합성 판정기)**입니다. "
    "이 에이전트는 단일 도메인 챗봇이 아니라, 필요할 때 외부 도구(현재는 NTIS Qdrant 벡터 DB)를 가져다 쓰는 자율 판단자입니다.\n"
    "당신의 역할은 사용자 질문과 지금까지 누적된 도구 호출 결과 요약을 보고, **충분히 답할 수 있는지**만 판정하는 것.\n"
    "\n"
    "[판정 기준]\n"
    "  - adequate    : evidence·정보가 사용자 질문에 핵심을 충분히 답할 수 있음. 즉 answer로 진입해도 됨.\n"
    "  - insufficient: evidence가 부족·무관·오류. 추가 도구 호출(다른 검색·필터·거절)이 필요.\n"
    "\n"
    "[원칙]\n"
    "1. 사용자 질문이 현재 도구로 풀 수 없는 영역이라도 에이전트가 response.unsupported로 정직 거절했으면 adequate(답이 이미 있음).\n"
    "2. 인사·잡담·간단 안내에 response.direct_answer로 직접 답했으면 adequate.\n"
    "3. 검색이 0건이거나 evidence 제목·요약이 사용자 질문과 무관하면 insufficient.\n"
    "4. evidence가 1건 이상 의미적으로 매칭되면 adequate.\n"
    "5. 모호하면 insufficient (Planner가 1 step 더 시도 가능).\n"
    "\n"
    "[출력 schema — 단일 JSON]\n"
    '  {"verdict": "adequate"|"insufficient", "reason": "<2문장 이내>", "confidence": 0.0~1.0}\n'
    "\n"
    "[절대 규칙]\n"
    "- 응답은 단일 JSON 객체. 마크다운/주석 금지.\n"
    "- evidence 본문을 인용·복원하지 마세요. 판정만.\n"
)


class AdequacyGate:
    """LLM judge로 plan_state가 답할 수 있는지 판정.

    의존성:
        llm: Solar 등 ainvoke 지원 ChatModel.
    """

    def __init__(self, *, llm: Any) -> None:
        self._llm = llm
        self._thinking_enabled = _adequacy_thinking_enabled()

    async def check(
        self,
        *,
        question: str,
        plan_state: PlanState,
        request_id: str = "",
        conversation_id: str = "",
    ) -> AdequacyVerdict:
        """plan_state 평가.

        우선순위:
            1. plan_state.observations가 비어 있으면 insufficient (도구 호출 한 번도 안 됨).
            2. 마지막 obs가 response.* + status=ok → adequate (결정적).
            3. 마지막 obs가 status=error → insufficient (결정적).
            4. 그 외 → LLM judge.
        """
        observations = list(plan_state.observations or [])
        if not observations:
            return AdequacyVerdict(
                verdict="insufficient", reason="no_observations",
                confidence=1.0, source="deterministic",
            )
        last = observations[-1]
        if last.status == "error":
            return AdequacyVerdict(
                verdict="insufficient",
                reason=f"last_obs_error:{last.error_code}",
                confidence=0.9, source="deterministic",
            )
        if last.tool in {"response.direct_answer", "response.unsupported"} and last.status == "ok":
            return AdequacyVerdict(
                verdict="adequate", reason="response_tool_already_emitted",
                confidence=0.95, source="deterministic",
            )

        # LLM judge
        try:
            verdict = await self._llm_judge(
                question=question, observations=observations,
                request_id=request_id, conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[AdequacyGate] llm_judge_failed(LLM 판정 실패) error={exc} "
                f"fallback=insufficient(추가 도구 호출로 안전 진행)"
            )
            return AdequacyVerdict(
                verdict="insufficient", reason=f"llm_error:{exc}",
                confidence=0.1, source="fallback",
            )
        return verdict

    async def _llm_judge(
        self,
        *,
        question: str,
        observations: List[Observation],
        request_id: str,
        conversation_id: str,
    ) -> AdequacyVerdict:
        user_payload = _build_judge_user_payload(question=question, observations=observations)
        thinking_kwargs: Dict[str, Any] = {}
        if self._thinking_enabled:
            thinking_kwargs = {
                "disable_thinking": False,
                "reasoning_effort": "medium",
                "include_reasoning": False,
            }
        response = await self._llm.ainvoke(
            [SystemMessage(content=_ADEQUACY_SYSTEM_PROMPT), HumanMessage(content=user_payload)],
            request_id=request_id,
            conversation_id=conversation_id,
            temperature=0.0,
            top_p=1.0,
            max_tokens=256,
            **thinking_kwargs,
        )
        raw = (getattr(response, "content", "") or "").strip()
        parsed = _extract_verdict_json(raw)
        if parsed is None:
            logger.warning(
                f"[AdequacyGate] parse_failure raw_len={len(raw)} raw_preview={raw[:200]!r}"
            )
            return AdequacyVerdict(
                verdict="insufficient", reason="parse_failure",
                confidence=0.1, source="fallback",
            )
        verdict_str = str(parsed.get("verdict") or "").strip().lower()
        if verdict_str not in {"adequate", "insufficient"}:
            return AdequacyVerdict(
                verdict="insufficient", reason=f"unknown_verdict:{verdict_str!r}",
                confidence=0.1, source="fallback",
            )
        reason = str(parsed.get("reason") or "")[:300]
        confidence_raw = parsed.get("confidence")
        try:
            confidence = float(confidence_raw) if confidence_raw is not None else 0.5
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        return AdequacyVerdict(
            verdict=verdict_str,  # type: ignore[arg-type]
            reason=reason,
            confidence=confidence,
            source="llm_judge",
        )


# ============================================================================
# Prompt builders / parsers
# ============================================================================

def _build_judge_user_payload(*, question: str, observations: List[Observation]) -> str:
    """LLM judge에 전달할 사용자 payload — 질문 + obs 요약 (원본 evidence 안 보냄)."""
    obs_lines: List[str] = []
    for i, obs in enumerate(observations, start=1):
        if obs.status == "error":
            obs_lines.append(
                f"#{i} {obs.tool} status=error error_code={obs.error_code!r}"
            )
            continue
        res = obs.result or {}
        if "evidences" in res:
            evs = res.get("evidences") or []
            preview = [
                (ev.get("title") or "")[:60]
                for ev in evs[:3]
                if isinstance(ev, dict)
            ]
            obs_lines.append(
                f"#{i} {obs.tool} search_status={res.get('status')!r} "
                f"evidence_n={len(evs)} titles_preview={preview}"
            )
        elif "hits" in res:
            obs_lines.append(
                f"#{i} {obs.tool} hit_count={res.get('hit_count', 0)}"
            )
        elif "identifiers" in res:
            obs_lines.append(
                f"#{i} {obs.tool} target={res.get('target')!r} "
                f"item_count={res.get('item_count', 1)}"
            )
        elif "final_text" in res:
            text_preview = (res.get("final_text") or "")[:80]
            obs_lines.append(
                f"#{i} {obs.tool} final_text_preview={text_preview!r}"
            )
        else:
            obs_lines.append(f"#{i} {obs.tool} result_keys={list(res.keys())[:6]}")
    payload = {
        "question": question,
        "observations": obs_lines,
    }
    return json.dumps(payload, ensure_ascii=False)


_VERDICT_PATTERN = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}", re.DOTALL)


def _extract_verdict_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    candidates = [text]
    match = _VERDICT_PATTERN.search(text)
    if match:
        candidates.insert(0, match.group(0))
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None
