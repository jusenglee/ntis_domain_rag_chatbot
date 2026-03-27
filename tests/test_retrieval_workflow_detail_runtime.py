import asyncio
from types import SimpleNamespace

import apps.api.services.retrieval_workflow as retrieval_workflow
from apps.api.services.view_state import ConversationViewState, FocusEntity
from apps.core.pipeline_steps import NormalizedIntent


class DummyRetriever:
    calls = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        DummyRetriever.calls.append(kwargs)

    def retrieve(self, query):
        return {
            "documents": [
                {
                    "title": "Project Alpha Detail",
                    "pjt_id": "1415144250",
                    "pjt_no": "N0001058-1",
                    "org_nm": "Org Alpha",
                }
            ],
            "canonical_evidence": [
                {
                    "ids": {"pjt_id": "1415144250", "pjt_no": "N0001058-1"},
                    "facts": {"title": "Project Alpha Detail", "year": 2025},
                    "roles": {"lead_org_name": ["Org Alpha"], "participant_researcher_name": ["Kim"]},
                }
            ],
            "render_profile": {"context_kind": "project"},
        }


class DummyTool:
    def __init__(self, name, description, func):
        self.name = name
        self.description = description
        self.func = func


class Payload:
    def __init__(self, normalized_intent, strategy_meta=None):
        self.normalized_intent = normalized_intent
        self.strategy_meta = strategy_meta or {}


def test_node_rag_search_uses_anchor_locked_exact_lookup_for_detail_followup(monkeypatch):
    DummyRetriever.calls = []
    events = []
    view_state = ConversationViewState(
        latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="1415144250", title_text="Project Alpha Detail")
    )

    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="third project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"pjt_id": ["1415144250"]},
                project_key_policy="anchor_locked_pjt_id",
            ),
            strategy_meta={
                "explicit_followup": True,
                "followup_resolution_status": "resolved",
                "anchor_source": "display_snapshot",
            },
        ),
        view_state=view_state,
        request_id="rid",
        conversation_id="cid",
        question="?? ??? ????",
        messages=[SimpleNamespace(content="?? ??? ????")],
        request_overrides={},
    )

    result = asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "?? ??? ????",
                "generic detail query",
                "generic detail query",
                1.0,
                True,
                ["anchor_axis_lost"],
                True,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert result["answer_artifact"] is None
    assert "[detail_evidence]" in result["answer_context_text"]
    assert DummyRetriever.calls[0]["top_k"] == 1
    assert DummyRetriever.calls[0]["intent_payload"] is state.intent_payload
    assert DummyRetriever.calls[0]["intent_payload"].normalized_intent.project_key_policy == "anchor_locked_pjt_id"
    assert DummyRetriever.calls[0]["intent_payload"].normalized_intent.ids_map == {"pjt_id": ["1415144250"]}
    assert any(event == "RAG.DETAIL.ANCHOR.EXACT_LOOKUP" for event, _ in events)


def test_node_rag_search_detail_cache_hit_returns_evidence_context_not_direct_answer(monkeypatch):
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    coverage = SimpleNamespace(
        entity_found=True,
        detail_level="rich_detail",
        available_fields=["title", "pjt_id", "researchers"],
        missing_fields=[],
        core_profile={"entity_kind": "project", "title": "Project Alpha", "pjt_id": "1415144250", "researchers": ["Kim"]},
        rich_detail={},
    )
    view_state = ConversationViewState(
        latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="1415144250", title_text="Project Alpha")
    )
    view_state.detail_cache["project:pjt_id:1415144250"] = SimpleNamespace(
        anchor=view_state.latest_focus_entity,
        coverage=coverage,
        schema_version=retrieval_workflow.DETAIL_CACHE_SCHEMA_VERSION,
    )

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"pjt_id": ["1415144250"]},
                project_key_policy="anchor_locked_pjt_id",
            ),
            strategy_meta={"explicit_followup": True, "followup_resolution_status": "resolved", "anchor_source": "display_snapshot"},
        ),
        view_state=view_state,
        request_id="rid",
        conversation_id="cid",
        question="? ??? ?????",
        messages=[SimpleNamespace(content="? ??? ?????")],
        request_overrides={},
    )

    result = asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("? ??? ?????", "project detail", "project detail", 1.0, False, [], False),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert result["answer_artifact"] is None
    assert result["context"] == []
    assert result["canonical_evidence"] == []
    assert result["retrieval_bundle"].context_source == "detail_contract_context"
    assert "[detail_evidence]" in result["answer_context_text"]
    assert any(event == "DETAIL.CACHE.HIT" for event, _ in events)


