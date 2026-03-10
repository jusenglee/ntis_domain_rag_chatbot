from __future__ import annotations

import ast
from pathlib import Path


SOURCE = Path("rag_pipeline.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename="rag_pipeline.py")


def _find_sync_function(name: str) -> ast.FunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"함수 {name} 를 찾을 수 없습니다.")


def test_affiliation_role_excludes_people_filter_in_org_gate() -> None:
    node = _find_sync_function("_run_rag_with_vectors")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert 'if org_role == "affiliation":\n            include_people_filter_in_gate = False' in src


def test_filter_miss_probe_logs_doc_title_and_flattened_fields() -> None:
    node = _find_sync_function("_run_rag_with_vectors")
    src = ast.get_source_segment(SOURCE, node) or ""

    assert 'probe_docs: List[Dict[str, Any]] = []' in src
    assert '_payload_get(payload, "prtcp_mp[].hm_nm")' in src
    assert '_payload_get(payload, "prtcp_mp.hm_nm")' in src
    assert '_payload_get(payload, "prtcp_mp[].blng_org_nm")' in src
    assert '_payload_get(payload, "prtcp_mp.blng_org_nm")' in src
    assert '"FILTER_MISS_SUSPECTED"' in src
    assert 'probe_docs=probe_docs' in src
