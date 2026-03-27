from types import SimpleNamespace
from apps.api.services.context_helpers import resolve_title_from_payload
from apps.api.services.detail_contract import build_detail_answer_context, compute_detail_coverage, make_entity_cache_key, render_detail_answer
from apps.api.services.view_state import FocusEntity, build_display_snapshot, focus_entity_from_detail
from apps.core.canonical_evidence import build_canonical_evidence


def test_render_detail_answer_uses_korean_labels_for_project_profile():
    coverage = compute_detail_coverage(
        {
            "title": "\ucc28\uc138\ub300 \ubc18\ub3c4\uccb4 \uacfc\uc81c",
            "pjt_id": "PJT-123",
            "pjt_no": "NO-456",
            "stan_yr": "2025",
            "org_nm": "\ud55c\uad6d\uc804\uc790\uc5f0\uad6c\uc6d0",
            "prtcp_mp": [{"hm_nm": "\ud64d\uae38\ub3d9"}],
        },
        anchor=FocusEntity(kind="project", pjt_id="PJT-123", title_text="\ucc28\uc138\ub300 \ubc18\ub3c4\uccb4 \uacfc\uc81c", source="detail_lookup"),
    )

    answer = render_detail_answer(coverage)

    assert "\uacfc\uc81c: \ucc28\uc138\ub300 \ubc18\ub3c4\uccb4 \uacfc\uc81c" in answer
    assert "\uacfc\uc81c ID: PJT-123" in answer
    assert "\uacfc\uc81c \ubc88\ud638: NO-456" in answer
    assert "\uc5f0\ub3c4: 2025" in answer
    assert "\uc218\ud589\uae30\uad00: \ud55c\uad6d\uc804\uc790\uc5f0\uad6c\uc6d0" in answer
    assert "\uc5f0\uad6c\uc790: \ud64d\uae38\ub3d9" in answer
    assert "\uc694\uccad\ud558\uc2e0 \uc0c1\uc138 \ud56d\ubaa9" in answer
    assert "YEAR:" not in answer
    assert "LEAD_ORG:" not in answer


def test_render_detail_answer_uses_korean_labels_for_perf_rich_detail():
    coverage = compute_detail_coverage(
        {
            "title": "\ubc18\ub3c4\uccb4 \ub17c\ubb38",
            "rst_id": "RST-1",
            "doi": "10.1234/example",
            "stan_yr": "2024",
            "summary": "\ud575\uc2ec \uc131\uacfc \uc694\uc57d",
            "meta_detail": {
                "perf_type": "paper",
                "outputs": [{"name": "SCI \ub17c\ubb38"}],
            },
        },
        anchor=FocusEntity(kind="perf", rst_id="RST-1", title_text="\ubc18\ub3c4\uccb4 \ub17c\ubb38", source="detail_lookup"),
    )

    answer = render_detail_answer(coverage)

    assert "\uc131\uacfc: \ubc18\ub3c4\uccb4 \ub17c\ubb38" in answer
    assert "\uc131\uacfc ID: RST-1" in answer
    assert "DOI: 10.1234/example" in answer
    assert "\uc5f0\ub3c4: 2024" in answer
    assert "\uc694\uc57d: \ud575\uc2ec \uc131\uacfc \uc694\uc57d" in answer
    assert "\uc131\uacfc \uc720\ud615: paper" in answer
    assert "\uc131\uacfc\ubb3c: SCI \ub17c\ubb38" in answer
    assert "OUTPUTS:" not in answer


