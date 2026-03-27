# NTIS Domain RAG Chatbot

NTIS 도메인 검색 시스템과 chat UX 서버다.

## 1. 먼저 볼 문서
- `docs/00_ONBOARDING.md`
- `docs/01_아키텍처와_흐름.md`
- `docs/02_실행계약과_전략규칙.md`
- `docs/03_운영과_환경.md`
- `docs/04_회귀기준과_점검.md`

## 2. 요청 1건의 처리 흐름
1. HTTP ingress: `apps/api/routes.py`
2. memory load/save: `apps/api/services/conversation_store.py`
3. intent assembly: `apps/api/services/request_facade.py`
4. planner/runtime merge: `apps/api/services/planner_runtime.py`, `apps/api/services/planner_service.py`
5. retrieval runtime: `apps/api/services/retrieval_workflow.py`, `apps/api/services/rag_retriever.py`, `apps/core/rag_pipeline.py`
6. evidence shaping: `apps/core/canonical_evidence.py`, `apps/api/services/view_state.py`
7. answer generation/merge: `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

## 3. 불변 규칙
- `SEARCH`, `LOOKUP`, `JOIN` 의미를 runtime이 재정의하지 않는다.
- `pjt_id`와 `pjt_no`를 같은 의미로 취급하지 않는다.
- raw payload를 prompt에 직접 넣지 않고 canonical evidence를 거친다.
- follow-up anchor truth는 현재 turn의 active `ids_map`을 우선한다.

## 4. 빠른 검증
```bash
PYTHONPATH=. python -m pytest --collect-only -q
python -m pytest -q tests/test_planner_stagewise.py tests/test_retrieval_workflow_detail_runtime.py
```

- 기본 `pytest` 수집 기준은 저장소 루트 `pytest.ini`다.
- stray `pytest-cache-files-*` 디렉터리는 기본 수집에서 제외된다.
