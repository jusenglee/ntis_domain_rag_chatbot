# NTIS RAG 검색 전략 설계 문서 v1.2
*(SEARCH / LOOKUP / JOIN + LLM Planner: 사용자 질의 현실 대응 강화판)*

작성일: 2026-02-27 (Asia/Seoul)

---

## v1.2 개정 이력

- planner 최종 계약에 `output_type`를 명시하고, stagewise planner에서는 deterministic gate/composer가 이를 결정하도록 정리했다.
- 실행 compile policy 섹션을 코드 기준으로 정리해 `lookup_title_filter_policy`와 `title_match_mode`가 실행 compile 결과와 로깅 필드로 해석된다는 점을 명시했다.
- JOIN `join_key_mode` 문구를 코드 동작과 1:1로 동기화했다.
  - `group + pjt_no 없음` + `pjt_id 존재` 시 `instance`로 보정하고 경고 로그를 남긴다.
  - `instance + pjt_no only`는 계약 위반이다.
- 불변 계약을 표 형태로 재구성하여 전략 비변경 원칙과 허용 보정 범위(정규화/컴파일)를 분리했다.
- app runtime과 실행 레이어 책임을 분리하고, join_key 계약이 문서와 코드에서 같은 의미를 갖도록 정리했다.

---

## 1. 목적

NTIS 도메인(과제/성과) RAG에서 질의 유형에 따라 **SEARCH / LOOKUP / JOIN**을 명확히 구분하고,
LLM planner가 **단 하나의 Strategy(JSON 계약)** 를 확정하면 실행 레이어(retrieval / filters / rerank)는 이를 **재해석하거나 재결정하지 않고 그대로 수행**하도록 한다.

목표:
- **일관성**: 같은 입력은 같은 전략/필터/결과 경향으로 이어진다.
- **재현성**: 로그만으로도 왜 그런 결과가 나왔는지 재구성할 수 있다.
- **디버깅 가능성**: 문제 발생 시 planner와 실행 레이어의 책임을 분리할 수 있다.

---

## 2. 데이터 전제 및 키 정의

### 2.1 데이터 범위
- 데이터는 **project**와 **perf**로 구성된다.
- 모든 문서에는 참여 객체가 포함될 수 있다.
  - 참여인력: `prtcp_mp[]`
  - 참여기관: `prtcp_org[]`

### 2.2 과제 키
- `PJT_ID`: 과제 고유번호, 즉 **단일 시행 instance key**
- `PJT_NO`: 동일 과제를 연도별 시행 instance로 묶는 **group ID**

#### 계약
- 모든 **project 문서 payload 최상위**에는 `pjt_id`가 반드시 있어야 한다.
- 없으면 dataset 오류로 보고 fallback을 허용하지 않는다.

### 2.3 기관 필드 의미

| 의미 | filters key | 실제 필드(매핑) |
|---|---|---|
| 수행기관(메인) | `lead_org_name` | `org_nm` |
| 참여기관(공동) | `participant_org_name` | `prtcp_org[].org_nm` |
| 참여인력 소속기관 | `people_affiliation_org_name` | `prtcp_mp[].blng_org_nm` |

---

## 3. 불변 계약

### 3.1 실행 레이어는 전략을 바꾸지 않는다

| 항목 | 규칙 |
|---|---|
| 단일 전략 | planner는 질의당 Strategy 1개만 출력한다 |
| mode / relation 불변 | 실행 레이어는 `mode`, `relation`, `target_cols`, `join_key_mode`를 다시 결정하지 않는다 |
| strict(fail-close) 기본 | planner invalid / contract violation은 기본적으로 즉시 `StrategyViolation`으로 실패한다 |
| strict-only runtime | 현재 runtime 기본 경로에서는 planner invalid fallback을 다시 열지 않는다 |

### 3.2 허용되는 보정 범위(정규화 / 컴파일)

| 레이어 | 허용 범위 |
|---|---|
| 파싱 정규화 | enum 대소문자 정리, ids_map 타입 정규화, `group + pjt_no 없음 + pjt_id 존재`일 때 `join_key_mode: group -> instance` 보정 + 경고 |
| 실행 컴파일 | `filters -> Qdrant Filter` 변환, topK 적용, rerank spec 생성 |
| 정책 강제 | detail이 아닌 LOOKUP에서 `lookup_title_filter_policy=hard` 입력 시 `soft`로 강등 |

### 3.3 strict 기본 경로

- 현재 runtime 기본 경로는 strict fail-close다.
- planner invalid / contract violation은 `StrategyViolation`으로 종료하며, lower layer에서 fallback strategy를 다시 만들지 않는다.
- 설계/운영 문서에 compat fallback이 남아 있더라도 현재 배포 기본값으로 읽지 않는다.

## 4. Mode 정의

| Mode | 목적 | server-side must |
|---|---|---|
| SEARCH | 탐색 / 누락 방지 | 금지 또는 최소화 |
| LOOKUP | 정확 조회 | 허용 |
| JOIN | 관계형 2-hop | 필수 |

핵심 규칙:
- 사람/기관 기반 질의는 기본적으로 JOIN보다 LOOKUP을 우선한다.
- 관계형(project↔perf) 의도가 명확하면 JOIN을 우선한다.

---

## 5. JOIN 세부 규칙

### 5.1 `join_key_mode=instance`
- 사용 키: `ids_map.pjt_id`
- `ids_map.pjt_no`만 단독으로 있으면 계약 위반이다.
- `ids_map.pjt_id`가 없더라도 Hop1 추출 경로가 허용된 범위에서만 운영할 수 있다.
- `pjt_id` 값은 런타임에서 숫자 형식(8~12자리)만으로 차단하지 않으며, 비어 있지 않으면 join key로 사용한다.

