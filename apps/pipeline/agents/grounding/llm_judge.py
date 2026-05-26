"""LLM-as-Judge grounding 검증 구현체.

CriticAgent에 주입 가능한 GroundingChecker. 답변 텍스트와 evidence 제목·요약을 LLM에
보내 의미 일치 판정. 휴리스틱 단어 매칭 금지.

설계:
    - sync 인터페이스 (CriticAgent.critique 호환).
    - 내부에 async LLM 호출이 있으면 asyncio.run으로 wrapping.
    - LLM 출력은 JSON {verdict, reason}만 허용. 파싱 실패 시 verdict="error" 처리하고
      CriticAgent가 안전하게 fallback (실패가 critic을 무너뜨리지 않음).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.pipeline.agents.critic_agent import GroundingVerdict
from apps.pipeline.contracts import CanonicalEvidence


_GROUNDING_SYSTEM_PROMPT = (
    "당신은 RAG 답변의 'grounding' 정합성을 판정하는 검증자입니다. 사용자 질문과 "
    "[근거 출처] 항목 제목·요약을 본 뒤, **답변 본문의 핵심 주장**이 evidence의 "
    "내용과 의미적으로 일치하는지 판정합니다.\n\n"
    "판정 기준:\n"
    "  - grounded     : 답변의 모든 주요 주장이 evidence 제목·요약에서 직접 확인 가능\n"
    "  - partial      : 일부 주장은 evidence에서 확인 가능하지만 일부는 무관 또는 일반 지식\n"
    "  - not_grounded : 답변의 핵심 주장이 evidence와 명백히 무관하거나, query 키워드를\n"
    "                   evidence 무시하고 도입부에 박은 경우 (예: 'X 관련 성과를 정리'라\n"
    "                   하지만 evidence는 X와 무관)\n\n"
    "**중요**: 단순 단어 겹침 비율로 판정하지 마세요. evidence 제목이 답변의 핵심 주제를\n"
    "**의미적으로 다룰 수 있는가**를 판단합니다.\n\n"
    "출력 형식 (단일 JSON 객체, 다른 텍스트 금지):\n"
    '  {"verdict": "grounded"|"partial"|"not_grounded", "reason": "<2문장 이내 사유>"}\n'
)


class LLMJudgeChecker:
    """GroundingChecker 구현체 — LLM에 답변·evidence를 보내 판정 받음.

    의존성:
        llm: langchain BaseChatModel 호환 (ainvoke 지원). Solar/Gemma 사용 가능.
        max_evidence: prompt에 노출할 최대 evidence 개수 (token 비용 제한).
        max_answer_chars: 답변 텍스트 길이 제한.
    """

    def __init__(
        self,
        *,
        llm: Any,
        max_evidence: int = 10,
        max_answer_chars: int = 2000,
    ) -> None:
        self._llm = llm
        self._max_evidence = max_evidence
        self._max_answer_chars = max_answer_chars

    def check(
        self,
        *,
        answer_text: str,
        evidences: List[CanonicalEvidence],
        question: str,
    ) -> GroundingVerdict:
        """sync 인터페이스 — 내부 async LLM 호출을 적절히 wrapping.

        FastAPI 같은 running event loop 컨텍스트에서 호출되면 별도 thread executor에
        넘기고, 그렇지 않으면 asyncio.run을 사용한다. running loop을 먼저 확인해야
        coroutine을 미리 생성해 await 누수(RuntimeWarning)가 나는 것을 막을 수 있다.

        Returns:
            GroundingVerdict — verdict + reason + diagnostics
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # 루프 없음 → 안전하게 asyncio.run 사용
            return asyncio.run(self._check_async(
                answer_text=answer_text, evidences=evidences, question=question,
            ))
        # 이미 running loop 안 → 별도 thread에서 새 loop 실행
        return self._run_in_thread(answer_text, evidences, question)

    def _run_in_thread(
        self,
        answer_text: str,
        evidences: List[CanonicalEvidence],
        question: str,
    ) -> GroundingVerdict:
        """이미 event loop 안에서 호출됐을 때의 fallback — 별도 thread로 새 loop 실행."""
        import concurrent.futures

        def _runner():
            return asyncio.run(self._check_async(
                answer_text=answer_text, evidences=evidences, question=question,
            ))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_runner)
            return future.result(timeout=30.0)

    async def _check_async(
        self,
        *,
        answer_text: str,
        evidences: List[CanonicalEvidence],
        question: str,
    ) -> GroundingVerdict:
        user_payload = _build_user_payload(
            question=question,
            answer_text=answer_text[: self._max_answer_chars],
            evidences=evidences[: self._max_evidence],
        )
        messages = [
            SystemMessage(content=_GROUNDING_SYSTEM_PROMPT),
            HumanMessage(content=user_payload),
        ]
        try:
            response = await self._llm.ainvoke(
                messages,
                temperature=0.0,
                max_tokens=256,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[LLMJudgeChecker] llm.ainvoke failed: {exc}")
            return GroundingVerdict(verdict="error", reason=f"llm_error:{exc}")

        raw = (getattr(response, "content", "") or "").strip()
        parsed = _extract_verdict_json(raw)
        if parsed is None:
            logger.warning(
                f"[LLMJudgeChecker] parse failure raw_len={len(raw)} "
                f"raw_preview={raw[:120]!r}"
            )
            return GroundingVerdict(
                verdict="error", reason="parse_failure",
                diagnostics={"raw_preview": raw[:200]},
            )
        verdict = str(parsed.get("verdict") or "").strip().lower()
        if verdict not in {"grounded", "partial", "not_grounded"}:
            return GroundingVerdict(
                verdict="error", reason=f"unknown_verdict:{verdict!r}",
                diagnostics={"raw_preview": raw[:200]},
            )
        reason = str(parsed.get("reason") or "")[:300]
        return GroundingVerdict(
            verdict=verdict,
            reason=reason,
            diagnostics={"raw_preview": raw[:200]},
        )


def _build_user_payload(
    *,
    question: str,
    answer_text: str,
    evidences: List[CanonicalEvidence],
) -> str:
    """LLM 검증자에 넘기는 user message: 질문 + 답변 + evidence 제목·요약·참여자.

    2026-05-26: facts.participant_role_map(이름↔역할)을 evidence별로 노출. anchor 인물
    검색 결과("X의 활동 내역")처럼 답변 도입부가 인물명을 언급할 때 judge가 의미 매칭
    가능하게 한다.
    """
    ev_lines: List[str] = []
    for ev in evidences:
        rank = ev.snapshot_rank
        title = (ev.title or "(제목 없음)").strip()
        line = f"[{rank}] {title}"
        # summary는 200자만
        summary = (ev.summary or "").strip()
        if summary:
            summary = summary[:200].replace("\n", " ").strip()
            line += f"\n      요약: {summary}"
        # 참여자 이름↔역할 (anchor 인물 매칭용)
        role_map = (ev.facts or {}).get("participant_role_map") or []
        if isinstance(role_map, list) and role_map:
            parts: List[str] = []
            for entry in role_map[:8]:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if not name:
                    continue
                role = str(entry.get("role") or "").strip()
                parts.append(f"{name}({role})" if role else name)
            if parts:
                line += f"\n      참여자: {', '.join(parts)}"
        ev_lines.append(line)
    return (
        f"[사용자 질문]\n{question}\n\n"
        f"[답변 본문]\n{answer_text}\n\n"
        f"[근거 출처 — 답변이 의존해야 하는 evidence]\n"
        + ("\n".join(ev_lines) if ev_lines else "(없음)")
        + "\n\n"
        "위 답변 본문의 핵심 주장이 [근거 출처]의 제목·요약·참여자 정보에서 직접 확인 가능한지 판정하세요. "
        "단일 JSON 객체로만 응답:\n"
        '  {"verdict": "grounded"|"partial"|"not_grounded", "reason": "<2문장 이내>"}'
    )


_VERDICT_PATTERN = re.compile(r"\{[^{}]*\"verdict\"[^{}]*\}", re.DOTALL)


def _extract_verdict_json(raw: str) -> Optional[dict]:
    """LLM 응답에서 단일 JSON 객체 추출. 코드펜스/부수 텍스트 허용."""
    if not raw:
        return None
    text = raw.strip()
    # 코드펜스 제거
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    # 본문에서 JSON 객체 찾기
    candidates = [text]
    fence = _VERDICT_PATTERN.search(text)
    if fence:
        candidates.insert(0, fence.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None
