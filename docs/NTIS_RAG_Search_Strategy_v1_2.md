# NTIS RAG 검색 전략 설계 문서 v1.2
*(SEARCH / LOOKUP / JOIN 전략 설계 참고 문서)*

작성일: 2026-02-27 (Asia/Seoul)

---

## 문서 역할

이 문서는 검색 전략 설계의 참고 문서입니다.
운영 기준선과 strict contract의 source of truth는 `docs/CONTRACT.md`, `docs/ARCHITECTURE_RETRIEVAL_FIRST.md`, `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`입니다.
파라미터 정의와 mode 결정 규칙의 기준 문서는 각각 `docs/PLANNER_PARAMETER_REFERENCE.md`, `docs/MODE_DECISION_GUIDE.md`입니다.
본 문서가 운영 계약과 충돌할 경우 운영 계약 문서를 우선합니다.

## 1. 목적

NTIS 도메인(과제/성과) 질의에 대해 **SEARCH / LOOKUP / JOIN**을 명확히 구분하고,
retrieval-first 시스템에서 planner가 하나의 strategy를 확정하면 실행 레이어가 이를 재해석하지 않고 수행하도록 설계 방향을 정리합니다.

신규 참여자의 온보딩 문서라기보다 설계 참고와 의사결정 배경을 제공하는 reference 문서로 사용합니다.

핵심 목표:
- 전략 일관성
- retrieval correctness
- join/filter 안전성
- canonical evidence fidelity

## 2. 핵심 키와 의미

- `pjt_id`: 과제 instance key
- `pjt_no`: 과제 group key
- `rst_id`: 성과 instance key

원칙:
- `pjt_id`와 `pjt_no`는 같은 값처럼 취급하지 않습니다.
- `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`은 서로 다른 역할 필드입니다.
- strategy 의미를 바꾸는 parser auto-correction은 strict 기본 경로에서 허용하지 않습니다.

## 3. 전략 불변 조건

- planner는 질의당 Strategy 1개만 출력합니다.
- 실행 레이어는 `mode`, `relation`, `target_cols`, `join_key_mode`를 다시 결정하지 않습니다.
- planner invalid / contract violation은 strict 기본 경로에서 `StrategyViolation`으로 종료합니다.
- lower layer fallback strategy는 운영 기본 경로에 포함되지 않습니다.

## 4. Mode 정의

| Mode | 목적 | 핵심 특징 |
|---|---|---|
| SEARCH | 탐색 / 누락 방지 | recall과 ranking signal 우선 |
| LOOKUP | 정확 조회 | identifier / 강한 제약 기반 |
| JOIN | 관계형 2-hop | join key contract 필수 |

핵심 규칙:
- 사람/기관 기반 질의는 기본적으로 JOIN보다 LOOKUP을 우선합니다.
- 관계형(project↔perf) 의도가 명확하면 JOIN을 우선합니다.

## 5. JOIN 세부 규칙

### `join_key_mode=instance`
- 사용 키: `ids_map.pjt_id`
- `ids_map.pjt_no`만 단독으로 있으면 계약 위반입니다.

### `join_key_mode=group`
- planner-stage seed는 `ids_map.pjt_no`를 사용합니다.
- `group + pjt_no 없음`은 planner contract 단계에서 계약 위반입니다.
- planner contract를 통과한 뒤 executor는 group expansion으로 얻은 `resolved pjt_id`를 Hop2 runtime key로 사용할 수 있습니다.
- 자동 보정이나 경고 후 진행은 strict 기본 경로에서 허용하지 않습니다.

### relation별 hop 정책
- `relation=project_perf`
  - `instance`: Hop2(perf)에 `pjt_id IN (...)`
  - `group`: 기본은 `pjt_no IN (...)`이고, group expansion 결과만 남으면 Hop2(perf)에 `pjt_id IN (...)` fallback을 사용할 수 있습니다.
- `relation=perf_project`
  - `instance`: Hop2(project)에 `pjt_id == PJT_ID`
  - `group`: Hop2(project)에 `pjt_no == PJT_NO`

### JOIN head 의미
- JOIN에서 `head`는 relation target과 같아야 합니다.
- `project_perf` -> `head=perf`
- `perf_project` -> `head=project`

## 6. Planner 출력 계약 요약

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
  "filters": {},
  "limit": 20,
  "retrieval_query": "...",
  "confidence": 0.92
}
```

요약 규칙:
- `mode=JOIN`이면 `join_key_mode`는 `instance|group` 중 하나여야 합니다.
- `mode!=JOIN`이면 `join_key_mode=null`이어야 합니다.
- `output_type`은 evidence presentation shape를 결정합니다.

## 7. Retrieval-first 해석 메모

- Query understanding은 retrieval intent를 조립합니다.
- Runtime prelude는 retrieval contract를 검증합니다.
- Retrieval execution은 strategy를 그대로 수행합니다.
- Evidence assembly는 canonical evidence와 render profile을 만듭니다.
- Chat UX는 retrieval result를 표현하지만 전략을 바꾸지 않습니다.
- 단계별 handoff와 sequence는 `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`를 기준으로 봅니다.

## 8. 운영 체크포인트

- [ ] 사람/기관 질의가 SEARCH로 가지 않는가
- [ ] LOOKUP에서 이름 기반 조건이 retrieval 의미에 맞게 적용되는가
- [ ] JOIN에서 `join_key_mode`와 `ids_map`이 strict contract를 만족하는가
- [ ] canonical evidence가 source semantics를 유지하는가
- [ ] chat UX가 retrieval contract를 덮어쓰지 않는가

## 9. 함께 읽을 문서

- 신규 참여자 온보딩: `docs/PLANNER_PARAMETER_REFERENCE.md`
- mode 결정 규칙: `docs/MODE_DECISION_GUIDE.md`
- 단계별 실행 흐름: `docs/SYSTEM_FLOW_RETRIEVAL_FIRST.md`
- strict contract: `docs/CONTRACT.md`
