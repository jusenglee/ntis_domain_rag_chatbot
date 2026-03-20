from __future__ import annotations

import pytest

from apps.api.contracts.workflow_models import PlannerParseError, PlannerV3ParseError, QuestionAnalysisV3


def test_question_analysis_keeps_invalid_group_join_key_mode_for_strict_contract():
    payload = QuestionAnalysisV3(
        mode='JOIN',
        head='perf',
        action='list',
        relation='project_perf',
        join_key_mode='group',
        ids_map={'pjt_id': ['1711015550']},
        target_cols=['ntis_project_v1', 'ntis_perf_v1'],
        filters={},
        limit=20,
        retrieval_query='PJT-2020-1234-5678 related outputs',
        confidence=0.9,
    )

    assert payload.join_key_mode == 'group'
    assert payload.ids_map == {'pjt_id': ['1711015550']}


def test_question_analysis_non_join_clears_join_key_mode():
    payload = QuestionAnalysisV3(
        mode='LOOKUP',
        head='project',
        action='detail',
        relation=None,
        join_key_mode='group',
        ids_map={'pjt_no': ['PJT-2020-1234-5678']},
        target_cols=['ntis_project_v1'],
        filters={},
        limit=20,
        retrieval_query='project detail',
        confidence=0.8,
    )

    assert payload.join_key_mode is None


def test_question_analysis_rejects_forbidden_people_org_relation():
    with pytest.raises(ValueError, match='PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG:project_people'):
        QuestionAnalysisV3(
            mode='JOIN',
            head='people',
            action='list',
            relation='project_people',
            join_key_mode='instance',
            ids_map={'pjt_id': ['1711015550']},
            target_cols=['ntis_project_v1'],
            filters={},
            limit=20,
            retrieval_query='related people',
            confidence=0.9,
        )


def test_question_analysis_does_not_promote_org_routing_from_role_hint_text():
    payload = QuestionAnalysisV3(
        mode='LOOKUP',
        head='project',
        action='list',
        relation=None,
        join_key_mode=None,
        ids_map={},
        target_cols=['ntis_project_v1'],
        filters={'org_name': ['과제']},
        limit=20,
        retrieval_query='공동연구 참여 과제',
        confidence=0.9,
    )

    assert payload.filters.get('org_name') == ['과제']
    assert payload.filters.get('participant_org_name') in (None, [])
    assert payload.filters.get('lead_org_name') in (None, [])
    assert payload.filters.get('people_affiliation_org_name') in (None, [])


def test_workflow_models_expose_v3_names_only():
    assert PlannerParseError is PlannerV3ParseError
