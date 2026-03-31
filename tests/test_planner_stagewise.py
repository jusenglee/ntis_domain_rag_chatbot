import asyncio
from types import SimpleNamespace

import apps.api.services.planner_runtime as planner_runtime_module
import apps.api.services.rag_retriever as rag_retriever_module
from apps.api.services.rag_retriever import detect_retrieval_query_drift, repair_query_for_resolved_anchor, resolve_rag_queries
from apps.api.services.followup_anchor import anchor_to_seed_map
from apps.api.services.detail_contract import compute_detail_coverage
from apps.api.services.view_state import ConversationViewState, DisplayItem, DisplaySnapshot, FocusEntity
from apps.api.services.planner_service import apply_planner_strategy, apply_question_analysis_v3, collect_researcher_name_terms, merge_planner_hints, normalize_hint_terms
from apps.api.services.planner_runtime import _apply_deterministic_stage2_repair, _normalize_stage2_slots_payload, _planner_prev_context_text, _sanitize_stage2_structured_filters
from apps.api.services.request_facade import build_intent_payload
from apps.api.services.view_state import build_display_snapshot, render_display_snapshot_text
from apps.core.planner_stage15_types import PlannerEntityRolePlan
from apps.core.planner_surface_signals import collect_surface_signals
from apps.core.planner_validation import Stage2ValidationResult, validate_stage2_slots
from apps.api.services.retrieval_workflow import (
    _extract_explicit_count,
    _normalize_display_payloads,
    _resolve_display_request,
    _resolve_retrieval_budget,
    _resolve_runtime_top_k,
)
from apps.core.settings import MAX_TOP_K_SIZE
from apps.core.followup_resolution import build_followup_clarification_message, build_followup_clarification_payload, resolve_reference_context_followup
from apps.core.pipeline_steps import NormalizedIntent, normalize_intent
from apps.core.planner_contract import validate_planner_contract
from apps.core.planner_staged import compose_locked_strategy, regate_locked_strategy
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


def test_compose_locked_strategy_keeps_broad_list_as_search_without_seed():
    locked = compose_locked_strategy(
        stage1={"action": "list", "head": "project", "relation_candidate": None},
        ids_map={},
        has_prev_anchor=False,
        prev_context_seed={},
        gate_seed_map={},
    )

    assert locked.mode == "SEARCH"
    assert locked.relation is None
    assert locked.join_key_mode is None


def test_regate_locked_strategy_upgrades_search_to_lookup_when_stage2_has_structured_filters():
    events = []
    locked = compose_locked_strategy(
        stage1={"action": "list", "head": "project", "relation_candidate": None},
        ids_map={},
        has_prev_anchor=False,
        prev_context_seed={},
        gate_seed_map={},
    )

    updated = regate_locked_strategy(
        request_id="rid",
        conversation_id="cid",
        stage1=SimpleNamespace(action="list", head="project", relation_candidate=None),
        stage2=SimpleNamespace(ids_map={}, filters={"participant_org_name": ["ETRI"]}),
        locked_strategy=locked,
        allowed_keys={"pjt_id", "pjt_no", "rst_id", "doi", "issn", "paper_id"},
        log_event=lambda name, **fields: events.append((name, fields)),
    )

    assert updated.mode == "LOOKUP"
    assert updated.target_cols == ["ntis_project_v1"]
    assert any(name == "PLANNER.REGATE" and fields["after_mode"] == "LOOKUP" for name, fields in events)


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


def test_build_intent_payload_keeps_planner_list_count_when_valid():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            action='list',
            output_type='list',
            limit=5,
            display_limit=3,
        )

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='\ubc18\ub3c4\uccb4 \ubd84\uc57c \uacfc\uc81c 3\uac74\uc744 \uc54c\ub824\uc918',
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
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, qa is not None),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert question_analysis is not None
    assert question_analysis.limit == 5
    assert question_analysis.display_limit == 3
    assert payload.question_analysis.limit == 5
    assert payload.question_analysis.display_limit == 3


def test_build_intent_payload_falls_back_for_invalid_planner_count():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            action='list',
            output_type='list',
            limit=0,
            display_limit=0,
        )

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='\ubc18\ub3c4\uccb4 \ubd84\uc57c \uacfc\uc81c 3\uac74\uc744 \uc54c\ub824\uc918',
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
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, qa is not None),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert question_analysis is not None
    assert question_analysis.limit == 20
    assert question_analysis.display_limit == 3
    assert payload.question_analysis.limit == 20
    assert payload.question_analysis.display_limit == 3


def test_build_intent_payload_overrides_planner_default_when_explicit_count_mismatches():
    log_calls = []

    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            action='list',
            output_type='list',
            limit=20,
            display_limit=20,
        )

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='\ubc18\ub3c4\uccb4\ubd84\uc57c \uacfc\uc81c 3\uac74\uc744 \ubcf4\uc5ec\uc918',
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
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, qa is not None),
            log_event=lambda *args, **kwargs: log_calls.append((args, kwargs)),
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert question_analysis is not None
    assert question_analysis.limit == 20
    assert question_analysis.display_limit == 3
    assert payload.question_analysis.display_limit == 3
    count_resolution = next(fields for args, fields in log_calls if args and args[0] == 'PLANNER.COUNT_RESOLUTION')
    assert count_resolution['source'] == 'planner_fallback'
    assert count_resolution['reason'] == 'planner_explicit_count_mismatch'
    assert count_resolution['explicit_count'] == 3


def test_build_intent_payload_clamps_invalid_planner_large_count_to_contract_max():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            action='list',
            output_type='list',
            limit=0,
            display_limit=0,
        )

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='반도체 분야 과제 100건을 알려줘',
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
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, qa is not None),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert question_analysis is not None
    assert question_analysis.limit == MAX_TOP_K_SIZE
    assert question_analysis.display_limit == MAX_TOP_K_SIZE
    assert payload.question_analysis.limit == MAX_TOP_K_SIZE
    assert payload.question_analysis.display_limit == MAX_TOP_K_SIZE


