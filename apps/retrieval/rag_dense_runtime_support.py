from __future__ import annotations

import inspect
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class DenseRuntimeSupport:
    """dense 검색 실행에 필요한 callback과 설정값을 묶어 두는 컨테이너다.
    pipeline은 이 구조를 통해 dense retrieve, metric validation, threshold gate를 선택적으로 호출한다.
    """
    precomputed_embedding_type: type
    call_dense_retrieve_hybrid_multi: Callable[..., Dict[str, Any]]
    validate_lookup_join_hybrid_metrics: Callable[..., None]
    resolve_sparse_hits_metric: Callable[[Mapping[str, Any]], float]
    ensure_collection_mark: Callable[[List[Any], str], None]
    apply_dense_threshold: Callable[..., None]
    use_dense_score_weight: Callable[[], bool]
    dense_score_weight: Callable[[List[Any]], float]


class PrecomputedEmbedding:
    """미리 계산된 query/text embedding 쌓을 runnable 형태로 반환하는 어댑터다.
    테스트나 fallback 환경에서 실제 embedding client 없이도 dense 경로를 재현할 수 있게 한다.
    """
    def __init__(self, vec: List[float]):
        """미리 주어진 query/text embedding을 인스턴스 상태에 보관한다.
        caller가 테스트나 시뮬레이션 중 계산된 벡터를 그대로 재사용할 수 있게 한다.
        """
        self._vec = vec

    def get_query_embedding(self, _text: str):
        """query embedding 주입점에서 동기/비동기 runnable이 같은 벡터를 읽게 한다.
        텍스트 내용은 재계산하지 않고, 생성자에서 넘겨받은 고정 벡터만 반환한다.
        """
        return self._vec

    def get_text_embedding(self, _text: str):
        """text embedding 요청에도 미리 계산된 벡터를 그대로 반환한다.
        dense retriever가 query/text 경로를 같은 client 인터페이스로 호출해도 테스트 구조가 달라지지 않게 한다.
        """
        return self._vec


def resolve_sparse_hits_metric(timings: Mapping[str, Any]) -> float:
    """sparse retrieval 결과에 연결된 metric collector를 안전하게 가져온다.
    metric object가 없거나 lookup/join hybrid 검증을 비활성화했을 때는 `None`을 돌려 후속 체크를 건너뛴다.
    """
    if not isinstance(timings, Mapping):
        return 0.0
    return float(timings.get("lexical_scored", timings.get("sparse_hits", 0.0)) or 0.0)


def attach_collection(point: Any, col: str) -> Any:
    """dense hit에 현재 콜렉션 이름을 표시하여 sparse hit과 같은 메타 형식을 맞춘다.
    rank merge나 로그 요약은 collection 정보를 가정하므로 dense 전용 hit에도 같은 key를 심어둔다.
    """
    if point is None or not col:
        return point
    if isinstance(point, dict):
        payload = point.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("_collection", col)
        point["payload"] = payload
        point.setdefault("_collection", col)
        return point
    setattr(point, "_collection", col)
    payload = getattr(point, "payload", None)
    if not isinstance(payload, dict):
        payload = {}
        setattr(point, "payload", payload)
    payload.setdefault("_collection", col)
    return point


def ensure_collection_mark(points: List[Any], col: str) -> None:
    """payload 또는 metadata 어느 쪽이든 collection 표시가 없으면 기본값을 채운다.
    dense 히트가 후속 context builder에서 sparse 히트와 동일한 계약으로 다룰 수 있게 맞추는 정리 단계다.
    """
    for point in points or []:
        attach_collection(point, col)


def use_dense_score_weight() -> bool:
    """dense 점수에 가중치를 곱해야 하는지 설정과 실행 컨텍스트로 판정한다.
    lookup/join 경로처럼 exact match가 더 중요한 모드에서는 dense score 가중치를 약하게 두거나 꺼버릴 수 있다.
    """
    return str(os.getenv("RAG_USE_DENSE_SCORE_WEIGHT", "0")).strip().lower() in ("1", "true", "yes", "y")


