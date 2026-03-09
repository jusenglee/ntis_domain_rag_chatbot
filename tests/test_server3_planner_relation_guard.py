from __future__ import annotations

import ast
from pathlib import Path


def _get_question_analysis_source_segment() -> str:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "QuestionAnalysisV2":
            return ast.get_source_segment(source, node) or ""
    raise AssertionError("QuestionAnalysisV2 class not found")


def test_question_analysis_v2_declares_forbidden_people_org_relation_code() -> None:
    segment = _get_question_analysis_source_segment()
    assert "PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG" in segment
    assert "_FORBIDDEN_PEOPLE_ORG_RELATIONS" in segment


def test_question_analysis_v2_relation_allowlist_is_project_perf_only() -> None:
    segment = _get_question_analysis_source_segment()
    assert '"project_perf"' in segment
    assert '"perf_project"' in segment
    assert "PLANNER_RELATION_INVALID" in segment
