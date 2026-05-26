# ADR-0020: Tooling Catalog Expansion (post-ADR-0019)

- **Status**: Accepted (2026-05-22)
- **Builds on**: [ADR-0019 Seven Agent Agentic Redesign](./ADR-0019_Seven_Agent_Agentic_Redesign.md)
- **Scope**: 7-agent core 위에 추가된 19 phase의 도구·필터·캐싱·진단을 단일 진실원으로 정리.

## Context

ADR-0019 (2026-05-19)에서 7-agent core를 확정한 이후, 실제 운영 로그에서 발견된 패턴·회귀를 phase 단위로 처리하며 도구를 확장했다. 매 phase는 회귀 테스트(R20~R38)로 가드되고 있으나, **README/01~05 문서가 따라가지 못해** 운영팀이 도구 가시성을 잃었다. 본 ADR은 그 갭을 닫는다.

## Decision

ADR-0019의 7-agent core는 그대로 유지하되, 다음 카탈로그를 production 기준으로 확정한다.

### 1. DialogueKind (10종)

| Kind | 도구 | 검색 호출? | LLM 답변 호출? |
|---|---|---|---|
| `ask_search` | hybrid_search / subject_activity | ✓ | ✓ |
| `ask_detail` | exact_lookup (+ cache hit 시 skip) | ✓ (또는 cache) | ✓ |
| `ask_meta` | manifest item tag/axis 즉답 | ✗ | ✗ |
| `ask_children` | focused_detail.child_entities 즉답 | ✗ | ✗ |
| `ask_similar` | focused_detail.title 기반 hybrid_search + anchor 제외 | ✓ | ✓ |
| `refine_previous` | subject 보존 또는 `manifest_filter` exact_lookup | ✓ | ✓ |
| `compare` | by_axis_groupby (대상별 task) | ✓ | ✓ |
| `stats` | `aggregate` (Qdrant scroll + Python group_by) | ✓ (scroll만) | ✓ |
| `direct_answer` | LLM 텍스트 그대로 | ✗ | ✗ |
| `clarification` | 사용자에게 되묻기 | ✗ | ✗ |

### 2. SearchStrategy (4종)

ADR-0019의 5종에서 `detail_anchor` 제거(미사용 dead code). 현재:

- `exact_lookup` — pjt_id/pjt_no/rst_id/person_no/org_id 정확 매칭
- `subject_anchor` — 사람/기관 anchor 강제 주입 후 hybrid
- `hybrid_search` — 일반 hybrid (detail action도 limit=1 강제로 처리)
- `aggregate` — Qdrant scroll → Python group_by count

### 3. 단축 노드 (5종)

LangGraph workflow의 즉답 분기. 검색 호출 0회.

- `emit_direct_answer` — 인사·잡담
- `emit_clarification` — 정보 부족 / 모호성
- `emit_meta_answer` — `ask_meta` 결정적 분류 답변
- `emit_children_list` — `ask_children` focused_detail.child_entities 글머리표
- `emit_internal_error` — 시스템 오류 (`_user_facing_error_message`로 친절 메시지)

### 4. FilterBundle (포함 + 제외 필드)

```python
class FilterBundle(BaseModel):
    # 시간
    year_from: Optional[int]
    year_to: Optional[int]

    # 기관·인력 (포함, AND)
    lead_org_name: List[str]
    participant_org_name: List[str]
    participant_person_name: List[str]   # 공동 참여자 (AND) — 각 이름이 모두 매칭돼야

    # 성과 유형 (포함)
    perf_type: List[str]
    domain_keywords: List[str]

    # 제외 (must_not — NOT)
    exclude_org_name: List[str]
    exclude_perf_type: List[str]
    exclude_person_name: List[str]
```

#### perf_type 별칭 자동 변환

