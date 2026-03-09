# NTIS RAG 검색전략 설계 문서 v1.2  
*(SEARCH / LOOKUP / JOIN + LLM Planner — 사용자 질의 현실 대응 강화판)*

작성일: 2026-02-27 (Asia/Seoul)

---

## v1.2 개정 이력

- 플래너 출력 계약에 `lookup_title_filter_policy`, `title_match_mode`, `relation_lookup_enforce`, `policy_version` 항목을 추가했다.
- 실행 컴파일 정책 섹션을 신설하여, 위 항목의 해석/강제 규칙(soft↔hard, EXACT|TEXT|CONTAINS, bool 게이트, 로깅 반영)을 명시했다.
- JOIN `join_key_mode` 문구를 코드 동작과 1:1로 동기화했다.
  - `group + pjt_no 없음` + `pjt_id 존재` 시 `instance` 보정 + 경고 로그.
  - `instance + pjt_no only`는 계약 위반.
- “불변 계약”을 표 형태로 재구성하여, **전략 비변경 원칙**과 **허용 보정 범위(정규화/컴파일)**를 분리했다.
- `server3.py` 플래너 프롬프트/파서 규칙과 본 문서의 모드·관계·join_key 규칙을 교차 검수해 동일 표현으로 정렬했다.

---

## 1. 목적

NTIS 도메인(과제/성과) RAG에서 질의 유형에 따라 **SEARCH / LOOKUP / JOIN**을 명확히 분리하고,  
LLM 플래너가 **단 하나의 Strategy(JSON 계약)** 를 확정하면 실행 레이어(retrieval / filters / rerank)는 이를 **재해석·재결정하지 않고 그대로 수행**한다.

목표:
- **일관성**: 같은 입력 → 같은 전략/필터/결과 경향
- **재현성**: 로그만으로 “왜 이렇게 나왔는지” 재구성 가능
- **디버깅 가능성**: 문제 발생 시 “플래너 vs 실행 레이어” 책임 분리

---

## 2. 데이터 전제 및 키 정의

### 2.1 데이터 범위
- 데이터는 **과제(project)** 와 **성과(perf)** 로 구성
- 모든 문서에 참여 객체 포함
  - 참여인력: `prtcp_mp[]`
  - 참여기관: `prtcp_org[]`

### 2.2 과제 키
- `PJT_ID`: 과제 고유번호 (**단일 시행 인스턴스 키**)
- `PJT_NO`: 동일 과제를 연도별로 시행한 인스턴스를 묶는 **그룹 ID**

#### 계약
- 모든 **project 문서 payload 최상위**에 `pjt_id`가 반드시 존재
- 없으면 데이터셋 오류로 간주하며 fallback 금지

### 2.3 기관 필드 의미 (3종 기관 구분)

| 의미 | filters 키 | 실제 필드(매핑) |
|---|---|---|
| 수행기관(메인) | `lead_org_name` | `org_nm` |
| 참여기관(공동) | `participant_org_name` | `prtcp_org[].org_nm` |
| 참여인력 소속기관 | `people_affiliation_org_name` | `prtcp_mp[].blng_org_nm` |

---

## 3. 불변 계약

### 3.1 실행 레이어가 전략을 변경하지 않음

| 항목 | 규칙 |
|---|---|
| 단일 전략 | 플래너는 질의당 1개의 Strategy만 출력 |
| 모드/관계 불변 | 실행 레이어는 `mode / relation / target_cols / join_key_mode` 재결정 금지 |
| 기본 strict(fail-close) | planner invalid/contract violation은 기본적으로 즉시 `StrategyViolation`으로 실패한다. |
| 호환 모드(opt-in) | `RAG_PLANNER_INVALID_FALLBACK=1`일 때만 기존 fallback 경로를 허용한다. |

### 3.2 허용되는 보정 범위(정규화/컴파일)

