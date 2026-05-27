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
    "당신은 NTIS(국가과학기술지식정보서비스) RAG 챗봇입니다.\n\n"
    "**핵심 규칙 (3가지)**:\n"
    "1. 한국어 자연어로 답변합니다.\n"
    "2. 답변 내용은 반드시 [근거 출처] 블록의 evidence에서 직접 확인 가능해야 합니다. "
    "근거에 없는 인물·기관·식별자·숫자를 만들어내지 마세요. 근거가 부족하면 "
    "'제공된 근거로는 확인할 수 없습니다'라고 답합니다.\n"
    "3. 사용자에게 보여줄 각 항목 끝에 출처 번호 [N]을 표기합니다 (N은 [근거 출처] 순번). "
    "raw 식별자(pjt_id/rst_id/person_no 등)는 본문에 적지 말고 [N] 인용으로만 노출합니다.\n"
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
            f"[AnswerAgent] generation_start(답변 생성 시작) "
            f"model={model_key}(모델) req={request_id[:8]} "
            f"view={bundle.view}(뷰) template={template}(템플릿) "
            f"evidence_n={len(bundle.items)}(근거 건수) "
            f"prompt_chars={prompt_chars}(프롬프트 길이) "
            f"max_tokens={max_tokens}(최대 토큰)"
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
            logger.exception(
                f"[AnswerAgent] stream_failure(LLM 스트림 실패) "
                f"model={model_key}(모델) error={exc} truncated=True(답변 잘림)"
            )

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
            f"[AnswerAgent] generation_done(답변 생성 완료) "
            f"model={model_key}(모델) latency_ms={latency_ms:.1f}(소요시간) "
            f"chars={len(final_text)}(답변 길이) "
            f"citations={len(citations)}(인용 개수) "
            f"truncated={truncated}(잘림 여부) "
            f"preview={preview!r}(답변 미리보기)"
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
    """LLM에 넘기는 user message: [context] + [근거 출처] + (child) + [사용자 질문] + [요청 형식].

    2026-05-26 공격적 재설계: 사용자 조건/활동 요약/score 분포/정렬/그룹 힌트의 5개 메타 블록을
    단일 [context] 블록으로 통합. evidence·question·instructions는 별도 블록 유지.

    single_detail view에서는 evidence.child_entities(참여연구자/참여기관)를 [근거 출처] 뒤에
    별도 블록으로 노출해 "참여연구자는?" follow-up 질문에 LLM이 직접 답할 수 있게 한다.
    """
    context_block = _build_context_block(bundle=bundle, intent=intent)
    evidence_block = _format_evidence_block(bundle.items)
    child_block = (
        _format_child_entities_block(bundle.items[0])
        if bundle.view == "single_detail" and bundle.items
        else ""
    )
    question = (intent.query or "").strip() or "(질문이 비어 있습니다)"
    instructions = _instructions_for_view(bundle=bundle, intent=intent)
    parts: List[str] = []
    if context_block:
        parts.append(context_block)
    parts.append(evidence_block)
    if child_block:
        parts.append(child_block)
    parts.append(f"\n[사용자 질문]\n{question}\n")
    parts.append(instructions)
    return "\n".join(parts)


def _build_context_block(*, bundle: EvidenceBundle, intent: DialogueIntent) -> str:
    """5개 메타 블록을 단일 [context]로 통합 — 사용자 조건 / 활동 요약 / score 분포 / 정렬 / 그룹.

    각 sub-block은 `## 라벨` sub-section으로 들어간다. 비어 있는 sub-block은 생략.
    sort_by는 LLM이 도입부 정직성 판단에 필요하므로 항상 노출 (relevance 포함).
    """
    sections: List[str] = []
    intent_text = _summarize_user_intent(intent=intent, item_count=len(bundle.items))
    if intent_text:
        sections.append(_promote_to_subsection(intent_text))
    if bundle.view == "subject_activity":
        act = _format_activity_summary_block(bundle)
        if act:
            sections.append(_promote_to_subsection(act))
    score = _format_score_distribution_block(bundle)
    if score:
        sections.append(_promote_to_subsection(score))
    sections.append(f"## 정렬\n  - sort_by={intent.sort_by}")
    group = _format_group_block(bundle)
    if group:
        sections.append(_promote_to_subsection(group))
    if not sections:
        return ""
    return "[context]\n" + "\n\n".join(sections) + "\n"


