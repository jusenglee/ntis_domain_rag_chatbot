from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

from apps.core.rag_base_orchestration import BaseOrchestrationRuntime
from apps.core.rag_join_orchestration import JoinOrchestrationRuntime, PerfFollowupRuntime, resolve_perf_followup_join_ids
from apps.core.rag_runtime_observability import point_summary


@dataclass(frozen=True)
class DispatchRuntimeSupport:
    """dispatch 레이어가 base/join 오케스트레이션을 조립할 때 필요한 callback 들을 묶어 둔다.
    hydration, dense retrieve, rerank, join key resolution, logging 후크를 하나의 구조로 넘기는 dispatch-time DI 컨테이너다.
    """
    hydrate_points_fn: Callable[..., None]
    get_attr_fn: Callable[..., Any]
    category_to_base_route_fn: Callable[[List[Any]], str]
    normalize_target_collections_fn: Callable[[Any], List[str]]
    coerce_int_fn: Callable[[Any, int], int]
    get_pre_vecs_fn: Callable[[str], Dict[str, Any]]
    resolve_perf_followup_join_ids_fn: Callable[..., List[str]]
    build_join_runtime_fn: Callable[..., JoinOrchestrationRuntime]
    build_base_runtime_fn: Callable[[], BaseOrchestrationRuntime]
    point_summary_fn: Callable[[Any], Dict[str, Any]]


