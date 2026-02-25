from __future__ import annotations

from schemas import StrategySpec, strategy_spec_to_response, to_strategy_spec


def test_to_strategy_spec_normalizes_join_meta_fields() -> None:
    raw = {
        "mode": "JOIN",
        "action": "RELATION",
        "relation": ["project", "perf"],
        "join_key_mode": "INSTANCE",
        "join_key_source": "IDS_MAP",
        "hop1_mode": "LOOKUP",
        "hop2_key_strategy": "BOTH",
        "join_keys_used_count": "7",
    }

    spec = to_strategy_spec(raw)

    assert spec.mode == "join"
    assert spec.action == "relation"
    assert spec.relation == ("project", "perf")
    assert spec.join_key_mode == "instance"
    assert spec.join_key_source == "ids_map"
    assert spec.hop1_mode == "lookup"
    assert spec.hop2_key_strategy == "both"
    assert spec.join_keys_used_count == 7


def test_strategy_spec_to_response_includes_join_meta_fields() -> None:
    spec = StrategySpec(
        mode="join",
        action="relation",
        relation=("project", "perf"),
        join_key_mode="group",
        join_key_source="hop1",
        hop1_mode="search",
        hop2_key_strategy="pjt_no",
        join_keys_used_count=3,
    )

    response = strategy_spec_to_response(spec)

    assert response["join_key_mode"] == "group"
    assert response["join_key_source"] == "hop1"
    assert response["hop1_mode"] == "search"
    assert response["hop2_key_strategy"] == "pjt_no"
    assert response["join_keys_used_count"] == 3
