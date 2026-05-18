"""3계층 파이프라인의 모든 계약(Data Contract) 모델.

ADR-0018에서 정의한 SearchTask / SearchResult / FinalAnswer 등 권한분리 경계에서
오가는 모든 자료구조를 한 파일에 모았다. 텍스트로 평탄화하지 않고 구조화 식별자를
끝까지 들고 가는 단일 캐리어가 본 모듈의 핵심 목적이다.

본 패키지의 절대 원칙:
    1. L1(JudgmentAgent 의도)은 SearchTask에 1급 필드로 박혀 그대로 SearchAgent에 전달된다.
    2. 식별자는 텍스트 보조 신호(retrieval_query)로 평탄화되지 않는다.
    3. 모든 모델은 frozen Pydantic으로 검증되며, 미리 정의된 enum/literal 화이트리스트를 강제한다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# 0. 공통 enum / literal
# ============================================================================

Action = Literal["list", "detail", "stats", "topic", "download"]
Target = Literal["project", "perf", "people", "org", "support"]
Axis = Literal["pjt_id", "pjt_no", "rst_id", "person_no", "org_id"]

SearchStrategy = Literal[
    "exact_lookup",       # ids_map의 식별자로 by-id 조회
    "subject_anchor",     # subject(person/org) anchor 강제 주입 후 hybrid
    "hybrid_search",      # 일반 hybrid 검색
    "detail_anchor",      # 단일 대상 상세 (limit=1)
]

SearchResultStatus = Literal["single", "multiple", "empty", "error"]

# Qdrant 컬렉션 화이트리스트.
#   - ntis_project_v1: 과제(IRD_NAI_PJT_INFO)
#   - ntis_perf_v1: 성과(논문/특허/SW/장비/보고서/기술요약 등 IRD_NAI_RI_*)
#   - ntis_supports: QNA·MANUAL (지원/도움말)
Collection = Literal["ntis_project_v1", "ntis_perf_v1", "ntis_supports"]

# 식별자 우선순위 (높을수록 우선). exact_lookup 분기 결정에 사용.
AXIS_PRIORITY: Dict[str, int] = {"pjt_id": 100, "rst_id": 90, "pjt_no": 80, "person_no": 70, "org_id": 60}


# ============================================================================
# 1. JudgmentAgent → SearchAgent 계약
# ============================================================================

class SubjectAnchor(BaseModel):
    """사람/기관 의도일 때 SearchTask가 들고 가는 구조화 anchor.

    JudgmentAgent가 identity resolution까지 끝낸 결과를 SearchAgent로 그대로 전달하는 통로.
    텍스트 이름만으로 평탄화하지 않고 person_no/org_id를 1급 필드로 유지한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["people", "org"]
    display_name: str = Field(..., min_length=1)
    person_no: Optional[str] = None        # kind=people일 때
    org_id: Optional[str] = None           # kind=org일 때
    org_code: Optional[str] = None
    biz_no: Optional[str] = None
    affiliation_org_name: Optional[str] = None  # 사람일 때 소속 disambiguation
    identity_status: Literal["ambiguous_name_only", "resolved_with_org", "resolved"] = "ambiguous_name_only"

    def primary_id(self) -> Optional[str]:
        """anchor의 1순위 ID를 돌려준다 (없으면 None)."""
        if self.kind == "people":
            return self.person_no
        return self.org_id or self.org_code or self.biz_no