LLM은 `"PATENT"` / `"PAPER"` 같은 약어를 출력하지만 NTIS payload `tag`는 `"IRD_NAI_RI_IPR"` / `"IRD_NAI_RI_PAPER"` 형식. `_expand_perf_type_aliases`가 양쪽 모두 매칭 후보로 확장 (include/exclude 모두 적용).

#### year filter — string MatchAny

NTIS `stan_yr` payload가 문자열("2013")로 저장되어 Qdrant `Range(int)` 매칭 실패. `year_from..year_to` 범위를 `MatchAny(["2020","2021",...])` 로 전개. 최대 60년 안전 한도.

### 5. SortBy (3 모드 + stats year 정렬)

- `relevance` (기본, Qdrant score 순)
- `recent_desc` (stan_yr 기준 내림차순)
- `recent_asc`

stats aggregate에서 `aggregate_by="year"` + `sort_by="recent_*"` 조합 시 연도순. 그 외 stats는 count 내림차순 유지.

**LLM 정직성 가드**: AnswerAgent prompt에 `sort_by=relevance` 메타가 노출돼 LLM이 "최근순으로 정리했습니다" 같은 거짓 도입부를 생성하지 못하도록 제약.

### 6. Length Hint (3 모드)

- `brief` — 각 항목 1줄, 도입부 1문장
- `default` — 기본
- `detailed` — 각 항목 2~3줄, 도입부 2~3문장 종합

### 7. stats aggregate — 6 group_by 축

- `year` (`stan_yr` 진실원, 1900~2100 검증)
- `lead_org` (수행기관)
- `tag` (NTIS DataTag, 과제용)
- `perf_type` (성과용)
- **`participant_org`** (nested array — 1 payload = N keys)
- **`participant_person`** (`prtcp_mp_hm_nm_list` 평탄화)

### 8. 결정적 추출 fallback (LLM 안전망 6종)

LLM이 누락하거나 잘못 분류했을 때 결정적 코드로 보강:

1. **manifest_rank → 식별자** — `EntityResolver._resolve_manifest_rank`
2. **focused_detail anaphora** — "해당 항목" → focused_detail.anchor
3. **refine_previous promotion** — `ask_search + manifest + "골라/뺀/만"` → `refine_previous`
4. **sort_by inference** — "최근순/오래된 순" 키워드
5. **year window** — "최근 N년/올해/작년/N년 전" → `year_from`
6. **length_hint** — "간단히/자세히" 키워드

### 9. 캐싱 (P0-B)

`FocusedDetailSlot`에 evidence 본문 캐싱 (title/summary/facts/roles/child_entities/cached_ids/cached_tag).

- 동일 식별자 detail 재조회 → RetrievalAgent skip + `diagnostics.cache_hit=True`
- `ask_children` 즉답 — 캐시된 child_entities를 글머리표로 직접 답변

### 10. AnswerAgent prompt 강화 (5 블록)

1. `[사용자 조건 요약]` — subject/year/perf_type/coparticipants/exclude_*/sort/length/manifest_rank reflection
2. `[활동 요약]` — subject_activity view 통계 (총 N건, year_min~max, top_orgs)
3. `[근거 출처]` 상단 `[sort_by=...]` 메타 — LLM 정직성
4. `[그룹 힌트]` — pjt_no 다년차 분리 보장
5. `[참여연구자]` / `[참여기관]` / `[연계 성과]` — single_detail view child_entities

### 11. response.diagnostics (운영 진단)

`/query` payload에 `diagnostics` 키로 한 turn의 모든 결정 경로 노출:

| 카테고리 | 키 |
|---|---|
| Dialogue | `dialogue_kind`, `sort_by`, `coparticipants_n`, `manifest_rank_in` |
| EntityResolver | `resolution_source`, `resolution_target`, `dropped_identifier_hints` |
| Planner | `plan_reason`, `plan_task_count`, `plan_merge_strategy` |
| Retrieval | `search_status`, `search_cache_hit`, `search_cache_matched_axis`, `search_latency_ms` |
| Curator | `evidence_view`, `evidence_count`, `child_entity_count` |
| Critic | `guard_decision`, `manifest_published_n` |
| Session | `has_subject/manifest/focused_detail`, `focused_detail_cached_axes`, `subject_name` |
| Artifact | `artifact_meta` (도구 라벨: `ask_meta_short_circuit`, `ask_children_short_circuit`, `cache_hit` 등) |

