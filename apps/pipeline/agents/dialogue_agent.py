"""Phase 3: DialogueAgent — 사용자 의도만 분류.

설계 원칙 (NTIS RAG Agentic Redesign):
    - 출력은 DialogueIntent 단 하나. SearchTask·collection·strategy·식별자 확정은 하지 않는다.
    - LLM 한 번 호출 → JSON 파싱 → DialogueIntent.
    - 직전 turn의 SessionState (current_subject / published_manifest / focused_detail)를 LLM payload로
      전달해 ordinal/title follow-up·refine_previous 분기를 LLM이 직접 인식하게 한다.
    - 결정적 매핑(manifest_rank → identifier, ambiguity 후보 생성 등)은 다음 단계의
      EntityResolverAgent에서 처리한다. DialogueAgent는 "manifest_rank=10"만 채워 넘긴다.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.pipeline.agents.contracts import (
    CompareTarget,
    DialogueIntent,
    DialogueKind,
    IntentClassification,
    SlotExtraction,
)
from apps.pipeline.agents.session_state import SessionState


# ============================================================================
# DialogueAgent
# ============================================================================

class DialogueAgent:
    """사용자 발화 → DialogueIntent.

    의존성:
        llm: ainvoke를 지원하는 ChatModel (Solar/vLLM 권장).
    """

    def __init__(self, *, llm: Any) -> None:
        self._llm = llm

    async def decide(
        self,
        *,
        question: str,
        session: SessionState,
        request_id: str,
        turn_id: str,
        conversation_id: str = "",
    ) -> DialogueIntent:
        """질문 분석 → DialogueIntent. 2-pass 구조 (2026-05-26 근본 원인 1·2 해결).

        Pass 1: 의도 분류 + manifest_rank/direct/clarification 즉답 (짧은 prompt).
        Pass 2: kind에 따라 필요한 슬롯만 추출 (kind-specific 짧은 prompt).
        Merge:  두 결과를 DialogueIntent로 통합해 downstream 호환 유지.

        흐름:
            1. 빈 질문 → kind=clarification (안전 닫기)
            2. Pass 1 호출 → IntentClassification
            3. direct_answer/clarification → Pass 2 skip 후 즉시 종료
            4. Pass 2 호출 → SlotExtraction
            5. merge → DialogueIntent
            6. 어느 단계든 실패 → kind=clarification 안전 닫기
        """
        q = (question or "").strip()
        if not q:
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 내용이 비어 있습니다. 어떤 정보를 원하시는지 알려주세요.",
                reason="empty_question",
                confidence=1.0,
            )

        cid = conversation_id or session.conversation_id or ""
        try:
            classification = await self._classify_pass1(
                question=q, session=session, request_id=request_id, conversation_id=cid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                f"[DialogueAgent] pass1_failure(Pass1 의도 분류 실패) error={exc} "
                f"fallback=clarification(되묻기로 안전 종료)"
            )
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                # legacy 호환 — reason에 "llm_failure" 키워드 포함
                reason=f"llm_failure(pass1):{exc}",
                confidence=0.1,
            )

        # direct_answer / clarification은 Pass 2 불필요 — 즉시 종료.
        if classification.kind in ("direct_answer", "clarification"):
            return _merge_to_intent(classification=classification, slots=None, query=q)

        try:
            slots = await self._extract_pass2(
                question=q, session=session, classification=classification,
                request_id=request_id, conversation_id=cid,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[DialogueAgent] pass2_failure(Pass2 슬롯 추출 실패) "
                f"kind={classification.kind} error={exc} "
                f"fallback=empty_slots(빈 슬롯으로 진행)"
            )
            slots = SlotExtraction()  # 안전한 빈 슬롯으로 fallback

        try:
            intent = _merge_to_intent(classification=classification, slots=slots, query=q)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[DialogueAgent] merge_failure(Pass1+Pass2 병합 실패) error={exc} "
                f"fallback=clarification(되묻기로 안전 종료)"
            )
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                reason=f"merge_failed:{exc}",
                confidence=0.1,
            )

        logger.debug(
            f"[DialogueAgent] intent_ready(의도 분류 완료) "
            f"kind={intent.kind} target_hint={intent.target_hint}(타깃 도메인) "
            f"action_hint={intent.action_hint}(액션 힌트) "
            f"subject={intent.subject_name!r}(대상) "
            f"manifest_rank={intent.manifest_rank}(직전 manifest 인용 번호) "
            f"year=({intent.year_from},{intent.year_to})(연도 범위)"
        )
        return intent

    # ------------------------------------------------------------------
    # LLM 호출 — vLLM guided_json (출력 schema 강제)
    # ------------------------------------------------------------------

    async def _ainvoke_structured(
        self,
        messages: List[Any],
        *,
        schema: Optional[Dict[str, Any]],
        request_id: str,
        conversation_id: str,
        max_tokens: int,
    ) -> Any:
        """schema가 있으면 vLLM guided_json으로 출력 문법을 강제해 호출한다.

        - JSON 파싱 실패·필드 누락·enum 이탈이 디코딩 레벨에서 차단된다.
        - 서버가 guided_json을 미지원(400 등)하면 process 수명 동안 비활성화하고
          평문 모드로 즉시 fallback — 기존 동작과 완전 동일하게 degrade.
        - 그 외 일시 오류는 이번 호출만 평문으로 재시도 (재시도도 실패하면 호출자
          안전망이 처리).
        """
        global _guided_json_runtime_disabled
        if schema is not None and _guided_json_enabled():
            try:
                return await self._llm.ainvoke(
                    messages,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    temperature=0.0,
                    top_p=1.0,
                    max_tokens=max_tokens,
                    extra_body={"guided_json": schema},
                )
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status_code", None)
                if status == 400 or "guided" in str(exc).lower():
                    _guided_json_runtime_disabled = True
                    logger.warning(
                        f"[DialogueAgent] guided_json_unsupported(서버 미지원 감지 — "
                        f"프로세스 수명 동안 비활성화) status={status} error={exc}"
                    )
                else:
                    logger.warning(
                        f"[DialogueAgent] guided_json_call_failed(일시 오류 — "
                        f"이번 호출만 평문 재시도) error={exc}"
                    )
        return await self._llm.ainvoke(
            messages,
            request_id=request_id,
            conversation_id=conversation_id,
            temperature=0.0,
            top_p=1.0,
            max_tokens=max_tokens,
        )

    # ------------------------------------------------------------------
    # Pass 1 — 의도 분류
    # ------------------------------------------------------------------

    async def _classify_pass1(
        self,
        *,
        question: str,
        session: SessionState,
        request_id: str,
        conversation_id: str,
    ) -> IntentClassification:
        """Pass 1: kind + manifest_rank + direct/clarification 즉답."""
        system_prompt = _build_classification_prompt()
        user_payload = _build_user_payload(question=question, session=session)
        response = await self._ainvoke_structured(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            schema=_PASS1_SCHEMA,  # 분류는 결정적 — guided_json으로 schema 강제
            request_id=request_id,
            conversation_id=conversation_id,
            max_tokens=384,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[DialogueAgent][Pass1] parse_failure(JSON 파싱 실패) "
                f"raw_len={len(raw)}(응답 길이) "
                f"raw_preview={(raw or '')[:120]!r}(응답 미리보기) "
                f"fallback=clarification(되묻기)"
            )
            return IntentClassification(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                # legacy 호환 — reason="llm_parse_failure" 사용
                reason="llm_parse_failure",
                confidence=0.1,
            )
        return _build_classification_from_llm(parsed)

    # ------------------------------------------------------------------
    # Pass 2 — kind별 슬롯 추출
    # ------------------------------------------------------------------

    async def _extract_pass2(
        self,
        *,
        question: str,
        session: SessionState,
        classification: IntentClassification,
        request_id: str,
        conversation_id: str,
    ) -> SlotExtraction:
        """Pass 2: kind별 짧은 prompt로 필요한 슬롯만 추출."""
        system_prompt = _build_extraction_prompt(kind=classification.kind)
        if not system_prompt:
            # 슬롯 추출 불필요한 kind (e.g. ask_meta 기본형) → 빈 SlotExtraction.
            return SlotExtraction()
        user_payload = _build_extraction_user_payload(
            question=question, session=session, classification=classification,
        )
        response = await self._ainvoke_structured(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            schema=_build_extraction_schema(classification.kind),
            request_id=request_id,
            conversation_id=conversation_id,
            max_tokens=512,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[DialogueAgent][Pass2] parse_failure(슬롯 추출 JSON 파싱 실패) "
                f"kind={classification.kind} raw_len={len(raw)} "
                f"raw_preview={(raw or '')[:120]!r} "
                f"fallback=empty_slots(빈 슬롯)"
            )
            return SlotExtraction()
        return _build_slots_from_llm(parsed)


# ============================================================================
# Prompt builders (2-pass)
# ============================================================================
# Pass 1: 의도 분류 (kind + manifest_rank + direct/clarification). 짧은 prompt.
# Pass 2: kind-specific 슬롯 추출. kind별 짧은 prompt로 NER 정확도 보장.
# _build_system_prompt: 회귀 가드(prompt 문자열 매칭) 호환 wrapper. 실제 운영 LLM
#   호출은 Pass 1/Pass 2 prompt를 사용한다.


def _build_classification_prompt() -> str:
    """Pass 1 — 의도 분류 + manifest_rank + direct/clarification 즉답 prompt.

    슬롯(subject_name·year·perf_type·sort_by 등)은 이 prompt에서 *절대* 채우지 않는다.
    그 작업은 Pass 2(kind-specific)가 담당하므로 instruction following 충돌이 없다.
    """
    return (
        "당신은 NTIS(국가과학기술지식정보서비스) RAG 시스템의 **의도 분류기**입니다.\n"
        "사용자 발화를 읽고 무엇을 원하는지만 결정합니다. **슬롯(subject_name·연도·"
        "성과 유형·정렬 등)은 다음 단계가 채우므로 이 단계에서는 채우지 마세요.**\n"
        "\n"
        "[출력 schema — 단일 JSON 객체]\n"
        "{\n"
        '  "kind": "ask_search"|"ask_detail"|"ask_meta"|"ask_children"|"ask_similar"|"refine_previous"|"compare"|"stats"|"direct_answer"|"clarification",\n'
        '  "target_hint": "project"|"perf"|"people"|"org"|"support"|null,\n'
        '  "action_hint": "list"|"detail"|"stats"|"topic"|"download"|null,\n'
        '  "manifest_rank": <정수>|null,\n'
        '  "direct_text": "<인사·잡담 응답>"|null,\n'
        '  "clarification_question": "<되묻기 문구>"|null,\n'
        '  "clarification_options": ["...", ...],\n'
        '  "reason": "<짧은 판정 이유>",\n'
        '  "confidence": 0.0~1.0\n'
        "}\n"
        "\n"
        "[kind 정의]\n"
        "1. ask_search   — 일반 검색 (사람/기관/주제로 목록 조회). 예: \"신동구 활동내역\"\n"
        "2. ask_detail   — 단일 대상 상세. 예: \"9번 항목 상세\", \"K-20-... 정보\"\n"
        "                  항목의 **내용·주제·관련성** 질문/이의 제기도 ask_detail + manifest_rank=N:\n"
        "                    - \"6번 항목은 LLM과 연관이 없지 않나?\" → ask_detail (manifest_rank=6)\n"
        "                    - \"N번이 왜 포함됐어?\", \"N번 내용이 뭐야?\" → ask_detail (manifest_rank=N)\n"
        "3. ask_meta     — 직전 항목(또는 manifest 전체)의 유형/분류/통계만.\n"
        "                  허용: \"이게 과제야 성과야?\", \"이 항목 유형은?\", \"전부 같은 인물이야?\"\n"
        "                  **detail 본문 요청은 ask_detail. 인물 검증(\"X 책임자?\")은 ask_children.**\n"
        "4. ask_children — 직전 1개 항목의 자식 엔티티 명단 또는 인물·기관 검증.\n"
        "                  명단 예: \"참여자 목록\", \"이 과제 참여한 사람들\"\n"
        "                  검증 예: \"김수빈 연구책임자?\", \"KISTI 참여했어?\"\n"
        "                  **금지 (이런 경우는 다른 kind로):**\n"
        "                    - \"X 연구자의 다른 활동/연구\" → ask_search\n"
        "                    - \"이 사람이 참여한 다른 항목\" → ask_search\n"
        "                    - \"N번은 X와 연관 없지 않나?\" 같은 항목 내용·관련성 질문/이의\n"
        "                      → ask_detail (manifest_rank=N). 참여자·기관 검증이 아니다.\n"
        "                  즉 ask_children은 \"이 항목 안의 자식\"이지 \"이 자식이 참여한 다른 항목\"이 아니다.\n"
        "                  예: \"유재수 다른 참여연구는?\" → ask_search (subject 활동 검색)\n"
        "5. ask_similar  — 직전 항목과 유사한 다른 항목. focused_detail 필수.\n"
        "6. refine_previous — 직전 결과를 조건 추가로 좁힘.\n"
        "7. compare      — 둘 이상 비교.\n"
        "8. stats        — 통계·집계. 예: \"연도별 과제 수\"\n"
        "9. direct_answer — 인사·잡담. direct_text를 채운다.\n"
        "10. clarification — 정보 부족으로 검색 불가. clarification_question을 채운다.\n"
        "\n"
        "[manifest_rank — 직전 manifest 인용]\n"
        "previous_manifest가 비어있지 않을 때 사용자가 manifest의 한 항목을 가리키면\n"
        "그 정수를 manifest_rank에 채우고 kind=\"ask_detail\"(또는 refine_previous).\n"
        "패턴: \"N번 항목\"/\"몇 번\"/제목 일치/\"[N]\" 인용/사용자가 같은 항목 재요청.\n"
        "\n"
        "[focused_detail — 단일 항목 anaphora]\n"
        "focused_detail이 있고 사용자가 \"해당 항목\", \"이 과제\", \"이 논문\", \"방금 본 것\",\n"
        "\"그것\" 같은 지시어로 가리키면 kind=\"ask_detail\"로 설정하고 manifest_rank는 null로.\n"
        "(시스템이 자동으로 anchor 매핑.)\n"
        "주의: focused_detail이 있는데 사용자가 새 list를 원하면(\"목록\"/\"전체\"/\"다른\")\n"
        "kind=\"ask_search\" 또는 \"refine_previous\"로 분류하고 focused_detail은 무시한다.\n"
        "\n"
        "[refine_previous vs ask_search — 핵심 분기]\n"
        "**refine_previous** 조건:\n"
        "  - previous_subject 또는 previous_manifest가 비어 있지 않음\n"
        "  - 사용자가 **직전 결과를 의미적으로 인정**하고 그 위에 조건만 추가\n"
        "    예: previous_subject=\"신동구\" + \"2020년 이후만\"\n"
        "\n"
        "**ask_search** 조건 — refine_previous로 분류하지 마세요:\n"
        "  - 사용자가 직전 결과의 적합성에 대해 **불만·재요청** 표시\n"
        "    예: \"그게 아니라 LLM 관련을 원했어\", \"이게 아니야\", \"다시 검색해줘\"\n"
        "    단, **특정 N번 항목만 지목한** 내용·관련성 이의(\"6번은 LLM과 관련 없지 않나?\")는\n"
        "    ask_detail(manifest_rank=N) — 목록 전체에 대한 불만·재검색 요청만 ask_search.\n"
        "  - 사용자가 previous_manifest와 **무관한 새 주제·키워드를 도입**\n"
        "  - 사용자가 명시적으로 \"다시 / 새로 / 처음부터\" 요청\n"
        "\n"
        "**모호하면 ask_search를 선택하는 것이 안전** — 잘못된 manifest_filter는 잘못된 결과를 반복하게 만든다.\n"
        "\n"
        "[direct_answer / clarification]\n"
        "- \"안녕\"/잡담 → direct_answer (direct_text 채움).\n"
        "- \"찾아줘\"처럼 대상 없음 → clarification (clarification_question 채움).\n"
        "\n"
        "[절대 규칙]\n"
        "- 응답은 반드시 단일 JSON 객체. 마크다운/주석/추가 텍스트 금지.\n"
        "- subject_name·year_from·perf_type_hint 등 슬롯 필드는 이 단계에서 채우지 않는다.\n"
        "  (다음 단계가 kind에 맞춰 추출한다.)\n"
    )


# kind별 Pass 2 prompt 캐싱
_EXTRACTION_PROMPT_CACHE: Dict[str, str] = {}


def _build_extraction_prompt(*, kind: DialogueKind) -> str:
    """Pass 2 — kind별 슬롯 추출 prompt.

    kind에 따라 필요한 슬롯만 명세 → prompt 짧음·모순 없음.
    슬롯 추출이 불필요한 kind(예: direct_answer/clarification)는 빈 문자열 반환 → 호출자가 LLM 호출 skip.
    """
    cached = _EXTRACTION_PROMPT_CACHE.get(kind)
    if cached is not None:
        return cached

    common_rules = (
        "[NER 규칙 — 모든 kind 공통]\n"
        "- 응답은 반드시 단일 JSON 객체. 마크다운 금지.\n"
        "- subject_name은 **실제 사람/기관 이름만**. 기술 약어·일반명사 절대 금지:\n"
        "  LLM, AI, ML, GPT, NLP, IoT, BD, VR, AR, 빅데이터, 머신러닝, 딥러닝, 인공지능,\n"
        "  자율주행, 양자, 신소재, 전자공학, 파이썬, 리눅스, Chrome 같은 토큰은 subject_name에\n"
        "  박지 마세요. 이런 토큰은 query 본문에 두고 subject_name=null로 비웁니다.\n"
        "- subject_kind: subject_name이 사람이면 \"people\", 기관이면 \"org\". subject_name이 null이면 null.\n"
        "- perf_type_hint는 사용자가 **명시적으로** 성과 유형을 지정한 경우만 (\"논문만\", \"특허만\").\n"
        "  query에 기술 키워드(LLM/AI/SW)가 있다고 자동으로 perf_type을 박지 마세요.\n"
        "- identifier_hints: pjt_id=10자리 숫자, pjt_no=코드 패턴(K-XX-...), rst_id=접두어(CNL-/PTR-/SNW-/REP-/EQU-/...).\n"
        "  사업명/제목 텍스트는 identifier에 박지 마세요.\n"
        "- year_from > year_to 금지. 명시 안 했으면 둘 다 null.\n"
        "- exclude_org_name/exclude_perf_type/exclude_person_name는 \"X 제외/빼고\" 패턴에서만.\n"
    )

    if kind == "ask_search":
        prompt = (
            "당신은 NTIS RAG 챗봇의 **슬롯 추출기**입니다. Pass 1이 분류한 kind=ask_search 의도에 대해\n"
            "필요한 슬롯만 추출합니다.\n"
            "\n"
            "[출력 schema — 단일 JSON 객체]\n"
            "{\n"
            '  "subject_name": "<사람/기관 실명>"|null,\n'
            '  "subject_kind": "people"|"org"|null,\n'
            '  "subject_affiliation_hint": "<소속 기관>"|null,\n'
            '  "coparticipants": ["<공동 참여자>", ...],\n'
            '  "exclude_org_name": [...], "exclude_perf_type": [...], "exclude_person_name": [...],\n'
            '  "identifier_hints": {"pjt_id":[...], "pjt_no":[...], "rst_id":[...]},\n'
            '  "year_from": <int>|null, "year_to": <int>|null,\n'
            '  "perf_type_hint": ["PAPER|PATENT|SOFTWARE|REPORT|EQUIPMENT|COMPOUND|ORGSM_INFO|ORGSM_RESOURCE|TECH_INFO|NVR", ...],\n'
            '  "sort_by": "relevance"|"recent_desc"|"recent_asc",\n'
            '  "length_hint": "brief"|"default"|"detailed",\n'
            '  "aggregate_hint": null,\n'
            '  "compare_targets": []\n'
            "}\n"
            "\n"
            "[추가 규칙 — ask_search]\n"
            "- sort_by: \"최근순/최신순/가장 최근\"→\"recent_desc\", \"오래된 순/예전부터\"→\"recent_asc\", 그 외 \"relevance\".\n"
            "- length_hint: \"간단히/한 줄/짧게\"→\"brief\", \"자세히/상세히\"→\"detailed\", 그 외 \"default\".\n"
            "- coparticipants: \"A와 B가 같이 참여한\"·\"A, B 공동\"이면 1순위를 subject_name에, 나머지를 coparticipants에.\n"
            "- subject_affiliation_hint: 사용자가 소속을 명시한 경우만 (\"X 연구자(KISTI)\").\n"
            "- compare_targets/aggregate_hint는 ask_search에서 사용하지 않음.\n"
            "\n"
            + common_rules
        )
    elif kind == "ask_detail":
        prompt = (
            "Pass 1이 분류한 kind=ask_detail 의도에 대해 슬롯을 추출합니다.\n"
            "manifest_rank는 Pass 1이 이미 채웠을 수 있습니다. 그 경우 identifier_hints는 비워두세요.\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "identifier_hints": {"pjt_id":[...], "pjt_no":[...], "rst_id":[...]},\n'
            '  "length_hint": "brief"|"default"|"detailed",\n'
            "  // 그 외 필드는 null/[] 기본값.\n"
            "}\n"
            "\n"
            "[추가 규칙]\n"
            "- 사용자가 직접 식별자를 말한 경우에만 identifier_hints를 채웁니다.\n"
            "- length_hint는 사용자 표현 기반.\n"
            "\n"
            + common_rules
        )
    elif kind == "ask_meta":
        prompt = (
            "Pass 1이 분류한 kind=ask_meta 의도에 대해 슬롯을 추출합니다.\n"
            "대부분 비어 있는 응답이 정상입니다 (검색 없이 manifest/focused_detail로 즉답하므로).\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "subject_name": "<인물·기관 검증 대상>"|null,\n'
            '  "subject_kind": "people"|"org"|null,\n'
            "  // 그 외 필드는 기본값.\n"
            "}\n"
            "\n"
            "[추가 규칙]\n"
            "- 사용자가 \"X가 책임자야?\" 같은 인물 검증 질문이면 subject_name=X를 채우세요.\n"
            "- 그 외엔 모두 비워두세요.\n"
            "\n"
            + common_rules
        )
    elif kind == "ask_children":
        prompt = (
            "Pass 1이 분류한 kind=ask_children 의도에 대해 슬롯을 추출합니다.\n"
            "사용자 의도가 (a) 명단 요청이면 모두 비우고, (b) 인물·기관 검증이면 subject_name에 검증 대상을 채웁니다.\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "subject_name": "<검증 대상 인물·기관>"|null,\n'
            '  "subject_kind": "people"|"org"|null,\n'
            "  // 그 외 필드는 기본값.\n"
            "}\n"
            "\n"
            "[추가 규칙]\n"
            "- 명단 요청(\"참여자 목록\", \"참여기관 알려줘\")이면 subject_name=null.\n"
            "- 검증 질문(\"김수빈 연구책임자?\", \"KISTI 참여했어?\")이면 subject_name에 검증 대상을 채운다.\n"
            "\n"
            + common_rules
        )
    elif kind == "ask_similar":
        # ask_similar는 focused_detail.title을 query로 사용 — 추가 슬롯 불필요
        prompt = ""
    elif kind == "refine_previous":
        prompt = (
            "Pass 1이 분류한 kind=refine_previous 의도에 대해 슬롯을 추출합니다.\n"
            "사용자가 직전 결과에 추가한 조건(연도/제외/성과 유형 등)만 채웁니다.\n"
            "previous_subject가 있으면 subject_name을 그대로 복사하세요.\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "subject_name": "<previous_subject 이름 복사 또는 null>",\n'
            '  "subject_kind": "people"|"org"|null,\n'
            '  "year_from": <int>|null, "year_to": <int>|null,\n'
            '  "perf_type_hint": [...], "exclude_org_name": [...], "exclude_perf_type": [...], "exclude_person_name": [...],\n'
            '  "coparticipants": [...],\n'
            '  "sort_by": "relevance"|"recent_desc"|"recent_asc",\n'
            '  "length_hint": "brief"|"default"|"detailed",\n'
            "}\n"
            "\n"
            + common_rules
        )
    elif kind == "compare":
        prompt = (
            "Pass 1이 분류한 kind=compare 의도에 대해 비교 대상을 추출합니다.\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "compare_targets": [{"name":"<이름>", "kind":"people|org|project|perf"}, ...],\n'
            '  "length_hint": "brief"|"default"|"detailed",\n'
            "}\n"
            "\n"
            "[추가 규칙]\n"
            "- compare_targets는 최소 2개.\n"
            "- name은 실명·코드·식별자.\n"
            "\n"
            + common_rules
        )
    elif kind == "stats":
        prompt = (
            "Pass 1이 분류한 kind=stats 의도에 대해 집계 슬롯을 추출합니다.\n"
            "\n"
            "[출력 schema]\n"
            "{\n"
            '  "subject_name": "<대상 인물·기관>"|null,\n'
            '  "subject_kind": "people"|"org"|null,\n'
            '  "aggregate_hint": "year"|"lead_org"|"tag"|"perf_type"|"participant_org"|"participant_person"|null,\n'
            '  "year_from": <int>|null, "year_to": <int>|null,\n'
            "}\n"
            "\n"
            "[aggregate_hint 매핑]\n"
            "- \"연도별\" → \"year\" (기본)\n"
            "- \"수행기관별\"/\"기관별\" → \"lead_org\"\n"
            "- \"분야별\"/\"유형별\"/\"종류별\" → project이면 \"tag\", perf이면 \"perf_type\"\n"
            "- \"참여기관별\"/\"공동 참여기관\" → \"participant_org\"\n"
            "- \"참여자별\"/\"공동 참여자\" → \"participant_person\"\n"
            "- 미명시 → null (Planner가 year fallback)\n"
            "\n"
            + common_rules
        )
    else:
        # direct_answer / clarification / 알 수 없는 kind — 추출 불필요
        prompt = ""

    _EXTRACTION_PROMPT_CACHE[kind] = prompt
    return prompt


def _build_extraction_user_payload(
    *,
    question: str,
    session: SessionState,
    classification: IntentClassification,
) -> str:
    """Pass 2 LLM user message: 질문 + 직전 turn 컨텍스트 + Pass 1 분류 결과."""
    payload: Dict[str, Any] = {
        "question": question,
        "pass1": {
            "kind": classification.kind,
            "target_hint": classification.target_hint,
            "action_hint": classification.action_hint,
            "manifest_rank": classification.manifest_rank,
        },
    }
    if session.has_subject():
        sub = session.current_subject
        payload["previous_subject"] = {
            "name": sub.subject_name, "kind": sub.subject_kind,
        }
    if session.has_focused_detail():
        anchor = session.focused_detail.anchor
        payload["focused_detail"] = {
            "kind": anchor.kind, "title": session.focused_detail.title,
        }
    return json.dumps(payload, ensure_ascii=False)


def _build_system_prompt() -> str:
    """**Deprecated 호환 wrapper** — 회귀 가드(prompt 문자열 매칭)용으로만 유지.

    실제 운영 LLM 호출은 `_build_classification_prompt`(Pass 1) +
    `_build_extraction_prompt(kind)`(Pass 2)를 사용한다. 이 함수는 둘을 concat해
    "회귀 가드에서 검사하는 문자열들이 어디든 살아 있다"는 사실만 보존한다.
    """
    return (
        _build_classification_prompt()
        + "\n\n# === Pass 2 (ask_search 슬롯 추출) — 호환 view ===\n\n"
        + _build_extraction_prompt(kind="ask_search")
    )


def _build_system_prompt_v1_legacy() -> str:
    """이전 단일 prompt 본문 — 보존 (롤백 시 참조용). 운영·호환 가드 어디서도 호출하지 않는다."""
    return (
        "당신은 NTIS(국가과학기술지식정보서비스) RAG 시스템의 **DialogueAgent**입니다. "
        "사용자 발화를 읽고 **사용자가 무엇을 원하는지**만 분류합니다. "
        "검색 전략·컬렉션·식별자 확정은 다른 에이전트가 합니다.\n"
        "\n"
        "[출력 schema — 단일 JSON 객체]\n"
        "{\n"
        '  "kind":             "ask_search"|"ask_detail"|"ask_meta"|"ask_children"|"ask_similar"|"refine_previous"|"compare"|"stats"|"direct_answer"|"clarification",\n'
        '  "target_hint":      "project"|"perf"|"people"|"org"|"support"|null,\n'
        '  "action_hint":      "list"|"detail"|"stats"|"topic"|"download"|null,\n'
        '  "subject_name":     "<사람/기관 이름>"|null,\n'
        '  "subject_kind":     "people"|"org"|null,\n'
        '  "subject_affiliation_hint": "<소속 기관>"|null,\n'
        '  "coparticipants":   ["<공동 참여자 이름>", ...],   // "A와 B가 같이 참여한" 패턴, subject 외 추가 인명\n'
        '  "exclude_org_name":   ["<제외할 기관>", ...],     // "X 제외", "Y 빼고" 패턴\n'
        '  "exclude_perf_type":  ["PAPER"|"PATENT"|..., ...],\n'
        '  "exclude_person_name":["<제외할 인명>", ...],\n'
        '  "identifier_hints": {"pjt_id":[...], "pjt_no":[...], "rst_id":[...]} | {},\n'
        '  "manifest_rank":    <정수>|null,\n'
        '  "year_from":        <정수>|null,\n'
        '  "year_to":          <정수>|null,\n'
        '  "perf_type_hint":   ["PAPER"|"PATENT"|"SOFTWARE"|"REPORT"|"EQUIPMENT"|"COMPOUND"|"ORGSM_INFO"|"ORGSM_RESOURCE"|"TECH_INFO"|"NVR"],\n'
        '  "sort_by":          "relevance"|"recent_desc"|"recent_asc",   // 기본 "relevance"(score 순)\n'
        '  "length_hint":      "brief"|"default"|"detailed",         // 답변 길이 선호\n'
        '  "aggregate_hint":   "year"|"lead_org"|"tag"|"perf_type"|"participant_org"|"participant_person"|null,  // kind=stats일 때 집계 축\n'
        '  "compare_targets":  [{"name":"...", "kind":"people|org|project|perf"}, ...]  // kind=compare일 때만\n'
        '  "query":            "<자연어 검색 질의 — 사용자 핵심 표현 보존>",\n'
        '  "direct_text":      "<인사·잡담 응답>" | null,\n'
        '  "clarification_question": "<되묻기 문구>" | null,\n'
        '  "clarification_options":  ["...","..."],\n'
        '  "reason":           "<짧은 판정 이유>",\n'
        '  "confidence":       0.0~1.0\n'
        "}\n"
        "\n"
        "[데이터 도메인]\n"
        "  - ntis_project_v1: 과제 (IRD_NAI_PJT_INFO)\n"
        "  - ntis_perf_v1   : 성과 (논문/특허/SW/장비/보고서/생명정보/화합물/기술요약 등)\n"
        "  - ntis_supports  : QNA/MANUAL\n"
        "\n"
        "[식별자 종류]\n"
        "  - pjt_id : 10자리 숫자 (예: 1345214806)\n"
        "  - pjt_no : 코드/숫자 혼합 (예: K-20-L01-C09, S2296183, 22A20130012425) — **사업명 텍스트가 아님**\n"
        "  - rst_id : 성과 ID, 접두어로 종류 구분:\n"
        "      CNL-=논문, PTR-=특허, SNW-=SW, REP-=보고서, BIN-=생명정보,\n"
        "      COM-=화합물, TAI-=기술요약, NVR-=신품종, EQU-=시설장비, BRS-=생물자원\n"
        "\n"
        "[kind 분류 가이드]\n"
        "1. ask_search   — 일반 검색 (사람/기관/주제로 목록 조회). 예: \"신동구 연구자 활동내역\"\n"
        "2. ask_detail   — 단일 대상 상세. 예: \"K-20-L01-C09 상세\", \"EQU-2020-... 정보\"\n"
        "3. ask_meta     — 직전 항목(또는 manifest 전체)의 **분류/유형/종류 통계만** 묻는 메타 질문.\n"
        "                  previous_manifest 또는 focused_detail이 필수. 검색 없이 manifest item의\n"
        "                  tag/id 또는 manifest 전체 통계로 즉답한다.\n"
        "                  허용 예: \"이게 과제야 성과야?\", \"8번 데이터는 과제인가 성과인가\",\n"
        "                          \"이거 무슨 종류야?\", \"이 항목 유형은?\", \"논문이야 특허야?\",\n"
        "                          \"전부 같은 인물이야?\", \"다 같은 과제야?\" (manifest 전체 통계)\n"
        "                  **금지 (이런 경우는 ask_children으로 분류):**\n"
        "                    - \"X가 책임자/연구책임자야?\", \"X 책임자?\" — 인물 검증/확인 → ask_children\n"
        "                    - \"X 참여했어?\", \"Y가 같이 했나?\" — 참여자 검증/확인 → ask_children\n"
        "                  **detail 본문(목표·기간·내용)을 요청하면 ask_meta가 아니라 ask_detail.**\n"
        "4. ask_children — **직전 1개 항목의 자식 엔티티 명단·검증** 요청. previous_manifest 또는\n"
        "                  focused_detail이 필수이며, 그 항목 *자신*의 참여자·참여기관 목록을 노출한다.\n"
        "                  허용 예 (명단 요청):\n"
        "                    - \"참여자 목록만 보여줘\", \"참여연구자만\", \"참여기관 알려줘\"\n"
        "                    - \"이 과제 참여한 사람들\", \"2번 항목의 연구자들\"\n"
        "                  허용 예 (인물·기관 검증/확인 — subject_name에 검증 대상을 채우세요):\n"
        "                    - \"김수빈 연구책임자?\", \"X가 책임자야?\" — 책임자가 X인지 확인 (subject_name=\"X\")\n"
        "                    - \"홍길동 참여했어?\", \"Y가 같이 했나?\" — Y가 참여자인지 확인 (subject_name=\"Y\")\n"
        "                    - \"KISTI가 수행기관이야?\" — 수행기관 확인 (subject_name=\"KISTI\", subject_kind=\"org\")\n"
        "                    위 검증 질문은 child_entities + participant_role_map에서 subject_name 매칭으로 답한다.\n"
        "                  **금지 (이런 경우는 ask_search로 분류):**\n"
        "                    - \"X 연구자의 *다른* 활동/연구/과제\" → ask_search(subject_name=X)\n"
        "                      (직전 항목의 child가 아니라 X라는 사람의 활동을 새로 검색)\n"
        "                    - \"이 사람이 *참여한* 다른 항목\" → ask_search(subject_name=...)\n"
        "                    - \"이 기관의 *다른* 과제\" → ask_search(subject_name=...)\n"
        "                  즉 ask_children은 \"이 항목 안의 자식 명단·검증\"이지 \"이 자식이 참여한 다른 항목\"이 아니다.\n"
        "5. ask_similar  — 직전 항목과 **유사한 다른 항목**을 찾아달라는 요청. focused_detail이 필수.\n"
        "                  focused_detail.title을 query로 사용해 hybrid_search 호출. anchor 자신은 결과에서 제외.\n"
        "                  예: \"이것과 비슷한 과제\", \"이 항목과 유사한\", \"비슷한 연구\", \"관련 사업\"\n"
        "6. refine_previous — 직전 결과를 조건 추가로 좁힘 (이름은 안 바뀜). 예: \"2020년 이후만\", \"논문만\"\n"
        "7. compare      — 둘 이상 비교. 예: \"A 사업과 B 사업 비교\"\n"
        "8. stats        — 통계·집계. 예: \"연도별 과제 수\"\n"
        "                  aggregate_hint 축 선택:\n"
        "                    - year                 : \"연도별\" (기본)\n"
        "                    - lead_org             : \"수행기관별\" / \"기관별\" (사업의 수행기관)\n"
        "                    - tag / perf_type      : \"분야별\" / \"유형별\" / \"종류별\"\n"
        "                                             (project이면 tag, perf면 perf_type)\n"
        "                    - participant_org      : \"참여기관별\" / \"공동 참여기관\"\n"
        "                    - participant_person   : \"참여자별\" / \"공동 참여자\"\n"
        "                  사용자가 명시 안 했으면 null (Planner가 year fallback).\n"
        "9. direct_answer — 인사·잡담. 예: \"안녕\"\n"
        "10. clarification — 정보 부족으로 검색 불가. 예: \"찾아줘\" (대상 없음)\n"
        "\n"
        "[manifest_rank — 직전 발행 manifest 인용]\n"
        "previous_manifest 가 비어있지 않을 때 사용자가 manifest의 한 항목을 가리키면 그 rank를\n"
        "manifest_rank 필드에 채우고 kind=\"ask_detail\"(또는 refine_previous)로 설정합니다.\n"
        "다음 패턴 모두 manifest 인용입니다:\n"
        "   - \"N번 항목\", \"N번째\", \"몇 번\"\n"
        "   - \"**<제목>** (N)\" — 답변 본문 표기에서 (N)이 manifest rank\n"
        "   - 답변 본문에서 본 [N] 인용\n"
        "   - 사업명/제목이 manifest item의 title과 정확/유사 일치\n"
        "   - 사용자가 화내며 같은 항목을 다시 요청\n"
        "**manifest_rank를 채울 때는 identifier_hints를 비워두세요.** 시스템이 manifest에서 ID를 매핑합니다.\n"
        "**사업명 텍스트를 identifier_hints.pjt_no/pjt_id에 절대 박지 마세요.**\n"
        "\n"
        "[subject_name / subject_kind — 사람/기관 이름만]\n"
        "subject_name은 **실제 사람 이름 또는 기관(법인) 이름**만 채운다. 다음은 절대 subject_name에\n"
        "박지 마세요 — 검색 결과를 0건으로 만드는 회귀가 발생합니다:\n"
        "  - 기술/도메인 약어·일반명사: \"LLM\", \"AI\", \"ML\", \"GPT\", \"NLP\", \"IoT\", \"BD\", \"VR\", \"AR\",\n"
        "                              \"빅데이터\", \"머신러닝\", \"딥러닝\", \"인공지능\", \"자율주행\", \"양자\"\n"
        "  - 학문/연구 분야명: \"생명공학\", \"신소재\", \"전자공학\"\n"
        "  - 제품/도구명: \"파이썬\", \"리눅스\", \"Chrome\"\n"
        "이런 토큰은 모두 `query` 본문에만 두고 subject_name은 null로 비웁니다.\n"
        "예: \"LLM 같은 AI를 농사에 활용한 기술\" → subject_name=null, target_hint=\"project\",\n"
        "    query=\"LLM AI 농사 활용 기술\". (subject 없는 일반 검색)\n"
        "\n"
        "[perf_type_hint — 성과 유형 명시적 요청만]\n"
        "perf_type_hint는 **사용자가 명시적으로 성과 유형을 지정한 경우에만** 채운다 (예: \"논문만\",\n"
        "\"특허만 보여줘\", \"SW만\"). 사용자 query에 'LLM/AI/SW' 같은 기술 키워드가 들어 있다고 해서\n"
        "perf_type_hint=[\"SOFTWARE\"]로 자동 채우지 마세요 — 성과 컬렉션 외 결과를 모두 배제해\n"
        "0건 회귀가 납니다.\n"
        "\n"
        "[focused_detail — 직전에 사용자가 본 단일 항목 anaphora]\n"
        "focused_detail 이 비어있지 않을 때 사용자가 **\"해당 항목\", \"이 과제\", \"이 논문\",\n"
        "\"방금 본 것\", \"그것\"** 같은 지시어로 가리키면 kind=\"ask_detail\"로 설정하고 **manifest_rank와\n"
        "identifier_hints를 모두 비워두세요.** 시스템이 자동으로 focused_detail anchor를 식별자로 매핑합니다.\n"
        "주의: focused_detail이 있는데 사용자가 새로운 list를 원하면(\"목록\", \"전체\", \"다른\") kind=\"ask_search\"\n"
        "또는 \"refine_previous\"로 분류하고 focused_detail은 무시합니다.\n"
        "\n"
        "[exclude_* — 제외(negative) 필터]\n"
        "사용자가 \"X 제외\", \"Y 빼고\", \"~을 제외한\" 패턴으로 특정 항목을 빼달라고 하면 다음 필드를 채운다:\n"
        "   - exclude_org_name: 기관 이름 제외 (예: \"KISTI 제외\" → [\"KISTI\"])\n"
        "   - exclude_perf_type: 성과 유형 제외 (예: \"특허 빼고\" → [\"PATENT\"])\n"
        "   - exclude_person_name: 인명 제외 (예: \"김재수 빼고\" → [\"김재수\"])\n"
        "필터는 must_not으로 결합. 양수(include) 조건과 함께 사용 가능.\n"
        "\n"
        "[coparticipants — 공동 참여 조건]\n"
        "사용자가 두 명 이상이 함께 참여한 결과를 원할 때 (\"A와 B가 같이 참여한\", \"A, B가 공동 연구한\")\n"
        "1순위 인물을 subject_name에, 나머지 인물(들)을 coparticipants 배열에 채운다.\n"
        "시스템은 prtcp_mp_hm_nm_list로 각 이름을 AND로 매칭한다.\n"
        "예: \"신동구와 김재수가 같이 참여한 과제\"\n"
        "→ {\"subject_name\":\"신동구\",\"subject_kind\":\"people\",\"coparticipants\":[\"김재수\"],...}\n"
        "주의: 비교(compare)는 \"A vs B\" 같은 분리 비교. coparticipants는 \"A AND B 모두 참여\".\n"
        "\n"
        "[length_hint — 답변 길이 선호]\n"
        "사용자가 명시적으로 답변 길이를 요청하면 length_hint를 채운다:\n"
        "   - \"간단히\", \"한 줄로\", \"짧게\", \"요약만\" → \"brief\"\n"
        "   - \"자세히\", \"상세히\", \"더 자세히\", \"풀어서\" → \"detailed\"\n"
        "   - 그 외(기본) → \"default\"\n"
        "\n"
        "[sort_by — 정렬 의도]\n"
        "사용자가 명시적으로 정렬을 요청하면 sort_by를 채운다:\n"
        "   - \"최근순\", \"최신순\", \"가장 최근\", \"근래\" → \"recent_desc\"\n"
        "   - \"오래된 순\", \"예전부터\", \"초기부터\" → \"recent_asc\"\n"
        "   - 그 외(기본) → \"relevance\" (Qdrant score 순)\n"
        "주의: 시스템은 stan_yr(연도) 기준으로 정렬한다. 연도 정보가 없는 항목은 끝으로 밀린다.\n"
        "\n"
        "[refine_previous vs ask_search — 핵심 분기]\n"
        "사용자 발화 의도가 (a) 직전 결과를 좁히는 것인지 (b) 직전 결과를 버리고 새로 검색하는 것인지\n"
        "명확히 판단한다. 잘못 분류하면 시스템이 이전의 잘못된 manifest를 부분집합 fetch하는\n"
        "치명적 회귀가 발생한다.\n"
        "\n"
        "**kind=\"refine_previous\" (직전 결과 유지·좁히기) 분류 조건**:\n"
        "  - previous_subject 또는 previous_manifest가 비어 있지 않음\n"
        "  - 사용자가 **직전 결과를 의미적으로 인정**하고 그 위에 조건을 더함\n"
        "    (a) previous_subject가 있고 이름 재언급 없이 조건만 추가:\n"
        "        예: previous_subject=\"신동구\" + \"2020년 이후만\"\n"
        "        → subject_name=\"신동구\"(복사), year_from=2020\n"
        "    (b) previous_manifest가 있고 사용자가 manifest 내 부분집합을 요청:\n"
        "        예: 직전 자동차 과제 10건 + \"공학/전자 관련만 골라줘\"\n"
        "        → 시스템이 직전 manifest를 좁히는 도구 활성화\n"
        "\n"
        "**kind=\"ask_search\" (새 검색) 분류 조건** — refine_previous로 분류하지 마세요:\n"
        "  - 사용자가 직전 결과의 적합성에 대해 **불만·의문·재요청**을 표시\n"
        "    예: \"그게 아니라 LLM 관련을 원했어\", \"내가 요구한 거는 X였는데\", \n"
        "        \"이게 아니야\", \"다시 검색해줘\", \"잘못 찾았네\"\n"
        "  - 사용자가 **previous_manifest와 무관한 새 주제·키워드**를 도입\n"
        "    예: previous_manifest=자동차 과제 + \"LLM 연구\" → 새 검색 (자동차 manifest의 부분집합 아님)\n"
        "  - 사용자가 **명시적으로 \"다시 / 새로 / 처음부터\"**를 요청\n"
        "이런 경우 manifest를 무시하고 ask_search로 분류해야 새 검색이 실행된다.\n"
        "\n"
        "**판단이 모호하면 ask_search를 선택하는 것이 안전** — 새 검색은 사용자가 다시 좁힐 수 있지만,\n"
        "잘못된 manifest_filter는 사용자가 잘못된 결과를 반복적으로 보게 만든다.\n"
        "\n"
        "[예시 출력]\n"
        "Q: \"신동구 연구자(한국과학기술정보연구원)의 활동내역\"\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\",\"subject_affiliation_hint\":\"한국과학기술정보연구원\","
        "\"query\":\"신동구 연구자 활동내역\",\"reason\":\"명시된 사람+소속\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"K-20-L01-C09 상세\"\n"
        "→ {\"kind\":\"ask_detail\",\"target_hint\":\"project\",\"action_hint\":\"detail\","
        "\"identifier_hints\":{\"pjt_no\":[\"K-20-L01-C09\"]},\"query\":\"K-20-L01-C09\","
        "\"reason\":\"코드형 pjt_no 명시\",\"confidence\":0.95}\n"
        "\n"
        "Q: \"EQU-2020-01211283770 정보\"\n"
        "→ {\"kind\":\"ask_detail\",\"target_hint\":\"perf\",\"action_hint\":\"detail\","
        "\"identifier_hints\":{\"rst_id\":[\"EQU-2020-01211283770\"]},\"query\":\"EQU-2020-01211283770\","
        "\"reason\":\"성과 rst_id 명시\",\"confidence\":0.95}\n"
        "\n"
        "Q: \"10번 항목에 대한 상세 정보요청\" (previous_manifest에 10개 항목 있음)\n"
        "→ {\"kind\":\"ask_detail\",\"manifest_rank\":10,\"query\":\"10번 항목 상세\","
        "\"reason\":\"manifest ordinal 인용\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"해당 항목의 참여연구자는?\" (focused_detail 있음, manifest는 stale)\n"
        "→ {\"kind\":\"ask_detail\",\"identifier_hints\":{},\"manifest_rank\":null,"
        "\"query\":\"해당 항목 참여연구자\",\"reason\":\"focused_detail anaphora\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"아까 8번 데이터는 과제인가 성과인가\" (previous_manifest 또는 focused_detail 있음)\n"
        "→ {\"kind\":\"ask_meta\",\"manifest_rank\":8,\"identifier_hints\":{},"
        "\"query\":\"8번 유형 분류\",\"reason\":\"메타 분류 질문\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"이 과제 참여자 목록만 보여줘\" (focused_detail 있음)\n"
        "→ {\"kind\":\"ask_children\",\"identifier_hints\":{},\"manifest_rank\":null,"
        "\"query\":\"참여자 목록\",\"reason\":\"focused_detail child entity 요청\",\"confidence\":0.95}\n"
        "\n"
        "Q: \"이것과 비슷한 과제 더 있어?\" (focused_detail 있음)\n"
        "→ {\"kind\":\"ask_similar\",\"identifier_hints\":{},\"manifest_rank\":null,"
        "\"query\":\"유사 과제\",\"reason\":\"focused_detail 기반 유사 검색\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"유재수 연구책임자의 다른 참여연구는?\" (focused_detail에 X 항목 있음)\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"유재수\",\"subject_kind\":\"people\","
        "\"query\":\"유재수 참여 연구\","
        "\"reason\":\"focused_detail child의 다른 활동 검색 (multi-hop이 아니라 새 subject 검색)\","
        "\"confidence\":0.9}\n"
        "**중요**: 직전 항목의 참여자였더라도 그 사람의 다른 활동을 묻는 것은 ask_children이 아닌\n"
        "ask_search이다 (ask_children은 '이 항목 자체의 자식', 새 subject 활동은 새 검색).\n"
        "\n"
        "Q: \"신동구 연구자의 연도별 참여 건수\"\n"
        "→ {\"kind\":\"stats\",\"target_hint\":\"people\",\"action_hint\":\"stats\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\","
        "\"aggregate_hint\":\"year\","
        "\"query\":\"신동구 연도별 참여 건수\",\"reason\":\"연도별 집계\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"한국과학기술정보연구원의 기관별 사업 수\"\n"
        "→ {\"kind\":\"stats\",\"target_hint\":\"org\",\"action_hint\":\"stats\","
        "\"subject_name\":\"한국과학기술정보연구원\",\"subject_kind\":\"org\","
        "\"aggregate_hint\":\"lead_org\","
        "\"query\":\"한국과학기술정보연구원 기관별 사업 수\",\"reason\":\"기관별 집계\",\"confidence\":0.85}\n"
        "\n"
        "Q: \"LLM 과 같은 AI를 농사에 활용/접목한 기술이 있을까?\"\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"project\",\"action_hint\":\"list\","
        "\"subject_name\":null,\"subject_kind\":null,\"perf_type_hint\":[],"
        "\"query\":\"LLM AI 농사 활용 기술\","
        "\"reason\":\"기술 키워드만 — subject나 perf_type 미지정\",\"confidence\":0.9}\n"
        "(LLM/AI는 기술 약어이므로 subject_name·perf_type_hint에 박지 않는다.)\n"
        "\n"
        "Q: \"자동차 관련 연구과제를 가장 최근순으로 찾아줘\"\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"project\",\"action_hint\":\"list\","
        "\"sort_by\":\"recent_desc\",\"query\":\"자동차 관련 연구과제\","
        "\"reason\":\"최신순 정렬 요청\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"신동구와 김재수가 같이 참여한 과제\"\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\","
        "\"coparticipants\":[\"김재수\"],"
        "\"query\":\"신동구 김재수 공동 참여 과제\",\"reason\":\"공동 참여 AND 조건\","
        "\"confidence\":0.9}\n"
        "\n"
        "Q: \"신동구 활동 중 KISTI 제외하고 보여줘\"\n"
        "→ {\"kind\":\"ask_search\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\","
        "\"exclude_org_name\":[\"KISTI\"],"
        "\"query\":\"신동구 활동\",\"reason\":\"기관 제외 필터\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"2020년 이후만 보여줘\" (previous_subject=신동구)\n"
        "→ {\"kind\":\"refine_previous\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\",\"year_from\":2020,"
        "\"query\":\"신동구 2020년 이후 활동\",\"reason\":\"직전 subject + 연도 필터\",\"confidence\":0.85}\n"
        "\n"
        "Q: \"공학/전자 관련만 골라줘\" (previous_manifest=자동차 list 10건, previous_subject 없음)\n"
        "→ {\"kind\":\"refine_previous\",\"target_hint\":\"project\",\"action_hint\":\"list\","
        "\"subject_name\":null,\"identifier_hints\":{},"
        "\"query\":\"자동차 공학/전자 관련 과제\","
        "\"reason\":\"manifest 부분집합 필터링 의도\",\"confidence\":0.85}\n"
        "\n"
        "Q: \"안녕\"\n"
        "→ {\"kind\":\"direct_answer\",\"direct_text\":\"안녕하세요. 무엇을 도와드릴까요?\","
        "\"reason\":\"인사\",\"confidence\":0.95}\n"
        "\n"
        "Q: \"찾아줘\"\n"
        "→ {\"kind\":\"clarification\",\"clarification_question\":\"어떤 정보를 찾고 싶으신가요? "
        "과제·논문·연구자 중 어느 것인지 알려주세요.\",\"reason\":\"대상 없음\",\"confidence\":0.9}\n"
        "\n"
        "[절대 규칙]\n"
        "- 응답은 반드시 단일 JSON 객체. 마크다운/주석/추가 텍스트 금지.\n"
        "- 사업명/제목 텍스트를 identifier_hints에 박지 말 것 (pjt_no는 코드 패턴만).\n"
        "- manifest 인용은 manifest_rank로만. identifier_hints 비우기.\n"
        "- year_from > year_to 인 응답 금지.\n"
        "- kind=compare면 compare_targets에 최소 2개.\n"
        "- 기술 약어·일반명사(LLM, AI, 빅데이터 등)를 subject_name·perf_type_hint에 박지 말 것.\n"
    )


def _build_user_payload(*, question: str, session: SessionState) -> str:
    """LLM에 넘기는 user message: 질문 + 직전 turn 컨텍스트 hint."""
    payload: Dict[str, Any] = {"question": question}

    # previous_subject
    if session.has_subject():
        sub = session.current_subject
        ids_map = dict(sub.subject_ids_map or {})
        payload["previous_subject"] = {
            "name": sub.subject_name,
            "kind": sub.subject_kind,
            "identity_status": sub.identity_status,
            "affiliation": sub.affiliation_org_name,
            "person_no": (ids_map.get("person_no") or [None])[0] if ids_map.get("person_no") else None,
            "org_id": (ids_map.get("org_id") or [None])[0] if ids_map.get("org_id") else None,
        }

    # previous_manifest
    if session.has_manifest():
        snap = session.published_manifest.snapshot
        items_view: List[Dict[str, Any]] = []
        for item in (snap.items or []):
            entry: Dict[str, Any] = {
                "rank": item.display_rank,
                "title": item.title_text,
                "entity_kind": item.entity_kind,
                "tag": item.doc_type,
            }
            for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
                value = getattr(item, axis, None)
                if value:
                    entry[axis] = value
            items_view.append(entry)
        payload["previous_manifest"] = items_view

    # focused_detail
    if session.has_focused_detail():
        anchor = session.focused_detail.anchor
        payload["focused_detail"] = {
            "kind": anchor.kind,
            "source": anchor.source,
            "pjt_id": anchor.pjt_id,
            "pjt_no": anchor.pjt_no,
            "rst_id": anchor.rst_id,
            "person_no": anchor.person_no,
            "org_id": anchor.org_id,
        }

    return json.dumps(payload, ensure_ascii=False)


# ============================================================================
# JSON 추출 / DialogueIntent 구성
# ============================================================================

def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """LLM 텍스트에서 단일 JSON 객체 추출. 코드펜스 / 부수 텍스트 허용."""
    raw_stripped = (raw or "").strip()
    if not raw_stripped:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_stripped, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        start = raw_stripped.find("{")
        end = raw_stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        candidate = raw_stripped[start : end + 1]
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


_VALID_KINDS = {
    "ask_search",
    "ask_detail",
    "ask_meta",
    "ask_children",
    "ask_similar",
    "refine_previous",
    "compare",
    "stats",
    "direct_answer",
    "clarification",
}
_VALID_TARGETS = {"project", "perf", "people", "org", "support"}
_VALID_ACTIONS = {"list", "detail", "stats", "topic", "download"}


# ============================================================================
# Guided decoding — vLLM guided_json schemas
# ============================================================================
# vLLM(OpenAI-compat)의 guided_json으로 LLM 출력 문법을 schema에 강제한다.
# 효과: JSON 파싱 실패·필드 누락·kind enum 이탈이 디코딩 레벨에서 구조적으로 차단.
# - 환경변수 DIALOGUE_GUIDED_JSON=0 으로 수동 비활성화.
# - 서버 미지원(400) 감지 시 process 수명 동안 자동 비활성화 (평문 모드 degrade).
# - schema는 xgrammar/outlines 호환성을 위해 type/enum/array/object만 사용
#   (pattern·minimum 등 고급 키워드 금지).

_GUIDED_JSON_ENV = "DIALOGUE_GUIDED_JSON"
_guided_json_runtime_disabled = False


def _guided_json_enabled() -> bool:
    if _guided_json_runtime_disabled:
        return False
    return os.getenv(_GUIDED_JSON_ENV, "1").strip().lower() not in {"0", "false", "off"}


_NULLABLE_STR: Dict[str, Any] = {"type": ["string", "null"]}
_NULLABLE_INT: Dict[str, Any] = {"type": ["integer", "null"]}
_STR_LIST: Dict[str, Any] = {"type": "array", "items": {"type": "string"}}

_PERF_TYPES = [
    "PAPER", "PATENT", "SOFTWARE", "REPORT", "EQUIPMENT",
    "COMPOUND", "ORGSM_INFO", "ORGSM_RESOURCE", "TECH_INFO", "NVR",
]
_PERF_TYPE_LIST: Dict[str, Any] = {"type": "array", "items": {"enum": _PERF_TYPES}}

_SUBJECT_KIND: Dict[str, Any] = {"enum": ["people", "org", None]}

_IDENTIFIER_HINTS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"pjt_id": _STR_LIST, "pjt_no": _STR_LIST, "rst_id": _STR_LIST},
    "additionalProperties": False,
}

_SORT_BY: Dict[str, Any] = {"enum": ["relevance", "recent_desc", "recent_asc"]}
_LENGTH_HINT: Dict[str, Any] = {"enum": ["brief", "default", "detailed"]}


def _schema(properties: Dict[str, Any]) -> Dict[str, Any]:
    """모든 property를 required로 강제하는 object schema — '필드 누락' 차단."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties.keys()),
        "additionalProperties": False,
    }


