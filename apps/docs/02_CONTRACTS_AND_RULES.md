# 02 실행 계약과 전략 규칙 (Contracts & Rules)

> **2026-05-19 갱신**: [ADR-0019](./ADR/ADR-0019_Seven_Agent_Agentic_Redesign.md) 7-agent
> 재설계 적용. 진실원은 다음 두 모듈이다:
>
> - `apps/pipeline/agents/contracts.py` — 신규 6개 계약 (`DialogueIntent`,
>   `EntityResolution`, `SearchPlan`, `EvidenceBundle`, `AnswerDraft`, `GuardDecision`).
>   모두 `frozen=True, extra="forbid"`.
> - `apps/pipeline/contracts.py` — 재사용 계약 (`SearchTask`, `CanonicalEvidence`,
>   `SearchResult`, `IdentifierBundle`, `SubjectAnchor`, `FilterBundle`, `ReferenceItem`,
>   `ReferenceManifest`).
>
> 본문에서 언급되는 "JudgmentAgent / SearchAgent / FinalGuard" 경계는 7-agent로 재분해되었다.
> 정확한 호출 경계는 ADR-0019의 Agent 표를 참조.
>
> ⚠️ 폐기된 모델: `JudgmentDecision`, `DirectAnswer`, `Clarification`(contracts.py 모델),
> `GeneratedAnswer`, `FinalAnswer`. 후속 모델은 README.md "더 이상 사용하지 않는 키워드"
> 섹션 또는 ADR-0019 참조.

---

## 1. 단일 캐리어 계약: `SearchTask`

`SearchTask`는 JudgmentAgent → SearchAgent 경계에서 오가는 **유일한 명령**이다.
이전 시스템의 `IntentContract`(L1) + `IntentPayloadV3`(L2) + `QuestionAnalysisV3`(L2 컴파일)의
3중 표현을 통합했다.

```python
class SearchTask(BaseModel):
    schema_version: Literal["v1"]
    action: Literal["list", "detail", "stats", "topic", "download"]
    target: Literal["project", "perf", "people", "org", "support"]
    axis: Optional[Literal["pjt_id", "pjt_no", "rst_id", "person_no", "org_id"]]

    subject: Optional[SubjectAnchor]      # 사람/기관 anchor (kind/person_no/org_id/affiliation)
    identifiers: IdentifierBundle         # by-axis lookup 후보 (각 축 list)
    filters: FilterBundle                 # year_from/year_to, lead_org, perf_type 등

    strategy: Literal["exact_lookup", "subject_anchor", "hybrid_search", "detail_anchor"]
    collections: list[Literal["ntis_project_v1", "ntis_perf_v1"]]
    limit: int
    display_limit: int

    retrieval_query: str                  # 보조 신호 (drift 비교 대상 아님)
    request_id: str
    turn_id: str
    judgment_reason: str
```

### 절대 규칙

1. **L1 의도 보존**: `subject`/`identifiers`/`filters`는 텍스트로 평탄화되지 않는다.
   `retrieval_query`가 비어 있어도 anchor만으로 SearchAgent는 동작한다.
2. **불변(frozen)**: SearchTask는 생성 후 수정 불가. refine은 새 SearchTask를 만들어 대체.
3. **action=detail 강제 정규화**: `limit=1, display_limit=1`. 다대 lookup 결과는 SearchAgent가
   status=multiple로 보고하고 FinalGuard가 clarify로 닫는다.
4. **collections 화이트리스트**: 현재 시스템은 `ntis_project_v1`, `ntis_perf_v1` 두 컬렉션만
   인식한다. 신규 도메인 추가 시 본 enum을 갱신해야 한다.
5. **`strategy=exact_lookup`은 `identifiers.has_any()=True`를 요구**한다. Pydantic 검증으로 강제.
6. **`strategy=subject_anchor`는 `subject is not None`을 요구**한다.

---

## 2. 식별자 의미 (Identifier Semantics)

