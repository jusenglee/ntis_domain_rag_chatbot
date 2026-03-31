"""실행 계층의 질의 분기와 결과 shaping을 돕는 RAG retrieval 헬퍼 모음."""

from __future__ import annotations

import re
from inspect import signature
from typing import Any, Dict, Optional
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import IntentPayloadV3
from apps.core.followup_resolution import build_followup_clarification_message, build_followup_clarification_payload

from apps.api.services.context_helpers import resolve_title_from_payload
from apps.api.services.detail_contract import FIELD_ALIASES, extract_requested_fields
from apps.api.contracts.runtime_contracts import friendly_strategy_violation_message


_DETAIL_RAW_PASSTHROUGH_FIELDS = (
    "pjt_id",
    "pjt_no",
    "rst_id",
    "person_no",
    "hm_id",
    "org_id",
    "org_code",
    "org_cd",
    "biz_no",
    "org_no",
    "doi",
    "issn",
    "stan_yr",
    "org_nm",
    "summary",
    "content",
    "content1",
    "content2",
    "content_text",
    "start_dt",
    "end_dt",
    "dt1",
    "dt2",
    "rndco_tot_amt",
    "kor_pjt_nm",
    "eng_pjt_nm",
)


def _load_run_rag_ab_compare() -> Any:
    """실제 runtime 구현을 지연 import한다."""
    from apps.core.rag_pipeline import run_rag_ab_compare as runtime_run_rag_ab_compare

    return runtime_run_rag_ab_compare


def _run_rag_ab_compare_proxy(*args: Any, **kwargs: Any) -> Any:
    """기존 monkeypatch 포인트를 유지하기 위한 모듈 레벨 프록시다."""
    return _load_run_rag_ab_compare()(*args, **kwargs)


run_rag_ab_compare = _run_rag_ab_compare_proxy


def _get_run_rag_ab_compare() -> Any:
    """패치된 함수가 있으면 그것을, 아니면 지연 import 구현을 반환한다."""
    patched = globals().get("run_rag_ab_compare")
    if patched is not None and patched is not _run_rag_ab_compare_proxy:
        return patched
    return _load_run_rag_ab_compare()


def _get_normalized_intent(state: Any) -> Any:
    """state의 intent payload에서 실제 `normalized_intent` 객체를 꺼낸다.

    wrapper shape가 다른 state/payload에서도 공통적으로 필요한 정보를 읽기 위한 헬퍼다.
    """
    payload = getattr(state, "intent_payload", None)
    return getattr(payload, "normalized_intent", None) if payload else None


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """여러 후보 source에서 특정 속성의 첫 값을 찾아 반환한다.

    knowledge sufficiency, normalized intent, question analysis가 같은 필드를 공유하므로
    우선순위를 두고 읽기 위해 사용한다.
    """
    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            value = source.get(key)
        else:
            value = getattr(source, key, None)
        if value is not None:
            return value
    return default


def _get_strategy_meta(state: Any) -> Dict[str, Any]:
    """state에서 strategy 메타데이터를 안전하게 꺼내 dict로 반환한다."""
    payload = getattr(state, "intent_payload", None)
    strategy_meta = getattr(payload, "strategy_meta", None) if payload else None
    return dict(strategy_meta or {})


def _get_view_state(state: Any) -> Any:
    """state가 들고 있는 view_state를 읽는 얇은 헬퍼다."""
    return getattr(state, "view_state", None)


