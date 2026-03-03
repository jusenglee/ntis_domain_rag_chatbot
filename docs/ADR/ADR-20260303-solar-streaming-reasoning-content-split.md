# ADR-20260303: Solar(vLLM) streaming reasoning/content split handling

## Status
Accepted

## Context
Solar(vLLM) OpenAI-compatible streaming에서 reasoning과 최종답(content)이 분리되어 출력되는 케이스가 확인되었다.
스트림은 `delta.reasoning(/reasoning_content)`가 먼저 길게 나오고, 최종 답변은 뒤늦게 `delta.content`로 출력될 수 있다.
기존 구현이 `delta.content`만 "텍스트"로 취급하면, content가 나오기 전에 TTFT deadline에 걸려 스트리밍이 실패한다.

## Decision
1) 스트리밍 파서는 reasoning/content를 모두 파싱하되, chunk에 `additional_kwargs["stream_field"]`로 유형을 태깅한다.
   - reasoning: keepalive/관측/TTFT_any 용
   - content: 사용자 최종답 누적 용
2) 최종 사용자 출력은 content-only로 고정한다(Reasoning 노출 금지).
3) TTFT 지표를 any/content로 분리한다.
   - ttft_any_ms: 스트림 시작/생존 확인용
   - ttft_content_ms: 최종답 시작 지연 판단용
4) content가 0이면 deadline_exceeded여도 fallback을 허용하여 “안내문만 반환”을 방지한다.
5) /query/stream에서는 `stream_field=="reasoning"` chunk를 drop 한다.

## Consequences
- Pros
  - reasoning-first 스트림에서도 “스트림이 죽었다”로 오판하지 않음
  - 사용자 출력에서 reasoning 노출을 구조적으로 차단
  - 관측성 개선(ttft_any/content, reasoning/content chars)
  - content 미생성/지연을 트리아지로 구분 가능
- Cons
  - 파서/스트리밍 유틸/서버 스트림 핸들러가 `stream_field` 계약을 공유해야 함
  - fallback 정책이 비용을 증가시킬 수 있음(스트림→non-stream 대체)

## Alternatives Considered
1) Reasoning parser 비활성화(서버 설정 변경)
   - 서버/모델별로 제약이 있으며 운영 유연성이 떨어질 수 있음
2) Reasoning을 사용자에게 그대로 출력
   - 정책/UX 상 부적합(금지)

## Implementation Notes
- openai_compat_llm.py: STREAM_FIELD_KEY 및 reasoning/content 태깅
- llm_streaming.py: reasoning keepalive 처리 및 ttft_any/content 분리
- server3.py: on_chat_model_stream에서 reasoning drop
