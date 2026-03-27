# Product Baseline

이 저장소의 상품화 게이트는 `전체 pytest` 하나로 끝나지 않는다. 아래 3축을 함께 만족해야 한다.

1. 수집/회귀 게이트
   - `PYTHONPATH=. python -m pytest --collect-only -q --ignore-glob=pytest-cache-files-* --ignore-glob=tests/pytest-cache-files-*`
   - 아래 핵심 계약 테스트 묶음 통과
     - `tests/test_planner_stagewise.py`
     - `tests/test_retrieval_workflow_detail_runtime.py`
     - `tests/test_rag_anchor_truth.py`
     - `tests/test_rag_anchor_truth_active_only.py`
     - `tests/test_request_facade_followup_seed_priority.py`
     - `tests/test_request_facade_strategy_meta_focus_entity.py`
     - `tests/test_retrieval_workflow_detail_cache_gate.py`
     - `tests/test_contract_debt_paydown.py`
2. 제품 품질 게이트
   - `eval/sample_queries.jsonl` 또는 후속 골든 질의 세트가 존재
   - 도메인별 검색 질의가 최소한 `project / perf / people / org / follow-up / id` 축을 포함
   - fixture schema 테스트가 통과
3. 운영 게이트
   - baseline 스크립트가 수집, 핵심 회귀, eval fixture 존재 여부를 함께 확인

운영 원칙:

- planner / contract / runtime 의미가 바뀌면 관련 문서와 골든 질의도 같은 변경 세트에서 갱신한다.
- 검색엔진형 상품성 기준은 `질문 의도 해석`, `전략 선택`, `근거 노출`, `후속질문 안정성`을 함께 본다.
- `pjt_id / pjt_no`, `SEARCH / LOOKUP / JOIN`, `output_type`, canonical evidence 계약은 baseline 핵심 항목이다.

