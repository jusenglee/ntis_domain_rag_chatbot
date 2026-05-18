"""단일 LLM 답변 생성기.

기존 apps.chat.answer_generation의 Solar+Gemma 이중 생성 + merge_answers 패턴을 폐기하고
SearchResult 컨텍스트로 단일 LLM(Triton gemma) 답변을 만든다.

본 모듈은 다음 책임만 갖는다:
    O canonical evidence를 prompt-safe block으로 정리
    O system prompt + user message 구성
    O 스트리밍 답변을 받아 GeneratedAnswer로 정리
    X groundedness / state_consistency 검증 (FinalGuard가 담당)
    X reference manifest 발행 (FinalGuard가 담당)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.api.streaming.contracts import StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.pipeline.contracts import (
    CanonicalEvidence,
    GeneratedAnswer,
    SearchResult,
    SearchTask,
)


_DEFAULT_SYSTEM_PROMPT = (
    "당신은 NTIS(국가과학기술지식정보서비스) RAG 챗봇입니다. "
    "사용자 질문에 대한 답변은 반드시 아래 [근거 출처] 블록의 정보만 사용해야 하며, "
    "근거에 없는 사실(이름, 식별자, 숫자)을 추정하거나 만들어내지 않습니다.\n\n"
    "출력 규칙:\n"
    "1. 답변은 한국어 자연어 문장으로 작성합니다.\n"
    "2. list 형식 답변일 때 각 항목 끝에 출처 번호 [N]을 표기합니다. N은 [근거 출처] 블록의 순번입니다.\n"
    "3. detail 형식 답변은 1개 대상의 상세 정보를 풀어 설명하고 [1]을 인용합니다.\n"
    "4. 근거가 부족하면 솔직히 '제공된 근거로는 확인할 수 없습니다'라고 답합니다.\n"
    "5. 동일한 사업의 여러 연차는 하나의 항목으로 묶지 말고 출처 순번대로 별도 항목으로 둡니다.\n"
)


def _format_evidence_block(evidences: List[CanonicalEvidence]) -> str:
    """canonical evidence를 prompt에 넣을 텍스트 블록으로 정리."""
    if not evidences:
        return "[근거 출처]\n(없음)\n"
    lines: List[str] = ["[근거 출처]"]
    for ev in evidences:
        head = f"[{ev.snapshot_rank}] {ev.title}".strip()
        if not ev.title:
            head = f"[{ev.snapshot_rank}] (제목 없음)"
        lines.append(head)
        if ev.ids:
            id_strs = []
            for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
                v = ev.ids.get(axis)
                if v:
                    id_strs.append(f"{axis}={v}")
            if id_strs:
                lines.append("  - 식별자: " + ", ".join(id_strs))
        if ev.facts.get("period"):
            lines.append(f"  - 기간: {ev.facts['period']}")
        if ev.facts.get("year"):
            lines.append(f"  - 연도: {ev.facts['year']}")
        if ev.roles.get("lead_org_name"):
            lines.append(f"  - 수행기관: {', '.join(ev.roles['lead_org_name'])}")
        if ev.summary:
            summary = ev.summary[:300].replace("\n", " ").strip()
            lines.append(f"  - 요약: {summary}")
        if ev.facts.get("goal"):
            goal = str(ev.facts["goal"])[:200].replace("\n", " ").strip()
            lines.append(f"  - 목표: {goal}")
    return "\n".join(lines) + "\n"


def _build_user_message(*, task: SearchTask, result: SearchResult) -> str:
    """LLM에 넘기는 user message: 질문 + 근거 블록."""
    evidence_block = _format_evidence_block(result.evidences)
    question = task.retrieval_query or "(질문이 비어 있습니다)"
    instructions = (
        f"\n[사용자 질문]\n{question}\n\n"
        f"[요청 형식]\naction={task.action}, target={task.target}, display_limit={task.display_limit}\n"
    )
    if task.action == "list":
        instructions += (
            f"\n위 [근거 출처]의 항목들을 {task.display_limit}건 이하로 정리해 답변하세요. "
            "각 항목 끝에 출처 번호 [N]을 반드시 표기하세요.\n"
        )
    elif task.action == "detail":
        instructions += "\n위 [근거 출처]의 항목 1건을 상세히 설명하세요. 마지막에 [1]을 인용하세요.\n"
    return evidence_block + instructions


class LLMGenerator:
    """답변 생성 wrapper.

    의존성:
        llm: BaseChatModel (TritonChatModel 권장). astream을 지원해야 한다.
    """

    def __init__(self, *, llm: Any, system_prompt: Optional[str] = None) -> None:
        self._llm = llm
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def generate(
        self,
        *,
        task: SearchTask,
        result: SearchResult,
        emitter: Optional[AsyncStreamEmitter] = None,
        model_key: str = "gemma_triton_0",
        max_tokens: int = 2048,
    ) -> GeneratedAnswer:
        """단일 LLM에 동기 호출 또는 스트리밍 호출.

        emitter가 주어지면 토큰 단위로 SSE에 publish하고, 최종 텍스트를 GeneratedAnswer로 반환.
        emitter가 없으면 단순히 텍스트 누적 후 반환.
        """
        user_text = _build_user_message(task=task, result=result)
        messages = [
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=user_text),
        ]

        prompt_chars = len(self._system_prompt) + len(user_text)
        logger.info(
            f"[LLMGenerator] start model={model_key} req={task.request_id[:8]} "
            f"action={task.action} target={task.target} evidence_n={len(result.evidences)} "
            f"prompt_chars={prompt_chars} max_tokens={max_tokens}"
        )

        t0 = time.perf_counter()
        chunks: List[str] = []
        total_chunk_n = 0
        try:
            async for chunk in self._llm.astream(messages, max_tokens=max_tokens, temperature=0.2):
                total_chunk_n += 1
                text = self._extract_chunk_text(chunk)
                if not text:
                    continue
                chunks.append(text)
                if emitter is not None:
                    await emitter.publish(
                        StreamEvent(
                            kind="answer.chunk",
                            request_id=task.request_id,
                            content=text,
                            model_key=model_key,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[LLMGenerator] stream failed: model={model_key} err={exc}")
            return GeneratedAnswer(
                text="".join(chunks),
                model_key=model_key,
                latency_ms=(time.perf_counter() - t0) * 1000,
                truncated=True,
                stream_metrics={"error": str(exc), "total_chunks": total_chunk_n},
            )

        final_text = "".join(chunks).strip()
        latency_ms = (time.perf_counter() - t0) * 1000
        preview_text = final_text.replace("\n", " ")[:100]
        logger.info(
            f"[LLMGenerator] done model={model_key} latency_ms={latency_ms:.1f} "
            f"total_chunks={total_chunk_n} content_chunks={len(chunks)} chars={len(final_text)} "
            f"preview={preview_text!r}"
        )
        return GeneratedAnswer(
            text=final_text,
            model_key=model_key,
            latency_ms=latency_ms,
            truncated=False,
            stream_metrics={"total_chunks": total_chunk_n, "content_chunks": len(chunks)},
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_chunk_text(chunk: Any) -> str:
        """LangChain 스트림 chunk에서 텍스트만 안전하게 추출.

        LangChain의 `BaseChatModel.astream`은 형태가 어댑터마다 다르다:
          - 보통 `AIMessageChunk(content=...)`를 직접 yield → `chunk.content`
          - 일부 어댑터는 `ChatGenerationChunk(message=AIMessageChunk(...))` 를 yield → `chunk.message.content`
          - raw string 형태도 가끔 있음

        세 경우 모두 안전하게 처리한다.
        """
        if chunk is None:
            return ""
        # 1) AIMessageChunk 또는 동등 객체: .content 직접
        direct_content = getattr(chunk, "content", None)
        if isinstance(direct_content, str) and direct_content:
            return direct_content
        # 2) ChatGenerationChunk: .message.content
        message = getattr(chunk, "message", None)
        if message is not None:
            text = getattr(message, "content", "")
            if isinstance(text, str):
                return text
        # 3) raw string
        if isinstance(chunk, str):
            return chunk
        return ""


async def generate_no_result_message(
    *,
    task: SearchTask,
    emitter: Optional[AsyncStreamEmitter] = None,
) -> GeneratedAnswer:
    """검색 결과가 0건일 때의 결정적 응답.

    LLM을 거치지 않고 안전한 문구를 생성한다. emitter가 있으면 한 번에 전송.
    """
    if task.subject:
        text = (
            f"'{task.subject.display_name}'에 대한 조건에 부합하는 결과를 찾지 못했습니다. "
            "검색 조건(연도, 소속 기관 등)을 조금 더 구체적으로 알려주실 수 있나요?"
        )
    elif task.identifiers.has_any():
        ids_used = ", ".join(
            f"{axis}={','.join(getattr(task.identifiers, axis))}"
            for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id")
            if getattr(task.identifiers, axis)
        )
        text = (
            f"입력하신 식별자({ids_used})에 해당하는 데이터를 NTIS에서 찾지 못했습니다. "
            "식별자가 정확한지 확인해 주세요."
        )
    else:
        text = "질문에 부합하는 결과를 찾지 못했습니다. 다른 단어로 검색을 시도해 보세요."

    if emitter is not None:
        await emitter.publish(
            StreamEvent(
                kind="answer.chunk",
                request_id=task.request_id,
                content=text,
                model_key="no_result",
            )
        )
    return GeneratedAnswer(
        text=text,
        model_key="no_result",
        latency_ms=0.0,
        truncated=False,
        stream_metrics={"answer_kind": "no_result"},
    )
