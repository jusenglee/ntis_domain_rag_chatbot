"""JudgmentAgent — "사용자가 무엇을 원했는가"만 결정한다.

권한:
    O 사용자 질문 파싱, 의도 분류, 대상 식별
    O SearchTask 생성 (검색 전략 권고 포함)
    O direct_answer / clarification 결정
    X 검색기 직접 호출
    X 검색 결과 후처리

설계 원칙 (2026-05-18 갱신):
    - **정규식 기반 rule-based pre-pass 제거**. 도메인 식별자 형식(pjt_no, rst_id 등)이 다양하고
      바뀔 수 있어 패턴 매칭 오작동의 비용이 너무 크다.
    - 모든 의도 분류·식별자 추출은 **단일 LLM 호출**로 처리한다.
    - SessionMemory.current_context를 LLM payload에 그대로 넘겨 follow-up refine은 LLM이 직접
      판단한다. 휴리스틱 분기 없음.
    - LLM 출력은 `JudgmentDecision` Pydantic 모델로 강제 검증되고, 실패하면 clarification으로
      안전하게 닫는다.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from apps.conversation.session_memory import SessionMemory
from apps.pipeline.contracts import (
    Action,
    Axis,
    Clarification,
    DirectAnswer,
    FilterBundle,
    IdentifierBundle,
    JudgmentDecision,
    SearchTask,
    SubjectAnchor,
    Target,
    build_search_task,
)


# ============================================================================
# 도메인 enum 매핑 (LLM이 반환할 수 있는 값을 보존)
# ============================================================================

# 성과 식별자 접두어 → DataTag 매핑. FinalGuard.ReferenceItem.tag 결정에도 사용.
# 실제 NTIS payload에서 관측되는 접두어:
RST_ID_PREFIX_TO_TAG: Dict[str, str] = {
    "CNL": "IRD_NAI_RI_PAPER",
    "PTR": "IRD_NAI_RI_IPR",
    "SNW": "IRD_NAI_RI_SW",
    "BIN": "IRD_NAI_RI_ORGSM_INFO",
    "BRS": "IRD_NAI_RI_ORGSM_RESOURCE",
    "COM": "IRD_NAI_RI_COMPOUND",
    "REP": "IRD_NAI_RI_RSCH_RPT",
    "TAI": "IRD_NAI_RI_TECH_INFO",
    "NVR": "IRD_NAI_RI_NVR",
    "EQU": "IRD_NAI_RI_FCLT_EQUIP",
}


def rst_id_tag(rst_id: str) -> Optional[str]:
    """rst_id 접두어로부터 tag를 결정.

    LLM/외부 코드가 ReferenceItem.tag를 결정할 때 사용. 접두어 추출 자체는 단일 `-` 기준의
    안전한 split이라 정규식이 아니다.
    """
    head = rst_id.split("-", 1)[0].strip().upper() if rst_id else ""
    return RST_ID_PREFIX_TO_TAG.get(head)


# ============================================================================
# JudgmentAgent
# ============================================================================

class JudgmentAgent:
    """사용자 질문 → JudgmentDecision (LLM 단일 호출).

    의존성:
        llm: ``ainvoke``를 통해 단일 답변(JSON)을 받을 수 있는 ChatModel.
              본 시스템은 OpenAICompatChatModel(vLLM Solar)을 1차로 쓴다.
    """

    def __init__(self, *, llm: Any) -> None:
        self._llm = llm

    # ------------------------------------------------------------------
    # public
    # ------------------------------------------------------------------

    async def decide(
        self,
        *,
        question: str,
        session_memory: SessionMemory,
        request_id: str,
        turn_id: str,
    ) -> JudgmentDecision:
        """질문을 받아 JudgmentDecision으로 결정한다.

        흐름:
            1. 빈 질문 → clarification 안전 닫기
            2. LLM 호출 → JSON 파싱 → JudgmentDecision 구성
            3. 결정적 후처리:
                a) pjt_no 코드 형식 검증 (한글 사업명 오인식 차단)
                b) manifest title substring 매칭으로 식별자 보강
            4. 파싱 실패/오류 → clarification 안전 닫기
        """
        question_norm = (question or "").strip()
        if not question_norm:
            return JudgmentDecision(
                clarification=Clarification(
                    question="질문 내용이 비어 있습니다. 어떤 정보를 원하시는지 알려주세요.",
                    reason="empty_question",
                )
            )

        try:
            decision = await self._llm_decide(
                question=question_norm,
                session_memory=session_memory,
                request_id=request_id,
                turn_id=turn_id,
            )
        except Exception as exc:  # noqa: BLE001 — LLM 실패는 clarification으로 흡수
            logger.exception(f"[JudgmentAgent] llm_decide failed: err={exc}")
            return JudgmentDecision(
                clarification=Clarification(
                    question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                    reason=f"llm_failure: {exc}",
                )
            )

        # ---- Manifest reference 결정적 매핑 (LangChain/LlamaIndex 표준 conversational RAG 패턴) ----
        # LLM이 schema의 `manifest_rank` 필드로 명시적 reference를 반환했을 때, 그 rank의
        # manifest item ID를 SearchTask.identifiers에 박는 단순 매핑. 휴리스틱 매칭 없음.
        if decision.search_task is not None:
            fixed = _resolve_manifest_rank(
                task=decision.search_task,
                session_memory=session_memory,
            )
            if fixed is not None:
                decision = JudgmentDecision(search_task=fixed)

        return decision

    # ------------------------------------------------------------------
    # LLM-based path
    # ------------------------------------------------------------------

    async def _llm_decide(
        self,
        *,
        question: str,
        session_memory: SessionMemory,
        request_id: str,
        turn_id: str,
    ) -> JudgmentDecision:
        """LLM에 JSON 구조 출력을 요청해 JudgmentDecision을 구성한다."""

        system_prompt = _build_system_prompt()
        user_payload = _build_user_payload(question=question, session_memory=session_memory)

        response = await self._llm.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_payload)],
            request_id=request_id,
            conversation_id=turn_id,
            temperature=0.1,
            top_p=0.8,
            max_tokens=768,
        )
        raw = getattr(response, "content", "") or ""
        parsed = _extract_json(raw)
        if parsed is None:
            logger.warning(
                f"[JudgmentAgent] llm_parse_failure raw_len={len(raw)} "
                f"raw_preview={(raw or '')[:200]!r}"
            )
            return JudgmentDecision(
                clarification=Clarification(
                    question="질문 의도를 파악하지 못했습니다. 좀 더 구체적으로 알려주실 수 있나요?",
                    reason="llm_parse_failure",
                )
            )
        logger.debug(
            f"[JudgmentAgent] llm_parsed kind={parsed.get('kind')!r} "
            f"action={parsed.get('action')!r} target={parsed.get('target')!r} "
            f"has_subject={bool(parsed.get('subject'))} "
            f"has_identifiers={bool(parsed.get('identifiers'))} "
            f"has_filters={bool(parsed.get('filters'))}"
        )

        kind = str(parsed.get("kind", "")).strip().lower()
        if kind == "direct_answer":
            return JudgmentDecision(
                direct_answer=DirectAnswer(
                    text=str(parsed.get("text", "")).strip() or "도움을 드리기 위해 더 많은 정보가 필요합니다.",
                    reason=str(parsed.get("reason", "")).strip(),
                )
            )

        if kind == "clarification":
            return JudgmentDecision(
                clarification=Clarification(
                    question=str(parsed.get("question", "")).strip()
                    or "원하시는 정보를 좀 더 구체적으로 알려주실 수 있나요?",
                    options=[str(o).strip() for o in (parsed.get("options") or []) if str(o).strip()],
                    reason=str(parsed.get("reason", "")).strip(),
                )
            )

        if kind == "search":
            return _build_search_decision_from_llm(
                parsed=parsed,
                question=question,
                request_id=request_id,
                turn_id=turn_id,
            )

        # 기타: 안전한 clarification
        return JudgmentDecision(
            clarification=Clarification(
                question="질문을 좀 더 구체적으로 알려주시면 정확하게 검색해 드리겠습니다.",
                reason=f"unknown_kind:{kind}",
            )
        )


# ============================================================================
# Prompt builders & LLM payload
# ============================================================================

def _build_system_prompt() -> str:
    """JudgmentAgent의 LLM 분류기를 위한 system prompt.

    실제 NTIS 컬렉션과 식별자 접두어를 반영. LLM이 식별자/이름/소속/연도/타입을 직접
    JSON 필드로 뽑아낸다 (정규식 매칭 없이).
    """
    return (
        "당신은 NTIS(국가과학기술지식정보서비스) RAG 시스템의 의도 분류 모듈입니다. "
        "사용자의 한국어 질문을 읽고, 시스템이 어떤 SearchTask를 만들어야 하는지 결정합니다.\n"
        "\n"
        "[NTIS 데이터 컬렉션]\n"
        "  - ntis_project_v1 : 과제 (IRD_NAI_PJT_INFO)\n"
        "  - ntis_perf_v1    : 성과 (논문/특허/SW/장비/보고서/생명정보/화합물/기술요약 등)\n"
        "  - ntis_supports   : QNA/MANUAL (지원/도움말)\n"
        "  모든 데이터에 참여연구자(prtcp_mp)와 참여기관(prtcp_org) 정보가 들어 있습니다.\n"
        "\n"
        "[식별자 종류]\n"
        "  - pjt_id (10자리 숫자, 예: 1345214806) : 개별 과제 instance\n"
        "  - pjt_no (코드/숫자 혼합, 예: K-20-L01-C09, S2296183, 22A20130012425) : 과제 그룹\n"
        "  - rst_id (성과 ID. 접두어로 종류 구분):\n"
        "      CNL- 논문, PTR- 특허, SNW- 소프트웨어, REP- 보고서, BIN- 생명정보,\n"
        "      COM- 화합물, TAI- 기술요약, NVR- 신품종, EQU- 시설장비, BRS- 생물자원\n"
        "\n"
        "[절대 규칙]\n"
        "1. 응답은 반드시 단일 JSON 객체. 다른 텍스트/마크다운/주석 금지.\n"
        "2. kind ∈ {search, direct_answer, clarification}\n"
        "3. kind='search'일 때 필수 필드: action, target, query.\n"
        "   - action ∈ {list, detail, stats, topic, download}\n"
        "   - target ∈ {project, perf, people, org, support}\n"
        "   - query : 검색 엔진에 넘길 자연어 질의 (사용자 질문의 핵심 표현 보존)\n"
        "4. 식별자가 명시되면 identifiers에 정확히 분리:\n"
        "   - {\"identifiers\": {\"pjt_id\": [...], \"pjt_no\": [...], \"rst_id\": [...]}}\n"
        "   - 사업명/제목 자체를 pjt_no에 넣지 말 것. pjt_no는 'K-20-L01-C09', 'S2296183' 같은 코드 식별자만.\n"
        "5. 사람 이름이 등장하면 subject에 분리:\n"
        "   - {\"subject\": {\"name\": \"<이름>\", \"kind\": \"people\", \"affiliation\": \"<소속>?\"}}\n"
        "   - 소속은 질문에 있을 때만 채움(없으면 생략).\n"
        "6. 기관 단독 질의면 subject.kind=\"org\".\n"
        "7. 연도/타입 조건은 filters에 분리:\n"
        "   - {\"filters\": {\"year_from\": 2020, \"year_to\": 2023, \"perf_type\": [\"PAPER\"]}}\n"
        "   - perf_type 허용값: PAPER, PATENT, SOFTWARE, REPORT, EQUIPMENT, COMPOUND, "
        "ORGSM_INFO, ORGSM_RESOURCE, TECH_INFO, NVR\n"
        "8. 직전 turn의 previous_subject가 있고 사용자가 \"그 사람\", \"같은 연구자\", "
        "\"2020년 이후만\" 같이 referencing하면, 그 subject를 그대로 살려 SearchTask에 포함.\n"
        "9. **manifest_rank — manifest item 인용을 위한 standard structured-output 필드.** "
        "previous_manifest는 직전 turn에 사용자에게 보여진 항목 리스트입니다. "
        "사용자가 \"N번 항목\", \"<사업명>\", \"그 과제\" 등으로 manifest item을 인용하면, "
        "정확히 그 item의 rank(1-based 정수)를 **manifest_rank** 필드에 넣고 action=\"detail\"로 설정합니다. "
        "이때 identifiers는 비워두세요 — 시스템이 manifest_rank로 정확한 ID를 자동 매핑합니다. "
        "**manifest item의 title을 pjt_no에 직접 넣지 마세요.**\n"
        "10. 인사·잡담 → kind='direct_answer'.\n"
        "11. 정보 부족으로 검색 불가 → kind='clarification' + question.\n"
        "\n"
        "[예시]\n"
        "Q: \"신동구 연구자(한국과학기술정보연구원)의 활동내역\"\n"
        "→ {\"kind\":\"search\",\"action\":\"list\",\"target\":\"people\","
        "\"subject\":{\"name\":\"신동구\",\"kind\":\"people\",\"affiliation\":\"한국과학기술정보연구원\"},"
        "\"query\":\"신동구 연구자 활동내역\"}\n"
        "\n"
        "Q: \"K-20-L01-C09 상세\"\n"
        "→ {\"kind\":\"search\",\"action\":\"detail\",\"target\":\"project\","
        "\"identifiers\":{\"pjt_no\":[\"K-20-L01-C09\"]},\"query\":\"K-20-L01-C09 상세\"}\n"
        "\n"
        "Q: \"EQU-2020-01211283770 정보\"\n"
        "→ {\"kind\":\"search\",\"action\":\"detail\",\"target\":\"perf\","
        "\"identifiers\":{\"rst_id\":[\"EQU-2020-01211283770\"]},\"query\":\"EQU-2020-01211283770\"}\n"
        "\n"
        "Q: \"1345126320 과제 정보\"\n"
        "→ {\"kind\":\"search\",\"action\":\"detail\",\"target\":\"project\","
        "\"identifiers\":{\"pjt_id\":[\"1345126320\"]},\"query\":\"1345126320\"}\n"
        "\n"
        "Q: \"한국화학연구원의 2020년 이후 논문\"\n"
        "→ {\"kind\":\"search\",\"action\":\"list\",\"target\":\"perf\","
        "\"subject\":{\"name\":\"한국화학연구원\",\"kind\":\"org\"},"
        "\"filters\":{\"year_from\":2020,\"perf_type\":[\"PAPER\"]},\"query\":\"한국화학연구원 논문\"}\n"
        "\n"
        "Q: \"2020년 이후의 내역만 보여줘\" (previous_subject: 신동구)\n"
        "→ {\"kind\":\"search\",\"action\":\"list\",\"target\":\"people\","
        "\"subject\":{\"name\":\"신동구\",\"kind\":\"people\"},"
        "\"filters\":{\"year_from\":2020},\"query\":\"신동구 2020년 이후 활동\"}\n"
        "\n"
        "Q: \"8번 항목 상세정보\" (previous_manifest: [{rank:1,…}, …, {rank:8, title:'NTIS …'}, …])\n"
        "→ {\"kind\":\"search\",\"action\":\"detail\",\"target\":\"project\","
        "\"manifest_rank\":8,\"query\":\"8번 항목 상세\"}\n"
        "\n"
        "Q: \"[NTIS 국가R&D참여인력 서비스의 이용현황 로그 관리시스템 연구] 상세\" "
        "(previous_manifest에 그 title을 가진 rank=8 item이 있음)\n"
        "→ {\"kind\":\"search\",\"action\":\"detail\",\"target\":\"project\","
        "\"manifest_rank\":8,\"query\":\"NTIS 국가R&D참여인력 서비스 이용현황 로그 관리시스템 연구\"}\n"
        "\n"
        "Q: \"안녕\"\n"
        "→ {\"kind\":\"direct_answer\",\"text\":\"안녕하세요. 무엇을 도와드릴까요?\"}\n"
        "\n"
        "Q: \"찾아줘\"\n"
        "→ {\"kind\":\"clarification\",\"question\":\"어떤 정보를 찾고 싶으신가요? "
        "과제·논문·연구자 중 어느 것인지 알려주세요.\"}\n"
    )


def _build_user_payload(*, question: str, session_memory: SessionMemory) -> str:
    """LLM에 넘기는 user message 본문 (질문 + 이전 turn subject + 이전 manifest hint)."""
    cc = getattr(session_memory, "current_context", None)
    previous_subject: Dict[str, Any] = {}
    if cc is not None and getattr(cc, "subject_name", None):
        ids_map = dict(getattr(cc, "subject_ids_map", {}) or {})
        previous_subject = {
            "name": cc.subject_name,
            "kind": getattr(cc, "subject_kind", None),
            "identity_status": getattr(cc, "identity_status", None),
            "person_no": (ids_map.get("person_no") or [None])[0] if ids_map.get("person_no") else None,
            "org_id": (ids_map.get("org_id") or [None])[0] if ids_map.get("org_id") else None,
        }

    # 이전 turn의 published manifest. ordinal/title follow-up이 가능하려면 LLM이 이것을 봐야 한다.
    previous_manifest: List[Dict[str, Any]] = []
    snapshot = getattr(cc, "result_manifest", None) if cc is not None else None
    if snapshot is not None and getattr(snapshot, "items", None):
        for item in snapshot.items:
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
            previous_manifest.append(entry)

    payload = {
        "question": question,
        "previous_subject": previous_subject,
        "previous_manifest": previous_manifest,
    }
    return json.dumps(payload, ensure_ascii=False)


# ============================================================================
# JSON 추출 / SearchTask 구성
# ============================================================================

def _extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """LLM이 반환한 텍스트에서 JSON 객체 하나를 추출 (코드펜스/부수 텍스트 허용).

    안전한 단일 정규식: 코드펜스 매칭만 사용. 그것도 실패하면 첫 ``{`` ~ 마지막 ``}``로 폴백.
    """
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


def _resolve_manifest_rank(
    *,
    task: SearchTask,
    session_memory: SessionMemory,
) -> Optional[SearchTask]:
    """LLM 응답의 ``manifest_rank`` (정수)를 직전 turn의 DisplaySnapshot item으로 해소.

    LangChain ConversationalRetrievalChain / LlamaIndex ChatEngine의 표준 패턴: LLM이 reference를
    명시적으로 schema 필드로 반환하고, framework가 그 reference를 결정적으로 ID로 매핑.

    Returns:
        업데이트된 SearchTask. manifest_rank가 없거나 매핑 실패면 None (원본 유지).
    """
    rank = getattr(task, "_manifest_rank", None)
    # SearchTask는 frozen이라 private attr 사용. judgment_reason에 임시 인코딩.
    if rank is None:
        # judgment_reason에서 manifest_rank 메타 추출
        meta = task.judgment_reason or ""
        if "manifest_rank=" in meta:
            try:
                rank_token = meta.split("manifest_rank=", 1)[1].split()[0].strip()
                rank = int(rank_token)
            except (ValueError, IndexError):
                rank = None
    if not isinstance(rank, int) or rank < 1:
        return None

    cc = getattr(session_memory, "current_context", None)
    snapshot = getattr(cc, "result_manifest", None) if cc is not None else None
    if snapshot is None or not getattr(snapshot, "items", None):
        return None
    if rank > len(snapshot.items):
        logger.info(
            f"[JudgmentAgent] manifest_rank={rank} > visible_count={len(snapshot.items)}; "
            "ignoring reference"
        )
        return None

    item = snapshot.items[rank - 1]
    # 우선순위: pjt_id > rst_id > pjt_no > person_no > org_id
    new_ids = task.identifiers.model_copy(update={
        "pjt_id": [item.pjt_id] if item.pjt_id else list(task.identifiers.pjt_id),
        "rst_id": [item.rst_id] if item.rst_id else list(task.identifiers.rst_id),
        "pjt_no": [item.pjt_no] if item.pjt_no else list(task.identifiers.pjt_no),
        "person_no": [item.person_no] if item.person_no else list(task.identifiers.person_no),
        "org_id": [item.org_id] if item.org_id else list(task.identifiers.org_id),
    })
    new_axis = new_ids.best_axis()
    # detail 액션으로 격상 + exact_lookup 전략
    target: Target = item.entity_kind if item.entity_kind in {"project", "perf", "people", "org", "support"} else task.target  # type: ignore[assignment]
    new_strategy = "exact_lookup"
    new_collections = (
        ["ntis_project_v1"] if target == "project"
        else ["ntis_perf_v1"] if target == "perf"
        else list(task.collections)
    )

    logger.info(
        f"[JudgmentAgent] manifest_rank={rank} resolved → "
        f"target={target} ids={new_ids.model_dump()} strategy={new_strategy}"
    )
    return task.model_copy(update={
        "action": "detail",
        "target": target,
        "axis": new_axis,
        "identifiers": new_ids,
        "strategy": new_strategy,
        "limit": 1,
        "display_limit": 1,
        "collections": new_collections,
        "judgment_reason": f"manifest_rank={rank}",
    })


def _build_search_decision_from_llm(
    *,
    parsed: Dict[str, Any],
    question: str,
    request_id: str,
    turn_id: str,
) -> JudgmentDecision:
    """LLM이 만든 search dict를 SearchTask로 검증 + 변환."""
    action_raw = str(parsed.get("action", "list")).strip().lower()
    target_raw = str(parsed.get("target", "project")).strip().lower()

    action: Action = action_raw if action_raw in {"list", "detail", "stats", "topic", "download"} else "list"  # type: ignore[assignment]
    target: Target = target_raw if target_raw in {"project", "perf", "people", "org", "support"} else "project"  # type: ignore[assignment]

    # subject
    subject_dict = parsed.get("subject") or {}
    subject = None
    if isinstance(subject_dict, dict) and subject_dict.get("name"):
        subject_kind = str(subject_dict.get("kind", "people")).strip().lower()
        affiliation_raw = subject_dict.get("affiliation")
        affiliation = (str(affiliation_raw).strip() or None) if affiliation_raw else None
        person_no = subject_dict.get("person_no")
        org_id = subject_dict.get("org_id")
        subject = SubjectAnchor(
            kind=subject_kind if subject_kind in {"people", "org"} else "people",
            display_name=str(subject_dict.get("name")).strip(),
            person_no=str(person_no).strip() if person_no else None,
            org_id=str(org_id).strip() if org_id else None,
            affiliation_org_name=affiliation,
            identity_status=_resolve_identity_status(
                person_no=person_no,
                org_id=org_id,
                affiliation=affiliation,
            ),
        )

    # identifiers
    ids_dict = parsed.get("identifiers") or {}
    identifiers = IdentifierBundle(
        pjt_id=_clean_str_list(ids_dict.get("pjt_id")),
        pjt_no=_clean_str_list(ids_dict.get("pjt_no")),
        rst_id=_clean_str_list(ids_dict.get("rst_id")),
        person_no=_clean_str_list(ids_dict.get("person_no")),
        org_id=_clean_str_list(ids_dict.get("org_id")),
    )

    # filters
    filt_dict = parsed.get("filters") or {}
    filters = _build_filters_from_llm(filt_dict)

    axis_hint: Optional[Axis] = identifiers.best_axis() if identifiers.has_any() else None
    query = str(parsed.get("query", "") or question).strip()

    # manifest_rank: LLM이 직전 turn의 published manifest를 명시적으로 인용했을 때만 채워진다.
    # 결정적 매핑은 JudgmentAgent.decide의 _resolve_manifest_rank에서 처리.
    judgment_reason_parts = [f"llm:{str(parsed.get('reason', ''))[:150]}"]
    raw_rank = parsed.get("manifest_rank")
    if isinstance(raw_rank, int) and raw_rank >= 1:
        judgment_reason_parts.append(f"manifest_rank={raw_rank}")

    task = build_search_task(
        action=action,
        target=target,
        subject=subject,
        identifiers=identifiers,
        filters=filters,
        axis_hint=axis_hint,
        retrieval_query=query,
        request_id=request_id,
        turn_id=turn_id,
        judgment_reason=" ".join(judgment_reason_parts),
    )
    return JudgmentDecision(search_task=task)


def _clean_str_list(value: Any) -> List[str]:
    """LLM이 반환한 list 후보에서 빈 문자열·중복 제거 후 str list로 정규화."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
    else:
        seq = [value]
    out: List[str] = []
    seen = set()
    for v in seq:
        s = str(v).strip()
        if not s or s in seen:
            continue
        out.append(s)
        seen.add(s)
    return out


