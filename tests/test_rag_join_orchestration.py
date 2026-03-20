from __future__ import annotations

from types import SimpleNamespace

from apps.core.rag_join_orchestration import (
    JoinOrchestrationRequest,
    JoinOrchestrationRuntime,
    PerfFollowupRequest,
    PerfFollowupRuntime,
    execute_join_orchestration,
    resolve_perf_followup_join_ids,
)
from apps.core.rag_types import RagResult
from apps.core.filters import validate_resolved_join_keys
import apps.core.rag_join_orchestration as join_mod


class _Point:
    def __init__(self, payload):
        self.payload = payload


def _noop(*args, **kwargs):
    return None


def test_resolve_perf_followup_join_ids_extracts_project_ids():
    point = _Point({"pjt_id": "1711015550", "tag": "IRD_NAI_PJT_INFO"})
    request = PerfFollowupRequest(
        relation=("project", "perf"),
        ids_map={},
        mode="lookup",
        target_cols=["ntis_project_v1", "ntis_perf_v1"],
        query_text="perf lookup",
        keywords=["perf"],
        year_range_filter=None,
        topk_dense=5,
        ctx_hard_limit=5,
        vector_names=["e5"],
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        action="detail",
        base_route="project",
        intent_item=SimpleNamespace(),
        lex_w_eff=0.0,
    )
    runtime = PerfFollowupRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_tag_filters=["IRD_NAI_PJT_INFO"]),
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"hybrid": [point], "dense": {}, "lexical": []},
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list(points),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        extract_join_keys_fn=lambda points, **kwargs: SimpleNamespace(keys=["1711015550"]),
    )

    result = resolve_perf_followup_join_ids(request=request, runtime=runtime)

    assert result == ["1711015550"]


def test_execute_join_orchestration_uses_seeded_instance_join_keys(monkeypatch):
    captured = {}

    def fake_assemble(**kwargs):
        captured.update(kwargs)
        return RagResult(
            stack="test",
            keywords=list(kwargs.get("keywords") or []),
            hits=list(kwargs.get("hits") or []),
            reranked_hits=list(kwargs.get("hop2_reranked") or []),
            context="join-context",
            refs=[],
            timings=dict(kwargs.get("timings") or {}),
            debug_meta=kwargs.get("debug_meta"),
        )

    monkeypatch.setattr(join_mod, "assemble_join_rag_result", fake_assemble)
    point = _Point({"doc_id": "perf-1", "pjt_id": "1711015550", "tag": "NTIS_RND_RSLT"})
    request = JoinOrchestrationRequest(
        relation=("project", "perf"),
        action="detail",
        mode="join",
        base_route="project",
        query_text="project perf lookup",
        keywords=["project", "perf"],
        output_type="detail",
        planner_limit=5,
        resolved_join_key_mode="instance",
        planner_raw_join_key_mode="instance",
        join_execution_policy={"policy_source": "execution_policy", "hop1_strategy": "skip", "reason": "instance_seed_pjt_id", "execution_policy_reason": "instance_seed_pjt_id", "seed_key_source": "ids_map.pjt_id", "seed_key_count": 1},
        compiled_strategy=SimpleNamespace(hop1_spec={}, hop2_spec={}),
        planner_filter_spec={},
        context_state=SimpleNamespace(people_terms=[]),
        people_terms=[],
        people_ids=[],
        org_terms=[],
        org_role=None,
        people_org_terms=[],
        gender_terms=[],
        people_min_should=0,
        people_match_mode="contains",
        people_promote_one_must=False,
        lookup_filter_policy="soft",
        join_hop1_lookup_filter_enabled=False,
        people_filter=None,
        participant_org_filter=None,
        org_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        year_range_filter=None,
        perf_type_filter=None,
        topk_dense=5,
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        ctx_hard_limit=5,
        vector_names=["e5"],
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        lex_w_eff=0.0,
        t_all0=0.0,
        stack="test",
        timings={},
        intent_item=SimpleNamespace(ids_map={"pjt_id": ["1711015550"]}),
        target_keep_hop2=3,
    )
    runtime = JoinOrchestrationRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        log_top_points=_noop,
        log_section=_noop,
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        point_summary_fn=lambda point: point.payload,
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"hybrid": [point], "dense": {}, "lexical": []},
        validate_lookup_join_hybrid_metrics_fn=_noop,
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list(points),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        build_people_filter_fn=lambda input_obj: None,
        people_filter_input_factory=lambda **kwargs: kwargs,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", list(pjt_ids), list(pjt_nos)),
        build_collection_join_filter_fn=lambda **kwargs: {"resolved_pjt_ids": list(kwargs.get("resolved_pjt_ids") or [])},
        join_filter_input_factory=lambda **kwargs: kwargs,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        serialize_filter_for_log_fn=lambda filter_obj: {"_meta": {}},
        diff_filter_spec_fn=lambda **kwargs: {"changed": {}, "planner_keys": []},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda points, **kwargs: SimpleNamespace(keys=["1711015550"], invalid_values=[], suspected_swap_count=0, to_log_dict=lambda: {}),
        resolve_group_pjt_ids_fn=lambda points, **kwargs: [],
        validate_resolved_join_keys_fn=_noop,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_col="ntis_project_v1", hop2_col="ntis_perf_v1", hop1_kind="project", hop2_kind="perf", hop1_tag_filters=["IRD_NAI_PJT_INFO"], hop2_tag_filters=["NTIS_RND_RSLT"], hop2_label="perf results"),
        context_builder=lambda *args, **kwargs: ("", [], None),
    )

    result = execute_join_orchestration(request=request, runtime=runtime)

    assert result.join_key_source == "ids_map"
    assert result.hop1_mode == "skip"
    assert result.join_compile_selection == "planner_contract"
    assert result.hop2_key_strategy == "pjt_id_in"
    assert result.resolved_runtime_key_kind == "pjt_id"
    assert result.join_keys_used_count == 1
    assert captured["join_pjt_ids"] == ["1711015550"]
    assert captured["hop2_label"] == "perf results"
    assert captured["debug_meta"]["join"]["policy_source"] == "execution_policy"
    assert captured["debug_meta"]["join"]["execution_policy_reason"] == "instance_seed_pjt_id"


