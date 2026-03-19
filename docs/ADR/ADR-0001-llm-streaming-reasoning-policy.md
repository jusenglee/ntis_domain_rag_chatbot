# ADR-0001 — LLM 스트리밍 Reasoning 분리 및 Stage별 Thinking Policy

- 작성일: 2026-03-03 (Asia/Seoul)
- 상태: Proposed

## 1) 배경

vLLM reasoning 계열 모델은 최종 답변(`content`)과 추론/생각(`reasoning`, 구버전 `reasoning_content`)을 분리해 출력할 수 있다.
스트리밍에서는 reasoning이 먼저 오고 content가 한참 뒤에 오는 경우가 있어, 운영 상 "빈 응답/지연"으로 오판되거나 guard가 잘못 동작할 위험이 있다.

또한 스트리밍 파이프라인(`openai_compat_llm -> llm_streaming -> app SSE`)에서 reasoning chunk를 content로 단순 연결하면 최종 사용자 응답에 reasoning이 섞이는 문제가 생긴다.

## 2) 결정

### 2.1 LLM 스트리밍 출력 계약 고정

- 각 chunk는 `message.additional_kwargs.stream_field`를 가진다.
  - 허용값: `reasoning`, `content`, `None`
- 최종 사용자 응답에는 `stream_field in {None,"content"}`만 append한다.
- `stream_field=reasoning`은 사용자 응답에 포함하지 않고 `reasoning_chars`로만 집계한다.
- 텍스트 추출 규칙:
  - 기본 텍스트는 `message.content`
  - 단, `stream_field=reasoning`이고 `message.content==""`이면 `message.additional_kwargs.reasoning_text`를 텍스트로 본다.
- TTFT는 두 축으로 유지한다.
  - `ttft_any_ms`: 첫 chunk 기준(=reasoning 포함)
  - `ttft_content_ms`: 첫 content chunk 기준
  - `ttft_ms`는 하위호환을 위해 `ttft_content_ms`와 같은 의미로 유지한다.
- fallback 정책:
  - `stream_content_emitted_chunks == 0` 상태에서 deadline/guard 때문에 fallback으로 완성형 응답을 얻은 경우, "부분 반환 안내문"을 붙이지 않는다.

### 2.2 Stage별 Thinking Policy(Planner vs Answer)

- Planner(전략/계약 JSON 생성):
  - 필요 시 thinking ON 허용(예: `reasoning_effort=high`)
  - 단, reasoning은 사용자 출력으로 emit하지 않으며 JSON 파싱 경로를 오염시키면 안 된다.
- Answer(특히 Solar/vLLM):
  - 기본은 thinking OFF 또는 최소화(예: `reasoning_effort=low`, `include_reasoning=false`)
  - 목적은 content 지연과 빈 응답 오판을 줄이고 `ttft_content`를 안정화하는 것이다.

## 3) 영향

- 장점
  - reasoning-first 스트리밍에서도 빈 응답과 content 지연을 분리해 관측/대응할 수 있다.
  - 사용자 응답 오염(Reasoning 노출)을 막을 수 있다.
  - 스트리밍 메트릭을 운영 기준으로 활용할 수 있다(`ttft_any/content`, `reasoning/content chars`).
- 단점 / 리스크
  - reasoning을 사용자에게 직접 노출하지 않으므로 디버깅은 로그와 메트릭 중심이 된다.
  - wrapper와 스트리밍 파이프라인이 같은 계약을 공유해야 하며, 불일치 시 회귀 위험이 있다.

## 4) 구현 메모

- `openai_compat_llm`
  - 요청 단위로 `reasoning_effort`, `include_reasoning`, `chat_template_kwargs`를 전달한다.
  - reasoning chunk는 `stream_field=reasoning`으로 emit하되 최종 content에는 섞지 않는다.
  - 필요 시 reasoning 텍스트는 `additional_kwargs.reasoning_text`로 전달한다.
- `llm_streaming`
  - chunk 파싱 시 `stream_field`와 `reasoning_text` fallback을 처리한다.
- app SSE
  - `stream_field in {None,"content"}`만 클라이언트에 emit한다.
- 테스트
  - `tests/test_llm_streaming.py`에 reasoning filtering / metric / fallback 안내문 규칙 회귀 테스트를 추가한다.
  - 추가 권장: `openai_compat_llm` reasoning chunk 계약 테스트

## 5) 검토한 대안

- 기각: reasoning을 content에 합쳐 그대로 사용자에게 노출
  - 사용자 응답 오염과 `ttft_content` 악화 가능성이 크다.
- 기각: 서버 레벨에서만 thinking을 강제로 OFF
  - Planner가 필요 시 thinking을 사용할 수 없고 요청 단위 override도 불가능해진다.


