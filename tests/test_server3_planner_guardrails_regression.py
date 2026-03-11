from __future__ import annotations

from pathlib import Path


def _server3_source() -> str:
    return Path("server3.py").read_text(encoding="utf-8")


def test_prompt_join_head_examples_use_relation_target_semantics() -> None:
    source = _server3_source()
    assert 'relation="project_perf"이면 head="perf"' in source
    assert 'relation="perf_project"이면 head="project"' in source
    assert '"mode":"JOIN","head":"perf","action":"list","relation":"project_perf"' in source


def test_join_and_lookup_seed_id_keys_are_split() -> None:
    source = _server3_source()
    for key in ("pjt_id", "pjt_no", "doi", "issn", "eissn", "pissn", "perf_id", "rst_id", "paper_id", "patent_reg_no", "patent_app_no"):
        assert f'"{key}"' in source
    for key in ("person_no", "org_id", "org_code", "biz_no"):
        assert f'"{key}"' in source
    assert "join_seed_id_keys" in source
    assert "lookup_seed_id_keys" in source


def test_mode_correction_uses_join_lookup_topic_conditions() -> None:
    source = _server3_source()
    assert 'if has_join_seed_id and relation_is_project_perf:' in source
    assert 'elif (action_norm in lookup_actions) and (not relation_is_project_perf):' in source
    assert 'elif (not has_join_seed_id) and action_norm == "topic":' in source


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


def test_stagewise_action_mode_mismatch_is_non_fatal_guardrail() -> None:
    source = _server3_source()
    assert '"planner_source": "stagewise"' in source
    assert 'RAG.STRATEGY.ACTION_MODE_MISMATCH_STAGEWISE' in source
    assert 'handling="non_fatal_keep_assembled_strategy"' in source
    assert 'if qa is not None and planner_source == "stagewise":' in source
