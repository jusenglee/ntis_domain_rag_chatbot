from __future__ import annotations

from types import SimpleNamespace

import rag_parts.filters as filters


class _DummyFieldCondition:
    def __init__(self, *, key, match):
        self.key = key
        self.match = match


def test_build_perf_filter_group_resolved_builds_or_filter(monkeypatch):
    dummy_qmodels = SimpleNamespace(
        FieldCondition=_DummyFieldCondition,
        Filter=lambda **kwargs: SimpleNamespace(**kwargs),
        MatchValue=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)
    monkeypatch.setattr(filters, "make_match_any", lambda values: {"any": list(values)})
    monkeypatch.setattr(filters, "pick_perf_tag_filters", lambda _query: [])

    out = filters.build_perf_filter_group_resolved(
        pjt_nos=["PNO-1"],
        pjt_ids=["202300001234"],
        strategy="or_both",
        query="",
    )

    assert len(out.must) == 1
    join_any = out.must[0]
    assert join_any.min_should in (1, {"min_count": 1}, {"count": 1})
    assert len(join_any.should) == 2
    assert [cond.key for cond in join_any.should[0].should] == ["pjt_no"]
    assert [cond.key for cond in join_any.should[1].should] == ["pjt_id"]


def test_build_perf_filter_group_resolved_strategy_order(monkeypatch):
    dummy_qmodels = SimpleNamespace(
        FieldCondition=_DummyFieldCondition,
        Filter=lambda **kwargs: SimpleNamespace(**kwargs),
        MatchValue=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)
    monkeypatch.setattr(filters, "make_match_any", lambda values: {"any": list(values)})
    monkeypatch.setattr(filters, "pick_perf_tag_filters", lambda _query: [])

    out = filters.build_perf_filter_group_resolved(
        pjt_nos=["PNO-1"],
        pjt_ids=["202300001234"],
        strategy="prefer_pjt_id",
        query="",
    )

    join_any = out.must[0]
    assert [cond.key for cond in join_any.should[0].should] == ["pjt_id"]
    assert [cond.key for cond in join_any.should[1].should] == ["pjt_no"]


def test_build_perf_filter_group_resolved_uses_alias_project_keys(monkeypatch):
    dummy_qmodels = SimpleNamespace(
        FieldCondition=_DummyFieldCondition,
        Filter=lambda **kwargs: SimpleNamespace(**kwargs),
        MatchValue=lambda **kwargs: SimpleNamespace(**kwargs),
    )

    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)
    monkeypatch.setattr(filters, "make_match_any", lambda values: {"any": list(values)})
    monkeypatch.setattr(filters, "pick_perf_tag_filters", lambda _query: [])
    monkeypatch.setattr(
        filters,
        "_project_key_candidates",
        lambda kind: [kind, f"meta_basic.{kind}"] if kind in {"pjt_id", "pjt_no"} else [kind],
    )

    out = filters.build_perf_filter_group_resolved(
        pjt_nos=["PNO-1"],
        pjt_ids=["202300001234"],
        strategy="or_both",
        query="",
    )

    join_any = out.must[0]
    pjt_no_keys = [cond.key for cond in join_any.should[0].should]
    pjt_id_keys = [cond.key for cond in join_any.should[1].should]
    assert pjt_no_keys == ["pjt_no", "meta_basic.pjt_no"]
    assert pjt_id_keys == ["pjt_id", "meta_basic.pjt_id"]


def test_build_collection_join_filter_group_allows_pjt_id_fallback_when_pjt_no_missing(monkeypatch):
    called = {}

    def _fake_build_perf_filter_by_pjt_id(join_ids, query, apply_query_tag_inference=False):
        called["join_ids"] = list(join_ids)
        called["query"] = query
        called["apply_query_tag_inference"] = apply_query_tag_inference
        return SimpleNamespace(must=["pjt_id_fallback"])

    def _should_not_call(*_args, **_kwargs):
        raise AssertionError("pjt_no가 없으면 group_resolved는 호출되면 안됩니다")

    monkeypatch.setattr(filters, "build_perf_filter_by_pjt_id", _fake_build_perf_filter_by_pjt_id)
    monkeypatch.setattr(filters, "build_perf_filter_group_resolved", _should_not_call)

    out = filters.build_collection_join_filter(
        hop2_col="ntis_perf",
        join_key_mode="group",
        join_ids=[],
        pjt_nos=[],
        resolved_pjt_ids=["202300001234", "202300001235"],
        query="fallback",
    )

    assert called["join_ids"] == ["202300001234", "202300001235"]
    assert called["query"] == "fallback"
    assert called["apply_query_tag_inference"] is False
    assert out.must == ["pjt_id_fallback"]
