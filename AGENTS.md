# AGENTS.md

## Repository identity
- repo: `ntis_domain_rag_chatbot`
- branch: `고도화`

## Mission
이 저장소는 NTIS 도메인 RAG / 챗봇 프로젝트다.
Codex의 목적은 다음 3가지를 반복하는 것이다.
1. 프로젝트를 계속 살핀다.
2. 프로젝트를 작은 단위로 계속 고도화한다.
3. 다음 단계 설계안과 ADR/RFC를 계속 제안한다.

## Standing loop
A. 현재 브랜치/HEAD 확인
B. 구조, 프롬프트, 평가, 로그, 문서를 읽음
C. 가장 값비싼 문제가 아니라 가장 안전한 다음 개선 1개를 고름
D. 작은 패치 또는 문서 제안을 만듦
E. 검증 수행
F. 다음 루프를 위한 handoff 문서를 갱신

## Non-negotiables
- planner가 정한 strategy는 단일·불변이다.
- answer stage에서 mode / relation / target_cols / join_key_mode를 재결정하지 않는다.
- SEARCH는 recall 우선이며 server-side must를 넣어서는 안 된다.
- LOOKUP / JOIN은 정확도 우선이며 server-side 게이트를 유지해야 한다.
- 사람/기관 기반 질의는 기본적으로 LOOKUP으로 다룬다.
- BM25-only 우회 금지.
- fallback chat, mode 변경, 재시도성 전략 변경 금지.
- 근거가 약하면 보수적으로 답한다.
- prompts / docs / evals / runbooks도 코드와 동일한 중요도로 다룬다.

## Mandatory preflight for every run
```bash
git rev-parse --show-toplevel
git rev-parse --abbrev-ref HEAD
git rev-parse HEAD
```

실행 결과가 아래와 다르면 반드시 보고만 하고 멈춘다.
- expected branch: `고도화`

## Role permissions
### Watcher
- production code 수정 금지
- 보고서/문서 갱신만 허용
- 목표: drift, regression, stale docs, weak evals, fragile prompts, observability gap 탐지

### Improver
- 1회 실행당 패치 1개만 허용
- 가능하면 5개 파일 이내 수정
- 관련 검증이 없으면 패치하지 않는다
- 대규모 리팩터링 금지

### Architect
- production code 수정 금지
- ADR / RFC / migration note만 작성
- 설계 제안은 staged migration 형태로 작성

## Always inspect these areas first
- planner prompt / planner assembler
- answer-generation prompt / verifier / repair prompt
- fallback / degraded response logic
- retrieval strategy compiler
- SEARCH / LOOKUP / JOIN tests
- docs/CODEX_CONTEXT.md
- docs/SESSION_HANDOFF.md
- docs/GOLDEN_TESTS.md
- logs or risk notes if present

## Preferred outputs for every run
1. what was inspected
2. what was found
3. what was changed
4. what was validated
5. what remains risky
6. what should be done next

## Validation policy
아래 명령은 아직 확실하지 않음이다. 실제 repo에서 발견되면 대체한다.
- `pytest -q`
- `pytest -q -k rag`
- `python -m pytest`
- `ruff check .`
- `python -m compileall .`

검증 명령을 실제 repo 기준으로 찾으면 docs/SESSION_HANDOFF.md에 갱신한다.

