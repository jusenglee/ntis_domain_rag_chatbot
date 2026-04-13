from __future__ import annotations

from apps.chat.llm_runtime import load_prompt_file
from apps.planner.prompt_asset_paths import planner_card_path


_CARD_FILES = {
    "collections_card": planner_card_path("collections_card.md"),
    "relationship_semantics_card": planner_card_path("relationship_semantics_card.md"),
    "id_semantics_card": planner_card_path("id_semantics_card.md"),
    "filter_semantics_card": planner_card_path("filter_semantics_card.md"),
    "perf_tag_card": planner_card_path("perf_tag_card.md"),
    "field_reliability_card": planner_card_path("field_reliability_card.md"),
    "semantic_disambiguation_card": planner_card_path("semantic_disambiguation_card.md"),
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
        "relationship_semantics_card",
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


async def build_planner_domain_cards(*, prompt_name: str) -> dict[str, str]:
    cards: dict[str, str] = {}
    for key in required_cards_for(prompt_name):
        cards[key] = await load_prompt_file(_CARD_FILES[key])
    return cards
