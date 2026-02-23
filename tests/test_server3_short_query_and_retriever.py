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