def test_execute_join_orchestration_allows_group_runtime_with_resolved_pjt_id_fallback(monkeypatch):
    captured = {}

    def fake_assemble(**kwargs):
        captured.update(kwargs)
        return RagResult(
            stack="test",
            keywords=list(kwargs.get("keywords") or []),
            hits=list(kwargs.get("hits") or []),
            reranked_hits=list(kwargs.get("hop2_reranked") or []),
            context="join-context",
            refs=[],
            timings=dict(kwargs.get("timings") or {}),
            debug_meta=kwargs.get("debug_meta"),
        )

    monkeypatch.setattr(join_mod, "assemble_join_rag_result", fake_assemble)
    point = _Point({"doc_id": "perf-1", "pjt_id": "1711015550", "tag": "NTIS_RND_RSLT"})
    request = JoinOrchestrationRequest(
        relation=("project", "perf"),
        action="list",
        mode="join",
        base_route="project",
        query_text="group related outputs",
        keywords=["group"],
        output_type="relation",
        planner_limit=5,
        resolved_join_key_mode="group",
        planner_raw_join_key_mode="group",
        join_execution_policy={
            "policy_source": "execution_policy",
            "hop1_strategy": "lookup",
            "reason": "group_seed_pjt_no_expand",
            "execution_policy_reason": "group_seed_pjt_no_expand",
            "seed_key_source": "ids_map.pjt_no",
            "seed_key_count": 1,
            "group_resolve_project_ids": 1,
            "group_resolve_topk": 10,
            "group_resolve_keep": 5,
            "group_resolve_max_ids": 20,
        },
        compiled_strategy=SimpleNamespace(hop1_spec={}, hop2_spec={}),
        planner_filter_spec={},
        context_state=SimpleNamespace(people_terms=[]),
        people_terms=[],
        people_ids=[],
        org_terms=[],
        org_role=None,
        people_org_terms=[],
        gender_terms=[],
        people_min_should=0,
        people_match_mode="contains",
        people_promote_one_must=False,
        lookup_filter_policy="soft",
        join_hop1_lookup_filter_enabled=True,
        people_filter=None,
        participant_org_filter=None,
        org_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        year_range_filter=None,
        perf_type_filter=None,
        topk_dense=5,
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        ctx_hard_limit=5,
        vector_names=["e5"],
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        lex_w_eff=0.0,
        t_all0=0.0,
        stack="test",
        timings={},
        intent_item=SimpleNamespace(ids_map={}),
        target_keep_hop2=3,
    )
    runtime = JoinOrchestrationRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        log_top_points=_noop,
        log_section=_noop,
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        point_summary_fn=lambda point: point.payload,
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"hybrid": [point], "dense": {}, "lexical": []},
        validate_lookup_join_hybrid_metrics_fn=_noop,
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list(points),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        build_people_filter_fn=lambda input_obj: None,
        people_filter_input_factory=lambda **kwargs: kwargs,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", list(pjt_ids), list(pjt_nos)),
        build_collection_join_filter_fn=lambda **kwargs: {"resolved_pjt_ids": list(kwargs.get("resolved_pjt_ids") or []), "pjt_nos": list(kwargs.get("pjt_nos") or [])},
        join_filter_input_factory=lambda **kwargs: kwargs,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        serialize_filter_for_log_fn=lambda filter_obj: {"_meta": {"join_compile_selection": "group_perf_pjt_id_fallback"}},
        diff_filter_spec_fn=lambda **kwargs: {"changed": {}, "planner_keys": []},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda points, **kwargs: SimpleNamespace(keys=[], invalid_values=[], suspected_swap_count=0, to_log_dict=lambda: {}),
        resolve_group_pjt_ids_fn=lambda points, **kwargs: ["1711015550"],
        validate_resolved_join_keys_fn=validate_resolved_join_keys,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_col="ntis_project_v1", hop2_col="ntis_perf_v1", hop1_kind="project", hop2_kind="perf", hop1_tag_filters=["IRD_NAI_PJT_INFO"], hop2_tag_filters=["NTIS_RND_RSLT"], hop2_label="perf results"),
        context_builder=lambda *args, **kwargs: ("", [], None),
    )

    result = execute_join_orchestration(request=request, runtime=runtime)

    assert result.join_key_source == "hop1"
    assert result.hop1_mode == "lookup"
    assert result.join_compile_selection == "group_perf_pjt_id_fallback"
    assert result.hop2_key_strategy == "pjt_id_in"
    assert result.resolved_runtime_key_kind == "pjt_id"
    assert result.join_keys_used_count == 1
    assert captured["join_pjt_ids"] == ["1711015550"]
    assert captured["join_pjt_nos"] == []
    assert captured["debug_meta"]["join"]["policy_source"] == "execution_policy"
    assert captured["debug_meta"]["join"]["execution_policy_reason"] == "group_seed_pjt_no_expand"
    assert captured["debug_meta"]["join"]["resolved_runtime_key_kind"] == "pjt_id"
    assert captured["debug_meta"]["join"]["join_compile_selection"] == "group_perf_pjt_id_fallback"

