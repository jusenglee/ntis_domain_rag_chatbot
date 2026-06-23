# 02. 계약과 규칙

> 에이전트가 진화해도 **안정적으로 지켜야 하는 계약**들이다. 처음엔 §1~§3(함정)만 봐도 된다. §4 이후는 정밀 참조다.

## 1. 데이터의 함정 ①: `pjt_id` vs `pjt_no`

**막으려는 사고:** 특정 과제 하나의 상세를 원했는데 같은 번호의 다른 연도 과제가 섞이는 것.

식별자는 섞지 않는다(don't mix axes). exact lookup 우선순위:

```
pjt_id > rst_id > pjt_no > person_no > org_id
```

| 축 | 의미 |
|---|---|
| `pjt_id` | 개별 과제 고유 내부 id |
| `pjt_no` | 과제 번호 — `pjt_id`와 **동등하지 않음** |
| `rst_id` | 연구성과 id |
| `person_no` | 참여자(사람) id |
| `org_id` | 기관 id |

## 2. 함정 ②: 식별자를 검색 텍스트로 뭉개지 않기

구조화된 식별자(`pjt_id` 등)와 타입 필터는 **구조 필드로** 넘긴다. 자연어 검색 텍스트(`retrieval_query`)에 욱여넣으면 의미가 사라진다. `retrieval_query`는 보조 힌트일 뿐 권한이 없다.

## 3. 함정 ③: raw payload 직접 주입 금지

`CanonicalEvidence`가 **답변에 들어가는 유일한 근거 단위**다. raw Qdrant payload를 답변 프롬프트나 공개 API 응답에 직접 넣지 않는다.

## 4. 정밀 참조 — SearchTask (검색 계약)

`apps/pipeline/contracts.py`. `SearchAgent`가 실행하는 **유일한** 계약.

- `action`: `list` / `detail` / `stats` / `topic` / `download`
- `target`: `project` / `perf` / `people` / `org` / `support`
- `axis`: 선택적 식별자 축
- `subject`: 구조화된 사람/기관 앵커
- `identifiers`: 구조화된 식별자 묶음
- `filters`: 타입 도메인 필터
- `strategy`: exact lookup / subject anchor / hybrid search / aggregate
- `collections`: 명시적 Qdrant 컬렉션
- `retrieval_query`: 자연어 보조 텍스트(권한 아님)

**검증(validator):** `detail` → `limit=1`, `display_limit=1` 강제 · `exact_lookup` → identifiers 필요 · `subject_anchor` → subject 필요 · `aggregate` → `aggregate_by` 필요 · `collections` 비어 있으면 안 됨.

## 5. 정밀 참조 — FilterBundle (도메인 필터)

`year_from`, `year_to`, `lead_org_name`, `participant_org_name`, `participant_person_name`, `perf_type`, `domain_keywords`, `exclude_org_name`, `exclude_perf_type`, `exclude_person_name`.

도구 인자는 이 이름으로 매핑한다. `coparticipants`는 레거시 별칭으로 허용될 수 있으나, **canonical 필드는 `participant_person_name`**.

## 6. 정밀 참조 — CanonicalEvidence

답변용 유일 근거 단위가 담는 것: 안정적 identity·source type, tag와 구조화된 id, title·summary, facts·roles·child entities, provenance, retrieval score·snapshot rank.

## 7. 정밀 참조 — Tool / Planner / Answer 계약

**Tool** (`apps/pipeline/tools/contracts.py`): `ToolSpec`(name, 설명, input/output schema, cost hint, preconditions) · `ToolCall`(플래너 요청) · `Observation`(실행 결과). `ToolExecutor`는 핸들러 예외를 잡아 `Observation(status="error")`로 반환한다(예외가 그래프로 던져지지 않음). `ToolSpec.preconditions`는 현재 프로그램적으로 강제되지 않음(프롬프트 가이드 + 핸들러 책임).

**Planner** (`apps/pipeline/agents/planner_contracts.py`): `PlannerStep.action` ∈ {`call_tool`, `answer`, `clarify`}. `call_tool`이면 `tool` 필수, `clarify`면 `clarification_question` 필수. `PlanState`는 한 턴의 결정·관찰을 누적하고 `answer`/`clarify`/`max_steps`/`duplicate_call`/`error`로 종료.

**Answer** (`apps/api/streaming/contracts.py`): 공개 답변 형태 `AnswerArtifact`. `answer_kind` ∈ {`llm_streamed`, `llm_collected`, `detail_cache`, `detail_profile`, `clarification`, `no_result`, `direct_answer`, `error`}. `response.unsupported`는 별도 `answer_kind` 값 없이 artifact meta `kind=agentic_unsupported`의 직접 최종 텍스트로 흐른다.

## 8. 정밀 참조 — 세션 계약

`SessionState` 세 슬롯: `current_subject`, `published_manifest`, `focused_detail`. KV 키: `pipeline:v1:{conversation_id}:session`.

**중요한 구현 제약:** `SessionStateAdapter.to_session_memory`가 세 슬롯을 하나의 `current_context`로 **압축**해 KV에 저장한다. `SubjectQueryContext`는 결과 manifest를, `DetailAnchorContext`는 캐시된 detail 필드를 담을 수 있으나, KV 포맷은 아직 **완전 독립적인 3슬롯 저장소가 아니다**. "무손실 3슬롯 KV 지속화"를 주장하는 코드는 먼저 `apps/pipeline/session_store.py`와 어댑터 계약을 갱신해야 한다.
