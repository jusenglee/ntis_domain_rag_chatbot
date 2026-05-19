"""Phase 4: EntityResolverAgent — DialogueIntent의 힌트를 결정적 식별자로 해소.

설계 원칙 (NTIS RAG Agentic Redesign):
    - LLM 없는 deterministic 변환기.
    - **manifest_rank가 있으면 LLM의 target_hint보다 항상 우선** — manifest item의 entity_kind와 ID로
      target/axis/identifiers를 강제 결정.
    - rst_id가 있으면 forced_target='perf' (도메인 불변).
    - subject 연속성: refine_previous 의도면 SessionState.current_subject에서 복원.
    - 형식 휴리스틱(한글 검사 등)은 적용하지 않는다. 식별자 유효성은 SearchPlanner/Retrieval이
      0건으로 판정해 자연스럽게 흐른다.

기존 자체 휴리스틱(한글 사업명 거부, substring 매칭 등)은 사용하지 않는다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from apps.pipeline.agents.contracts import (
    DialogueIntent,
    EntityResolution,
    IdentityCandidate,
)
from apps.pipeline.agents.session_state import SessionState
from apps.pipeline.contracts import (
    Axis,
    FilterBundle,
    IdentifierBundle,
    SubjectAnchor,
    Target,
)


# ============================================================================
# Tag → entity kind 매핑 (manifest item.doc_type / entity_kind 보정용)
# ============================================================================

_PERF_TAGS = {
    "IRD_NAI_RI_PAPER",
    "IRD_NAI_RI_IPR",
    "IRD_NAI_RI_SW",
    "IRD_NAI_RI_NVR",
    "IRD_NAI_RI_ORGSM_INFO",
    "IRD_NAI_RI_ORGSM_RESOURCE",
    "IRD_NAI_RI_COMPOUND",
    "IRD_NAI_RI_RSCH_RPT",
    "IRD_NAI_RI_FCLT_EQUIP",
    "IRD_NAI_RI_TECH_INFO",
}
_PROJECT_TAGS = {"IRD_NAI_PJT_INFO"}
_SUPPORT_TAGS = {"QNA", "MANUAL"}


# ============================================================================
# EntityResolverAgent
# ============================================================================

class EntityResolverAgent:
    """DialogueIntent + SessionState → EntityResolution.

    호출 흐름:
        1. manifest_rank 결정적 해소 (있으면 LLM 결정 무시)
        2. identifier_hints → IdentifierBundle 그대로 (형식 휴리스틱 없음)
        3. subject 결정 (intent.subject_* 또는 refine_previous면 session.current_subject)
        4. filters 변환
        5. rst_id 있으면 forced_target='perf'
    """

    def resolve(
        self,
        *,
        intent: DialogueIntent,
        session: SessionState,
    ) -> EntityResolution:
        # ---- 1. manifest_rank 결정적 해소 ----
        manifest_info = _resolve_manifest_rank(intent=intent, session=session)
        if manifest_info is not None:
            resolution = manifest_info
            logger.info(
                f"[EntityResolver] manifest_rank={intent.manifest_rank} resolved "
                f"target={resolution.manifest_resolved_target} "
                f"axis={resolution.manifest_resolved_axis} "
                f"ids={resolution.identifiers.model_dump()}"
            )
            return resolution

        # ---- 1.5. focused_detail anaphora 해소 ----
        # ask_detail이고 identifier_hints/manifest_rank가 모두 비어있는데 session에 focused_detail이
        # 있으면 그것을 가리킨다고 본다 ("해당 항목", "이 과제", "방금 본 것" 등의 LLM 미감지 케이스).
        focused_info = _resolve_focused_detail(intent=intent, session=session)
        if focused_info is not None:
            logger.info(
                f"[EntityResolver] focused_detail resolved "
                f"target={focused_info.manifest_resolved_target} "
                f"axis={focused_info.manifest_resolved_axis} "
                f"ids={focused_info.identifiers.model_dump()}"
            )
            return focused_info

        # ---- 2. identifier_hints → IdentifierBundle (형식 검증 + 드롭 진단) ----
        identifiers, dropped_hints = _build_identifier_bundle(intent.identifier_hints)

        # ---- 3. subject 결정 ----
        subject = _resolve_subject(intent=intent, session=session)

        # ---- 4. filters ----
        filters = _build_filter_bundle(intent)

        # ---- 5. forced_target (rst_id → perf) ----
        forced_target: Optional[Target] = _resolve_forced_target(
            identifiers=identifiers,
            target_hint=intent.target_hint,
            subject_kind=subject.kind if subject else None,
        )

        # ---- 6. 모호성 처리 ----
        # 본 단계는 deterministic이라 실제 후보 lookup은 불가. 다음 케이스만 표시:
        #   - 모든 identifier_hint가 형식 검증으로 드롭되고 subject도 없음 → clarification
        #   - 그 외 모호성(동명이인 등)은 RetrievalAgent 결과 후 상위 노드가 결정
        ambiguity: List[IdentityCandidate] = []
        clarification_needed = False
        clarification_reason: Optional[str] = None
        if (
            dropped_hints
            and not identifiers.has_any()
            and subject is None
            and not intent.query.strip()
        ):
            clarification_needed = True
            clarification_reason = (
                "입력하신 식별자가 NTIS 형식과 맞지 않아 검색할 수 없습니다. "
                "정확한 과제번호(pjt_id/pjt_no) 또는 성과 ID(rst_id)를 다시 알려주실 수 있나요?"
            )

        resolution_source = _classify_resolution_source(intent, identifiers, subject)

        return EntityResolution(
            subject=subject,
            identifiers=identifiers,
            filters=filters,
            manifest_rank=None,
            manifest_resolved_target=None,
            manifest_resolved_axis=None,
            forced_target=forced_target,
            ambiguity_candidates=ambiguity,
            clarification_needed=clarification_needed,
            clarification_reason=clarification_reason,
            resolution_source=resolution_source,
            diagnostics={
                "intent_kind": intent.kind,
                "intent_target_hint": intent.target_hint,
                "had_subject_in_session": session.has_subject(),
                "had_manifest_in_session": session.has_manifest(),
                "dropped_identifier_hints": dropped_hints,
            },
        )


# ============================================================================
# manifest_rank 결정적 해소
# ============================================================================

def _resolve_manifest_rank(
    *,
    intent: DialogueIntent,
    session: SessionState,
) -> Optional[EntityResolution]:
    """intent.manifest_rank가 있으면 SessionState.published_manifest로 결정적 매핑.

    Returns:
        성공 시 완성된 EntityResolution. 매핑 불가능(rank 없음, manifest 없음, 범위 벗어남)이면 None.
    """
    rank = intent.manifest_rank
    if rank is None or rank < 1:
        return None
    if not session.has_manifest():
        logger.info(f"[EntityResolver] manifest_rank={rank} but no manifest in session; skipping")
        return None

    snapshot = session.published_manifest.snapshot
    items = snapshot.items or []
    if rank > len(items):
        logger.info(
            f"[EntityResolver] manifest_rank={rank} > visible_count={len(items)}; "
            "cannot resolve"
        )
        return None

    item = items[rank - 1]

    # entity_kind → target 결정. doc_type(tag) 기반 보정 우선.
    resolved_target = _resolve_target_from_item(item)
    # axis 결정: 우선순위 pjt_id > rst_id > pjt_no > person_no > org_id
    resolved_axis: Optional[Axis] = None
    if item.pjt_id:
        resolved_axis = "pjt_id"
    elif item.rst_id:
        resolved_axis = "rst_id"
    elif item.pjt_no:
        resolved_axis = "pjt_no"
    elif item.person_no:
        resolved_axis = "person_no"
    elif item.org_id:
        resolved_axis = "org_id"

    identifiers = IdentifierBundle(
        pjt_id=[item.pjt_id] if item.pjt_id else [],
        pjt_no=[item.pjt_no] if item.pjt_no else [],
        rst_id=[item.rst_id] if item.rst_id else [],
        person_no=[item.person_no] if item.person_no else [],
        org_id=[item.org_id] if item.org_id else [],
    )

    return EntityResolution(
        subject=None,  # detail로 좁히면 subject는 일단 비움. Phase 7(Curator)이 부모 subject 보존 처리.
        identifiers=identifiers,
        filters=_build_filter_bundle(intent),
        manifest_rank=rank,
        manifest_resolved_target=resolved_target,
        manifest_resolved_axis=resolved_axis,
        forced_target=resolved_target,
        ambiguity_candidates=[],
        clarification_needed=False,
        clarification_reason=None,
        resolution_source="manifest",
        diagnostics={
            "manifest_item_title": item.title_text,
            "manifest_item_doc_type": item.doc_type,
            "manifest_item_entity_kind": item.entity_kind,
        },
    )


# ============================================================================
# focused_detail 결정적 해소 (anaphora resolution)
# ============================================================================

def _resolve_focused_detail(
    *,
    intent: DialogueIntent,
    session: SessionState,
) -> Optional[EntityResolution]:
    """직전 turn에서 발행된 focused_detail을 가리키는 anaphora 해소.

    조건:
        - intent.kind == "ask_detail" (또는 action_hint == "detail")
        - identifier_hints 비어있음
        - manifest_rank None
        - session.focused_detail 존재

    이 모든 조건을 만족하면 "해당 항목", "이 과제", "방금 본 것" 같은 anaphora로 해석해
    focused_detail anchor의 식별자로 검색을 좁힌다.
    """
    if not session.has_focused_detail():
        return None
    if intent.manifest_rank is not None:
        return None  # manifest_rank가 우선
    if intent.identifier_hints:
        # 사용자가 새 식별자를 명시한 경우는 anaphora 아님
        for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
            if intent.identifier_hints.get(axis):
                return None
    is_detail_intent = (
        intent.kind == "ask_detail" or intent.action_hint == "detail"
    )
    if not is_detail_intent:
        return None

    anchor = session.focused_detail.anchor
    # axis 우선순위: pjt_id > rst_id > pjt_no > person_no > org_id
    resolved_axis: Optional[Axis] = None
    if anchor.pjt_id:
        resolved_axis = "pjt_id"
    elif anchor.rst_id:
        resolved_axis = "rst_id"
    elif anchor.pjt_no:
        resolved_axis = "pjt_no"
    elif anchor.person_no:
        resolved_axis = "person_no"
    elif anchor.org_id:
        resolved_axis = "org_id"
    if resolved_axis is None:
        return None  # anchor에 사용 가능한 식별자가 없음

    identifiers = IdentifierBundle(
        pjt_id=[anchor.pjt_id] if anchor.pjt_id else [],
        pjt_no=[anchor.pjt_no] if anchor.pjt_no else [],
        rst_id=[anchor.rst_id] if anchor.rst_id else [],
        person_no=[anchor.person_no] if anchor.person_no else [],
        org_id=[anchor.org_id] if anchor.org_id else [],
    )
    # target 결정: anchor.kind 우선, doc_type(tag) 보조
    resolved_target = _resolve_target_from_anchor(anchor)
    return EntityResolution(
        subject=None,
        identifiers=identifiers,
        filters=_build_filter_bundle(intent),
        manifest_rank=None,
        manifest_resolved_target=resolved_target,
        manifest_resolved_axis=resolved_axis,
        forced_target=resolved_target,
        ambiguity_candidates=[],
        clarification_needed=False,
        clarification_reason=None,
        resolution_source="focused_detail",
        diagnostics={
            "focused_detail_kind": anchor.kind,
            "focused_detail_doc_type": anchor.doc_type,
            "focused_detail_title": anchor.title_text,
            "focused_detail_display_rank": anchor.display_rank,
        },
    )


def _resolve_target_from_anchor(anchor) -> Target:
    """FocusEntity의 doc_type(tag) 또는 kind로부터 target 결정.

    NTIS tag를 우선적으로 본다. 미확정 시 anchor.kind fallback.
    """
    tag = (anchor.doc_type or "").strip()
    if tag in _PROJECT_TAGS:
        return "project"
    if tag in _PERF_TAGS:
        return "perf"
    if tag in _SUPPORT_TAGS:
        return "support"
    kind = (anchor.kind or "").strip().lower()
    if kind == "people":
        return "people"
    if kind == "org":
        return "org"
    if kind == "perf":
        return "perf"
    if kind == "support":
        return "support"
    return "project"


def _resolve_target_from_item(item) -> Target:
    """DisplayItem의 doc_type(tag) 또는 entity_kind로부터 target 결정.

    실제 NTIS payload tag를 우선적으로 본다 (LLM이 entity_kind를 잘못 분류해도 tag가 진실원).
    """
    tag = (item.doc_type or "").strip()
    if tag in _PROJECT_TAGS:
        return "project"
    if tag in _PERF_TAGS:
        return "perf"
    if tag in _SUPPORT_TAGS:
        return "support"
    # tag 미확정 시 entity_kind fallback
    kind = (item.entity_kind or "").strip().lower()
    if kind in {"project", "perf", "people", "org", "support"}:
        return kind  # type: ignore[return-value]
    return "project"


# ============================================================================
# identifier_hints → IdentifierBundle
# ============================================================================

# 안전한 식별자 형식 검증 — 정규식 사용을 최소화하기 위해 char class 기반 검사.
# NTIS 모든 식별자는 ASCII 기호/숫자/영문자로 구성된다. 한글 등 비ASCII 거부.
_AXIS_CHAR_ALLOWED: Dict[str, frozenset[str]] = {
    "pjt_id": frozenset("0123456789"),
    "pjt_no": frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_/"),
    "rst_id": frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"),
    "person_no": frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-_"),
    "org_id": frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"),
}
_AXIS_LEN_MIN = 3
_AXIS_LEN_MAX = 40


def _is_valid_identifier(axis: str, value: str) -> bool:
    """축별 허용 문자집합과 길이로 안전 검증. 정규식 미사용."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if not v or not (_AXIS_LEN_MIN <= len(v) <= _AXIS_LEN_MAX):
        return False
    if not v.isascii():
        return False
    allowed = _AXIS_CHAR_ALLOWED.get(axis)
    if allowed is None:
        return True
    return all(ch in allowed for ch in v)


