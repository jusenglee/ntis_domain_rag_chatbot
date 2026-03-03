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

## 4. 세션 로그(최근 5개만 유지)
- 2026-02-27: DocOps 스캐폴드 생성(초기)



## Current Status

### ✅ Solar(vLLM) Streaming Stability (P0)
- Solar(vLLM) OpenAI-compatible streaming에서 **reasoning/content 분리 출력**이 확인됨.
- 스트림에서 `choices[0].delta.reasoning(/reasoning_content)`가 먼저 길게 나오고, 최종 답변은 `choices[0].delta.content`로 뒤늦게 출력됨.
- 기존 파서가 `delta.content`만 읽어 TTFT 가드에서 끊기던 문제를 해결하기 위해:
  - `openai_compat_llm.py`: reasoning/content를 모두 파싱하고 `stream_field` 태깅
  - `llm_streaming.py`: reasoning은 keepalive/TTFT용으로만 처리하고 final_text에는 content만 누적
  - `server3.py`: /query/stream에서 reasoning chunk를 클라이언트로 보내지 않도록 필터링
  - TTFT를 any/content로 분리하여 관측/트리아지 개선

### Known Good Behavior
- 스트리밍 SSE에서 `Content-Type: text/event-stream` + `object=chat.completion.chunk` 유지
- reasoning이 먼저 나오더라도 content가 나오기 전까지 스트림을 "죽었다"고 판단하지 않음
- 사용자 출력은 content만(Reasoning은 절대 노출 금지)

## Backlog

### P0
- [ ] Solar 모델에서 content가 지나치게 늦게 시작되는 케이스 튜닝
  - 옵션/템플릿/토큰예산/가드 정책을 기반으로 content 지연을 줄이는 실험

### P1
- [ ] Stream 메트릭을 Prometheus/로그에 일관된 필드로 노출 (`ttft_any_ms`, `ttft_content_ms`, `reasoning_chars`, `content_chars`)
- [ ] merge 정책에서 Solar 실패/경고 분류(`stream stalled` vs `content delayed`) 기반 규칙 고도화

## Session Log (Latest)

### 2026-03-03 Solar(vLLM) streaming output parser / TTFT 안정화
- 원인
  - Solar(vLLM)이 스트리밍에서 reasoning을 `delta.reasoning`로 먼저 방출하고 `delta.content`는 뒤늦게 생성
  - 기존 구현이 `delta.content`만 "텍스트"로 간주하여 TTFT 가드(예: 4500ms)에서 content가 나오기 전에 중단됨
- 해결
  - `openai_compat_llm.py`
    - `STREAM_FIELD_KEY="stream_field"` 추가
    - `delta.reasoning`/`delta.reasoning_content` 및 `delta.content`를 각각 `AIMessageChunk.additional_kwargs[stream_field]`로 태깅하여 스트림으로 전달
    - close coroutine 안전 처리(awaitable close/aclose)
    - EmptyStream 판단은 **content 기준** 유지(최종답이 없으면 실패)
  - `llm_streaming.py`
    - `stream_field == "reasoning"`은 keepalive/metrics만, final_text 누적 금지
    - `ttft_any_ms`(첫 reasoning 포함) / `ttft_content_ms`(첫 content) 분리
    - content=0이면 deadline_exceeded여도 fallback 허용("안내문만 반환" 방지)
  - `server3.py`
    - /query/stream에서 `stream_field=="reasoning"` chunk는 skip
    - stream_done_metrics/solar_stream_guard에서 any vs content 기준 분리 로그
