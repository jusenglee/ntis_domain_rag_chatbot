import logging
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routes import RouteDeps, register_routes
from apps.api.services.workflow_builder import WorkflowNodes, build_request_workflow


class FakeStateGraph:
    def __init__(self, agent_state_type):
        self.agent_state_type = agent_state_type
        self.nodes = {}
        self.edges = []
        self.conditional_edges = []
        self.entry_point = None

    def add_node(self, name, node):
        self.nodes[name] = node

    def set_entry_point(self, name):
        self.entry_point = name

    def add_edge(self, start, end):
        self.edges.append((start, end))

    def add_conditional_edges(self, start, route_fn, mapping):
        self.conditional_edges.append((start, route_fn, mapping))


def _noop(*args, **kwargs):
    return None


def test_register_routes_exposes_expected_paths():
    app = FastAPI()
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path('templates/index.html'),
            logger=logging.getLogger('test'),
            log_event=_noop,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ('', None),
            is_hit_source=lambda doc: False,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: 'strategy violation',
            collect_metrics_snapshot=lambda http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=_noop,
            rag_mapper=type('Mapper', (), {'get_references': staticmethod(lambda doc: doc)}),
            human_message=lambda content: {'content': content},
            strategy_violation=RuntimeError,
        ),
    )

    paths = {route.path for route in app.router.routes}
    assert {'/', '/query/stream', '/query/debug', '/health', '/health/details', '/metrics', '/metrics/stream'} <= paths


def test_build_request_workflow_keeps_expected_shape():
    nodes = WorkflowNodes(*(lambda state: state for _ in range(11)))
    workflow = build_request_workflow(
        agent_state_type=dict,
        state_graph_cls=FakeStateGraph,
        end='END',
        nodes=nodes,
    )

    assert workflow.entry_point == 'load_memory'
    assert 'analyze_question' in workflow.nodes
    assert ('merge_answers', 'save_history') in workflow.edges
    conditional_starts = {item[0] for item in workflow.conditional_edges}
    assert {'rule_precheck', 'judge_knowledge_sufficiency'} <= conditional_starts


class _HealthyKV:
    async def ping(self):
        return False


def test_health_is_ready_when_graph_exists_even_if_kv_is_disconnected():
    app = FastAPI()
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path('templates/index.html'),
            logger=logging.getLogger('test'),
            log_event=_noop,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ('', None),
            is_hit_source=lambda doc: False,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: 'strategy violation',
            collect_metrics_snapshot=lambda http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=_noop,
            rag_mapper=type('Mapper', (), {'get_references': staticmethod(lambda doc: doc)}),
            human_message=lambda content: {'content': content},
            strategy_violation=RuntimeError,
        ),
    )
    app.state.graph = object()
    app.state.kv_store = _HealthyKV()
    app.state.metrics_http = object()

    client = TestClient(app)
    response = client.get('/health')

    assert response.status_code == 200
    payload = response.json()
    assert payload['ready'] is True
    assert payload['status'] == 'degraded'
    assert payload['kv'] == 'disconnected'


class _DebugGraph:
    async def ainvoke(self, inputs):
        return {
            "answer_gemma": "gemma-answer",
            "answer_solar": "solar-answer",
            "messages": [SimpleNamespace(content="final-output")],
            "question_analysis": SimpleNamespace(
                model_dump=lambda: {
                    "mode": "join",
                    "action": "list",
                    "relation": ["project", "perf"],
                }
            ),
            "knowledge_sufficiency": SimpleNamespace(
                requires_new_knowledge=False,
                model_dump=lambda: {"requires_new_knowledge": False},
            ),
            "strategy": SimpleNamespace(
                mode="join",
                action="list",
                relation=("project", "perf"),
                join_key_mode="group",
                join_key_source="hop1",
                hop1_mode="lookup",
                join_compile_selection="group_perf_pjt_id_fallback",
                hop2_key_strategy="pjt_id_in",
                resolved_runtime_key_kind="pjt_id",
                join_keys_used_count=1,
                people_terms=(),
                target_collections=("ntis_project_v1", "ntis_perf_v1"),
                search_filter_enabled=False,
                lookup_filter_enabled=True,
                relation_lookup_enforce=False,
                lookup_filter_policy="soft",
                lookup_filter_min_should=None,
                lookup_filter_gate=None,
                lookup_filter_promote_one_must=False,
                lookup_title_filter_policy=None,
                title_match_mode=None,
                search_filter_server_policy=None,
            ),
            "context": [{"doc_id": "d1"}],
            "latencies": {"total": 1.0},
        }


def test_query_debug_exposes_strategy_summary():
    app = FastAPI()
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("templates/index.html"),
            logger=logging.getLogger("test"),
            log_event=_noop,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ("", None),
            is_hit_source=lambda doc: False,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: "strategy violation",
            collect_metrics_snapshot=lambda http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=_noop,
            rag_mapper=type("Mapper", (), {"get_references": staticmethod(lambda doc: doc)}),
            human_message=lambda content: {"content": content},
            strategy_violation=RuntimeError,
        ),
    )
    app.state.graph = _DebugGraph()
    app.state.kv_store = None

    client = TestClient(app)
    response = client.post("/query/debug", json={"question": "related outputs"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["strategy_summary"]["mode"] == "join"
    assert payload["strategy_summary"]["join_compile_selection"] == "group_perf_pjt_id_fallback"
    assert payload["strategy_summary"]["resolved_runtime_key_kind"] == "pjt_id"


class _StreamChunk:
    def __init__(self, text):
        self.content = text


class _StreamGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_start",
            "metadata": {"langgraph_node": "rag_search"},
            "data": {},
        }
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate_answer_solar"},
            "data": {"chunk": _StreamChunk("hello")},
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "context": [
                        {
                            "doc_id": "d1",
                            "tag": "IRD_NAI_PJT_INFO",
                            "title_text": "AI related project",
                            "meta_basic": {"kor_pjt_nm": "AI related project"},
                        }
                    ]
                }
            },
        }
        if False:
            yield {}


