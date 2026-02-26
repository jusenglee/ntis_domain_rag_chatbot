# vLLM request_id 추적 설정/검증

이 저장소에서 vLLM 서버 실행 커맨드는 아래 경로의 엔트리포인트 스크립트로 관리합니다.

- `scripts/vllm/start_triton_gpt_oss.sh`

## 반영된 실행 옵션

- `--enable-request-id-headers`
  - 클라이언트의 `X-Request-Id`를 수신/응답 헤더에 반영합니다.
- `--uvicorn-log-level debug`
  - Uvicorn 레벨을 디버그로 설정합니다. (`trace`로 상향 가능)
- `--disable-log-requests` **미사용**
  - 요청 로그 기본 동작을 유지합니다.
- `--max-log-len 512`
  - 로그 길이 상한을 512로 설정합니다.

## 런타임 엔진 로그 상세화(선택)

운영 중 엔진 로그를 더 보고 싶다면 아래 환경 변수를 설정한 뒤 서버를 기동하세요.

```bash
export VLLM_LOGGING_LEVEL=DEBUG
./scripts/vllm/start_triton_gpt_oss.sh
```

## request_id 추적 검증

1) 서버 기동

```bash
./scripts/vllm/start_triton_gpt_oss.sh
```

2) 클라이언트 요청 (`X-Request-Id` 전달)

```bash
curl -i http://localhost:8010/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer EMPTY' \
  -H 'X-Request-Id: req-ntis-001' \
  -d '{
    "model": "/model",
    "messages": [{"role": "user", "content": "ping"}],
    "max_tokens": 16
  }'
```

3) 확인 포인트

- 응답 헤더에 `X-Request-Id: req-ntis-001`가 포함되는지 확인
- 서버 로그에서 동일 request_id를 기준으로 요청 흐름 추적 가능 여부 확인



## OpenAI 2.16.0 스트리밍 스모크 테스트

`tests/test_openai_compat_stream_smoke.py`를 추가해 OpenAI Python 클라이언트(2.16.0)와 vLLM OpenAI-compatible `/v1/chat/completions` 스트리밍 회귀를 조기에 감지할 수 있습니다.

실행 예시:

```bash
OPENAI_COMPAT_SMOKE=1 \
OPENAI_COMPAT_BASE_URL=http://localhost:8010/v1 \
OPENAI_COMPAT_MODEL=/model \
pytest -q tests/test_openai_compat_stream_smoke.py
```

검증 항목:
- stream=True 호출 시 chunk가 1개 이상 수신되는지
- 수신 텍스트를 합친 최종 응답이 비어있지 않은지
