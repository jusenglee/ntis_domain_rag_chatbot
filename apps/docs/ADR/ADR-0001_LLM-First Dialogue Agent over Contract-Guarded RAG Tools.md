ADR-0015: LLM-First Dialogue Agent over Contract-Guarded RAG Tools
Status

Accepted / In Progress

Date

2026-04-22

Implementation update (2026-04-22)

사용자 지시에 따라 신규 Agent 계약에서는 `fallback_legacy_pipeline` decision을 만들지 않는다.
Agent가 안전한 tool call을 만들 수 없으면 사용자 모호성일 때만 clarification으로 fail-closed 한다. LLM 응답 parse/schema/tool validation 실패는 이전 front-controller 경로로 넘기지 않고 1회 self-repair 후 `agent_internal_error`로 종료한다.
현재 workflow graph는 `agentic front-controller`일 때 `rule_precheck` 이후 agent state card -> Dialogue Agent -> direct/tool/clarification/internal-error 경로로 분기한다.
`search_ntis_domain` / `refine_current_subject`가 guarded intent를 만들면 기존 knowledge sufficiency -> retrieval -> answer path에 합류하고, 미구현 tool / contract violation은 이전 front-controller 경로 없이 executor contract block으로 종료한다.

Related ADRs
ADR-0013: SessionMemory / CurrentContext 기반 대화 메모리 정리
ADR-0014: LLM-Driven Clarification & Turn Interpretation Layer
ADR-0015: 본 문서. 기존 deterministic dialogue controller를 LLM-first agentic controller로 재배치
1. Context

현재 NTIS RAG 시스템은 대화 제어를 주로 코드 기반 deterministic pipeline이 담당한다.

현재 흐름은 대략 다음과 같다.

user input
→ turn_trigger
→ turn_interpreter
→ turn_policy
→ scope_resolver / context_router
→ planner
→ retrieval
→ answer generation
→ validation / publication

이 구조는 안정성과 재현성 측면에서는 강하다.
특히 QuestionAnalysisV3는 mode, head, action, relation, join_key_mode, target_cols, ids_map, filters, retrieval_query 등을 검증된 실행 계약으로 유지하고, HardContractV1은 pjt_id / pjt_no 축, JOIN legality, project key axis lock 같은 의미 불변식을 보존한다.

또한 최신 구조는 SessionMemory.current_context를 중심으로 PublishedManifestContext, SubjectQueryContext, DetailAnchorContext, ClarificationContext를 구분하여 다음 턴의 참조 기준을 명확히 하려 한다.

하지만 사용자 경험 측면에서는 문제가 남아 있다.

대표 예시는 다음과 같다.

사용자: 신동구 연구자의 활동기록
시스템: 검색 결과를 만들었지만 최종 검증에서 보류

사용자: 해당 연구자의 2014년~2023년까지의 활동내역은?
시스템: 무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.

사용자: 신동구 연구자의 2014년~2023년까지의 활동내역은?
시스템: 무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.

이 동작은 사용자 입장에서 부자연스럽다.

사용자의 의도는 명확하다.

"해당 연구자" = 직전의 신동구
"2014년~2023년" = 기간 조건
"활동내역" = 같은 subject에 대한 refinement

하지만 현재 구조는 이를 자연스럽게 이어가기보다, 코드 기반 상태 전이와 publishability 조건에 걸려 clarification 또는 보류로 빠진다.

최신 turn_trigger는 연구책임자, 참여연구원, pi, 최신순, 건만 등의 refinement cue를 추가했고, subject context가 있으면 reference_style="refinement"로 판정하도록 개선되어 있다.
그러나 이 로직은 이미 publishable subject context가 존재할 때만 충분히 작동한다. 첫 턴이 검증 보류로 끝나면 SubjectQueryContext가 생성되지 않고, 결국 후속 대화가 끊긴다.

즉 현재 문제는 단순한 prompt 문제가 아니다.

핵심 문제는 다음과 같다.

대화의 주도권을 deterministic pipeline이 가지고 있고,
LLM은 중간 JSON parser / 보정기 역할에 머무른다.

사용자가 원하는 방향은 반대다.

LLM이 대화의 주도권을 가진다.
코드는 LLM의 판단을 안전하게 실행하는 Tool Backend와 Validator가 된다.
2. Problem Statement
   P1. 대화 판단 주체가 코드에 있다

현재 시스템은 turn_trigger, turn_interpreter, turn_policy, scope_resolver, context_router가 먼저 대화 흐름을 분류한다.
LLM은 이 흐름 안에서 제한적으로만 호출된다.

그 결과 사용자의 자연스러운 follow-up이 코드 규칙과 조금만 어긋나면 다음과 같은 기계적 응답이 나온다.

무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.
질문을 조금 더 구체적으로 작성해 주세요.
대상 축에 맞는 후보가 없습니다.

