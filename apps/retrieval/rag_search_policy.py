from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from apps.planner.query_intent import QueryIntent

logger = logging.getLogger(__name__)

SEARCH_POLICY_VERSION = "v1.2"

PEOPLE_ORG_FIELDS = [
    "prtcp_mp[].hm_nm",
    "prtcp_mp.hm_nm",
    "prtcp_mp[].blng_org_nm",
    "prtcp_mp.blng_org_nm",
    "prtcp_org[].org_nm",
    "prtcp_org.org_nm",
]

_PROJECT_KEY_POLICY_LOGGED = False


@dataclass
class SearchPreset:
    """검색 실행 정책의 하나의 세트를 묶는다.
    
    top-k, sparse, rerank, dense threshold, context 예산을 함께 들고 가며,
    planner/contract가 정한 mode를 넘어 의미를 바꾸지 않는 범위에서만 활용된다."""
    top_k_dense: int
    top_k_lex_cand: int
    top_k_lex: int
    w_lex: float
    lexical_fields: List[str] = field(default_factory=list)
    lexical_field_weights: Dict[str, float] = field(default_factory=dict)
    sparse_vector_name: str = "bm25"
    sparse_topk: int = 0
    sparse_weight: float = 0.0
    use_dense_threshold: bool = True
    min_dense_score: float = 0.52
    min_reranked: int = 4
    max_ctx_items: int = 12
    use_org_filter: bool = False
    org_lex_boost: bool = False
    stop_if_top1_confident: bool = False
    early_stop_dense_score: float = 0.0
    early_stop_min_hits: int = 0
    tag_boost: float = 0.0
    tag_mismatch_penalty: float = 0.0
    strategy_key: Optional[str] = None

    def as_debug(self) -> Dict[str, object]:
        """SearchPreset을 로그/디버그용 dict 형태로 펼친다."""
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


def safe_json(obj: Any, max_len: int = 1200) -> str:
    """디버그 대상을 JSON 문자열로 바꾸고 길이가 길면 자른다."""
    try:
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = str(obj)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def first_match(text_lower: str, cues: List[str]) -> Optional[str]:
    """소문자 질의에서 가장 먼저 맞는 cue를 찾는다."""
    for cue in cues:
        if cue and cue.lower() in text_lower:
            return cue
    return None


def count_hits(sr: Dict[str, Any]) -> Tuple[int, int, float]:
    """dense hit 수, lexical hit 수, best dense score를 한번에 계산한다."""
    dense_hit = 0
    best_dense = -1.0
    for _, lst in (sr.get("dense") or {}).items():
        dense_hit += len(lst)
        if lst:
            best_dense = max(best_dense, float(lst[0].score))
    lex_hit = len(sr.get("lexical") or [])
    return dense_hit, lex_hit, best_dense


