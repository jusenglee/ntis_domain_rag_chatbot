# NTIS RAG 검색 전략 설계 문서 v1.2
*(SEARCH / LOOKUP / JOIN 설계 참고 문서)*

작성일: 2026-02-27 (Asia/Seoul)

---

## 문서 역할

이 문서는 검색 전략 설계 참고 문서다.
현재 운영 기준과 strict contract의 source of truth는 `docs/CONTRACT.md`, `docs/ARCHITECTURE_RETRIEVAL_FIRST.md`, `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`다.
파라미터 정의와 mode 결정 규칙은 각각 `docs/PLANNER_PARAMETER_REFERENCE.md`, `docs/MODE_DECISION_GUIDE.md`를 우선한다.
이 문서가 운영 계약과 충돌하면 운영 계약 문서를 우선한다.

## 목적

NTIS 도메인 질의에서 **SEARCH / LOOKUP / JOIN**을 명확히 구분하고,
retrieval-first 시스템에서 planner가 하나의 strategy를 고정한 뒤 실행 레이어가 이를 다시 해석하지 않도록 설계 방향을 정리한다.

핵심 목표:
- 전략 분리 유지
- retrieval correctness
- join/filter 안전성
- canonical evidence fidelity

## 핵심 의미

- `pjt_id`: 과제 instance key
- `pjt_no`: 과제 group key
- `rst_id`: 성과 instance key

주의:
- `pjt_id`와 `pjt_no`를 같은 값처럼 취급하지 않는다.
- `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`은 서로 다른 역할 필드다.
- parser auto-correction은 strict 기본 경로에서 허용하지 않는다.

## 전략 분리 조건

- planner는 질의마다 strategy 1개만 출력한다.
- 실행 레이어는 `mode`, `relation`, `target_cols`, `join_key_mode`를 다시 결정하지 않는다.
- planner invalid / contract violation은 strict 기본 경로에서 `StrategyViolation`으로 종료한다.
- lower layer fallback strategy는 운영 기본 경로에 포함하지 않는다.

## Mode 정의

| Mode | 목적 | retrieval 특징 |
|---|---|---|
| SEARCH | 탐색 / 주제형 질문 | recall과 ranking signal 우선 |
| LOOKUP | 정확 조회 | identifier / 강한 제약 기반 |
| JOIN | 관계형 2-hop | join key contract 필수 |

규칙:
- 사람/기관 기반 질의는 기본적으로 JOIN보다 LOOKUP을 우선한다.
- 관계형(project<->perf) 의도가 명확하면 JOIN을 우선한다.

## JOIN key 규칙

### `join_key_mode=instance`
- 사용 key는 `ids_map.pjt_id`
- `ids_map.pjt_no`만 단독으로 있으면 계약 위반이다.

### `join_key_mode=group`
- planner-stage seed는 `ids_map.pjt_no`를 사용한다.
- `group + pjt_no 없음`은 planner contract 위반이다.
- planner contract를 통과한 뒤 executor는 group expansion으로 얻은 `resolved pjt_id`를 hop2 runtime key로 사용할 수 있다.

### `join_key_mode=deferred`
- ambiguous exact project key에서 사용하는 합법 전략이다.
- `candidate_keys.project_key`와 `project_key_policy=ambiguous_or`가 함께 있어야 한다.
- runtime이 hop1 discovery 뒤 `instance`, `group`, `dual_branch` 중 실행 key kind를 결정한다.

### relation별 hop 규칙
- `relation=project_perf`
  - `instance`: Hop2(perf)는 `pjt_id IN (...)`
  - `group`: 기본은 `pjt_no IN (...)`, 필요 시 group expansion 결과로 `pjt_id IN (...)`를 사용한다.
- `relation=perf_project`
  - `instance`: Hop2(project)는 `pjt_id == PJT_ID`
  - `group`: Hop2(project)는 `pjt_no == PJT_NO`

### JOIN head 규칙
- JOIN에서 `head`는 relation target과 같아야 한다.
- `project_perf` -> `head=perf`
- `perf_project` -> `head=project`

## Planner 출력 계약 요약

```json
{
  "mode": "SEARCH|LOOKUP|JOIN",
  "head": "project|perf|people|org|support",
  "action": "topic|list|detail|stats|download",
  "relation": "project_perf|perf_project|null",
  "join_key_mode": "instance|group|deferred|null",
  "target_cols": ["ntis_project_v1", "ntis_perf_v1"],
  "output_type": "summary|list|detail|stats|relation|comparison|series",
  "ids_map": {},
  "candidate_keys": {},
  "project_key_policy": null,
  "join_resolution_policy": null,
  "filters": {},
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

요약 규칙:
- `mode=JOIN`이면 `join_key_mode`는 `instance|group|deferred` 중 하나여야 한다.
- `mode!=JOIN`이면 `join_key_mode=null`이어야 한다.
- `output_type`은 evidence presentation shape를 결정한다.

## Retrieval-first 해석 메모

- Query understanding은 retrieval intent를 조립한다.
- Runtime prelude는 retrieval contract를 검증한다.
- Retrieval execution은 strategy를 그대로 수행한다.
- Evidence assembly는 canonical evidence와 render profile을 만든다.
- Chat UX는 retrieval result를 표현하지만 전략을 바꾸지 않는다.
- 단계별 handoff는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 본다.

## 운영 체크리스트

- [ ] 사람/기관 질의가 SEARCH로 가지 않는가
- [ ] LOOKUP에서 이름 기반 조건이 retrieval filter에 맞게 적용되는가
- [ ] JOIN에서 `join_key_mode`와 `ids_map`이 strict contract를 만족하는가
- [ ] canonical evidence가 source semantics를 유지하는가
- [ ] chat UX가 retrieval contract를 덮어쓰지 않는가
