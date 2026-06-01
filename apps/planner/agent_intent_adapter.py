from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_ALLOWED_ACTIONS = {"topic", "list", "detail", "stats", "download"}
_ALLOWED_HEADS = {"project", "perf", "people", "org", "support"}
_ALLOWED_RELATIONS = {"project_perf", "perf_project"}


@dataclass(frozen=True)
class AgentIntentGateDecision:
    action: str
    head: str
    relation_candidate: str | None = None
    referential_followup: bool = False
    confidence: float = 0.92


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_action(action: Any, output_type: Any = None) -> str:
    action_norm = str(action or "").strip().lower()
    output_norm = str(output_type or "").strip().lower()

    if output_norm in {"detail", "stats", "download"}:
        return output_norm
    if output_norm in {"list", "relation", "comparison", "series"}:
        return "list"
    if output_norm == "summary":
        return "topic"

    if action_norm in _ALLOWED_ACTIONS:
        return action_norm
    if action_norm in {"id_exact", "id_fuzzy", "lookup", "view", "show"}:
        return "detail"
    if action_norm in {"search", "ask_search", "filter"}:
        return "list"
    return "topic"


def _normalize_head(head: Any) -> str:
    head_norm = str(head or "").strip().lower()
    aliases = {
        "researcher": "people",
        "person": "people",
        "human": "people",
        "organization": "org",
        "institution": "org",
        "company": "org",
        "performance": "perf",
        "result": "perf",
        "results": "perf",
        "paper": "perf",
        "patent": "perf",
        "manual": "support",
        "qna": "support",
    }
    head_norm = aliases.get(head_norm, head_norm)
    return head_norm if head_norm in _ALLOWED_HEADS else "project"


def _normalize_relation(relation: Any) -> str | None:
    if isinstance(relation, (list, tuple)) and len(relation) >= 2:
        lhs = str(relation[0] or "").strip().lower()
        rhs = str(relation[1] or "").strip().lower()
        relation_norm = f"{lhs}_{rhs}" if lhs and rhs else ""
    else:
        relation_norm = str(relation or "").strip().lower()
    return relation_norm if relation_norm in _ALLOWED_RELATIONS else None


def _has_referential_signal(*, signals: Any = None, normalized_intent: Any = None) -> bool:
    if bool(_field(signals, "ordinal_ref", None)):
        return True
    if _field(signals, "followup_cues", None):
        return True
    if _field(normalized_intent, "context_owner_lock", None):
        return True
    return False


def build_agent_intent_gate(
    *,
    normalized_intent: Any,
    signals: Any = None,
) -> AgentIntentGateDecision:
    """Adapt Agent/normalized intent truth into the planner gate contract.

    The adapter replaces the removed LLM-based high-level intent decision and
    does not infer filters, identifiers, or query text.
    """
    action = _normalize_action(
        _field(normalized_intent, "action", None),
        _field(normalized_intent, "output_type", None),
    )
    head = _normalize_head(_field(normalized_intent, "base_route", None))
    relation_candidate = _normalize_relation(_field(normalized_intent, "relation", None))

    if relation_candidate and action == "topic":
        action = "list"

    confidence = _field(normalized_intent, "planner_confidence", None)
    try:
        confidence_value = float(confidence)
    except Exception:
        confidence_value = 0.92
    confidence_value = max(0.0, min(1.0, confidence_value or 0.92))

    return AgentIntentGateDecision(
        action=action,
        head=head,
        relation_candidate=relation_candidate,
        referential_followup=_has_referential_signal(
            signals=signals,
            normalized_intent=normalized_intent,
        ),
        confidence=confidence_value,
    )
