import ast
from pathlib import Path
from typing import Any
import re


def _load_sanitizer():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target_ids = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if target_ids & {"PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS"}:
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "_sanitize_ids_map_semantics":
            selected.append(node)
    module = ast.Module(body=selected, type_ignores=[])
    ns: dict[str, Any] = {"Any": Any, "re": re}
    exec(compile(module, filename="server3.py", mode="exec"), ns, ns)
    return ns["_sanitize_ids_map_semantics"]


def test_sanitize_ids_map_semantics_removes_invalid_values():
    sanitize = _load_sanitizer()
    cleaned, invalid = sanitize({"pjt_id": ["ETRI", "1711015550"], "doi": ["10.1000/xyz", "bad"]})
    assert cleaned["pjt_id"] == ["1711015550"]
    assert cleaned["doi"] == ["10.1000/xyz"]
    assert any(item["key"] == "pjt_id" for item in invalid)



def test_sanitize_ids_map_semantics_accepts_extended_keys_with_valid_patterns():
    sanitize = _load_sanitizer()
    cleaned, invalid = sanitize(
        {
            "rst_id": ["RPT-2024-0001"],
            "perf_id": ["PERF-AB12"],
            "paper_id": ["PAP-XY99"],
            "person_no": ["1234567890"],
            "biz_no": ["123-45-67890"],
            "org_code": ["ETRI001"],
            "org_id": ["org_system_01"],
        }
    )
    assert invalid == []
    assert cleaned["rst_id"] == ["RPT-2024-0001"]
    assert cleaned["perf_id"] == ["PERF-AB12"]
    assert cleaned["paper_id"] == ["PAP-XY99"]
    assert cleaned["person_no"] == ["1234567890"]
    assert cleaned["biz_no"] == ["123-45-67890"]
    assert cleaned["org_code"] == ["ETRI001"]
    assert cleaned["org_id"] == ["org_system_01"]


def test_sanitize_ids_map_semantics_drops_extended_keys_with_invalid_patterns():
    sanitize = _load_sanitizer()
    cleaned, invalid = sanitize(
        {
            "rst_id": ["RS"],
            "perf_id": ["성과-123"],
            "paper_id": ["1234"],
            "person_no": ["12A456"],
            "biz_no": ["12-345-6789"],
            "org_code": ["etri001"],
            "org_id": ["_ORG001"],
            "unknown_id": ["U-001"],
        }
    )
    assert cleaned == {}
    invalid_pairs = {(item["key"], item["value"]) for item in invalid}
    for expected in {
        ("rst_id", "RS"),
        ("perf_id", "성과-123"),
        ("paper_id", "1234"),
        ("person_no", "12A456"),
        ("biz_no", "12-345-6789"),
        ("org_code", "etri001"),
        ("org_id", "_ORG001"),
        ("unknown_id", "U-001"),
    }:
        assert expected in invalid_pairs



def test_planner_stage2_prompt_ids_map_allowlist_is_synced_with_validator():
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed_keys = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target_ids = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "PLANNER_STAGE2_IDS_MAP_ALLOWED_KEYS" in target_ids:
                allowed_keys = ast.literal_eval(node.value)
                break
    assert isinstance(allowed_keys, set)

    prompt = Path("prompts/planner_stage2_v1.md").read_text(encoding="utf-8")
    for key in sorted(allowed_keys):
        assert key in prompt
