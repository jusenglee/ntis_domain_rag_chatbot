"""ADR-0014 Scope B clarification prose composer.

This module is the single synthesis point for clarification/notice copy.
It keeps the semantic decision (`blocked_reason`, selection requirement,
publishability premise) in deterministic templates and lets an optional LLM
only paraphrase that seed when explicitly enabled.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional

from apps.api.runtime_helpers import log_event


_TRUTHY = {"1", "true", "yes", "y", "on"}
_LLM_MODEL_NAME = "solar_vllm_0"
_LLM_TIMEOUT_SECONDS = 3.0
_MAX_SUGGESTIONS_IN_MESSAGE = 5

_SUBJECT_BY_KIND = {
    "project": "과제",
    "perf": "성과",
    "people": "연구자",
    "org": "기관",
}


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def llm_prose_enabled() -> bool:
    return _norm_lower(os.getenv("LLM_PROSE_ENABLED", "0")) in _TRUTHY


def _timeout_seconds() -> float:
    raw = _norm(os.getenv("LLM_PROSE_TIMEOUT_SECONDS"))
    if not raw:
        return _LLM_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _LLM_TIMEOUT_SECONDS
    return value if value > 0 else _LLM_TIMEOUT_SECONDS


def _extract_suggestion_label(suggestion: Any) -> Optional[str]:
    if suggestion is None:
        return None
    if isinstance(suggestion, str):
        return _norm(suggestion) or None
    label = _norm(getattr(suggestion, "label", None))
    if label:
        return label
    if isinstance(suggestion, dict):
        for key in ("label", "title", "title_text", "display_name", "name", "candidate_id"):
            value = _norm(suggestion.get(key))
            if value:
                kind = _norm(suggestion.get("entity_kind") or suggestion.get("context_kind"))
                if kind and key != "label" and f"({kind})" not in value:
                    return f"{value} ({kind})"
                return value
    return None


def _suggestion_labels(suggestions: Iterable[Any] | None) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for suggestion in list(suggestions or []):
        label = _extract_suggestion_label(suggestion)
        if not label or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return labels


def _focus_summary(focus_entity: Any) -> dict[str, Any]:
    if focus_entity is None:
        return {}
    return {
        "kind": _norm(getattr(focus_entity, "kind", None)) or None,
        "source": _norm(getattr(focus_entity, "source", None)) or None,
        "title_text": _norm(getattr(focus_entity, "title_text", None)) or None,
        "view_id": _norm(getattr(focus_entity, "view_id", None)) or None,
    }


def _subject_from_ctx(ctx: dict[str, Any]) -> str:
    explicit = _norm(ctx.get("subject"))
    if explicit:
        return explicit
    kind = _norm_lower(ctx.get("subject_kind") or ctx.get("context_kind"))
    return _SUBJECT_BY_KIND.get(kind, "항목")


def _template_for_reason(blocked_reason: str, ctx: dict[str, Any], labels: list[str]) -> tuple[str, str]:
    reason = _norm_lower(blocked_reason) or "unknown"
    reference_kind = _norm_lower(ctx.get("reference_kind"))
    status = _norm_lower(ctx.get("status"))
    subject = _subject_from_ctx(ctx)
    available_count = int(ctx.get("available_count") or 0)

    if reason == "child_entity_ambiguity":
        return reason, f"현재 상세 안에서 어떤 {subject}를 가리키는지 다시 지정해 주세요."
    if reason == "child_entity_missing_id":
        return reason, f"현재 상세 안에서 언급된 {subject}는 찾았지만, 정확한 식별자를 확인할 수 없습니다. 다른 기준으로 다시 지정해 주세요."
    if reason == "detail_requires_instance_project_id":
        return reason, "최근 언급한 과제는 묶음 수준 식별자만 있어 개별 상세를 바로 특정할 수 없습니다. 제목이나 개별 과제를 다시 지정해 주세요."
    if reason in {"recent_mention_year_ambiguity", "recent_mention_ambiguity", "reference_ambiguity"} and labels:
        return reason, "최근 언급한 대상이 여러 개입니다. 번호나 제목으로 지정해 주세요."
    if reason == "refinement_target_missing":
        return reason, "무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요."
    if reason == "reference_missing_context":
        return reason, "이전 결과 목록이나 상세 맥락이 없어 무엇을 가리키는지 판단하기 어렵습니다. 먼저 목록을 확인해 주세요."
    if reason == "unresolved_reference":
        return reason, "이전 결과 중 어떤 항목을 뜻하는지 다시 지정해 주세요."

    if reason in {"source_reference_missing_context", "source_reference_out_of_range", "source_reference_unresolved"}:
        reference_kind = "source_reference"
        status = reason.removeprefix("source_reference_")
    elif reason in {"ordinal_missing_context", "ordinal_out_of_range", "ordinal_unresolved"}:
        reference_kind = reference_kind or "ordinal"
        status = reason.split("_", 1)[1]

    if reference_kind == "source_reference":
        if status == "missing_context":
            return reason, "이전 출처 목록이 없어 몇 번째 출처인지 판단하기 어렵습니다. 먼저 목록을 확인한 뒤 다시 질문해 주세요."
        if status == "out_of_range":
            return reason, f"이전 출처 목록에는 {available_count}개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요."
        if status == "unresolved":
            return reason, "이전 목록에서 어느 출처를 말씀하시는지 확인해 주세요."

    if status == "missing_context":
        return reason, f"이전 목록이 없어 몇 번째 {subject}인지 판단하기 어렵습니다. 먼저 목록을 확인한 뒤 다시 질문해 주세요."
    if status == "out_of_range":
        return reason, f"이전 목록에는 {available_count}개만 있습니다. 몇 번째 {subject}를 말씀하시는지 다시 알려주세요."
    if status == "unresolved":
        return reason, f"이전 목록에서 어느 {subject}를 말씀하시는지 확인해 주세요."

    if reason == "non_publishable_previous_turn":
        return reason, "직전 응답은 번호나 출처를 재사용할 수 있는 publishable 목록이 아닙니다. 대상을 다시 지정해 주세요."
    if reason == "same_kind_candidates_missing":
        return reason, "질문에서 요구한 대상 축에 맞는 후보가 없어 대상을 특정할 수 없습니다. 대상을 다시 지정해 주세요."
    if labels:
        return reason, "어떤 대상을 가리키는지 확인해 주세요."
    return reason, "이전 대화의 어떤 대상을 가리키는지 다시 지정해 주세요."


def _append_suggestions(seed: str, labels: list[str]) -> str:
    if not labels:
        return seed
    visible = labels[:_MAX_SUGGESTIONS_IN_MESSAGE]
    if all(label in seed for label in visible):
        return seed
    return f"{seed} 후보: {' / '.join(visible)}"


def _extract_llm_text(result: Any) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(_norm(item.get("text") or item.get("content")))
            else:
                parts.append(_norm(item))
        return " ".join(part for part in parts if part).strip()
    return _norm(content)


def _llm_output_is_safe(message: str, *, seed: str, labels: list[str]) -> bool:
    text = _norm(message)
    if not text:
        return False
    if len(text) > max(800, len(seed) * 4):
        return False
    return all(label in text for label in labels)


async def _compose_llm_prose_async(
    *,
    question: str,
    blocked_reason: str,
    seed: str,
    labels: list[str],
    focus_entity: Any,
    view_state_summary: dict[str, Any],
    ctx: dict[str, Any],
) -> str:
    from apps.chat.llm_runtime import build_llm, load_prompt_file
    from apps.platform.langchain_compat import ChatPromptTemplate, SystemMessage
    from apps.planner.planner_defaults import PLANNER_DISABLE_THINKING, PLANNER_TEMPERATURE
    from apps.planner.prompt_asset_paths import planner_prompt_path

    llm = build_llm(model_name=_LLM_MODEL_NAME)
    system_prompt = await load_prompt_file(planner_prompt_path("clarification_prose_v1.md"))
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(content=system_prompt),
            (
                "human",
                "<question>{question}</question>\n"
                "<blocked_reason>{blocked_reason}</blocked_reason>\n"
                "<seed>{seed}</seed>\n"
                "<suggestions>{suggestions}</suggestions>\n"
                "<focus_entity>{focus_entity}</focus_entity>\n"
                "<view_state_summary>{view_state_summary}</view_state_summary>\n"
                "<ctx>{ctx}</ctx>",
            ),
        ]
    )
    prose_llm = llm.bind(
        reasoning_effort="low",
        include_reasoning=False,
        disable_thinking=PLANNER_DISABLE_THINKING,
        temperature=PLANNER_TEMPERATURE,
        top_p=1.0,
        max_tokens=220,
    )
    chain = prompt | prose_llm
    result = await chain.ainvoke(
        {
            "question": question,
            "blocked_reason": blocked_reason,
            "seed": seed,
            "suggestions": json.dumps(labels, ensure_ascii=False),
            "focus_entity": json.dumps(_focus_summary(focus_entity), ensure_ascii=False),
            "view_state_summary": json.dumps(view_state_summary or {}, ensure_ascii=False),
            "ctx": json.dumps(ctx or {}, ensure_ascii=False),
        }
    )
    return _extract_llm_text(result)


def _run_llm_prose(
    *,
    question: str,
    blocked_reason: str,
    seed: str,
    labels: list[str],
    focus_entity: Any,
    view_state_summary: dict[str, Any],
    ctx: dict[str, Any],
) -> str:
    timeout = _timeout_seconds()
    coro = asyncio.wait_for(
        _compose_llm_prose_async(
            question=question,
            blocked_reason=blocked_reason,
            seed=seed,
            labels=labels,
            focus_entity=focus_entity,
            view_state_summary=view_state_summary,
            ctx=ctx,
        ),
        timeout=timeout,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(lambda: asyncio.run(coro))
    try:
        return future.result(timeout=timeout + 0.5)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _emit_prose_event(
    *,
    blocked_reason: str,
    seed_template_id: str,
    prose_source: str,
    started_at: float,
) -> None:
    try:
        log_event(
            "CLARIFICATION.PROSE",
            blocked_reason=blocked_reason,
            seed_template_id=seed_template_id,
            prose_source=prose_source,
            generation_latency_ms=round((time.perf_counter() - started_at) * 1000, 3),
        )
    except Exception:
        return


def compose_clarification_message(
    *,
    question: str,
    blocked_reason: str,
    suggestions: Iterable[Any] | None,
    focus_entity: Any,
    view_state_summary: dict[str, Any] | None,
    ctx: dict[str, Any] | None,
) -> str:
    """Compose a user-facing clarification message.

    The deterministic seed is authoritative. The optional LLM path can only
    paraphrase it and must preserve all suggestion labels verbatim.
    """

    started_at = time.perf_counter()
    safe_ctx = dict(ctx or {})
    labels = _suggestion_labels(suggestions)
    seed_template_id, seed = _template_for_reason(blocked_reason, safe_ctx, labels)
    seed = _append_suggestions(seed, labels)
    prose_source = "template"
    message = seed

    if llm_prose_enabled():
        try:
            candidate = _run_llm_prose(
                question=_norm(question),
                blocked_reason=_norm(blocked_reason),
                seed=seed,
                labels=labels,
                focus_entity=focus_entity,
                view_state_summary=dict(view_state_summary or {}),
                ctx=safe_ctx,
            )
            if _llm_output_is_safe(candidate, seed=seed, labels=labels):
                message = candidate
                prose_source = "llm"
            else:
                prose_source = "fallback"
        except Exception:
            prose_source = "fallback"

    _emit_prose_event(
        blocked_reason=_norm(blocked_reason),
        seed_template_id=seed_template_id,
        prose_source=prose_source,
        started_at=started_at,
    )
    return message