def test_node_rag_search_ignores_stale_detail_cache_schema(monkeypatch):
    events = []
    coverage = SimpleNamespace(
        entity_found=True,
        detail_level="profile_only",
        available_fields=["title"],
        missing_fields=[],
        core_profile={"entity_kind": "project", "title": "Project Alpha", "pjt_id": "1415144250"},
        rich_detail={},
    )
    view_state = ConversationViewState(
        latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="1415144250", title_text="Project Alpha")
    )
    view_state.detail_cache["project:pjt_id:1415144250"] = SimpleNamespace(
        anchor=view_state.latest_focus_entity,
        coverage=coverage,
        schema_version=retrieval_workflow.DETAIL_CACHE_SCHEMA_VERSION - 1,
    )

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"pjt_id": ["1415144250"]},
                project_key_policy="anchor_locked_pjt_id",
            ),
            strategy_meta={"explicit_followup": True, "followup_resolution_status": "resolved", "anchor_source": "display_snapshot"},
        ),
        view_state=view_state,
        request_id="rid",
        conversation_id="cid",
        question="????",
        messages=[SimpleNamespace(content="????")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("????", "project detail", "project detail", 1.0, False, [], False),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert any(event == "DETAIL.CACHE.STALE_SCHEMA" for event, _ in events)


def test_node_rag_search_does_not_force_exact_lookup_from_stale_focus_alone(monkeypatch):
    DummyRetriever.calls = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="fresh project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={},
            ),
            strategy_meta={"explicit_followup": False, "followup_resolution_status": "none"},
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="STALE-ID", title_text="Stale Project")
        ),
        request_id="rid",
        conversation_id="cid",
        question="? ?? ????",
        messages=[SimpleNamespace(content="? ?? ????")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "? ?? ????",
                "fresh project detail",
                "fresh project detail",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert DummyRetriever.calls[0]["top_k"] == 5


def test_node_rag_search_fresh_detail_does_not_use_stale_cache(monkeypatch):
    DummyRetriever.calls = []
    events = []
    view_state = ConversationViewState(
        latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="STALE-ID", title_text="Stale Project")
    )
    view_state.detail_cache["project:pjt_id:STALE-ID"] = SimpleNamespace(
        coverage=SimpleNamespace(entity_found=True, detail_level="profile_only", available_fields=["title"]),
        schema_version=retrieval_workflow.DETAIL_CACHE_SCHEMA_VERSION - 1,
    )

    monkeypatch.setattr(retrieval_workflow, "coverage_satisfies_fields", lambda coverage, requested_fields: True)
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="fresh project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={},
            ),
            strategy_meta={"explicit_followup": False, "followup_resolution_status": "none", "anchor_source": None},
        ),
        view_state=view_state,
        request_id="rid",
        conversation_id="cid",
        question="? ?? ????",
        messages=[SimpleNamespace(content="? ?? ????")],
        request_overrides={},
    )

    result = asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "? ?? ????",
                "fresh project detail",
                "fresh project detail",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert all(event != "DETAIL.CACHE.HIT" for event, _ in events)
    assert DummyRetriever.calls[0]["top_k"] == 5