def _promote_to_subsection(block_text: str) -> str:
    """`[라벨]\n...` 형태 sub-block을 `## 라벨\n...` 형태로 변환해 [context] 안에 통합."""
    text = block_text.rstrip()
    if not text.startswith("["):
        return text
    end = text.find("]")
    if end == -1:
        return text
    label = text[1:end].strip()
    rest = text[end + 1:].lstrip("\n")
    return f"## {label}\n{rest}"


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


def _summarize_user_intent(*, intent: DialogueIntent, item_count: int) -> str:
    """active filter를 한 줄 요약해 LLM에 노출. 답변 도입부에 사용자 조건 반복 유도.

    **주의 (P0 hotfix 2026-05-22)**: 가이드 문장은 구체 인물명·조직명·연도 같은 사용자가
    한 적 없는 토큰을 절대 포함하지 않는다. LLM이 예시 문장을 그대로 베껴 거짓 답변을
    생성하는 회귀가 있었다. 가이드는 placeholder만 사용.

    또 manifest_rank가 유일한 active 신호일 때는 reflection 블록을 만들지 않는다 (LLM이
    "N번 항목 상세"라고 답변에 이미 자연스럽게 표기하므로 추가 가이드는 거짓 위험만 증가).
    """
    lines: List[str] = []
    substantive_signal = False  # subject/year/perf_type/coparticipants/exclude_* 같은 진짜 사용자 조건
    if intent.subject_name:
        kind_label = {"people": "people", "org": "org"}.get(intent.subject_kind or "", intent.subject_kind or "")
        aff = (intent.subject_affiliation_hint or "").strip()
        if aff:
            lines.append(f"  - 대상: '{intent.subject_name}' ({kind_label}, {aff} 소속)")
        else:
            lines.append(f"  - 대상: '{intent.subject_name}' ({kind_label})")
        substantive_signal = True
    if intent.year_from or intent.year_to:
        if intent.year_from and intent.year_to:
            lines.append(f"  - 연도: {intent.year_from}년 ~ {intent.year_to}년")
        elif intent.year_from:
            lines.append(f"  - 연도: {intent.year_from}년 이후")
        elif intent.year_to:
            lines.append(f"  - 연도: {intent.year_to}년 이전")
        substantive_signal = True
    if intent.perf_type_hint:
        lines.append(f"  - 성과 유형: {', '.join(intent.perf_type_hint)}")
        substantive_signal = True
    if intent.coparticipants:
        lines.append(f"  - 공동 참여자: {', '.join(intent.coparticipants)}")
        substantive_signal = True
    if intent.exclude_org_name:
        lines.append(f"  - 제외 기관: {', '.join(intent.exclude_org_name)}")
        substantive_signal = True
    if intent.exclude_perf_type:
        lines.append(f"  - 제외 성과 유형: {', '.join(intent.exclude_perf_type)}")
        substantive_signal = True
    if intent.exclude_person_name:
        lines.append(f"  - 제외 인명: {', '.join(intent.exclude_person_name)}")
        substantive_signal = True
    if intent.sort_by and intent.sort_by != "relevance":
        sort_label = {"recent_desc": "최근순", "recent_asc": "오래된 순"}.get(intent.sort_by, intent.sort_by)
        lines.append(f"  - 정렬: {sort_label}")
        substantive_signal = True
    if intent.length_hint and intent.length_hint != "default":
        length_label = {"brief": "간결한 요약", "detailed": "상세한 설명"}.get(intent.length_hint, intent.length_hint)
        lines.append(f"  - 답변 길이: {length_label}")
        # length_hint만으로는 substantive_signal 승격 안 함 (단독으론 reflection 가치 작음)
    if intent.manifest_rank:
        lines.append(f"  - 직전 N번 항목 인용: {intent.manifest_rank}")
        # manifest_rank 단독은 substantive_signal 아님 (LLM이 답변에 이미 자연 반영)
    if not lines:
        return ""
    if not substantive_signal:
        # manifest_rank/length_hint만 있는 경우 → reflection 블록 미생성 (거짓 답변 위험 회피)
        return ""
    lines.append(f"  - 결과 건수: {item_count}")
    return (
        "[사용자 조건 요약]\n"
        + "\n".join(lines)
        + "\n답변 도입부에 위 조건들을 사용자가 한 그대로 (또는 자연스럽게 묶어서) 반복해\n"
        "사용자가 의도가 정확히 이해됐음을 확인할 수 있게 하세요. **위 조건에 없는 인물명·"
        "조직명·필터값을 임의로 만들어 도입부에 넣지 마세요.** 조건이 없는 항목은 도입부에서\n"
        "언급하지 않습니다.\n"
    )


