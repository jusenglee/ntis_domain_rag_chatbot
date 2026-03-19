# Raw -> Context 예시

## 목적

이 문서는 NTIS 원천 payload가 prompt에 그대로 들어가면 왜 문제가 생기는지 보여주고,
`raw -> canonical -> rendered context` 경로를 안정적으로 구현할 수 있도록 예시를 제공한다.

기본 원칙:
- raw payload 전체 dump 금지
- retrieval view와 prompt view 분리
- 역할이 다른 필드는 역할대로 보존
- `output_type`에 맞는 context shape 사용

---

## Example 1. project detail

### 1.1 raw example
```json
{
  "PJT_ID": "1711015550",
  "PJT_NO": "2024R1A2B3001234",
  "STAN_YR": "2024",
  "PJT_NM": "AI 반도체 고도화 과제",
  "PJT_PRFRM_ORG_NM": "한국전자통신연구원",
  "RSCH_GOAL_ABSTRACT": "저전력 AI 반도체 설계 및 검증 기술을 개발한다.",
  "RSCH_ABSTRACT": "NPU 아키텍처, 메모리 최적화, 검증 자동화 파이프라인을 연구한다.",
  "title": "AI 반도체 고도화 과제",
  "answer_public": "연구목표 요약: 저전력 AI 반도체 설계 및 검증 기술을 개발한다.",
  "meta_flat": "PJT_ID 1711015550 ; PJT_NO 2024R1A2B3001234"
}
```

### 1.2 canonical example
```json
{
  "identity": {
    "doc_id": "1711015550",
    "doc_type": "project",
    "title": "AI 반도체 고도화 과제",
    "year": "2024"
  },
  "ids": {
    "pjt_id": "1711015550",
    "pjt_no": "2024R1A2B3001234"
  },
  "roles": {
    "lead_org_name": "한국전자통신연구원"
  },
  "facts": {
    "summary": "저전력 AI 반도체 설계 및 검증 기술을 개발하는 과제"
  },
  "evidence": [
    {
      "snippet": "저전력 AI 반도체 설계 및 검증 기술을 개발한다.",
      "source": "RSCH_GOAL_ABSTRACT",
      "score": 0.86
    }
  ],
  "provenance": {
    "source_table": "SH_PJT_MAIN",
    "source_pk": "1711015550",
    "visibility": "public"
  }
}
```

### 1.3 rendered detail context
```xml
<document>
  <identity>
    <doc_type>project</doc_type>
    <title>AI 반도체 고도화 과제</title>
    <pjt_id>1711015550</pjt_id>
    <pjt_no>2024R1A2B3001234</pjt_no>
    <year>2024</year>
  </identity>
  <facts>
    <summary>저전력 AI 반도체 설계 및 검증 기술을 개발하는 과제</summary>
  </facts>
  <evidence>
    <snippet source="RSCH_GOAL_ABSTRACT">저전력 AI 반도체 설계 및 검증 기술을 개발한다.</snippet>
  </evidence>
</document>
```

---

## Example 2. paper detail

### 2.1 canonical example
```json
{
  "identity": {
    "doc_id": "RST_9001",
    "doc_type": "perf",
    "title": "과학기술 정보서비스의 연계 및 통합에 관한 연구"
  },
  "ids": {
    "rst_id": "RST_9001",
    "pjt_id": "1711015550",
    "doi": "10.1000/xyz123"
  },
  "facts": {
    "summary": "정보서비스의 연계 구조를 다룬 논문이다."
  }
}
```

### 2.2 rendered detail context
```xml
<document>
  <identity>
    <doc_type>perf</doc_type>
    <title>과학기술 정보서비스의 연계 및 통합에 관한 연구</title>
    <rst_id>RST_9001</rst_id>
  </identity>
  <facts>
    <summary>정보서비스의 연계 구조를 다룬 논문이다.</summary>
  </facts>
</document>
```

---

## 나쁜 패턴

- raw payload 전체를 JSON dump로 삽입
- `meta_flat` 전체를 evidence처럼 사용
- `PJT_PRFRM_ORG_NM`, `PRTCP_ORG_NM`, `BLNG_ORG_NM`를 모두 하나의 `org` 필드로 합침
- `PJT_NO`만 있는데 instance join처럼 처리
- 사람 이름을 ids_map의 식별자처럼 사용


