import asyncio
from types import SimpleNamespace

from apps.api.app_factory import _state_log_summary_fields
import apps.api.services.request_facade as request_facade
from apps.api.services.request_facade import build_intent_payload
from apps.api.services.retrieval_workflow import _normalize_display_payloads, node_rag_search
from apps.api.services.view_state import ConversationViewState, FocusEntity
from apps.core.followup_resolution import resolve_reference_context_followup
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
                    "title": "Thin Project",
                    "pjt_id": "PJT-1",
                }
            ],
            "canonical_evidence": [
                {
                    "ids": {"pjt_id": "PJT-1", "pjt_no": "NO-1"},
                    "facts": {"title": "Thin Project", "year": 2025},
                    "roles": {
                        "lead_org_name": ["Org Alpha"],
                        "participant_org_name": ["Org Beta"],
                        "participant_researcher_name": ["Kim"],
                    },
                }
            ],
            "render_profile": {"context_kind": "project"},
        }


class DummyTool:
    def __init__(self, name, description, func):
        self.func = func


class Payload:
    def __init__(self, normalized_intent, strategy_meta=None):
        self.normalized_intent = normalized_intent
        self.strategy_meta = strategy_meta or {}


def _project_canonical_item(*, pjt_id: str, pjt_no: str, title: str) -> dict:
    return {
        "ids": {"pjt_id": pjt_id, "pjt_no": pjt_no},
        "facts": {"title": title},
        "roles": {"lead_org_name": ["Org Alpha"]},
    }


def test_build_intent_payload_coerces_project_anchor_org_followup_to_detail():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            head="project",
            action="list",
            output_type="list",
            filters={"participant_org_name": ["참여기관"]},
            limit=5,
            display_limit=3,
            retrieval_query="generic org list",
        )

    payload, qa = asyncio.run(
        build_intent_payload(
            question="그 과제의 참여기관은?",
            conversation_id="cid",
            chat_history=[],
            prev_context=[],
            canonical_evidence=[],
            view_state=ConversationViewState(
                latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="PJT-1", title_text="Thin Project")
            ),
            request_id="rid",
            cheap_precheck=lambda question: {"ids_map": {}, "years": [], "people_terms": [], "org_terms": [], "perf_tag_filters": [], "perf_types": [], "title_terms": []},
            has_superlative_cue=lambda question: False,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: SimpleNamespace(base_route="project", action="list", output_type="list", org_role="participant", org_terms=[], participant_org_terms=["참여기관"], ids_map={}, candidate_keys={}, project_key_policy=None, join_resolution_policy=None, join_key_mode=None, is_exact_key_query=False),
            normalize_intent=lambda raw_intent, **kwargs: raw_intent,
            run_question_analysis=fake_run_question_analysis,
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, True),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version="v1",
            planner_stage2_prompt_version="v1",
        )
    )

    assert payload.normalized_intent.action == "detail"
    assert payload.normalized_intent.output_type == "detail"
    assert payload.normalized_intent.ids_map == {"pjt_id": ["PJT-1"]}
    assert qa.action == "detail"
    assert qa.output_type == "detail"
    assert qa.limit == 1
    assert qa.display_limit == 1


def test_resolve_reference_context_followup_allows_org_deictic_over_project_carrier():
    resolution = resolve_reference_context_followup(
        question="그 기관은?",
        canonical_evidence=[
            {
                "ids": {"pjt_id": "PJT-1", "pjt_no": "NO-1"},
                "facts": {"title": "Thin Project"},
                "roles": {"lead_org_name": ["Org Alpha"], "participant_org_name": ["Org Beta"]},
                "source_type": "project",
            }
        ],
        prev_context=[],
        default_context_kind="org",
    )

    assert resolution["followup_resolution_status"] == "resolved"
    assert resolution["seed_map"] == {"pjt_id": ["PJT-1"]}


def test_normalize_display_payloads_promotes_canonical_axis_for_wrapper_docs():
    log_calls = []
    bundle = _normalize_display_payloads(
        docs=[{"title": "wrapper row", "source_type": "aggregation"}],
        canonical_evidence=[
            _project_canonical_item(pjt_id="PJT-1", pjt_no="NO-1", title="first project"),
            _project_canonical_item(pjt_id="PJT-2", pjt_no="NO-2", title="second project"),
            _project_canonical_item(pjt_id="PJT-3", pjt_no="NO-3", title="third project"),
        ],
        base_route="project",
        output_type="list",
        requested_count=3,
        explicit_count=3,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        request_id="rid",
        conversation_id="cid",
    )

    assert bundle.display_source == "canonical_axis"
    assert len(bundle.snapshot_documents) == 3
    assert all(event != "RAG.DISPLAY_INPUT_MISMATCH" for event, _ in log_calls)
    assert any(event == "RAG.DISPLAY_CANONICAL_AXIS.PROMOTED" for event, _ in log_calls)


