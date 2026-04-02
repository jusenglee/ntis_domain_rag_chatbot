import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routes import RouteDeps, register_routes
from apps.api.request_overrides import (
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
    def __init__(self, defaults=None, meta=None):
        self.defaults = dict(defaults or {})
        self.meta = dict(meta or {})

    async def load_defaults(self):
        return dict(self.defaults)

    async def load_defaults_with_meta(self):
        meta = {
            "oracle_lookup_status": ("loaded" if self.defaults else "empty"),
            "oracle_lookup_attempted": True,
            "oracle_loaded_key_count": len(self.defaults),
            "oracle_loaded_keys": sorted(self.defaults.keys()),
            **self.meta,
        }
        return dict(self.defaults), meta


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



def test_normalize_oracle_request_defaults_maps_oracle_uppercase_columns():
    normalized = normalize_oracle_request_defaults(
        {
            "TEMPERATURE": 0.7,
            "TOPP": 0.9,
            "TOPK": 17,
            "MAXTOKENS": 321,
            "RAGMINDENSESCORE": 0.61,
            "RAGWEIGHTLEXICAL": 0.33,
            "RAGTOPKDENSE": 44,
            "RAGTOPKLEXICALCANDIDATE": 555,
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



def test_oracle_request_defaults_loader_from_env_uses_builtin_defaults(monkeypatch):
    monkeypatch.delenv("ORACLE_PARAM_USER", raising=False)
    monkeypatch.delenv("ORACLE_PARAM_PASSWORD", raising=False)
    monkeypatch.delenv("ORACLE_PARAM_DSN", raising=False)

    loader = OracleRequestDefaultsLoader.from_env(
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
    )

    assert loader is not None
    assert loader.user == "ird"
    assert loader.password == "ird_12#$"
    assert "HOST=172.31.234.203" in loader.dsn
    assert "SERVICE_NAME=KNTIS" in loader.dsn



def test_oracle_request_defaults_loader_executes_select_and_normalizes_uppercase_columns(monkeypatch):
    connect_calls = []

    class FakeCursor:
        def __init__(self):
            self.description = [
                ("TEMPERATURE",),
                ("TOPP",),
                ("TOPK",),
                ("MAXTOKENS",),
                ("RAGMINDENSESCORE",),
                ("RAGWEIGHTLEXICAL",),
                ("RAGTOPKDENSE",),
                ("RAGTOPKLEXICALCANDIDATE",),
            ]
            self.executed = []
            self.closed = False

        def execute(self, query):
            self.executed.append(query)

        def fetchone(self):
            return (0.4, 0.75, 9, 444, 0.61, 0.33, 44, 555)

        def close(self):
            self.closed = True

    class FakeConnection:
        def __init__(self):
            self.cursor_obj = FakeCursor()
            self.closed = False

        def cursor(self):
            return self.cursor_obj

        def close(self):
            self.closed = True

    connection = FakeConnection()

    def fake_connect(*, user, password, dsn):
        connect_calls.append({"user": user, "password": password, "dsn": dsn})
        return connection

    monkeypatch.setitem(sys.modules, "oracledb", SimpleNamespace(connect=fake_connect))
    loader = OracleRequestDefaultsLoader(
        user="ird",
        password="pw",
        dsn="(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=172.31.234.203)(PORT=1253))(CONNECT_DATA=(SERVICE_NAME=KNTIS)))",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
    )

    defaults, meta = loader._load_defaults_sync_with_meta()

    assert connect_calls == [{"user": "ird", "password": "pw", "dsn": loader.dsn}]
    assert connection.cursor_obj.executed == [loader.query]
    assert connection.cursor_obj.closed is True
    assert connection.closed is True
    assert defaults == {
        "temperature": 0.4,
        "top_p": 0.75,
        "top_k": 9,
        "max_tokens": 444,
        "RAG_MIN_DENSE_SCORE": 0.61,
        "RAG_W_LEX": 0.33,
        "RAG_TOPK_DENSE": 44,
        "RAG_TOPK_LEX_CAND": 555,
    }
    assert meta["oracle_lookup_status"] == "loaded"
    assert meta["oracle_lookup_attempted"] is True
    assert meta["oracle_loaded_key_count"] == 8
    assert meta["oracle_loaded_keys"] == sorted(defaults.keys())


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


def test_req_oracle_defaults_logs_lookup_status_for_query_stream():
    graph = CaptureGraph()
    capture = CaptureLogger()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader(
            {"temperature": 0.4, "top_p": 0.75},
            meta={
                "oracle_connector_host": "172.31.234.203",
                "oracle_connector_service_name": "KNTIS",
            },
        ),
        log_event=capture,
    )

    response = client.post("/query/stream", json={"question": "test", "Temperature": 0.9})

    assert response.status_code == 200
    oracle_event = next(fields for name, fields in capture.events if name == "REQ.ORACLE.DEFAULTS")
    assert oracle_event["route"] == "/query/stream"
    assert oracle_event["oracle_lookup_status"] == "loaded"
    assert oracle_event["oracle_lookup_attempted"] is True
    assert oracle_event["oracle_loaded_key_count"] == 2
    assert oracle_event["request_override_source_counts"] == {"oracle": 1, "request": 1}
    assert oracle_event["oracle_connector_host"] == "172.31.234.203"
    assert oracle_event["oracle_connector_service_name"] == "KNTIS"


def test_req_oracle_defaults_logs_lookup_status_for_query_debug():
    graph = CaptureDebugGraph()
    capture = CaptureLogger()
    client = _build_client(
        graph,
        request_defaults_loader=FakeRequestDefaultsLoader(
            {"temperature": 0.4, "top_p": 0.75},
            meta={
                "oracle_connector_host": "172.31.234.203",
                "oracle_connector_service_name": "KNTIS",
            },
        ),
        log_event=capture,
    )

    response = client.post("/query/debug", json={"question": "test"})

    assert response.status_code == 200
    oracle_event = next(fields for name, fields in capture.events if name == "REQ.ORACLE.DEFAULTS")
    assert oracle_event["route"] == "/query/debug"
    assert oracle_event["oracle_lookup_status"] == "loaded"
    assert oracle_event["oracle_lookup_attempted"] is True
    assert oracle_event["oracle_loaded_key_count"] == 2
    assert oracle_event["request_override_source_counts"] == {"oracle": 2}
    assert oracle_event["oracle_connector_host"] == "172.31.234.203"
    assert oracle_event["oracle_connector_service_name"] == "KNTIS"


def test_req_oracle_defaults_logs_skip_when_all_request_values_present():
    graph = CaptureGraph()
    capture = CaptureLogger()
    loader = CountingRequestDefaultsLoader({"temperature": 0.4})
    client = _build_client(graph, request_defaults_loader=loader, log_event=capture)

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
    assert loader.lookup_calls == 0
    oracle_event = next(fields for name, fields in capture.events if name == "REQ.ORACLE.DEFAULTS")
    assert oracle_event["route"] == "/query/stream"
    assert oracle_event["oracle_lookup_status"] == "skipped_all_request_values_present"
    assert oracle_event["oracle_lookup_attempted"] is False


class CountingRequestDefaultsLoader(FakeRequestDefaultsLoader):
    def __init__(self, defaults=None):
        super().__init__(defaults=defaults)
        self.lookup_calls = 0
        self.load_defaults_calls = 0
        self.load_defaults_with_meta_calls = 0

    async def load_defaults(self):
        self.lookup_calls += 1
        self.load_defaults_calls += 1
        return await super().load_defaults()

    async def load_defaults_with_meta(self):
        self.lookup_calls += 1
        self.load_defaults_with_meta_calls += 1
        return await super().load_defaults_with_meta()


def test_query_stream_calls_oracle_lookup_when_request_values_absent():
    graph = CaptureGraph()
    loader = CountingRequestDefaultsLoader({"temperature": 0.4, "top_p": 0.75})
    client = _build_client(graph, request_defaults_loader=loader)

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    assert loader.lookup_calls == 1
    assert loader.load_defaults_calls == 0
    assert loader.load_defaults_with_meta_calls == 1
    assert graph.last_inputs["request_overrides"] == {"temperature": 0.4, "top_p": 0.75}


def test_query_debug_calls_oracle_lookup_when_request_values_absent():
    graph = CaptureDebugGraph()
    loader = CountingRequestDefaultsLoader({"temperature": 0.4, "RAG_TOPK_DENSE": 44})
    client = _build_client(graph, request_defaults_loader=loader)

    response = client.post("/query/debug", json={"question": "test"})

    assert response.status_code == 200
    assert loader.lookup_calls == 1
    assert loader.load_defaults_calls == 0
    assert loader.load_defaults_with_meta_calls == 1
    assert graph.last_inputs["request_overrides"] == {"temperature": 0.4, "RAG_TOPK_DENSE": 44}


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
    assert loader.lookup_calls == 0


def test_query_debug_skips_oracle_lookup_when_all_request_values_present():
    graph = CaptureDebugGraph()
    loader = CountingRequestDefaultsLoader({"temperature": 0.4})
    client = _build_client(graph, request_defaults_loader=loader)

    response = client.post(
        "/query/debug",
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
    assert loader.lookup_calls == 0