def point_brief(p: Any) -> Dict[str, Any]:
    """hit payload에서 score, collection, doc_id, tag, title만 가볍게 뽑아 진단용 요약을 만든다."""
    payload = getattr(p, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    score = getattr(p, "score", None)
    title = payload.get("title_text") or payload.get("title1") or payload.get("title2") or ""
    return {
        "score": float(score) if score is not None else None,
        "col": str(payload.get("_collection") or ""),
        "doc_id": str(payload.get("doc_id") or ""),
        "tag": str(payload.get("tag") or ""),
        "source_table": "",
        "title": str(title)[:80],
    }


def build_strategy_key(action: Optional[str], mode: Optional[str]) -> str:
    """action과 mode를 결합한 검색 전략 키를 만든다."""
    action_norm = str(action or "").strip().lower() or "unknown"
    mode_norm = str(mode or "").strip().lower() or "unknown"
    return f"{action_norm}:{mode_norm}"


def build_rerank_spec(mode: Optional[str]) -> Dict[str, Any]:
    """mode별 최종 keep 개수와 rerank weight 기본값을 반환한다."""
    mode_norm = str(mode or "").strip().lower()
    if mode_norm == "lookup":
        return {"final_keep": 80, "rerank_weights": {"lexical": 1.2, "dense": 1.0, "tag": 0.5}}
    if mode_norm == "join":
        return {"final_keep": 120, "rerank_weights": {"lexical": 1.1, "dense": 1.0, "join_key": 0.8}}
    return {"final_keep": 80, "rerank_weights": {"lexical": 1.0, "dense": 1.0}}


def resolve_sparse_vector_name(
    *,
    runtime_sparse_vector_name: Optional[str],
    preset_sparse_vector_name: Optional[str],
) -> tuple[str, str]:
    """runtime arg, preset, env, default 순서로 sparse vector 이름과 출처를 결정한다."""
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
    """SearchPreset에 sparse 실행값을 덮어 실행용 top-k 스펙 dict를 만든다."""
    spec = dict(preset.as_debug())
    spec["top_k_dense"] = int(spec.get("top_k_dense", preset.top_k_dense))
    spec["top_k_lex_cand"] = int(spec.get("top_k_lex_cand", preset.top_k_lex_cand))
    spec["top_k_lex"] = int(spec.get("top_k_lex", preset.top_k_lex))
    spec["use_dense_threshold"] = bool(spec.get("use_dense_threshold", preset.use_dense_threshold))
    spec["min_dense_score"] = float(spec.get("min_dense_score", preset.min_dense_score))
    spec.update({"sparse_vector_name": sparse_vector_name, "sparse_topk": int(sparse_topk), "sparse_weight": float(sparse_weight)})
    return spec


def parse_vecsets_env(s: str) -> Dict[str, set]:
    """RAG_VECSETS 환경변수를 `collection:vec1,vec2` 지도로 파싱한다."""
    out: Dict[str, set] = {}
    text = (s or "").strip()
    if not text:
        return out
    for part in text.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        col, vecs = part.split(":", 1)
        vset = {v.strip() for v in vecs.split(",") if v.strip()}
        if col.strip() and vset:
            out[col.strip()] = vset
    return out


_VECSETS_MAP = parse_vecsets_env(os.getenv("RAG_VECSETS", ""))
_VECSETS_CACHE: Dict[str, Optional[set]] = {}


def named_vectors_in_collection(client, collection_name: str) -> Optional[set[str]]:
    """컬렉션에서 사용 가능한 named vector 집합을 env override 또는 Qdrant metadata에서 구한다."""
    if _VECSETS_MAP:
        return _VECSETS_MAP.get(collection_name)
    if collection_name in _VECSETS_CACHE:
        return _VECSETS_CACHE[collection_name]
    vecset: Optional[set] = None
    try:
        info = client.get_collection(collection_name)
        params = info.config.params
        vecs = params.vectors
        if isinstance(vecs, dict):
            vecset = set(vecs.keys())
    except Exception:
        vecset = None
    _VECSETS_CACHE[collection_name] = vecset
    return vecset


def _pjt_no_fields() -> List[str]:
    """검색 정책이 우선하는 pjt_no 필드 목록을 돌려준다."""
    return ["pjt_no"]


def _is_people_org_intent(intent: QueryIntent) -> bool:
    """현재 intent가 사람/기관 중심 검색인지 판단한다."""
    if intent.base_route in ("people", "org"):
        return True
    if intent.action == "relation" and intent.relation:
        return any(route in ("people", "org") for route in intent.relation)
    return False


def _prioritize_people_org_fields(preset: SearchPreset, *, intent: QueryIntent, default_weights: Dict[str, float]) -> SearchPreset:
    """사람/기관 intent에서는 해당 lexical field를 앞으로 끌어올려 가중치를 보존한다."""
    if not _is_people_org_intent(intent):
        return preset
    prioritized = [field for field in PEOPLE_ORG_FIELDS if field in preset.lexical_fields]
    if prioritized:
        preset.lexical_fields = prioritized + [field for field in preset.lexical_fields if field not in prioritized]
        for field in prioritized:
            preset.lexical_field_weights.setdefault(field, default_weights.get(field, 1.0))
    return preset


def _ensure_pjt_no_fields(preset: SearchPreset, *, default_weights: Dict[str, float]) -> SearchPreset:
    """pjt_no 필드가 lexical field에 있으면 기본 weight가 비어 있지 않게 보정한다."""
    for field in _pjt_no_fields():
        if field in preset.lexical_fields:
            preset.lexical_field_weights.setdefault(field, default_weights.get(field, 1.0))
    return preset


def _f(name: str, default: float) -> float:
    """환경변수 float 값을 읽고 실패 시 기본값을 쓴다."""
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return float(default)


def _i(name: str, default: int) -> int:
    """환경변수 int 값을 읽고 실패 시 기본값을 쓴다."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return int(default)


def build_search_preset(intent: QueryIntent) -> SearchPreset:
    """intent action과 route에 따라 SearchPreset 기본값을 고른다.
    
    support, relation, id_exact, id_fuzzy, filter/list, topic 경로를 나눠 top-k와 sparse/dense 비중, context 예산을 고정한다."""
    default_title_w = _f("RAG_W_TITLE", 5.0)
    default_content_w = _f("RAG_W_CONTENT", 3.0)
    default_keyword_w = _f("RAG_W_KEYWORD_TEXT", 3.0)
    default_flat_w = _f("RAG_W_FLAT_TEXT", 2.0)
    default_sparse_vector = os.getenv("RAG_SPARSE_VECTOR_NAME", "bm25").strip()
    base_fields = ["title_text", "content_text", "keyword_text", "flat_text"]
    weights = {"title_text": default_title_w, "content_text": default_content_w, "keyword_text": default_keyword_w, "flat_text": default_flat_w}
    action = intent.action
    action_for_preset = "id_exact" if (action == "detail" and bool(getattr(intent, "is_id_query", False))) else action

    def finish(preset: SearchPreset) -> SearchPreset:
        """preset에 pjt_no 필드와 people/org 우선순위 보정을 한 번에 적용한다."""
        return _prioritize_people_org_fields(_ensure_pjt_no_fields(preset, default_weights=weights), intent=intent, default_weights=weights)

    if action_for_preset == "support":
        top_k_lex = _i("RAG_TOPK_LEX_SUPPORT", _i("RAG_TOPK_LEX", 50))
        w_lex = _f("RAG_W_LEX_SUPPORT", _f("RAG_W_LEX", 0.25))
        return finish(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_SUPPORT", _i("RAG_TOPK_DENSE", 25)), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_SUPPORT", _i("RAG_TOPK_LEX_CAND", 250)), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_SUPPORT", "0") == "1"), min_dense_score=_f("RAG_MIN_DENSE_SCORE_SUPPORT", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_SUPPORT", _i("RAG_MIN_RERANKED", 4)), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_SUPPORT", _i("RAG_MAX_CONTEXT_ITEMS", 12)), tag_boost=_f("RAG_TAG_BOOST_SUPPORT", _f("RAG_TAG_BOOST", 0.4)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_SUPPORT", _f("RAG_TAG_MISMATCH_PENALTY", 0.0))))
    if action_for_preset == "relation":
        top_k_lex = _i("RAG_TOPK_LEX_REL", 180)
        w_lex = _f("RAG_W_LEX_REL", 0.65)
        return finish(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_REL", 16), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_REL", 900), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=False, min_dense_score=_f("RAG_MIN_DENSE_SCORE_REL", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_REL", 4), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_REL", 12), tag_boost=_f("RAG_TAG_BOOST_REL", _f("RAG_TAG_BOOST", 1.0)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_REL", _f("RAG_TAG_MISMATCH_PENALTY", 0.2))))
    if action_for_preset == "id_exact":
        top_k_lex = _i("RAG_TOPK_LEX_ID_EXACT", 160)
        w_lex = _f("RAG_W_LEX_ID_EXACT", 0.78)
        return finish(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_ID_EXACT", 8), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID_EXACT", 1200), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=False, min_dense_score=_f("RAG_MIN_DENSE_SCORE_ID_EXACT", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_ID_EXACT", 2), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID_EXACT", 3), stop_if_top1_confident=True, tag_boost=_f("RAG_TAG_BOOST_ID_EXACT", _f("RAG_TAG_BOOST", 1.2)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID_EXACT", _f("RAG_TAG_MISMATCH_PENALTY", 0.3))))
    if action_for_preset == "id_fuzzy":
        top_k_lex = _i("RAG_TOPK_LEX_ID", 120)
        w_lex = _f("RAG_W_LEX_ID", 0.60)
        return finish(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_ID", 12), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_ID", 600), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=False, min_dense_score=_f("RAG_MIN_DENSE_SCORE_ID", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_ID", 3), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_ID", 6), tag_boost=_f("RAG_TAG_BOOST_ID", _f("RAG_TAG_BOOST", 0.9)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_ID", _f("RAG_TAG_MISMATCH_PENALTY", 0.2))))
    if action_for_preset in ("list", "download", "stats"):
        top_k_lex = _i("RAG_TOPK_LEX_FILTER", 180)
        w_lex = _f("RAG_W_LEX_FILTER", 0.65)
        preset = SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_FILTER", 16), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_FILTER", 900), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=False, min_dense_score=_f("RAG_MIN_DENSE_SCORE_FILTER", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_FILTER", 4), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_FILTER", 10), tag_boost=_f("RAG_TAG_BOOST_FILTER", _f("RAG_TAG_BOOST", 1.0)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_FILTER", _f("RAG_TAG_MISMATCH_PENALTY", 0.25)))
        if intent.base_route == "people":
            preset.top_k_lex_cand = max(preset.top_k_lex_cand, _i("RAG_TOPK_LEX_CAND_PEOPLE", 1200))
            preset.top_k_lex = max(preset.top_k_lex, _i("RAG_TOPK_LEX_PEOPLE", 240))
        if intent.base_route == "project" and intent.org_terms:
            preset.use_org_filter = True
            preset.org_lex_boost = True
        return finish(preset)
    if action_for_preset == "topic":
        top_k_lex = _i("RAG_TOPK_LEX_TOPIC", 80)
        w_lex = _f("RAG_W_LEX_TOPIC", 0.22)
        return _prioritize_people_org_fields(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE_TOPIC", 45), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND_TOPIC", 450), top_k_lex=top_k_lex, w_lex=w_lex, lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=top_k_lex, sparse_weight=w_lex, use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD_TOPIC", "1") == "1"), min_dense_score=_f("RAG_MIN_DENSE_SCORE_TOPIC", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED_TOPIC", 4), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS_TOPIC", 12), tag_boost=_f("RAG_TAG_BOOST_TOPIC", _f("RAG_TAG_BOOST", 0.6)), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY_TOPIC", _f("RAG_TAG_MISMATCH_PENALTY", 0.1))), intent=intent, default_weights=weights)
    return _prioritize_people_org_fields(SearchPreset(top_k_dense=_i("RAG_TOPK_DENSE", 25), top_k_lex_cand=_i("RAG_TOPK_LEX_CAND", 250), top_k_lex=_i("RAG_TOPK_LEX", 50), w_lex=_f("RAG_W_LEX", 0.25), lexical_fields=base_fields, lexical_field_weights=weights, sparse_vector_name=default_sparse_vector, sparse_topk=_i("RAG_TOPK_LEX", 50), sparse_weight=_f("RAG_W_LEX", 0.25), use_dense_threshold=(os.getenv("RAG_USE_DENSE_THRESHOLD", "1") == "1"), min_dense_score=_f("RAG_MIN_DENSE_SCORE_DEFAULT", _f("RAG_MIN_DENSE_SCORE", 0.52)), min_reranked=_i("RAG_MIN_RERANKED", 4), max_ctx_items=_i("RAG_MAX_CONTEXT_ITEMS", 30), tag_boost=_f("RAG_TAG_BOOST", 0.6), tag_mismatch_penalty=_f("RAG_TAG_MISMATCH_PENALTY", 0.1)), intent=intent, default_weights=weights)


def _log_project_key_policy_once() -> None:
    """project key 정책을 한 번만 로그에 남긴다."""
    global _PROJECT_KEY_POLICY_LOGGED
    if _PROJECT_KEY_POLICY_LOGGED:
        return
    _PROJECT_KEY_POLICY_LOGGED = True
    logger.info("project key policy mode=top-level-only")


def _as_list(value: Any) -> List[str]:
    """scalar/list/set 값을 빈 값·none·중복을 제거한 문자열 리스트로 통일한다."""
    if value is None:
        return []
    seq = list(value) if isinstance(value, (list, tuple, set)) else [value]
    out: List[str] = []
    seen: set[str] = set()
    for v in seq:
        text = str(v).strip()
        if not text or text.lower() in ("none", "null") or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _payload_get(payload: Dict[str, Any], *keys: str) -> List[str]:
    """payload에서 여러 dotted key 후보를 순차적으로 꼭 집어 리스트로 모은다."""
    values: List[str] = []
    for key in keys:
        if "." not in key:
            values.extend(_as_list(payload.get(key)))
            continue
        cur: Any = payload
        ok = True
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur.get(part)
        if ok:
            values.extend(_as_list(cur))
    return values


def _extract_ids_from_hits(search_hits: List[Any], *, limit: int = 20) -> Dict[str, List[str]]:
    """검색 hit payload에서 pjt_id, pjt_no, rst_id, doi, issn 등 후속 모드 승격에 쓸 id를 추출한다."""
    out: Dict[str, List[str]] = {"pjt_id": [], "pjt_no": [], "rst_id": [], "doi": [], "issn": [], "patent_reg_no": []}

    def _add(key: str, values: List[str]) -> None:
        """id 목록에 중복 없이 값을 추가한다."""
        for value in values:
            if value not in out[key]:
                out[key].append(value)

    for hit in (search_hits or [])[: max(1, int(limit))]:
        payload = getattr(hit, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        _log_project_key_policy_once()
        _add("pjt_id", _payload_get(payload, "pjt_id"))
        _add("pjt_no", _payload_get(payload, "pjt_no"))
        _add("rst_id", _payload_get(payload, "rst_id", "meta_basic.rst_id", "id"))
        _add("doi", _payload_get(payload, "doi", "meta_basic.doi"))
        _add("issn", _payload_get(payload, "issn", "eissn", "pissn", "meta_basic.issn"))
        _add("patent_reg_no", _payload_get(payload, "patent_reg_no", "meta_basic.patent_reg_no"))
    return out


def _merge_ids_map(base: Dict[str, List[str]], extra: Dict[str, List[str]]) -> Dict[str, List[str]]:
    """기존 ids_map과 search hit에서 추출한 ids_map를 합친다."""
    merged: Dict[str, List[str]] = {}
    for key in set((base or {}).keys()) | set((extra or {}).keys()):
        merged[key] = _as_list((base or {}).get(key))
        for value in _as_list((extra or {}).get(key)):
            if value not in merged[key]:
                merged[key].append(value)
    return merged


def _infer_join_relation(planner_relation: Any, planner_action: Optional[str]) -> Optional[str]:
    """planner relation/action에서 project_perf 또는 perf_project JOIN 의도를 정규화한다."""
    if isinstance(planner_relation, tuple) and len(planner_relation) == 2:
        return f"{planner_relation[0]}_{planner_relation[1]}"
    rel = str(planner_relation or "").strip().lower()
    if rel in ("project_perf", "perf_project"):
        return rel
    if str(planner_action or "").strip().lower() == "relation":
        return "project_perf"
    return None


def promote_mode_from_search_hits(*, current_mode: str, search_hits: List[Any], ids_map: Dict[str, List[str]], planner_strategy: Any) -> Dict[str, Any]:
    """search 결과에서 뽑은 id를 보고 mode를 lookup 또는 join으로 승격할지 판단한다.
    
    이 단계는 planner가 정한 action/relation을 존중하되, hit에서 실제 id가 나왔을 때만 승격을 허용한다."""
    mode_now = str(current_mode or "").strip().lower() or "search"
    merged_ids_map: Dict[str, List[str]] = dict(ids_map or {}) if isinstance(ids_map, dict) else {}
    planner_mode = str(getattr(planner_strategy, "mode", "") or "").strip().lower() or None
    planner_action = str(getattr(planner_strategy, "action", "") or "").strip().lower() or None
    planner_relation = getattr(planner_strategy, "relation", None)
    if mode_now != "search":
        return {"mode": mode_now, "kind": None, "reason": "non_search_mode_passthrough", "ids_map": merged_ids_map, "planner_mode": planner_mode, "planner_action": planner_action, "planner_relation": planner_relation, "query_text": None, "strategy_key": build_strategy_key(planner_action, mode_now), "allowed": {"policy": "search_only", "lookup": False, "join": False}, "signals": {"has_pjt_id": int(bool((merged_ids_map.get("pjt_id") or []))), "has_pjt_no": int(bool((merged_ids_map.get("pjt_no") or []))), "has_history_nuance": 0, "has_detail_nuance": 0, "wants_project_perf_relation": 0}}
    extracted = _extract_ids_from_hits(search_hits)
    merged_ids_map = _merge_ids_map(merged_ids_map, extracted)
    has_pjt_id = bool(merged_ids_map.get("pjt_id"))
    has_pjt_no = bool(merged_ids_map.get("pjt_no"))
    has_perf_id = bool(merged_ids_map.get("rst_id") or merged_ids_map.get("doi") or merged_ids_map.get("issn") or merged_ids_map.get("patent_reg_no"))
    action_like_lookup = planner_action in ("list", "detail", "stats", "download", "id_exact", "id_fuzzy", "relation")
    wants_join = bool(_infer_join_relation(planner_relation, planner_action) in ("project_perf", "perf_project") or planner_action == "relation")
    promote_mode = mode_now
    kind = None
    reason = "promotion_conditions_not_met"
    if action_like_lookup and (has_pjt_id or has_pjt_no or has_perf_id):
        if wants_join:
            promote_mode = "join"
            kind = "search_to_join"
            reason = "search_hit_ids_and_relation_action"
        else:
            promote_mode = "lookup"
            kind = "search_to_lookup"
            reason = "search_hit_ids_and_lookup_like_action"
    return {"mode": promote_mode, "kind": kind, "reason": reason, "ids_map": merged_ids_map, "planner_mode": planner_mode, "planner_action": planner_action, "planner_relation": planner_relation, "query_text": None, "strategy_key": build_strategy_key(planner_action, promote_mode), "allowed": {"policy": "search_hit_promotion", "lookup": bool(action_like_lookup and (has_pjt_id or has_pjt_no or has_perf_id)), "join": bool(wants_join and (has_pjt_id or has_pjt_no or has_perf_id))}, "signals": {"has_pjt_id": int(has_pjt_id), "has_pjt_no": int(has_pjt_no), "has_perf_id": int(has_perf_id), "has_history_nuance": 0, "has_detail_nuance": int(planner_action in ("detail", "stats", "list")), "wants_project_perf_relation": int(wants_join)}}
