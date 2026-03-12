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
- `docs/NTIS_RAG_Search_Strategy_v1_2.md` (원문 정책)
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
- 검색전략 원문: `docs/NTIS_RAG_Search_Strategy_v1_2.md`

## 4. 상태/백로그/세션 로그(최근 5개만 유지)

### 4.1 현재 상태
- planner 내부 생성 경로는 기본적으로 **stagewise** 이다: `RAG_PLANNER_PIPELINE=staged` 기본값, 외부화된 프롬프트(`planner_stage1_v1.md`, `planner_stage2_v1.md`, `planner_legacy_v2.md`), stage별 스키마(`PlannerStage1Decision`, `PlannerStage2Slots`) 및 stage별 관측 로그(`PLANNER.STAGE1`, `PLANNER.GATE`, `PLANNER.STAGE2`, `PLANNER.REGATE`, `PLANNER.ASSEMBLE`)가 코드에 연결되어 있다.
- executor-facing 최종 계약(`QuestionAnalysisV2`)의 shape는 유지되며, stagewise는 **internal planner generation path** 만 변경함.
- 현재 운영 리스크 1: stagewise가 기본 경로이지만, `RAG_PLANNER_PIPELINE=legacy` 또는 `PLANNER_STAGEWISE_ENABLED=false`로 여전히 legacy fallback을 탈 수 있으므로 배포 설정 일관성이 필요함.
- 현재 운영 리스크 2: `apply_planner_v2()`가 반영한 stagewise assembled strategy와 `rag_pipeline.py`의 strict/guardrail 코드가 충돌하지 않는지 회귀 테스트로 계속 고정해야 함.
- 현재 운영 리스크 3: 문서와 운영 설명이 아직 compat/legacy 중심 표현을 일부 유지하고 있어 실제 기본값(`staged`, strict, promotion off)과 혼동될 수 있음.

### 4.2 백로그(Top)
- [ ] **P0 문서/운영 정합화**: `RAG_PLANNER_PIPELINE=staged` 기본값, `PLANNER.REGATE` 존재, staged runtime fallback/promotion 차단을 운영 문서에 반영.
- [ ] **P0 stagewise 후단 guardrail 회귀 고정**: `apply_planner_v2()`/runtime plan 경로에서 stagewise assembled strategy가 다시 바뀌지 않도록 테스트를 보강.
- [ ] **P1 planner cleanup**: legacy fallback machinery와 staged 기본 경로를 더 명확히 분리하고, dead compat 경로를 축소.
- [ ] **P1 support/startup 정합화**: planner gate의 `ntis_supports_v1`와 startup/bootstrap 컬렉션 설정 일치 여부를 최종 점검.
- [ ] **P2 stage 프롬프트/validator 확장**: stage1 few-shot 보강(support/perf_project/stats/detail), stage2 filter allowlist·역할 슬롯 강화, `ids_map` semantic validator 확장.

### 4.3 세션 로그(최근 5개만 유지)
- 2026-03-12: planner giant prompt를 external prompt 파일(`planner_stage1_v1.md`, `planner_stage2_v1.md`, `planner_legacy_v2.md`)로 분리하고 prompt cache loader를 추가.
- 2026-03-12: planner를 stage1 분류 → deterministic gate → stage2 슬롯 추출 → final assemble 구조로 분리하는 stagewise skeleton을 코드에 반영.
- 2026-03-12: `PlannerStage1Decision`, `PlannerStage2Slots`, `_sanitize_ids_map_semantics()`, `PLANNER.STAGE1/GATE/STAGE2/REGATE/ASSEMBLE`, `PLANNER.IDS_MAP.INVALID_VALUE` 관측 로그를 추가.
- 2026-03-12: 후속 점검 결과, `RAG_PLANNER_PIPELINE=staged` 기본화, `PLANNER.REGATE` 반영, staged runtime invalid fallback/promotion 차단, `output_type` 전파, extractor slot 보존이 코드에 적용됨. 남은 이슈는 문서 정합화와 회귀 테스트 보강 중심으로 재정의.
- 2026-03-12: staged planner compose/regate, `output_type` propagation, runtime strategy lock 관련 회귀 테스트 파일을 추가.
- 2026-03-09: strict/compat 계약 정합성과 fallback/보정 경로 잔존 이슈를 문서 캐시에 반영.

## 5. 스트리밍 장애 정책(요약)
- 스트림 실패 시 non-stream fallback 재시도는 하지 않는다(지연 최소화 우선).
- 운영 판정은 `ttft_any_ms`, `ttft_content_ms`, `deadline_exceeded`, `stream_content_emitted_chunks` 중심으로 본다.
- reasoning 모델(vLLM reasoning outputs)은 스트리밍에서 reasoning이 먼저 오고 `content`가 늦게 올 수 있으므로, “빈 응답”이 아니라 “content 지연”으로 분리 판정한다.
- 최종 사용자 출력에는 `stream_field in {None,"content"}`만 포함한다(=reasoning은 집계/메트릭용).


## 최근 정책 메모
- 기관(ORG) 필터는 LOOKUP에서만 부분일치(MatchText + prefix 확장) 하드게이트를 사용합니다.
- 제목(TITLE)은 server-side 하드필터를 적용하지 않고 soft ranking 신호로만 사용합니다.