def test_execute_join_orchestration_builds_reverse_trace_followup(monkeypatch):
    captured = {}

    def fake_assemble(**kwargs):
        captured.update(kwargs)
        return RagResult(
            stack="test",
            keywords=list(kwargs.get("keywords") or []),
            hits=list(kwargs.get("hits") or []),
            reranked_hits=list(kwargs.get("hop2_reranked") or []),
            context="join-context",
            refs=[],
            timings=dict(kwargs.get("timings") or {}),
            reverse_trace=kwargs.get("reverse_trace"),
            debug_meta=kwargs.get("debug_meta"),
        )

    monkeypatch.setattr(join_mod, "assemble_join_rag_result", fake_assemble)

    origin_perf = _Point({"doc_id": "paper-1", "pjt_id": "1711015550", "tag": "NTIS_RND_RSLT", "title": "Digital Twin Paper"})
    origin_project = _Point({"doc_id": "project-1", "pjt_id": "1711015550", "pjt_no": "PJT-2020-1234-5678", "tag": "IRD_NAI_PJT_INFO", "title": "Digital Twin Project"})
    followup_duplicate = _Point({"doc_id": "paper-1", "pjt_id": "1711015550", "tag": "NTIS_RND_RSLT", "title": "Digital Twin Paper"})
    followup_other = _Point({"doc_id": "paper-2", "pjt_id": "1711015550", "tag": "NTIS_RND_RSLT", "title": "Digital Twin Patent"})

    def fake_dense(**kwargs):
        collection = kwargs.get("collection")
        scope = kwargs.get("contract_scope")
        if collection == "ntis_perf_v1" and scope == "join_hop1":
            return {"hybrid": [origin_perf], "dense": {}, "lexical": []}
        if collection == "ntis_project_v1" and scope == "join_hop2":
            return {"hybrid": [origin_project], "dense": {}, "lexical": []}
        if collection == "ntis_perf_v1" and scope == "reverse_trace_hop3":
            return {"hybrid": [followup_duplicate, followup_other], "dense": {}, "lexical": []}
        return {"hybrid": [], "dense": {}, "lexical": []}

    request = JoinOrchestrationRequest(
        relation=("perf", "project"),
        action="list",
        mode="join",
        base_route="perf",
        query_text="digital twin origin project and other outputs",
        keywords=["digital", "twin"],
        output_type="relation",
        planner_limit=5,
        resolved_join_key_mode="instance",
        planner_raw_join_key_mode="instance",
        join_execution_policy={
            "policy_source": "execution_policy",
            "hop1_strategy": "search",
            "reason": "perf_anchor_reverse_trace",
            "execution_policy_reason": "perf_anchor_reverse_trace",
            "seed_key_source": None,
            "seed_key_count": 0,
        },
        compiled_strategy=SimpleNamespace(hop1_spec={}, hop2_spec={}),
        planner_filter_spec={},
        context_state=SimpleNamespace(people_terms=[]),
        people_terms=[],
        people_ids=[],
        org_terms=[],
        org_role=None,
        people_org_terms=[],
        gender_terms=[],
        people_min_should=0,
        people_match_mode="contains",
        people_promote_one_must=False,
        lookup_filter_policy="soft",
        join_hop1_lookup_filter_enabled=False,
        people_filter=None,
        participant_org_filter=None,
        org_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        year_range_filter=None,
        perf_type_filter=None,
        topk_dense=5,
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        ctx_hard_limit=6,
        vector_names=["e5"],
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        lex_w_eff=0.0,
        t_all0=0.0,
        stack="test",
        timings={},
        intent_item=SimpleNamespace(ids_map={}),
        target_keep_hop2=3,
        reverse_trace_followup=True,
        followup_relation_hint="origin_project_other_perf",
    )
    runtime = JoinOrchestrationRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        log_top_points=_noop,
        log_section=_noop,
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        point_summary_fn=lambda point: point.payload,
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=fake_dense,
        validate_lookup_join_hybrid_metrics_fn=_noop,
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list(points),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        build_people_filter_fn=lambda input_obj: None,
        people_filter_input_factory=lambda **kwargs: kwargs,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", list(pjt_ids), list(pjt_nos)),
        build_collection_join_filter_fn=lambda **kwargs: {"resolved_pjt_ids": list(kwargs.get("resolved_pjt_ids") or []), "join_ids": list(kwargs.get("join_ids") or [])},
        join_filter_input_factory=lambda **kwargs: kwargs,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        serialize_filter_for_log_fn=lambda filter_obj: {"_meta": {}},
        diff_filter_spec_fn=lambda **kwargs: {"changed": {}, "planner_keys": []},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda points, **kwargs: SimpleNamespace(keys=["1711015550"], invalid_values=[], suspected_swap_count=0, to_log_dict=lambda: {}),
        resolve_group_pjt_ids_fn=lambda points, **kwargs: [],
        validate_resolved_join_keys_fn=_noop,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_col="ntis_perf_v1", hop2_col="ntis_project_v1", hop1_kind="perf", hop2_kind="project", hop1_tag_filters=["NTIS_RND_RSLT"], hop2_tag_filters=["IRD_NAI_PJT_INFO"], hop2_label="origin projects"),
        context_builder=lambda *args, **kwargs: ("", [], None),
        payload_get_fn=lambda payload, key: payload.get(key.split(".")[-1]) if isinstance(payload, dict) else None,
    )

    result = execute_join_orchestration(request=request, runtime=runtime)

    assert result.result.reverse_trace is not None
    assert captured["reverse_trace"]["relation_chain"] == ["perf", "project", "perf"]
    assert len(captured["reverse_trace"]["followup_perf"]) == 1
    assert captured["reverse_trace"]["followup_perf"][0]["doc_id"] == "paper-2"
    assert captured["debug_meta"]["reverse_trace"]["enabled"] is True
    assert request.timings["info.origin_project_count"] == 1
    assert request.timings["info.followup_perf_count"] == 1