### `pjt_id` vs `pjt_no`
- **`pjt_id` (Instance)**: 개별 과제 고유 ID. `action=detail` 조회는 반드시 이 축 사용.
- **`pjt_no` (Group)**: 같은 번호를 가진 다년차 과제군 묶음. `stats`나 `perf` 조인 시 기본 축.
- **규칙**: 한 SearchTask 내에서 두 축을 혼용하지 않는다. `IdentifierBundle.best_axis()`는
  우선순위(pjt_id=100 > rst_id=90 > pjt_no=80 > person_no=70 > org_id=60)에 따라 1개만 선택.
- **금지**: 지원하지 않는 alias(`RJT_ID`, `project_id` 등)를 임의로 매핑하지 않는다.

### `rst_id` 접두어 (성과 식별자)
| 접두어 | 의미 | DataTag |
|--------|------|---------|
| `EQU-` | 장비 | `IRD_NAI_RI_FCLT_EQUIP` |
| `SNW-` | 소프트웨어 | `IRD_NAI_RI_SW` |
| `PTO-` | 특허 | `IRD_NAI_RI_IPR` |
| `REP-` | 보고서 | `IRD_NAI_RI_RSCH_RPT` |
| `PAP-` | 논문 | `IRD_NAI_RI_PAPER` |

JudgmentAgent의 rule-based pre-pass(`apps/pipeline/judgment_agent.py:_detect_patterns`)가 이
접두어를 인식해 `target=perf`, `axis=rst_id`, `strategy=exact_lookup`으로 즉시 컴파일한다.

### 기관 역할 분리

| 필드 | 의미 | FilterBundle 위치 |
|------|------|-------------------|
| 수행기관 | 과제를 주관하는 기관 (lead/PI) | `filters.lead_org_name` |
| 참여기관 | 참여한 모든 기관 (컨소시엄) | `filters.participant_org_name` |
| 인력 소속 | 참여 인력의 소속 기관 (사람 anchor 시) | `SubjectAnchor.affiliation_org_name` |

세 필드는 **서로 다른 Qdrant payload 경로**로 매칭된다. 텍스트로 통합하지 않는다.

---

## 3. JudgmentAgent → SearchTask 변환 규칙

### Rule-based 즉시 결정

JudgmentAgent는 다음 패턴이 보이면 LLM을 호출하지 않고 즉시 SearchTask를 만든다.

| 입력 패턴 | 출력 SearchTask |
|-----------|-----------------|
| `\d{10}` (pjt_id) | `target=project, identifiers.pjt_id=[...], axis=pjt_id, strategy=exact_lookup` |
| `K-20-L01-C07` 형식 (pjt_no) | `target=project, identifiers.pjt_no=[...], axis=pjt_no, strategy=exact_lookup` |
| `EQU-2020-...`, `SNW-...`, `PTO-...`, `REP-...`, `PAP-...` (rst_id) | `target=perf, identifiers.rst_id=[...], axis=rst_id, strategy=exact_lookup` |
| 사람이름 + 기관 cue + 활동 keyword | `target=people, subject=SubjectAnchor(...resolved_with_org), strategy=subject_anchor` |
| 직전 SubjectQueryContext + 연도/타입 refine 패턴 | `subject 복원 + filters 추가, strategy=subject_anchor` |

### LLM-based 분류

위 패턴에 해당하지 않으면 Solar(vLLM)에 JSON 출력 요청. system prompt는
`apps/pipeline/judgment_agent.py:_build_system_prompt()` 에 인라인 정의되어 있다.

LLM 출력은 다음 3종 중 하나:
- `{"kind": "search", "action": ..., "target": ..., "query": ..., "subject"?: ..., "filters"?: ..., "identifiers"?: ...}`
- `{"kind": "direct_answer", "text": ..., "reason"?: ...}`
- `{"kind": "clarification", "question": ..., "options"?: [...], "reason"?: ...}`

`JudgmentDecision` Pydantic 모델은 `search_task` / `direct_answer` / `clarification` 중 **정확히
1개**만 가질 수 있다 (mutually exclusive).