def _build_identifier_bundle(
    hints: Dict[str, List[str]],
) -> tuple[IdentifierBundle, Dict[str, List[str]]]:
    """LLM이 채운 identifier_hints를 형식 검증 후 IdentifierBundle로 변환.

    안전 검증 정책 (정규식 최소 사용):
        - 비ASCII(한글 등) 거부 — NTIS 모든 식별자는 ASCII만
        - 길이 3..40자 외 거부
        - 축별 허용 문자집합 (`_AXIS_CHAR_ALLOWED`)으로 char class 검사
        - dedup/trim은 IdentifierBundle 모델 검증에 위임

    Returns:
        (IdentifierBundle, dropped_hints) — dropped_hints는 진단·관측용
        ``{"pjt_no": ["NTIS 국가R&D구축"], ...}`` 형태.
    """
    raw = hints or {}
    kept: Dict[str, List[str]] = {axis: [] for axis in _AXIS_CHAR_ALLOWED}
    dropped: Dict[str, List[str]] = {}
    for axis, values in raw.items():
        if axis not in _AXIS_CHAR_ALLOWED:
            # 알 수 없는 축은 무시 (계약 위반 방지).
            if values:
                dropped.setdefault(axis, []).extend(str(v) for v in values)
            continue
        for v in values or []:
            if _is_valid_identifier(axis, v):
                kept[axis].append(str(v).strip())
            else:
                dropped.setdefault(axis, []).append(str(v))
    return (
        IdentifierBundle(
            pjt_id=kept["pjt_id"],
            pjt_no=kept["pjt_no"],
            rst_id=kept["rst_id"],
            person_no=kept["person_no"],
            org_id=kept["org_id"],
        ),
        dropped,
    )


