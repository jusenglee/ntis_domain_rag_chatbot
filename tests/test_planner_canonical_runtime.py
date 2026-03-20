from __future__ import annotations

from types import SimpleNamespace

from apps.api.services.planner_runtime import _planner_prev_context_text, determine_locked_strategy


class LockedLogger:
    def __call__(self, *args, **kwargs):
        return None


def test_determine_locked_strategy_uses_canonical_seed_when_prev_context_empty():
    stage1 = SimpleNamespace(
        action="detail",
        head="project",
        relation_candidate=None,
        referential_followup=False,
        confidence=0.9,
        model_dump=lambda: {
            "action": "detail",
            "head": "project",
            "relation_candidate": None,
            "referential_followup": False,
            "confidence": 0.9,
        },
    )
    normalized_intent = SimpleNamespace(ids_map={})
    locked = determine_locked_strategy(
        stage1=stage1,
        normalized_intent=normalized_intent,
        prev_context=[],
        canonical_evidence=[{"ids": {"pjt_id": "1711015550"}}],
        planner_stage2_regate_seed_allowed_keys={"pjt_id", "pjt_no"},
        log_event=LockedLogger(),
    )

    assert locked.prev_context_seed == {"pjt_id": ["1711015550"]}
    assert locked.mode == "LOOKUP"


def test_planner_prev_context_text_prefers_canonical_evidence():
    text = _planner_prev_context_text(
        prev_context=[],
        canonical_evidence=[
            {
                "identity": "1711015550",
                "ids": {"pjt_id": "1711015550", "pjt_no": "PJT-2020-1234-5678"},
                "facts": {"title": "AI related project", "summary": "project summary"},
                "roles": {"lead_org_name": ["ETRI"]},
            }
        ],
        normalized_intent=SimpleNamespace(output_type="summary", base_route="project"),
    )

    assert "PJT_ID=1711015550" in text
    assert "LEAD_ORG=ETRI" in text
