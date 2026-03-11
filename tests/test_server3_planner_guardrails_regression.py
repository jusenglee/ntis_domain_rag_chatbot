from __future__ import annotations

from pathlib import Path


def _server3_source() -> str:
    return Path("server3.py").read_text(encoding="utf-8")


def test_prompt_join_head_examples_use_relation_target_semantics() -> None:
    source = _server3_source()
    assert 'relation="project_perf"이면 head="perf"' in source
    assert 'relation="perf_project"이면 head="project"' in source
    assert '"mode":"JOIN","head":"perf","action":"list","relation":"project_perf"' in source


def test_explicit_id_keys_include_people_org_and_issn_family() -> None:
    source = _server3_source()
    for key in ("issn", "eissn", "pissn", "person_no", "org_id", "org_code", "biz_no"):
        assert f'"{key}"' in source


def test_keyword_search_signal_does_not_treat_list_as_search() -> None:
    source = _server3_source()
    assert 'is_keyword_search = mode_norm == "SEARCH" or action_norm == "topic"' in source


def test_role_hint_routing_clears_other_org_slots_when_single_role_hint() -> None:
    source = _server3_source()
    assert 'role_hint_count = int(has_lead_hint) + int(has_participant_hint) + int(has_affiliation_hint)' in source
    assert 'filters["participant_org_name"] = []' in source
    assert 'filters["people_affiliation_org_name"] = []' in source
    assert 'filters["lead_org_name"] = []' in source


def test_deterministic_rescue_runs_before_auto_correction_gate() -> None:
    source = _server3_source()
    rescue_idx = source.index('if self.mode == "SEARCH":')
    gate_idx = source.index('if not ALLOW_PARSER_STRATEGY_AUTO_CORRECTION:')
    assert rescue_idx < gate_idx