| 레이어 | 허용 범위 |
|---|---|
| 파싱 정규화 | enum 대소문자 정리, ids_map 타입 정규화, `group + pjt_no 없음 + pjt_id 존재` 시 `join_key_mode: group→instance` 보정 + 경고 |
| 실행 컴파일 | `filters → Qdrant Filter` 변환, topK 적용, rerank spec 생성 |
| 정책 강제 | detail이 아닌 LOOKUP에서 `lookup_title_filter_policy=hard` 입력 시 `soft`로 강등 |

---



### 3.3 strict 기본 + 호환 모드 예외

- 기본값은 strict(`RAG_PLANNER_INVALID_FALLBACK=0`)이며, planner invalid/contract violation은 fail-close로 처리한다.
- 호환 모드가 필요한 배포에서만 `RAG_PLANNER_INVALID_FALLBACK=1`을 명시해 기존 fallback(`ids_or_id_query=>lookup_else_search`)을 opt-in으로 활성화한다.
- 정책 적용 지점은 다음 오류군을 동일 정책으로 묶는다.
  - `PLANNER_INVALID_STRATEGY`
  - `PLANNER_JOIN_*`
  - `PLANNER_*_MISMATCH`

## 4. Mode 정의

| Mode | 목적 | server-side must |
|---|---|---|
| SEARCH | 탐색/누락 방지 | 금지(또는 최소화) |
| LOOKUP | 정확 조회 | 허용 |
| JOIN | 관계형 2-hop | 필수 |

핵심 규칙:
- 사람/기관 기반 질의는 기본 JOIN이 아니라 LOOKUP 우선.
- 관계형(project↔perf) 의도가 명확하면 JOIN 우선.

---

## 5. JOIN 세부 규칙 (코드 1:1 동기화)

### 5.1 `join_key_mode=instance`
- 사용 키: `ids_map.pjt_id`
- `ids_map.pjt_no`가 단독으로 존재하면 계약 위반
- `ids_map.pjt_id` 미존재는 Hop1 추출 경로가 허용된 범위에서만 운영 가능

### 5.2 `join_key_mode=group`
- 사용 키: `ids_map.pjt_no`
- `group + pjt_no 없음 + pjt_id 존재`인 경우:
  - 파싱 정규화 단계에서 `join_key_mode=instance`로 자동 보정
  - 경고 로그를 남김
- `group + pjt_no 없음 + pjt_id 없음`은 계약 위반

### 5.3 relation별 hop 정책
- `relation=project_perf`
  - `instance`: Hop2(perf) `pjt_id IN (...)` must
  - `group`: Hop2(perf) `pjt_no IN (...)` must
- `relation=perf_project`
  - `instance`: Hop2(project) `pjt_id == PJT_ID` must
  - `group`: Hop2(project) `pjt_no == PJT_NO` must

### 5.4 JOIN head 의미(단일 규칙)
- `head`는 최종 응답 엔티티이며, JOIN에서는 항상 relation target(두 번째 엔티티)과 동일해야 함
- `project_perf` → source=project, target=perf, head=perf
- `perf_project` → source=perf, target=project, head=project

### 5.5 planner 입력 계약 vs executor 런타임 계약
- planner 입력 계약(`validate_planner_contract`, `_validate_join_key_contract`)
  - `join_key_mode=instance`는 `ids_map={}`도 허용 가능(관계 명확 + Hop1 source 추출 경로 전제)
- executor 런타임 계약(`validate_resolved_join_keys`, `validate_join_mode_key_inputs`)
  - Hop2 실행 직전에는 실제 join key가 반드시 존재해야 함
  - 없으면 `JOIN_KEYS_MISSING` 또는 `JOIN_GROUP_KEYS_UNRESOLVED`로 명시 실패

---

## 6. 플래너 출력 계약 (v1.2)

