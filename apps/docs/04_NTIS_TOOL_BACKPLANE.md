# 04. NTIS 도구 backplane

> NTIS 벡터 DB를 "에이전트가 부르는 도구 묶음"으로 보는 관점. §1이 도구 목록, §2 이후가 정밀 참조.

## 1. 도구 9개 (registry)

`apps/pipeline/tools/registry.py`의 `build_default_registry()`가 **9개**를 등록한다(검색 3 + 조회 2 + manifest 2 + 응답 2).

| 도구 | 용도 |
|---|---|
| `search` | 일반 NTIS 검색. 컬렉션/전략/필터는 세션 맥락에서 자동 라우팅 |
| `search.detail` | 세션 식별자(또는 질의 폴백)로 단일 항목 상세 |
| `search.stats` | 그룹 집계/통계 요약 (`axis`: year/org/type) |
| `lookup.person_by_name` | 사람 이름 → NTIS 후보 해소 |
| `lookup.org_by_name` | 기관 이름 → NTIS 후보 해소 |
| `manifest.get_item` | 화면에 보인 목록의 rank → 항목 식별자 |
| `manifest.filter` | 현재 보이는 manifest 필터 |
| `response.direct_answer` | NTIS 검색 없이 직접 답 (종결 의도 도구) |
| `response.unsupported` | 지원 안 되는 요청 정직한 거절 (종결 의도 도구) |

> 구 도구 `search.hybrid`/`search.exact_lookup`/`search.aggregate`는 ToolSpec은 남아 있으나 **등록되지 않는다**(ADR-0022로 새 3개에 매핑·호환만 유지).

## 2. SearchRouter — DB 스키마를 플래너에서 숨김 (ADR-0022)

플래너는 DB 스키마(`target`, `perf_type`, `aggregate_by`, `collection` 등)를 **보지 않는다.** 오직 `search(query, hint?)` / `search.detail(query?)` / `search.stats(query, axis?)`만 본다(`hint`∈{recent,brief,null}, `axis`∈{year,org,type,null}).

`SearchRouter`가 주입된 `entity_resolution` + `dialogue_kind`를 읽어 target/anchor/filter를 자동 해소한다. (이게 예전 `target="research"` 같은 invalid_arg 무한루프의 근본 원인을 제거했다.)

## 3. 정밀 참조 — SearchAgent

`apps/pipeline/search_agent.py`. `SearchTask`만 받아 `SearchResult` 반환. 실행 가능: 식별자 축 exact lookup, hybrid search, subject-anchored hybrid, aggregate scroll + 파이썬 그룹핑. 도메인 타겟이 안 잡히면(주제 질의, subject/identifier 없음) `search`는 `project`+`perf`를 병렬 실행해 병합. `applied_context`(검색한 컬렉션·필터)를 플래너에 돌려 0건 진단에 쓴다. **의도를 재해석하거나 플래너 실수를 고치거나 계약 필드를 우회하지 않는다.**

### 컬렉션 (`build_search_task`)

| target | 컬렉션 |
|---|---|
| `project` | `ntis_project_v1` |
| `perf` | `ntis_perf_v1` |
| `people` | `ntis_project_v1`, `ntis_perf_v1` |
| `org` | `ntis_project_v1`, `ntis_perf_v1` |
| `support` | `ntis_supports` |

> `settings.py` allowlist 기본값엔 `ntis_supports_v1`도 있다 — 활성 retrieval 경로 확인 없이 `ntis_supports`와 호환 가정 금지.

## 4. 정밀 참조 — NTIS를 쓰지 *않는* 경우

인사, 능력 설명("뭐 할 수 있어?"), 모호해서 되물어야 하는 턴, 지원 안 되는 요청(HR 역할, 연락처, 실시간 사실, 일반 웹 지식, 사용자 주입 "기억해" 사실). → `response.direct_answer` / `response.unsupported` / 되묻기 사용.

## 5. canonical 경계

NTIS 도구 계층은 답변 생성 전에 `CanonicalEvidence`를 내야 한다. 허용 필드: identity, source type, NTIS tag, 구조화 id, title, summary, facts, roles, child entities, provenance, score, snapshot rank. **raw Qdrant payload를 프롬프트 텍스트로 쓰지 않는다.**
