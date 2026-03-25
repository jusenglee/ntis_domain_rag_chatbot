# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import os
import logging
from typing import Any, Dict, List, Optional

from rag_parts.search_strategy import build_strategy_key


_PJT_ID_RE = re.compile(r"^\d{8,12}$")
logger = logging.getLogger(__name__)
_PROJECT_KEY_POLICY_LOGGED = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, str(default))).strip().lower()
    if raw in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "off", ""}:
        return False
    return bool(default)


def _allow_legacy_meta_keys() -> bool:
    return _env_bool("RAG_ALLOW_LEGACY_META_KEYS", default=False)


def _log_project_key_policy_once() -> None:
    global _PROJECT_KEY_POLICY_LOGGED
    if _PROJECT_KEY_POLICY_LOGGED:
        return
    _PROJECT_KEY_POLICY_LOGGED = True
    mode = "legacy-enabled" if _allow_legacy_meta_keys() else "top-level-only"
    logger.info("project key policy mode=%s (RAG_ALLOW_LEGACY_META_KEYS)", mode)


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        seq = list(value)
    else:
        seq = [value]
    out: List[str] = []
    seen: set[str] = set()
    for v in seq:
        s = str(v).strip()
        if not s or s.lower() in ("none", "null"):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _payload_get(payload: Dict[str, Any], *keys: str) -> List[str]:
    values: List[str] = []
    for key in keys:
        if "." in key:
            cur: Any = payload
            ok = True
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    ok = False
                    break
                cur = cur.get(part)
            if ok:
                values.extend(_as_list(cur))
        else:
            values.extend(_as_list(payload.get(key)))
    return values


def _extract_ids_from_hits(search_hits: List[Any], *, limit: int = 20) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {
        "pjt_id": [],
        "pjt_no": [],
        "rst_id": [],
        "doi": [],
        "issn": [],
        "patent_reg_no": [],
    }

    def _add(key: str, values: List[str]) -> None:
        for v in values:
            if v not in out[key]:
                out[key].append(v)

    for hit in (search_hits or [])[: max(1, int(limit))]:
        payload = getattr(hit, "payload", None) or {}
        if not isinstance(payload, dict):
            continue

        _log_project_key_policy_once()
        pjt_id_keys = ["pjt_id", "meta_basic.pjt_id"]
        pjt_no_keys = ["pjt_no", "meta_basic.pjt_no"]
        _add("pjt_id", _payload_get(payload, *pjt_id_keys))
        _add("pjt_no", _payload_get(payload, *pjt_no_keys))
        _add("rst_id", _payload_get(payload, "rst_id", "meta_basic.rst_id", "id"))
        _add("doi", _payload_get(payload, "doi", "meta_basic.doi"))
        _add("issn", _payload_get(payload, "issn", "eissn", "pissn", "meta_basic.issn"))
        _add("patent_reg_no", _payload_get(payload, "patent_reg_no", "meta_basic.patent_reg_no"))

    return out


def _merge_ids_map(base: Dict[str, List[str]], extra: Dict[str, List[str]]) -> Dict[str, List[str]]:
    merged: Dict[str, List[str]] = {}
    keys = set((base or {}).keys()) | set((extra or {}).keys())
    for key in keys:
        merged[key] = _as_list((base or {}).get(key))
        for v in _as_list((extra or {}).get(key)):
            if v not in merged[key]:
                merged[key].append(v)
    return merged


def _infer_join_relation(planner_relation: Any, planner_action: Optional[str]) -> Optional[str]:
    if isinstance(planner_relation, (tuple, list)) and len(planner_relation) == 2:
        lhs = str(planner_relation[0] or "").strip().lower()
        rhs = str(planner_relation[1] or "").strip().lower()
        if lhs and rhs:
            return f"{lhs}_{rhs}"
    rel = str(planner_relation or "").strip().lower()
    if rel in ("project_perf", "perf_project"):
        return rel
    action_norm = str(planner_action or "").strip().lower()
    if action_norm == "relation":
        return "project_perf"
    return None


