from __future__ import annotations

from apps.core.rag_execution_policy import resolve_join_execution_policy


def test_resolve_join_execution_policy_skips_hop1_for_instance_seed():
    policy = resolve_join_execution_policy(
        relation=("project", "perf"),
        mode="join",
        action="list",
        join_key_mode="instance",
        seed_join_pjt_ids=["1711015550"],
        seed_join_pjt_nos=[],
        has_people_org_gate=False,
    )

    assert policy["hop1_strategy"] == "skip"
    assert policy["policy_source"] == "execution_policy"
    assert policy["reason"] == "instance_seed_pjt_id"
    assert policy["execution_policy_reason"] == "instance_seed_pjt_id"
    assert policy["seed_key_source"] == "ids_map.pjt_id"
    assert policy["join_seed_presence"] == "pjt_id"
    assert policy["hop1_key_extraction_status"] == "preseeded"


def test_resolve_join_execution_policy_uses_lookup_for_group_seed_when_expand_enabled(monkeypatch):
    monkeypatch.setenv("RAG_JOIN_GROUP_RESOLVE_PROJECT_IDS", "1")
    policy = resolve_join_execution_policy(
        relation=("project", "perf"),
        mode="join",
        action="list",
        join_key_mode="group",
        seed_join_pjt_ids=[],
        seed_join_pjt_nos=["PJT-2020-1234-5678"],
        has_people_org_gate=False,
    )

    assert policy["hop1_strategy"] == "lookup"
    assert policy["policy_source"] == "execution_policy"
    assert policy["reason"] == "group_seed_pjt_no_expand"
    assert policy["execution_policy_reason"] == "group_seed_pjt_no_expand"
    assert policy["seed_key_source"] == "ids_map.pjt_no"
    assert policy["join_seed_presence"] == "pjt_no"
    assert policy["hop1_key_extraction_status"] == "lookup_required"


def test_resolve_join_execution_policy_prefers_people_org_lookup_without_seed():
    policy = resolve_join_execution_policy(
        relation=("project", "perf"),
        mode="join",
        action="list",
        join_key_mode=None,
        seed_join_pjt_ids=[],
        seed_join_pjt_nos=[],
        has_people_org_gate=True,
    )

    assert policy["hop1_strategy"] == "lookup"
    assert policy["policy_source"] == "execution_policy"
    assert policy["reason"] == "people_org_gate_lookup"
    assert policy["execution_policy_reason"] == "people_org_gate_lookup"
    assert policy["join_seed_presence"] == "none"
    assert policy["hop1_key_extraction_status"] == "lookup_required"


def test_resolve_join_execution_policy_returns_noop_for_non_join():
    policy = resolve_join_execution_policy(
        relation=None,
        mode="lookup",
        action="detail",
        join_key_mode=None,
    )

    assert policy["hop1_strategy"] is None
    assert policy["policy_source"] == "execution_policy"
    assert policy["execution_policy_reason"] is None
    assert policy["seed_key_count"] == 0
    assert policy["join_seed_presence"] == "none"
    assert policy["hop1_key_extraction_status"] == "not_applicable"
