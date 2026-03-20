from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.api.services.planner_service import apply_planner_strategy, normalize_hint_terms
from apps.core.pipeline_steps import NormalizedIntent


class DummyStrategyViolation(Exception):
    def __init__(self, *, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def _base_intent(**kwargs):
    payload = {
        "action": "topic",
        "base_route": "project",
        "relation": None,
        "is_id_query": False,
        "mode": "search",
        "output_type": "summary",
    }
    payload.update(kwargs)
    return NormalizedIntent(**payload)


def _changed_fields(before, after, fields, *, changed_by):
    changed = {}
    for field in fields:
        if before.get(field) == after.get(field):
            continue
        changed[field] = {
            "before": before.get(field),
            "after": after.get(field),
            "changed_by": changed_by,
        }
    return changed


def test_apply_planner_strategy_rejects_stagewise_action_mode_mismatch():
    events = []
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="topic",
        mode="lookup",
        relation=None,
        head="project",
        output_type="summary",
        join_key_mode=None,
        target_cols=[],
        ids_map={},
    )

    with pytest.raises(DummyStrategyViolation) as exc_info:
        apply_planner_strategy(
            _base_intent(),
            qa,
            request_id="rid",
            conversation_id="cid",
            normalize_hint_terms=normalize_hint_terms,
            log_event=lambda event, **fields: events.append((event, fields)),
            build_changed_fields=_changed_fields,
            changed_by_planner_merge="planner_merge",
            strategy_violation_cls=DummyStrategyViolation,
        )

    assert exc_info.value.error_code == "PLANNER_ACTION_MODE_MISMATCH"
    assert [event for event, _ in events] == ["RAG.STRATEGY.ACTION_MODE_MISMATCH"]
    assert events[0][1]["planner_source"] == "stagewise"


def test_apply_planner_strategy_rejects_join_without_required_fields():
    events = []
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="join",
        mode="join",
        relation="project_perf",
        head="perf",
        output_type="relation",
        join_key_mode=None,
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={},
    )

    with pytest.raises(DummyStrategyViolation) as exc_info:
        apply_planner_strategy(
            _base_intent(action="list", mode="lookup", output_type="list"),
            qa,
            request_id="rid",
            conversation_id="cid",
            normalize_hint_terms=normalize_hint_terms,
            log_event=lambda event, **fields: events.append((event, fields)),
            build_changed_fields=_changed_fields,
            changed_by_planner_merge="planner_merge",
            strategy_violation_cls=DummyStrategyViolation,
        )

    assert exc_info.value.error_code == "PLANNER_JOIN_FIELDS_MISSING"
    assert [event for event, _ in events] == ["RAG.STRATEGY.JOIN_FIELDS_MISSING"]
    assert events[0][1]["policy_mode"] == "strict"


def test_apply_planner_strategy_preserves_comparison_output_type():
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="stats",
        mode="lookup",
        relation=None,
        head="project",
        output_type="comparison",
        join_key_mode=None,
        target_cols=["ntis_project_v1"],
        ids_map={},
        candidate_keys={},
        project_key_policy=None,
        join_resolution_policy=None,
    )

    patched, applied = apply_planner_strategy(
        _base_intent(action="stats", mode="lookup", output_type="stats"),
        qa,
        request_id="rid",
        conversation_id="cid",
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda *args, **kwargs: None,
        build_changed_fields=_changed_fields,
        changed_by_planner_merge="planner_merge",
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.output_type == "comparison"


def test_apply_planner_strategy_preserves_series_output_type():
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="list",
        mode="lookup",
        relation=None,
        head="project",
        output_type="series",
        join_key_mode=None,
        target_cols=["ntis_project_v1"],
        ids_map={},
        candidate_keys={},
        project_key_policy=None,
        join_resolution_policy=None,
    )

    patched, applied = apply_planner_strategy(
        _base_intent(action="list", mode="lookup", output_type="list"),
        qa,
        request_id="rid",
        conversation_id="cid",
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda *args, **kwargs: None,
        build_changed_fields=_changed_fields,
        changed_by_planner_merge="planner_merge",
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.output_type == "series"


def test_planner_service_source_has_no_compat_strategy_fallback_tokens():
    source = open("apps/api/services/planner_service.py", encoding="utf-8").read()

    assert "RAG.STRATEGY.JOIN_FALLBACK" not in source
    assert "RAG.STRATEGY.ACTION_MODE_CORRECTED" not in source
    assert "RAG.STRATEGY.ACTION_MODE_MISMATCH_STAGEWISE" not in source
    assert "non_fatal_keep_assembled_strategy" not in source


def test_apply_planner_strategy_downgrades_seedless_instance_join_without_gate():
    events = []
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="join",
        mode="join",
        relation="project_perf",
        head="perf",
        output_type="relation",
        join_key_mode="instance",
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={},
        filters={},
    )

    patched, applied = apply_planner_strategy(
        _base_intent(action="list", mode="lookup", output_type="list", relation=None),
        qa,
        request_id="rid",
        conversation_id="cid",
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda event, **fields: events.append((event, fields)),
        build_changed_fields=_changed_fields,
        changed_by_planner_merge="planner_merge",
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.mode == "lookup"
    assert patched.relation == ("project", "perf")
    assert patched.join_key_mode is None
    downgrade_event = next(fields for event, fields in events if event == "PLANNER.ASSEMBLE.JOIN_RESHAPED")
    assert downgrade_event["reason"] == "seedless_instance_join_without_gate"


def test_apply_planner_strategy_downgrades_unresolved_researcher_org_join():
    events = []
    qa = SimpleNamespace(
        confidence=0.9,
        planner_source="stagewise",
        action="join",
        mode="join",
        relation="project_perf",
        head="perf",
        output_type="relation",
        join_key_mode="instance",
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        ids_map={},
        filters={
            "participant_researcher_name": ["홍길동"],
            "org_name": ["KISTI"],
        },
    )

    patched, applied = apply_planner_strategy(
        _base_intent(action="list", mode="lookup", output_type="list"),
        qa,
        request_id="rid",
        conversation_id="cid",
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda event, **fields: events.append((event, fields)),
        build_changed_fields=_changed_fields,
        changed_by_planner_merge="planner_merge",
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.mode == "lookup"
    downgrade_event = next(fields for event, fields in events if event == "PLANNER.ASSEMBLE.JOIN_RESHAPED")
    assert downgrade_event["reason"] == "seedless_instance_join_with_unresolved_anchor_pair"
    assert downgrade_event["unresolved_anchor_pair"] == 1


def test_merge_planner_hints_carries_bundle_metadata():
    from apps.api.services.planner_service import collect_researcher_name_terms, merge_planner_hints

    qa = SimpleNamespace(
        confidence=0.9,
        limit=20,
        retrieval_query="국가 R&D 관련 과제",
        filters={
            "participant_researcher_name": ["홍길동"],
            "bundle_targets": ["paper", "patent"],
            "bundle_mode": "project_outputs",
            "guidance_required": True,
        },
    )

    merged = merge_planner_hints(
        _base_intent(),
        qa,
        normalize_org_terms=lambda values: values or [],
        normalize_hint_terms=normalize_hint_terms,
        collect_researcher_name_terms=collect_researcher_name_terms,
    )

    assert merged.bundle_kind == "project_outputs"
    assert merged.bundle_targets == ["paper", "patent"]
    assert merged.guidance_required is True

