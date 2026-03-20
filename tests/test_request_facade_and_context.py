from __future__ import annotations

import asyncio
from types import SimpleNamespace

from apps.api.services.context_build_policy import build_context_bundle
from apps.api.services.request_facade import RequestUnderstandingFacade
from apps.api.services.rag_result_assembly import (
    SearchLookupResultOrchestrator,
    ResultAssemblyPolicy,
    ResultAssemblyRequest,
    ResultAssemblyRuntime,
    assemble_join_rag_result,
    collect_filter_probe_docs,
    finalize_rag_result,
)


class Payload:
    def __init__(self, normalized_intent, intent_payload_version=None, question_analysis=None, strategy_meta=None):
        self.normalized_intent = normalized_intent
        self.intent_payload_version = intent_payload_version
        self.question_analysis = question_analysis
        self.strategy_meta = strategy_meta or {}


class Point:
    def __init__(self, payload):
        self.payload = payload


async def _run_facade(question: str, ids_map: dict[str, list[str]]):
    calls = []

    async def fake_run_question_analysis(**kwargs):
        calls.append(kwargs["question"])
        return SimpleNamespace(confidence=0.8)

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
        return ({"intent": intent, "planner_used": qa is not None}, qa is not None)

    facade = RequestUnderstandingFacade(
        cheap_precheck=lambda value: {"ids_map": ids_map},
        has_superlative_cue=lambda value: False,
        extract_years=lambda value: [],
        extract_perf_types=lambda value: [],
        extract_title_terms=lambda value: [],
        classify_query_intent=lambda value, kws, hint=None: {"raw": value, "hint": hint},
        normalize_intent=lambda raw_intent, **kwargs: {"normalized": raw_intent},
        run_question_analysis=fake_run_question_analysis,
        apply_question_analysis_v3=fake_apply_question_analysis_v3,
        log_event=lambda *args, **kwargs: None,
        intent_payload_cls=Payload,
        planner_stagewise_enabled=True,
        planner_stage1_prompt_version="v1",
        planner_stage2_prompt_version="v1",
    )
    payload, question_analysis = await facade.build_intent_payload(
        question=question,
        conversation_id="cid",
        chat_history=[],
        prev_context=[],
        canonical_evidence=[],
        request_id="rid",
    )
    return payload, question_analysis, calls


def test_request_understanding_facade_skips_planner_for_explicit_id():
    payload, question_analysis, calls = asyncio.run(
        _run_facade("1711015550 project detail", {"pjt_id": ["1711015550"]})
    )

    assert calls == []
    assert question_analysis is None
    assert payload.normalized_intent["planner_used"] is False
    assert payload.intent_payload_version == "v3"
    assert payload.strategy_meta["strategy_version"] == "v3"


def test_request_understanding_facade_explicit_hint_keeps_structural_signals_only():
    captured = {}

    async def fake_run_question_analysis(**kwargs):
        return None

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
        return ({"intent": intent, "planner_used": qa is not None}, qa is not None)

    facade = RequestUnderstandingFacade(
        cheap_precheck=lambda value: {"ids_map": {}},
        has_superlative_cue=lambda value: False,
        extract_years=lambda value: ["2024"],
        extract_perf_types=lambda value: [],
        extract_title_terms=lambda value: [],
        classify_query_intent=lambda value, kws, hint=None: captured.setdefault("hint", hint) or {"raw": value, "hint": hint},
        normalize_intent=lambda raw_intent, **kwargs: {"normalized": raw_intent},
        run_question_analysis=fake_run_question_analysis,
        apply_question_analysis_v3=fake_apply_question_analysis_v3,
        log_event=lambda *args, **kwargs: None,
        intent_payload_cls=Payload,
        planner_stagewise_enabled=True,
        planner_stage1_prompt_version="v1",
        planner_stage2_prompt_version="v1",
    )

    payload, question_analysis = asyncio.run(
        facade.build_intent_payload(
            question="신동구 참여 과제",
            conversation_id="cid",
            chat_history=[],
            prev_context=[],
            canonical_evidence=[],
            request_id="rid",
        )
    )

    assert question_analysis is None
    hint = captured["hint"]
    assert hint["years"] == ["2024"]
    assert "people_terms" not in hint
    assert "org_terms" not in hint
    assert "org_role" not in hint
    assert payload.normalized_intent["planner_used"] is False
    assert payload.intent_payload_version == "v3"
    assert payload.strategy_meta["strategy_version"] == "v3"


