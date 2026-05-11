"""
검색된 근거(Evidence)를 바탕으로 최종 사용자 답변을 생성하고 품질을 검증하는 모듈입니다.

[설계 의도: ADR-0016 및 2층 계약(2-Layer Contract) 아키텍처]
이 모듈은 시스템의 최종 관문인 'Answer Publication Guard' 역할을 수행합니다.
단순히 LLM이 생성한 텍스트를 전달하는 것이 아니라, 검색된 실제 데이터(L2 계층의 진실)와 
사용자의 의도(L1 계층의 진실)가 최종 답변에서 정합성을 이루는지 엄격하게 검증합니다.

주요 특징:
1. Groundedness (근거성): 답변의 모든 내용이 검색된 Evidence 내에 존재하는지 확인하여 환각(Hallucination)을 방지합니다.
2. State Consistency (상태 정합성): 특히 '목록형' 응답에서, 화면에 표시된 항목의 순서나 개수가 답변 내용과 일치하는지 체크합니다.
3. Subject Continuity (주제 연속성): 답변 생성 후, 다음 대화에서도 현재 대화의 주제(연구자, 기관 등)를 유지할 수 있도록 SubjectQueryContext를 관리합니다.
"""

from __future__ import annotations

import os
import re

from pathlib import Path
from typing import Any, Dict, Optional

from apps.platform.langchain_compat import AIMessage, HumanMessage, SystemMessage

from apps.api.rag_mapper.schema_types import DataTag
from apps.api.contracts.answer_groundedness import (
    AnswerGroundednessVerdict,
    build_groundedness_snapshot_from_canonical_evidence,
    evaluate_answer_groundedness,
)
from apps.api.contracts.answer_state_consistency import (
    AnswerStateConsistencyPolicy,
    AnswerStateConsistencyVerdict,
    build_state_snapshot_from_result_set,
    evaluate_answer_state_consistency,
)
from apps.api.contracts.visible_answer_manifest_publication import build_visible_answer_manifest_publication
from apps.evidence.canonical_context import (
    render_canonical_evidence_debug_text,
    render_canonical_evidence_text,
)
from apps.conversation.session_memory import SubjectQueryContext, build_current_context
from apps.conversation.view_state import get_active_result_snapshot, set_visible_answer_manifest
from apps.api.streaming.contracts import AnswerArtifact
from apps.evidence.canonical_evidence import build_canonical_evidence
from apps.api.runtime_helpers import log_event, log_section, logger, measure_latency, select_max_tokens_hint
from apps.chat.answer_merge import select_final_answer
from apps.chat.execution_trace_summary import summarize_execution_trace
from apps.chat.llm_runtime import build_llm, load_system_prompt, resolve_system_prompt_path
from apps.chat.llm_streaming import run_llm_streaming
from apps.evidence.context_renderer import split_sentences
from apps.planner.prompt_asset_paths import planner_card_path, planner_prompt_path
from apps.platform.settings import MAX_DOC_SENTENCES, MAX_DOC_TOKENS



_SHORT_ANSWER_MAX_TOKENS_HINT = int(os.getenv("SHORT_ANSWER_MAX_TOKENS_HINT", "4096"))
_FOLLOW_UP_MAX_TOKENS_HINT = int(os.getenv("FOLLOW_UP_MAX_TOKENS_HINT", "4096"))
_SOLAR_DEADLINE_MS = int(os.getenv("SOLAR_DEADLINE_MS", "9000"))
_SOLAR_TTFT_DEADLINE_MS = int(os.getenv("SOLAR_TTFT_DEADLINE_MS", str(_SOLAR_DEADLINE_MS)))
_SOLAR_GEN_DEADLINE_MS = int(os.getenv("SOLAR_GEN_DEADLINE_MS", "15000"))
_SOLAR_STREAM_MAX_CHARS = int(os.getenv("SOLAR_STREAM_MAX_CHARS", "10000"))
_DUAL_MODEL_MERGE_POLICY = os.getenv("DUAL_MODEL_MERGE_POLICY", "solar_first").strip().lower()
# Internal selector placeholder. When fallback wins, merge_answers() replaces this
# with a user-visible degraded message that matches the failure reason.
_DUAL_MODEL_FALLBACK_MESSAGE = "The generated answer was empty. Please try again."
_GENERIC_DEGRADED_FALLBACK_MESSAGE = (
    "현재 근거 기반 답변을 안정적으로 생성하지 못했습니다. 질문 범위를 조금 더 좁혀 다시 질문해 주세요."
)
_STATE_CONSISTENCY_DEGRADED_FALLBACK_MESSAGE = (
    "검색된 항목의 대상·순서·개수를 답변과 안정적으로 맞추지 못해 답변을 보류합니다. "
    "대상이나 범위를 더 구체적으로 지정해 다시 질문해 주세요."
)
_GROUNDEDNESS_DEGRADED_FALLBACK_MESSAGE = (
    "검색 근거로 확인되지 않은 정보가 포함돼 답변을 보류합니다. "
    "대상 과제명, 기관명, 연도 등을 더 구체적으로 지정해 다시 질문해 주세요."
)
_SOLAR_MIN_ANSWER_CHARS = int(os.getenv("SOLAR_MIN_ANSWER_CHARS", "60"))
_DEFAULT_SYSTEM_PROMPT_PATH = Path(os.getenv("DEFAULT_SYSTEM_PROMPT_PATH", planner_prompt_path("ntis_chatbot.md")))
_GEMMA_SYSTEM_PROMPT_PATH_RAW = os.getenv("GEMMA_SYSTEM_PROMPT_PATH", "").strip()
_GEMMA_SYSTEM_PROMPT_PATH = Path(_GEMMA_SYSTEM_PROMPT_PATH_RAW) if _GEMMA_SYSTEM_PROMPT_PATH_RAW else None
_SOLAR_SYSTEM_PROMPT_PATH_RAW = os.getenv("SOLAR_SYSTEM_PROMPT_PATH", "").strip()
_SOLAR_SYSTEM_PROMPT_PATH = Path(_SOLAR_SYSTEM_PROMPT_PATH_RAW) if _SOLAR_SYSTEM_PROMPT_PATH_RAW else None
_SOLAR_MAX_DOC_SENTENCES = int(os.getenv("SOLAR_MAX_DOC_SENTENCES", str(MAX_DOC_SENTENCES)))
_SOLAR_MAX_DOC_TOKENS = int(os.getenv("SOLAR_MAX_DOC_TOKENS", str(MAX_DOC_TOKENS)))
_LIST_LIKE_OUTPUT_TYPES = {"list", "relation", "comparison", "series", "stats"}
_DETERMINISTIC_VISIBLE_LIST_PREAMBLE = "현재 화면에 보이는 항목을 기준으로 정리하면 다음과 같습니다."
_EXPLICIT_COUNT_REQUEST_PATTERN = re.compile(r"(\d+)\s*(건|개|명)")




def _coerce_float(value: Any) -> Optional[float]:

    try:

        return float(value)

    except Exception:

        return None




def _coerce_int(value: Any) -> Optional[int]:

    try:

        return int(value)

    except Exception:

        return None


def _coerce_positive_int(value: Any) -> Optional[int]:

    number = _coerce_int(value)
    if number is None or number < 1:
        return None
    return number




def _resolve_llm_request_overrides(state: Any) -> dict[str, Any]:
    overrides = getattr(state, "request_overrides", None) or {}
    if not isinstance(overrides, dict):
        return {}

    llm_overrides: dict[str, Any] = {}

    temperature = _coerce_float(overrides.get("temperature"))

    top_p = _coerce_float(overrides.get("top_p"))

    max_tokens = _coerce_int(overrides.get("max_tokens"))

    top_k = _coerce_int(overrides.get("top_k"))

    if temperature is not None:

        llm_overrides["temperature"] = temperature

    if top_p is not None:

        llm_overrides["top_p"] = top_p

    if max_tokens is not None:

        llm_overrides["max_tokens_hint"] = max_tokens

    if top_k is not None:
        llm_overrides["top_k"] = top_k
    return llm_overrides


def _reference_tag_from_source_type(source_type: Any) -> Optional[str]:
    normalized = str(source_type or "").strip().lower()
    mapping = {
        "project": DataTag.PROJECT.value,
        "paper": DataTag.PAPER.value,
        "patent": DataTag.PATENT.value,
        "report": DataTag.REPORT.value,
        "software": DataTag.SOFTWARE.value,
        "standard": DataTag.STANDARD.value,
        "compound": DataTag.COMPOUND.value,
        "equipment": DataTag.EQUIPMENT.value,
        "organism_info": DataTag.ORGANISM_INFO.value,
        "organism_resource": DataTag.ORGANISM_RESOURCE.value,
        "manual": DataTag.MANUAL.value,
        "tech_summary": DataTag.TECH_SUMMARY.value,
        "variety": DataTag.VARIETY.value,
        "qna": DataTag.QNA.value,
    }
    return mapping.get(normalized)