---

## 4. SearchAgent 상태 머신

```text
SearchTask
   └─ dispatch by strategy
         ├─ exact_lookup → lookup_by_axis (axis 우선순위순 시도)
         ├─ subject_anchor → hybrid_search + subject nested filter
         ├─ detail_anchor → hybrid_search + limit=1 강제
         └─ hybrid_search → hybrid_search (anchor 없음)

   evidences (dedup, sorted by score, capped to limit)
         └─ status 판정:
              action=detail:
                 0 hit → empty
                 1 hit → single
                 ≥2 hit → multiple
              action ∈ {list, stats, topic}:
                 0 hit → empty
                 ≥1 hit → single   (대표 결과로 답변)
```

### multiple → refine_judgment (1회 한정)

`status=multiple`이고 `state.refine_attempted=False`이면 워크플로우가
`refine_judgment` 노드로 분기한다. 이 노드는:

1. 현재 evidences의 `pjt_id`/`rst_id`를 SearchTask.identifiers에 OR 추가
2. `action=detail`, `strategy=exact_lookup`, `limit=1`로 강제
3. SearchAgent를 1회 더 호출

`refine_attempted=True`로 마킹되어 무한 루프가 봉인된다. 두 번째 시도도 multiple이면
FinalGuard가 clarify로 닫는다.

---

## 5. FinalGuard 검증 규칙

### 5.1 결과 상태별 단축 분기

| `SearchResult.status` | FinalGuard 결정 |
|-----------------------|-----------------|
| `error` | `decision="internal_error"` |
| `empty` | `decision="publish"` (no_result 메시지, manifest empty) |
| `multiple` | `decision="clarify"` (refine 이후에도 단일화 실패) |
| `single` | groundedness 검증 후 publish/clarify 결정 |

### 5.2 Groundedness

답변 본문에서 추출한 식별자(`\d{10}`, `[A-Z]-\d{2}-...` 형식, `(EQU|SNW|PTO|REP|PAP)-\d{4}-...`)가
모두 `evidences[*].ids` 안에 있어야 한다. 하나라도 없으면 `decision="clarify"`.

### 5.3 ReferenceManifest 발행 (ADR-0017)

```text
답변 본문의 [N] 인용 → published_rank
└─ evidence.snapshot_rank = N → CanonicalEvidence 매핑
   └─ tag → id_axis 결정:
         IRD_NAI_PJT_INFO  → id_axis="pjt_id"
         IRD_NAI_RI_PAPER  → id_axis="rst_id"
         IRD_NAI_RI_IPR    → id_axis="rst_id"
         IRD_NAI_RI_SW     → id_axis="rst_id"
         IRD_NAI_RI_RSCH_RPT → id_axis="rst_id"
         IRD_NAI_RI_FCLT_EQUIP → id_axis="rst_id"
   └─ ReferenceItem(published_rank, source_snapshot_rank, tag, id, title, id_axis)
```

답변에 `[N]` 인용이 없으면 evidence 전체를 published 순서대로 manifest에 담는다.

### 5.4 부분 발행 허용

이전 시스템의 `allow_prefix_subset=True` ↔ `allow_manifest_publish_on_subset=False` 모순은
폐기되었다. FinalGuard는 정합한 항목만 manifest에 포함시키고, `publication_status="published"`
상태로 발행한다. 1건도 매핑 불가능하면 manifest는 비어 있지만 답변 텍스트는 그대로 발행
(no_result 케이스와 동일).

---

## 6. 세션 연속성 (Session Continuity)

### SessionMemory.current_context

`apps/conversation/session_memory.py`의 `CurrentContext` discriminated union은 보존된다.
신규 파이프라인은 다음 두 타입만 사용한다:

| 타입 | 사용 시점 |
|------|-----------|
| `EmptyContext` | 신규 세션 또는 초기화 |
| `SubjectQueryContext` | 사람/기관 anchor를 가진 SearchTask가 publish된 직후 |

