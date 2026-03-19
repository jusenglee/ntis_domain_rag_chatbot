import asyncio
from types import SimpleNamespace

from apps.api.services.request_facade import build_intent_payload
from apps.core.pipeline_steps import normalize_intent
from apps.core.planner_staged import compose_locked_strategy
from apps.core.query_intent import QueryIntent, classify_query


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

    assert intent.people_terms == []
    assert intent.action == 'list'


def test_query_intent_keeps_people_org_heuristics_disabled():
    topic_intent = classify_query('AI technology trend', [])
    people_intent = classify_query('researcher participation projects', [])

    assert topic_intent.people_terms == []
    assert topic_intent.org_terms == []
    assert topic_intent.org_role is None
    assert people_intent.people_terms == []
    assert people_intent.org_terms == []
    assert people_intent.org_role is None


def test_compose_locked_strategy_uses_shared_people_target_collections():
    locked = compose_locked_strategy(
        stage1={"action": "list", "head": "people", "relation_candidate": None},
        ids_map={},
        has_prev_anchor=False,
        prev_context_seed={},
        gate_seed_map={},
    )

    assert locked.target_cols == ["ntis_project_v1", "ntis_perf_v1"]


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


def test_normalize_intent_keeps_group_join_mode_for_relation_query():
    intent = classify_query('PJT-2020-1234-5678 related outputs', [])

    normalized = normalize_intent(
        intent,
        query='PJT-2020-1234-5678 related outputs',
        keywords=[],
    )

    assert normalized.relation == ('project', 'perf')
    assert normalized.join_key_mode == 'group'
    assert normalized.contract_violations == []


def test_normalize_intent_keeps_existing_contract_violations_without_type_error():
    intent = QueryIntent(
        base_route='project',
        action='list',
        relation=('project', 'perf'),
        output_type='relation',
        join_key_mode='group',
        ids_map={'pjt_id': ['1711015550'], 'pjt_no': ['PJT-2020-1234-5678']},
    )
    setattr(intent, 'contract_violations', ['PLANNER_MIXED_PROJECT_KEYS'])
    setattr(intent, 'parsing_warnings', ['join_key_mode_from_planner'])

    normalized = normalize_intent(
        intent,
        query='PJT-2020-1234-5678 related outputs',
        keywords=[],
    )

    assert normalized.join_key_mode == 'group'
    assert normalized.contract_violations == ['PLANNER_MIXED_PROJECT_KEYS']
    assert normalized.parsing_warnings == ['join_key_mode_from_planner']



def test_query_intent_extracts_labeled_alphanumeric_project_id_only_with_explicit_label():
    labeled = classify_query('과제고유번호 AI2024X001 detail', [])
    ambiguous = classify_query('과제번호 AI2024X001 detail', [])

    assert labeled.ids_map["pjt_id"] == ["AI2024X001"]
    assert ambiguous.ids_map == {}
