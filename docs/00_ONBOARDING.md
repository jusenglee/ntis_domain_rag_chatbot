# NTIS Domain RAG 온보딩

이 문서는 저장소에 처음 들어온 사람이 "이 시스템이 무엇을 하고, 어디부터 읽고, 문제 생기면 어디를 봐야 하는지" 빠르게 잡기 위한 입구다.

## 한 줄 요약
- NTIS 질문을 검색 전략으로 바꾸고, 검색 근거를 정리해 답변으로 전달하는 retrieval-first 시스템이다.

## 이 문서를 읽을 사람
- 신규 개발자, PM, 운영자, QA

## 이 문서에서 바로 찾을 수 있는 것
- 시스템이 하는 일
- 기준 파일 지도
- 처음 읽을 순서
- 디버깅 순서

## 지금 챙길 것
- `request_facade -> planner_runtime -> retrieval_workflow -> answer_generation/answer_merge` 순서로 읽으면 전체 흐름이 잡힌다.
- 로그는 `REQ.START -> PLANNER.* -> RAG.RETRIEVAL_QUERY.RESOLUTION -> RAG.RESULT -> LLM.RESULT` 순서로 보면 된다.
- `planner contract`는 "무엇을 어떤 방식으로 찾을지에 대한 약속", `canonical evidence`는 "raw 검색 결과를 LLM에 보여주기 전에 정리한 근거"라는 뜻으로 읽으면 된다.

## 1. 이 시스템은 무엇을 하는가
사용자 질문을 planner contract(검색 전략 약속)에 맞는 retrieval 전략으로 바꾸고,
그 결과를 canonical evidence(정리된 근거)로 정규화한 뒤 answer generation으로 전달한다.

## 2. 기준 파일은 어디를 봐야 하는가
| 주제 | 기준 파일 | 왜 여기서 봐야 하는가 |
|---|---|---|
| request payload / follow-up | `apps/api/services/request_facade.py` | 질문 해석과 anchor lock이 시작되는 지점 |
| planner runtime | `apps/api/services/planner_runtime.py` | stagewise planner 진입점 |
| planner contract | `apps/core/planner_contract.py` | `SEARCH/LOOKUP/JOIN` 계약 |
| retrieval workflow | `apps/api/services/retrieval_workflow.py` | 실제 검색, display snapshot, detail cache |
| retriever adapter | `apps/api/services/rag_retriever.py` | runtime query와 RAG pipeline 연결부 |
| canonical evidence | `apps/core/canonical_evidence.py` | raw 검색 결과를 정리된 근거로 바꾸는 지점 |
| view/session state | `apps/api/services/view_state.py` | display snapshot / latest focus / detail cache |

## 3. 처음 합류하면 이 순서로 읽는다
1. `routes.py`에서 `/query/stream`, `/query/debug`, `/health`
2. `request_facade.py`에서 follow-up, anchor lock, ids_map
3. `retrieval_workflow.py`에서 `node_rag_search`
4. `view_state.py`에서 display snapshot / focus entity
5. `answer_generation.py`와 `answer_merge.py`

## 4. 문제가 생기면 이 순서로 본다
1. `REQ.START`
2. `PLANNER.*`
3. `RAG.RETRIEVAL_QUERY.RESOLUTION`
4. `RAG.RESULT` / `DISPLAY.SNAPSHOT.*`
5. `LLM.RESULT` / `REQ.SUMMARY`