def _normalize_reference_tag(tag_value: Any, *, source_type: Any = None) -> Optional[str]:
    normalized = str(tag_value or "").strip()
    if normalized:
        try:
            return DataTag(normalized).value
        except ValueError:
            return None
    return _reference_tag_from_source_type(source_type)


def _normalize_reference_id(reference: dict[str, Any]) -> Optional[str]:
    tag = str(reference.get("tag") or "").strip()
    source_type = str(reference.get("source_type") or "").strip().lower()
    project_like = (
        tag == DataTag.PROJECT.value
        or source_type == "project"
        or (reference.get("pjt_id") and not any(reference.get(key) for key in ("rst_id", "perf_id", "paper_id")))
    )
    if project_like:
        value = reference.get("pjt_id") or reference.get("id")
    else:
        value = reference.get("rst_id") or reference.get("perf_id") or reference.get("paper_id") or reference.get("id")
    text = str(value or "").strip()
    return text or None


def _normalize_reference_payload(reference: dict[str, Any]) -> Optional[dict[str, Any]]:
    tag = _normalize_reference_tag(reference.get("tag"), source_type=reference.get("source_type"))
    title = str(reference.get("title") or reference.get("title_text") or "").strip() or None
    normalized = {
        "tag": tag,
        "id": _normalize_reference_id(reference),
        "title": title,
    }
    if tag is None or not (normalized["id"] or normalized["title"]):
        return None
    return normalized


def _reference_seed_from_canonical_item(
    canonical: dict[str, Any],
    *,
    fallback_doc: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    ids = canonical.get("ids") if isinstance(canonical.get("ids"), dict) else {}
    facts = canonical.get("facts") if isinstance(canonical.get("facts"), dict) else {}
    evidence = canonical.get("evidence") if isinstance(canonical.get("evidence"), dict) else {}
    fallback = fallback_doc if isinstance(fallback_doc, dict) else {}
    return {
        "tag": str(fallback.get("tag") or "").strip() or _reference_tag_from_source_type(canonical.get("source_type")),
        "source_type": canonical.get("source_type"),
        "pjt_id": ids.get("pjt_id") or fallback.get("pjt_id"),
        "pjt_no": ids.get("pjt_no") or fallback.get("pjt_no"),
        "rst_id": ids.get("rst_id") or ids.get("perf_id") or ids.get("paper_id") or fallback.get("rst_id") or fallback.get("perf_id") or fallback.get("paper_id"),
        "id": fallback.get("id"),
        "title": facts.get("title") or evidence.get("title_text") or canonical.get("identity") or fallback.get("title"),
        "title_text": evidence.get("title_text") or facts.get("title") or fallback.get("title_text"),
    }


def _projection_payload(source: Any) -> dict[str, Any]:
    if source is None:
        return {}
    if isinstance(source, dict):
        return dict(source)
    if hasattr(source, "model_dump"):
        try:
            payload = source.model_dump()
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}
    if hasattr(source, "__dict__"):
        try:
            return dict(vars(source))
        except Exception:
            return {}
    return {}


def _projection_bundle_payload(state: Any) -> dict[str, Any]:
    return _projection_payload(getattr(state, "evidence_projection_bundle", None))


def _projection_sequence(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return list(value) if isinstance(value, list) else []


def _snapshot_matches_projection(snapshot: Any, projection_payload: dict[str, Any], state: Any) -> bool:
    if snapshot is None or not projection_payload:
        return snapshot is not None
    expected_projection_id = str(projection_payload.get("projection_id") or "").strip()
    expected_request_id = str(projection_payload.get("request_id") or getattr(state, "request_id", "") or "").strip()
    expected_turn_id = str(projection_payload.get("turn_id") or getattr(state, "turn_id", "") or "").strip()
    observed_projection_id = str(getattr(snapshot, "projection_id", None) or "").strip()
    observed_request_id = str(getattr(snapshot, "request_id", None) or getattr(state, "request_id", "") or "").strip()
    observed_turn_id = str(getattr(snapshot, "turn_id", None) or "").strip()
    if expected_projection_id and observed_projection_id != expected_projection_id:
        return False
    if expected_request_id and observed_request_id != expected_request_id:
        return False
    if expected_turn_id and observed_turn_id != expected_turn_id:
        return False
    return True


def _projection_lineage_status(projection_payload: dict[str, Any], matched: bool) -> str:
    if not projection_payload:
        return "not_applicable"
    return "matched" if matched else "mismatch"


def _collect_state_references(state: Any) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, Any, Any]] = set()

    def _append(reference: Optional[dict[str, Any]]) -> None:
        if not isinstance(reference, dict):
            return
        normalized = _normalize_reference_payload(reference)
        if normalized is None:
            return
        dedupe_key = (normalized.get("tag"), normalized.get("id"), normalized.get("title"))
        if dedupe_key in seen_keys:
            return
        seen_keys.add(dedupe_key)
        references.append(normalized)

    projection_payload = _projection_bundle_payload(state)
    projection_canonical = _projection_sequence(projection_payload, "canonical_evidence")
    projection_display = _projection_sequence(projection_payload, "display_documents")
    if projection_payload:
        for index, canonical in enumerate(projection_canonical):
            if not isinstance(canonical, dict):
                continue
            display = projection_display[index] if index < len(projection_display) and isinstance(projection_display[index], dict) else {}
            _append(_reference_seed_from_canonical_item(canonical, fallback_doc=display))
        return references

    retrieval_bundle = getattr(state, "retrieval_bundle", None)
    if isinstance(retrieval_bundle, dict):
        items = retrieval_bundle.get("items")
    else:
        items = getattr(retrieval_bundle, "items", None)
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                display = item.get("display")
                canonical = item.get("canonical")
            else:
                display = getattr(item, "display", None)
                canonical = getattr(item, "canonical", None)
            display_doc = dict(display) if isinstance(display, dict) else {}
            canonical_doc = dict(canonical) if isinstance(canonical, dict) else {}
            _append(_reference_seed_from_canonical_item(canonical_doc, fallback_doc=display_doc))

    if references:
        return references

    canonical_evidence = getattr(state, "canonical_evidence", None) or []
    for item in canonical_evidence:
        if not isinstance(item, dict):
            continue
        _append(_reference_seed_from_canonical_item(item))
    return references


def _with_references(artifact: AnswerArtifact, references: list[dict[str, Any]]) -> AnswerArtifact:
    return AnswerArtifact(
        text=artifact.text,
        answer_kind=artifact.answer_kind,
        stream_metrics=dict(artifact.stream_metrics or {}),
        user_visible_final_required=bool(artifact.user_visible_final_required),
        references=list(references or []),
        visible_answer_manifest=artifact.visible_answer_manifest,
        visible_answer_manifest_publication=artifact.visible_answer_manifest_publication,
        clarification=artifact.clarification,
        error=artifact.error,
        meta=dict(artifact.meta or {}),
    )


