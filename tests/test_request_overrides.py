import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from apps.api.routes import RouteDeps, register_routes
from apps.api.services.answer_generation import build_answer_context, generate_answer
from apps.api.services.rag_retriever import CustomRAGRetriever
from apps.core import triton_client
from apps.core.openai_compat_llm import OpenAICompatChatModel
from apps.core.rag_pipeline import run_rag_ab_compare
from apps.core.settings import TRITON_TIMEOUTS


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


def _build_client(graph):
    app = FastAPI()
    app.state.graph = graph
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


def test_query_stream_includes_request_overrides_in_graph_inputs():
    graph = CaptureGraph()
    client = _build_client(graph)

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
    assert graph.last_inputs["request_overrides"] == {
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 321,
        "top_k": 17,
        "RAG_MIN_DENSE_SCORE": 0.61,
        "RAG_TOPK_DENSE": 44,
        "RAG_W_LEX": 0.33,
        "RAG_TOPK_LEX_CAND": 555,
    }


def test_generate_answer_passes_llm_request_overrides():
    calls = []

    async def fake_load_system_prompt(_path):
        return "system"

    async def fake_run_llm_streaming(_llm, _messages, **kwargs):
        calls.append(kwargs)
        return "answer", {"ttft_any_ms": 1, "ttft_content_ms": 2, "reasoning_chars": 0, "content_chars": 6}

    state = SimpleNamespace(
        no_result_message="",
        knowledge_sufficiency=None,
        question_analysis=SimpleNamespace(mode="SEARCH"),
        intent_payload=None,
        strategy=None,
        context=[],
        prev_context=[],
        canonical_evidence=[],
        render_profile={},
        messages=[HumanMessage(content="질문")],
        request_id="rid",
        conversation_id="cid",
        request_overrides={"temperature": 0.55, "top_p": 0.88, "max_tokens": 321, "top_k": 19},
    )

    result = __import__("asyncio").run(
        generate_answer(
            state,
            model_name="gemma_triton_0",
            final_field="answer_gemma",
            build_llm_fn=lambda model_name: object(),
            build_answer_context_fn=lambda **kwargs: {
                "context_text": "ctx",
                "rendered_context_used": True,
                "context_sentences": 1,
                "context_tokens_est": 1,
                "context_source": "canonical_evidence",
            },
            load_system_prompt_fn=fake_load_system_prompt,
            system_prompt_path=Path("prompt.md"),
            log_section_fn=lambda *args, **kwargs: None,
            select_max_tokens_hint_fn=lambda qa: 123,
            run_llm_streaming_fn=fake_run_llm_streaming,
            log_event=lambda *args, **kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            solar_ttft_deadline_ms=0,
            solar_gen_deadline_ms=0,
            solar_stream_max_chars=0,
        )
    )

    assert result["answer_gemma"] == "answer"
    assert calls[0]["max_tokens_hint"] == 321
    assert calls[0]["astream_kwargs"] == {"temperature": 0.55, "top_p": 0.88, "top_k": 19}


def test_custom_rag_retriever_forwards_request_overrides(monkeypatch):
    captured = {}

    def fake_run_rag_ab_compare(*, query, model_name, intent_payload=None, request_overrides=None):
        kwargs = {
            "query": query,
            "model_name": model_name,
            "intent_payload": intent_payload,
            "request_overrides": request_overrides,
        }
        captured.update(kwargs)
        return {
            "M": SimpleNamespace(
                reranked_hits=[],
                aggregation={},
                series={},
                canonical_evidence=[],
                render_profile={},
                timings={},
            )
        }

    monkeypatch.setattr("apps.api.services.rag_retriever.run_rag_ab_compare", fake_run_rag_ab_compare)

    retriever = CustomRAGRetriever(request_overrides={"RAG_TOPK_DENSE": 77, "RAG_W_LEX": 0.42})
    retriever.retrieve("test")

    assert captured["request_overrides"] == {"RAG_TOPK_DENSE": 77, "RAG_W_LEX": 0.42}


