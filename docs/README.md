# ntis_domain_rag_chatbot 문서 허브

이 문서는 운영자가 RAG 파이프라인을 빠르게 파악하고 재현하기 위한 진입점입니다.

## 1) 아키텍처 요약

- API/오케스트레이션: `server3.py`
- 실행 파이프라인: `rag_pipeline.py`
- 계약/검증: `rag_parts/planner_contract.py`, `rag_parts/result_contract.py`
- 검색 계층: `retrieval.py`
- 의도 payload 계약: `docs/intent_payload_v2_schema.md`

핵심 운영 모드는 `SEARCH / LOOKUP / JOIN`이며, 플래너 산출을 실행 레이어가 검증/컴파일 후 수행합니다.

## 2) 로컬 실행 (예시)

```bash
# 필수 예시 (환경에 맞게 조정)
export OPENAI_API_KEY=...
export QDRANT_URL=http://localhost:6333
export QDRANT_API_KEY=...

# RAG 계약 관련 주요 스위치
export RAG_PLANNER_INVALID_FALLBACK=1
export RAG_PROMOTION_MODE=disable
export RAG_PROMOTION_MAX_DEPTH=1
export RAG_EMPTY_RESULT_CONTRACT=1
export RAG_FORCE_FALLBACK_CHAT=0

python server3.py
```

운영 ENV 상세는 `docs/ENVIRONMENT.md`, 키 매핑 계약은 `docs/project_key_env_contract.md`를 참고하세요.

## 3) 정책/계약 문서

- 실행 계약(단일 전략, fallback/promotion/result contract): `docs/CONTRACT.md`
- intent payload 송신/수신 스키마: `docs/intent_payload_v2_schema.md`
- 전략 배경 문서(v1.2): `docs/NTIS_RAG_Search_Strategy_v1_2.md`
- 장애 대응/관측성: `docs/RUNBOOK.md`
- 골든 테스트 운영: `docs/GOLDEN_TESTS.md`

## 4) 관측/디버깅 로그 키

문서/코드 동기화 시 아래 이벤트를 우선 관측합니다.

- Planner/계약
  - `RAG.PLAN.INVALID_STRATEGY`
  - `RAG.PLAN.FALLBACK_ON_CONTRACT_VIOLATION`
- Promotion
  - `RAG.PROMOTION`
  - `RAG.PLAN_PROMOTED`
- Result contract
  - `RAG.CONTRACT.MIN_RERANKED`
  - `RAG_EMPTY_RESULT_CONTRACT`(error_code)
- 서버 응답 보조 경로
  - `fallback_context` 포함 여부 (`server3.py` 응답 payload)

## 5) Golden Test 실행/갱신 규칙

`docs/GOLDEN_TESTS.md`의 모드별 대표 질의를 기준으로 아래 변경 시 반드시 골든 기대값을 갱신합니다.

- `RAG_PLANNER_INVALID_FALLBACK` on/off
- `RAG_PROMOTION_MODE` on/off, `RAG_PROMOTION_MAX_DEPTH`
- `RAG_EMPTY_RESULT_CONTRACT`, `RAG_FORCE_FALLBACK_CHAT` 정책 변경
- 최소 rerank 임계값(`RAG_MIN_RERANKED_*`) 또는 프리셋 변경

권장 검증 축:

1. 기대 plan(mode/action/relation/join_key_mode)
2. 기대 filter/ids_map 컴파일 결과
3. 결과 계약 통과/실패 및 반환 정책(예외 vs fallback_chat)

## 6) 현재 문서-코드 정합 메모

- 문서상 “fallback 전략 금지” 취지가 있어도, 현재 기본값은 `RAG_PLANNER_INVALID_FALLBACK=1`입니다.
- 문서상 promotion이 passthrough로 소개된 버전이 있었으나, 현재 `rag_pipeline.py`에는 SEARCH→LOOKUP/JOIN 승격 재실행 로직이 존재합니다.

운영 의사결정 시 위 두 항목을 우선 확정하고, `docs/CONTRACT.md`를 기준 계약으로 유지하세요.