def test_build_context_bundle_emits_render_profile_and_canonical_evidence():
    bundle = build_context_bundle(
        [
            Point(
                {
                    "pjt_id": "1711015550",
                    "pjt_no": "PJT-2020-1234-5678",
                    "org_nm": "ETRI",
                    "title_text": "AI related project",
                    "summary": "project summary",
                    "prtcp_org": [{"org_nm": "KISTI"}],
                    "prtcp_mp": [{"hm_nm": "Kim", "blng_org_nm": "ETRI"}],
                }
            )
        ],
        min_ctx_items=1,
        preset_max_ctx_items=3,
        ctx_hard_limit=3,
        action="list",
        base_route="project",
        mode="lookup",
        output_type="list",
        query_text="AI related projects",
        people_terms=["Kim"],
        person_ids=None,
        org_terms=None,
        org_role=None,
    )

    assert bundle["render_profile"]["name"] == "list"
    assert bundle["render_profile"]["context_kind"] == "people"
    assert bundle["context"].startswith("질의: AI related projects")
    assert bundle["canonical_evidence"][0]["ids"]["pjt_id"] == "1711015550"
    assert bundle["canonical_evidence"][0]["ids"]["pjt_no"] == "PJT-2020-1234-5678"
    assert bundle["canonical_evidence"][0]["roles"]["lead_org_name"] == ["ETRI"]
    assert bundle["canonical_evidence"][0]["roles"]["participant_org_name"] == ["KISTI"]



def test_assemble_join_rag_result_emits_join_context_and_canonical_evidence():
    result = assemble_join_rag_result(
        context_builder=lambda points, **kwargs: (
            "Hop1 project context" if kwargs.get("base_route") == "project" else "Hop2 perf context",
            [{"base_route": kwargs.get("base_route")}],
            (),
        ),
        hop1_points=[Point({"title_text": "Hop1 item"})],
        hop1_kind="project",
        hop1_query_text="project hop",
        hop1_max_items=2,
        hop2_reranked=[
            Point(
                {
                    "pjt_id": "1711015550",
                    "pjt_no": "PJT-2020-1234-5678",
                    "org_nm": "ETRI",
                    "title_text": "AI paper",
                    "summary": "paper summary",
                    "tag": "IRD_NAI_RI_PAPER",
                }
            )
        ],
        hop2_label="perf",
        effective_join_mode="instance",
        join_pjt_ids=["1711015550"],
        join_pjt_nos=[],
        preset_max_ctx_items=3,
        ctx_hard_limit=3,
        action="list",
        hop2_kind="perf",
        output_type="relation",
        mode="join",
        query_text="related perf lookup",
        people_terms=None,
        person_ids=None,
        org_terms=None,
        org_role=None,
        timings={},
        t_all0=0.0,
        stack="M",
        keywords=["AI"],
        hits=[],
        timing_put=lambda key, value: None,
        log_kv=lambda *args, **kwargs: None,
    )

    assert "Hop1 project context" in result.context
    assert "PJT_ID" in result.context
    assert result.render_profile["name"] == "relation"
    assert result.render_profile["context_kind"] == "perf"
    assert result.canonical_evidence[0]["ids"]["pjt_id"] == "1711015550"
    assert result.canonical_evidence[0]["facts"]["title"] == "AI paper"



