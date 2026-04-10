from __future__ import annotations

from pathlib import Path


_APPS_ROOT = Path(__file__).resolve().parents[1]
_PROMPTS_ROOT = (_APPS_ROOT / "prompts").resolve()
_PROMPT_CARDS_ROOT = (_PROMPTS_ROOT / "cards").resolve()


def planner_prompt_path(filename: str) -> Path:
    """Resolve a planner prompt asset under apps/prompts."""

    return (_PROMPTS_ROOT / filename).resolve()


def planner_card_path(filename: str) -> Path:
    """Resolve a planner domain-card asset under apps/prompts/cards."""

    return (_PROMPT_CARDS_ROOT / filename).resolve()
