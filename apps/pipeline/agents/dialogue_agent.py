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
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.pipeline.agents.contracts import (
    CompareTarget,
    DialogueIntent,
    DialogueKind,
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
        """질문 분석 → DialogueIntent.

        흐름:
            1. 빈 질문 → kind=clarification (안전 닫기)
            2. LLM 호출 → JSON → DialogueIntent
            3. JSON 파싱 실패/예외 → kind=clarification 안전 닫기
        """
        q = (question or "").strip()
        if not q:
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 내용이 비어 있습니다. 어떤 정보를 원하시는지 알려주세요.",
                reason="empty_question",
                confidence=1.0,
            )

        try:
            return await self._llm_decide(
                question=q,
                session=session,
                request_id=request_id,
                turn_id=turn_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[DialogueAgent] llm_decide failed: err={exc}")
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                reason=f"llm_failure:{exc}",
                confidence=0.1,
            )

    # ------------------------------------------------------------------
    # LLM 분기
    # ------------------------------------------------------------------

    async def _llm_decide(
        self,
        *,
        question: str,
        session: SessionState,
        request_id: str,
        turn_id: str,
        conversation_id: str = "",
    ) -> DialogueIntent:
        system_prompt = _build_system_prompt()
        user_payload = _build_user_payload(question=question, session=session)

        # LLM 어댑터/관측 시스템이 conversation_id 인자를 세션 식별자로 사용하므로,
        # turn_id가 아니라 진짜 conversation_id를 전달한다 (호출자가 명시 인자로 주입).
        cid = conversation_id or session.conversation_id or ""
        response = await self._llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            request_id=request_id,
            conversation_id=cid,
            temperature=0.1,
            top_p=0.8,
            max_tokens=768,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[DialogueAgent] llm_parse_failure raw_len={len(raw)} "
                f"raw_preview={(raw or '')[:200]!r}"
            )
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                reason="llm_parse_failure",
                confidence=0.1,
            )

        try:
            intent = _build_intent_from_llm(parsed, question)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[DialogueAgent] intent_construction_failed: {exc}")
            return DialogueIntent(
                kind="clarification",
                clarification_question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                reason=f"intent_construction_failed:{exc}",
                confidence=0.1,
            )

        logger.debug(
            f"[DialogueAgent] kind={intent.kind} target_hint={intent.target_hint} "
            f"action_hint={intent.action_hint} subject={intent.subject_name!r} "
            f"manifest_rank={intent.manifest_rank} year=({intent.year_from},{intent.year_to})"
        )
        return intent


# ============================================================================
# Prompt builders
# ============================================================================