이는 사람과 상호작용하는 대화형 assistant보다는, form validation 기반 검색 UI처럼 느껴진다.

P2. LLM이 대화 맥락을 능동적으로 해석하지 못한다

LLM은 사용자의 생략, 지시어, 맥락 보완을 사람처럼 해석할 수 있다.

예를 들어:

사용자: 신동구 연구자의 활동기록
사용자: 해당 연구자의 2014년~2023년까지는?

LLM은 자연스럽게 다음처럼 추론할 수 있다.

해당 연구자 = 신동구
2014~2023 = 추가 시간 필터
새로운 대상 선택이 아니라 기존 subject refinement
검색 필요

하지만 현재 시스템에서는 이 판단을 코드의 cue, publishability, context state, policy block이 먼저 결정한다.

P3. clarification이 대화 행동이 아니라 error path로 동작한다

Clarification은 필요하다.
하지만 현재 clarification은 시스템 내부 상태 실패를 사용자에게 노출하는 방식으로 나타나는 경우가 많다.

바람직한 clarification은 다음과 같다.

신동구라는 이름의 후보가 여러 명일 수 있습니다.
한국과학기술정보연구원 소속 신동구 연구자를 기준으로 정리하면 될까요?

현재는 다음처럼 보인다.

무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요.

즉 clarification은 삭제 대상이 아니라, LLM이 필요하다고 판단할 때 수행하는 대화 행동으로 재정의되어야 한다.

P4. 메모리가 구조화되어 있지만 LLM에게 자연스럽게 전달되지 않는다

SessionMemory.current_context는 좋은 방향이다.
그러나 LLM이 대화를 이어가기 위해 필요한 것은 raw JSON 후보 16개가 아니라, 현재 대화 상황을 요약한 Conversation State Card다.

내부 truth는 구조화되어야 하지만, LLM 입력은 대화적으로 읽을 수 있어야 한다.

3. Decision
   D1. LLM Dialogue Agent를 최상위 대화 오케스트레이터로 도입한다

기존 deterministic pipeline을 완전히 삭제하지 않는다.
대신 역할을 바꾼다.

Before
코드가 대화 흐름을 판단한다.
LLM은 JSON parser / fallback / prose 보정기로 사용된다.
After
LLM Dialogue Agent가 대화 흐름을 판단한다.
코드는 Tool Backend, Contract Validator, Retrieval Executor로 사용된다.

새로운 최상위 흐름은 다음과 같다.

user input
→ Dialogue Agent
→ Agent decision
├─ direct_answer
├─ call_tool(search/refine/lookup/join)
└─ ask_clarification
→ tool execution
→ observation
→ Agent final response
→ validation / publication
D2. 기존 Planner / Contract / Retrieval은 Tool Backend로 재배치한다

기존 HardContractV1, QuestionAnalysisV3, IntentPayloadV3, CustomRAGRetriever, SEARCH/LOOKUP/JOIN 계약은 제거하지 않는다.
다만 대화 판단 전면에 세우지 않고, Agent가 호출하는 tool 내부의 안전 실행 계층으로 이동한다.

LLM Agent tool call
→ Tool Adapter
→ existing planner / QuestionAnalysisV3 / HardContractV1
→ retrieval
→ evidence projection
→ observation returned to Agent

즉 contract는 유지된다.
하지만 사용자가 체감하는 대화 제어권은 LLM에게 넘어간다.

D3. turn_trigger / turn_interpreter / turn_policy는 validator/materializer로 강등한다

기존 파일을 즉시 삭제하지 않는다.

다만 역할을 다음처럼 바꾼다.

기존 모듈	기존 역할	변경 후 역할
turn_trigger.py	follow-up/fresh 1차 판단	agent decision validator
turn_interpreter.py	후보 선택 / clarification 판단	candidate materializer, validator
turn_policy.py	실행 경로 차단 / 허용	safety policy checker
context_router.py	recent mention 기반 라우팅	low-confidence 보조 materializer
request_facade.py	대화 제어 orchestration	tool adapter / compatibility layer
D4. Agent는 Tool Calling 기반으로 행동한다

Agent는 직접 DB filter를 생성하지 않는다.
Agent는 의미 수준의 tool call만 만든다.

예:

{
"tool": "refine_current_subject",
"arguments": {
"subject_ref": "current_subject",
"year_from": 2014,
"year_to": 2023,
"activity_type": "activity_history"
}
}

Tool adapter가 이를 기존 planner/retrieval 계약으로 변환한다.

D5. clarification은 ask_user_for_clarification tool로 재정의한다

Clarification은 더 이상 policy block의 자동 오류 메시지가 아니다.

Agent가 다음 조건을 판단한 경우에만 호출한다.

현재 대화 문맥으로 subject를 안정적으로 특정할 수 없음
후보가 여러 명이고 구분 근거가 부족함
검색을 해도 사용자의 의도와 다른 결과가 나올 가능성이 큼
사용자 선택 없이는 pjt_id / pjt_no, 사람/기관 축이 불명확함
D6. 메모리는 2층 구조로 유지한다
내부 truth

