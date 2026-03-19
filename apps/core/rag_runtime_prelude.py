from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import ExecutionContext, StrategySpec
from apps.core.settings import get_ctx_token_budget, get_model_max_output_tokens
from apps.core.query_intent import get_relation_route, normalize_categories, normalize_org_terms, pick_perf_tag_filters
from apps.core.planner_locking import resolve_planner_locked_plan
from apps.core.rag_strategy_guard import (
    derive_planner_locks,
    normalize_strategy_target_cols,
    strategy_consistency_or_violation,
    strategy_must_match_or_violation,
    validate_project_key_exclusive,
)
from apps.core.rag_compile_runtime import assemble_runtime_compile_policy
from apps.core.rag_search_policy import build_rerank_spec as _build_rerank_spec, build_search_preset as _build_search_preset, build_topk_spec as _build_topk_spec, resolve_sparse_vector_name as _resolve_sparse_vector_name
from apps.core.planner_contract import validate_planner_contract, StrategyViolation
from apps.core.rag_join_runtime import normalize_relation_hint as _normalize_relation_hint
from apps.core.filters import (
    TITLE_MATCH_MODE_EXACT,
    TITLE_MATCH_MODE_TEXT,
    OrgFilterInput,
    PeopleFilterInput,
    build_org_filter,
    build_people_filter,
    build_perf_type_filter,
    build_prtcp_org_nested_filter,
    build_tag_only_filter as _build_tag_only_filter,
    build_title_exact_filter,
    build_title_text_filter,
    build_year_range_filter,
)

try:
    from qdrant_client.http import models as qmodels
except Exception:
    qmodels = None


@dataclass(frozen=True)
class RuntimePreludeRequest:
    """runtime prelude가 실행 전에 받는 요청 입력을 묶는 구조체다.
    query, normalized intent payload, allowlist, lexical/dense hint, 한도 설정을 한 바구니에 모아 prelude 조립을 순수 함수처럼 다룰 수 있게 한다.
    """
    query: str
    model_name: str
    intent_payload: Any
    domain_hint: Optional[str]
    stack: str
    lexical_field_weights: Optional[Dict[str, float]]
    sparse_vector_name: Optional[str]
    sparse_topk: Optional[int]
    sparse_weight: Optional[float]
    effective_allow: List[str]
    hinted_limit: int
    hinted_cols: List[str]
    w_dense_map: Dict[str, float]


@dataclass(frozen=True)
class RuntimePreludeRuntime:
    """runtime prelude 중 호출할 로깅·계측·보조 callback을 묶는다.
    strategy guard, diff logging, id flattening, join-seed check를 주입받아 prelude가 현재 환경에 맞는 계측과 검증을 수행한다.
    """
    logger: Any
    log_kv_fn: Callable[..., None]
    log_section_fn: Callable[..., None]
    timing_put_fn: Callable[[Dict[str, Any], str, Any], None]
    diff_filter_spec_fn: Callable[..., Dict[str, Any]]
    flatten_ids_from_intent_fn: Callable[[Any], List[str]]
    has_relation_join_ids_fn: Callable[[NormalizedIntent], bool]


@dataclass(frozen=True)
class RuntimePreludeResult:
    """runtime prelude가 조립한 실행 truth 전체를 보관하는 결과 구조체다.
    plan, strategy, target collections, search/rerank preset, filter objects, people/org/title signal을 함께 실어 후속 retrieval·join·render가 같은 truth를 읽게 한다.
    """
    query_text: str
    keywords: List[str]
    intent_item: Any
    context_state: Any
    plan: Any
    strategy: Any
    mode: str
    action: str
    base_route: str
    relation: Any
    target_collections: List[str]
    planner_limit: int
    hinted_limit: int
    compiled_strategy: Any
    planner_filter_spec: Dict[str, Any]
    resolved_join_key_mode: Optional[str]
    planner_raw_join_key_mode: Optional[str]
    preset: Any
    lex_w_eff: Dict[str, float]
    sparse_vector_name_eff: Optional[str]
    sparse_topk_eff: int
    sparse_weight_eff: float
    topk_spec: Dict[str, Any]
    rerank_spec: Dict[str, Any]
    topk_dense: int
    topk_lex_cand: int
    topk_lex: int
    use_dense_threshold_policy: bool
    min_dense_score_policy: float
    title_terms: List[str]
    title_match_mode: str
    title_filter: Any
    title_filter_server_applied: bool
    lookup_title_filter_policy: str
    lookup_filter_policy: str
    search_filter_signal: bool
    search_filter_conf_ok: bool
    search_filter_enabled: bool
    lookup_filter_enabled: bool
    relation_lookup_enforce: bool
    join_hop1_lookup_filter_enabled: bool
    search_filter_server_policy: str
    org_terms: List[str]
    org_role: Optional[str]
    people_terms: List[str]
    people_ids: List[str]
    gender_terms: List[str]
    people_org_terms: List[str]
    people_min_should: Optional[int]
    people_match_mode: Optional[str]
    people_promote_one_must: bool
    people_filter: Any
    participant_org_filter: Any
    org_filter: Any
    planner_org_filter_present: bool
    project_tag_filter: Any
    perf_tag_filter: Any
    year_range_filter: Any
    perf_type_filter: Any