def test_custom_rag_retriever_unwraps_nested_payload_and_preserves_detail_fields(monkeypatch):
    def fake_run_rag_ab_compare(*, query, model_name, intent_payload=None, request_overrides=None):
        return {
            "M": SimpleNamespace(
                reranked_hits=[
                    {
                        "payload": {
                            "payload": {
                                "doc_id": "1711135956",
                                "pjt_id": "1711135956",
                                "pjt_no": "2021R1F1A1057134",
                                "org_nm": "숙명여자대학",
                                "title_text": "단일 반도체물질 기반 3진 논리 게이트 개발 Development of ternary logic gates using a single semiconducting material",
                                "title1": "단일 반도체물질 기반 3진 논리 게이트 개발",
                                "title2": "Development of ternary logic gates using a single semiconducting material",
                                "meta_basic": {
                                    "kor_pjt_nm": "단일 반도체물질 기반 3진 논리 게이트 개발",
                                    "rsch_abstract": "요약 본문",
                                    "rsch_goal_abstract": "목표 본문",
                                    "tot_rsch_start_dt": "2021-06-01",
                                    "tot_rsch_end_dt": "2022-05-31",
                                    "rndco_tot_amt": "48400000",
                                },
                                "content1": "목표 본문",
                                "content2": "요약 본문",
                                "content_text": "전체 본문",
                            }
                        }
                    }
                ],
                aggregation={},
                series={},
                canonical_evidence=[],
                render_profile={},
                timings={},
            )
        }

    monkeypatch.setattr("apps.api.services.rag_retriever.run_rag_ab_compare", fake_run_rag_ab_compare)

    retriever = CustomRAGRetriever(top_k=1)
    result = retriever.retrieve("test")

    assert len(result["documents"]) == 1
    doc = result["documents"][0]
    assert doc["title"] == "단일 반도체물질 기반 3진 논리 게이트 개발"
    assert doc["pjt_id"] == "1711135956"
    assert doc["pjt_no"] == "2021R1F1A1057134"
    assert doc["org_nm"] == "숙명여자대학"
    assert doc["content1"] == "목표 본문"
    assert doc["content2"] == "요약 본문"
    assert doc["content_text"] == "전체 본문"
    assert doc["meta_basic"]["rndco_tot_amt"] == "48400000"


def test_query_debug_includes_request_overrides_in_graph_inputs():
    graph = CaptureDebugGraph()
    client = _build_client(graph)

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
    assert graph.last_inputs["request_overrides"] == {
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 321,
        "top_k": 17,
        "RAG_MIN_DENSE_SCORE": 0.61,
        "RAG_TOPK_DENSE": 44,
        "RAG_W_LEX": 0.33,
        "RAG_TOPK_LEX_CAND": 555,
    }

def test_run_rag_ab_compare_signature_accepts_request_overrides():
    params = inspect.signature(run_rag_ab_compare).parameters

    assert "request_overrides" in params


def test_custom_rag_retriever_fails_fast_for_stale_run_rag_ab_compare(monkeypatch):
    def legacy_run_rag_ab_compare(*, query, model_name, intent_payload=None):
        return {
            "M": SimpleNamespace(
                reranked_hits=[],
                aggregation={},
                series={},
                canonical_evidence=[],
                render_profile={},
                timings={},
            )
        }

    monkeypatch.setattr("apps.api.services.rag_retriever.run_rag_ab_compare", legacy_run_rag_ab_compare)

    retriever = CustomRAGRetriever(request_overrides={"RAG_TOPK_DENSE": 77})

    try:
        retriever.retrieve("test")
    except TypeError as exc:
        assert "does not accept request_overrides" in str(exc)
    else:
        raise AssertionError("expected stale runtime signature error")



def test_triton_infer_signature_accepts_top_k():
    params = inspect.signature(triton_client.triton_infer).parameters

    assert "top_k" in params


def test_triton_make_inputs_serializes_top_k(monkeypatch):
    captured = {}

    class FakeInferInput:
        def __init__(self, name, shape, dtype):
            self.name = name
            self.shape = shape
            self.dtype = dtype

        def set_data_from_numpy(self, arr):
            captured[self.name] = arr

    monkeypatch.setattr(triton_client, "InferInput", FakeInferInput)

    triton_client._make_inputs(
        "prompt",
        max_tokens=64,
        temperature=0.2,
        top_p=0.8,
        top_k=11,
    )

    params = json.loads(captured["sampling_parameters"][0].decode("utf-8"))

    assert params["top_k"] == "11"


