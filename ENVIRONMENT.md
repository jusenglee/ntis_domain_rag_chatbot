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
