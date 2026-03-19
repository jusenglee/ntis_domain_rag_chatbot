---
name: ntis-rag-context-refine
description: Refine NTIS RAG context from raw source data into canonical schema and output_type-specific prompt views without changing source semantics.
---

# ntis-rag-context-refine

## 언제 쓰는가

NTIS RAG 문맥을 raw payload에서 canonical schema로 정리하고, 다시 `output_type`별 prompt view로 렌더링해야 할 때 사용한다.

## 작업 순서

### 1. 현재 파이프라인 위치 파악
- retrieval 단계에서 어떤 필드를 가져오는가
- vector / bm25 / filter key는 무엇인가
- prompt 직전에 어떤 context 구조를 쓰는가
- `output_type`별 분기점은 어디인가

### 2. 절대 섞이면 안 되는 semantic slot 확인
- `pjt_id`, `pjt_no`, `rst_id`
- `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`
- 사람 이름과 사람 ID
- ids_map와 filters의 경계

### 3. canonical schema 점검
- identity
- ids
- roles
- facts
- evidence
- provenance

### 4. renderer와 output_type 연결
- summary
- detail
- list
- stats
- relation

### 5. 구현 시 주의사항
- raw payload dump 금지
- retrieval용 필드와 prompt용 필드 혼용 금지
- 사람/기관 이름을 ids_map에 임의로 넣지 않기

### 6. 검증
- py_compile
- pytest
- golden regression
- output_type propagation 확인
- join/filter contract 확인
- UTF-8 무결성 확인

## 문서를 같이 갱신해야 하는 경우

- contract / strategy 변경 -> `CONTRACT.md`, `GOLDEN_TESTS.md`, `README.md`
- triage / 운영 변경 -> `RUNBOOK.md`
- validation / env 변경 -> `ENVIRONMENT.md`, `RUNBOOK.md`
- 설계 의사결정 변경 -> ADR