def test_triton_make_inputs_omits_top_k_when_absent(monkeypatch):
    captured = {}

    class FakeInferInput:
        def __init__(self, name, shape, dtype):
            self.name = name
            self.shape = shape
            self.dtype = dtype

        def set_data_from_numpy(self, arr):
            captured[self.name] = arr

    monkeypatch.setattr(triton_client, "InferInput", FakeInferInput)

    triton_client._make_inputs(
        "prompt",
        max_tokens=64,
        temperature=0.2,
        top_p=0.8,
    )

    params = json.loads(captured["sampling_parameters"][0].decode("utf-8"))

    assert "top_k" not in params


def test_query_stream_rejects_invalid_top_k():
    graph = CaptureGraph()
    client = _build_client(graph)

    response = client.post("/query/stream", json={"question": "test", "Top-K": 0})

    assert response.status_code == 422
    assert response.json()["detail"] == "Top-K must be >= 1"


def test_query_debug_rejects_invalid_top_p():
    graph = CaptureDebugGraph()
    client = _build_client(graph)

    response = client.post("/query/debug", json={"question": "test", "Top-P": 1.5})

    assert response.status_code == 422
    assert response.json()["detail"] == "Top-P must be > 0 and <= 1"


def test_openai_compat_extra_body_includes_top_k():
    llm = OpenAICompatChatModel()

    body = llm._build_extra_body({"top_k": 23})

    assert body is not None
    assert body["top_k"] == 23


def test_openai_compat_extra_body_omits_top_k_when_absent():
    llm = OpenAICompatChatModel()

    body = llm._build_extra_body({})

    assert body is not None
    assert "top_k" not in body


def test_triton_timeouts_include_gpt_oss_model():
    assert "gpt_triton_0" in TRITON_TIMEOUTS
    assert TRITON_TIMEOUTS["gpt_triton_0"]["stream"][0] >= 1
    assert TRITON_TIMEOUTS["gpt_triton_0"]["sync"][1] >= 1


def test_generate_answer_uses_existing_answer_artifact_without_llm_call():
    async def fake_load_system_prompt(_path):
        return "system"

    async def fake_run_llm_streaming(_llm, _messages, **kwargs):
        raise AssertionError("llm should not run for answer_artifact short-circuit")

    from apps.api.streaming.contracts import AnswerArtifact

    artifact = AnswerArtifact(
        text="detail fallback",
        answer_kind="detail_profile",
        stream_metrics={"content_chars": 15, "stream_content_emitted_chunks": 1},
        user_visible_final_required=True,
        meta={"answer_source": "detail_lookup", "model_key": "gemma"},
    )
    state = SimpleNamespace(
        answer_artifact=artifact,
        no_result_message="",
        knowledge_sufficiency=None,
        question_analysis=SimpleNamespace(mode="SEARCH"),
        intent_payload=None,
        strategy=None,
        context=[],
        prev_context=[],
        canonical_evidence=[],
        render_profile={},
        messages=[HumanMessage(content="question")],
        request_id="rid",
        conversation_id="cid",
        request_overrides={},
    )

    result = __import__("asyncio").run(
        generate_answer(
            state,
            model_name="gemma_triton_0",
            final_field="answer_gemma",
            build_llm_fn=lambda model_name: object(),
            build_answer_context_fn=lambda **kwargs: {
                "context_text": "ctx",
                "rendered_context_used": True,
                "context_sentences": 1,
                "context_tokens_est": 1,
                "context_source": "canonical_evidence",
            },
            load_system_prompt_fn=fake_load_system_prompt,
            system_prompt_path=Path("prompt.md"),
            log_section_fn=lambda *args, **kwargs: None,
            select_max_tokens_hint_fn=lambda qa: 123,
            run_llm_streaming_fn=fake_run_llm_streaming,
            log_event=lambda *args, **kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            solar_ttft_deadline_ms=0,
            solar_gen_deadline_ms=0,
            solar_stream_max_chars=0,
        )
    )

    meta = result["answer_gemma_meta"]
    assert meta["answer_kind"] == "detail_profile"
    assert meta["answer_source"] == "detail_lookup"
    assert meta["user_visible_final_required"] is True



