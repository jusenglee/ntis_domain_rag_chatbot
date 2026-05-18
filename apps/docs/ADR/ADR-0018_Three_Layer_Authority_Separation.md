# ADR-0018: 권한 분리 3계층 아키텍처 (JudgmentAgent / SearchAgent / FinalGuard)

## 상태 (Status)
승인됨 (Accepted) — 브랜치 `판단-검색-에이전트` 클린 브레이크 전면 적용.

## 날짜 (Date)
2026-05-18

## 배경 (Background)
ADR-0001(LLM-First Dialogue Agent), ADR-0016(Shock Absorber), ADR-0017(Remapped Reference Manifest)를 거치며
"L1 의도 진실 / L2 기술 실행" 2층 계약 철학을 유지해 왔다. 그러나 2026-05-18 운영 로그 분석에서
**L1 의도가 L2 실행 경계에 닿기 전 텍스트 한 줄로 평탄화되어 사실상 증발**하는 패턴이 관측되었다
(자세한 진단은 `plans/precious-nibbling-sunrise.md` 참고).

대표 증상:

1. JudgmentAgent(`run_dialogue_agent`)가 `identity_status=resolved_with_org`로 단일 person을 확정해도
   retrieval 단계에서 `anchor_present=false` — 식별자가 캐리어 없이 텍스트로만 흐름.
2. `RAG_LOOKUP_FILTER_POLICY=name_must` 같은 화이트리스트에 없는 정책값이 silent fallback으로 동작.
3. drift detection이 planner_query vs raw_query **텍스트만** 비교 — L1 식별자가 보존됐는지는 검사하지 않음.
4. `answer_state_consistency`가 `allow_prefix_subset=True`이면서 `allow_manifest_publish_on_subset=False`로
   subset 수용과 발행 차단이 동시에 선언된 자기모순.
5. 에이전트 재시도가 **동일 tool · 동일 인자**를 허용하여 `LOOP_GUARD`가 마지막 안전망 역할을 하고 있음.

위 다섯 가지는 모두 **계층 경계에서 L1이 새는 한 가지 근본 문제의 변종**이다. 부분 패치로는 또 다른 누수가
누적될 가능성이 크다. 따라서 본 ADR은 이 누수를 봉합하기 위해 **권한 분리 3계층 + 단일 구조화 캐리어**로
파이프라인 전체를 재설계한다.

## 결정 (Decision)

### 1. 3계층 권한 분리 (Authority Separation)

| 계층 | 단일 책임 | 입력 | 출력 |
|------|-----------|------|------|
| **JudgmentAgent** | "사용자가 무엇을 원했는가" | `UserQuestion + SessionMemory` | `SearchTask` 또는 `DirectAnswer` 또는 `Clarification` |
| **SearchAgent** | "그 요구를 만족하는 근거가 있는가" | `SearchTask` | `SearchResult{status, canonical_evidence}` |
| **FinalGuard** | "이 답변이 근거와 계약을 위반하지 않는가" | `SearchTask + SearchResult + GeneratedAnswer` | `FinalAnswer` 또는 `Clarification` |

각 계층은 **상대 계층의 내부 상태를 직접 참조하지 못한다.** 통신은 정의된 계약 객체로만 한다.

- JudgmentAgent는 검색기를 호출하지 않는다. 검색이 필요한지/어떻게 할지 권고(SearchTask)만 만든다.
- SearchAgent는 사용자 의도를 재해석하지 않는다. SearchTask만 실행한다.
- FinalGuard는 검색을 다시 트리거할 수 없다. 발행/보류/명확화 중 하나만 결정한다.

### 2. 단일 구조화 캐리어 `SearchTask`

기존 `IntentPayloadV3 / QuestionAnalysisV3 / strategy_meta / HardContract / SoftStrategyHints`의 **5중 표현**을
하나의 `SearchTask` 계약으로 통합한다. SearchTask는 텍스트(`retrieval_query`)를 보조 신호로만 들고,
**구조화 식별자(person_no, ids_map, entity_kind, axis 등)를 1급 필드로** 끝까지 흘려보낸다.

