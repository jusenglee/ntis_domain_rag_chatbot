"""Phase 2: 7-agent 재설계용 SessionState.

기존 SessionMemory.current_context는 discriminated union으로 한 번에 하나의 컨텍스트만 보존한다.
그래서 detail follow-up이 발생하면 직전 turn의 SubjectQueryContext가 PublishedManifestContext로
덮어쓰이며 subject 연속성이 끊기는 회귀가 있었다.

본 모듈은 세 영역을 **명시적으로 평행 저장**한다:

    current_subject    : 사람/기관 anchor (refine follow-up용)
    published_manifest : 직전 turn 발행 매니페스트 (ordinal/title follow-up용)
    focused_detail    : 가장 최근 본 단일 대상 (자식 엔티티 follow-up용)

세 영역은 서로 독립이며 한 영역의 갱신이 다른 영역을 절대 지우지 않는다. 따라서:
    - 신동구 검색 결과 manifest 발행 → current_subject=신동구, published_manifest=10건
    - "10번 항목 상세" → focused_detail=10번 item, **current_subject/published_manifest 그대로 유지**
    - 사용자가 다시 "그 사람의 다른 활동" → current_subject(신동구)에서 복원 가능

기존 SessionMemory와의 호환은 SessionStateAdapter가 양방향 변환을 제공한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from apps.conversation.session_memory import (
    DetailAnchorContext,
    EmptyContext,
    PublishedManifestContext,
    SessionMemory,
    SubjectQueryContext,
)
from apps.conversation.view_state import DisplaySnapshot, FocusEntity


# ============================================================================
# Three independent context slices
# ============================================================================

class SubjectSlot(BaseModel):
    """current_subject 슬롯. 사람/기관 anchor 보존."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject_kind: str          # "people" | "org"
    subject_name: str
    subject_ids_map: Dict[str, List[str]] = Field(default_factory=dict)
    identity_status: str = "ambiguous_name_only"   # SubjectIdentityStatus 값
    affiliation_org_name: Optional[str] = None
    last_turn_id: Optional[str] = None


class ManifestSlot(BaseModel):
    """published_manifest 슬롯. 직전 turn에 사용자에게 보여진 N개 항목."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_kind: str           # "project" | "perf" | "people" | "org" | "support"
    snapshot: DisplaySnapshot
    published_turn_id: Optional[str] = None


class FocusedDetailSlot(BaseModel):
    """focused_detail 슬롯. 가장 최근 본 단일 대상."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor: FocusEntity
    focused_turn_id: Optional[str] = None


# ============================================================================
# SessionState — three slots in parallel
# ============================================================================