_PASS1_SCHEMA: Dict[str, Any] = _schema({
    "kind": {"enum": sorted(_VALID_KINDS)},
    "target_hint": {"enum": sorted(_VALID_TARGETS) + [None]},
    "action_hint": {"enum": sorted(_VALID_ACTIONS) + [None]},
    "manifest_rank": _NULLABLE_INT,
    "direct_text": _NULLABLE_STR,
    "clarification_question": _NULLABLE_STR,
    "clarification_options": _STR_LIST,
    "reason": {"type": "string"},
    "confidence": {"type": "number"},
})

# kind별 Pass 2 schema — _build_extraction_prompt의 출력 schema와 1:1 대응.
_EXTRACTION_SCHEMA_MAP: Dict[str, Dict[str, Any]] = {
    "ask_search": _schema({
        "subject_name": _NULLABLE_STR,
        "subject_kind": _SUBJECT_KIND,
        "subject_affiliation_hint": _NULLABLE_STR,
        "coparticipants": _STR_LIST,
        "exclude_org_name": _STR_LIST,
        "exclude_perf_type": _PERF_TYPE_LIST,
        "exclude_person_name": _STR_LIST,
        "identifier_hints": _IDENTIFIER_HINTS_SCHEMA,
        "year_from": _NULLABLE_INT,
        "year_to": _NULLABLE_INT,
        "perf_type_hint": _PERF_TYPE_LIST,
        "sort_by": _SORT_BY,
        "length_hint": _LENGTH_HINT,
    }),
    "ask_detail": _schema({
        "identifier_hints": _IDENTIFIER_HINTS_SCHEMA,
        "length_hint": _LENGTH_HINT,
    }),
    "ask_meta": _schema({
        "subject_name": _NULLABLE_STR,
        "subject_kind": _SUBJECT_KIND,
    }),
    "ask_children": _schema({
        "subject_name": _NULLABLE_STR,
        "subject_kind": _SUBJECT_KIND,
    }),
    "refine_previous": _schema({
        "subject_name": _NULLABLE_STR,
        "subject_kind": _SUBJECT_KIND,
        "year_from": _NULLABLE_INT,
        "year_to": _NULLABLE_INT,
        "perf_type_hint": _PERF_TYPE_LIST,
        "exclude_org_name": _STR_LIST,
        "exclude_perf_type": _PERF_TYPE_LIST,
        "exclude_person_name": _STR_LIST,
        "coparticipants": _STR_LIST,
        "sort_by": _SORT_BY,
        "length_hint": _LENGTH_HINT,
    }),
    "compare": _schema({
        "compare_targets": {
            "type": "array",
            "items": _schema({
                "name": {"type": "string"},
                "kind": {"enum": ["people", "org", "project", "perf"]},
            }),
        },
        "length_hint": _LENGTH_HINT,
    }),
    "stats": _schema({
        "subject_name": _NULLABLE_STR,
        "subject_kind": _SUBJECT_KIND,
        "aggregate_hint": {"enum": [
            "year", "lead_org", "tag", "perf_type",
            "participant_org", "participant_person", None,
        ]},
        "year_from": _NULLABLE_INT,
        "year_to": _NULLABLE_INT,
    }),
}


