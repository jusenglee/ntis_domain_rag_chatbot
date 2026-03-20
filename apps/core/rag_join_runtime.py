from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from apps.core.rag_constants import COL_PERF

from apps.core.filters import validate_resolved_join_keys
from apps.core.planner_contract import StrategyViolation


def build_join_hop_timing_payload(*, merge_log_fields: Callable[[dict, dict], dict], local_timings: Dict[str, float]) -> dict:
    """JOIN hop 실행 timing을 로그용 payload로 정리한다."""
    timing_fields = {k: float(v) for k, v in (local_timings or {}).items()}
    dense_queries = float(timing_fields.pop("dense_queries", 0.0))
    hybrid_once_hits = float(timing_fields.pop("hybrid_once_hits", 0.0))
    sparse_hits = float(timing_fields.pop("lexical_scored", timing_fields.pop("sparse_hits", 0.0)))
    return merge_log_fields(
        {
            "dense_queries": dense_queries,
            "sparse_hits": sparse_hits,
            "hybrid_once_hits": hybrid_once_hits,
            "timings": timing_fields,
        },
        {},
    )


def build_join_hop_context(
    *,
    context_builder: Callable[..., Tuple[str, List[Any], Any]],
    points: List[Any],
    action: str,
    base_route: str,
    mode: str,
    output_type: str | None,
    max_items: int,
    query_text: str,
    people_terms: List[str],
    person_ids: List[str],
    org_role: str | None,
) -> Tuple[str, List[Any]]:
    """hop1/hop2 hit를 context builder에 넘겨 JOIN 단계용 context와 refs를 만든다."""
    if not points:
        return "", []
    context, refs, _ = context_builder(
        points[: max(1, max_items)],
        action=action,
        base_route=base_route,
        mode=mode,
        output_type=output_type,
        max_items=max(1, max_items),
        query_text=query_text,
        people_terms=people_terms,
        person_ids=person_ids,
        org_role=org_role,
    )
    return context, refs


def compose_join_context(
    *,
    hop1_ctx: str,
    hop2_ctx: str,
    hop2_label: str,
    effective_join_mode: str,
    join_pjt_ids: List[str],
    join_pjt_nos: List[str],
) -> str:
    """hop1 context, hop2 context, 해석된 join key 미리보기를 합쳐 최종 JOIN context를 만든다."""
    join_key_label = "PJT_NO" if effective_join_mode == "group" else "PJT_ID"
    join_key_preview = join_pjt_nos[:10] if effective_join_mode == "group" else join_pjt_ids[:10]
    return (
        f"### [Hop1] Join seed candidates\n{hop1_ctx or '(no hop1 context)'}\n\n"
        f"### [Hop2] {hop2_label}\n"
        f"- Resolved {join_key_label}: {', '.join(join_key_preview)}\n\n"
        f"{hop2_ctx or '(no hop2 context)'}"
    )


def ensure_join_keys_in_payload(
    points: Iterable[Any],
    *,
    get_meta: Callable[[dict], dict],
    pick_first: Callable[..., str],
    force_from_meta: bool = False,
    force_tag_from_tags: bool = True,
) -> Dict[str, int]:
    """payload에 join key와 tag가 비어 있을 때 meta를 이용한 보강을 시도한다."""
    stats = {
        "total": 0,
        "forced_pjt_id": 0,
        "forced_pjt_no": 0,
        "forced_tag": 0,
    }
    for point in points or []:
        payload = getattr(point, "payload", None)
        if not isinstance(payload, dict):
            continue
        stats["total"] += 1
        meta = get_meta(payload)
        if force_from_meta:
            if not payload.get("pjt_id"):
                candidate = pick_first(payload.get("pjt_id"), meta.get("pjt_id"))
                if candidate:
                    payload["pjt_id"] = candidate
                    stats["forced_pjt_id"] += 1
            if not payload.get("pjt_no"):
                candidate = pick_first(payload.get("pjt_no"), meta.get("pjt_no"))
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
                candidate = pick_first(meta.get("tag"))
            if candidate:
                payload["tag"] = candidate
                stats["forced_tag"] += 1
    return stats