SessionMemory.current_context를 유지한다.
이는 system truth다.

LLM input

Agent에는 구조화 JSON 전체가 아니라 Conversation State Card를 제공한다.

예:

현재 대화 주제:
- subject: 신동구
- subject_kind: people
- 추정 소속: 한국과학기술정보연구원
- 최근 요청: 활동기록
- 최근 확인된 범위: 2009~2012 참여연구원 활동, 2024 연구책임자 활동
- 사용자가 기간/역할 조건을 추가하면 같은 subject 기준으로 refine 가능
- 최근 최종 답변은 일부 검증 보류였지만, 대화 subject는 신동구로 유지 가능
4. Non-Goals

이번 ADR은 다음을 목표로 하지 않는다.

HardContractV1, QuestionAnalysisV3 삭제
LLM이 Qdrant filter를 직접 생성
LLM이 pjt_id, pjt_no, rst_id, person_no 등을 새로 발명
SEARCH/LOOKUP/JOIN 의미 제거
검증 없이 LLM 답변을 그대로 publish
chat history만으로 referential truth를 대체
기존 deterministic pipeline 즉시 삭제
5. Target Architecture
   5.1 High-level Flow
   ┌────────────────────┐
   │ User Message        │
   └─────────┬──────────┘
   │
   ▼
   ┌────────────────────┐
   │ Conversation State  │
   │ Card Builder        │
   └─────────┬──────────┘
   │
   ▼
   ┌────────────────────┐
   │ Dialogue Agent      │
   │ LLM-first Router    │
   └─────────┬──────────┘
   │
   ├─────────────── direct_answer
   │
   ├─────────────── ask_clarification
   │
   └─────────────── call_tool
   │
   ▼
   ┌────────────────────┐
   │ Tool Adapter        │
   └─────────┬──────────┘
   ▼
   ┌────────────────────┐
   │ Planner/Contract    │
   │ Guarded Execution   │
   └─────────┬──────────┘
   ▼
   ┌────────────────────┐
   │ Retrieval / Lookup  │
   │ Join / Evidence     │
   └─────────┬──────────┘
   ▼
   ┌────────────────────┐
   │ Observation         │
   └─────────┬──────────┘
   ▼
   ┌────────────────────┐
   │ Agent Final Answer  │
   └─────────┬──────────┘
   ▼
   ┌────────────────────┐
   │ Validation / Publish│
   └────────────────────┘
   5.2 New Modules
   apps/conversation/agent_contracts.py

workflow state와 router가 공유하는 Agent decision 계약.

class AgentDecision(BaseModel):
decision_type: Literal[
"direct_answer",
"call_tool",
"ask_clarification",
"agent_internal_error",
]
tool_name: Optional[str] = None
tool_args: Dict[str, Any] = Field(default_factory=dict)
response_text: Optional[str] = None
clarification_question: Optional[str] = None
confidence: float
reasoning_summary: str

`agent_internal_error`는 LLM invoke 실패, JSON parse 실패, schema/tool validation 실패 후 self-repair 실패 같은 시스템 오류에만 사용한다. 사용자 대상이 실제로 모호한 경우에만 `ask_clarification`을 사용한다.

apps/conversation/agent_dialogue_router.py

최상위 LLM Dialogue Agent.

async def run_dialogue_agent(
*,
question: str,
conversation_state_card: str,
recent_chat: list[BaseMessage],
available_tools: list[AgentToolSpec],
request_id: str,
conversation_id: str,
) -> AgentDecision:
...
apps/conversation/conversation_state_card.py

SessionMemory, CurrentContext, 최근 retrieval summary를 LLM이 읽기 쉬운 상태 카드로 변환한다.

def build_conversation_state_card(
*,
session_memory: SessionMemory,
view_state: ConversationViewState,
selected_answer_meta: dict[str, Any] | None = None,
) -> str:
...

예시 출력:

[현재 대화 상태]
현재 subject: 신동구 (people)
subject source: previous subject query
refinement allowed: yes
최근 사용자가 요청한 작업: 활동기록 조회
최근 검색 결과 상태: final answer withheld, but subject context retained
사용자가 "해당 연구자", "이 연구원", "연구책임자만", "2014~2023"이라고 하면 신동구 기준으로 refine 가능
apps/conversation/agent_tools.py

Agent가 호출할 tool registry.

class AgentToolSpec(BaseModel):
name: str
description: str
input_schema: dict[str, Any]
apps/conversation/agent_tool_executor.py

Tool call을 기존 pipeline으로 연결한다.

async def execute_agent_tool(
*,
tool_name: str,
tool_args: dict[str, Any],
state: AgentState,
) -> AgentObservation:
...
apps/conversation/agent_observation.py