def _build_extraction_schema(kind: DialogueKind) -> Optional[Dict[str, Any]]:
    """Pass 2 guided_json schema. 슬롯 추출이 없는 kind는 None (guided 미적용)."""
    return _EXTRACTION_SCHEMA_MAP.get(kind)


def _build_intent_from_llm(parsed: Dict[str, Any], question: str) -> DialogueIntent:
    """LLM이 반환한 dict를 DialogueIntent로 변환."""
    kind_raw = str(parsed.get("kind", "")).strip().lower()
    if kind_raw not in _VALID_KINDS:
        # 안전망: kind 누락이면 clarification
        return DialogueIntent(
            kind="clarification",
            clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
            reason=f"unknown_kind:{kind_raw!r}",
            confidence=0.2,
        )
    kind: DialogueKind = kind_raw  # type: ignore[assignment]

    def _opt_str(key: str) -> Optional[str]:
        v = parsed.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    target_hint = _opt_str("target_hint")
    if target_hint and target_hint not in _VALID_TARGETS:
        target_hint = None

    action_hint = _opt_str("action_hint")
    if action_hint and action_hint not in _VALID_ACTIONS:
        action_hint = None

    subject_kind_raw = _opt_str("subject_kind")
    subject_kind = subject_kind_raw if subject_kind_raw in {"people", "org"} else None

    identifier_hints: Dict[str, List[str]] = {}
    raw_ids = parsed.get("identifier_hints") or {}
    if isinstance(raw_ids, dict):
        for axis_key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
            v = raw_ids.get(axis_key)
            cleaned = _clean_str_list(v)
            if cleaned:
                identifier_hints[axis_key] = cleaned

    manifest_rank_raw = parsed.get("manifest_rank")
    manifest_rank = _to_pos_int(manifest_rank_raw)

    year_from = _to_year(parsed.get("year_from"))
    year_to = _to_year(parsed.get("year_to"))
    # year range 안전망
    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from

    perf_type_hint: List[str] = []
    for v in parsed.get("perf_type_hint") or []:
        s = str(v).strip().upper()
        if s and s not in perf_type_hint:
            perf_type_hint.append(s)

    coparticipants: List[str] = []
    for v in parsed.get("coparticipants") or []:
        s = str(v).strip()
        if s and s not in coparticipants:
            coparticipants.append(s)

    exclude_org_name = _clean_str_list(parsed.get("exclude_org_name"))
    exclude_perf_type = [s.upper() for s in _clean_str_list(parsed.get("exclude_perf_type"))]
    exclude_person_name = _clean_str_list(parsed.get("exclude_person_name"))

    compare_targets: List[CompareTarget] = []
    for entry in parsed.get("compare_targets") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        ck = str(entry.get("kind") or "").strip().lower()
        if name and ck in {"people", "org", "project", "perf"}:
            compare_targets.append(CompareTarget(name=name, kind=ck))  # type: ignore[arg-type]

    query = str(parsed.get("query") or question).strip()

    # sort_by — LLM 분류만. 결정적 추출 fallback 제거 (2026-05-26 재설계).
    sort_by_raw = _opt_str("sort_by")
    sort_by = sort_by_raw if sort_by_raw in {"relevance", "recent_desc", "recent_asc"} else "relevance"

    # length_hint — LLM 분류만.
    length_hint_raw = _opt_str("length_hint")
    length_hint = length_hint_raw if length_hint_raw in {"brief", "default", "detailed"} else "default"

    # aggregate_hint — stats kind에서 LLM이 분류 (year/lead_org/tag/perf_type/participant_*).
    aggregate_hint_raw = _opt_str("aggregate_hint")
    aggregate_hint = (
        aggregate_hint_raw
        if aggregate_hint_raw in {"year", "lead_org", "tag", "perf_type", "participant_org", "participant_person"}
        else None
    )

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    # kind별 필수 필드 보강 (안전망)
    direct_text = _opt_str("direct_text") if kind == "direct_answer" else None
    if kind == "direct_answer" and not direct_text:
        direct_text = "도움이 필요하시면 좀 더 자세히 말씀해 주세요."

    clarification_question = _opt_str("clarification_question") if kind == "clarification" else None
    if kind == "clarification" and not clarification_question:
        clarification_question = "원하시는 정보를 좀 더 구체적으로 알려주실 수 있나요?"

    clarification_options = [
        str(o).strip() for o in (parsed.get("clarification_options") or []) if str(o).strip()
    ]

    # compare이지만 targets가 부족하면 clarification으로 안전 다운그레이드
    if kind == "compare" and len(compare_targets) < 2:
        return DialogueIntent(
            kind="clarification",
            clarification_question="비교 대상 두 개를 알려주세요. 예: 'A 사업과 B 사업 비교'",
            reason="compare_targets_insufficient",
            confidence=0.5,
            query=query,
        )

    return DialogueIntent(
        kind=kind,
        target_hint=target_hint,  # type: ignore[arg-type]
        action_hint=action_hint,  # type: ignore[arg-type]
        subject_name=_opt_str("subject_name"),
        subject_kind=subject_kind,  # type: ignore[arg-type]
        subject_affiliation_hint=_opt_str("subject_affiliation_hint"),
        identifier_hints=identifier_hints,
        manifest_rank=manifest_rank,
        year_from=year_from,
        year_to=year_to,
        perf_type_hint=perf_type_hint,
        coparticipants=coparticipants,
        exclude_org_name=exclude_org_name,
        exclude_perf_type=exclude_perf_type,
        exclude_person_name=exclude_person_name,
        compare_targets=compare_targets,
        sort_by=sort_by,  # type: ignore[arg-type]
        length_hint=length_hint,  # type: ignore[arg-type]
        aggregate_hint=aggregate_hint,  # type: ignore[arg-type]
        query=query,
        direct_text=direct_text,
        clarification_question=clarification_question,
        clarification_options=clarification_options,
        reason=str(parsed.get("reason") or "")[:200],
        confidence=confidence,
    )


