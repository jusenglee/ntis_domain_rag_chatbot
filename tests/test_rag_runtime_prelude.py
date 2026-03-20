from __future__ import annotations

from pathlib import Path

from apps.core.rag_runtime_prelude import build_runtime_prelude_result


def test_build_runtime_prelude_result_preserves_fields():
    result = build_runtime_prelude_result(
        query_text="query",
        keywords=["alpha"],
        intent_item=object(),
        context_state=object(),
        plan=object(),
        strategy=object(),
        mode="lookup",
        action="detail",
        base_route="project",
        relation=None,
        target_collections=["ntis_project_v1"],
        planner_limit=3,
        hinted_limit=2,
        compiled_strategy=object(),
        planner_filter_spec={"a": 1},
        resolved_join_key_mode=None,
        planner_raw_join_key_mode=None,
        preset=object(),
        lex_w_eff={"title": 1.0},
        sparse_vector_name_eff="bm25",
        sparse_topk_eff=10,
        sparse_weight_eff=1.0,
        topk_spec={"top_k_dense": 5},
        rerank_spec={"final_keep": 10},
        topk_dense=5,
        topk_lex_cand=10,
        topk_lex=10,
        use_dense_threshold_policy=True,
        min_dense_score_policy=0.1,
        title_terms=["AI"],
        title_match_mode="contains",
        title_filter=None,
        title_filter_server_applied=False,
        lookup_title_filter_policy="soft",
        lookup_filter_policy="soft",
        search_filter_signal=True,
        search_filter_conf_ok=True,
        search_filter_enabled=True,
        lookup_filter_enabled=True,
        relation_lookup_enforce=False,
        join_hop1_lookup_filter_enabled=False,
        search_filter_server_policy="none",
        org_terms=["ETRI"],
        org_role="lead",
        people_terms=["Kim"],
        people_ids=["P1"],
        gender_terms=[],
        people_org_terms=[],
        people_min_should=1,
        people_match_mode="contains",
        people_promote_one_must=False,
        people_filter="people",
        participant_org_filter=None,
        org_filter="org",
        planner_org_filter_present=True,
        project_tag_filter="project_tag",
        perf_tag_filter=None,
        year_range_filter="year_range",
        perf_type_filter=None,
        resolved_anchors=object(),
        reverse_trace_followup=True,
        followup_relation_hint="origin_project_other_perf",
        pattern_kind="perf_mix_gap",
        bundle_kind="project_outputs",
        bundle_targets=["paper", "patent"],
        guidance_required=True,
    )

    assert result.query_text == "query"
    assert result.keywords == ["alpha"]
    assert result.target_collections == ["ntis_project_v1"]
    assert result.search_filter_signal is True
    assert result.search_filter_conf_ok is True
    assert result.lookup_filter_enabled is True
    assert result.org_terms == ["ETRI"]
    assert result.reverse_trace_followup is True
    assert result.followup_relation_hint == "origin_project_other_perf"
    assert result.pattern_kind == "perf_mix_gap"
    assert result.bundle_kind == "project_outputs"
    assert result.bundle_targets == ["paper", "patent"]
    assert result.guidance_required is True


def test_rag_pipeline_uses_runtime_prelude_contract():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "build_runtime_prelude(" in source
    assert "RuntimePreludeRequest(" in source
    assert "RuntimePreludeRuntime(" in source
    assert "def _extract_payload_normalized_intent" not in source
    assert "def _normalize_payload_intent" not in source
    assert "_ensure_iterable_list(" not in source


def test_runtime_prelude_source_has_no_join_key_defaulting_or_duplicate_validator():
    source = Path("apps/core/rag_runtime_prelude.py").read_text(encoding="utf-8")

    assert 'resolved_join_key_mode = "instance"' not in source
    assert 'def _validate_join_key_contract(' not in source
