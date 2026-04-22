"""ADR-0014 Scope A — Disambiguation 후보 라벨 enrichment.

동명(同名) 후보가 같은 entity_kind로 남는 경우, 사용자/LLM이 둘을 구분할
수 있도록 각 TurnCandidate에 대해 1줄 disambiguator와 aux dict를 만든다.

설계 원칙
---------
- 이 모듈은 candidate/ view_state에서 이미 채워진 값만 사용한다. **새로운
  ID를 생성하지 않는다** (ADR-0013/ADR-0014 non-negotiables).
- raw payload는 주입하지 않는다.
- heuristic disambiguator가 실패하고, flag로 LLM enrichment가 켜져 있을
  때에만 LLM을 호출한다. LLM 실패/timeout은 fail-open (disambiguator=None).
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

import os
from typing import Any, Dict, List, Optional, Tuple

from apps.conversation.view_state import (
    ConversationViewState,
    RecentMentionRecord,
    SubjectIndexEntry,
)


__all__ = [
    "enrich_candidate_labels",
    "render_label",
    "llm_disambiguation_enabled",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def llm_disambiguation_enabled() -> bool:
    """ADR-0014 Scope A의 LLM enrichment hook이 켜져 있는지 판정."""
    return str(os.getenv("LLM_DISAMBIGUATION_LABEL_ENABLED", "0")).strip().lower() in (
        "1",
        "true",
        "yes",
        "y",
    )


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
    subject_entry: Optional[SubjectIndexEntry],
) -> Dict[str, Any]:
    aux: Dict[str, Any] = {}
    if subject_entry is not None:
        affiliation = _norm(subject_entry.affiliation)
        role = _norm(subject_entry.role)
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
    mentions: List[RecentMentionRecord],
) -> Dict[str, Any]:
    aux: Dict[str, Any] = {}
    years = [m.year for m in mentions if _norm(m.year)]
    lead_orgs = [m.lead_org for m in mentions if _norm(m.lead_org)]
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
    subject_entry: Optional[SubjectIndexEntry],
    mentions: List[RecentMentionRecord],
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


def enrich_candidate_labels(
    *,
    candidates: List[Any],
    view_state: Optional[ConversationViewState],
) -> List[Tuple[Any, Dict[str, Any], Optional[str]]]:
    """각 candidate에 대해 (candidate, aux, disambiguator)를 반환.

    - 기본 경로는 heuristic이다.
    - 동일 base label (`display_name` + `entity_kind`)을 가진 candidate가
      2개 이상 남고, LLM flag가 켜져 있으면 LLM enrichment를 시도한다.
    - LLM 경로는 이 ADR 단계에서는 placeholder다 (후속 ADR-0015에서
      prompt/호출 구체화). 현재는 heuristic만 동작.
    """
    subject_index = _subject_index_by_name(view_state)
    recent_index = _recent_mentions_by_name(view_state)

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

    # 3rd pass — 동명 후보 중 여전히 disambiguator가 None이거나 중복이면
    # ID suffix로 최후 fallback을 채운다. (LLM 경로는 후속에서 여기 훅)
    label_signature: Dict[Tuple[str, str, str], int] = {}
    final_with_fallback: List[Tuple[Any, Dict[str, Any], Optional[str]]] = []
    for candidate, aux, disamb in finalized:
        name_key = _norm_lower(getattr(candidate, "display_name", ""))
        kind_key = _norm_lower(getattr(candidate, "entity_kind", ""))
        if base_label_count.get((name_key, kind_key), 0) <= 1:
            final_with_fallback.append((candidate, aux, disamb))
            continue
        effective = disamb
        key = (name_key, kind_key, _norm(effective))
        while effective is None or label_signature.get(key, 0) > 0:
            # 같은 disambiguator가 이미 쓰였으면 ID 접미로 보강
            fallback_parts: List[str] = []
            if effective:
                fallback_parts.append(effective)
            for id_key in ("person_no_suffix", "pjt_id_suffix", "pjt_no_suffix", "org_id_suffix", "biz_no_suffix"):
                suffix = aux.get(id_key)
                if suffix:
                    fallback_parts.append(f"{id_key.removesuffix('_suffix')} ···{suffix}")
                    break
            if not fallback_parts:
                # 그래도 못 찾으면 candidate_id 자체를 마지막 수단으로
                cand_id = _norm(getattr(candidate, "candidate_id", ""))
                if cand_id:
                    fallback_parts.append(f"id ···{_id_suffix(cand_id)}")
            new_disamb = " · ".join(fallback_parts) if fallback_parts else None
            if new_disamb == effective:
                break  # 더 이상 구분 불가 — 무한 루프 방지
            effective = new_disamb
            key = (name_key, kind_key, _norm(effective))
        label_signature[key] = label_signature.get(key, 0) + 1
        final_with_fallback.append((candidate, aux, effective))

    # LLM enrichment hook — flag on일 때 아직도 None인 동명 후보가 있으면 시도
    if llm_disambiguation_enabled():
        # placeholder: ADR-0015에서 구체 prompt/호출을 붙인다. 현재는 no-op.
        pass

    return final_with_fallback
