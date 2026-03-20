from __future__ import annotations

from types import SimpleNamespace

from apps.core.rag_runtime_safety import build_pattern_analysis_payload


class Point:
    def __init__(self, payload):
        self.payload = payload


def test_build_pattern_analysis_payload_coauthor_org_repeat():
    reranked = [
        Point({"title_text": "Paper A", "prtcp_mp": [{"hm_nm": "Kim", "blng_org_nm": "KISTI"}, {"hm_nm": "Lee", "blng_org_nm": "KISTI"}]}),
        Point({"title_text": "Paper B", "prtcp_mp": [{"hm_nm": "Kim", "blng_org_nm": "KISTI"}, {"hm_nm": "Park", "blng_org_nm": "ETRI"}]}),
    ]
    payload = build_pattern_analysis_payload(
        reranked=reranked,
        intent=SimpleNamespace(pattern_kind="coauthor_org_repeat"),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
    )
    assert payload["status"] == "ok"
    assert payload["pattern_kind"] == "coauthor_org_repeat"
    assert payload["items"][0]["org_name"] == "KISTI"
    assert payload["items"][0]["repeated_author_count"] == 2


def test_build_pattern_analysis_payload_perf_mix_gap():
    reranked = [
        Point({"pjt_id": "1711015550", "kor_pjt_nm": "AI Forecast", "tag": "IRD_NAI_RI_PAPER"}),
        Point({"pjt_id": "1711015550", "kor_pjt_nm": "AI Forecast", "tag": "IRD_NAI_RI_PAPER"}),
        Point({"pjt_id": "1711015550", "kor_pjt_nm": "AI Forecast", "tag": "IRD_NAI_RI_REPORT"}),
        Point({"pjt_id": "1711015551", "kor_pjt_nm": "Battery Vision", "tag": "IRD_NAI_RI_PAPER"}),
        Point({"pjt_id": "1711015551", "kor_pjt_nm": "Battery Vision", "tag": "IRD_NAI_RI_IPR"}),
    ]
    payload = build_pattern_analysis_payload(
        reranked=reranked,
        intent=SimpleNamespace(pattern_kind="perf_mix_gap"),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
    )
    assert payload["status"] == "ok"
    assert payload["items"][0]["gap_kind"] == "paper_without_patent"
    assert payload["items"][0]["project_title"] == "AI Forecast"


def test_build_pattern_analysis_payload_series_member_change():
    reranked = [
        Point({"pjt_id": "1711015550", "prtcp_mp": [{"hm_nm": "Kim"}, {"hm_nm": "Lee"}]}),
        Point({"pjt_id": "1711015551", "prtcp_mp": [{"hm_nm": "Kim"}, {"hm_nm": "Park"}]}),
    ]
    payload = build_pattern_analysis_payload(
        reranked=reranked,
        intent=SimpleNamespace(pattern_kind="series_member_change"),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
        series={
            "instance_projects": [
                {"pjt_id": "1711015550", "project_title": "AI Forecast Y1", "year": "2021"},
                {"pjt_id": "1711015551", "project_title": "AI Forecast Y2", "year": "2022"},
            ]
        },
    )
    assert payload["status"] == "ok"
    assert payload["items"][1]["added_members"] == ["Park"]
    assert payload["items"][1]["removed_members"] == ["Lee"]
