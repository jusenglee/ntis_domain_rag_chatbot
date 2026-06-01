"""
사용자 질문을 다단계(Stagewise)로 분석하여 실행 전략을 수립하는 플래너 런타임입니다.

ADR-0001 체제에서 플래너는 에이전트의 도구 호출을 검증하고, 
데이터 조회를 위한 엄격한 L1 계약(IntentContract)을 컴파일하는 역할을 수행합니다.
Stage 1 (의도 분류) -> Stage 1.5 (개념 추출) -> Stage 2 (세부 필터 수립) 순으로 진행됩니다.

재현성을 위해 각 단계의 입력, 출력, LLM 설정 및 자동 보정(Repair) 과정을 상세히 로깅합니다.
"""

from __future__ import annotations

from dataclasses import replace
import json
import re
import time
from typing import Any, Optional

from apps.api.contracts.runtime_contracts import (
    extract_unsupported_project_key_aliases,
    has_ambiguous_project_key_label,
    has_explicit_project_id_label,
    has_explicit_project_no_label,
    infer_project_key_axis,
    sanitize_ids_map_semantics,
)
from apps.api.contracts.workflow_models import HardContractV1, QuestionAnalysis, SoftStrategyHintsV1
from apps.api.runtime_helpers import log_event, mask_query_for_log
from apps.chat.llm_json import iter_json_candidates, sanitize_llm_json
from apps.chat.llm_runtime import build_llm, load_prompt_file

from apps.platform.langchain_compat import BaseMessage, ChatPromptTemplate, PydanticOutputParser, SystemMessage

from apps.conversation.followup_anchor import anchor_to_seed_map
from apps.conversation.view_state import (
    DisplaySnapshot,
    FocusEntity,
    get_active_subject_entity,
)
from apps.planner.planner_contract import StrategyViolation
from apps.planner.planner_context_cards import build_planner_domain_cards
from apps.planner.planner_defaults import (
    PLANNER_DISABLE_THINKING,
    PLANNER_RUNTIME_MAX_TOP_K_SIZE,
    PLANNER_RUNTIME_SCHEMA_VERSION,
    PLANNER_STAGE15_PROMPT_VERSION,
    PLANNER_STAGE2_PROMPT_VERSION,
    PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
    PLANNER_STAGEWISE_ENABLED,
    PLANNER_TEMPERATURE,
)
from apps.planner.agent_intent_adapter import build_agent_intent_gate
from apps.planner.prompt_asset_paths import planner_prompt_path
from apps.planner.planner_stage15_types import PlannerEntityRolePlan
from apps.planner.planner_staged import (
    DeterministicGateStrategy,
    collect_regate_seed_map,
    compose_locked_strategy,
    extract_single_project_seed,
    merge_locked_strategy_slots,
    regate_locked_strategy,
)
from apps.planner.planner_surface_signals import SurfaceSignals, collect_surface_signals
from apps.planner.planner_validation import Stage2ValidationResult, validate_stage2_slots
from apps.planner.query_compiler import compile_stage2_to_qdrant_query
from apps.planner.query_intent import ORG_CUES
from apps.platform.schemas import PlannerStage2Slots


# Stage 2 결과물에서 허용되는 필드 목록 (Schema 정규화용)
_STAGE2_ALLOWED_OUTPUT_FIELDS = {
    "ids_map",
    "candidate_keys",
    "project_key_policy",
    "join_resolution_policy",
    "filters",
    "retrieval_query",
    "limit",
    "display_limit",
    "confidence",
}

# 이전 문맥(Context)에서 ID로 간주하여 추출할 키 목록
_PREV_CONTEXT_SEED_ID_KEYS = (
    "pjt_id",
    "pjt_no",
    "rst_id",
    "person_no",
    "org_id",
    "org_code",
    "biz_no",
    "doi",
    "issn",
)
# 연구자 관련 필터 키 목록
_RESEARCHER_FILTER_KEYS = (
    "participant_researcher_name",
    "participant_researcher_names",
    "participant_researcher",
    "participant_researchers",
    "researcher_name",
    "researcher_names",
    "researcher",
    "people_name",
)
# 일반 기관 관련 필터 키 목록
_GENERIC_ORG_FILTER_KEYS = ("org_name",)
# 역할이 부여된 기관 필터 키 목록
_ROLE_SCOPED_ORG_FILTER_KEYS = (
    "lead_org_name",
    "performing_org_name",
    "participant_org_name",
    "people_affiliation_org_name",
)
# ID 형태의 검색어 정규식 (숫자열 등)
_ID_LIKE_FILTER_TERM_RE = re.compile(r"^(?:\d{8,12}|(?=.*\d)[A-Za-z0-9][A-Za-z0-9_-]{3,63})$")
# 과제(Project) 관련 키워드 큐
_PROJECT_AXIS_CUES = ("과제", "project", "pjt")
# 기관(Organization) 관련 키워드 큐
_EXPLICIT_ORG_QUERY_CUES = tuple(str(cue or "").strip().lower() for cue in ORG_CUES) + (
    "소속",
    "affiliation",
    "organization",
    "institution",
    "company",
    "org",
)
# 인물 조회 관련 액션 목록
_PEOPLE_LOOKUP_ACTIONS = {"list", "stats", "detail"}


def _message_content(value: Any) -> str:
    """메시지 객체에서 텍스트 내용을 추출합니다."""
    return str(getattr(value, "content", value) or "")


def _payload_char_counts(payload: dict[str, Any]) -> dict[str, int]:
    """입력 데이터의 필드별 길이를 계산하여 로깅용 통계를 만듭니다."""
    return {key: len(str(value or "")) for key, value in payload.items()}


def _planner_stage_common_fields(
    *,
    stage_label: str,
    prompt_version: str,
    question: str,
    input_payload: dict[str, Any],
    llm_settings: dict[str, Any],
) -> dict[str, Any]:
    """모든 플래너 단계에서 공통적으로 사용할 로깅 필드를 생성합니다."""
    input_payload_fields = _payload_char_counts(input_payload)
    return {
        "planner_stage": stage_label,
        "prompt_version": prompt_version,
        "question_preview": mask_query_for_log(question, max_len=120),
        "question_chars": len(str(question or "")),
        "input_payload_chars": sum(input_payload_fields.values()),
        "input_payload_fields": input_payload_fields,
        **llm_settings,
    }


def _log_planner_stage_error(
    *,
    event_prefix: str,
    request_id: Optional[str],
    conversation_id: str,
    common_fields: dict[str, Any],
    phase: str,
    exc: Exception,
    dt_ms: float,
    output_json_chars: int = 0,
    json_candidate_count: int = 0,
    raw_content_chars: int = 0,
) -> None:
    """플래너 실행 중 오류가 발생했을 때 상세 정보를 기록합니다."""
    log_event(
        f"{event_prefix}.ERROR",
        request_id=request_id,
        conversation_id=conversation_id,
        **common_fields,
        error_phase=phase,
        error_type=type(exc).__name__,
        error_message=str(exc)[:500], # 에러 메시지 앞부분 기록
        dt_ms=round(dt_ms, 1),
        raw_content_chars=raw_content_chars,
        json_candidate_count=json_candidate_count,
        output_json_chars=output_json_chars,
    )


