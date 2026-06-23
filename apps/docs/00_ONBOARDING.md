# 00. 온보딩 — 이 시스템을 처음 보는 사람을 위한 안내

이 문서만 끝까지 읽으면 "이게 뭘 하는 물건이고, 코드 어디를 봐야 하는지"가 잡힌다. 전문 용어는 처음 나올 때 풀어서 쓴다.

## 1. 이게 무슨 시스템인가요?

**NTIS**는 국가 R&D(연구개발) 정보를 모아둔 정부 포털이다. 이 프로젝트는 그 데이터를 다루는 **자율 에이전트 챗봇**이다.

여기서 핵심은 — **"검색 챗봇"이 아니라 "에이전트"** 라는 점이다. 매 질문마다 무조건 DB를 뒤지는 고정 RAG 파이프라인이 아니라, **챗봇이 먼저 "이 질문에 내가 뭘 해야 하나"를 판단**한다. NTIS 벡터 검색은 *필요할 때 부르는 도구 하나*(MCP 서버 비슷한 backplane)일 뿐이다.

### 실제로 이렇게 동작한다 (에이전트답게)

> - **"안녕하세요"** → DB 검색 안 함. 그냥 인사로 답함(`response.direct_answer`).
> - **"넌 뭘 할 수 있어?"** → DB 검색 안 함. 능력 설명으로 답함.
> - **"신동구 연구자(KISTI)의 2014~2020년 활동 내역"** → 이제 NTIS가 필요하다고 판단 → `search` 도구 호출 → 근거 모아서 출처[1][2]와 함께 답함.
> - **"오늘 환율 알려줘"** → NTIS 범위 밖이라 정직하게 거절(`response.unsupported`).
> - **대상이 모호하면** → 지어내지 않고 되묻는다(clarify).

### 한 가지 더 — 단발이 아니라 "루프"다

복잡한 질문은 **플래너(LLM)가 도구를 여러 번 부르며 단계적으로** 풀 수 있다. 예: 사람 이름 해소 → 그 사람의 과제 검색 → 부족하면 다시 검색. 플래너가 "이제 충분하다, 답하자"를 스스로 판단한다(최대 8스텝, 안전망). 이게 이 브랜치를 *진짜 에이전트*로 만드는 부분이다. → [03 에이전트 런타임](./03_AGENTIC_RUNTIME.md)

## 2. 한 요청의 흐름 (그래프)

```
load_session            세션 불러오기
  → dialogue_agent      "무슨 의도? 직접답변/되묻기/진행?" (LLM 판단)
  → entity_resolver     사람/기관 이름 → ID 해소
  → planner_loop  ⇄  tool_executor    도구 부르고 → 다시 판단 (반복, 최대 8스텝)
  → answer_curator      근거(EvidenceBundle)만 정리
  → answer_agent        Solar·Gemma 두 답 생성
  → critic_agent        근거와 맞나 검증 (틀리면 1회 repair / 되묻기)
  → save_session        세션 저장 → 끝
```

그래프 정의: `apps/pipeline/agentic_workflow.py`의 `build_agentic_pipeline_graph()`. 단일 파이프라인이다(ADR-0020이 정적 그래프를 제거).

## 3. 핵심 용어 빠른 사전

| 용어 | 한 줄 설명 |
|---|---|
| **planner_loop** | LLM 플래너가 매 스텝 "도구 호출/답변/되묻기"를 정하는 반복 루프(최대 8스텝). |
| **3계층 권한분리** | ①판단(누가 뭘 할지) ②근거(검색·정규화) ③발행(답 생성·검증). 서로 침범 금지. (ADR-0018) |
| **SearchTask** | SearchAgent가 실행하는 유일한 검색 계약. |
| **CanonicalEvidence** | 답변에 들어가는 유일한 근거 단위. raw Qdrant payload 직접 주입 금지. |
| **듀얼 답변** | Solar(A, 메인·검증 대상) + Gemma(B, 비교·raw) 두 답. (ADR-0021) |
| **pjt_id / pjt_no** | 과제 식별자. 개별 과제 / 다년도 묶음. **혼용 금지.** |

## 4. 코드는 어디부터 보나

| 알고 싶은 것 | 파일 |
|---|---|
| 런타임 조립 (그래프 컴파일) | `apps/api/runtime.py` |
| 요청/응답·SSE | `apps/api/routes.py` |
| **그래프 정의(라이브)** | `apps/pipeline/agentic_workflow.py` |
| 공유 상태 | `apps/pipeline/agent_state.py` |
| **플래너 루프(핵심)** | `apps/pipeline/agents/planner_agent.py` |
| 도구 계약/실행 | `apps/pipeline/tools/contracts.py` |
| 도구 카탈로그(9개) | `apps/pipeline/tools/registry.py` |
| NTIS 검색 실행 | `apps/pipeline/search_agent.py` |
| 근거 단위 정의 | `apps/pipeline/contracts.py` |
| 세션 슬롯 | `apps/pipeline/agents/session_state.py` |

## 5. 딱 이것만은 기억하세요

| 규칙 | 왜 |
|---|---|
| NTIS 검색은 *필요할 때만* — 인사·능력설명·거절·되묻기엔 안 쓴다 | 에이전트지 고정 RAG가 아니다 |
| 3계층(판단/근거/발행) 권한을 섞지 않는다 | 한 계층이 남의 일을 하면 구조가 무너진다 |
| `pjt_id` ≠ `pjt_no` — 섞지 않는다 | 개별 과제 vs 다년도 묶음 |
| raw Qdrant payload를 프롬프트·API 응답에 직접 넣지 않는다 | 반드시 `CanonicalEvidence` 경유 |
| 구조화된 식별자를 검색 텍스트로 뭉개지 않는다 | 식별자 의미 보존 |

## 6. 개발 흐름 + 검증 주의

1. 동작을 소유한 소스 경로를 먼저 본다 → 매칭되는 계약 모델 확인 → 좁은 테스트(`tests/pipeline`) 추가/수정 → 타깃 테스트 먼저, 그다음 넓게.
2. ⚠️ **이 저장소엔 의존성 매니페스트(requirements 등)가 없다.** 테스트 통과는 *환경 의존적*이다 — 검증 게이트와 현재 테스트 트리 상태는 [07 검증과 리뷰](./07_VALIDATION_AND_REVIEW.md) 참조.