def build_runtime_prelude_result(**kwargs: Any) -> RuntimePreludeResult:
    """keyword 인자로 모은 prelude 산출물을 `RuntimePreludeResult`로 재포장한다.
    prelude 조립 코드가 긴 인자 목록을 다룰 때 목적값을 명시하는 엔트리 포인트로 쓴다.
    """
    return RuntimePreludeResult(**kwargs)


def _normalize_hint_terms(values: Any) -> List[str]:
    """hint로 들어온 용어 목록을 중복 없는 문자열 리스트로 정리한다.
    title/org/people 히트가 여러 형태로 들어와도 후속 filter 빌더가 같은 shape를 보게 한다.
    """
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_ids_map(ids_map: Any) -> Dict[str, List[str]]:
    """planner 또는 hint에서 들어온 ids_map을 정규화한다.
    scalar/list/set 차이를 흡수하고 빈 값을 제거해 contract validation과 join seed 판단에 쓸 canonical ids_map를 만든다.
    """
    if not isinstance(ids_map, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for key, values in ids_map.items():
        seq = values if isinstance(values, (list, tuple, set)) else [values]
        norm = _normalize_hint_terms(seq)
        if norm:
            out[str(key)] = norm
    return out


def _normalize_target_collections(raw: Any) -> List[str]:
    """target collection 후보를 순서 유지를 하면서 중복 없이 정리한다.
    enum value·문자열·리스트 형태 차이를 흡수해 strategy truth에 실 target collection 목록을 고정한다.
    """
    if raw is None:
        return []
    items = raw
    if isinstance(items, str):
        items = [x.strip() for x in items.split(",") if x.strip()] or [items]
    if not isinstance(items, (list, tuple, set)):
        items = [items]
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(getattr(item, "value", item) or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _extract_payload_normalized_intent(payload: Any) -> Any:
    """intent payload 래퍼에서 `normalized_intent` 본문만 꺼내온다.
    mapping/object wrapper 양쪽을 허용해 prelude가 payload shape에 덤 민감하게 한다.
    """
    if payload is None:
        return None
    if isinstance(payload, Mapping):
        return payload.get("normalized_intent")
    return getattr(payload, "normalized_intent", None)


def _normalize_payload_intent(raw: Any) -> Optional[NormalizedIntent]:
    """payload 내 normalized intent를 `NormalizedIntent` 형식으로 복원한다.
    mapping/object에서 필드를 주워담고 relation·categories같은 복합 필드를 추가 정규화해 runtime contract에 맞춘다.
    """
    if isinstance(raw, NormalizedIntent):
        return raw
    if raw is None:
        return None
    getter = raw.get if isinstance(raw, Mapping) else (lambda key, default=None: getattr(raw, key, default))
    data: Dict[str, Any] = {}
    for field in fields(NormalizedIntent):
        value = getter(field.name, None)
        if value is not None:
            data[field.name] = value
    if not data:
        return None
    if "relation" in data:
        data["relation"] = _normalize_relation_hint(data.get("relation"))
    for key in ("years", "people_terms", "gender_terms", "org_terms", "perf_types", "keywords", "title", "perf_tag_filters", "project_tag_filters", "tag_filters", "ids_flat", "remove_terms_for_head", "target_cols"):
        if key in data:
            data[key] = _normalize_hint_terms(data.get(key))
    if "ids_map" in data:
        data["ids_map"] = _normalize_ids_map(data.get("ids_map"))
    if "categories" in data:
        data["categories"] = normalize_categories(data.get("categories"))
    required = {"action", "base_route", "is_id_query"}
    if not required.issubset(data.keys()):
        return None
    try:
        return NormalizedIntent(**data)
    except Exception:
        return None


def _assert_allowlist_only(*, target_cols: List[str], allow_cols: List[str]) -> None:
    """현재 target collection이 허용된 allowlist 내에 있는지 fail-close로 검증한다.
    planner나 hint가 루트 밖 collection을 집어넣는 경로를 막아 retrieval-first 위반을 초기에 차단한다.
    """
    normalized_targets = normalize_strategy_target_cols(target_cols)
    normalized_allow = normalize_strategy_target_cols(allow_cols)
    if normalized_allow and any(col not in set(normalized_allow) for col in normalized_targets):
        raise StrategyViolation(error_code="PLANNER_TARGET_COLS_ALLOWLIST_VIOLATION", reason="planner target_cols contains disallowed collections")


def build_runtime_prelude(*, request: RuntimePreludeRequest, runtime: RuntimePreludeRuntime, timings: Dict[str, Any]) -> RuntimePreludeResult:
    """normalized intent, planner compile result, filter policy를 합친 runtime prelude truth를 조립한다.
    plan·strategy·search preset·rerank spec·filter object·allowlist check를 한 곳에서 완결하는 retrieval 실행 전 최종 관문이다.
    """
    q = request.query
    hinted_limit = int(request.hinted_limit or 0)
    hinted_cols = list(request.hinted_cols or [])

    payload_normalized_intent = _extract_payload_normalized_intent(request.intent_payload)
    if payload_normalized_intent is None:
        raise StrategyViolation(error_code="PLANNER_INTENT_PAYLOAD_REQUIRED", reason="intent_payload.normalized_intent is required")

    payload_target_cols = _normalize_target_collections(getattr(payload_normalized_intent, "target_cols", None) if not isinstance(payload_normalized_intent, dict) else payload_normalized_intent.get("target_cols"))
    if payload_target_cols:
        hinted_cols = payload_target_cols
    payload_keywords = getattr(payload_normalized_intent, "keywords", None) if not isinstance(payload_normalized_intent, dict) else payload_normalized_intent.get("keywords")
    kws = _normalize_hint_terms(payload_keywords)
    runtime.timing_put_fn(timings, "phase.kw_det", 0.0)

    it = _normalize_payload_intent(payload_normalized_intent)
    if it is None:
        raise StrategyViolation(error_code="PLANNER_INTENT_PAYLOAD_INVALID", reason="intent_payload.normalized_intent is invalid or missing required fields")

    ctx = ExecutionContext.from_intent(it)
    planner_mode = str(ctx.mode or "").strip().lower() or None
    planner_action = str(ctx.action or "").strip().lower() or None
    planner_limit = ctx.planner_limit
    planner_filter_spec = dict(getattr(ctx, "lookup_filter_spec", None) or {})
    if ctx.retrieval_query:
        q = str(ctx.retrieval_query).strip() or q
    if planner_limit is not None:
        hinted_limit = int(planner_limit)

    runtime.timing_put_fn(timings, "info.ctx_budget", float(get_ctx_token_budget(request.model_name, max_output_tokens=get_model_max_output_tokens(request.model_name))))

    org_terms = normalize_org_terms(list(ctx.org_terms or []))
    org_role = str(ctx.org_role or "").strip().lower() or None
    people_terms = [t.strip() for t in (list(ctx.people_terms or []) or []) if str(t).strip()]
    people_ids = list((ctx.ids_map or {}).get("person_no") or [])
    gender_terms = [t.strip() for t in (list(ctx.gender_terms or []) or []) if str(t).strip()]
    people_org_terms = normalize_org_terms(list(getattr(ctx, "people_affiliation_org_terms", []) or []))
    people_match_mode = str(getattr(ctx, "people_terms_match_mode", "") or "").strip().lower() or None
    people_min_should = None if people_match_mode == "and" and len(people_terms) >= 2 else (getattr(ctx, "people_terms_min_should", None) if getattr(ctx, "people_terms_min_should", None) is not None else (1 if len(people_terms) >= 2 else None))
    people_promote_one_must = False
    planner_org_filter_present = bool(getattr(ctx, "lead_org_terms", None) or getattr(ctx, "participant_org_terms", None) or getattr(ctx, "people_affiliation_org_terms", None))
    org_filter = build_org_filter(OrgFilterInput(normalize_org_terms(list(getattr(ctx, "lead_org_terms", []) or [])), role="lead")) if getattr(ctx, "lead_org_terms", None) else None
    participant_org_filter = build_prtcp_org_nested_filter(OrgFilterInput(normalize_org_terms(list(getattr(ctx, "participant_org_terms", []) or [])), role="participant")) if getattr(ctx, "participant_org_terms", None) else None
    people_filter = build_people_filter(PeopleFilterInput(people_terms=people_terms, person_ids=people_ids, gender_terms=gender_terms, org_terms=people_org_terms, filter_spec=None, min_should=people_min_should, promote_one_must=people_promote_one_must)) if (people_terms or people_ids or gender_terms or people_org_terms) else None

    year_from = str(ctx.year_from or "").strip() or None
    year_to = str(ctx.year_to or "").strip() or None
    if not year_from and (ctx.years or []):
        year_from = str(ctx.years[0]).strip() or None
    if not year_to and (ctx.years or []):
        year_to = str(ctx.years[-1]).strip() or year_from
    year_range_filter = build_year_range_filter(year_from, year_to) if (year_from or year_to) else None

    perf_types = [t.strip() for t in (list(ctx.perf_types or []) or []) if str(t).strip()]
    perf_type_filter = build_perf_type_filter(perf_types) if perf_types else None
    title_terms = [t.strip() for t in (list(ctx.title or []) or []) if str(t).strip()]
    keyword_terms = _normalize_hint_terms([*kws, *list(ctx.keywords or []), *title_terms])
    ctx.keywords = keyword_terms
    kws = keyword_terms
    project_tag_filter = _build_tag_only_filter(list(ctx.project_tag_filters or [])) if getattr(ctx, "project_tag_filters", None) else None
    explicit_perf_tags = list(ctx.perf_tag_filters or [])
    inferred_perf_tags = pick_perf_tag_filters(q)
    perf_tag_filter = _build_tag_only_filter(explicit_perf_tags) if explicit_perf_tags else None
    ctx.perf_tag_filters = explicit_perf_tags if explicit_perf_tags else inferred_perf_tags

    search_filter_signal = bool(title_terms or people_terms or org_terms or ctx.tag_filters or ctx.project_tag_filters or ctx.perf_tag_filters)
    planner_confidence = getattr(it, "planner_confidence", None)
    try:
        planner_confidence = float(planner_confidence) if planner_confidence is not None else None
    except Exception:
        planner_confidence = None
    search_filter_conf_ok = bool(planner_confidence is not None and planner_confidence >= float(os.getenv("RAG_SEARCH_FILTER_MIN_CONF", "0.6")))

    plan, _, planner_mode, _ = resolve_planner_locked_plan(it, planner_mode=planner_mode, planner_action=planner_action, hinted_cols=hinted_cols)
    if planner_filter_spec:
        plan = replace(plan, filters=planner_filter_spec)
    ctx.ids_map = validate_project_key_exclusive(ctx.ids_map, plan.mode)
    ctx.target_collections = list(plan.target_collections)
    planner_mode_locked, planner_relation_locked, planner_target_cols_locked = derive_planner_locks(plan)
    _assert_allowlist_only(target_cols=planner_target_cols_locked, allow_cols=request.effective_allow)

    resolved_join_key_mode = str(getattr(ctx, "join_key_mode", "") or "").strip().lower() or None
    route_for_contract = get_relation_route(ctx.relation) if ctx.relation else None
    relation_target_cols_for_contract = ((route_for_contract.hop1_col, route_for_contract.hop2_col) if route_for_contract is not None else None)
    contract_violations = validate_planner_contract(mode=plan.mode, head=ctx.base_route, relation=ctx.relation, target_cols=list(ctx.target_collections or plan.target_collections or []), ids_map=getattr(ctx, "ids_map", None), relation_target_cols=relation_target_cols_for_contract, join_key_mode=resolved_join_key_mode)
    if contract_violations:
        first = contract_violations[0]
        raise StrategyViolation(error_code=first.error_code, reason=first.reason, violations=contract_violations)

    preset = _build_search_preset(ctx.intent_view())
    lex_w_eff = dict(request.lexical_field_weights) if request.lexical_field_weights is not None else dict(preset.lexical_field_weights)
    sparse_vector_name_eff, _ = _resolve_sparse_vector_name(runtime_sparse_vector_name=request.sparse_vector_name, preset_sparse_vector_name=preset.sparse_vector_name)
    sparse_topk_eff = int(request.sparse_topk or preset.sparse_topk or preset.top_k_lex)
    sparse_weight_eff = float(request.sparse_weight or preset.sparse_weight or preset.w_lex)
    topk_spec = _build_topk_spec(preset, sparse_vector_name=sparse_vector_name_eff, sparse_topk=sparse_topk_eff, sparse_weight=sparse_weight_eff)
    rerank_spec = _build_rerank_spec(plan.mode)
    rerank_spec.setdefault("final_keep", 80)

    compile_runtime = assemble_runtime_compile_policy(
        plan_mode=plan.mode,
        action=ctx.action,
        output_type=getattr(plan, "output_type", None),
        relation=ctx.relation,
        base_route=ctx.base_route,
        planner_mode=planner_mode,
        planner_filter_spec=planner_filter_spec,
        target_cols=list(ctx.target_collections or []),
        fallback_target_cols=list(plan.target_collections or []),
        topk_spec=topk_spec,
        rerank_spec=rerank_spec,
        search_filter_signal=search_filter_signal,
        search_filter_conf_ok=search_filter_conf_ok,
        lookup_filter_policy_hint=getattr(ctx, "lookup_filter_policy_hint", None),
        title_terms=title_terms,
        people_terms=people_terms,
        people_ids=people_ids,
        has_relation_join_ids=runtime.has_relation_join_ids_fn(it),
        title_text_match_supported=bool(getattr(qmodels, "MatchText", None) is not None),
        logger=runtime.logger,
        log_kv=runtime.log_kv_fn,
    )
    compiled_strategy = compile_runtime.compiled_strategy
    lookup_filter_policy = compile_runtime.lookup_filter_policy
    lookup_title_filter_policy = compile_runtime.lookup_title_filter_policy
    title_match_mode = compile_runtime.title_match_mode
    relation_lookup_enforce = compile_runtime.relation_lookup_enforce
    search_filter_enabled = compile_runtime.search_filter_enabled
    lookup_filter_enabled = compile_runtime.lookup_filter_enabled
    join_hop1_lookup_filter_enabled = compile_runtime.join_hop1_lookup_filter_enabled
    search_filter_server_policy = compile_runtime.search_filter_server_policy
    if compile_runtime.people_promote_one_must != people_promote_one_must:
        people_promote_one_must = compile_runtime.people_promote_one_must
        people_filter = build_people_filter(PeopleFilterInput(people_terms=people_terms, person_ids=people_ids, gender_terms=gender_terms, org_terms=people_org_terms, filter_spec=None, min_should=people_min_should, promote_one_must=people_promote_one_must)) if (people_terms or people_ids or gender_terms or people_org_terms) else None

    title_filter = None
    if title_terms and title_match_mode == TITLE_MATCH_MODE_EXACT:
        title_filter = build_title_exact_filter(title_terms)
    elif title_terms and title_match_mode == TITLE_MATCH_MODE_TEXT:
        title_filter = build_title_text_filter(title_terms)
    title_filter_server_applied = bool(title_filter and plan.mode == "lookup" and lookup_filter_enabled and title_match_mode in (TITLE_MATCH_MODE_EXACT, TITLE_MATCH_MODE_TEXT) and search_filter_conf_ok)

    filter_spec = {
        **dict(compiled_strategy.filter_spec or {}),
        "title_match_mode": title_match_mode,
        "search_filter_server_policy": search_filter_server_policy,
        "search_filter_server_applied": False,
        "title_filter_server_applied": bool(title_filter_server_applied),
    }
    if runtime.diff_filter_spec_fn(planner_filter_spec=planner_filter_spec, executed_filter_spec=filter_spec).get("changed", {}):
        strategy_consistency_or_violation(strict=True, mismatch_kind="filter_spec", planner_value=planner_filter_spec, executed_value=filter_spec, log_kv=runtime.log_kv_fn, context={"phase": "compile"})

    strategy = StrategySpec(mode=plan.mode, action=ctx.action, relation=ctx.relation, join_key_mode=resolved_join_key_mode, people_terms=tuple(people_terms or []), target_collections=tuple(compiled_strategy.target_cols or tuple(ctx.target_collections or [])), search_filter_enabled=bool(search_filter_enabled), lookup_filter_enabled=bool(lookup_filter_enabled), relation_lookup_enforce=bool(relation_lookup_enforce), lookup_filter_policy=lookup_filter_policy, lookup_filter_min_should=people_min_should, lookup_filter_gate=people_match_mode, lookup_filter_promote_one_must=people_promote_one_must, lookup_title_filter_policy=lookup_title_filter_policy, title_match_mode=title_match_mode, search_filter_server_policy=search_filter_server_policy)
    plan = replace(plan, relation=ctx.relation, join_key_mode=resolved_join_key_mode, target_collections=tuple(compiled_strategy.target_cols or tuple(ctx.target_collections or [])), filters=filter_spec)
    ctx.plan = plan
    ctx.strategy = strategy

    mode = str(strategy.mode or plan.mode or "").strip().lower()
    relation = strategy.relation
    target_collections = list(strategy.target_collections or plan.target_collections or ())
    strategy_must_match_or_violation(mismatch_kind="mode", planner_value=planner_mode_locked, executed_value=mode, log_kv=runtime.log_kv_fn, context={"phase": "execution"})
    strategy_must_match_or_violation(mismatch_kind="relation", planner_value=planner_relation_locked, executed_value=relation, log_kv=runtime.log_kv_fn, context={"phase": "execution"})
    strategy_must_match_or_violation(mismatch_kind="target_cols", planner_value=planner_target_cols_locked, executed_value=normalize_strategy_target_cols(target_collections), log_kv=runtime.log_kv_fn, context={"phase": "execution"})

    policy_topk = dict(compiled_strategy.topk_spec or {})
    topk_dense = int(policy_topk.get("top_k_dense", preset.top_k_dense))
    topk_lex_cand = int(policy_topk.get("top_k_lex_cand", preset.top_k_lex_cand))
    topk_lex = int(policy_topk.get("top_k_lex", sparse_topk_eff))
    use_dense_threshold_policy = bool(policy_topk.get("use_dense_threshold", preset.use_dense_threshold))
    min_dense_score_policy = float(policy_topk.get("min_dense_score", preset.min_dense_score))

    return build_runtime_prelude_result(query_text=q, keywords=kws, intent_item=it, context_state=ctx, plan=plan, strategy=strategy, mode=mode, action=ctx.action, base_route=ctx.base_route, relation=relation, target_collections=list(target_collections or []), planner_limit=int(planner_limit or 0), hinted_limit=hinted_limit, compiled_strategy=compiled_strategy, planner_filter_spec=dict(planner_filter_spec or {}), resolved_join_key_mode=resolved_join_key_mode, planner_raw_join_key_mode=getattr(ctx, "join_key_mode", None), preset=preset, lex_w_eff=dict(lex_w_eff or {}), sparse_vector_name_eff=sparse_vector_name_eff, sparse_topk_eff=int(sparse_topk_eff), sparse_weight_eff=float(sparse_weight_eff), topk_spec=dict(compiled_strategy.topk_spec or {}), rerank_spec=dict(compiled_strategy.rerank_spec or {}), topk_dense=int(topk_dense), topk_lex_cand=int(topk_lex_cand), topk_lex=int(topk_lex), use_dense_threshold_policy=bool(use_dense_threshold_policy), min_dense_score_policy=float(min_dense_score_policy), title_terms=list(title_terms or []), title_match_mode=str(title_match_mode or ""), title_filter=title_filter, title_filter_server_applied=bool(title_filter_server_applied), lookup_title_filter_policy=str(lookup_title_filter_policy or ""), lookup_filter_policy=str(lookup_filter_policy or ""), search_filter_signal=bool(search_filter_signal), search_filter_conf_ok=bool(search_filter_conf_ok), search_filter_enabled=bool(search_filter_enabled), lookup_filter_enabled=bool(lookup_filter_enabled), relation_lookup_enforce=bool(relation_lookup_enforce), join_hop1_lookup_filter_enabled=bool(join_hop1_lookup_filter_enabled), search_filter_server_policy=str(search_filter_server_policy or ""), org_terms=list(org_terms or []), org_role=org_role, people_terms=list(people_terms or []), people_ids=list(people_ids or []), gender_terms=list(gender_terms or []), people_org_terms=list(people_org_terms or []), people_min_should=people_min_should, people_match_mode=people_match_mode, people_promote_one_must=bool(people_promote_one_must), people_filter=people_filter, participant_org_filter=participant_org_filter, org_filter=org_filter, planner_org_filter_present=bool(planner_org_filter_present), project_tag_filter=project_tag_filter, perf_tag_filter=perf_tag_filter, year_range_filter=year_range_filter, perf_type_filter=perf_type_filter)
