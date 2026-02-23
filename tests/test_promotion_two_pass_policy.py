from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.promotion import promote_mode_from_search_hits


class _Hit:
    def __init__(self, payload):
        self.payload = payload


class _Strategy:
    def __init__(self, mode="search", action="list", relation=("project", "perf")):
        self.mode = mode
        self.action = action
        self.relation = relation


def test_promote_search_to_join_when_relation_and_ids_extracted() -> None:
    hits = [_Hit({"meta_basic": {"pjt_id": "1711015550"}})]
    out = promote_mode_from_search_hits(
        current_mode="search",
        search_hits=hits,
        ids_map={},
        planner_strategy=_Strategy(action="relation", relation=("project", "perf")),
    )
    assert out["mode"] == "join"
    assert out["ids_map"].get("pjt_id")


def test_promote_search_to_lookup_for_list_action_with_ids() -> None:
    hits = [_Hit({"pjt_no": "PNO-123"})]
    out = promote_mode_from_search_hits(
        current_mode="search",
        search_hits=hits,
        ids_map={},
        planner_strategy=_Strategy(action="list", relation=None),
    )
    assert out["mode"] == "lookup"
