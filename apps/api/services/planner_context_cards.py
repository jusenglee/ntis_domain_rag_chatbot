from __future__ import annotations

from pathlib import Path
from typing import Any


_CARD_FILES = {
    "collections_card": Path("prompts/cards/collections_card.md"),
    "relationship_semantics_card": Path("prompts/cards/relationship_semantics_card.md"),
    "id_semantics_card": Path("prompts/cards/id_semantics_card.md"),
    "filter_semantics_card": Path("prompts/cards/filter_semantics_card.md"),
    "perf_tag_card": Path("prompts/cards/perf_tag_card.md"),
    "field_reliability_card": Path("prompts/cards/field_reliability_card.md"),
    "semantic_disambiguation_card": Path("prompts/cards/semantic_disambiguation_card.md"),
}

_PROMPT_CARD_MANIFEST = {
    "planner_stage1_v1": (
        "collections_card",
        "id_semantics_card",
        "filter_semantics_card",
        "perf_tag_card",
        "field_reliability_card",
        "semantic_disambiguation_card",
    ),
    "planner_stage1_v2": (
        "collections_card",
        "relationship_semantics_card",
        "id_semantics_card",
        "filter_semantics_card",
        "perf_tag_card",
        "field_reliability_card",
        "semantic_disambiguation_card",
    ),
    "planner_stage15_v1": (
        "collections_card",
        "relationship_semantics_card",
        "id_semantics_card",
        "filter_semantics_card",
        "perf_tag_card",
        "field_reliability_card",
        "semantic_disambiguation_card",
    ),
    "planner_stage2_v1": (
        "collections_card",
        "id_semantics_card",
        "filter_semantics_card",
        "perf_tag_card",
        "field_reliability_card",
        "semantic_disambiguation_card",
    ),
    "planner_stage2_v2": (
        "collections_card",
        "relationship_semantics_card",
        "id_semantics_card",
        "filter_semantics_card",
        "perf_tag_card",
        "field_reliability_card",
        "semantic_disambiguation_card",
    ),
}


def required_cards_for(prompt_name: str) -> tuple[str, ...]:
    try:
        return _PROMPT_CARD_MANIFEST[prompt_name]
    except KeyError as exc:
        raise RuntimeError(f"unknown planner prompt manifest: {prompt_name}") from exc


async def build_planner_domain_cards(*, load_prompt_file: Any, prompt_name: str) -> dict[str, str]:
    cards: dict[str, str] = {}
    for key in required_cards_for(prompt_name):
        cards[key] = await load_prompt_file(_CARD_FILES[key])
    return cards
