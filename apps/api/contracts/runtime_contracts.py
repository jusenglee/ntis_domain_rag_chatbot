"""Runtime contract helpers kept separate from the main server module.

These helpers validate environment and planner payload semantics in a location that both tests and
startup code can import directly.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable

_PROJECT_ID_LABEL_RE = re.compile(r"(?:과제고유번호|과제\s*고유\s*번호|pjt[_\s-]*id|project\s*id)", re.I)
_PROJECT_NO_LABEL_RE = re.compile(r"(?:과제그룹번호|동일과제번호|과제\s*그룹\s*번호|pjt[_\s-]*no|project\s*group\s*number|group\s*number)", re.I)
_AMBIGUOUS_PROJECT_KEY_RE = re.compile(r"(?:과제번호|과제\s*번호|project\s*number|project\s*id|pjt)", re.I)
_UNSUPPORTED_PROJECT_KEY_ALIAS_RE = re.compile(r"\brjt[_\s-]*id\b", re.I)
_STRUCTURED_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_ALNUM_WITH_DIGIT_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9_-]{4,64}$")
_BIZ_NO_NUMERIC_RE = re.compile(r"^\d{3}-?\d{2}-?\d{5}$")
_ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dXx]$")
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I)

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
        log_info(f"[startup][key-mapping] RAG_KEY_PJT_ID={pjt_id_key}, RAG_KEY_PJT_NO={pjt_no_key}")
    return pjt_id_key, pjt_no_key


def has_explicit_project_id_label(text: str | None) -> bool:
    """질문이 `pjt_id`를 직접 가리키는 라벨을 포함하는지 판단한다."""
    return bool(_PROJECT_ID_LABEL_RE.search(str(text or "")))


def has_explicit_project_no_label(text: str | None) -> bool:
    """질문이 `pjt_no`를 직접 가리키는 라벨을 포함하는지 판단한다."""
    return bool(_PROJECT_NO_LABEL_RE.search(str(text or "")))


def has_ambiguous_project_key_label(text: str | None) -> bool:
    """`과제번호`처럼 instance/group을 가리지 않는 project key 표현을 감지한다."""
    raw = str(text or "")
    return bool(_AMBIGUOUS_PROJECT_KEY_RE.search(raw)) and not has_explicit_project_id_label(raw) and not has_explicit_project_no_label(raw)


def extract_unsupported_project_key_aliases(text: str | None) -> list[str]:
    """지원하지 않는 project-key alias를 질문 표면에서 추출한다."""
    raw = str(text or "")
    aliases = {match.group(0).upper().replace(" ", "_").replace("-", "_") for match in _UNSUPPORTED_PROJECT_KEY_ALIAS_RE.finditer(raw)}
    return sorted(aliases)


def is_unsupported_project_key_alias(key: str | None) -> bool:
    """canonical contract에 없는 project-key alias인지 판단한다."""
    normalized = str(key or "").strip().lower().replace("-", "_").replace(" ", "")
    return normalized in {"rjt_id", "rjtid"}


def infer_project_key_axis(
    *,
    ids_map: dict[str, list[str]] | None = None,
    project_key_policy: str | None = None,
) -> str | None:
    """resolved/locked project key axis를 pjt_id 또는 pjt_no로 정규화한다."""
    normalized_ids_map = ids_map if isinstance(ids_map, dict) else {}
    if normalized_ids_map.get("pjt_id"):
        return "pjt_id"
    if normalized_ids_map.get("pjt_no"):
        return "pjt_no"
    policy = str(project_key_policy or "").strip().lower()
    if policy in {"resolved_pjt_id", "anchor_locked_pjt_id"}:
        return "pjt_id"
    if policy in {"resolved_pjt_no", "anchor_locked_pjt_no"}:
        return "pjt_no"
    return None


def _slot_allows_labeled_alnum(key: str) -> bool:
    """슬롯별로 라벨이 있을 때 영문+숫자 식별자를 허용할지 결정한다."""
    return key in {"pjt_id", "pjt_no", "person_no", "biz_no", "org_code", "org_id", "rst_id", "perf_id", "paper_id", "patent_reg_no", "patent_app_no"}


def _has_slot_label(key: str, question_text: str | None) -> bool:
    """질문이 특정 식별자 슬롯을 명시적으로 가리키는지 판단한다."""
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
        return any(label in lowered for label in ("rst_id", "rst id", "성과번호", "성과 번호"))
    if key == "perf_id":
        return any(label in lowered for label in ("perf_id", "perf id", "성과id", "성과 id"))
    if key == "paper_id":
        return any(label in lowered for label in ("paper_id", "paper id", "논문id", "논문 id"))
    if key == "patent_reg_no":
        return any(label in lowered for label in ("patent_reg_no", "특허등록번호", "특허 등록번호"))
    if key == "patent_app_no":
        return any(label in lowered for label in ("patent_app_no", "특허출원번호", "특허 출원번호"))
    return False


def _is_hygiene_valid_token(value: str) -> bool:
    token = str(value or "").strip()
    if not token:
        return False
    if len(token) < 3 or len(token) > 64:
        return False
    if any(ch.isspace() for ch in token):
        return False
    lowered = token.lower()
    if lowered in {"none", "null", "na", "n/a", "project", "pjt", "unknown", "미상", "없음"}:
        return False
    if re.search(r"[?占?]{2,}", token):
        return False
    return True


def _candidate_key_item(*, value: str, source: str) -> dict[str, Any]:
    return {
        "value": value,
        "candidate_types": ["pjt_id", "pjt_no"],
        "source": source,
        "confidence": 0.35,
    }


def _is_valid_id_value(*, key: str, value: str, question_text: str | None = None) -> bool:
    """Validate resolved ids by hygiene for internal IDs and by format for external standard IDs."""
    if not _is_hygiene_valid_token(value):
        return False

    if key == "doi":
        return bool(_DOI_RE.fullmatch(value))
    if key in {"issn", "eissn", "pissn"}:
        return bool(_ISSN_RE.fullmatch(value))
    if key == "biz_no":
        return bool(_BIZ_NO_NUMERIC_RE.fullmatch(value) or _has_slot_label(key, question_text))
    if key in {"pjt_id", "pjt_no", "rst_id", "perf_id", "paper_id", "patent_reg_no", "patent_app_no", "person_no", "org_code", "org_id"}:
        if not _has_slot_label(key, question_text):
            return False
        if _slot_allows_labeled_alnum(key):
            return bool(_STRUCTURED_TOKEN_RE.fullmatch(value) or _ALNUM_WITH_DIGIT_RE.fullmatch(value))
        return True
    return True


def _merge_candidate_keys(existing: dict[str, list[dict[str, Any]]], incoming: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in (existing or {}).items()}
    for key, values in (incoming or {}).items():
        bucket = list(merged.get(key) or [])
        seen = {(str(item.get("value") or "").strip(), str(item.get("source") or "").strip()) for item in bucket if isinstance(item, dict)}
        for item in values or []:
            if not isinstance(item, dict):
                continue
            sig = (str(item.get("value") or "").strip(), str(item.get("source") or "").strip())
            if not sig[0] or sig in seen:
                continue
            bucket.append(item)
            seen.add(sig)
        if bucket:
            merged[key] = bucket
    return merged



def sanitize_ids_map_semantics(
    ids_map: dict[str, list[str]],
    *,
    allowed_keys: set[str] | None = None,
    question_text: str | None = None,
    candidate_keys: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, list[str]], dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Keep only resolved ids in ids_map and preserve ambiguous project keys as candidate_keys."""
    cleaned: dict[str, list[str]] = {}
    cleaned_candidates: dict[str, list[dict[str, Any]]] = dict(candidate_keys or {})
    invalid: list[dict[str, Any]] = []
    allowed = allowed_keys or PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS
    question = str(question_text or "")

    for key, values in (ids_map or {}).items():
        if key not in allowed:
            for raw in values or []:
                value = str(raw).strip()
                if value:
                    invalid.append(
                        {
                            "key": key,
                            "value": value,
                            "reason": "unsupported_project_key_alias" if is_unsupported_project_key_alias(key) else "unsupported_slot",
                        }
                    )
            continue
        out: list[str] = []
        for raw in values or []:
            value = str(raw).strip()
            if not value:
                continue
            if _is_valid_id_value(key=key, value=value, question_text=question):
                out.append(value)
                continue

            reason = "invalid_hygiene"
            if key in {"pjt_id", "pjt_no"} and has_ambiguous_project_key_label(question):
                cleaned_candidates = _merge_candidate_keys(
                    cleaned_candidates,
                    {"project_key": [_candidate_key_item(value=value, source="label:과제번호")]},
                )
                reason = "ambiguous_project_key"
            invalid.append({"key": key, "value": value, "reason": reason})
        if out:
            cleaned[key] = list(dict.fromkeys(out))
    return cleaned, cleaned_candidates, invalid



