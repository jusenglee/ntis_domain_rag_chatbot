from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional


def _load_target_policy_bundle() -> Dict[str, Any]:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")
    wanted = {"_has_perf_focus_signal", "_default_target_collections_for_route"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Optional": Optional,
        "Any": Any,
        "NormalizedIntent": object,
        "COL_PROJECT": "ntis_project_v1",
        "COL_PERF": "ntis_perf_v1",
        "COL_SUPPORT": "ntis_support_v1",
        "RAG_COLLECTION_ALLOWLIST": [],
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), namespace)
    return namespace


def test_people_route_defaults_project_only_without_perf_signal() -> None:
    ns = _load_target_policy_bundle()
    fn = ns["_default_target_collections_for_route"]

    intent = SimpleNamespace(perf_types=[], keywords=["연구자"], title=[], retrieval_query="홍길동 과제")
    out = fn("people", intent)

    assert out == ["ntis_project_v1"]


def test_people_route_adds_perf_on_perf_signal_keywords_or_perf_types() -> None:
    ns = _load_target_policy_bundle()
    fn = ns["_default_target_collections_for_route"]

    keyword_intent = SimpleNamespace(perf_types=[], keywords=["논문"], title=[], retrieval_query="")
    perf_type_intent = SimpleNamespace(perf_types=["SCI"], keywords=[], title=[], retrieval_query="")

    assert fn("people", keyword_intent) == ["ntis_project_v1", "ntis_perf_v1"]
    assert fn("org", perf_type_intent) == ["ntis_project_v1", "ntis_perf_v1"]
