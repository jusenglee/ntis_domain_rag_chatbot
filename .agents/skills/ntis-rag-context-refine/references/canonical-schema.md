# 정규 스키마 (Canonical Schema)

## 목적

원천 필드를 LLM 친화적인 semantic slot으로 정리하기 위한 중간 schema다.

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

## 슬롯 설명

- `identity`: 문서 정체성
- `ids`: 조인/필터/참조 식별자
- `roles`: 기관/사람 역할 분리
- `facts`: 답변에 직접 쓰이는 사실
- `evidence`: 실제 근거 snippet
- `provenance`: 출처와 최신성

## 원칙

- raw field를 바로 prompt에 넣지 않는다.
- `pjt_id`와 `pjt_no`를 합치지 않는다.
- `lead_org_name`, `participant_org_name`, `people_affiliation_org_name`을 섞지 않는다.
- retrieval metadata는 필요한 최소 범위만 prompt에 노출한다.