def promote_mode_from_search_hits(
        *,
        current_mode: str,
        search_hits: List[Any],
        ids_map: Dict[str, List[str]],
        planner_strategy: Any,
) -> Dict[str, Any]:
    """1차 SEARCH hit 기반 2차 실행 승격 정책.

    - SEARCH에서만 동작
    - hit에서 키 추출 성공 + list/detail/stats/relation 계열 의도면
      LOOKUP/JOIN 2차 실행 후보를 반환
    """
    mode_now = str(current_mode or "").strip().lower() or "search"
    merged_ids_map: Dict[str, List[str]] = dict(ids_map or {}) if isinstance(ids_map, dict) else {}

    planner_mode = str(getattr(planner_strategy, "mode", "") or "").strip().lower() or None
    planner_action = str(getattr(planner_strategy, "action", "") or "").strip().lower() or None
    planner_relation = getattr(planner_strategy, "relation", None)

    if mode_now != "search":
        return {
            "mode": mode_now,
            "kind": None,
            "reason": "non_search_mode_passthrough",
            "ids_map": merged_ids_map,
            "planner_mode": planner_mode,
            "planner_action": planner_action,
            "planner_relation": planner_relation,
            "query_text": None,
            "strategy_key": build_strategy_key(planner_action, mode_now),
            "allowed": {"policy": "search_only", "lookup": False, "join": False},
            "signals": {"has_pjt_id": int(bool((merged_ids_map.get("pjt_id") or []))), "has_pjt_no": int(bool((merged_ids_map.get("pjt_no") or []))), "has_history_nuance": 0, "has_detail_nuance": 0, "wants_project_perf_relation": 0},
        }

    extracted = _extract_ids_from_hits(search_hits)
    merged_ids_map = _merge_ids_map(merged_ids_map, extracted)

    has_pjt_id = bool(merged_ids_map.get("pjt_id"))
    has_pjt_no = bool(merged_ids_map.get("pjt_no"))
    has_perf_id = bool(merged_ids_map.get("rst_id") or merged_ids_map.get("doi") or merged_ids_map.get("issn") or merged_ids_map.get("patent_reg_no"))

    action_like_lookup = planner_action in ("list", "detail", "stats", "download", "id_exact", "id_fuzzy", "relation")
    relation_text = _infer_join_relation(planner_relation, planner_action)
    wants_join = bool(relation_text in ("project_perf", "perf_project") or planner_action == "relation")

    promote_mode = mode_now
    kind = None
    reason = "promotion_conditions_not_met"
    if action_like_lookup and (has_pjt_id or has_pjt_no or has_perf_id):
        if wants_join and (has_pjt_id or has_pjt_no or has_perf_id):
            promote_mode = "join"
            kind = "search_to_join"
            reason = "search_hit_ids_and_relation_action"
        else:
            promote_mode = "lookup"
            kind = "search_to_lookup"
            reason = "search_hit_ids_and_lookup_like_action"

    strategy_key = build_strategy_key(planner_action, promote_mode)

    return {
        "mode": promote_mode,
        "kind": kind,
        "reason": reason,
        "ids_map": merged_ids_map,
        "planner_mode": planner_mode,
        "planner_action": planner_action,
        "planner_relation": planner_relation,
        "query_text": None,
        "strategy_key": strategy_key,
        "allowed": {
            "policy": "search_hit_promotion",
            "lookup": bool(action_like_lookup and (has_pjt_id or has_pjt_no or has_perf_id)),
            "join": bool(wants_join and (has_pjt_id or has_pjt_no or has_perf_id)),
        },
        "signals": {
            "has_pjt_id": int(has_pjt_id),
            "has_pjt_no": int(has_pjt_no),
            "has_perf_id": int(has_perf_id),
            "has_history_nuance": 0,
            "has_detail_nuance": int(planner_action in ("detail", "stats", "list")),
            "wants_project_perf_relation": int(wants_join),
        },
    }
