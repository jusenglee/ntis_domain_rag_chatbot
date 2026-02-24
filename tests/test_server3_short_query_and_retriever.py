from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional


class _RuleDecision:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _identity_decorator(_name: str):
    def _wrap(func):
        return func

    return _wrap


def _load_precheck_helpers() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    targets = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_is_short_query_exception"
    ]
    targets += [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "node_rule_precheck"
    ]

    module = ast.Module(body=targets, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "AgentState": object,
        "RuleDecision": _RuleDecision,
        "measure_latency": _identity_decorator,
        "re": __import__("re"),
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


def _load_retriever_class() -> Any:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    target = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CustomRAGRetriever"
    )

    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    class _BaseModel:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Dict": Dict,
        "Optional": Optional,
        "BaseModel": _BaseModel,
        "ConfigDict": lambda **kwargs: kwargs,
        "IntentPayloadV2": object,
        "NormalizedIntent": object,
        "_resolve_title_from_payload": lambda payload: payload.get("title") or payload.get("title_text") or "",
        "run_rag_ab_compare": lambda **kwargs: {
            "M": SimpleNamespace(
                reranked_hits=[],
                context="",
            )
        },
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace["CustomRAGRetriever"]


def _load_refine_documents_rule_based() -> Any:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    target = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "refine_documents_rule_based"
    )

    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "List": list,
        "Optional": Optional,
        "Dict": Dict,
        "Document": dict,
        "MAX_FIELD_SENTENCES": 10,
        "MAX_FIELD_TOKENS": 200,
        "MAX_DOC_SENTENCES": 20,
        "MAX_DOC_TOKENS": 500,
        "_safe_map_doc": lambda doc, context=None: dict(doc),
        "_apply_title_preference": lambda mapped_doc: None,
        "format_metadata": lambda *args, **kwargs: "",
        "_match_prtcp_members": lambda *args, **kwargs: [],
        "RagMapper": SimpleNamespace(get_researcher_info=lambda _doc: []),
        "_format_researcher_line": lambda *args, **kwargs: "",
        "_collect_org_hints": lambda *args, **kwargs: ([], [], None),
        "_match_prtcp_orgs": lambda prtcp_orgs, *args, **kwargs: prtcp_orgs,
        "_format_org_line": lambda matched_orgs, *_args, **_kwargs: (
            "참여기관: " + ", ".join(
                str(org.get("org_nm") or org.get("name") or "")
                for org in (matched_orgs or [])
                if isinstance(org, dict)
            )
        ).strip(),
        "_limit_text_by_sentences_and_tokens": lambda text, **kwargs: text,
        "_split_sentences": lambda text: [line for line in text.splitlines() if line.strip()],
        "log_section": lambda *args, **kwargs: None,
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace["refine_documents_rule_based"]


def test_short_query_exception_patterns_allow():
    ns = _load_precheck_helpers()
    helper = ns["_is_short_query_exception"]

    assert helper("ETRI") is True
    assert helper("KAIST") is True
    assert helper("123456") is True
    assert helper("12-3456") is True
    assert helper("ab") is False


def test_node_rule_precheck_allows_short_acronym_query():
    ns = _load_precheck_helpers()
    node_rule_precheck = ns["node_rule_precheck"]

    state = SimpleNamespace(messages=[SimpleNamespace(content="ETRI")])
    out = asyncio.run(node_rule_precheck(state))

    assert out["rule_decision"].action == "proceed"


def test_retriever_infers_tag_from_collection_and_keeps_tagless_doc():
    retriever_cls = _load_retriever_class()

    hit_without_tag = SimpleNamespace(
        payload={
            "_collection": "ntis_project_v1",
            "doc_id": "doc-1",
            "title_text": "제목",
            "meta_basic": {},
        }
    )

    retriever = retriever_cls(model_name="x", top_k=3, intent_payload=None)

    def _fake_run_rag_ab_compare(**kwargs):
        return {
            "M": SimpleNamespace(reranked_hits=[hit_without_tag], context="ctx")
        }

    # 클래스가 정의된 namespace의 전역 함수를 교체
    retriever.retrieve.__globals__["run_rag_ab_compare"] = _fake_run_rag_ab_compare

    out = retriever.retrieve("q")

    assert len(out["documents"]) == 1
    assert out["documents"][0]["tag"] == "IRD_NAI_PJT_INFO"


def test_refine_documents_rule_based_exposes_participant_organization_line():
    refine_documents_rule_based = _load_refine_documents_rule_based()

    docs = [
        {
            "source_index": 1,
            "title": "테스트 과제",
            "prtcp_org": [{"org_nm": "한국전자통신연구원"}],
        }
    ]

    output = refine_documents_rule_based(docs)

    assert "참여기관" in output
    assert "한국전자통신연구원" in output


def test_retriever_stats_response_includes_topk_metric_window_contract():
    retriever_cls = _load_retriever_class()
    retriever = retriever_cls(model_name="x", top_k=2, intent_payload=None)

    def _fake_run_rag_ab_compare(**kwargs):
        return {
            "M": SimpleNamespace(
                reranked_hits=[],
                context="stats-ctx",
                aggregation={
                    "metric": "project_participation_count",
                    "window_years": {"from": "2020", "to": "2024"},
                    "candidate_docs": 12,
                    "rank_items": [
                        {"hm_id": "P-1", "hm_nm": "홍길동", "project_participation_count": 7},
                        {"hm_id": "P-2", "hm_nm": "김철수", "project_participation_count": 5},
                        {"hm_id": "P-3", "hm_nm": "이영희", "project_participation_count": 4},
                    ],
                },
            )
        }

    retriever.retrieve.__globals__["run_rag_ab_compare"] = _fake_run_rag_ab_compare

    out = retriever.retrieve("최근 5년 최다 참여 연구자")
    docs = out["documents"]

    assert len(docs) == 2
    assert [doc["rank_item"]["hm_id"] for doc in docs] == ["P-1", "P-2"]
    assert all(doc["source_type"] == "aggregation" for doc in docs)
    assert all(doc["metric"] == "project_participation_count" for doc in docs)
    assert all(doc["window_years"] == {"from": "2020", "to": "2024"} for doc in docs)
