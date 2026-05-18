# NTIS RAG 챗봇 온보딩

이 문서는 신규 기여자가 10분 안에 시스템의 진입점, 핵심 계약, 운영 점검 순서를 잡기 위한 입구 문서다.

> **2026-05-18 갱신**: [ADR-0018](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) 권한분리 3계층 아키텍처 기준으로 전면 갱신.

## Source of Truth

- 문서 루트: `apps/docs`
- 코드 진입점: `apps/api/routes.py` → `apps/pipeline/workflow.py`
- 함께 읽을 문서: [`README.md`](./README.md), [`01_ARCHITECTURE.md`](./01_ARCHITECTURE.md), [`02_CONTRACTS_AND_RULES.md`](./02_CONTRACTS_AND_RULES.md), [`07_회귀기준과_점검.md`](./07_회귀기준과_점검.md)
- 현재 진실원 ADR: [`ADR-0018`](./ADR/ADR-0018_Three_Layer_Authority_Separation.md)

## 시스템 한 줄 요약

`사용자 질문 -> JudgmentAgent (SearchTask 생성) -> SearchAgent (canonical evidence 조회) -> LLMGenerator (단일 LLM) -> FinalGuard (groundedness + manifest 검증) -> SSE 발행`

핵심 원칙은 **권한 분리(Authority Separation)** 다. 세 계층이 각자 정확히 한 가지 책임만 갖고 통신은 정의된 계약(`SearchTask`, `SearchResult`, `FinalAnswer`)으로만 한다.

| 계층 | 단일 책임 |
|------|-----------|
| **JudgmentAgent** | 사용자가 무엇을 원했는가 |
| **SearchAgent** | 그 요구를 만족하는 근거가 있는가 |
| **FinalGuard** | 이 답변이 근거와 계약을 위반하지 않는가 |

## 가장 먼저 알아야 할 용어

| 용어 | 의미 |
|---|---|
| `SearchTask` | JudgmentAgent → SearchAgent 단일 캐리어 계약. `subject/identifiers/filters/strategy/collections` 등 구조화 필드 |
| `SubjectAnchor` | 사람/기관 anchor. `kind/display_name/person_no/org_id/affiliation_org_name/identity_status` |
| `IdentifierBundle` | by-axis lookup 후보. `pjt_id/pjt_no/rst_id/person_no/org_id` 각 list |
| `FilterBundle` | 구조화 필터. `year_from/year_to/lead_org_name/participant_org_name/perf_type/domain_keywords` |
| `strategy` | `exact_lookup` / `subject_anchor` / `hybrid_search` / `detail_anchor` 중 하나 |
| `SearchResult.status` | `single` / `multiple` / `empty` / `error` |
| `CanonicalEvidence` | raw payload → prompt-safe 단위. `identity/source_type/tag/ids/title/facts/roles/snapshot_rank` |
| `ReferenceManifest` | FinalGuard가 발행하는 답변 기준 출처표. `published_rank`/`source_snapshot_rank` 매핑 (ADR-0017) |
| `FinalAnswer.decision` | `publish` / `clarify` / `internal_error` |
| `SubjectQueryContext` | 직전 turn의 subject anchor를 다음 turn에 전달하는 SessionMemory 컨텍스트 |
| `pjt_id` | 과제 instance key (detail 축) |
| `pjt_no` | 과제 group key (stats/perf join 축) |

## 추천 읽기 순서

1. [`README.md`](./README.md)
2. [`01_ARCHITECTURE.md`](./01_ARCHITECTURE.md) ← ADR-0018 흐름과 폐기된 개념
3. [`02_CONTRACTS_AND_RULES.md`](./02_CONTRACTS_AND_RULES.md) ← SearchTask·FinalGuard 검증 규칙
4. [`ADR/ADR-0018_Three_Layer_Authority_Separation.md`](./ADR/ADR-0018_Three_Layer_Authority_Separation.md) ← 결정 기록
5. [`06_운영과_환경.md`](./06_운영과_환경.md)
6. [`07_회귀기준과_점검.md`](./07_회귀기준과_점검.md)

`03_BEHAVIORAL_SAFETY.md` 와 `04_TOOLING_STANDARDS.md` 는 폐기 문서로 보존되어 있다. 신규 코드 작성 시 참고하지 말 것.

코드 읽기 순서:

1. `apps/api/main.py` → `app_factory.py` → `runtime.py` (FastAPI 구성)
2. `apps/api/routes.py` (SSE 인코딩 + 워크플로우 호출)
3. `apps/pipeline/workflow.py` (LangGraph 그래프)
4. `apps/pipeline/judgment_agent.py` (rule-based + LLM 분기)
5. `apps/pipeline/search_agent.py` (Qdrant 분기)
6. `apps/pipeline/final_guard.py` (groundedness 검증 + ReferenceManifest)
7. `apps/pipeline/contracts.py` (모든 계약 모델)

## 요청 1건 흐름

