import os
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routes import RouteDeps, register_routes


class FakeGraph:
    def __init__(self, docs):
        self._docs = docs

    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {"output": {"context": self._docs}},
        }


class FakeRagSearchGraph:
    def __init__(self, docs):
        self._docs = docs

    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "rag_search"},
            "data": {"output": {"context": self._docs}},
        }


class FakeRagMapper:
    def get_references(self, item):
        return {}


def _build_test_client(*, docs, graph_cls=FakeGraph):
    app = FastAPI()
    app.state.graph = graph_cls(docs)
    app.state.kv_store = None
    app.state.metrics_http = None
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("index.html"),
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ("", "content"),
            is_hit_source=lambda doc: True,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: "strategy_violation",
            collect_metrics_snapshot=lambda metrics_http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=lambda **kwargs: None,
            rag_mapper=FakeRagMapper(),
            human_message=lambda content: {"role": "user", "content": content},
            strategy_violation=RuntimeError,
        ),
    )
    return TestClient(app)


def _read_event_payload(response_text, tag):
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if payload.get("tag") == tag:
            return payload
    raise AssertionError(f"{tag} event not found")


def _read_reference_event(response_text):
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if "reference" in payload:
            return payload["reference"]
    raise AssertionError("legacy reference payload not found")


def _read_legacy_reference_payload(response_text):
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if "reference" in payload:
            return payload
    raise AssertionError("legacy reference payload not found")


def test_reference_event_contains_project_reference_with_pjt_id():
    client = _build_test_client(
        docs=[
            {
                "tag": "IRD_NAI_PJT_INFO",
                "pjt_id": "PJT-123",
                "pjt_no": "NO-999",
                "doc_id": "DOC-1",
                "title_text": "project title",
            }
        ]
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert _read_reference_event(response.text) == [
        {
            "tag": "IRD_NAI_PJT_INFO",
            "id": "PJT-123",
            "title": "project title",
        }
    ]


def test_reference_event_contains_perf_reference_with_rst_id():
    client = _build_test_client(
        docs=[
            {
                "tag": "IRD_NAI_RI_PAPER",
                "rst_id": "RST-321",
                "pjt_id": "PJT-should-not-win",
                "doc_id": "DOC-2",
                "title_text": "paper title",
            }
        ]
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert _read_reference_event(response.text) == [
        {
            "tag": "IRD_NAI_RI_PAPER",
            "id": "RST-321",
            "title": "paper title",
        }
    ]


def test_reference_event_keeps_items_even_when_id_is_missing():
    client = _build_test_client(
        docs=[
            {
                "tag": "IRD_NAI_PJT_INFO",
                "pjt_no": "NO-ONLY",
                "doc_id": "DOC-ONLY",
                "title_text": "project title",
            },
            {
                "tag": "QNA",
                "doc_id": "DOC-QNA",
                "title_text": "qna title",
            },
        ]
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert _read_reference_event(response.text) == [
        {
            "tag": "IRD_NAI_PJT_INFO",
            "id": None,
            "title": "project title",
        },
        {
            "tag": "QNA",
            "id": None,
            "title": "qna title",
        },
    ]


def test_reference_event_collects_context_from_rag_search_node():
    client = _build_test_client(
        docs=[
            {
                "tag": "IRD_NAI_RI_PAPER",
                "rst_id": "RST-RAG",
                "title_text": "rag paper title",
            }
        ],
        graph_cls=FakeRagSearchGraph,
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert _read_reference_event(response.text) == [
        {
            "tag": "IRD_NAI_RI_PAPER",
            "id": "RST-RAG",
            "title": "rag paper title",
        }
    ]


def test_reference_event_uses_legacy_flat_payload_without_tag_envelope():
    client = _build_test_client(
        docs=[
            {
                "tag": "IRD_NAI_PJT_INFO",
                "pjt_id": "PJT-123",
                "title_text": "project title",
            }
        ]
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    payload = _read_legacy_reference_payload(response.text)
    assert "tag" not in payload
    assert payload == {
        "reference": [
            {
                "tag": "IRD_NAI_PJT_INFO",
                "id": "PJT-123",
                "title": "project title",
            }
        ]
    }


class FakeClarificationGraph:
    def __init__(self, docs=None):
        self._docs = docs or []

    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "rag_search"},
            "data": {
                "output": {
                    "context": [],
                    "clarification": {
                        "clarification_type": "followup_reference",
                        "status": "unresolved",
                        "candidates": [{"index": 1, "title": "paper title", "rst_id": "RST-1"}],
                    },
                }
            },
        }


class FakeDebugGraph:
    async def ainvoke(self, inputs):
        return {
            "answer_gemma": None,
            "answer_solar": None,
            "messages": [],
            "context": [],
            "latencies": {},
            "clarification": {"clarification_type": "followup_reference", "status": "unresolved"},
        }


class FakeSolarChunkGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "generate_answer_solar"},
            "data": {"chunk": {"content": "solar token"}},
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": "solar token",
                    "merge_debug": {
                        "selected_model": "solar",
                        "selected_answer_source": "llm_streamed",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "llm_streamed",
                        "answer_source": "llm_streamed",
                        "user_visible_final_required": True,
                    },
                    "context": [],
                }
            },
        }