Tool 결과를 Agent가 다시 읽을 수 있는 observation으로 표준화한다.

class AgentObservation(BaseModel):
observation_type: Literal[
"search_results",
"lookup_result",
"join_result",
"clarification_required",
"no_results",
"contract_violation",
"error",
"planned_intent",
]
summary: str
structured_refs: dict[str, Any] = Field(default_factory=dict)
evidence_count: int = 0
warnings: list[str] = Field(default_factory=list)
next_suggested_actions: list[str] = Field(default_factory=list)

# 2026-04-23 C1 Option B 추가: Agent가 tool decision 맥락에서 생성한
# "답변 생성 노드로 전달할 짧은 메모". generate_answer_* 프롬프트의
# {agent_observation_note} placeholder에 주입되는 **정보성** 문자열이며
# truth(ids_map, mode, relation 등)를 재정의하지 않는다.
# 값이 None/빈 문자열이면 placeholder는 빈 문자열로 대체되어 기존 답변
# 품질이 변하지 않도록 보장한다 (정적 fallback guard).
answer_note: Optional[str] = None
6. Tool Design
   6.1 search_ntis_domain

새로운 검색이 필요할 때 호출한다.

search_ntis_domain(
query: str,
domain_head: Literal["project", "perf", "people", "org", "support", "auto"],
year_from: int | None = None,
year_to: int | None = None,
people_name: str | None = None,
org_name: str | None = None,
perf_type: str | None = None,
limit: int | None = None,
)
내부 동작
tool args
→ normalized_intent seed 생성
→ run_question_analysis
→ QuestionAnalysisV3 검증
→ retrieval
→ evidence projection
→ AgentObservation 반환
6.2 refine_current_subject

현재 대화 subject를 유지한 채 조건을 추가할 때 호출한다.

refine_current_subject(
subject_ref: Literal["current_subject"],
year_from: int | None = None,
year_to: int | None = None,
role: Literal["연구책임자", "참여연구원"] | None = None,
perf_type: str | None = None,
target: Literal["project", "perf", "activity_history", "auto"] = "auto",
limit: int | None = None,
)
사용 예
사용자: 해당 연구자의 2014년~2023년까지의 활동내역은?
→ refine_current_subject(subject_ref="current_subject", year_from=2014, year_to=2023)
사용자: 연구책임자로 활동한 과제만 보여줘
→ refine_current_subject(subject_ref="current_subject", role="연구책임자", target="project")
내부 동작
SessionMemory.current_context에서 subject 복원
→ subject_kind/name/ids_map seed 생성
→ 추가 filters 병합
→ planner / contract 검증
→ retrieval
→ observation 반환
6.3 lookup_specific_entity

특정 ID 또는 최근 manifest item을 상세 조회할 때 호출한다.

lookup_specific_entity(
entity_ref: str,
entity_kind: Literal["project", "perf", "people", "org", "auto"],
detail_level: Literal["summary", "detail"] = "detail",
)

예:

사용자: 2번 과제 자세히
→ lookup_specific_entity(entity_ref="manifest:2", entity_kind="project")
6.4 join_project_perf

과제 ↔ 성과 관계형 질의에 사용한다.

join_project_perf(
direction: Literal["project_to_perf", "perf_to_project"],
anchor_ref: str,
perf_type: str | None = None,
year_from: int | None = None,
year_to: int | None = None,
)

내부에서는 기존 JOIN legality를 그대로 사용한다.

6.5 ask_user_for_clarification

정말 필요한 경우에만 호출한다.

ask_user_for_clarification(
reason: str,
question_to_user: str,
suggested_options: list[str] = [],
)
금지
"무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요."
권장
신동구라는 이름만으로는 후보가 여러 명일 수 있습니다.
한국과학기술정보연구원 소속 신동구 연구자를 기준으로 2014~2023년 활동을 정리하면 될까요?
7. Agent Decision Policy

Dialogue Agent는 다음 우선순위로 판단한다.

7.1 Direct Answer

검색 없이 답할 수 있는 일반 대화.

사용자: 고마워
→ direct_answer
7.2 Current Subject Refinement

현재 subject가 있고 사용자가 기간, 역할, 성과유형, 정렬, 개수 조건을 추가한 경우.

해당 연구자의 2014년~2023년 활동내역
연구책임자로 참여한 과제만
논문만
최근 3건만

→ refine_current_subject

7.3 Explicit New Search

사용자가 새 대상 또는 새 질의를 명시한 경우.

김재수 연구자의 참여과제
ETRI 수행 과제
신발 생산 자동화기기 개발

→ search_ntis_domain

7.4 Entity Lookup

사용자가 manifest item, ordinal, source reference, ID를 지정한 경우.

1번 자세히
PJT_ID 1711015550 상세
이 과제 상세

→ lookup_specific_entity

7.5 Relation / Join

과제와 성과 관계를 요청한 경우.

