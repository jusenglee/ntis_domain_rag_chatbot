# 검색 정확도 평가 가이드

이 디렉터리는 검색 정확도 측정을 위한 **질의-정답셋**과 **평가 스크립트**를 제공합니다.

## 1) 평가셋 형식(JSONL)

파일: `eval/sample_queries.jsonl` 예시

각 줄은 아래 형태의 JSON 객체입니다.

```json
{"query": "서울대학교 과제명 등록 현황", "expect_doc_ids": ["IRD_NAI_PJT_INFO::2710084213"]}
```

- `query`: 사용자 질의 텍스트
- `expect_doc_ids`: 정답 문서 ID 목록(`doc_id` 기준)

## 2) 실행 방법

```bash
python eval/run_eval.py --data eval/sample_queries.jsonl --k 10
```

## 3) 측정 지표

- **Recall@k**: 정답 문서가 상위 k개 결과 안에 포함되는지
- **MRR@k**: 정답 문서의 첫 등장 순위 점수

## 4) 운영 팁

- 과제 / 성과 / 기관 / 인명 / ID / NTIS QnA를 고르게 포함하세요.
- 최소 50~100개 질의를 권장합니다.

