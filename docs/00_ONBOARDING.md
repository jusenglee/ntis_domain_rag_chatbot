# NTIS Domain RAG 온보딩

## 1. 이 시스템이 하는 일
사용자 질문을 planner contract에 맞는 retrieval 전략으로 바꾸고,
그 결과를 canonical evidence로 정규화한 뒤 answer generation으로 전달한다.

## 2. 소스 오브 트루스 맵
| 주제 | 기준 파일 | 왜 여기서 봐야 하는가 |
|---|---|---|
| request payload / follow-up | `apps/api/services/request_facade.py` | 질문 해석과 anchor lock이 시작되는 지점 |
| planner runtime | `apps/api/services/planner_runtime.py` | stagewise planner 진입점 |
| planner contract | `apps/core/planner_contract.py` | `SEARCH/LOOKUP/JOIN` 계약 |
| retrieval workflow | `apps/api/services/retrieval_workflow.py` | 실제 검색, display snapshot, detail cache |
| retriever adapter | `apps/api/services/rag_retriever.py` | runtime query와 RAG pipeline 연결부 |
| canonical evidence | `apps/core/canonical_evidence.py` | raw -> canonical 정규화 |
| view/session state | `apps/api/services/view_state.py` | display snapshot / latest focus / detail cache |

## 3. 신규 인원이 따라가야 할 읽기 순서
1. `routes.py`에서 `/query/stream`, `/query/debug`, `/health`
2. `request_facade.py`에서 follow-up, anchor lock, ids_map
3. `retrieval_workflow.py`에서 `node_rag_search`
4. `view_state.py`에서 display snapshot / focus entity
5. `answer_generation.py`와 `answer_merge.py`

## 4. 디버깅 순서
1. `REQ.START`
2. `PLANNER.*`
3. `RAG.RETRIEVAL_QUERY.RESOLUTION`
4. `RAG.RESULT` / `DISPLAY.SNAPSHOT.*`
5. `LLM.RESULT` / `REQ.SUMMARY`