def test_execute_join_orchestration_deferred_empty_result_returns_no_result(monkeypatch):
    captured = {}

    def fake_assemble(**kwargs):
        captured.update(kwargs)
        return RagResult(
            stack="test",
            keywords=list(kwargs.get("keywords") or []),
            hits=list(kwargs.get("hits") or []),
            reranked_hits=list(kwargs.get("hop2_reranked") or []),
            context="join-context",
            refs=[],
            timings=dict(kwargs.get("timings") or {}),
            debug_meta=kwargs.get("debug_meta"),
        )

    monkeypatch.setattr(join_mod, "assemble_join_rag_result", fake_assemble)
    request = JoinOrchestrationRequest(
        relation=("project", "perf"),
        action="list",
        mode="join",
        base_route="project",
        query_text="과제번호 a4412354543 성과",
        keywords=["과제번호"],
        output_type="relation",
        planner_limit=5,
        resolved_join_key_mode="deferred",
        planner_raw_join_key_mode="deferred",
        join_execution_policy={
            "policy_source": "execution_policy",
            "hop1_strategy": "lookup",
            "reason": "ambiguous_project_key_discovery",
            "execution_policy_reason": "ambiguous_project_key_discovery",
            "seed_key_source": "candidate_keys.project_key",
            "seed_key_count": 0,
        },
        compiled_strategy=SimpleNamespace(hop1_spec={}, hop2_spec={}),
        planner_filter_spec={},
        context_state=SimpleNamespace(people_terms=[]),
        people_terms=[],
        people_ids=[],
        org_terms=[],
        org_role=None,
        people_org_terms=[],
        gender_terms=[],
        people_min_should=0,
        people_match_mode="contains",
        people_promote_one_must=False,
        lookup_filter_policy="soft",
        join_hop1_lookup_filter_enabled=True,
        people_filter=None,
        participant_org_filter=None,
        org_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        year_range_filter=None,
        perf_type_filter=None,
        topk_dense=5,
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        ctx_hard_limit=5,
        vector_names=["e5"],
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        lex_w_eff=0.0,
        t_all0=0.0,
        stack="test",
        timings={},
        intent_item=SimpleNamespace(
            ids_map={},
            candidate_keys={"project_key": [{"value": "a4412354543", "candidate_types": ["pjt_id", "pjt_no"]}]},
            project_key_policy="ambiguous_or",
            join_resolution_policy="auto_resolve",
        ),
        target_keep_hop2=3,
    )
    runtime = JoinOrchestrationRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        log_top_points=_noop,
        log_section=_noop,
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        point_summary_fn=lambda point: point.payload,
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=lambda **kwargs: {"hybrid": [], "dense": {}, "lexical": []},
        validate_lookup_join_hybrid_metrics_fn=_noop,
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list(points),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        build_people_filter_fn=lambda input_obj: None,
        people_filter_input_factory=lambda **kwargs: kwargs,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", list(pjt_ids), list(pjt_nos)),
        build_collection_join_filter_fn=lambda **kwargs: {"candidate_project_keys": list(kwargs.get("candidate_project_keys") or []), "project_key_policy": kwargs.get("project_key_policy")},
        join_filter_input_factory=lambda **kwargs: kwargs,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        serialize_filter_for_log_fn=lambda filter_obj: {"_meta": {}},
        diff_filter_spec_fn=lambda **kwargs: {"changed": {}, "planner_keys": []},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda points, **kwargs: SimpleNamespace(keys=[], invalid_values=[], suspected_swaps=[], suspected_swap_count=0, to_log_dict=lambda: {}),
        resolve_group_pjt_ids_fn=lambda points, **kwargs: [],
        validate_resolved_join_keys_fn=_noop,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_col="ntis_project_v1", hop2_col="ntis_perf_v1", hop1_kind="project", hop2_kind="perf", hop1_tag_filters=["IRD_NAI_PJT_INFO"], hop2_tag_filters=["NTIS_RND_RSLT"], hop2_label="perf results"),
        context_builder=lambda *args, **kwargs: ("", [], None),
    )

    result = execute_join_orchestration(request=request, runtime=runtime)

    assert result.join_compile_selection == "deferred_empty_result"
    assert captured["debug_meta"]["join"]["project_key_policy"] == "ambiguous_or"
    assert request.timings["info.failed_step"] == "join_deferred_empty_result"
    assert request.timings["info.candidate_project_key_count"] == 1



