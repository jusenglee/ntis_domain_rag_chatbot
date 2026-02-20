# 운영 환경 변수

프로덕션에서 Gemma 모델을 사용하는 경우 아래 값을 기준으로 설정합니다.

```bash
# Gemma 컨텍스트 길이 (Triton/VLLM 설정과 동일하게 맞춰주세요)
export GEMMA_MAX_MODEL_LEN=32768

# Gemma 출력 토큰 상한 (운영 요구사항에 맞게 조정)
# 예: 4096, 8192 등
export GEMMA_MAX_TOKENS=8192
```

## 참고

- `MAX_TOKENS`는 모델별 출력 토큰 상한이 지정되지 않았을 때의 기본값입니다.
- Gemma 모델에서는 `GEMMA_MAX_TOKENS`가 `MAX_TOKENS`보다 우선합니다.
- `DEFAULT_MAX_MODEL_LEN`은 모델별 길이를 지정하지 않았을 때의 기본값입니다.

## 수동 확인 시나리오 (최종 응답 페이로드 절단 완화)

1. 긴 `meta_basic`/`meta_detail` 및 본문이 포함된 문서를 반환하도록 질의합니다.
   - 예: 상세 정보가 풍부한 과제/성과 검색, 혹은 관련 문서를 많이 포함하는 질의.
2. 응답 생성 로그에서 `context_text - 페이로드 평탄화 후 데이터` 섹션을 확인합니다.
3. 동일한 입력에서 이전 대비 `context_text`의 길이가 더 길거나, 문장/토큰 절단이 줄어든 것을 확인합니다.
   - 최종 응답 경로는 `relax_limits=True`로 전달되어 `MAX_DOC_*` 절단이 완화됩니다.

## vLLM request_id 추적 운영 설정

- 실행 경로: `scripts/vllm/start_triton_gpt_oss.sh`
- 상세 가이드: `docs/vllm_request_id_tracking.md`