def test_build_intent_payload_falls_back_for_invalid_detail_count():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(
            confidence=0.8,
            action='detail',
            output_type='detail',
            limit=0,
            display_limit=0,
        )

    payload, question_analysis = asyncio.run(
        build_intent_payload(
            question='\ubc18\ub3c4\uccb4 \uacfc\uc81c 3\uac74 \uc0c1\uc138\ub97c \uc54c\ub824\uc918',
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
            apply_question_analysis_v3=lambda intent, qa, **kwargs: (intent, qa is not None),
            log_event=lambda *args, **kwargs: None,
            intent_payload_cls=Payload,
            planner_stagewise_enabled=True,
            planner_stage1_prompt_version='v1',
            planner_stage2_prompt_version='v1',
        )
    )

    assert question_analysis is not None
    assert question_analysis.limit == 1
    assert question_analysis.display_limit == 1
    assert payload.question_analysis.limit == 1
    assert payload.question_analysis.display_limit == 1


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




def test_apply_planner_strategy_preserves_perf_context_owner_lock_without_relocking_route():
    log_calls = []

    class DummyStrategyViolation(Exception):
        def __init__(self, *, error_code=None, reason=None):
            super().__init__(reason)
            self.error_code = error_code
            self.reason = reason

    def build_changed_fields(before_snapshot, after_snapshot, tracked_fields, *, changed_by):
        changed = {}
        for field in tracked_fields:
            if before_snapshot.get(field) != after_snapshot.get(field):
                changed[field] = {
                    'before': before_snapshot.get(field),
                    'after': after_snapshot.get(field),
                    'changed_by': changed_by,
                }
        return changed

    intent = NormalizedIntent(
        action='detail',
        base_route='perf',
        relation=None,
        is_id_query=False,
        mode='lookup',
        target_cols=['ntis_perf_v1'],
        ids_map={'rst_id': ['REP-1'], 'pjt_id': ['PJT-1']},
        context_owner_lock='perf',
        context_owner_lock_reason='followup_context_perf',
    )
    qa = SimpleNamespace(
        confidence=0.9,
        head='project',
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

    patched, applied = apply_planner_strategy(
        intent,
        qa,
        request_id='rid',
        conversation_id='cid',
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        build_changed_fields=build_changed_fields,
        changed_by_planner_merge='planner_merge',
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.base_route == 'project'
    assert patched.target_cols == ['ntis_project_v1']
    assert patched.context_owner_lock == 'perf'
    restore_event = next(fields for event, fields in log_calls if event == 'FOLLOWUP.CONTEXT.OWNER_LOCKED')
    assert restore_event['attempted_base_route'] == 'project'
    assert restore_event['attempted_target_cols'] == ['ntis_project_v1']
    assert restore_event['context_owner_lock'] == 'perf'
    assert restore_event['context_owner_lock_reason'] == 'followup_context_perf'
    assert restore_event['final_base_route'] == 'project'
    assert restore_event['final_target_cols'] == ['ntis_project_v1']
    assert restore_event['enforced_by'] == 'planner_service.apply_planner_strategy'


def test_apply_planner_strategy_preserves_project_context_owner_lock_without_relocking_route():
    log_calls = []

    class DummyStrategyViolation(Exception):
        def __init__(self, *, error_code=None, reason=None):
            super().__init__(reason)
            self.error_code = error_code
            self.reason = reason

    def build_changed_fields(before_snapshot, after_snapshot, tracked_fields, *, changed_by):
        changed = {}
        for field in tracked_fields:
            if before_snapshot.get(field) != after_snapshot.get(field):
                changed[field] = {
                    'before': before_snapshot.get(field),
                    'after': after_snapshot.get(field),
                    'changed_by': changed_by,
                }
        return changed

    intent = NormalizedIntent(
        action='detail',
        base_route='project',
        relation=None,
        is_id_query=False,
        mode='lookup',
        target_cols=['ntis_project_v1'],
        ids_map={'pjt_id': ['PJT-1']},
        context_owner_lock='project',
        context_owner_lock_reason='followup_context_project',
    )
    qa = SimpleNamespace(
        confidence=0.9,
        head='perf',
        action='detail',
        mode='lookup',
        relation=None,
        join_key_mode=None,
        target_cols=['ntis_perf_v1'],
        ids_map={},
        candidate_keys={},
        project_key_policy=None,
        join_resolution_policy=None,
        output_type='detail',
        planner_source='stagewise',
    )

    patched, applied = apply_planner_strategy(
        intent,
        qa,
        request_id='rid',
        conversation_id='cid',
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        build_changed_fields=build_changed_fields,
        changed_by_planner_merge='planner_merge',
        strategy_violation_cls=DummyStrategyViolation,
    )

    assert applied is True
    assert patched.base_route == 'perf'
    assert patched.target_cols == ['ntis_perf_v1']
    assert patched.context_owner_lock == 'project'
    restore_event = next(fields for event, fields in log_calls if event == 'FOLLOWUP.CONTEXT.OWNER_LOCKED')
    assert restore_event['attempted_base_route'] == 'perf'
    assert restore_event['attempted_target_cols'] == ['ntis_perf_v1']
    assert restore_event['context_owner_lock'] == 'project'
    assert restore_event['context_owner_lock_reason'] == 'followup_context_project'
    assert restore_event['final_base_route'] == 'perf'
    assert restore_event['final_target_cols'] == ['ntis_perf_v1']
    assert restore_event['enforced_by'] == 'planner_service.apply_planner_strategy'

def test_resolve_retrieval_budget_uses_question_analysis_limit_as_source_of_truth():
    qa = SimpleNamespace(limit=7, display_limit=3)

    assert _resolve_retrieval_budget(qa, max_top_k_size=20) == 7


def test_resolve_retrieval_budget_clamps_to_contract_max():
    qa = SimpleNamespace(limit=999, display_limit=999)

    assert _resolve_retrieval_budget(qa, max_top_k_size=20) == 20


def test_resolve_display_request_prefers_display_limit_over_limit():
    qa = SimpleNamespace(limit=10, display_limit=3)

    assert _resolve_display_request(qa) == 3


def test_extract_explicit_count_reads_numeric_request():
    assert _extract_explicit_count('반도체 분야 과제 3건을 알려줘') == 3
    assert _extract_explicit_count('반도체 과제 목록을 알려줘') is None


def test_detect_retrieval_query_drift_allows_compact_topic_preserving_hint():
    drift_detected, drift_reasons = detect_retrieval_query_drift(
        raw_query='반도체 분야 과제 3건을 알려줘',
        hint_query='반도체 과제',
    )

    assert drift_detected is False
    assert drift_reasons == []



def test_detect_retrieval_query_drift_rejects_perf_axis_change():
    drift_detected, drift_reasons = detect_retrieval_query_drift(
        raw_query='반도체 분야 과제 3건을 알려줘',
        hint_query='반도체 과제 성과',
    )

    assert drift_detected is True
    assert 'perf_axis_added' in drift_reasons


def test_detect_retrieval_query_drift_flags_parenthesized_people_org_loss():
    drift_detected, drift_reasons = detect_retrieval_query_drift(
        raw_query='신동구(한국과학기술정보연구원) 연구자의 활동이력',
        hint_query='연구자 활동이력',
    )

    assert drift_detected is True
    assert 'people_terms_lost' in drift_reasons
    assert 'org_terms_lost' in drift_reasons



def test_resolve_rag_queries_falls_back_to_raw_query_on_drift():
    state = SimpleNamespace(question='반도체 분야 과제 3건을 알려줘', intent_payload=None)
    qa = SimpleNamespace(retrieval_query='반도체 과제 성과', confidence=0.95)
    ks = SimpleNamespace(retrieval_query='반도체 과제 성과', confidence=0.95)

    raw_query, planner_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=ks,
        min_confidence=0.55,
    )

    assert raw_query == '반도체 분야 과제 3건을 알려줘'
    assert planner_query == '반도체 과제 성과'
    assert search_query == raw_query
    assert confidence == 0.95
    assert drift_detected is True
    assert 'perf_axis_added' in drift_reasons
    assert fallback_applied is True



def test_detect_retrieval_query_drift_keeps_korean_year_tokens():
    drift_detected, drift_reasons = detect_retrieval_query_drift(
        raw_query='2024년 반도체 과제를 알려줘',
        hint_query='반도체 과제',
    )

    assert drift_detected is True
    assert 'missing_identifier_or_year' in drift_reasons


def test_resolve_rag_queries_keeps_identifier_and_year_terms():
    state = SimpleNamespace(question='2023 ETRI 반도체 과제를 알려줘', intent_payload=None)
    qa = SimpleNamespace(retrieval_query='반도체 과제', confidence=0.95)
    ks = SimpleNamespace(retrieval_query='반도체 과제', confidence=0.95)

    raw_query, planner_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=ks,
        min_confidence=0.55,
    )

    assert planner_query == '반도체 과제'
    assert search_query == raw_query
    assert confidence == 0.95
    assert drift_detected is True
    assert 'missing_identifier_or_year' in drift_reasons
    assert fallback_applied is True