def test_execute_join_orchestration_deferred_dual_branch_merges_perf_hits(monkeypatch):
    captured = {}

    def fake_assemble(**kwargs):
        captured.update(kwargs)
        return RagResult(
            stack="test",
            keywords=list(kwargs.get("keywords") or []),
            hits=list(kwargs.get("hits") or []),
            reranked_hits=list(kwargs.get("hop2_reranked") or []),
            context="join-context",
            refs=[],
            timings=dict(kwargs.get("timings") or {}),
            debug_meta=kwargs.get("debug_meta"),
        )

    monkeypatch.setattr(join_mod, "assemble_join_rag_result", fake_assemble)
    project_a = _Point({"doc_id": "p1", "pjt_id": "A4412354543-1", "pjt_no": "A4412354543", "tag": "IRD_NAI_PJT_INFO"})
    project_b = _Point({"doc_id": "p2", "pjt_id": "A4412354543-2", "pjt_no": "A4412354543", "tag": "IRD_NAI_PJT_INFO"})
    perf_instance = _Point({"doc_id": "perf-1", "pjt_id": "A4412354543-1", "tag": "NTIS_RND_RSLT"})
    perf_group = _Point({"doc_id": "perf-2", "pjt_id": "A4412354543-2", "tag": "NTIS_RND_RSLT"})

    def fake_dense(**kwargs):
        if kwargs.get("collection") == "ntis_project_v1":
            return {"hybrid": [project_a, project_b], "dense": {}, "lexical": []}
        qf = kwargs.get("query_filter") or {}
        if isinstance(qf, dict) and qf.get("join_ids"):
            return {"hybrid": [perf_instance], "dense": {}, "lexical": []}
        return {"hybrid": [perf_group], "dense": {}, "lexical": []}

    request = JoinOrchestrationRequest(
        relation=("project", "perf"),
        action="list",
        mode="join",
        base_route="project",
        query_text="과제번호 a4412354543 성과",
        keywords=["과제번호"],
        output_type="relation",
        planner_limit=5,
        resolved_join_key_mode="deferred",
        planner_raw_join_key_mode="deferred",
        join_execution_policy={
            "policy_source": "execution_policy",
            "hop1_strategy": "lookup",
            "reason": "ambiguous_project_key_discovery",
            "execution_policy_reason": "ambiguous_project_key_discovery",
            "seed_key_source": "candidate_keys.project_key",
            "seed_key_count": 0,
        },
        compiled_strategy=SimpleNamespace(hop1_spec={}, hop2_spec={}),
        planner_filter_spec={},
        context_state=SimpleNamespace(people_terms=[]),
        people_terms=[],
        people_ids=[],
        org_terms=[],
        org_role=None,
        people_org_terms=[],
        gender_terms=[],
        people_min_should=0,
        people_match_mode="contains",
        people_promote_one_must=False,
        lookup_filter_policy="soft",
        join_hop1_lookup_filter_enabled=True,
        people_filter=None,
        participant_org_filter=None,
        org_filter=None,
        project_tag_filter=None,
        perf_tag_filter=None,
        year_range_filter=None,
        perf_type_filter=None,
        topk_dense=5,
        sparse_vector_name_eff=None,
        sparse_weight_eff=1.0,
        ctx_hard_limit=8,
        vector_names=["e5"],
        w_dense_map={"e5": 1.0},
        fallback_emb={"e5": [0.1]},
        lex_w_eff=0.0,
        t_all0=0.0,
        stack="test",
        timings={},
        intent_item=SimpleNamespace(
            ids_map={},
            candidate_keys={"project_key": [{"value": "A4412354543", "candidate_types": ["pjt_id", "pjt_no"]}]},
            project_key_policy="ambiguous_or",
            join_resolution_policy="dual_branch",
        ),
        target_keep_hop2=3,
    )
    runtime = JoinOrchestrationRuntime(
        qdr=object(),
        preset=SimpleNamespace(lexical_fields=[], tag_boost=0.0, tag_mismatch_penalty=0.0),
        log_kv=_noop,
        log_top_points=_noop,
        log_section=_noop,
        timing_put_fn=lambda timings, key, value: timings.__setitem__(key, value),
        point_summary_fn=lambda point: point.payload,
        named_vectors_in_collection_fn=lambda qdr, col: {"e5"},
        get_pre_vecs_fn=lambda q: {"e5": [0.2]},
        call_dense_retrieve_hybrid_multi_fn=fake_dense,
        validate_lookup_join_hybrid_metrics_fn=_noop,
        apply_dense_threshold_fn=_noop,
        ensure_collection_mark_fn=_noop,
        rank_source_factory=lambda **kwargs: kwargs,
        use_dense_score_weight_fn=lambda: False,
        dense_score_weight_fn=lambda points: 1.0,
        rrf_merge_fn=lambda sources, **kwargs: [],
        dedup_by_doc_id_fn=lambda points: list({getattr(p, "payload", {}).get("doc_id"): p for p in points}.values()),
        final_rerank_fn=lambda points, **kwargs: list(points),
        hydrate_points_fn=_noop,
        count_missing_join_keys_fn=lambda points, **kwargs: {"missing_pjt_any": 0, "missing_tag": 0, "invalid_pjt_id": 0, "invalid_pjt_no": 0, "suspected_swap": 0, "same_id_no": 0},
        debug_force_join_keys_enabled_fn=lambda: False,
        get_meta_fn=lambda payload: payload,
        pick_first_fn=lambda *args: next((x for x in args if x), ""),
        build_people_filter_fn=lambda input_obj: None,
        people_filter_input_factory=lambda **kwargs: kwargs,
        build_project_id_filter_fn=lambda pjt_ids, pjt_nos: ("project_id", list(pjt_ids), list(pjt_nos)),
        build_collection_join_filter_fn=lambda **kwargs: {"resolved_pjt_ids": list(kwargs.get("resolved_pjt_ids") or []), "join_ids": list(kwargs.get("join_ids") or []), "pjt_nos": list(kwargs.get("pjt_nos") or []), "candidate_project_keys": list(kwargs.get("candidate_project_keys") or []), "project_key_policy": kwargs.get("project_key_policy")},
        join_filter_input_factory=lambda **kwargs: kwargs,
        build_tag_only_filter_fn=lambda tags: ("tag", tuple(tags)),
        and_filter_fn=lambda a, b: (a, b),
        with_org_must_gate_fn=lambda base_filter, **kwargs: base_filter,
        serialize_filter_for_log_fn=lambda filter_obj: {"_meta": {}} if isinstance(filter_obj, dict) else {"_meta": {}},
        diff_filter_spec_fn=lambda **kwargs: {"changed": {}, "planner_keys": []},
        merge_log_fields_fn=lambda a, b: {**a, **b},
        extract_join_keys_fn=lambda points, mode="instance", **kwargs: SimpleNamespace(keys=[p.payload["pjt_no" if mode == "group" else "pjt_id"] for p in points], invalid_values=[], suspected_swaps=[], suspected_swap_count=0, to_log_dict=lambda: {}),
        resolve_group_pjt_ids_fn=lambda points, **kwargs: [],
        validate_resolved_join_keys_fn=validate_resolved_join_keys,
        get_relation_route_fn=lambda relation: SimpleNamespace(hop1_col="ntis_project_v1", hop2_col="ntis_perf_v1", hop1_kind="project", hop2_kind="perf", hop1_tag_filters=["IRD_NAI_PJT_INFO"], hop2_tag_filters=["NTIS_RND_RSLT"], hop2_label="perf results"),
        context_builder=lambda *args, **kwargs: ("", [], None),
    )

    result = execute_join_orchestration(request=request, runtime=runtime)

    assert result.join_compile_selection == "deferred_dual_branch"
    assert result.hop2_key_strategy == "dual_branch"
    assert result.resolved_runtime_key_kind == "mixed"
    assert captured["debug_meta"]["join"]["dual_branch_used"] == 1
    assert request.timings["info.dual_branch_used"] == 1
    assert len(captured["hop2_reranked"]) == 2
