# NTIS Domain RAG Chatbot

NTIS 데이터를 대상으로 "먼저 찾고, 그다음 답하는" retrieval-first 검색/대화 서버다.
planner(무엇을 어떻게 찾을지 정하는 단계), retrieval(실제 검색 단계), answer generation(근거를 바탕으로 설명하는 단계)을 분리해 시스템 설명과 운영 기준을 명확하게 유지한다.

## 한 줄 요약
- NTIS 질문을 검색 전략으로 바꾸고, 검색 근거를 정리해 답변으로 전달하는 서버다.

## 이 문서를 읽을 사람
- 저장소를 처음 여는 개발자, PM, 운영자, QA

## 이 문서에서 바로 찾을 수 있는 것
- 시스템 목적
- 요청 처리 큰 흐름
- 먼저 읽을 문서
- baseline entrypoint

## 지금 챙길 것
- 처음이면 `docs/00_ONBOARDING.md`부터 순서대로 읽는다.
- 검증 진입점은 `scripts/run_baseline_checks.ps1`와 루트 `pytest.ini`다.
- `SEARCH / LOOKUP / JOIN`, `pjt_id / pjt_no`, follow-up anchor truth는 아래 코어 문서에서 절대 흐리면 안 된다.

## 1. 처음 보면 좋은 문서
- `docs/00_ONBOARDING.md`
- `docs/01_아키텍처와_흐름.md`
- `docs/02_실행계약과_전략규칙.md`
- `docs/03_운영과_환경.md`
- `docs/04_회귀기준과_점검.md`

## 2. 요청 1건은 이렇게 흐른다
1. HTTP ingress: `apps/api/routes.py`
2. memory load/save: `apps/conversation/conversation_store.py`
3. intent assembly: `apps/conversation/request_facade.py`
4. planner/runtime merge: `apps/planner/planner_runtime.py`, `apps/planner/planner_service.py`
5. retrieval runtime: `apps/retrieval/retrieval_workflow.py`, `apps/retrieval/rag_retriever.py`, `apps/retrieval/rag_pipeline.py`
6. evidence shaping: `apps/evidence/canonical_evidence.py`, `apps/conversation/view_state.py`
7. answer generation/merge: `apps/chat/answer_generation.py`, `apps/chat/answer_merge.py`

## 3. 절대 흐리면 안 되는 규칙
- `SEARCH`, `LOOKUP`, `JOIN` 의미를 runtime이 다시 정하지 않는다.
- `pjt_id`와 `pjt_no`를 같은 의미로 취급하지 않는다.
- raw payload를 prompt에 직접 넣지 않고 canonical evidence(LLM에 보여주기 전에 정리한 근거)를 거친다.
- follow-up anchor truth는 현재 turn의 active `ids_map`을 우선한다.

## 4. 빠르게 상태를 확인하려면
```bash
python -m py_compile apps/conversation/request_facade.py apps/retrieval/retrieval_workflow.py apps/chat/answer_merge.py
powershell -ExecutionPolicy Bypass -File scripts/run_baseline_checks.ps1
```

- 기본 `pytest` 수집 기준과 제외 규칙은 저장소 루트 `pytest.ini`다.
- stray `pytest-cache-files-*` 디렉터리는 기본 수집에서 제외된다.