def test_resolve_rag_queries_falls_back_when_quoted_title_is_lost():
    state = SimpleNamespace(question="'단일 반도체물질 기반 3진 논리 게이트 개발' 과제 상세정보", intent_payload=None)
    qa = SimpleNamespace(retrieval_query='과제 상세정보', confidence=0.95)
    ks = SimpleNamespace(retrieval_query='과제 상세정보', confidence=0.95)

    raw_query, planner_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=ks,
        min_confidence=0.55,
    )

    assert planner_query == '과제 상세정보'
    assert search_query == raw_query
    assert confidence == 0.95
    assert drift_detected is True
    assert 'title_terms_lost' in drift_reasons
    assert fallback_applied is True



def test_repair_query_for_resolved_anchor_uses_selected_title_for_detail_followup():
    state = SimpleNamespace(
        question='1번 프로젝트의 상세정보',
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action='detail',
                base_route='project',
                relation=None,
                is_id_query=False,
                output_type='detail',
                ids_map={'pjt_id': ['1345376671']},
            ),
            strategy_meta={
                'followup_resolution_status': 'resolved',
                'anchor_source': 'display_snapshot',
                'selected_prev_item': {
                    'index': 1,
                    'pjt_id': '1345376671',
                    'title': '반도체 전공트랙 사업',
                },
            },
        ),
    )

    repaired_query, metadata = repair_query_for_resolved_anchor(
        state=state,
        query='1번 프로젝트의 상세정보',
    )

    assert repaired_query == '반도체 전공트랙 사업'
    assert metadata['anchor_present'] is True
    assert metadata['anchor_query_repaired'] is True
    assert metadata['anchor_repair_reason'] == 'anchor_axis_lost'


def test_repair_query_for_resolved_anchor_preserves_requested_field_terms():
    state = SimpleNamespace(
        question='첫번째 과제의 연구자는?',
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action='detail',
                base_route='project',
                relation=None,
                is_id_query=False,
                output_type='detail',
                ids_map={'pjt_id': ['1345376671']},
            ),
            strategy_meta={
                'followup_resolution_status': 'resolved',
                'anchor_source': 'display_snapshot',
                'selected_prev_item': {
                    'index': 1,
                    'pjt_id': '1345376671',
                    'title': '반도체 전공트랙 사업',
                },
            },
        ),
    )

    repaired_query, metadata = repair_query_for_resolved_anchor(
        state=state,
        query='첫번째 연구자',
    )

    assert repaired_query.startswith('반도체 전공트랙 사업')
    assert repaired_query.endswith('연구자')
    assert metadata['anchor_query_repaired'] is True



def test_resolve_rag_queries_repairs_project_detail_followup_with_resolved_anchor():
    state = SimpleNamespace(
        question='detail for item three',
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action='detail',
                base_route='project',
                relation=None,
                is_id_query=False,
                output_type='detail',
                ids_map={'pjt_id': ['1415144250']},
            ),
            strategy_meta={
                'followup_resolution_status': 'resolved',
                'anchor_source': 'display_snapshot',
                'selected_prev_item': {
                    'index': 3,
                    'pjt_id': '1415144250',
                    'pjt_no': 'N0001058-1',
                    'title': 'Project Alpha Detail',
                    'context_kind': 'project',
                },
            },
        ),
    )
    qa = SimpleNamespace(retrieval_query='project number three outputs', confidence=0.88, action='detail', output_type='detail')
    ks = SimpleNamespace(retrieval_query='project number three outputs', confidence=1.0)

    raw_query, planner_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied = resolve_rag_queries(
        state=state,
        qa=qa,
        ks=ks,
        min_confidence=0.55,
    )

    assert raw_query == 'detail for item three'
    assert planner_query == 'project number three outputs'
    assert search_query == 'Project Alpha Detail'
    assert confidence == 1.0
    assert drift_detected is True
    assert 'perf_axis_added' in drift_reasons
    assert fallback_applied is True


