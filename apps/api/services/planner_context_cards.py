from __future__ import annotations

from pathlib import Path
from typing import Any


_CARD_FILES = {
    "collections_card": Path("prompts/cards/collections_card.md"),
    "id_semantics_card": Path("prompts/cards/id_semantics_card.md"),
    "filter_semantics_card": Path("prompts/cards/filter_semantics_card.md"),
    "perf_tag_card": Path("prompts/cards/perf_tag_card.md"),
    "field_reliability_card": Path("prompts/cards/field_reliability_card.md"),
    "semantic_disambiguation_card": Path("prompts/cards/semantic_disambiguation_card.md"),
}


async def build_planner_domain_cards(*, load_prompt_file: Any) -> dict[str, str]:
    cards: dict[str, str] = {}
    for key, path in _CARD_FILES.items():
        cards[key] = await load_prompt_file(path)
    return cards