def friendly_strategy_violation_message(
    *,
    error_code: str,
    reason: str,
    question_analysis: Any,
) -> str:
    """계약 위반이나 빈 결과 상태를 사용자에게 설명 가능한 문장으로 바꾼다."""
    mode = str(getattr(question_analysis, "mode", "") or "").strip().upper()
    action = str(getattr(question_analysis, "action", "") or "").strip().lower()
    ids_map = getattr(question_analysis, "ids_map", None) or {}
    has_explicit_id = isinstance(ids_map, dict) and any(bool(v) for v in ids_map.values())

    if error_code == "JOIN_KEYS_MISSING":
        return (
            "질문은 관계형 조회로 해석됐지만 Hop1 결과에서 과제 키를 확정하지 못해 성과를 연결할 수 없었습니다. "
            "과제명이나 과제번호처럼 더 구체적인 식별자를 포함해 다시 질문해 주세요."
        )

    if error_code == "JOIN_GROUP_KEYS_UNRESOLVED":
        return (
            "관계형 조회를 위해 과제 그룹을 찾았지만 실제 과제 instance로 확장하지 못했습니다. "
            "과제번호나 과제명을 조금 더 구체적으로 적어 다시 질문해 주세요."
        )

    if error_code == "RAG_EMPTY_RESULT_CONTRACT" and mode == "LOOKUP":
        if action == "detail" or has_explicit_id:
            return "조건에 맞는 조회 결과를 찾지 못했습니다. 식별자나 질문 조건이 정확한지 다시 확인해 주세요."
        if action == "list":
            return "조건에 맞는 목록 결과를 찾지 못했습니다. 기관명, 연구자명, 연도 같은 조건을 조금 더 조정해 다시 시도해 주세요."

    if error_code == "RAG_EMPTY_RESULT_CONTRACT" and mode == "JOIN":
        return (
            "관계형 조회를 시도했지만 연결 가능한 결과를 찾지 못했습니다. "
            "기준 과제나 성과를 더 구체적으로 지정해 다시 질문해 주세요."
        )

    if error_code == "LOOKUP_JOIN_HYBRID_RUNTIME_FAILURE" and mode == "LOOKUP":
        return (
            "정밀 조회를 수행하는 검색 엔진 응답을 받지 못했습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    if error_code == "LOOKUP_JOIN_HYBRID_RUNTIME_FAILURE" and mode == "JOIN":
        return (
            "관계형 정밀 조회를 수행하는 검색 엔진 응답을 받지 못했습니다. "
            "잠시 후 다시 시도해 주세요."
        )

    return "요청을 처리하는 데 필요한 근거를 만들지 못했습니다. 질문을 조금 더 구체적으로 바꿔 다시 시도해 주세요."
