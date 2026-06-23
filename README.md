# NTIS Domain RAG Chatbot — 판단-검색-에이전트 브랜치

NTIS(국가과학기술지식정보서비스) 국가 R&D 데이터를 다루는 **자율 에이전트 챗봇**이다. 고정 RAG가 아니라, 챗봇이 "이 질문에 무엇을 할지"를 먼저 판단하고 **NTIS 벡터 검색은 필요할 때 부르는 도구 하나**(MCP식 backplane)로 쓴다.

> 예: "안녕하세요" → 검색 없이 인사 / "신동구 연구자(KISTI) 활동 내역" → `search` 도구 호출 후 출처와 함께 답 / "오늘 환율" → 정직한 거절. 복잡한 질문은 플래너가 도구를 여러 번(최대 8스텝) 부른다.

## 빠른 시작

| 상황 | 먼저 볼 것 |
|---|---|
| **프로젝트를 막 인수받았다** | 👉 [`HANDOVER.md`](./HANDOVER.md) |
| 시스템을 이해하고 싶다 | [`apps/docs/00_ONBOARDING.md`](./apps/docs/00_ONBOARDING.md) |
| 코드를 수정한다 | [`AGENTS.md`](./AGENTS.md) |

## 문서 지도

정본 문서는 [`apps/docs/`](./apps/docs/README.md)에 통합.

- [00 온보딩](./apps/docs/00_ONBOARDING.md) · [01 아키텍처](./apps/docs/01_ARCHITECTURE.md) · [02 계약과 규칙](./apps/docs/02_CONTRACTS_AND_RULES.md)
- [03 에이전트 런타임](./apps/docs/03_AGENTIC_RUNTIME.md) · [04 NTIS 도구 backplane](./apps/docs/04_NTIS_TOOL_BACKPLANE.md)
- [05 API와 스트리밍](./apps/docs/05_API_AND_STREAMING.md) · [06 운영과 환경](./apps/docs/06_OPERATIONS_AND_ENV.md) · [07 검증과 리뷰](./apps/docs/07_VALIDATION_AND_REVIEW.md)
- [ADR/](./apps/docs/ADR/) — 아키텍처 결정 기록(0018~0025)

## 실행 (요약)

```bash
export PYTHONPATH=.            # PowerShell: $env:PYTHONPATH='.'
python apps/api/main.py
```

외부 의존(Qdrant/Triton/Solar vLLM/Redis) 엔드포인트 기본값은 코드에 하드코딩되어 있으나 환경변수로 오버라이드된다. 인수 인프라 이전 시 교체 필요 → [06 운영과 환경](./apps/docs/06_OPERATIONS_AND_ENV.md), [`HANDOVER.md`](./HANDOVER.md).

> ⚠️ 이 저장소는 **v1 = `고도화`(현재 운영 중, `apps/api` 계열)** 와 **v2 = `판단-검색-에이전트`(개발 중, `apps/pipeline` agentic — 이 README 기준)** 로 나뉜다. v2가 v1을 대체할 예정. 상세 → [`HANDOVER.md`](./HANDOVER.md).
