from pathlib import Path


def _read(rel: str) -> str:
    return Path(rel).read_text(encoding="utf-8")


def test_readme_links_new_onboarding_guides():
    text = _read("docs/README.md")
    assert "MODE_DECISION_GUIDE.md" in text
    assert "PLANNER_PARAMETER_REFERENCE.md" in text


def test_mode_decision_guide_covers_core_mode_inputs():
    text = _read("docs/MODE_DECISION_GUIDE.md")
    for token in [
        "SEARCH",
        "LOOKUP",
        "JOIN",
        "people/org name",
        "explicit id",
        "relation",
        "select_mode_policy()",
    ]:
        assert token in text


def test_parameter_reference_covers_core_planner_fields():
    text = _read("docs/PLANNER_PARAMETER_REFERENCE.md")
    for token in [
        "`mode`",
        "`action`",
        "`relation`",
        "`join_key_mode`",
        "`ids_map`",
        "`target_cols`",
        "`retrieval_query`",
        "`planner_limit`",
        "`output_type`",
        "`StrategySpec`",
        "`QueryPlan`",
    ]:
        assert token in text


def test_runbook_tracks_current_empty_result_baseline():
    text = _read("docs/RUNBOOK.md")
    assert "normal_no_result" in text
    assert "`lookup` and `join` with `reason=no_reranked`" in text
    assert "`search` with `reason=no_reranked` remains `strict_search`" in text


def test_runbook_lists_current_query_graph_values():
    text = _read("docs/RUNBOOK.md")
    for token in [
        "perf_to_project_to_perf",
        "pattern_analysis",
        "candidate_keys.project_key",
        "project_key_policy",
        "join_key_mode=deferred",
    ]:
        assert token in text


def test_contract_tracks_question_analysis_v3_fields():
    text = _read("docs/CONTRACT.md")
    for token in [
        "candidate_keys.project_key",
        "project_key_policy=ambiguous_or",
        "join_key_mode=deferred",
        "dual_branch_used",
        "IntentPayloadV3",
        'intent_payload_version="v3"',
    ]:
        assert token in text


def test_drift_checklist_tracks_v3_audit_targets():
    text = _read("docs/DRIFT_CHECKLIST_V3.md")
    for token in [
        "IntentPayloadV3 = transport version + semantic version dual promotion",
        "apps/core/query_intent.py",
        "apps/core/rag_pipeline.py",
        "docs/README.md",
        "docs/CONTRACT.md",
        "docs/RUNBOOK.md",
        "docs/GOLDEN_TESTS.md",
        "tests/test_doc_guides.py",
        "tests/test_text_integrity.py",
        "P0:",
    ]:
        assert token in text


def test_readme_tracks_extended_runtime_capabilities():
    text = _read("docs/README.md")
    for token in [
        "comparison",
        "series",
        "perf_to_project_to_perf",
        "anchor_resolution",
        "pattern_analysis",
        "multi_hop_bundle",
    ]:
        assert token in text


def test_contract_tracks_core_sections():
    text = _read("docs/CONTRACT.md")
    for token in [
        "Retrieval Intent Contract",
        "Strategy Assembly Contract",
        "Runtime Enforcement Contract",
        "QuestionAnalysis v3 / IntentPayloadV3",
    ]:
        assert token in text


def test_golden_tests_tracks_v3_cases():
    text = _read("docs/GOLDEN_TESTS.md")
    for token in [
        "Golden Queries",
        "QuestionAnalysis v3 Golden Cases",
        "IntentPayloadV3",
        'strategy_version="v3"',
    ]:
        assert token in text
