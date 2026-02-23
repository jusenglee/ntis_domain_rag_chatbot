# -*- coding: utf-8 -*-
"""
Search preset builder

- QueryIntent(action/intent/route/엔티티)에 따라:
  - 검색 파라미터(top_k_dense/top_k_lex_cand/top_k_lex)
  - 가중치(w_lex, field weights)
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

from .query_intent import QueryIntent
from .search_strategy import resolve_preset_key

PEOPLE_ORG_FIELDS = [
    "prtcp_mp[].hm_nm",
    "prtcp_mp.hm_nm",
    "prtcp_mp[].blng_org_nm",
    "prtcp_mp.blng_org_nm",
    "prtcp_org[].org_nm",
    "prtcp_org.org_nm",
]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    return bool(default)


def _allow_legacy_meta_keys() -> bool:
    return _env_bool("RAG_ALLOW_LEGACY_META_KEYS", default=False)


def _pjt_no_fields() -> List[str]:
    fields = ["pjt_no"]
    if _allow_legacy_meta_keys():
        fields.append("meta_basic.pjt_no")
    return fields


def _is_people_org_intent(intent: QueryIntent) -> bool:
    if intent.base_route in ("people", "org"):
        return True
    if intent.action == "relation" and intent.relation:
        return any(route in ("people", "org") for route in intent.relation)
    return False


def _prioritize_people_org_fields(
        preset: SearchPreset,
        *,
        intent: QueryIntent,
        default_weights: Dict[str, float],
) -> SearchPreset:
    if not _is_people_org_intent(intent):
        return preset
    prioritized = [field for field in PEOPLE_ORG_FIELDS if field in preset.lexical_fields]
    if prioritized:
        preset.lexical_fields = prioritized + [f for f in preset.lexical_fields if f not in prioritized]
        for field in prioritized:
            preset.lexical_field_weights.setdefault(field, default_weights.get(field, 1.0))
    return preset


def _ensure_pjt_no_fields(
        preset: SearchPreset,
        *,
        default_weights: Dict[str, float],
) -> SearchPreset:
    for field in _pjt_no_fields():
        if field in preset.lexical_fields:
            preset.lexical_field_weights.setdefault(field, default_weights.get(field, 1.0))
    return preset


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
    sparse_vector_name: str = "bm25"
    sparse_topk: int = 0
    sparse_weight: float = 0.0

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
    stop_if_top1_confident: bool = False  # id 계열: top1이 충분히 높으면 context 최소화

    early_stop_dense_score: float = 0.0
    early_stop_min_hits: int = 0

    # tag match bonus (post-rerank)
    tag_boost: float = 0.0
    tag_mismatch_penalty: float = 0.0

    # strategy meta
    strategy_key: Optional[str] = None

    def as_debug(self) -> Dict[str, object]:
        return {
            "top_k_dense": self.top_k_dense,
            "top_k_lex_cand": self.top_k_lex_cand,
            "top_k_lex": self.top_k_lex,
            "w_lex": self.w_lex,
            "lexical_fields": self.lexical_fields,
            "lexical_field_weights": self.lexical_field_weights,
            "sparse_vector_name": self.sparse_vector_name,
            "sparse_topk": self.sparse_topk,
            "sparse_weight": self.sparse_weight,
            "use_dense_threshold": int(self.use_dense_threshold),
            "min_dense_score": self.min_dense_score,
            "min_reranked": self.min_reranked,
            "max_ctx_items": self.max_ctx_items,
            "use_org_filter": int(self.use_org_filter),
            "org_lex_boost": int(self.org_lex_boost),
            "stop_if_top1_confident": int(self.stop_if_top1_confident),
            "tag_boost": self.tag_boost,
            "tag_mismatch_penalty": self.tag_mismatch_penalty,
            "strategy_key": self.strategy_key,
        }

def resolve_sparse_vector_name(
        *,
        runtime_sparse_vector_name: Optional[str],
        preset_sparse_vector_name: Optional[str],
) -> tuple[str, str]:
    """Resolve sparse vector name with explicit priority.

    Priority:
    1) runtime arg (pipeline input)
    2) preset value
    3) env RAG_SPARSE_VECTOR_NAME
    4) hard default "bm25"
    """
    runtime_val = str(runtime_sparse_vector_name or "").strip()
    if runtime_val:
        return runtime_val, "runtime_arg"

    preset_val = str(preset_sparse_vector_name or "").strip()
    if preset_val:
        return preset_val, "preset"

    env_val = str(os.getenv("RAG_SPARSE_VECTOR_NAME", "")).strip()
    if env_val:
        return env_val, "env"

    return "bm25", "default"


def build_topk_spec(
        preset: SearchPreset,
        *,
        sparse_vector_name: str,
        sparse_topk: int,
        sparse_weight: float,
) -> Dict[str, object]:
    # planner가 실행 레이어에 전달하는 retrieval 정책 스냅샷.
    # (실행 레이어에서 env 정책으로 재덮어쓰지 않도록 정규화)
    spec = dict(preset.as_debug())
    spec["top_k_dense"] = int(spec.get("top_k_dense", preset.top_k_dense))
    spec["top_k_lex_cand"] = int(spec.get("top_k_lex_cand", preset.top_k_lex_cand))
    spec["top_k_lex"] = int(spec.get("top_k_lex", preset.top_k_lex))
    spec["use_dense_threshold"] = bool(spec.get("use_dense_threshold", preset.use_dense_threshold))
    spec["min_dense_score"] = float(spec.get("min_dense_score", preset.min_dense_score))
    spec.update(
        {
            "sparse_vector_name": sparse_vector_name,
            "sparse_topk": int(sparse_topk),
            "sparse_weight": float(sparse_weight),
        }
    )
    return spec


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
    default_title_w = _f("RAG_W_TITLE", 5.0)
    default_content_w = _f("RAG_W_CONTENT", 3.0)
    default_keyword_w = _f("RAG_W_KEYWORD_TEXT", 3.0)
    default_flat_w = _f("RAG_W_FLAT_TEXT", 2.0)
    default_category_w = _f("RAG_W_CATEGORY", _f("RAG_W_CETEGORY", 5.0))
    default_pjt_no_w = _f("RAG_W_PJT_NO", default_title_w)
    default_prtcp_person_w = max(_f("RAG_W_PRTCP_PERSON", default_title_w), default_title_w)
    default_prtcp_org_w = max(_f("RAG_W_PRTCP_ORG", default_title_w), default_title_w)
    default_sparse_vector = os.getenv("RAG_SPARSE_VECTOR_NAME", "bm25").strip()

    # base lexical fields (schema-defined)
    base_fields = [
        "title_text",
        "content_text",
        "keyword_text",
        "flat_text",
    ]

    weights = {
        "title_text": default_title_w,
        "content_text": default_content_w,
        "keyword_text": default_keyword_w,
        "flat_text": default_flat_w,
    }

    # ---- action presets ----
    action = intent.action

    strategy_key = resolve_preset_key(action)

    # 1) Support (QnA/Manual)
    if action == "support":
        top_k_lex = _i("RAG_TOPK_LEX_SUPPORT", _i("RAG_TOPK_LEX", 50))
        w_lex = _f("RAG_W_LEX_SUPPORT", _f("RAG_W_LEX", 0.25))
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_SUPPORT", _i("RAG_TOPK_DENSE", 25)),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_SUPPORT", _i("RAG_TOPK_LEX_CAND", 250)),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_SUPPORT", "0") == "1"),
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_SUPPORT", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_SUPPORT", _i("RAG_MIN_RERANKED", 4)),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_SUPPORT", _i("RAG_MAX_CONTEXT_ITEMS", 12)),
            tag_boost=_f("RAG_TAG_BOOST_SUPPORT", _f("RAG_TAG_BOOST", 0.4)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_SUPPORT", _f("RAG_TAG_MISMATCH_PENALTY", 0.0)),
            strategy_key=strategy_key,
        )
        preset = _ensure_pjt_no_fields(preset, default_weights=weights)
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 2) Relation (2-hop) — hop1/hop2는 파이프라인에서 별도 조정 가능.
    if action == "relation":
        top_k_lex = _i("RAG_TOPK_LEX_REL", 180)
        w_lex = _f("RAG_W_LEX_REL", 0.65)
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_REL", 16),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_REL", 900),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=False,  # 조인/필터 계열은 dense threshold 오탐 가능
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_REL", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_REL", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_REL", 12),
            tag_boost=_f("RAG_TAG_BOOST_REL", _f("RAG_TAG_BOOST", 1.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_REL", _f("RAG_TAG_MISMATCH_PENALTY", 0.2)),
            strategy_key=strategy_key,
        )
        preset = _ensure_pjt_no_fields(preset, default_weights=weights)
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 3) Exact ID lookup
    if action == "id_exact":
        top_k_lex = _i("RAG_TOPK_LEX_ID_EXACT", 160)
        w_lex = _f("RAG_W_LEX_ID_EXACT", 0.78)
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_ID_EXACT", 8),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID_EXACT", 1200),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_ID_EXACT", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_ID_EXACT", 2),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID_EXACT", 3),
            stop_if_top1_confident=True,
            tag_boost=_f("RAG_TAG_BOOST_ID_EXACT", _f("RAG_TAG_BOOST", 1.2)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID_EXACT", _f("RAG_TAG_MISMATCH_PENALTY", 0.3)),
            strategy_key=strategy_key,
        )
        preset = _ensure_pjt_no_fields(preset, default_weights=weights)
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 4) Fuzzy ID-like query
    if action == "id_fuzzy":
        top_k_lex = _i("RAG_TOPK_LEX_ID", 120)
        w_lex = _f("RAG_W_LEX_ID", 0.60)
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_ID", 12),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID", 600),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_ID", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_ID", 3),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID", 6),
            tag_boost=_f("RAG_TAG_BOOST_ID", _f("RAG_TAG_BOOST", 0.9)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID", _f("RAG_TAG_MISMATCH_PENALTY", 0.2)),
            strategy_key=strategy_key,
        )
        preset = _ensure_pjt_no_fields(preset, default_weights=weights)
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 5) List/filter
    if action in ("list", "download", "stats"):
        top_k_lex = _i("RAG_TOPK_LEX_FILTER", 180)
        w_lex = _f("RAG_W_LEX_FILTER", 0.65)
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_FILTER", 16),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_FILTER", 900),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=False,
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_FILTER", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_FILTER", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_FILTER", 10),
            tag_boost=_f("RAG_TAG_BOOST_FILTER", _f("RAG_TAG_BOOST", 1.0)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_FILTER", _f("RAG_TAG_MISMATCH_PENALTY", 0.25)),
            strategy_key=strategy_key,
        )
        if intent.base_route == "people":
            preset.top_k_lex_cand = max(preset.top_k_lex_cand, _i("RAG_TOPK_LEX_CAND_PEOPLE", 1200))
            preset.top_k_lex = max(preset.top_k_lex, _i("RAG_TOPK_LEX_PEOPLE", 240))
        # org_query: project + org_terms 있을 때
        if intent.base_route == "project" and intent.org_terms:
            preset.use_org_filter = True
            preset.org_lex_boost = True
        preset = _ensure_pjt_no_fields(preset, default_weights=weights)
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 6) Topic summary
    if action == "topic":
        top_k_lex = _i("RAG_TOPK_LEX_TOPIC", 80)
        w_lex = _f("RAG_W_LEX_TOPIC", 0.22)
        preset = SearchPreset(
            top_k_dense=_i("RAG_TOPK_DENSE_TOPIC", 45),
            top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_TOPIC", 450),
            top_k_lex=top_k_lex,
            w_lex=w_lex,
            lexical_fields=base_fields,
            lexical_field_weights=weights,
            sparse_vector_name=default_sparse_vector,
            sparse_topk=top_k_lex,
            sparse_weight=w_lex,
            use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_TOPIC", "1") == "1"),
            min_dense_score=_f("RAG_MIN_DENSE_SCORE_TOPIC", _f("RAG_MIN_DENSE_SCORE", 0.52)),
            min_reranked=_i("RAG_MIN_RERANKED_TOPIC", 4),
            max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_TOPIC", 12),
            tag_boost=_f("RAG_TAG_BOOST_TOPIC", _f("RAG_TAG_BOOST", 0.6)),
            tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_TOPIC", _f("RAG_TAG_MISMATCH_PENALTY", 0.1)),
            strategy_key=strategy_key,
        )
        return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)

    # 7) Detail/content (default)
    top_k_lex = _i("RAG_TOPK_LEX", 50)
    w_lex = _f("RAG_W_LEX", 0.25)
    preset = SearchPreset(
        top_k_dense=_i("RAG_TOPK_DENSE", 25),
        top_k_lex_cand=_i("RAG_TOPK_LEX_CAND", 250),
        top_k_lex=top_k_lex,
        w_lex=w_lex,
        lexical_fields=base_fields,
        lexical_field_weights=weights,
        sparse_vector_name=default_sparse_vector,
        sparse_topk=top_k_lex,
        sparse_weight=w_lex,
        use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD", "1") == "1"),
        min_dense_score=_f("RAG_MIN_DENSE_SCORE_DEFAULT", _f("RAG_MIN_DENSE_SCORE", 0.52)),
        min_reranked=_i("RAG_MIN_RERANKED", 4),
        max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS", 30),
        tag_boost=_f("RAG_TAG_BOOST", 0.6),
        tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY", 0.1),
        strategy_key=strategy_key,
    )
    return _prioritize_people_org_fields(preset, intent=intent, default_weights=weights)