```json
{
  "strategy_version": "v2",
  "mode": "SEARCH|LOOKUP|JOIN",
  "head": "project|perf|people|org|support",
  "action": "topic|list|detail|stats|download",
  "relation": "project_perf|perf_project|null",
  "join_key_mode": "instance|group|null",
  "target_cols": ["project","perf"],
  "ids_map": {},
  "filters": {
    "lookup_title_filter_policy": "soft|hard",
    "relation_lookup_enforce": true
  },
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

추가 계약:
- `mode=JOIN`이면 `join_key_mode`는 `instance|group` 중 하나
- `mode!=JOIN`이면 `join_key_mode=null`
- `lookup_title_filter_policy`:
  - `soft`: title은 server-side must로 강제하지 않음(soft signal)
  - `hard`: title server-side 적용(단 detail lookup에서만 최종 유지)
- `relation_lookup_enforce`:
  - bool로 해석되며 `true`일 때 relation 기반 lookup 필터 강제
- `title_match_mode`는 실행 컴파일 결과 필드로 확정 (`EXACT|TEXT|CONTAINS`)
- `policy_version`은 플래너가 결정하지 않고 실행 정책 버전(`SEARCH_POLICY_VERSION`)으로 로깅/응답에 반영

---

## 7. 실행 컴파일 정책 (Execution Compile Policy)

### 7.1 정책 필드 해석
- `lookup_title_filter_policy`: `soft|hard`
  - 유효하지 않은 값은 `soft`로 정규화
  - detail lookup이 아닌 경우 `hard`는 `soft`로 강등
- `title_match_mode`: `EXACT|TEXT|CONTAINS`
  - `hard` + text 인덱스 지원 시 `TEXT`
  - `hard` + text 인덱스 미지원 시 `EXACT`
  - `soft`면 `CONTAINS`
- `relation_lookup_enforce`:
  - `1,true,yes,y` → `true`
  - 그 외 → `false`

### 7.2 로깅/응답 반영
- 로그는 `strategy_version`(플래너 계약 버전)와 `policy_version`(실행 정책 버전)을 분리 기록
- 응답/디버그 payload에 아래를 함께 노출:
  - `policy_version`
  - `lookup_title_filter_policy`
  - `title_match_mode`
  - `relation_lookup_enforce`

---

## 8. 운영 체크포인트

- [ ] 사람/기관 질의가 SEARCH로 가지 않는가
- [ ] LOOKUP에서 이름 기반 조건이 하드 게이트(`min_should=1`)로 적용되는가
- [ ] JOIN에서 `join_key_mode`와 ids_map 규칙이 계약대로 검증/보정되는가
- [ ] 실행 레이어가 전략을 변경하지 않고 컴파일만 수행하는가
- [ ] `strategy_version`과 `policy_version`이 분리 로깅되는가

---


## 9. 환경변수 마이그레이션 (strict 기본 전환)

| 항목 | 기존 운영값(관행) | 신규 권장값 | 롤백값(임시) |
|---|---|---|---|
| `RAG_PLANNER_INVALID_FALLBACK` | `1` (fallback 기본 허용) | `0` (strict fail-close 기본) | `1` (호환 모드 opt-in) |

운영 가이드:
- 신규/기본 배포는 `RAG_PLANNER_INVALID_FALLBACK`를 설정하지 않거나 `0`으로 명시한다.
- strict 전환 직후 장애 완화가 필요하면 단기적으로만 `1`로 롤백한다.
- 롤백 시에도 `PLANNER_INVALID_STRATEGY`, `PLANNER_JOIN_*`, `PLANNER_*_MISMATCH` 로그 비율을 모니터링하고, 원인 수정 후 다시 `0`으로 복귀한다.

## 부록 A. 금지 패턴

- SEARCH인데 사람 이름이 server-side must로 들어감
- JOIN인데 `pjt_id`와 `pjt_no`를 동시 사용
- `join_key_mode=instance`인데 `ids_map.pjt_no only`
- `join_key_mode=group`인데 `ids_map.pjt_no` 없이 진행(보정 가능 조건 외)
- LOOKUP/JOIN인데 BM25-only로 우회