def raise_on_missing_join_keys(
    points: Iterable[Any],
    *,
    scope: str,
    join_key_mode: str,
    count_missing_join_keys: Callable[..., Dict[str, int]],
    log_kv: Callable[..., None],
    debug_force_join_keys_enabled: Callable[[], bool],
    ensure_join_keys_in_payload_fn: Callable[[Iterable[Any]], Dict[str, int]],
) -> Dict[str, int]:
    """hop1 payload에 JOIN 키가 없거나 잘못되면 로그를 남기고 StrategyViolation을 올린다."""
    missing = count_missing_join_keys(points, join_key_mode=join_key_mode)
    has_missing = bool(missing.get("missing_pjt_any") or missing.get("missing_tag"))
    has_invalid = bool(
        missing.get("invalid_pjt_id")
        or missing.get("invalid_pjt_no")
        or missing.get("suspected_swap")
        or missing.get("same_id_no")
    )
    if not (has_missing or has_invalid):
        return missing

    if has_missing:
        log_kv(
            "RAG.JOIN_KEYS.MISSING",
            level="error",
            scope=scope,
            join_key_mode=join_key_mode,
            missing_pjt_id=int(missing.get("missing_pjt_id", 0) or 0),
            missing_pjt_no=int(missing.get("missing_pjt_no", 0) or 0),
            missing_pjt_any=int(missing.get("missing_pjt_any", 0) or 0),
            missing_tag=int(missing.get("missing_tag", 0) or 0),
            total=int(missing.get("total", 0) or 0),
            tier="debug",
        )

    if has_invalid:
        log_kv(
            "RAG.JOIN_KEYS.INVALID",
            level="error",
            scope=scope,
            join_key_mode=join_key_mode,
            invalid_pjt_id=int(missing.get("invalid_pjt_id", 0) or 0),
            invalid_pjt_no=int(missing.get("invalid_pjt_no", 0) or 0),
            suspected_swap=int(missing.get("suspected_swap", 0) or 0),
            same_id_no=int(missing.get("same_id_no", 0) or 0),
            total=int(missing.get("total", 0) or 0),
            tier="debug",
        )

    if has_missing and debug_force_join_keys_enabled():
        forced = ensure_join_keys_in_payload_fn(points)
        log_kv("RAG.JOIN_KEYS.DEBUG_FORCE", level="warning", scope=scope, **forced, tier="debug")
        missing = count_missing_join_keys(points, join_key_mode=join_key_mode)
        has_missing = bool(missing.get("missing_pjt_any") or missing.get("missing_tag"))
        has_invalid = bool(
            missing.get("invalid_pjt_id")
            or missing.get("invalid_pjt_no")
            or missing.get("suspected_swap")
            or missing.get("same_id_no")
        )
        if not (has_missing or has_invalid):
            return missing
        if has_missing:
            log_kv(
                "RAG.JOIN_KEYS.DEBUG_FORCE_FAILED",
                level="error",
                scope=scope,
                missing_pjt_id=int(missing.get("missing_pjt_id", 0) or 0),
                missing_pjt_no=int(missing.get("missing_pjt_no", 0) or 0),
                missing_pjt_any=int(missing.get("missing_pjt_any", 0) or 0),
                missing_tag=int(missing.get("missing_tag", 0) or 0),
                total=int(missing.get("total", 0) or 0),
                tier="debug",
            )

    error_code = "JOIN_KEYS_INVALID" if has_invalid else "JOIN_KEYS_MISSING"
    raise StrategyViolation(
        error_code=error_code,
        reason=(
            f"[{scope}] invalid/missing join keys in hop1 payload: "
            f"missing_pjt_id={missing.get('missing_pjt_id', 0)}, "
            f"missing_pjt_no={missing.get('missing_pjt_no', 0)}, "
            f"missing_pjt_any={missing.get('missing_pjt_any', 0)}, "
            f"missing_tag={missing.get('missing_tag', 0)}, "
            f"invalid_pjt_id={missing.get('invalid_pjt_id', 0)}, "
            f"invalid_pjt_no={missing.get('invalid_pjt_no', 0)}, "
            f"suspected_swap={missing.get('suspected_swap', 0)}, "
            f"same_id_no={missing.get('same_id_no', 0)}, "
            f"total={missing.get('total', 0)}"
        ),
    )