이 과제의 논문 성과
이 보고서가 나온 과제

→ join_project_perf

7.6 Clarification

다음 경우만 clarification.

current subject 없음
명시된 이름이 다중 후보이고 소속/ID/맥락으로도 구분 불가
tool 실행이 contract violation으로 막힘
LLM confidence 낮음
user choice 없이는 다른 결과가 나올 가능성이 큼
8. Contract Boundary

Agent는 자유롭게 판단하되, 다음 boundary를 넘을 수 없다.

8.1 Agent가 해도 되는 것
현재 대화 subject 추론
사용자가 생략한 subject 보완
기간/역할/성과유형 조건 추출
어떤 tool을 쓸지 결정
tool 결과를 보고 다음 행동 결정
자연스러운 clarification 생성
8.2 Agent가 하면 안 되는 것
pjt_id, pjt_no, rst_id, person_no 생성
JOIN legality 판단을 우회
SEARCH/LOOKUP/JOIN 내부 정책 직접 변경
raw Qdrant filter 직접 생성
QuestionAnalysisV3 필드 직접 조작
검증 실패 결과를 근거 있는 사실처럼 답변
8.3 Tool Backend가 보장해야 하는 것
HardContractV1 준수
QuestionAnalysisV3 검증
pjt_id와 pjt_no 축 분리
exact ID 완화 금지
JOIN anchor legality
evidence projection 생성
answer publication 가능 여부 판단
9. Workflow Changes
   9.1 Existing Workflow
   load_memory
   → rule_precheck
   → analyze_question
   → judge_knowledge_sufficiency
   → rag_search
   → answer_generation
   → merge
   → save_history
   9.2 New Workflow

Production workflow (2026-04-22 실제 구현 기준, workflow_builder.py):

load_memory
→ rule_precheck
   ├─(direct_answer precheck)→ direct_answer → save_history
   └─(normal)→ build_conversation_state_card
→ run_dialogue_agent
   ├─ agent_direct_answer    → save_history
   ├─ agent_clarification    → save_history
   ├─ agent_internal_error   → save_history
   └─ execute_agent_tool
        ├─(observation_type == "planned_intent")
        │   → judge_knowledge_sufficiency
        │     ├─(prev_context sufficient)→ generate_answer_solar / generate_answer_gemma
        │     └─(else)→ rag_search
        │         ├─ relax_and_retry → rag_search
        │         └─ generate_answer_* → join_answers → merge_answers → save_history
        └─(contract violation / no planned_intent)→ agent_clarification → save_history

설계 메모:
- ADR 원안의 `dialogue_agent_final`, `answer_validation`, `publish_context` 별도 노드는 도입하지 않는다.
  그 책임은 다음 위치에 흡수된다.
  * `dialogue_agent_final` 역할: 기존 answer generation (generate_answer_solar / generate_answer_gemma)
    그리고 `merge_answers` 노드가 Agent observation을 이어받아 자연어 답변을 조립한다.
  * `answer_validation` / `publish_context` 역할: `merge_answers` 내부의 publication gate와
    기존 HardContract / QuestionAnalysisV3 검증, 그리고 `save_history` 내부의 context publish 로직.
- Agent는 tool을 1회 호출하고 그 결과를 기존 retrieval→answer 파이프라인으로 넘긴다.
  Observation 기반 2차 Agent 호출(self-iterating loop)은 현 단계에서 도입하지 않으며,
  `AGENTIC_MAX_STEPS` 는 향후 loop 도입 시의 상한을 선언한다.

Implementation update 2026-04-23 (C1 Option B 최소 observation 훅):
- `AgentState`에 `agent_answer_context: Optional[AgentAnswerContext]` 필드 추가.
  `AgentAnswerContext`는 `note: Optional[str]`, `source_observation_type: Optional[str]`만 담는
  순수 정보 계약이며, 전략 필드(mode/relation/target_cols/join_key_mode 등)는 금지된다.
- `node_execute_agent_tool`과 `node_retry_dialogue_agent_after_tool_error`가 성공 경로에서
  `AgentObservation.answer_note`를 복사해 `agent_answer_context`로 승격한다.
  실패/contract_violation 경로에서는 `agent_answer_context`를 None으로 유지한다.
- `node_generate_answer_gemma` / `node_generate_answer_solar`는 prompt template의
  `{agent_observation_note}` placeholder를 `agent_answer_context.note or ""`로 채운다.
  값이 없으면 빈 문자열로 대체되어 기존 프롬프트와 문법적으로 동일해진다 (정적 fallback guard).
- 본 훅은 Planner truth·HardContract·VisibleAnswerManifest를 건드리지 않으며,
  실패 시 무시되도록 모든 소비자는 None/빈값 fallback을 강제 유지한다.