def test_search_lookup_orchestrator_returns_render_profile_and_canonical_evidence():
    log_events = []
    request = ResultAssemblyRequest(
        base_route="project",
        mode="lookup",
        action="detail",
        output_type="detail",
        query_text="1711015550 project detail",
        people_terms=None,
        people_ids=None,
        org_terms=None,
        org_role=None,
        stack="M",
        plan_mode="lookup",
        relation=None,
        keywords=["AI"],
        sources=[],
    )
    policy = ResultAssemblyPolicy(
        rerank_spec={"final_keep": 5},
        preset=SimpleNamespace(tag_boost=0.0, tag_mismatch_penalty=0.0, max_ctx_items=3, min_reranked=1),
        ctx_hard_limit=3,
        hinted_limit=0,
        title_match_mode="contains",
        title_match_mode_contains="contains",
        title_terms=[],
        lookup_title_filter_policy="soft",
    )
    runtime = ResultAssemblyRuntime(
        timings={},
        t_all0=0.0,
        qdr=None,
        intent_payload=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
        log_kv=lambda event, **kwargs: log_events.append((event, kwargs)),
        timing_put=lambda key, value: None,
        hit_key=lambda hit: (str(getattr(hit, "payload", {}).get("pjt_id") or ""), str(getattr(hit, "payload", {}).get("title_text") or "")),
        payload_get=lambda payload, key: payload.get(key.split(".")[-1]) if isinstance(payload, dict) and "[]." not in key else [],
        resolve_env_topn=lambda *args, **kwargs: 3,
        log_top_points=lambda *args, **kwargs: None,
        prepare_title_post_rerank_fn=lambda *args, **kwargs: {"title_soft_terms_for_rerank": [], "title_soft_boost": 0.0},
        final_rerank_fn=lambda merged_rrf, **kwargs: list(merged_rrf),
        dedup_by_doc_id_fn=lambda hits: list(hits),
        resolve_effective_min_reranked_fn=lambda **kwargs: (1, "none"),
        has_explicit_identifiers=lambda intent: True,
        enforce_reranked_contract_fn=lambda **kwargs: None,
        hydrate_reranked_payloads_fn=lambda **kwargs: None,
        coerce_int=lambda x, default: default,
        get_attr=lambda obj, name, default=None: getattr(obj, name, default),
        hydrate_points_payload=lambda *args, **kwargs: None,
        soft_title_contains=lambda *args, **kwargs: True,
        aggregation_builder=lambda **kwargs: None,
    )
    hits = [
        Point(
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        )
    ]

    result = SearchLookupResultOrchestrator(
        merged_rrf=hits,
        query_intent=SimpleNamespace(),
        keywords=["AI"],
        lex_w_eff=1.0,
        request=request,
        policy=policy,
        runtime=runtime,
    ).run()

    assert result.render_profile["name"] == "detail"
    assert result.canonical_evidence[0]["ids"]["pjt_id"] == "1711015550"
    assert result.context
    context_events = [payload for event, payload in log_events if event == "RAG.CONTEXT"]
    assert context_events
    assert context_events[0]["execution_mode"] == "lookup"
    assert context_events[0]["execution_base_route"] == "project"
    assert context_events[0]["strategy_source"] == "execution_request"


def test_finalize_rag_result_wrapper_keeps_search_lookup_contract():
    hits = [
        Point(
            {
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "org_nm": "ETRI",
                "title_text": "AI related project",
                "summary": "project summary",
            }
        )
    ]

    result = finalize_rag_result(
        merged_rrf=hits,
        query_intent=SimpleNamespace(),
        keywords=["AI"],
        lex_w_eff=1.0,
        base_route="project",
        mode="lookup",
        rerank_spec={"final_keep": 5},
        preset=SimpleNamespace(tag_boost=0.0, tag_mismatch_penalty=0.0, max_ctx_items=3, min_reranked=1),
        ctx_hard_limit=3,
        hinted_limit=0,
        timings={},
        qdr=None,
        intent_payload=None,
        people_terms=None,
        people_ids=None,
        query_text="1711015550 project detail",
        org_terms=None,
        org_role=None,
        sources=[],
        action="detail",
        output_type="detail",
        stack="M",
        plan_mode="lookup",
        relation=None,
        t_all0=0.0,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
        log_kv=lambda *args, **kwargs: None,
        timing_put=lambda key, value: None,
        hit_key=lambda hit: (str(getattr(hit, "payload", {}).get("pjt_id") or ""), str(getattr(hit, "payload", {}).get("title_text") or "")),
        payload_get=lambda payload, key: payload.get(key.split(".")[-1]) if isinstance(payload, dict) and "[]." not in key else [],
        resolve_env_topn=lambda *args, **kwargs: 3,
        log_top_points=lambda *args, **kwargs: None,
        prepare_title_post_rerank_fn=lambda *args, **kwargs: {"title_soft_terms_for_rerank": [], "title_soft_boost": 0.0},
        final_rerank_fn=lambda merged_rrf, **kwargs: list(merged_rrf),
        dedup_by_doc_id_fn=lambda hits: list(hits),
        resolve_effective_min_reranked_fn=lambda **kwargs: (1, "none"),
        has_explicit_identifiers=lambda intent: True,
        enforce_reranked_contract_fn=lambda **kwargs: None,
        hydrate_reranked_payloads_fn=lambda **kwargs: None,
        coerce_int=lambda x, default: default,
        get_attr=lambda obj, name, default=None: getattr(obj, name, default),
        hydrate_points_payload=lambda *args, **kwargs: None,
        soft_title_contains=lambda *args, **kwargs: True,
        title_match_mode="contains",
        title_match_mode_contains="contains",
        title_terms=[],
        lookup_title_filter_policy="soft",
        aggregation_builder=lambda **kwargs: None,
    )

    assert result.render_profile["name"] == "detail"
    assert result.canonical_evidence[0]["facts"]["title"] == "AI related project"


