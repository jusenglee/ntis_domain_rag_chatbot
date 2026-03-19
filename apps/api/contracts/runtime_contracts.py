"""Runtime contract helpers kept separate from the main server module.

These helpers validate env and planner payload semantics in a location that both tests and
startup code can import directly.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

_PROJECT_ID_LABEL_RE = re.compile(r"(?:과제고유번호|과제\s*고유\s*번호|pjt[_\s-]*id|project\s*id)", re.I)
_PROJECT_NO_LABEL_RE = re.compile(r"(?:과제\s*그룹\s*번호|pjt[_\s-]*no|project\s*group\s*number|group\s*number)", re.I)
_AMBIGUOUS_PROJECT_KEY_RE = re.compile(r"(?:과제번호|과제\s*번호|project\s*number)", re.I)
_CANONICAL_PJT_ID_RE = re.compile(r"^\d{8,12}$")
_CANONICAL_PJT_NO_RE = re.compile(r"^PJT[-_/]?[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+){1,}$", re.I)
_STRUCTURED_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_ALNUM_WITH_DIGIT_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{4,64}$")
_PERSON_NO_NUMERIC_RE = re.compile(r"^\d{6,12}$")
_BIZ_NO_NUMERIC_RE = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
_ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dXx]$")
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I)
_RST_ID_RE = re.compile(r"^(?:RPT|RST)-?[A-Za-z0-9\-]{2,}$", re.I)
_PERF_ID_RE = re.compile(r"^(?:PERF|PFM)-?[A-Za-z0-9\-]{2,}$", re.I)
_PAPER_ID_RE = re.compile(r"^(?:PAP|PAPER)-?[A-Za-z0-9\-]{2,}$", re.I)
_PATENT_ID_RE = re.compile(r"^[A-Za-z0-9\-]{6,}$")
_ORG_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_\-]{2,15}$")
_ORG_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]{2,31}$")

PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS = {
    "pjt_id",
    "pjt_no",
    "doi",
    "issn",
    "eissn",
    "pissn",
    "rst_id",
    "perf_id",
    "paper_id",
    "patent_reg_no",
    "patent_app_no",
    "person_no",
    "biz_no",
    "org_code",
    "org_id",
}


def validate_project_key_env_contract(
    *,
    env: Any = os.environ,
    log_info: Callable[..., None] | None = None,
) -> tuple[str, str]:
    """환경 변수로 지정한 project instance/group key 이름이 서로 다른지 검증한다."""
    pjt_id_key = str(env.get("RAG_KEY_PJT_ID", "pjt_id")).strip() or "pjt_id"
    pjt_no_key = str(env.get("RAG_KEY_PJT_NO", "pjt_no")).strip() or "pjt_no"
    if pjt_id_key == pjt_no_key:
        raise RuntimeError(
            "RAG_KEY_PJT_ID and RAG_KEY_PJT_NO must differ "
            f"(got {pjt_id_key!r})"
        )
    if log_info is not None:
        log_info(
            "[startup][key-mapping] RAG_KEY_PJT_ID=%s, RAG_KEY_PJT_NO=%s",
            pjt_id_key,
            pjt_no_key,
        )
    return pjt_id_key, pjt_no_key


def has_explicit_project_id_label(text: str | None) -> bool:
    """질문에 `pjt_id`를 직접 가리키는 라벨이 있는지 판단한다."""
    return bool(_PROJECT_ID_LABEL_RE.search(str(text or "")))


def has_explicit_project_no_label(text: str | None) -> bool:
    """질문에 `pjt_no`를 직접 가리키는 라벨이 있는지 판단한다."""
    return bool(_PROJECT_NO_LABEL_RE.search(str(text or "")))


def has_ambiguous_project_key_label(text: str | None) -> bool:
    """`과제번호`처럼 instance/group을 가르지 않는 모호한 project key 표현을 감지한다."""
    raw = str(text or "")
    return bool(_AMBIGUOUS_PROJECT_KEY_RE.search(raw)) and not has_explicit_project_id_label(raw) and not has_explicit_project_no_label(raw)


def _slot_allows_labeled_alnum(key: str) -> bool:
    """slot별로 라벨이 있을 때 영문+숫자 식별자를 허용할지 결정한다."""
    return key in {"pjt_id", "pjt_no", "person_no", "biz_no", "org_code", "org_id", "rst_id", "perf_id", "paper_id", "patent_reg_no", "patent_app_no"}


def _has_slot_label(key: str, question_text: str | None) -> bool:
    """질문에 slot별 명시 라벨이 있었는지 확인한다."""
    text = str(question_text or "")
    lowered = text.lower()
    if key == "pjt_id":
        return has_explicit_project_id_label(text)
    if key == "pjt_no":
        return has_explicit_project_no_label(text)
    if key == "person_no":
        return any(label in lowered for label in ("person_no", "person no", "연구자번호", "연구자 번호", "researcher id"))
    if key == "biz_no":
        return any(label in lowered for label in ("biz_no", "biz no", "사업자번호", "사업자 번호", "business id"))
    if key == "org_code":
        return any(label in lowered for label in ("org_code", "org code", "기관코드", "기관 코드"))
    if key == "org_id":
        return any(label in lowered for label in ("org_id", "org id", "기관id", "기관 id"))
    if key == "rst_id":
        return any(label in lowered for label in ("rst_id", "rst id", "보고서번호", "보고서 번호"))
    if key == "perf_id":
        return any(label in lowered for label in ("perf_id", "perf id", "성과id", "성과 id"))
    if key == "paper_id":
        return any(label in lowered for label in ("paper_id", "paper id", "논문id", "논문 id"))
    if key == "patent_reg_no":
        return any(label in lowered for label in ("patent_reg_no", "특허등록번호", "특허 등록번호"))
    if key == "patent_app_no":
        return any(label in lowered for label in ("patent_app_no", "특허출원번호", "특허 출원번호"))
    return False


def _is_valid_id_value(*, key: str, value: str, question_text: str | None = None) -> bool:
    """slot 의미와 질문 라벨을 함께 보고 값이 seed로 안전한지 판정한다."""
    if not value or any(ch.isspace() for ch in value):
        return False

    labeled = _has_slot_label(key, question_text)
    if key == "pjt_id":
        return bool(_CANONICAL_PJT_ID_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "pjt_no":
        return bool(_CANONICAL_PJT_NO_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "doi":
        return bool(_DOI_RE.fullmatch(value))
    if key in {"issn", "eissn", "pissn"}:
        return bool(_ISSN_RE.fullmatch(value))
    if key in {"patent_reg_no", "patent_app_no"}:
        return bool(_PATENT_ID_RE.fullmatch(value) or (labeled and _STRUCTURED_TOKEN_RE.fullmatch(value)))
    if key == "rst_id":
        return bool(_RST_ID_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "perf_id":
        return bool(_PERF_ID_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "paper_id":
        return bool(_PAPER_ID_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "person_no":
        return bool(_PERSON_NO_NUMERIC_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "biz_no":
        return bool(_BIZ_NO_NUMERIC_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "org_code":
        return bool(_ORG_CODE_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if key == "org_id":
        return bool(_ORG_ID_RE.fullmatch(value) or (labeled and _ALNUM_WITH_DIGIT_RE.fullmatch(value)))
    if _slot_allows_labeled_alnum(key):
        return bool(labeled and _STRUCTURED_TOKEN_RE.fullmatch(value))
    return True


def sanitize_ids_map_semantics(
    ids_map: dict[str, list[str]],
    *,
    allowed_keys: set[str] | None = None,
    question_text: str | None = None,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """planner ids_map에서 slot 의미에 맞는 seed만 남기고 나머지는 invalid로 분리한다.

    숫자형 `pjt_id`와 canonical `pjt_no`는 강한 패턴으로 바로 허용하고, 영문+숫자 식별자는
    질문에 해당 slot 라벨이 있을 때만 seed로 승격시킨다. `과제번호`처럼 모호한 표현만 있을 때는
    `pjt_id`/`pjt_no` 어느 쪽도 확정하지 않는다.
    """
    cleaned: dict[str, list[str]] = {}
    invalid: list[dict[str, Any]] = []
    allowed = allowed_keys or PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS
    for key, values in (ids_map or {}).items():
        if key not in allowed:
            for raw in values or []:
                value = str(raw).strip()
                if value:
                    invalid.append({"key": key, "value": value})
            continue
        out: list[str] = []
        for raw in values or []:
            value = str(raw).strip()
            if not value:
                continue
            if not _is_valid_id_value(key=key, value=value, question_text=question_text):
                invalid.append({"key": key, "value": value})
                continue
            out.append(value)
        if out:
            cleaned[key] = out
    return cleaned, invalid


def friendly_strategy_violation_message(
    *,
    error_code: str,
    reason: str,
    question_analysis: Any,
) -> str:
    """계약 위반이나 빈 결과 실패를 사용자에게 설명 가능한 문장으로 바꾼다."""
    mode = str(getattr(question_analysis, "mode", "") or "").strip().upper()
    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    ids_map = getattr(question_analysis, "ids_map", None) or {}
    has_explicit_id = isinstance(ids_map, dict) and any(bool(v) for v in ids_map.values())

    if error_code == "JOIN_KEYS_MISSING":
        return (
            "질문은 관계형 조회로 해석되었지만, Hop1 결과에서 과제 키를 확정하지 못해 "
            "성과를 연결할 수 없었습니다. 과제명, 과제번호, 또는 더 구체적인 식별자를 포함해 다시 질문해 주세요."
        )

    if error_code == "JOIN_GROUP_KEYS_UNRESOLVED":
        return (
            "관계형 조회를 위해 과제 그룹 키는 찾았지만, 실제 과제 instance 키로 확장하지 못했습니다. "
            "과제번호 대신 과제명이나 pjt_id에 가까운 식별자를 함께 주시면 재시도할 수 있습니다."
        )

    if error_code == "RAG_EMPTY_RESULT_CONTRACT" and mode == "LOOKUP":
        if action == "detail" or has_explicit_id:
            return "조건에 맞는 조회 결과를 찾지 못했습니다. 식별자나 질문 조건이 정확한지 다시 확인해 주세요."
        if action == "list":
            return "조건에 맞는 목록 결과를 찾지 못했습니다. 기관명, 인명, 연도 같은 조건을 조금 더 좁혀서 다시 시도해 주세요."

    if error_code == "RAG_EMPTY_RESULT_CONTRACT" and mode == "JOIN":
        return (
            "관계형 조회를 시도했지만 연결 가능한 근거를 찾지 못했습니다. "
            "기준 과제나 성과를 더 구체적으로 지정해 다시 질문해 주세요."
        )

    return "요청을 처리하는 데 필요한 근거를 만들지 못했습니다. 질문을 조금 더 구체적으로 바꿔 다시 시도해 주세요."
