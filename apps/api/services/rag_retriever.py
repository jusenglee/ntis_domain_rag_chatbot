"""RAG retrieval helpers for execution-layer query dispatch and shaping."""

from __future__ import annotations

from typing import Any, Dict, Optional
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from apps.core.rag_pipeline import run_rag_ab_compare
from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import IntentPayloadV2

from apps.api.services.context_helpers import resolve_title_from_payload
from apps.api.contracts.runtime_contracts import friendly_strategy_violation_message


def _get_normalized_intent(state: Any) -> Any:
    """state나 intent payload에서 실제 `normalized_intent` 객체를 꺼내온다.
    wrapper shape가 다른 state·payload에서 공통으로 의도 정보를 읽기 위한 엔트리 헬퍼다.
    """
    payload = getattr(state, "intent_payload", None)
    return getattr(payload, "normalized_intent", None) if payload else None


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """여러 후보 source에서 특정 속성을 차례로 찾아 첫 값을 반환한다.
    knowledge sufficiency, normalized intent, question analysis가 같은 필드를 공유할 때 우선순위를 주고 함께 읽게 한다.
    """
    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            value = source.get(key)
        else:
            value = getattr(source, key, None)
        if value is not None:
            return value
    return default


class CustomRAGRetriever(BaseModel):
    """LangChain/서비스 측에서 일관된 RAG 조회 엔트리로 쓸 간단한 retriever 어댑터다.
    AB 비교 결과에서 히트, aggregation, canonical evidence, render profile를 꺼내 외부 소비자가 읽기 쉬운 shape로 바꾼다.
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    model_name: str = "gemma_triton_0"
    top_k: int = 5
    intent_payload: Optional[IntentPayloadV2] = None

    @staticmethod
    def _infer_tag_from_hit_data(hit_data: Dict[str, Any], intent_payload: Optional[IntentPayloadV2] = None) -> Optional[str]:
        """히트 payload와 intent target collection을 바탕으로 문서 태그를 추정한다.
        payload에 태그가 없어도 project/perf 계열 템플릿을 맞게 렌더할 수 있도록 보조 태그를 만든다.
        """
        tag = hit_data.get("tag")
        if tag:
            return str(tag)

        collection = str(hit_data.get("_collection") or "").strip().lower()
        if collection.startswith("ntis_project_v1"):
            return "IRD_NAI_PJT_INFO"
        if collection.startswith("ntis_perf_v1"):
            return "IRD_NAI_RI_PAPER"

        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        target_cols = getattr(normalized_intent, "target_cols", None) if normalized_intent else None
        if isinstance(target_cols, list):
            lowered = [str(c).strip().lower() for c in target_cols]
            if any(c.startswith("ntis_project_v1") for c in lowered):
                return "IRD_NAI_PJT_INFO"
            if any(c.startswith("ntis_perf_v1") for c in lowered):
                return "IRD_NAI_RI_PAPER"

        return None

    @staticmethod
    def _has_minimum_document_fields(hit_data: Dict[str, Any]) -> bool:
        """히트가 외부 문서 뷰로 내보낼 최소한의 정보를 가졌는지 검사한다.
        title·content·meta·nested member 중 하나라도 의미 있는 값이 없으면 retriever 응답에서 제외한다.
        """
        candidates = [
            hit_data.get("doc_id"),
            hit_data.get("title"),
            hit_data.get("title_text"),
            hit_data.get("title1"),
            hit_data.get("content"),
            hit_data.get("meta_basic"),
            hit_data.get("meta_detail"),
            hit_data.get("prtcp_mp"),
            hit_data.get("prtcp_org"),
        ]
        for val in candidates:
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            return True
        return False

    @staticmethod
    def _build_rag_intent_payload(intent_payload: Optional[IntentPayloadV2]) -> Optional[Dict[str, Any]]:
        """`IntentPayloadV2`에서 RAG runtime이 직접 쓸 payload 뷰만 추출한다.
        normalized intent가 올바른 타입일 때만 넘기며, 아니면 retriever가 planner/runtime contract 바깥 shape를 집어넣지 않게 한다.
        """
        if intent_payload is None:
            return None
        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        if not isinstance(normalized_intent, NormalizedIntent):
            return None
        return {"normalized_intent": normalized_intent}

    def retrieve(self, query: str) -> Dict[str, Any]:
        """AB 비교 RAG 실행 결과에서 사용자가 보기 쉬운 documents/canonical_evidence/render_profile 구조를 만든다.
        aggregation rank_items와 일반 hit 경로를 구분해 서비스 뷰에 맞는 열린 dict 형태로 재포장한다.
        """
        res_map = run_rag_ab_compare(
            query=query,
            model_name=self.model_name,
            intent_payload=self._build_rag_intent_payload(self.intent_payload),
        )
        res_m = res_map.get("M") or res_map.get("A") or next(iter(res_map.values()))

        hits = getattr(res_m, "reranked_hits", []) or []
        aggregation = getattr(res_m, "aggregation", None) or {}
        canonical_evidence = getattr(res_m, "canonical_evidence", None) or []
        render_profile = getattr(res_m, "render_profile", None) or {}
        timings = getattr(res_m, "timings", None) or {}
        normalized_intent = getattr(self.intent_payload, "normalized_intent", None)
        no_result_message = None
        if not hits and str(timings.get("info.contract_fail_reason") or "").strip().lower() == "no_reranked":
            mode = str(getattr(normalized_intent, "mode", "") or "").strip().upper()
            action = str(getattr(normalized_intent, "action", "") or "").strip().lower()
            if mode in {"LOOKUP", "JOIN"}:
                no_result_message = friendly_strategy_violation_message(
                    error_code="RAG_EMPTY_RESULT_CONTRACT",
                    reason="reranked result violated contract(reason=no_reranked)",
                    question_analysis=SimpleNamespace(
                        mode=mode,
                        action=action,
                        ids_map=dict(getattr(normalized_intent, "ids_map", {}) or {}),
                    ),
                )

        rank_items = aggregation.get("rank_items") if isinstance(aggregation, dict) else None
        if isinstance(rank_items, list) and rank_items:
            agg_metric = str(aggregation.get("metric") or "project_participation_count")
            agg_candidate_docs = int(aggregation.get("candidate_docs") or 0)
            agg_window_years = aggregation.get("window_years") or {}
            documents = []
            for idx, item in enumerate(rank_items[: self.top_k], start=1):
                documents.append(
                    {
                        "title": f"{idx}. {item.get('hm_nm') or item.get('hm_id') or item.get('person_key')}",
                        "source_index": idx,
                        "source_type": "aggregation",
                        "metric": agg_metric,
                        "window_years": agg_window_years,
                        "candidate_docs": agg_candidate_docs,
                        "rank_item": dict(item),
                    }
                )
            return {
                "documents": documents,
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
            }

        if not hits:
            return {
                "documents": [],
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
            }

        documents = []
        for idx, hit in enumerate(hits[: self.top_k], start=1):
            if hasattr(hit, "payload"):
                hit_data = hit.payload
            elif isinstance(hit, dict):
                hit_data = hit
            else:
                hit_data = getattr(hit, "__dict__", {})

            inferred_tag = self._infer_tag_from_hit_data(hit_data, self.intent_payload)
            rag_data = {
                "title": resolve_title_from_payload(hit_data),
                "source_index": idx,
                "source_type": "hit",
                "tag": inferred_tag,
                "doc_id": hit_data.get("doc_id"),
                "_collection": hit_data.get("_collection"),
                "meta_basic": hit_data.get("meta_basic", {}),
                "meta_detail": hit_data.get("meta_detail", {}),
                "prtcp_mp": hit_data.get("prtcp_mp", []),
                "prtcp_org": hit_data.get("prtcp_org", []) or [],
                "title_text": hit_data.get("title_text"),
                "title1": hit_data.get("title1"),
                "title2": hit_data.get("title2"),
            }

            if inferred_tag is not None or self._has_minimum_document_fields(hit_data):
                documents.append(rag_data)

        return {
            "documents": documents,
            "canonical_evidence": canonical_evidence,
            "render_profile": render_profile,
            "no_result_message": no_result_message,
        }


def is_hit_source(doc: Dict[str, Any]) -> bool:
    """retriever 뷰 문서가 일반 hit source인지 여부를 판별한다.
    aggregation 결과와 hit 결과를 소비 측에서 쉽게 구분하는 짧은 헬퍼다.
    """
    return doc.get("source_type", "hit") == "hit"


def resolve_rag_queries(*, state: Any, qa: Any, ks: Any, min_confidence: float) -> tuple[str, str, str, float]:
    """raw query, retrieval hint query, 실제 search query와 confidence를 함께 계산한다.
    knowledge sufficiency나 question analysis가 내놓은 retrieval_query를 바로 쓸지, 원문 query로 돌아갈지를 confidence gate로 결정한다.
    """
    raw_query = state.question
    normalized_intent = _get_normalized_intent(state)
    hint_query = _pick_attr(ks, normalized_intent, qa, key="retrieval_query", default=raw_query)
    ks_confidence = float(ks.confidence) if getattr(ks, "confidence", None) is not None else None
    qa_confidence = float(qa.confidence) if getattr(qa, "confidence", None) is not None else None
    confidence = ks_confidence if ks_confidence is not None else (qa_confidence if qa_confidence is not None else 0.0)
    search_query = hint_query if confidence >= min_confidence else raw_query
    return raw_query, hint_query, search_query, confidence