def _state_consistency_diag(verdict: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(verdict or {})
    return {
        "status": str(payload.get("status") or "").strip().lower() or "not_applicable",
        "reason_codes": list(payload.get("reason_codes") or []),
        "snapshot_visible_count": int(payload.get("snapshot_visible_count") or 0),
        "parsed_item_count": int(payload.get("parsed_item_count") or 0),
        "declared_count": payload.get("declared_count"),
        "mismatch_reason": payload.get("mismatch_reason"),
        "parsed_titles_preview": list(payload.get("parsed_titles_preview") or []),
        "snapshot_titles_preview": list(payload.get("snapshot_titles_preview") or []),
        "policy_name": str(payload.get("policy_name") or "").strip().lower() or "exact_count",
        "subset_accepted": bool(payload.get("subset_accepted")),
        "manifest_publish_allowed": bool(payload.get("manifest_publish_allowed")),
        "accepted_item_count": int(payload.get("accepted_item_count") or 0),
        "required_visible_count": int(payload.get("required_visible_count") or 0),
    }


def _state_snapshot_diag(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(snapshot or {})
    ordered_items = payload.get("ordered_items") if isinstance(payload.get("ordered_items"), list) else []
    titles_preview: list[str] = []
    entity_kind_counts: dict[str, int] = {}
    pjt_no_bucket_sizes: dict[str, int] = {}
    items_without_pjt_no = 0
    for item in ordered_items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title_text") or "").strip()
        if title and len(titles_preview) < 3:
            titles_preview.append(title)
        kind = str(item.get("entity_kind") or "").strip().lower() or "project"
        entity_kind_counts[kind] = entity_kind_counts.get(kind, 0) + 1
        ids_map = item.get("ids_map") if isinstance(item.get("ids_map"), dict) else {}
        pjt_no_values = ids_map.get("pjt_no") if isinstance(ids_map.get("pjt_no"), list) else []
        pjt_no_key = str(pjt_no_values[0]).strip() if pjt_no_values and str(pjt_no_values[0]).strip() else ""
        if pjt_no_key:
            pjt_no_bucket_sizes[pjt_no_key] = pjt_no_bucket_sizes.get(pjt_no_key, 0) + 1
        else:
            items_without_pjt_no += 1
    declared_context_kind = str(payload.get("context_kind") or "").strip().lower() or "project"
    effective_context_kind = declared_context_kind
    if entity_kind_counts:
        dominant_kind, dominant_count = max(entity_kind_counts.items(), key=lambda pair: pair[1])
        total_items = sum(entity_kind_counts.values())
        if total_items > 0 and dominant_count * 2 > total_items:
            effective_context_kind = dominant_kind
    pjt_no_bucket_count = len(pjt_no_bucket_sizes)
    pjt_no_grouped_rows = sum(size for size in pjt_no_bucket_sizes.values() if size > 1)
    return {
        "available": bool(payload.get("available")),
        "snapshot_source": str(payload.get("snapshot_source") or "none"),
        "context_kind": declared_context_kind,
        "effective_context_kind": effective_context_kind,
        "context_kind_mismatch": bool(
            effective_context_kind != declared_context_kind and effective_context_kind
        ),
        "visible_count": int(payload.get("visible_count") or 0),
        "titles_preview": titles_preview,
        "entity_kind_counts": entity_kind_counts,
        "pjt_no_bucket_count": pjt_no_bucket_count,
        "pjt_no_grouped_rows": pjt_no_grouped_rows,
        "items_without_pjt_no": items_without_pjt_no,
    }


def _resolve_output_family(state: Any) -> str:
    render_profile = getattr(state, "render_profile", None) or {}
    question_analysis = getattr(state, "question_analysis", None)
    return str(
        render_profile.get("name")
        or _pick_attr(question_analysis, key="output_type", default=None)
        or "summary"
    ).strip().lower() or "summary"


def _resolve_turn_contract(state: Any) -> dict[str, Any]:
    intent_payload = getattr(state, "intent_payload", None)
    strategy_meta = dict(getattr(intent_payload, "strategy_meta", None) or {})
    turn_contract = strategy_meta.get("turn_contract")
    return dict(turn_contract or {}) if isinstance(turn_contract, dict) else {}


def _snapshot_to_payload(snapshot: Any) -> dict[str, Any]:
    if snapshot is None:
        return {}
    if hasattr(snapshot, "model_dump"):
        try:
            payload = snapshot.model_dump()
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
    if isinstance(snapshot, dict):
        return dict(snapshot)
    return dict(getattr(snapshot, "__dict__", {}) or {})


def _question_has_explicit_count(question: Any) -> bool:
    return bool(_EXPLICIT_COUNT_REQUEST_PATTERN.search(str(question or "")))


def _resolve_state_consistency_policy(state: Any) -> dict[str, Any]:
    output_family = _resolve_output_family(state)
    if output_family not in _LIST_LIKE_OUTPUT_TYPES:
        return AnswerStateConsistencyPolicy(
            policy_name="bypass",
            enforce_exact_count=False,
        ).model_dump()

    turn_contract = _resolve_turn_contract(state)
    count_contract = str(turn_contract.get("count_contract") or "").strip().lower()
    explicit_count_requested = bool(turn_contract.get("explicit_count_requested"))
    requested_count = _coerce_positive_int(turn_contract.get("requested_count"))
    question_requests_count = _question_has_explicit_count(getattr(state, "question", None))

    if count_contract == "partial_ok":
        return AnswerStateConsistencyPolicy(
            policy_name="prefix_subset",
            enforce_exact_count=False,
            allow_prefix_subset=True,
            min_required_items=1,
            allow_manifest_publish_on_subset=False,
        ).model_dump()

    if (
        count_contract == "exact"
        or explicit_count_requested
        or requested_count is not None
        or output_family in {"relation", "comparison", "series"}
        or question_requests_count
    ):
        return AnswerStateConsistencyPolicy(
            policy_name="exact_count",
            enforce_exact_count=True,
            allow_prefix_subset=False,
            min_required_items=1,
            allow_manifest_publish_on_subset=False,
        ).model_dump()

    if output_family == "list":
        return AnswerStateConsistencyPolicy(
            policy_name="prefix_subset",
            enforce_exact_count=False,
            allow_prefix_subset=True,
            min_required_items=1,
            allow_manifest_publish_on_subset=False,
        ).model_dump()

    return AnswerStateConsistencyPolicy(
        policy_name="bypass",
        enforce_exact_count=False,
    ).model_dump()


def _snapshot_items(snapshot: Any) -> list[dict[str, Any]]:
    if snapshot is None:
        return []
    items = getattr(snapshot, "items", None)
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw_item in items:
        if isinstance(raw_item, dict):
            normalized.append(dict(raw_item or {}))
        elif hasattr(raw_item, "model_dump"):
            try:
                payload = raw_item.model_dump()
                if isinstance(payload, dict):
                    normalized.append(dict(payload))
                    continue
            except Exception:
                pass
            normalized.append(dict(getattr(raw_item, "__dict__", {}) or {}))
        else:
            normalized.append(dict(getattr(raw_item, "__dict__", {}) or {}))
    return normalized


def _has_text_value(item: dict[str, Any], *keys: str) -> bool:
    return any(str(item.get(key) or "").strip() for key in keys)


def _is_activity_like_visible_snapshot(snapshot: Any) -> bool:
    items = _snapshot_items(snapshot)
    if not items:
        return False
    activity_rows = 0
    for item in items:
        has_result_identity = _has_text_value(item, "rst_id", "doi", "issn")
        has_actor_identity = _has_text_value(item, "person_no", "org_id", "org_code", "biz_no")
        has_project_identity = _has_text_value(item, "pjt_id", "pjt_no")
        if has_result_identity and has_actor_identity and has_project_identity:
            activity_rows += 1
    return activity_rows > 0 and activity_rows * 2 >= len(items)


def _snapshot_visible_count_matches_items(snapshot: Any) -> bool:
    items = _snapshot_items(snapshot)
    if not items:
        return False
    try:
        visible_count = int(getattr(snapshot, "visible_count", 0) or 0)
    except Exception:
        return False
    return visible_count > 0 and visible_count == len(items)


def _is_grouped_project_visible_snapshot(snapshot: Any) -> bool:
    if not _snapshot_visible_count_matches_items(snapshot):
        return False
    items = _snapshot_items(snapshot)
    project_rows = 0
    pjt_no_bucket_sizes: dict[str, int] = {}
    for item in items:
        kind = str(item.get("entity_kind") or "").strip().lower() or "project"
        has_project_identity = _has_text_value(item, "pjt_id", "pjt_no")
        if kind != "project" or not has_project_identity:
            continue
        project_rows += 1
        pjt_no = str(item.get("pjt_no") or "").strip()
        if pjt_no:
            pjt_no_bucket_sizes[pjt_no] = pjt_no_bucket_sizes.get(pjt_no, 0) + 1
    return project_rows == len(items) and any(size > 1 for size in pjt_no_bucket_sizes.values())


def _can_render_deterministic_visible_list(snapshot: Any, *, state_inconsistent: bool) -> bool:
    if not state_inconsistent:
        return False
    context_kind = str(getattr(snapshot, "context_kind", None) or "").strip().lower()
    if context_kind in {"people", "org"}:
        return True
    if context_kind == "project" and _is_grouped_project_visible_snapshot(snapshot):
        return True
    return _is_activity_like_visible_snapshot(snapshot)


def _render_deterministic_visible_list(snapshot: Any, *, state_inconsistent: bool = False) -> Optional[str]:
    if snapshot is None:
        return None
    if not _can_render_deterministic_visible_list(snapshot, state_inconsistent=state_inconsistent):
        return None
    items = _snapshot_items(snapshot)
    if not items:
        return None
    lines = [_DETERMINISTIC_VISIBLE_LIST_PREAMBLE]
    for index, raw_item in enumerate(items, start=1):
        item = dict(raw_item or {})
        title = str(item.get("title_text") or "").strip()
        if not title:
            continue
        suffixes: list[str] = []
        year = str(item.get("year") or "").strip()
        if year:
            suffixes.append(f"({year})")
        lead_org = str(item.get("lead_org") or "").strip()
        if lead_org:
            suffixes.append(f": {lead_org}")
        rst_id = str(item.get("rst_id") or "").strip()
        if rst_id:
            suffixes.append(f"[rst_id: {rst_id}]")
        lines.append(f"{index}. {title}{(' ' + ' '.join(suffixes)) if suffixes else ''}")
    return "\n".join(lines) if len(lines) > 1 else None


def _build_deterministic_visible_list_candidate(
    *,
    state: Any,
    active_result_snapshot: Any,
    groundedness_snapshot: dict[str, Any],
    state_consistency_snapshot: dict[str, Any],
    state_consistency_policy: dict[str, Any],
    state_inconsistent: bool = False,
) -> Optional[dict[str, Any]]:
    if _resolve_output_family(state) != "list":
        return None

    text = _render_deterministic_visible_list(
        active_result_snapshot,
        state_inconsistent=state_inconsistent,
    )
    if not text:
        return None

    groundedness = evaluate_answer_groundedness(
        answer_text=text,
        answer_kind="deterministic_render",
        evidence_snapshot=groundedness_snapshot,
    ).model_dump()
    state_consistency = evaluate_answer_state_consistency(
        answer_text=text,
        answer_kind="deterministic_render",
        state_snapshot=state_consistency_snapshot,
        state_policy=state_consistency_policy,
    ).model_dump()
    if str(state_consistency.get("status") or "").strip().lower() != "supported":
        return None
    if str(groundedness.get("status") or "").strip().lower() == "unsupported":
        return None
    return {
        "text": text,
        "groundedness": groundedness,
        "state_consistency": state_consistency,
        "meta": {
            "answer_source": "deterministic_snapshot",
            "model_key": "deterministic_snapshot",
            "answer_kind": "deterministic_render",
            "deterministic_render_mode": "visible_snapshot",
        },
    }


def _build_verified_projection_summary(candidate: dict[str, Any] | None) -> Optional[dict[str, Any]]:
    if not isinstance(candidate, dict):
        return None
    text = str(candidate.get("text") or "").strip()
    if not text:
        return None
    candidate_meta = dict(candidate.get("meta") or {})
    return {
        "text": text,
        "answer_source": str(candidate_meta.get("answer_source") or "deterministic_snapshot"),
        "model_key": str(candidate_meta.get("model_key") or "deterministic_snapshot"),
        "answer_kind": str(candidate_meta.get("answer_kind") or "deterministic_render"),
        "render_mode": str(candidate_meta.get("deterministic_render_mode") or "visible_snapshot"),
        "groundedness": dict(candidate.get("groundedness") or {}),
        "state_consistency": dict(candidate.get("state_consistency") or {}),
    }


def _promote_llm_for_augmented_state_inconsistency(
    *,
    selection: dict[str, Any],
    answer_solar: str,
    answer_gemma: str,
    solar_failed: bool,
    gemma_failed: bool,
) -> bool:
    if str(selection.get("selection_reason") or "").strip().lower() != "both_models_state_inconsistent":
        return False
    if answer_solar and not solar_failed:
        selection["selected_model"] = "solar"
        selection["selected_answer"] = answer_solar
        selection["selection_reason"] = "solar_augmented_state_inconsistent"
        return True
    if answer_gemma and not gemma_failed:
        selection["selected_model"] = "gemma"
        selection["selected_answer"] = answer_gemma
        selection["selection_reason"] = "gemma_augmented_state_inconsistent"
        return True
    return False


def _build_user_visible_fallback_message(selection: dict[str, Any]) -> str:
    selection_reason = str(selection.get("selection_reason") or "").strip().lower()
    fail_reasons = {
        str(reason or "").strip().lower()
        for reason in [
            *(selection.get("solar_fail_reasons") or []),
            *(selection.get("gemma_fail_reasons") or []),
        ]
        if str(reason or "").strip()
    }
    if selection_reason == "both_models_state_inconsistent":
        return _STATE_CONSISTENCY_DEGRADED_FALLBACK_MESSAGE
    if "unsupported_groundedness" in fail_reasons:
        return _GROUNDEDNESS_DEGRADED_FALLBACK_MESSAGE
    return _GENERIC_DEGRADED_FALLBACK_MESSAGE


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """여러 객체나 dict에서 같은 속성의 첫 non-None 값을 찾는다."""
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


def _resolve_groundedness_visible_count(state: Any) -> Optional[int]:
    render_profile = getattr(state, "render_profile", None) or {}
    question_analysis = getattr(state, "question_analysis", None)
    output_name = str(
        _pick_attr(render_profile, question_analysis, key="name", default=None)
        or _pick_attr(question_analysis, key="output_type", default=None)
        or ""
    ).strip().lower()
    if output_name not in {"list", "relation", "comparison", "series", "stats"}:
        return None
    view_state = getattr(state, "view_state", None)
    active_result_snapshot = get_active_result_snapshot(view_state)
    try:
        value = getattr(active_result_snapshot, "visible_count", None)
        return int(value) if value is not None else None
    except Exception:
        return None


def build_answer_context(
    *,
    answer_context_text: Optional[str] = None,
    debug_answer_context_text: Optional[str] = None,
    docs_for_ctx: list[Any],
    canonical_evidence: Optional[list[dict[str, Any]]] = None,
    render_profile: Optional[dict[str, Any]] = None,
    normalized_intent: Any = None,
    strategy: Any = None,
    qa: Any,
    model_name: str,
) -> dict[str, Any]:

    """문서와 canonical evidence를 답변 생성용 context text로 정리한다.

    가능하면 canonical_evidence를 그대로 렌더링하고, 없을 때만 docs를 canonical 형태로 파생해
    raw payload 직접 참조를 줄인다.
    """

    is_solar = model_name == "solar_vllm_0"

    profile = dict(render_profile or {})

    if not profile:
        profile = {
            "name": str(_pick_attr(normalized_intent, qa, key="output_type", default="summary")).strip().lower() or "summary",
            "context_kind": str(
                _pick_attr(normalized_intent, strategy, qa, key="base_route")
                or _pick_attr(qa, key="head")
                or "project"
            ).strip().lower() or "project",
        }

    pipeline_context = str(answer_context_text or "").strip()
    pipeline_debug_context = str(debug_answer_context_text or "").strip()
    if pipeline_context:
        context_text = pipeline_context
        debug_context_text = pipeline_debug_context or context_text
        context_sentences = len(split_sentences(context_text)) if context_text and context_text != "NONE" else 0
        context_tokens_est = len(context_text.split()) if context_text and context_text != "NONE" else 0
        return {
            "context_text": context_text,
            "debug_context_text": debug_context_text,
            "rendered_context_used": context_text != "NONE",
            "context_sentences": context_sentences,
            "context_tokens_est": context_tokens_est,
            "is_solar": is_solar,
            "context_source": "pipeline_context",
        }

    effective_canonical = list(canonical_evidence or [])
    context_source = "canonical_evidence"

    if not effective_canonical and docs_for_ctx:

        base_route = str(profile.get("context_kind") or _pick_attr(normalized_intent, strategy, qa, key="base_route") or _pick_attr(qa, key="head") or "project").strip().lower() or "project"

        output_type = str(profile.get("name") or _pick_attr(normalized_intent, qa, key="output_type", default="summary")).strip().lower() or "summary"

        derived: list[dict[str, Any]] = []

        for rank, item in enumerate(docs_for_ctx, start=1):

            if not isinstance(item, dict):

                continue

            derived.append(
                build_canonical_evidence(
                    item,
                    rank=rank,
                    base_route=base_route,
                    output_type=output_type,
                ).to_dict()
            )

        effective_canonical = derived
        context_source = "derived_canonical_evidence"

    context_text = render_canonical_evidence_text(
        effective_canonical,
        profile,
        max_chars=0,
    )
    debug_context_text = render_canonical_evidence_debug_text(
        effective_canonical,
        profile,
        max_chars=0,
    )
    rendered_context_used = bool(effective_canonical) and context_text != "NONE"

    context_sentences = len(split_sentences(context_text)) if context_text and context_text != "NONE" else 0
    context_tokens_est = len(context_text.split()) if context_text and context_text != "NONE" else 0
    return {
        "context_text": context_text,
        "debug_context_text": debug_context_text,
        "rendered_context_used": rendered_context_used,
        "context_sentences": context_sentences,
        "context_tokens_est": context_tokens_est,
        "is_solar": is_solar,
        "context_source": context_source,
    }


async def generate_answer(
    state: Any,
    *,
    model_name: str,
    final_field: str,
) -> Dict[str, Any]:

    """모델별 system prompt, reference context, user question을 묶어 최종 답변을 생성합니다.

    [상태 관리 로직 설명]
    1. Short-circuit (단축 경로): 이미 유효한 답변 아티팩트가 있거나 결과 없음 메시지가 설정된 경우, 
       LLM을 호출하지 않고 즉시 반환하여 비용과 지연 시간을 줄입니다.
    2. Context Building: 검색된 raw payload를 'Canonical Evidence' 형태로 변환하여 LLM이 이해하기 
       쉬운 표준화된 텍스트로 제공합니다. 이는 모델 간(Solar, Gemma)의 일관된 성능을 보장합니다.
    3. Agent Observation Note: 대화 에이전트가 탐색 과정에서 남긴 특이 사항([결과 주석])이 있다면 
       이를 프롬프트에 주입하여, LLM이 단순 검색 결과 이상의 맥락을 답변에 반영하도록 합니다.
    4. Streaming Metrics: 스트리밍 응답의 품질(TTFT, 지연 등)을 실시간으로 측정하여 성능 저하 시 
       로그를 남기고 후속 병합 단계에서 참고할 수 있게 합니다.
    """
    execution_trace_summary = summarize_execution_trace(getattr(state, "execution_trace", None) or [])

    def _build_short_circuit_artifact(*, content: str, answer_kind: str, answer_source: str) -> AnswerArtifact:
        meta = {"answer_source": answer_source, "model_key": final_field.replace("answer_", "")}
        if execution_trace_summary:
            meta.update(execution_trace_summary)
        return AnswerArtifact(
            text=content,
            answer_kind=answer_kind,
            stream_metrics={
                "content_chars": len(content),
                "stream_content_emitted_chunks": 1,
                "emitted_chars": len(content),
                "ttft_any_ms": 0.0,
                "ttft_content_ms": 0.0,
            },
            user_visible_final_required=True,
            meta=meta,
        )

    answer_artifact = getattr(state, "answer_artifact", None)

    if isinstance(answer_artifact, AnswerArtifact) and answer_artifact.text:
        rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
        return {
            final_field: answer_artifact.text,
            f"{final_field}_meta": answer_artifact.to_meta_dict(),
            f"answer_artifact_{final_field.replace('answer_', '')}": answer_artifact,
            rendered_context_key: False,
            "stream_meta": {final_field: answer_artifact.to_meta_dict()},
        }

    no_result_message = str(getattr(state, "no_result_message", "") or "").strip()
    if no_result_message:
        log_event(
            "LLM.GENERATE",
            request_id=getattr(state, "request_id", None),
            conversation_id=getattr(state, "conversation_id", None),
            stage="generate_answer_no_result",
            model=model_name,
            ks_level="short_circuit",
            ctx_chars=0,
            ctx_sentences=0,
            ctx_tokens_est=0,
            context_source="no_result_message",
            emitted_chars=len(no_result_message),
        )
        rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
        no_result_artifact = _build_short_circuit_artifact(
            content=no_result_message,
            answer_source="no_result_message",
            answer_kind="no_result",
        )
        return {
            final_field: no_result_message,
            f"{final_field}_meta": no_result_artifact.to_meta_dict(),
            f"answer_artifact_{final_field.replace('answer_', '')}": no_result_artifact,
            rendered_context_key: False,
            "stream_meta": {final_field: no_result_artifact.to_meta_dict()},
        }

    llm = build_llm(model_name=model_name)

    ks = getattr(state, "knowledge_sufficiency", None)
    qa = getattr(state, "question_analysis", None)
    intent_payload = getattr(state, "intent_payload", None)
    normalized_intent = getattr(intent_payload, "normalized_intent", None) if intent_payload else None
    strategy = getattr(state, "strategy", None)
    retrieval_bundle = getattr(state, "retrieval_bundle", None)
    projection_payload = _projection_bundle_payload(state)
    answer_context_text = str(
        getattr(state, "answer_context_text", None)
        or projection_payload.get("answer_context_text")
        or getattr(retrieval_bundle, "answer_context_text", None)
        or ""
    ).strip()
    debug_answer_context_text = str(
        getattr(state, "debug_answer_context_text", None)
        or projection_payload.get("debug_answer_context_text")
        or getattr(retrieval_bundle, "debug_answer_context_text", None)
        or ""
    ).strip()
    docs_for_ctx = getattr(state, "context", None) or getattr(state, "prev_context", None) or []
    canonical_evidence = getattr(state, "canonical_evidence", None) or _projection_sequence(projection_payload, "canonical_evidence")
    render_profile = getattr(state, "render_profile", None) or projection_payload.get("render_profile") or {}

    context_info = build_answer_context(
        answer_context_text=answer_context_text,
        debug_answer_context_text=debug_answer_context_text,
        docs_for_ctx=docs_for_ctx,
        canonical_evidence=canonical_evidence,
        render_profile=render_profile,
        normalized_intent=normalized_intent,
        strategy=strategy,
        qa=qa,
        model_name=model_name,
    )
    context_text = context_info["context_text"]
    debug_context_text = context_info.get("debug_context_text") or context_text
    rendered_context_used = context_info["rendered_context_used"]
    context_sentences = context_info["context_sentences"]
    context_tokens_est = context_info["context_tokens_est"]
    context_source = context_info.get("context_source", "canonical_evidence")

    system_prompt_path = resolve_system_prompt_path(
        model_name=model_name,
        default_path=_DEFAULT_SYSTEM_PROMPT_PATH,
        gemma_path=_GEMMA_SYSTEM_PROMPT_PATH,
        solar_path=_SOLAR_SYSTEM_PROMPT_PATH,
    )
    system_prompt = await load_system_prompt(system_prompt_path)

    messages_state = getattr(state, "messages", None) or []
    last_message = messages_state[-1] if messages_state else HumanMessage(content="")
    question_summary = str(
        _pick_attr(normalized_intent, qa, key="retrieval_query")
        or _pick_attr(qa, key="question_summary")
        or getattr(last_message, "content", "")
        or ""
    ).strip()
    # ADR-0015 C1 Option B: agent_answer_context.note가 있으면 프롬프트에 주입.
    # 값 없을 때 기존 프롬프트와 완전 동일 (빈 문자열 fallback).
    agent_answer_context = getattr(state, "agent_answer_context", None)
    agent_observation_note = ""
    if agent_answer_context is not None:
        note_value = getattr(agent_answer_context, "note", None)
        if isinstance(note_value, str) and note_value.strip():
            agent_observation_note = f"[결과 주석]\n{note_value.strip()}\n\n"
    human_prompt = (
        f"[질문 요약]\n{question_summary or '없음'}\n\n"
        f"[원본 질문]\n{getattr(last_message, 'content', '')}\n\n"
        f"{agent_observation_note}"
        f"[제공된 정보]\n{context_text}"
    )
    log_section("Reference Context", debug_context_text)

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_prompt)]

    token_hint_source = qa
    strategy_mode = _pick_attr(strategy, key="mode")
    if strategy_mode:
        token_hint_source = type("TokenHintSource", (), {"mode": str(strategy_mode).strip().upper()})()

    max_tokens_hint = select_max_tokens_hint(
        token_hint_source,
        short_answer_max_tokens_hint=_SHORT_ANSWER_MAX_TOKENS_HINT,
        follow_up_max_tokens_hint=_FOLLOW_UP_MAX_TOKENS_HINT,
    )

    llm_request_overrides = _resolve_llm_request_overrides(state)
    max_tokens_hint = int(llm_request_overrides.get("max_tokens_hint", max_tokens_hint))

    # Phase 13 진단 관측 (docs/08): contract-invalid 신호가 있는데도 스트리밍이 시작되는 경로를
    # 찾기 위한 정찰 로그. 행동은 바꾸지 않으며, 추후 publishability gate 도입 근거 데이터로 사용한다.
    try:
        _intent_payload_for_diag = getattr(state, "intent_payload", None)
        _strategy_meta_for_diag = dict(getattr(_intent_payload_for_diag, "strategy_meta", {}) or {})
        _qa_for_diag = getattr(state, "question_analysis", None)
        _pre_stream_signals = {
            "early_exit_reason": getattr(state, "early_exit_reason", None)
            or _strategy_meta_for_diag.get("early_exit_reason"),
            "count_contract_invalid_reason": _strategy_meta_for_diag.get("count_contract_invalid_reason"),
            "answer_publishability_policy": _strategy_meta_for_diag.get("answer_publishability_policy"),
            "detail_guard_blocked": bool(_strategy_meta_for_diag.get("detail_guard_blocked")),
            "no_result_message_present": bool(getattr(state, "no_result_message", None)),
            "intent_validation_status": _strategy_meta_for_diag.get("validation_status"),
        }
        if any(
            value
            for key, value in _pre_stream_signals.items()
            if key not in {"answer_publishability_policy", "intent_validation_status"}
        ) or str(_pre_stream_signals.get("answer_publishability_policy") or "").strip().lower() == "never_publish":
            log_event(
                "LLM.STREAM.PRE_GUARD_DIAG",
                request_id=getattr(state, "request_id", None),
                conversation_id=getattr(state, "conversation_id", None),
                turn_id=getattr(state, "turn_id", None),
                model=model_name,
                phase13_followup=True,
                **{k: v for k, v in _pre_stream_signals.items() if v is not None},
            )
    except Exception:
        # 진단 로그 실패는 스트리밍을 막지 않는다.
        pass

    final_artifact = await run_llm_streaming(
        llm,
        messages,
        emitter=getattr(state, "stream_emitter", None),
        model_key=final_field.replace("answer_", ""),
        max_tokens_hint=max_tokens_hint,
        request_id=getattr(state, "request_id", None),
        ttft_deadline_ms=_SOLAR_TTFT_DEADLINE_MS if model_name == "solar_vllm_0" else None,
        gen_deadline_ms=_SOLAR_GEN_DEADLINE_MS if model_name == "solar_vllm_0" else None,
        max_chars=_SOLAR_STREAM_MAX_CHARS if model_name == "solar_vllm_0" else None,
        astream_kwargs={key: value for key, value in llm_request_overrides.items() if key != "max_tokens_hint"},
    )

    if isinstance(final_artifact, tuple):
        final_answer, stream_metrics = final_artifact
        final_artifact = AnswerArtifact(
            text=str(final_answer or ""),
            answer_kind="llm_collected",
            stream_metrics=dict(stream_metrics or {}),
            user_visible_final_required=True,
            meta={"model_key": final_field.replace("answer_", "")},
        )
    if isinstance(final_artifact, AnswerArtifact) and not list(final_artifact.references or []):
        inferred_references = _collect_state_references(state)
        if inferred_references:
            final_artifact = _with_references(final_artifact, inferred_references)
    final_answer = final_artifact.text
    stream_metrics = dict(final_artifact.stream_metrics or {})

    ttft_any_ms = stream_metrics.get("ttft_any_ms")
    ttft_content_ms = stream_metrics.get("ttft_content_ms")
    reasoning_chars = int(stream_metrics.get("reasoning_chars") or 0)
    content_chars = int(stream_metrics.get("content_chars") or 0)

    logger.info(
        "[stream_metrics] request_id={} model={} ttft_any_ms={} ttft_content_ms={} reasoning_chars={} content_chars={}",
        getattr(state, "request_id", None),
        model_name,
        ttft_any_ms,
        ttft_content_ms,
        reasoning_chars,
        content_chars,
    )

    if model_name == "solar_vllm_0":
        content_delay_ms: Optional[float] = None
        if ttft_any_ms is not None and ttft_content_ms is not None:
            content_delay_ms = round(ttft_content_ms - ttft_any_ms, 1)

        if ttft_any_ms is None:
            logger.warning(
                "[solar_stream_guard] request_id={} category=stream_not_started_or_stalled ttft_any_ms={} ttft_content_ms={} ttft_deadline_exceeded={} deadline_exceeded={}",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                bool(stream_metrics.get("ttft_deadline_exceeded")),
                bool(stream_metrics.get("deadline_exceeded")),
            )
        elif ttft_content_ms is None or (content_delay_ms is not None and content_delay_ms >= 500):
            logger.warning(
                "[solar_stream_guard] request_id={} category=content_delayed ttft_any_ms={} ttft_content_ms={} content_delay_ms={} reasoning_chars={} content_chars={}",
                getattr(state, "request_id", None),
                ttft_any_ms,
                ttft_content_ms,
                content_delay_ms,
                reasoning_chars,
                content_chars,
            )
        elif stream_metrics.get("gen_deadline_exceeded"):
            logger.warning(
                "[solar_stream_guard] request_id={} category=gen_deadline_exceeded gen_deadline_ms={} truncated_chars={} emitted_chars={}",
                getattr(state, "request_id", None),
                _SOLAR_GEN_DEADLINE_MS,
                len(final_answer),
                stream_metrics.get("emitted_chars"),
            )
            if stream_metrics.get("short_output_guard_triggered"):
                logger.warning(
                    "[solar_stream_guard] request_id={} short_output_guard_triggered min_chars={} emitted_chars={}",
                    getattr(state, "request_id", None),
                    stream_metrics.get("short_output_guard_min_chars"),
                    stream_metrics.get("emitted_chars"),
                )
        elif stream_metrics.get("char_limited"):
            logger.warning(
                "[solar_stream_guard] request_id={} category=char_limited max_chars={} truncated_chars={}",
                getattr(state, "request_id", None),
                _SOLAR_STREAM_MAX_CHARS,
                len(final_answer),
            )

    log_event(
        "LLM.GENERATE",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        stage="generate_answer",
        model=model_name,
        request_temperature=llm_request_overrides.get("temperature"),
        request_top_p=llm_request_overrides.get("top_p"),
        request_max_tokens=max_tokens_hint,
        request_top_k=llm_request_overrides.get("top_k"),
        ks_level=(getattr(ks, "requires_new_knowledge", None) if ks else "unknown"),
        ctx_chars=len(context_text),
        ctx_sentences=context_sentences,
        ctx_tokens_est=context_tokens_est,
        context_source=context_source,
        emitted_chars=len(final_answer or ""),
    )

    rendered_context_key = f"rendered_context_used_{final_field.replace('answer_', '')}"
    return {
        final_field: final_answer,
        f"{final_field}_meta": stream_metrics,
        f"answer_artifact_{final_field.replace('answer_', '')}": final_artifact,
        rendered_context_key: rendered_context_used,
        "stream_meta": {final_field: stream_metrics},
    }