def _route_deps_for_stream():
    return RouteDeps(
        template_index_path=Path("templates/index.html"),
        logger=logging.getLogger("test"),
        log_event=_noop,
        is_debug_logging_enabled=lambda: False,
        mask_query_for_log=lambda value: value,
        extract_stream_chunk_text_and_field=lambda chunk: (getattr(chunk, "content", ""), "content"),
        is_hit_source=lambda doc: isinstance(doc, dict),
        derive_stream_error_code=lambda meta: None,
        compute_total_ms_from_start=lambda started_at: 0,
        extract_contract_failure_details=lambda reason: {},
        friendly_strategy_violation_message=lambda **kwargs: "strategy violation",
        collect_metrics_snapshot=lambda http: None,
        metrics_stream_interval_seconds=1.0,
        set_log_context=_noop,
        rag_mapper=type("Mapper", (), {"get_references": staticmethod(lambda doc: {"tag": doc.get("tag")})}),
        human_message=lambda content: {"content": content},
        strategy_violation=RuntimeError,
    )


def test_query_stream_includes_tag_in_all_payloads():
    app = FastAPI()
    register_routes(app, _route_deps_for_stream())
    app.state.graph = _StreamGraph()
    app.state.kv_store = None

    client = TestClient(app)
    response = client.post("/query/stream", json={"question": "hello"})

    assert response.status_code == 200
    payloads = []
    for part in response.text.split("\n\n"):
        part = part.strip()
        if not part.startswith("data: "):
            continue
        payloads.append(json.loads(part[len("data: "):]))

    assert payloads
    assert all("tag" in payload for payload in payloads)
    assert payloads[0]["tag"] == "conversation"
    assert any(payload.get("tag") == "status" and payload.get("status") == "retrieve" for payload in payloads)
    assert any(payload.get("tag") == "chunk" and payload.get("model") == "UPSTAGE" for payload in payloads)
    assert any(payload.get("tag") == "reference" for payload in payloads)
    reference_payload = next(payload for payload in payloads if payload.get("tag") == "reference")
    assert reference_payload["reference"][0]["tag"] == "IRD_NAI_PJT_INFO"
    assert reference_payload["reference"][0]["id"] == "d1"
    assert reference_payload["reference"][0]["title"] == "AI related project"
    assert payloads[-1]["tag"] == "status"
    assert payloads[-1]["status"] == "done"


def test_query_stream_runtime_not_ready_includes_tag():
    app = FastAPI()
    register_routes(app, _route_deps_for_stream())

    client = TestClient(app)
    response = client.post("/query/stream", json={"question": "hello"})

    assert response.status_code == 200
    payloads = []
    for part in response.text.split("\n\n"):
        part = part.strip()
        if not part.startswith("data: "):
            continue
        payloads.append(json.loads(part[len("data: "):]))

    assert payloads[0]["tag"] == "error"
    assert payloads[0]["error_code"] == "RUNTIME_NOT_READY"
    assert payloads[1]["tag"] == "status"
    assert payloads[1]["status"] == "done"


class _StreamStrategyViolation(RuntimeError):
    def __init__(self, *, error_code: str, reason: str):
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class _ErrorStreamGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "analyze_question"},
            "data": {
                "output": SimpleNamespace(mode="SEARCH", relation=None, action="topic")
            },
        }
        raise _StreamStrategyViolation(
            error_code="RAG_EMPTY_RESULT_CONTRACT",
            reason="reranked result violated contract(reason=no_reranked, reranked=0, min_reranked=4, empty_result_policy=strict_search)",
        )
        if False:
            yield {}


def test_query_stream_logs_strict_search_contract_failure_details_in_req_error():
    events = []
    app = FastAPI()
    deps = _route_deps_for_stream()
    deps = RouteDeps(**{**deps.__dict__, "log_event": lambda event, **fields: events.append((event, fields)), "strategy_violation": _StreamStrategyViolation, "extract_contract_failure_details": lambda reason: {"contract_fail_reason": "no_reranked", "empty_result_policy": "strict_search", "reranked_count": 0}})
    register_routes(app, deps)
    app.state.graph = _ErrorStreamGraph()
    app.state.kv_store = None

    client = TestClient(app)
    response = client.post("/query/stream", json={"question": "hello"})

    assert response.status_code == 200
    error_event = next(fields for event, fields in events if event == "REQ.ERROR")
    assert error_event["error_code"] == "RAG_EMPTY_RESULT_CONTRACT"
    assert error_event["contract_fail_reason"] == "no_reranked"
    assert error_event["empty_result_policy"] == "strict_search"
    assert error_event["reranked_count"] == 0
    assert error_event["mode"] == "SEARCH"
