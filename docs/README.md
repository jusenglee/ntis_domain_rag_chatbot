# NTIS RAG 문서 안내

이 문서 묶음은 NTIS Domain RAG의 현재 실행 기준, 운영 절차, 회귀 점검 기준을 설명한다.
문서는 현재 동작을 기준으로 유지하며, 과거 설명보다 현재 계약과 운영 기준을 우선한다.

## 먼저 읽을 문서

1. `01_아키텍처와_흐름.md`
2. `02_실행계약과_전략규칙.md`
3. `03_운영과_환경.md`
4. `04_회귀기준과_점검.md`
5. 필요 시 `05_유지보수와_확장.md`

## 현재 기준선

- 시스템은 retrieval-first 구조를 따른다.
- planner contract와 runtime execution truth는 엄격하게 유지한다.
- `SEARCH`, `LOOKUP`, `JOIN` 의미를 lower layer가 바꾸지 않는다.
- raw payload, canonical evidence, prompt view를 같은 artifact처럼 다루지 않는다.
- `pjt_id`와 `pjt_no`는 절대 같은 의미로 취급하지 않는다.

## Source Of Truth

- 요청 해석: `apps/api/services/request_facade.py`
- planner/service: `apps/api/services/planner_service.py`, `apps/api/services/planner_runtime.py`
- contract: `apps/core/planner_contract.py`
- runtime prelude: `apps/core/rag_runtime_prelude.py`
- pipeline/orchestration: `apps/core/rag_pipeline.py`, `apps/core/rag_join_runtime.py`
- evidence shaping: `apps/core/canonical_evidence.py`
- answer generation: `apps/api/services/answer_generation.py`

## 문서 사용 원칙

- 문서는 현재 코드와 운영 기준을 설명해야 한다.
- 같은 규칙을 여러 문서에 중복 서술하지 말고, 가장 권위 있는 문서에 먼저 적는다.
- 문서 변경이 필요한 코드 변경이면 같은 작업에서 함께 갱신한다.

## UTF-8 공통 규칙

- 모든 문서와 한글이 포함된 핵심 코드는 UTF-8로 유지한다.
- 한글이 포함된 파일은 shell 기반 전체 문자열 치환으로 수정하지 않는다.
- `Get-Content -Raw` 후 전체 재저장, 콘솔 문자열 치환, 코드페이지 의존 편집은 금지한다.
- 한글 파일은 최소 라인 패치 방식으로 수정하고, 수정 후 깨진 문자와 의도치 않은 대량 diff를 확인한다.
- 한글이 깨진 상태로 문서나 코드를 남기지 않는다.

## Streaming note

- detail/no-result short-circuit? answer generation ??? ??? ?? fallback ?? ???.
- UI blank response? ?? ?? ?? merge ???? synthetic `chunk`? 1? emit??.
