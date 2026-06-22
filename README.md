# NTIS Domain RAG Chatbot

NTIS(국가과학기술지식정보서비스)의 국가 R&D 데이터를 **자연어로 묻고 출처와 함께 답을 받는** 챗봇이다.

> 예: "신동구 연구자(한국과학기술정보연구원)의 2014~2020년 활동 내역"이라고 물으면, 그 연구자가 참여한 과제를 연도별로 정리하고 각 항목에 `[1]` 같은 **출처 번호**를 달아 답한다. 이어서 "그럼 2020년 이후 과제는?"이라고 하면 앞 대화를 **이어받아** 답한다.

흔한 LLM 챗봇과 달리 *검색된 실제 데이터에 있는 것만 답하고*(환각 방지), 동명이인·과제번호 차이 같은 함정에서 엉뚱한 대상을 섞지 않도록 설계됐다. 기술적으로는 키워드(BM25)+벡터 하이브리드 검색 위에 LangGraph 다중 에이전트를 얹은 RAG 시스템이다. 무슨 시스템인지 5분 안에 잡으려면 → **[00 온보딩](./apps/docs/00_ONBOARDING.md)**.

## 빠른 시작

| 상황 | 먼저 볼 것 |
|---|---|
| **프로젝트를 막 인수받았다** | 👉 [`HANDOVER.md`](./HANDOVER.md) — 현재 상태·진입점·즉시 처리 이슈 |
| 시스템을 이해하고 싶다 | [`apps/docs/00_ONBOARDING.md`](./apps/docs/00_ONBOARDING.md) |
| 코드를 수정한다 | [`AGENTS.md`](./AGENTS.md) — 작업 원칙(필독) |

## 문서 지도

모든 정본 문서는 [`apps/docs/`](./apps/docs/README.md)에 통합되어 있다.

- [00 온보딩](./apps/docs/00_ONBOARDING.md) · [01 아키텍처](./apps/docs/01_ARCHITECTURE.md) · [02 계약과 도메인 규칙](./apps/docs/02_CONTRACTS_AND_RULES.md)
- [03 행동 안전](./apps/docs/03_BEHAVIORAL_SAFETY.md) · [04 API와 스트리밍](./apps/docs/04_API_AND_STREAMING.md)
- [05 운영과 환경](./apps/docs/05_OPERATIONS.md) · [06 테스트와 회귀](./apps/docs/06_TESTING.md)
- [ADR/](./apps/docs/ADR/) — 아키텍처 결정 기록

## 실행 (요약)

```bash
export PYTHONPATH=.            # 윈도우 PowerShell: $env:PYTHONPATH='.'
python apps/api/main.py        # uvicorn, 0.0.0.0:8008
```

외부 의존(Qdrant / Triton / Solar vLLM / Redis / Oracle)의 엔드포인트 기본값은 코드에 하드코딩되어 있으나 환경변수로 오버라이드된다. 인수 인프라 이전 시 교체가 필요하다 — [05 운영과 환경](./apps/docs/05_OPERATIONS.md), [`HANDOVER.md`](./HANDOVER.md).