### 5.2 `join_key_mode=group`
- 사용 키: `ids_map.pjt_no`
- `group + pjt_no 없음 + pjt_id 존재`인 경우:
  - 파싱 정규화 단계에서 `join_key_mode=instance`로 자동 보정한다.
  - 경고 로그를 남긴다.
- `group + pjt_no 없음 + pjt_id 없음`은 계약 위반이다.

### 5.3 relation별 hop 정책
- `relation=project_perf`
  - `instance`: Hop2(perf)에 `pjt_id IN (...)` must
  - `group`: Hop2(perf)에 `pjt_no IN (...)` must
- `relation=perf_project`
  - `instance`: Hop2(project)에 `pjt_id == PJT_ID` must
  - `group`: Hop2(project)에 `pjt_no == PJT_NO` must

### 5.4 JOIN head 의미
- `head`는 최종 응답 entity이며, JOIN에서는 항상 relation target(두 번째 entity)과 같아야 한다.
- `project_perf` -> source=project, target=perf, head=perf
- `perf_project` -> source=perf, target=project, head=project

### 5.5 planner 입력 계약 vs executor 런타임 계약
- planner 입력 계약(`validate_planner_contract`, `_validate_join_key_contract`)
  - `join_key_mode=instance`는 `ids_map={}`도 허용할 수 있다. 단, relation이 명확하고 Hop1 source 추출 경로가 있다는 전제가 필요하다.
- executor 런타임 계약(`validate_resolved_join_keys`, `validate_join_mode_key_inputs`)
  - Hop2 실행 직전에는 실제 join key가 반드시 존재해야 한다.
  - 없으면 `JOIN_KEYS_MISSING` 또는 `JOIN_GROUP_KEYS_UNRESOLVED`로 명시적으로 실패한다.

---

## 6. Planner 출력 계약(v1.2)

```json
{
  "mode": "SEARCH|LOOKUP|JOIN",
  "head": "project|perf|people|org|support",
  "action": "topic|list|detail|stats|download",
  "relation": "project_perf|perf_project|null",
  "join_key_mode": "instance|group|null",
  "target_cols": ["ntis_project_v1","ntis_perf_v1"],
  "output_type": "summary|list|detail|stats|relation",
  "ids_map": {},
  "filters": {
    "lookup_title_filter_policy": "soft",
    "relation_lookup_enforce": true
  },
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

추가 계약:
- `mode=JOIN`이면 `join_key_mode`는 `instance|group` 중 하나여야 한다.
- `mode!=JOIN`이면 `join_key_mode=null`이어야 한다.
- `output_type`은 LLM 자유 생성보다 deterministic compose 결과를 우선한다.
- `lookup_title_filter_policy`
  - 현재 구현 기본값은 `soft`
  - title은 server-side must가 아니라 soft signal로 사용한다.
- `relation_lookup_enforce`
  - bool로 해석되며 `true`일 때 relation 기반 lookup filter를 강제한다.
- `title_match_mode`는 실행 compile 결과/로깅 필드로 확정되며, 현재 운영 경로에서는 `CONTAINS` 중심으로 해석한다.
- `policy_version`은 planner가 결정하지 않으며, 실행 정책 버전(`SEARCH_POLICY_VERSION`)으로 로깅/응답에 반영한다.

---

## 7. 실행 컴파일 정책

### 7.1 정책 필드 해석
- `lookup_title_filter_policy`: 현재 구현 기본값은 `soft`
  - 유효하지 않은 값은 `soft`로 정규화한다.
  - title은 server-side hard gate가 아니라 soft ranking signal로 사용한다.
- `title_match_mode`: 실행 compile / logging field
  - 현재 운영 경로에서는 `CONTAINS` 중심으로 사용한다.
  - `EXACT|TEXT|CONTAINS`는 설계상 가능한 해석값이지만, 현재 기본 구현 계약과 동일시하면 안 된다.
- `relation_lookup_enforce`
  - `1,true,yes,y` -> `true`
  - 그 외 -> `false`

### 7.2 로깅 / 응답 반영
- 응답 및 debug payload에 다음 필드를 함께 노출한다.
  - `policy_version`
  - `lookup_title_filter_policy`
  - `title_match_mode`
  - `relation_lookup_enforce`

---

## 8. 운영 체크포인트

- [ ] 사람/기관 질의가 SEARCH로 가지 않는가
- [ ] LOOKUP에서 이름 기반 조건이 hard gate(`min_should=1`)로 적용되는가
- [ ] JOIN에서 `join_key_mode`와 `ids_map` 규칙이 계약대로 검증/보정되는가
- [ ] 실행 레이어가 전략을 변경하지 않고 compile만 수행하는가

---

## 9. 환경 기본값 메모

- 현재 deploy 기본값 세트는 활성 runtime 경로에 필요한 env만 유지한다.
- `RAG_ENSURE_PAYLOAD_INDEX_ON_BOOT=true`는 startup 시 payload index 보장 로직을 활성화한다.
- planner invalid fallback과 promotion 재실행용 dead toggle은 기본 배포 세트에서 제거한다.

## 부록 A. 금지 패턴

- SEARCH인데 사람 이름이 server-side must로 들어감
- JOIN인데 `pjt_id`와 `pjt_no`를 동시에 사용함
- `join_key_mode=instance`인데 `ids_map.pjt_no only`
- `join_key_mode=group`인데 `ids_map.pjt_no` 없이 진행함(보정 가능한 조건 제외)
- LOOKUP/JOIN인데 BM25-only로 우회함

