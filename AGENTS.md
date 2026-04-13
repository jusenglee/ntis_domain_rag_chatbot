# AGENTS.md

이 저장소에서 Codex가 작업할 때 반드시 따라야 하는 운영 지침이다.

## 1. 기본 원칙

이 프로젝트는 NTIS 원천 DB와 검색 인덱스를 기반으로 사용자 질의를 해석하고,
그 결과를 LLM과 RAG로 조합해 응답을 만드는 검색엔진 + 챗봇 UI 시스템이다.

따라서 아래 원칙을 항상 지킨다.
- 모든 문서는 UTF-8을 사용한다.
- 한글이 깨진 문서는 그대로 두지 않는다.
- 원천 의미를 바꾸는 임의 보정은 하지 않는다.
- 식별자 의미를 섞어서 다루지 않는다.
- `output_type`과 renderer의 역할을 분리한다.
- raw payload를 그대로 prompt에 넣지 않는다.

## 2. 먼저 읽어야 할 문서

- `docs/README.md`
- `docs/01_아키텍처와_흐름.md`
- `docs/02_실행계약과_전략규칙.md`
- `docs/03_운영과_환경.md`
- `docs/04_회귀기준과_점검.md`
- `docs/05_유지보수와_확장.md`
- `docs/SESSION_HANDOFF_*.md`가 있으면 함께 읽는다.
- `.agents/skills/ntis-rag-context-refine/SKILL.md`
- `.agents/skills/ntis-rag-context-refine/references/SKILL_revised_ko.md`

## 2-1. 신규 기여자용 빠른 진입점
## Mission
This repository is a chatbot / RAG application.
Your purpose is not just to write code, but to continuously improve production quality, answer quality, maintainability, and architectural clarity.

- 루트 `README.md`
- `docs/00_ONBOARDING.md`
- `apps/api/routes.py`
- `apps/api/services/request_facade.py`
- `apps/api/services/retrieval_workflow.py`
## Standing loop
1. Watch the project
2. Improve the project
3. Design the project
4. Repeat

## 3. 절대 섞으면 안 되는 의미
## Rules
- Inspect before patching
- Prefer small safe changes
- Never do broad rewrites without evidence
- Run all relevant checks after changes
- Keep outputs reviewable
- Treat prompts, docs, evals, and runbooks as part of the product
- Explicitly state uncertainty
- Never fake confidence when code or docs do not support a conclusion

- `pjt_id`는 과제 instance key다.
- `pjt_no`는 과제 group key다.
- `pjt_id`와 `pjt_no`는 같은 값처럼 취급하면 안 된다.
- 수행기관(`lead_org_name`), 참여기관(`participant_org_name`), 참여인력 소속기관(`people_affiliation_org_name`)은 서로 다른 의미다.
- 검색 보조 필드(`title`, `answer_public`, `meta_flat`)를 prompt context의 사실 필드와 혼동하면 안 된다.
- retrieval metadata 전체를 LLM에 직접 노출하면 안 된다.
## Priority order
1. user-visible bugs
2. correctness / safety issues
3. missing tests / evals
4. prompt and config drift
5. docs / runbook synchronization
6. maintainability
7. architecture proposals

## 4. 3층 artifact 구조
## Always inspect these areas
- prompt files
- chatbot answer generation
- verifier / repair logic
- fallback and degraded behavior
- RAG / retrieval contracts
- tests and evals
- logs / observability
- docs / ADR / RUNBOOK / GOLDEN_TESTS

이 저장소는 아래 3층 artifact를 전제로 한다.
1. gate artifact
2. assembled question analysis
3. execution strategy

현재 지원하는 prompt view는 다음과 같다.
- summary
- detail
- list
- stats
- relation
- comparison
- series

## 5. 변경 시 같이 볼 항목

- planner / contract / filter / join key / output_type 문서
- raw -> canonical 매핑 문서
- renderer fieldset 문서
- 운영 triage 및 summary 로그 문서
- golden test 및 회귀 기준
- env / runtime / validation entrypoint
- 관련 ADR 및 handoff 문서

## 6. 금지 사항

- raw payload 전체를 prompt에 dump하지 않는다.
- regex만으로 구조 의미를 대충 복원하지 않는다.
- SEARCH / LOOKUP / JOIN 등의 의미를 서로 바꾸지 않는다.
- gate artifact에 `deferred`를 넣지 않는다.
- lower layer가 새 전략 fallback을 발명하지 않는다.
- 사람/기관 이름을 ids_map에 임의로 넣지 않는다.

## 7. 변경 후 최소 검증

- syntax / import 오류 확인
- baseline checks 실행
- golden query 동작 확인
- `output_type` propagation 확인
- filter / join contract 확인
- 한글 인코딩 깨짐 여부 확인

## 8. 작업 보고에 포함할 내용

- 무엇을 바꿨는지
- 왜 바꿨는지
- raw -> canonical -> prompt 흐름에서 어떤 의미를 유지했는지
- 검증 결과
- 남은 리스크
- 추가로 보면 좋은 문서 또는 파일

## Required output for each task
- What was inspected
- What was found
- What changed
- What was validated
- What remains risky
- What should happen next