def test_node_rag_search_exact_lookup_uses_seed_query_and_hydrates_core_profile(monkeypatch):
    import apps.api.services.retrieval_workflow as retrieval_workflow

    DummyRetriever.calls = []
    events = []
    monkeypatch.setattr(retrieval_workflow, "DetailCacheEntry", lambda **kwargs: SimpleNamespace(**kwargs))

    state = SimpleNamespace(
        knowledge_sufficiency=SimpleNamespace(retrieval_query="that project participant org"),
        question_analysis=SimpleNamespace(output_type="detail", base_route="project", limit=5, display_limit=1, action="detail"),
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="detail",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="detail",
                ids_map={"pjt_id": ["PJT-1"]},
                project_key_policy="anchor_locked_pjt_id",
            ),
            strategy_meta={"explicit_followup": True, "followup_resolution_status": "resolved", "anchor_source": "display_snapshot"},
        ),
        view_state=ConversationViewState(
            latest_focus_entity=FocusEntity(kind="project", source="detail_lookup", pjt_id="PJT-1", title_text="Thin Project")
        ),
        request_id="rid",
        conversation_id="cid",
        question="그 과제의 참여기관은?",
        messages=[SimpleNamespace(content="그 과제의 참여기관은?")],
        request_overrides={},
    )

    result = asyncio.run(
        node_rag_search(
            state,
            resolve_rag_queries_fn=lambda **kwargs: ("그 과제의 참여기관은?", "generic detail query", "generic detail query", 1.0, False, [], False),
            custom_rag_retriever_cls=DummyRetriever,
            tool_cls=DummyTool,
            max_top_k_size=10,
            strategy_violation_cls=RuntimeError,
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda event, **payload: events.append((event, payload)),
        )
    )

    assert any(event == "RAG.DETAIL.ANCHOR.EXACT_LOOKUP" and payload["selected_search_query"] == "PJT-1" for event, payload in events)
    assert result["answer_artifact"] is None
    assert result["answer_context_text"].startswith("# 출처 1. Thin Project")
    assert "연도: 2025" in result["answer_context_text"]
    assert "수행기관: Org Alpha" in result["answer_context_text"]
    assert "참여기관: Org Beta" in result["answer_context_text"]
    assert "[detail_evidence]" in result["debug_answer_context_text"]


def test_build_intent_payload_keeps_planner_locked_perf_route_without_post_merge_restore(monkeypatch):
    log_calls = []
    monkeypatch.setattr(
        request_facade,
        "resolve_followup_anchor",
        lambda **kwargs: FocusEntity(kind="perf", source="display_snapshot", display_rank=7, rst_id="REP-2014-0118236988", pjt_id="1415128833", title_text="?? 7"),
    )

    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            head="perf",
            action="detail",
            output_type="detail",
            limit=1,
            display_limit=1,
            retrieval_query="7?? ?? ??",
        )

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
        patched = SimpleNamespace(**vars(intent))
        patched.base_route = "perf"
        patched.target_cols = ["ntis_perf_v1"]
        return patched, True

    payload, _ = asyncio.run(
        build_intent_payload(
            question="?? ?? ? 7?? ??? ??????",
            conversation_id="cid",
            chat_history=[],
            prev_context=[],
            canonical_evidence=[],
            view_state=ConversationViewState(
                latest_focus_entity=FocusEntity(kind="perf", source="display_snapshot", rst_id="REP-2014-0118236988", pjt_id="1415128833", title_text="?? 7")
            ),
            request_id="rid",
            cheap_precheck=lambda question: {"ids_map": {}, "years": [], "people_terms": [], "org_terms": [], "perf_tag_filters": [], "perf_types": [], "title_terms": []},
            has_superlative_cue=lambda question: False,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: SimpleNamespace(base_route="project", action="detail", output_type="detail", ids_map={}, candidate_keys={}, project_key_policy=None, join_resolution_policy=None, join_key_mode=None, is_exact_key_query=False, context_owner_lock=None, context_owner_lock_reason=None),
            normalize_intent=lambda raw_intent, **kwargs: raw_intent,
            run_question_analysis=fake_run_question_analysis,
            apply_question_analysis_v3=fake_apply_question_analysis_v3,
            log_event=lambda event, **kwargs: log_calls.append((event, kwargs)),
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version="v1",
            planner_stage2_prompt_version="v1",
        )
    )

    assert payload.normalized_intent.base_route == "perf"
    assert payload.normalized_intent.target_cols == ["ntis_perf_v1"]
    assert payload.normalized_intent.ids_map["rst_id"] == ["REP-2014-0118236988"]
    restore_events = [fields for event, fields in log_calls if event == "FOLLOWUP.CONTEXT.RESTORED"]
    assert restore_events == []
    assert payload.normalized_intent.context_owner_lock == "perf"
    assert payload.normalized_intent.context_owner_lock_reason == "followup_context_perf"