def ensure_join_mode_has_keys(
    *,
    has_join_keys: bool,
    join_key_mode: str,
    hop1_top: List[Any],
    hop1_col: str,
    join_pjt_ids_count: int = 0,
    join_pjt_nos_count: int = 0,
    log_kv: Callable[..., None],
    raise_on_missing_join_keys_fn: Callable[..., Dict[str, int]],
) -> None:
    """hop2 진입 전에 join_key_mode에 맞는 키가 확보됐는지 검증한다."""
    log_kv(
        "RAG.JOIN.HOP2.ENTRY_GUARD",
        join_key_mode=join_key_mode,
        hop1_col=hop1_col,
        join_pjt_ids_count=int(join_pjt_ids_count or 0),
        join_pjt_nos_count=int(join_pjt_nos_count or 0),
        has_join_keys=int(bool(has_join_keys)),
        tier="debug",
    )
    if has_join_keys:
        return

    if hop1_top:
        raise_on_missing_join_keys_fn(
            hop1_top,
            scope=f"join_hop1:{hop1_col}:drop_keys",
            join_key_mode=join_key_mode,
        )

    join_mode_norm = str(join_key_mode or "").strip().lower()
    join_key_label = "PROJECT_KEY" if join_mode_norm == "deferred" else ("PJT_NO" if join_mode_norm == "group" else "PJT_ID")
    error_code = "JOIN_GROUP_KEYS_UNRESOLVED" if join_mode_norm == "group" else ("JOIN_KEYS_MISSING" if join_mode_norm == "instance" else "JOIN_DEFERRED_UNRESOLVED")
    raise StrategyViolation(
        error_code=error_code,
        reason=(
            "mode=join requires Hop2 execution, but join keys were not extracted "
            f"from Hop1 ({join_key_label} missing; "
            f"join_key_mode={join_key_mode}, hop1_col={hop1_col}, "
            f"join_pjt_ids_count={int(join_pjt_ids_count or 0)}, "
            f"join_pjt_nos_count={int(join_pjt_nos_count or 0)})."
        ),
    )


