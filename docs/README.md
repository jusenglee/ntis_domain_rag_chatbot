# ntis_domain_rag_chatbot — DocOps (ChatGPT Pro 문서화+캐시 운영)

> 목적: **대규모 RAG(SEARCH / LOOKUP / JOIN)** 파이프라인을 “대화”가 아니라 **문서 캐시(SSoT)** 로 운영하기.
>
> - 대화(L0): 디버깅/실험 (휘발)
> - 문서(L1): 계약/정책/현재상태 (영속, 단일 기준)
> - 재현장치(L2): Runbook/Golden tests/ADR (영속, 회귀)

## 0. 이 레포에서 문서가 ‘캐시’가 되는 이유

이 프로젝트는 레이어가 많습니다.

- 서버: `server3.py` (FastAPI + LangGraph)
- 파이프라인: `rag_pipeline.py`
- 전처리/의도분석: `rag_parts/query_intent.py`, `rag_parts/pipeline_steps.py`
- 계약/검증/컴파일: `rag_parts/planner_contract.py`, `rag_parts/filters.py`
- 검색/컨텍스트: `retrieval.py`
- 결과 계약: `rag_parts/result_contract.py`

그래서 **“어느 레이어에서 의미가 바뀌었는지”** 를 문서로 고정하지 않으면, 운영이 ‘촉’으로 변합니다.

## 1. 문서 구조(SSoT)

- `docs/README.md`
  - 현재 상태 / 백로그 / 세션 로그 / 링크
- `docs/CONTRACT.md`
  - Strategy Contract (플래너→실행 불변), 모드 규칙, JOIN 규칙, 필터 의미론, 에러코드
- `docs/RUNBOOK.md`
  - 장애 트리아지 / 관측성(로그 키) / 재현 절차 / 자주 터지는 패턴
- `docs/GOLDEN_TESTS.md`
  - “정답 문장”이 아니라 **계약 invariants** 를 회귀 테스트로 유지하기 위한 골든 질의 셋
- `docs/ADR/`
  - 중요한 결정(계약/정책/데이터모델)이 바뀔 때마다 1장씩 기록

## 2. ChatGPT Pro에서 ‘프로젝트 캐시’로 굴리는 운영 루틴

### 2.1 ChatGPT Project 세팅(권장)
1) ChatGPT에서 Project 생성: `NTIS-RAG (DocOps)`
2) 가능하면 **Project-only memory** 사용 (프로젝트 밖 맥락 오염 방지)
3) 아래 파일을 Project에 업로드(40개 제한 고려):

- `docs/README.md`
- `docs/CONTRACT.md`
- `docs/RUNBOOK.md`
- `docs/GOLDEN_TESTS.md`
- `NTIS_RAG_Search_Strategy_v1_1.md` (원문 정책)
- (코드 레퍼런스) `rag_pipeline.py`, `rag_parts/planner_contract.py`, `rag_parts/filters.py`, `rag_parts/query_intent.py`, `schemas.py`

### 2.2 세션 시작 프롬프트(복붙)
```txt
docs/README.md와 docs/CONTRACT.md를 기준으로
1) 현재 상태 5줄 요약
2) 오늘의 우선순위 Top 3
3) 리스크/불확실 3개
4) 오늘 바뀌면 문서에 반드시 남겨야 할 항목(계약/검증/로그/골든테스트)
을 뽑아줘.
```

### 2.3 세션 종료 프롬프트(복붙) — “문서 캐시 업데이트 엔진”
```txt
지금 세션 결과를 ‘문서 캐시’로 반영해줘.

반드시 아래 파일들에 대한 “복붙 가능한 패치 블록”을 만들어:
- docs/README.md: 상태/백로그/세션로그 업데이트
- docs/CONTRACT.md: 계약/불변성/금지 규칙이 바뀐 경우만
- docs/RUNBOOK.md: 트리아지/관측성/장애 대응이 바뀐 경우만
- docs/GOLDEN_TESTS.md: 기대 invariants가 바뀐 경우만

그리고 마지막에 “이번 세션에서 새 ADR이 필요한가?” 판단해서
필요하면 docs/ADR/ADR-XXXX 초안도 만들어줘.
```