def build_dispatch_runtime_support(
    *,
    qdr: Any,
    resources: Any,
    vector_names: List[str],
    timings: Dict[str, Any],
    fallback_emb: Dict[str, Any],
    use_dense_threshold_policy: bool,
    min_dense_score_policy: float,
    col_project: str,
    col_perf: str,
    col_support: str,
    tag_pjt_info: str,
    tag_pjt_mp: str,
    tag_pjt_org: str,
    rag_collection_allowlist: List[str],
    project_tags_norm: Any,
    perf_tags_norm: Any,
    normalize_tag_value_fn: Callable[..., Any],
    named_vectors_in_collection_fn: Callable[[Any, str], Any],
    get_relation_route_fn: Callable[[Any], Any],
    build_tag_only_filter_fn: Callable[[List[str]], Any],
    and_filter_fn: Callable[[Any, Any], Any],
    build_project_id_filter_fn: Callable[[List[str], List[str]], Any],
    build_people_filter_fn: Callable[..., Any],
    build_collection_join_filter_fn: Callable[..., Any],
    with_org_must_gate_fn: Callable[..., Any],
    validate_resolved_join_keys_fn: Callable[..., None],
    retrieve_collections_fn: Callable[..., Any],
    call_dense_retrieve_hybrid_multi_fn: Callable[..., Dict[str, Any]],
    validate_lookup_join_hybrid_metrics_fn: Callable[..., None],
    apply_dense_threshold_fn: Callable[..., None],
    ensure_collection_mark_fn: Callable[[List[Any], str], None],
    resolve_sparse_hits_metric_fn: Callable[..., Any],
    record_col_timings_fn: Callable[..., None],
    rank_source_factory: Callable[..., Any],
    use_dense_score_weight_fn: Callable[[], bool],
    dense_score_weight_fn: Callable[[List[Any]], float],
    rrf_merge_fn: Callable[..., List[Any]],
    dedup_by_doc_id_fn: Callable[[List[Any]], List[Any]],
    timing_put_fn: Callable[[Dict[str, Any], str, Any], None],
    finalize_rag_result_fn: Callable[..., Any],
    resolve_env_topn_fn: Callable[..., int],
    prepare_title_post_rerank_fn: Callable[..., Dict[str, Any]],
    final_rerank_fn: Callable[..., List[Any]],
    resolve_effective_min_reranked_fn: Callable[..., Any],
    has_explicit_identifiers_fn: Callable[[Any], bool],
    enforce_reranked_contract_fn: Callable[..., Any],
    hydrate_reranked_payloads_fn: Callable[..., None],
    soft_title_contains_fn: Callable[..., bool],
    aggregation_builder_fn: Callable[..., Optional[Dict[str, Any]]],
    payload_get_fn: Callable[..., Any],
    hit_key_fn: Callable[..., Any],
    title_match_mode_contains: str,
    serialize_filter_for_log_fn: Callable[[Any], Any],
    diff_filter_spec_fn: Callable[..., Dict[str, Any]],
    merge_log_fields_fn: Callable[[dict, dict], dict],
    extract_join_keys_fn: Callable[..., Any],
    resolve_group_pjt_ids_fn: Callable[..., List[str]],
    count_missing_join_keys_fn: Callable[..., Dict[str, int]],
    debug_force_join_keys_enabled_fn: Callable[[], bool],
    get_meta_fn: Callable[[dict], dict],
    pick_first_fn: Callable[..., str],
    people_filter_input_factory: Callable[..., Any],
    join_filter_input_factory: Callable[..., Any],
    context_builder_fn: Callable[..., Any],
    logger_obj: Any,
    log_kv_fn: Callable[..., None],
    log_section_fn: Callable[..., None],
    log_top_points_fn: Callable[..., None],
    point_summary_get_meta_fn: Callable[[dict], dict],
    point_summary_resolve_collection_fn: Callable[[Any, dict], str],
    precomputed_embedding_cls: type,
    series_builder_fn: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
) -> DispatchRuntimeSupport:
    """dispatch 레이어에서 쓸 runtime helper들을 현재 자원과 설정으로 조립한다.
    join/base orchestration이 공통으로 쓸 포인트 hydration, dense precompute, collection normalization, perf follow-up runtime를 이 함수에서 묶어 만든다.
    """
    pre_vecs_cache: Dict[str, Dict[str, Any]] = {}

    def hydrate_points(points: Iterable[Any], *, chunk_size: int = 128) -> None:
        """point 목록에 부족한 payload를 원천 Qdrant 조회로 보충한다.
        rerank나 context builder가 원하는 canonical tag field를 통일하기 위해 hydration runtime에 위임한다.
        """
        from apps.core.rag_hydration_runtime import hydrate_points_payload

        hydrate_points_payload(
            qdr,
            list(points or []),
            normalize_tag_value=normalize_tag_value_fn,
            project_tags_norm=project_tags_norm,
            perf_tags_norm=perf_tags_norm,
            col_project=col_project,
            col_perf=col_perf,
            tag_pjt_info=tag_pjt_info,
            tag_pjt_mp=tag_pjt_mp,
            tag_pjt_org=tag_pjt_org,
            rag_collection_allowlist=rag_collection_allowlist,
            chunk_size=chunk_size,
        )

    def get_attr(obj: Any, name: str, default=None):
        """dict와 object 양쪽에서 같은 이름의 속성을 가져오는 어댑터다.
        point·request·meta 구조가 조금씩 다를 때 호출자가 형태 차이를 의식하지 않게 한다.
        """
        if obj is None:
            return default
        if isinstance(obj, dict):
            return obj.get(name, default)
        return getattr(obj, name, default)

    def category_to_base_route(cats: List[Any]) -> str:
        """카테고리 집합을 하나의 base route 이름으로 접어 말한다.
        plural alias와 enum-style name을 흡수해 project/perf/people/org/support/mixed 중 하나로 고정한다.
        """
        norm = set()
        for cat in (cats or []):
            text = str(cat).replace("ContentCategory.", "").strip().lower()
            norm.add(text)
        if norm == {"project"}:
            return "project"
        if norm in ({"perf"}, {"performance"}):
            return "perf"
        if norm in ({"people"}, {"researcher"}):
            return "people"
        if norm in ({"org"}, {"organization"}, {"institution"}):
            return "org"
        if norm in ({"qna"}, {"qnt"}, {"qna "}, {"q&a"}):
            return "support"
        return "mixed"

    def normalize_target_collections(raw: Any) -> List[str]:
        """route·enum·실제 collection 이름이 섞인 입력을 runtime collection 목록으로 정리한다.
        allowlist 내 이름은 중복 없이 정리하고, 받은 순서도 가능한 한 유지해 후속 retrieval 순서와 로그가 어긋나지 않게 한다.
        """
        if raw is None:
            return []
        items = raw
        if isinstance(items, str):
            items = [x.strip() for x in items.split(",") if x.strip()] or [items]
        if not isinstance(items, (list, tuple, set)):
            items = [items]
        col_map = {"project": col_project}
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
            elif key_lower == col_project.lower():
                col = col_project
            elif key_lower == col_perf.lower():
                col = col_perf
            elif key_lower == col_support.lower():
                col = col_support
            if col:
                if col not in seen:
                    seen.add(col)
                    out.append(col)
            elif key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def coerce_int(value: Any, default: int) -> int:
        """임의의 값을 정수로 강제하되 실패 시 default를 쓴다.
        env/settings에서 들어온 문자열 값이 올바르지 않아도 dispatch runtime가 죽지 않게 하는 보조 헬퍼다.
        """
        try:
            return int(value)
        except Exception:
            return default

    def get_pre_vecs(qtext: str) -> Dict[str, Any]:
        """질의문에 대한 dense embedding을 미리 계산하고 캐시한다.
        join/base runtime이 같은 질의문으로 여러 컬렉션을 돌 때 embedding 계산을 반복하지 않게 한다.
        """
        if qtext in pre_vecs_cache:
            return pre_vecs_cache[qtext]
        try:
            out: Dict[str, Any] = {}
            if "e5i_qa" in vector_names:
                vec = resources.embed_e5i.get_query_embedding(qtext) if hasattr(resources.embed_e5i, "get_query_embedding") else resources.embed_e5i.get_text_embedding(qtext)
                if hasattr(vec, "tolist"):
                    vec = vec.tolist()
                out["e5i_qa"] = precomputed_embedding_cls(list(vec))
            if "e5_qa" in vector_names:
                vec = resources.embed_e5.get_query_embedding(qtext) if hasattr(resources.embed_e5, "get_query_embedding") else resources.embed_e5.get_text_embedding(qtext)
                if hasattr(vec, "tolist"):
                    vec = vec.tolist()
                out["e5_qa"] = precomputed_embedding_cls(list(vec))
        except Exception as exc:
            timing_put_fn(timings, "event.embed_precompute_error", 1.0)
            logger_obj.warning(f"[RAG] embed precompute failed (q='{qtext[:40]}'): {exc}")
            out = {}
        pre_vecs_cache[qtext] = out
        return out

    def _point_summary(point: Any) -> Dict[str, Any]:
        """관측성 로그에 쓸 point summary 생성을 현재 runtime callback에 맞게 감싼다.
        meta resolver와 collection resolver를 외부에서 주입받아 로그 shape를 조립 지점에서 일관되게 유지한다.
        """
        return point_summary(point, get_meta_fn=point_summary_get_meta_fn, resolve_collection_fn=point_summary_resolve_collection_fn)

    def resolve_perf_followup_join_ids_for_request(*, request: Any, preset: Any) -> List[str]:
        """project base retrieval 결과에서 perf follow-up join key를 추출하는 runtime를 조립해 실행한다.
        strict JOIN이 아니라도 project -> perf follow-up 경로를 실행할 수 있도록 필요 부품을 한 곳에서 묶어 넘긴다.
        """
        return resolve_perf_followup_join_ids(
            request=request,
            runtime=PerfFollowupRuntime(
                qdr=qdr,
                preset=preset,
                log_kv=log_kv_fn,
                get_relation_route_fn=get_relation_route_fn,
                build_tag_only_filter_fn=build_tag_only_filter_fn,
                and_filter_fn=and_filter_fn,
                named_vectors_in_collection_fn=named_vectors_in_collection_fn,
                get_pre_vecs_fn=get_pre_vecs,
                call_dense_retrieve_hybrid_multi_fn=call_dense_retrieve_hybrid_multi_fn,
                apply_dense_threshold_fn=lambda sr, **kwargs: apply_dense_threshold_fn(sr, use_dense_threshold=use_dense_threshold_policy, min_dense_score=min_dense_score_policy, **kwargs),
                ensure_collection_mark_fn=ensure_collection_mark_fn,
                rank_source_factory=rank_source_factory,
                use_dense_score_weight_fn=use_dense_score_weight_fn,
                dense_score_weight_fn=dense_score_weight_fn,
                rrf_merge_fn=rrf_merge_fn,
                dedup_by_doc_id_fn=dedup_by_doc_id_fn,
                final_rerank_fn=final_rerank_fn,
                hydrate_points_fn=hydrate_points,
                count_missing_join_keys_fn=count_missing_join_keys_fn,
                debug_force_join_keys_enabled_fn=debug_force_join_keys_enabled_fn,
                get_meta_fn=get_meta_fn,
                pick_first_fn=pick_first_fn,
                extract_join_keys_fn=extract_join_keys_fn,
            ),
        )

    def build_join_runtime(*, preset: Any) -> JoinOrchestrationRuntime:
        """JOIN 오케스트레이션이 필요로 하는 callback들을 주입해 runtime 객체를 만든다.
        dense hybrid retrieve, join key validation, rerank, hydration, debug logging까지 JOIN 전용 의존성을 한 지점에서 결합한다.
        """
        return JoinOrchestrationRuntime(
            qdr=qdr,
            preset=preset,
            log_kv=log_kv_fn,
            log_top_points=log_top_points_fn,
            log_section=log_section_fn,
            timing_put_fn=timing_put_fn,
            point_summary_fn=_point_summary,
            named_vectors_in_collection_fn=named_vectors_in_collection_fn,
            get_pre_vecs_fn=get_pre_vecs,
            call_dense_retrieve_hybrid_multi_fn=call_dense_retrieve_hybrid_multi_fn,
            validate_lookup_join_hybrid_metrics_fn=validate_lookup_join_hybrid_metrics_fn,
            apply_dense_threshold_fn=lambda sr, **kwargs: apply_dense_threshold_fn(sr, use_dense_threshold=use_dense_threshold_policy, min_dense_score=min_dense_score_policy, **kwargs),
            ensure_collection_mark_fn=ensure_collection_mark_fn,
            rank_source_factory=rank_source_factory,
            use_dense_score_weight_fn=use_dense_score_weight_fn,
            dense_score_weight_fn=dense_score_weight_fn,
            rrf_merge_fn=rrf_merge_fn,
            dedup_by_doc_id_fn=dedup_by_doc_id_fn,
            final_rerank_fn=final_rerank_fn,
            hydrate_points_fn=hydrate_points,
            count_missing_join_keys_fn=count_missing_join_keys_fn,
            debug_force_join_keys_enabled_fn=debug_force_join_keys_enabled_fn,
            get_meta_fn=get_meta_fn,
            pick_first_fn=pick_first_fn,
            build_people_filter_fn=build_people_filter_fn,
            people_filter_input_factory=people_filter_input_factory,
            build_project_id_filter_fn=build_project_id_filter_fn,
            build_collection_join_filter_fn=build_collection_join_filter_fn,
            join_filter_input_factory=join_filter_input_factory,
            build_tag_only_filter_fn=build_tag_only_filter_fn,
            and_filter_fn=and_filter_fn,
            with_org_must_gate_fn=with_org_must_gate_fn,
            serialize_filter_for_log_fn=serialize_filter_for_log_fn,
            diff_filter_spec_fn=diff_filter_spec_fn,
            merge_log_fields_fn=merge_log_fields_fn,
            extract_join_keys_fn=extract_join_keys_fn,
            resolve_group_pjt_ids_fn=resolve_group_pjt_ids_fn,
            validate_resolved_join_keys_fn=validate_resolved_join_keys_fn,
            get_relation_route_fn=get_relation_route_fn,
            context_builder=context_builder_fn,
            series_builder_fn=series_builder_fn,
            payload_get_fn=payload_get_fn,
        )

    def build_base_runtime() -> BaseOrchestrationRuntime:
        """비-JOIN base orchestration에 필요한 runtime callback과 정책을 조립한다.
        collection retrieval, rerank contract enforcement, aggregation builder, context assembly가 같은 계약으로 소비되게 맞춘다.
        """
        return BaseOrchestrationRuntime(
            qdr=qdr,
            logger=logger_obj,
            retrieve_collections_fn=retrieve_collections_fn,
            get_relation_route_fn=get_relation_route_fn,
            build_tag_only_filter_fn=build_tag_only_filter_fn,
            and_filter_fn=and_filter_fn,
            build_project_id_filter_fn=build_project_id_filter_fn,
            with_org_must_gate_fn=with_org_must_gate_fn,
            named_vectors_in_collection_fn=named_vectors_in_collection_fn,
            call_dense_retrieve_hybrid_multi_fn=call_dense_retrieve_hybrid_multi_fn,
            validate_lookup_join_hybrid_metrics_fn=validate_lookup_join_hybrid_metrics_fn,
            apply_dense_threshold_fn=lambda sr, **kwargs: apply_dense_threshold_fn(sr, use_dense_threshold=use_dense_threshold_policy, min_dense_score=min_dense_score_policy, **kwargs),
            ensure_collection_mark_fn=ensure_collection_mark_fn,
            log_kv_fn=log_kv_fn,
            log_section_fn=log_section_fn,
            log_top_points_fn=log_top_points_fn,
            serialize_filter_for_log_fn=serialize_filter_for_log_fn,
            resolve_sparse_hits_metric_fn=resolve_sparse_hits_metric_fn,
            record_col_timings_fn=record_col_timings_fn,
            rank_source_factory=rank_source_factory,
            use_dense_score_weight_fn=use_dense_score_weight_fn,
            dense_score_weight_fn=dense_score_weight_fn,
            rrf_merge_fn=rrf_merge_fn,
            dedup_by_doc_id_fn=dedup_by_doc_id_fn,
            timing_put_fn=timing_put_fn,
            finalize_rag_result_fn=finalize_rag_result_fn,
            resolve_env_topn_fn=resolve_env_topn_fn,
            prepare_title_post_rerank_fn=prepare_title_post_rerank_fn,
            final_rerank_fn=final_rerank_fn,
            resolve_effective_min_reranked_fn=resolve_effective_min_reranked_fn,
            has_explicit_identifiers_fn=has_explicit_identifiers_fn,
            enforce_reranked_contract_fn=enforce_reranked_contract_fn,
            hydrate_reranked_payloads_fn=hydrate_reranked_payloads_fn,
            coerce_int_fn=coerce_int,
            get_attr_fn=get_attr,
            hydrate_points_payload_fn=hydrate_points,
            soft_title_contains_fn=soft_title_contains_fn,
            aggregation_builder_fn=aggregation_builder_fn,
            series_builder_fn=series_builder_fn,
            payload_get_fn=payload_get_fn,
            hit_key_fn=hit_key_fn,
            title_match_mode_contains=title_match_mode_contains,
        )

    return DispatchRuntimeSupport(
        hydrate_points_fn=hydrate_points,
        get_attr_fn=get_attr,
        category_to_base_route_fn=category_to_base_route,
        normalize_target_collections_fn=normalize_target_collections,
        coerce_int_fn=coerce_int,
        get_pre_vecs_fn=get_pre_vecs,
        resolve_perf_followup_join_ids_fn=resolve_perf_followup_join_ids_for_request,
        build_join_runtime_fn=build_join_runtime,
        build_base_runtime_fn=build_base_runtime,
        point_summary_fn=_point_summary,
    )
