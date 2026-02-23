from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict


class DummyQdr:
    def __init__(self, payload_by_id: Dict[str, Dict[str, Any]]):
        self.payload_by_id = payload_by_id

    def retrieve(self, *, collection_name: str, ids, with_payload: bool, with_vectors: bool):
        out = []
        for pid in ids:
            key = str(pid)
            if key in self.payload_by_id:
                out.append(SimpleNamespace(id=pid, payload=dict(self.payload_by_id[key])))
        return out


def _load_hydrate() -> Any:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="rag_pipeline.py")

    target = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_hydrate_points_payload"
    )

    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Optional": __import__("typing").Optional,
        "RAG_COLLECTION_ALLOWLIST": ["ntis_perf_v1", "ntis_project_v1"],
        "PROJECT_TAGS_NORM": set(),
        "PERF_TAGS_NORM": set(),
        "COL_PROJECT": "ntis_project_v1",
        "COL_PERF": "ntis_perf_v1",
        "TAG_PJT_MP": "IRD_NAI_PJT_MP",
        "TAG_PJT_ORG": "IRD_NAI_PJT_ORG",
        "TAG_PJT_INFO": "IRD_NAI_PJT_INFO",
        "_normalize_tag_value": lambda x: str(x).strip().upper(),
    }
    exec(compile(module, filename="rag_pipeline.py", mode="exec"), namespace)
    return namespace["_hydrate_points_payload"]


def test_hydrate_keeps_internal_scores():
    hydrate = _load_hydrate()
    point = SimpleNamespace(
        id=1,
        _collection="ntis_perf_v1",
        payload={"_collection": "ntis_perf_v1", "_rrf": 0.12, "_final_total": 0.77, "title": "old"},
    )
    qdr = DummyQdr({"1": {"title": "new", "db_only": "x"}})

    hydrate(qdr, [point])

    assert point.payload["title"] == "new"
    assert point.payload["db_only"] == "x"
    assert point.payload["_rrf"] == 0.12
    assert point.payload["_final_total"] == 0.77
