# ADR-0022: SearchRouter — DB 스키마 추상화

Status: accepted, 2026-06-02

## Context

ADR-0020 통합 Agentic 파이프라인 이후, Planner는 NTIS 검색 도구를 직접 호출했다.

```
Planner → search.hybrid(query, target="project", filters={perf_type:[...]}, sort_by="relevance")
         → ToolExecutor → Qdrant
```

이 구조는 Planner가 DB 내부 개념을 알아야 했다:

- `target` enum: `project | perf | people | org | support`
- `sort_by` enum: `relevance | recent_desc | recent_asc`
- `filters.perf_type` enum: `PAPER | PATENT | SOFTWARE | ...`
- `aggregate_by` enum: `year | lead_org | tag | perf_type | ...`

운영 장애: Planner LLM이 `target="research"` 를 생성 →
`invalid_arg` 오류 → 6단계 루프 → 65초 → no_result 응답.

근본 원인: **Qdrant 컬렉션 구조(인프라 구현 세부사항)가 에이전트 인터페이스로 노출됐다.**

## Decision

SearchRouter를 도입해 DB 스키마를 Planner 인터페이스에서 완전히 제거한다.

Planner는 "무엇을 찾을지"만 결정한다.
SearchRouter는 "어떤 컬렉션에서 어떤 전략으로 찾을지"를 결정한다.

## New Tool Interface (Planner가 보는 것)

```
search(query, hint?)        — 일반 검색 (query만 필수)
search.detail(query?)       — 단건 상세 (세션 식별자 자동 사용)
search.stats(query, axis?)  — 통계 집계 (axis는 의미 단위)
```

`hint` 허용값: `recent | brief | null`
`axis` 허용값: `year | org | type | null`

DB 내부 enum(`target`, `perf_type`, `aggregate_by`, `collection`)은 Planner에 미노출.

## SearchRouter 라우팅 로직 (Planner가 모르는 것)

ToolContext에 주입된 `entity_resolution`과 `dialogue_kind`를 읽어 자동 결정:

```
target 결정 우선순위:
  1. entity_resolution.forced_target → 해당 컬렉션
  2. entity_resolution.identifiers.rst_id → perf
  3. entity_resolution.subject.kind=people/org → project (anchor 검색)
  4. 기본값 → project

subject anchor:
  entity_resolution.subject의 person_no / org_id 자동 주입

filters:
  entity_resolution.filters 자동 주입

axis 매핑 (search.stats):
  year → year
  org  → lead_org
  type → tag (project) / perf_type (perf)
  null → year (기본)
```

## Context 주입

`node_tool_executor`가 매 turn:

```python
ctx.entity_resolution = state.entity_resolution
ctx.dialogue_kind = state.dialogue_intent.kind if state.dialogue_intent else ""
```

ToolHandler는 `ctx.entity_resolution`을 읽어 라우팅 결정. args에서 받지 않는다.

## 이전 도구 처리

| 구 도구 | 새 도구 | 비고 |
|---|---|---|
| `search.hybrid` | `search` | target/filters 자동화 |
| `search.exact_lookup` | `search.detail` | identifiers 세션 자동 읽기 |
| `search.aggregate` | `search.stats` | axis 의미 단위로 추상화 |

`_select_evidences_for_answer`에서 구 도구명도 하위 호환 인식.

## Consequences

- Planner 프롬프트에서 DB 스키마 완전 제거
- `target="research"` 류 invalid_arg 오류 근본 차단
- 새 컬렉션 추가 시 Planner 프롬프트 변경 불필요
- SearchRouter 내부 로직이 라우팅 단일 책임 담당
- 현재 한계: 도메인 미지정 시 project 단일 컬렉션 기본값
  (향후: query 임베딩 기반 멀티 컬렉션 자동 병렬 검색으로 확장 가능)
