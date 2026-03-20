---
name: ntis-rag-context-refine
description: NTIS RAG에서 raw source data를 canonical schema로 정리하고, output_type별 prompt view로 렌더링하기 위한 Codex 작업 지침.
---

# ntis-rag-context-refine

## 목적

이 스킬은 NTIS Domain RAG에서 **raw source data → canonical evidence → rendered prompt view** 경로를 안정적으로 유지하도록 돕는다.
목표는 단순 문자열 정리가 아니라, **원천 의미를 보존한 채 검색용 필드와 생성용 문맥을 분리**하는 것이다.

이 스킬은 다음 전제를 따른다.
- 저장소 기준선은 **retrieval-first + v3 contract**다.
- planner / contract / canonical evidence / renderer가 현재 동작의 기준선이다.
- raw payload, retrieval view, prompt view는 서로 다른 artifact다.

## 언제 쓰는가

다음 작업을 할 때 이 스킬을 사용한다.

- raw payload를 prompt 문맥으로 직접 넣고 있는 코드를 정리할 때
- retrieval 결과를 canonical schema로 바꾸는 매퍼를 손볼 때
- `output_type`별 문맥 구조를 분리하거나 renderer를 수정할 때
- 사람/기관/과제/성과 키 의미가 섞이지 않도록 점검할 때
- evidence shaping, canonical evidence, render profile을 리팩토링할 때
- prompt-facing 문서를 업데이트할 때

## 소스 오브 트루스

작업 전에 아래 파일을 우선 본다.

### 코드 기준선
- `apps/api/services/request_facade.py`
- `apps/api/services/planner_service.py`
- `apps/core/planner_contract.py`
- `apps/core/rag_runtime_prelude.py`
- `apps/core/rag_pipeline.py`
- `apps/core/rag_base_orchestration.py`
- `apps/core/rag_join_orchestration.py`
- `apps/core/canonical_evidence.py`
- `apps/api/services/rag_result_assembly.py`

### 문서 기준선
- `README.md`
- `CONTRACT.md`
- `RUNBOOK.md`
- `GOLDEN_TESTS.md`
- `PLANNER_PARAMETER_REFERENCE.md`

### 참고 전용 문서
- `NTIS_RAG_Search_Strategy_v1_2.md`
  - 역사/배경 참고용이다.
  - 현행 runtime invariant의 정본으로 사용하지 않는다.

## 핵심 불변 조건

### 1. 식별자 의미 불변
- `pjt_id`는 과제 instance key다.
- `pjt_no`는 과제 group key다.
- `rst_id`는 성과 instance key다.
- `pjt_id`와 `pjt_no`를 같은 값처럼 다루지 않는다.

### 2. 역할 의미 불변
- `lead_org_name` = 수행기관
- `participant_org_name` = 참여기관
- `people_affiliation_org_name` = 참여인력 소속기관
- 세 기관 역할은 하나의 `org` 필드로 합치지 않는다.

### 3. 사람 의미 불변
- `participant_researcher_name`은 사람 이름 role slot이다.
- `person_id` / `hm_id`는 사람 ID다.
- 사람 이름을 ids_map의 식별자처럼 임의 승격하지 않는다.

### 4. 실행 불변
- planner는 질의마다 하나의 strategy를 확정한다.
- executor는 확정된 strategy를 재해석하거나 새로 발명하지 않는다.
- `SEARCH`, `LOOKUP`, `JOIN`의 경계는 lower execution layer에서 바꾸지 않는다.

### 5. 렌더링 불변
- retrieval view와 prompt view는 다르다.
- prompt context는 canonical schema를 거쳐 renderer에서 조립한다.
- `output_type`이 renderer fieldset과 evidence shape를 결정한다.
- `summary`, `detail`, `list`, `stats`, `relation`, `comparison`, `series`는 같은 context shape를 재사용하지 않는다.

## Canonical Schema 기준

작업 시 최소한 아래 구조를 유지한다.

```python
CanonicalDoc = {
    "identity": {
        "doc_id": "...",
        "doc_type": "project|perf|qna|manual",
        "title": "...",
    },
    "ids": {
        "pjt_id": "...",
        "pjt_no": "...",
        "rst_id": "...",
    },
    "roles": {
        "lead_org_name": "...",
        "participant_org_names": [],
        "people_affiliation_org_names": [],
        "participant_researcher_names": [],
    },
    "facts": {
        "summary": "...",
        "year": 2024,
        "status": "...",
        "key_metrics": [],
    },
    "evidence": [
        {"snippet": "...", "source": "...", "score": 0.0}
    ],
    "provenance": {
        "source_table": "...",
        "source_pk": "...",
        "last_verified": "...",
        "visibility": "public|internal",
    },
}
```

### 슬롯 의미
- `identity`: 문서 정체성
- `ids`: 조인, 필터, 참조에 쓰이는 식별자
- `roles`: 기관/사람 역할 슬롯
- `facts`: 답변에 직접 쓰이는 핵심 사실
- `evidence`: 실제 근거 snippet
- `provenance`: 출처, 검증시점, 공개 범위