def test_search_lookup_orchestrator_passes_payload_get_to_aggregation_builder():
    seen = {}
    payload_get = lambda payload, key: payload.get(key)
    request = ResultAssemblyRequest(
        base_route="project",
        mode="lookup",
        action="detail",
        output_type="detail",
        query_text="1711015550 project detail",
        people_terms=None,
        people_ids=None,
        org_terms=None,
        org_role=None,
        stack="M",
        plan_mode="lookup",
        relation=None,
        keywords=["AI"],
        sources=[],
    )
    policy = ResultAssemblyPolicy(
        rerank_spec={"final_keep": 5},
        preset=SimpleNamespace(tag_boost=0.0, tag_mismatch_penalty=0.0, max_ctx_items=3, min_reranked=1),
        ctx_hard_limit=3,
        hinted_limit=0,
        title_match_mode="contains",
        title_match_mode_contains="contains",
        title_terms=[],
        lookup_title_filter_policy="soft",
    )
    runtime = ResultAssemblyRuntime(
        timings={},
        t_all0=0.0,
        qdr=None,
        intent_payload=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
        log_kv=lambda *args, **kwargs: None,
        timing_put=lambda key, value: None,
        hit_key=lambda hit: ("k", "v"),
        payload_get=payload_get,
        resolve_env_topn=lambda *args, **kwargs: 3,
        log_top_points=lambda *args, **kwargs: None,
        prepare_title_post_rerank_fn=lambda *args, **kwargs: {"title_soft_terms_for_rerank": [], "title_soft_boost": 0.0},
        final_rerank_fn=lambda merged_rrf, **kwargs: list(merged_rrf),
        dedup_by_doc_id_fn=lambda hits: list(hits),
        resolve_effective_min_reranked_fn=lambda **kwargs: (0, "none"),
        has_explicit_identifiers=lambda intent: True,
        enforce_reranked_contract_fn=lambda **kwargs: None,
        hydrate_reranked_payloads_fn=lambda **kwargs: None,
        coerce_int=lambda x, default: default,
        get_attr=lambda obj, name, default=None: getattr(obj, name, default),
        hydrate_points_payload=lambda *args, **kwargs: None,
        soft_title_contains=lambda *args, **kwargs: False,
        aggregation_builder=lambda **kwargs: seen.setdefault("kwargs", dict(kwargs)) or None,
    )

    SearchLookupResultOrchestrator(
        merged_rrf=[Point({"pjt_id": "1711015550", "title_text": "AI related project"})],
        query_intent=SimpleNamespace(),
        keywords=["AI"],
        lex_w_eff=1.0,
        request=request,
        policy=policy,
        runtime=runtime,
    ).run()

    assert seen["kwargs"]["payload_get_fn"] is payload_get


