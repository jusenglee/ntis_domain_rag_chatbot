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
- Solar(vLLM) 스트리밍에서 reasoning이 먼저 출력되고 content가 지연되는 케이스가 발생할 수 있음(운영상 `content_delayed`로 분류).
- openai_compat_llm ↔ llm_streaming 간 “reasoning 청크 텍스트 전달 방식” 불일치 가능성 점검/정리 중.

### 4.2 백로그(Top)
- [ ] openai_compat_llm: (Planner vs Answer) stage별 reasoning 정책을 요청 단위로 고정(`reasoning_effort`, `include_reasoning`, `chat_template_kwargs`).
- [ ] llm_streaming: reasoning 청크가 `content=""`로 오고 별도 필드로 텍스트가 전달되는 케이스까지 파싱 계약 확정(+테스트).
- [ ] server3: Planner 체인에서 `llm.bind(...)`로 planner-only thinking/high 적용 + `format_instructions` 실제 프롬프트 삽입 누락 보완.
- [ ] RUNBOOK: `content_delayed` 트리아지(체크리스트/재현 절차) 보강 + 알람 후보 정의.

### 4.3 세션 로그(최근 5개만 유지)
- 2026-03-03: Solar(vLLM) 스트리밍 “reasoning 먼저/ content 지연” 이슈 원인 정리 + 스트리밍 계약/트리아지 문서 반영.
- 2026-02-27: DocOps 스캐폴드 생성(초기)

## 5. 스트리밍 장애 정책(요약)
- 스트림 실패 시 non-stream fallback 재시도는 하지 않는다(지연 최소화 우선).
- 운영 판정은 `ttft_any_ms`, `ttft_content_ms`, `deadline_exceeded`, `stream_content_emitted_chunks` 중심으로 본다.
- reasoning 모델(vLLM reasoning outputs)은 스트리밍에서 reasoning이 먼저 오고 `content`가 늦게 올 수 있으므로, “빈 응답”이 아니라 “content 지연”으로 분리 판정한다.
- 최종 사용자 출력에는 `stream_field in {None,"content"}`만 포함한다(=reasoning은 집계/메트릭용).
