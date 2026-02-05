# -*- coding: utf-8 -*-
"""
검색 전략 매트릭스

- 프리셋(action 기반 파라미터)와 파이프라인(mode 기반 rerank/필터 정책)을
  하나의 매트릭스로 묶어 버전/키를 관리합니다.
"""
from __future__ import annotations

from typing import Dict

SEARCH_STRATEGY_VERSION = "v1.0"

PRESET_KEYS_BY_ACTION: Dict[str, str] = {
    "support": "support",
    "relation": "relation",
    "id_exact": "id_exact",
    "id_fuzzy": "id_fuzzy",
    "list": "filter",
    "download": "filter",
    "stats": "filter",
    "topic": "topic",
    "search": "search",
}

MODE_POLICY: Dict[str, Dict[str, object]] = {
    "search": {
        "rerank_weights": {"rrf": 0.45, "kw": 0.35, "filter": 0.20},
        "strict_ids": False,
        "filter_scope": "optional(search_filter+tag/people/org/year/keyword/perf_type)",
    },
    "lookup": {
        "rerank_weights": {"rrf": 0.30, "kw": 0.25, "filter": 0.45},
        "strict_ids": True,
        "filter_scope": "server_filters(id/tag/org/people/year/keyword/perf_type)",
    },
    "join": {
        "rerank_weights": {"rrf": 0.25, "kw": 0.25, "filter": 0.50},
        "strict_ids": True,
        "filter_scope": "hop1(search)+hop2(join_filter+year/keyword/perf_type)",
    },
}

STRATEGY_MATRIX = {
    "version": SEARCH_STRATEGY_VERSION,
    "preset_keys_by_action": PRESET_KEYS_BY_ACTION,
    "mode_policy": MODE_POLICY,
}


def resolve_preset_key(action: str) -> str:
    key = PRESET_KEYS_BY_ACTION.get((action or "").strip().lower())
    return key or "default"


def build_strategy_key(action: str, mode: str) -> str:
    return f"{resolve_preset_key(action)}:{(mode or '').strip().lower() or 'search'}"


def get_mode_policy(mode: str) -> Dict[str, object]:
    return dict(MODE_POLICY.get((mode or "").strip().lower(), MODE_POLICY["search"]))


def build_rerank_spec(mode: str) -> Dict[str, object]:
    policy = get_mode_policy(mode)
    return {
        "rerank_weights": dict(policy.get("rerank_weights", {})),
        "strict_ids": bool(policy.get("strict_ids", False)),
        "filter_scope": policy.get("filter_scope"),
    }
