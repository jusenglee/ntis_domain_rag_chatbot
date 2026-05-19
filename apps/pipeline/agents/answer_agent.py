"""Phase 8: AnswerAgent — EvidenceBundle + DialogueIntent → AnswerDraft.

설계 원칙:
    - LLM은 본 단계에서만 답변 문장을 생성한다 (DialogueAgent는 의도 분류 LLM, 본 단계는 답변 LLM).
    - prompt는 view 종류로 분기한다. view → template 매핑:
        empty            → "no_result"   (LLM 호출 없음, 결정적 메시지)
        single_detail    → "detail"
        subject_activity → "list"        (그룹 정보를 prompt에 힌트로 첨부)
        list_compact     → "list"
        stats_summary    → "stats"
        comparison_table → "compare"     (compare_targets 그룹 힌트)
    - 출력 text의 [N] 인용은 EvidenceBundle.items의 snapshot_rank를 가리킨다 (curator가 진실원).
    - emitter가 있으면 토큰 단위 SSE publish. 없으면 단순 누적.
    - CriticAgent가 본 결과를 검증해 publish/repair/clarify로 결정.

본 모듈의 책임 경계:
    O view별 prompt 구성
    O LLM astream 소비 + chunk 누적
    O 본문 내 [N] 인용 파싱 → Citation 리스트
    X groundedness / state_consistency 검증 (CriticAgent)
    X reference manifest 발행 (CriticAgent)
    X retry / repair (CriticAgent decision=repair_answer로 1회 재호출)
"""

from __future__ import annotations

import re
import time
from typing import Any, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.api.streaming.contracts import StreamEvent
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.pipeline.agents.contracts import (
    AnswerDraft,
    Citation,
    DialogueIntent,
    EvidenceBundle,
)
from apps.pipeline.contracts import CanonicalEvidence


_DEFAULT_SYSTEM_PROMPT = (
    "당신은 NTIS(국가과학기술지식정보서비스) RAG 챗봇입니다. "
    "사용자 질문에 대한 답변은 반드시 아래 [근거 출처] 블록의 정보만 사용해야 하며, "
    "근거에 없는 사실(이름, 식별자, 숫자, 기관)을 추정하거나 만들어내지 않습니다.\n\n"
    "출력 규칙:\n"
    "1. 답변은 한국어 자연어로 작성합니다.\n"
    "2. list 형식 답변은 각 항목 끝에 출처 번호 [N]을 표기합니다. N은 [근거 출처]의 순번과 동일합니다.\n"
    "3. detail 형식 답변은 1개 대상의 상세 정보를 풀어 설명하고 마지막에 [1]을 인용합니다.\n"
    "4. compare 형식 답변은 비교 대상별로 항목을 나누어 정리하고 각 항목 끝에 [N]을 표기합니다.\n"
    "5. 동일한 사업(pjt_no)의 여러 연차는 한 항목으로 묶지 말고 [근거 출처] 순번대로 별도 항목으로 둡니다.\n"
    "6. 근거가 부족하면 솔직히 '제공된 근거로는 확인할 수 없습니다'라고 답합니다.\n"
    "7. **식별자 비노출**: pjt_id / pjt_no / rst_id / person_no / org_id / org_code / biz_no / doi / issn 같은\n"
    "   raw 식별자 값을 답변 본문에 직접 적지 마세요. [근거 출처]의 식별자 라인은 LLM이 어떤 항목을\n"
    "   가리키는지 식별하기 위한 내부 단서일 뿐이며, 사용자에게는 [N] 인용 번호로만 노출됩니다.\n"
    "   허용: 사업/성과의 사람-읽기 가능 이름(제목, 사업명, 수행기관명, 기간, 연도).\n"
    "   금지: 'pjt_id=1234567890', 'rst_id=CNL-...', 'person_no=ntis:B551...' 같은 명시적 식별자 표기.\n"
)


