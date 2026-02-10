# -*- coding: utf-8 -*-
"""
rag_pipeline.py (redesigned)

핵심 목표
- SEARCH / LOOKUP / JOIN 을 모드로 분리해 "필터의 역할"을 설계로 고정한다.
- SEARCH: 모든 컬렉션에서 얇고 넓게 후보 탐색 -> (약한 RRF) + (강한 키워드/소프트필터)로 최종 랭킹
- LOOKUP(list/stats/download, id query 등): 서버단 필터로 후보군을 먼저 좁힘 -> 소프트 랭킹으로 마무리
- JOIN(2-hop): Hop1=SEARCH로 join-key 확보 -> Hop2=JOIN 필터로 강제 제한 + 소프트 랭킹

의존
- build_rag_objects(): qdr/emb 2종
- dense_retrieve_hybrid_multi(): dense+lexical 후보를 dict로 반환
- build_context_mixed(): 문서형 컨텍스트 빌더
- (옵션) rag_parts/* 유틸들
"""
from __future__ import annotations
from collections.abc import Mapping
import os
import re
import time
import inspect
import json
from pprint import pformat
from dataclasses import dataclass, fields, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple
from rag_parts.pipeline_steps import NormalizedIntent, classify_query_compat, normalize_intent
from schemas import ExecutionContext, QueryPlan, StrategySpec
from settings import (
    DEFAULT_MODEL_NAME,
    logger,
    get_ctx_token_budget,
    get_model_max_output_tokens,
    RAG_COLLECTION_ALLOWLIST,
)
from rag_types import RagResult
from rag_store import build_rag_objects
from retrieval import (
    normalize_query,
    dense_retrieve_hybrid_multi,
    build_context_mixed,
    _payload_get,
)

# -------------------------
# rag_parts imports
# -------------------------
from rag_parts.constants import (
    COL_SUPPORT,
    COL_PROJECT,
    COL_PERF,
    PROJECT_TAGS,
    PERF_TAGS,
    TAG_PJT_INFO,
    TAG_PJT_MP,
    TAG_PJT_ORG,
    normalize_perf_types,
)
from rag_parts.query_intent import (
    get_relation_route,
    relation_target_collections,
    normalize_categories,
)
from rag_parts.search_preset import (
    SearchPreset as _SearchPreset,
    build_search_preset as _build_search_preset,
    build_topk_spec as _build_topk_spec,
)
from rag_parts.search_strategy import (
    SEARCH_POLICY_VERSION,
    build_strategy_key,
    build_rerank_spec as _build_rerank_spec,
)
from rag_parts.planner_contract import (
    planner_contract_mode,
    normalize_lookup_filter_policy,
    validate_planner_contract,
    StrategyViolation,
)
from rag_parts.vecsets import named_vectors_in_collection as _named_vectors_in_collection
from rag_parts.post_policy import (
    dedup_by_doc_id as _dedup_by_doc_id,
    tag_match_bonus as _tag_match_bonus,
)
from rag_parts.result_contract import enforce_reranked_contract as _enforce_reranked_contract
from rag_parts.join import (
    extract_pjt_ids as _extract_pjt_ids,
    normalize_relation_hint as _normalize_relation_hint,
)
from rag_parts.filters import (
    build_tag_only_filter as _build_tag_only_filter,
    build_join_filter as build_join_filter,
    build_perf_filter_by_pjt_id,
    build_perf_filter_by_pjt_no,
    build_year_range_filter,
    build_perf_type_filter,
    and_filter as _and_filter, build_org_filter, build_prtcp_org_nested_filter, build_people_filter,
    make_match_any,
    build_project_id_filter,
    build_title_filter,
    validate_join_mode_key_inputs,
    JoinFilterInput,
    PeopleFilterInput, OrgFilterInput,
)

try:
    from qdrant_client.http import models as qmodels
except Exception:
    qmodels = None


def _normalize_tag_value(tag: object) -> str:
    if tag is None:
        return ""
    t = str(tag).strip().upper()
    return t[4:] if t.startswith("IRD_") else t

PROJECT_TAGS_NORM = {_normalize_tag_value(t) for t in PROJECT_TAGS}
PERF_TAGS_NORM = {_normalize_tag_value(t) for t in PERF_TAGS}

def _classify_tag_family(tag: object) -> str:
    norm = _normalize_tag_value(tag)
    if not norm:
        return "other"
    if norm.startswith("NAI_PJT_") or norm in PROJECT_TAGS_NORM:
        return "project"
    if norm.startswith("NAI_RI_") or norm in PERF_TAGS_NORM:
        return "perf"
    return "other"

def _split_tag_filters_by_family(tag_filters: Iterable[object]) -> tuple[list[str], list[str], list[str]]:
    project_tags: list[str] = []
    perf_tags: list[str] = []
    other_tags: list[str] = []
    for tag in (tag_filters or []):
        tag_str = str(tag).strip()
        if not tag_str:
            continue
        family = _classify_tag_family(tag_str)
        if family == "project":
            project_tags.append(tag_str)
        elif family == "perf":
            perf_tags.append(tag_str)
        else:
            other_tags.append(tag_str)
    return project_tags, perf_tags, other_tags

# =====================================================================
# Pretty / Section Logging (RAG)  ✅✅ 상세 로그 트래킹 유틸
# =====================================================================

def _rag_debug_on() -> bool:
    return str(os.getenv("RAG_DEBUG", "1")).strip().lower() in ("1", "true", "yes", "y")

def _rag_color_on() -> bool:
    # 파일 로깅이면 ANSI가 지저분할 수 있으니 기본 OFF
    return str(os.getenv("RAG_LOG_COLOR", "0")).strip().lower() in ("1", "true", "yes", "y")

def _clip_text(s: str, max_chars: int) -> str:
    if s is None:
        return ""
    s = str(s)
    if max_chars > 0 and len(s) > max_chars:
        return s[: max_chars - 1] + "…(trunc)"
    return s

def _safe_json(obj: object) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return pformat(obj, width=120, compact=True)

def log_section(title: str, content: object = None, *, level: str = "info", max_chars: int = None) -> None:
    """
    RAG_DEBUG=1 일 때만 출력.
    - content: str/dict/list/anything
    - max_chars: 환경변수 RAG_LOG_MAX_CHARS(기본 6000)로 제한
    """
    if not _rag_debug_on():
        return

    lvl = (level or "info").lower().strip()
    log_fn = getattr(logger, lvl, logger.info)

    max_chars = int(max_chars) if max_chars is not None else int(os.getenv("RAG_LOG_MAX_CHARS", "6000"))

    if content is None:
        body = ""
    elif isinstance(content, str):
        body = content
    else:
        body = _safe_json(content)

    body = _clip_text(body, max_chars)

    if _rag_color_on():
        header = f"\n\033[96m{'='*10} [{title}] {'='*10}\033[0m"
        footer = f"\033[96m{'='*36}\033[0m\n"
    else:
        header = f"\n{'='*10} [{title}] {'='*10}"
        footer = f"{'='*36}\n"

    log_fn(f"{header}\n{body}\n{footer}")

def log_kv(title: str, *, level: str = "info", **kwargs) -> None:
    """key=value를 한 섹션으로 예쁘게."""
    if not _rag_debug_on():
        return
    payload = {}
    for k, v in kwargs.items():
        if isinstance(v, str):
            payload[k] = _clip_text(v, int(os.getenv("RAG_LOG_KV_STR_MAX", "240")))
        else:
            payload[k] = v
    log_section(title, payload, level=level)

def _point_summary(p: Any) -> Dict[str, Any]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = _get_meta(pl)

    def pick(*vals):
        for v in vals:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                return s
        return ""

    title = pick(
        pl.get("title_text"),
        pl.get("title1"),
        pl.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
    )

    tag = pick(pl.get("tag"))
    doc_id = pick(pl.get("doc_id"), getattr(p, "id", None))
    col = pick(_resolve_collection(p, pl))

    sc = getattr(p, "score", None)
    try:
        sc = float(sc) if sc is not None else None
    except Exception:
        sc = None

    return {
        "col": col,
        "doc_id": doc_id,
        "tag": tag,
        "score": sc,
        "_rrf": pl.get("_rrf"),
        "_final_total": pl.get("_final_total"),
        "_final_rrf": pl.get("_final_rrf"),
        "_final_kw": pl.get("_final_kw"),
        "_final_f": pl.get("_final_f"),
        "title": _clip_text(title, int(os.getenv("RAG_LOG_TITLE_MAX", "180"))),
    }

def log_top_points(title: str, points: List[Any], *, topn: int = None, level: str = "info") -> None:
    """후보/리랭크 결과 TopN 요약."""
    if not _rag_debug_on():
        return
    topn = int(topn) if topn is not None else int(os.getenv("RAG_LOG_TOPN", "8"))
    arr = []
    for p in (points or [])[: max(0, topn)]:
        arr.append(_point_summary(p))
    log_section(title, arr, level=level)



# =====================================================================
# Timings 규칙
# - phase.*: 파이프라인 단계 소요 시간(초)
# - col.<collection>.stats.*: 컬렉션별 통계(힛/스코어/합계)
# - col.<collection>.phase.*: 컬렉션별 검색 단계 소요 시간(초)
# - metric.*: 품질 측정 지표(점수 등)
# - flag.*: bool/indicator (0.0/1.0)
# - info.*: 메타 정보(예: contract_fail_reason, ctx_budget)
# - event.*: 오류/예외 표시(0.0/1.0)
# =====================================================================

_TIMING_DEFAULTS: Dict[str, Any] = {
    "phase.stack_init": 0.0,
    "phase.kw_det": 0.0,
    "phase.dense_search": 0.0,
    "phase.rrf_merge": 0.0,
    "phase.final_rerank": 0.0,
    "phase.build_context": 0.0,
    "phase.hydrate_full_payload": 0.0,
    "phase.hop_total": 0.0,
    "phase.total": 0.0,
    "info.ctx_budget": 0.0,
    "info.contract_fail_reason": "",
    "metric.final_score_avg": 0.0,
    "metric.final_score_max": 0.0,
    "event.embed_precompute_error": 0.0,
}

def _init_timings() -> Dict[str, Any]:
    return dict(_TIMING_DEFAULTS)

def _timing_put(timings: Dict[str, Any], key: str, value: Any) -> None:
    if key in timings:
        default_val = _TIMING_DEFAULTS.get(key, None)
        if key in _TIMING_DEFAULTS and timings[key] == default_val:
            timings[key] = value
            return
        if timings[key] == value:
            return
        idx = 2
        next_key = f"{key}.dup{idx}"
        while next_key in timings:
            idx += 1
            next_key = f"{key}.dup{idx}"
        timings[next_key] = value
        return
    timings[key] = value

def _record_col_timings(
    timings: Dict[str, Any],
    col: str,
    *,
    stats: Dict[str, float],
    local_timings: Dict[str, float],
) -> None:
    prefix = f"col.{col}"
    for k, v in (stats or {}).items():
        _timing_put(timings, f"{prefix}.stats.{k}", float(v))
    for k, v in (local_timings or {}).items():
        try:
            _timing_put(timings, f"{prefix}.phase.{k}", float(v))
        except Exception:
            continue

# =====================================================================

# -------------------------
# Lightweight list/stats context
# -------------------------
def _clean_one_line(s: object, max_len: int = 160) -> str:
    if s is None:
        return ""
    t = str(s).replace("\r", " ").replace("\n", " ").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) > max_len:
        t = t[: max_len - 1] + "…"
    return t

def _get_meta(pl: dict) -> dict:
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        v = pl.get(key)
        if isinstance(v, dict):
            merged.update(v)
    return merged