# ============================================================================
# Subject 결정
# ============================================================================

def _resolve_subject(
    *,
    intent: DialogueIntent,
    session: SessionState,
) -> Optional[SubjectAnchor]:
    """intent.subject_* 또는 refine_previous일 때 session.current_subject 복원."""
    # intent에 subject가 명시되어 있으면 그것을 우선
    if intent.subject_name:
        kind = intent.subject_kind or ("org" if "기관" in (intent.subject_name or "") else "people")
        identity_status = (
            "resolved_with_org" if intent.subject_affiliation_hint else "ambiguous_name_only"
        )
        return SubjectAnchor(
            kind=kind,
            display_name=intent.subject_name,
            affiliation_org_name=intent.subject_affiliation_hint,
            identity_status=identity_status,
        )

    # refine_previous면 session.current_subject에서 복원
    if intent.kind == "refine_previous" and session.has_subject():
        sub = session.current_subject
        ids_map = dict(sub.subject_ids_map or {})
        person_no = (ids_map.get("person_no") or [None])[0] if ids_map.get("person_no") else None
        org_id = (ids_map.get("org_id") or [None])[0] if ids_map.get("org_id") else None
        kind = sub.subject_kind if sub.subject_kind in {"people", "org"} else "people"
        return SubjectAnchor(
            kind=kind,  # type: ignore[arg-type]
            display_name=sub.subject_name,
            person_no=person_no,
            org_id=org_id,
            affiliation_org_name=sub.affiliation_org_name,
            identity_status=sub.identity_status if sub.identity_status in {
                "ambiguous_name_only", "resolved_with_org", "resolved"
            } else "ambiguous_name_only",  # type: ignore[arg-type]
        )

    return None


