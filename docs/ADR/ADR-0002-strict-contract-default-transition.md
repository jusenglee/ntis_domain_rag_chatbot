# ADR-0002: Strategy 불변 계약 strict 기본값 전환(단계적)

- 상태: Draft
- 날짜: 2026-03-09
- 관련 문서: `docs/CONTRACT.md`, `docs/RUNBOOK.md`, `docs/GOLDEN_TESTS.md`, `docs/NTIS_RAG_Search_Strategy_v1_2.md`

## 배경
현행 구현은 JOIN 계약 검증과 필터 컴파일이 견고하나, 실행 기본값은 compat 경로(`planner invalid fallback`, 일부 late-stage warning)가 활성화되어 있다.
문서 원칙(실행 레이어 전략 재결정 금지, fail-close)과 런타임 기본값 간 정합성이 완전하지 않다.

## 결정(초안)
1. 기본 운영 원칙은 strict/fail-close를 목표 상태로 유지한다.
2. 다만 운영 안정성을 위해 전환은 단계적으로 수행한다.
   - 1단계: 관측성 표준화(정책/변형 로그 고정)
   - 2단계: `RAG_PLANNER_INVALID_FALLBACK` 기본값 0 전환
   - 3단계: parser/validator/normalizer의 전략 자동 보정 경로 단일화
   - 4단계: people/org forbidden relation의 upstream 조기 차단 강화

## 근거
- planner_contract/rag_pipeline/query_intent/filters 교차 점검에서,
  - 계약 검증 강도는 높음
  - 불변 계약 측면에서는 fallback/보정 통로가 잔존
- people/org relation 금지 가드는 executor에도 있으나, 정상 경로 주 방어선은 upstream 차단임

## 영향
- 장점: 계약-실행 정합성 향상, 디버깅 복잡도 감소, 회귀 기준 명확화
- 리스크: strict 전환 시 기존 compat 의존 질의의 실패율 증가 가능

## 대응
- strict/compat 이원 테스트를 병행한다.
- 골든 질의를 정책 축( strict/fallback/promotion/chat )과 질의 축( ID/relation/복합 )으로 분리해 회귀를 관리한다.

## 오픈 이슈
- 전략 필드 자동 보정의 허용 범위를 어디까지 둘지(운영 플래그 vs 완전 제거)
- fallback/promotion/chat fallback 간 상호작용 로깅 표준의 최종 스키마