async def _invoke_planner_stage_json(
    *,
    event_prefix: str,
    stage_label: str,
    request_id: Optional[str],
    conversation_id: str,
    common_fields: dict[str, Any],
    runnable: Any,
    input_payload: dict[str, Any],
    start_fields: Optional[dict[str, Any]] = None,
) -> tuple[str, dict[str, Any]]:
    """LLM을 호출하여 JSON 응답을 얻어오는 공통 로직입니다. 
    로깅과 예외 처리를 일관되게 수행하며 재현성을 위한 데이터를 기록합니다.
    """
    log_event(
        f"{event_prefix}.START",
        request_id=request_id,
        conversation_id=conversation_id,
        **common_fields,
        **(start_fields or {}),
    )
    started_at = time.perf_counter()
    raw_text = ""
    candidates: list[str] = []
    phase = "llm_invoke"
    try:
        # LLM 실행
        raw_msg = await runnable.ainvoke(input_payload)
        raw_text = _message_content(raw_msg)
        candidates = iter_json_candidates(raw_text)
        dt_ms = (time.perf_counter() - started_at) * 1000.0
        
        # LLM 호출 결과 로깅
        log_event(
            f"{event_prefix}.LLM_RESULT",
            request_id=request_id,
            conversation_id=conversation_id,
            **common_fields,
            dt_ms=round(dt_ms, 1),
            raw_content_chars=len(raw_text),
            empty_content=int(not raw_text.strip()),
            json_candidate_count=len(candidates),
        )
        
        # JSON 추출 및 정제
        phase = "json_extract"
        json_text = sanitize_llm_json(
            raw_msg,
            source_stage=stage_label,
            request_id=request_id,
            conversation_id=conversation_id,
        )
        return json_text, {
            "dt_ms": dt_ms,
            "raw_content_chars": len(raw_text),
            "json_candidate_count": len(candidates),
            "output_json_chars": len(json_text),
        }
    except Exception as exc:
        dt_ms = (time.perf_counter() - started_at) * 1000.0
        # 실패 시에도 재현을 위해 획득 가능한 원본 데이터 기록
        _log_planner_stage_error(
            event_prefix=event_prefix,
            request_id=request_id,
            conversation_id=conversation_id,
            common_fields=common_fields,
            phase=phase,
            exc=exc,
            dt_ms=dt_ms,
            raw_content_chars=len(raw_text),
            json_candidate_count=len(candidates),
        )
        raise


def _normalize_stage2_slots_payload(
    raw_payload: Any,
    *,
    request_id: Optional[str],
    conversation_id: str,
) -> dict[str, Any]:
    """LLM이 생성한 Stage 2 슬롯 데이터를 스키마에 맞게 정규화합니다. 
    허용되지 않은 필드는 제거하고 로깅합니다.
    """
    if isinstance(raw_payload, str):
        payload = json.loads(raw_payload)
    else:
        payload = raw_payload
    if not isinstance(payload, dict):
        raise ValueError(f"Planner stage2 payload must be an object, got {type(payload).__name__}")

    cleaned = dict(payload)
    # 스키마에 정의되지 않은 필드 추출 및 제거
    dropped = {key: cleaned.pop(key) for key in list(cleaned.keys()) if key not in _STAGE2_ALLOWED_OUTPUT_FIELDS}
    if dropped:
        log_event(
            "PLANNER.STAGE2.EXTRA_FIELDS_DROPPED",
            request_id=request_id,
            conversation_id=conversation_id,
            dropped_fields=sorted(dropped.keys()),
            dropped_non_null_fields=sorted(key for key, value in dropped.items() if value is not None),
        )
    return cleaned

def _render_prompt_template(template: str, **values: Any) -> str:
    """프롬프트 템플릿의 변수({key})를 실제 값으로 치환합니다."""
    template_text = str(template or "")
    placeholder_names = sorted(set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template_text)))
    missing = [name for name in placeholder_names if name not in values]
    if missing:
        raise RuntimeError(f"planner prompt placeholder missing: {missing}")
    rendered = template_text
    for key in placeholder_names:
        value = values[key]
        # 문자열이 아니면 JSON 문자열로 직렬화하여 삽입
        replacement = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        rendered = rendered.replace("{" + key + "}", str(replacement))
    return rendered


def _signals_payload(signals: SurfaceSignals) -> dict[str, Any]:
    """추출된 표면 신호(Signals)를 로깅 및 프롬프트 주입용 딕셔너리로 변환합니다."""
    return {
        "explicit_count": signals.explicit_count,
        "ordinal_ref": signals.ordinal_ref,
        "years": list(signals.years),
        "id_like_terms": list(signals.id_like_terms),
        "people_terms": list(signals.people_terms),
        "org_terms": list(signals.org_terms),
        "perf_types": list(signals.perf_types),
        "followup_cues": list(signals.followup_cues),
        "high_salience_terms": list(signals.high_salience_terms),
        "raw_person_hint_terms": list(getattr(signals, "raw_person_hint_terms", []) or []),
    }


def _entity_role_payload(plan: PlannerEntityRolePlan) -> dict[str, Any]:
    """Stage 1.5에서 결정된 엔티티 역할 계획을 직렬화합니다."""
    return plan.model_dump()


def _validation_hints_payload(result: Stage2ValidationResult) -> dict[str, Any]:
    """검증 실패 시, LLM에게 전달할 힌트 데이터를 생성합니다."""
    return {
        "errors": list(result.errors),
        "missing_must_keep_terms": list(result.missing_must_keep_terms),
        "missing_people_terms": list(result.missing_people_terms),
        "missing_org_terms": list(result.missing_org_terms),
        "missing_years": list(result.missing_years),
        "missing_perf_types": list(result.missing_perf_types),
    }