_NO_RESULT_TEMPLATE_TEXTS = {
    "default": "질문에 부합하는 결과를 찾지 못했습니다. 다른 단어로 검색을 시도해 보세요.",
    "subject": "'{name}'에 대한 조건에 부합하는 결과를 찾지 못했습니다. 검색 조건을 조금 더 구체적으로 알려주실 수 있나요?",
    "identifier": "입력하신 식별자({ids})에 해당하는 데이터를 NTIS에서 찾지 못했습니다. 식별자가 정확한지 확인해 주세요.",
}


_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


# ============================================================================
# AnswerAgent
# ============================================================================

class AnswerAgent:
    """EvidenceBundle + DialogueIntent → AnswerDraft.

    의존성:
        llm: BaseChatModel (TritonChatModel 권장). astream(messages, max_tokens, temperature) 지원.
    """

    def __init__(self, *, llm: Any, system_prompt: Optional[str] = None) -> None:
        self._llm = llm
        self._system_prompt = system_prompt or _DEFAULT_SYSTEM_PROMPT

    async def generate(
        self,
        *,
        bundle: EvidenceBundle,
        intent: DialogueIntent,
        request_id: str,
        emitter: Optional[AsyncStreamEmitter] = None,
        model_key: str = "gemma_triton_0",
        max_tokens: int = 2048,
        temperature: float = 0.2,
        repair_hint: Optional[str] = None,
    ) -> AnswerDraft:
        """답변 초안 생성.

        - bundle.view == "empty" → no_result 결정적 메시지 (LLM 호출 없음)
        - 그 외 → LLM astream 호출 + 누적 + citation 파싱
        - ``repair_hint``가 주어지면 system prompt에 보수 지시사항을 첨부해 LLM이 직전 위반을
          반복하지 않도록 유도한다 (CriticAgent.decision='repair_answer'에서 전달).
        """
        template = _template_from_view(bundle.view)

        if bundle.view == "empty":
            return await self._generate_no_result(
                intent=intent,
                request_id=request_id,
                emitter=emitter,
            )

        user_text = _build_user_message(bundle=bundle, intent=intent)
        system_prompt = self._system_prompt
        if repair_hint:
            system_prompt = (
                f"{system_prompt}\n\n[답변 재작성 지시사항 (CriticAgent 피드백)]\n"
                f"{repair_hint}\n"
                "위 지시사항을 반드시 준수해 답변을 다시 작성하세요.\n"
            )
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_text),
        ]

        prompt_chars = len(self._system_prompt) + len(user_text)
        logger.info(
            f"[AnswerAgent] start model={model_key} req={request_id[:8]} "
            f"view={bundle.view} template={template} evidence_n={len(bundle.items)} "
            f"prompt_chars={prompt_chars} max_tokens={max_tokens}"
        )

        t0 = time.perf_counter()
        chunks: List[str] = []
        total_chunk_n = 0
        truncated = False
        error_msg: Optional[str] = None

        try:
            async for chunk in self._llm.astream(
                messages, max_tokens=max_tokens, temperature=temperature
            ):
                total_chunk_n += 1
                text = _extract_chunk_text(chunk)
                if not text:
                    continue
                chunks.append(text)
                if emitter is not None:
                    await emitter.publish(
                        StreamEvent(
                            kind="answer.chunk",
                            request_id=request_id,
                            content=text,
                            model_key=model_key,
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)
            truncated = True
            logger.exception(f"[AnswerAgent] stream failed: model={model_key} err={exc}")

        final_text = "".join(chunks).strip()
        latency_ms = (time.perf_counter() - t0) * 1000
        citations = _parse_citations(final_text)

        stream_metrics: dict[str, Any] = {
            "total_chunks": total_chunk_n,
            "content_chunks": len(chunks),
            "view": bundle.view,
        }
        if error_msg:
            stream_metrics["error"] = error_msg

        preview = final_text.replace("\n", " ")[:100]
        logger.info(
            f"[AnswerAgent] done model={model_key} latency_ms={latency_ms:.1f} "
            f"chars={len(final_text)} citations={len(citations)} truncated={truncated} "
            f"preview={preview!r}"
        )
        return AnswerDraft(
            text=final_text,
            citations=citations,
            template=template,
            model_key=model_key,
            latency_ms=latency_ms,
            truncated=truncated,
            stream_metrics=stream_metrics,
        )

    # ------------------------------------------------------------------
    # no_result fast path
    # ------------------------------------------------------------------

    async def _generate_no_result(
        self,
        *,
        intent: DialogueIntent,
        request_id: str,
        emitter: Optional[AsyncStreamEmitter] = None,
    ) -> AnswerDraft:
        text = _no_result_text_for(intent)
        if emitter is not None:
            await emitter.publish(
                StreamEvent(
                    kind="answer.chunk",
                    request_id=request_id,
                    content=text,
                    model_key="no_result",
                )
            )
        return AnswerDraft(
            text=text,
            citations=[],
            template="no_result",
            model_key="no_result",
            latency_ms=0.0,
            truncated=False,
            stream_metrics={"view": "empty"},
        )


# ============================================================================
# view → template
# ============================================================================

def _template_from_view(view: str) -> str:
    if view == "empty":
        return "no_result"
    if view == "single_detail":
        return "detail"
    if view == "stats_summary":
        return "stats"
    if view == "comparison_table":
        return "compare"
    # subject_activity / list_compact
    return "list"


# ============================================================================
# Prompt building
# ============================================================================

def _build_user_message(*, bundle: EvidenceBundle, intent: DialogueIntent) -> str:
    """LLM에 넘기는 user message: 질문 + 근거 블록 + view별 지시사항.

    single_detail view에서는 evidence.child_entities(참여연구자/참여기관)를 별도 블록으로 노출해
    "참여연구자는?" 같은 follow-up 질문에 LLM이 직접 답할 수 있게 한다.
    """
    evidence_block = _format_evidence_block(bundle.items)
    group_block = _format_group_block(bundle)
    child_block = (
        _format_child_entities_block(bundle.items[0])
        if bundle.view == "single_detail" and bundle.items
        else ""
    )
    question = (intent.query or "").strip() or "(질문이 비어 있습니다)"
    instructions = _instructions_for_view(bundle=bundle, intent=intent)
    parts = [evidence_block]
    if group_block:
        parts.append(group_block)
    if child_block:
        parts.append(child_block)
    parts.append(f"\n[사용자 질문]\n{question}\n")
    parts.append(instructions)
    return "\n".join(parts)


def _format_evidence_block(items: List[CanonicalEvidence]) -> str:
    if not items:
        return "[근거 출처]\n(없음)\n"
    lines: List[str] = ["[근거 출처]"]
    for ev in items:
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
        if ev.facts.get("count") is not None:
            lines.append(f"  - 카운트: {ev.facts['count']}")
        if ev.facts.get("group_by"):
            lines.append(f"  - 그룹기준: {ev.facts['group_by']}")
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


def _format_child_entities_block(evidence: CanonicalEvidence) -> str:
    """single_detail evidence의 child_entities(참여연구자/참여기관/연계성과)를 LLM에 노출.

    parent_relation별로 분류해 표시한다:
        - participant_researcher / top_level_researcher → '[참여연구자]'
        - lead_org / prtcp_org → '[참여기관]'
        - related_perf → '[연계 성과]'

    raw person_no 같은 식별자는 표시하지 않는다 — display_name + role + affiliation만 노출.
    """
    children = evidence.child_entities or []
    if not children:
        return ""

    by_relation: Dict[str, List[Dict[str, Any]]] = {"researcher": [], "org": [], "perf": []}
    for entity in children:
        if not isinstance(entity, dict):
            continue
        name = (entity.get("display_name") or "").strip()
        if not name:
            continue
        relation = (entity.get("parent_relation") or "").lower()
        kind = (entity.get("kind") or "").lower()
        if kind == "people" or "researcher" in relation:
            by_relation["researcher"].append(entity)
        elif kind == "org" or "org" in relation:
            by_relation["org"].append(entity)
        elif kind == "perf" or "perf" in relation:
            by_relation["perf"].append(entity)

    lines: List[str] = []

    def _format_person(e: Dict[str, Any]) -> str:
        name = e.get("display_name", "")
        role = (e.get("role") or "").strip()
        aff = (e.get("affiliation") or "").strip()
        parts = [name]
        if role:
            parts.append(f"({role})")
        if aff:
            parts.append(f"— {aff}")
        return " ".join(parts)

    def _format_org(e: Dict[str, Any]) -> str:
        name = e.get("display_name", "")
        role = (e.get("role") or "").strip()
        if role and role != "lead_org":
            return f"{name} ({role})"
        return name

    if by_relation["researcher"]:
        lines.append("[참여연구자] (사용자가 참여자/참여인력을 물으면 이 목록을 그대로 사용)")
        for e in by_relation["researcher"][:50]:
            lines.append(f"  - {_format_person(e)}")
    if by_relation["org"]:
        lines.append("[참여기관]")
        for e in by_relation["org"][:30]:
            lines.append(f"  - {_format_org(e)}")
    if by_relation["perf"]:
        lines.append("[연계 성과]")
        for e in by_relation["perf"][:30]:
            name = (e.get("display_name") or "").strip()
            relation = (e.get("parent_relation") or "").strip()
            line = f"  - {name}"
            if relation:
                line += f" ({relation})"
            lines.append(line)

    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def _format_group_block(bundle: EvidenceBundle) -> str:
    """EvidenceGroup이 있으면 LLM에 그룹 힌트를 별도 블록으로 제공.

    같은 사업(pjt_no)의 여러 행을 한 항목으로 묶지 않도록 명시적으로 보여준다.
    """
    if not bundle.groups:
        return ""
    lines = ["[그룹 힌트] (참고용 — 같은 묶음의 항목들도 출처 순번대로 별도 항목으로 답변에 포함하세요)"]
    for g in bundle.groups:
        ranks_text = ", ".join(f"[{r}]" for r in g.member_ranks)
        role = f" ({g.role})" if g.role else ""
        lines.append(f"- {g.group_label}{role}: {ranks_text}")
    return "\n".join(lines) + "\n"


def _instructions_for_view(*, bundle: EvidenceBundle, intent: DialogueIntent) -> str:
    """view별 지시사항. display_limit 같은 강제 한도는 EvidenceCurator가 이미 결정했으므로
    여기서는 자연어 가이드만 둔다.
    """
    n = len(bundle.items)
    if bundle.view == "single_detail":
        question = (intent.query or "").lower()
        # 사용자가 참여연구자/참여인력/참여기관을 물으면 child_entities 블록을 우선 활용
        wants_participants = any(
            kw in question
            for kw in ("참여연구자", "참여 연구자", "참여인력", "참여 인력", "연구진", "수행연구자", "참여기관", "참여 기관")
        )
        if wants_participants:
            return (
                "[요청 형식]\n사용자가 참여자/참여기관 정보를 요청했습니다. 위 [참여연구자] 또는 "
                "[참여기관] 블록의 목록을 그대로 풀어 답변하세요. 각 사람은 이름·역할·소속만 표기하고 "
                "person_no 같은 식별자는 노출하지 마세요. 답변 마지막에 [1]을 인용하세요. "
                "참여자 정보가 [참여연구자] 블록에 없으면 '제공된 근거로는 참여연구자 명단을 확인할 수 "
                "없습니다'라고 답하세요.\n"
            )
        return (
            "[요청 형식]\n위 [근거 출처]의 항목 1건을 상세히 풀어 설명하세요. "
            "수행기관·기간·목표·요약을 포함하되 **raw 식별자(pjt_id/pjt_no/rst_id/person_no/org_id "
            "등) 값은 본문에 표기하지 마세요**. 식별자는 [1] 인용으로만 노출됩니다. "
            "[참여연구자]/[참여기관] 블록이 있으면 핵심 인물·기관을 1~2명 간단히 언급할 수 있습니다. "
            "마지막에 [1]을 인용하세요.\n"
        )
    if bundle.view == "subject_activity":
        subject_label = intent.subject_name or "해당 대상"
        return (
            f"[요청 형식]\n'{subject_label}'의 활동내역을 {n}건 이하로 정리하세요. "
            "각 항목은 1줄~2줄로 사업/성과명·기관·연도(있는 경우)를 표기하고 끝에 [N]을 표기합니다. "
            "같은 사업의 다년차/성과는 한 항목으로 묶지 말고 [근거 출처] 순번대로 별도 항목으로 둡니다.\n"
        )
    if bundle.view == "stats_summary":
        return (
            "[요청 형식]\n위 [근거 출처]는 그룹별 집계 결과입니다 (각 항목의 title=그룹 키, "
            "facts.count=그룹별 카운트, facts.group_by=집계 축). count 내림차순으로 정렬되어 있습니다. "
            "그룹별 수치를 글머리표나 표 형태로 정리하세요. "
            "예: '* 2020년: 5건 [1]', '* 2021년: 3건 [2]'. 각 항목 끝에 출처 번호 [N]을 표기합니다. "
            "전체 합계를 마지막에 명시할 수 있습니다.\n"
        )
    if bundle.view == "comparison_table":
        target_names = [t.name for t in intent.compare_targets]
        target_str = ", ".join(target_names) or "각 비교 대상"
        return (
            f"[요청 형식]\n{target_str}을 비교 정리하세요. 각 비교 대상별로 항목을 나누고 "
            "주요 차이점(기간·기관·규모·목표 등)을 1~2줄로 정리하며 각 항목 끝에 [N]을 표기합니다.\n"
        )
    # list_compact (기본)
    return (
        f"[요청 형식]\n위 [근거 출처]를 {n}건 이하로 정리하세요. "
        "각 항목은 1줄로 핵심을 정리하고 끝에 출처 번호 [N]을 표기합니다.\n"
    )


# ============================================================================
# Citation parsing
# ============================================================================

def _parse_citations(text: str) -> List[Citation]:
    """본문에 등장한 [N]을 위치와 함께 Citation으로 모은다.

    동일 rank가 여러 번 등장하면 모두 보존한다 (CriticAgent가 visible_count/manifest 정합 검증).
    """
    if not text:
        return []
    citations: List[Citation] = []
    for m in _CITATION_PATTERN.finditer(text):
        try:
            rank = int(m.group(1))
        except (TypeError, ValueError):
            continue
        if rank < 1:
            continue
        citations.append(Citation(rank=rank, span_start=m.start(), span_end=m.end()))
    return citations


# ============================================================================
# No-result message
# ============================================================================

def _no_result_text_for(intent: DialogueIntent) -> str:
    name = (intent.subject_name or "").strip()
    if name:
        return _NO_RESULT_TEMPLATE_TEXTS["subject"].format(name=name)
    ids_kv = _format_identifier_hints(intent)
    if ids_kv:
        return _NO_RESULT_TEMPLATE_TEXTS["identifier"].format(ids=ids_kv)
    return _NO_RESULT_TEMPLATE_TEXTS["default"]


def _format_identifier_hints(intent: DialogueIntent) -> str:
    if not intent.identifier_hints:
        return ""
    parts: List[str] = []
    for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
        values = intent.identifier_hints.get(axis) or []
        if values:
            parts.append(f"{axis}={','.join(values)}")
    return ", ".join(parts)


# ============================================================================
# LangChain stream chunk extraction
# ============================================================================

def _extract_chunk_text(chunk: Any) -> str:
    """LangChain `astream`의 다양한 chunk 형태에서 텍스트만 안전하게 추출."""
    if chunk is None:
        return ""
    direct_content = getattr(chunk, "content", None)
    if isinstance(direct_content, str) and direct_content:
        return direct_content
    message = getattr(chunk, "message", None)
    if message is not None:
        text = getattr(message, "content", "")
        if isinstance(text, str):
            return text
    if isinstance(chunk, str):
        return chunk
    return ""
