# 07. 검증과 리뷰

> "뭘 돌리면 안 깨졌다가 보장되나" + 리뷰 체크리스트. **§0의 테스트 트리 현실을 먼저 읽어라.**

## 0. ⚠️ 현재 테스트 트리 현실 (인수자 필독)

- `tests/`는 `.gitignore`에 들어 있어 **버전관리되지 않는다**(로컬에만 존재). 인수 후 버전관리 포함 여부 **정책 결정** 필요.
- 로컬 `tests/`엔 **다른 브랜치용 테스트가 섞여** 있다. 그냥 `pytest tests`를 돌리면 수집 에러로 중단된다.
- **이 브랜치의 그린 기준선: `496 passed / 32 failed`** (2026-06-23 기준, `557cc6e`). 단, 아래 stale을 제외/감안해야 한다:

### 무시할 stale (회귀 판단에서 제외, 전부 사전 존재)

- **수집 에러 6개** (제외하고 실행):
  - `tests/test_agent_intent_adapter.py`, `tests/test_agent_search_tool_materialization.py`, `tests/test_qdrant_query_compiler.py`, `tests/test_query_intent_detail_anchor.py` — 이 브랜치에 없는 `apps.planner.query_intent` 등 임포트(타 브랜치용).
  - `tests/pipeline/test_agent_workflow.py`, `tests/pipeline/test_golden_scenarios.py` — ADR-0020으로 제거된 `build_agent_pipeline_graph` 임포트.
- **FAILED 32개** — 대부분 `tests/pipeline/test_tools_phase1.py`(ADR-0022로 제거된 구 도구 `search.exact_lookup`/`aggregate`/`hybrid` 가정), `test_regression_incidents`, `test_thinking_mode_phase5` 드리프트. 사전 존재 stale.

## 1. 검증 게이트

```powershell
$env:PYTHONPATH='.'
# 환경: conda env ntis_domain_rag_chatbot (Python 3.12). conda run이 불안정하면 env python.exe 직접 호출:
#   C:/Users/<user>/anaconda3/envs/ntis_domain_rag_chatbot/python.exe -m pytest ...

# 스모크
python -m compileall -q apps

# 타깃 먼저 (07 문서 검증 영역)
python -m pytest tests/pipeline/test_planner_phase2.py tests/pipeline/test_agentic_workflow_phase3.py tests/pipeline/test_session_state.py -q

# 넓게 (stale 6개 제외 → 496 passed / 32 failed 유지 확인)
python -m pytest tests --continue-on-collection-errors `
  --ignore=tests/pipeline/test_agent_workflow.py --ignore=tests/pipeline/test_golden_scenarios.py `
  --ignore=tests/test_agent_intent_adapter.py --ignore=tests/test_agent_search_tool_materialization.py `
  --ignore=tests/test_qdrant_query_compiler.py --ignore=tests/test_query_intent_detail_anchor.py `
  -q -p no:cacheprovider
```

코드 변경 후 **passed가 496 미만으로 떨어지거나 failed가 32를 넘으면** 회귀로 본다.

> ⚠️ 의존성 매니페스트(requirements 등)가 저장소에 없다. 테스트 통과는 *환경 의존적*이다 — 의존성 환경을 명시·고정한 뒤에야 "검증됨"이라 말할 수 있다.

## 2. 주요 테스트 영역 (`tests/pipeline`)

`test_planner_phase2`, `test_active_planner_phase5`, `test_agentic_workflow_phase3`, `test_agentic_golden_suite`, `test_tools_phase1`, `test_search_task_contract`, `test_session_state`, `test_thinking_mode_phase5`, `test_regression_incidents`.

## 3. 리뷰 체크리스트 (핵심)

- **Planner:** Pass 1은 전체 인자 스키마를 보면 안 됨 · Pass 2는 선택 도구 스키마만 · 모르는 도구가 `ToolExecutor`에 닿으면 안 됨 · `action` ∈ {`call_tool`,`answer`,`clarify`} · 직접답변이 도메인 안전을 우회하면 안 됨.
- **Tool:** `ToolSpec` 추가 · 핸들러는 dict 반환 · `build_default_registry`에 등록 · 도구인자→`SearchTask` 변환 테스트 추가 · 프롬프트 노출 결과에 raw payload 없음.
- **Search:** `pjt_id`/`pjt_no` 구분 보존 · 타입 필터 보존 · `SearchAgent`는 의도 재해석 금지 · canonical evidence 출력 확인.
- **Answer/Critic:** `GuardDecision` 라우팅 확인 · repair 무한루프 금지(1회) · references/manifest rank가 보이는 근거와 매핑 · grounding-judge 실패 시 안전 폴백.
- **Session:** subject/manifest/focused-detail 독립 확인 · KV 왕복(인메모리 `SessionState`만 보지 말 것) · `SessionMemory` 압축 경로가 여전히 쓰이면 명시.

리뷰 출력은 **findings 먼저**(심각도, 소스 파일+라인, 행동 리스크, 구체적 수정), 요약·후속은 그다음.
