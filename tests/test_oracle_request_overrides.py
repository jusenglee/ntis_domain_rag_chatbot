import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routes import RouteDeps, register_routes
from apps.api.services.request_overrides import (
    OracleRequestDefaultsLoader,
    merge_request_overrides,
    normalize_oracle_request_defaults,
)


class CaptureGraph:
    def __init__(self):
        self.last_inputs = None

    async def astream_events(self, inputs, version="v2"):
        self.last_inputs = inputs
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {"output": {"context": []}},
        }


class CaptureDebugGraph:
    def __init__(self):
        self.last_inputs = None

    async def ainvoke(self, inputs):
        self.last_inputs = inputs
        return {
            "answer_gemma": None,
            "answer_solar": None,
            "messages": [],
            "context": [],
            "latencies": {},
        }


class FakeRagMapper:
    def get_references(self, item):
        return {}


class FakeRequestDefaultsLoader:
    def __init__(self, defaults=None):
        self.defaults = dict(defaults or {})

    async def load_defaults(self):
        return dict(self.defaults)


class CaptureLogger:
    def __init__(self):
        self.events = []

    def __call__(self, name, **fields):
        self.events.append((name, fields))



def _build_client(graph, *, request_defaults_loader=None, log_event=None):
    app = FastAPI()
    app.state.graph = graph
    app.state.kv_store = None
    app.state.metrics_http = None
    register_routes(
        app,
        RouteDeps(
            template_index_path=Path("index.html"),
            logger=SimpleNamespace(error=lambda *args, **kwargs: None),
            log_event=log_event or (lambda *args, **kwargs: None),
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
            request_defaults_loader=request_defaults_loader,
        ),
    )
    return TestClient(app)



def test_query_stream_uses_oracle_defaults_when_request_values_absent():
    graph = CaptureGraph()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader(
            {
                "temperature": 0.4,
                "top_p": 0.75,
                "max_tokens": 444,
                "top_k": 9,
                "RAG_MIN_DENSE_SCORE": 0.61,
                "RAG_TOPK_DENSE": 44,
                "RAG_W_LEX": 0.33,
                "RAG_TOPK_LEX_CAND": 555,
            }
        ),
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert graph.last_inputs["request_overrides"] == {
        "temperature": 0.4,
        "top_p": 0.75,
        "max_tokens": 444,
        "top_k": 9,
        "RAG_MIN_DENSE_SCORE": 0.61,
        "RAG_TOPK_DENSE": 44,
        "RAG_W_LEX": 0.33,
        "RAG_TOPK_LEX_CAND": 555,
    }



def test_query_stream_request_values_override_oracle_defaults():
    graph = CaptureGraph()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader({"temperature": 0.4, "top_p": 0.75, "top_k": 9}),
    )

    response = client.post("/query/stream", json={"question": "test", "Temperature": 0.9, "Top-K": 13})

    assert response.status_code == 200
    assert graph.last_inputs["request_overrides"] == {
        "temperature": 0.9,
        "top_p": 0.75,
        "top_k": 13,
    }



def test_query_debug_uses_oracle_defaults_when_request_values_absent():
    graph = CaptureDebugGraph()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader({"temperature": 0.4, "RAG_TOPK_DENSE": 44}),
    )

    response = client.post("/query/debug", json={"question": "test"})

    assert response.status_code == 200
    assert graph.last_inputs["request_overrides"] == {
        "temperature": 0.4,
        "RAG_TOPK_DENSE": 44,
    }



def test_query_stream_rejects_invalid_merged_oracle_override():
    graph = CaptureGraph()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader({"top_p": 1.5}),
    )

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Top-P must be > 0 and <= 1"



def test_normalize_oracle_request_defaults_maps_oracle_columns():
    normalized = normalize_oracle_request_defaults(
        {
            "temperature": 0.7,
            "topP": 0.9,
            "topK": 17,
            "maxTokens": 321,
            "ragMinDenseScore": 0.61,
            "ragWeightLexical": 0.33,
            "ragTopKDense": 44,
            "ragTopKLexicalCandidate": 555,
        }
    )

    assert normalized == {
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 17,
        "max_tokens": 321,
        "RAG_MIN_DENSE_SCORE": 0.61,
        "RAG_W_LEX": 0.33,
        "RAG_TOPK_DENSE": 44,
        "RAG_TOPK_LEX_CAND": 555,
    }



def test_merge_request_overrides_preserves_request_precedence():
    merged, sources = merge_request_overrides(
        request_values={"temperature": 0.8, "top_k": 11},
        oracle_defaults={"temperature": 0.4, "top_p": 0.75, "top_k": 9},
    )

    assert merged == {"temperature": 0.8, "top_p": 0.75, "top_k": 11}
    assert sources == {"temperature": "request", "top_p": "oracle", "top_k": "request"}



def test_oracle_request_defaults_loader_from_env_returns_none_when_incomplete(monkeypatch):
    monkeypatch.delenv("ORACLE_PARAM_USER", raising=False)
    monkeypatch.delenv("ORACLE_PARAM_PASSWORD", raising=False)
    monkeypatch.delenv("ORACLE_PARAM_DSN", raising=False)

    loader = OracleRequestDefaultsLoader.from_env(
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )

    assert loader is None



def test_req_start_logs_request_override_sources():
    graph = CaptureGraph()
    capture = CaptureLogger()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader({"temperature": 0.4, "top_p": 0.75}),
        log_event=capture,
    )

    response = client.post("/query/stream", json={"question": "test", "Temperature": 0.9})

    assert response.status_code == 200
    req_start = next(fields for name, fields in capture.events if name == "REQ.START")
    assert req_start["request_override_sources"] == {"temperature": "request", "top_p": "oracle"}

class CountingRequestDefaultsLoader(FakeRequestDefaultsLoader):
    def __init__(self, defaults=None):
        super().__init__(defaults=defaults)
        self.calls = 0

    async def load_defaults(self):
        self.calls += 1
        return await super().load_defaults()


def test_query_stream_skips_oracle_lookup_when_all_request_values_present():
    graph = CaptureGraph()
    loader = CountingRequestDefaultsLoader({"temperature": 0.4})
    client = _build_client(graph, request_defaults_loader=loader)

    response = client.post(
        "/query/stream",
        json={
            "question": "test",
            "Temperature": 0.7,
            "Top-P": 0.9,
            "Max-Token": 321,
            "Top-K": 17,
            "RAG_MIN_DENSE_SCORE": 0.61,
            "RAG_TOPK_DENSE": 44,
            "RAG_W_LEX": 0.33,
            "RAG_TOPK_LEX_CAND": 555,
        },
    )

    assert response.status_code == 200
    assert loader.calls == 0