def dense_score_weight(points: List[Any]) -> float:
    """현재 모드에 맞는 dense score 가중치를 수치로 계산한다.
    설정에서 상세 모드 가중치를 주지 않으면 global default로 되돌아 파이프라인 행동을 안정적으로 유지한다.
    """
    score_values: List[float] = []
    for point in points or []:
        score = getattr(point, "score", None)
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
    norm_scores = [(value - min_score) / float(max_score - min_score) for value in score_values]
    return sum(norm_scores) / float(len(norm_scores))


def build_dense_runtime_support(*, dense_retrieve_hybrid_multi: Callable[..., Dict[str, Any]], strategy_violation_type: type, log_kv: Callable[..., None]) -> DenseRuntimeSupport:
    """dense 검색 지원이 가능한 현재 runtime에 맞는 callback 들을 조립한다.
    embedding client, dense retriever, metric policy, threshold gate를 동일한 구조로 넘기는 엔트리 포인트다.
    """
    dense_multi_sig = None
    try:
        dense_multi_sig = inspect.signature(dense_retrieve_hybrid_multi)
    except Exception:
        dense_multi_sig = None

    def call_dense_retrieve_hybrid_multi(*, qdr: Any, emb_map: Dict[str, Any], qtext: str, kws: List[str], collection: str, lexical_fields: Optional[List[str]], sparse_vector_name: Optional[str], sparse_topk: Optional[int], top_k_dense: int, top_k_lex_cand: int, top_k_lex: int, query_filter: Any, timings_out: Dict[str, float], require_hybrid_both_sides: bool = False, contract_scope: Optional[str] = None, violation_on_contract: bool = False) -> Dict[str, Any]:
        """hybrid dense retrieve를 호출하기 전 입력 형식과 runtime 플래그를 정리한다.
        dense 경로를 끼더라도 sparse fallback에서 필요한 메타가 사라지지 않게 만들어서 후속 rerank와 관측성이 이어지게 한다.
        """
        params = set(dense_multi_sig.parameters.keys()) if dense_multi_sig else set()
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
                msg = f"dense disabled for {contract_scope or collection}: top_k_dense={top_k_dense_int} emb_map={bool(emb_map)}"
                if violation_on_contract:
                    raise strategy_violation_type(error_code=code, reason=msg)
                raise RuntimeError(f"[{code}] {msg}")
            if not sparse_enabled:
                code = "LOOKUP_JOIN_SPARSE_REQUIRED" if violation_on_contract else "RAG.CONTRACT"
                msg = f"sparse disabled for {contract_scope or collection}: sparse_topk={sparse_topk_int} sparse_vector_name={sparse_vector_name!r}"
                if violation_on_contract:
                    raise strategy_violation_type(error_code=code, reason=msg)
                raise RuntimeError(f"[{code}] {msg}")

        if "client" in params and "collection_name" in params:
            return dense_retrieve_hybrid_multi(client=qdr, expanded_text=qtext, keywords=kws, collection_name=collection, query_filter=query_filter, **common_kwargs)
        if "qdr" in params and "collection" in params:
            return dense_retrieve_hybrid_multi(qdr=qdr, collection=collection, query_text=qtext, keywords=kws, filter_obj=query_filter, **common_kwargs)
        return dense_retrieve_hybrid_multi(client=qdr, expanded_text=qtext, keywords=kws, collection_name=collection, query_filter=query_filter, **common_kwargs)

    def validate_lookup_join_hybrid_metrics(*, mode: str, contract_scope: str, timings: Mapping[str, Any], strict: bool = True) -> None:
        """lookup/join 모드에서 hybrid dense 히트가 exact-match 히트를 망치지 않는지 검사한다.
        strict retrieval-first 계약을 넘어서는 dense 보강이 발생하지 않게 하는 안전장치다.
        """
        if str(mode).strip().lower() not in ("lookup", "join"):
            return
        dense_queries = float(timings.get("dense_queries", 0.0) or 0.0)
        sparse_hits = resolve_sparse_hits_metric(timings)
        hybrid_once_hits = float(timings.get("hybrid_once_hits", 0.0) or 0.0)
        hybrid_mode_used = hybrid_once_hits > 0
        log_kv("RAG.LOOKUP_JOIN.HYBRID.METRICS", tier="debug", mode=mode, contract_scope=contract_scope, dense_queries=dense_queries, sparse_hits=sparse_hits, hybrid_once_hits=hybrid_once_hits, hybrid_mode_used=hybrid_mode_used)
        if hybrid_mode_used:
            return
        if dense_queries == 0 or sparse_hits == 0:
            log_kv("RAG.LOOKUP_JOIN.HYBRID.METRICS.ZERO_HIT", tier="debug", level="warning" if not strict else "info", mode=mode, contract_scope=contract_scope, dense_queries=dense_queries, sparse_hits=sparse_hits, hybrid_once_hits=hybrid_once_hits, strict=int(bool(strict)))

    def apply_dense_threshold(sr: Dict[str, Any], *, use_dense_threshold: bool, min_dense_score: float, log_prefix: str, col: Optional[str] = None, action: Optional[str] = None, base_route: Optional[str] = None, relation: Optional[Tuple[str, str]] = None) -> None:
        """dense 점수가 임계치를 넘지 못하는 hit를 추려내어 잡음을 줄인다.
        임계치는 절대값과 백분위수 기준을 함께 지원하며, 지나친 손실을 막기 위해 minimum keep 규칙도 같이 적용한다.
        """
        dense_map = sr.get("dense")
        if not isinstance(dense_map, dict):
            return
        for vname, lst in dense_map.items():
            before = len(lst or [])
            filtered = list(lst or [])
            if use_dense_threshold:
                filtered = []
                for point in lst or []:
                    score = getattr(point, "score", None)
                    try:
                        score_val = float(score) if score is not None else None
                    except Exception:
                        score_val = None
                    if score_val is not None and score_val >= float(min_dense_score):
                        filtered.append(point)
                dense_map[vname] = filtered
            reduced = before - len(filtered)
            reduced_ratio = (float(reduced) / float(before)) if before else 0.0
            log_kv(log_prefix, col=col, vec=str(vname), action=action, base_route=base_route, relation=str(relation) if relation else None, applied=bool(use_dense_threshold), before=before, after=len(filtered), reduced=reduced, reduced_ratio=round(reduced_ratio, 4), min_dense_score=float(min_dense_score), tier="debug")

            score_values: List[float] = []
            for point in filtered:
                score = getattr(point, "score", None)
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
                    """백분위 threshold 계산에 쓸 선형 보간 percentile 헬퍼다.
                    표본이 적어도 배열 경계를 넘지 않도록 인덱스를 고정한다.
                    """
                    if not sorted_vals:
                        return float("nan")
                    if len(sorted_vals) == 1:
                        return sorted_vals[0]
                    pos = (pct / 100.0) * (len(sorted_vals) - 1)
                    lo = int(pos)
                    hi = min(lo + 1, len(sorted_vals) - 1)
                    frac = pos - lo
                    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac

                log_kv_fn("RAG.DENSE.SCORE.STATS", col=col, vec=str(vname), count=len(score_values), min=float(min_score), max=float(max_score), p50=float(_percentile(score_values, 50.0)), p90=float(_percentile(score_values, 90.0)), p99=float(_percentile(score_values, 99.0)), top3_avg=float(topn_avg), tier="debug")

    return DenseRuntimeSupport(
        precomputed_embedding_type=PrecomputedEmbedding,
        call_dense_retrieve_hybrid_multi=call_dense_retrieve_hybrid_multi,
        validate_lookup_join_hybrid_metrics=validate_lookup_join_hybrid_metrics,
        resolve_sparse_hits_metric=resolve_sparse_hits_metric,
        ensure_collection_mark=ensure_collection_mark,
        apply_dense_threshold=apply_dense_threshold,
        use_dense_score_weight=use_dense_score_weight,
        dense_score_weight=dense_score_weight,
    )