# ============================================================================
# Filters
# ============================================================================

def _build_filter_bundle(intent: DialogueIntent) -> FilterBundle:
    """DialogueIntent의 필터 힌트 → FilterBundle."""
    perf_types: List[str] = []
    for v in intent.perf_type_hint or []:
        s = str(v).strip().upper()
        if s and s not in perf_types:
            perf_types.append(s)
    return FilterBundle(
        year_from=intent.year_from,
        year_to=intent.year_to,
        perf_type=perf_types,
    )


# ============================================================================
# Forced target (도메인 불변)
# ============================================================================

def _resolve_forced_target(
    *,
    identifiers: IdentifierBundle,
    target_hint: Optional[Target],
    subject_kind: Optional[str],
) -> Optional[Target]:
    """식별자/주체 종류 기반 target 강제 결정.

    규칙 (도메인 불변):
        - rst_id 있음 → 'perf'
        - pjt_id 또는 pjt_no 있음(+ rst_id 없음) → 'project'
        - subject_kind=people → 'people'
        - subject_kind=org → 'org'
        - 그 외 → target_hint 유지 (None 포함)
    """
    if identifiers.rst_id:
        return "perf"
    if identifiers.pjt_id or identifiers.pjt_no:
        return "project"
    if subject_kind == "people":
        return "people"
    if subject_kind == "org":
        return "org"
    return target_hint


# ============================================================================
# Resolution source 분류 (관측용)
# ============================================================================

def _classify_resolution_source(
    intent: DialogueIntent,
    identifiers: IdentifierBundle,
    subject: Optional[SubjectAnchor],
) -> str:
    if identifiers.has_any():
        return "identifier_literal"
    if subject is not None and intent.kind == "refine_previous":
        return "session_subject"
    if subject is not None:
        return "llm_hint_subject"
    return "llm_hint_query_only"
