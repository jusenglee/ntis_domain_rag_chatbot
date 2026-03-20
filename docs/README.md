# NTIS RAG 문서 안내

이 문서 묶음은 NTIS Domain RAG 저장소의 **현행 기준 문서**를 한국어 중심으로 다시 정리한 버전입니다.

목표는 세 가지입니다.

1. 문서 수를 줄여 처음 읽는 사람이 길을 잃지 않게 한다.
2. 현재 기준선(v3, retrieval-first, strict contract)을 한글로 일관되게 설명한다.
3. 레거시 설명, 중복 문장, 영어 위주 표현을 걷어내고 운영·개발에 바로 쓰기 쉽게 만든다.

## 먼저 읽을 문서

아래 네 문서를 우선 읽으면 현재 시스템의 큰 틀을 파악할 수 있습니다.

1. `01_아키텍처와_흐름.md`
2. `02_실행계약과_전략규칙.md`
3. `03_운영과_환경.md`
4. `04_회귀기준과_점검.md`

그다음 유지보수와 확장 계획이 필요할 때 `05_유지보수와_확장.md`를 봅니다.

## 현재 기준선

현재 문서 묶음이 전제로 두는 기준은 다음과 같습니다.

- 시스템은 **retrieval-first** 구조이며, 채팅 UX는 그 위에 올라가는 표현 계층입니다.
- `IntentPayloadV3`가 활성 transport 계약이고, `strategy_version="v3"`가 활성 의미 계약입니다.
- `ids_map`에는 **resolved identifier**만 들어갑니다.
- `candidate_keys.project_key`는 **미해결 exact project key**를 담습니다.
- `project_key_policy=ambiguous_or`는 **exact OR exact discovery**를 뜻하며 자유 텍스트 fallback을 뜻하지 않습니다.
- `join_key_mode=deferred`는 합법적인 runtime 전략입니다.
- `lookup` / `join`에서 `reason=no_reranked`는 `normal_no_result`이고, `search`의 `reason=no_reranked`는 `strict_search`입니다.
- 사람/기관/관계/역추적/패턴 의미는 planner-first 원칙을 유지합니다.

## 문서 구성

### 1. 구조와 흐름
- `01_아키텍처와_흐름.md`
- 시스템 계층, artifact 경계, 요청 1건의 처리 흐름을 설명합니다.

### 2. 계약과 전략
- `02_실행계약과_전략규칙.md`
- SEARCH / LOOKUP / JOIN, planner stage, join 규칙, project key 규칙, no-result 정책을 설명합니다.

### 3. 운영과 환경
- `03_운영과_환경.md`
- 운영 로그, triage 순서, 필수 환경 변수, 검증 명령을 설명합니다.

### 4. 회귀와 점검
- `04_회귀기준과_점검.md`
- 골든 질의, 핵심 불변식, 파일별 점검 포인트를 설명합니다.

### 5. 유지보수와 확장
- `05_유지보수와_확장.md`
- 현재 구조 debt, 우선순위, retrieval robustness 확장 방향을 설명합니다.

### 6. 재편성 매핑
- `문서_재편성_매핑표.md`
- 기존 문서가 새 문서 어디로 흡수되었는지 정리합니다.

## 코드 기준 Source of Truth

- 요청 조립: `apps/api/services/request_facade.py`
- planner 조립: `apps/api/services/planner_service.py`, `apps/api/services/planner_runtime.py`
- 계약 검증: `apps/core/planner_contract.py`
- execution strategy compile: `apps/core/rag_runtime_prelude.py`
- runtime dispatcher: `apps/core/rag_pipeline.py`
- SEARCH/LOOKUP 실행: `apps/core/rag_base_orchestration.py`
- JOIN 실행: `apps/core/rag_join_orchestration.py`, `apps/core/rag_join_runtime.py`
- canonical evidence: `apps/core/canonical_evidence.py`
- 결과 조립: `apps/api/services/rag_result_assembly.py`
- 응답 생성: `apps/api/services/answer_generation.py`, `apps/api/services/answer_merge.py`

## 문서 사용 원칙

- 원문 표현보다 **현재 동작과 운영 판단**을 우선합니다.
- 같은 개념을 여러 문서에서 반복하지 않습니다.
- raw payload, canonical evidence, prompt view를 같은 artifact처럼 설명하지 않습니다.
- `pjt_id`와 `pjt_no`를 절대 같은 의미로 섞지 않습니다.
- 레거시 문서는 참고 자료일 뿐, 현행 계약 문서가 아닙니다.


## Final-answer prompt note
- ?? ??? system prompt? ?? `prompts/ntis_chatbot.md`? ????? ????.
- ?? ? `GEMMA_SYSTEM_PROMPT_PATH`, `SOLAR_SYSTEM_PROMPT_PATH`? ??? prompt ??? ??? ? ??.