```python
class SearchTask:
    schema_version: Literal["v1"]
    # L1 의도
    action: Literal["list", "detail", "stats", "topic", "download"]
    target: Literal["project", "perf", "people", "org", "support"]
    axis: Literal["pjt_id", "pjt_no", "rst_id", "person_no", "org_id"] | None

    # 구조화 식별자 (텍스트로 평탄화 금지)
    subject: SubjectAnchor | None      # 사람·기관 의도일 때
    identifiers: IdentifierBundle       # exact lookup 후보
    filters: FilterBundle               # year, lead_org, perf_type 등

    # 검색 전략 권고 (FinalGuard에는 노출 안 됨)
    strategy: SearchStrategy            # exact_lookup / hybrid_search / detail_anchor
    collections: list[Literal["ntis_project_v1", "ntis_perf_v1"]]
    limit: int
    display_limit: int

    # 자연어 보조
    retrieval_query: str                # 텍스트 보조 신호 (drift 감지 대상 아님)

    # 추적
    request_id: str
    turn_id: str
    judgment_reason: str                # JudgmentAgent의 자기설명 (로그용)
```

### 3. SearchAgent 상태 머신

SearchAgent는 SearchTask 하나를 입력으로 받아 **단일/복수/무결과/오류** 네 가지 상태 중 하나로 종료한다.

```text
SearchTask
  ├─ strategy=exact_lookup → pjt_id/pjt_no/rst_id by-id 조회
  │     ├─ 1 hit  → SearchResult(status=single)
  │     ├─ N hit  → SearchResult(status=multiple)
  │     └─ 0 hit  → SearchResult(status=empty)
  ├─ strategy=hybrid_search → Qdrant hybrid query + filter
  │     └─ → canonical_evidence 정규화 후 status 판정
  └─ strategy=detail_anchor → subject anchor 강제 주입 후 hybrid
        └─ → 위와 동일
```

`drift detection`, `raw_query fallback`, `planner_query` 평탄화는 모두 제거한다.
Filter 정책 값은 enum으로 정의되고 부팅 시 검증된다(silent fallback 금지).

### 4. FinalGuard 단일 검증 게이트

기존 `answer_state_consistency` + `visible_answer_manifest_publication` + `merge_answers`의 3중 검증을
단일 `FinalGuard.review(task, result, generated)`로 통합한다.

```python
class FinalAnswer:
    decision: Literal["publish", "clarify", "internal_error"]
    text: str | None
    reference_manifest: ReferenceManifest | None   # ADR-0017 published_rank/source_snapshot_rank 매핑
    reasoning: str
```

검증 규칙:

1. **groundedness**: 답변의 모든 식별자 인용이 `result.canonical_evidence`에 존재해야 한다.
2. **identity integrity**: 답변 항목 N개가 모두 `canonical_evidence`의 항목으로 remap 가능해야 한다.
   매칭 알고리즘은 (a) exact id 매치 우선, (b) 제목 키워드 셋 포함 매치, (c) 둘 다 실패 시 차단.
3. **domain contract**: `pjt_id ≠ pjt_no`, `IRD_NAI_PJT_INFO → ReferenceItem.id=pjt_id`,
   성과 계열 → `ReferenceItem.id=rst_id` (ADR-0017).
4. **partial publish**: 1건이라도 정합한 항목이 있고 SearchTask가 `action=list`이면 정합한 항목만 발행.
   `allow_prefix_subset`과 `allow_manifest_publish_on_subset`이 함께 True로 결합된다.

### 5. 단일 LLM 생성