### 12. 친절한 empty/error 메시지

`_no_result_text_for` — intent 분석해 다음 시도 가이드 자동 생성:
- year filter 있음 → "연도 조건을 빼고 다시 시도"
- subject_name 있고 affiliation 없음 → "소속 기관과 함께 알려달라"
- perf_type 있음 → "성과 유형 조건 빼기"
- exclude_* 있음 → "제외 조건 완화"
- 식별자만 있고 형식 의심 → "pjt_id 10자리 / rst_id CNL/EQU/PTR 접두어 안내"

`_user_facing_error_message` — error_code 분류:
- `qdrant_failed`/`all_tasks_failed`/`search_dispatch_failed` → "검색 시스템 일시 장애" + retryable
- `*timeout*`/`*deadline*` → "조건을 좁혀 다시 시도" + retryable
- `empty_generation` → "질문 구체화 후 재시도" + retryable
- 기타 → "내부 오류" + non-retryable

## Migration (코드 레벨)

- 삭제: `apps/pipeline/contracts.py:SearchStrategy`의 `"detail_anchor"` 제거 + `SearchAgent._exec_detail_anchor` 삭제
- 추가: `apps/pipeline/agents/dialogue_agent.py`에 결정적 추출 fallback 6개 함수
- 변경: `apps/api/routes.py:_build_diagnostics` — 진단 dict 노출
- 신규: `apps/pipeline/agents/answer_agent.py`의 prompt 블록 5종 (위 10번 참조)
- 영향: 코드 레벨 production 경로 모두 frozen Pydantic 계약을 유지. 외부 API의 응답 payload만 `diagnostics` 키 추가됨 (UI 영향 0, optional consumer).

## Validation

- `pytest tests/pipeline -q` → 334 passed (R20~R38, 누적 119개 회귀 가드)
- `pytest tests -q --ignore=tests/.claude` → 354 passed
- 매 phase가 R{N}* 시리즈로 가드되어 회귀 시 즉시 식별 가능

## Consequences

### Positive
- 자연스러운 사용자 패턴 ("최근 3년 신동구의 KISTI 제외한 특허 자세히") 1 turn에 모두 처리
- LLM 일관성 위험을 결정적 fallback 6개로 보완
- 동일 detail 재조회 0ms (캐시), child_entities follow-up 0ms (즉답 노드)
- 운영팀이 매 turn 도구 분기·캐시·세션 상태를 단일 dict로 가시화

### Negative
- DialogueIntent 필드 수 증가 (16개 → 23개) — JSON 페이로드 약간 커짐
- SearchAgent dispatch 분기 4종 + aggregate axis 6종으로 복잡도 ↑
- AnswerAgent prompt 길이 누적 — token 비용 ↑ (개별 답변당 약 1.5~2배)

### Risks / Mitigations
- LLM 결정 사라짐 위험 → 결정적 fallback만 우선 적용하면 동등 결과
- prompt 슬림화 필요성 → 후속 ADR-0021 대상

## Reference Implementation

- 결정 분기 진실원: `apps/pipeline/agents/search_planner.py`
- 결정적 추출: `apps/pipeline/agents/dialogue_agent.py` (`_augment_*`, `_infer_*`, `_promote_*`)
- 캐시: `apps/pipeline/agent_workflow.py:_try_cached_detail`
- 진단 dict: `apps/api/routes.py:_build_diagnostics`
- 회귀 시리즈: `tests/pipeline/test_regression_incidents.py` R20~R38