class IdentifierBundle(BaseModel):
    """SearchTask가 들고 가는 exact-lookup 가능한 식별자 목록.

    list 자료형을 사용해 동일 축에서 여러 후보를 동시에 들고 갈 수 있게 한다.
    각 list는 dedup된 비공백 문자열만 보존한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    pjt_id: List[str] = Field(default_factory=list)
    pjt_no: List[str] = Field(default_factory=list)
    rst_id: List[str] = Field(default_factory=list)
    person_no: List[str] = Field(default_factory=list)
    org_id: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_dedup(self) -> "IdentifierBundle":
        for field_name in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
            values = getattr(self, field_name)
            cleaned = []
            seen = set()
            for v in values:
                s = str(v).strip()
                if not s or s in seen:
                    continue
                cleaned.append(s)
                seen.add(s)
            object.__setattr__(self, field_name, cleaned)
        return self

    def best_axis(self) -> Optional[Axis]:
        """우선순위가 가장 높은 채워진 축을 돌려준다."""
        for axis, _ in sorted(AXIS_PRIORITY.items(), key=lambda kv: -kv[1]):
            if getattr(self, axis):
                return axis  # type: ignore[return-value]
        return None

    def has_any(self) -> bool:
        return any((self.pjt_id, self.pjt_no, self.rst_id, self.person_no, self.org_id))


class FilterBundle(BaseModel):
    """SearchTask가 들고 가는 도메인 필터.

    year_from/year_to, lead_org_name 등 의미별로 1급 필드를 두고, 자유로운 dict로 평탄화하지 않는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    year_from: Optional[int] = Field(default=None, ge=1900, le=2100)
    year_to: Optional[int] = Field(default=None, ge=1900, le=2100)
    lead_org_name: List[str] = Field(default_factory=list)
    participant_org_name: List[str] = Field(default_factory=list)
    perf_type: List[str] = Field(default_factory=list)   # PAPER, PATENT, SOFTWARE, REPORT, EQUIPMENT 등
    domain_keywords: List[str] = Field(default_factory=list)  # 자유 키워드 필터

    @model_validator(mode="after")
    def _validate_year_range(self) -> "FilterBundle":
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must be <= year_to")
        return self

    def has_any(self) -> bool:
        return bool(
            self.year_from
            or self.year_to
            or self.lead_org_name
            or self.participant_org_name
            or self.perf_type
            or self.domain_keywords
        )


class SearchTask(BaseModel):
    """JudgmentAgent가 SearchAgent에 넘기는 단일 캐리어 계약.

    이 객체는 텍스트(`retrieval_query`)에 의존하지 않고 구조화 필드로 의도를 보존한다.
    SearchAgent는 본 객체만 읽고 실행하며, JudgmentAgent의 내부 상태를 별도로 참조하지 않는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["v1"] = "v1"

    # L1 의도
    action: Action
    target: Target
    axis: Optional[Axis] = None

    # 구조화 식별자/필터
    subject: Optional[SubjectAnchor] = None
    identifiers: IdentifierBundle = Field(default_factory=IdentifierBundle)
    filters: FilterBundle = Field(default_factory=FilterBundle)

    # 검색 전략 권고
    strategy: SearchStrategy
    collections: List[Collection]
    limit: int = Field(default=10, ge=1, le=50)
    display_limit: int = Field(default=10, ge=1, le=50)

    # 자연어 보조 (드리프트 비교 대상 아님)
    retrieval_query: str = Field(default="", max_length=2000)

    # 추적
    request_id: str
    turn_id: str
    judgment_reason: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _validate_consistency(self) -> "SearchTask":
        # detail 액션은 limit=1로 강제
        if self.action == "detail":
            object.__setattr__(self, "limit", 1)
            object.__setattr__(self, "display_limit", 1)

        # exact_lookup 전략은 identifiers가 있어야 함
        if self.strategy == "exact_lookup" and not self.identifiers.has_any():
            raise ValueError("strategy=exact_lookup requires non-empty identifiers")

        # subject_anchor 전략은 subject가 있어야 함
        if self.strategy == "subject_anchor" and self.subject is None:
            raise ValueError("strategy=subject_anchor requires subject")

        # display_limit <= limit
        if self.display_limit > self.limit:
            object.__setattr__(self, "display_limit", self.limit)

        # collections 비어있지 않음
        if not self.collections:
            raise ValueError("collections must not be empty")

        return self


# ============================================================================
# 2. SearchAgent 출력 계약
# ============================================================================

class CanonicalEvidence(BaseModel):
    """검색 결과 1건을 정규화한 단위.

    apps.evidence.canonical_evidence.build_canonical_evidence와 동일한 의미를 갖되,
    Pydantic 모델로 감싸 파이프라인 검증을 가능하게 했다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    identity: str                                  # canonical 고유 식별
    source_type: str                               # project/perf/people 등
    tag: Optional[str] = None                      # DataTag 원본 (IRD_NAI_PJT_INFO 등)
    ids: Dict[str, str] = Field(default_factory=dict)
    title: str = ""
    summary: str = ""
    facts: Dict[str, Any] = Field(default_factory=dict)
    roles: Dict[str, List[str]] = Field(default_factory=dict)
    child_entities: List[Dict[str, Any]] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0
    snapshot_rank: int = 0                         # 원본 retrieval 순위 (ADR-0017)