def test_render_detail_answer_uses_korean_labels_for_org_detail():
    coverage = compute_detail_coverage(
        {
            "title": "\ud55c\uad6d\uc804\uc790\uc5f0\uad6c\uc6d0",
            "org_id": "ORG-77",
            "org_code": "ORG-CODE",
            "biz_no": "123-45-67890",
            "meta_detail": {
                "affiliation": "\ub300\uc804",
            },
        },
        anchor=FocusEntity(kind="org", org_id="ORG-77", title_text="\ud55c\uad6d\uc804\uc790\uc5f0\uad6c\uc6d0", source="detail_lookup"),
    )

    answer = render_detail_answer(coverage)

    assert "\uae30\uad00: \ud55c\uad6d\uc804\uc790\uc5f0\uad6c\uc6d0" in answer
    assert "\uae30\uad00 ID: ORG-77" in answer
    assert "\uae30\uad00 \ucf54\ub4dc: ORG-CODE" in answer
    assert "\uc0ac\uc5c5\uc790\ub4f1\ub85d\ubc88\ud638: 123-45-67890" in answer
    assert "\uc18c\uc18d\uae30\uad00: \ub300\uc804" in answer


def test_make_entity_cache_key_tolerates_partial_anchor_objects():
    partial_anchor = SimpleNamespace(kind="project", pjt_id="PJT-123", title_text="Project Alpha")

    assert make_entity_cache_key(partial_anchor) == "project:pjt_id:PJT-123"


def test_make_entity_cache_key_falls_back_to_title_for_partial_anchor():
    partial_anchor = SimpleNamespace(kind="org", title_text="Org Alpha")

    assert make_entity_cache_key(partial_anchor) == "org:title:Org Alpha"



def test_compute_detail_coverage_prefers_hydrated_profile_fields_over_anchor_fallbacks():
    coverage = compute_detail_coverage(
        {
            "title": "Hydrated Project",
            "pjt_id": "PJT-123",
            "stan_yr": "2025",
            "org_nm": "Hydrated Org",
            "prtcp_mp": [{"hm_nm": "Hydrated Researcher"}],
        },
        anchor=FocusEntity(
            kind="project",
            source="display_snapshot",
            pjt_id="PJT-123",
            title_text="Anchor Project",
            year=2024,
            lead_org="Anchor Org",
            researchers=["Anchor Researcher"],
        ),
    )

    assert coverage.core_profile["title"] == "Anchor Project"
    assert coverage.core_profile["year"] == "2025"
    assert coverage.core_profile["lead_org"] == "Hydrated Org"
    assert coverage.core_profile["researchers"] == ["Hydrated Researcher"]


def test_compute_detail_coverage_uses_anchor_profile_fallback_when_doc_is_thin():
    coverage = compute_detail_coverage(
        {"pjt_id": "PJT-123"},
        anchor=FocusEntity(
            kind="project",
            source="display_snapshot",
            pjt_id="PJT-123",
            title_text="Anchor Project",
            year=2024,
            lead_org="Anchor Org",
            participant_org=["Anchor Partner"],
            researchers=["Anchor Researcher"],
        ),
    )

    assert coverage.core_profile["title"] == "Anchor Project"
    assert coverage.core_profile["year"] == "2024"
    assert coverage.core_profile["lead_org"] == "Anchor Org"
    assert coverage.core_profile["participant_org"] == ["Anchor Partner"]
    assert coverage.core_profile["researchers"] == ["Anchor Researcher"]


def test_make_entity_cache_key_prefers_project_when_anchor_kind_is_wrong():
    partial_anchor = SimpleNamespace(kind="people", pjt_id="PJT-123", title_text="Project Alpha")

    assert make_entity_cache_key(partial_anchor) == "project:pjt_id:PJT-123"


def test_build_detail_answer_context_renders_structured_evidence():
    coverage = compute_detail_coverage(
        {
            "title": "??? ??? ??",
            "pjt_id": "PJT-123",
            "pjt_no": "NO-456",
            "stan_yr": "2025",
            "org_nm": "???????",
            "prtcp_mp": [{"hm_nm": "???"}],
            "meta_detail": {"budget": "100??", "goal": "??? ???"},
        },
        anchor=FocusEntity(kind="project", pjt_id="PJT-123", title_text="??? ??? ??", source="detail_lookup"),
    )

    context = build_detail_answer_context(coverage, requested_fields={"researchers", "budget"})

    assert "[detail_evidence]" in context
    assert "entity_kind: project" in context
    assert "requested_fields: budget, researchers" in context
    assert "pjt_id: PJT-123" in context
    assert "researchers: ???" in context
    assert "budget: 100??" in context