@measure_latency("generate_answer_gemma")
async def node_generate_answer_gemma(state: Any) -> Dict[str, Any]:
    return await generate_answer(state, model_name="gemma_triton_0", final_field="answer_gemma")


@measure_latency("generate_answer_solar")
async def node_generate_answer_solar(state: Any) -> Dict[str, Any]:
    return await generate_answer(state, model_name="solar_vllm_0", final_field="answer_solar")


@measure_latency("merge_answers")
async def node_merge_answers(state: Any) -> Dict[str, Any]:
    return await merge_answers(state)


async def merge_answers(state: Any) -> Dict[str, Any]:

    """Solar과 Gemma 결과 중 최종 답변을 선택하고 병합 메타를 기록합니다.

    [상태 관리 및 검증 로직 상세 설명]
    1. Dual Model Validation: 두 모델의 답변에 대해 Groundedness와 State Consistency를 동시에 검증합니다.
       하나의 모델이 실패하더라도 다른 모델의 성공적인 답변을 선택할 수 있는 복원력을 제공합니다.
    2. Answer Publication Guard: 만약 모든 모델이 '목록 불일치'나 '환각' 등의 이유로 검증을 통과하지 
       못할 경우, 사용자에게 잘못된 정보를 전달하는 대신 미리 정의된 안전한 'Fallback 메시지'를 
       출력하여 시스템의 신뢰성을 유지합니다.
    3. Deterministic Visible List: 목록형 응답에서 모델의 답변이 불안정할 경우, 검색된 데이터를 기반으로 
       시스템이 직접 확정적인 리스트를 생성하여 제공합니다(Answer Augmentation).
    4. Next Current Context (중요): 답변이 나간 후, 사용자가 "그 사람의 다른 과제는?"과 같이 
       대명사나 생략된 표현을 썼을 때 이를 이해할 수 있도록 현재 대화의 주제(Subject)를 
       추출하여 세션 메모리에 저장합니다. 
       - 답변이 정상적으로 나갔다면 'answer_published' 상태로 저장.
       - 데이터는 찾았으나 답변 생성에 실패해 질문 보정 유도가 필요한 경우 'answer_withheld_subject_retained' 
         상태로 저장하여 주제 맥락은 유지하되 답변만 보류합니다.
    """

    has_docs_context = bool(getattr(state, "context", None) or getattr(state, "prev_context", None) or getattr(state, "canonical_evidence", None))
    rendered_context_used = bool(getattr(state, "rendered_context_used_gemma", False)) or bool(getattr(state, "rendered_context_used_solar", False))
    execution_trace_summary = summarize_execution_trace(getattr(state, "execution_trace", None) or [])

    answer_gemma = (getattr(state, "answer_gemma", "") or "").strip()
    answer_gemma_meta = getattr(state, "answer_gemma_meta", None) or {}
    answer_solar_raw = getattr(state, "answer_solar", None) or ""
    answer_solar = answer_solar_raw.strip()
    solar_meta = getattr(state, "answer_solar_meta", None) or {}
    answer_artifact_gemma = getattr(state, "answer_artifact_gemma", None)
    answer_artifact_solar = getattr(state, "answer_artifact_solar", None)
    projection_payload = _projection_bundle_payload(state)
    canonical_evidence = getattr(state, "canonical_evidence", None) or _projection_sequence(projection_payload, "canonical_evidence")
    active_result_snapshot_raw = get_active_result_snapshot(getattr(state, "view_state", None))
    projection_lineage_ok = _snapshot_matches_projection(active_result_snapshot_raw, projection_payload, state)
    projection_lineage_status = _projection_lineage_status(projection_payload, projection_lineage_ok)
    active_result_snapshot = active_result_snapshot_raw if projection_lineage_ok else None
    output_family = _resolve_output_family(state)
    publication_applicable = output_family in _LIST_LIKE_OUTPUT_TYPES
    visible_order_count = (
        int(getattr(active_result_snapshot, "visible_count", 0) or 0)
        if publication_applicable and active_result_snapshot is not None
        else None
    )
    protect_visible_order = visible_order_count is not None
    groundedness_snapshot_model = build_groundedness_snapshot_from_canonical_evidence(
        canonical_evidence=canonical_evidence,
        visible_count=visible_order_count,
    )
    groundedness_snapshot = groundedness_snapshot_model.model_dump()
    state_consistency_snapshot_model = (
        build_state_snapshot_from_result_set(active_result_snapshot)
        if protect_visible_order
        else None
    )
    state_consistency_snapshot = (
        state_consistency_snapshot_model.model_dump()
        if state_consistency_snapshot_model is not None
        else {"available": False, "snapshot_source": "none"}
    )
    state_consistency_policy = _resolve_state_consistency_policy(state)
    selection = select_final_answer(
        answer_solar=answer_solar,
        answer_gemma=answer_gemma,
        solar_meta=solar_meta,
        gemma_meta=answer_gemma_meta,
        policy=_DUAL_MODEL_MERGE_POLICY,
        fallback_message=_DUAL_MODEL_FALLBACK_MESSAGE,
        min_answer_chars=_SOLAR_MIN_ANSWER_CHARS,
        evidence_snapshot=groundedness_snapshot,
        protect_visible_order=protect_visible_order,
        state_consistency_snapshot=state_consistency_snapshot,
        state_consistency_policy=state_consistency_policy,
    )
    solar_fail_reasons = list(selection["solar_fail_reasons"])
    solar_warning_reasons = list(selection["solar_warning_reasons"])
    solar_failed = bool(selection["solar_failed"])
    gemma_fail_reasons = list(selection.get("gemma_fail_reasons", []))
    gemma_warning_reasons = list(selection.get("gemma_warning_reasons", []))
    gemma_failed = bool(selection.get("gemma_failed", False))
    solar_groundedness = dict(selection.get("solar_groundedness") or {})
    gemma_groundedness = dict(selection.get("gemma_groundedness") or {})
    solar_state_consistency = dict(selection.get("solar_state_consistency") or {})
    gemma_state_consistency = dict(selection.get("gemma_state_consistency") or {})
    solar_state_diag = _state_consistency_diag(solar_state_consistency)
    gemma_state_diag = _state_consistency_diag(gemma_state_consistency)
    state_snapshot_diag = _state_snapshot_diag(state_consistency_snapshot)
    deterministic_visible_list = _build_deterministic_visible_list_candidate(
        state=state,
        active_result_snapshot=active_result_snapshot,
        groundedness_snapshot=groundedness_snapshot,
        state_consistency_snapshot=state_consistency_snapshot,
        state_consistency_policy=state_consistency_policy,
        state_inconsistent=str(selection.get("selection_reason") or "").strip().lower() == "both_models_state_inconsistent",
    )
    verified_projection_summary = _build_verified_projection_summary(deterministic_visible_list)
    augmented_state_inconsistent = False
    if verified_projection_summary is not None:
        augmented_state_inconsistent = _promote_llm_for_augmented_state_inconsistency(
            selection=selection,
            answer_solar=answer_solar,
            answer_gemma=answer_gemma,
            solar_failed=solar_failed,
            gemma_failed=gemma_failed,
        )
    selected_model = str(selection["selected_model"])
    selected_answer = str(selection["selected_answer"])
    if selected_model == "fallback":
        selected_answer = _build_user_visible_fallback_message(selection)
    degraded = bool(getattr(state, "degraded", False)) or (selected_model == "fallback")
    selected_meta = {}
    if selected_model == "solar":
        selected_meta = solar_meta
    elif selected_model == "gemma":
        selected_meta = answer_gemma_meta

    selected_answer_source = str(selected_meta.get("answer_source") or selected_model)
    selected_artifact = None

    if selected_model == "solar" and isinstance(answer_artifact_solar, AnswerArtifact):
        selected_artifact = answer_artifact_solar
    elif selected_model == "gemma" and isinstance(answer_artifact_gemma, AnswerArtifact):
        selected_artifact = answer_artifact_gemma
    elif selected_answer:
        selected_artifact = AnswerArtifact(
            text=selected_answer,
            answer_kind=("direct_answer" if selected_model == "fallback" else "llm_collected"),
            stream_metrics=dict(selected_meta or {}),
            user_visible_final_required=True,
            references=_collect_state_references(state),
            meta={
                "answer_source": selected_answer_source,
                "model_key": selected_model,
                "selection_reason": selection["selection_reason"],
                "degraded": selected_model == "fallback",
            },
        )
    if selected_model == "solar":
        selected_groundedness = solar_groundedness
    elif selected_model == "gemma":
        selected_groundedness = gemma_groundedness
    else:
        selected_groundedness = AnswerGroundednessVerdict(
            status="skipped_fallback",
            reason_codes=[],
            unsupported_claims=[],
            insufficient_axes=[],
            checked_claims=0,
            snapshot_source=str(groundedness_snapshot.get("snapshot_source") or "none"),
            claims={},
        ).model_dump()
    selected_state_consistency = dict(selection.get("selected_state_consistency") or {})
    if selected_model == "solar":
        selected_state_consistency = solar_state_consistency
    elif selected_model == "gemma":
        selected_state_consistency = gemma_state_consistency
    if not selected_state_consistency:
        selected_state_consistency = AnswerStateConsistencyVerdict(
            status="not_applicable",
        ).model_dump()
    selected_state_diag = _state_consistency_diag(selected_state_consistency)
    visible_answer_manifest_status = "not_applicable"
    visible_answer_manifest_publication = None
    view_state_manifest_snapshot = None
    if isinstance(selected_artifact, AnswerArtifact):
        enriched_meta = dict(selected_artifact.meta or {})
        enriched_meta.setdefault("answer_source", selected_answer_source)
        enriched_meta.setdefault("model_key", selected_model)
        enriched_meta["selection_reason"] = selection["selection_reason"]
        if verified_projection_summary is not None:
            enriched_meta["verified_projection_summary"] = dict(verified_projection_summary)
            enriched_meta["deterministic_snapshot_suppressed"] = True
            enriched_meta["deterministic_render_mode"] = verified_projection_summary.get("render_mode")
            if augmented_state_inconsistent:
                enriched_meta["answer_augmentation_mode"] = "verified_projection_summary"
        if execution_trace_summary:
            enriched_meta.update(execution_trace_summary)
        enriched_meta["groundedness_status"] = selected_groundedness.get("status")
        enriched_meta["groundedness_reason_codes"] = list(selected_groundedness.get("reason_codes") or [])
        enriched_meta["groundedness_summary"] = selected_groundedness
        enriched_meta["answer_state_consistency"] = selected_state_consistency
        enriched_meta["answer_state_consistency_status"] = selected_state_consistency.get("status")
        enriched_meta["answer_state_consistency_reason_codes"] = list(selected_state_consistency.get("reason_codes") or [])
        enriched_meta["projection_id"] = projection_payload.get("projection_id")
        enriched_meta["projection_lineage_status"] = projection_lineage_status
        selected_references = list(selected_artifact.references or [])
        if not selected_references:
            selected_references = _collect_state_references(state)
        snapshot_payload = _snapshot_to_payload(active_result_snapshot) if active_result_snapshot is not None else None
        publication_groundedness = selected_groundedness
        publication_state_consistency = selected_state_consistency
        if augmented_state_inconsistent and isinstance(verified_projection_summary, dict):
            publication_groundedness = dict(verified_projection_summary.get("groundedness") or selected_groundedness)
            publication_state_consistency = dict(verified_projection_summary.get("state_consistency") or selected_state_consistency)
            enriched_meta["visible_answer_manifest_publication_source"] = "verified_projection_summary"
        publication = build_visible_answer_manifest_publication(
            publication_applicable=publication_applicable,
            snapshot_payload=snapshot_payload,
            projection_payload=projection_payload,
            projection_lineage_ok=projection_lineage_ok,
            projection_lineage_status=projection_lineage_status,
            groundedness=publication_groundedness,
            state_consistency=publication_state_consistency,
        )
        visible_answer_manifest_status = publication.publication_status
        visible_answer_manifest = publication.published_manifest
        visible_answer_manifest_publication = publication.to_meta_dict()
        if visible_answer_manifest_status == "approved":
            view_state_manifest_snapshot = active_result_snapshot
        enriched_meta["visible_answer_manifest_status"] = visible_answer_manifest_status
        enriched_meta["visible_answer_manifest_publication"] = dict(visible_answer_manifest_publication or {})
        enriched_meta["answer_publishability"] = (
            "publishable"
            if visible_answer_manifest_status == "approved"
            else "withheld_partial"
            if visible_answer_manifest_status == "withheld_partial"
            else "blocked"
            if visible_answer_manifest_status.startswith("blocked_")
            else "not_applicable"
        )
        selected_artifact = AnswerArtifact(
            text=selected_artifact.text,
            answer_kind=selected_artifact.answer_kind,
            stream_metrics=dict(selected_artifact.stream_metrics or {}),
            user_visible_final_required=bool(selected_artifact.user_visible_final_required),
            references=selected_references,
            visible_answer_manifest=visible_answer_manifest,
            visible_answer_manifest_publication=dict(visible_answer_manifest_publication or {}),
            clarification=selected_artifact.clarification,
            error=selected_artifact.error,
            meta=enriched_meta,
        )
    next_view_state = getattr(state, "view_state", None)
    if visible_answer_manifest_status != "not_applicable" and next_view_state is not None:
        next_view_state = set_visible_answer_manifest(
            next_view_state,
            snapshot=view_state_manifest_snapshot,
        )
    selected_answer_meta = (
        selected_artifact.to_meta_dict()
        if isinstance(selected_artifact, AnswerArtifact)
        else selected_meta
    )
    retrieval_evidence_count = max(
        len(canonical_evidence or []) if isinstance(canonical_evidence, list) else 0,
        len(getattr(state, "context", None) or []) if isinstance(getattr(state, "context", None), list) else 0,
        len(getattr(state, "prev_context", None) or []) if isinstance(getattr(state, "prev_context", None), list) else 0,
        int(getattr(active_result_snapshot, "visible_count", 0) or 0) if active_result_snapshot is not None else 0,
    )
    next_current_context = build_current_context(
        view_state=next_view_state,
        selected_answer_meta=selected_answer_meta,
        intent_payload=getattr(state, "intent_payload", None),
        staged_current_context=getattr(state, "next_current_context", None),
        retrieval_evidence_count=retrieval_evidence_count,
    )
    intent_payload_for_context = getattr(state, "intent_payload", None)
    normalized_for_context = getattr(intent_payload_for_context, "normalized_intent", None)
    strategy_meta_for_context = dict(getattr(intent_payload_for_context, "strategy_meta", {}) or {})
    subject_kind_for_context = None
    subject_name_for_context = None
    people_terms_for_context = list(getattr(normalized_for_context, "people_terms", []) or [])
    org_terms_for_context = list(getattr(normalized_for_context, "org_terms", []) or [])
    if people_terms_for_context:
        subject_kind_for_context = "people"
        subject_name_for_context = str(people_terms_for_context[0] or "").strip() or None
    elif org_terms_for_context:
        subject_kind_for_context = "org"
        subject_name_for_context = str(org_terms_for_context[0] or "").strip() or None
    if isinstance(next_current_context, SubjectQueryContext):
        log_event(
            "AGENT.SUBJECT_CONTEXT.RETAINED",
            request_id=getattr(state, "request_id", None),
            conversation_id=getattr(state, "conversation_id", None),
            turn_id=getattr(state, "turn_id", None),
            subject_kind=next_current_context.subject_kind,
            subject_name=next_current_context.subject_name,
            publication_status=next_current_context.publication_status,
            identity_status=next_current_context.identity_status,
            subject_identity_candidate_count=max(
                len(list(next_current_context.subject_ids_map.get("person_no") or [])),
                len(list(next_current_context.subject_ids_map.get("org_id") or [])),
                len(list(next_current_context.subject_ids_map.get("org_code") or [])),
                len(list(next_current_context.subject_ids_map.get("biz_no") or [])),
            ),
            answer_publishability=selected_answer_meta.get("answer_publishability") if isinstance(selected_answer_meta, dict) else None,
            subject_continuity_retained=bool(next_current_context.followup_rights.refinement_allowed),
            current_context_type=next_current_context.context_type,
            tool_execution_source=strategy_meta_for_context.get("tool_execution_source"),
            withheld_but_subject_retained=bool(
                next_current_context.publication_status == "answer_withheld_subject_retained"
            ),
            planner_llm_skipped=strategy_meta_for_context.get("planner_llm_skipped"),
        )
    elif subject_kind_for_context and subject_name_for_context:
        log_event(
            "AGENT.SUBJECT_CONTEXT.SKIPPED",
            request_id=getattr(state, "request_id", None),
            conversation_id=getattr(state, "conversation_id", None),
            turn_id=getattr(state, "turn_id", None),
            subject_kind=subject_kind_for_context,
            subject_name=subject_name_for_context,
            publication_status=selected_answer_meta.get("answer_publishability") if isinstance(selected_answer_meta, dict) else None,
            answer_publishability=selected_answer_meta.get("answer_publishability") if isinstance(selected_answer_meta, dict) else None,
            subject_continuity_retained=False,
            current_context_type=getattr(next_current_context, "context_type", None),
            tool_execution_source=strategy_meta_for_context.get("tool_execution_source"),
            withheld_but_subject_retained=False,
            planner_llm_skipped=strategy_meta_for_context.get("planner_llm_skipped"),
        )

    merge_debug = {
        "policy": _DUAL_MODEL_MERGE_POLICY,
        "selected_model": selected_model,
        "selected_answer_source": selected_answer_source,
        "selection_reason": selection["selection_reason"],
        "solar_failed": solar_failed,
        "solar_fail_reasons": solar_fail_reasons,
        "solar_warning_reasons": solar_warning_reasons,
        "solar_meta": selection["solar_meta"],
        "gemma_failed": gemma_failed,
        "gemma_fail_reasons": gemma_fail_reasons,
        "gemma_warning_reasons": gemma_warning_reasons,
        "gemma_meta": selection.get("gemma_meta", {}),
        "solar_answer_chars": selection["solar_answer_chars"],
        "gemma_answer_chars": selection["gemma_answer_chars"],
        "min_chars_threshold": _SOLAR_MIN_ANSWER_CHARS,
        "selected_answer_kind": (selected_artifact.answer_kind if isinstance(selected_artifact, AnswerArtifact) else None),
        "groundedness_snapshot": groundedness_snapshot,
        "solar_groundedness": solar_groundedness,
        "gemma_groundedness": gemma_groundedness,
        "selected_groundedness": selected_groundedness,
        "state_consistency_snapshot": state_consistency_snapshot,
        "state_consistency_snapshot_diag": state_snapshot_diag,
        "projection_id": projection_payload.get("projection_id"),
        "projection_lineage_status": projection_lineage_status,
        "solar_state_consistency": solar_state_consistency,
        "solar_state_diag": solar_state_diag,
        "gemma_state_consistency": gemma_state_consistency,
        "gemma_state_diag": gemma_state_diag,
        "selected_state_consistency": selected_state_consistency,
        "selected_state_diag": selected_state_diag,
        "verified_projection_summary": verified_projection_summary,
        "deterministic_snapshot_suppressed": bool(verified_projection_summary is not None),
        "answer_augmentation_mode": "verified_projection_summary" if augmented_state_inconsistent else None,
        "visible_answer_manifest_status": (
            (selected_artifact.meta or {}).get("visible_answer_manifest_status")
            if isinstance(selected_artifact, AnswerArtifact)
            else "not_applicable"
        ),
        "visible_answer_manifest_publication": (
            (selected_artifact.meta or {}).get("visible_answer_manifest_publication")
            if isinstance(selected_artifact, AnswerArtifact)
            else None
        ),
    }

    log_event(
        "LLM.RESULT",
        request_id=getattr(state, "request_id", None),
        conversation_id=getattr(state, "conversation_id", None),
        stage="merge_answers",
        selected_model=selected_model,
        solar_failed=int(solar_failed),
        solar_fail_reasons=solar_fail_reasons,
        gemma_failed=int(gemma_failed),
        gemma_fail_reasons=gemma_fail_reasons,
        gemma_answer_chars=len(answer_gemma),
        solar_answer_chars=len(answer_solar),
        groundedness_status=selected_groundedness.get("status"),
        groundedness_reason_codes=list(selected_groundedness.get("reason_codes") or []),
        state_consistency_status=selected_state_consistency.get("status"),
        state_consistency_reason_codes=list(selected_state_consistency.get("reason_codes") or []),
        state_snapshot_visible_count=state_snapshot_diag.get("visible_count"),
        solar_state_status=solar_state_diag.get("status"),
        solar_state_reason_codes=list(solar_state_diag.get("reason_codes") or []),
        gemma_state_status=gemma_state_diag.get("status"),
        gemma_state_reason_codes=list(gemma_state_diag.get("reason_codes") or []),
        state_consistency_subset_accepted=int(bool(selected_state_diag.get("subset_accepted"))),
        state_manifest_publish_allowed=int(bool(selected_state_diag.get("manifest_publish_allowed"))),
        visible_answer_manifest_status=(
            (selected_artifact.meta or {}).get("visible_answer_manifest_status")
            if isinstance(selected_artifact, AnswerArtifact)
            else "not_applicable"
        ),
        selection_reason=selection["selection_reason"],
    )
    if protect_visible_order:
        log_event(
            "ANSWER.STATE_DIAG",
            request_id=getattr(state, "request_id", None),
            conversation_id=getattr(state, "conversation_id", None),
            stage="merge_answers",
            selection_reason=selection["selection_reason"],
            selected_model=selected_model,
            state_snapshot=state_snapshot_diag,
            solar_state=solar_state_diag,
            gemma_state=gemma_state_diag,
            selected_state=selected_state_diag,
        )
    logger.info(
        "[merge_selection] request_id={} selected_model={} solar_fail_reasons={}",
        getattr(state, "request_id", None),
        selected_model,
        solar_fail_reasons,
    )

    return {
        "messages": [AIMessage(content=selected_answer)],
        "final_answer_text": selected_answer,
        "final_answer_artifact": selected_artifact,
        "answer_solar_raw": answer_solar_raw,
        "merge_debug": merge_debug,
        "selected_answer_meta": selected_answer_meta,
        "next_current_context": next_current_context,
        "answer_groundedness_snapshot": groundedness_snapshot_model,
        "answer_groundedness_verdict": AnswerGroundednessVerdict.model_validate(selected_groundedness),
        "answer_state_consistency_snapshot": (
            state_consistency_snapshot_model
            if state_consistency_snapshot_model is not None
            else None
        ),
        "answer_state_consistency_verdict": AnswerStateConsistencyVerdict.model_validate(selected_state_consistency),
        "rendered_context_used": rendered_context_used,
        "degraded": degraded,
        "view_state": next_view_state,
    }