def _format_score_distribution_block(bundle: EvidenceBundle) -> str:
    """retrieval score 분포를 LLM에 노출해 신뢰도 판단 자료로 활용.

    휴리스틱 threshold 없이 score 분포만 보여주고 LLM이 판단하도록 한다:
    - top이 충분히 높고 spread도 적정하면 일반 답변
    - top이 낮거나 모두 비슷한 점수면 LLM이 도입부에 정직 안내
      ("검색 결과가 요청과 정확히 일치하지 않을 수 있습니다")

    이 블록은 사례별 땜빵 아니라 모든 retrieval에 자동 노출되는 일반 신호.
    """
    dist = (bundle.diagnostics or {}).get("score_distribution")
    if not isinstance(dist, dict) or not dist.get("n"):
        return ""
    top = dist.get("top", 0.0)
    median = dist.get("median", 0.0)
    bottom = dist.get("min", 0.0)
    spread = dist.get("spread", 0.0)
    n = dist.get("n", 0)
    return (
        f"[retrieval score 분포]\n"
        f"  - 결과 {n}건, 최고 {top:.3f} · 중앙 {median:.3f} · 최저 {bottom:.3f} · 폭 {spread:.3f}\n"
        f"점수가 낮거나(top<<일반적 기대치) 모든 결과의 점수가 비슷해 분포가 평평하면(spread가\n"
        f"매우 작으면) 검색 결과가 사용자 질문과 의미적으로 잘 매칭되지 않은 것입니다.\n"
        f"이 경우 답변 도입부에 \"검색 결과가 요청과 정확히 일치하지 않을 수 있습니다\"라고\n"
        f"정직하게 안내한 뒤 결과를 보여주세요. (시스템 9·10번 규칙 — 도입부 grounding).\n"
    )


def _format_activity_summary_block(bundle: EvidenceBundle) -> str:
    """subject_activity view의 활동 요약 통계를 prompt 블록으로 노출.

    답변 도입부에 "신동구는 과제 7건 / 성과 3건, 주요 기관 KISTI(5), 활동 연도 2008~2025"
    같은 한눈 요약을 LLM이 작성하도록 유도.
    """
    summary = (bundle.diagnostics or {}).get("activity_summary")
    if not isinstance(summary, dict):
        return ""
    lines: List[str] = ["[활동 요약]"]
    total = summary.get("total")
    by_source = summary.get("by_source") or {}
    if total:
        # source 분포 표기 (project/perf만 사람-읽기 라벨)
        label_map = {"project": "과제", "perf": "성과", "people": "사람", "org": "기관", "support": "지원"}
        parts = [
            f"{label_map.get(src, src)} {count}건"
            for src, count in by_source.items() if count > 0
        ]
        if parts:
            lines.append(f"  - 총 {total}건 ({', '.join(parts)})")
        else:
            lines.append(f"  - 총 {total}건")
    year_min = summary.get("year_min")
    year_max = summary.get("year_max")
    if year_min and year_max:
        if year_min == year_max:
            lines.append(f"  - 활동 연도: {year_min}년")
        else:
            lines.append(f"  - 활동 연도: {year_min}년 ~ {year_max}년")
    top_orgs = summary.get("top_orgs") or []
    if top_orgs:
        org_labels = [f"{o['name']}({o['count']})" for o in top_orgs[:3]]
        lines.append(f"  - 주요 수행기관: {', '.join(org_labels)}")
    # subject 인물의 역할 분포 (anchor 인물 검색 시 grounding 신호)
    subject_roles = summary.get("subject_roles") or []
    if subject_roles:
        role_labels = [f"{r['role']}({r['count']})" for r in subject_roles[:4]]
        match_count = summary.get("subject_match_count")
        match_text = f" / 매칭 {match_count}건" if match_count else ""
        lines.append(f"  - 대상 인물의 역할: {', '.join(role_labels)}{match_text}")
    if len(lines) == 1:
        return ""
    lines.append(
        "답변 도입부에 위 요약을 1~2문장으로 자연스럽게 요약한 뒤 활동 list로 이어가세요."
    )
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