def test_repair_query_for_resolved_anchor_uses_project_identifier_without_title():
    state = SimpleNamespace(
        question='detail for item three',
        intent_payload=Payload(
            normalized_intent=NormalizedIntent(
                action='detail',
                base_route='project',
                relation=None,
                is_id_query=False,
                output_type='detail',
                ids_map={'pjt_id': ['1415144250']},
            ),
            strategy_meta={
                'followup_resolution_status': 'resolved',
                'anchor_source': 'display_snapshot',
                'selected_prev_item': {
                    'index': 3,
                    'pjt_id': '1415144250',
                    'pjt_no': 'N0001058-1',
                    'context_kind': 'project',
                },
            },
        ),
    )

    repaired_query, metadata = repair_query_for_resolved_anchor(
        state=state,
        query='detail for item three',
    )

    assert repaired_query == '1415144250'
    assert metadata['anchor_query_repaired'] is True
    assert metadata['anchor_repair_reason'] == 'anchor_axis_lost'

def test_build_display_snapshot_keeps_requested_count_when_canonical_outnumbers_docs():
    snapshot = build_display_snapshot(
        conversation_id='cid',
        turn_id='rid',
        context_kind='project',
        requested_count=3,
        documents=[
            {'title': 'first project', 'source_type': 'hit', 'pjt_id': 'PJT-1', 'pjt_no': 'NO-1'},
            {'title': 'second project', 'source_type': 'hit', 'pjt_id': 'PJT-2', 'pjt_no': 'NO-2'},
            {'title': 'third project', 'source_type': 'hit', 'pjt_id': 'PJT-3', 'pjt_no': 'NO-3'},
            {'title': 'fourth project', 'source_type': 'hit', 'pjt_id': 'PJT-4', 'pjt_no': 'NO-4'},
        ],
        canonical_evidence=[_project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project')],
        raw_count=10,
    )

    assert snapshot.requested_count == 3
    assert snapshot.visible_count == 3
    assert snapshot.raw_count == 10
    assert [item.pjt_id for item in snapshot.items] == ['PJT-1', 'PJT-2', 'PJT-3']
    assert snapshot.items[1].title_text == 'second project'
    assert snapshot.items[2].title_text == 'third project'


def test_normalize_display_payloads_derives_missing_canonical_entries():
    log_calls = []
    bundle = _normalize_display_payloads(
        docs=[
            {'title': 'first project', 'source_type': 'hit', 'pjt_id': 'PJT-1', 'pjt_no': 'NO-1'},
            {'title': 'second project', 'source_type': 'hit', 'pjt_id': 'PJT-2', 'pjt_no': 'NO-2'},
            {'title': 'third project', 'source_type': 'hit', 'pjt_id': 'PJT-3', 'pjt_no': 'NO-3'},
        ],
        canonical_evidence=[_project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project')],
        base_route='project',
        output_type='list',
        requested_count=3,
        explicit_count=3,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        request_id='rid',
        conversation_id='cid',
    )

    assert len(bundle.snapshot_documents) == 3
    assert len(bundle.snapshot_canonical_evidence) == 3
    assert bundle.snapshot_canonical_evidence[1]['ids']['pjt_id'] == 'PJT-2'
    assert bundle.snapshot_canonical_evidence[2]['ids']['pjt_id'] == 'PJT-3'
    assert any(event == 'RAG.CANONICAL_EVIDENCE.DERIVED' for event, _ in log_calls)
    assert all(event != 'RAG.DISPLAY_INPUT_MISMATCH' for event, _ in log_calls)


def test_normalize_display_payloads_recovers_list_snapshot_from_canonical_items():
    log_calls = []
    bundle = _normalize_display_payloads(
        docs=[
            {'title': 'wrapper row', 'source_type': 'aggregation'},
        ],
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
            _project_canonical_item(pjt_id='PJT-3', pjt_no='NO-3', title='third project'),
        ],
        base_route='project',
        output_type='list',
        requested_count=3,
        explicit_count=3,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        request_id='rid',
        conversation_id='cid',
    )

    assert bundle.docs_count == 3
    assert bundle.canonical_count == 3
    assert bundle.docs_kind == 'item_list'
    assert bundle.display_source == 'canonical_axis'
    assert len(bundle.snapshot_documents) == 3
    assert bundle.snapshot_documents[1]['pjt_id'] == 'PJT-2'
    promoted_fields = next(fields for event, fields in log_calls if event == 'RAG.DISPLAY_CANONICAL_AXIS.PROMOTED')
    assert promoted_fields['docs_count_before'] == 1
    assert promoted_fields['canonical_count'] == 3
    assert all(event != 'RAG.DISPLAY_INPUT_MISMATCH' for event, _ in log_calls)


def test_normalize_display_payloads_prefers_explicit_count_over_requested_count_for_recovery():
    log_calls = []
    bundle = _normalize_display_payloads(
        docs=[
            {'title': 'wrapper row', 'source_type': 'aggregation'},
        ],
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
            _project_canonical_item(pjt_id='PJT-3', pjt_no='NO-3', title='third project'),
        ],
        base_route='project',
        output_type='list',
        requested_count=20,
        explicit_count=3,
        log_event=lambda event, **fields: log_calls.append((event, fields)),
        request_id='rid',
        conversation_id='cid',
    )

    assert bundle.display_source == 'canonical_axis'
    assert len(bundle.snapshot_documents) == 3
    promoted_fields = next(fields for event, fields in log_calls if event == 'RAG.DISPLAY_CANONICAL_AXIS.PROMOTED')
    assert promoted_fields['canonical_count'] == 3
    assert all(event != 'RAG.DISPLAY_INPUT_MISMATCH' for event, _ in log_calls)


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