`PublishedManifestContext`, `DetailAnchorContext`, `ClarificationContext`,
`GroupAnchorContext` 등 다른 변형은 데이터 모델로는 존재하나 신규 파이프라인은 작성/소비하지
않는다. 향후 확장 시 활용 가능.

### Commit 시점

`save_session` 노드(workflow.py)는 다음 조건이 모두 참일 때만 `SubjectQueryContext`를 commit한다:

1. `state.search_task.subject is not None`
2. `state.final_answer.decision == "publish"`

commit되지 않으면 직전 `current_context`가 그대로 보존된다 (사용자가 같은 주제로 follow-up
하면 직전 turn의 anchor를 그대로 사용).

### Follow-up Refine 해석

JudgmentAgent rule-based pre-pass(`_detect_patterns(text, session_memory)`)가 다음 두 조건을
동시에 만족하면 `subject refine`으로 처리:

1. `session_memory.current_context.subject_name`이 비어있지 않음
2. 입력 텍스트가 `(\d{4})년\s*(이전|이후|부터|까지|전후)` 또는 `(논문|특허|보고서|소프트웨어)\s*만`
   같은 refine 패턴 매치

이 경우 `target=people` 또는 `target=org`, `subject=직전 SubjectAnchor`,
`filters=텍스트에서 추출한 year/perf_type`으로 새 SearchTask를 만든다. LLM 호출 없음.

---

## 7. 핵심 불변 약속 (Invariants)

| 약속 | 내용 |
|------|------|
| **L1 캐리어 보존** | JudgmentAgent가 결정한 subject/identifiers/filters는 SearchAgent까지 텍스트로 평탄화되지 않는다. |
| **strategy 불변** | SearchAgent는 SearchTask.strategy를 실행 중 변경하지 않는다. |
| **detail fail-closed** | `action=detail` + `status=multiple`이면 broad search로 확장하지 않고 refine 1회 후 clarify. |
| **identifier axis 분리** | `pjt_id`와 `pjt_no`를 같은 ReferenceItem의 id로 사용하지 않는다. |
| **no raw payload in prompt** | LLMGenerator는 `CanonicalEvidence`를 통과한 정형 블록만 prompt에 넣는다. |
| **에이전트 루프 차단** | refine_judgment는 1회만 동작 (`state.refine_attempted` flag). |

---

## 8. 폐기된 개념 (Decommissioned)

다음 개념은 ADR-0018 이후 더 이상 시스템에 존재하지 않는다. 코드/테스트/문서에 등장하면
혼란을 유발하므로 제거 대상이다.

- `IntentContract`, `IntentPayloadV3`, `QuestionAnalysisV3`, `HardContractV1`, `SoftStrategyHintsV1`
- `PlannerStage1/Stage1.5/Stage2`, `planner_stagewise_enabled`, `planner_prompt_version`
- `RAG_LOOKUP_FILTER_POLICY`, `name_must`, `must_one_then_should` 같은 정책 enum
- `drift_detected`, `people_terms_lost`, `topic_terms_lost`, `raw_query_fallback`
- `merge_answers`, `solar_state`, `gemma_state`, `prefix_subset` 정책
- `allow_manifest_publish_on_subset`, `state_consistency_subset_accepted`
- `agent_dialogue_router`, `agent_tool_executor`, `request_facade`, `turn_trigger/policy/interpreter`
- `OPS` 이벤트 카탈로그 (REQ.START, RAG.RETRIEVE, ANSWER.STATE_DIAG 등)

---

## 9. 관련 문서

- [01 아키텍처와 흐름](./01_ARCHITECTURE.md) — 권한분리 3계층 흐름
- [ADR-0017: Answer Rank Remapped Reference Manifest](./ADR/ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md) — FinalGuard 매니페스트 발행 규칙
- [ADR-0018: 권한 분리 3계층 아키텍처](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) — 본 문서의 진실원
- [레거시 제거 매니페스트](./reports/ADR-0018_Legacy_Cleanup_Manifest.md) — 폐기 항목 목록
