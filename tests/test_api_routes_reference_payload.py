import os
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.api.routes import RouteDeps, register_routes
from apps.api.streaming.contracts import AnswerArtifact


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


def _read_stream_payloads(response_text):
    payloads = []
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payloads.append(json.loads(line[6:]))
    return payloads


def _read_stream_events(response_text):
    events = []
    for payload in _read_stream_payloads(response_text):
        if payload.get("tag") == "event":
            events.append(payload["event"])
    return events


def _read_event_by_kind(response_text, kind):
    for event in _read_stream_events(response_text):
        if event.get("kind") == kind:
            return event
    raise AssertionError(f"{kind} event not found")


def _read_reference_event(response_text):
    return _read_event_by_kind(response_text, "reference.set")["meta"]["references"]


def _assert_event_only_stream(response_text):
    payloads = _read_stream_payloads(response_text)
    assert payloads
    assert all(payload.get("tag") == "event" for payload in payloads)


def _index_of_event_kind(response_text, kind):
    for idx, payload in enumerate(_read_stream_payloads(response_text)):
        if payload.get("tag") == "event" and isinstance(payload.get("event"), dict) and payload["event"].get("kind") == kind:
            return idx
    raise AssertionError(f"{kind} event not found")


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


def test_stream_uses_canonical_event_envelope_only():
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
    _assert_event_only_stream(response.text)
    assert _read_reference_event(response.text) == [
        {
            "tag": "IRD_NAI_PJT_INFO",
            "id": "PJT-123",
            "title": "project title",
        }
    ]


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
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": "이전 출처 목록에는 1개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요.",
                    "merge_debug": {
                        "selected_model": "solar",
                        "selected_answer_source": "followup_clarification",
                        "selected_answer_kind": "clarification",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "clarification",
                        "answer_source": "followup_clarification",
                        "user_visible_final_required": True,
                    },
                    "context": [],
                }
            },
        }