def _build_filters_from_llm(filt_dict: Dict[str, Any]) -> FilterBundle:
    """LLM 출력의 filters 객체를 FilterBundle로 변환. 잘못된 타입은 None/[]로 폴백."""
    yf_raw = filt_dict.get("year_from")
    yt_raw = filt_dict.get("year_to")

    def _to_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        if 1900 <= n <= 2100:
            return n
        return None

    year_from = _to_int(yf_raw)
    year_to = _to_int(yt_raw)

    perf_types: List[str] = []
    for v in filt_dict.get("perf_type") or []:
        s = str(v).strip().upper()
        if s and s not in perf_types:
            perf_types.append(s)

    lead_orgs = [str(o).strip() for o in (filt_dict.get("lead_org_name") or []) if str(o).strip()]
    participant_orgs = [str(o).strip() for o in (filt_dict.get("participant_org_name") or []) if str(o).strip()]
    keywords = [str(k).strip() for k in (filt_dict.get("domain_keywords") or []) if str(k).strip()]

    return FilterBundle(
        year_from=year_from,
        year_to=year_to,
        perf_type=perf_types,
        lead_org_name=lead_orgs,
        participant_org_name=participant_orgs,
        domain_keywords=keywords,
    )


def _resolve_identity_status(*, person_no: Any, org_id: Any, affiliation: Any) -> str:
    """SubjectAnchor.identity_status 결정 규칙."""
    if person_no or org_id:
        return "resolved"
    if affiliation:
        return "resolved_with_org"
    return "ambiguous_name_only"