def test_build_intent_payload_prefers_reference_context_for_source_reference_even_with_display_snapshot():
    async def fake_run_question_analysis(**kwargs):
        return SimpleNamespace(confidence=0.8)

    payload, _ = asyncio.run(
        build_intent_payload(
            question='출처 2의 연구자 정보',
            conversation_id='cid',
            chat_history=[],
            prev_context=[],
            canonical_evidence=[
                _project_canonical_item(pjt_id='PJT-CANONICAL-1', pjt_no='NO-CANONICAL-1', title='canonical first project'),
                _project_canonical_item(pjt_id='PJT-CANONICAL-2', pjt_no='NO-CANONICAL-2', title='canonical second project'),
            ],
            view_state=ConversationViewState(
                latest_display_snapshot=DisplaySnapshot(
                    view_id='view-1',
                    conversation_id='cid',
                    turn_id='turn-1',
                    context_kind='project',
                    requested_count=2,
                    visible_count=2,
                    raw_count=2,
                    items=[
                        DisplayItem(display_rank=1, entity_kind='project', title_text='display first project', pjt_id='PJT-DISPLAY-1', pjt_no='NO-DISPLAY-1'),
                        DisplayItem(display_rank=2, entity_kind='project', title_text='display second project', pjt_id='PJT-DISPLAY-2', pjt_no='NO-DISPLAY-2'),
                    ],
                )
            ),
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

    assert payload.normalized_intent.ids_map == {'pjt_id': ['PJT-CANONICAL-2']}
    assert payload.strategy_meta['seed_source'] == 'reference_context_source_reference'
    assert payload.strategy_meta['anchor_source'] is None
    assert payload.strategy_meta['followup_reference_kind'] == 'source_reference'
    assert payload.strategy_meta['anchor_reference_kind'] == 'source_reference'


def test_resolve_reference_context_followup_resolves_source_reference():
    resolution = resolve_reference_context_followup(
        question='출처 2의 연구자 정보',
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        prev_context=[],
        default_context_kind='project',
    )

    assert resolution['followup_resolution_status'] == 'resolved'
    assert resolution['followup_reference_kind'] == 'source_reference'
    assert resolution['seed_source'] == 'reference_context_source_reference'
    assert resolution['requested_index'] == 1
    assert resolution['seed_map'] == {'pjt_id': ['PJT-2']}


def test_source_reference_clarification_message_and_payload_use_source_wording():
    resolution = resolve_reference_context_followup(
        question='출처 3의 연구자 정보',
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        prev_context=[],
        default_context_kind='project',
    )

    message = build_followup_clarification_message(resolution)
    payload = build_followup_clarification_payload(resolution)

    assert resolution['followup_resolution_status'] == 'out_of_range'
    assert resolution['followup_reference_kind'] == 'source_reference'
    assert '출처' in message
    assert '2개' in message
    assert payload['selection_hint'] == 'source_reference_or_entity_reference'


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


def test_sanitize_stage2_structured_filters_drops_researcher_filter_without_people_terms():
    events = []

    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    sanitized = _sanitize_stage2_structured_filters(
        slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={"participant_researcher_name": ["???"]},
            retrieval_query="??? ??",
            limit=5,
            display_limit=5,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=[],
            org_terms_to_keep=[],
            org_role_hint="unspecified",
            perf_type_hints=[],
            must_keep_terms=["???"],
            confidence=0.9,
        ),
        request_id="rid",
        conversation_id="cid",
        log_event=lambda name, **fields: events.append((name, fields)),
    )

    assert sanitized.filters == {}
    assert "???" in sanitized.retrieval_query
    assert any(name == "PLANNER.STAGE2.FILTERS.SANITIZED" for name, _ in events)


def test_sanitize_stage2_structured_filters_drops_role_scoped_org_filter_without_org_role():
    events = []

    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    sanitized = _sanitize_stage2_structured_filters(
        slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={"participant_org_name": ["???"], "org_name": ["???"]},
            retrieval_query="??? ?? ????",
            limit=3,
            display_limit=3,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=[],
            org_terms_to_keep=["???"],
            org_role_hint="unspecified",
            perf_type_hints=[],
            must_keep_terms=["???"],
            confidence=0.9,
        ),
        request_id="rid",
        conversation_id="cid",
        log_event=lambda name, **fields: events.append((name, fields)),
    )

    assert sanitized.filters == {"org_name": ["???"]}
    assert any(name == "PLANNER.STAGE2.FILTERS.SANITIZED" for name, _ in events)


def test_resolve_runtime_top_k_overfetches_for_visible_count():
    qa = SimpleNamespace(limit=20, display_limit=20)

    assert _resolve_runtime_top_k(qa, max_top_k_size=100, exact_detail_lookup=False) == 60


def test_sanitize_stage2_structured_filters_keeps_lead_org_filter_when_role_is_resolved():
    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    sanitized = _sanitize_stage2_structured_filters(
        slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={"lead_org_name": ["ETRI"], "participant_org_name": ["ETRI"]},
            retrieval_query="ETRI ?? ??",
            limit=5,
            display_limit=5,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=[],
            org_terms_to_keep=["ETRI"],
            org_role_hint="lead_org",
            perf_type_hints=[],
            must_keep_terms=["ETRI"],
            confidence=0.9,
        ),
        request_id="rid",
        conversation_id="cid",
        log_event=lambda *args, **kwargs: None,
    )

    assert sanitized.filters == {"lead_org_name": ["ETRI"]}


def test_sanitize_stage2_structured_filters_keeps_affiliation_org_filter_when_role_is_resolved():
    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    sanitized = _sanitize_stage2_structured_filters(
        slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={"people_affiliation_org_name": ["KISTI"], "lead_org_name": ["KISTI"]},
            retrieval_query="KISTI ?? ??? ??",
            limit=5,
            display_limit=5,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=["???"],
            org_terms_to_keep=["KISTI"],
            org_role_hint="affiliation_org",
            perf_type_hints=[],
            must_keep_terms=["KISTI"],
            confidence=0.9,
        ),
        request_id="rid",
        conversation_id="cid",
        log_event=lambda *args, **kwargs: None,
    )

    assert sanitized.filters == {"people_affiliation_org_name": ["KISTI"]}


