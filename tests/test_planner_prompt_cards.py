from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest

from apps.planner.planner_context_cards import _CARD_FILES, _PROMPT_CARD_MANIFEST, build_planner_domain_cards
from apps.planner.planner_runtime import _render_prompt_template
from apps.planner.planner_stage15_types import PlannerEntityRolePlan
from apps.planner.planner_surface_signals import collect_surface_signals
from apps.planner.planner_validation import validate_stage2_slots


def test_all_required_cards_are_registered():
    for _, keys in _PROMPT_CARD_MANIFEST.items():
        for key in keys:
            assert key in _CARD_FILES


def test_relationship_card_is_required_for_stage_prompts():
    assert "relationship_semantics_card" in _PROMPT_CARD_MANIFEST["planner_stage1_v2"]
    assert "relationship_semantics_card" in _PROMPT_CARD_MANIFEST["planner_stage15_v1"]
    assert "relationship_semantics_card" in _PROMPT_CARD_MANIFEST["planner_stage2_v2"]


def test_build_planner_domain_cards_loads_only_manifest_cards():
    calls: list[Path] = []

    async def fake_load_prompt_file(path: Path) -> str:
        calls.append(path)
        return path.name

    cards = asyncio.run(
        build_planner_domain_cards(
            load_prompt_file=fake_load_prompt_file,
            prompt_name="planner_stage1_v2",
        )
    )

    assert set(cards) == set(_PROMPT_CARD_MANIFEST["planner_stage1_v2"])
    assert Path("prompts/cards/relationship_semantics_card.md") in calls
    assert Path("prompts/cards/semantic_disambiguation_card.md") in calls


def test_render_prompt_template_fails_when_placeholder_missing():
    with pytest.raises(RuntimeError, match="planner prompt placeholder missing"):
        _render_prompt_template("{collections_card} {relationship_semantics_card}", collections_card="ok")


def test_render_prompt_template_ignores_json_literal_braces():
    rendered = _render_prompt_template('{"sample": 1}\n{collections_card}', collections_card="card")

    assert rendered == '{"sample": 1}\ncard'


def test_stage2_system_prompt_keeps_runtime_inputs_literal():
    template = Path("prompts/planner_stage2_v2.md").read_text(encoding="utf-8")
    rendered = _render_prompt_template(
        template,
        **{key: key for key in _PROMPT_CARD_MANIFEST["planner_stage2_v2"]},
    )

    assert "<locked_strategy>{locked_strategy}</locked_strategy>" not in rendered
    assert "<surface_signals>{surface_signals}</surface_signals>" not in rendered
    assert "<entity_role_plan>{entity_role_plan}</entity_role_plan>" not in rendered
    assert "<validation_hints>{validation_hints}</validation_hints>" not in rendered
    assert "<previous_output>{previous_output}</previous_output>" not in rendered
    assert "- locked_strategy" in rendered
    assert "- user_query" in rendered


def test_broad_history_query_does_not_require_perf_types_when_policy_is_explicit_only():
    signals = SimpleNamespace(years=[], people_terms=["Kim"], org_terms=["KISTI"])
    entity_role_plan = PlannerEntityRolePlan(
        people_terms_to_keep=["Kim"],
        org_terms_to_keep=["KISTI"],
        perf_type_hints=["paper"],
        must_keep_terms=["Kim", "KISTI", "history"],
        semantic_kind="broad_history",
        perf_type_policy="explicit_only",
        confidence=0.9,
    )
    stage2_slots = SimpleNamespace(
        ids_map={},
        candidate_keys={},
        filters={
            "participant_researcher_name": ["Kim"],
            "people_affiliation_org_name": ["KISTI"],
        },
        retrieval_query="Kim KISTI researcher history",
    )
    locked_strategy = SimpleNamespace(
        mode="LOOKUP",
        head="people",
        action="list",
        relation=None,
        join_key_mode=None,
        prev_context_seed={},
    )

    result = validate_stage2_slots(
        question="Kim(KISTI) researcher history?",
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        stage2_slots=stage2_slots,
    )

    assert result.ok is True
    assert "missing_perf_types" not in result.errors


def test_collect_surface_signals_preserves_parenthesized_people_org_and_drops_internal_perf_tags():
    normalized_intent = SimpleNamespace(
        people_terms=[],
        org_terms=[],
        perf_types=["IRD_NAI_RI_TECH_INFO"],
        years=[],
        ids_map={},
    )

    signals = collect_surface_signals(
        "신동구(한국과학기술정보연구원) 연구자의 활동내역 20건만 뽑아줘",
        normalized_intent,
    )

    assert signals.people_terms == ["신동구"]
    assert signals.org_terms == ["한국과학기술정보연구원"]
    assert signals.perf_types == []


def test_broad_history_validation_ignores_count_and_generic_history_must_keep_terms():
    signals = SimpleNamespace(years=[], people_terms=["신동구"], org_terms=["한국과학기술정보연구원"])
    entity_role_plan = PlannerEntityRolePlan(
        people_terms_to_keep=["신동구"],
        org_terms_to_keep=["한국과학기술정보연구원"],
        perf_type_hints=[],
        must_keep_terms=["신동구", "한국과학기술정보연구원", "활동이력", "20건"],
        semantic_kind="broad_history",
        perf_type_policy="explicit_only",
        confidence=0.9,
    )
    stage2_slots = SimpleNamespace(
        ids_map={},
        candidate_keys={},
        filters={
            "participant_researcher_name": ["신동구"],
            "people_affiliation_org_name": ["한국과학기술정보연구원"],
        },
        retrieval_query="신동구 한국과학기술정보연구원 연구자 활동내역",
    )
    locked_strategy = SimpleNamespace(
        mode="LOOKUP",
        head="people",
        action="list",
        relation=None,
        join_key_mode=None,
        prev_context_seed={},
    )

    result = validate_stage2_slots(
        question="신동구(한국과학기술정보연구원) 연구자의 활동내역 20건만 뽑아줘",
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        stage2_slots=stage2_slots,
    )

    assert result.ok is True
    assert "missing_must_keep_terms" not in result.errors