def _normalize_id_values(values: Any) -> list[str]:
    """단일 값 또는 시퀀스 형태의 식별자 후보를 중복 없는 문자열 리스트로 정리한다."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _first_non_empty_text(*values: Any) -> Optional[str]:
    """여러 후보 중 첫 번째 유효한 문자열을 반환한다."""
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _build_anchor_requested_terms(question: Any) -> list[str]:
    """질문에서 anchor 유지에 필요한 요청 축 용어를 추출한다.

    follow-up detail 질문에서 원래 앵커를 보존한 검색 질의를 재구성할 때 사용한다.
    """
    question_text = str(question or "").strip()
    requested_fields = extract_requested_fields(question_text)
    preferred_fields = [
        field
        for field in ("researchers", "lead_org", "participant_org", "year")
        if field in requested_fields
    ]
    lowered_question = question_text.lower()
    terms: list[str] = []
    seen: set[str] = set()
    for field in preferred_fields:
        aliases = [str(alias or "").strip() for alias in (FIELD_ALIASES.get(field) or []) if str(alias or "").strip()]
        term = next((alias for alias in aliases if alias.lower() in lowered_question), None)
        if term is None:
            term = next((alias for alias in aliases if any(ord(ch) > 127 for ch in alias)), None)
        if term is None:
            term = aliases[0] if aliases else ""
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _has_anchor_seed_ids(ids_map: Any) -> bool:
    """ids_map 안에 실제 anchor seed로 쓸 식별자가 존재하는지 검사한다."""
    if not isinstance(ids_map, dict):
        return False
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        if _normalize_id_values(ids_map.get(key)):
            return True
    return False


def has_active_anchor_seed(state: Any) -> bool:
    """현재 execution truth에 활성 anchor seed가 있는지 판별한다."""
    normalized_intent = _get_normalized_intent(state)

    ids_map = getattr(normalized_intent, "ids_map", None) or {}
    if isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map") or {}

    return _has_anchor_seed_ids(ids_map)


def get_followup_anchor_context(state: Any, *, active_only: bool = False) -> Dict[str, Any]:
    """follow-up 해석에 필요한 anchor 문맥을 하나의 dict로 조립한다.

    active execution seed만 볼지, view_state와 strategy_meta의 보조 정보까지 함께 볼지
    `active_only` 플래그로 제어한다.
    """
    normalized_intent = _get_normalized_intent(state)
    ids_map = getattr(normalized_intent, "ids_map", None) or {}
    if isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map") or {}

    if active_only and not has_active_anchor_seed(state):
        return {
            "present": False,
            "anchor_source": None,
            "anchor_reference_kind": None,
            "entity_key": None,
            "entity_kind": None,
            "title_text": None,
        }

    view_state = _get_view_state(state)
    latest_focus = getattr(view_state, "latest_focus_entity", None) if view_state is not None else None

    strategy_meta = _get_strategy_meta(state)
    selected_prev_item = dict(strategy_meta.get("selected_prev_item") or {})
    focus_entity = dict(strategy_meta.get("focus_entity") or {})

    def first_value(key: str) -> Optional[str]:
        values = _normalize_id_values((ids_map or {}).get(key))
        candidates = [*(values[:1] or [])]
        if not active_only:
            candidates.append(getattr(latest_focus, key, None))
        candidates.extend([focus_entity.get(key), selected_prev_item.get(key)])
        return _first_non_empty_text(*candidates)

    resolved_ids = {
        "pjt_id": first_value("pjt_id"),
        "pjt_no": first_value("pjt_no"),
        "rst_id": first_value("rst_id"),
        "person_no": first_value("person_no"),
        "org_id": first_value("org_id"),
        "org_code": first_value("org_code"),
        "biz_no": first_value("biz_no"),
        "doi": first_value("doi"),
        "issn": first_value("issn"),
    }

    title_candidates = []
    if not active_only:
        title_candidates.append(getattr(latest_focus, "title_text", None))
    title_candidates.extend([
        focus_entity.get("title_text"),
        selected_prev_item.get("title"),
    ])
    title_text = _first_non_empty_text(*title_candidates)

    entity_key = _first_non_empty_text(
        resolved_ids["pjt_id"],
        resolved_ids["rst_id"],
        resolved_ids["person_no"],
        resolved_ids["org_id"],
        resolved_ids["org_code"],
        resolved_ids["biz_no"],
        resolved_ids["doi"],
        resolved_ids["issn"],
        resolved_ids["pjt_no"],
        strategy_meta.get("focus_entity_key"),
    )

    entity_kind_candidates = []
    if not active_only:
        entity_kind_candidates.append(getattr(latest_focus, "kind", None))
    entity_kind_candidates.extend([
        focus_entity.get("kind"),
        selected_prev_item.get("context_kind"),
    ])

    payload: Dict[str, Any] = {
        "present": bool(entity_key or title_text),
        "anchor_source": (
            strategy_meta.get("anchor_source")
            or (None if active_only else getattr(latest_focus, "source", None))
            or strategy_meta.get("seed_source")
        ),
        "anchor_reference_kind": (
            strategy_meta.get("anchor_reference_kind")
            or strategy_meta.get("followup_reference_kind")
        ),
        "entity_key": entity_key,
        "entity_kind": _first_non_empty_text(*entity_kind_candidates) or "project",
        "title_text": title_text,
    }
    payload.update(resolved_ids)
    return payload


def _resolve_followup_anchor_context(state: Any) -> Dict[str, Any]:
    """보조 호환용 wrapper로, 전체 anchor 문맥을 반환한다."""
    return get_followup_anchor_context(state, active_only=False)


def _query_mentions_anchor(query: Any, anchor_context: Dict[str, Any]) -> bool:
    """현재 질의가 anchor 식별자나 제목 단서를 이미 포함하는지 검사한다."""
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return False
    anchor_tokens = [
        str(anchor_context.get(key) or "").strip().lower()
        for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")
    ]
    if any(token and token in normalized_query for token in anchor_tokens):
        return True

    title_text = str(anchor_context.get("title_text") or "").strip()
    if not title_text:
        return False
    normalized_title = title_text.lower()
    if normalized_title and normalized_title in normalized_query:
        return True
    title_terms = {term for term in _extract_topic_terms(title_text) if len(term) >= 2}
    query_terms = _extract_topic_terms(query)
    return bool(title_terms and title_terms.intersection(query_terms))


def _build_anchor_preserving_query_from_context(*, state: Any, query: Any, anchor_context: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """anchor를 잃지 않도록 질의를 다시 구성하고 보조 메타데이터를 남긴다."""
    metadata = {
        "anchor_present": anchor_context.get("present", False),
        "anchor_source": anchor_context.get("anchor_source"),
        "anchor_reference_kind": anchor_context.get("anchor_reference_kind"),
        "anchor_entity_key": anchor_context.get("entity_key"),
        "anchor_query_repaired": False,
        "anchor_repair_reason": None,
    }
    if not anchor_context.get("present"):
        return str(query or ""), metadata

    anchor_phrase = _first_non_empty_text(
        anchor_context.get("title_text"),
        anchor_context.get("pjt_id"),
        anchor_context.get("pjt_no"),
        anchor_context.get("rst_id"),
        anchor_context.get("person_no"),
        anchor_context.get("org_id"),
        anchor_context.get("org_code"),
        query,
    ) or ""
    requested_terms = _build_anchor_requested_terms(getattr(state, "question", ""))
    repaired_terms: list[str] = []
    seen: set[str] = set()
    for part in [anchor_phrase, *requested_terms]:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        repaired_terms.append(text)
    repaired_query = " ".join(repaired_terms) or str(query or "")
    metadata["anchor_query_repaired"] = repaired_query != str(query or "")
    metadata["anchor_repair_reason"] = "anchor_axis_lost" if metadata["anchor_query_repaired"] else None
    return repaired_query, metadata


def repair_query_for_resolved_anchor(*, state: Any, query: Any) -> tuple[str, Dict[str, Any]]:
    """resolved detail follow-up에서 anchor가 빠진 질의를 복구한다."""
    normalized_intent = _get_normalized_intent(state)
    output_type = str(_pick_attr(normalized_intent, key="output_type", default="") or "").strip().lower()
    action = str(_pick_attr(normalized_intent, key="action", default="") or "").strip().lower()
    anchor_context = get_followup_anchor_context(state, active_only=True)
    metadata = {
        "anchor_present": anchor_context.get("present", False),
        "anchor_source": anchor_context.get("anchor_source"),
        "anchor_reference_kind": anchor_context.get("anchor_reference_kind"),
        "anchor_entity_key": anchor_context.get("entity_key"),
        "anchor_query_repaired": False,
        "anchor_repair_reason": None,
    }
    if not anchor_context.get("present"):
        return str(query or ""), metadata
    if action != "detail" and output_type != "detail":
        return str(query or ""), metadata
    if _query_mentions_anchor(query, anchor_context):
        return str(query or ""), metadata
    return _build_anchor_preserving_query_from_context(state=state, query=query, anchor_context=anchor_context)


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+|[\uac00-\ud7a3]{2,}")
_GENERIC_QUERY_TERMS = {
    "알려줘",
    "보여줘",
    "조회",
    "목록",
    "리스트",
    "정보",
    "내용",
    "건",
    "개",
    "top",
    "the",
    "and",
    "for",
}
_PROJECT_AXIS_TERMS = {"과제", "project", "projects"}
_PERF_AXIS_TERMS = {"성과", "output", "outputs", "논문", "특허", "보고서"}
_DETAIL_AXIS_TERMS = {"상세", "detail", "details"}
_STATS_AXIS_TERMS = {"통계", "추이", "trend", "trends", "집계"}

_ORG_ROLE_TERMS = {"\uc8fc\uad00\uae30\uad00", "\uc218\ud589\uae30\uad00", "\ucc38\uc5ec\uae30\uad00", "\uc18c\uc18d\uae30\uad00", "lead_org", "participant_org", "affiliation"}
_ORG_SUFFIXES = ("\uae30\uad00", "\ub300\ud559", "\uc5f0\uad6c\uc6d0", "\uc5f0\uad6c\uc18c", "\uc13c\ud130", "\ud559\uad50", "\ub7a9", "lab")
_QUOTED_TERM_RE = re.compile(r"[\"']([^\"']{2,80})[\"']")
_ORG_TOKEN_RE = re.compile(r'([A-Za-z][A-Za-z0-9&._-]{1,31}|[\uac00-\ud7a3A-Za-z0-9]{2,32}(?:\ub300\ud559|\uc5f0\uad6c\uc6d0|\uc5f0\uad6c\uc18c|\uc13c\ud130|\ud559\uad50|\uae30\uad00))')
_PEOPLE_TOKEN_RE = re.compile(r'([\uac00-\ud7a3]{2,8}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:\uc5f0\uad6c\uc790|\uc5f0\uad6c\uc6d0|\uad50\uc218)?')


def _extract_org_role_terms(text: Any) -> set[str]:
    """질문에서 기관 역할 용어를 추출한다."""
    normalized = _normalize_query_text(text)
    return {term for term in _ORG_ROLE_TERMS if term in normalized}


def _extract_org_terms(text: Any) -> set[str]:
    """질문에서 기관명 후보를 추출해 소문자 집합으로 반환한다."""
    normalized = str(text or "").strip()
    if not normalized:
        return set()
    results: set[str] = set()
    for match in _ORG_TOKEN_RE.finditer(normalized):
        token = str(match.group(1) or "").strip()
        lowered = token.lower()
        if len(token) < 2:
            continue
        if any(token.endswith(suffix) for suffix in _ORG_SUFFIXES) or token.isupper():
            results.add(lowered)
    return results


def _extract_people_terms(text: Any) -> set[str]:
    """질문에서 연구자명 후보를 추출한다."""
    normalized = str(text or "").strip()
    if not normalized or not any(marker in normalized for marker in ("\uc5f0\uad6c\uc790", "\uc5f0\uad6c\uc6d0", "\uad50\uc218", "\ucc45\uc784\uc790")):
        return set()
    results: set[str] = set()
    for match in _PEOPLE_TOKEN_RE.finditer(normalized):
        token = str(match.group(1) or "").strip()
        if len(token) >= 2 and not token.isdigit():
            results.add(token.lower())
    return results


def _extract_title_terms(text: Any) -> set[str]:
    """따옴표로 감싼 제목성 표현을 추출한다."""
    normalized = str(text or "")
    return {str(match.group(1) or "").strip().lower() for match in _QUOTED_TERM_RE.finditer(normalized) if str(match.group(1) or "").strip()}



def _normalize_query_text(text: Any) -> str:
    """질의를 비교용 소문자 문자열로 정규화한다."""
    return str(text or "").strip().lower()


def _is_korean_char(ch: str) -> bool:
    """문자가 한글 완성형 음절 범위에 속하는지 판별한다."""
    code = ord(ch)
    return 0xAC00 <= code <= 0xD7A3


def _extract_query_tokens(text: Any) -> list[str]:
    """질의를 한글/영숫자 토큰 단위로 잘라 drift 검출에 쓰기 쉽게 만든다."""
    normalized = _normalize_query_text(text)
    tokens: list[str] = []
    current: list[str] = []
    current_kind: Optional[str] = None

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            tokens.append("".join(current))
            current = []
            current_kind = None

    for ch in normalized:
        if _is_korean_char(ch):
            kind = "ko"
        elif ch.isalnum() or ch in {"_", "-"}:
            kind = "ascii"
        else:
            flush()
            continue
        if current_kind is not None and kind != current_kind:
            flush()
        current.append(ch)
        current_kind = kind
    flush()
    return [token for token in tokens if token]


def _extract_identifier_like_tokens(text: Any) -> set[str]:
    """연도나 영숫자 조합처럼 식별자에 가까운 토큰을 뽑는다."""
    protected: set[str] = set()
    for token in _extract_query_tokens(text):
        if re.fullmatch(r"\d{4,}", token):
            protected.add(token)
        elif any(ch.isdigit() for ch in token) and any(("a" <= ch.lower() <= "z") for ch in token):
            protected.add(token)
    return protected


def _extract_topic_terms(text: Any) -> set[str]:
    """일반적인 불용성 표현을 제외한 주제 토큰만 추린다."""
    return {
        token
        for token in _extract_query_tokens(text)
        if token not in _GENERIC_QUERY_TERMS and not token.isdigit()
    }


def _contains_any_term(terms: set[str], candidates: set[str]) -> bool:
    """두 용어 집합이 교집합을 가지는지 반환한다."""
    return bool(terms.intersection(candidates))


def detect_retrieval_query_drift(*, raw_query: Any, hint_query: Any) -> tuple[bool, list[str]]:
    """planner hint query가 원 질문의 축을 잃었는지 검사한다.

    식별자, 기관/연구자 용어, 제목, 프로젝트/성과 축 변화 등을 비교해
    raw query fallback이 필요한지 판단할 근거를 만든다.
    """
    raw = _normalize_query_text(raw_query)
    hint = _normalize_query_text(hint_query)
    if not raw or not hint or raw == hint:
        return False, []

    reasons: list[str] = []
    raw_identifiers = _extract_identifier_like_tokens(raw)
    hint_identifiers = _extract_identifier_like_tokens(hint)
    if raw_identifiers - hint_identifiers:
        reasons.append("missing_identifier_or_year")

    raw_topic_terms = _extract_topic_terms(raw)
    hint_topic_terms = _extract_topic_terms(hint)
    if raw_topic_terms and not raw_topic_terms.intersection(hint_topic_terms):
        reasons.append("topic_terms_lost")

    raw_org_terms = _extract_org_terms(raw)
    hint_org_terms = _extract_org_terms(hint)
    if raw_org_terms - hint_org_terms:
        reasons.append("org_terms_lost")

    raw_people_terms = _extract_people_terms(raw)
    hint_people_terms = _extract_people_terms(hint)
    if raw_people_terms - hint_people_terms:
        reasons.append("people_terms_lost")

    raw_title_terms = _extract_title_terms(raw)
    hint_title_terms = _extract_title_terms(hint)
    if raw_title_terms - hint_title_terms:
        reasons.append("title_terms_lost")

    raw_role_terms = _extract_org_role_terms(raw)
    hint_role_terms = _extract_org_role_terms(hint)
    if raw_role_terms - hint_role_terms:
        reasons.append("org_role_lost")

    raw_has_project = _contains_any_term(raw_topic_terms, _PROJECT_AXIS_TERMS)
    raw_has_perf = _contains_any_term(raw_topic_terms, _PERF_AXIS_TERMS)
    hint_has_project = _contains_any_term(hint_topic_terms, _PROJECT_AXIS_TERMS)
    hint_has_perf = _contains_any_term(hint_topic_terms, _PERF_AXIS_TERMS)
    raw_has_detail = _contains_any_term(raw_topic_terms, _DETAIL_AXIS_TERMS)
    hint_has_detail = _contains_any_term(hint_topic_terms, _DETAIL_AXIS_TERMS)
    raw_has_stats = _contains_any_term(raw_topic_terms, _STATS_AXIS_TERMS)
    hint_has_stats = _contains_any_term(hint_topic_terms, _STATS_AXIS_TERMS)

    if not raw_has_perf and hint_has_perf:
        reasons.append("perf_axis_added")
    if raw_has_project and not hint_has_project:
        reasons.append("project_axis_removed")
    if not raw_has_detail and hint_has_detail:
        reasons.append("detail_axis_added")
    if not raw_has_stats and hint_has_stats:
        reasons.append("stats_axis_added")

    deduped_reasons = list(dict.fromkeys(reasons))
    return bool(deduped_reasons), deduped_reasons


def _ensure_run_rag_ab_compare_supports_request_overrides() -> None:
    """현재 로딩된 runtime이 `request_overrides` 계약을 지원하는지 확인한다."""
    params = signature(_get_run_rag_ab_compare()).parameters
    if "request_overrides" in params:
        return
    raise TypeError(
        "run_rag_ab_compare() does not accept request_overrides; "
        "loaded runtime appears stale or out of sync with rag_retriever"
    )


class CustomRAGRetriever(BaseModel):
    """서비스 계층에서 쓰기 쉬운 RAG 조회 래퍼 모델이다.

    AB 비교 결과에서 히트, 집계 결과, canonical evidence, render profile을 꺼내
    상위 서비스 계층이 바로 쓸 수 있는 형태로 바꾼다.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    model_name: str = "gemma_triton_0"
    top_k: int = 5
    intent_payload: Optional[IntentPayloadV3] = None
    request_overrides: Dict[str, Any] = {}

    @staticmethod
    def _infer_tag_from_hit_data(hit_data: Dict[str, Any], intent_payload: Optional[IntentPayloadV3] = None) -> Optional[str]:
        """히트 payload와 intent target collection을 바탕으로 문서 태그를 추정한다.

        payload에 태그가 없으면 project/perf 계열 분기를 맞추기 위한 보조 태그를 만든다.
        """
        tag = hit_data.get("tag")
        if tag:
            return str(tag)

        collection = str(hit_data.get("_collection") or "").strip().lower()
        if collection.startswith("ntis_project_v1"):
            return "IRD_NAI_PJT_INFO"
        if collection.startswith("ntis_perf_v1"):
            return "IRD_NAI_RI_PAPER"

        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        target_cols = getattr(normalized_intent, "target_cols", None) if normalized_intent else None
        if isinstance(target_cols, list):
            lowered = [str(c).strip().lower() for c in target_cols]
            if any(c.startswith("ntis_project_v1") for c in lowered):
                return "IRD_NAI_PJT_INFO"
            if any(c.startswith("ntis_perf_v1") for c in lowered):
                return "IRD_NAI_RI_PAPER"

        return None

    @staticmethod
    def _has_minimum_document_fields(hit_data: Dict[str, Any]) -> bool:
        """히트가 상위 문서 뷰로 올라올 최소한의 정보를 갖추는지 검사한다.

        title, content, meta, nested member 중 하나라도 유효한 값이 없으면 retriever 응답에서 제외한다.
        """
        candidates = [
            hit_data.get("doc_id"),
            hit_data.get("title"),
            hit_data.get("title_text"),
            hit_data.get("title1"),
            hit_data.get("content"),
            hit_data.get("meta_basic"),
            hit_data.get("meta_detail"),
            hit_data.get("prtcp_mp"),
            hit_data.get("prtcp_org"),
        ]
        for val in candidates:
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            return True
        return False

    @staticmethod
    def _coerce_hit_data(hit: Any) -> Dict[str, Any]:
        """Flatten payload-wrapped reranked hits into a payload-first document view."""
        if isinstance(hit, dict):
            wrapper = dict(hit)
        else:
            wrapper = dict(getattr(hit, "__dict__", {}) or {})

        payload = getattr(hit, "payload", None)
        if not isinstance(payload, dict):
            payload = wrapper.get("payload")

        payload_dict = dict(payload or {}) if isinstance(payload, dict) else {}
        nested_payload = payload_dict.get("payload")
        if isinstance(nested_payload, dict):
            payload_dict = {**{key: value for key, value in payload_dict.items() if key != "payload"}, **nested_payload}

        merged = {key: value for key, value in wrapper.items() if key != "payload"}
        merged.update(payload_dict)
        return merged

    @staticmethod
    def _build_rag_intent_payload(intent_payload: Optional[IntentPayloadV3]) -> Optional[Dict[str, Any]]:
        """`IntentPayloadV3`에서 RAG 런타임이 직접 쓸 payload 뷰만 추출한다.

        normalized intent가 올바른 타입일 때만 넘기며, retriever가 planner/runtime contract 바깥 shape를
        직접 끌어오지 않게 한다.
        """
        if intent_payload is None:
            return None
        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        if normalized_intent is None:
            return None
        payload_version = getattr(intent_payload, "intent_payload_version", None)
        question_analysis = getattr(intent_payload, "question_analysis", None)
        strategy_meta = getattr(intent_payload, "strategy_meta", None) or {}
        return {"intent_payload_version": payload_version or "v3", "normalized_intent": normalized_intent, "question_analysis": question_analysis, "strategy_meta": dict(strategy_meta)}

    @staticmethod
    def _format_aggregation_title(item: Dict[str, Any], metric: str, index: int) -> str:
        """집계 결과 한 행을 사람이 읽기 쉬운 제목으로 만든다."""
        if metric == "project_participation_count":
            return f"{index}. {item.get('hm_nm') or item.get('hm_id') or item.get('person_key')}"
        project_title = str(item.get("project_title") or item.get("group_key") or "project").strip()
        metric_value = int(item.get("metric_value") or 0)
        return f"{index}. {project_title} ({metric_value})"


    @staticmethod
    def _format_series_title(item: Dict[str, Any], index: int) -> str:
        """시계열 결과 한 행을 사람이 읽기 쉬운 제목으로 만든다."""
        project_title = str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip()
        year = str(item.get("year") or "").strip()
        return f"{index}. {project_title}" + (f" ({year})" if year else "")

    def retrieve(self, query: str) -> Dict[str, Any]:
        """AB 비교 RAG 실행 결과에서 상위 계층이 바로 쓰는 `documents/canonical_evidence/render_profile` 구조를 만든다.

        aggregation rank_items와 일반 hit 경로를 구분해 서비스 계층이 바로 소비할 단일 dict 형태로 반환한다.
        """
        strategy_meta = getattr(self.intent_payload, "strategy_meta", None) or {}
        clarification = build_followup_clarification_payload(dict(strategy_meta))
        followup_message = build_followup_clarification_message(dict(strategy_meta))
        if followup_message:
            return {
                "documents": [],
                "canonical_evidence": [],
                "render_profile": {},
                "no_result_message": followup_message,
                "clarification": clarification,
            }

        _ensure_run_rag_ab_compare_supports_request_overrides()
        res_map = _get_run_rag_ab_compare()(
            query=query,
            model_name=self.model_name,
            intent_payload=self._build_rag_intent_payload(self.intent_payload),
            request_overrides=dict(self.request_overrides or {}),
        )
        res_m = res_map.get("M") or res_map.get("A") or next(iter(res_map.values()))

        hits = getattr(res_m, "reranked_hits", []) or []
        aggregation = getattr(res_m, "aggregation", None) or {}
        series = getattr(res_m, "series", None) or {}
        canonical_evidence = getattr(res_m, "canonical_evidence", None) or []
        render_profile = getattr(res_m, "render_profile", None) or {}
        answer_context_text = str(getattr(res_m, "context", "") or "")
        timings = getattr(res_m, "timings", None) or {}
        normalized_intent = getattr(self.intent_payload, "normalized_intent", None)
        no_result_message = None

        def _result(documents: list[dict[str, Any]]) -> Dict[str, Any]:
            return {
                "documents": documents,
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "answer_context_text": answer_context_text,
                "actual_retrieval_query": str(query or "").strip(),
                "no_result_message": no_result_message,
                "clarification": clarification,
            }

        if not hits and str(timings.get("info.contract_fail_reason") or "").strip().lower() == "no_reranked":
            mode = str(getattr(normalized_intent, "mode", "") or "").strip().upper()
            action = str(getattr(normalized_intent, "action", "") or "").strip().lower()
            if mode in {"LOOKUP", "JOIN"}:
                no_result_message = friendly_strategy_violation_message(
                    error_code="RAG_EMPTY_RESULT_CONTRACT",
                    reason="reranked result violated contract(reason=no_reranked)",
                    question_analysis=SimpleNamespace(
                        mode=mode,
                        action=action,
                        ids_map=dict(getattr(normalized_intent, "ids_map", {}) or {}),
                    ),
                )

        pattern_analysis = getattr(res_m, "pattern_analysis", None) or {}
        multi_hop_bundle = getattr(res_m, "multi_hop_bundle", None) or {}
        series_items = series.get("instance_projects") if isinstance(series, dict) else None
        if isinstance(series_items, list) and series_items:
            documents = []
            for idx, item in enumerate(series_items[: self.top_k], start=1):
                documents.append({
                    "title": self._format_series_title(item, idx),
                    "source_index": idx,
                    "source_type": "series",
                    "series_key_kind": series.get("series_key_kind"),
                    "series_key": series.get("series_key"),
                    "series_item": dict(item),
                    "year_buckets": list(series.get("year_buckets") or []),
                    "candidate_docs": int(series.get("candidate_docs") or 0),
                })
            return _result(documents)
        if isinstance(multi_hop_bundle, dict) and str(multi_hop_bundle.get("status") or "").strip().lower() in {"ok", "partial"} and (list(multi_hop_bundle.get("projects") or []) or list(multi_hop_bundle.get("bundles") or [])):
            documents = []
            for idx, project in enumerate(list(multi_hop_bundle.get("projects") or [])[: self.top_k] or [None], start=1):
                title = "multi-hop bundle"
                if isinstance(project, dict):
                    title = str(project.get("project_title") or project.get("pjt_id") or project.get("pjt_no") or title).strip()
                documents.append(
                    {
                        "title": title,
                        "source_index": idx,
                        "source_type": "multi_hop_bundle",
                        "bundle_kind": str(multi_hop_bundle.get("bundle_kind") or "project_outputs"),
                        "projects": list(multi_hop_bundle.get("projects") or []),
                        "bundles": list(multi_hop_bundle.get("bundles") or []),
                        "ambiguities": list(multi_hop_bundle.get("ambiguities") or []),
                        "guidance_message": multi_hop_bundle.get("guidance_message"),
                    }
                )
            return _result(documents)

        if isinstance(pattern_analysis, dict) and str(pattern_analysis.get("status") or "").strip().lower() in {"ok", "partial"} and list(pattern_analysis.get("items") or []):
            documents = []
            pattern_kind = str(pattern_analysis.get("pattern_kind") or "pattern_analysis")
            for idx, item in enumerate(list(pattern_analysis.get("items") or [])[: self.top_k], start=1):
                title = pattern_kind
                if pattern_kind == "coauthor_org_repeat":
                    title = f"{idx}. {item.get('org_name')} ({item.get('repeated_author_count', 0)})"
                elif pattern_kind == "perf_mix_gap":
                    title = f"{idx}. {item.get('project_title') or item.get('group_key')}"
                elif pattern_kind == "series_member_change":
                    title = f"{idx}. {item.get('project_title') or item.get('year') or 'series_change'}"
                documents.append(
                    {
                        "title": title,
                        "source_index": idx,
                        "source_type": "pattern_analysis",
                        "pattern_kind": pattern_kind,
                        "pattern_item": dict(item),
                        "candidate_docs": int(pattern_analysis.get("candidate_docs") or 0),
                        "subject_count": int(pattern_analysis.get("subject_count") or 0),
                        "support_doc_count": int(pattern_analysis.get("support_doc_count") or 0),
                    }
                )
            return _result(documents)

        reverse_trace = getattr(res_m, "reverse_trace", None) or {}
        if isinstance(reverse_trace, dict) and (reverse_trace.get("origin_projects") or reverse_trace.get("followup_perf") or reverse_trace.get("origin_perf")):
            documents = []
            for idx, item in enumerate(list(reverse_trace.get("origin_projects") or [])[: self.top_k], start=1):
                documents.append(
                    {
                        "title": str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip(),
                        "source_index": idx,
                        "source_type": "reverse_trace",
                        "relation_chain": list(reverse_trace.get("relation_chain") or []),
                        "origin_perf": list(reverse_trace.get("origin_perf") or []),
                        "origin_project": dict(item),
                        "followup_perf": list(reverse_trace.get("followup_perf") or []),
                    }
                )
            if not documents:
                documents.append(
                    {
                        "title": "reverse trace",
                        "source_index": 1,
                        "source_type": "reverse_trace",
                        "relation_chain": list(reverse_trace.get("relation_chain") or []),
                        "origin_perf": list(reverse_trace.get("origin_perf") or []),
                        "origin_project": None,
                        "followup_perf": list(reverse_trace.get("followup_perf") or []),
                    }
                )
            return _result(documents)

        rank_items = aggregation.get("rank_items") if isinstance(aggregation, dict) else None
        if isinstance(rank_items, list) and rank_items:
            agg_metric = str(aggregation.get("metric") or "project_participation_count")
            agg_candidate_docs = int(aggregation.get("candidate_docs") or 0)
            agg_window_years = aggregation.get("window_years") or {}
            agg_group_by = str(aggregation.get("group_by") or "").strip() or None
            agg_threshold = aggregation.get("threshold")
            agg_sort_order = aggregation.get("sort_order")
            documents = []
            for idx, item in enumerate(rank_items[: self.top_k], start=1):
                documents.append(
                    {
                        "title": self._format_aggregation_title(item, agg_metric, idx),
                        "source_index": idx,
                        "source_type": "aggregation",
                        "metric": agg_metric,
                        "group_by": agg_group_by,
                        "threshold": agg_threshold,
                        "sort_order": agg_sort_order,
                        "window_years": agg_window_years,
                        "candidate_docs": agg_candidate_docs,
                        "rank_item": dict(item),
                    }
                )
            return _result(documents)

        if not hits:
            return _result([])

        documents = []
        for idx, hit in enumerate(hits[: self.top_k], start=1):
            hit_data = self._coerce_hit_data(hit)

            inferred_tag = self._infer_tag_from_hit_data(hit_data, self.intent_payload)
            rag_data = {
                "title": resolve_title_from_payload(hit_data),
                "source_index": idx,
                "source_type": "hit",
                "tag": inferred_tag,
                "doc_id": hit_data.get("doc_id"),
                "_collection": hit_data.get("_collection"),
                "meta_basic": hit_data.get("meta_basic", {}),
                "meta_detail": hit_data.get("meta_detail", {}),
                "prtcp_mp": hit_data.get("prtcp_mp", []),
                "prtcp_org": hit_data.get("prtcp_org", []) or [],
                "title_text": hit_data.get("title_text"),
                "title1": hit_data.get("title1"),
                "title2": hit_data.get("title2"),
            }
            for field in _DETAIL_RAW_PASSTHROUGH_FIELDS:
                value = hit_data.get(field)
                if value not in (None, "", [], {}):
                    rag_data[field] = value

            if inferred_tag is not None or self._has_minimum_document_fields(hit_data):
                documents.append(rag_data)

        return _result(documents)