def test_build_display_snapshot_prefers_canonical_project_title_over_hit_title():
    snapshot = build_display_snapshot(
        conversation_id="cid",
        turn_id="tid",
        context_kind="project",
        requested_count=1,
        documents=[
            {
                "title": "1. 김봉준",
                "source_type": "hit",
                "pjt_id": "1711135956",
                "pjt_no": "2021R1F1A1057134",
            }
        ],
        canonical_evidence=[
            {
                "ids": {"pjt_id": "1711135956", "pjt_no": "2021R1F1A1057134"},
                "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발"},
            }
        ],
        raw_count=1,
    )

    assert snapshot.items[0].title_text == "단일 반도체물질 기반 3진 논리 게이트 개발"


def test_build_display_snapshot_items_path_prefers_canonical_project_title():
    snapshot = build_display_snapshot(
        conversation_id="cid",
        turn_id="tid",
        context_kind="project",
        requested_count=1,
        items=[
            SimpleNamespace(
                display={
                    "title": "1. 김봉준",
                    "source_type": "hit",
                    "pjt_id": "1711135956",
                    "pjt_no": "2021R1F1A1057134",
                },
                canonical={
                    "context_kind": "project",
                    "source_type": "hit",
                    "ids": {"pjt_id": "1711135956", "pjt_no": "2021R1F1A1057134"},
                    "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발"},
                    "roles": {},
                },
            )
        ],
        raw_count=1,
    )

    assert snapshot.items[0].title_text == "단일 반도체물질 기반 3진 논리 게이트 개발"


def test_focus_entity_from_detail_prefers_canonical_project_title_before_anchor_coverage():
    focus = focus_entity_from_detail(
        context_kind="project",
        document={
            "title": "1. 김봉준",
            "source_type": "hit",
            "pjt_id": "1711135956",
            "pjt_no": "2021R1F1A1057134",
            "org_nm": "숙명여자대학",
            "prtcp_mp": [{"hm_nm": "김봉준"}],
        },
        canonical_item={
            "ids": {"pjt_id": "1711135956", "pjt_no": "2021R1F1A1057134"},
            "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발", "year": 2021},
            "roles": {"lead_org_name": ["숙명여자대학"], "participant_researcher_name": ["김봉준"]},
        },
        source="detail_lookup",
    )

    assert focus is not None
    assert focus.title_text == "단일 반도체물질 기반 3진 논리 게이트 개발"

    coverage = compute_detail_coverage({"pjt_id": "1711135956"}, anchor=focus)

    assert coverage.core_profile["title"] == "단일 반도체물질 기반 3진 논리 게이트 개발"


def test_focus_entity_from_detail_prefers_raw_title1_over_bilingual_title_text():
    focus = focus_entity_from_detail(
        context_kind="project",
        document={
            "title": "1. 김봉준",
            "title_text": "단일 반도체물질 기반 3진 논리 게이트 개발 Development of ternary logic gates using a single semiconducting material",
            "title1": "단일 반도체물질 기반 3진 논리 게이트 개발",
            "title2": "Development of ternary logic gates using a single semiconducting material",
            "source_type": "hit",
            "pjt_id": "1711135956",
            "pjt_no": "2021R1F1A1057134",
        },
        canonical_item={
            "ids": {"pjt_id": "1711135956", "pjt_no": "2021R1F1A1057134"},
            "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발 Development of ternary logic gates using a single semiconducting material"},
            "roles": {},
        },
        source="detail_lookup",
    )

    assert focus is not None
    assert focus.title_text == "단일 반도체물질 기반 3진 논리 게이트 개발"