class FakeDebugGraph:
    async def ainvoke(self, inputs):
        return {
            "answer_gemma": None,
            "answer_solar": "이전 출처 목록에는 1개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요.",
            "final_answer_text": "이전 출처 목록에는 1개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요.",
            "messages": [],
            "context": [],
            "latencies": {},
            "clarification": {"clarification_type": "followup_reference", "status": "unresolved"},
            "merge_debug": {
                "selected_model": "solar",
                "selected_answer_source": "followup_clarification",
                "selected_answer_kind": "clarification",
            },
            "selected_answer_meta": {
                "answer_kind": "clarification",
                "answer_source": "followup_clarification",
                "user_visible_final_required": True,
            },
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


class FakeExplicitReferenceArtifactGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": "artifact backed detail",
                    "final_answer_artifact": AnswerArtifact(
                        text="artifact backed detail",
                        answer_kind="detail_cache",
                        stream_metrics={},
                        user_visible_final_required=True,
                        references=[
                            {
                                "tag": "IRD_NAI_PJT_INFO",
                                "id": "PJT-ARTIFACT-1",
                                "title": "artifact project title",
                            }
                        ],
                        meta={"answer_source": "detail_cache", "model_key": "solar"},
                    ),
                    "merge_debug": {
                        "selected_model": "solar",
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


class FakeCanonicalEvidenceReferenceGraph:
    async def astream_events(self, inputs, version="v2"):
        yield {
            "event": "on_chain_end",
            "metadata": {"langgraph_node": "merge_answers"},
            "data": {
                "output": {
                    "final_answer_text": "canonical backed detail",
                    "merge_debug": {
                        "selected_model": "solar",
                        "selected_answer_source": "detail_cache",
                    },
                    "selected_answer_meta": {
                        "answer_kind": "detail_cache",
                        "answer_source": "detail_cache",
                        "user_visible_final_required": True,
                    },
                    "canonical_evidence": [
                        {
                            "source_type": "project",
                            "identity": "canonical-project-1",
                            "ids": {
                                "pjt_id": "PJT-CANON-1",
                            },
                            "facts": {
                                "title": "canonical project title",
                            },
                            "evidence": {
                                "title_text": "canonical project title",
                            },
                        }
                    ],
                    "context": [],
                }
            },
        }


class FakeAinvokeFinalGraph:
    async def ainvoke(self, inputs):
        return {
            "final_answer_text": "ainvoke detail",
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
            "context": [],
            "messages": [],
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


class FakeDebugGroundednessGraph:
    async def ainvoke(self, inputs):
        return {
            "answer_gemma": "unsupported answer",
            "answer_solar": "unsupported answer",
            "final_answer_text": "unsupported answer",
            "messages": [],
            "context": [],
            "latencies": {},
            "clarification": None,
            "merge_debug": {
                "selected_model": "solar",
                "selected_answer_source": "solar",
                "selected_answer_kind": "llm_collected",
            },
            "selected_answer_meta": {
                "answer_kind": "llm_collected",
                "answer_source": "solar",
                "user_visible_final_required": True,
                "groundedness_status": "unsupported",
                "groundedness_reason_codes": ["unsupported_project_id"],
                "groundedness_summary": {
                    "status": "unsupported",
                    "reason_codes": ["unsupported_project_id"],
                },
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


def test_stream_emits_answer_final_once_without_chunk_duplication_for_short_circuit_answer():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeFinalArtifactGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    events = _read_stream_events(response.text)
    kinds = [event["kind"] for event in events]

    _assert_event_only_stream(response.text)
    assert "answer.chunk" not in kinds
    assert kinds[-2:] == ["reference.set", "done"]
    assert kinds.count("done") == 1
    assert _read_reference_event(response.text) == []
    assert _index_of_event_kind(response.text, "answer.final") < _index_of_event_kind(response.text, "reference.set") < _index_of_event_kind(response.text, "done")


def test_stream_emits_reference_payload_even_when_reference_list_is_empty():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeFinalArtifactGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    _assert_event_only_stream(response.text)
    assert _read_reference_event(response.text) == []


def test_stream_uses_explicit_final_answer_artifact_references_when_context_is_empty():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeExplicitReferenceArtifactGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    _assert_event_only_stream(response.text)
    expected = [
        {
            "tag": "IRD_NAI_PJT_INFO",
            "id": "PJT-ARTIFACT-1",
            "title": "artifact project title",
        }
    ]
    assert _read_reference_event(response.text) == expected
    assert _read_event_by_kind(response.text, "reference.set")["meta"]["references"] == expected


def test_stream_uses_canonical_evidence_references_when_context_is_empty():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeCanonicalEvidenceReferenceGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    _assert_event_only_stream(response.text)
    expected = [
        {
            "tag": "IRD_NAI_PJT_INFO",
            "id": "PJT-CANON-1",
            "title": "canonical project title",
        }
    ]
    assert _read_reference_event(response.text) == expected
    assert _read_event_by_kind(response.text, "reference.set")["meta"]["references"] == expected


def test_stream_keeps_final_event_without_synthetic_chunk_for_direct_answer_path():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeDirectThenMergedGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    events = _read_stream_events(response.text)
    kinds = [event["kind"] for event in events]

    _assert_event_only_stream(response.text)
    assert kinds.count("answer.chunk") == 0
    assert kinds.count("answer.final") == 1
    assert kinds.count("done") == 1
    assert _index_of_event_kind(response.text, "answer.final") < _index_of_event_kind(response.text, "reference.set") < _index_of_event_kind(response.text, "done")


def test_stream_clarification_tail_emits_reference_then_done_without_answer_final():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeClarificationGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    events = _read_stream_events(response.text)
    kinds = [event["kind"] for event in events]
    clarification_event = _read_event_by_kind(response.text, "clarification")

    _assert_event_only_stream(response.text)
    assert "answer.chunk" not in kinds
    assert "answer.final" not in kinds
    assert kinds[-2:] == ["reference.set", "done"]
    assert clarification_event["content"] == "이전 출처 목록에는 1개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요."
    assert clarification_event["meta"]["clarification"]["clarification_type"] == "followup_reference"
    assert clarification_event["meta"]["clarification"]["status"] == "unresolved"
    assert clarification_event["meta"]["clarification"]["message"] == clarification_event["content"]
    assert _read_reference_event(response.text) == []
    assert _index_of_event_kind(response.text, "clarification") < _index_of_event_kind(response.text, "reference.set") < _index_of_event_kind(response.text, "done")


def test_debug_route_backfills_clarification_message_from_selected_artifact():
    os.environ["ENABLE_DEBUG_ROUTES"] = "true"
    app = FastAPI()
    app.state.graph = FakeDebugGraph()
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
    assert payload["clarification"]["message"] == "이전 출처 목록에는 1개만 있습니다. 몇 번째 출처를 말씀하시는지 다시 알려주세요."
    assert payload["clarification"]["status"] == "unresolved"


def test_stream_emits_answer_chunk_event_with_canonical_model_key():
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
    _assert_event_only_stream(response.text)
    chunk_event = _read_event_by_kind(response.text, "answer.chunk")
    assert chunk_event["model_key"] == "solar"
    assert chunk_event["content"] == "solar token"


def test_stream_event_seq_is_monotonic_and_done_is_terminal():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeDirectThenMergedGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    events = _read_stream_events(response.text)
    seqs = [event["seq"] for event in events]
    kinds = [event["kind"] for event in events]

    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert kinds[-1] == "done"
    assert kinds.count("done") == 1


def test_stream_emits_conversation_event_with_conversation_id():
    client = _build_test_client(docs=[])

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    conversation_event = _read_event_by_kind(response.text, "conversation")
    assert isinstance(conversation_event["meta"]["conversation_id"], str)
    assert conversation_event["meta"]["conversation_id"]


def test_stream_emits_status_event_for_ainvoke_path():
    app = FastAPI()
    app.state.graph = FakeAinvokeFinalGraph()
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

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    _assert_event_only_stream(response.text)
    status_event = _read_event_by_kind(response.text, "status")
    assert status_event["meta"]["status"] == "retrieve"
    assert [event["kind"] for event in _read_stream_events(response.text)][:2] == ["conversation", "status"]


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


def test_debug_route_preserves_groundedness_metadata_in_reconstructed_final_answer():
    os.environ["ENABLE_DEBUG_ROUTES"] = "true"
    app = FastAPI()
    app.state.graph = FakeDebugGroundednessGraph()
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
    assert payload["selected_answer_meta"]["groundedness_status"] == "unsupported"
    assert payload["final_answer_meta"]["groundedness_status"] == "unsupported"
    assert payload["final_answer_meta"]["groundedness_reason_codes"] == ["unsupported_project_id"]


def test_stream_emits_guard_final_before_done_when_terminal_payload_is_missing():
    client = _build_test_client(docs=[], graph_cls=lambda docs: FakeMissingTerminalGraph())

    response = client.post("/query/stream", json={"question": "test"})

    assert response.status_code == 200
    _assert_event_only_stream(response.text)
    events = _read_stream_events(response.text)
    guard_event = _read_event_by_kind(response.text, "answer.final")
    assert guard_event["meta"]["error_code"] == "MISSING_FINAL_ANSWER"
    assert guard_event["meta"]["degraded"] is True
    assert [event["kind"] for event in events][-2:] == ["reference.set", "done"]
    assert _read_reference_event(response.text) == []
    assert _index_of_event_kind(response.text, "answer.final") < _index_of_event_kind(response.text, "reference.set") < _index_of_event_kind(response.text, "done")