def _build_system_prompt() -> str:
    """DialogueAgent system prompt.

    LLM은 의도 분류 + 힌트 추출만 한다. collection/strategy/식별자 최종 확정은 다음 단계 책임.
    """
    return (
        "당신은 NTIS(국가과학기술지식정보서비스) RAG 시스템의 **DialogueAgent**입니다. "
        "사용자 발화를 읽고 **사용자가 무엇을 원하는지**만 분류합니다. "
        "검색 전략·컬렉션·식별자 확정은 다른 에이전트가 합니다.\n"
        "\n"
        "[출력 schema — 단일 JSON 객체]\n"
        "{\n"
        '  "kind":             "ask_search"|"ask_detail"|"ask_meta"|"refine_previous"|"compare"|"stats"|"direct_answer"|"clarification",\n'
        '  "target_hint":      "project"|"perf"|"people"|"org"|"support"|null,\n'
        '  "action_hint":      "list"|"detail"|"stats"|"topic"|"download"|null,\n'
        '  "subject_name":     "<사람/기관 이름>"|null,\n'
        '  "subject_kind":     "people"|"org"|null,\n'
        '  "subject_affiliation_hint": "<소속 기관>"|null,\n'
        '  "identifier_hints": {"pjt_id":[...], "pjt_no":[...], "rst_id":[...]} | {},\n'
        '  "manifest_rank":    <정수>|null,\n'
        '  "year_from":        <정수>|null,\n'
        '  "year_to":          <정수>|null,\n'
        '  "perf_type_hint":   ["PAPER"|"PATENT"|"SOFTWARE"|"REPORT"|"EQUIPMENT"|"COMPOUND"|"ORGSM_INFO"|"ORGSM_RESOURCE"|"TECH_INFO"|"NVR"],\n'
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
        "3. ask_meta     — 직전 항목의 **유형/종류만** 묻는 메타 분류 질문. previous_manifest 또는 focused_detail이\n"
        "                  필수. 검색 없이 manifest item의 tag/id로 즉답한다.\n"
        "                  예: \"이게 과제야 성과야?\", \"8번 데이터는 과제인가 성과인가\", \"이거 무슨 종류야?\",\n"
        "                      \"이 항목 유형은?\", \"논문이야 특허야?\"\n"
        "                  **detail 본문(목표·기간·내용)을 요청하면 ask_meta가 아니라 ask_detail.**\n"
        "4. refine_previous — 직전 결과를 조건 추가로 좁힘 (이름은 안 바뀜). 예: \"2020년 이후만\", \"논문만\"\n"
        "5. compare      — 둘 이상 비교. 예: \"A 사업과 B 사업 비교\"\n"
        "6. stats        — 통계·집계. 예: \"연도별 과제 수\"\n"
        "7. direct_answer — 인사·잡담. 예: \"안녕\"\n"
        "8. clarification — 정보 부족으로 검색 불가. 예: \"찾아줘\" (대상 없음)\n"
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
        "[focused_detail — 직전에 사용자가 본 단일 항목 anaphora]\n"
        "focused_detail 이 비어있지 않을 때 사용자가 **\"해당 항목\", \"이 과제\", \"이 논문\",\n"
        "\"방금 본 것\", \"그것\"** 같은 지시어로 가리키면 kind=\"ask_detail\"로 설정하고 **manifest_rank와\n"
        "identifier_hints를 모두 비워두세요.** 시스템이 자동으로 focused_detail anchor를 식별자로 매핑합니다.\n"
        "주의: focused_detail이 있는데 사용자가 새로운 list를 원하면(\"목록\", \"전체\", \"다른\") kind=\"ask_search\"\n"
        "또는 \"refine_previous\"로 분류하고 focused_detail은 무시합니다.\n"
        "\n"
        "[refine_previous — 직전 subject 보존]\n"
        "previous_subject 가 있고 사용자가 이름을 다시 말하지 않고 조건만 바꾸면 kind=\"refine_previous\".\n"
        "subject_name/subject_kind/subject_affiliation_hint 는 previous_subject 값 그대로 복사.\n"
        "예: previous_subject=\"신동구\"+이전 turn 활동내역 발행 + 사용자 \"2020년 이후만\"\n"
        "→ kind=\"refine_previous\", subject_name=\"신동구\", year_from=2020.\n"
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
        "Q: \"신동구 연구자의 연도별 참여 건수\"\n"
        "→ {\"kind\":\"stats\",\"target_hint\":\"people\",\"action_hint\":\"stats\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\","
        "\"query\":\"신동구 연도별 참여 건수\",\"reason\":\"연도별 집계\",\"confidence\":0.9}\n"
        "\n"
        "Q: \"한국과학기술정보연구원의 기관별 사업 수\"\n"
        "→ {\"kind\":\"stats\",\"target_hint\":\"org\",\"action_hint\":\"stats\","
        "\"subject_name\":\"한국과학기술정보연구원\",\"subject_kind\":\"org\","
        "\"query\":\"한국과학기술정보연구원 기관별 사업 수\",\"reason\":\"기관별 집계\",\"confidence\":0.85}\n"
        "\n"
        "Q: \"2020년 이후만 보여줘\" (previous_subject=신동구)\n"
        "→ {\"kind\":\"refine_previous\",\"target_hint\":\"people\",\"action_hint\":\"list\","
        "\"subject_name\":\"신동구\",\"subject_kind\":\"people\",\"year_from\":2020,"
        "\"query\":\"신동구 2020년 이후 활동\",\"reason\":\"직전 subject + 연도 필터\",\"confidence\":0.85}\n"
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
    "refine_previous",
    "compare",
    "stats",
    "direct_answer",
    "clarification",
}
_VALID_TARGETS = {"project", "perf", "people", "org", "support"}
_VALID_ACTIONS = {"list", "detail", "stats", "topic", "download"}


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

    compare_targets: List[CompareTarget] = []
    for entry in parsed.get("compare_targets") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        ck = str(entry.get("kind") or "").strip().lower()
        if name and ck in {"people", "org", "project", "perf"}:
            compare_targets.append(CompareTarget(name=name, kind=ck))  # type: ignore[arg-type]

    query = str(parsed.get("query") or question).strip()

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
        compare_targets=compare_targets,
        query=query,
        direct_text=direct_text,
        clarification_question=clarification_question,
        clarification_options=clarification_options,
        reason=str(parsed.get("reason") or "")[:200],
        confidence=confidence,
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