def is_hit_source(doc: Dict[str, Any]) -> bool:
    """retriever 뷰 문서가 일반 hit source인지 여부를 판별한다.

    aggregation 결과와 hit 결과를 상위 계층에서 쉽게 구분하기 위한 얇은 헬퍼다.
    """
    return doc.get("source_type", "hit") == "hit"



def resolve_rag_queries(*, state: Any, qa: Any, ks: Any, min_confidence: float) -> tuple[str, str, str, float, bool, list[str], bool]:
    """raw query, planner hint query, 최종 search query를 함께 결정한다.

    기본값은 planner hint를 따르되, semantic-axis drift가 감지되거나 confidence가 낮으면
    raw query로 되돌린다. detail follow-up에서는 anchor가 빠진 경우 anchor 보존 질의를 다시 만든다.
    """
    raw_query = state.question
    normalized_intent = _get_normalized_intent(state)
    hint_query = _pick_attr(ks, normalized_intent, qa, key="retrieval_query", default=raw_query)
    action = str(_pick_attr(normalized_intent, qa, key="action", default="") or "").strip().lower()
    output_type = str(_pick_attr(normalized_intent, qa, key="output_type", default="") or "").strip().lower()
    anchor_context = get_followup_anchor_context(state, active_only=True)
    ks_confidence = float(ks.confidence) if getattr(ks, "confidence", None) is not None else None
    qa_confidence = float(qa.confidence) if getattr(qa, "confidence", None) is not None else None
    confidence = ks_confidence if ks_confidence is not None else (qa_confidence if qa_confidence is not None else 0.0)
    drift_detected, drift_reasons = detect_retrieval_query_drift(raw_query=raw_query, hint_query=hint_query)
    fallback_applied = drift_detected or confidence < min_confidence
    search_query = raw_query if fallback_applied else hint_query

    if anchor_context.get("present") and action == "detail" and output_type == "detail":
        anchor_kind = str(anchor_context.get("entity_kind") or "").strip().lower()
        if anchor_kind == "project" and "perf_axis_added" in drift_reasons:
            fallback_applied = True
            drift_detected = True
        if anchor_kind == "project" and (
            fallback_applied
            or "perf_axis_added" in drift_reasons
            or "project_axis_removed" in drift_reasons
            or not _query_mentions_anchor(search_query, anchor_context)
        ):
            search_query, _ = _build_anchor_preserving_query_from_context(
                state=state,
                query=search_query,
                anchor_context=anchor_context,
            )
    return raw_query, hint_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied




