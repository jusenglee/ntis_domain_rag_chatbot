from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

class BaseModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def ConfigDict(**kwargs):
    return kwargs


class _IntentPayloadV2:  # pragma: no cover - 테스트용 최소 스텁
    normalized_intent: Any = None


class _NormalizedIntent:  # pragma: no cover - 테스트용 최소 스텁
    pass


def _load_server3_symbols(run_rag_impl):
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    wanted = {"_resolve_title_from_payload", "_apply_title_preference", "CustomRAGRetriever"}
    selected_nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            selected_nodes.append(node)
        elif isinstance(node, ast.ClassDef) and node.name in wanted:
            selected_nodes.append(node)

    module = ast.Module(body=selected_nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace = {
        "BaseModel": BaseModel,
        "ConfigDict": ConfigDict,
        "Optional": Optional,
        "Dict": Dict,
        "Any": Any,
        "IntentPayloadV2": _IntentPayloadV2,
        "NormalizedIntent": _NormalizedIntent,
        "run_rag_ab_compare": run_rag_impl,
        "_extract_allowed_render_fields": lambda payload: {},
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


def test_apply_title_preference_uses_payload_title_text_when_mapper_title_unknown() -> None:
    symbols = _load_server3_symbols(run_rag_impl=lambda **_: {"M": SimpleNamespace(reranked_hits=[], context="")})
    apply_title = symbols["_apply_title_preference"]

    mapped_doc = {
        "title": "과제명 미상",
        "title_text": "AI 기반 해양 예측 플랫폼 개발",
        "title1": None,
        "title2": None,
    }

    apply_title(mapped_doc)

    assert mapped_doc["title"] == "AI 기반 해양 예측 플랫폼 개발"
    assert mapped_doc["title"] != "과제명 미상"


def test_retriever_copies_raw_title_fields_and_title_preference_fallback_works() -> None:
    hit_payload = {
        "tag": "PROJECT",
        "title_text": "원본 페이로드 제목",
        "title1": "대체 제목 1",
        "title2": "대체 제목 2",
        "meta_basic": {"kor_pjt_nm": ""},
        "meta_detail": {},
        "prtcp_mp": [],
    }

    def _run_rag_ab_compare_stub(**_: Any):
        return {
            "M": SimpleNamespace(
                reranked_hits=[SimpleNamespace(payload=hit_payload)],
                context=" sample context ",
            )
        }

    symbols = _load_server3_symbols(run_rag_impl=_run_rag_ab_compare_stub)
    retriever_cls = symbols["CustomRAGRetriever"]
    apply_title = symbols["_apply_title_preference"]

    retriever = retriever_cls(top_k=1)
    result = retriever.retrieve("질문")

    assert result["documents"], "문서가 생성되어야 합니다."
    rag_doc = result["documents"][0]

    assert rag_doc["title_text"] == "원본 페이로드 제목"
    assert rag_doc["title1"] == "대체 제목 1"
    assert rag_doc["title2"] == "대체 제목 2"

    mapped_doc = dict(rag_doc)
    mapped_doc["title"] = "과제명 미상"
    apply_title(mapped_doc)

    assert mapped_doc["title"] == "원본 페이로드 제목"
    assert mapped_doc["title"] != "과제명 미상"
