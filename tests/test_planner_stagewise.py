import asyncio
from types import SimpleNamespace

from apps.api.services.request_facade import build_intent_payload
from apps.core.query_intent import classify_query, extract_people_terms


class Payload:
    def __init__(self, normalized_intent):
        self.normalized_intent = normalized_intent


def test_query_intent_marks_project_output_relation():
    intent = classify_query('PJT-2020-1234-5678 related outputs', [])

    assert intent.relation == ('project', 'perf')
    assert intent.join_key_mode == 'group'
    assert intent.action == 'list'
    assert intent.output_type == 'relation'


def test_query_intent_handles_english_researcher_lookup():
    intent = classify_query('Kim researcher projects', [])

    assert intent.people_terms == ['Kim']
    assert intent.action == 'list'


def test_query_intent_does_not_infer_fake_people_for_topic_query():
    assert extract_people_terms('AI technology trend') == []


def test_build_intent_payload_runs_planner_when_precheck_has_no_signal():
    run_calls = []

    async def fake_run_question_analysis(**kwargs):
        run_calls.append(kwargs['question'])
        return SimpleNamespace(confidence=0.8)

    def fake_apply_planner_v2(intent, qa, **kwargs):
        return ({'intent': intent, 'planner_used': qa is not None}, qa is not None)

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='Explain the overall NTIS domain',
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            request_id='rid',
            cheap_precheck=lambda question: {'years': [], 'people_terms': [], 'org_terms': [], 'perf_tag_filters': [], 'perf_types': [], 'ids_map': {}, 'title_terms': []},
            has_superlative_cue=lambda question: False,
            extract_people_terms=lambda question: [],
            normalize_org_terms=lambda values: values,
            extract_org_terms=lambda question: [],
            extract_org_role=lambda question: None,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: {'raw': question, 'hint': hint},
            normalize_intent=lambda raw_intent, **kwargs: {'normalized': raw_intent},
            run_question_analysis=fake_run_question_analysis,
            apply_planner_v2=fake_apply_planner_v2,
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert run_calls == ['Explain the overall NTIS domain']
    assert question_analysis is not None
    assert payload.normalized_intent['planner_used'] is True


def test_build_intent_payload_skips_planner_for_explicit_id_signal():
    run_calls = []

    async def fake_run_question_analysis(**kwargs):
        run_calls.append(kwargs['question'])
        return SimpleNamespace(confidence=0.8)

    def fake_apply_planner_v2(intent, qa, **kwargs):
        return ({'intent': intent, 'planner_used': qa is not None}, qa is not None)

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='1711015550 project detail',
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            request_id='rid',
            cheap_precheck=lambda question: {'years': [], 'people_terms': [], 'org_terms': [], 'perf_tag_filters': [], 'perf_types': [], 'ids_map': {'pjt_id': ['1711015550']}, 'title_terms': []},
            has_superlative_cue=lambda question: False,
            extract_people_terms=lambda question: [],
            normalize_org_terms=lambda values: values,
            extract_org_terms=lambda question: [],
            extract_org_role=lambda question: None,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: {'raw': question, 'hint': hint},
            normalize_intent=lambda raw_intent, **kwargs: {'normalized': raw_intent},
            run_question_analysis=fake_run_question_analysis,
            apply_planner_v2=fake_apply_planner_v2,
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert run_calls == []
    assert question_analysis is None
    assert payload.normalized_intent['planner_used'] is False


def test_build_intent_payload_keeps_planner_for_non_id_precheck_signals():
    run_calls = []

    async def fake_run_question_analysis(**kwargs):
        run_calls.append(kwargs['question'])
        return SimpleNamespace(confidence=0.8)

    def fake_apply_planner_v2(intent, qa, **kwargs):
        return ({'intent': intent, 'planner_used': qa is not None}, qa is not None)

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='ETRI papers stats 2021 2023',
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            request_id='rid',
            cheap_precheck=lambda question: {'years': ['2021', '2023'], 'people_terms': [], 'org_terms': ['ETRI'], 'perf_tag_filters': ['paper'], 'perf_types': ['paper'], 'ids_map': {}, 'title_terms': []},
            has_superlative_cue=lambda question: False,
            extract_people_terms=lambda question: [],
            normalize_org_terms=lambda values: values,
            extract_org_terms=lambda question: ['ETRI'],
            extract_org_role=lambda question: None,
            extract_years=lambda question: ['2021', '2023'],
            extract_perf_types=lambda question: ['paper'],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: {'raw': question, 'hint': hint},
            normalize_intent=lambda raw_intent, **kwargs: {'normalized': raw_intent},
            run_question_analysis=fake_run_question_analysis,
            apply_planner_v2=fake_apply_planner_v2,
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert run_calls == ['ETRI papers stats 2021 2023']
    assert question_analysis is not None
    assert payload.normalized_intent['planner_used'] is True