def test_sanitize_stage2_structured_filters_drops_id_like_org_gate_for_project_detail_query():
    events = []

    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    sanitized = _sanitize_stage2_structured_filters(
        slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={"org_name": ["B555000149"]},
            retrieval_query="B555000149 과제의 상세정보",
            limit=1,
            display_limit=1,
            confidence=0.97,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=[],
            org_terms_to_keep=["B555000149"],
            org_role_hint="unspecified",
            perf_type_hints=[],
            must_keep_terms=["B555000149"],
            confidence=0.97,
        ),
        request_id="rid",
        conversation_id="cid",
        log_event=lambda name, **fields: events.append((name, fields)),
    )

    assert sanitized.filters == {}
    assert "B555000149" in sanitized.retrieval_query
    assert any(name == "PLANNER.STAGE2.FILTERS.SANITIZED" for name, _ in events)


def test_apply_deterministic_stage2_repair_restores_missing_people_org_year_and_query_terms():
    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    validation = Stage2ValidationResult(
        ok=False,
        errors=["missing_must_keep_terms", "missing_people_terms", "missing_org_terms", "missing_years"],
        missing_must_keep_terms=["활동이력"],
        missing_people_terms=["신동구"],
        missing_org_terms=["한국과학기술정보연구원"],
        missing_years=["2024"],
        missing_perf_types=[],
    )
    repaired, meta = _apply_deterministic_stage2_repair(
        stage2_slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={},
            retrieval_query="연구자 활동",
            limit=5,
            display_limit=5,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        validation=validation,
        locked_strategy=SimpleNamespace(mode="LOOKUP"),
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=["신동구"],
            org_terms_to_keep=["한국과학기술정보연구원"],
            org_role_hint="affiliation_org",
            perf_type_hints=[],
            must_keep_terms=["신동구", "한국과학기술정보연구원", "활동이력"],
            semantic_kind="broad_history",
            perf_type_policy="explicit_only",
            confidence=0.95,
        ),
        question="2024 신동구(한국과학기술정보연구원) 연구자의 활동이력",
        request_id="rid",
        conversation_id="cid",
        log_event=lambda *args, **kwargs: None,
    )

    assert repaired.filters == {
        "participant_researcher_name": ["신동구"],
        "people_affiliation_org_name": ["한국과학기술정보연구원"],
        "years": ["2024"],
    }
    assert "활동이력" in repaired.retrieval_query
    assert "2024" in repaired.retrieval_query
    assert meta["applied"] is True
    assert meta["filter_repairs_allowed"] is True


def test_apply_deterministic_stage2_repair_keeps_search_mode_query_only():
    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    validation = Stage2ValidationResult(
        ok=False,
        errors=["missing_must_keep_terms", "missing_years"],
        missing_must_keep_terms=["반도체"],
        missing_people_terms=[],
        missing_org_terms=[],
        missing_years=["2024"],
        missing_perf_types=[],
    )
    repaired, meta = _apply_deterministic_stage2_repair(
        stage2_slots=FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={},
            retrieval_query="과제",
            limit=5,
            display_limit=5,
            confidence=0.9,
        ),
        planner_stage2_slots_cls=FakeStage2Slots,
        validation=validation,
        locked_strategy=SimpleNamespace(mode="SEARCH"),
        entity_role_plan=PlannerEntityRolePlan(
            people_terms_to_keep=[],
            org_terms_to_keep=[],
            org_role_hint="unspecified",
            perf_type_hints=[],
            must_keep_terms=["반도체"],
            semantic_kind="generic_lookup",
            perf_type_policy="explicit_only",
            confidence=0.95,
        ),
        question="2024 반도체 과제를 알려줘",
        request_id="rid",
        conversation_id="cid",
        log_event=lambda *args, **kwargs: None,
    )

    assert repaired.filters == {}
    assert repaired.retrieval_query == "과제 반도체 2024"
    assert meta["filter_repairs_allowed"] is False


def test_run_stagewise_question_analysis_uses_deterministic_repair_before_raw_fallback(monkeypatch):
    events = []

    class FakeStage2Slots:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def model_dump(self):
            return dict(self.__dict__)

        @classmethod
        def model_validate(cls, payload):
            return cls(**payload)

    async def fake_build_planner_domain_cards(**kwargs):
        return {}

    async def fake_run_planner_stage1(**kwargs):
        return SimpleNamespace(action="list", head="people", relation_candidate=None, confidence=0.95)

    async def fake_run_planner_stage15(**kwargs):
        return PlannerEntityRolePlan(
            people_terms_to_keep=["신동구"],
            org_terms_to_keep=["한국과학기술정보연구원"],
            org_role_hint="affiliation_org",
            perf_type_hints=[],
            must_keep_terms=["신동구", "한국과학기술정보연구원", "활동이력"],
            semantic_kind="broad_history",
            perf_type_policy="explicit_only",
            confidence=0.98,
        )

    async def fake_run_planner_stage2(**kwargs):
        return FakeStage2Slots(
            ids_map={},
            candidate_keys={},
            project_key_policy=None,
            join_resolution_policy=None,
            filters={},
            retrieval_query="연구자 활동",
            limit=5,
            display_limit=5,
            confidence=0.92,
        )

    monkeypatch.setattr(planner_runtime_module, "build_planner_domain_cards", fake_build_planner_domain_cards)
    monkeypatch.setattr(planner_runtime_module, "run_planner_stage1", fake_run_planner_stage1)
    monkeypatch.setattr(planner_runtime_module, "run_planner_stage15", fake_run_planner_stage15)
    monkeypatch.setattr(planner_runtime_module, "run_planner_stage2", fake_run_planner_stage2)
    monkeypatch.setattr(
        planner_runtime_module,
        "determine_locked_strategy",
        lambda **kwargs: SimpleNamespace(mode="LOOKUP", head="people", action="list", relation=None, join_key_mode=None, target_cols=["ntis_project_v1", "ntis_perf_v1"], prev_context_seed={}),
    )
    monkeypatch.setattr(planner_runtime_module, "regate_locked_strategy", lambda **kwargs: kwargs["locked_strategy"])
    monkeypatch.setattr(planner_runtime_module, "assemble_question_analysis", lambda **kwargs: kwargs["stage2"])

    result = asyncio.run(
        planner_runtime_module.run_stagewise_question_analysis(
            question="2024 신동구(한국과학기술정보연구원) 연구자의 활동이력",
            conversation_id="cid",
            request_id="rid",
            chat_history=[],
            prev_context=[],
            canonical_evidence=[],
            normalized_intent=SimpleNamespace(people_terms=[], org_terms=[], perf_types=[], years=[]),
            view_state=None,
            build_llm=lambda *args, **kwargs: None,
            planner_stage1_decision_cls=object,
            planner_stage15_plan_cls=object,
            planner_stage2_slots_cls=FakeStage2Slots,
            question_analysis_cls=object,
            load_prompt_file=lambda *args, **kwargs: "",
            sanitize_llm_json=lambda value: value,
            sanitize_ids_map_semantics=lambda ids_map, **kwargs: (ids_map, {}, []),
            log_event=lambda name, **fields: events.append((name, fields)),
            planner_stage1_prompt_version="v2",
            planner_stage15_prompt_version="v1",
            planner_stage2_prompt_version="v2",
            planner_disable_thinking=True,
            planner_temperature=0.0,
            planner_schema_version="v3",
            planner_stagewise_enabled=True,
            planner_stage2_regate_seed_allowed_keys={"pjt_id", "pjt_no"},
            max_top_k_size=20,
        )
    )

    assert result.filters == {
        "participant_researcher_name": ["신동구"],
        "people_affiliation_org_name": ["한국과학기술정보연구원"],
        "years": ["2024"],
    }
    assert "활동이력" in result.retrieval_query
    assert any(name == "PLANNER.STAGE2.DETERMINISTIC_REPAIR" for name, _ in events)
    assert not any(name == "PLANNER.STAGE2.RAW_QUERY_FALLBACK" for name, _ in events)


