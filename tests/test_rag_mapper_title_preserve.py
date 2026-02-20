from rag_mapper.mapping_config import DataTag
from rag_mapper.rag_mapper import RagMapper


def test_map_preserves_existing_title_when_project_name_missing() -> None:
    item = {
        "tag": DataTag.PROJECT.value,
        "title": "기존 과제 제목",
        "meta_basic": {
            "pjt_id": "PJT-001",
            "kor_pjt_nm": "",
            "stan_yr": "2024",
        },
        "meta_detail": {},
        "prtcp_mp": [],
    }

    mapped = RagMapper.map(item)

    assert mapped["title"] == "기존 과제 제목"


def test_map_generates_title_when_existing_title_is_invalid() -> None:
    item = {
        "tag": DataTag.PROJECT.value,
        "title": "   ",
        "meta_basic": {
            "pjt_id": "PJT-002",
            "kor_pjt_nm": "해양 AI 플랫폼",
            "stan_yr": "2025",
        },
        "meta_detail": {},
        "prtcp_mp": [],
    }

    mapped = RagMapper.map(item)

    assert mapped["title"] == "해양 AI 플랫폼(2025)"


def test_map_preserves_existing_title_for_project_and_outcome_tags() -> None:
    cases = [
        (
            DataTag.PROJECT.value,
            {
                "meta_basic": {"pjt_id": "PJT-003", "kor_pjt_nm": "", "stan_yr": "2023"},
                "meta_detail": {},
                "prtcp_mp": [{"hm_nm": "홍길동", "blng_org_nm": "A기관"}],
            },
        ),
        (
            DataTag.REPORT.value,
            {
                "meta_basic": {"rst_id": "RPT-001", "kor_rpt_title_nm": ""},
                "meta_detail": {},
                "prtcp_mp": [{"hm_nm": "김연구", "blng_org_nm": "B기관"}],
            },
        ),
        (
            DataTag.PAPER.value,
            {
                "meta_basic": {"rst_id": "PAP-001", "paper_nm": ""},
                "meta_detail": {},
                "prtcp_mp": [{"hm_nm": "이과학", "blng_org_nm": "C기관"}],
            },
        ),
    ]

    for tag, fields in cases:
        item = {
            "tag": tag,
            "title": "입력 title 유지",
            **fields,
        }

        mapped = RagMapper.map(item)

        assert mapped["title"] == "입력 title 유지"