class SearchResult(BaseModel):
    """SearchAgent가 SearchTask를 실행한 결과.

    status에 따라 단일/복수/무결과/오류로 결정되고, 각 상태에 맞는 후속 분기를 FinalGuard나
    JudgmentAgent refine 단계가 결정한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: SearchResultStatus
    evidences: List[CanonicalEvidence] = Field(default_factory=list)
    total_hits: int = 0                            # 컬렉션 전체 hit 수 (display_limit 이전)
    error_code: Optional[str] = None               # status=error일 때
    error_detail: Optional[str] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)  # latency, retrieval meta 등

    @model_validator(mode="after")
    def _validate_status(self) -> "SearchResult":
        # status 의미 (ADR-0018):
        #   - empty   : evidence 0건
        #   - single  : "답변 가능 상태". evidence 1건 이상. detail 액션에선 정확히 1건,
        #               list/stats/topic에선 display_limit까지.
        #   - multiple: 식별자 단일화가 안 됐고 refine이 필요한 상태. evidence는 후보군 그대로.
        #   - error   : 시스템 오류.
        if self.status == "error" and not self.error_code:
            raise ValueError("status=error requires error_code")
        if self.status == "single" and len(self.evidences) < 1:
            raise ValueError("status=single requires at least one evidence")
        if self.status == "empty" and len(self.evidences) != 0:
            raise ValueError("status=empty requires no evidence")
        return self


# ============================================================================
# 3. JudgmentAgent 결정 계약
# ============================================================================

class DirectAnswer(BaseModel):
    """JudgmentAgent가 검색 없이 즉시 답할 수 있다고 판단한 경우."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    reason: str = ""


class Clarification(BaseModel):
    """JudgmentAgent가 사용자에게 추가 질문이 필요하다고 판단한 경우."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str
    options: List[str] = Field(default_factory=list)
    reason: str = ""


class JudgmentDecision(BaseModel):
    """JudgmentAgent의 최종 결정 (3가지 중 정확히 하나).

    하나만 채워야 한다. 두 개 이상 채우면 검증 실패.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    search_task: Optional[SearchTask] = None
    direct_answer: Optional[DirectAnswer] = None
    clarification: Optional[Clarification] = None

    @model_validator(mode="after")
    def _validate_exactly_one(self) -> "JudgmentDecision":
        filled = [bool(self.search_task), bool(self.direct_answer), bool(self.clarification)]
        if sum(filled) != 1:
            raise ValueError(
                "JudgmentDecision must have exactly one of {search_task, direct_answer, clarification}"
            )
        return self

    @property
    def kind(self) -> Literal["search", "direct_answer", "clarification"]:
        if self.search_task:
            return "search"
        if self.direct_answer:
            return "direct_answer"
        return "clarification"


# ============================================================================
# 4. LLM 생성 / FinalGuard 계약
# ============================================================================