def normalize_join_collection_name(collection: str) -> str:
    """project/perf 논리 이름을 실제 JOIN 실행에 쓰는 컬렉션명으로 맞춘다."""
    raw = str(collection or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "project":
        return "ntis_project_v1"
    if lowered == "perf":
        return COL_PERF
    return raw


def resolve_join_compile_selection(
    *,
    hop2_col: str,
    join_key_mode: str,
    join_pjt_ids: List[str],
    join_pjt_nos: List[str],
) -> str:
    """hop2에서 어떤 join key 전략을 쓸지 런타임 선택값으로 정리한다."""
    join_mode_norm = str(join_key_mode or "instance").strip().lower()
    if join_mode_norm == "deferred":
        if join_pjt_ids and join_pjt_nos:
            return "deferred_dual_branch"
        if join_pjt_nos:
            return "deferred_group"
        if join_pjt_ids:
            return "deferred_instance"
        return "deferred_unresolved"
    if hop2_col == COL_PERF and join_mode_norm == "group":
        if join_pjt_nos:
            return "group_pjt_no_only"
        if join_pjt_ids:
            return "group_perf_pjt_id_fallback"
        return "group_join_keys_missing"
    return "planner_contract"


def resolve_hop2_key_strategy(*, join_key_mode: str, join_compile_selection: str) -> str:
    """join compile selection을 hop2 runtime key 전략으로 변환한다."""
    selection_norm = str(join_compile_selection or "").strip().lower()
    join_mode_norm = str(join_key_mode or "instance").strip().lower()
    if selection_norm == "group_perf_pjt_id_fallback":
        return "pjt_id_in"
    if selection_norm == "deferred_dual_branch":
        return "dual_branch"
    if selection_norm == "deferred_group":
        return "pjt_no"
    if selection_norm == "deferred_instance":
        return "pjt_id_in"
    if join_mode_norm == "deferred":
        return "project_key_exact_or"
    return "pjt_no" if join_mode_norm == "group" else "pjt_id_in"


def _build_join_hop2_meta(
    *,
    hop2_col: str,
    join_key_mode: str,
    join_pjt_ids: List[str],
    join_pjt_nos: List[str],
    join_ids: List[str],
    planner_hop2_filter_applied: bool,
) -> Dict[str, Any]:
    """hop2 필터 실행에 대한 메타 정보를 응답/로그용으로 조립한다."""
    join_compile_selection = resolve_join_compile_selection(
        hop2_col=hop2_col,
        join_key_mode=join_key_mode,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
    )
    hop2_key_strategy = resolve_hop2_key_strategy(
        join_key_mode=join_key_mode,
        join_compile_selection=join_compile_selection,
    )
    if hop2_key_strategy == "dual_branch":
        resolved_runtime_key_kind = "mixed"
        join_keys_used_count = len(join_pjt_ids) + len(join_pjt_nos)
    elif hop2_key_strategy == "pjt_id_in":
        resolved_runtime_key_kind = "pjt_id"
        join_keys_used_count = len(join_pjt_ids)
    else:
        resolved_runtime_key_kind = "pjt_no"
        join_keys_used_count = len(join_pjt_nos)
    return {
        "hop2_col": hop2_col,
        "join_key_mode": join_key_mode,
        "join_compile_selection": join_compile_selection,
        "hop2_key_strategy": hop2_key_strategy,
        "resolved_runtime_key_kind": resolved_runtime_key_kind,
        "join_ids_count": len(join_pjt_ids if str(join_key_mode or "").strip().lower() == "instance" else join_ids),
        "pjt_nos_count": len(join_pjt_nos),
        "resolved_pjt_ids_count": len(join_pjt_ids),
        "join_keys_used_count": join_keys_used_count,
        "planner_hop2_filter_applied": int(planner_hop2_filter_applied),
    }


def build_join_hop1_filter(
    *,
    relation: Optional[Tuple[str, str]],
    hop1_col: str,
    hop1_filter: Any,
    compiled_hop1_spec: Optional[Dict[str, Any]],
    and_filter: Callable[[Any, Any], Any],
) -> Tuple[Any, Dict[str, Any]]:
    """planner hop1 filter와 실행 hop1 filter를 합치고 collection mismatch를 검증한다."""
    planner_hop1_spec = dict(compiled_hop1_spec or {})
    planner_hop1_col = normalize_join_collection_name(str(planner_hop1_spec.get("collection") or ""))
    if planner_hop1_col and planner_hop1_col != hop1_col:
        raise StrategyViolation(
            error_code="PLANNER_JOIN_HOP1_COLLECTION_MISMATCH",
            reason=(
                "planner hop1_spec.collection must match the executed hop1_col "
                f"(planner={planner_hop1_col}, executed={hop1_col}, relation={relation})"
            ),
        )

    planner_hop1_filter = planner_hop1_spec.get("qdrant_filter")
    if planner_hop1_filter is not None:
        hop1_filter = and_filter(hop1_filter, planner_hop1_filter)

    executed_hop1_filter_spec = {
        "hop1_col": hop1_col,
        "planner_hop1_filter_applied": int(planner_hop1_filter is not None),
    }
    return hop1_filter, executed_hop1_filter_spec


def build_join_hop2_filter(
    *,
    relation: Optional[Tuple[str, str]],
    hop2_col: str,
    join_key_mode: str,
    join_pjt_ids: List[str],
    join_pjt_nos: List[str],
    join_ids: Optional[List[str]],
    q: str,
    hop2_tag_filters: Optional[List[str]],
    people_terms: List[str],
    org_terms: List[str],
    planner_filter_spec: Dict[str, Any],
    compiled_hop2_spec: Optional[Dict[str, Any]],
    build_collection_join_filter: Callable[..., Any],
    join_filter_input_factory: Callable[..., Any],
    and_filter: Callable[[Any, Any], Any],
    serialize_filter_for_log: Callable[[Any], Any],
    log_kv: Callable[..., None],
) -> Tuple[Any, Dict[str, Any]]:
    """hop2 collection, join_key_mode, planner filter를 합쳐 JOIN hop2 필터를 만든다."""
    join_ids = join_ids or []
    if str(join_key_mode or "instance").strip().lower() == "group":
        join_ids = []

    join_mode_norm = str(join_key_mode or "instance").strip().lower()
    hop2_meta = _build_join_hop2_meta(
        hop2_col=hop2_col,
        join_key_mode=join_key_mode,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
        join_ids=join_ids,
        planner_hop2_filter_applied=False,
    )

    relation_matrix = {
        "relation": relation,
        "hop2_col": hop2_col,
        "join_key_mode": join_key_mode,
        "join_compile_selection": hop2_meta["join_compile_selection"],
        "join_pjt_ids_count": len(join_pjt_ids),
        "join_pjt_nos_count": len(join_pjt_nos),
    }
    log_kv("RAG.JOIN.HOP2.RELATION_MATRIX", **relation_matrix, tier="debug")

    hop2_filter = build_collection_join_filter(
        hop2_col=hop2_col,
        join_key_mode=join_mode_norm,
        join_ids=(join_pjt_ids if join_key_mode == "instance" else join_ids),
        pjt_nos=join_pjt_nos,
        resolved_pjt_ids=join_pjt_ids,
        perf_group_strategy="prefer_pjt_no",
        query=q,
        fallback_spec=join_filter_input_factory(
            join_ids=join_pjt_ids,
            pjt_nos=join_pjt_nos,
            join_key_mode=join_key_mode,
            tag_filters=hop2_tag_filters,
            people_terms=people_terms,
            org_terms=org_terms,
            relation=relation,
            filter_spec=planner_filter_spec.get("join_filter"),
        ),
    )
    planner_hop2_spec = dict(compiled_hop2_spec or {})
    planner_hop2_col = normalize_join_collection_name(str(planner_hop2_spec.get("collection") or ""))
    if planner_hop2_col and planner_hop2_col != hop2_col:
        raise StrategyViolation(
            error_code="PLANNER_JOIN_HOP2_COLLECTION_MISMATCH",
            reason=(
                "planner hop2_spec.collection must match the executed hop2_col "
                f"(planner={planner_hop2_col}, executed={hop2_col}, relation={relation})"
            ),
        )
    planner_hop2_filter = planner_hop2_spec.get("qdrant_filter")
    if planner_hop2_filter is not None:
        hop2_filter = and_filter(hop2_filter, planner_hop2_filter)

    executed_join_filter_spec = dict(serialize_filter_for_log(hop2_filter) or {})
    executed_join_filter_spec.setdefault("_meta", {})
    if isinstance(executed_join_filter_spec.get("_meta"), Mapping):
        hop2_meta = _build_join_hop2_meta(
            hop2_col=hop2_col,
            join_key_mode=join_key_mode,
            join_pjt_ids=join_pjt_ids,
            join_pjt_nos=join_pjt_nos,
            join_ids=join_ids,
            planner_hop2_filter_applied=planner_hop2_filter is not None,
        )
        executed_join_filter_spec["_meta"] = {
            **dict(executed_join_filter_spec.get("_meta") or {}),
            **hop2_meta,
        }
    return hop2_filter, executed_join_filter_spec


@dataclass(frozen=True)
class Hop1JoinResolution:
    """hop1에서 추출한 JOIN 키 해결 결과를 묶는다."""
    join_key_result: Any
    join_pjt_ids: List[str]
    join_pjt_nos: List[str]
    join_key_source: Optional[str]



@dataclass(frozen=True)
class DeferredJoinResolution:
    """ambiguous project key discovery 이후 resolved 또는 dual-branch 결정을 담는다."""
    resolved_runtime_join_mode: str
    join_pjt_ids: List[str]
    join_pjt_nos: List[str]
    join_key_source: Optional[str]
    dual_branch_used: bool
    resolution_reason: str
@dataclass(frozen=True)
class JoinHop2Preparation:
    """hop2 진입 전 준비된 join mode와 filter 결과를 묶는다."""
    planner_join_key_mode: str
    hop2_join_key_mode: str
    effective_join_mode: str
    join_compile_selection: str
    hop2_filter: Any
    executed_join_filter_spec: Dict[str, Any]


_PJT_NO_ALLOWED_RE = re.compile(os.getenv("RAG_JOIN_PJT_NO_ALLOWED_RE", r"^[A-Za-z0-9_-]{4,40}$"))


@dataclass(frozen=True)
class JoinKeySwapHint:
    """잘못 들어온 join key가 다른 key와 뒤바뀐 정황을 설명한다."""
    index: int
    expected_key: str
    candidate: str
    swap_candidate: str


@dataclass(frozen=True)
class JoinKeyExtractionResult:
    """hop1 payload에서 추출한 JOIN 키, invalid 값, swap 의심을 함께 보존한다."""
    keys: List[str]
    invalid_values: List[str]
    suspected_swaps: List[JoinKeySwapHint]

    @property
    def suspected_swap_count(self) -> int:
        """swap 의심 힌트 개수를 계산한다."""
        return len(self.suspected_swaps)

    def to_log_dict(self) -> Dict[str, Any]:
        """JoinKeyExtractionResult를 로그에 실을 수 있는 dict로 바꾼다."""
        return {
            "keys": list(self.keys),
            "invalid_values": list(self.invalid_values),
            "suspected_swaps": [
                {
                    "index": hint.index,
                    "expected_key": hint.expected_key,
                    "candidate": hint.candidate,
                    "swap_candidate": hint.swap_candidate,
                }
                for hint in self.suspected_swaps
            ],
            "suspected_swap_count": self.suspected_swap_count,
        }


def _as_dict(x: Any) -> Optional[Dict[str, Any]]:
    """dict가 아닌 값은 None으로 정리한다."""
    return x if isinstance(x, dict) else None


def _get_payload(point: Any) -> Optional[Dict[str, Any]]:
    """point 또는 dict 형태 입력에서 payload dict를 안전하게 꼭 집어 온다."""
    payload = getattr(point, "payload", None)
    if isinstance(payload, dict):
        return payload
    data = _as_dict(point)
    if data is None:
        return None
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload
    return data


def is_valid_join_key(value: Any, *, mode: str) -> bool:
    """join_key_mode에 따라 주어진 값이 JOIN 키로 유효한지 판단한다."""
    text_value = str(value or "").strip()
    if not text_value:
        return False
    mode_norm = str(mode or "instance").strip().lower()
    if mode_norm == "group":
        return bool(_PJT_NO_ALLOWED_RE.fullmatch(text_value))
    return True


def extract_join_keys(points: Iterable[Any], *, mode: str = "instance", max_ids: int = 80) -> JoinKeyExtractionResult:
    """hop1 hit들에서 mode에 맞는 JOIN 키를 순서대로 추출한다."""
    mode_norm = str(mode or "instance").strip().lower()
    if mode_norm not in {"instance", "group"}:
        raise ValueError(f"unsupported join key mode: {mode_norm}")
    target_key = "pjt_no" if mode_norm == "group" else "pjt_id"
    other_key = "pjt_id" if target_key == "pjt_no" else "pjt_no"
    keys: List[str] = []
    seen: set[str] = set()
    invalid_values: List[str] = []
    suspected_swaps: List[JoinKeySwapHint] = []
    for idx, point in enumerate(points or []):
        payload = _get_payload(point)
        if not payload:
            continue
        target_value = str(payload.get(target_key) or "").strip()
        other_value = str(payload.get(other_key) or "").strip()
        if target_value and not is_valid_join_key(target_value, mode=mode_norm):
            invalid_values.append(target_value)
            if other_value and is_valid_join_key(other_value, mode=mode_norm):
                suspected_swaps.append(JoinKeySwapHint(index=idx, expected_key=target_key, candidate=target_value, swap_candidate=other_value))
            continue
        if not target_value or target_value in seen:
            continue
        keys.append(target_value)
        seen.add(target_value)
        if len(keys) >= max_ids:
            break
    return JoinKeyExtractionResult(keys=keys, invalid_values=invalid_values, suspected_swaps=suspected_swaps)


def normalize_relation_hint(value: Any) -> Optional[Tuple[str, str]]:
    """relation hint를 `(left, right)` tuple 형태로 정규화한다."""
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return str(value[0]).strip().lower(), str(value[1]).strip().lower()
    text_value = str(value).strip().lower()
    if not text_value:
        return None
    if "_" in text_value:
        parts = [part.strip() for part in text_value.split("_") if part.strip()]
        if len(parts) == 2:
            return parts[0], parts[1]
    return None


def resolve_deferred_join_from_hop1(
    *,
    hop1_top: List[Any],
    hop1_keep: int,
    seed_join_pjt_ids: List[str],
    seed_join_pjt_nos: List[str],
    extract_join_keys: Callable[..., Any],
    log_kv: Callable[..., None],
    join_resolution_policy: Optional[str] = None,
) -> DeferredJoinResolution:
    """hop1 discovery 결과로 deferred join의 instance, group, dual-branch를 결정한다."""
    instance_result = extract_join_keys(hop1_top[:hop1_keep], mode="instance", max_ids=max(1, hop1_keep))
    group_result = extract_join_keys(hop1_top[:hop1_keep], mode="group", max_ids=max(1, hop1_keep))
    join_pjt_ids = list(dict.fromkeys([*(str(x).strip() for x in (seed_join_pjt_ids or []) if str(x).strip()), *(str(x).strip() for x in (instance_result.keys or []) if str(x).strip())]))
    join_pjt_nos = list(dict.fromkeys([*(str(x).strip() for x in (seed_join_pjt_nos or []) if str(x).strip()), *(str(x).strip() for x in (group_result.keys or []) if str(x).strip())]))

    resolution_policy = str(join_resolution_policy or "").strip().lower()
    if join_pjt_ids and join_pjt_nos:
        if resolution_policy == "dual_branch":
            resolved_mode = "deferred"
            dual_branch_used = True
            resolution_reason = "planner_requested_dual_branch"
        elif len(join_pjt_ids) == 1 and len(hop1_top or []) <= 1:
            resolved_mode = "instance"
            dual_branch_used = False
            resolution_reason = "single_project_exact_match"
        elif len(join_pjt_nos) == 1 and len(join_pjt_ids) > 1:
            resolved_mode = "group"
            dual_branch_used = False
            resolution_reason = "shared_group_across_instances"
        else:
            resolved_mode = "deferred"
            dual_branch_used = True
            resolution_reason = "ambiguous_dual_branch"
    elif join_pjt_ids:
        resolved_mode = "instance"
        dual_branch_used = False
        resolution_reason = "instance_keys_only"
    elif join_pjt_nos:
        resolved_mode = "group"
        dual_branch_used = False
        resolution_reason = "group_keys_only"
    else:
        resolved_mode = "deferred"
        dual_branch_used = False
        resolution_reason = "no_join_keys_from_discovery"

    log_kv(
        "RAG.JOIN.DEFERRED.RESOLVE",
        resolved_runtime_join_mode=resolved_mode,
        dual_branch_used=int(dual_branch_used),
        resolution_reason=resolution_reason,
        join_pjt_ids_count=len(join_pjt_ids),
        join_pjt_nos_count=len(join_pjt_nos),
        tier="debug",
    )
    return DeferredJoinResolution(
        resolved_runtime_join_mode=resolved_mode,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
        join_key_source=("hop1" if hop1_top else None),
        dual_branch_used=dual_branch_used,
        resolution_reason=resolution_reason,
    )


def resolve_join_keys_from_hop1(
    *,
    hop1_top: List[Any],
    hop1_reranked: List[Any],
    hop1_keep: int,
    join_key_mode: str,
    seed_join_pjt_nos: List[str],
    join_execution_policy: Dict[str, Any],
    relation: Any,
    hop1_col: str,
    hop1_k_base: int,
    log_kv: Callable[..., None],
    extract_join_keys: Callable[..., Any],
    resolve_group_pjt_ids: Callable[..., List[str]],
) -> Hop1JoinResolution:
    """hop1 top/reranked 결과에서 instance/group JOIN 키를 해결한다."""
    if str(join_key_mode or "").strip().lower() == "group":
        join_key_result = extract_join_keys(hop1_top[:hop1_keep], mode="group", max_ids=hop1_keep)
        group_resolve_max = int(join_execution_policy.get("group_resolve_max_ids") or 1)
        group_resolve_keep_env = int(join_execution_policy.get("group_resolve_keep") or 1)
        group_resolve_enabled = int(join_execution_policy.get("group_resolve_project_ids") or 0)
        resolve_keep_effective = max(hop1_keep, min(max(1, group_resolve_max), max(1, group_resolve_keep_env)))
        resolve_candidates = hop1_reranked[:resolve_keep_effective]
        resolve_input_count = len(resolve_candidates)
        join_pjt_nos = seed_join_pjt_nos[:] if seed_join_pjt_nos else [str(x).strip() for x in join_key_result.keys if str(x).strip()]
        join_pjt_ids = (
            resolve_group_pjt_ids(resolve_candidates, max_ids=max(1, group_resolve_max))
            if group_resolve_enabled
            else []
        )
        log_kv(
            "RAG.JOIN.GROUP.RESOLVE",
            pjt_no=(join_pjt_nos[0] if join_pjt_nos else None),
            resolve_project_ids=group_resolve_enabled,
            resolve_input_count=resolve_input_count,
            resolve_keep_effective=resolve_keep_effective,
            resolved_pjt_ids_count=len(join_pjt_ids),
            resolved_pjt_ids_top10=join_pjt_ids[:10],
            hop1_k=hop1_k_base,
            hop1_keep=hop1_keep,
            cache_hit=0,
            tier="debug",
        )
    else:
        join_key_result = extract_join_keys(hop1_top[:hop1_keep], mode="instance", max_ids=hop1_keep)
        join_pjt_ids = [str(x).strip() for x in join_key_result.keys if str(x).strip()]
        join_pjt_nos = []

    return Hop1JoinResolution(
        join_key_result=join_key_result,
        join_pjt_ids=join_pjt_ids,
        join_pjt_nos=join_pjt_nos,
        join_key_source=("hop1" if hop1_top else None),
    )
