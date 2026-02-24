# 프로젝트 키 ENV 계약 (JOIN/LOOKUP)

운영 환경에서 `pjt_id`(과제 인스턴스 식별자)와 `pjt_no`(과제 번호 그룹 식별자)를 혼동하면,
JOIN/LOOKUP 결과가 오염될 수 있습니다. 이를 방지하기 위해 아래 ENV 계약을 강제합니다.

## 기본값(Default)

- `RAG_KEY_PJT_ID=pjt_id`
- `RAG_KEY_PJT_NO=pjt_no`

서버(`server3.py`)는 초기화 시점에 위 두 값을 읽어 key mapping으로 사용합니다.

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
  - 일반 detail/list/search 시나리오는 기존 preset의 `min_reranked`를 그대로 사용합니다.
- **관측 지표 정규화**
  - sparse 지표는 `lexical_scored` 우선, 없으면 `sparse_hits`를 사용하도록 통일되었습니다.
- **로그 가독성 개선**
  - `coq` 로그는 `coq: <conversation_id> | q: <question>` 형식으로 출력됩니다.