class GeneratedAnswer(BaseModel):
    """단일 LLM 생성 결과. FinalGuard 입력의 한 부분."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    model_key: str
    latency_ms: float = 0.0
    truncated: bool = False
    stream_metrics: Dict[str, Any] = Field(default_factory=dict)


class ReferenceItem(BaseModel):
    """ADR-0017의 published_rank 기반 출처 항목.

    프론트에 노출되는 ReferenceItem.id는 tag에 따라 의미가 고정된다:
      - tag=IRD_NAI_PJT_INFO         → id = pjt_id
      - tag=IRD_NAI_RI_PAPER 등 성과 → id = rst_id
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    published_rank: int = Field(..., ge=1)
    source_snapshot_rank: int = Field(..., ge=1)
    tag: str
    id: str
    title: str
    id_axis: Literal["pjt_id", "pjt_no", "rst_id", "person_no", "org_id"]


class ReferenceManifest(BaseModel):
    """FinalGuard가 발행하는 답변 기준 reference manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: List[ReferenceItem] = Field(default_factory=list)
    total_visible: int = 0
    publication_status: Literal["published", "blocked"] = "published"
    block_reason: Optional[str] = None


class FinalAnswer(BaseModel):
    """FinalGuard의 최종 결정. 답변 발행/명확화/내부오류 중 하나."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["publish", "clarify", "internal_error"]
    text: Optional[str] = None
    reference_manifest: Optional[ReferenceManifest] = None
    clarification: Optional[Clarification] = None
    reasoning: str = ""
    diagnostics: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_decision_payload(self) -> "FinalAnswer":
        if self.decision == "publish":
            if not self.text:
                raise ValueError("decision=publish requires text")
            if not self.reference_manifest:
                raise ValueError("decision=publish requires reference_manifest")
        if self.decision == "clarify" and not self.clarification:
            raise ValueError("decision=clarify requires clarification")
        return self


# ============================================================================
# 5. 헬퍼: build_search_task
# ============================================================================

def build_search_task(
    *,
    action: Action,
    target: Target,
    request_id: str,
    turn_id: str,
    judgment_reason: str = "",
    subject: Optional[SubjectAnchor] = None,
    identifiers: Optional[IdentifierBundle] = None,
    filters: Optional[FilterBundle] = None,
    retrieval_query: str = "",
    axis_hint: Optional[Axis] = None,
    limit: int = 10,
    display_limit: int = 10,
    collections: Optional[Sequence[Collection]] = None,
) -> SearchTask:
    """JudgmentAgent가 쓰는 SearchTask 빌더.

    전략(SearchStrategy)과 collections는 입력으로부터 자동 결정한다.
    """

    ids = identifiers or IdentifierBundle()
    flt = filters or FilterBundle()

    # 전략 결정
    if ids.has_any() and action == "detail":
        strategy: SearchStrategy = "exact_lookup"
    elif ids.has_any():
        strategy = "exact_lookup"
    elif subject is not None:
        strategy = "subject_anchor"
    else:
        strategy = "detail_anchor" if action == "detail" else "hybrid_search"

    # axis 결정
    axis: Optional[Axis] = axis_hint or ids.best_axis()

    # collections 결정 (실제 NTIS 컬렉션 매핑)
    if collections is not None:
        cols = list(collections)
    elif target == "project":
        cols = ["ntis_project_v1"]
    elif target == "perf":
        cols = ["ntis_perf_v1"]
    elif target == "people":
        # 모든 데이터에 참여연구자(prtcp_mp)가 들어있으므로 project + perf 두 컬렉션 모두 검색
        cols = ["ntis_project_v1", "ntis_perf_v1"]
    elif target == "org":
        # 수행기관(org_nm) / 참여기관(prtcp_org)도 두 컬렉션 모두 보유
        cols = ["ntis_project_v1", "ntis_perf_v1"]
    elif target == "support":
        # QNA / MANUAL
        cols = ["ntis_supports"]
    else:
        cols = ["ntis_project_v1"]

    return SearchTask(
        action=action,
        target=target,
        axis=axis,
        subject=subject,
        identifiers=ids,
        filters=flt,
        strategy=strategy,
        collections=cols,  # type: ignore[arg-type]
        limit=limit,
        display_limit=display_limit,
        retrieval_query=retrieval_query.strip(),
        request_id=request_id,
        turn_id=turn_id,
        judgment_reason=judgment_reason.strip(),
    )
