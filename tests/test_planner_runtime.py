from types import SimpleNamespace

from apps.api.contracts.runtime_contracts import sanitize_ids_map_semantics
from apps.api.services.planner_runtime import assemble_question_analysis


class DummyQA:
    @classmethod
    def model_validate(cls, payload):
        return SimpleNamespace(**payload)


def test_assemble_question_analysis_preserves_ambiguous_project_key_as_deferred_join():
    events = []
    qa = assemble_question_analysis(
        question="과제번호 AI_SEMICONDUCTOR_2023 성과",
        conversation_id="cid",
        request_id="rid",
        stage1=SimpleNamespace(confidence=0.9),
        stage2=SimpleNamespace(
            ids_map={},
            candidate_keys={"project_key": [{"value": "AI_SEMICONDUCTOR_2023", "candidate_types": ["pjt_id", "pjt_no"], "source": "label:과제번호", "confidence": 0.35}]},
            project_key_policy="ambiguous_or",
            join_key_mode=None,
            join_resolution_policy="auto_resolve",
            filters={},
            limit=10,
            retrieval_query="과제번호 AI_SEMICONDUCTOR_2023 성과",
            confidence=0.8,
        ),
        locked_strategy=SimpleNamespace(
            schema_version="v3-staged",
            mode="join",
            head="perf",
            action="list",
            relation="project_perf",
            join_key_mode="instance",
            target_cols=["ntis_project_v1", "ntis_perf_v1"],
            output_type="relation",
        ),
        sanitize_ids_map_semantics=sanitize_ids_map_semantics,
        question_analysis_cls=DummyQA,
        log_event=lambda event, **fields: events.append((event, fields)),
        planner_schema_version="v3-staged",
        max_top_k_size=20,
        planner_stagewise_enabled=True,
        planner_stage1_prompt_version="v1",
        planner_stage2_prompt_version="v1",
    )

    assert qa.mode == "join"
    assert qa.join_key_mode == "deferred"
    assert qa.project_key_policy == "ambiguous_or"
    assert qa.candidate_keys["project_key"][0]["value"] == "AI_SEMICONDUCTOR_2023"
    adjusted_event = next(fields for event, fields in events if event == "PLANNER.ASSEMBLE.ADJUSTED")
    assert adjusted_event["assembly_adjustment_kind"] == "assembly_legalize"
    assert adjusted_event["assembly_adjustment_reason"] == "ambiguous_project_key_deferred_join"
    assert adjusted_event["candidate_project_key_count"] == 1
