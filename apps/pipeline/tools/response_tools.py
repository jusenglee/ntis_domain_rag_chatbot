"""Phase 5 Step 4 — 응답 도구.

능동 에이전트 철학: RAG는 도구 중 하나일 뿐이고, PlannerAgent는 "도구 호출 없이 답변/거절"을
직접 선택할 수 있어야 한다. 이 모듈은 그 두 선택지를 표준 도구로 노출한다.

도구:
    - response.direct_answer  : 도구 호출 없이 직접 답변 (인사/소개/메타/간단 안내).
    - response.unsupported    : NTIS R&D 검색 범위 밖 질문에 정직 거절 + 가능한 질문 예시.

두 도구의 결과 dict에는 `final_text` 키가 들어가며, answer_curator가 이를 우선 적용해
AnswerAgent/Critic을 건너뛰고 final_answer_text로 흐른다.
"""

from __future__ import annotations

from typing import Any, Dict, List

from apps.pipeline.tools.contracts import ToolContext, ToolEntry, ToolSpec


# ============================================================================
# response.direct_answer
# ============================================================================

RESPONSE_DIRECT_ANSWER_SPEC = ToolSpec(
    name="response.direct_answer",
    description=(
        "도구 호출 없이 직접 답변. 인사·소개·메타 응답·간단 안내처럼 RAG가 필요 없는 발화일 때 사용. "
        "답변 텍스트(text)를 인자로 받아 그대로 최종 답변으로 흐른다."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "사용자에게 보여줄 답변 텍스트. 1~3문장 권장.",
            },
        },
        "required": ["text"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "final_text": {"type": "string"},
            "kind": {"type": "string"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)


async def response_direct_answer_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("response.direct_answer: 'text' is required (비어 있음)")
    return {"final_text": text, "kind": "direct_answer"}


# ============================================================================
# response.unsupported
# ============================================================================

RESPONSE_UNSUPPORTED_SPEC = ToolSpec(
    name="response.unsupported",
    description=(
        "현재 연결된 외부 도구로 풀 수 없는 사용자 질문에 정직 거절. 현 시점 외부 도구는 "
        "NTIS Qdrant 벡터 DB(국가 R&D 과제·성과·참여인력 활동)뿐이며, 인사·직책·연락처·"
        "외부 사실·일반 상식·실시간 정보·사용자 사실 주입 등은 도구로 답할 수 없다. "
        "답변 텍스트는 표준 안내로 구성되며, reason 인자로 어떤 영역인지 짧게 명시한다."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "거절 사유 (사용자에게 노출됨, 1줄). 예: 'NTIS DB에 인사·직책 정보 없음', "
                    "'외부 사실 학습/저장 능력 없음', '실시간 정보 미보유'."
                ),
            },
            "examples": {
                "type": "array",
                "description": "현재 도구로 답할 수 있는 질문 예시 1~3개 (사용자에게 안내).",
            },
        },
        "required": ["reason"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "final_text": {"type": "string"},
            "kind": {"type": "string"},
        },
    },
    cost_hint="fast",
    preconditions=[],
)

_UNSUPPORTED_DEFAULT_EXAMPLES = (
    "'XX 관련 연구과제 목록'",
    "'YY 연구자의 활동내역'",
    "'ZZ 사업의 참여기관'",
)


async def response_unsupported_handler(args: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    reason = str(args.get("reason") or "현 도구로 답할 수 없는 영역").strip()
    examples = args.get("examples") or []
    if not isinstance(examples, list) or not examples:
        examples = list(_UNSUPPORTED_DEFAULT_EXAMPLES)
    examples_clean: List[str] = []
    for e in examples[:3]:
        s = str(e).strip()
        if s:
            examples_clean.append(s)
    examples_text = ", ".join(examples_clean) if examples_clean else "(예시 없음)"
    final_text = (
        f"죄송합니다. 요청하신 정보는 현재 연결된 외부 도구로 답하기 어렵습니다.\n"
        f"현 시점에 제가 가져다 쓸 수 있는 도구는 NTIS(국가과학기술지식정보서비스) R&D 데이터 "
        f"하나이며, 이 DB에는 해당 정보가 없습니다.\n사유: {reason}\n"
        f"현재 도구로 답 가능한 질문 예시: {examples_text}"
    )
    return {"final_text": final_text, "kind": "unsupported", "reason": reason}


# ============================================================================
# Registry entries
# ============================================================================

def response_tool_entries() -> List[ToolEntry]:
    return [
        ToolEntry(spec=RESPONSE_DIRECT_ANSWER_SPEC, handler=response_direct_answer_handler),
        ToolEntry(spec=RESPONSE_UNSUPPORTED_SPEC, handler=response_unsupported_handler),
    ]