def _count_missing_join_keys(points: Iterable[Any]) -> Dict[str, int]:
    stats = {
        "total": 0,
        "missing_pjt_id": 0,
        "missing_pjt_no": 0,
        "missing_tag": 0,
        "missing_pjt_any": 0,
    }
    for p in points or []:
        payload = getattr(p, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        stats["total"] += 1
        pjt_id = str(payload.get("pjt_id") or "").strip()
        pjt_no = str(payload.get("pjt_no") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        if not pjt_id:
            stats["missing_pjt_id"] += 1
        if not pjt_no:
            stats["missing_pjt_no"] += 1
        if not tag:
            stats["missing_tag"] += 1
        if not pjt_id and not pjt_no:
            stats["missing_pjt_any"] += 1
    return stats

def _ensure_join_keys_in_payload(
    points: Iterable[Any],
    *,
    force_from_meta: bool = True,
    force_tag_from_tags: bool = True,
) -> Dict[str, int]:
    stats = {
        "total": 0,
        "forced_pjt_id": 0,
        "forced_pjt_no": 0,
        "forced_tag": 0,
    }
    for p in points or []:
        payload = getattr(p, "payload", None)
        if not isinstance(payload, dict):
            continue
        stats["total"] += 1
        meta = _get_meta(payload)
        if force_from_meta:
            if not payload.get("pjt_id"):
                candidate = _pick_first(payload.get("pjt_id"), meta.get("pjt_id"))
                if candidate:
                    payload["pjt_id"] = candidate
                    stats["forced_pjt_id"] += 1
            if not payload.get("pjt_no"):
                candidate = _pick_first(payload.get("pjt_no"), meta.get("pjt_no"))
                if candidate:
                    payload["pjt_no"] = candidate
                    stats["forced_pjt_no"] += 1
        if not payload.get("tag"):
            candidate = ""
            if force_tag_from_tags:
                tags_value = payload.get("tags")
                if isinstance(tags_value, list) and tags_value:
                    candidate = str(tags_value[0]).strip()
            if not candidate and force_from_meta:
                candidate = _pick_first(meta.get("tag"))
            if candidate:
                payload["tag"] = candidate
                stats["forced_tag"] += 1
    return stats

def _debug_force_join_keys_enabled() -> bool:
    return str(os.getenv("RAG_DEBUG_FORCE_JOIN_KEYS", "0")).strip().lower() in ("1", "true", "yes", "y")

def _raise_on_missing_join_keys(
    points: Iterable[Any],
    *,
    scope: str,
) -> Dict[str, int]:
    missing = _count_missing_join_keys(points)
    if not (missing.get("missing_pjt_any") or missing.get("missing_tag")):
        return missing

    log_kv(
        "RAG.JOIN_KEYS.MISSING",
        level="error",
        scope=scope,
        missing_pjt_id=int(missing.get("missing_pjt_id", 0) or 0),
        missing_pjt_no=int(missing.get("missing_pjt_no", 0) or 0),
        missing_pjt_any=int(missing.get("missing_pjt_any", 0) or 0),
        missing_tag=int(missing.get("missing_tag", 0) or 0),
        total=int(missing.get("total", 0) or 0),
    )

    if _debug_force_join_keys_enabled():
        forced = _ensure_join_keys_in_payload(points)
        log_kv("RAG.JOIN_KEYS.DEBUG_FORCE", level="warning", scope=scope, **forced)
        missing = _count_missing_join_keys(points)
        if not (missing.get("missing_pjt_any") or missing.get("missing_tag")):
            return missing
        log_kv(
            "RAG.JOIN_KEYS.DEBUG_FORCE_FAILED",
            level="error",
            scope=scope,
            missing_pjt_id=int(missing.get("missing_pjt_id", 0) or 0),
            missing_pjt_no=int(missing.get("missing_pjt_no", 0) or 0),
            missing_pjt_any=int(missing.get("missing_pjt_any", 0) or 0),
            missing_tag=int(missing.get("missing_tag", 0) or 0),
            total=int(missing.get("total", 0) or 0),
        )

    raise StrategyViolation(
        error_code="JOIN_KEYS_MISSING",
        reason=(
            f"[{scope}] missing join keys in hop1 payload: "
            f"missing_pjt_id={missing.get('missing_pjt_id', 0)}, "
            f"missing_pjt_no={missing.get('missing_pjt_no', 0)}, "
            f"missing_pjt_any={missing.get('missing_pjt_any', 0)}, "
            f"missing_tag={missing.get('missing_tag', 0)}, "
            f"total={missing.get('total', 0)}"
        ),
    )

def _extract_pjt_nos(points: Iterable[Any], *, max_ids: int = 80) -> List[str]:
    pjt_nos: List[str] = []
    seen: set[str] = set()
    for p in points or []:
        payload = getattr(p, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        meta = _get_meta(payload)
        pjt_no = _pick_first(payload.get("pjt_no"), meta.get("pjt_no"))
        if not pjt_no or pjt_no in seen:
            continue
        seen.add(pjt_no)
        pjt_nos.append(pjt_no)
        if len(pjt_nos) >= max_ids:
            break
    return pjt_nos


def _ensure_join_mode_has_keys(
    *,
    has_join_keys: bool,
    join_key_mode: str,
    hop1_top: List[Any],
    hop1_col: str,
) -> None:
    """
    mode=join 계약:
    - Hop1는 join key 추출 전용 단계이며, key가 없으면 Hop2를 생략한 성공 응답을 반환하지 않는다.
    - 결과는 "Hop2 실행 성공" 또는 "명시적 실패(StrategyViolation)"만 허용한다.
    """
    if has_join_keys:
        return

    if hop1_top:
        _raise_on_missing_join_keys(hop1_top, scope=f"join_hop1:{hop1_col}:drop_keys")

    join_key_label = "PJT_NO" if join_key_mode == "group" else "PJT_ID"
    raise StrategyViolation(
        error_code="JOIN_KEYS_MISSING",
        reason=(
            "mode=join requires Hop2 execution, but join keys were not extracted "
            f"from Hop1 ({join_key_label} missing)."
        ),
    )

def _approx_token_len(text: str) -> int:
    """토크나이저 없이 예산 기반 컷오프용 근사치."""
    if not text:
        return 0
    words = len(text.split())
    return max(words, int(len(text) / 4))

def _get_ctx_hard_limit() -> int:
    return max(1, int(os.getenv("RAG_CTX_HARD_LIMIT", "200")))

def _get_list_ctx_token_budget() -> int:
    return int(os.getenv("RAG_LIST_CTX_TOKEN_BUDGET", os.getenv("CTX_TOKEN_BUDGET", "2048")))

def _pick_first(*vals: object) -> str:
    for v in vals:
        if v is None:
            continue
        sv = str(v).strip()
        if sv:
            return sv
    return ""

def _pick_nested_first(pl: Dict[str, Any], list_key: str, field_key: str) -> str:
    items = pl.get(list_key)
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict):
            v = item.get(field_key)
            if v not in (None, ""):
                return str(v).strip()
    return ""

def _payload_title(pl: Dict[str, Any], meta: Dict[str, Any]) -> str:
    return _pick_first(
        pl.get("title_text"),
        pl.get("title1"),
        pl.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
    )

def build_context_list_light(
        points: List[Any],
        *,
        kind: str,
        max_items: int,
        query_text: str = "",
        token_budget: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """목록/통계형 질의용 경량 컨텍스트."""
    items: List[str] = []
    kind = (kind or "").lower().strip() or "project"
    date_year_pattern = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
    token_budget = _get_list_ctx_token_budget() if token_budget is None else int(token_budget)
    total_tok = 0

    def _normalize_year(value: Any) -> Any:
        if value is None:
            return value
        s = str(value).strip()
        if not s:
            return value
        match = date_year_pattern.match(s)
        if match:
            return match.group(1)
        return value

    def _pjt_id(pl):
        meta_basic = pl.get("meta_basic")
        if not isinstance(meta_basic, dict):
            meta_basic = {}
        return _pick_first(pl.get("pjt_id"))

    for p in (points or [])[: max(0, int(max_items))]:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            continue
        meta = _get_meta(pl)

        if kind == "project":
            title = _payload_title(pl, meta)
            pjt_id = _pjt_id(pl)
            org = _pick_first(
                pl.get("org_nm")
            )
            year = _pick_first(pl.get("stan_yr"), meta.get("stan_yr"))
            year = _normalize_year(year)
            line = f"- {_clean_one_line(title, 180)}"
            extra: List[str] = []
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if org: extra.append(_clean_one_line(org, 60))
            if year: extra.append(str(year))
            if extra: line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        if kind == "people":
            name = _pick_nested_first(pl, "prtcp_mp", "hm_nm")
            role = _pick_nested_first(pl, "prtcp_mp", "role_slct_nm")
            org = _pick_nested_first(pl, "prtcp_mp", "blng_org_nm")
            pjt_id = _pjt_id(pl)
            line = f"- {_clean_one_line(name or '(이름없음)', 80)}"
            extra: List[str] = []
            if role: extra.append(_clean_one_line(role, 30))
            if org: extra.append(_clean_one_line(org, 50))
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if extra: line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        if kind == "org":
            org = _pick_first(
                pl.get("org_nm"),
                _pick_nested_first(pl, "prtcp_org", "org_nm"),
            )
            role = _pick_nested_first(pl, "prtcp_org", "org_slct_nm")
            pjt_id = _pjt_id(pl)
            line = f"- {_clean_one_line(org or '(기관없음)', 100)}"
            extra: List[str] = []
            if role: extra.append(_clean_one_line(role, 30))
            if pjt_id: extra.append(f"PJT_ID={pjt_id}")
            if extra: line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        # perf default
        title = _payload_title(pl, meta)
        pjt_name = _pick_first(meta.get("kor_pjt_nm"), meta.get("eng_pjt_nm"))
        pjt_id = _pjt_id(pl)
        perf_type = _pick_first(pl.get("tag"))
        year = _pick_first(pl.get("dt1"), pl.get("dt2"), pl.get("stan_yr"), meta.get("stan_yr"))

        line = f"- {_clean_one_line(title, 180)}"
        extra: List[str] = []
        if perf_type: extra.append(_clean_one_line(perf_type, 32))
        if year: extra.append(str(year))
        if pjt_name or pjt_id: extra.append(_clean_one_line(pjt_name or f"PJT_ID={pjt_id}", 60))
        if extra: line += " (" + ", ".join(extra) + ")"
        tok = _approx_token_len(line)
        if total_tok + tok > token_budget:
            break
        items.append(line)
        total_tok += tok

    header = f"질의: {_clean_one_line(query_text, 120)}\n" if query_text else ""
    if header:
        header_tok = _approx_token_len(header)
        if header_tok + total_tok > token_budget and items:
            header = ""
        else:
            total_tok += header_tok
    empty_notice = "성과 없음" if kind == "perf" else "(후보 없음)"
    ctx = header + ("\n".join(items) if items else empty_notice)
    return ctx, points


# -------------------------
# Output type -> fieldset
# -------------------------
_OUTPUT_TYPE_FIELDSETS: Dict[str, Tuple[str, ...]] = {
    "list": ("title_text", "org", "year", "id"),
    "detail": ("title_text", "meta_detail", "summary"),
    "stats": ("aggregation_keys",),
    "summary": ("title_text", "meta_basic", "summary", "content"),
    "relation": ("title_text", "relation", "id"),
}


def _normalize_output_type(output_type: Optional[str]) -> Optional[str]:
    ot = (output_type or "").strip().lower()
    return ot or None


def _resolve_output_fieldset(output_type: Optional[str]) -> Tuple[str, ...]:
    ot = _normalize_output_type(output_type) or "summary"
    return _OUTPUT_TYPE_FIELDSETS.get(ot, _OUTPUT_TYPE_FIELDSETS["summary"])


def _should_use_list_context(
    *,
    action: str,
    base_route: str,
    output_type: Optional[str],
) -> bool:
    ot = _normalize_output_type(output_type)
    if ot in ("list", "stats"):
        return base_route in ("project", "perf", "people", "org")
    return action in ("list", "stats", "download") and base_route in ("project", "perf", "people", "org")


def _build_context_with_output_type(
    points: List[Any],
    *,
    action: str,
    base_route: str,
    output_type: Optional[str],
    max_items: int,
    query_text: str,
) -> Tuple[str, List[Dict[str, Any]], Tuple[str, ...]]:
    fieldset = _resolve_output_fieldset(output_type)
    if _should_use_list_context(action=action, base_route=base_route, output_type=output_type):
        context, refs = build_context_list_light(points, kind=base_route, max_items=max_items, query_text=query_text)
        return context, refs, fieldset
    meta_source = "detail_only" if _normalize_output_type(output_type) == "detail" else None
    include_meta_long = _normalize_output_type(output_type) == "detail"
    context, refs = build_context_mixed(
        points,
        max_items=max_items,
        query_text=query_text,
        output_type=output_type,
        fieldset_keys=fieldset,
        meta_source=meta_source,
        include_meta_long=include_meta_long,
    )
    return context, refs, fieldset

# -------------------------
# Precomputed embedding wrapper
# -------------------------
class _PrecomputedEmbedding:
    def __init__(self, vec: List[float]):
        self._vec = vec
    def get_query_embedding(self, _text: str):
        return self._vec
    def get_text_embedding(self, _text: str):
        return self._vec

# -------------------------
# signature-safe wrapper
# -------------------------
_DENSE_MULTI_SIG = None
try:
    _DENSE_MULTI_SIG = inspect.signature(dense_retrieve_hybrid_multi)
except Exception:
    _DENSE_MULTI_SIG = None

def _call_dense_retrieve_hybrid_multi(
        *,
        qdr: Any,
        emb_map: Dict[str, Any],
        qtext: str,
        kws: List[str],
        collection: str,
        lexical_fields: Optional[List[str]],
        sparse_vector_name: Optional[str],
        sparse_topk: Optional[int],
        top_k_dense: int,
        top_k_lex_cand: int,
        top_k_lex: int,
        query_filter: Any,
        timings_out: Dict[str, float],
        require_hybrid_both_sides: bool = False,
        contract_scope: Optional[str] = None,
        violation_on_contract: bool = False,
) -> Dict[str, Any]:
    params = set(_DENSE_MULTI_SIG.parameters.keys()) if _DENSE_MULTI_SIG else set()
    common_kwargs = {
        "emb_map": emb_map,
        "top_k_dense": top_k_dense,
        "top_k_lexical_candidates": top_k_lex_cand,
        "top_k_lexical": top_k_lex,
        "sparse_vector_name": sparse_vector_name,
        "sparse_topk": sparse_topk,
        "timings": timings_out,
    }
    if "lexical_fields" in params:
        common_kwargs["lexical_fields"] = lexical_fields
    if "require_hybrid_both_sides" in params:
        common_kwargs["require_hybrid_both_sides"] = bool(require_hybrid_both_sides)
    if "contract_scope" in params:
        common_kwargs["contract_scope"] = contract_scope

    sparse_vector_name_eff = str(sparse_vector_name or "").strip()
    sparse_topk_int = int(sparse_topk or 0)
    sparse_enabled = bool(sparse_topk_int > 0 and sparse_vector_name_eff)
    top_k_dense_int = int(top_k_dense or 0)
    dense_enabled = bool(top_k_dense_int > 0 and bool(emb_map))

    if require_hybrid_both_sides:
        if not dense_enabled:
            code = "LOOKUP_JOIN_DENSE_REQUIRED" if violation_on_contract else "RAG.CONTRACT"
            msg = (
                f"dense disabled for {contract_scope or collection}: "
                f"top_k_dense={top_k_dense_int} emb_map={bool(emb_map)}"
            )
            if violation_on_contract:
                raise StrategyViolation(error_code=code, reason=msg)
            raise RuntimeError(f"[{code}] {msg}")
        if not sparse_enabled:
            code = "LOOKUP_JOIN_SPARSE_REQUIRED" if violation_on_contract else "RAG.CONTRACT"
            msg = (
                f"sparse disabled for {contract_scope or collection}: "
                f"sparse_topk={sparse_topk_int} sparse_vector_name={sparse_vector_name!r}"
            )
            if violation_on_contract:
                raise StrategyViolation(error_code=code, reason=msg)
            raise RuntimeError(f"[{code}] {msg}")

    if "client" in params and "collection_name" in params:
        return dense_retrieve_hybrid_multi(
            client=qdr,
            expanded_text=qtext,
            keywords=kws,
            collection_name=collection,
            query_filter=query_filter,
            **common_kwargs,
        )

    if "qdr" in params and "collection" in params:
        return dense_retrieve_hybrid_multi(
            qdr=qdr,
            collection=collection,
            query_text=qtext,
            keywords=kws,
            filter_obj=query_filter,
            **common_kwargs,
        )

    # last resort
    return dense_retrieve_hybrid_multi(
        client=qdr,
        expanded_text=qtext,
        keywords=kws,
        collection_name=collection,
        query_filter=query_filter,
        **common_kwargs,
    )

def _validate_lookup_join_hybrid_metrics(
    *,
    mode: str,
    contract_scope: str,
    timings: Mapping[str, Any],
) -> None:
    if str(mode).strip().lower() not in ("lookup", "join"):
        return
    dense_queries = float(timings.get("dense_queries", 0.0) or 0.0)
    sparse_hits = float(timings.get("lexical_scored", timings.get("sparse_hits", 0.0)) or 0.0)
    hybrid_once_hits = float(timings.get("hybrid_once_hits", 0.0) or 0.0)
    sparse_metric = max(sparse_hits, hybrid_once_hits)
    log_kv(
        "RAG.LOOKUP_JOIN.HYBRID.METRICS",
        mode=mode,
        contract_scope=contract_scope,
        dense_queries=dense_queries,
        sparse_hits=sparse_hits,
        hybrid_once_hits=hybrid_once_hits,
    )
    if dense_queries <= 0:
        raise StrategyViolation(
            error_code="LOOKUP_JOIN_DENSE_METRIC_ZERO",
            reason=f"dense_queries must be >0 for {contract_scope}; got {dense_queries}",
        )
    if sparse_metric <= 0:
        raise StrategyViolation(
            error_code="LOOKUP_JOIN_SPARSE_METRIC_ZERO",
            reason=f"sparse_hits/hybrid_once_hits must be >0 for {contract_scope}; got sparse_hits={sparse_hits}, hybrid_once_hits={hybrid_once_hits}",
        )

def _attach_collection(p: Any, col: str) -> Any:
    if p is None or not col:
        return p
    if isinstance(p, dict):
        payload = p.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("_collection", col)
        p["payload"] = payload
        p.setdefault("_collection", col)
        return p
    setattr(p, "_collection", col)
    payload = getattr(p, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
        setattr(p, "payload", payload)
    payload.setdefault("_collection", col)
    return p

def _ensure_collection_mark(points: List[Any], col: str) -> None:
    for p in points or []:
        _attach_collection(p, col)

def _apply_dense_threshold(
    sr: Dict[str, Any],
    *,
    use_dense_threshold: bool,
    min_dense_score: float,
    log_prefix: str,
    col: Optional[str] = None,
    action: Optional[str] = None,
    base_route: Optional[str] = None,
    relation: Optional[Tuple[str, str]] = None,
) -> None:
    dense_map = sr.get("dense")
    if not isinstance(dense_map, dict):
        return
    for vname, lst in dense_map.items():
        before = len(lst or [])
        filtered = list(lst or [])
        if use_dense_threshold:
            filtered = []
            for p in lst or []:
                score = getattr(p, "score", None)
                try:
                    score_val = float(score) if score is not None else None
                except Exception:
                    score_val = None
                if score_val is not None and score_val >= float(min_dense_score):
                    filtered.append(p)
            dense_map[vname] = filtered
        reduced = before - len(filtered)
        reduced_ratio = (float(reduced) / float(before)) if before else 0.0
        log_kv(
            log_prefix,
            col=col,
            vec=str(vname),
            action=action,
            base_route=base_route,
            relation=str(relation) if relation else None,
            applied=bool(use_dense_threshold),
            before=before,
            after=len(filtered),
            reduced=reduced,
            reduced_ratio=round(reduced_ratio, 4),
            min_dense_score=float(min_dense_score),
        )

        score_values: List[float] = []
        for p in filtered:
            score = getattr(p, "score", None)
            try:
                score_val = float(score) if score is not None else None
            except Exception:
                score_val = None
            if score_val is not None:
                score_values.append(score_val)

        if score_values:
            score_values.sort()
            min_score = score_values[0]
            max_score = score_values[-1]
            topn = min(3, len(score_values))
            topn_avg = sum(score_values[-topn:]) / float(topn)

            def _percentile(sorted_vals: List[float], pct: float) -> float:
                if not sorted_vals:
                    return float("nan")
                if len(sorted_vals) == 1:
                    return sorted_vals[0]
                pos = (pct / 100.0) * (len(sorted_vals) - 1)
                lo = int(pos)
                hi = min(lo + 1, len(sorted_vals) - 1)
                frac = pos - lo
                return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac

            log_kv(
                "RAG.DENSE.SCORE.STATS",
                col=col,
                vec=str(vname),
                count=len(score_values),
                min=float(min_score),
                max=float(max_score),
                p50=float(_percentile(score_values, 50.0)),
                p90=float(_percentile(score_values, 90.0)),
                p99=float(_percentile(score_values, 99.0)),
                top3_avg=float(topn_avg),
            )

def _resolve_collection(p: Any, payload: Optional[dict] = None) -> str:
    if payload is not None:
        pl = payload
    elif isinstance(p, dict):
        pl = p.get("payload", None)
    else:
        pl = getattr(p, "payload", None)
    pl = pl or {}
    if not isinstance(pl, dict):
        pl = {}
    col = pl.get("_collection")
    if not col:
        if isinstance(p, dict):
            col = p.get("_collection")
        else:
            col = getattr(p, "_collection", None)
    return str(col) if col else ""

# -------------------------
# RRF (independent / stable)
# -------------------------
@dataclass
class _RankSource:
    name: str
    weight: float
    points: List[Any]

def _hit_key(p: Any) -> Tuple[str, str]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    col = _resolve_collection(p, pl)
    pid = str(getattr(p, "id", "") or pl.get("doc_id") or "")
    return (col, pid)

def _rrf_merge(sources: List[_RankSource], *, rrf_k: int = 60, keep: int = 2000) -> List[Any]:
    """sources 내의 랭킹을 RRF로 합친다(가중치 지원)."""
    score: Dict[Tuple[str, str], float] = {}
    best_obj: Dict[Tuple[str, str], Any] = {}
    for src in sources:
        w = float(src.weight)
        for r, p in enumerate(src.points or []):
            k = _hit_key(p)
            if k not in best_obj:
                best_obj[k] = p
            score[k] = score.get(k, 0.0) + (w / float(rrf_k + r + 1))

    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out: List[Any] = []
    for k, sc in ranked[: max(1, int(keep))]:
        p = best_obj.get(k)
        if p is None:
            continue
        pl = getattr(p, "payload", None)
        if isinstance(pl, dict):
            pl["_rrf"] = float(sc)
        out.append(p)
    return out

def _use_dense_score_weight() -> bool:
    return str(os.getenv("RAG_USE_DENSE_SCORE_WEIGHT", "0")).strip().lower() in ("1", "true", "yes", "y")

def _dense_score_weight(points: List[Any]) -> float:
    score_values: List[float] = []
    for p in points or []:
        score = getattr(p, "score", None)
        try:
            score_val = float(score) if score is not None else None
        except Exception:
            score_val = None
        if score_val is not None:
            score_values.append(score_val)
    if not score_values:
        return 1.0
    min_score = min(score_values)
    max_score = max(score_values)
    if max_score == min_score:
        return 1.0
    norm_scores = [(s - min_score) / float(max_score - min_score) for s in score_values]
    return sum(norm_scores) / float(len(norm_scores))

# -------------------------
# Keyword / Filter soft rerank
# -------------------------
def _to_text(v: object) -> str:
    if v is None:
        return ""
    s = str(v).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()

def _prefer_meta_title(pl: Dict[str, Any], meta: Dict[str, Any]) -> str:
    title = _to_text(pl.get("title_text") or pl.get("title1") or pl.get("title2") or "")
    meta_title = _to_text(meta.get("kor_pjt_nm") or meta.get("eng_pjt_nm") or "")
    if not title:
        return meta_title

    # ✅ pjt_no를 pjt_id 대용으로 쓰지 않는다.
    pjt_id = _to_text(pl.get("pjt_id") or meta.get("pjt_id") or "")

    if meta_title and (title.isdigit() or title.lower().startswith("ntis:") or (pjt_id and title == pjt_id)):
        return meta_title
    return title or meta_title

def _payload_text_bundle(p: Any) -> Dict[str, str]:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    meta = _get_meta(pl)

    title = _prefer_meta_title(pl, meta)
    flat_text = _to_text(
        pl.get("flat_text")
        or pl.get("keyword_text")
        or ""
    )
    content_text = _to_text(
        pl.get("content_text")
        or pl.get("content")
        or pl.get("content1")
        or pl.get("content2")
        or ""
    )
    keyword_text = _to_text(pl.get("keyword_text") or pl.get("keyword1") or pl.get("keyword2") or "")
    category_text = _to_text(pl.get("category") or pl.get("cetegory") or "")
    prtcp_mp_names = _to_text(_payload_get(pl, "prtcp_mp[].hm_nm"))
    prtcp_mp_orgs = _to_text(_payload_get(pl, "prtcp_mp[].blng_org_nm"))
    prtcp_org_names = _to_text(_payload_get(pl, "prtcp_org[].org_nm"))

    meta_kv = []
    for k in (
            "pjt_id",
            "pjt_no",
            "pjt_prfrm_org_nm",
            "kor_pjt_nm",
            "eng_pjt_nm",
            "rndco_tot_amt",
            "rsch_goal_abstract",
            "rsch_abstract",
            "kor_kywd",
            "eng_kywd",
            "rst_id",
            "paper_nm",
            "abstract_str",
            "jrnl_nm",
            "paper_regist_no",
            "paper_type_slct_nm",
            "dmabr_slct_nm",
            "prcd_nm",
            "prcd_venue_nat_nm",
            "prcd_pst_dt",
            "jrnl_pub_dt",
            "jrnl_vol_no",
            "issn",
            "sci_slct_nm",
            "doi",
            "tot_rsch_start_dt",
            "tot_rsch_end_dt",
            "stan_yr",
    ):
        if k in meta and meta.get(k) not in (None, ""):
            meta_kv.append(f"{k}:{_to_text(meta.get(k))}")
    if pl.get("org_nm"):
        meta_kv.append(f"org_nm:{_to_text(pl.get('org_nm'))}")
    if pl.get("category") or pl.get("cetegory"):
        meta_kv.append(f"category:{category_text}")
    if keyword_text:
        meta_kv.append(f"keyword_text:{keyword_text}")
    if prtcp_mp_names:
        meta_kv.append(f"prtcp_mp_hm_nm:{prtcp_mp_names}")
    if prtcp_mp_orgs:
        meta_kv.append(f"prtcp_mp_blng_org_nm:{prtcp_mp_orgs}")
    if prtcp_org_names:
        meta_kv.append(f"prtcp_org_nm:{prtcp_org_names}")
    meta_kv_s = _to_text("; ".join(meta_kv))[:800]

    return {
        "title_text": title,
        "flat_text": flat_text[:2000],
        "content_text": content_text[:2000],
        "keyword_text": keyword_text[:1200],
        "category": category_text[:200],
        "meta_kv": meta_kv_s,
    }

def _count_term_hits(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return text.lower().count(term.lower())

def _keyword_score(p: Any, kws: List[str], w: Dict[str, float]) -> float:
    w = w or {}
    tb = _payload_text_bundle(p)
    w_title = float(w.get("title_text", 2.0))
    w_flat = float(w.get("flat_text", 0.6))
    w_content = float(w.get("content_text", 1.0))
    w_keyword = float(w.get("keyword_text", 0.8))
    w_category = float(w.get("category", 0.0))
    w_meta = float(w.get("meta_kv", 0.0))

    sc = 0.0
    for kw in (kws or [])[:30]:
        kw = kw.strip()
        if not kw:
            continue
        sc += w_title * min(_count_term_hits(tb["title_text"], kw), 2)
        sc += w_flat * min(_count_term_hits(tb["flat_text"], kw), 4)
        sc += w_content * min(_count_term_hits(tb["content_text"], kw), 3)
        sc += w_keyword * min(_count_term_hits(tb["keyword_text"], kw), 3)
        sc += w_category * min(_count_term_hits(tb["category"], kw), 2)
        sc += w_meta * min(_count_term_hits(tb["meta_kv"], kw), 2)
    return float(sc)

def _keyword_exact_match_hits(p: Any, kws: List[str]) -> int:
    tb = _payload_text_bundle(p)
    title = (tb.get("title_text") or "").lower()
    keyword_text = (tb.get("keyword_text") or "").lower()
    hits = 0
    for kw in (kws or [])[:30]:
        kw = kw.strip()
        if not kw:
            continue
        kw_lower = kw.lower()
        if kw_lower in title:
            hits += 1
        if kw_lower in keyword_text:
            hits += 1
    return hits

def _flatten_ids_from_intent(it: Any) -> List[str]:
    # ids_flat 우선, 없으면 ids_map/ids(dict) 평탄화
    flat = getattr(it, "ids_flat", None)
    if isinstance(flat, list) and flat:
        out = []
        for x in flat:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
        return out

    m = getattr(it, "ids_map", None)
    if not isinstance(m, dict):
        m = getattr(it, "ids", None)
    if not isinstance(m, dict):
        return []

    out = []
    for _, lst in m.items():
        if not isinstance(lst, list):
            continue
        for x in lst:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
    return out

def _filter_score(p: Any, it: NormalizedIntent, base_route: str, *, strict_ids: bool) -> float:
    tb = _payload_text_bundle(p)
    hay = " | ".join([tb["title_text"], tb["flat_text"], tb["meta_kv"], tb["content_text"]]).lower()

    sc = 0.0

    # IDs
    flat_ids = _flatten_ids_from_intent(it)
    for _id in flat_ids[:10]:
        if _id.lower() in hay:
            sc += 120.0
        else:
            sc += (-45.0 if strict_ids else -12.0)

    # years
    years = [str(y).strip() for y in (it.years or []) if str(y).strip()]
    for y in years[:6]:
        if y.lower() in hay:
            sc += 35.0

    # org
    org_terms = [t.strip() for t in (it.org_terms or []) if t.strip()]
    for ot in org_terms[:4]:
        if ot.lower() in hay:
            sc += 60.0
        elif strict_ids:
            sc -= 10.0

    # people
    people_terms = [t.strip() for t in (it.people_terms or []) if t.strip()]
    for pt in people_terms[:4]:
        if pt.lower() in hay:
            sc += 70.0
        elif strict_ids:
            sc -= 15.0

    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    tag = str(pl.get("tag") or "")
    col = _resolve_collection(p, pl)

    # perf tag filters (exact) - perf 컬렉션에만 적용
    if it.perf_tag_filters and col == COL_PERF:
        for t in list(it.perf_tag_filters)[:8]:
            if str(t) == tag:
                sc += 90.0
                break

    # project tag filters (exact) - project 컬렉션에만 적용
    if it.project_tag_filters and col == COL_PROJECT:
        for t in list(it.project_tag_filters)[:8]:
            if str(t) == tag:
                sc += 80.0
                break

    return float(sc)

def _family_bonus(p: Any, base_route: str) -> float:
    pl = getattr(p, "payload", None) or {}
    if not isinstance(pl, dict):
        return 0.0

    tag = _normalize_tag_value(pl.get("tag"))
    if tag:
        if base_route == "support":
            return 0.0
        if base_route in ("project", "people", "org") and tag in PROJECT_TAGS_NORM:
            return 4.0
        if base_route == "perf" and tag in PERF_TAGS_NORM:
            return 4.0

    col = _resolve_collection(p, pl)
    if base_route == "support" and col == COL_SUPPORT:
        return 6.0
    if base_route in ("project", "people", "org") and col == COL_PROJECT:
        return 4.0
    if base_route == "perf" and col == COL_PERF:
        return 4.0
    return 0.0

def _score_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)
    mean = sum(vals) / max(1, n)
    var = sum((v - mean) ** 2 for v in vals) / max(1, n)
    std = var ** 0.5

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        idx = int(round((n - 1) * p))
        return vals[max(0, min(n - 1, idx))]

    return {
        "min": vals[0],
        "max": vals[-1],
        "mean": mean,
        "std": std,
        "p50": pct(0.5),
        "p90": pct(0.9),
    }

def _normalize_values(values: List[float], policy: str) -> List[float]:
    if not values:
        return []
    policy = (policy or "minmax").strip().lower()
    if policy == "none":
        return list(values)
    if policy == "zscore":
        mean = sum(values) / max(1, len(values))
        var = sum((v - mean) ** 2 for v in values) / max(1, len(values))
        std = var ** 0.5
        if std == 0:
            return [0.5 for _ in values]
        return [1 / (1 + pow(2.718281828, -((v - mean) / std))) for v in values]

    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        return [0.5 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]

def _rerank_compare_summary(points: List[Any], total_key: str, *, topn: int) -> List[Dict[str, Any]]:
    out = []
    for p in (points or [])[: max(1, topn)]:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            pl = {}
        out.append({
            "doc_id": pl.get("doc_id") or getattr(p, "id", None),
            "col": _resolve_collection(p, pl),
            "tag": pl.get("tag"),
            total_key: pl.get(total_key),
            "_final_total": pl.get("_final_total"),
            "title": _clip_text(_payload_title(pl, _get_meta(pl)), 120),
        })
    return out

def _pick_collections(all_cols: list[str], allow: Optional[Iterable[str]] = None) -> list[str]:
    allow_list = list(allow) if allow is not None else list(RAG_COLLECTION_ALLOWLIST)
    if not allow_list:
        return all_cols  # 제한 없음
    allow_set = set(allow_list)
    return [c for c in all_cols if c in allow_set]

def _default_target_collections() -> list[str]:
    allow_list = list(RAG_COLLECTION_ALLOWLIST)
    if allow_list:
        return allow_list
    return [COL_PROJECT]


def _default_target_collections_for_route(base_route: str) -> list[str]:
    route_defaults = {
        "project": [COL_PROJECT],
        "perf": [COL_PERF],
        "support": [COL_SUPPORT],
        # people/org 질의는 프로젝트 컬렉션을 기본 엔티티 저장소로 사용
        "people": [COL_PROJECT],
        "org": [COL_PROJECT],
    }
    desired = list(route_defaults.get(str(base_route or "").strip().lower(), [COL_PROJECT]))
    allow_list = list(RAG_COLLECTION_ALLOWLIST)
    if not allow_list:
        return desired

    allow_set = set(allow_list)
    selected = [c for c in desired if c in allow_set]
    if selected:
        return selected
    return allow_list

def _final_rerank(
        cands: List[Any],
        *,
        it: NormalizedIntent,
        kws: List[str],
        lex_w: Dict[str, float],
        base_route: str,
        mode: str,
        keep: int,
        tag_boost: float = 0.0,
        tag_mismatch_penalty: float = 0.0,
) -> List[Any]:
    if not cands:
        return []

    if mode in ("search", "lookup") and it.people_terms and base_route == "people":
        terms = [t.strip() for t in (it.people_terms or []) if t.strip()][:2]
        if terms:
            matched = [p for p in cands if _must_contain_terms(p, terms)]
            if matched:
                min_keep = max(2, min(int(keep), 5))
                if len(matched) >= min_keep:
                    cands = matched

    legacy_weights = {
        "lookup": (0.55, 0.70, 1.35),
        "join": (0.45, 0.65, 1.60),
        "search": (0.85, 1.00, 0.75),
    }
    compare_legacy = str(os.getenv("RAG_RERANK_COMPARE", "0")).strip().lower() in ("1", "true", "yes", "y")
    score_norm_policy = os.getenv("RAG_SCORE_NORM", "minmax")
    score_sample = int(os.getenv("RAG_SCORE_SAMPLE", "200"))

    # mode별 가중치 (정규화 스코어 기준)
    if mode == "lookup":
        w_rrf, w_kw, w_f = 0.30, 0.25, 0.45
        strict_ids = True
    elif mode == "join":
        w_rrf, w_kw, w_f = 0.25, 0.25, 0.50
        strict_ids = True
    else:  # search
        w_rrf, w_kw, w_f = 0.45, 0.35, 0.20
        strict_ids = False

    raw_rrf = []
    raw_kw = []
    raw_f = []
    raw_fam = []
    raw_tag = []
    raw_exact_hits = []
    raw_items = []
    for idx, p in enumerate(cands):
        pl = getattr(p, "payload", None)
        rrf_sc = float(pl.get("_rrf", 0.0)) if isinstance(pl, dict) else 0.0
        kw_sc = _keyword_score(p, kws, lex_w)
        exact_hits = _keyword_exact_match_hits(p, kws)
        f_sc = _filter_score(p, it, base_route, strict_ids=strict_ids)
        fam = _family_bonus(p, base_route)
        tag_sc = _tag_match_bonus(
            p,
            tag_filters=getattr(it, "tag_filters", None),
            boost=tag_boost,
            mismatch_penalty=tag_mismatch_penalty,
        )
        raw_rrf.append(rrf_sc)
        raw_kw.append(kw_sc)
        raw_f.append(f_sc)
        raw_fam.append(fam)
        raw_tag.append(tag_sc)
        raw_exact_hits.append(float(exact_hits))
        raw_items.append({
            "idx": idx,
            "p": p,
            "rrf": rrf_sc,
            "kw": kw_sc,
            "f": f_sc,
            "fam": fam,
            "tag": tag_sc,
            "exact_hits": exact_hits,
        })

    if raw_kw and max(raw_kw) <= 0 and any(raw_exact_hits):
        exact_bonus = float(os.getenv("RAG_KW_EXACT_MATCH_BONUS", "2.0"))
        for i, item in enumerate(raw_items):
            bonus = exact_bonus * float(item["exact_hits"])
            if bonus > 0:
                raw_kw[i] += bonus
                item["kw"] = raw_kw[i]
        log_kv(
            "RAG.RERANK.KEYWORD_EXACT_MATCH_BONUS",
            applied=True,
            bonus=exact_bonus,
            hits_total=sum(raw_exact_hits),
        )
    else:
        log_kv(
            "RAG.RERANK.KEYWORD_EXACT_MATCH_BONUS",
            applied=False,
            hits_total=sum(raw_exact_hits),
        )

    sample_slice = slice(0, max(0, min(score_sample, len(raw_items))))
    log_section(
        "RAG.RERANK.SCORE_RANGE_RAW",
        {
            "_rrf": _score_stats(raw_rrf[sample_slice]),
            "_keyword_score": _score_stats(raw_kw[sample_slice]),
            "keyword_exact_match_hits": _score_stats(raw_exact_hits[sample_slice]),
            "_filter_score": _score_stats(raw_f[sample_slice]),
            "_family_bonus": _score_stats(raw_fam[sample_slice]),
            "_tag_match_bonus": _score_stats(raw_tag[sample_slice]),
        },
    )

    norm_rrf = _normalize_values(raw_rrf, score_norm_policy)
    norm_kw = _normalize_values(raw_kw, score_norm_policy)
    norm_f = _normalize_values(raw_f, score_norm_policy)
    norm_fam = _normalize_values(raw_fam, score_norm_policy)
    norm_tag = _normalize_values(raw_tag, score_norm_policy)

    log_section(
        "RAG.RERANK.SCORE_RANGE_NORM",
        {
            "policy": score_norm_policy,
            "_rrf": _score_stats(norm_rrf[sample_slice]),
            "_keyword_score": _score_stats(norm_kw[sample_slice]),
            "_filter_score": _score_stats(norm_f[sample_slice]),
            "_family_bonus": _score_stats(norm_fam[sample_slice]),
            "_tag_match_bonus": _score_stats(norm_tag[sample_slice]),
        },
    )

    legacy_w_rrf, legacy_w_kw, legacy_w_f = legacy_weights.get(mode, legacy_weights["search"])
    scored = []
    legacy_scored = []
    for i, item in enumerate(raw_items):
        idx = item["idx"]
        p = item["p"]
        rrf_sc = item["rrf"]
        kw_sc = item["kw"]
        f_sc = item["f"]
        fam = item["fam"]
        tag_sc = item["tag"]
        tot = (
            (w_rrf * norm_rrf[i])
            + (w_kw * norm_kw[i])
            + (w_f * norm_f[i])
            + norm_fam[i]
            + norm_tag[i]
        )
        legacy_tot = (legacy_w_rrf * rrf_sc) + (legacy_w_kw * kw_sc) + (legacy_w_f * f_sc) + fam + tag_sc

        pl = getattr(p, "payload", None)
        if isinstance(pl, dict):
            pl["_raw_rrf"] = rrf_sc
            pl["_raw_kw"] = kw_sc
            pl["_raw_f"] = f_sc
            pl["_raw_family"] = fam
            pl["_raw_tag"] = tag_sc
            pl["_final_rrf"] = norm_rrf[i]
            pl["_final_kw"] = norm_kw[i]
            pl["_final_f"] = norm_f[i]
            pl["_final_family"] = norm_fam[i]
            pl["_final_tag"] = norm_tag[i]
            pl["_final_total"] = tot
            pl["_legacy_total"] = legacy_tot

        scored.append((-tot, idx, p))
        legacy_scored.append((-legacy_tot, idx, p))

    scored.sort(key=lambda x: (x[0], x[1]))
    out = [x[2] for x in scored[: max(1, int(keep))]]

    if compare_legacy:
        legacy_scored.sort(key=lambda x: (x[0], x[1]))
        compare_topn = int(os.getenv("RAG_RERANK_COMPARE_TOPN", "8"))
        log_section(
            "RAG.RERANK.COMPARE_LEGACY_TOP",
            _rerank_compare_summary([x[2] for x in legacy_scored], "_legacy_total", topn=compare_topn),
        )
        log_section(
            "RAG.RERANK.COMPARE_NEW_TOP",
            _rerank_compare_summary(out, "_final_total", topn=compare_topn),
        )

    return out

# -------------------------
# Plan
# -------------------------
def _has_any_ids(it: NormalizedIntent) -> bool:
    ids_map = getattr(it, "ids_map", None)
    if isinstance(ids_map, dict) and any(v for v in ids_map.values() if v):
        return True
    ids_flat = getattr(it, "ids_flat", None)
    if isinstance(ids_flat, list) and len(ids_flat) > 0:
        return True
    # backward compat
    legacy = getattr(it, "ids", None)
    if isinstance(legacy, dict) and any(v for v in legacy.values() if v):
        return True
    return False

def _has_explicit_identifiers(it: NormalizedIntent) -> bool:
    if bool(getattr(it, "is_id_query", False)):
        return True
    if getattr(it, "action", None) in ("id_exact", "id_fuzzy"):
        return True
    ids_map = getattr(it, "ids_map", None) or {}
    if isinstance(ids_map, dict):
        for values in ids_map.values():
            if values:
                return True
    ids_flat = getattr(it, "ids_flat", None) or []
    if isinstance(ids_flat, list) and ids_flat:
        return True
    return False

def _has_relation_join_ids(it: NormalizedIntent) -> bool:
    ids_map = getattr(it, "ids_map", None) or {}
    if not isinstance(ids_map, dict):
        return False
    if any(ids_map.get(key) for key in ("pjt_id", "pjt_no")):
        return True
    perf_id_keys = (
        "doi",
        "issn",
        "eissn",
        "pissn",
        "patent_reg_no",
        "patent_app_no",
        "paper_id",
        "perf_id",
        "rst_id",
    )
    return any(ids_map.get(key) for key in perf_id_keys)


def _resolve_join_execution_policy(
    *,
    relation: Optional[Tuple[str, str]],
    mode: str,
    action: Optional[str],
    has_relation_join_ids: bool,
) -> Dict[str, Any]:
    """JOIN 경로 실행 정책을 단일화한다.

    정책:
    - relation && mode=join 이면 ids 유무와 무관하게 Hop1→Hop2 경로를 강제한다.
    - "skip" 개념을 제거하고 required/executed 신호만 남긴다.
    """
    is_required = bool(relation and mode == "join")
    if not is_required:
        return {
            "required": False,
            "reason": None,
            "action": action,
            "has_relation_join_ids": bool(has_relation_join_ids),
        }

    reason = "seed_ids_present" if has_relation_join_ids else "hop1_key_extraction_required"
    return {
        "required": True,
        "reason": reason,
        "action": action,
        "has_relation_join_ids": bool(has_relation_join_ids),
    }

def _select_mode_policy(it: NormalizedIntent) -> Tuple[str, str]:
    """action/intent 기반 모드 결정 정책 (강제 규칙 포함).

    우선순위(강제):
    0) relation action -> join (ids 유무와 무관)
    1) relation + join ids -> join
    2) id query 또는 명확한 ids -> lookup
    3) list/stats/download -> lookup
    4) topic/search -> search (기본 유지)
    5) 그 외 -> search
    """
    action = it.action
    rel = it.relation
    if action == "relation" and rel:
        return "join", "relation_action"
    if rel == ("people", "project") and list(getattr(it, "people_terms", []) or []):
        return "lookup", "people_project_lookup"
    if rel and _has_relation_join_ids(it):
        return "join", "relation_ids"
    if bool(it.is_id_query) or _has_any_ids(it) or action in ("id_exact", "id_fuzzy"):
        return "lookup", "id_or_exact"
    if action in ("list", "stats", "download"):
        return "lookup", "list_like"
    if action in ("topic", "search"):
        return "search", "topic_search"
    return "search", "default"


def _build_plan(
    it: NormalizedIntent,
    *,
    preferred_mode: Optional[str] = None,
    preferred_mode_source: Optional[str] = None,
) -> Tuple[QueryPlan, str]:
    # relation_target_collections vs RAG_COLLECTION_ALLOWLIST 정책:
    # 1) allowlist가 있으면 relation target과 교집합을 우선 사용한다.
    # 2) 교집합이 비면 allowlist를 우선 적용한다.
    # 3) allowlist가 없으면 relation target을 그대로 사용한다.
    def _resolve_relation_target_cols(rel: Tuple[str, str], *, reason: str) -> List[str]:
        relation_cols = list(relation_target_collections(rel) or [])
        allowlist = list(RAG_COLLECTION_ALLOWLIST)
        policy = "relation_only"
        collection_policy_reason = None

        if allowlist:
            if relation_cols:
                intersection = [c for c in relation_cols if c in allowlist]
                if intersection:
                    selected = intersection
                    policy = "relation_intersect_allowlist"
                else:
                    selected = allowlist
                    policy = "allowlist_fallback"
                    collection_policy_reason = "relation_allowlist_disjoint"
            else:
                selected = allowlist
                policy = "allowlist_only"
                collection_policy_reason = "relation_empty"
        else:
            selected = relation_cols or _default_target_collections()
            policy = "relation_only" if relation_cols else "default_only"
            if not relation_cols:
                collection_policy_reason = "relation_empty"

        log_kv(
            "RAG.PLAN.COLLECTION_POLICY",
            reason=reason,
            relation=rel,
            allowlist=allowlist,
            relation_target_cols=relation_cols,
            selected_cols=selected,
            policy=policy,
            collection_policy_reason=collection_policy_reason,
        )
        return selected

    action = it.action
    base_route = it.base_route
    rel = it.relation
    output_type = getattr(it, "output_type", None)

    if preferred_mode:
        mode = preferred_mode
        mode_reason = f"planner:{preferred_mode_source or 'mode'}"
    else:
        mode, mode_reason = _select_mode_policy(it)

    if rel:
        target_cols = _resolve_relation_target_cols(rel, reason="relation_target")
    else:
        target_cols = _default_target_collections_for_route(base_route)

    return QueryPlan(
        mode=mode,
        base_route=base_route,
        action=action,
        relation=rel,
        join_key_mode=getattr(it, "join_key_mode", None),
        output_type=output_type,
        target_collections=tuple(target_cols),
        filters={},
    ), mode_reason


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "y", "on")


def _normalize_strategy_target_cols(cols: Any) -> List[str]:
    out: List[str] = []
    for c in (list(cols or [])):
        v = str(c).strip()
        if v:
            out.append(v)
    return out


def _strategy_consistency_or_violation(
    *,
    strict: bool,
    mismatch_kind: str,
    planner_value: Any,
    executed_value: Any,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    if planner_value == executed_value:
        return
    payload = {
        "kind": mismatch_kind,
        "planner": planner_value,
        "executed": executed_value,
    }
    if context:
        payload.update(context)
    log_kv("RAG.STRATEGY.MISMATCH", level="error" if strict else "warning", **payload)
    if strict:
        raise StrategyViolation(
            error_code="STRATEGY_MISMATCH",
            reason=(
                f"planner/executed mismatch({mismatch_kind}): "
                f"planner={planner_value}, executed={executed_value}"
            ),
        )


def _diff_filter_spec(
    *,
    planner_filter_spec: Dict[str, Any],
    executed_filter_spec: Dict[str, Any],
) -> Dict[str, Any]:
    """planner가 명시한 filter 계약 키 기준으로 실행 스펙 diff를 계산한다."""
    planner_keys = sorted(str(k) for k in (planner_filter_spec or {}).keys())
    changed: Dict[str, Dict[str, Any]] = {}
    for key in planner_keys:
        planner_val = planner_filter_spec.get(key)
        exec_val = executed_filter_spec.get(key)
        if planner_val != exec_val:
            changed[key] = {"planner": planner_val, "executed": exec_val}
    return {
        "planner_keys": planner_keys,
        "changed": changed,
    }

# -------------------------
# 2-hop JOIN helpers
# -------------------------
def _extract_quoted_terms(q: str) -> List[str]:
    if not q:
        return []
    out = re.findall(r'"([^"]+)"', q) + re.findall(r"'([^']+)'", q)
    return [t.strip() for t in out if t.strip()]

def _must_contain_terms(p: Any, terms: List[str]) -> bool:
    tb = _payload_text_bundle(p)
    hay = " | ".join([tb["title_text"], tb["flat_text"], tb["meta_kv"], tb["content_text"]]).lower()
    for t in (terms or []):
        if t.strip().lower() not in hay:
            return False
    return True


def _build_join_hop2_filter(
        *,
        relation: Optional[Tuple[str, str]],
        join_key_mode: str,
        join_pjt_ids: List[str],
        join_pjt_nos: List[str],
        join_ids: List[str],
        q: str,
        hop2_tag_filters: Optional[List[str]],
        people_terms: List[str],
        org_terms: List[str],
        planner_filter_spec: Dict[str, Any],
):
    """JOIN Hop2 필터 생성: join_key_mode 단일 소스(strategy/contract)만 사용."""
    if relation in (("project", "perf"), ("people", "perf"), ("org", "perf")):
        if join_key_mode == "group":
            return build_perf_filter_by_pjt_no(join_pjt_nos, q)
        return build_perf_filter_by_pjt_id(join_pjt_ids or join_ids, q)

    return build_join_filter(
        JoinFilterInput(
            join_ids=join_pjt_ids,
            pjt_nos=join_pjt_nos,
            join_key_mode=join_key_mode,
            tag_filters=hop2_tag_filters,
            people_terms=people_terms,
            org_terms=org_terms,
            relation=relation,
            filter_spec=planner_filter_spec.get("join_filter"),
        )
    )


# -------------------------
# Main
# -------------------------
def _hydrate_points_payload(
        qdr,
        points,
        *,
        chunk_size: int = 128,
) -> None:
    """
    Always hydrate points with FULL payload from Qdrant (with_payload=True).
    - 내부 메타(_collection/_rrf/_final_*)는 보존하지 않고 DB payload로 덮어씀
    - include_fields는 호환용으로만 두고 무시
    """
    _INTERNAL_KEYS = {
        "_collection", "_rrf",
        "_raw_rrf","_raw_kw","_raw_f","_raw_family","_raw_tag",
        "_final_rrf","_final_kw","_final_f","_final_family","_final_tag",
        "_final_total","_legacy_total",
    }


    if not points:
        return

    def _get(p, k, default=None):
        return getattr(p, k, default)

    def _set(p, k, v):
        setattr(p, k, v)

    def _infer_collection_from_payload(payload: dict) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        candidate_tags = []
        tag_value = payload.get("tag")
        if tag_value:
            candidate_tags.append(tag_value)
        tags_value = payload.get("tags")
        if isinstance(tags_value, list):
            candidate_tags.extend([t for t in tags_value if t])
        for t in candidate_tags:
            norm = _normalize_tag_value(t)
            if norm in PROJECT_TAGS_NORM:
                return COL_PROJECT
            if norm in PERF_TAGS_NORM:
                return COL_PERF
        return None

    def _fallback_collection() -> Optional[str]:
        allow_list = list(RAG_COLLECTION_ALLOWLIST)
        if len(allow_list) == 1:
            return allow_list[0]
        return None

    def _normalize_project_tag(payload: dict) -> None:
        if not isinstance(payload, dict):
            return
        tag_value = payload.get("tag")
        if tag_value in {TAG_PJT_MP, TAG_PJT_ORG}:
            payload["tag"] = TAG_PJT_INFO
        tags_value = payload.get("tags")
        if isinstance(tags_value, list):
            payload["tags"] = [
                TAG_PJT_INFO if t in {TAG_PJT_MP, TAG_PJT_ORG} else t for t in tags_value
            ]
    # collection별로 묶어서 retrieve 호출 수를 줄임
    buckets = {}
    for p in points:
        payload = _get(p, "payload", {}) or {}
        col = None
        if isinstance(payload, dict):
            col = payload.get("_collection")
        if not col:
            col = _get(p, "_collection", None)
        if not col:
            col = _infer_collection_from_payload(payload)
        if not col:
            col = _fallback_collection()
        if not col:
            # collection을 모르면 hydrate 불가(안전 스킵)
            continue
        buckets.setdefault(col, []).append(p)

    for collection_name, plist in buckets.items():
        step = max(1, int(chunk_size))
        for i in range(0, len(plist), step):
            chunk = plist[i : i + step]
            ids = [_get(p, "id") for p in chunk if _get(p, "id", None) is not None]
            if not ids:
                continue

            recs = qdr.retrieve(
                collection_name=collection_name,
                ids=ids,
                with_payload=True,
                with_vectors=False,
            )

            rec_payload = {}
            for r in (recs or []):
                rid = _get(r, "id", None)
                if rid is None:
                    continue
                rec_payload[str(rid)] = _get(r, "payload", {}) or {}
            # payload를 "그대로" 덮어씀 (내부 메타 유지 X)
            for p in chunk:
                pid = _get(p, "id", None)
                if pid is None:
                    continue
                key = str(pid)
                if key in rec_payload:
                    _normalize_project_tag(rec_payload[key])
                    _set(p, "payload", rec_payload[key])


def _run_rag_with_vectors(
        *,
        query: str,
        model_name: str,
        hint: Any = None,
        intent_payload: Any = None,
        stack: str,
        vector_names: List[str],
        w_dense_map: Dict[str, float],
        lexical_field_weights: Optional[Dict[str, float]] = None,
        sparse_vector_name: Optional[str] = None,
        sparse_topk: Optional[int] = None,
        sparse_weight: Optional[float] = None,
        domain_hint: Optional[str] = None,
) -> RagResult:
    t_all0 = time.time()
    timings: Dict[str, Any] = _init_timings()

    q = normalize_query(query)
    if not q:
        _timing_put(timings, "phase.total", 0.0)
        _timing_put(timings, "info.contract_fail_reason", "empty_query")
        return RagResult(
            stack=stack, keywords=[], hits=[], reranked_hits=[], context="", refs=[],
            timings=timings,
        )

    # shared objects
    t0 = time.time()
    resources = build_rag_objects()
    qdr = resources.qdrant_client
    _timing_put(timings, "phase.stack_init", time.time() - t0)
    ctx_hard_limit = _get_ctx_hard_limit()

    fallback_emb: Dict[str, Any] = {
        "e5i_qa": resources.embed_e5i,
        "e5_qa": resources.embed_e5,
    }

    def _get_attr(obj: Any, name: str, default=None):
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def _category_to_base_route(cats: List[Any]) -> str:
        norm = set()
        for c in (cats or []):
            s = str(c)
            s = s.replace("ContentCategory.", "").strip().lower()
            norm.add(s)

        if norm == {"project"}:
            return "project"
        if norm in ({"perf"}, {"performance"}):
            return "perf"
        if norm in ({"people"}, {"researcher"}):
            return "people"
        if norm in ({"org"}, {"organization"}, {"기관"}, {"institution"}):
            return "org"
        if norm in ({"qna"}, {"qnt"}, {"qna "}, {"q&a"}):
            return "support"

        return "mixed"

    def _normalize_target_collections(raw: Any) -> List[str]:
        if raw is None:
            return []
        items = raw
        if isinstance(items, str):
            items = [x.strip() for x in items.split(",") if x.strip()] or [items]
        if not isinstance(items, (list, tuple, set)):
            items = [items]

        col_map = {
            "project": COL_PROJECT,
        }

        out: List[str] = []
        seen: set[str] = set()
        for item in items:
            if hasattr(item, "value"):
                item = item.value
            key = str(item).strip()
            if not key:
                continue
            key_lower = key.lower()
            col = None
            if key_lower in col_map:
                col = col_map[key_lower]
            elif key_lower == COL_PROJECT.lower():
                col = COL_PROJECT
            elif key_lower == COL_PERF.lower():
                col = COL_PERF
            elif key_lower == COL_SUPPORT.lower():
                col = COL_SUPPORT
            if col:
                if col not in seen:
                    seen.add(col)
                    out.append(col)
            elif key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def _coerce_int(x: Any, default: int) -> int:
        try:
            return int(x)
        except Exception:
            return default

    def _schema_fail_fast(scope: str, message: str) -> None:
        strict = str(os.getenv("RAG_FAIL_FAST_SCHEMA", "0")).strip().lower() in ("1", "true", "yes", "y")
        log_msg = f"[RAG] {scope} schema mismatch: {message}"
        if strict:
            logger.error(log_msg)
            raise ValueError(log_msg)
        logger.warning(log_msg)

    def _extract_payload_normalized_intent(payload: Any) -> Any:
        if payload is None:
            return None
        if isinstance(payload, Mapping):
            allowed = {"normalized_intent"}
            unknown = sorted(str(k) for k in payload.keys() if str(k) not in allowed)
            if unknown:
                _schema_fail_fast("intent_payload.v2", f"unknown fields={unknown}")
                return None
            return payload.get("normalized_intent")

        normalized = getattr(payload, "normalized_intent", None)
        has_query_intent = getattr(payload, "query_intent", None) is not None
        has_raw_intent = getattr(payload, "raw_intent", None) is not None
        if has_query_intent or has_raw_intent:
            _schema_fail_fast("intent_payload.v2", "query_intent/raw_intent is no longer supported")
        return normalized

    def _normalize_hint_payload(raw: Any) -> Dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, Mapping):
            source = dict(raw)
        else:
            source = {
                "mode": getattr(raw, "mode", None),
                "head": getattr(raw, "head", None),
                "action": getattr(raw, "action", None),
                "relation": getattr(raw, "relation", None),
                "ids_map": getattr(raw, "ids_map", None),
                "filters": getattr(raw, "filters", None),
                "target_cols": getattr(raw, "target_cols", None),
                "limit": getattr(raw, "limit", None),
                "retrieval_query": getattr(raw, "retrieval_query", None),
                "confidence": getattr(raw, "confidence", None),
            }

        allowed = {
            "mode", "head", "action", "relation", "join_key_mode", "ids_map", "filters",
            "target_cols", "limit", "retrieval_query", "confidence",
        }
        unknown = sorted(str(k) for k in source.keys() if str(k) not in allowed)
        if unknown:
            _schema_fail_fast("hint.v2", f"unknown fields={unknown}")
            return {}

        normalized = {k: source.get(k) for k in allowed if source.get(k) is not None}
        filters_obj = normalized.get("filters")
        if filters_obj is not None and not isinstance(filters_obj, Mapping):
            _schema_fail_fast("hint.v2", "filters must be an object")
            return {}
        return normalized

    # --- hint 적용 (single-pass) ---
    qa = _normalize_hint_payload(hint)
    payload_normalized_intent = _extract_payload_normalized_intent(intent_payload)
    qa_conf = float(_get_attr(qa, "confidence", 0.0) or 0.0)
    hint_min_conf = float(os.getenv("RAG_HINT_MIN_CONF", "0.55"))
    hint_conf_ok = bool(qa and qa_conf >= hint_min_conf)
    strategy_source = "payload" if payload_normalized_intent is not None else "qa"
    qa_strategy_active = bool(hint_conf_ok and strategy_source == "qa")
    hint_policy = "merge" if qa_strategy_active else "ignore"
    strategy_mode = None
    strategy_head = None
    strategy_relation = None
    strategy_action = None
    strategy_join_key_mode = None
    strategy_query_text = None
    strategy_filter_spec = {}
    strategy_topk_spec = {}
    strategy_rerank_spec = {}
    strategy_enabled = False

    hinted_base = None
    hinted_limit = 0
    hinted_cols: List[str] = _normalize_target_collections(_get_attr(qa, "target_cols", None))
    payload_target_cols = _normalize_target_collections(_get_attr(payload_normalized_intent, "target_cols", None))
    if strategy_source == "payload" and payload_target_cols:
        hinted_cols = payload_target_cols

    def _strategy_mode_from_action(action_value: Any) -> Optional[str]:
        value = str(action_value or "").strip().lower()
        if value in ("list", "stats", "download", "id_exact", "id_fuzzy", "detail"):
            return "lookup"
        if value in ("topic", "search"):
            return "search"
        if value == "join":
            return "join"
        return None

    def _strategy_view_from_qa(qa_obj: Any) -> Dict[str, Any]:
        view = {
            "mode": str(_get_attr(qa_obj, "mode", "") or "").strip().lower() or None,
            "action": str(_get_attr(qa_obj, "action", "") or "").strip().lower() or None,
            "head": str(_get_attr(qa_obj, "head", "") or "").strip().lower() or None,
            "relation": _normalize_relation_hint(_get_attr(qa_obj, "relation", None)),
            "join_key_mode": str(_get_attr(qa_obj, "join_key_mode", "") or "").strip().lower() or None,
            "target_cols": _normalize_target_collections(_get_attr(qa_obj, "target_cols", None)),
            "limit": _coerce_int(_get_attr(qa_obj, "limit", 0), 0),
            "retrieval_query": normalize_query(_get_attr(qa_obj, "retrieval_query", "") or "") or None,
        }
        if not view["mode"] and view["action"]:
            view["mode"] = _strategy_mode_from_action(view["action"])
        return view

    def _strategy_view_from_payload(payload_obj: Any) -> Dict[str, Any]:
        view = {
            "mode": str(_get_attr(payload_obj, "mode", "") or "").strip().lower() or None,
            "action": str(_get_attr(payload_obj, "action", "") or "").strip().lower() or None,
            "head": str(_get_attr(payload_obj, "base_route", "") or "").strip().lower() or None,
            "relation": _normalize_relation_hint(_get_attr(payload_obj, "relation", None)),
            "join_key_mode": str(_get_attr(payload_obj, "join_key_mode", "") or "").strip().lower() or None,
            "target_cols": _normalize_target_collections(_get_attr(payload_obj, "target_cols", None)),
            "limit": _coerce_int(_get_attr(payload_obj, "planner_limit", 0), 0),
            "retrieval_query": normalize_query(_get_attr(payload_obj, "retrieval_query", "") or "") or None,
        }
        if not view["mode"] and view["action"]:
            view["mode"] = _strategy_mode_from_action(view["action"])
        return view

    def _normalize_strategy_view(view: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for key, value in view.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            if key == "limit" and int(value) <= 0:
                continue
            normalized[key] = value
        return normalized

    if qa_strategy_active:
        q_for_retrieval = normalize_query(_get_attr(qa, "retrieval_query", "") or "") or q
        hinted_base = str(_get_attr(qa, "head", "") or "").strip().lower() or None
        hinted_limit = _coerce_int(_get_attr(qa, "limit", 0), 0)
    else:
        q_for_retrieval = q

    # ✅ 표준 q 확정
    q = q_for_retrieval

    force_cols = _normalize_target_collections(os.getenv("RAG_SEARCH_COLLECTIONS", ""))
    allow_cols = _normalize_target_collections(RAG_COLLECTION_ALLOWLIST)

    effective_allow = force_cols or allow_cols  # force 있으면 force가 최우선

    # -------------------------
    # ids_map / ids_flat 안전 접근 유틸
    # -------------------------
    def _normalize_hint_terms(values: Any) -> List[str]:
        if values is None:
            return []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        out: List[str] = []
        seen: set[str] = set()
        for v in values:
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return out

    def _merge_keywords_with_priority(primary: Any, secondary: Any) -> List[str]:
        primary_terms = _normalize_hint_terms(primary)
        secondary_terms = _normalize_hint_terms(secondary)
        return list(dict.fromkeys([*primary_terms, *secondary_terms]))

    def _normalize_hint_ids_map(raw: Any) -> Dict[str, List[str]]:
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, List[str]] = {}
        for key, values in raw.items():
            norm = _normalize_hint_terms(values)
            if norm:
                out[str(key)] = norm
        return out

    def _normalize_payload_intent(raw: Any) -> Optional[NormalizedIntent]:
        if isinstance(raw, NormalizedIntent):
            return raw
        if raw is None:
            return None

        if isinstance(raw, Mapping):
            getter = raw.get
        else:
            getter = lambda key, default=None: getattr(raw, key, default)

        data: Dict[str, Any] = {}
        for field in fields(NormalizedIntent):
            val = getter(field.name, None)
            if val is not None:
                data[field.name] = val

        if not data:
            return None

        if "relation" in data:
            data["relation"] = _normalize_relation_hint(data.get("relation"))

        for key in (
            "years",
            "people_terms",
            "gender_terms",
            "org_terms",
            "perf_types",
            "keywords",
            "perf_tag_filters",
            "project_tag_filters",
            "tag_filters",
            "ids_flat",
            "remove_terms_for_head",
        ):
            if key in data:
                data[key] = _normalize_hint_terms(data.get(key))
        if "categories" in data:
            data["categories"] = normalize_categories(data.get("categories"))
        if "retrieval_query" in data:
            data["retrieval_query"] = str(data.get("retrieval_query") or "").strip() or None

        if "year_from" in data:
            year_from_terms = _normalize_hint_terms([data.get("year_from")])
            data["year_from"] = year_from_terms[0] if year_from_terms else None
        if "year_to" in data:
            year_to_terms = _normalize_hint_terms([data.get("year_to")])
            data["year_to"] = year_to_terms[0] if year_to_terms else None

        if "ids_map" in data:
            data["ids_map"] = _normalize_hint_ids_map(data.get("ids_map"))

        if "is_id_query" in data:
            data["is_id_query"] = bool(data.get("is_id_query"))

        required = {"action", "base_route", "is_id_query"}
        if not required.issubset(data.keys()):
            return None

        try:
            return NormalizedIntent(**data)
        except Exception:
            return None

    def _extract_org_filter_hints(filters_obj: Any) -> Dict[str, List[str]]:
        if not isinstance(filters_obj, dict):
            return {
                "lead_org_terms": [],
                "participant_org_terms": [],
                "people_affiliation_org_terms": [],
                "org_terms": [],
            }

        lead_org_terms = _normalize_hint_terms(filters_obj.get("lead_org_name"))
        participant_org_terms = _normalize_hint_terms(filters_obj.get("participant_org_name"))
        people_affiliation_org_terms = _normalize_hint_terms(filters_obj.get("people_affiliation_org_name"))
        generic_org_terms = _normalize_hint_terms(filters_obj.get("org_name"))

        org_terms = _normalize_hint_terms(
            [
                *generic_org_terms,
                *lead_org_terms,
                *participant_org_terms,
                *people_affiliation_org_terms,
            ]
        )

        return {
            "lead_org_terms": lead_org_terms,
            "participant_org_terms": participant_org_terms,
            "people_affiliation_org_terms": people_affiliation_org_terms,
            "org_terms": org_terms,
        }

    def _extract_people_filter_hints(filters_obj: Any) -> List[str]:
        if not isinstance(filters_obj, dict):
            return []
        return _normalize_hint_terms(
            [
                *(_normalize_hint_terms(filters_obj.get("researcher_name"))),
                *(_normalize_hint_terms(filters_obj.get("participant_researcher_name"))),
            ]
        )

    # keywords (payload/hint only)
    t0 = time.time()
    payload_kws = _get_attr(payload_normalized_intent, "keywords", None)
    if isinstance(payload_kws, (list, tuple)) and payload_kws:
        kws = _normalize_hint_terms(payload_kws)
    else:
        kws = []
    _timing_put(timings, "phase.kw_det", time.time() - t0)

    intent_from_payload = False
    perf_types_source: Optional[str] = None
    planner_confidence: Optional[float] = None
    normalized_intent_raw = payload_normalized_intent
    it = _normalize_payload_intent(normalized_intent_raw)
    if normalized_intent_raw is not None and it is None:
        _schema_fail_fast("intent_payload.v2", "normalized_intent is invalid or missing required fields")

    qa_strategy_view = _normalize_strategy_view(_strategy_view_from_qa(qa))
    payload_strategy_view = _normalize_strategy_view(_strategy_view_from_payload(it)) if it is not None else {}
    if payload_strategy_view and qa_strategy_view:
        conflicts = []
        for key in sorted(set(qa_strategy_view.keys()) & set(payload_strategy_view.keys())):
            if qa_strategy_view[key] != payload_strategy_view[key]:
                conflicts.append(
                    {
                        "field": key,
                        "qa": qa_strategy_view[key],
                        "payload": payload_strategy_view[key],
                    }
                )
        if conflicts:
            raise StrategyViolation(
                error_code="STRATEGY_SOURCE_CONFLICT",
                reason=f"hint/payload strategy mismatch: {conflicts}",
            )

    if it is not None:
        intent_from_payload = True
        planner_confidence = _get_attr(it, "planner_confidence", None)
    else:
        domain_hint = hinted_base if hinted_base in ("project", "perf", "people", "support", "org") else None
        raw_intent = classify_query_compat(q, kws, domain_hint=domain_hint, hint=qa)
        planner_confidence = _get_attr(raw_intent, "planner_confidence", None)
        org_filter_hints = _extract_org_filter_hints(_get_attr(qa, "filters", None) or {})
        hint_org_role = str(_get_attr(qa, "org_role", "") or "").strip().lower() or None
        hint_org_terms = _normalize_hint_terms(org_filter_hints["org_terms"])

        it = normalize_intent(
            raw_intent,
            query=q,
            keywords=kws,
            hint_org_terms=hint_org_terms,
            hint_org_role=hint_org_role,
            hint_lead_org_terms=org_filter_hints["lead_org_terms"],
            hint_participant_org_terms=org_filter_hints["participant_org_terms"],
            hint_people_affiliation_org_terms=org_filter_hints["people_affiliation_org_terms"],
        )
        intent_perf_types = list(getattr(it, "perf_types", []) or [])
        if intent_perf_types:
            perf_types_source = "intent"
            log_kv("RAG.PERF_TYPES.SOURCE", source=perf_types_source, values=intent_perf_types)

    ctx = ExecutionContext.from_intent(it)
    planner_keywords = _normalize_hint_terms(ctx.keywords)

    hint_mode = str(_get_attr(qa, "mode", "") or "").strip().lower() or None
    planner_action = str(_get_attr(qa, "action", "") or "").strip().lower() or None
    hint_head = str(_get_attr(qa, "head", "") or "").strip().lower() or None
    hint_relation = _normalize_relation_hint(_get_attr(qa, "relation", None))
    hint_join_key_mode = str(_get_attr(qa, "join_key_mode", "") or "").strip().lower() or None
    if hint_join_key_mode in ("instance", "group"):
        strategy_join_key_mode = hint_join_key_mode
    hint_ids_map = _normalize_hint_ids_map(_get_attr(qa, "ids_map", None) or {})
    hint_filters = _get_attr(qa, "filters", None) or {}

    if (not strategy_enabled) and (not intent_from_payload and qa_strategy_active):
        if hint_head in ("project", "perf", "people", "org", "support"):
            ctx.base_route = hint_head
        if hint_relation:
            ctx.relation = hint_relation
        if hint_join_key_mode in ("instance", "group"):
            ctx.join_key_mode = hint_join_key_mode
        if hint_ids_map:
            merged_ids = dict(ctx.ids_map or {})
            for key, values in hint_ids_map.items():
                merged_ids[key] = list(dict.fromkeys(list(merged_ids.get(key, [])) + values))
            ctx.ids_map = merged_ids
        if isinstance(hint_filters, dict):
            org_filter_hints = _extract_org_filter_hints(hint_filters)
            if org_filter_hints["lead_org_terms"]:
                ctx.lead_org_terms = org_filter_hints["lead_org_terms"]
            if org_filter_hints["participant_org_terms"]:
                ctx.participant_org_terms = org_filter_hints["participant_org_terms"]
            if org_filter_hints["people_affiliation_org_terms"]:
                ctx.people_affiliation_org_terms = org_filter_hints["people_affiliation_org_terms"]
            if org_filter_hints["org_terms"]:
                ctx.org_terms = org_filter_hints["org_terms"]
            people_terms_hint = _extract_people_filter_hints(hint_filters)
            if people_terms_hint:
                ctx.people_terms = people_terms_hint
            year_terms_hint = _normalize_hint_terms(
                [hint_filters.get("year_from"), hint_filters.get("year_to")]
            )
            if year_terms_hint:
                ctx.years = year_terms_hint
                ctx.year_from = year_terms_hint[0]
                ctx.year_to = year_terms_hint[-1]
            year_from = hint_filters.get("year_from")
            year_to = hint_filters.get("year_to")
            if year_from is not None:
                ctx.year_from = str(year_from).strip() or None
            if year_to is not None:
                ctx.year_to = str(year_to).strip() or None
            tag_filters_hint = _normalize_hint_terms(hint_filters.get("tag_filters"))
            if tag_filters_hint:
                ctx.tag_filters = tag_filters_hint
            perf_types_hint = _normalize_hint_terms(hint_filters.get("perf_types"))
            if perf_types_hint:
                ctx.perf_types = perf_types_hint
                perf_types_source = "hint"
                log_kv("RAG.PERF_TYPES.SOURCE", source=perf_types_source, values=perf_types_hint)
            keywords_hint = _normalize_hint_terms(hint_filters.get("keywords"))
            if keywords_hint:
                ctx.keywords = keywords_hint
            title_hint = _normalize_hint_terms(hint_filters.get("title_terms"))
            if title_hint:
                ctx.title = title_hint
                ctx.keywords = list(dict.fromkeys([*list(ctx.keywords or []), *title_hint]))

    planner_categories = normalize_categories(ctx.categories)
    planner_limit = ctx.planner_limit
    planner_retrieval_query = str(ctx.retrieval_query or "").strip() or None
    planner_meta_source = strategy_source
    planner_applied = int(bool(intent_from_payload))
    planner_failed = 0

    if not qa_strategy_active:
        if planner_retrieval_query:
            q = normalize_query(planner_retrieval_query) or q
        if planner_limit is not None:
            hinted_limit = _coerce_int(planner_limit, hinted_limit)
            if hinted_limit < 0:
                hinted_limit = 0

    pending_strategy_filter_spec: Optional[Dict[str, Any]] = None
    if strategy_enabled:
        if strategy_head in ("project", "perf", "people", "org", "support"):
            ctx.base_route = strategy_head
        if strategy_relation:
            ctx.relation = strategy_relation
        if strategy_action:
            ctx.action = strategy_action
        if isinstance(strategy_filter_spec, dict) and strategy_filter_spec:
            pending_strategy_filter_spec = dict(strategy_filter_spec)

    action = ctx.action
    base_route = ctx.base_route
    relation = ctx.relation
    if planner_confidence is not None:
        try:
            planner_confidence = float(planner_confidence)
        except Exception:
            planner_confidence = None

    qa_categories = normalize_categories([_get_attr(qa, "head", None)] if _get_attr(qa, "head", None) else [])
    qa_limit = _get_attr(qa, "limit", None)
    qa_retrieval_query = str(_get_attr(qa, "retrieval_query", "") or "").strip() or None

    log_kv(
        "RAG.INPUT",
        raw_query=query,
        normalized=q,
        hint_conf=qa_conf,
        qa_conf=qa_conf,
        hint_applied=int(hint_conf_ok),
        planner_applied=planner_applied,
        planner_failed=planner_failed,
        hinted_base=hinted_base,
        hinted_limit=hinted_limit,
        hinted_cols=hinted_cols,
        payload_target_cols=payload_target_cols,
        allow_cols=allow_cols,
        qa_categories=qa_categories,
        qa_limit=qa_limit,
        qa_retrieval_query=qa_retrieval_query,
        planner_categories=planner_categories,
        planner_limit=planner_limit,
        planner_retrieval_query=planner_retrieval_query,
        planner_confidence=planner_confidence,
        planner_meta_source=planner_meta_source,
        strategy_source=strategy_source,
        model_name=model_name,
        stack=stack,
        vector_names=vector_names,
        intent_payload=bool(intent_payload),
    )

    # budget (for ctx builder)
    ctx_budget = int(
        get_ctx_token_budget(
            model_name,
            max_output_tokens=get_model_max_output_tokens(model_name),
        )
    )
    _timing_put(timings, "info.ctx_budget", float(ctx_budget))

    # org terms/filter (필요 시)
    org_terms = [t.strip() for t in (list(ctx.org_terms or []) or []) if str(t).strip()]
    lead_org_terms = [t.strip() for t in (list(getattr(ctx, "lead_org_terms", []) or []) or []) if str(t).strip()]
    participant_org_terms = [
        t.strip() for t in (list(getattr(ctx, "participant_org_terms", []) or []) or []) if str(t).strip()
    ]
    people_affiliation_org_terms = [
        t.strip()
        for t in (list(getattr(ctx, "people_affiliation_org_terms", []) or []) or [])
        if str(t).strip()
    ]
    if (not org_terms) and (lead_org_terms or participant_org_terms or people_affiliation_org_terms):
        org_terms = _normalize_hint_terms([
            *lead_org_terms,
            *participant_org_terms,
            *people_affiliation_org_terms,
        ])
    ctx.org_terms = org_terms
    ctx.lead_org_terms = lead_org_terms
    ctx.participant_org_terms = participant_org_terms
    ctx.people_affiliation_org_terms = people_affiliation_org_terms

    org_role = _get_attr(qa, "org_role", None) or ctx.org_role
    org_role = str(org_role or "").strip().lower() or None

    effective_lead_org_terms = list(lead_org_terms)
    effective_participant_org_terms = list(participant_org_terms)
    effective_people_affiliation_org_terms = list(people_affiliation_org_terms)
    planner_org_filter_present = bool(
        effective_lead_org_terms
        or effective_participant_org_terms
        or effective_people_affiliation_org_terms
    )
    org_filter_keys: List[str] = []
    if effective_lead_org_terms:
        org_filter_keys.append("lead")
    if effective_participant_org_terms:
        org_filter_keys.append("participant")
    if effective_people_affiliation_org_terms:
        org_filter_keys.append("affiliation")

    # 명시 키가 없을 때만 org_role을 보조 신호로 사용한다.
    if not effective_lead_org_terms and not effective_participant_org_terms and org_terms:
        if org_role in ("performer", "lead", "performing"):
            effective_lead_org_terms = list(org_terms)
        elif org_role == "participant":
            effective_participant_org_terms = list(org_terms)
    if not effective_people_affiliation_org_terms and org_terms and org_role == "affiliation":
        effective_people_affiliation_org_terms = list(org_terms)

    # 컨텍스트 호환 필드 유지
    ctx.org_role = org_role

    people_filter_spec = dict(pending_strategy_filter_spec or {})
    org_filter = build_org_filter(OrgFilterInput(effective_lead_org_terms, role="lead")) if effective_lead_org_terms else None
    participant_org_filter = (
        build_prtcp_org_nested_filter(OrgFilterInput(effective_participant_org_terms, role="participant"))
        if effective_participant_org_terms
        else None
    )

    # people terms/filter (필요 시)
    people_terms = [t.strip() for t in (list(ctx.people_terms or []) or []) if str(t).strip()]
    gender_terms = [t.strip() for t in (list(ctx.gender_terms or []) or []) if str(t).strip()]
    people_org_terms: List[str] = []
    if org_role == "affiliation" and org_terms:
        people_org_terms = list(org_terms)
    qa_researchers = _get_attr(qa, "researchers", None) or []
    if isinstance(qa_researchers, str):
        qa_researchers = [qa_researchers]
    hint_people_terms = []
    hint_people_ids: List[Any] = []
    for researcher in (qa_researchers or []):
        if isinstance(researcher, str):
            name = researcher.strip()
            if name:
                hint_people_terms.append(name)
            continue
        name = _get_attr(researcher, "name", None)
        if isinstance(name, str):
            name = name.strip()
        if name:
            hint_people_terms.append(name)
        researcher_id = _get_attr(researcher, "researcher_id", None)
        if researcher_id not in (None, ""):
            hint_people_ids.append(researcher_id)
    participant_people_terms_hint = _normalize_hint_terms(
        (people_filter_spec or {}).get("participant_researcher_name")
    ) if isinstance(people_filter_spec, dict) else []
    participant_people_terms_hint = _normalize_hint_terms(
        [
            *participant_people_terms_hint,
            *_extract_people_filter_hints(hint_filters),
        ]
    )

    if hint_people_terms:
        if hint_policy == "merge":
            merged_people = hint_people_terms + people_terms
            deduped_people: List[str] = []
            seen_people: set[str] = set()
            for term in merged_people:
                if term in seen_people:
                    continue
                seen_people.add(term)
                deduped_people.append(term)
            people_terms = deduped_people
    if participant_people_terms_hint:
        people_terms = _normalize_hint_terms([*participant_people_terms_hint, *people_terms])
    ctx.people_terms = people_terms
    people_ids = list((ctx.ids_map or {}).get("person_no") or [])
    if hint_people_ids:
        if hint_policy == "merge":
            merged_ids = hint_people_ids + people_ids
            deduped_ids: List[Any] = []
            seen_ids: set[Any] = set()
            for pid in merged_ids:
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                deduped_ids.append(pid)
            people_ids = deduped_ids
    people_org_terms = list(effective_people_affiliation_org_terms)
    people_match_mode = str(getattr(ctx, "people_terms_match_mode", "") or "").strip().lower() or None
    # 분기 순서와 무관하게 참조 가능하도록 초기값 고정
    lookup_filter_enabled = False
    lookup_filter_policy = "off"
    people_min_should_hint = getattr(ctx, "people_terms_min_should", None)
    if people_match_mode == "and" and len(people_terms) >= 2:
        people_min_should = None
    elif people_min_should_hint is not None:
        people_min_should = people_min_should_hint
    elif len(people_terms) >= 2:
        people_min_should = 1
    else:
        people_min_should = None
    # lookup 정책 계산 전 단계이므로 초기값은 False로 둔다.
    people_promote_one_must = False

    people_spec = PeopleFilterInput(
        people_terms=people_terms,
        person_ids=people_ids,
        gender_terms=gender_terms,
        org_terms=people_org_terms,
        filter_spec=people_filter_spec.get("people_filter"),
        min_should=people_min_should,
        promote_one_must=people_promote_one_must,
    )
    people_filter = (
        build_people_filter(people_spec)
        if (people_terms or people_ids or gender_terms or people_org_terms)
        else None
    )

    def _build_org_must_gate() -> Any:
        if not planner_org_filter_present:
            return None
        gate_filters = [f for f in (participant_org_filter, org_filter, people_filter) if f is not None]
        if not gate_filters:
            return None
        if qmodels is not None and hasattr(qmodels, "Filter"):
            return qmodels.Filter(must=list(gate_filters))
        org_gate = None
        for gate_filter in gate_filters:
            org_gate = _and_filter(org_gate, gate_filter)
        return org_gate

    def _with_org_must_gate(base_filter: Any, *, col: Optional[str] = None, mode_override: Optional[str] = None) -> Any:
        mode_for_gate = mode_override or mode
        should_apply_org_gate = mode_for_gate in ("lookup", "join") and planner_org_filter_present
        org_gate = _build_org_must_gate() if should_apply_org_gate else None
        combined = _and_filter(base_filter, org_gate) if org_gate is not None else base_filter
        log_kv(
            "RAG.SERVER_FILTER.ORG_GATE",
            col=col,
            mode=mode_for_gate,
            planner_org_filter_present=planner_org_filter_present,
            server_org_filter_applied=bool(org_gate is not None),
            org_filter_keys=org_filter_keys,
        )
        return combined

    year_from = str(ctx.year_from or "").strip() or None
    year_to = str(ctx.year_to or "").strip() or None
    if not year_from and (ctx.years or []):
        year_from = str(ctx.years[0]).strip() or None
    if not year_to and (ctx.years or []):
        year_to = str(ctx.years[-1]).strip() or year_from
    ctx.year_from = year_from
    ctx.year_to = year_to
    year_range_filter = build_year_range_filter(year_from, year_to) if (year_from or year_to) else None

    perf_types_raw = [
        t.strip()
        for t in (list(ctx.perf_types or []) or [])
        if str(t).strip()
    ]
    perf_type_norm = normalize_perf_types(perf_types_raw)
    perf_types = perf_type_norm["tags"] or perf_type_norm["unknown"]
    ctx.perf_types = perf_types
    perf_type_filter = build_perf_type_filter(perf_types) if perf_types else None
    if perf_types or perf_types_source:
        log_kv(
            "RAG.PERF_TYPES.FINAL",
            source=perf_types_source or "derived",
            values=perf_types,
        )

    title_terms = [
        t.strip()
        for t in (list(ctx.title or []) or [])
        if str(t).strip()
    ]
    ctx.title = title_terms
    title_filter = build_title_filter(title_terms) if title_terms else None

    keyword_terms = [t.strip() for t in (list(ctx.keywords or []) or []) if str(t).strip()]
    # LLM(Planner) 키워드를 상위로 정렬해 상위 30개/쿼리 생성에서 우선 반영한다.
    keyword_terms = _merge_keywords_with_priority(planner_keywords, keyword_terms)
    if title_terms:
        keyword_terms = _merge_keywords_with_priority(title_terms, keyword_terms)
    ctx.keywords = keyword_terms
    kws = keyword_terms

    # perf/project tag filter (필요 시) + generic tag 분리 적용
    generic_tag_filter_raw = _build_tag_only_filter(list(ctx.tag_filters)) if ctx.tag_filters else None

    generic_project_tags, generic_perf_tags, generic_other_tags = _split_tag_filters_by_family(
        list(ctx.tag_filters or [])
    )
    project_tag_filters_for_col = list(ctx.project_tag_filters or []) + generic_project_tags + generic_other_tags
    perf_tag_filters_for_col = list(ctx.perf_tag_filters or []) + generic_perf_tags + generic_other_tags
    project_tag_filter = (
        _build_tag_only_filter(project_tag_filters_for_col) if project_tag_filters_for_col else None
    )
    perf_tag_filter = (
        _build_tag_only_filter(perf_tag_filters_for_col) if perf_tag_filters_for_col else None
    )

    hint_people_filters = _extract_people_filter_hints(hint_filters) if isinstance(hint_filters, dict) and hint_policy == "merge" else []
    hint_org_filters = _normalize_hint_terms(
        hint_filters.get("org_name")
    ) if isinstance(hint_filters, dict) and hint_policy == "merge" else []
    hint_tag_filters = _normalize_hint_terms(
        hint_filters.get("tag_filters")
    ) if isinstance(hint_filters, dict) and hint_policy == "merge" else []

    search_filter_min_conf = float(os.getenv("RAG_SEARCH_FILTER_MIN_CONF", "0.6"))
    search_filter_signal = bool(
        hint_people_filters
        or hint_org_filters
        or hint_tag_filters
        or title_terms
        or people_terms
        or org_terms
        or ctx.tag_filters
        or ctx.project_tag_filters
        or ctx.perf_tag_filters
    )
    search_filter_conf_ok = bool(
        (planner_confidence is not None and planner_confidence >= search_filter_min_conf)
        or (qa_conf >= search_filter_min_conf)
    )

    people_relation_disabled = False
    if people_terms and base_route in ("project", "perf") and relation and "people" in set(relation):
        people_relation_disabled = True
        log_kv(
            "RAG.PLAN.RELATION_DISABLED",
            level="warning",
            reason="people_terms_base_route",
            base_route=base_route,
            relation=relation,
            planner_only_adjustment=1,
            people_terms=people_terms[:4],
        )

    intent_view = ctx.intent_view()
    it = intent_view

    # -------------------------
    # 상세 로그: INTENT / PRESET / KEYWORDS
    # -------------------------
    log_section(
        "RAG.KEYWORDS",
        {
            "planner_keywords": planner_keywords,
            "final_keywords": kws,
            "priority_rule": "planner>hint/payload",
        },
    )
    log_kv(
        "RAG.INTENT",
        action=action,
        base_route=base_route,
        relation=relation,
        domain_hint=domain_hint,
        is_id_query=ctx.is_id_query,
        years=ctx.years,
        year_from=ctx.year_from,
        year_to=ctx.year_to,
        people_terms=list(ctx.people_terms or []),
        gender_terms=list(ctx.gender_terms or []),
        org_terms=list(ctx.org_terms or []),
        org_role=org_role,
        perf_tag_filters=list(ctx.perf_tag_filters or []),
        perf_types=list(ctx.perf_types or []),
        keywords=list(ctx.keywords or []),
        tag_filters=list(ctx.tag_filters or []),
        ids_flat=_flatten_ids_from_intent(intent_view)[:20],
    )
    # precompute embedding cache
    pre_vecs_cache: Dict[str, Dict[str, _PrecomputedEmbedding]] = {}

    def _get_pre_vecs(qtext: str) -> Dict[str, _PrecomputedEmbedding]:
        qtext = qtext or ""
        if qtext in pre_vecs_cache:
            return pre_vecs_cache[qtext]

        out: Dict[str, _PrecomputedEmbedding] = {}
        try:
            if "e5i_qa" in vector_names:
                v = resources.embed_e5i.get_query_embedding(qtext) if hasattr(resources.embed_e5i, "get_query_embedding") else resources.embed_e5i.get_text_embedding(qtext)
                if hasattr(v, "tolist"):
                    v = v.tolist()
                out["e5i_qa"] = _PrecomputedEmbedding(list(v))
            if "e5_qa" in vector_names:
                v = resources.embed_e5.get_query_embedding(qtext) if hasattr(resources.embed_e5, "get_query_embedding") else resources.embed_e5.get_text_embedding(qtext)
                if hasattr(v, "tolist"):
                    v = v.tolist()
                out["e5_qa"] = _PrecomputedEmbedding(list(v))
        except Exception as e:
            _timing_put(timings, "event.embed_precompute_error", 1.0)
            logger.warning(f"[RAG] embed precompute failed (q='{qtext[:40]}'): {e}")
            out = {}

        pre_vecs_cache[qtext] = out
        return out

    pre_vecs = _get_pre_vecs(q)

    def _validate_join_key_contract(
            mode_value: Optional[str],
            join_key_mode: Optional[str],
            ids_map_obj: Dict[str, List[str]],
            *,
            allow_missing_instance_ids: bool = True,
    ) -> None:
        mode_norm = str(mode_value or "").strip().lower()
        join_norm = str(join_key_mode or "").strip().lower() or None

        has_pjt_id = bool((ids_map_obj or {}).get("pjt_id"))
        has_pjt_no = bool((ids_map_obj or {}).get("pjt_no"))

        if mode_norm != "join":
            if join_norm is not None:
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_INVALID",
                    reason="join_key_mode must be null when mode is not JOIN",
                )
            return

        if join_norm not in ("instance", "group"):
            raise StrategyViolation(
                error_code="PLANNER_JOIN_KEY_MODE_INVALID",
                reason="join mode requires join_key_mode in {'instance','group'}",
            )

        if has_pjt_id and has_pjt_no:
            raise StrategyViolation(
                error_code="PLANNER_JOIN_MIXED_PROJECT_KEYS",
                reason="ids_map.pjt_id and ids_map.pjt_no are mutually exclusive in JOIN",
            )

        if join_norm == "instance":
            if has_pjt_no:
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                    reason="join_key_mode=instance only allows ids_map.pjt_id",
                )
            # ✅ instance는 ids_map.pjt_id가 없어도 허용 (Hop1에서 pjt_id를 payload 최상위에서 뽑는다)
            if (not has_pjt_id) and (not allow_missing_instance_ids):
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                    reason="join_key_mode=instance requires ids_map.pjt_id (or allow hop1 extraction)",
                )

        if join_norm == "group":
            if has_pjt_id:
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                    reason="join_key_mode=group only allows ids_map.pjt_no",
                )
            # ✅ group은 반드시 ids_map.pjt_no 필요 (pjt_no는 전략 자체가 다르므로 플래너가 명시해야 함)
            if not has_pjt_no:
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_IDS_MISMATCH",
                    reason="join_key_mode=group requires ids_map.pjt_no",
                )

    def _planner_action_to_mode(action_value: Optional[str]) -> Optional[str]:
        if not action_value:
            return None
        action_value = str(action_value).strip().lower()
        if action_value in ("list", "stats", "download", "id_exact", "id_fuzzy", "detail"):
            return "lookup"
        if action_value in ("topic", "search"):
            return "search"
        if action_value == "join":
            return "join"
        return None

    def validate_strategy(strategy: StrategySpec) -> tuple[bool, list[str]]:
        _mode, errors = planner_contract_mode(
            strategy_mode=strategy.mode,
            strategy_action=strategy.action,
            strategy_relation=strategy.relation,
            fallback_mode=strategy.mode,
        )
        try:
            _validate_join_key_contract(strategy.mode, strategy.join_key_mode, dict(ctx.ids_map or {}))
        except StrategyViolation as exc:
            errors.append(str(exc))
        return (len(errors) == 0), errors

    # plan
    planner_mode = strategy_mode or hint_mode
    planner_mode_source = "strategy.mode" if strategy_mode else ("qa.mode" if planner_mode else None)
    if not planner_mode:
        planner_mode = _planner_action_to_mode(planner_action)
        planner_mode_source = "qa.action" if planner_mode else None
    if planner_mode not in ("search", "lookup", "join"):
        if planner_mode:
            log_kv(
                "RAG.PLAN.PLANNER_MODE_INVALID",
                level="warning",
                planner_mode=planner_mode,
                planner_mode_source=planner_mode_source,
                planner_action=planner_action,
            )
        planner_mode = None
        planner_mode_source = None

    plan, policy_reason = _build_plan(
        intent_view,
        preferred_mode=planner_mode,
        preferred_mode_source=planner_mode_source,
    )
    if pending_strategy_filter_spec:
        plan = replace(plan, filters=pending_strategy_filter_spec)
    ctx.plan = plan
    ctx.target_collections = list(plan.target_collections)
    planner_target_cols_from_hint = _normalize_strategy_target_cols(hinted_cols)
    if planner_target_cols_from_hint:
        # v1.1 계약: planner가 확정한 target_cols를 실행 레이어가 재결정하지 않는다.
        ctx.target_collections = list(planner_target_cols_from_hint)
        plan = replace(plan, target_collections=tuple(planner_target_cols_from_hint))
        ctx.plan = plan
    strict_strategy_consistency = _env_flag("RAG_STRICT_STRATEGY_CONSISTENCY", "1")
    planner_relation_locked = plan.relation
    planner_target_cols_locked = _normalize_strategy_target_cols(plan.target_collections)
    log_kv(
        "RAG.STRATEGY.POLICY",
        strict_strategy_consistency=int(strict_strategy_consistency),
        allowed_branches=["compile_validation_only"],
        forbidden_branches=["hinted_target_cols_redecision", "effective_allow_redecision"],
        planner_relation=planner_relation_locked,
        planner_target_cols=planner_target_cols_locked,
    )
    planner_first_applied = bool(planner_mode)
    mode_override_requested = False
    mode_override_reason = None
    mode_override_from = None
    mode_override_to = None
    if hinted_cols:
        hinted_cols_norm = _normalize_strategy_target_cols(hinted_cols)
        log_kv(
            "RAG.STRATEGY.ALLOWLIST",
            policy="compile_validation",
            branch="hinted_target_cols",
            planner_target_cols=planner_target_cols_locked,
            hinted_target_cols=hinted_cols_norm,
            applied=int(planner_target_cols_locked == hinted_cols_norm),
        )
    if people_relation_disabled and plan.mode == "join":
        logger.warning(
            "[RAG] people_relation_disabled while join mode (action=%s, base_route=%s)",
            action,
            base_route,
        )

    planner_mode_error = None
    planner_recalled = False
    planner_strategy_mode = strategy_mode or plan.mode
    planner_strategy_action = strategy_action or action
    planner_strategy_relation = strategy_relation if strategy_relation is not None else relation
    strategy_snapshot = StrategySpec(
        mode=planner_strategy_mode,
        action=planner_strategy_action,
        relation=planner_strategy_relation,
        join_key_mode=(strategy_join_key_mode or hint_join_key_mode or getattr(ctx, "join_key_mode", None)),
    )
    strategy_ok, strategy_errors = validate_strategy(strategy_snapshot)
    if not strategy_ok:
        planner_mode_error = ",".join(strategy_errors)
        planner_confidence = min(float(planner_confidence), 0.01) if planner_confidence is not None else 0.01
        logger.warning(
            "[RAG] invalid planner strategy: mode=%s action=%s relation=%s errors=%s",
            planner_strategy_mode,
            planner_strategy_action,
            planner_strategy_relation,
            strategy_errors,
        )
        log_kv(
            "RAG.PLAN.INVALID_STRATEGY",
            level="warning",
            mode=planner_strategy_mode,
            action=planner_strategy_action,
            relation=planner_strategy_relation,
            errors=strategy_errors,
            planner_confidence=planner_confidence,
        )
        raise StrategyViolation(error_code="PLANNER_INVALID_STRATEGY", reason=f"invalid planner strategy: {planner_mode_error}")

    route_for_contract = get_relation_route(planner_strategy_relation) if planner_strategy_relation else None
    relation_target_cols_for_contract = (
        (route_for_contract.hop1_col, route_for_contract.hop2_col)
        if route_for_contract is not None
        else None
    )
    join_key_mode_for_contract = str(strategy_snapshot.join_key_mode or "").strip().lower() or "instance"
    planner_contract_violations = validate_planner_contract(
        mode=planner_strategy_mode,
        head=hint_head or base_route,
        relation=planner_strategy_relation,
        target_cols=list(ctx.target_collections or plan.target_collections or []),
        ids_map=getattr(ctx, "ids_map", None),
        relation_target_cols=relation_target_cols_for_contract,
        join_key_mode=join_key_mode_for_contract,
    )
    if planner_contract_violations:
        first = planner_contract_violations[0]
        raise StrategyViolation(
            error_code=first.error_code,
            reason=first.reason,
            violations=planner_contract_violations,
        )

    planner_filter_spec = dict(plan.filters or {})

    preset_intent_view = ctx.intent_view()
    preset: _SearchPreset = _build_search_preset(preset_intent_view)
    lex_w_eff = dict(lexical_field_weights) if lexical_field_weights is not None else dict(preset.lexical_field_weights)

    # -----------------------------------------------------------------
    # Compile Stage: planner 정책(filter/topk/rerank)을 실행 스펙으로 컴파일한다.
    # 이 단계는 실행 mode를 바꾸지 않으며, mode 결정 이후에만 동작한다.
    # -----------------------------------------------------------------
    strategy_limit = _coerce_int(_get_attr(strategy_topk_spec, "limit", 0), 0) if strategy_enabled else 0
    if strategy_limit > 0:
        hinted_limit = strategy_limit

    if hinted_limit > 0:
        preset.top_k_lex_cand = min(int(preset.top_k_lex_cand), hinted_limit * 20)
        preset.top_k_lex = min(int(preset.top_k_lex), max(10, hinted_limit * 2))
        preset.sparse_topk = min(int(preset.sparse_topk or preset.top_k_lex), max(10, hinted_limit * 2))
        preset.max_ctx_items = min(int(preset.max_ctx_items), hinted_limit)

    sparse_vector_name_eff = (sparse_vector_name or preset.sparse_vector_name or "bm25").strip()
    sparse_topk_eff = int(sparse_topk or preset.sparse_topk or preset.top_k_lex)
    # sparse_weight는 RRF에서 lexical 소스 가중치로만 사용 (retrieval API에는 전달하지 않음).
    sparse_weight_eff = float(sparse_weight or preset.sparse_weight or preset.w_lex)
    topk_spec = _build_topk_spec(
        preset,
        sparse_vector_name=sparse_vector_name_eff,
        sparse_topk=sparse_topk_eff,
        sparse_weight=sparse_weight_eff,
    )
    rerank_spec = dict(strategy_rerank_spec) if (strategy_enabled and isinstance(strategy_rerank_spec, dict) and strategy_rerank_spec) else _build_rerank_spec(plan.mode)
    rerank_spec.setdefault("final_keep", 80)

    log_kv(
        "RAG.PRESET/PLAN.PRE",
        policy_version=SEARCH_POLICY_VERSION,
        preset_key=getattr(preset, "strategy_key", None),
        topk_spec=topk_spec,
        ctx_budget=int(ctx_budget),
    )

    search_filter_enabled = bool(plan.mode == "search" and search_filter_signal and search_filter_conf_ok)

    lookup_filter_policy_raw = (
        getattr(ctx, "lookup_filter_policy_hint", None)
        or os.getenv("RAG_LOOKUP_FILTER_POLICY", "hard")
    )
    lookup_filter_policy = normalize_lookup_filter_policy(lookup_filter_policy_raw)
    if lookup_filter_policy is None:
        logger.warning(
            "[RAG] invalid RAG_LOOKUP_FILTER_POLICY=%s, falling back to 'hard'",
            lookup_filter_policy_raw,
        )
        lookup_filter_policy = "hard"

    lookup_title_filter_policy = str(os.getenv("RAG_LOOKUP_TITLE_FILTER_POLICY", "soft")).strip().lower()
    if lookup_title_filter_policy != "soft":
        logger.warning(
            "[RAG] invalid RAG_LOOKUP_TITLE_FILTER_POLICY=%s, forcing 'soft'",
            lookup_title_filter_policy,
        )
        lookup_title_filter_policy = "soft"
    log_kv(
        "RAG.LOOKUP.TITLE_FILTER_POLICY",
        policy=lookup_title_filter_policy,
        mode=plan.mode,
        title_terms=title_terms[:4],
    )

    lookup_filter_enabled = bool(
        plan.mode == "lookup"
        and lookup_filter_policy in ("hard", "must_one_then_should")
        and search_filter_signal
        and search_filter_conf_ok
    )

    # lookup 정책 확정 후 people must 승격 여부를 재계산하고 필요 시 필터를 재컴파일한다.
    people_promote_one_must_resolved = bool(
        lookup_filter_enabled
        and lookup_filter_policy == "must_one_then_should"
        and not people_ids
        and len(people_terms) == 1
    )
    if people_promote_one_must_resolved != people_promote_one_must:
        people_promote_one_must = people_promote_one_must_resolved
        people_spec = PeopleFilterInput(
            people_terms=people_terms,
            person_ids=people_ids,
            gender_terms=gender_terms,
            org_terms=people_org_terms,
            filter_spec=people_filter_spec.get("people_filter"),
            min_should=people_min_should,
            promote_one_must=people_promote_one_must,
        )
        people_filter = (
            build_people_filter(people_spec)
            if (people_terms or people_ids or gender_terms or people_org_terms)
            else None
        )

    force_people_terms = list(participant_people_terms_hint)

    if relation and plan.mode in ("search", "lookup"):
        logger.warning(
            "[RAG] relation-mode conflict detected (mode=%s, relation=%s, hint_mode=%s)",
            plan.mode,
            relation,
            hint_mode,
        )
        log_kv(
            "RAG.PLAN.MODE_CONFLICT",
            level="warning",
            mode=plan.mode,
            relation=relation,
            hint_mode=hint_mode,
            base_route=base_route,
            action=action,
        )

    relation_lookup_policy = str(os.getenv("RAG_RELATION_LOOKUP_POLICY", "filter")).strip().lower()
    if relation_lookup_policy not in ("filter", "join"):
        logger.warning(
            "[RAG] invalid RAG_RELATION_LOOKUP_POLICY=%s, falling back to 'filter'",
            relation_lookup_policy,
        )
        relation_lookup_policy = "filter"

    relation_action = bool(relation and action == "relation")
    has_relation_join_ids = _has_relation_join_ids(it)
    relation_lookup_enforce_raw = (planner_filter_spec or {}).get("relation_lookup_enforce")
    relation_lookup_enforce = str(relation_lookup_enforce_raw).strip().lower() in ("1", "true", "yes", "y")
    if relation and plan.mode == "lookup":
        if relation_action:
            logger.warning(
                "[RAG] lookup+relation(action) requested to join (relation=%s)",
                relation,
            )
        elif relation_lookup_policy == "join" and has_relation_join_ids:
            logger.warning(
                "[RAG] lookup+relation requested to join (policy=%s, relation=%s)",
                relation_lookup_policy,
                relation,
            )
        if relation_lookup_enforce:
            logger.warning(
                "[RAG] relation_lookup_enforce=1 enforcing relation filters in lookup (policy=%s, relation=%s)",
                relation_lookup_policy,
                relation,
            )
        log_kv(
            "RAG.PLAN.RELATION_LOOKUP_POLICY",
            level="warning",
            policy=relation_lookup_policy,
            mode=plan.mode,
            relation=relation,
            enforced=int(relation_lookup_enforce),
            source="planner_filter_contract",
        )

    join_hop1_lookup_filter_enabled = bool(
        plan.mode == "join"
        and (
            relation_lookup_enforce
            or (
                base_route == "project"
                and relation == ("project", "perf")
                and bool(people_terms)
            )
        )
    )
    if join_hop1_lookup_filter_enabled:
        log_kv(
            "RAG.JOIN.HOP1.LOOKUP_FILTER.ENFORCE",
            mode=plan.mode,
            base_route=base_route,
            relation=relation,
            people_terms=people_terms[:4],
            relation_lookup_enforce=int(relation_lookup_enforce),
        )
    join_execution_policy = _resolve_join_execution_policy(
        relation=relation,
        mode=plan.mode,
        action=action,
        has_relation_join_ids=has_relation_join_ids,
    )
    if join_execution_policy["required"]:
        logger.warning(
            "[RAG] join required: relation=%s action=%s has_relation_join_ids=%s reason=%s",
            relation,
            action,
            has_relation_join_ids,
            join_execution_policy["reason"],
        )
        log_kv(
            "RAG.PLAN.JOIN_REQUIRED",
            level="warning",
            relation=relation,
            action=action,
            has_relation_join_ids=int(has_relation_join_ids),
            reason=join_execution_policy["reason"],
        )

    # allow 적용 (force/allow)
    if effective_allow:
        filtered = _pick_collections((ctx.target_collections or []), effective_allow)
        effective_allow_norm = _normalize_strategy_target_cols(filtered if filtered else list(effective_allow))
        log_kv(
            "RAG.STRATEGY.ALLOWLIST",
            policy="compile_validation",
            branch="effective_allow",
            planner_target_cols=planner_target_cols_locked,
            effective_allow_target_cols=effective_allow_norm,
            applied=0,
        )
        _strategy_consistency_or_violation(
            strict=strict_strategy_consistency,
            mismatch_kind="target_cols",
            planner_value=planner_target_cols_locked,
            executed_value=effective_allow_norm,
            context={"branch": "effective_allow"},
        )

    title_filter_applied_to = None
    if title_filter and plan.mode == "lookup":
        title_filter_applied_to = "project/perf"
    tag_filter_applied_to = None
    if (project_tag_filter or perf_tag_filter) and plan.mode == "lookup":
        tag_targets: list[str] = []
        if project_tag_filter:
            tag_targets.append("project")
        if perf_tag_filter:
            tag_targets.append("perf")
        tag_filter_applied_to = "/".join(tag_targets) if tag_targets else None

    search_filter_server_policy = "must_not_only" if plan.mode == "search" else "lookup_only"
    search_filter_server_applied = False
    filter_spec = {
        **planner_filter_spec,
        "search_filter_enabled": search_filter_enabled,
        "lookup_filter_enabled": lookup_filter_enabled,
        "relation_lookup_enforce": relation_lookup_enforce,
        "lookup_filter_policy": lookup_filter_policy,
        "lookup_title_filter_policy": lookup_title_filter_policy,
        "filter_signal": search_filter_signal,
        "filter_conf_ok": search_filter_conf_ok,
        "search_filter_server_policy": search_filter_server_policy,
        "search_filter_server_applied": search_filter_server_applied,
    }
    planner_filter_diff = _diff_filter_spec(
        planner_filter_spec=planner_filter_spec,
        executed_filter_spec=filter_spec,
    )
    planner_filter_diff_changed = planner_filter_diff.get("changed", {})
    log_kv(
        "RAG.STRATEGY.FILTER_SPEC.DIFF",
        level="error" if (strict_strategy_consistency and planner_filter_diff_changed) else "info",
        planner_filter_keys=planner_filter_diff.get("planner_keys", []),
        changed=planner_filter_diff_changed,
        changed_count=len(planner_filter_diff_changed),
        strict_strategy_consistency=int(strict_strategy_consistency),
    )
    if planner_filter_diff_changed:
        _strategy_consistency_or_violation(
            strict=strict_strategy_consistency,
            mismatch_kind="filter_spec",
            planner_value={k: planner_filter_spec.get(k) for k in planner_filter_diff.get("planner_keys", [])},
            executed_value={k: filter_spec.get(k) for k in planner_filter_diff.get("planner_keys", [])},
            context={"phase": "compile"},
        )
    strategy = StrategySpec(
        mode=plan.mode,
        action=action,
        relation=relation,
        join_key_mode=(strategy_join_key_mode or hint_join_key_mode or getattr(ctx, "join_key_mode", None)),
        people_terms=tuple(people_terms or []),
        target_collections=tuple(ctx.target_collections or []),
        search_filter_enabled=bool(search_filter_enabled),
        lookup_filter_enabled=bool(lookup_filter_enabled),
        relation_lookup_enforce=bool(relation_lookup_enforce),
        lookup_filter_policy=lookup_filter_policy,
        lookup_filter_min_should=people_min_should,
        lookup_filter_gate=people_match_mode,
        lookup_filter_promote_one_must=people_promote_one_must,
        lookup_title_filter_policy=lookup_title_filter_policy,
        search_filter_server_policy=search_filter_server_policy,
    )
    plan = replace(
        plan,
        relation=relation,
        join_key_mode=(strategy.join_key_mode or plan.join_key_mode),
        target_collections=tuple(ctx.target_collections or []),
        filters=filter_spec,
    )
    ctx.plan = plan
    ctx.strategy = strategy

    mode_raw = (strategy.mode or plan.mode or "").strip().lower()
    if mode_raw not in ("search", "lookup", "join"):
        raise ValueError(f"invalid execution mode: {mode_raw!r}")
    mode = mode_raw
    relation = strategy.relation
    target_collections = list(strategy.target_collections or plan.target_collections or ())
    planner_target_cols_exec = _normalize_strategy_target_cols(planner_target_cols_locked)
    executed_target_cols = _normalize_strategy_target_cols(target_collections)
    planner_relation_eq = int(planner_relation_locked == relation)
    planner_target_cols_eq = int(planner_target_cols_exec == executed_target_cols)
    log_kv(
        "RAG.MODE.EXECUTION",
        planner_mode=planner_mode,
        executed_mode=mode,
        mode_equal=int(str(planner_mode or "").strip().lower() == str(mode or "").strip().lower()) if planner_mode else None,
        planner_relation=planner_relation_locked,
        executed_relation=relation,
        planner_relation_eq=planner_relation_eq,
        planner_target_cols=planner_target_cols_exec,
        executed_target_cols=executed_target_cols,
        planner_target_cols_eq=planner_target_cols_eq,
        **{
            "planner_relation == executed_relation": planner_relation_eq,
            "planner_target_cols == executed_target_cols": planner_target_cols_eq,
        },
        strict_strategy_consistency=int(strict_strategy_consistency),
    )
    _strategy_consistency_or_violation(
        strict=strict_strategy_consistency,
        mismatch_kind="relation",
        planner_value=planner_relation_locked,
        executed_value=relation,
        context={"phase": "execution"},
    )
    _strategy_consistency_or_violation(
        strict=strict_strategy_consistency,
        mismatch_kind="target_cols",
        planner_value=planner_target_cols_exec,
        executed_value=executed_target_cols,
        context={"phase": "execution"},
    )
    people_terms = [t.strip() for t in strategy.people_terms if str(t).strip()]
    search_filter_enabled = bool(strategy.search_filter_enabled)
    lookup_filter_enabled = bool(strategy.lookup_filter_enabled)
    relation_lookup_enforce = bool(strategy.relation_lookup_enforce)

    log_kv(
        "RAG.FILTERS",
        policy_version=SEARCH_POLICY_VERSION,
        org_terms=org_terms,
        org_role=org_role,
        people_terms=people_terms,
        people_ids=people_ids,
        gender_terms=gender_terms,
        people_org_terms=people_org_terms,
        year_from=year_from,
        year_to=year_to,
        perf_types=perf_types,
        keywords=keyword_terms,
        perf_tag_filters=list(ctx.perf_tag_filters or []),
        title_terms=title_terms,
        tag_filters=list(ctx.tag_filters or []),
        org_filter=str(org_filter) if org_filter is not None else None,
        participant_org_filter=str(participant_org_filter) if participant_org_filter is not None else None,
        people_filter=str(people_filter) if people_filter is not None else None,
        perf_tag_filter=str(perf_tag_filter) if perf_tag_filter is not None else None,
        title_filter=str(title_filter) if title_filter is not None else None,
        project_tag_filter=str(project_tag_filter) if project_tag_filter is not None else None,
        generic_tag_filter=str(generic_tag_filter_raw) if generic_tag_filter_raw is not None else None,
        year_range_filter=str(year_range_filter) if year_range_filter is not None else None,
        perf_type_filter=str(perf_type_filter) if perf_type_filter is not None else None,
        title_filter_applied_to=title_filter_applied_to,
        tag_filter_applied_to=tag_filter_applied_to,
        search_filter_server_policy=search_filter_server_policy,
        search_filter_server_applied=int(search_filter_server_applied),
    )

    strategy_key = build_strategy_key(action, plan.mode)
    # 로그 키 구분: strategy_version(planner 계약 버전) vs policy_version(내부 검색 정책 버전)
    strategy_summary = {
        "mode": mode,
        "policy_version": SEARCH_POLICY_VERSION,
        "strategy_key": strategy_key,
        "policy_reason": policy_reason,
        "planner": {
            "planner_first_applied": planner_first_applied,
            "planner_mode": planner_mode,
            "planner_action": planner_action,
            "planner_recalled": planner_recalled,
            "planner_mode_error": planner_mode_error,
            "mode_override_requested": mode_override_requested,
            "mode_override_reason": mode_override_reason,
            "mode_override_from": mode_override_from,
            "mode_override_to": mode_override_to,
        },
        "filter": filter_spec,
        "filter_spec": filter_spec,
        "topk_spec": topk_spec,
        "rerank_spec": rerank_spec,
        "mix_weights": {
            "dense": {k: float(v) for k, v in (w_dense_map or {}).items()},
            "sparse": float(sparse_weight_eff),
            "rerank": rerank_spec.get("rerank_weights"),
        },
    }

    log_kv(
        "RAG.INTENT",
        strategy_summary=strategy_summary,
    )
    log_kv(
        "RAG.FILTERS",
        strategy_summary=strategy_summary,
        mode=plan.mode,
        search_filter_signal=search_filter_signal,
        search_filter_conf_ok=search_filter_conf_ok,
        lookup_filter_policy=lookup_filter_policy,
    )

    log_kv(
        "RAG.ROUTE/PLAN",
        strategy_summary=strategy_summary,
        mode=plan.mode,
        route=getattr(plan, "route", None),
        base_route=base_route,
        action=action,
        relation=relation,
        output_type=getattr(plan, "output_type", None),
        target_cols=list(target_collections or []),
        qa_conf=qa_conf,
        hint_applied=int(hint_conf_ok),
        planner_applied=planner_applied,
        planner_failed=planner_failed,
        planner_first_applied=int(planner_first_applied),
        planner_mode=planner_mode,
        executed_mode=mode,
        planner_action=planner_action,
        mode_override_requested=int(mode_override_requested),
        mode_override_reason=mode_override_reason,
        mode_override_from=mode_override_from,
        mode_override_to=mode_override_to,
        lookup_filter_policy=lookup_filter_policy,
        search_filter_enabled=int(search_filter_enabled),
        lookup_filter_enabled=int(lookup_filter_enabled),
        relation_lookup_enforce=int(relation_lookup_enforce),
        search_filter_server_policy=search_filter_server_policy,
        search_filter_server_applied=int(search_filter_server_applied),
    )
    log_kv(
        "RAG.PRESET/PLAN.POST",
        strategy_summary=strategy_summary,
        policy_version=SEARCH_POLICY_VERSION,
        strategy_key=strategy_key,
        mode=plan.mode,
        base_route=plan.base_route,
        action=plan.action,
        relation=ctx.relation,
        output_type=getattr(plan, "output_type", None),
        target_cols=target_collections,
        filter_spec=filter_spec,
        topk_spec=topk_spec,
        rerank_spec=rerank_spec,
        planner_categories=planner_categories,
        planner_limit=planner_limit,
        planner_retrieval_query=planner_retrieval_query,
        planner_confidence=planner_confidence,
        planner_meta_source=planner_meta_source,
    )

    # planner 정책(topk_spec/rerank_spec)을 실행 레이어에서 그대로 사용
    policy_topk = topk_spec or {}
    topk_dense = int(policy_topk.get("top_k_dense", preset.top_k_dense))
    topk_lex_cand = int(policy_topk.get("top_k_lex_cand", preset.top_k_lex_cand))
    topk_lex = int(policy_topk.get("top_k_lex", sparse_topk_eff))
    use_dense_threshold_policy = bool(policy_topk.get("use_dense_threshold", preset.use_dense_threshold))
    min_dense_score_policy = float(policy_topk.get("min_dense_score", preset.min_dense_score))

    def _maybe_followup_perf_hop_from_project() -> List[str]:
        if relation != ("project", "perf"):
            return []
        ids_map = getattr(it, "ids_map", None) or {}
        if isinstance(ids_map, dict) and any(v for v in ids_map.values() if v):
            return []
        if mode == "join":
            return []
        target_cols = list(target_collections or _default_target_collections())
        if COL_PROJECT not in target_cols or COL_PERF not in target_cols:
            log_kv(
                "RAG.PERF.FOLLOWUP.SKIP",
                reason="target_collections",
                target_cols=target_cols,
            )
            return []

        route = get_relation_route(relation)
        hop1_tag_filters = route.hop1_tag_filters if route else None
        hop1_filter = _build_tag_only_filter(hop1_tag_filters) if hop1_tag_filters else None
        if year_range_filter:
            hop1_filter = _and_filter(hop1_filter, year_range_filter)

        hop1_keep = int(os.getenv("RAG_HOP1_KEEP", "5"))
        hop1_k_base = int(os.getenv("RAG_HOP1_TOPK_BASE", "250"))

        log_kv(
            "RAG.PERF.FOLLOWUP.HOP1",
            hop1_col=COL_PROJECT,
            hop1_q=q,
            hop1_tag_filters=hop1_tag_filters,
            hop1_filter=str(hop1_filter) if hop1_filter is not None else None,
            hop1_k_base=hop1_k_base,
            hop1_keep=hop1_keep,
        )

        vec_avail = _named_vectors_in_collection(qdr, COL_PROJECT)
        use_vecs_h1 = [v for v in vector_names if (not isinstance(vec_avail, set) or v in vec_avail)]
        pre_vecs_h1 = _get_pre_vecs(q)
        emb_map_h1: Dict[str, Any] = {}
        for vname in use_vecs_h1:
            pe = pre_vecs_h1.get(vname)
            emb_map_h1[vname] = pe if pe is not None else fallback_emb.get(vname)
        emb_map_h1 = {k: v for k, v in emb_map_h1.items() if v is not None}

        local_timings_h1: Dict[str, float] = {}
        sr1 = _call_dense_retrieve_hybrid_multi(
            qdr=qdr,
            emb_map=emb_map_h1,
            qtext=q,
            kws=kws,
            collection=COL_PROJECT,
            lexical_fields=preset.lexical_fields,
            sparse_vector_name=sparse_vector_name_eff,
            sparse_topk=min(hop1_k_base, 80),
            top_k_dense=topk_dense,
            top_k_lex_cand=hop1_k_base,
            top_k_lex=min(hop1_k_base, 80),
            query_filter=hop1_filter,
            timings_out=local_timings_h1,
            require_hybrid_both_sides=True,
            contract_scope="join_hop1_followup",
            violation_on_contract=True,
        )
        _apply_dense_threshold(
            sr1,
            use_dense_threshold=use_dense_threshold_policy,
            min_dense_score=min_dense_score_policy,
            log_prefix="RAG.DENSE.THRESHOLD.FOLLOWUP",
            col=COL_PROJECT,
            action=action,
            base_route=base_route,
            relation=relation,
        )
        _ensure_collection_mark((sr1.get("lexical") or []), COL_PROJECT)
        for _, lst in (sr1.get("dense") or {}).items():
            _ensure_collection_mark(lst or [], COL_PROJECT)

        sources_h1: List[_RankSource] = []
        for vname, lst in (sr1.get("dense") or {}).items():
            base_weight = float(w_dense_map.get(vname, 1.0))
            score_weight = _dense_score_weight(lst or []) if _use_dense_score_weight() else 1.0
            sources_h1.append(
                _RankSource(
                    name=f"{COL_PROJECT}:{vname}",
                    weight=base_weight * score_weight,
                    points=lst or [],
                )
            )
        sources_h1.append(
            _RankSource(
                name=f"{COL_PROJECT}:lex",
                weight=float(sparse_weight_eff),
                points=sr1.get("lexical") or [],
            )
        )
        h1_rrf = _rrf_merge(sources_h1, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=500)
        h1_rrf = _dedup_by_doc_id(h1_rrf)
        hop1_reranked = _final_rerank(
            h1_rrf,
            it=it,
            kws=kws,
            lex_w=lex_w_eff,
            base_route="project",
            mode="search",
            keep=int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")),
            tag_boost=float(getattr(preset, "tag_boost", 0.0)),
            tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
        )
        hop1_reranked = _dedup_by_doc_id(hop1_reranked)
        if len(hop1_reranked) > ctx_hard_limit:
            hop1_reranked = hop1_reranked[:ctx_hard_limit]

        hop1_top = hop1_reranked[: max(1, hop1_keep)]
        if hop1_top:
            hydrate_keep = min(
                max(hop1_keep, int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), 20),
                ctx_hard_limit,
            )
            _hydrate_points_payload(qdr, hop1_reranked[:hydrate_keep])
            missing = _count_missing_join_keys(hop1_top)
            if missing.get("missing_pjt_any") or missing.get("missing_tag"):
                if str(os.getenv("RAG_HOP1_REHYDRATE_ON_MISSING_KEYS", "1")).strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "y",
                ):
                    _hydrate_points_payload(qdr, hop1_top)
                _raise_on_missing_join_keys(hop1_top, scope="join_hop1_followup")

        join_ids = _extract_pjt_ids(hop1_top, max_ids=50)
        join_keys = list(dict.fromkeys(join_ids))
        log_kv(
            "RAG.PERF.FOLLOWUP.JOIN_IDS",
            join_ids_preview=join_keys[:10],
            join_ids_count=len(join_keys),
            timings={k: float(v) for k, v in (local_timings_h1 or {}).items()},
        )
        return join_keys

    # -------------------------
    # JOIN mode (2-hop)
    # -------------------------
    if mode == "join" and relation:
        t_hop0 = time.time()
        ids_map = getattr(it, "ids_map", None) or getattr(it, "ids", None) or {}
        join_key_mode = str((getattr(strategy, "join_key_mode", None) or "")).strip().lower() or None
        pjt_ids = [str(x).strip() for x in (ids_map.get("pjt_id") or []) if str(x).strip()]
        pjt_nos = [str(x).strip() for x in (ids_map.get("pjt_no") or []) if str(x).strip()]
        seed_join_pjt_ids = list(dict.fromkeys(pjt_ids))
        seed_join_pjt_nos = list(dict.fromkeys(pjt_nos))
        seed_join_ids = seed_join_pjt_ids if join_key_mode == "instance" else seed_join_pjt_nos



        # relation mapping
        hop1_col = hop2_col = ""
        hop1_kind = hop2_kind = "project"
        hop1_tag_filters: Optional[List[str]] = None
        hop2_tag_filters: Optional[List[str]] = None
        hop2_label = ""

        join_relation = relation
        route = get_relation_route(join_relation)
        if route is None:
            raise StrategyViolation(
                error_code="PLANNER_JOIN_RELATION_UNRESOLVED",
                reason=f"JOIN relation 해석 실패(relation={join_relation})",
            )
        hop1_col, hop2_col = route.hop1_col, route.hop2_col
        hop1_kind, hop2_kind = route.hop1_kind, route.hop2_kind
        hop1_tag_filters, hop2_tag_filters = route.hop1_tag_filters, route.hop2_tag_filters
        hop2_label = route.hop2_label

        if join_relation:
            hop1_keep_env = int(os.getenv("RAG_HOP1_KEEP", "5"))
            planner_limit = int(planner_limit or 0)
            hop1_keep = max(1, min(hop1_keep_env, planner_limit)) if planner_limit > 0 else hop1_keep_env

            hop2_keep = int(os.getenv("RAG_HOP2_KEEP", "10"))
            hop1_k_base = int(os.getenv("RAG_HOP1_TOPK_BASE", "250"))
            hop2_k_base = int(os.getenv("RAG_HOP2_TOPK_BASE", "300"))

            # Hop1 query sanitize (people/org head에서 잡음 제거)
            hop1_q = q
            hop2_q = q

            join_pjt_ids: List[str] = []
            join_pjt_nos: List[str] = []
            hop1_top: List[Any] = []
            hop1_filter = None

            # 1) Hop1 (SEARCH): mode=join이면 ids 유무와 무관하게 항상 수행
            has_seed_join_keys = bool(seed_join_pjt_nos) if join_key_mode == "group" else bool(seed_join_pjt_ids)
            log_kv(
                "RAG.PLAN.JOIN_EXECUTED",
                relation=relation,
                action=action,
                join_key_mode=join_key_mode,
                has_seed_join_keys=int(has_seed_join_keys),
            )
            if join_key_mode == "group" and seed_join_pjt_nos:
                join_pjt_nos = seed_join_pjt_nos[:]
            elif join_key_mode == "instance" and seed_join_pjt_ids:
                join_pjt_ids = seed_join_pjt_ids[:]

            hop1_filter = _build_tag_only_filter(hop1_tag_filters) if hop1_tag_filters else None
            if  people_filter:
                hop1_filter = _and_filter(hop1_filter, people_filter)
            if  org_filter:
                hop1_filter = _and_filter(hop1_filter, org_filter)
            hop1_filter = _with_org_must_gate(hop1_filter, col=hop1_col, mode_override="join")
            if join_hop1_lookup_filter_enabled and hop1_col in (COL_PROJECT, COL_PERF):
                join_people_filter = people_filter
                join_promote_one_must = bool(
                    people_promote_one_must
                    or (
                        join_hop1_lookup_filter_enabled
                        and lookup_filter_policy == "must_one_then_should"
                        and not people_ids
                        and len(people_terms) == 1
                    )
                )
                final_people_terms = list(ctx.people_terms or people_terms or [])
                if join_people_filter is None and final_people_terms:
                    join_people_filter = build_people_filter(
                        PeopleFilterInput(
                            people_terms=final_people_terms,
                            person_ids=people_ids,
                            gender_terms=gender_terms,
                            org_terms=people_org_terms,
                            min_should=people_min_should,
                            promote_one_must=join_promote_one_must,
                        )
                    )
                    if join_people_filter is None:
                        log_kv(
                            "RAG.JOIN.HOP1.LOOKUP_FILTER.PEOPLE_MISSING",
                            level="warning",
                            reason="people_filter_unavailable",
                            people_terms=final_people_terms[:4],
                            people_ids=people_ids[:4],
                            match_mode=people_match_mode,
                            min_should=people_min_should,
                        )
                hop1_lookup_filter = None
                if hop1_col == COL_PROJECT:
                    if join_people_filter or participant_org_filter or org_filter:
                        hop1_lookup_filter = _and_filter(
                            hop1_lookup_filter,
                            _build_tag_only_filter([TAG_PJT_INFO]),
                        )
                    if join_people_filter:
                        hop1_lookup_filter = _and_filter(hop1_lookup_filter, join_people_filter)
                    if participant_org_filter or org_filter:
                        hop1_lookup_filter = _and_filter(
                            hop1_lookup_filter,
                            participant_org_filter or org_filter,
                        )
                    if project_tag_filter:
                        hop1_lookup_filter = _and_filter(hop1_lookup_filter, project_tag_filter)
                elif hop1_col == COL_PERF:
                    if base_route == "perf" and join_people_filter:
                        hop1_lookup_filter = _and_filter(hop1_lookup_filter, join_people_filter)
                    if perf_tag_filter:
                        hop1_lookup_filter = _and_filter(hop1_lookup_filter, perf_tag_filter)
                if hop1_lookup_filter is not None:
                    hop1_filter = _and_filter(hop1_filter, hop1_lookup_filter)
            if hop1_col in (COL_PROJECT, COL_PERF):
                if year_range_filter:
                    hop1_filter = _and_filter(hop1_filter, year_range_filter)
            if hop1_col == COL_PERF and perf_type_filter:
                hop1_filter = _and_filter(hop1_filter, perf_type_filter)

            log_kv(
                "RAG.JOIN.HOP1",
                hop1_col=hop1_col, hop1_kind=hop1_kind, hop1_q=hop1_q,
                hop1_tag_filters=hop1_tag_filters,
                hop1_filter=str(hop1_filter) if hop1_filter is not None else None,
                hop1_k_base=hop1_k_base,
                hop1_keep=hop1_keep,
            )

            vec_avail = _named_vectors_in_collection(qdr, hop1_col)
            use_vecs_h1 = [v for v in vector_names if (not isinstance(vec_avail, set) or v in vec_avail)]
            pre_vecs_h1 = _get_pre_vecs(hop1_q)
            emb_map_h1: Dict[str, Any] = {}
            for vname in use_vecs_h1:
                pe = pre_vecs_h1.get(vname)
                emb_map_h1[vname] = pe if pe is not None else fallback_emb.get(vname)
            emb_map_h1 = {k: v for k, v in emb_map_h1.items() if v is not None}

            local_timings_h1: Dict[str, float] = {}
            sr1 = _call_dense_retrieve_hybrid_multi(
                qdr=qdr,
                emb_map=emb_map_h1,
                qtext=hop1_q,
                kws=kws,
                collection=hop1_col,
                lexical_fields=preset.lexical_fields,
                sparse_vector_name=sparse_vector_name_eff,
                sparse_topk=min(hop1_k_base, 80),
                top_k_dense=topk_dense,
                top_k_lex_cand=hop1_k_base,
                top_k_lex=min(hop1_k_base, 80),
                query_filter=hop1_filter,  # ✅ 실제 적용
                timings_out=local_timings_h1,
                require_hybrid_both_sides=True,
                contract_scope="join_hop1",
                violation_on_contract=True,
            )
            _apply_dense_threshold(
                sr1,
                use_dense_threshold=use_dense_threshold_policy,
                min_dense_score=min_dense_score_policy,
                log_prefix="RAG.DENSE.THRESHOLD.HOP1",
                col=hop1_col,
                action=action,
                base_route=base_route,
                relation=relation,
            )
            hybrid_points = sr1.get("hybrid") or []
            if hybrid_points:
                _ensure_collection_mark(hybrid_points, hop1_col)
                h1_rrf = _dedup_by_doc_id(hybrid_points)
            else:
                _ensure_collection_mark((sr1.get("lexical") or []), hop1_col)
                for _, lst in (sr1.get("dense") or {}).items():
                    _ensure_collection_mark(lst or [], hop1_col)

                # Hop1 RRF merge
                sources_h1: List[_RankSource] = []
                for vname, lst in (sr1.get("dense") or {}).items():
                    base_weight = float(w_dense_map.get(vname, 1.0))
                    score_weight = _dense_score_weight(lst or []) if _use_dense_score_weight() else 1.0
                    sources_h1.append(_RankSource(name=f"{hop1_col}:{vname}", weight=base_weight * score_weight, points=lst or []))
                sources_h1.append(_RankSource(name=f"{hop1_col}:lex", weight=float(sparse_weight_eff), points=sr1.get("lexical") or []))
                h1_rrf = _rrf_merge(sources_h1, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=500)
                h1_rrf = _dedup_by_doc_id(h1_rrf)
            hop1_reranked = _final_rerank(
                h1_rrf,
                it=it,
                kws=kws,
                lex_w=lex_w_eff,
                base_route=("perf" if hop1_kind == "perf" else base_route),
                mode="search",
                keep=int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")),
                tag_boost=float(getattr(preset, "tag_boost", 0.2)),
                tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
            )

            if len(hop1_reranked) > ctx_hard_limit:
                hop1_reranked = hop1_reranked[:ctx_hard_limit]

            hop1_top = hop1_reranked[: max(1, hop1_keep)]
            if not hop1_top:
                log_kv(
                    "RAG.JOIN.HOP1.EMPTY",
                    hop1_kind=hop1_kind,
                    hop1_tag_filters=hop1_tag_filters,
                    hop1_filter=str(hop1_filter) if hop1_filter is not None else None,
                )
            else:
                # ✅ hop1 결과에 meta_basic 포함 payload 보강
                hydrate_keep = min(
                    max(hop1_keep, int(os.getenv("RAG_HOP1_FINAL_KEEP", "40")), 20),
                    ctx_hard_limit,
                )
                _hydrate_points_payload(qdr, hop1_reranked[:hydrate_keep])
                missing = _count_missing_join_keys(hop1_top)
                if missing.get("missing_pjt_any") or missing.get("missing_tag"):
                    if str(os.getenv("RAG_HOP1_REHYDRATE_ON_MISSING_KEYS", "1")).strip().lower() in ("1", "true", "yes", "y"):
                        _hydrate_points_payload(qdr, hop1_top)
                    _raise_on_missing_join_keys(hop1_top, scope=f"join_hop1:{hop1_col}")

            if join_key_mode == "group":
                join_pjt_nos = _extract_pjt_nos(hop1_top[:hop1_keep], max_ids=hop1_keep)
            else:
                join_pjt_ids = _extract_pjt_ids(hop1_top[:hop1_keep], max_ids=hop1_keep)

            log_top_points("RAG.JOIN.HOP1.TOP", hop1_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP1", "6")))
            if join_key_mode == "group":
                log_section("RAG.JOIN.JOIN_PJT_NOS", join_pjt_nos[: min(len(join_pjt_nos), 30)])
            else:
                log_section("RAG.JOIN.JOIN_PJT_IDS", join_pjt_ids[: min(len(join_pjt_ids), 30)])
            log_kv(
                "RAG.JOIN.HOP1.TIMINGS",
                dense_queries=float(local_timings_h1.get("dense_queries", 0.0)),
                sparse_hits=float(local_timings_h1.get("lexical_scored", 0.0)),
                hybrid_once_hits=float(local_timings_h1.get("hybrid_once_hits", 0.0)),
                **{k: float(v) for k, v in (local_timings_h1 or {}).items()}
            )
            _validate_lookup_join_hybrid_metrics(
                mode="join",
                contract_scope=f"join_hop1:{hop1_col}",
                timings=local_timings_h1,
            )

            # Hop1 context
            hop1_ctx, hop1_refs = ("", [])
            if hop1_top:
                hop1_ctx, hop1_refs, _ = _build_context_with_output_type(
                    hop1_top[: max(1, hop1_keep)],
                    action=action,
                    base_route=hop1_kind,
                    output_type=plan.output_type,
                    max_items=max(1, hop1_keep),
                    query_text=hop1_q,
                )

            # mode=join 계약: join key가 없으면 Hop1-only 성공 반환 없이 명시적 실패로 종료
            has_join_keys = bool(join_pjt_nos) if join_key_mode == "group" else bool(join_pjt_ids)
            _ensure_join_mode_has_keys(
                has_join_keys=has_join_keys,
                join_key_mode=join_key_mode,
                hop1_top=hop1_top,
                hop1_col=hop1_col,
            )

            # 2) Hop2 (LOOKUP/JOIN): JOIN 필터로 강제 제한
            planner_join_key_mode = str(join_key_mode or "").strip().lower() or None
            executed_join_key_mode = "group" if join_pjt_nos else "instance"
            log_kv(
                "RAG.JOIN.KEY_MODE.CHECK",
                planner_join_key_mode=planner_join_key_mode,
                executed_join_key_mode=executed_join_key_mode,
                join_pjt_ids_count=len(join_pjt_ids),
                join_pjt_nos_count=len(join_pjt_nos),
                relation=relation,
                hop2_col=hop2_col,
            )
            if planner_join_key_mode != executed_join_key_mode:
                raise StrategyViolation(
                    error_code="PLANNER_JOIN_KEY_MODE_EXECUTION_MISMATCH",
                    reason=(
                        "planner join_key_mode와 실행 join filter key가 불일치"
                        f"(planner={planner_join_key_mode}, executed={executed_join_key_mode}, relation={relation})"
                    ),
                )

            validate_join_mode_key_inputs(
                mode=planner_join_key_mode,
                join_ids=join_pjt_ids,
                pjt_nos=join_pjt_nos,
            )

            hop2_filter = _build_join_hop2_filter(
                relation=relation,
                join_key_mode=planner_join_key_mode,
                join_pjt_ids=join_pjt_ids,
                join_pjt_nos=join_pjt_nos,
                join_ids=join_ids,
                q=q,
                hop2_tag_filters=hop2_tag_filters,
                people_terms=people_terms,
                org_terms=org_terms,
                planner_filter_spec=planner_filter_spec,
            )
            if relation not in (("project", "perf"), ("people", "perf"), ("org", "perf")) and hop2_kind in ("project", "org") and org_filter:
                hop2_filter = _and_filter(hop2_filter, org_filter)
            hop2_filter = _with_org_must_gate(hop2_filter, col=hop2_col, mode_override="join")
            if hop2_col in (COL_PROJECT, COL_PERF):
                if year_range_filter:
                    hop2_filter = _and_filter(hop2_filter, year_range_filter)
            if hop2_col == COL_PERF and perf_type_filter:
                hop2_filter = _and_filter(hop2_filter, perf_type_filter)


            log_kv(
                "RAG.JOIN.HOP2",
                hop2_col=hop2_col, hop2_kind=hop2_kind, hop2_q=hop2_q,
                hop2_tag_filters=hop2_tag_filters,
                hop2_filter=str(hop2_filter) if hop2_filter is not None else None,
                hop2_k_base=hop2_k_base,
                hop2_keep=hop2_keep,
                join_pjt_ids_preview=join_pjt_ids[:10],
                join_pjt_nos_preview=join_pjt_nos[:10],
            )

            vec_avail2 = _named_vectors_in_collection(qdr, hop2_col)
            use_vecs_h2 = [v for v in vector_names if (not isinstance(vec_avail2, set) or v in vec_avail2)]
            pre_vecs_h2 = _get_pre_vecs(hop2_q)
            emb_map_h2: Dict[str, Any] = {}
            for vname in use_vecs_h2:
                pe = pre_vecs_h2.get(vname)
                emb_map_h2[vname] = pe if pe is not None else fallback_emb.get(vname)
            emb_map_h2 = {k: v for k, v in emb_map_h2.items() if v is not None}

            local_timings_h2: Dict[str, float] = {}
            sr2 = _call_dense_retrieve_hybrid_multi(
                qdr=qdr,
                emb_map=emb_map_h2,
                qtext=hop2_q,
                kws=kws,
                collection=hop2_col,
                lexical_fields=preset.lexical_fields,
                sparse_vector_name=sparse_vector_name_eff,
                sparse_topk=min(hop2_k_base, 120),
                top_k_dense=topk_dense,
                top_k_lex_cand=hop2_k_base,
                top_k_lex=min(hop2_k_base, 120),
                query_filter=hop2_filter,  # ✅ JOIN 필터 강제 적용
                timings_out=local_timings_h2,
                require_hybrid_both_sides=True,
                contract_scope="join_hop2",
                violation_on_contract=True,
            )
            _apply_dense_threshold(
                sr2,
                use_dense_threshold=use_dense_threshold_policy,
                min_dense_score=min_dense_score_policy,
                log_prefix="RAG.DENSE.THRESHOLD.HOP2",
                col=hop2_col,
                action=action,
                base_route=base_route,
                relation=relation,
            )
            hybrid_points = sr2.get("hybrid") or []
            if hybrid_points:
                _ensure_collection_mark(hybrid_points, hop2_col)
                h2_rrf = _dedup_by_doc_id(hybrid_points)
            else:
                _ensure_collection_mark((sr2.get("lexical") or []), hop2_col)
                for _, lst in (sr2.get("dense") or {}).items():
                    _ensure_collection_mark(lst or [], hop2_col)

                # Hop2 RRF + JOIN 최종 rerank (mode="join")
                sources_h2: List[_RankSource] = []
                for vname, lst in (sr2.get("dense") or {}).items():
                    base_weight = float(w_dense_map.get(vname, 1.0))
                    score_weight = _dense_score_weight(lst or []) if _use_dense_score_weight() else 1.0
                    sources_h2.append(_RankSource(name=f"{hop2_col}:{vname}", weight=base_weight * score_weight, points=lst or []))
                sources_h2.append(_RankSource(name=f"{hop2_col}:lex", weight=float(sparse_weight_eff), points=sr2.get("lexical") or []))
                h2_rrf = _rrf_merge(sources_h2, rrf_k=int(os.getenv("RAG_RRF_K", "60")), keep=800)
                h2_rrf = _dedup_by_doc_id(h2_rrf)

            hop2_reranked = _final_rerank(
                h2_rrf,
                it=it,
                kws=kws,
                lex_w=lex_w_eff,
                base_route=("perf" if hop2_kind == "perf" else base_route),
                mode="join",
                keep=int(os.getenv("RAG_HOP2_FINAL_KEEP", "80")),
                tag_boost=float(getattr(preset, "tag_boost", 0.0)),
                tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
            )
            hop2_reranked = _dedup_by_doc_id(hop2_reranked)
            if len(hop2_reranked) > ctx_hard_limit:
                hop2_reranked = hop2_reranked[:ctx_hard_limit]
            hop2_top = hop2_reranked[: max(1, hop2_keep)]

            log_top_points("RAG.JOIN.HOP2.TOP", hop2_top, topn=int(os.getenv("RAG_LOG_TOPN_HOP2", "8")))
            log_kv(
                "RAG.JOIN.HOP2.TIMINGS",
                dense_queries=float(local_timings_h2.get("dense_queries", 0.0)),
                sparse_hits=float(local_timings_h2.get("lexical_scored", 0.0)),
                hybrid_once_hits=float(local_timings_h2.get("hybrid_once_hits", 0.0)),
                **{k: float(v) for k, v in (local_timings_h2 or {}).items()}
            )
            _validate_lookup_join_hybrid_metrics(
                mode="join",
                contract_scope=f"join_hop2:{hop2_col}",
                timings=local_timings_h2,
            )

            # Hop2 context
            hop2_ctx, hop2_refs, _ = _build_context_with_output_type(
                hop2_top,
                action=action,
                base_route=hop2_kind,
                output_type=plan.output_type,
                max_items=max(1, hop2_keep),
                query_text=hop2_q,
            )

            join_key_label = "PJT_NO" if join_key_mode == "group" else "PJT_ID"
            join_key_preview = join_pjt_nos[:10] if join_key_mode == "group" else join_pjt_ids[:10]
            context = (
                f"### [Hop1] 검색 결과 요약\n{hop1_ctx or '(후보 없음)'}\n\n"
                f"### [Hop2] {hop2_label}\n"
                f"- 필터 {join_key_label} 후보: {', '.join(join_key_preview)}\n\n"
                f"{hop2_ctx or '(후보 없음)'}"
            )
            t0 = time.time()
            # 검색단계에서 최소 페아로드 -> server3.py 에는 전체 페이로드를 전달하기 위해 선정된 정보들 페이로드 채우기
            hydrate_limit = min(max(1, hop2_keep), ctx_hard_limit)
            hydrate_points = hop2_reranked[:hydrate_limit]
            _hydrate_points_payload(
                qdr,
                hydrate_points,  # len == final_keep
                chunk_size=int(os.getenv("RAG_HYDRATE_FULL_CHUNK", "64")),
            )
            _timing_put(timings, "phase.hydrate_full_payload", time.time() - t0)
            refs = (hop1_refs or []) + (hop2_refs or [])
            _timing_put(timings, "phase.hop_total", time.time() - t_hop0)
            _timing_put(timings, "phase.total", time.time() - t_all0)
            hits = (hop1_top or []) + (hop2_top or [])
            return RagResult(
                stack=stack,
                keywords=kws,
                hits=hits,
                reranked_hits=hop2_top,
                context=context,
                refs=refs,
                timings=timings,
            )

    # -------------------------
    # Base (SEARCH / LOOKUP) federated
    # -------------------------
    t0 = time.time()

    # collection list
    target_cols = list(target_collections or _default_target_collections())
    perf_followup_join_ids = _maybe_followup_perf_hop_from_project()
    perf_followup_filter = (
        build_perf_filter_by_pjt_id(perf_followup_join_ids, q)
        if perf_followup_join_ids
        else None
    )

    def _expand_vector_names(names: List[str]) -> List[str]:
        expanded: List[str] = []
        for name in names:
            if name not in expanded:
                expanded.append(name)
            if name.endswith("_qa"):
                base = name[:-3]
                if base and base not in expanded:
                    expanded.append(base)
        return expanded

    def _build_emb_map_for_collection(col: str) -> Dict[str, Any]:
        vec_avail = _named_vectors_in_collection(qdr, col)
        expanded_names = _expand_vector_names(vector_names)
        use_vecs = [v for v in expanded_names if (not isinstance(vec_avail, set) or v in vec_avail)]
        if isinstance(vec_avail, set) and not use_vecs and vector_names:
            logger.warning(
                "[RAG] no matching vectors for col=%s available=%s requested=%s expanded=%s",
                col,
                sorted(vec_avail),
                vector_names,
                expanded_names,
            )
        out: Dict[str, Any] = {}
        for vname in use_vecs:
            pe = pre_vecs.get(vname)
            out[vname] = pe if pe is not None else fallback_emb.get(vname)
        return {k: v for k, v in out.items() if v is not None}

    # server-side filter policy (LOOKUP에서만 적극 적용)
    def _server_filter_for_col(col: str) -> Any:
        def _safe_exclusion_only(filter_obj: Any) -> Any:
            if qmodels is None or filter_obj is None:
                return None
            must_not = list(getattr(filter_obj, "must_not", None) or [])
            if not must_not:
                return None
            return qmodels.Filter(must_not=must_not)

        def _relation_lookup_filter_for_col(apply_name_filters: bool) -> Any:
            if not relation_lookup_enforce or not relation:
                return None
            route = get_relation_route(relation)
            if route is None:
                return None
            if col == route.hop1_col:
                tag_filters_local = route.hop1_tag_filters
            elif col == route.hop2_col:
                tag_filters_local = route.hop2_tag_filters
            else:
                return None
            base_filter = _build_tag_only_filter(tag_filters_local) if tag_filters_local else None
            relation_parts = set(route.relation)
            if col == COL_PROJECT:
                if "people" in relation_parts and people_filter and apply_name_filters:
                    base_filter = _and_filter(base_filter, people_filter)
                if "org" in relation_parts and (participant_org_filter or org_filter) and apply_name_filters:
                    base_filter = _and_filter(base_filter, participant_org_filter or org_filter)
            return base_filter

        def _build_soft_filter_for_col(col_name: str, apply_name_filters: bool) -> Any:
            base_filter = None
            if col_name == COL_PROJECT:
                if title_filter and (mode == "search" and search_filter_conf_ok):
                    base_filter = _and_filter(base_filter, title_filter)
                if apply_name_filters and (people_filter or participant_org_filter or org_filter):
                    tag_filter_local = _build_tag_only_filter([TAG_PJT_INFO])
                    base_filter = _and_filter(base_filter, tag_filter_local)
                if apply_name_filters and people_filter:
                    base_filter = _and_filter(base_filter, people_filter)
                if apply_name_filters and (participant_org_filter or org_filter):
                    base_filter = _and_filter(base_filter, participant_org_filter or org_filter)
                if project_tag_filter:
                    base_filter = _and_filter(base_filter, project_tag_filter)
            elif col_name == COL_PERF:
                if title_filter and (mode == "search" and search_filter_conf_ok):
                    base_filter = _and_filter(base_filter, title_filter)
                if base_route == "perf" and people_filter and apply_name_filters:
                    base_filter = _and_filter(base_filter, people_filter)
                if perf_tag_filter:
                    base_filter = _and_filter(base_filter, perf_tag_filter)
            return base_filter

        def _apply_extra_filters(base_filter: Any) -> Any:
            combined = _and_filter(relation_filter, base_filter) if relation_filter else base_filter
            if col in (COL_PROJECT, COL_PERF):
                if year_range_filter:
                    combined = _and_filter(combined, year_range_filter)
            if col == COL_PERF and perf_type_filter:
                combined = _and_filter(combined, perf_type_filter)
            if col == COL_PERF and perf_followup_filter:
                combined = _and_filter(combined, perf_followup_filter) if combined else perf_followup_filter
            return combined

        ids_map = getattr(it, "ids_map", {}) or {}
        raw_pjt_ids = [str(x).strip() for x in (ids_map.get("pjt_id") or []) if str(x).strip()]
        raw_pjt_nos = [str(x).strip() for x in (ids_map.get("pjt_no") or []) if str(x).strip()]

        # LOOKUP 서버 필터 단계에서는 project key 타입을 반드시 단일화한다.
        if raw_pjt_ids and raw_pjt_nos:
            raise StrategyViolation(
                error_code="PLANNER_MIXED_PROJECT_KEYS",
                reason="lookup ids_map.pjt_id/pjt_no 혼합 입력은 허용되지 않음",
            )
        project_key_filter_type = "pjt_id" if raw_pjt_ids else ("pjt_no" if raw_pjt_nos else None)
        pjt_ids = raw_pjt_ids if project_key_filter_type == "pjt_id" else []
        pjt_nos = raw_pjt_nos if project_key_filter_type == "pjt_no" else []

        perf_id_keys = (
            "doi",
            "issn",
            "eissn",
            "pissn",
            "patent_reg_no",
            "patent_app_no",
            "paper_id",
            "perf_id",
            "rst_id",
        )
        has_perf_ids = any(ids_map.get(key) for key in perf_id_keys)
        lookup_has_ids = bool(pjt_ids or pjt_nos or has_perf_ids)
        lookup_has_name_filters = bool(people_terms or people_ids or org_terms)
        apply_name_filters = mode != "lookup" or lookup_has_ids or lookup_has_name_filters

        relation_filter = _relation_lookup_filter_for_col(apply_name_filters)

        if mode != "lookup":
            if mode == "search" and search_filter_server_policy == "must_not_only":
                return _safe_exclusion_only(_with_org_must_gate(_apply_extra_filters(None), col=col))
            return None

        base_filter_lookup = _build_soft_filter_for_col(col, apply_name_filters) if lookup_filter_enabled else None

        if relation and not (pjt_ids or pjt_nos or has_perf_ids):
            log_kv(
                "RAG.LOOKUP.IDS_MAP.EMPTY",
                build_project_id_filter="skip",
                ids_map=ids_map,
                pjt_id=pjt_ids,
                pjt_no=pjt_nos,
                relation=relation,
                perf_ids=has_perf_ids,
                project_key_filter_type=project_key_filter_type,
            )
        pjt_filter = build_project_id_filter(pjt_ids, pjt_nos)
        if pjt_filter is not None:
            log_kv(
                "RAG.LOOKUP.PROJECT_KEY_FILTER",
                project_key_filter_type=project_key_filter_type,
                pjt_id_count=len(pjt_ids),
                pjt_no_count=len(pjt_nos),
            )
            # PJT_ID/PJT_NO는 project/perf 모두 join 키로 쓰이니 tag 과제 제한은 하지 말고 먼저 강제
            combined = _and_filter(pjt_filter, base_filter_lookup) if base_filter_lookup else pjt_filter
            return _apply_extra_filters(_with_org_must_gate(combined, col=col))

        # (선택) perf_tag_filters가 있으면 perf 컬렉션에서만 tag_filter
        if col == COL_PERF and perf_tag_filter:
            combined = _and_filter(perf_tag_filter, base_filter_lookup) if base_filter_lookup else perf_tag_filter
            return _apply_extra_filters(_with_org_must_gate(combined, col=col))

        # (선택) org_filter는 project 컬렉션에서만
        if col == COL_PROJECT and relation == ("people", "project") and people_filter and lookup_has_ids:
            tag_filter_local = _build_tag_only_filter([TAG_PJT_INFO])
            combined = _and_filter(_and_filter(tag_filter_local, people_filter), base_filter_lookup) if base_filter_lookup else _and_filter(tag_filter_local, people_filter)
            return _apply_extra_filters(_with_org_must_gate(combined, col=col))

        if col == COL_PROJECT and org_terms and base_route not in ("project", "org", "people") and lookup_has_ids:
            if org_role == "participant":
                combined = _and_filter(participant_org_filter or org_filter, base_filter_lookup) if base_filter_lookup else (participant_org_filter or org_filter)
                return _apply_extra_filters(_with_org_must_gate(combined, col=col))
            if org_filter:
                combined = _and_filter(org_filter, base_filter_lookup) if base_filter_lookup else org_filter
                return _apply_extra_filters(_with_org_must_gate(combined, col=col))

        # base_route가 명확하면 tag로 1차 후보 노이즈를 줄임 (lookup에서만)
        if col == COL_PROJECT:
            if base_route == "people":
                tag_filter_local = _build_tag_only_filter([TAG_PJT_INFO])
                base_filter = _and_filter(tag_filter_local, people_filter) if (people_filter and lookup_has_ids) else tag_filter_local
                return _apply_extra_filters(_with_org_must_gate(base_filter, col=col))
            if base_route == "org":
                tag_filter_local = _build_tag_only_filter([TAG_PJT_INFO])
                base_filter = (
                    _and_filter(tag_filter_local, participant_org_filter or org_filter)
                    if (participant_org_filter or org_filter)
                    else tag_filter_local
                )
                return _apply_extra_filters(_with_org_must_gate(base_filter, col=col))
            if base_route == "project":
                # 프로젝트 목록/상세 조회면 INFO로 제한
                tag_filter_local = _build_tag_only_filter([TAG_PJT_INFO])
                combined_filter = tag_filter_local
                if people_filter and (lookup_has_ids or planner_org_filter_present):
                    combined_filter = _and_filter(combined_filter, people_filter)
                if (participant_org_filter or org_filter):
                    combined_filter = _and_filter(combined_filter, participant_org_filter or org_filter)
                combined_filter = _and_filter(combined_filter, base_filter_lookup) if base_filter_lookup else combined_filter
                return _apply_extra_filters(_with_org_must_gate(combined_filter, col=col))

        if col == COL_PERF and base_route == "perf":
            combined_filter = base_filter_lookup
            if people_filter and (lookup_has_ids or planner_org_filter_present):
                combined_filter = _and_filter(combined_filter, people_filter) if combined_filter else people_filter
            if perf_tag_filter:
                combined_filter = _and_filter(combined_filter, perf_tag_filter) if combined_filter else perf_tag_filter
            if combined_filter is not None:
                return _apply_extra_filters(_with_org_must_gate(combined_filter, col=col))
        return _apply_extra_filters(_with_org_must_gate(base_filter_lookup, col=col))


    # retrieve each collection
    sr_by_col: Dict[str, Dict[str, Any]] = {}
    per_col_stats: Dict[str, Dict[str, float]] = {}

    for col in target_cols:
        emb_map_col = _build_emb_map_for_collection(col)
        use_dense_k = topk_dense if emb_map_col else 0

        qfilter = _server_filter_for_col(col)

        log_kv(
            "RAG.COL.RETRIEVE",
            col=col,
            mode=plan.mode,
            search_filter_enabled=search_filter_enabled,
            search_filter_signal=search_filter_signal,
            search_filter_conf_ok=search_filter_conf_ok,
            use_dense_k=use_dense_k,
            topk_lex_cand=topk_lex_cand,
            topk_lex=topk_lex,
            sparse_vector_name=sparse_vector_name_eff,
            sparse_topk=int(sparse_topk_eff),
            sparse_weight=float(sparse_weight_eff),
            qfilter=str(qfilter) if qfilter is not None else None,
            lex_w_preview={k: float(lex_w_eff.get(k)) for k in list(lex_w_eff.keys())[:8]},
            dense_vecs=list(emb_map_col.keys()),
        )

        local_timings: Dict[str, float] = {}
        sr = _call_dense_retrieve_hybrid_multi(
            qdr=qdr,
            emb_map=emb_map_col,
            qtext=q,
            kws=kws,
            collection=col,
            lexical_fields=preset.lexical_fields,
            sparse_vector_name=sparse_vector_name_eff,
            sparse_topk=sparse_topk_eff,
            top_k_dense=topk_dense if plan.mode == "lookup" else use_dense_k,
            top_k_lex_cand=topk_lex_cand,
            top_k_lex=topk_lex,
            query_filter=qfilter,  # ✅ plan 기반 적용
            timings_out=local_timings,
            require_hybrid_both_sides=(plan.mode == "lookup"),
            contract_scope=f"{plan.mode}:{col}",
            violation_on_contract=(plan.mode == "lookup"),
        )
        if plan.mode in ("lookup", "join"):
            _validate_lookup_join_hybrid_metrics(
                mode=plan.mode,
                contract_scope=f"{plan.mode}:{col}",
                timings=local_timings,
            )

        _apply_dense_threshold(
            sr,
            use_dense_threshold=use_dense_threshold_policy,
            min_dense_score=min_dense_score_policy,
            log_prefix="RAG.DENSE.THRESHOLD.COL",
            col=col,
            action=action,
            base_route=base_route,
            relation=relation,
        )

        hybrid_points = sr.get("hybrid") or []
        if hybrid_points:
            _ensure_collection_mark(hybrid_points, col)
        else:
            _ensure_collection_mark((sr.get("lexical") or []), col)
            for _, lst in (sr.get("dense") or {}).items():
                _ensure_collection_mark(lst or [], col)

        sr_by_col[col] = sr

        dense_vec_stats: Dict[str, Dict[str, float]] = {}
        if not hybrid_points:
            for vname, lst in (sr.get("dense") or {}).items():
                if not lst:
                    continue
                top_score = None
                try:
                    top_score = float(getattr(lst[0], "score", 0.0))
                except Exception:
                    top_score = None
                dense_vec_stats[str(vname)] = {
                    "hits": float(len(lst)),
                    "top_score": float(top_score) if top_score is not None else -1.0,
                }

            lex_top_score = None
            if sr.get("lexical"):
                try:
                    lex_top_score = float(getattr(sr["lexical"][0], "score", 0.0))
                except Exception:
                    lex_top_score = None

            log_section(
                "RAG.COL.RESULTS",
                {
                    "col": col,
                    "dense": dense_vec_stats,
                    "lexical": {
                        "hits": float(len(sr.get("lexical") or [])),
                        "top_score": float(lex_top_score) if lex_top_score is not None else -1.0,
                    },
                },
            )
        else:
            top_score = None
            try:
                top_score = float(getattr(hybrid_points[0], "score", 0.0))
            except Exception:
                top_score = None
            log_section(
                "RAG.COL.RESULTS",
                {
                    "col": col,
                    "hybrid": {
                        "hits": float(len(hybrid_points)),
                        "top_score": float(top_score) if top_score is not None else -1.0,
                    },
                },
            )

        dense_topn = int(os.getenv("RAG_LOG_TOPN_COL_DENSE", "4"))
        if not hybrid_points:
            for vname, lst in (sr.get("dense") or {}).items():
                log_top_points(f"RAG.COL.DENSE.TOP.{col}.{vname}", lst or [], topn=dense_topn)

        lex_topn = int(os.getenv("RAG_LOG_TOPN_COL_LEX", "4"))
        if not hybrid_points:
            log_top_points(f"RAG.COL.LEX.TOP.{col}", sr.get("lexical") or [], topn=lex_topn)
        else:
            log_top_points(f"RAG.COL.HYBRID.TOP.{col}", hybrid_points, topn=lex_topn)

        # stats
        if not hybrid_points:
            d_hit = sum(len(lst or []) for lst in (sr.get("dense") or {}).values())
            l_hit = len(sr.get("lexical") or [])
            best_dense = None
            for lst in (sr.get("dense") or {}).values():
                if lst:
                    s0 = getattr(lst[0], "score", None)
                    try:
                        best_dense = max(best_dense or -1.0, float(s0) if s0 is not None else -1.0)
                    except Exception:
                        pass

            per_col_stats[col] = {
                "dense_hits": float(d_hit),
                "lex_hits": float(l_hit),
                "dense_queries": float(local_timings.get("dense_queries", 0.0)),
                "sparse_hits": float(local_timings.get("lexical_scored", 0.0)),
                "hybrid_once_hits": float(local_timings.get("hybrid_once_hits", 0.0)),
                "best_dense": float(best_dense) if best_dense is not None else -1.0,
                "total": float(local_timings.get("total", 0.0)),
            }

            log_kv(
                "RAG.COL.STATS",
                col=col,
                dense_hits=int(d_hit),
                lex_hits=int(l_hit),
                best_dense=float(best_dense) if best_dense is not None else -1.0,
                timings=local_timings,
            )
            _record_col_timings(
                timings,
                col,
                stats=per_col_stats[col],
                local_timings=local_timings,
            )
        else:
            per_col_stats[col] = {
                "hybrid_hits": float(len(hybrid_points)),
                "dense_queries": float(local_timings.get("dense_queries", 0.0)),
                "sparse_hits": float(local_timings.get("lexical_scored", 0.0)),
                "hybrid_once_hits": float(local_timings.get("hybrid_once_hits", 0.0)),
                "total": float(local_timings.get("total", 0.0)),
            }
            log_kv(
                "RAG.COL.STATS",
                col=col,
                hybrid_hits=int(len(hybrid_points)),
                timings=local_timings,
            )
            _record_col_timings(
                timings,
                col,
                stats=per_col_stats[col],
                local_timings=local_timings,
            )

    _timing_put(timings, "phase.dense_search", time.time() - t0)

    # federated RRF merge sources
    sources: List[_RankSource] = []
    for col, sr in sr_by_col.items():
        hybrid_points = sr.get("hybrid") or []
        if hybrid_points:
            sources.append(_RankSource(name=f"{col}:hybrid", weight=1.0, points=hybrid_points))
            continue
        for vname, lst in (sr.get("dense") or {}).items():
            base_weight = float(w_dense_map.get(vname, 1.0))
            score_weight = _dense_score_weight(lst or []) if _use_dense_score_weight() else 1.0
            sources.append(_RankSource(name=f"{col}:{vname}", weight=base_weight * score_weight, points=lst or []))
        sources.append(_RankSource(name=f"{col}:lex", weight=float(sparse_weight_eff), points=sr.get("lexical") or []))

    log_section("RAG.PER_COL_STATS", per_col_stats)

    t0 = time.time()
    merged_rrf = _rrf_merge(
        sources,
        rrf_k=int(os.getenv("RAG_RRF_K", "60")),
        keep=int(os.getenv("RAG_MERGED_KEEP", "1200")),
    )
    merged_rrf = _dedup_by_doc_id(merged_rrf)
    _timing_put(timings, "phase.rrf_merge", time.time() - t0)

    log_top_points("RAG.MERGED_RRF.TOP", merged_rrf, topn=int(os.getenv("RAG_LOG_TOPN_MERGED", "10")))

    # promotion 비활성 기본값(disable): 명시적으로 켠 경우에만 동작 가능
    promotion_mode = mode
    promotion_intent = it
    promotion_feature_mode = str(os.getenv("RAG_PROMOTION_MODE", "disable") or "disable").strip().lower()
    promotion_enabled = promotion_feature_mode in ("enable", "enabled", "on", "1", "true", "yes", "y")
    if promotion_enabled:
        # 기본 정책은 비활성. 켜져도 현재는 passthrough 동작만 수행한다.
        log_kv(
            "RAG.PROMOTION.DISABLED_POLICY",
            promotion_feature_mode=promotion_feature_mode,
            planner_mode=planner_mode,
            executed_mode=promotion_mode,
            reason="promotion_policy_passthrough",
        )

    # final rerank
    t0 = time.time()
    final_keep = int((rerank_spec or {}).get("final_keep", 80))
    reranked = _final_rerank(
        merged_rrf,
        it=promotion_intent,
        kws=kws,
        lex_w=lex_w_eff,
        base_route=base_route,
        mode=promotion_mode,
        keep=final_keep,
        tag_boost=float(getattr(preset, "tag_boost", 0.0)),
        tag_mismatch_penalty=float(getattr(preset, "tag_mismatch_penalty", 0.0)),
    )
    reranked = _dedup_by_doc_id(reranked)
    if len(reranked) > ctx_hard_limit:
        reranked = reranked[:ctx_hard_limit]
    _timing_put(timings, "phase.final_rerank", time.time() - t0)

    log_top_points("RAG.FINAL_RERANK.TOP", reranked, topn=int(os.getenv("RAG_LOG_TOPN_FINAL", "10")))

    # contract policy (NTIS_RAG_Search_Strategy_v1_1.md 계약: 검색 실패 시 chat fallback 없음)
    min_ctx_items = max(1, min(2, int(os.getenv("RAG_MIN_CTX_ITEMS", "2"))))
    min_reranked = max(0, int(getattr(preset, "min_reranked", 0) or 0))
    min_final_avg = float(os.getenv("RAG_FALLBACK_MIN_FINAL_AVG", "0"))
    min_final_max = float(os.getenv("RAG_FALLBACK_MIN_FINAL_MAX", "0"))
    score_topn = max(1, int(os.getenv("RAG_FALLBACK_SCORE_TOPN", "5")))

    contract_fail_reason = _enforce_reranked_contract(
        reranked=reranked,
        min_reranked=min_reranked,
        min_final_avg=min_final_avg,
        min_final_max=min_final_max,
        score_topn=score_topn,
        timing_put=lambda key, value: _timing_put(timings, key, value),
    )

    # ✅ 최종 컨텍스트에 들어갈 애들만 payload를 두껍게 채움
    if reranked:

        max_items = min(ctx_hard_limit, max(min_ctx_items, int(preset.max_ctx_items)))
        requested_limit = max(
            _coerce_int(_get_attr(intent_payload, "limit", 0), 0),
            _coerce_int(_get_attr(hint, "limit", 0), 0),
        )
        hydrate_upper = min(ctx_hard_limit, max(max_items, requested_limit, 1))
        reranked_for_hydrate = reranked[:hydrate_upper]
        t0 = time.time()
        _hydrate_points_payload(qdr, reranked_for_hydrate)
        _timing_put(timings, "phase.hydrate_full_payload", time.time() - t0)

        check_top_k = min(len(reranked), max(1, requested_limit or max_items))
        missing_kor = []
        for rank, p in enumerate(reranked[:check_top_k], start=1):
            pl = getattr(p, "payload", {}) or {}
            meta_basic = pl.get("meta_basic") or {}
            if not meta_basic.get("kor_pjt_nm"):
                missing_kor.append(rank)
        logger.info(
            "[RAG.HYDRATE_CHECK] top_k=%s missing_meta_basic_kor_pjt_nm=%s",
            check_top_k,
            missing_kor or "none",
        )

        # 공통 필터 적용 검증(관측용): LOOKUP/JOIN에서 인명 하드 필터가 걸렸는데 topN에 0건이면 경고
        probe_terms = [str(t).strip() for t in (force_people_terms or []) if str(t).strip()]
        if not probe_terms and mode in ("lookup", "join"):
            if bool(people_terms) and not bool(people_ids):
                probe_terms = [str(t).strip() for t in (people_terms or []) if str(t).strip()][:1]
        if probe_terms and mode in ("lookup", "join"):
            inspect_topn = min(max(1, int(os.getenv("RAG_FILTER_PROBE_TOPN", "10"))), len(reranked))
            matched = 0
            for p in reranked[:inspect_topn]:
                payload = getattr(p, "payload", None) or {}
                names_raw = _payload_get(payload, "prtcp_mp[].hm_nm")
                if isinstance(names_raw, list):
                    names = [str(x).strip() for x in names_raw if str(x).strip()]
                else:
                    names = [str(names_raw).strip()] if str(names_raw).strip() else []
                if any(term in names for term in probe_terms):
                    matched += 1
            if matched == 0:
                log_kv(
                    "FILTER_MISS_SUSPECTED",
                    level="warning",
                    mode=mode,
                    filter="participant_researcher_name",
                    values=probe_terms,
                    topN=inspect_topn,
                    matched=matched,
                )

    # build context
    t0 = time.time()
    max_items = min(ctx_hard_limit, max(min_ctx_items, int(preset.max_ctx_items)))
    if reranked:
        reranked_for_ctx = reranked[: max(1, max_items)]
        context, refs, ctx_fieldset = _build_context_with_output_type(
            reranked_for_ctx,
            action=action,
            base_route=base_route,
            output_type=plan.output_type,
            max_items=max_items,
            query_text=q,
        )
    else:
        context = ""
        refs = []
        ctx_fieldset = _resolve_output_fieldset(plan.output_type)

    _timing_put(timings, "phase.build_context", time.time() - t0)
    _timing_put(timings, "phase.total", time.time() - t_all0)

    ctx_max_items = max_items if "max_items" in locals() else min(int(preset.max_ctx_items), ctx_hard_limit)
    kept_ctx = min(len(reranked or []), ctx_max_items)
    discarded_ctx = max(0, len(reranked or []) - kept_ctx)

    log_kv(
        "RAG.CONTEXT",
        reranked_total=len(reranked or []),
        min_ctx_items=min_ctx_items,
        kept_ctx=kept_ctx,
        discarded_ctx=discarded_ctx,
        contract_fail_reason=timings.get("info.contract_fail_reason"),
    )

    log_kv(
        "RAG.CTX",
        ctx_len=len(context or ""),
        refs=len(refs or []),
        max_items=int(ctx_max_items),
        contract_fail_reason=timings.get("info.contract_fail_reason"),
        output_type=plan.output_type,
        fieldset_keys=list(ctx_fieldset or []),
    )

    # merged raw hits (for trace)
    merged_hits: List[Any] = []
    seen: set[Tuple[str, str]] = set()
    for src in sources:
        for h in (src.points or []):
            k = _hit_key(h)
            if k in seen:
                continue
            seen.add(k)
            merged_hits.append(h)

    logger.info(
        f"[PERF][{stack}] mode={plan.mode} base={base_route} action={action} rel={relation} "
        f"kw_det={timings.get('phase.kw_det',0):.4f}s, search={timings.get('phase.dense_search',0):.4f}s, "
        f"rrf={timings.get('phase.rrf_merge',0):.4f}s, rerank={timings.get('phase.final_rerank',0):.4f}s, "
        f"ctx={timings.get('phase.build_context',0):.4f}s, total={timings.get('phase.total',0):.4f}s"
    )

    return RagResult(
        stack=stack,
        keywords=kws,
        hits=merged_hits,
        reranked_hits=reranked,
        context=context,
        refs=refs,
        timings=timings,
    )

# -------------------------
# Public entry
# -------------------------
def run_rag_once(
    query: str,
    model_name: str = DEFAULT_MODEL_NAME,
    hint: Any = None,
    intent_payload: Any = None,
) -> RagResult:
    domain_hint: Optional[str] = None
    vector_names_env = os.getenv("RAG_VECTOR_NAMES", "e5i_qa,e5_qa")
    vector_names = [v.strip() for v in vector_names_env.split(",") if v.strip()]
    w_dense_map = {
        "e5i_qa": 1.0,
        "e5_qa": 0.8,
        "e5i": 1.0,
        "e5": 0.8,
    }
    return _run_rag_with_vectors(
        query=query,
        model_name=model_name,
        hint=hint,
        intent_payload=intent_payload,
        stack="M",
        vector_names=vector_names or ["e5i_qa", "e5_qa"],
        w_dense_map=w_dense_map,
        lexical_field_weights=None,
        domain_hint=domain_hint,
    )

def run_rag_ab_compare(
    query: str,
    model_name: str = DEFAULT_MODEL_NAME,
    hint: Any = None,
    intent_payload: Any = None,
) -> Dict[str, RagResult]:
    res_m = run_rag_once(query=query, model_name=model_name, hint=hint, intent_payload=intent_payload)
    return {"M": res_m}
