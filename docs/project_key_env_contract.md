# 프로젝트 키 ENV 계약 (JOIN/LOOKUP)

> 운영 RUNBOOK 진입점: [`RUNBOOK.md`](./RUNBOOK.md)


운영 환경에서 `pjt_id`(과제 인스턴스 식별자)와 `pjt_no`(과제 번호 그룹 식별자)를 혼동하면,
JOIN/LOOKUP 결과가 오염될 수 있습니다. 이를 방지하기 위해 아래 ENV 계약을 강제합니다.

## 기본값(Default)

- `RAG_KEY_PJT_ID=pjt_id`
- `RAG_KEY_PJT_NO=pjt_no`
- `RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS=1` (group JOIN에서 Hop1 `pjt_no -> pjt_id` resolve 수행 여부)
- `RAG_JOIN_GROUP_RESOLVE_MAX_IDS=200` (group JOIN resolve 시 최대 `pjt_id` 수; 실효 상한은 resolve 입력 포인트 수에 의해 추가 제한됨)
- `RAG_JOIN_GROUP_RESOLVE_TOPK=400` (group JOIN resolve를 위한 Hop1 최소 topk)
- `RAG_JOIN_GROUP_RESOLVE_KEEP=50` (group JOIN resolve 입력 후보 keep; `max(RAG_HOP1_KEEP, min(RAG_JOIN_GROUP_RESOLVE_MAX_IDS, RAG_JOIN_GROUP_RESOLVE_KEEP))`로 보정)

서버(`server3.py`)는 초기화 시점에 위 두 값을 읽어 key mapping으로 사용합니다.
같은 값이면 startup에서 즉시 `RuntimeError`로 중단합니다.


## JOIN group resolve 운영 정책

- `RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS=1`이면 group JOIN에서 Hop1 resolve를 수행하고,
  미해결(`resolved_pjt_ids_count=0`) 시 `JOIN_GROUP_KEYS_UNRESOLVED`로 실패합니다.
- `RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS=0`이면 group JOIN에서 `pjt_no` 계약 키만으로 Hop2를 실행하며,
  resolve 미수행은 정상 동작입니다.
- 실행 시 `RAG.JOIN.POLICY`, `RAG.JOIN.GROUP.RESOLVE` 로그에 정책값/적용 결과가 기록됩니다.
  - `resolve_input_count`: resolve 함수에 실제 전달된 Hop1 후보 수
  - `resolve_keep_effective`: `RAG_HOP1_KEEP`/`RAG_JOIN_GROUP_RESOLVE_MAX_IDS`/`RAG_JOIN_GROUP_RESOLVE_KEEP`를 반영한 최종 keep

### resolve 입력 상한 계산식

- 실효 resolve 입력 상한은 아래 식으로 계산합니다.

  - `effective_resolve_cap = min(group_resolve_max_ids, group_resolve_keep_effective, available_hop1_points)`
  - `group_resolve_max_ids = RAG_JOIN_GROUP_RESOLVE_MAX_IDS`
  - `group_resolve_keep_effective = resolve_keep_effective`
  - `available_hop1_points = Hop1에서 resolve 대상으로 실제 확보된 포인트 수`

- 즉, `RAG_JOIN_GROUP_RESOLVE_MAX_IDS`만 단독으로 증가시켜도 Hop1 가용 포인트(`available_hop1_points`)나 keep 보정값(`resolve_keep_effective`)이 더 작으면 `resolve_input_count`는 함께 증가하지 않을 수 있습니다.

## 금지사항(Prohibition)

- `RAG_KEY_PJT_ID`와 `RAG_KEY_PJT_NO`를 **동일한 값으로 설정하면 안 됩니다.**

동일할 경우 서버 시작 시 `RuntimeError`가 발생하여 부팅이 중단됩니다.

## 운영 점검 포인트

서버 시작 로그에 아래 형식으로 현재 key mapping이 1회 출력됩니다.

- `[startup][key-mapping] RAG_KEY_PJT_ID=<...>, RAG_KEY_PJT_NO=<...>`

배포 직후 해당 로그를 통해 실운영 매핑 상태를 점검하세요.


## 2026-02-24 운영 보강 사항

- **ID 기반 LOOKUP 상세조회 계약 완화**
  - `mode=lookup` 이고 명시적 ID(`pjt_id/pjt_no` 등)가 존재하는 경우,
    reranked 결과 계약의 최소 건수는 `RAG_MIN_RERANKED_LOOKUP_ID`(기본 `1`)로 완화됩니다.
  - 단, `hinted_limit`/요청 `limit`이 더 작으면 최종 계약값은 해당 limit으로 한 번 더 clamp됩니다.
- **관측 지표 정규화**
  - sparse 지표는 `lexical_scored` 우선, 없으면 `sparse_hits`를 사용하도록 통일되었습니다.
- **로그 가독성 개선**
  - `coq` 로그는 `coq: <conversation_id> | q: <question>` 형식으로 출력됩니다.

- **스트림 예외 처리 보강**
  - `RAG_EMPTY_RESULT_CONTRACT`가 LOOKUP/detail+ID 맥락에서 발생하면 스트림은 계약 오류를 사용자 친화 문구로 변환하고 `done` 이벤트로 종료합니다.