def test_state_log_summary_fields_uses_final_enforced_route_truth_for_followup_perf():
    normalized_intent = NormalizedIntent(
        action="detail",
        base_route="perf",
        relation=None,
        is_id_query=False,
        output_type="detail",
        target_cols=["ntis_perf_v1"],
        ids_map={"rst_id": ["REP-2014-0118236988"], "pjt_id": ["1415128833"]},
        context_owner_lock="perf",
        context_owner_lock_reason="followup_context_perf",
    )
    state = SimpleNamespace(
        request_id="rid",
        conversation_id="cid",
        question_analysis=SimpleNamespace(
            mode="lookup",
            head="project",
            base_route="project",
            relation=None,
            join_key_mode=None,
            target_cols=["ntis_project_v1"],
            strategy_version="v3",
        ),
        strategy=SimpleNamespace(
            mode="lookup",
            relation=None,
            join_key_mode=None,
            target_collections=("ntis_perf_v1",),
        ),
        intent_payload=Payload(
            normalized_intent=normalized_intent,
            strategy_meta={
                "followup_resolution_status": "resolved",
                "selected_prev_context_kind": "perf",
            },
        ),
        context=[],
        merge_debug={},
        timings={},
    )

    summary = _state_log_summary_fields(state)

    assert summary["strategy_source"] == "execution_strategy"
    assert summary["base_route"] == "perf"
    assert summary["planner_base_route"] == "project"
    assert summary["target_cols"] == ["ntis_perf_v1"]
    assert summary["planner_target_cols"] == ["ntis_project_v1"]
    assert summary["selected_prev_context_kind"] == "perf"
    assert summary["followup_resolution_status"] == "resolved"


def test_state_log_summary_fields_uses_normalized_intent_target_cols_without_strategy():
    normalized_intent = NormalizedIntent(
        action="detail",
        base_route="perf",
        relation=None,
        is_id_query=False,
        output_type="detail",
        target_cols=["ntis_perf_v1"],
        ids_map={"rst_id": ["RST-1"]},
    )
    state = SimpleNamespace(
        request_id="rid",
        conversation_id="cid",
        question_analysis=SimpleNamespace(
            mode="lookup",
            head="project",
            base_route="project",
            relation=None,
            join_key_mode=None,
            target_cols=["ntis_project_v1"],
            strategy_version="v3",
        ),
        strategy=None,
        intent_payload=Payload(normalized_intent=normalized_intent, strategy_meta={}),
        context=[],
        merge_debug={},
        timings={},
    )

    summary = _state_log_summary_fields(state)

    assert summary["strategy_source"] == "assembled_question_analysis_only"
    assert summary["base_route"] == "perf"
    assert summary["target_cols"] == ["ntis_perf_v1"]
    assert summary["planner_target_cols"] == ["ntis_project_v1"]

def test_state_log_summary_fields_include_query_provenance():
    state = SimpleNamespace(
        request_id="rid",
        conversation_id="cid",
        question="원본 질문",
        resolved_retrieval_query="planner selected",
        actual_retrieval_query="retriever mutated",
        question_analysis=SimpleNamespace(mode="SEARCH", relation=None, head="project"),
        strategy=None,
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action="list",
                base_route="project",
                relation=None,
                is_id_query=False,
                output_type="list",
                target_cols=["ntis_project_v1"],
            ),
            strategy_meta={},
        ),
        context=[],
        merge_debug={},
        timings={},
    )

    summary = _state_log_summary_fields(state)

    assert summary["raw_query"] == "원본 질문"
    assert summary["resolved_retrieval_query"] == "planner selected"
    assert summary["actual_retrieval_query"] == "retriever mutated"
    assert summary["retrieval_query_mismatch"] == 1