`answer_generation.merge_answers` 의 두 모델(Solar/Gemma) 동시 생성 + 사후 머지 패턴을 제거한다.
대신 SearchTask의 `action`/`target`에 따라 단일 모델을 선택한다(기본 Gemma, 코드/숫자 위주 응답은 Solar).
중복 생성으로 인한 비용·시간(평균 +12s)을 제거하고, 답변 검증을 단순화한다.

### 6. Workflow 그래프 (LangGraph)

```text
load_session
  └─ judgment_agent
        ├─ DirectAnswer → emit_answer
        ├─ Clarification → emit_clarification
        └─ SearchTask
              └─ search_agent
                    ├─ status=error → emit_internal_error
                    ├─ status=empty → emit_no_result
                    ├─ status=multiple → judgment_agent_refine (1회만)
                    │     └─ 재선택 실패 시 emit_clarification
                    └─ status=single → generate_answer
                          └─ final_guard
                                ├─ publish → emit_answer
                                ├─ clarify → emit_clarification
                                └─ internal_error → emit_internal_error
  └─ save_session
```

기존 노드 11개(`rule_precheck`, `build_conversation_state_card`, `run_dialogue_agent`,
`execute_agent_tool`, `retry_agent_after_tool_error`, `judge_knowledge_sufficiency`,
`rag_search`, `relax_and_retry`, `generate_answer_gemma`, `generate_answer_solar`,
`merge_answers`, `render_anchor_answer`, `render_participant_answer`)를 5개로 축소한다.

## 결과 (Impact)

### 신규 모듈

```
apps/pipeline/
  __init__.py
  contracts.py            # SearchTask, SearchResult, CanonicalEvidence, FinalAnswer, JudgmentDecision
  state.py                # slim PipelineState (langgraph state container)
  judgment_agent.py       # 사용자 질문 → JudgmentDecision
  search_agent.py         # SearchTask → SearchResult
  final_guard.py          # SearchResult + answer → FinalAnswer
  llm_generator.py        # 단일 LLM 호출 (Triton/vLLM)
  retrieval/
    qdrant_search.py      # Qdrant hybrid 호출만 담당
    exact_lookup.py       # pjt_id/pjt_no/rst_id by-id 조회
    canonical_normalizer.py  # raw payload → CanonicalEvidence
  workflow.py             # LangGraph 그래프 빌더
```

### 폐기 (Branch에서 삭제)

- `apps/planner/` 전체 (12개 모듈)
- `apps/conversation/agent_*` (router/dialogue/observation/tool_executor 등 9개)
- `apps/conversation/anchor_*`, `scope_resolver`, `followup_*`, `fact_followup_resolver`, `turn_*` (≈10개)
- `apps/retrieval/` 중: `rag_*`, `execution_manager`, `retrieval_workflow`, `runtime_routing`,
  `result_contract`, `result_policy`, `retrieval.py`, `filters.py`, `tools/`
  → 새 `apps/pipeline/retrieval/`에 최소 기능만 신규 작성
- `apps/api/contracts/answer_state_consistency.py`
- `apps/api/contracts/visible_answer_manifest_publication.py`
- `apps/api/contracts/answer_groundedness.py` 내 검증 정책 (단 데이터 모델만 신규 모듈로 이주)
- `apps/chat/answer_generation.py`, `answer_merge.py`, `execution_trace_summary.py`
- `apps/api/workflow_builder.py`, `workflow_nodes.py` (`apps/pipeline/workflow.py`로 대체)
- `apps/api/contracts/workflow_models.py` (`apps/pipeline/state.py`로 대체)
- `apps/conversation/request_facade.py` 의 intent build 부분
- `apps/api/request_overrides.py` (Oracle defaults — 별도 결정)
- `apps/evidence/` 일부: `context_compression_service`, `derived_facts_builder`,
  `memory_facts_resolver`, `evidence_lineage`, `prompt_evidence_envelope` 등 비활성화

### 유지 (변경 없음)