class SessionState(BaseModel):
    """7-agent 재설계의 세션 상태 컨테이너.

    기존 SessionMemory.current_context는 무엇이든 하나만 들 수 있지만, SessionState는
    세 슬롯을 평행 저장한다. 한 슬롯의 갱신이 다른 슬롯을 지우지 않는다.

    사용 흐름 (예):
        - DialogueAgent: SessionState.published_manifest로 manifest_rank 해소 가능 여부 판정
        - EntityResolver: SessionState.current_subject로 refine 시 subject 복원
        - SearchPlanner: SessionState.focused_detail로 자식 엔티티 follow-up
        - save_session: 세 슬롯 모두 유지 또는 갱신
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    conversation_id: str = ""

    current_subject: Optional[SubjectSlot] = None
    published_manifest: Optional[ManifestSlot] = None
    focused_detail: Optional[FocusedDetailSlot] = None

    # 운영용 진단 (선택)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    # ----- 편의 메서드 -----

    def has_manifest(self) -> bool:
        return self.published_manifest is not None and bool(
            getattr(self.published_manifest.snapshot, "items", None)
        )

    def has_subject(self) -> bool:
        return self.current_subject is not None and bool(self.current_subject.subject_name)

    def has_focused_detail(self) -> bool:
        return self.focused_detail is not None

    def with_subject(self, subject: Optional[SubjectSlot]) -> "SessionState":
        return self.model_copy(update={"current_subject": subject})

    def with_manifest(self, manifest: Optional[ManifestSlot]) -> "SessionState":
        return self.model_copy(update={"published_manifest": manifest})

    def with_focused_detail(self, focused: Optional[FocusedDetailSlot]) -> "SessionState":
        return self.model_copy(update={"focused_detail": focused})


# ============================================================================
# Adapter: SessionMemory ↔ SessionState
# ============================================================================

class SessionStateAdapter:
    """기존 SessionMemory와 신규 SessionState 간 변환 헬퍼.

    저장 매체(KV)는 여전히 SessionMemory를 직렬화하므로, 신규 7-agent는 로드 시 SessionState로
    변환해 작업하고 저장 직전에 SessionMemory로 다시 반영한다.
    """

    @staticmethod
    def from_session_memory(
        memory: SessionMemory,
        *,
        conversation_id: str = "",
    ) -> SessionState:
        """SessionMemory.current_context를 슬롯 단위로 분해해 SessionState를 만든다.

        ``SessionMemory`` 자체에는 ``conversation_id`` 필드가 없으므로(Phase 1 호환), 호출자가
        명시적으로 인자로 넘겨야 한다. 누락 시 빈 문자열로 유지된다.

        Mapping rule (안전한 lossless 변환):
            - SubjectQueryContext  → current_subject (+ result_manifest 있으면 published_manifest 동시에)
            - PublishedManifestContext → published_manifest
            - DetailAnchorContext  → focused_detail
            - EmptyContext / Clarification / GroupAnchor → 빈 슬롯
        """
        cc = getattr(memory, "current_context", None)
        state = SessionState(conversation_id=conversation_id)

        if isinstance(cc, SubjectQueryContext):
            slot = SubjectSlot(
                subject_kind=cc.subject_kind,
                subject_name=cc.subject_name,
                subject_ids_map=dict(cc.subject_ids_map or {}),
                identity_status=cc.identity_status or "ambiguous_name_only",
            )
            state = state.with_subject(slot)
            # SubjectQueryContext.result_manifest가 있으면 published_manifest 슬롯에도 복사
            if cc.result_manifest is not None:
                state = state.with_manifest(
                    ManifestSlot(result_kind=cc.result_kind or "project", snapshot=cc.result_manifest)
                )
        elif isinstance(cc, PublishedManifestContext):
            state = state.with_manifest(
                ManifestSlot(result_kind=cc.result_kind or "project", snapshot=cc.result_manifest)
            )
        elif isinstance(cc, DetailAnchorContext):
            state = state.with_focused_detail(FocusedDetailSlot(anchor=cc.anchor))
        # EmptyContext / 그 외는 빈 슬롯 유지

        return state

    @staticmethod
    def to_session_memory(state: SessionState, base: Optional[SessionMemory] = None) -> SessionMemory:
        """SessionState를 SessionMemory로 반영. 세 슬롯 중 우선순위를 정해 current_context로 매핑.

        우선순위 (위에서부터 채워지면 그것을 선택):
            1. current_subject가 있으면 → SubjectQueryContext (manifest가 있으면 result_manifest로 함께)
            2. focused_detail이 있으면 → DetailAnchorContext
            3. published_manifest만 있으면 → PublishedManifestContext
            4. 아무것도 없으면 → EmptyContext

        Notes:
            기존 SessionMemory의 discriminated union 구조 때문에 동시 저장이 불가능하므로 우선순위로
            압축한다. 다만 SubjectQueryContext는 result_manifest 필드를 가질 수 있어 subject + manifest
            동시 보존 가능. focused_detail은 별도 turn에서 anchor 따로 사용되므로 일단 우선순위 2.
        """
        if base is None:
            # SessionMemory에는 conversation_id 필드가 없다 — 외부에서 별도로 들고 다닌다.
            base = SessionMemory()

        cc = _pick_current_context(state)
        return base.model_copy(update={"current_context": cc})


# ============================================================================
# Internal helpers
# ============================================================================

def _pick_current_context(state: SessionState):
    """세 슬롯에서 우선순위에 따라 single CurrentContext를 결정."""
    if state.current_subject is not None:
        manifest_snapshot: Optional[DisplaySnapshot] = (
            state.published_manifest.snapshot if state.published_manifest is not None else None
        )
        result_kind = (
            state.published_manifest.result_kind if state.published_manifest is not None else "project"
        )
        return SubjectQueryContext(
            subject_kind=state.current_subject.subject_kind,
            subject_name=state.current_subject.subject_name,
            subject_ids_map=dict(state.current_subject.subject_ids_map),
            identity_status=state.current_subject.identity_status,  # type: ignore[arg-type]
            result_kind=result_kind,
            result_manifest=manifest_snapshot,
        )
    if state.focused_detail is not None:
        return DetailAnchorContext(anchor=state.focused_detail.anchor)
    if state.published_manifest is not None:
        return PublishedManifestContext(
            result_kind=state.published_manifest.result_kind,
            result_manifest=state.published_manifest.snapshot,
        )
    return EmptyContext()