def test_anchor_to_seed_map_supports_perf_people_org_ids():
    perf_anchor = FocusEntity(kind='perf', source='test', rst_id='RST-1', doi='10.1234/example')
    people_anchor = FocusEntity(kind='people', source='test', person_no='P-1')
    org_anchor = FocusEntity(kind='org', source='test', org_id='ORG-1', org_code='ORG-CODE')

    assert anchor_to_seed_map(perf_anchor) == {'rst_id': ['RST-1'], 'doi': ['10.1234/example']}
    assert anchor_to_seed_map(people_anchor) == {'person_no': ['P-1']}
    assert anchor_to_seed_map(org_anchor) == {'org_id': ['ORG-1'], 'org_code': ['ORG-CODE']}


def test_resolve_reference_context_followup_supports_perf_deictic_seed():
    resolution = resolve_reference_context_followup(
        question='그 논문 자세히',
        canonical_evidence=[
            {
                'ids': {'rst_id': 'RST-100', 'doi': '10.1000/test'},
                'facts': {'title': 'paper title'},
                'source_type': 'paper',
            }
        ],
        prev_context=[],
        default_context_kind='perf',
    )

    assert resolution['followup_resolution_status'] == 'resolved'
    assert resolution['seed_map'] == {'rst_id': ['RST-100']}


def test_resolve_reference_context_followup_supports_people_and_org_deictic_seed():
    people_resolution = resolve_reference_context_followup(
        question='그 연구자 다시',
        canonical_evidence=[
            {
                'ids': {'person_no': 'PERSON-9'},
                'facts': {'title': 'Kim Researcher'},
                'source_type': 'people',
            }
        ],
        prev_context=[],
        default_context_kind='people',
    )
    org_resolution = resolve_reference_context_followup(
        question='그 기관 다시',
        canonical_evidence=[
            {
                'ids': {'org_id': 'ORG-9', 'org_code': 'ORG-C9'},
                'facts': {'title': 'ETRI'},
                'source_type': 'org',
            }
        ],
        prev_context=[],
        default_context_kind='org',
    )

    assert people_resolution['followup_resolution_status'] == 'resolved'
    assert people_resolution['seed_map'] == {'person_no': ['PERSON-9']}
    assert org_resolution['followup_resolution_status'] == 'resolved'
    assert org_resolution['seed_map'] == {'org_id': ['ORG-9']}


def test_detect_retrieval_query_drift_flags_org_and_role_loss():
    drifted, reasons = detect_retrieval_query_drift(
        raw_query='ETRI 참여기관 반도체 과제',
        hint_query='반도체 과제',
    )

    assert drifted is True
    assert 'org_terms_lost' in reasons
    assert 'org_role_lost' in reasons


def test_compute_detail_coverage_supports_perf_entity_fields():
    coverage = compute_detail_coverage(
        {
            'title': 'paper title',
            'rst_id': 'RST-222',
            'doi': '10.2000/example',
            'meta_detail': {'perf_type': 'paper'},
        },
        anchor=FocusEntity(kind='perf', source='test', rst_id='RST-222'),
    )

    assert coverage.entity_found is True
    assert coverage.core_profile['rst_id'] == 'RST-222'
    assert coverage.core_profile['doi'] == '10.2000/example'
    assert coverage.rich_detail['perf_type'] == 'paper'


def test_apply_planner_strategy_preserves_anchor_locked_project_key_policy():
    intent = NormalizedIntent(
        action="detail",
        base_route="project",
        relation=None,
        is_id_query=False,
        output_type="detail",
        ids_map={"pjt_id": ["PJT-1"]},
        target_cols=["ntis_project_v1"],
        project_key_policy="anchor_locked_pjt_id",
    )
    qa = SimpleNamespace(
        confidence=0.9,
        action="detail",
        mode="LOOKUP",
        relation=None,
        join_key_mode=None,
        target_cols=["ntis_project_v1"],
        ids_map={"pjt_id": ["PJT-1"]},
        candidate_keys={},
        project_key_policy=None,
        join_resolution_policy=None,
        output_type="detail",
        head="project",
        planner_source="stagewise",
    )

    patched, changed = apply_planner_strategy(
        intent,
        qa,
        request_id="rid",
        conversation_id="cid",
        normalize_hint_terms=normalize_hint_terms,
        log_event=lambda *args, **kwargs: None,
        build_changed_fields=lambda before, after, fields, changed_by=None: {field: {"before": before.get(field), "after": after.get(field)} for field in fields if before.get(field) != after.get(field)},
        changed_by_planner_merge="planner_merge",
        strategy_violation_cls=RuntimeError,
    )

    assert changed is True
    assert patched.project_key_policy == "anchor_locked_pjt_id"