def test_node_rag_search_logs_seed_missing_for_followup_detail_without_ids_map(monkeypatch):
    DummyRetriever.calls = []
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="third project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={},
            ),
            strategy_meta={
                "explicit_followup": True,
                "followup_resolution_status": "resolved",
                "anchor_source": "display_snapshot",
            },
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="STALE-ID", title_text="Stale Project")
        ),
        request_id="rid",
        conversation_id="cid",
        question="? ?? ????",
        messages=[SimpleNamespace(content="? ?? ????")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "? ?? ????",
                "generic detail query",
                "generic detail query",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert DummyRetriever.calls[0]["top_k"] == 5
    assert all(event != "DETAIL.CACHE.HIT" for event, _ in events)
    assert any(event == "RAG.DETAIL.ANCHOR.SEED_MISSING" for event, _ in events)


def test_node_rag_search_prefers_rst_id_for_perf_detail_followup(monkeypatch):
    DummyRetriever.calls = []
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="that performance detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="perf", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="perf",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"rst_id": ["RST-900"], "pjt_id": ["PJT-900"]},
                context_owner_lock="perf",
                context_owner_lock_reason="followup_context_perf",
            ),
            strategy_meta={
                "explicit_followup": True,
                "followup_resolution_status": "resolved",
                "anchor_source": "display_snapshot",
            },
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="perf", source="display_snapshot", rst_id="RST-900", pjt_id="PJT-900", title_text="Perf Detail")
        ),
        request_id="rid",
        conversation_id="cid",
        question="? ?? ??",
        messages=[SimpleNamespace(content="? ?? ??")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "? ?? ??",
                "generic perf detail query",
                "generic perf detail query",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert DummyRetriever.calls[0]["top_k"] == 1
    assert any(event == "RAG.DETAIL.ANCHOR.EXACT_LOOKUP" and payload["selected_search_query"] == "RST-900" for event, payload in events)


def test_resolve_detail_entity_ref_reorders_strategy_meta_seed_for_perf():
    resolved = retrieval_workflow._resolve_detail_entity_ref(
        strategy_meta={
            "seed_map": {"pjt_id": ["PJT-900"], "rst_id": ["RST-900"]},
            "anchor_source": "display_snapshot",
            "followup_resolution_status": "resolved",
        },
        latest_focus_entity=None,
        ids_map={},
        preferred_entity_kind="perf",
    )

    assert isinstance(resolved, retrieval_workflow.ResolvedEntityRef)
    assert resolved.entity_kind == "perf"
    assert resolved.seed_map == {"rst_id": ["RST-900"]}


def test_node_rag_search_fresh_detail_does_not_emit_exact_lookup_event_from_stale_focus(monkeypatch):
    DummyRetriever.calls = []
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="fresh project detail"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={},
            ),
            strategy_meta={"explicit_followup": False, "followup_resolution_status": "none"},
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="STALE-ID", title_text="Stale Project")
        ),
        request_id="rid",
        conversation_id="cid",
        question="? ?? ??",
        messages=[SimpleNamespace(content="? ?? ??")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "? ?? ??",
                "fresh project detail",
                "fresh project detail",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert all(event != "RAG.DETAIL.ANCHOR.EXACT_LOOKUP" for event, _ in events)
    assert DummyRetriever.calls[0]["top_k"] == 5


def test_node_rag_search_list_snapshot_uses_retrieval_bundle_items(monkeypatch):
    captured = {}

    class ListDummyRetriever:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def retrieve(self, query):
            return {
                "documents": [{"title": "wrapper row", "source_type": "aggregation"}],
                "canonical_evidence": [
                    {
                        "ids": {"pjt_id": "PJT-1", "pjt_no": "NO-1"},
                        "facts": {"title": "first project"},
                        "roles": {"lead_org_name": ["Org Alpha"]},
                    },
                    {
                        "ids": {"pjt_id": "PJT-2", "pjt_no": "NO-2"},
                        "facts": {"title": "second project"},
                        "roles": {"lead_org_name": ["Org Beta"]},
                    },
                ],
                "render_profile": {"context_kind": "project"},
                "raw_result_count": 2,
            }

    def fake_build_display_snapshot(**kwargs):
        captured["kwargs"] = kwargs
        item_count = len(kwargs.get("items") or [])
        return SimpleNamespace(view_id="cid:rid:list", items=[], visible_count=item_count, raw_count=item_count)

    monkeypatch.setattr(retrieval_workflow, "build_display_snapshot", fake_build_display_snapshot)

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="list projects"),
        question_analysis=SimpleNamespace(output_type="list", base_route="project", limit=2, display_limit=2, action="list"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="list",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="list",
                ids_map={},
            ),
            strategy_meta={},
        ),
        view_state=ConversationViewState(),
        request_id="rid",
        conversation_id="cid",
        question="project list",
        messages=[SimpleNamespace(content="project list")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: (
                "project list",
                "project list",
                "project list",
                1.0,
                False,
                [],
                False,
            ),
            custom_rag_retriever_cls=ListDummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    snapshot_kwargs = captured["kwargs"]
    assert "items" in snapshot_kwargs
    assert "documents" not in snapshot_kwargs
    assert len(snapshot_kwargs["items"]) == 2



def test_node_rag_search_carries_pipeline_answer_context(monkeypatch):
    class ContextDummyRetriever:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def retrieve(self, query):
            return {
                "documents": [{"title": "doc"}],
                "canonical_evidence": [{"ids": {"pjt_id": "PJT-1"}, "facts": {"title": "doc"}}],
                "render_profile": {"context_kind": "project"},
                "answer_context_text": "assembled pipeline context",
            }

    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="detail"),
        question_analysis=SimpleNamespace(output_type="summary", base_route="project", limit=2, display_limit=2, action="list"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="list",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="summary",
                ids_map={},
            ),
            strategy_meta={},
        ),
        view_state=ConversationViewState(),
        request_id="rid",
        conversation_id="cid",
        question="question",
        messages=[SimpleNamespace(content="question")],
        request_overrides={},
    )

    result = asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("question", "question", "question", 1.0, False, [], False),
            custom_rag_retriever_cls=ContextDummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
        )
    )

    assert result["answer_context_text"] == "assembled pipeline context"
    assert result["retrieval_bundle"].answer_context_text == "assembled pipeline context"
    assert result["retrieval_bundle"].context_source == "pipeline_context"