def test_generate_answer_prefers_pipeline_answer_context_text():
    captured = {}

    async def fake_load_system_prompt(_path):
        return "system"

    async def fake_run_llm_streaming(_llm, messages, **kwargs):
        captured["messages"] = messages
        return "answer", {"ttft_any_ms": 1, "ttft_content_ms": 2, "reasoning_chars": 0, "content_chars": 6}

    state = SimpleNamespace(
        no_result_message="",
        knowledge_sufficiency=None,
        question_analysis=SimpleNamespace(mode="SEARCH", question_summary="summary question"),
        intent_payload=SimpleNamespace(normalized_intent=SimpleNamespace(retrieval_query="normalized query")),
        strategy=None,
        context=[],
        prev_context=[],
        canonical_evidence=[],
        retrieval_bundle=SimpleNamespace(answer_context_text="pipeline context"),
        answer_context_text="",
        render_profile={},
        messages=[HumanMessage(content="raw question")],
        request_id="rid",
        conversation_id="cid",
        request_overrides={},
    )

    result = __import__("asyncio").run(
        generate_answer(
            state,
            model_name="gemma_triton_0",
            final_field="answer_gemma",
            build_llm_fn=lambda model_name: object(),
            build_answer_context_fn=lambda **kwargs: build_answer_context(
                refine_documents_rule_based_fn=None,
                priority_context_fields=(),
                max_field_sentences=0,
                max_field_tokens=0,
                default_max_doc_sentences=0,
                default_max_doc_tokens=0,
                solar_max_doc_sentences=0,
                solar_max_doc_tokens=0,
                solar_max_context_chars=0,
                split_sentences_fn=lambda text: [part for part in text.split(".") if part],
                logger=SimpleNamespace(info=lambda *args, **kwargs: None),
                **kwargs,
            ),
            load_system_prompt_fn=fake_load_system_prompt,
            system_prompt_path=Path("prompt.md"),
            log_section_fn=lambda *args, **kwargs: None,
            select_max_tokens_hint_fn=lambda qa: 123,
            run_llm_streaming_fn=fake_run_llm_streaming,
            log_event=lambda *args, **kwargs: None,
            logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None),
            solar_ttft_deadline_ms=0,
            solar_gen_deadline_ms=0,
            solar_stream_max_chars=0,
        )
    )

    assert result["answer_gemma"] == "answer"
    human_prompt = captured["messages"][1].content
    assert "[\uC9C8\uBB38 \uC694\uC57D]\nnormalized query" in human_prompt
    assert "[\uC6D0\uBCF8 \uC9C8\uBB38]\nraw question" in human_prompt
    assert "[\uC81C\uACF5\uB41C \uC815\uBCF4]\npipeline context" in human_prompt


def test_build_answer_context_uses_pipeline_context_before_canonical():
    result = build_answer_context(
        answer_context_text="pipeline context",
        docs_for_ctx=[{"title": "ignored"}],
        canonical_evidence=[{"facts": {"title": "ignored"}}],
        render_profile={"name": "detail", "context_kind": "project"},
        normalized_intent=None,
        strategy=None,
        qa=SimpleNamespace(output_type="detail", base_route="project"),
        model_name="gemma_triton_0",
        refine_documents_rule_based_fn=None,
        priority_context_fields=(),
        max_field_sentences=0,
        max_field_tokens=0,
        default_max_doc_sentences=0,
        default_max_doc_tokens=0,
        solar_max_doc_sentences=0,
        solar_max_doc_tokens=0,
        solar_max_context_chars=0,
        split_sentences_fn=lambda text: [part for part in text.split(".") if part],
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )

    assert result["context_text"] == "pipeline context"
    assert result["context_source"] == "pipeline_context"