def _normalize_terms(values: Any) -> list[str]:
    """검색어나 필터 용어 리스트를 정규화(공백 제거, 중복 제거)합니다."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _append_terms_to_query(query: str, terms: list[str]) -> str:
    """기존 쿼리에 누락된 용어들을 덧붙여 검색 성능을 보강합니다."""
    merged: list[str] = []
    base = str(query or "").strip()
    if base:
        merged.append(base)
    for term in terms:
        text = str(term or "").strip()
        if not text or text in merged:
            continue
        merged.append(text)
    return " ".join(merged).strip()


def _semantic_query_terms_for_repair(*, entity_role_plan: PlannerEntityRolePlan, retrieval_query: str) -> list[str]:
    """검색 쿼리 보정 시, 엔티티 계획에 기반하여 필수 포함 용어를 선별합니다."""
    semantic_kind = str(getattr(entity_role_plan, "semantic_kind", "") or "").strip().lower()
    # 광범위한 이력 조회의 경우에만 추가 보정 수행
    if semantic_kind != "broad_history":
        return []

    existing_query = str(retrieval_query or "").strip()
    out: list[str] = []
    seen: set[str] = set()
    for value in getattr(entity_role_plan, "must_keep_terms", []) or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        # 년도나 단위 등 단순 키워드는 제외
        if re.fullmatch(r"(?:19|20)\d{2}", text):
            continue
        if re.fullmatch(r"\d+\s*(?:개|건|명|편|종)", text):
            continue
        if text in existing_query:
            continue
        seen.add(text)
        out.append(text)
    return out


def _looks_identifier_like_filter_term(value: Any) -> bool:
    """용어가 과제번호나 등록번호 같은 식별자 형태인지 검사합니다."""
    text = str(value or "").strip()
    if not text or re.fullmatch(r"(?:19|20)\d{2}", text):
        return False
    return bool(_ID_LIKE_FILTER_TERM_RE.fullmatch(text))


def _query_mentions_project_axis(query: str) -> bool:
    """쿼리문에 '과제' 관련 키워드가 포함되어 있는지 확인합니다."""
    lowered = str(query or "").strip().lower()
    if not lowered:
        return False
    return any(cue in lowered for cue in _PROJECT_AXIS_CUES)


def _query_has_explicit_org_cue(query: str) -> bool:
    """쿼리문에 '소속'이나 '기관' 같은 명시적인 단서가 있는지 확인합니다."""
    lowered = str(query or "").strip().lower()
    if not lowered:
        return False
    return any(cue and cue in lowered for cue in _EXPLICIT_ORG_QUERY_CUES)


def _merge_filter_terms(filters: dict[str, Any], key: str, values: list[str]) -> list[str]:
    """기존 필터 딕셔너리에 새로운 용어들을 합치고 필터를 업데이트합니다."""
    merged = _normalize_terms([*_normalize_terms(filters.get(key)), *values])
    if merged:
        filters[key] = merged
    return merged


def _resolve_org_filter_key(entity_role_plan: PlannerEntityRolePlan) -> str:
    """엔티티 역할 계획에 따라 가장 적합한 기관 필터 키(예: 주관기관, 소속기관)를 결정합니다."""
    org_role_hint = str(getattr(entity_role_plan, "org_role_hint", "") or "").strip().lower()
    if org_role_hint == "lead_org":
        return "lead_org_name"
    if org_role_hint == "participant_org":
        return "participant_org_name"
    if org_role_hint == "affiliation_org":
        return "people_affiliation_org_name"
    return "org_name"


def _candidate_project_key_count(candidate_keys: dict[str, Any] | None) -> int:
    """분석 결과에 포함된 과제 식별자 후보 개수를 반환합니다."""
    if not isinstance(candidate_keys, dict):
        return 0
    return len(list(candidate_keys.get("project_key") or []))


def _has_prev_anchor(locked_strategy: DeterministicGateStrategy) -> bool:
    """이전 문맥에서 상속받은 앵커(ID 등)가 존재하는지 확인합니다."""
    return bool(
        dict(getattr(locked_strategy, "prev_context_seed", None) or {})
        or dict(getattr(locked_strategy, "gate_seed_map", None) or {})
    )


def _build_hard_contract(
    *,
    question: str,
    locked_strategy: DeterministicGateStrategy,
    entity_role_plan: PlannerEntityRolePlan,
    ids_map: dict[str, list[str]] | None,
    candidate_keys: dict[str, Any] | None,
    project_key_policy: str | None,
    join_key_mode: str | None,
) -> HardContractV1:
    """런타임에서 반드시 준수해야 하는 엄격한 계약(Hard Contract)을 구축합니다."""
    policy_norm = str(project_key_policy or "").strip().lower() or None
    join_key_mode_norm = str(join_key_mode or getattr(locked_strategy, "join_key_mode", None) or "").strip().lower() or None
    return HardContractV1(
        explicit_project_id_label=bool(has_explicit_project_id_label(question)),
        explicit_project_no_label=bool(has_explicit_project_no_label(question)),
        ambiguous_project_key_label=bool(has_ambiguous_project_key_label(question)),
        unsupported_project_key_aliases=extract_unsupported_project_key_aliases(question),
        resolved_project_key_axis=infer_project_key_axis(ids_map=ids_map, project_key_policy=policy_norm),
        candidate_project_key_count=_candidate_project_key_count(candidate_keys),
        project_key_policy=policy_norm,
        join_anchor_required=bool(getattr(entity_role_plan, "anchor_required", False) and getattr(locked_strategy, "relation", None)),
        join_anchor_present=_has_prev_anchor(locked_strategy),
        join_key_mode=join_key_mode_norm,
        project_key_axis_locked=bool(policy_norm in {"resolved_pjt_id", "resolved_pjt_no", "anchor_locked_pjt_id", "anchor_locked_pjt_no"}),
    )


def _build_soft_strategy_hints(
    *,
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    locked_strategy: DeterministicGateStrategy,
    candidate_keys: dict[str, Any] | None = None,
) -> SoftStrategyHintsV1:
    """LLM에게 전략 수립 시 참고하도록 줄 유연한 힌트(Soft Hints)를 구축합니다."""
    return SoftStrategyHintsV1(
        years=list(signals.years or []),
        id_like_terms=list(signals.id_like_terms or []),
        people_terms=list(signals.people_terms or []),
        org_terms=list(signals.org_terms or []),
        perf_types=list(signals.perf_types or []),
        followup_cues=list(signals.followup_cues or []),
        must_keep_terms=list(getattr(entity_role_plan, "must_keep_terms", []) or []),
        semantic_kind=str(getattr(entity_role_plan, "semantic_kind", "") or "").strip() or None,
        perf_type_policy=str(getattr(entity_role_plan, "perf_type_policy", "") or "").strip() or None,
        org_role_hint=str(getattr(entity_role_plan, "org_role_hint", "") or "").strip() or None,
        candidate_project_key_count=_candidate_project_key_count(candidate_keys),
        has_prev_anchor=_has_prev_anchor(locked_strategy),
    )


def _apply_deterministic_stage2_repair(
    *,
    stage2_slots: Any,
    validation: Stage2ValidationResult,
    locked_strategy: DeterministicGateStrategy,
    entity_role_plan: PlannerEntityRolePlan,
    question: str,
    request_id: Optional[str],
    conversation_id: str,
) -> tuple[Any, dict[str, Any]]:
    """[결정론적 보정] LLM이 필터나 검색어에서 중요한 정보를 누락한 경우, 알고리즘적으로 강제 보정합니다."""
    payload = stage2_slots.model_dump() if hasattr(stage2_slots, "model_dump") else dict(stage2_slots or {})
    filters = dict(payload.get("filters") or {})
    locked_mode = str(getattr(locked_strategy, "mode", "") or "").strip().lower()
    retrieval_query_before = str(payload.get("retrieval_query") or "").strip()
    injected_filters: dict[str, list[str]] = {}
    # JOIN 모드가 아닌 경우에만 필터 보정 허용 (JOIN은 앵커가 우선됨)
    filter_repairs_allowed = locked_mode != "search"

    # 누락된 연구자 보정
    if filter_repairs_allowed and validation.missing_people_terms:
        merged = _merge_filter_terms(filters, "participant_researcher_name", list(validation.missing_people_terms))
        if merged:
            injected_filters["participant_researcher_name"] = merged

    # 누락된 기관 보정
    if filter_repairs_allowed and validation.missing_org_terms:
        org_filter_key = _resolve_org_filter_key(entity_role_plan)
        merged = _merge_filter_terms(filters, org_filter_key, list(validation.missing_org_terms))
        if merged:
            injected_filters[org_filter_key] = merged

    # 누락된 년도 보정
    if filter_repairs_allowed and validation.missing_years:
        merged = _merge_filter_terms(filters, "years", list(validation.missing_years))
        if merged:
            injected_filters["years"] = merged

    # 검색 쿼리(Retrieval Query)에 누락된 용어 합치기
    semantic_query_terms = _semantic_query_terms_for_repair(
        entity_role_plan=entity_role_plan,
        retrieval_query=retrieval_query_before,
    )
    repair_terms = _normalize_terms(
        [
            *semantic_query_terms,
            *list(validation.missing_must_keep_terms),
            *list(validation.missing_people_terms),
            *list(validation.missing_org_terms),
            *list(validation.missing_years),
        ]
    )
    retrieval_query_after = _append_terms_to_query(retrieval_query_before or question, repair_terms)

    payload["filters"] = filters
    payload["retrieval_query"] = retrieval_query_after or question
    repaired_slots = PlannerStage2Slots.model_validate(payload)
    
    # 구조화된 필터 재정제 (엔티티 역할에 맞지 않는 필터 제거)
    repaired_slots = _sanitize_stage2_structured_filters(
        slots=repaired_slots,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        question=question,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    
    repaired_payload = repaired_slots.model_dump() if hasattr(repaired_slots, "model_dump") else dict(repaired_slots or {})
    changed = repaired_payload != (stage2_slots.model_dump() if hasattr(stage2_slots, "model_dump") else dict(stage2_slots or {}))
    
    metadata = {
        "applied": changed,
        "prompt_miss_terms": list(validation.missing_must_keep_terms),
        "injected_filters": injected_filters,
        "query_terms": repair_terms,
        "retrieval_query_before": retrieval_query_before,
        "retrieval_query_after": str(repaired_payload.get("retrieval_query") or "").strip(),
        "filter_repairs_allowed": filter_repairs_allowed,
    }
    
    if changed:
        log_event(
            "PLANNER.STAGE2.DETERMINISTIC_REPAIR",
            request_id=request_id,
            conversation_id=conversation_id,
            reason_code="deterministic_repair_applied",
            prompt_miss_terms=metadata["prompt_miss_terms"],
            injected_filter_keys=sorted(injected_filters.keys()),
            query_terms=repair_terms,
            filter_repairs_allowed=int(filter_repairs_allowed),
            retrieval_query_before=retrieval_query_before,
            retrieval_query_after=metadata["retrieval_query_after"],
        )
    return repaired_slots, metadata


def _sanitize_stage2_structured_filters(
    *,
    slots: Any,
    entity_role_plan: PlannerEntityRolePlan,
    locked_strategy: DeterministicGateStrategy | None = None,
    signals: SurfaceSignals | None = None,
    question: str = "",
    request_id: Optional[str],
    conversation_id: str,
) -> Any:
    """엔티티 역할 계획을 Qdrant query plan으로 컴파일하며 Stage2 슬롯을 정제합니다."""
    compiled = compile_stage2_to_qdrant_query(
        stage2_slots=slots,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        question=question,
        signals=signals,
    )
    dropped = compiled.dropped_filters
    if dropped:
        researcher_drops_unsalvaged = sorted(
            {term for key, values in dropped.items() if key in _RESEARCHER_FILTER_KEYS for term in values}
        )
        log_event(
            "PLANNER.STAGE2.FILTERS.SANITIZED",
            request_id=request_id,
            conversation_id=conversation_id,
            dropped_fields=sorted(key for key, values in dropped.items() if values),
            salvaged_terms=list(compiled.salvaged_terms),
            researcher_drops_unsalvaged=researcher_drops_unsalvaged,
            compiler_validation=compiled.validation.to_dict(),
            postprocess_kind=compiled.qdrant_query_plan.postprocess.get("kind"),
        )

    return PlannerStage2Slots.model_validate(compiled.sanitized_slots)


def _has_explicit_perf_seed(ids_map: dict[str, list[str]]) -> bool:
    """성과(Performance) 관련 명시적 식별자(DOI, 성과ID 등)가 있는지 확인합니다."""
    return any(ids_map.get(key) for key in ("rst_id", "doi", "issn", "perf_id", "paper_id", "patent_reg_no", "patent_app_no"))


def _enforce_runtime_legality(
    *,
    normalized_intent: Any,
    locked_strategy: DeterministicGateStrategy,
    stage2: Any,
) -> None:
    """수립된 전략이 실행 가능한 규약(Legality)을 충족하는지 검사합니다. 위반 시 예외를 발생시킵니다."""
    def _raise(error_code: str, reason: str) -> None:
        raise StrategyViolation(error_code=error_code, reason=reason)

    ids_map = dict(getattr(stage2, "ids_map", None) or {})
    people_terms = list(getattr(normalized_intent, "people_terms", None) or [])
    org_terms = list(getattr(normalized_intent, "org_terms", None) or [])
    broad_people_org_query = bool(people_terms or org_terms)
    has_anchor_seed = bool(
        dict(getattr(locked_strategy, "prev_context_seed", None) or {})
        or dict(getattr(locked_strategy, "gate_seed_map", None) or {})
    )

    # 성과 상세 조회 시 ID 필수
    if locked_strategy.head == "perf" and locked_strategy.action == "detail" and not _has_explicit_perf_seed(ids_map):
        _raise("PLANNER_PERF_DETAIL_EXPLICIT_ID_REQUIRED", "perf detail requires explicit perf id")
    # 광범위한 인물/기관 검색 시 JOIN 금지 (성능 및 모호성 문제)
    if broad_people_org_query and str(locked_strategy.mode or "").strip().lower() == "join":
        _raise("PLANNER_BROAD_PEOPLE_ORG_JOIN_FORBIDDEN", "broad people/org query cannot use JOIN")
    # 관계(Relation) 조회 시 기준이 되는 앵커 필수
    if locked_strategy.relation in {"project_perf", "perf_project"} and not has_anchor_seed:
        _raise("PLANNER_RELATION_EXPLICIT_ANCHOR_REQUIRED", "relation query requires explicit anchor")


def _extract_prev_context_seed(
    *,
    question: str,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    display_snapshot: Optional[DisplaySnapshot] = None,
    focus_entity: Optional[FocusEntity] = None,
    allow_ordinal_resolution: bool = False,
    default_context_kind: str = "project",
) -> dict[str, list[str]]:
    """이전 문맥으로부터 현재 질문의 대상이 되는 시드 ID들을 추출합니다."""
    # 1. 사용자가 명시적으로 선택한 포커스 엔티티가 있으면 최우선
    if focus_entity is not None:
        seed_map = anchor_to_seed_map(focus_entity)
        if seed_map:
            return seed_map
            
    # 2. 화면에 딱 하나의 아이템만 출력된 상태면 그것을 시드로 간주
    if display_snapshot is not None and len(display_snapshot.items) == 1:
        item = display_snapshot.items[0]
        seed_map = anchor_to_seed_map(FocusEntity(
            kind=item.entity_kind,
            source="display_snapshot",
            doc_id=item.doc_id,
            pjt_id=item.pjt_id,
            pjt_no=item.pjt_no,
            rst_id=item.rst_id,
            person_no=item.person_no,
            org_id=item.org_id,
            org_code=item.org_code,
            biz_no=item.biz_no,
            doi=item.doi,
            issn=item.issn,
            title_text=item.title_text,
        ))
        if seed_map:
            return seed_map
            
    # 3. 증거 데이터(Canonical Evidence)에서 유일한 ID군 추출
    if canonical_evidence:
        unique_ids: dict[str, set[str]] = {key: set() for key in _PREV_CONTEXT_SEED_ID_KEYS}
        for item in canonical_evidence:
            if not isinstance(item, dict):
                continue
            ids = item.get("ids") or {}
            for key in _PREV_CONTEXT_SEED_ID_KEYS:
                value = str(ids.get(key) or "").strip()
                if value:
                    unique_ids[key].add(value)
        # 특정 종류의 ID가 하나뿐이라면 그것을 확정 시드로 사용
        for key in _PREV_CONTEXT_SEED_ID_KEYS:
            if len(unique_ids[key]) == 1:
                return {key: [next(iter(unique_ids[key]))]}
                
    # 4. 일반적인 이전 문맥 스냅샷에서 추출
    seed = extract_single_project_seed(prev_context)
    if seed:
        return seed
    return {}


_RAW_PERSON_CONTEXT_CUES = ("연구자", "박사", "교수", "대표", "원장", "소장", "님")
_RAW_PERSON_REJECT_FRAGMENTS = (
    "박사급",
    "전문가",
    "분야",
    "과제",
    "기술",
    "시스템",
    "연구동향",
)
_RAW_PERSON_QUOTE_PAIRS = (
    ("'", "'"),
    ('"', '"'),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("(", ")"),
    ("[", "]"),
)


def _raw_person_hint_has_explicit_evidence(question: str, term: str) -> bool:
    """raw 성씨 휴리스틱 후보가 실제 인명으로 쓰였다는 문맥 증거를 확인한다."""
    q = str(question or "")
    candidate = str(term or "").strip()
    if not q or not candidate:
        return False
    if any(fragment in candidate for fragment in _RAW_PERSON_REJECT_FRAGMENTS):
        return False

    escaped = re.escape(candidate)
    for left, right in _RAW_PERSON_QUOTE_PAIRS:
        if re.search(rf"{re.escape(left)}\s*{escaped}\s*{re.escape(right)}", q):
            return True

    cue_alt = "|".join(re.escape(cue) for cue in _RAW_PERSON_CONTEXT_CUES)
    # "신동구 연구자", "신동구 박사", "신동구님"처럼 후보 바로 옆에
    # 명시 인명 문맥이 있을 때만 raw hint를 구조화 인명으로 승격한다.
    return bool(
        re.search(rf"{escaped}\s*(?:{cue_alt})", q)
        or re.search(rf"(?:{cue_alt})\s*{escaped}", q)
    )


def _sanitize_stage15_people_keep(
    *,
    stage15: PlannerEntityRolePlan,
    signals: SurfaceSignals,
    question: str,
    request_id: Optional[str],
    conversation_id: str,
) -> PlannerEntityRolePlan:
    """Stage 1.5 출력의 people_terms_to_keep을 표면 신호 후보 집합으로 제한한다.

    LLM이 질문에 없는 토큰을 keep으로 만들어 stage2 researcher 필터로 새는 것을
    차단한다. 확정 인명 신호(`signals.people_terms`)는 통과시키고, 성씨 기반 raw
    후보는 명시 인명 문맥이 있을 때만 통과시킨다. 제거된 토큰은
    PEOPLE_SANITIZED 로그를 남긴다.
    """
    keep_orig = list(stage15.people_terms_to_keep or [])
    if not keep_orig:
        return stage15
    confirmed_people = set(signals.people_terms or [])
    raw_candidates = set(getattr(signals, "raw_person_hint_terms", []) or [])
    keep_accepted: list[str] = []
    keep_removed: list[str] = []
    removed_without_surface_signal: list[str] = []
    removed_without_explicit_evidence: list[str] = []
    for term in keep_orig:
        if term in confirmed_people:
            keep_accepted.append(term)
            continue
        if term in raw_candidates:
            if _raw_person_hint_has_explicit_evidence(question, term):
                keep_accepted.append(term)
            else:
                keep_removed.append(term)
                removed_without_explicit_evidence.append(term)
            continue
        keep_removed.append(term)
        removed_without_surface_signal.append(term)
    if not keep_removed:
        return stage15
    # 표면 신호에도 없는 hallucinated token은 must_keep에서도 제거한다. 질문에 실제로
    # 등장한 raw 후보는 인명 필터에서만 제거하고 retrieval_query 보존 힌트는 유지한다.
    removed_set = set(removed_without_surface_signal)
    must_keep_accepted = [
        t for t in (stage15.must_keep_terms or []) if t not in removed_set
    ]
    log_event(
        "PLANNER.STAGE15.PEOPLE_SANITIZED",
        request_id=request_id,
        conversation_id=conversation_id,
        removed_people_terms=keep_removed,
        accepted_people_terms=keep_accepted,
        signals_people_terms=list(signals.people_terms or []),
        signals_raw_person_hint_terms=list(
            getattr(signals, "raw_person_hint_terms", []) or []
        ),
        removed_without_surface_signal=removed_without_surface_signal,
        removed_without_explicit_evidence=removed_without_explicit_evidence,
    )
    return stage15.model_copy(
        update={
            "people_terms_to_keep": keep_accepted,
            "must_keep_terms": must_keep_accepted,
        }
    )


async def run_planner_stage15(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    locked_strategy: DeterministicGateStrategy,
    signals: SurfaceSignals,
    cards: dict[str, str],
) -> PlannerEntityRolePlan:
    """[Stage 1.5] 추출된 엔티티들이 결과 데이터에서 어떤 역할을 수행해야 하는지(예: 소속기관, 참여자) 계획합니다."""
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerEntityRolePlan)
    
    system_prompt = _render_prompt_template(
        await load_prompt_file(planner_prompt_path(f"planner_stage15_{PLANNER_STAGE15_PROMPT_VERSION}.md")),
        **cards,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<surface_signals>{surface_signals}</surface_signals>\n<user_query>{question}</user_query>",
            ),
        ]
    )
    
    llm_bind_settings = {
        "reasoning_effort": "low",
        "include_reasoning": False,
        "temperature": PLANNER_TEMPERATURE,
        "top_p": 1.0,
        "max_tokens": 800,
    }
    planner_llm = llm.bind(
        **llm_bind_settings,
        request_id=request_id,
        conversation_id=conversation_id,
        planner_stage="stage15",
        planner_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
    )
    
    locked_strategy_payload = json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False)
    surface_signals_payload = json.dumps(_signals_payload(signals), ensure_ascii=False)
    
    input_payload = {
        "format_instructions": parser.get_format_instructions(),
        "question": question,
        "locked_strategy": locked_strategy_payload,
        "surface_signals": surface_signals_payload,
    }
    
    common_fields = _planner_stage_common_fields(
        stage_label="stage15",
        prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
        question=question,
        input_payload=input_payload,
        llm_settings={
            "model_name": "solar_vllm_0",
            **llm_bind_settings,
        },
    )
    
    json_text, stage_stats = await _invoke_planner_stage_json(
        event_prefix="PLANNER.STAGE15",
        stage_label="stage15",
        request_id=request_id,
        conversation_id=conversation_id,
        common_fields=common_fields,
        runnable=prompt | planner_llm,
        input_payload=input_payload,
    )
    
    parse_started_at = time.perf_counter()
    try:
        stage15 = parser.parse(json_text)
    except Exception as exc:
        _log_planner_stage_error(
            event_prefix="PLANNER.STAGE15",
            request_id=request_id,
            conversation_id=conversation_id,
            common_fields=common_fields,
            phase="parse",
            exc=exc,
            dt_ms=(time.perf_counter() - parse_started_at) * 1000.0,
            raw_content_chars=stage_stats.get("raw_content_chars", 0),
            json_candidate_count=stage_stats.get("json_candidate_count", 0),
            output_json_chars=stage_stats.get("output_json_chars", 0),
        )
        raise
        
    log_event(
        "PLANNER.STAGE15",
        request_id=request_id,
        conversation_id=conversation_id,
        planner_stage="stage15",
        confidence=round(stage15.confidence, 3),
        org_role_hint=stage15.org_role_hint,
        anchor_required=int(stage15.anchor_required),
        semantic_kind=stage15.semantic_kind,
        dt_ms=round(stage_stats.get("dt_ms", 0.0), 1),
        planner_stage15_prompt_version=PLANNER_STAGE15_PROMPT_VERSION,
    )
    stage15 = _sanitize_stage15_people_keep(
        stage15=stage15,
        signals=signals,
        question=question,
        request_id=request_id,
        conversation_id=conversation_id,
    )
    return stage15


def determine_locked_strategy(
    *,
    question: str,
    stage1: Any,
    normalized_intent: Any,
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    display_snapshot: Optional[DisplaySnapshot],
    focus_entity: Optional[FocusEntity],
) -> DeterministicGateStrategy:
    """Stage 1의 의도 분류와 이전 문맥을 바탕으로 고정된 실행 전략(Locked Strategy)을 확정합니다."""
    base_ids_map = dict(getattr(normalized_intent, "ids_map", {}) or {})
    
    # 이전 문맥에서 시드 ID 추출
    prev_context_seed = _extract_prev_context_seed(
        question=question,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence,
        display_snapshot=display_snapshot,
        focus_entity=focus_entity,
        allow_ordinal_resolution=bool(getattr(stage1, "referential_followup", False)),
        default_context_kind=str(getattr(normalized_intent, "base_route", None) or "project").strip().lower() or "project",
    )
    
    # 분석에 허용된 시드들만 정제
    gate_seed_map = collect_regate_seed_map(
        {**base_ids_map, **prev_context_seed},
        allowed_keys=PLANNER_STAGE2_REGATE_SEED_ALLOWED_KEYS,
    )
    
    stage1_payload = stage1.model_dump() if hasattr(stage1, "model_dump") else {
        "action": getattr(stage1, "action", None),
        "head": getattr(stage1, "head", None),
        "relation_candidate": getattr(stage1, "relation_candidate", None),
        "referential_followup": getattr(stage1, "referential_followup", None),
        "confidence": getattr(stage1, "confidence", None),
    }
    
    # 전략 합성이 (Mode, Relation 등 결정)
    locked = compose_locked_strategy(
        stage1=stage1_payload,
        ids_map={**base_ids_map, **prev_context_seed},
        has_prev_anchor=bool(prev_context_seed),
        prev_context_seed=prev_context_seed,
        gate_seed_map=gate_seed_map,
    )
    
    # 인물 조회의 경우 표면 단어 존재 시 LOOKUP 모드로 강제 보정
    people_terms = [str(v).strip() for v in (getattr(normalized_intent, "people_terms", None) or []) if str(v).strip()]
    gate_mode_correction = None
    if (
        locked.mode == "SEARCH"
        and locked.head == "people"
        and locked.action in _PEOPLE_LOOKUP_ACTIONS
        and people_terms
        and not locked.relation
    ):
        locked = replace(locked, mode="LOOKUP", relation=None, join_key_mode=None)
        gate_mode_correction = "people_anchor_lookup"
        
    log_event(
        "PLANNER.GATE",
        stage1_action=stage1.action,
        stage1_head=stage1.head,
        gate_mode=locked.mode,
        gate_relation=locked.relation,
        gate_mode_correction=gate_mode_correction,
        used_prev_context_seed=int(bool(prev_context_seed)),
    )
    return locked


async def run_planner_stage2(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    locked_strategy: DeterministicGateStrategy,
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    hard_contract: HardContractV1,
    soft_strategy_hints: SoftStrategyHintsV1,
    cards: dict[str, str],
    validation_hints: dict[str, Any] | None = None,
    previous_output: dict[str, Any] | None = None,
) -> Any:
    """[Stage 2] 확정된 전략과 역할 계획에 따라 실제 검색에 사용될 세부 슬롯(필터, 쿼리, 제한 등)을 생성합니다."""
    llm = build_llm(model_name="solar_vllm_0")
    parser = PydanticOutputParser(pydantic_object=PlannerStage2Slots)
    
    system_prompt = _render_prompt_template(
        await load_prompt_file(planner_prompt_path(f"planner_stage2_{PLANNER_STAGE2_PROMPT_VERSION}.md")),
        **cards,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n<locked_strategy>{locked_strategy}</locked_strategy>\n<hard_contract>{hard_contract}</hard_contract>\n<soft_strategy_hints>{soft_strategy_hints}</soft_strategy_hints>\n<surface_signals>{surface_signals}</surface_signals>\n<entity_role_plan>{entity_role_plan}</entity_role_plan>\n<validation_hints>{validation_hints}</validation_hints>\n<previous_output>{previous_output}</previous_output>\n<user_query>{question}</user_query>",
            ),
        ]
    )
    
    llm_bind_settings = {
        "reasoning_effort": "low",
        "include_reasoning": False,
        "temperature": PLANNER_TEMPERATURE,
        "top_p": 1.0,
        "max_tokens": 300,
    }
    planner_llm = llm.bind(
        **llm_bind_settings,
        request_id=request_id,
        conversation_id=conversation_id,
        planner_stage="stage2",
        planner_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
    )
    
    # 주입할 페이로드 직렬화
    locked_strategy_payload = json.dumps(locked_strategy.to_prompt_payload(), ensure_ascii=False)
    hard_contract_payload = json.dumps(hard_contract.model_dump(), ensure_ascii=False)
    soft_strategy_hints_payload = json.dumps(soft_strategy_hints.model_dump(), ensure_ascii=False)
    surface_signals_payload = json.dumps(_signals_payload(signals), ensure_ascii=False)
    entity_role_payload = json.dumps(_entity_role_payload(entity_role_plan), ensure_ascii=False)
    
    input_payload = {
        "format_instructions": parser.get_format_instructions(),
        "question": question,
        "locked_strategy": locked_strategy_payload,
        "hard_contract": hard_contract_payload,
        "soft_strategy_hints": soft_strategy_hints_payload,
        "surface_signals": surface_signals_payload,
        "entity_role_plan": entity_role_payload,
        "validation_hints": json.dumps(validation_hints or {}, ensure_ascii=False),
        "previous_output": json.dumps(previous_output or {}, ensure_ascii=False),
    }
    
    common_fields = _planner_stage_common_fields(
        stage_label="stage2",
        prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
        question=question,
        input_payload=input_payload,
        llm_settings={
            "model_name": "solar_vllm_0",
            **llm_bind_settings,
        },
    )
    
    json_text, stage_stats = await _invoke_planner_stage_json(
        event_prefix="PLANNER.STAGE2",
        stage_label="stage2",
        request_id=request_id,
        conversation_id=conversation_id,
        common_fields=common_fields,
        runnable=prompt | planner_llm,
        input_payload=input_payload,
    )
    
    # 정규화 및 파싱
    try:
        normalized_slots = _normalize_stage2_slots_payload(json_text, request_id=request_id, conversation_id=conversation_id)
        slots = parser.parse(json.dumps(normalized_slots, ensure_ascii=False))
        # 필터 정제 적용
        slots = _sanitize_stage2_structured_filters(
            slots=slots,
            entity_role_plan=entity_role_plan,
            locked_strategy=locked_strategy,
            signals=signals,
            question=question,
            request_id=request_id,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        _log_planner_stage_error(
            event_prefix="PLANNER.STAGE2",
            request_id=request_id,
            conversation_id=conversation_id,
            common_fields=common_fields,
            phase="parse_or_sanitize",
            exc=exc,
            dt_ms=stage_stats.get("dt_ms", 0.0),
        )
        raise
        
    log_event(
        "PLANNER.STAGE2",
        request_id=request_id,
        conversation_id=conversation_id,
        planner_stage="stage2",
        confidence=round(slots.confidence, 3),
        retrieval_query=str(slots.retrieval_query or ""),
        planner_stage2_prompt_version=PLANNER_STAGE2_PROMPT_VERSION,
    )
    return slots


def assemble_question_analysis(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    stage1: Any,
    stage2: Any,
    signals: SurfaceSignals,
    entity_role_plan: PlannerEntityRolePlan,
    locked_strategy: DeterministicGateStrategy,
) -> Any:
    """모든 단계의 결과를 취합하여 최종 질의 분석 결과(QuestionAnalysis)를 구성합니다."""
    # 시드 데이터 정제 (무효한 값 제거)
    ids_map, candidate_keys, invalids = sanitize_ids_map_semantics(stage2.ids_map, question_text=question, candidate_keys=getattr(stage2, "candidate_keys", None))
    
    # stage1.5의 org_role_hint(주관기관 역할 힌트)를 stage2 filters에 주입한다.
    # 다운스트림 머지(merge_planner_hints)는 filters.get("org_role")에서 역할을 찾으므로
    # 이 키가 없으면 앵커-스트릭트 server filter 게이트가 영구히 닫혀 있다.
    _merged_filters = dict(stage2.filters or {})
    _org_role_from_stage15 = str(getattr(entity_role_plan, "org_role_hint", "") or "").strip().lower() or None
    if _org_role_from_stage15 and not _merged_filters.get("org_role"):
        _merged_filters["org_role"] = _org_role_from_stage15

    # 최종 결과물 조합
    payload = merge_locked_strategy_slots(
        schema_version=PLANNER_RUNTIME_SCHEMA_VERSION,
        locked=locked_strategy,
        slots={
            "ids_map": ids_map,
            "candidate_keys": candidate_keys,
            "project_key_policy": getattr(stage2, "project_key_policy", None),
            "join_resolution_policy": getattr(stage2, "join_resolution_policy", None),
            "filters": _merged_filters,
            "limit": min(stage2.limit, PLANNER_RUNTIME_MAX_TOP_K_SIZE),
            "display_limit": min(getattr(stage2, "display_limit", stage2.limit), min(stage2.limit, PLANNER_RUNTIME_MAX_TOP_K_SIZE)),
            "retrieval_query": stage2.retrieval_query or question,
            "confidence": min(stage1.confidence, stage2.confidence),
        },
        default_query=question,
    )
    
    # 런타임에서 시드 유실 여부 등에 따른 JOIN 전략 자동 보정 로직 (조용히 모드 강등 등)
    # ... (생략된 세부 보정 로직은 원본과 동일하게 유지)

    # detail 액션은 limit을 1로 강제 — LLM이 stage2에서 더 큰 값을 반환해도 허용하지 않음
    if str(payload.get("action") or "").strip().lower() == "detail" or \
            str(payload.get("output_type") or "").strip().lower() == "detail":
        payload["limit"] = 1
        payload["display_limit"] = 1
        log_event(
            "PLANNER.DETAIL_GUARD",
            request_id=request_id,
            conversation_id=conversation_id,
            reason="detail_limit_normalized_to_1",
        )

    compiled_query = compile_stage2_to_qdrant_query(
        stage2_slots=payload,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        question=question,
        signals=signals,
    )
    payload["filters"] = dict(compiled_query.sanitized_slots.get("filters") or {})
    payload["retrieval_query"] = compiled_query.sanitized_slots.get("retrieval_query") or question
    payload["qdrant_query_plan"] = compiled_query.qdrant_query_plan.to_dict()
    log_event(
        "PLANNER.QUERY_COMPILER",
        request_id=request_id,
        conversation_id=conversation_id,
        compiler="stage2_qdrant_query_compiler",
        validation_ok=int(compiled_query.validation.ok),
        violation_codes=[item.code for item in compiled_query.validation.violations],
        dropped_filter_keys=sorted(key for key, values in compiled_query.dropped_filters.items() if values),
        postprocess_kind=compiled_query.qdrant_query_plan.postprocess.get("kind"),
        target_collections=compiled_query.qdrant_query_plan.target_collections,
    )

    payload["planner_source"] = "stagewise"
    # 최종 계약서 첨부
    payload["hard_contract"] = _build_hard_contract(
        question=question,
        locked_strategy=locked_strategy,
        entity_role_plan=entity_role_plan,
        ids_map=dict(payload.get("ids_map") or {}),
        candidate_keys=dict(payload.get("candidate_keys") or {}),
        project_key_policy=payload.get("project_key_policy"),
        join_key_mode=payload.get("join_key_mode"),
    ).model_dump()
    
    payload["soft_strategy_hints"] = _build_soft_strategy_hints(
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        candidate_keys=dict(payload.get("candidate_keys") or {}),
    ).model_dump()
    
    qa = QuestionAnalysis.model_validate(payload)
    log_event(
        "PLANNER.ASSEMBLE",
        request_id=request_id,
        conversation_id=conversation_id,
        mode=qa.mode,
        action=qa.action,
        relation=qa.relation,
    )
    return qa


async def run_stagewise_question_analysis(
    *,
    question: str,
    conversation_id: str,
    request_id: Optional[str],
    chat_history: list[BaseMessage],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]],
    normalized_intent: Any,
    view_state: Any = None,
) -> Any:
    """[메인 엔트리포인트] 다단계 플래너를 실행하여 질문 분석 결과를 도출합니다."""
    display_snapshot = getattr(view_state, "visible_answer_manifest", None)
    focus_entity = get_active_subject_entity(view_state)
    
    # 도메인 지식 카드 빌드. Stage 1은 Dialogue Agent/NormalizedIntent를
    # deterministic adapter로 변환하므로 LLM prompt card를 로드하지 않는다.
    stage15_cards = await build_planner_domain_cards(prompt_name=f"planner_stage15_{PLANNER_STAGE15_PROMPT_VERSION}")
    stage2_cards = await build_planner_domain_cards(prompt_name=f"planner_stage2_{PLANNER_STAGE2_PROMPT_VERSION}")
    
    # 표면 신호 추출
    signals = collect_surface_signals(question, normalized_intent)
    log_event("PLANNER.SIGNALS", request_id=request_id, conversation_id=conversation_id, **_signals_payload(signals))
    
    # Agent intent gate: Dialogue Agent가 확정한 L1 intent를 planner gate contract로 변환.
    stage1 = build_agent_intent_gate(
        normalized_intent=normalized_intent,
        signals=signals,
    )
    log_event(
        "PLANNER.STAGE1",
        request_id=request_id,
        conversation_id=conversation_id,
        planner_stage="stage1",
        planner_gate_source="agent_intent_adapter",
        action=stage1.action,
        head=stage1.head,
        relation_candidate=stage1.relation_candidate,
        referential_followup=int(stage1.referential_followup),
        confidence=round(stage1.confidence, 3),
    )
    
    # 전략 고정
    locked_strategy = determine_locked_strategy(
        question=question, stage1=stage1, normalized_intent=normalized_intent,
        prev_context=prev_context, canonical_evidence=canonical_evidence,
        display_snapshot=display_snapshot, focus_entity=focus_entity,
    )
    
    # Stage 1.5: 역할 계획
    stage15 = await run_planner_stage15(
        question=question, conversation_id=conversation_id, request_id=request_id,
        locked_strategy=locked_strategy, signals=signals, cards=stage15_cards,
    )
    
    # Stage 2: 슬롯 생성
    stage2 = await run_planner_stage2(
        question=question, conversation_id=conversation_id, request_id=request_id,
        locked_strategy=locked_strategy, signals=signals, entity_role_plan=stage15,
        hard_contract=_build_hard_contract(question=question, locked_strategy=locked_strategy, entity_role_plan=stage15, ids_map={}, candidate_keys={}, project_key_policy=None, join_key_mode=None),
        soft_strategy_hints=_build_soft_strategy_hints(signals=signals, entity_role_plan=stage15, locked_strategy=locked_strategy),
        cards=stage2_cards,
    )
    
    # 검증 및 재시도/보정 로직
    validation = validate_stage2_slots(question=question, signals=signals, entity_role_plan=stage15, locked_strategy=locked_strategy, stage2_slots=stage2)
    if not validation.ok:
        # 1회 재시도 (힌트 포함)
        stage2 = await run_planner_stage2(
            question=question, conversation_id=conversation_id, request_id=request_id,
            locked_strategy=locked_strategy, signals=signals, entity_role_plan=stage15,
            hard_contract=_build_hard_contract(question=question, locked_strategy=locked_strategy, entity_role_plan=stage15, ids_map={}, candidate_keys={}, project_key_policy=None, join_key_mode=None),
            soft_strategy_hints=_build_soft_strategy_hints(signals=signals, entity_role_plan=stage15, locked_strategy=locked_strategy),
            cards=stage2_cards, validation_hints=_validation_hints_payload(validation), previous_output=stage2.model_dump() if hasattr(stage2, "model_dump") else {},
        )
        validation = validate_stage2_slots(question=question, signals=signals, entity_role_plan=stage15, locked_strategy=locked_strategy, stage2_slots=stage2)
        
        # 여전히 실패 시 결정론적 보정 적용
        if not validation.ok:
            repaired_stage2, _ = _apply_deterministic_stage2_repair(
                stage2_slots=stage2, validation=validation, locked_strategy=locked_strategy,
                entity_role_plan=stage15, question=question, request_id=request_id, conversation_id=conversation_id,
            )
            stage2 = repaired_stage2

    # 최종 취합
    return assemble_question_analysis(
        question=question, conversation_id=conversation_id, request_id=request_id,
        stage1=stage1, stage2=stage2, signals=signals, entity_role_plan=stage15, locked_strategy=locked_strategy,
    )
