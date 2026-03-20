from __future__ import annotations

from pathlib import Path

from apps.core.rag_executor_support import build_executor_support


class Point:
    def __init__(self, *, payload=None, point_id=None):
        self.payload = payload if payload is not None else {}
        self.id = point_id


class Dumpable:
    def model_dump(self, exclude_none=True):
        return {"must": [{"key": "tag", "match": {"value": "NTIS"}}]}


def test_get_meta_merges_basic_and_detail():
    support = build_executor_support()
    meta = support.get_meta_fn({"meta_basic": {"a": 1}, "meta_detail": {"b": 2}})
    assert meta == {"a": 1, "b": 2}


def test_payload_title_prefers_payload_then_meta():
    support = build_executor_support()
    assert support.payload_title_fn({"title_text": "payload title"}, {"kor_pjt_nm": "meta title"}) == "payload title"
    assert support.payload_title_fn({}, {"kor_pjt_nm": "meta title"}) == "meta title"


def test_count_missing_join_keys_reports_missing_and_invalid_stats():
    support = build_executor_support()
    points = [
        Point(payload={"pjt_id": "", "pjt_no": "", "tag": ""}),
        Point(payload={"pjt_id": "bad id", "pjt_no": "A123", "tag": "NTIS"}),
        Point(payload={"pjt_id": "12345", "pjt_no": "12345", "tag": "NTIS"}),
    ]
    stats = support.count_missing_join_keys_fn(points, join_key_mode="instance")
    assert stats["total"] == 3
    assert stats["missing_pjt_any"] == 1
    assert stats["missing_tag"] == 1
    assert stats["same_id_no"] == 1
    assert stats["invalid_pjt_id"] == 0


def test_resolve_group_pjt_ids_uses_payload_meta_and_point_id_candidates():
    support = build_executor_support()
    points = [
        Point(payload={"meta_basic": {"pjt_id": "bad id"}}, point_id="also bad"),
        Point(payload={"meta_basic": {"pjt_id": "12345"}}, point_id="ignored"),
        Point(payload={"pjt_id": "67890"}, point_id="ignored2"),
    ]
    assert support.resolve_group_pjt_ids_fn(points, max_ids=2) == ["bad id", "12345"]


def test_serialize_filter_for_log_handles_dumpable_objects():
    support = build_executor_support()
    serialized = support.serialize_filter_for_log_fn(Dumpable())
    assert serialized == {"must": [{"key": "tag", "match": {"value": "NTIS"}}]}


def test_rag_pipeline_uses_executor_support_module():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "build_executor_support(" in source
    assert "def _get_meta(" not in source
    assert "def _count_missing_join_keys(" not in source
    assert "def _resolve_group_pjt_ids(" not in source
    assert "def _payload_title(" not in source
    assert "def _serialize_filter_for_log(" not in source