# ============================================================================
# 2-pass: Pass 1 / Pass 2 / merge helpers
# ============================================================================

def _build_classification_from_llm(parsed: Dict[str, Any]) -> IntentClassification:
    """Pass 1 LLM dict → IntentClassification.

    누락·이상값은 안전한 기본값으로 fallback (clarification으로 다운그레이드 아님 — kind=ask_search 안전 기본).
    """
    kind_raw = str(parsed.get("kind", "")).strip().lower()
    if kind_raw not in _VALID_KINDS:
        return IntentClassification(
            kind="clarification",
            clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
            reason=f"pass1_unknown_kind:{kind_raw!r}",
            confidence=0.2,
        )
    kind: DialogueKind = kind_raw  # type: ignore[assignment]

    def _opt_str(key: str) -> Optional[str]:
        v = parsed.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    target_hint = _opt_str("target_hint")
    if target_hint and target_hint not in _VALID_TARGETS:
        target_hint = None
    action_hint = _opt_str("action_hint")
    if action_hint and action_hint not in _VALID_ACTIONS:
        action_hint = None

    manifest_rank = _to_pos_int(parsed.get("manifest_rank"))

    direct_text = _opt_str("direct_text") if kind == "direct_answer" else None
    if kind == "direct_answer" and not direct_text:
        direct_text = "도움이 필요하시면 좀 더 자세히 말씀해 주세요."

    clarification_question = _opt_str("clarification_question") if kind == "clarification" else None
    if kind == "clarification" and not clarification_question:
        clarification_question = "원하시는 정보를 좀 더 구체적으로 알려주실 수 있나요?"

    clarification_options = [
        str(o).strip() for o in (parsed.get("clarification_options") or []) if str(o).strip()
    ]

    confidence_raw = parsed.get("confidence")
    try:
        confidence = float(confidence_raw) if confidence_raw is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return IntentClassification(
        kind=kind,
        target_hint=target_hint,  # type: ignore[arg-type]
        action_hint=action_hint,  # type: ignore[arg-type]
        manifest_rank=manifest_rank,
        direct_text=direct_text,
        clarification_question=clarification_question,
        clarification_options=clarification_options,
        reason=str(parsed.get("reason") or "")[:200],
        confidence=confidence,
    )


