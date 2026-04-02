from types import SimpleNamespace

from apps.evidence.rag_result_assembly import apply_structured_result_constraint


def test_apply_structured_result_constraint_keeps_same_member_match_only():
    reranked = [
        SimpleNamespace(
            payload={
                "prtcp_mp": [
                    {"hm_nm": "신동구", "blng_org_nm": "한국과학기술정보연구원"},
                    {"hm_nm": "김철수", "blng_org_nm": "다른기관"},
                ]
            }
        ),
        SimpleNamespace(
            payload={
                "prtcp_mp": [
                    {"hm_nm": "신동구", "blng_org_nm": "다른기관"},
                    {"hm_nm": "김철수", "blng_org_nm": "한국과학기술정보연구원"},
                ]
            }
        ),
    ]

    filtered, meta = apply_structured_result_constraint(
        reranked,
        people_terms=["신동구"],
        people_ids=[],
        org_terms=[],
        people_org_terms=["한국과학기술정보연구원"],
        org_role="affiliation",
    )

    assert len(filtered) == 1
    assert meta["applied"] is True
    assert meta["output_count"] == 1
    assert meta["dropped_count"] == 1


def test_apply_structured_result_constraint_filters_participant_org_role():
    reranked = [
        SimpleNamespace(payload={"prtcp_org": [{"org_nm": "한국과학기술정보연구원"}]}),
        SimpleNamespace(payload={"org_nm": "한국과학기술정보연구원", "prtcp_org": []}),
    ]

    filtered, meta = apply_structured_result_constraint(
        reranked,
        people_terms=[],
        people_ids=[],
        org_terms=["한국과학기술정보연구원"],
        people_org_terms=[],
        org_role="participant",
    )

    assert len(filtered) == 1
    assert meta["output_count"] == 1