def test_node_rag_search_stores_resolved_retrieval_query(monkeypatch):
    DummyRetriever.calls = []
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="planner query"),
        question_analysis=SimpleNamespace(output_type="summary", base_route="project", limit=2, display_limit=2, action="list"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="list",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="summary",
                ids_map={},
            ),
            strategy_meta={},
        ),
        view_state=ConversationViewState(),
        request_id="rid",
        conversation_id="cid",
        question="?? ??",
        messages=[SimpleNamespace(content="?? ??")],
        request_overrides={},
    )

    result = asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("?? ??", "planner query", "actual query", 1.0, False, [], False),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert DummyRetriever.calls
    assert result["resolved_retrieval_query"] == "actual query"
    assert any(event == "RAG.RETRIEVAL_QUERY.ACTUAL" and payload["actual_retrieval_query"] == "actual query" for event, payload in events)


def test_node_rag_search_logs_query_mismatch_when_retriever_reports_different_actual_query(monkeypatch):
    class MismatchDummyRetriever:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def retrieve(self, query):
            return {
                "documents": [{"title": "doc"}],
                "canonical_evidence": [],
                "render_profile": {"context_kind": "project"},
                "actual_retrieval_query": "rewritten by retriever",
            }

    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))
    events = []
    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="planner query"),
        question_analysis=SimpleNamespace(output_type="summary", base_route="project", limit=2, display_limit=2, action="list"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="list",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="summary",
                ids_map={},
            ),
            strategy_meta={},
        ),
        view_state=ConversationViewState(),
        request_id="rid",
        conversation_id="cid",
        question="?? ??",
        messages=[SimpleNamespace(content="?? ??")],
        request_overrides={},
    )

    asyncio.run(
        retrieval_workflow.node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("?? ??", "planner query", "actual query", 1.0, False, [], False),
            custom_rag_retriever_cls=MismatchDummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert any(event == "RAG.RETRIEVAL_QUERY.MISMATCH" for event, _ in events)
