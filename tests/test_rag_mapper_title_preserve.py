from rag_mapper.mapping_config import DataTag
from rag_mapper.rag_mapper import RagMapper

"""RagMapper의 title 보존/생성 규칙을 고정하는 테스트."""


def test_map_preserves_existing_title_when_project_name_missing() -> None:
    """유효한 기존 title이 있으면 source 필드가 비어 있어도 덮어쓰지 않는다."""
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
    """기존 title이 공백이면 schema formatter로 새 title을 생성해야 한다."""
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
    """project/outcome 계열 tag 전반에서 기존 title 보존 규칙이 유지돼야 한다."""
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
