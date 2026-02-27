# ChatGPT Pro 운영 가이드 — 문서화 + 캐시(Projects) 최적화

이 문서는 승주님의 NTIS RAG 프로젝트를 ChatGPT Pro로 운영할 때,
**대화가 아니라 문서(SSoT)가 ‘캐시’가 되도록** 만드는 실전 루틴입니다.

---

## 1) 기본 전략: “대화는 휘발, 문서는 캐시”
- 대화(L0): 로그 붙여넣고 원인 좁히는 공간 (언제든 잊혀도 됨)
- 문서(L1/L2): 계약/결정/재현 장치 (다음 세션이 읽는 캐시)

이 레포에서는 `docs/` 가 그 캐시입니다.

---

## 2) ChatGPT Project 세팅(권장)
### 2.1 Project-only memory
장기 프로젝트는 외부 대화가 섞이면 품질이 흔들립니다.  
가능하면 프로젝트를 “폐쇄된 캐시”로 운영하세요.

### 2.2 업로드 파일(40개 제한 고려)
**필수(캐시 문서)**
- `docs/README.md`
- `docs/CONTRACT.md`
- `docs/RUNBOOK.md`
- `docs/GOLDEN_TESTS.md`

**정책 원문**
- `NTIS_RAG_Search_Strategy_v1_1.md`

**코드 레퍼런스(최소)**
- `rag_pipeline.py`
- `rag_parts/planner_contract.py`
- `rag_parts/filters.py`
- `rag_parts/query_intent.py`
- `rag_parts/pipeline_steps.py`
- `schemas.py`
- `server3.py`

> 위 조합이면 “계약/전략/검증/실행 경계”를 대화 안에서 바로 짚을 수 있습니다.

---

## 3) 세션 운영 루프(이게 핵심)
### 3.1 세션 시작 프롬프트(복붙)
```txt
docs/README.md + docs/CONTRACT.md를 읽고,
오늘 작업의 우선순위와 리스크를 정리해줘.
그리고 오늘 바뀌면 반드시 문서에 남겨야 할 항목을 체크리스트로 뽑아줘.
```

### 3.2 디버깅 프롬프트(로그/코드 붙였을 때)
```txt
아래 로그/코드를 기준으로,
(1) 어떤 레이어(Planner/Validator/Compile/Execute/Rerank/LLM)에서 문제가 발생했는지,
(2) 어떤 계약(invariant)이 깨졌는지,
(3) 재현 가능한 최소 입력(질의+hint+env)과
(4) docs/RUNBOOK.md에 남길 트리아지 항목(패치 블록)
을 만들어줘.
```

### 3.3 세션 종료 프롬프트(복붙)
```txt
지금 세션 결과를 문서 캐시에 반영해줘.

- docs/README.md: 상태/백로그/세션로그 업데이트 패치
- docs/CONTRACT.md: 계약이 바뀌면 패치
- docs/RUNBOOK.md: 장애 대응/관측성 바뀌면 패치
- docs/GOLDEN_TESTS.md: 골든 케이스/기대 invariants 바뀌면 패치
- docs/ADR/ADR-XXXX.md: 큰 결정이 있으면 초안

전부 “복붙 가능한 패치 블록”으로 줘.
```

---

## 4) “메모리”를 어디까지 쓸까?
- Saved memory(저장 메모리)는 **말투/포맷 선호 같은 장기 상수**만.
- 프로젝트 스펙/계약/로그는 메모리가 아니라 **docs/**에 둡니다.

---

## 5) 파일 제한을 이기는 요령
- ‘파일 수’ 제한은 금방 옵니다(특히 로그를 파일로 저장하면).
- 그래서:
  - 로그는 **대화에 붙이고**, 최종 결론만 docs에 패치
  - 코드도 전부 업로드하지 말고 “계약 경계 파일”만 업로드

---

## 6) Pro 모델 선택 팁(실전)
- 문서 패치/요약/정리: Instant/Auto
- 계약 충돌/설계 결정(ADR): Thinking
- (주의) GPT‑5.2 Pro(모델)는 Memory/Canvas 등을 못 써서 문서화 워크플로우에는 불리할 수 있음

