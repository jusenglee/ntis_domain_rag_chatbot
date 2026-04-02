# NTIS RAG 문서 안내

이 문서 묶음은 NTIS 도메인 RAG(NTIS Domain RAG)가 지금 어떤 기준으로 동작하는지 설명한다.
문서는 예전 설명보다 지금 실제로 돌아가는 방식과 운영 기준을 우선한다.

## 한 줄 요약
- 이 문서 묶음은 "시스템 설명 -> 운영 기준 -> 회귀 기준" 순서로 현재 동작을 이해하도록 돕는 코어 문서 모음이다.

## 이 문서를 읽을 사람
- 비개발자 포함 신규 참여자, 개발자, 운영자, QA

## 이 문서에서 바로 찾을 수 있는 것
- 처음 읽을 문서 순서
- 각 문서의 역할
- 기준 진실원(Source of Truth) 위치
- 운영과 스트리밍 관련 핵심 참고점

## 지금 챙길 것
- 처음이면 `00 -> 01 -> 02 -> 03 -> 04` 순서로 읽는다.
- 코드가 바뀌면 가장 기준이 되는 문서부터 같은 변경 세트에서 갱신한다.
- 문서끼리 충돌하면 source-of-truth owner를 code/manifest에서 다시 확인한다.

## 처음 읽을 순서

1. `00_ONBOARDING.md`
2. `01_아키텍처와_흐름.md`
3. `02_실행계약과_전략규칙.md`
4. `03_운영과_환경.md`
5. `04_회귀기준과_점검.md`
6. 필요 시 `05_유지보수와_확장.md`

## 각 문서는 이런 일을 한다

| 문서 | 역할 |
|---|---|
| `00_ONBOARDING.md` | 신규 인원용 빠른 진입 |
| `01_아키텍처와_흐름.md` | 계층/책임/요청 흐름 |
| `02_실행계약과_전략규칙.md` | planner / retrieval contract |
| `03_운영과_환경.md` | 운영 로그, triage, health |
| `04_회귀기준과_점검.md` | 깨지면 안 되는 회귀 목록 |
| `05_유지보수와_확장.md` | debt, 확장 원칙 |

## ADR은 여기서 본다

- 현재 구조 변경 결정과 staged migration 메모는 `docs/ADR/` 아래에서 관리한다.
- 2026-03-27 기준 post-retrieval boundary 개선 타깃은 `ADR-0004-execution-artifact-projection-boundary.md`다.

## 먼저 이해해야 할 현재 기준선

- 시스템은 먼저 찾고(retrieval) 그다음 답을 만드는 구조를 따른다.
- planner 계약(planner contract)과 runtime 실행 기준(runtime execution truth)은 중간 단계에서 임의로 바뀌면 안 된다.
- `SEARCH`, `LOOKUP`, `JOIN` 의미를 아래 단계에서 다른 뜻으로 바꾸지 않는다.
- raw payload, canonical evidence(LLM에 보여주기 전에 정리한 근거), prompt view는 서로 역할이 다르므로 같은 것으로 취급하지 않는다.
- `pjt_id`와 `pjt_no`는 절대 같은 의미로 취급하지 않는다.
- 요청별 LLM/RAG 파라미터는 `request payload > Oracle IRD_PARAM > ENV/code default` 우선순위로 해석한다.

## 기준 진실원(Source of Truth)은 여기서 찾는다

- 요청 해석: `apps/conversation/request_facade.py`
- planner/service: `apps/planner/planner_service.py`, `apps/planner/planner_runtime.py`
- 계약(planner contract): `apps/planner/planner_contract.py`
- 전처리(runtime prelude): `apps/retrieval/rag_runtime_prelude.py`
- 파이프라인/오케스트레이션(pipeline/orchestration): `apps/retrieval/rag_pipeline.py`, `apps/retrieval/rag_join_runtime.py`
- 증거 정규화(evidence shaping): `apps/evidence/canonical_evidence.py`
- 답변 생성(answer generation): `apps/chat/answer_generation.py`

## 문서는 이렇게 사용한다

- 문서는 현재 코드와 운영 기준을 그대로 설명해야 한다.
- 같은 규칙을 여러 문서에 반복해서 쓰지 말고, 가장 기준이 되는 문서에 먼저 적는다.
- 코드가 바뀌어서 문서도 바뀌어야 하면 같은 작업 안에서 함께 고친다.

## UTF-8은 이렇게 지킨다

- 모든 문서와 한글이 들어간 핵심 코드는 UTF-8로 유지한다.
- 한글이 들어간 파일은 shell로 한꺼번에 문자열을 바꾸는 방식으로 수정하지 않는다.
- `Get-Content -Raw`로 읽은 뒤 파일 전체를 다시 저장하거나, 콘솔 문자열 치환이나 코드페이지에 기대는 편집은 금지한다.
- 한글 파일은 필요한 줄만 최소한으로 고치고, 수정 뒤에는 글자가 깨지지 않았는지와 예상 밖의 큰 변경이 없는지 확인한다.
- 한글이 깨진 상태의 문서나 코드는 그대로 두지 않는다.

## 스트리밍은 여기만 기억해도 된다

- detail/no-result short-circuit는 답변을 만들기 전에 이미 대신 보낼 응답을 확정하는 흐름이다.
- route 계층은 `/query/stream`에서 canonical SSE event만 유지한다. terminal 구간은 `clarification` 또는 `answer.final` 뒤에 `reference.set`, `done`으로 해석하며, `reference.set`은 `selected_answer_artifact.references` -> retrieval evidence(`retrieval_bundle.items`/`canonical_evidence`) -> `context` 순서로 채운다.
