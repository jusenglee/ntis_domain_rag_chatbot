from __future__ import annotations

from typing import Any, Optional

from apps.planner.planner_runtime import run_stagewise_question_analysis
from apps.planner.query_intent import classify_query as classify_query_intent
from apps.platform.pipeline_steps import normalize_intent


async def run_question_analysis(
    *,
    question: str,
    conversation_id: str,
    chat_history: list[Any],
    prev_context: list[dict[str, Any]],
    canonical_evidence: list[dict[str, Any]] | None = None,
    view_state: Any = None,
    request_id: Optional[str] = None,
    normalized_intent_base: Any = None,
) -> Any:
    """Prepare normalized intent and delegate to the stagewise planner runtime."""

    normalized_intent = normalized_intent_base or normalize_intent(
        classify_query_intent(question, [], hint={}),
        query=question,
        keywords=[],
    )

    return await run_stagewise_question_analysis(
        question=question,
        conversation_id=conversation_id,
        request_id=request_id,
        chat_history=chat_history,
        prev_context=prev_context,
        canonical_evidence=canonical_evidence or [],
        view_state=view_state,
        normalized_intent=normalized_intent,
    )