- `apps/api/main.py`, `apps/api/app_factory.py`
- `apps/api/streaming/` (SSE emitter/encoder)
- `apps/api/rag_mapper/` (도메인 스키마)
- `apps/platform/` (settings/storage/triton_client/openai_compat_llm 등)
- `apps/evidence/canonical_evidence.py`, `result_set.py`, `source_reference.py`,
  `render_profile.py`, `context_renderer.py`, `context_packer.py`
- `apps/conversation/session_memory.py`, `conversation_store.py`, `view_state.py`,
  `entity_registry.py`, `raw_payload_store.py`, `memory_observer.py`, `conversation_state_card.py`
- `apps/prompts/`
- `apps/docs/` (이 ADR 추가)
- `tests/conversation/test_conversation_store_session_memory.py`, `test_session_memory.py`,
  `test_entity_registry.py`, `test_view_state_recent_mentions.py`

### 신규 테스트 (`tests/pipeline/`)

- `test_search_task_contract.py` — SearchTask 직렬화/검증
- `test_judgment_agent_shindonggu.py` — 4건 회귀 시나리오
- `test_search_agent_status.py` — 단일/복수/무결과/오류 판정
- `test_final_guard_remap.py` — published_rank / source_snapshot_rank 매핑
- `test_workflow_smoke.py` — 전체 그래프 end-to-end

## 비목표 (Non-Goals)

- Oracle defaults 조회 재설계 (별도 운영 결정 필요)
- 새 도메인(특허·표준 등) 추가
- LLM 모델 교체 또는 prompt 전면 재작성 (필요한 부분만 새 SearchTask 컨텍스트 반영)
- 백업 호환: 본 ADR은 클린 브레이크로, 이전 ADR의 IntentPayloadV3/QuestionAnalysisV3 호환 어댑터를 제공하지 않는다.

## 회귀 검증 (Validation)

다음 4건의 로그(2026-05-18 05:02~05:14)를 회귀 픽스처로 등록한다.

| 시나리오 | 기대 동작 |
|----------|-----------|
| "신동구 연구자(한국과학기술정보연구원) 활동내역" | JudgmentAgent→SubjectAnchor(name=신동구, org=...) 구조화, SearchAgent→exact_lookup(person_no) 후 hybrid, FinalGuard→published_rank manifest 발행 |
| 같은 세션 "2020년 이후의 내역만 보여줘" | refine task로 year_from=2020 추가, anchor 유지, 동일 발행 |
| "6번 항목 - 검색엔진용 서버 (EQU-2020-...)" | 접두어 EQU- → target=perf, axis=rst_id로 정확 분기, manifest published_rank=6 검증 |
| "K-20-L01-C07 상세정보" | 코드형 식별자 → axis=pjt_no exact_lookup, 동일 도구 재시도 없음 |

## 관련 문서 (Related Documents)
- [01 아키텍처와 흐름](../01_ARCHITECTURE.md) — 본 ADR 적용 후 갱신 필요
- [02 실행 계약과 전략 규칙](../02_CONTRACTS_AND_RULES.md) — `IntentContract` → `SearchTask` 용어 갱신 필요
- [ADR-0001: LLM-First Dialogue Agent](./ADR-0001_LLM-First%20Dialogue%20Agent%20over%20Contract-Guarded%20RAG%20Tools.md)
- [ADR-0016: Agent Contract Shock Absorber](./ADR-0016_Agent_Contract_Shock_Absorber.md) — Smart Coercion 원칙은 `SearchTask` 검증에 흡수
- [ADR-0017: Answer Rank Remapped Reference Manifest](./ADR-0017_Answer_Rank_Remapped_Reference_Manifest.md) — `FinalGuard.reference_manifest`로 구현

## 결정 영향 추적 (Decision Trail)
- 2026-05-18 05:02~05:14 운영 로그 4건 → 본 ADR 작성
- 분석 보고: `plans/precious-nibbling-sunrise.md`