def _build_slots_from_llm(parsed: Dict[str, Any]) -> SlotExtraction:
    """Pass 2 LLM dict → SlotExtraction. 누락은 모두 안전한 기본값."""
    def _opt_str(key: str) -> Optional[str]:
        v = parsed.get(key)
        if v is None:
            return None
        s = str(v).strip()
        return s or None

    subject_kind_raw = _opt_str("subject_kind")
    subject_kind = subject_kind_raw if subject_kind_raw in {"people", "org"} else None

    identifier_hints: Dict[str, List[str]] = {}
    raw_ids = parsed.get("identifier_hints") or {}
    if isinstance(raw_ids, dict):
        for axis_key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
            v = raw_ids.get(axis_key)
            cleaned = _clean_str_list(v)
            if cleaned:
                identifier_hints[axis_key] = cleaned

    year_from = _to_year(parsed.get("year_from"))
    year_to = _to_year(parsed.get("year_to"))
    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from

    perf_type_hint: List[str] = []
    for v in parsed.get("perf_type_hint") or []:
        s = str(v).strip().upper()
        if s and s not in perf_type_hint:
            perf_type_hint.append(s)

    coparticipants: List[str] = []
    for v in parsed.get("coparticipants") or []:
        s = str(v).strip()
        if s and s not in coparticipants:
            coparticipants.append(s)

    sort_by_raw = _opt_str("sort_by")
    sort_by = sort_by_raw if sort_by_raw in {"relevance", "recent_desc", "recent_asc"} else "relevance"
    length_hint_raw = _opt_str("length_hint")
    length_hint = length_hint_raw if length_hint_raw in {"brief", "default", "detailed"} else "default"
    aggregate_hint_raw = _opt_str("aggregate_hint")
    aggregate_hint = (
        aggregate_hint_raw
        if aggregate_hint_raw in {"year", "lead_org", "tag", "perf_type", "participant_org", "participant_person"}
        else None
    )

    compare_targets: List[CompareTarget] = []
    for entry in parsed.get("compare_targets") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        ck = str(entry.get("kind") or "").strip().lower()
        if name and ck in {"people", "org", "project", "perf"}:
            compare_targets.append(CompareTarget(name=name, kind=ck))  # type: ignore[arg-type]

    return SlotExtraction(
        subject_name=_opt_str("subject_name"),
        subject_kind=subject_kind,  # type: ignore[arg-type]
        subject_affiliation_hint=_opt_str("subject_affiliation_hint"),
        coparticipants=coparticipants,
        exclude_org_name=_clean_str_list(parsed.get("exclude_org_name")),
        exclude_perf_type=[s.upper() for s in _clean_str_list(parsed.get("exclude_perf_type"))],
        exclude_person_name=_clean_str_list(parsed.get("exclude_person_name")),
        identifier_hints=identifier_hints,
        year_from=year_from,
        year_to=year_to,
        perf_type_hint=perf_type_hint,
        sort_by=sort_by,  # type: ignore[arg-type]
        length_hint=length_hint,  # type: ignore[arg-type]
        aggregate_hint=aggregate_hint,  # type: ignore[arg-type]
        compare_targets=compare_targets,
    )