```text
HTTP /query/stream
  -> PipelineState 생성 (request_id, turn_id, conversation_id, stream_emitter)
  -> load_session: KV에서 SessionMemory 복원
  -> JudgmentAgent.decide()
        ├─ rule-based: pjt_id/pjt_no/rst_id/person+org 감지 시 즉시 SearchTask
        ├─ LLM-based: Solar(vLLM)로 JSON 분류
        └─ JudgmentDecision { search_task | direct_answer | clarification }
  -> 분기:
        ├─ direct_answer → emit_direct_answer
        ├─ clarification → emit_clarification
        └─ search_task → SearchAgent
  -> SearchAgent.execute(task)
        ├─ strategy 분기: exact_lookup / subject_anchor / hybrid_search / detail_anchor
        ├─ Qdrant scroll 또는 query_points 호출
        ├─ canonical_normalizer로 CanonicalEvidence 리스트 변환
        └─ SearchResult { status, evidences, total_hits, diagnostics }
  -> 분기:
        ├─ status=error → emit_internal_error
        ├─ status=multiple AND !refine_attempted → refine_judgment (1회 한정)
        ├─ status=empty → generate(no_result)
        └─ status=single → generate(LLM)
  -> LLMGenerator.generate()
        ├─ Triton(Gemma)로 단일 호출
        ├─ evidence block을 prompt-safe 형태로 정리
        ├─ 스트리밍 답변 → SSE answer.chunk publish
        └─ GeneratedAnswer { text, latency_ms, stream_metrics }
  -> FinalGuard.review()
        ├─ groundedness: 답변 식별자가 evidence.ids 안에 있는지
        ├─ ReferenceManifest 발행 (tag → id_axis 매핑)
        ├─ 부분 발행 허용 (정합한 항목만 manifest)
        └─ FinalAnswer { decision, text, reference_manifest, reasoning }
  -> save_session: SubjectQueryContext commit + KV 저장
  -> /query/stream: reference.set + done 이벤트
```

## 기준 파일 지도

| 확인하고 싶은 것 | 파일 |
|---|---|
| HTTP 라우트, SSE 인코딩 | `apps/api/routes.py` |
| 런타임 부트스트랩 (Qdrant/LLM/KV) | `apps/api/runtime.py` |
| FastAPI Composition Root | `apps/api/app_factory.py` |
| LangGraph 워크플로우 | `apps/pipeline/workflow.py` |
| 사용자 의도 결정 (Rule + LLM) | `apps/pipeline/judgment_agent.py` |
| Qdrant 검색 dispatch + 상태 판정 | `apps/pipeline/search_agent.py` |
| by-axis 정확 조회 | `apps/pipeline/retrieval/exact_lookup.py` |
| Hybrid 검색 + 필터 빌더 | `apps/pipeline/retrieval/qdrant_search.py` |
| raw payload → CanonicalEvidence | `apps/pipeline/retrieval/canonical_normalizer.py` |
| 단일 LLM 답변 생성 | `apps/pipeline/llm_generator.py` |
| 답변 검증 + ReferenceManifest | `apps/pipeline/final_guard.py` |
| 계약 모델 (모든 Pydantic) | `apps/pipeline/contracts.py` |
| PipelineState | `apps/pipeline/state.py` |
| 세션 저장/복원 (KV) | `apps/pipeline/session_store.py` |
| LLM 어댑터 캐시 (Solar/Gemma) | `apps/chat/llm_runtime.py` |
| Qdrant + 임베딩 리소스 | `apps/retrieval/rag_store.py` |
| Canonical evidence dataclass | `apps/evidence/canonical_evidence.py` |
| Session memory 모델 | `apps/conversation/session_memory.py` |
| 환경값 | `apps/platform/settings.py` |

## 문제 진단 순서

1. `[load_session]` 로그에서 session 복원 결과(`conversation_id`, `current_context.subject_name` 등) 확인.
2. `[node_judgment]` 에서 `kind=search|direct_answer|clarification`, `strategy`, `latency_ms` 확인. rule-based 통과 시에는 LLM 호출 없음.
3. `[node_search]` 에서 `status=single|multiple|empty|error`, `total_hits`, `latency_ms` 확인.
4. `[hybrid_search]` 또는 `[exact_lookup]` 에서 Qdrant 호출 자체의 성패 확인.
5. multiple 발생 시 `node_refine_judgment` 호출 여부 (`state.refine_attempted=True`) 확인.
6. `[FinalGuard]` 에서 groundedness 위반(unsupported identifier 목록)이 있었는지 확인. `decision`이 publish/clarify/internal_error 중 어느 쪽으로 닫혔는지 본다.
7. `[stream_metrics]` 에서 TTFT, chunks, content_char_n, truncated 확인.
8. `[save_session]` 에서 SubjectQueryContext commit 여부 확인.

레거시 `OPS` 이벤트(`REQ.START`, `AGENT.DECISION`, `RAG.RETRIEVE`, `ANSWER.STATE_DIAG`, `REQ.SUMMARY` 등)는 더 이상 발행되지 않는다.

## 절대 잊지 말아야 할 규칙

| 규칙 | 이유 |
|---|---|
| JudgmentAgent가 만든 `SearchTask`는 SearchAgent에서 수정 불가 (frozen) | L1 의도 진실 보존 |
| `pjt_id`와 `pjt_no`는 같은 ReferenceItem의 id로 쓰지 않는다 | instance/group 의미가 다르다 |
| `lead_org_name`, `participant_org_name`, `SubjectAnchor.affiliation_org_name`는 다른 의미다 | 기관 역할 보존 |
| raw payload를 LLM 프롬프트에 직접 넣지 않는다 | `CanonicalEvidence`를 반드시 거쳐 prompt-safe block으로 정리 |
| `action=detail`은 SearchTask 생성 시점에 `limit=1`로 강제 normalize | detail은 단일 대상 답변 계약 |
| `status=multiple` → `refine_judgment`는 1회만 (refine_attempted 플래그) | 무한 루프 차단 |
| 시스템/tool 오류는 clarification으로 위장하지 않는다 | `internal_error` 분기로 명확히 닫는다 |
| `ReferenceItem.id`는 tag에 따라 의미 고정 (PROJECT→pjt_id, 성과→rst_id) | ADR-0017 매니페스트 정합 |
| 폐기된 개념은 코드/문서에 다시 등장시키지 않는다 | 02 문서 §8 폐기 목록 참조 |
