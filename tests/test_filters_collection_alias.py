from __future__ import annotations

from types import SimpleNamespace

import rag_parts.filters as filters


def test_build_collection_join_filter_uses_perf_filter_for_aliases(monkeypatch):
    calls = []

    def _fake_perf_by_id(join_ids, query=""):
        calls.append(("id", list(join_ids), query))
        return "perf-by-id"

    def _fake_perf_by_no(pjt_nos, query=""):
        calls.append(("no", list(pjt_nos), query))
        return "perf-by-no"

    monkeypatch.setattr(filters, "build_perf_filter_by_pjt_id", _fake_perf_by_id)
    monkeypatch.setattr(filters, "build_perf_filter_by_pjt_no", _fake_perf_by_no)

    perf_aliases = [
        "ntis_perf",
        "ntis_perf_v1",
        "NTIS_PERF_V2",
        filters.COL_PERF,
    ]

    for alias in perf_aliases:
        out = filters.build_collection_join_filter(
            hop2_col=alias,
            join_key_mode="instance",
            join_ids=["202300001234"],
            pjt_nos=[],
            query="q",
        )
        assert out == "perf-by-id"

    for alias in perf_aliases:
        out = filters.build_collection_join_filter(
            hop2_col=alias,
            join_key_mode="group",
            join_ids=[],
            pjt_nos=["PNO-001"],
            query="q2",
        )
        assert out == "perf-by-no"

    assert any(call[0] == "id" for call in calls)
    assert any(call[0] == "no" for call in calls)


def test_build_collection_join_filter_uses_project_filter_for_alias(monkeypatch):
    dummy_qmodels = SimpleNamespace(Filter=lambda **kwargs: {"kind": "filter", **kwargs})
    monkeypatch.setattr(filters, "qmodels", dummy_qmodels)
    monkeypatch.setattr(filters, "build_project_id_filter", lambda *_args, **_kwargs: {"kind": "project"})

    out = filters.build_collection_join_filter(
        hop2_col="ntis_project_v1",
        join_key_mode="instance",
        join_ids=["202300001234"],
        pjt_nos=[],
    )
    assert out == {"kind": "project"}