def test_collect_filter_probe_docs_confirms_match_from_raw_nested_members():
    result = collect_filter_probe_docs(
        [
            Point(
                {
                    "doc_id": "1711015550",
                    "title": "NTIS project",
                    "prtcp_mp": [
                        {"hm_nm": "김철수", "blng_org_nm": "KISTI", "hm_id": "1"},
                        {"hm_nm": "신동구", "blng_org_nm": "KISTI", "hm_id": "2"},
                    ],
                    "prtcp_mp_hm_nm": ["김철수 신동구"],
                }
            )
        ],
        payload_get=lambda payload, key: payload.get(key.split(".")[-1]),
        probe_terms=["신동구"],
        mode="lookup",
    )

    assert result is not None
    assert result["status"] == "confirmed_match"
    assert result["matched"] == 1
    assert result["probe_docs"][0]["raw_nested_available"] is True
    assert result["probe_docs"][0]["prtcp_mp_preview"][1]["hm_nm"] == "신동구"


def test_collect_filter_probe_docs_returns_unknown_without_raw_nested_payload():
    result = collect_filter_probe_docs(
        [
            Point(
                {
                    "doc_id": "1711015550",
                    "title": "NTIS project",
                    "prtcp_mp_hm_nm": ["김철수 신동구"],
                }
            )
        ],
        payload_get=lambda payload, key: payload.get(key.split(".")[-1]),
        probe_terms=["신동구"],
        mode="lookup",
    )

    assert result is not None
    assert result["status"] == "unknown"
    assert result["reason"] == "raw_nested_unavailable"
    assert result["matched"] == 0
    assert result["probe_docs"][0]["raw_nested_available"] is False


def test_search_lookup_orchestrator_skips_warning_when_probe_is_unknown():
    log_events = []
    request = ResultAssemblyRequest(
        base_route="project",
        mode="lookup",
        action="detail",
        output_type="detail",
        query_text="신동구 참여 과제",
        people_terms=["신동구"],
        people_ids=None,
        org_terms=None,
        org_role=None,
        stack="M",
        plan_mode="lookup",
        relation=None,
        keywords=["신동구"],
        sources=[],
    )
    policy = ResultAssemblyPolicy(
        rerank_spec={"final_keep": 5},
        preset=SimpleNamespace(tag_boost=0.0, tag_mismatch_penalty=0.0, max_ctx_items=3, min_reranked=1),
        ctx_hard_limit=3,
        hinted_limit=0,
        title_match_mode="contains",
        title_match_mode_contains="contains",
        title_terms=[],
        lookup_title_filter_policy="soft",
    )
    runtime = ResultAssemblyRuntime(
        timings={},
        t_all0=0.0,
        qdr=None,
        intent_payload=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
        log_kv=lambda event, **kwargs: log_events.append((event, kwargs)),
        timing_put=lambda key, value: None,
        hit_key=lambda hit: ("k", "v"),
        payload_get=lambda payload, key: payload.get(key.split(".")[-1]) if isinstance(payload, dict) and "[]." not in key else [],
        resolve_env_topn=lambda *args, **kwargs: 3,
        log_top_points=lambda *args, **kwargs: None,
        prepare_title_post_rerank_fn=lambda *args, **kwargs: {"title_soft_terms_for_rerank": [], "title_soft_boost": 0.0},
        final_rerank_fn=lambda merged_rrf, **kwargs: list(merged_rrf),
        dedup_by_doc_id_fn=lambda hits: list(hits),
        resolve_effective_min_reranked_fn=lambda **kwargs: (1, "none"),
        has_explicit_identifiers=lambda intent: False,
        enforce_reranked_contract_fn=lambda **kwargs: None,
        hydrate_reranked_payloads_fn=lambda **kwargs: None,
        coerce_int=lambda x, default: default,
        get_attr=lambda obj, name, default=None: getattr(obj, name, default),
        hydrate_points_payload=lambda *args, **kwargs: None,
        soft_title_contains=lambda *args, **kwargs: True,
        aggregation_builder=lambda **kwargs: None,
    )

    SearchLookupResultOrchestrator(
        merged_rrf=[Point({"pjt_id": "1711015550", "title_text": "AI related project", "prtcp_mp_hm_nm": ["김철수 신동구"]})],
        query_intent=SimpleNamespace(),
        keywords=["신동구"],
        lex_w_eff=1.0,
        request=request,
        policy=policy,
        runtime=runtime,
    ).run()

    assert "FILTER_MISS_SUSPECTED" not in [event for event, _ in log_events]
