from __future__ import annotations

from pathlib import Path


def _server3_source() -> str:
    return Path("server3.py").read_text(encoding="utf-8")


def _planner_staged_source() -> str:
    return Path("rag_parts/planner_staged.py").read_text(encoding="utf-8")


def test_stagewise_composer_defines_join_relations_and_seed_keys() -> None:
    source = _planner_staged_source()
    for token in (
        'PROJECT_TO_PERF = "project_perf"',
        'PERF_TO_PROJECT = "perf_project"',
        '"pjt_id"',
        '"pjt_no"',
        '"doi"',
        '"rst_id"',
        '"patent_reg_no"',
    ):
        assert token in source


def test_stagewise_composer_uses_join_lookup_search_conditions() -> None:
    source = _planner_staged_source()
    assert 'if relation in (PROJECT_TO_PERF, PERF_TO_PROJECT) and (explicit_join_seed or has_prev_anchor):' in source
    assert 'elif action == "topic" and not explicit_join_seed:' in source
    assert 'mode = "LOOKUP"' in source


def test_role_hint_routing_clears_other_org_slots_when_single_role_hint() -> None:
    source = _server3_source()
    assert 'role_hint_count = int(has_lead_hint) + int(has_participant_hint) + int(has_affiliation_hint)' in source
    assert 'filters["participant_org_name"] = []' in source
    assert 'filters["people_affiliation_org_name"] = []' in source
    assert 'filters["lead_org_name"] = []' in source


def test_parser_search_to_lookup_rescue_is_disabled() -> None:
    source = _server3_source()
    assert 'SEARCH -> LOOKUP parser rescue is intentionally disabled.' in source


def test_stagewise_action_mode_mismatch_is_non_fatal_guardrail() -> None:
    source = _server3_source()
    assert '"planner_source": "stagewise"' in source
    assert 'RAG.STRATEGY.ACTION_MODE_MISMATCH_STAGEWISE' in source
    assert 'handling="non_fatal_keep_assembled_strategy"' in source
    assert 'if qa is not None and planner_source == "stagewise":' in source