- 운영 관측: `AGENT.ANSWER_NOTE.ATTACHED` 이벤트를 `execute_agent_tool` 단계에서 1회 발행하여
  note 길이/source observation_type만 기록 (내용 전문은 로그 금지).
10. Runtime Guardrails
    agentic front-controller
AGENTIC_MAX_STEPS=3
초기값은 모두 off.
Agentic front-controller is the normal production path; old front-controller and shadow branches are not runtime modes.

11. State and Memory Design
    11.1 유지할 내부 구조

SessionMemory와 CurrentContext는 유지한다.
특히 SubjectQueryContext는 Agent가 “현재 대화 주제”를 이해하는 핵심 truth다.

11.2 추가할 Agent용 State Card
class ConversationStateCard(BaseModel):
current_subject_kind: Optional[str]
current_subject_name: Optional[str]
current_subject_ids_map: dict[str, list[str]]
current_context_type: str
refinement_allowed: bool
last_user_task: Optional[str]
last_result_summary: Optional[str]
last_publication_status: Optional[str]
unresolved_question: Optional[str]
unresolved_constraints: dict[str, Any]

이 모델은 내부적으로만 쓰고, LLM에는 자연어 카드로 렌더링한다.

11.3 Subject Context 승격 규칙 수정

현재는 publishability == "publishable"일 때만 SubjectQueryContext가 강하게 유지된다.

Agentic 구조에서는 다음을 허용한다.

최종 답변이 full publish되지 않았더라도,
사용자 질의에서 subject가 명확하고 retrieval에서 해당 subject 근거가 발견되면
"dialogue subject context"는 유지할 수 있다.

단, 이 context는 answer truth가 아니라 dialogue continuity truth다.

새 필드 제안:

class SubjectQueryContext(BaseModel):
context_type: Literal["subject_query"]
subject_kind: str
subject_name: str
subject_ids_map: dict[str, list[str]]
result_kind: str = "project"
result_manifest: Optional[DisplaySnapshot] = None
followup_rights: FollowupRights
publication_status: Optional[Literal[
"answer_published",
"answer_withheld_subject_retained",
"clarification_pending",
]] = None
# 기본값 None은 "값 미정/초기 상태"를 뜻한다. build_current_context 는
# publishability=="publishable" 이면 "answer_published",
# publishability in {"withheld_partial","blocked"} 이고 subject manifest 가
# 유지되면 "answer_withheld_subject_retained"를 설정한다.
# "clarification_pending"은 ClarificationContext 가 별도로 존재하므로
# 사용되지 않을 수 있으며, 향후 subject context가 clarification을 끌고 가는
# 케이스가 필요해지면 그 때 사용한다.
12. File-level Migration Plan
    Phase 0. 준비
    변경 없음
    HardContractV1
    QuestionAnalysisV3
    IntentPayloadV3
    기존 retrieval core

이들은 Tool Backend에서 계속 사용한다.

Phase 1. Agent State Card 추가
Add
apps/conversation/conversation_state_card.py

역할:

SessionMemory.current_context 요약
최근 answer meta 요약
unresolved constraints 요약
LLM-readable state card 생성
Tests
SubjectQueryContext(신동구) → "현재 subject: 신동구" 포함
ClarificationContext(2014~2023 unresolved) → unresolved constraints 포함
EmptyContext → "현재 확정 subject 없음" 포함
Phase 2. Tool Registry 추가
Add
apps/conversation/agent_tools.py
apps/conversation/agent_tool_executor.py
apps/conversation/agent_observation.py

초기 tool은 2개만 구현한다.

search_ntis_domain
refine_current_subject

lookup_specific_entity, join_project_perf는 Phase 3 이후.

Phase 3. Dialogue Agent 추가
Add
apps/conversation/agent_contracts.py
apps/conversation/agent_dialogue_router.py
apps/prompts/dialogue_agent_v1.md
Prompt 핵심
당신은 NTIS RAG 시스템의 대화 오케스트레이터다.
사용자의 말뜻을 대화 문맥에서 해석하고, 필요한 경우 tool을 호출한다.

중요:
- 사용자가 "해당 연구자", "그 사람", "이 연구원"이라고 하면 Conversation State Card의 current subject를 우선 사용한다.
- 기간, 역할, 성과유형, 개수 조건만 추가한 경우 clarification하지 말고 refine_current_subject를 호출한다.
- 정말 대상이 불명확할 때만 ask_user_for_clarification을 사용한다.
- ID를 새로 만들지 말라.
- Tool 결과에 없는 사실을 단정하지 말라.
  Phase 4. Workflow에 Agent Node 삽입
  Modify
  apps/api/workflow_builder.py

Production path:

load_memory
→ rule_precheck
→ dialogue_agent
→ ...

Old front-controller pipeline is not a runtime mode.

Phase 5. request_facade 강등
Modify
apps/conversation/request_facade.py

agent tool backend는 build_agent_intent_payload()로 old turn front-controller 없이 planner / QuestionAnalysisV3 / HardContractV1 검증을 통과한다.