_LENGTH_HINT_INSTRUCTIONS = {
    "brief": (
        "[답변 길이: brief] 사용자가 간결한 답변을 요청했습니다. "
        "각 항목은 1줄(약 50자 이내)로 핵심만 정리하고 도입부도 한 문장으로 짧게 유지하세요."
    ),
    "detailed": (
        "[답변 길이: detailed] 사용자가 상세한 답변을 요청했습니다. "
        "각 항목에 사업/성과 핵심 정보(수행기관·기간·요약)를 2~3줄로 풀어 설명하고, "
        "도입부에서 결과 전반을 2~3문장으로 종합하세요."
    ),
    "default": "",
}


def _instructions_for_view(*, bundle: EvidenceBundle, intent: DialogueIntent) -> str:
    """view별 지시사항 + length_hint 가이드 결합."""
    base = _base_instructions_for_view(bundle=bundle, intent=intent)
    length_extra = _LENGTH_HINT_INSTRUCTIONS.get(intent.length_hint, "")
    if length_extra:
        return base + length_extra + "\n"
    return base


def _base_instructions_for_view(*, bundle: EvidenceBundle, intent: DialogueIntent) -> str:
    """view별 자연어 가이드 (length_hint 미적용 원본).

    display_limit 같은 강제 한도는 EvidenceCurator가 이미 결정했으므로 여기서는 자연어
    가이드만 둔다.
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
    """결과 0건일 때 사용자에게 보여줄 메시지 + 다음 시도 가이드.

    intent의 필터/식별자를 분석해 무엇이 결과를 0건으로 만들었을 가능성이 높은지 진단:
        - year_from/year_to 있음 → "연도 조건을 빼고 다시 시도"
        - subject_name 있고 affiliation 없음 → "소속 기관 함께 알려주면 정확도 ↑"
        - identifier_hints 있음 → "식별자 형식 확인"
        - perf_type_hint 있음 → "성과 유형을 빼고 다시 시도"
        - coparticipants 있음 → "공동 참여자 조건이 좁힐 수 있음"
    """
    name = (intent.subject_name or "").strip()
    has_year = intent.year_from is not None or intent.year_to is not None
    has_perf_type = bool(intent.perf_type_hint)
    has_coparticipants = bool(intent.coparticipants)
    ids_kv = _format_identifier_hints(intent)

    suggestions: List[str] = []
    if has_year:
        year_range = []
        if intent.year_from:
            year_range.append(f"{intent.year_from}년 이후")
        if intent.year_to:
            year_range.append(f"{intent.year_to}년 이전")
        year_label = " · ".join(year_range) or "지정한 연도"
        suggestions.append(f"연도 조건({year_label})을 빼고 다시 시도")
    if name and not (intent.subject_affiliation_hint or "").strip():
        suggestions.append(f"'{name}'의 소속 기관을 함께 알려주시면 동명이인 구분이 가능")
    if has_perf_type:
        types_label = ", ".join(intent.perf_type_hint)
        suggestions.append(f"성과 유형({types_label}) 조건을 빼고 다시 시도")
    if has_coparticipants:
        labels = ", ".join(intent.coparticipants)
        suggestions.append(f"공동 참여자({labels}) 조건을 빼고 다시 시도")
    if ids_kv and not name:
        suggestions.append(
            "식별자 형식 확인: pjt_id는 10자리 숫자, pjt_no는 'K-20-...' 코드, "
            "rst_id는 CNL/JNL/PTR/SNW/REP/EQU 접두어"
        )

    # 본문 구성
    if name:
        base = _NO_RESULT_TEMPLATE_TEXTS["subject"].format(name=name)
    elif ids_kv:
        base = _NO_RESULT_TEMPLATE_TEXTS["identifier"].format(ids=ids_kv)
    else:
        base = _NO_RESULT_TEMPLATE_TEXTS["default"]

    if not suggestions:
        return base
    suggestion_text = "\n\n다음 중 하나를 시도해 보세요:\n" + "\n".join(
        f"  - {s}" for s in suggestions
    )
    return base + suggestion_text


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
