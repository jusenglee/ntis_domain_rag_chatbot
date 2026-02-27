# vLLM request_id 추적 가이드

이 레포는 vLLM(OpenAI-Compat) 경로에서 요청 단위 추적을 위해
`--enable-request-id-headers` 옵션을 사용합니다.

- 실행 스크립트: `scripts/vllm/start_vllm_solar.sh`
- 핵심 플래그: `--enable-request-id-headers`

## 1) 왜 필요한가?
스트리밍 장애/지연(TTFT)처럼 “재현이 어려운” 문제는
요청 단위 request_id가 없으면 서버/프록시/LLM 엔진 로그를 연결하기 어렵습니다.

## 2) 운영 체크리스트
- [ ] vLLM이 request id 헤더를 활성화했는지 확인
- [ ] gateway(리버스프록시)가 해당 헤더를 보존하는지 확인
- [ ] `server3.py`에서 request_id를 로그에 남기는지 확인
- [ ] 장애 티켓에 query + request_id + timestamp를 함께 남기기

## 3) 권장 로그 필드(요청 1건)
- request_id
- model / base_url
- TTFT / total latency
- streaming chunk count
- finish_reason

