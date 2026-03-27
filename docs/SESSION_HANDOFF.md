# SESSION_HANDOFF.md

## Bootstrap checkpoint
- repo: `ntis_domain_rag_chatbot`
- working branch expected: `고도화`
- created_for: Codex Watcher / Improver / Architect

## What is confirmed
- 이 프로젝트는 NTIS RAG다.
- retrieval strategy는 SEARCH / LOOKUP / JOIN으로 분리된다.
- planner는 단일·불변 strategy를 내려야 한다.
- 사람/기관 질의는 기본 LOOKUP이다.
- group join은 `pjt_no`, instance join은 `pjt_id`를 쓴다.
- 운영 기본은 promotion disable, fallback chat off 이다.

## What is not yet confirmed
- 실제 planner prompt 파일 경로
- 실제 answer/verifier/repair prompt 파일 경로
- 실제 테스트 명령
- 실제 스트리밍 구현 파일 경로
- 실제 observability/logging 경로

## First tasks for Watcher
1. branch / head 일치 여부 확인
2. planner / answer / verifier / repair 파일 위치 식별
3. fallback / degraded response 로직 위치 식별
4. strategy drift 가능 경로 식별
5. docs와 코드의 불일치 목록화

## First tasks for Improver
1. Watcher 보고서에서 가장 작은 P0 또는 가장 값싼 P1 1건 선택
2. 관련 검증 명령 확인
3. 1개 패치만 수행
4. regression 또는 golden test 추가
5. 다음 위험 포인트 handoff 남기기

## First tasks for Architect
1. 현재 answer pipeline 문서화
2. prompt stack과 verifier stack을 시스템 구성요소로 정리
3. staged ADR 작성
4. production code 수정은 하지 않기

## Candidate first improvement
- 스트리밍 경로에서 async close await 누락 여부 확인
- emitted_chunks=0 판정 로직과 ttft deadline 상호작용 확인

## Update rule
이 파일은 매 실행 후 아래를 append 한다.
- date/time
- branch/head
- inspected files
- findings
- changes
- validations
- next best task
