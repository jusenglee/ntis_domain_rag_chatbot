# NTIS RAG 문서 안내

이 문서 묶음은 NTIS 도메인 RAG(NTIS Domain RAG)가 지금 어떤 기준으로 동작하는지 설명한다.
문서는 예전 설명보다 지금 실제로 돌아가는 방식과 운영 기준을 우선한다.

## 먼저 읽을 문서

1. `01_아키텍처와_흐름.md`
2. `02_실행계약과_전략규칙.md`
3. `03_운영과_환경.md`
4. `04_회귀기준과_점검.md`
5. 필요 시 `05_유지보수와_확장.md`

## 현재 기준선

- 시스템은 먼저 찾고(retrieval) 그다음 답을 만드는 구조를 따른다.
- planner 계약(planner contract)과 runtime 실행 기준(runtime execution truth)은 중간 단계에서 임의로 바뀌면 안 된다.
- `SEARCH`, `LOOKUP`, `JOIN` 의미를 아래 단계에서 다른 뜻으로 바꾸지 않는다.
- raw payload, canonical evidence, prompt view는 서로 역할이 다르므로 같은 것으로 취급하지 않는다.
- `pjt_id`와 `pjt_no`는 절대 같은 의미로 취급하지 않는다.
- 요청별 LLM/RAG 파라미터는 `request payload > Oracle IRD_PARAM > ENV/code default` 우선순위로 해석한다.

## 기준 진실원(Source of Truth)

- 요청 해석: `apps/api/services/request_facade.py`
- planner/service: `apps/api/services/planner_service.py`, `apps/api/services/planner_runtime.py`
- 계약(planner contract): `apps/core/planner_contract.py`
- 전처리(runtime prelude): `apps/core/rag_runtime_prelude.py`
- 파이프라인/오케스트레이션(pipeline/orchestration): `apps/core/rag_pipeline.py`, `apps/core/rag_join_runtime.py`
- 증거 정규화(evidence shaping): `apps/core/canonical_evidence.py`
- 답변 생성(answer generation): `apps/api/services/answer_generation.py`

## 문서 사용 원칙

- 문서는 현재 코드와 운영 기준을 그대로 설명해야 한다.
- 같은 규칙을 여러 문서에 반복해서 쓰지 말고, 가장 기준이 되는 문서에 먼저 적는다.
- 코드가 바뀌어서 문서도 바뀌어야 하면 같은 작업 안에서 함께 고친다.

## UTF-8 공통 규칙

- 모든 문서와 한글이 들어간 핵심 코드는 UTF-8로 유지한다.
- 한글이 들어간 파일은 shell로 한꺼번에 문자열을 바꾸는 방식으로 수정하지 않는다.
- `Get-Content -Raw`로 읽은 뒤 파일 전체를 다시 저장하거나, 콘솔 문자열 치환이나 코드페이지에 기대는 편집은 금지한다.
- 한글 파일은 필요한 줄만 최소한으로 고치고, 수정 뒤에는 글자가 깨지지 않았는지와 예상 밖의 큰 변경이 없는지 확인한다.
- 한글이 깨진 상태의 문서나 코드는 그대로 두지 않는다.

## 스트리밍 참고(Streaming Note)

- detail/no-result short-circuit는 답변을 만들기 전에 이미 대신 보낼 응답을 확정하는 흐름이다.
- 화면에 아무 응답도 안 보이는 일을 막기 위해 merge 단계는 synthetic `chunk`를 한 번 보낸다.