def test_build_display_snapshot_keeps_wrapper_title_for_synthetic_rows():
    snapshot = build_display_snapshot(
        conversation_id="cid",
        turn_id="tid",
        context_kind="project",
        requested_count=1,
        documents=[
            {
                "title": "1. project wrapper",
                "source_type": "aggregation",
                "pjt_id": "1711135956",
            }
        ],
        canonical_evidence=[
            {
                "ids": {"pjt_id": "1711135956"},
                "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발"},
            }
        ],
        raw_count=1,
    )

    assert snapshot.items[0].title_text == "1. project wrapper"


def test_compute_detail_coverage_prefers_hydrated_project_title_over_polluted_anchor_label():
    coverage = compute_detail_coverage(
        {
            "title": "1. Kim",
            "title_text": "1. Kim",
            "title1": "Canonical Project Title",
            "pjt_id": "PJT-123",
        },
        anchor=FocusEntity(
            kind="project",
            source="detail_lookup",
            pjt_id="PJT-123",
            title_text="1. Kim",
        ),
    )

    assert coverage.core_profile["title"] == "Canonical Project Title"


def test_compute_detail_coverage_hydrates_ntis_project_fields_from_meta_basic():
    coverage = compute_detail_coverage(
        {
            "title1": "Canonical Project Title",
            "pjt_id": "PJT-123",
            "meta_basic": {
                "rsch_goal_abstract": "Build a stable ternary inverter.",
                "rsch_abstract": "This project summarizes the selective doping approach.",
                "tot_rsch_start_dt": "2021-06-01",
                "tot_rsch_end_dt": "2022-05-31",
                "rndco_tot_amt": "48400000",
            },
        },
        anchor=FocusEntity(kind="project", source="detail_lookup", pjt_id="PJT-123", title_text="1. Kim"),
    )

    assert coverage.core_profile["title"] == "Canonical Project Title"
    assert coverage.rich_detail["summary"] == "This project summarizes the selective doping approach."
    assert coverage.rich_detail["goal"] == "Build a stable ternary inverter."
    assert coverage.rich_detail["period"] == "2021-06-01 ~ 2022-05-31"
    assert coverage.rich_detail["budget"] == "48400000"


def test_resolve_title_from_payload_prefers_title1_over_title_text():
    assert resolve_title_from_payload(
        {
            "title_text": "1. Kim",
            "title1": "Canonical Project Title",
            "title2": "Canonical English Title",
        }
    ) == "Canonical Project Title"


def test_build_canonical_evidence_prefers_title1_over_polluted_title_text():
    evidence = build_canonical_evidence(
        {
            "title_text": "1. Kim",
            "title1": "Canonical Project Title",
            "title2": "Canonical English Title",
            "pjt_id": "PJT-123",
        },
        rank=1,
        base_route="project",
        output_type="detail",
    ).to_dict()

    assert evidence["facts"]["title"] == "Canonical Project Title"


def test_build_display_snapshot_prefers_raw_title1_over_bilingual_title_text():
    snapshot = build_display_snapshot(
        conversation_id="cid",
        turn_id="tid",
        context_kind="project",
        requested_count=1,
        documents=[
            {
                "title": "1. 김봉준",
                "title_text": "단일 반도체물질 기반 3진 논리 게이트 개발 Development of ternary logic gates using a single semiconducting material",
                "title1": "단일 반도체물질 기반 3진 논리 게이트 개발",
                "title2": "Development of ternary logic gates using a single semiconducting material",
                "source_type": "hit",
                "pjt_id": "1711135956",
                "pjt_no": "2021R1F1A1057134",
            }
        ],
        canonical_evidence=[
            {
                "ids": {"pjt_id": "1711135956", "pjt_no": "2021R1F1A1057134"},
                "facts": {"title": "단일 반도체물질 기반 3진 논리 게이트 개발 Development of ternary logic gates using a single semiconducting material"},
            }
        ],
        raw_count=1,
    )

    assert snapshot.items[0].title_text == "단일 반도체물질 기반 3진 논리 게이트 개발"
