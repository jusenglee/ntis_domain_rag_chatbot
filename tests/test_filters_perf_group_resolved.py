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