def _merge_to_intent(
    *,
    classification: IntentClassification,
    slots: Optional[SlotExtraction],
    query: str,
) -> DialogueIntent:
    """Pass 1 classification + Pass 2 slots → DialogueIntent (downstream 호환 view).

    slots=None은 direct_answer/clarification (Pass 2 skip) 경로.
    compare이지만 targets<2면 clarification으로 안전 다운그레이드.
    """
    s = slots or SlotExtraction()

    if classification.kind == "compare" and len(s.compare_targets) < 2:
        return DialogueIntent(
            kind="clarification",
            clarification_question="비교 대상 두 개를 알려주세요. 예: 'A 사업과 B 사업 비교'",
            reason="compare_targets_insufficient",
            confidence=0.5,
            query=query,
        )

    return DialogueIntent(
        kind=classification.kind,
        target_hint=classification.target_hint,
        action_hint=classification.action_hint,
        subject_name=s.subject_name,
        subject_kind=s.subject_kind,
        subject_affiliation_hint=s.subject_affiliation_hint,
        identifier_hints=s.identifier_hints,
        manifest_rank=classification.manifest_rank,
        year_from=s.year_from,
        year_to=s.year_to,
        perf_type_hint=s.perf_type_hint,
        coparticipants=s.coparticipants,
        exclude_org_name=s.exclude_org_name,
        exclude_perf_type=s.exclude_perf_type,
        exclude_person_name=s.exclude_person_name,
        compare_targets=s.compare_targets,
        sort_by=s.sort_by,
        length_hint=s.length_hint,
        aggregate_hint=s.aggregate_hint,
        query=query,
        direct_text=classification.direct_text,
        clarification_question=classification.clarification_question,
        clarification_options=classification.clarification_options,
        reason=classification.reason,
        confidence=classification.confidence,
    )


def _clean_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    seq = list(value) if isinstance(value, (list, tuple, set)) else [value]
    out: List[str] = []
    seen = set()
    for v in seq:
        s = str(v).strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    return out


def _to_pos_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def _to_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1900 <= n <= 2100 else None
