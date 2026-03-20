from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.core.rag_runtime_safety import (
    build_people_superlative_aggregation,
    debug_force_join_keys_enabled,
    get_ctx_hard_limit,
    with_org_must_gate,
)


class Point:
    def __init__(self, payload):
        self.payload = payload



def test_get_ctx_hard_limit_respects_explicit_env(monkeypatch):
    monkeypatch.setenv('RAG_CTX_HARD_LIMIT', '23')
    assert get_ctx_hard_limit(model_name='solar_vllm_0') == 23



def test_debug_force_join_keys_enabled_reads_env(monkeypatch):
    monkeypatch.setenv('RAG_DEBUG_FORCE_JOIN_KEYS', 'true')
    assert debug_force_join_keys_enabled() is True



def test_with_org_must_gate_applies_project_org_filter():
    result = with_org_must_gate(
        ('base',),
        col='ntis_project_v1',
        org_role='lead',
        org_filter=('org',),
        participant_org_filter=None,
        col_project='ntis_project_v1',
        and_filter_fn=lambda a, b: (a, b),
    )
    assert result == (('base',), ('org',))



def test_build_people_superlative_aggregation_counts_participants():
    reranked = [
        Point({'prtcp_mp': [{'hm_nm': 'Kim', 'hm_id': '1', 'blng_org_nm': 'OrgA'}, {'hm_nm': 'Lee', 'hm_id': '2', 'blng_org_nm': 'OrgB'}]}),
        Point({'prtcp_mp': [{'hm_nm': 'Kim', 'hm_id': '1', 'blng_org_nm': 'OrgA'}]}),
    ]

    def payload_get(payload, key):
        if key == 'prtcp_mp[].hm_nm':
            return [item.get('hm_nm') for item in payload.get('prtcp_mp', [])]
        if key == 'prtcp_mp[].hm_id':
            return [item.get('hm_id') for item in payload.get('prtcp_mp', [])]
        if key == 'prtcp_mp[].blng_org_nm':
            return [item.get('blng_org_nm') for item in payload.get('prtcp_mp', [])]
        return payload.get(key)

    aggregation = build_people_superlative_aggregation(
        reranked=reranked,
        intent=SimpleNamespace(wants_rank=True, top_k=2, stats_metric='project_participation_count', window_years=3),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=payload_get,
    )

    assert aggregation['candidate_docs'] == 2
    assert aggregation['rank_items'][0]['hm_nm'] == 'Kim'
    assert aggregation['rank_items'][0]['project_participation_count'] == 2



def test_rag_pipeline_no_undefined_runtime_helper_references():
    source = Path('apps/core/rag_pipeline.py').read_text(encoding='utf-8')
    assert 'get_ctx_hard_limit as _get_ctx_hard_limit' in source
    assert 'debug_force_join_keys_enabled as _debug_force_join_keys_enabled' in source
    assert 'build_people_superlative_aggregation as _build_people_superlative_aggregation' in source
    assert 'with_org_must_gate as _with_org_must_gate_base' in source
    assert '_flatten_ids_from_intent' in source


def test_build_project_perf_aggregation_counts_papers_by_project():
    reranked = [
        Point({'pjt_id': '1711015550', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast', 'tag': 'IRD_NAI_RI_PAPER'}),
        Point({'pjt_id': '1711015550', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast', 'tag': 'IRD_NAI_RI_PAPER'}),
        Point({'pjt_id': '1711015551', 'pjt_no': 'PJT-2020-1234-9999', 'kor_pjt_nm': 'Battery Vision', 'tag': 'IRD_NAI_RI_IPR'}),
    ]

    aggregation = build_people_superlative_aggregation(
        reranked=reranked,
        intent=SimpleNamespace(output_type='comparison', stats_metric='paper_count', min_metric_count=None, top_k=5, ids_map={}),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
    )

    assert aggregation['metric'] == 'paper_count'
    assert aggregation['group_by'] == 'project'
    assert aggregation['status'] == 'ok'
    assert aggregation['rank_items'][0]['pjt_id'] == '1711015550'
    assert aggregation['rank_items'][0]['metric_value'] == 2


def test_build_project_perf_aggregation_applies_threshold_and_group_key():
    reranked = [
        Point({'pjt_id': '1711015550', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast Y1', 'tag': 'IRD_NAI_RI_PAPER'}),
        Point({'pjt_id': '1711015551', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast Y2', 'tag': 'IRD_NAI_RI_PAPER'}),
        Point({'pjt_id': '1711015552', 'pjt_no': 'PJT-2020-1234-9999', 'kor_pjt_nm': 'Battery Vision', 'tag': 'IRD_NAI_RI_PAPER'}),
    ]

    aggregation = build_people_superlative_aggregation(
        reranked=reranked,
        intent=SimpleNamespace(output_type='comparison', stats_metric='paper_count', min_metric_count=2, top_k=5, ids_map={'pjt_no': ['PJT-2020-1234-5678']}),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
    )

    assert aggregation['group_by'] == 'project_group'
    assert aggregation['threshold'] == 2
    assert aggregation['status'] == 'ok'
    assert len(aggregation['rank_items']) == 1
    assert aggregation['rank_items'][0]['group_key'] == 'PJT-2020-1234-5678'
    assert aggregation['rank_items'][0]['metric_value'] == 2


def test_build_project_series_payload_groups_projects_and_perf_by_group_key():
    from apps.core.rag_runtime_safety import build_project_series_payload

    reranked = [
        Point({'_collection': 'ntis_project_v1', 'pjt_id': '1711015550', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast Y1', 'stan_yr': '2021', 'org_nm': 'KISTI', 'tag': 'IRD_NAI_PJT_INFO'}),
        Point({'_collection': 'ntis_project_v1', 'pjt_id': '1711015551', 'pjt_no': 'PJT-2020-1234-5678', 'kor_pjt_nm': 'AI Forecast Y2', 'stan_yr': '2022', 'org_nm': 'KISTI', 'tag': 'IRD_NAI_PJT_INFO'}),
        Point({'_collection': 'ntis_perf_v1', 'pjt_id': '1711015550', 'pjt_no': 'PJT-2020-1234-5678', 'title_text': 'Forecast Paper', 'tag': 'IRD_NAI_RI_PAPER', 'dt1': '2023-01-01'}),
    ]

    series = build_project_series_payload(
        reranked=reranked,
        intent=SimpleNamespace(output_type='series', ids_map={'pjt_no': ['PJT-2020-1234-5678']}, relation=('project', 'perf')),
        hinted_limit=0,
        policy_limit=10,
        payload_get_fn=lambda payload, key: payload.get(key.split('.')[-1]),
    )

    assert series['status'] == 'ok'
    assert series['series_key_kind'] == 'pjt_no'
    assert len(series['instance_projects']) == 2
    assert series['linked_perf'][0]['perf_type'] == 'paper'
    assert {bucket['year'] for bucket in series['year_buckets']} >= {'2021', '2022'}