class FakeFinalArtifactGraph:
    def __init__(self, *, answer="fallback detail", selected_model="solar"):
        self.answer = answer
        self.selected_model = selected_model

    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": self.answer,
                    "merge_debug": {
                        "selected_model": self.selected_model,
                        "selected_answer_source": "detail_cache",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "detail_cache",
                        "answer_source": "detail_cache",
                        "user_visible_final_required": True,
                    },
                    "context": [],
                }
            },
        }


class FakeDirectThenMergedGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "direct_answer"},
            "data": {
                "output": {
                    "answer_gemma": "real token",
                    "final_answer_text": "real token",
                    "final_answer_artifact": {"ignored": True},
                    "merge_debug": {
                        "selected_model": "direct",
                        "selected_answer_source": "direct_answer",
                        "selected_answer_kind": "direct_answer",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "direct_answer",
                        "answer_source": "direct_answer",
                        "user_visible_final_required": True,
                    },
                }
            },
        }
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": "real token",
                    "merge_debug": {
                        "selected_model": "gemma",
                        "selected_answer_source": "direct_answer",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "direct_answer",
                        "answer_source": "direct_answer",
                    },
                    "context": [],
                }
            },
        }


class FakeDebugSelectionGraph:
    async def ainvoke(self, inputs):
        return {
            "answer_gemma": "fallback detail",
            "answer_solar": "fallback detail",
            "final_answer_text": "fallback detail",
            "messages": [],
            "context": [],
            "latencies": {},
            "clarification": None,
            "merge_debug": {
                "selected_model": "solar",
                "selected_answer_source": "detail_cache",
                "selected_answer_kind": "detail_cache",
            },
            "selected_answer_meta": {
                "answer_kind": "detail_cache",
                "answer_source": "detail_cache",
                "user_visible_final_required": True,
            },
        }


class FakeObjectFinalStateGraph:
    async def ainvoke(self, inputs):
        return SimpleNamespace(
            answer_gemma="object detail",
            answer_solar="object detail",
            final_answer_text="object detail",
            final_answer_artifact=SimpleNamespace(),
            context=[],
            latencies={},
            clarification=None,
            merge_debug={
                "selected_model": "solar",
                "selected_answer_source": "detail_cache",
                "selected_answer_kind": "detail_cache",
            },
            selected_answer_meta={
                "answer_kind": "detail_cache",
                "answer_source": "detail_cache",
                "user_visible_final_required": True,
            },
            question_analysis=None,
            knowledge_sufficiency=None,
            strategy=None,
            messages=[],
        )


class FakeMissingTerminalGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "merge_debug": {
                        "selected_model": "solar",
                        "selected_answer_source": "detail_cache",
                    },
                    "selected_answer_meta": {},
                    "context": [],
                }
            },
        }


def _read_tag_payloads(response_text, tag):
    payloads = []
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = json.loads(line[6:])
        if payload.get("tag") == tag:
            payloads.append(payload)
    return payloads


def test_stream_emits_answer_final_once_without_chunk_duplication_for_short_circuit_answer():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeFinalArtifactGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    answer_payloads = _read_tag_payloads(response.text, "answer")
    chunk_payloads = _read_tag_payloads(response.text, "chunk")
    event_payloads = _read_tag_payloads(response.text, "event")
    done_payloads = _read_tag_payloads(response.text, "status")

    assert answer_payloads == []
    assert chunk_payloads == []
    assert [payload["event"]["kind"] for payload in event_payloads][-2:] == ["answer.final", "done"]
    assert len(done_payloads) == 1


def test_stream_keeps_final_event_without_synthetic_chunk_for_direct_answer_path():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeDirectThenMergedGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    chunk_payloads = _read_tag_payloads(response.text, "chunk")
    answer_payloads = _read_tag_payloads(response.text, "answer")
    event_payloads = _read_tag_payloads(response.text, "event")

    assert chunk_payloads == []
    assert answer_payloads == []
    kinds = [payload["event"]["kind"] for payload in event_payloads]
    assert kinds.count("answer.chunk") == 0
    assert kinds.count("answer.final") == 1
    assert kinds.count("done") == 1