def test_validate_planner_contract_accepts_anchor_locked_project_key_policies():
    for project_key_policy in ("anchor_locked_pjt_id", "anchor_locked_pjt_no"):
        violations = validate_planner_contract(
            mode="lookup",
            head="project",
            relation=None,
            target_cols=["ntis_project_v1"],
            ids_map={"pjt_id": ["PJT-1"]} if project_key_policy.endswith("pjt_id") else {"pjt_no": ["NO-1"]},
            relation_target_cols=None,
            join_key_mode=None,
            candidate_keys=None,
            project_key_policy=project_key_policy,
        )

        assert violations == []


def test_validate_planner_contract_rejects_unknown_project_key_policy():
    violations = validate_planner_contract(
        mode="lookup",
        head="project",
        relation=None,
        target_cols=["ntis_project_v1"],
        ids_map={"pjt_id": ["PJT-1"]},
        relation_target_cols=None,
        join_key_mode=None,
        candidate_keys=None,
        project_key_policy="invalid_policy",
    )

    assert any(v.error_code == "PLANNER_PROJECT_KEY_POLICY_INVALID" for v in violations)



def test_build_display_snapshot_prefers_project_kind_when_project_ids_exist():
    snapshot = build_display_snapshot(
        conversation_id="cid",
        turn_id="rid",
        context_kind="project",
        requested_count=1,
        documents=[
            {"title": "Project", "source_type": "hit", "pjt_id": "PJT-1", "hm_id": "PERSON-1"},
        ],
        canonical_evidence=[
            {"ids": {"pjt_id": "PJT-1", "person_no": "PERSON-1"}, "facts": {"title": "Project"}, "roles": {}},
        ],
        raw_count=1,
    )

    assert snapshot.items[0].entity_kind == "project"


def test_render_display_snapshot_text_uses_real_newlines():
    snapshot = build_display_snapshot(
        conversation_id='cid',
        turn_id='rid',
        context_kind='project',
        requested_count=2,
        documents=[
            {'title': 'first project', 'source_type': 'hit', 'pjt_id': 'PJT-1', 'pjt_no': 'NO-1'},
            {'title': 'second project', 'source_type': 'hit', 'pjt_id': 'PJT-2', 'pjt_no': 'NO-2'},
        ],
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        raw_count=2,
    )

    text = render_display_snapshot_text(snapshot, max_chars=0)

    assert '`n' not in text
    assert chr(10) in text
    assert '1. first project' in text
    assert '2. second project' in text


def test_planner_prev_context_text_uses_display_snapshot_newlines():
    snapshot = build_display_snapshot(
        conversation_id='cid',
        turn_id='rid',
        context_kind='project',
        requested_count=2,
        documents=[
            {'title': 'first project', 'source_type': 'hit', 'pjt_id': 'PJT-1', 'pjt_no': 'NO-1'},
            {'title': 'second project', 'source_type': 'hit', 'pjt_id': 'PJT-2', 'pjt_no': 'NO-2'},
        ],
        canonical_evidence=[
            _project_canonical_item(pjt_id='PJT-1', pjt_no='NO-1', title='first project'),
            _project_canonical_item(pjt_id='PJT-2', pjt_no='NO-2', title='second project'),
        ],
        raw_count=2,
    )

    text = _planner_prev_context_text(
        prev_context=[],
        canonical_evidence=[],
        normalized_intent=SimpleNamespace(output_type='list', base_route='project'),
        display_snapshot=snapshot,
    )

    assert '`n' not in text
    assert chr(10) in text
    assert text.count(chr(10)) >= 2


def test_collect_surface_signals_extracts_minimal_literal_signals():
    normalized_intent = SimpleNamespace(
        years=["2023"],
        ids_map={"pjt_id": ["PJT-1"]},
        people_terms=["Kim"],
        org_terms=["ETRI"],
        perf_types=["paper"],
    )

    signals = collect_surface_signals("Kim paper 3\uac74 2024\ub144 DOI question", normalized_intent)

    assert signals.explicit_count == 3
    assert signals.years == ["2023", "2024"]
    assert "DOI" in signals.id_like_terms
    assert signals.people_terms == ["Kim"]
    assert signals.org_terms == ["ETRI"]
    assert signals.perf_types == ["paper"]


def test_validate_stage2_slots_detects_axis_loss():
    signals = SimpleNamespace(years=["2024"], people_terms=["Kim"], org_terms=["ETRI"])
    entity_role_plan = PlannerEntityRolePlan(
        people_terms_to_keep=["Kim"],
        org_terms_to_keep=["ETRI"],
        perf_type_hints=["paper"],
        must_keep_terms=["Kim", "ETRI", "paper"],
        confidence=0.9,
    )
    stage2_slots = SimpleNamespace(
        ids_map={},
        candidate_keys={},
        filters={},
        retrieval_query="semiconductor project",
    )
    locked_strategy = SimpleNamespace(
        mode="LOOKUP",
        head="project",
        action="list",
        relation=None,
        join_key_mode=None,
        prev_context_seed={},
    )

    result = validate_stage2_slots(
        question="Kim ETRI paper 2024",
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        stage2_slots=stage2_slots,
    )

    assert result.ok is False
    assert "missing_people_terms" in result.errors
    assert "missing_org_terms" in result.errors
    assert "missing_years" in result.errors
    assert "missing_perf_types" in result.errors


def test_validate_stage2_slots_rejects_perf_detail_without_explicit_perf_id():
    signals = SimpleNamespace(years=[], people_terms=["Kim"], org_terms=[])
    entity_role_plan = PlannerEntityRolePlan(
        people_terms_to_keep=["Kim"],
        must_keep_terms=["Kim"],
        confidence=0.9,
    )
    stage2_slots = SimpleNamespace(
        ids_map={},
        candidate_keys={},
        filters={"participant_researcher_name": ["Kim"]},
        retrieval_query="Kim researcher performance detail",
    )
    locked_strategy = SimpleNamespace(
        mode="LOOKUP",
        head="perf",
        action="detail",
        relation=None,
        join_key_mode=None,
        prev_context_seed={},
    )

    result = validate_stage2_slots(
        question="Kim researcher performance detail",
        signals=signals,
        entity_role_plan=entity_role_plan,
        locked_strategy=locked_strategy,
        stage2_slots=stage2_slots,
    )

    assert result.ok is False
    assert "perf_detail_without_explicit_perf_id" in result.errors
    assert "broad_query_collapsed_to_perf_detail" in result.errors
