import asyncio
from types import SimpleNamespace

import apps.api.services.rag_retriever as rag_retriever_module
from apps.api.services.planner_service import apply_question_analysis_v3, collect_researcher_name_terms, merge_planner_hints, normalize_hint_terms
from apps.api.services.planner_runtime import _normalize_stage2_slots_payload
from apps.api.services.request_facade import build_intent_payload
from apps.core.followup_resolution import resolve_reference_context_followup
from apps.core.pipeline_steps import NormalizedIntent, normalize_intent
from apps.core.planner_staged import compose_locked_strategy
from apps.core.query_intent import QueryIntent, classify_query
from apps.core.rag_pipeline import _validate_intent_payload_version


class Payload:
    def __init__(self, normalized_intent, intent_payload_version=None, question_analysis=None, strategy_meta=None):
        self.normalized_intent = normalized_intent
        self.intent_payload_version = intent_payload_version
        self.question_analysis = question_analysis
        self.strategy_meta = strategy_meta or {}


def test_query_intent_marks_project_output_relation():
    intent = classify_query('PJT-2020-1234-5678 related outputs', [])

    assert intent.relation == ('project', 'perf')
    assert intent.join_key_mode == 'deferred'
    assert intent.project_key_policy == 'ambiguous_or'
    assert intent.candidate_keys['project_key'][0]['value'] == 'PJT-2020-1234-5678'
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

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
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
            apply_question_analysis_v3=fake_apply_question_analysis_v3,
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

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
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
            apply_question_analysis_v3=fake_apply_question_analysis_v3,
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

    def fake_apply_question_analysis_v3(intent, qa, **kwargs):
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
            apply_question_analysis_v3=fake_apply_question_analysis_v3,
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
    assert normalized.join_key_mode == 'deferred'
    assert normalized.project_key_policy == 'ambiguous_or'
    assert normalized.candidate_keys['project_key'][0]['value'] == 'PJT-2020-1234-5678'
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
    labeled = classify_query('\uacfc\uc81c\uace0\uc720\ubc88\ud638 AI2024X001 detail', [])
    ambiguous = classify_query('\uacfc\uc81c\ubc88\ud638 AI2024X001 detail', [])

    assert labeled.ids_map["pjt_id"] == ["AI2024X001"]
    assert ambiguous.ids_map == {}


def test_query_intent_does_not_promote_year_only_token_as_project_key():
    intent = classify_query('\uacfc\uc81c\ubc88\ud638 2024 detail', [])

    assert intent.candidate_keys == {}
    assert intent.project_key_policy is None


def test_intent_payload_version_requires_v3_when_payload_is_present():
    try:
        _validate_intent_payload_version(SimpleNamespace(normalized_intent=SimpleNamespace()))
    except ValueError as exc:
        assert "intent_payload_version must be 'v3'" in str(exc)
    else:
        raise AssertionError('missing intent_payload_version must fail when payload is supplied')


def test_query_intent_does_not_confuse_issn_with_project_candidate():
    intent = classify_query('\uacfc\uc81c\ubc88\ud638 ISSN 2020-1234 detail', [])

    assert intent.candidate_keys == {}
    assert intent.project_key_policy is None






def _project_canonical_item(*, pjt_id: str, pjt_no: str, title: str) -> dict:
    return {
        "ids": {"pjt_id": pjt_id, "pjt_no": pjt_no},
        "facts": {"title": title, "year": "2024"},
        "roles": {"participant_researcher_name": ["Kim"]},
    }


def test_resolve_reference_context_followup_uses_reference_context_order():
    resolution = resolve_reference_context_followup(
        question="1\ubc88 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918",
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        prev_context=[],
        default_context_kind='project',
    )

    assert resolution['followup_resolution_status'] == 'resolved'
    assert resolution['seed_map'] == {'pjt_id': ['PJT-1']}
    assert resolution['selected_prev_item']['index'] == 1


