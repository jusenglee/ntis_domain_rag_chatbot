# -*- coding: utf-8 -*-
"""
Search preset builder

- QueryIntent(action/intent/route/엔티티)에 따라:
  - 검색 파라미터(top_k_dense/top_k_lex_cand/top_k_lex)
  - 가중치(w_lex, field weights)
  - meta_flat / org_name_norm 사용 여부
  - dense threshold 사용 여부
  - 컨텍스트 아이템 수/조기 종료 조건
를 결정한다.

주의:
- "조기종료"는 retrieval 레이어 내부 최적화가 가장 효과적이지만,
  일단 pipeline 레벨에서 (dense skip, 2-pass skip, context 축소)로 구현한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .constants import KEY_ORG_NORM
from .query_intent import QueryIntent

@dataclass
class SearchPreset:
    # retrieval sizing
    top_k_dense: int
    top_k_lex_cand: int
    top_k_lex: int
    w_lex: float

    # lexical config
    lexical_fields: List[str] = field(default_factory=list)
    lexical_field_weights: Dict[str, float] = field(default_factory=dict)

    # fallback / threshold
    use_dense_threshold: bool = True
    min_dense_score: float = 0.52
    min_reranked: int = 4

    # context
    max_ctx_items: int = 12

    # org assist (pipeline에서 2-pass)
    use_org_filter: bool = False
    org_lex_boost: bool = False

    # pipeline-level early stop hints
    prefer_lex_only: bool = False     # dense를 아예 끌지 여부
    stop_if_top1_confident: bool = False  # id 계열: top1이 충분히 높으면 context 최소화

    early_stop_dense_score: float = 0.0
    early_stop_min_hits: int = 0

    # tag match bonus (post-rerank)
    tag_boost: float = 0.0
    tag_mismatch_penalty: float = 0.0

    def as_debug(self) -> Dict[str, object]:
        return {
            "top_k_dense": self.top_k_dense,
            "top_k_lex_cand": self.top_k_lex_cand,
            "top_k_lex": self.top_k_lex,
            "w_lex": self.w_lex,
            "lexical_fields": self.lexical_fields,
            "lexical_field_weights": self.lexical_field_weights,
            "use_dense_threshold": int(self.use_dense_threshold),
            "min_dense_score": self.min_dense_score,
            "min_reranked": self.min_reranked,
            "max_ctx_items": self.max_ctx_items,
            "use_org_filter": int(self.use_org_filter),
            "org_lex_boost": int(self.org_lex_boost),
            "prefer_lex_only": int(self.prefer_lex_only),
            "stop_if_top1_confident": int(self.stop_if_top1_confident),
            "tag_boost": self.tag_boost,
            "tag_mismatch_penalty": self.tag_mismatch_penalty,
        }


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)

def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)

def build_search_preset(intent: QueryIntent) -> SearchPreset:
    """
    기본값은 기존 파이프라인 값을 유지하고,
    action 별로 파라미터를 더 극단적으로 조정한다(초기 제안값이며 튜닝 전제).
    """
    # common defaults (기존과 호환)
    default_title_w = _f("RAG_W_TITLE", 2.2)
    default_ans_w = _f("RAG_W_ANSWER_PUBLIC", 1.2)
    default_meta_flat_w = _f("RAG_W_META_FLAT", 0.35)

    # base lexical fields
    base_fields = ["title", "answer_public"]
    if intent.is_id_query or intent.intent in ("id", "filter"):
        # 구조 질의는 meta_flat이 효율적인 경우가 많음
        base_fields = base_fields + ["meta_flat"]
    if intent.people_terms or (intent.ids_map or {}).get("person_no"):
        if "meta_flat" not in base_fields:
            base_fields = base_fields + ["meta_flat"]

    weights = {"title": default_title_w, "answer_public": default_ans_w, "meta_flat": default_meta_flat_w}

    # route별 meta_flat 보수 옵션
    if intent.base_route == "support" and ("meta_flat" in base_fields):
        if os.getenv("RAG_SUPPORT_USE_META_FLAT", "0") != "1":
            base_fields = [f for f in base_fields if f != "meta_flat"]

    # ---- action presets ----
    action = intent.action

    # 1) Support (QnA/Manual)
    if action == "support":
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_SUPPORT", _i("RAG_TOPK_DENSE", 25)),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_SUPPORT", _i("RAG_TOPK_LEX_CAND", 250)),
            top_k_lex=_i("RAG_TOPK_LEX_SUPPORT", _i("RAG_TOPK_LEX", 50)),
            w_lex=_f("RAG_W_LEX_SUPPORT", _f("RAG_W_LEX", 0.25)),
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_SUPPORT", "0") == "1"),
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_SUPPORT", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_SUPPORT", _i("RAG_MIN_RERANKED", 4)),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_SUPPORT", _i("RAG_MAX_CONTEXT_ITEMS", 12)),
            tag_boost=_f("RAG_TAG_BOOST_SUPPORT", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_SUPPORT", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        return preset

    # 2) Relation (2-hop) — hop1/hop2는 파이프라인에서 별도 조정 가능.
    if action == "relation":
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_REL", 16),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_REL", 900),
            top_k_lex=_i("RAG_TOPK_LEX_REL", 180),
            w_lex=_f("RAG_W_LEX_REL", 0.65),
            lexical_fields=base_fields,
            lexical_field_weights={**weights, "meta_flat": max(weights.get("meta_flat", 0.35), 0.8)},
            use_dense_threshold=False,  # 조인/필터 계열은 dense threshold 오탐 가능
            min_dense_score=_f("RAG_MIN_DENSE_SCORE", 0.52),
            min_reranked=_i("RAG_MIN_RERANKED_REL", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_REL", 12),
            tag_boost=_f("RAG_TAG_BOOST_REL", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_REL", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        return preset

    # 3) Exact ID lookup
    if action == "id_exact":
        # 추측입니다: exact id는 dense보다 lex/meta_flat이 더 안정적일 때가 많음
        prefer_lex_only = os.getenv("RAG_ID_EXACT_LEX_ONLY", "1") == "1"
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_ID_EXACT", 8),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID_EXACT", 1200),
            top_k_lex=_i("RAG_TOPK_LEX_ID_EXACT", 160),
            w_lex=_f("RAG_W_LEX_ID_EXACT", 0.78),
            lexical_fields=list(dict.fromkeys(base_fields + ["meta_flat"])),  # meta_flat 강제
            lexical_field_weights={**weights, "meta_flat": max(weights.get("meta_flat", 0.35), 1.0)},
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE", 0.52),
            min_reranked=_i("RAG_MIN_RERANKED_ID_EXACT", 2),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID_EXACT", 3),
            prefer_lex_only=prefer_lex_only,
            stop_if_top1_confident=True,
            tag_boost=_f("RAG_TAG_BOOST_ID_EXACT", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID_EXACT", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        return preset

    # 4) Fuzzy ID-like query
    if action == "id_fuzzy":
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_ID", 12),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID", 600),
            top_k_lex=_i("RAG_TOPK_LEX_ID", 120),
            w_lex=_f("RAG_W_LEX_ID", 0.60),
            lexical_fields=list(dict.fromkeys(base_fields + ["meta_flat"])),
            lexical_field_weights={**weights, "meta_flat": max(weights.get("meta_flat", 0.35), 0.7)},
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE", 0.52),
            min_reranked=_i("RAG_MIN_RERANKED_ID", 3),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID", 6),
            tag_boost=_f("RAG_TAG_BOOST_ID", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        return preset

    # 5) List/filter
    if action in ("list", "download", "stats"):
        # list/filter/stats/download는 구조 키워드 비중이 높아 lex/meta_flat이 유리한 경우가 많음(추측입니다)
        prefer_lex_only = os.getenv("RAG_LIST_LEX_ONLY", "0") == "1"
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_FILTER", 16),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_FILTER", 900),
            top_k_lex=_i("RAG_TOPK_LEX_FILTER", 180),
            w_lex=_f("RAG_W_LEX_FILTER", 0.65),
            lexical_fields=list(dict.fromkeys(base_fields + ["meta_flat"])),
            lexical_field_weights={**weights, "meta_flat": max(weights.get("meta_flat", 0.35), 0.8)},
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE", 0.52),
            min_reranked=_i("RAG_MIN_RERANKED_FILTER", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_FILTER", 10),
            prefer_lex_only=prefer_lex_only,
            tag_boost=_f("RAG_TAG_BOOST_FILTER", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_FILTER", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        if intent.base_route == "people":
            preset.top_k_lex_cand = max(preset.top_k_lex_cand, _i("RAG_TOPK_LEX_CAND_PEOPLE", 1200))
            preset.top_k_lex = max(preset.top_k_lex, _i("RAG_TOPK_LEX_PEOPLE", 240))
        # org_query: project + org_terms 있을 때
        if intent.base_route == "project" and intent.org_terms:
            preset.use_org_filter = True
            preset.org_lex_boost = True
            # org_name_norm을 lexical에 포함(가중치 부여)
            if KEY_ORG_NORM not in preset.lexical_fields:
                preset.lexical_fields = ["title", "answer_public", KEY_ORG_NORM, "meta_flat"]
            preset.lexical_field_weights[KEY_ORG_NORM] = _f("RAG_W_ORG_NORM", 3.0)
        return preset

    # 6) Topic summary
    if action == "topic":
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_TOPIC", 45),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_TOPIC", 450),
            top_k_lex=_i("RAG_TOPK_LEX_TOPIC", 80),
            w_lex=_f("RAG_W_LEX_TOPIC", 0.22),
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_TOPIC", "1") == "1"),
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_TOPIC", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_TOPIC", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_TOPIC", 12),
            tag_boost=_f("RAG_TAG_BOOST_TOPIC", _f("RAG_TAG_BOOST", 0.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_TOPIC", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
        )
        # topic에서는 meta_flat을 보수적으로
        if os.getenv("RAG_TOPIC_USE_META_FLAT", "0") != "1" and "meta_flat" in preset.lexical_fields:
            preset.lexical_fields = [f for f in preset.lexical_fields if f != "meta_flat"]
        return preset

    # 7) Detail/content (default)
    preset = SearchPreset(
        top_k_dense=_i("RAG_TOPK_DENSE", 25),
        top_k_lex_cand=_i("RAG_TOPK_LEX_CAND", 250),
        top_k_lex=_i("RAG_TOPK_LEX", 50),
        w_lex=_f("RAG_W_LEX", 0.25),
        lexical_fields=base_fields,
        lexical_field_weights=weights,
        use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD", "1") == "1"),
        min_dense_score=_f("RAG_MIN_DENSE_SCORE", 0.52),
        min_reranked=_i("RAG_MIN_RERANKED", 4),
        max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS", 30),
        tag_boost=_f("RAG_TAG_BOOST", 0.0),
        tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY", 0.0),
    )
    return preset