## 3. 빠른 링크
- 운영 환경 변수: `ENVIRONMENT.md`
- 검색전략 원문: `NTIS_RAG_Search_Strategy_v1_1.md`

## 4. 상태/백로그/세션 로그(최근 5개만 유지)

### 4.1 현재 상태
- JOIN 계약(`head == relation target`, `join_key_mode` XOR, Hop2 key-only 필터) 자체는 코드/문서 정합이 높은 상태.
- 다만 실행 기본값은 여전히 compat 성격(`RAG_PLANNER_INVALID_FALLBACK=1`, `RAG_STRICT_STRATEGY_CONSISTENCY=0`)이어서 “문서상 strict 원칙”과 완전 일치하지 않음.
- 전략 재작성 경로가 `apply_planner_v2` 외에도 parser/validator/normalizer에 분산되어 있어 단일 책임 경계가 아직 미완.
- people/org relation JOIN 금지는 upstream에서 대부분 차단되지만, executor late guard는 기본 경고 경로가 남아 있음.

### 4.2 백로그(Top)
- [ ] **P0 관측성 표준화**: `policy_mode`, `planner_invalid_fallback`, `strict_strategy_consistency`, `promotion_mode`, `force_fallback_chat`, `strategy_mutation_stage` 로그를 공통 스키마로 고정.
- [ ] **P1 기본값 정합화**: `RAG_PLANNER_INVALID_FALLBACK=0` 기본 전환(호환모드는 opt-in).
- [ ] **P2 전략 재작성 인벤토리 정리**: `apply_planner_v2` + `normalize_planner_payload` + `validate_join_contract` + `normalize_intent`를 단일 책임 모델로 정리.
- [ ] **P3 people/org 조기 차단 강화**: parser/intent 단계에서 forbidden relation 확정 차단, executor는 최종 안전장치로 축소.
- [ ] **P4 회귀 고정**: strict/compat 정책 축 + 복합 질의 축 골든 테스트 분리.

### 4.3 세션 로그(최근 5개만 유지)
- 2026-03-09: planner_contract/rag_pipeline/filters/query_intent/전략문서 교차 점검 결과를 문서 캐시에 반영. 주요 결론은 “JOIN 계약은 강함, 불변 계약 기본값은 미정합”.
- 2026-03-09: 문서-주석 정합성 점검 수행. 코드 주석은 “문서상 strict 원칙 vs 현재 compat 경로 존재”를 명시하도록 갱신.
- 2026-03-09: strict 전환 관련 ADR 초안(단계적 전환 + 조기 차단 우선)을 추가.
- 2026-03-09: `metrics.py` API 계약 문서화(`/metrics`, `/metrics/stream`, None/로깅 정책, 환경 변수 기본값) + 함수 docstring 보강.
- 2026-03-03: Solar(vLLM) 스트리밍 “reasoning 먼저/ content 지연” 이슈 원인 정리 + 스트리밍 계약/트리아지 문서 반영.

## 5. 스트리밍 장애 정책(요약)
- 스트림 실패 시 non-stream fallback 재시도는 하지 않는다(지연 최소화 우선).
- 운영 판정은 `ttft_any_ms`, `ttft_content_ms`, `deadline_exceeded`, `stream_content_emitted_chunks` 중심으로 본다.
- reasoning 모델(vLLM reasoning outputs)은 스트리밍에서 reasoning이 먼저 오고 `content`가 늦게 올 수 있으므로, “빈 응답”이 아니라 “content 지연”으로 분리 판정한다.
- 최종 사용자 출력에는 `stream_field in {None,"content"}`만 포함한다(=reasoning은 집계/메트릭용).


## 최근 정책 메모
- 기관(ORG) 필터는 LOOKUP에서만 부분일치(MatchText + prefix 확장) 하드게이트를 사용합니다.
- 제목(TITLE)은 server-side 하드필터를 적용하지 않고 soft ranking 신호로만 사용합니다.