실제 구현 함수 (2026-04-22 업데이트: 이름/시그니처 반영):

async def build_agent_intent_payload(
*,
question: str,
conversation_id: str,
chat_history: list[BaseMessage],
prev_context: list[dict] | None,
request_id: str,
turn_id: str,
canonical_evidence: list[dict] | None,
view_state: ConversationViewState | None,
session_memory: SessionMemory | None,
) -> tuple[IntentPayloadV3, QuestionAnalysisV3]:
    """
    run_turn_trigger / run_turn_interpreter / run_turn_policy / run_context_router 를
    호출하지 않고, run_question_analysis → apply_question_analysis_v3 만으로
    guarded IntentPayloadV3 / QuestionAnalysisV3를 생성한다.
    Agent tool executor의 search_ntis_domain / refine_current_subject 경로가 이 함수를 사용한다.
    """
Phase 6. Publication Context 조정
Modify
apps/conversation/session_memory.py

목표:

full answer publish 실패와 dialogue subject retention 분리
SubjectQueryContext를 answer truth가 아니라 dialogue continuity truth로도 유지 가능하게 함
ClarificationContext의 unresolved constraints를 Agent State Card에 노출
13. Agent Prompt Draft
    당신은 국가 R&D 데이터를 다루는 지능형 연구 어시스턴트입니다.

당신의 역할은 사용자의 말을 대화 문맥 안에서 이해하고,
필요한 도구를 호출하여 근거 기반 답변을 만드는 것입니다.

[중요 원칙]
1. 사용자가 주어를 생략하면 Conversation State Card의 current subject를 먼저 확인하세요.
2. "해당 연구자", "그 연구원", "이 사람"은 current subject가 있으면 그 대상을 가리킵니다.
3. 기간, 역할, 성과유형, 개수, 정렬 조건만 추가된 질문은 clarification하지 말고 refine_current_subject 도구를 사용하세요.
4. 정말 대상이 여러 명이고 구분할 수 없을 때만 ask_user_for_clarification을 사용하세요.
5. 도구 결과에 없는 사실은 말하지 마세요.
6. pjt_id, pjt_no, rst_id, person_no 같은 식별자는 새로 만들지 마세요.
7. 검색/조회가 필요한 질문은 적극적으로 도구를 사용하세요.

[사용 가능한 도구]
- search_ntis_domain
- refine_current_subject
- lookup_specific_entity
- join_project_perf
- ask_user_for_clarification
14. Example Scenario
    Case 1. Subject refinement
    Input
    사용자: 신동구 연구자의 활동기록
    Agent decision
    {
    "decision_type": "call_tool",
    "tool_name": "search_ntis_domain",
    "tool_args": {
    "query": "신동구 연구자 활동기록",
    "domain_head": "people",
    "people_name": "신동구"
    }
    }
    Next input
    사용자: 해당 연구자의 2014년~2023년까지의 활동내역은?
    Conversation State Card
    current subject: 신동구 (people)
    refinement allowed: yes
    last task: activity history
    Agent decision
    {
    "decision_type": "call_tool",
    "tool_name": "refine_current_subject",
    "tool_args": {
    "subject_ref": "current_subject",
    "year_from": 2014,
    "year_to": 2023,
    "target": "activity_history"
    }
    }
    Case 2. Role narrowing
    Input
    사용자: 연구책임자로 활동한 과제만 보여줘
    Agent decision
    {
    "decision_type": "call_tool",
    "tool_name": "refine_current_subject",
    "tool_args": {
    "subject_ref": "current_subject",
    "role": "연구책임자",
    "target": "project"
    }
    }
    Case 3. True ambiguity
    Input
    사용자: 신동구 활동내역 보여줘
    State
    current subject: none
    known candidates:
- 신동구 / 한국과학기술정보연구원
- 신동구 / 다른 기관
  Agent decision
  {
  "decision_type": "ask_clarification",
  "clarification_question": "신동구라는 이름의 후보가 여러 명일 수 있습니다. 한국과학기술정보연구원 소속 신동구 연구자를 기준으로 정리하면 될까요?",
  "confidence": 0.42
  }
15. Validation Plan
    Unit Tests
    conversation_state_card
    SubjectQueryContext → current subject 렌더링
    ClarificationContext → unresolved constraints 렌더링
    PublishedManifestContext → manifest summary 렌더링
    agent_dialogue_router
    current subject + 기간 조건 → refine_current_subject
    current subject + role 조건 → refine_current_subject
    명시 새 이름 → search_ntis_domain
    true ambiguity → ask_user_for_clarification
    agent_tool_executor
    refine_current_subject가 SessionMemory.current_context의 subject를 seed로 사용
    year filter가 planner/retrieval까지 전달
    role filter가 people facet narrowing으로 전달
    tool result가 AgentObservation으로 반환
    Golden Tests
    G1. 문제 재현 케이스
    신동구 연구자의 활동기록
    해당 연구자의 2014년~2023년까지의 활동내역은?