def test_stream_hides_clarification_from_legacy_flat_payload():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeClarificationGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert _read_tag_payloads(response.text, "clarification") == []
    event_payloads = _read_tag_payloads(response.text, "event")
    kinds = [payload["event"]["kind"] for payload in event_payloads]
    assert "clarification" in kinds
    assert kinds[-1] == "done"


def test_stream_legacy_chunk_maps_solar_to_upstage_label():
    app = FastAPI()
    app.state.graph = FakeSolarChunkGraph()
    app.state.kv_store = None
    app.state.metrics_http = None
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("index.html"),
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: (str((chunk or {}).get("content") or ""), "content"),
            is_hit_source=lambda doc: True,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: "strategy_violation",
            collect_metrics_snapshot=lambda metrics_http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=lambda **kwargs: None,
            rag_mapper=FakeRagMapper(),
            human_message=lambda content: {"role": "user", "content": content},
            strategy_violation=RuntimeError,
        ),
    )
    client = TestClient(app)

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    chunk_payloads = _read_tag_payloads(response.text, "chunk")
    assert len(chunk_payloads) == 1
    assert chunk_payloads[0]["model"] == "UPSTAGE"
    assert chunk_payloads[0]["content"] == "solar token"


def test_stream_event_seq_is_monotonic_and_done_is_terminal():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeDirectThenMergedGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    event_payloads = _read_tag_payloads(response.text, "event")
    seqs = [payload["event"]["seq"] for payload in event_payloads]
    kinds = [payload["event"]["kind"] for payload in event_payloads]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1


def test_debug_route_includes_selected_answer_artifact_metadata():
    os.environ["ENABLE_DEBUG_ROUTES"] = "true"
    app = FastAPI()
    app.state.graph = FakeDebugSelectionGraph()
    app.state.kv_store = None
    app.state.metrics_http = None
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("index.html"),
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ("", "content"),
            is_hit_source=lambda doc: True,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: "strategy_violation",
            collect_metrics_snapshot=lambda metrics_http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=lambda **kwargs: None,
            rag_mapper=FakeRagMapper(),
            human_message=lambda content: {"role": "user", "content": content},
            strategy_violation=RuntimeError,
        ),
    )
    client = TestClient(app)

    response = client.post("/query/debug", json={"question": "test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["merge_debug"]["selected_answer_source"] == "detail_cache"
    assert payload["selected_answer_meta"]["answer_kind"] == "detail_cache"
    assert payload["selected_answer_meta"]["user_visible_final_required"] is True
    assert payload["final_answer_meta"]["answer_kind"] == "detail_cache"


def test_debug_route_reads_final_answer_from_object_state():
    os.environ["ENABLE_DEBUG_ROUTES"] = "true"
    app = FastAPI()
    app.state.graph = FakeObjectFinalStateGraph()
    app.state.kv_store = None
    app.state.metrics_http = None
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("index.html"),
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=lambda *args, **kwargs: None,
            is_debug_logging_enabled=lambda: False,
            mask_query_for_log=lambda value: value,
            extract_stream_chunk_text_and_field=lambda chunk: ("", "content"),
            is_hit_source=lambda doc: True,
            derive_stream_error_code=lambda meta: None,
            compute_total_ms_from_start=lambda started_at: 0,
            extract_contract_failure_details=lambda reason: {},
            friendly_strategy_violation_message=lambda **kwargs: "strategy_violation",
            collect_metrics_snapshot=lambda metrics_http: None,
            metrics_stream_interval_seconds=1.0,
            set_log_context=lambda **kwargs: None,
            rag_mapper=FakeRagMapper(),
            human_message=lambda content: {"role": "user", "content": content},
            strategy_violation=RuntimeError,
        ),
    )
    client = TestClient(app)

    response = client.post("/query/debug", json={"question": "test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["output_message"] == "object detail"
    assert payload["final_answer_meta"]["answer_kind"] == "detail_cache"


def test_stream_emits_guard_final_before_done_when_terminal_payload_is_missing():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeMissingTerminalGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    answer_payloads = _read_tag_payloads(response.text, "answer")
    event_payloads = _read_tag_payloads(response.text, "event")
    assert len(answer_payloads) == 1
    assert answer_payloads[0]["error_code"] == "MISSING_FINAL_ANSWER"
    assert answer_payloads[0]["degraded"] is True
    assert [payload["event"]["kind"] for payload in event_payloads][-2:] == ["answer.final", "done"]
