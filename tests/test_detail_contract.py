from types import SimpleNamespace
from apps.api.services.detail_contract import build_detail_answer_context, compute_detail_coverage, make_entity_cache_key, render_detail_answer
from apps.api.services.view_state import FocusEntity


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
