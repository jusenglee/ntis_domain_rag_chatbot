from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import rag_parts.filters as filters


@dataclass
class _DummyMatchAny:
    values: list[str]

    def __init__(self, any=None, any_values=None):
        self.values = list(any if any is not None else (any_values or []))


@dataclass
class _DummyMatchValue:
    value: str


@dataclass
class _DummyFieldCondition:
    key: str
    match: object | None = None


class _DummyFilter:
    def __init__(self, *, must=None, should=None, must_not=None, min_should=None, support_min_should=True):
        if (not support_min_should) and (min_should is not None):
            raise TypeError("min_should is not supported")
        self.must = must
        self.should = should
        self.must_not = must_not
        self.min_should = min_should


class _DummyFilterFactory:
    def __init__(self, support_min_should: bool):
        self.support_min_should = support_min_should

    def __call__(self, **kwargs):
        return _DummyFilter(support_min_should=self.support_min_should, **kwargs)


def _has_min_should_or_gate(flt) -> bool:
    min_should = getattr(flt, "min_should", None)
    if isinstance(min_should, dict):
        min_should = min_should.get("min_count", min_should.get("count"))
    if min_should == 1 and getattr(flt, "should", None):
        return True

    must = list(getattr(flt, "must", None) or [])
    if not must:
        return False

    gate = must[0]
    return bool(getattr(gate, "should", None))


@pytest.mark.parametrize("support_min_should", [True, False])
def test_build_project_id_filter_has_should_gate_or_min_should(monkeypatch, support_min_should):
    dummy_qmodels = SimpleNamespace(
        Filter=_DummyFilterFactory(support_min_should=support_min_should),
        FieldCondition=_DummyFieldCondition,
        MatchAny=_DummyMatchAny,
        MatchValue=_DummyMatchValue,
    )
    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)

    out = filters.build_project_id_filter(["202300001234"], [])

    assert out is not None
    assert _has_min_should_or_gate(out)


@pytest.mark.parametrize("support_min_should", [True, False])
def test_build_perf_filter_for_keys_join_any_has_should_gate_or_min_should(monkeypatch, support_min_should):
    dummy_qmodels = SimpleNamespace(
        Filter=_DummyFilterFactory(support_min_should=support_min_should),
        FieldCondition=_DummyFieldCondition,
        MatchAny=_DummyMatchAny,
        MatchValue=_DummyMatchValue,
    )
    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)

    out = filters._build_perf_filter_for_keys(
        join_values=["202300001234"],
        key_cands=["pjt_id", "meta_basic.pjt_id"],
        query="",
    )

    assert out is not None
    assert out.must
    join_any = out.must[0]
    assert _has_min_should_or_gate(join_any)
