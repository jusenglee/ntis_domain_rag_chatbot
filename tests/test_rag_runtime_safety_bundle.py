from __future__ import annotations

from types import SimpleNamespace

from apps.core.rag_runtime_safety import build_multi_hop_bundle_payload


class Point:
    def __init__(self, payload):
        self.payload = payload


def test_build_multi_hop_bundle_payload_collects_projects_and_targets():
    project_point = Point({
        "pjt_id": "1711015550",
        "pjt_no": "PJT-2020-1234-5678",
        "kor_pjt_nm": "AI Forecast",
        "org_nm": "KISTI",
        "stan_yr": "2024",
        "prtcp_org": [{"org_nm": "ETRI", "org_slct_nm": "참여기관"}],
        "prtcp_mp": [{"hm_nm": "홍길동", "blng_org_nm": "KISTI"}],
    })
    paper_point = Point({
        "pjt_id": "1711015550",
        "pjt_no": "PJT-2020-1234-5678",
        "title_text": "AI Forecast Paper",
        "tag": "IRD_NAI_RI_PAPER",
        "year": "2024",
    })
    patent_point = Point({
        "pjt_id": "1711015550",
        "pjt_no": "PJT-2020-1234-5678",
        "title_text": "AI Forecast Patent",
        "tag": "IRD_NAI_RI_IPR",
        "year": "2024",
    })

    payload = build_multi_hop_bundle_payload(
        reranked=[project_point, paper_point, patent_point],
        intent=SimpleNamespace(base_route="project", bundle_kind="project_outputs", bundle_targets=["paper", "patent", "participant_org", "researcher"], guidance_required=True),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
        project_points=[project_point],
        perf_points=[paper_point, patent_point],
        resolved_anchors=SimpleNamespace(ambiguities=("researcher_name_only",)),
    )

    assert payload["status"] == "partial"
    assert payload["bundle_kind"] == "project_outputs"
    assert payload["projects"][0]["pjt_id"] == "1711015550"
    targets = {entry["target_kind"]: entry for entry in payload["bundles"]}
    assert targets["paper"]["items"][0]["perf_title"] == "AI Forecast Paper"
    assert targets["patent"]["items"][0]["perf_title"] == "AI Forecast Patent"
    assert targets["participant_org"]["items"][0]["org_name"] == "ETRI"
    assert targets["researcher"]["items"][0]["researcher_name"] == "홍길동"