def test_build_intent_payload_injects_reference_context_seed_before_planner():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(confidence=0.8)

    payload, _ = asyncio.run(
        build_intent_payload(
            question="1\ubc88 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918",
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            canonical_evidence=[
                _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
                _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
            ],
            request_id='rid',
            cheap_precheck=lambda question: {'years': [], 'people_terms': [], 'org_terms': [], 'perf_tag_filters': [], 'perf_types': [], 'ids_map': {}, 'title_terms': []},
            has_superlative_cue=lambda question: False,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: {'raw': question, 'hint': hint},
            normalize_intent=lambda raw_intent, **kwargs: SimpleNamespace(
                action='list',
                base_route='project',
                ids_map={},
                candidate_keys={},
                project_key_policy=None,
                join_resolution_policy=None,
                join_key_mode=None,
                is_exact_key_query=False,
            ),
            run_question_analysis=fake_run_question_analysis,
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, False),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert payload.normalized_intent.ids_map == {'pjt_id': ['PJT-1']}
    assert payload.strategy_meta['seed_source'] == 'reference_context_ordinal'
    assert payload.strategy_meta['selected_prev_item']['pjt_id'] == 'PJT-1'


def test_collect_researcher_name_terms_strips_ordinal_tokens():
    assert collect_researcher_name_terms({'participant_researcher_name': ['1\ubc88\uc9f8', 'Kim']}) == ['Kim']


def test_apply_question_analysis_v3_logs_ordinal_filter_strip():
    log_calls = []
    intent = NormalizedIntent(action='detail', base_route='project', relation=None, is_id_query=False)
    qa = SimpleNamespace(
        confidence=0.9,
        filters={'participant_researcher_name': ['1\ubc88\uc9f8', 'Kim']},
        limit=5,
        retrieval_query='1\ubc88 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918',
        action='detail',
        mode='lookup',
        relation=None,
        join_key_mode=None,
        target_cols=['ntis_project_v1'],
        ids_map={},
        candidate_keys={},
        project_key_policy=None,
        join_resolution_policy=None,
        output_type='detail',
        planner_source='stagewise',
    )

    hinted_intent, applied = apply_question_analysis_v3(
        intent,
        qa,
        request_id='rid',
        conversation_id='cid',
        merge_planner_hints=lambda intent, qa: merge_planner_hints(
            intent,
            qa,
            normalize_org_terms=lambda values: values or [],
            normalize_hint_terms=normalize_hint_terms,
            collect_researcher_name_terms=collect_researcher_name_terms,
        ),
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        apply_planner_strategy_fn=lambda intent, qa, **kwargs: (intent, True),
    )

    assert applied is True
    assert hinted_intent.people_terms == ['Kim']
    assert any(event == 'PLANNER.FILTER.ORDINAL_STRIPPED' for event, _ in log_calls)


def test_custom_rag_retriever_short_circuits_followup_clarification():
    from apps.core.schemas import IntentPayloadV3

    original = rag_retriever_module.run_rag_ab_compare
    rag_retriever_module.run_rag_ab_compare = lambda **kwargs: (_ for _ in ()).throw(AssertionError('retrieval should be skipped'))
    try:
        payload = IntentPayloadV3(
            normalized_intent=NormalizedIntent(action='detail', base_route='project', relation=None, is_id_query=False),
            strategy_meta={
                'followup_resolution_status': 'out_of_range',
                'explicit_followup': True,
                'selected_prev_context_kind': 'project',
                'available_count': 3,
            },
        )
        retriever = rag_retriever_module.CustomRAGRetriever(intent_payload=payload)
        result = retriever.retrieve('10\ubc88 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918')
    finally:
        rag_retriever_module.run_rag_ab_compare = original

    assert result['documents'] == []
    assert '\uc774\uc804 \ubaa9\ub85d\uc5d0\ub294 3\uac1c\ub9cc \uc788\uc2b5\ub2c8\ub2e4.' in result['no_result_message']


def test_resolve_reference_context_followup_resolves_deictic_with_single_previous_item():
    resolution = resolve_reference_context_followup(
        question='\uadf8 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918',
        canonical_evidence=[_project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project')],
        prev_context=[],
        default_context_kind='project',
    )

    assert resolution['followup_resolution_status'] == 'resolved'
    assert resolution['seed_map'] == {'pjt_id': ['PJT-1']}
    assert resolution['seed_source'] == 'reference_context_deictic'


def test_resolve_reference_context_followup_clarifies_deictic_with_multiple_previous_items():
    resolution = resolve_reference_context_followup(
        question='\uadf8 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918',
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        prev_context=[],
        default_context_kind='project',
    )

    assert resolution['followup_resolution_status'] == 'unresolved'
    assert resolution['explicit_followup'] is True
    assert resolution['followup_reference_kind'] == 'deictic'


def test_build_intent_payload_keeps_deictic_seed_source_metadata():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(confidence=0.8)

    payload, _ = asyncio.run(
        build_intent_payload(
            question='\uadf8 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918',
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            canonical_evidence=[_project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project')],
            request_id='rid',
            cheap_precheck=lambda question: {'years': [], 'people_terms': [], 'org_terms': [], 'perf_tag_filters': [], 'perf_types': [], 'ids_map': {}, 'title_terms': []},
            has_superlative_cue=lambda question: False,
            extract_years=lambda question: [],
            extract_perf_types=lambda question: [],
            extract_title_terms=lambda question: [],
            classify_query_intent=lambda question, kws, hint=None: {'raw': question, 'hint': hint},
            normalize_intent=lambda raw_intent, **kwargs: SimpleNamespace(
                action='detail',
                base_route='project',
                ids_map={},
                candidate_keys={},
                project_key_policy=None,
                join_resolution_policy=None,
                join_key_mode=None,
                is_exact_key_query=False,
            ),
            run_question_analysis=fake_run_question_analysis,
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, False),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert payload.normalized_intent.ids_map == {'pjt_id': ['PJT-1']}
    assert payload.strategy_meta['seed_source'] == 'reference_context_deictic'
    assert payload.strategy_meta['followup_reference_kind'] == 'deictic'


def test_custom_rag_retriever_short_circuits_deictic_followup_clarification():
    from apps.core.schemas import IntentPayloadV3

    original = rag_retriever_module.run_rag_ab_compare
    rag_retriever_module.run_rag_ab_compare = lambda **kwargs: (_ for _ in ()).throw(AssertionError('retrieval should be skipped'))
    try:
        payload = IntentPayloadV3(
            normalized_intent=NormalizedIntent(action='detail', base_route='project', relation=None, is_id_query=False),
            strategy_meta={
                'followup_resolution_status': 'unresolved',
                'followup_reference_kind': 'deictic',
                'explicit_followup': True,
                'selected_prev_context_kind': 'project',
                'available_count': 2,
                'requested_token': '\uadf8 \uacfc\uc81c',
            },
        )
        retriever = rag_retriever_module.CustomRAGRetriever(intent_payload=payload)
        result = retriever.retrieve('\uadf8 \uacfc\uc81c\uc758 \uc5f0\uad6c\uc790\ub97c \uc54c\ub824\uc918')
    finally:
        rag_retriever_module.run_rag_ab_compare = original

    assert result['documents'] == []
    assert '\uc774\uc804 \ubaa9\ub85d\uc5d0\uc11c \uc5b4\ub290 \uacfc\uc81c\ub97c \ub9d0\uc500\ud558\uc2dc\ub294\uc9c0 \ud655\uc778\ud574 \uc8fc\uc138\uc694.' in result['no_result_message']


def test_normalize_stage2_slots_payload_drops_legacy_join_key_mode_extra_field():
    events = []

    payload = _normalize_stage2_slots_payload(
        {
            "ids_map": {},
            "candidate_keys": {},
            "project_key_policy": None,
            "join_key_mode": None,
            "join_resolution_policy": None,
            "filters": {},
            "retrieval_query": "semiconductor project outputs",
            "limit": 20,
            "display_limit": 20,
            "confidence": 0.92,
        },
        request_id="rid",
        conversation_id="cid",
        log_event=lambda name, **kwargs: events.append((name, kwargs)),
    )

    assert "join_key_mode" not in payload
    assert payload["display_limit"] == 20
    assert events == [
        (
            "PLANNER.STAGE2.EXTRA_FIELDS_DROPPED",
            {
                "request_id": "rid",
                "conversation_id": "cid",
                "dropped_fields": ["join_key_mode"],
                "dropped_non_null_fields": [],
            },
        )
    ]


def test_normalize_stage2_slots_payload_keeps_clean_payload_without_logging():
    events = []

    payload = _normalize_stage2_slots_payload(
        {
            "ids_map": {},
            "candidate_keys": {},
            "project_key_policy": None,
            "join_resolution_policy": None,
            "filters": {},
            "retrieval_query": "semiconductor project outputs",
            "limit": 20,
            "display_limit": 10,
            "confidence": 0.92,
        },
        request_id="rid",
        conversation_id="cid",
        log_event=lambda name, **kwargs: events.append((name, kwargs)),
    )

    assert payload["display_limit"] == 10
    assert events == []