기대:

clarification 없이 refine_current_subject 호출
G2. Role narrowing
신동구 연구자의 활동기록
연구책임자로 활동한 과제만 보여줘

기대:

role=연구책임자, target=project
G3. Clarification recovery
해당 연구자의 2014년~2023년 활동내역은?
신동구

기대:

직전 unresolved constraint(year_from=2014, year_to=2023) 복원
people_name=신동구와 병합
G4. ID safety
PJT_NO=xxx 성과 전체

기대:

Agent가 직접 JOIN 처리하지 않고 join_project_perf tool 호출
Tool backend에서 pjt_no group semantics 유지
16. Observability

신규 이벤트:

AGENT.DECISION
AGENT.PARSE_ERROR
AGENT.SELF_REPAIR.START
AGENT.SELF_REPAIR.SUCCESS
AGENT.SELF_REPAIR.FAILED
AGENT.INVOKE_ERROR
AGENT.TOOL_CALL
AGENT.TOOL_OBSERVATION
AGENT.CLARIFICATION
AGENT.CONTRACT_BLOCKED
AGENT.STATE_CARD.BUILT
AGENT.DIRECT_ANSWER
AGENT.INTERNAL_ERROR
AGENT.LOOP_GUARD.TRIGGERED

예:

{
"event": "AGENT.DECISION",
"decision_type": "call_tool",
"tool_name": "refine_current_subject",
"confidence": 0.86,
"current_context_type": "subject_query",
"subject_kind": "people",
"subject_name": "신동구"
}
17. Rollout Plan
Agentic front-controller is the normal production path; old front-controller and shadow branches are not runtime modes.
Agent decision만 생성
실제 user-visible 실행은 기존 workflow graph가 담당하되, Agent decision 결과에는 이전 front-controller 경로을 두지 않는다.
agentic front-controller
Stage 1. Guarded Tool Path (current)
`agentic front-controller`이면 user-visible workflow가 agent direct/tool/clarification/internal-error path로 진입한다.
`search_ntis_domain`과 `refine_current_subject`는 guarded intent를 만들어 기존 retrieval path에 합류한다. unknown tool은 self-repair 대상이고, registry에 선언되었으나 미구현인 tool과 contract violation은 `AGENT.CONTRACT_BLOCKED` / contract block으로 닫는다.
agentic front-controller
    Stage 2. Search Tool Enable
    명시적 새 검색도 Agent가 search_ntis_domain 호출
Stage 3. Lookup / Join Enable
lookup_specific_entity
join_project_perf
Stage 4. Legacy front-controller 제거
안정화 후 turn_trigger, turn_interpreter, turn_policy의 front-controller 역할을 제거하고 validator/materializer로 축소한다.
18. Risks
    R1. LLM이 과도하게 tool을 호출할 수 있음

대응:

AGENTIC_MAX_STEPS
tool timeout
duplicate tool call suppression
same query cache
R2. LLM이 잘못된 subject를 이어받을 수 있음

대응:

Conversation State Card에 confidence 포함
subject retention status 구분
low confidence면 clarification
tool backend에서 contract validation
R3. 답변 자연스러움은 좋아지나 재현성이 낮아질 수 있음

대응:

Agent decision logging
front-controller log verification
golden regression 유지
final answer validation 유지
R4. 기존 코드와 병렬 운영 복잡도 증가

대응:

single production path with guarded tools
agent tool adapter를 기존 request_facade 위에 얇게 구성
기존 runtime graph는 agent path가 대체 가능한 단위부터 제거한다.
19. Acceptance Criteria

이 ADR은 다음 조건을 만족하면 성공으로 본다.

“해당 연구자의 2014년~2023년 활동내역”이 clarification 없이 current subject refinement로 처리된다.
“연구책임자로 활동한 과제만”이 role narrowing으로 처리된다.
첫 턴 final answer가 일부 withheld여도, subject context는 dialogue continuity 용도로 유지된다.
LLM이 직접 ID를 생성하지 않는다.
기존 HardContractV1, QuestionAnalysisV3 검증은 tool backend에서 계속 적용된다.
Agentic front-controller is the only supported production workflow path.
Agent decision, tool call, observation이 모두 로그로 남는다.
20. Final Decision Summary

본 ADR은 기존 RAG core를 폐기하지 않는다.

대신 다음 전환을 수행한다.

Deterministic dialogue pipeline as front controller
→ LLM-first Dialogue Agent as front controller

Planner / Contract / Retrieval as front controller
→ Planner / Contract / Retrieval as tool backend

핵심 문장:

LLM이 대화의 주도권을 가진다.
기존 Planner / HardContract / Retrieval은 LLM의 판단을 안전하게 실행하는 Tool Backend로 재배치한다.
