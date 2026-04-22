"""ADR-0014 Scope A — Disambiguation 후보 라벨 enrichment.

동명(同名) 후보가 같은 entity_kind로 남는 경우, 사용자/LLM이 둘을 구분할
수 있도록 각 TurnCandidate에 대해 1줄 disambiguator와 aux dict를 만든다.

설계 원칙
---------
- 이 모듈은 candidate/ view_state에서 이미 채워진 값만 사용한다. **새로운
  ID를 생성하지 않는다** (ADR-0013/ADR-0014 non-negotiables).
- raw payload는 주입하지 않는다.
- heuristic disambiguator가 실패하고, flag로 LLM enrichment가 켜져 있을
  때에만 LLM을 호출한다. LLM 실패/timeout/invalid output은 deterministic
  heuristic fallback으로 되돌린다.
- dedup 키 `_candidate_id`는 건드리지 않는다. 렌더링 레이어 전용이다.

Env flags
---------
- ``LLM_DISAMBIGUATION_LABEL_ENABLED`` (default ``0``):
  truthy일 때만 heuristic 이후 동명 후보가 남아 있으면 LLM을 호출한다.
  dark launch를 위해 기본값은 off다.

주의
----
- ``aux``는 API 응답 호환을 위해 빈 dict면 payload에서 제거된다
  (``ClarificationSuggestion.to_payload``).
- ``disambiguator``는 사용자에게 노출되는 짧은 한 줄이다. 의미를 바꾸지
  않고, 라벨에 붙어 동명 후보 구분 용도로만 쓰인다.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from apps.api.runtime_helpers import log_event
from apps.chat.llm_json import sanitize_llm_json
from apps.chat.llm_runtime import build_llm, load_prompt_file
from apps.conversation.view_state import (
    ConversationViewState,
    RecentMentionRecord,
    SubjectIndexEntry,
)
from apps.platform.langchain_compat import ChatPromptTemplate, PydanticOutputParser, SystemMessage
from apps.planner.planner_defaults import PLANNER_DISABLE_THINKING, PLANNER_TEMPERATURE
from apps.planner.prompt_asset_paths import planner_prompt_path
from pydantic import BaseModel, Field


__all__ = [
    "enrich_candidate_labels",
    "enrich_candidate_labels_for_summary",
    "render_label",
    "llm_disambiguation_enabled",
    "summarize_view_state_for_labeler",
]


_TRUTHY_ENV_VALUES = {"1", "true", "yes", "y", "on"}
_LLM_MODEL_NAME = "solar_vllm_0"
_LLM_TIMEOUT_SECONDS = 3.0


class ClarificationLabelerItem(BaseModel):
    candidate_id: str
    disambiguator: Optional[str] = None


class ClarificationLabelerResult(BaseModel):
    labels: List[ClarificationLabelerItem] = Field(default_factory=list)
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def llm_disambiguation_enabled() -> bool:
    """ADR-0014 Scope A의 LLM enrichment hook이 켜져 있는지 판정."""
    return _norm_lower(os.getenv("LLM_DISAMBIGUATION_LABEL_ENABLED", "0")) in _TRUTHY_ENV_VALUES


def _timeout_seconds() -> float:
    raw = _norm(os.getenv("LLM_DISAMBIGUATION_LABEL_TIMEOUT_SECONDS"))
    if not raw:
        return _LLM_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _LLM_TIMEOUT_SECONDS
    return value if value > 0 else _LLM_TIMEOUT_SECONDS


def _field(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _id_suffix(value: Optional[str], *, tail: int = 6) -> Optional[str]:
    text = _norm(value)
    if not text:
        return None
    if len(text) <= tail:
        return text
    return text[-tail:]


def _subject_index_by_name(view_state: Optional[ConversationViewState]) -> Dict[Tuple[str, str], SubjectIndexEntry]:
    """(display_name_lower, kind_lower) -> SubjectIndexEntry 인덱스."""
    if view_state is None:
        return {}
    index: Dict[Tuple[str, str], SubjectIndexEntry] = {}
    raw = getattr(view_state, "subject_index", {}) or {}
    if not isinstance(raw, dict):
        return index
    for value in raw.values():
        try:
            entry = value if isinstance(value, SubjectIndexEntry) else SubjectIndexEntry.model_validate(value)
        except Exception:
            continue
        name_key = _norm_lower(entry.display_name)
        kind_key = _norm_lower(entry.kind)
        if not name_key:
            continue
        index.setdefault((name_key, kind_key), entry)
    return index


def _recent_mentions_by_name(
    view_state: Optional[ConversationViewState],
) -> Dict[Tuple[str, str], List[RecentMentionRecord]]:
    """(title_text_lower, entity_kind_lower) -> 최근 언급 리스트."""
    if view_state is None:
        return {}
    index: Dict[Tuple[str, str], List[RecentMentionRecord]] = {}
    for mention in getattr(view_state, "recent_mentions", []) or []:
        if not isinstance(mention, RecentMentionRecord):
            continue
        name_key = _norm_lower(getattr(mention, "title_text", None))
        kind_key = _norm_lower(getattr(mention, "entity_kind", None))
        if not name_key:
            continue
        index.setdefault((name_key, kind_key), []).append(mention)
    return index


def summarize_view_state_for_labeler(view_state: Optional[ConversationViewState]) -> Dict[str, Any]:
    """Build the small, raw-payload-free summary used by Scope A label enrichment."""
    if view_state is None:
        return {"subject_index_entries": [], "recent_mentions": []}

    subject_index_entries: List[Dict[str, Any]] = []
    raw_subject_index = getattr(view_state, "subject_index", {}) or {}
    if isinstance(raw_subject_index, dict):
        for value in raw_subject_index.values():
            try:
                entry = value if isinstance(value, SubjectIndexEntry) else SubjectIndexEntry.model_validate(value)
            except Exception:
                continue
            subject_index_entries.append(
                {
                    "display_name": _norm(entry.display_name),
                    "kind": _norm(entry.kind),
                    "affiliation": _norm(entry.affiliation),
                    "role": _norm(entry.role),
                }
            )

    recent_mentions: List[Dict[str, Any]] = []
    for mention in getattr(view_state, "recent_mentions", []) or []:
        if not isinstance(mention, RecentMentionRecord):
            continue
        recent_mentions.append(
            {
                "title_text": _norm(getattr(mention, "title_text", None)),
                "entity_kind": _norm(getattr(mention, "entity_kind", None)),
                "year": _norm(getattr(mention, "year", None)),
                "lead_org": _norm(getattr(mention, "lead_org", None)),
            }
        )
    return {
        "subject_index_entries": subject_index_entries,
        "recent_mentions": recent_mentions,
    }


def _subject_index_by_name_from_summary(view_state_summary: Optional[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    summary = view_state_summary if isinstance(view_state_summary, dict) else {}
    for entry in list(summary.get("subject_index_entries") or []):
        if not isinstance(entry, dict):
            continue
        name_key = _norm_lower(entry.get("display_name"))
        kind_key = _norm_lower(entry.get("kind"))
        if not name_key:
            continue
        index.setdefault((name_key, kind_key), dict(entry))
    return index


def _recent_mentions_by_name_from_summary(
    view_state_summary: Optional[Dict[str, Any]],
) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    summary = view_state_summary if isinstance(view_state_summary, dict) else {}
    for mention in list(summary.get("recent_mentions") or []):
        if not isinstance(mention, dict):
            continue
        name_key = _norm_lower(mention.get("title_text"))
        kind_key = _norm_lower(mention.get("entity_kind"))
        if not name_key:
            continue
        index.setdefault((name_key, kind_key), []).append(dict(mention))
    return index


def _ids_map_value(candidate: Any, key: str) -> Optional[str]:
    ids_map = getattr(candidate, "ids_map", None) or {}
    if not isinstance(ids_map, dict):
        return None
    values = ids_map.get(key) or []
    if not values:
        return None
    first = values[0] if isinstance(values, (list, tuple)) else values
    text = _norm(first)
    return text or None


# ---------------------------------------------------------------------------
# Heuristic disambiguator
# ---------------------------------------------------------------------------


def _build_people_aux(
    candidate: Any,
    *,
    subject_entry: Optional[Any],
) -> Dict[str, Any]:
    aux: Dict[str, Any] = {}
    if subject_entry is not None:
        affiliation = _norm(_field(subject_entry, "affiliation"))
        role = _norm(_field(subject_entry, "role"))
        if affiliation:
            aux["affiliation"] = affiliation
        if role:
            aux["role"] = role
    person_no = _ids_map_value(candidate, "person_no")
    if person_no:
        aux["person_no_suffix"] = _id_suffix(person_no)
    return aux


def _build_project_aux(
    candidate: Any,
    *,
    mentions: List[Any],
) -> Dict[str, Any]:
    aux: Dict[str, Any] = {}
    years = [_field(m, "year") for m in mentions if _norm(_field(m, "year"))]
    lead_orgs = [_field(m, "lead_org") for m in mentions if _norm(_field(m, "lead_org"))]
    if years:
        aux["year"] = _norm(years[-1])  # 가장 최근 등장 연도
    if lead_orgs:
        aux["lead_org"] = _norm(lead_orgs[-1])
    pjt_no = _ids_map_value(candidate, "pjt_no")
    pjt_id = _ids_map_value(candidate, "pjt_id")
    if pjt_id:
        aux["pjt_id_suffix"] = _id_suffix(pjt_id)
    if pjt_no:
        aux["pjt_no_suffix"] = _id_suffix(pjt_no)
    return aux


def _build_org_aux(candidate: Any) -> Dict[str, Any]:
    aux: Dict[str, Any] = {}
    org_id = _ids_map_value(candidate, "org_id")
    org_code = _ids_map_value(candidate, "org_code")
    biz_no = _ids_map_value(candidate, "biz_no")
    if org_code:
        aux["org_code"] = org_code
    if org_id:
        aux["org_id_suffix"] = _id_suffix(org_id)
    if biz_no:
        aux["biz_no_suffix"] = _id_suffix(biz_no)
    return aux


def _build_aux_for_candidate(
    candidate: Any,
    *,
    subject_entry: Optional[Any],
    mentions: List[Any],
) -> Dict[str, Any]:
    kind = _norm_lower(getattr(candidate, "entity_kind", ""))
    if kind == "people":
        aux = _build_people_aux(candidate, subject_entry=subject_entry)
    elif kind == "org":
        aux = _build_org_aux(candidate)
    else:
        aux = _build_project_aux(candidate, mentions=mentions)
    source = _norm(getattr(candidate, "source", ""))
    if source:
        aux["source"] = source
    return aux


def _compose_disambiguator(aux: Dict[str, Any], *, kind: str) -> Optional[str]:
    """aux dict에서 사람이 읽기 좋은 한 줄을 조립한다.

    우선순위 — 사람: affiliation > role > person_no_suffix
               과제: year > lead_org > pjt_id_suffix > pjt_no_suffix
               기관: org_code > org_id_suffix > biz_no_suffix
    """
    kind = _norm_lower(kind)
    parts: List[str] = []
    if kind == "people":
        if aux.get("affiliation"):
            parts.append(str(aux["affiliation"]))
        if aux.get("role") and aux["role"] not in parts:
            parts.append(str(aux["role"]))
        if not parts and aux.get("person_no_suffix"):
            parts.append(f"person_no ···{aux['person_no_suffix']}")
    elif kind == "org":
        if aux.get("org_code"):
            parts.append(str(aux["org_code"]))
        elif aux.get("org_id_suffix"):
            parts.append(f"org_id ···{aux['org_id_suffix']}")
        elif aux.get("biz_no_suffix"):
            parts.append(f"biz_no ···{aux['biz_no_suffix']}")
    else:
        if aux.get("year"):
            parts.append(str(aux["year"]))
        if aux.get("lead_org") and aux["lead_org"] not in parts:
            parts.append(str(aux["lead_org"]))
        if not parts and aux.get("pjt_id_suffix"):
            parts.append(f"pjt_id ···{aux['pjt_id_suffix']}")
        elif not parts and aux.get("pjt_no_suffix"):
            parts.append(f"pjt_no ···{aux['pjt_no_suffix']}")
    text = " · ".join(p for p in parts if p)
    return text or None


def _compact_ids_map(ids_map: Any) -> Dict[str, List[str]]:
    if not isinstance(ids_map, dict):
        return {}
    compact: Dict[str, List[str]] = {}
    for key, raw_values in ids_map.items():
        if isinstance(raw_values, str):
            values = [raw_values]
        elif isinstance(raw_values, (list, tuple, set)):
            values = list(raw_values)
        else:
            values = [raw_values]
        normalized = [_norm(value) for value in values if _norm(value)]
        if normalized:
            compact[_norm(key)] = normalized
    return compact


def _rendered_label_counts(enriched: List[Tuple[Any, Dict[str, Any], Optional[str]]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for candidate, _aux, disamb in enriched:
        label = render_label(
            display_name=getattr(candidate, "display_name", None),
            entity_kind=getattr(candidate, "entity_kind", None),
            disambiguator=disamb,
            fallback_id=getattr(candidate, "candidate_id", None),
        )
        counts[label] = counts.get(label, 0) + 1
    return counts


def _duplicate_rendered_candidate_ids(enriched: List[Tuple[Any, Dict[str, Any], Optional[str]]]) -> set[str]:
    counts = _rendered_label_counts(enriched)
    duplicate_ids: set[str] = set()
    for candidate, _aux, disamb in enriched:
        label = render_label(
            display_name=getattr(candidate, "display_name", None),
            entity_kind=getattr(candidate, "entity_kind", None),
            disambiguator=disamb,
            fallback_id=getattr(candidate, "candidate_id", None),
        )
        candidate_id = _norm(getattr(candidate, "candidate_id", None))
        if candidate_id and counts.get(label, 0) > 1:
            duplicate_ids.add(candidate_id)
    return duplicate_ids


def _apply_deterministic_fallback(
    enriched: List[Tuple[Any, Dict[str, Any], Optional[str]]],
    *,
    base_label_count: Dict[Tuple[str, str], int],
) -> List[Tuple[Any, Dict[str, Any], Optional[str]]]:
    label_signature: Dict[Tuple[str, str, str], int] = {}
    final_with_fallback: List[Tuple[Any, Dict[str, Any], Optional[str]]] = []
    for candidate, aux, disamb in enriched:
        name_key = _norm_lower(getattr(candidate, "display_name", ""))
        kind_key = _norm_lower(getattr(candidate, "entity_kind", ""))
        if base_label_count.get((name_key, kind_key), 0) <= 1:
            final_with_fallback.append((candidate, aux, disamb))
            continue
        effective = disamb
        key = (name_key, kind_key, _norm(effective))
        while effective is None or label_signature.get(key, 0) > 0:
            fallback_parts: List[str] = []
            if effective:
                fallback_parts.append(effective)
            for id_key in ("person_no_suffix", "pjt_id_suffix", "pjt_no_suffix", "org_id_suffix", "biz_no_suffix"):
                suffix = aux.get(id_key)
                if suffix:
                    fallback_parts.append(f"{id_key.removesuffix('_suffix')} ···{suffix}")
                    break
            if not fallback_parts:
                cand_id = _norm(getattr(candidate, "candidate_id", ""))
                if cand_id:
                    fallback_parts.append(f"id ···{_id_suffix(cand_id)}")
            new_disamb = " · ".join(fallback_parts) if fallback_parts else None
            if new_disamb == effective:
                break
            effective = new_disamb
            key = (name_key, kind_key, _norm(effective))
        label_signature[key] = label_signature.get(key, 0) + 1
        final_with_fallback.append((candidate, aux, effective))
    return final_with_fallback


def _llm_candidate_payload(
    enriched: List[Tuple[Any, Dict[str, Any], Optional[str]]],
    *,
    target_ids: set[str],
) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for candidate, aux, _disamb in enriched:
        candidate_id = _norm(getattr(candidate, "candidate_id", None))
        if candidate_id not in target_ids:
            continue
        item = {
            "candidate_id": candidate_id,
            "entity_kind": _norm(getattr(candidate, "entity_kind", None)) or None,
            "display_name": _norm(getattr(candidate, "display_name", None)) or None,
            "ids_map": _compact_ids_map(getattr(candidate, "ids_map", None)),
            "aux": dict(aux or {}),
        }
        payload.append({key: value for key, value in item.items() if value not in (None, {}, [], "")})
    return payload


async def _invoke_disambiguation_label_llm_async(
    *,
    question: str,
    candidate_payload: List[Dict[str, Any]],
) -> ClarificationLabelerResult:
    llm = build_llm(model_name=_LLM_MODEL_NAME)
    parser = PydanticOutputParser(pydantic_object=ClarificationLabelerResult)
    system_prompt = await load_prompt_file(planner_prompt_path("clarification_labeler_v1.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "{format_instructions}\n"
                "<user_query>{question}</user_query>\n"
                "<candidates>{candidates}</candidates>",
            ),
        ]
    )
    labeler_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
        top_p=1.0,
        max_tokens=220,
    )
    chain = prompt | labeler_llm | sanitize_llm_json | parser
    return await chain.ainvoke(
        {
            "format_instructions": parser.get_format_instructions(),
            "question": _norm(question),
            "candidates": json.dumps(candidate_payload, ensure_ascii=False),
        }
    )


def _run_disambiguation_label_llm(
    *,
    question: str,
    candidate_payload: List[Dict[str, Any]],
) -> Any:
    timeout = _timeout_seconds()
    coro = asyncio.wait_for(
        _invoke_disambiguation_label_llm_async(
            question=question,
            candidate_payload=candidate_payload,
        ),
        timeout=timeout,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: asyncio.run(coro))
    try:
        return future.result(timeout=timeout + 0.5)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _coerce_labeler_items(result: Any) -> List[Dict[str, Any]]:
    if isinstance(result, ClarificationLabelerResult):
        return [item.model_dump() for item in result.labels]
    if isinstance(result, dict):
        raw_items = result.get("labels") or result.get("items") or result.get("candidates") or []
    elif isinstance(result, list):
        raw_items = result
    else:
        raw_items = getattr(result, "labels", [])
    items: List[Dict[str, Any]] = []
    for item in list(raw_items or []):
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            raise ValueError("invalid_label_item")
        items.append(dict(item))
    return items


def _validate_llm_label_output(
    result: Any,
    *,
    target_ids: set[str],
    candidates_by_id: Dict[str, Any],
) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for item in _coerce_labeler_items(result):
        candidate_id = _norm(item.get("candidate_id"))
        if not candidate_id:
            raise ValueError("missing_candidate_id")
        if candidate_id not in target_ids:
            raise ValueError("invalid_candidate_id")
        if candidate_id in labels:
            raise ValueError("duplicate_candidate_id")
        disamb = " ".join(_norm(item.get("disambiguator")).split())
        if not disamb:
            raise ValueError("missing_disambiguator")
        if len(disamb) > 120:
            raise ValueError("invalid_disambiguator")
        labels[candidate_id] = disamb

    if set(labels) != set(target_ids):
        raise ValueError("missing_candidate_id")

    rendered: Dict[str, str] = {}
    for candidate_id, disamb in labels.items():
        candidate = candidates_by_id[candidate_id]
        label = render_label(
            display_name=getattr(candidate, "display_name", None),
            entity_kind=getattr(candidate, "entity_kind", None),
            disambiguator=disamb,
            fallback_id=getattr(candidate, "candidate_id", None),
        )
        if label in rendered:
            raise ValueError("duplicate_disambiguator")
        rendered[label] = candidate_id
    return labels


def _apply_llm_disambiguators(
    enriched: List[Tuple[Any, Dict[str, Any], Optional[str]]],
    *,
    disambiguators: Dict[str, str],
) -> List[Tuple[Any, Dict[str, Any], Optional[str]]]:
    updated: List[Tuple[Any, Dict[str, Any], Optional[str]]] = []
    for candidate, aux, disamb in enriched:
        candidate_id = _norm(getattr(candidate, "candidate_id", None))
        updated.append((candidate, aux, disambiguators.get(candidate_id, disamb)))
    return updated


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def render_label(
    *,
    display_name: Optional[str],
    entity_kind: Optional[str],
    disambiguator: Optional[str],
    fallback_id: Optional[str] = None,
) -> str:
    """라벨 렌더링. disambiguator가 있으면 '이름 — 보조 ( kind )' 형태."""
    base = _norm(display_name) or _norm(fallback_id) or "후보"
    kind = _norm(entity_kind)
    disamb = _norm(disambiguator)
    label = base
    if disamb:
        label = f"{label} — {disamb}"
    if kind:
        label = f"{label} ({kind})"
    return label


def enrich_candidate_labels_for_summary(
    *,
    question: str,
    candidates: List[Any],
    view_state_summary: Optional[Dict[str, Any]],
) -> List[Tuple[Any, Dict[str, Any], Optional[str]]]:
    """각 candidate에 대해 (candidate, aux, disambiguator)를 반환.

    - 기본 경로는 heuristic이다.
    - 동일 base label (`display_name` + `entity_kind`)을 가진 candidate가
      2개 이상 남고, LLM flag가 켜져 있으면 LLM enrichment를 시도한다.
    - LLM은 `disambiguator`만 생성할 수 있으며 candidate ID/kind/ids_map은
      원본 candidate 값을 그대로 보존한다.
    """
    subject_index = _subject_index_by_name_from_summary(view_state_summary)
    recent_index = _recent_mentions_by_name_from_summary(view_state_summary)

    decorated: List[Tuple[Any, Dict[str, Any], Optional[str]]] = []
    base_label_count: Dict[Tuple[str, str], int] = {}

    # 1st pass — aux 수집 + 기본 heuristic disambiguator 조립
    for candidate in candidates:
        name_key = _norm_lower(getattr(candidate, "display_name", ""))
        kind_key = _norm_lower(getattr(candidate, "entity_kind", ""))
        base_label_count[(name_key, kind_key)] = base_label_count.get((name_key, kind_key), 0) + 1

        subject_entry = subject_index.get((name_key, kind_key))
        mentions = recent_index.get((name_key, kind_key), [])
        aux = _build_aux_for_candidate(
            candidate,
            subject_entry=subject_entry,
            mentions=mentions,
        )
        disamb = _compose_disambiguator(aux, kind=kind_key or "")
        decorated.append((candidate, aux, disamb))

    # 2nd pass — 동명 후보만 disambiguator 유지, 단일은 생략해 깔끔히.
    finalized: List[Tuple[Any, Dict[str, Any], Optional[str]]] = []
    for candidate, aux, disamb in decorated:
        name_key = _norm_lower(getattr(candidate, "display_name", ""))
        kind_key = _norm_lower(getattr(candidate, "entity_kind", ""))
        if base_label_count.get((name_key, kind_key), 0) <= 1:
            finalized.append((candidate, aux, None))
        else:
            finalized.append((candidate, aux, disamb))

    final_result = list(finalized)
    duplicate_target_ids = _duplicate_rendered_candidate_ids(finalized)
    llm_attempted = False
    disambiguator_source = "heuristic"
    reason = "heuristic"
    fallback_reason: Optional[str] = None

    if llm_disambiguation_enabled() and duplicate_target_ids:
        llm_attempted = True
        started_at = time.perf_counter()
        try:
            candidates_by_id = {
                _norm(getattr(candidate, "candidate_id", None)): candidate
                for candidate, _aux, _disamb in finalized
                if _norm(getattr(candidate, "candidate_id", None))
            }
            candidate_payload = _llm_candidate_payload(finalized, target_ids=duplicate_target_ids)
            result = _run_disambiguation_label_llm(
                question=question,
                candidate_payload=candidate_payload,
            )
            disambiguators = _validate_llm_label_output(
                result,
                target_ids=duplicate_target_ids,
                candidates_by_id=candidates_by_id,
            )
            final_result = _apply_llm_disambiguators(finalized, disambiguators=disambiguators)
            if sum(count for count in _rendered_label_counts(final_result).values() if count > 1) > 0:
                raise ValueError("duplicate_label_after_llm")
            disambiguator_source = "llm"
            reason = "llm_enriched"
        except Exception as exc:
            final_result = _apply_deterministic_fallback(finalized, base_label_count=base_label_count)
            disambiguator_source = "fallback"
            fallback_reason = type(exc).__name__
            reason = f"llm_fallback:{type(exc).__name__}"
        finally:
            llm_latency_ms = round((time.perf_counter() - started_at) * 1000, 3)
    else:
        llm_latency_ms = None
        if llm_disambiguation_enabled():
            reason = "no_duplicate_label_after_heuristic"
        else:
            reason = "flag_off"

    if not llm_attempted or disambiguator_source != "llm":
        final_result = _apply_deterministic_fallback(final_result, base_label_count=base_label_count)

    duplicate_before = sum(count for count in base_label_count.values() if count > 1)
    rendered_label_count = _rendered_label_counts(final_result)
    duplicate_after = sum(count for count in rendered_label_count.values() if count > 1)
    try:
        event_payload: Dict[str, Any] = {
            "duplicate_before": duplicate_before,
            "duplicate_after": duplicate_after,
            "disambiguator_source": disambiguator_source,
            "llm_attempted": llm_attempted,
            "reason": reason,
        }
        if fallback_reason:
            event_payload["fallback_reason"] = fallback_reason
        if llm_latency_ms is not None:
            event_payload["llm_latency_ms"] = llm_latency_ms
        log_event("DISAMBIGUATION.LABEL.ENRICHED", **event_payload)
    except Exception:
        pass

    return final_result


def enrich_candidate_labels(
    *,
    candidates: List[Any],
    view_state: Optional[ConversationViewState],
) -> List[Tuple[Any, Dict[str, Any], Optional[str]]]:
    """Backward-compatible Scope A entry point."""
    return enrich_candidate_labels_for_summary(
        question="",
        candidates=candidates,
        view_state_summary=summarize_view_state_for_labeler(view_state),
    )
