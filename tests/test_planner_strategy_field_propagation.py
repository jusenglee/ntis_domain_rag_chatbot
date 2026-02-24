from __future__ import annotations

import ast
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.pipeline_steps import normalize_intent
from rag_parts.query_intent import classify_query
from schemas import ExecutionContext


def _load_apply_planner_v2_bundle() -> Dict[str, Any]:
    source = Path("server3.py").read_text(encoding="utf-8")
    tree = ast.parse(source, filename="server3.py")

    wanted = {"_normalize_hint_terms", "_collect_researcher_name_terms", "apply_planner_v2"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]

    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)

    namespace: Dict[str, Any] = {
        "Any": Any,
        "Optional": Optional,
        "Dict": Dict,
        "replace": replace,
        "normalize_org_terms": lambda values: values if isinstance(values, list) else ([values] if values else []),
        "QuestionAnalysis": object,
    }
    exec(compile(module, filename="server3.py", mode="exec"), namespace)
    return namespace


@dataclass(frozen=True)
class _IntentStub:
    base_route: str = "project"
    action: str = "topic"
    mode: Optional[str] = None
    relation: Optional[tuple[str, str]] = None
    join_key_mode: Optional[str] = None
    ids_map: Dict[str, list[str]] = None
    planner_limit: int = 20
    retrieval_query: Optional[str] = None
    planner_confidence: Optional[float] = None
    org_role: Optional[str] = None
    org_terms: list[str] = None
    people_terms: list[str] = None
    lead_org_terms: list[str] = None
    participant_org_terms: list[str] = None
    people_affiliation_org_terms: list[str] = None
    year_from: Optional[str] = None
    year_to: Optional[str] = None
    years: list[str] = None
    perf_types: list[str] = None
    keywords: list[str] = None
    title: list[str] = None
    target_cols: list[str] = None
    wants_rank: bool = False

    def __post_init__(self):
        object.__setattr__(self, "ids_map", self.ids_map or {})
        object.__setattr__(self, "org_terms", self.org_terms or [])
        object.__setattr__(self, "people_terms", self.people_terms or [])
        object.__setattr__(self, "lead_org_terms", self.lead_org_terms or [])
        object.__setattr__(self, "participant_org_terms", self.participant_org_terms or [])
        object.__setattr__(self, "people_affiliation_org_terms", self.people_affiliation_org_terms or [])
        object.__setattr__(self, "years", self.years or [])
        object.__setattr__(self, "perf_types", self.perf_types or [])
        object.__setattr__(self, "keywords", self.keywords or [])
        object.__setattr__(self, "title", self.title or [])
        object.__setattr__(self, "target_cols", self.target_cols or [])


def test_apply_planner_v2_propagates_strategy_fields_and_merges_ids_map() -> None:
    ns = _load_apply_planner_v2_bundle()
    fn = ns["apply_planner_v2"]

    intent = _IntentStub(ids_map={"pjt_no": ["OLD-1"], "id_exact": ["KEEP"]}, target_cols=["project"], keywords=["기존"])
    qa = SimpleNamespace(
        confidence=0.9,
        head="perf",
        action="list",
        mode="lookup",
        relation="perf_project",
        join_key_mode="group",
        ids_map={"pjt_no": ["NEW-2"], "person_no": ["P-1"]},
        limit=7,
        retrieval_query="rq",
        target_cols=["perf"],
        filters={
            "keywords": ["빅데이터"],
            "participant_researcher_name": ["홍길동"],
        },
    )

    patched, applied = fn(intent, qa)
    assert applied is True
    assert patched.mode == "lookup"
    assert patched.target_cols == ["perf"]
    assert patched.keywords == ["빅데이터"]
    assert patched.people_terms == ["홍길동"]
    assert patched.ids_map["id_exact"] == ["KEEP"]
    assert patched.ids_map["pjt_no"] == ["NEW-2"]
    assert patched.ids_map["person_no"] == ["P-1"]


def test_normalize_intent_uses_keywords_argument_as_fallback() -> None:
    intent = SimpleNamespace(
        action="topic",
        base_route="project",
        relation=None,
        is_id_query=False,
        keywords=[],
        ids_map={},
    )
    out = normalize_intent(intent, query="빅데이터 과제", keywords=["빅데이터"])
    assert out.keywords == ["빅데이터"]


def test_execution_context_from_intent_carries_mode() -> None:
    normalized = normalize_intent(
        SimpleNamespace(action="topic", base_route="project", relation=None, is_id_query=False, mode="search", ids_map={}),
        query="테스트",
        keywords=[],
    )
    ctx = ExecutionContext.from_intent(normalized)
    assert ctx.mode == "search"


def test_affiliation_query_without_person_name_keeps_people_terms_empty() -> None:
    intent = classify_query("한국과학기술정보연구원 소속 연구자", ["한국과학기술정보연구원", "소속", "연구자"])
    assert intent.org_role == "affiliation"
    assert intent.people_terms == []
    assert intent.people_affiliation_org_terms