## 작업 절차

### 1. 현재 파이프라인 위치 파악
먼저 아래를 찾는다.
- retrieval 단계에서 가져오는 필드
- filter key / lexical field / dense field
- canonical evidence 생성 지점
- prompt 직전 renderer 또는 context assembly 지점
- `output_type` 분기 지점

### 2. artifact 구분
수정 대상이 아래 중 어디인지 명확히 적는다.
- raw source payload
- retrieval view
- canonical evidence
- rendered prompt view
- answer-facing text

이 다섯 개를 섞지 않는다.

### 3. raw → canonical 매핑 점검
아래를 우선 점검한다.
- `PJT_ID → ids.pjt_id`
- `PJT_NO → ids.pjt_no`
- `rst_id / RST_ID → ids.rst_id`
- `org_nm / PJT_PRFRM_ORG_NM → roles.lead_org_name`
- `prtcp_org[].org_nm / PRTCP_ORG_NM → roles.participant_org_names`
- `prtcp_mp[].blng_org_nm / BLNG_ORG_NM → roles.people_affiliation_org_names`
- `prtcp_mp[].hm_nm / HM_NM → roles.participant_researcher_names`

### 4. retrieval view와 prompt view 분리
retrieval view는 검색과 랭킹을 위한 구조다.
- `title`
- `answer_public` 또는 `content`
- `meta_flat`
- `tags`
- filter key

prompt view는 모델에게 보여줄 근거 구조다.
- identity
- roles
- facts
- evidence
- provenance

retrieval view를 prompt에 그대로 넣지 않는다.

### 5. output_type별 renderer 연결
다음 출력 유형을 분리해서 본다.
- `summary`
- `detail`
- `list`
- `stats`
- `relation`
- `comparison`
- `series`

`output_type`은 답변 말투가 아니라 **evidence presentation shape**를 결정한다.

### 6. 문서와 테스트 동시 갱신
다음이 바뀌면 문서도 함께 바꾼다.
- contract / strategy / filter / query intent / runtime compile
- canonical mapping / render fieldset / output_type propagation
- golden baseline / invariant / no-result policy

## 좋은 패턴

### 좋은 패턴 1. project detail
- raw payload에서 `PJT_ID`, `PJT_NO`, 수행기관, 목표 요약을 가져온다.
- canonical evidence에서 `identity`, `ids`, `roles`, `facts`, `evidence`, `provenance`로 정리한다.
- rendered detail context에서는 필요한 항목만 XML/구조화 context로 내린다.

### 좋은 패턴 2. perf detail
- `rst_id`, `pjt_id`, `doi`, title, summary를 canonical evidence에 정리한다.
- relation/detail 출력에서는 성과 식별자와 핵심 사실만 유지한다.

## 금지 패턴

- raw payload 전체를 prompt에 JSON dump로 삽입
- `meta_flat` 전체를 evidence처럼 사용
- `PJT_PRFRM_ORG_NM`, `PRTCP_ORG_NM`, `BLNG_ORG_NM`를 하나의 `org` 필드로 병합
- `PJT_NO`만 있는데 instance join처럼 처리
- 사람 이름을 ids_map의 resolved identifier처럼 사용
- retrieval metadata 전체를 LLM에 직접 노출
- `summary`, `detail`, `stats`, `relation`에 같은 context template 재사용

## 구현 시 체크리스트

### 코드 수정 전
- 현재 source of truth 문서 확인
- raw / retrieval / canonical / rendered 중 어디를 수정하는지 명시
- `output_type` 영향 범위 확인

### 코드 수정 중
- `pjt_id`와 `pjt_no` 분리 유지
- 기관 역할 슬롯 분리 유지
- 사람 이름과 사람 ID 분리 유지
- canonical evidence를 거치지 않는 direct prompt dump 금지
- renderer fieldset과 output_type 연결 유지

### 코드 수정 후
- `python -m py_compile` 또는 동등 검증
- 관련 pytest 실행
- golden regression 실행
- `output_type` propagation 확인
- join/filter contract 확인
- UTF-8 무결성 확인

## 문서를 같이 갱신해야 하는 경우

- contract / strategy 변경
  - `CONTRACT.md`
  - `GOLDEN_TESTS.md`
  - `README.md`
- triage / 운영 변경
  - `RUNBOOK.md`
- validation / env 변경
  - `ENVIRONMENT.md`
  - `RUNBOOK.md`
- canonical evidence / renderer / output_type 변경
  - `README.md`
  - `GOLDEN_TESTS.md`
  - 관련 설계 문서
- 큰 설계 결정 변경
  - ADR 추가 또는 갱신

## 한 줄 기준

이 스킬의 목적은 **원천 의미를 바꾸지 않고 raw payload를 canonical evidence로 정리한 뒤, output_type에 맞는 prompt view로 안전하게 렌더링하는 것**이다.
