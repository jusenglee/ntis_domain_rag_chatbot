"""RAG retrieval helpers for execution-layer query dispatch and shaping."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from apps.core.rag_pipeline import run_rag_ab_compare
from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import IntentPayloadV3
from apps.core.followup_resolution import build_followup_clarification_message

from apps.api.services.context_helpers import resolve_title_from_payload
from apps.api.services.detail_contract import FIELD_ALIASES, extract_requested_fields
from apps.api.contracts.runtime_contracts import friendly_strategy_violation_message


def _get_normalized_intent(state: Any) -> Any:
    """state??intent payload?먯꽌 ?ㅼ젣 `normalized_intent` 媛앹껜瑜?爰쇰궡?⑤떎.
    wrapper shape媛 ?ㅻⅨ state쨌payload?먯꽌 怨듯넻?쇰줈 ?섎룄 ?뺣낫瑜??쎄린 ?꾪븳 ?뷀듃由??ы띁??
    """
    payload = getattr(state, "intent_payload", None)
    return getattr(payload, "normalized_intent", None) if payload else None


def _pick_attr(*sources: Any, key: str, default: Any = None) -> Any:
    """?щ윭 ?꾨낫 source?먯꽌 ?뱀젙 ?띿꽦??李⑤?濡?李얠븘 泥?媛믪쓣 諛섑솚?쒕떎.
    knowledge sufficiency, normalized intent, question analysis媛 媛숈? ?꾨뱶瑜?怨듭쑀?????곗꽑?쒖쐞瑜?二쇨퀬 ?④퍡 ?쎄쾶 ?쒕떎.
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


def _get_strategy_meta(state: Any) -> Dict[str, Any]:
    payload = getattr(state, "intent_payload", None)
    strategy_meta = getattr(payload, "strategy_meta", None) if payload else None
    return dict(strategy_meta or {})


def _normalize_id_values(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _first_non_empty_text(*values: Any) -> Optional[str]:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _build_anchor_requested_terms(question: Any) -> list[str]:
    requested_fields = extract_requested_fields(str(question or ""))
    preferred_fields = [
        field
        for field in ("researchers", "lead_org", "participant_org", "year")
        if field in requested_fields
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for field in preferred_fields:
        aliases = FIELD_ALIASES.get(field) or []
        term = str(aliases[0] if aliases else "").strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _resolve_followup_anchor_context(state: Any) -> Dict[str, Any]:
    normalized_intent = _get_normalized_intent(state)
    strategy_meta = _get_strategy_meta(state)
    followup_status = str(strategy_meta.get("followup_resolution_status") or "").strip().lower()
    if followup_status != "resolved":
        return {
            "present": False,
            "anchor_source": None,
            "entity_key": None,
            "pjt_id": None,
            "pjt_no": None,
            "title_text": None,
        }

    ids_map = getattr(normalized_intent, "ids_map", None) or {}
    if isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map") or {}
    selected_prev_item = dict(strategy_meta.get("selected_prev_item") or {})
    focus_entity = dict(strategy_meta.get("focus_entity") or {})
    pjt_ids = _normalize_id_values((ids_map or {}).get("pjt_id"))
    pjt_nos = _normalize_id_values((ids_map or {}).get("pjt_no"))
    pjt_id = _first_non_empty_text(*(pjt_ids[:1] or []), selected_prev_item.get("pjt_id"), focus_entity.get("pjt_id"))
    pjt_no = _first_non_empty_text(*(pjt_nos[:1] or []), selected_prev_item.get("pjt_no"), focus_entity.get("pjt_no"))
    title_text = _first_non_empty_text(selected_prev_item.get("title"), focus_entity.get("title_text"))
    entity_key = pjt_id or pjt_no or strategy_meta.get("focus_entity_key")
    return {
        "present": bool(entity_key or title_text),
        "anchor_source": strategy_meta.get("anchor_source") or strategy_meta.get("seed_source"),
        "entity_key": entity_key,
        "pjt_id": pjt_id,
        "pjt_no": pjt_no,
        "title_text": title_text,
    }


def _query_mentions_anchor(query: Any, anchor_context: Dict[str, Any]) -> bool:
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return False
    anchor_tokens = [
        str(anchor_context.get("pjt_id") or "").strip().lower(),
        str(anchor_context.get("pjt_no") or "").strip().lower(),
    ]
    if any(token and token in normalized_query for token in anchor_tokens):
        return True

    title_text = str(anchor_context.get("title_text") or "").strip()
    if not title_text:
        return False
    normalized_title = title_text.lower()
    if normalized_title and normalized_title in normalized_query:
        return True
    title_terms = {term for term in _extract_topic_terms(title_text) if len(term) >= 2}
    query_terms = _extract_topic_terms(query)
    return bool(title_terms and title_terms.intersection(query_terms))


def repair_query_for_resolved_anchor(*, state: Any, query: Any) -> tuple[str, Dict[str, Any]]:
    normalized_intent = _get_normalized_intent(state)
    output_type = str(_pick_attr(normalized_intent, key="output_type", default="") or "").strip().lower()
    action = str(_pick_attr(normalized_intent, key="action", default="") or "").strip().lower()
    anchor_context = _resolve_followup_anchor_context(state)
    metadata = {
        "anchor_present": anchor_context.get("present", False),
        "anchor_source": anchor_context.get("anchor_source"),
        "anchor_entity_key": anchor_context.get("entity_key"),
        "anchor_query_repaired": False,
        "anchor_repair_reason": None,
    }
    if not anchor_context.get("present"):
        return str(query or ""), metadata
    if action != "detail" and output_type != "detail":
        return str(query or ""), metadata
    if _query_mentions_anchor(query, anchor_context):
        return str(query or ""), metadata

    anchor_phrase = _first_non_empty_text(anchor_context.get("title_text"), anchor_context.get("pjt_id"), anchor_context.get("pjt_no"), query) or ""
    requested_terms = _build_anchor_requested_terms(getattr(state, "question", ""))
    parts = [anchor_phrase, *requested_terms]
    repaired_terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        repaired_terms.append(text)
    repaired_query = " ".join(repaired_terms) or str(query or "")
    metadata["anchor_query_repaired"] = repaired_query != str(query or "")
    metadata["anchor_repair_reason"] = "anchor_axis_lost" if metadata["anchor_query_repaired"] else None
    return repaired_query, metadata


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+|[?-?]{2,}")
_GENERIC_QUERY_TERMS = {
    "???",
    "???",
    "??",
    "??",
    "???",
    "??",
    "??",
    "?",
    "?",
    "top",
    "the",
    "and",
    "for",
}
_PROJECT_AXIS_TERMS = {"??", "project", "projects"}
_PERF_AXIS_TERMS = {"??", "output", "outputs", "??", "??", "???"}
_DETAIL_AXIS_TERMS = {"??", "detail", "details"}
_STATS_AXIS_TERMS = {"??", "??", "trend", "trends", "??"}


def _normalize_query_text(text: Any) -> str:
    return str(text or "").strip().lower()


def _extract_query_tokens(text: Any) -> list[str]:
    normalized = _normalize_query_text(text)
    return [token for token in _QUERY_TOKEN_RE.findall(normalized) if token]


def _extract_identifier_like_tokens(text: Any) -> set[str]:
    protected: set[str] = set()
    for token in _extract_query_tokens(text):
        if re.fullmatch(r"\d{4,}", token):
            protected.add(token)
        elif any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token):
            protected.add(token)
    return protected


def _extract_topic_terms(text: Any) -> set[str]:
    return {
        token
        for token in _extract_query_tokens(text)
        if token not in _GENERIC_QUERY_TERMS and not token.isdigit()
    }


def _contains_any_term(terms: set[str], candidates: set[str]) -> bool:
    return bool(terms.intersection(candidates))


def detect_retrieval_query_drift(*, raw_query: Any, hint_query: Any) -> tuple[bool, list[str]]:
    raw = _normalize_query_text(raw_query)
    hint = _normalize_query_text(hint_query)
    if not raw or not hint or raw == hint:
        return False, []

    reasons: list[str] = []
    raw_identifiers = _extract_identifier_like_tokens(raw)
    hint_identifiers = _extract_identifier_like_tokens(hint)
    if raw_identifiers - hint_identifiers:
        reasons.append("missing_identifier_or_year")

    raw_topic_terms = _extract_topic_terms(raw)
    hint_topic_terms = _extract_topic_terms(hint)
    if raw_topic_terms and not raw_topic_terms.intersection(hint_topic_terms):
        reasons.append("topic_terms_lost")

    raw_has_project = _contains_any_term(raw_topic_terms, _PROJECT_AXIS_TERMS)
    raw_has_perf = _contains_any_term(raw_topic_terms, _PERF_AXIS_TERMS)
    hint_has_project = _contains_any_term(hint_topic_terms, _PROJECT_AXIS_TERMS)
    hint_has_perf = _contains_any_term(hint_topic_terms, _PERF_AXIS_TERMS)
    raw_has_detail = _contains_any_term(raw_topic_terms, _DETAIL_AXIS_TERMS)
    hint_has_detail = _contains_any_term(hint_topic_terms, _DETAIL_AXIS_TERMS)
    raw_has_stats = _contains_any_term(raw_topic_terms, _STATS_AXIS_TERMS)
    hint_has_stats = _contains_any_term(hint_topic_terms, _STATS_AXIS_TERMS)

    if not raw_has_perf and hint_has_perf:
        reasons.append("perf_axis_added")
    if raw_has_project and not hint_has_project:
        reasons.append("project_axis_removed")
    if not raw_has_detail and hint_has_detail:
        reasons.append("detail_axis_added")
    if not raw_has_stats and hint_has_stats:
        reasons.append("stats_axis_added")

    deduped_reasons = list(dict.fromkeys(reasons))
    return bool(deduped_reasons), deduped_reasons


class CustomRAGRetriever(BaseModel):
    """LangChain/?쒕퉬??痢≪뿉???쇨???RAG 議고쉶 ?뷀듃由щ줈 ??媛꾨떒??retriever ?대뙌?곕떎.
    AB 鍮꾧탳 寃곌낵?먯꽌 ?덊듃, aggregation, canonical evidence, render profile瑜?爰쇰궡 ?몃? ?뚮퉬?먭? ?쎄린 ?ъ슫 shape濡?諛붽씔??
    """
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
    )

    model_name: str = "gemma_triton_0"
    top_k: int = 5
    intent_payload: Optional[IntentPayloadV3] = None
    request_overrides: Dict[str, Any] = {}

    @staticmethod
    def _infer_tag_from_hit_data(hit_data: Dict[str, Any], intent_payload: Optional[IntentPayloadV3] = None) -> Optional[str]:
        """?덊듃 payload? intent target collection??諛뷀깢?쇰줈 臾몄꽌 ?쒓렇瑜?異붿젙?쒕떎.
        payload???쒓렇媛 ?놁뼱??project/perf 怨꾩뿴 ?쒗뵆由우쓣 留욊쾶 ?뚮뜑?????덈룄濡?蹂댁“ ?쒓렇瑜?留뚮뱺??
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
        """?덊듃媛 ?몃? 臾몄꽌 酉곕줈 ?대낫??理쒖냼?쒖쓽 ?뺣낫瑜?媛議뚮뒗吏 寃?ы븳??
        title쨌content쨌meta쨌nested member 以??섎굹?쇰룄 ?섎? ?덈뒗 媛믪씠 ?놁쑝硫?retriever ?묐떟?먯꽌 ?쒖쇅?쒕떎.
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
    def _build_rag_intent_payload(intent_payload: Optional[IntentPayloadV3]) -> Optional[Dict[str, Any]]:
        """`IntentPayloadV3`?먯꽌 RAG runtime??吏곸젒 ??payload 酉곕쭔 異붿텧?쒕떎.
        normalized intent媛 ?щ컮瑜???낆씪 ?뚮쭔 ?섍린硫? ?꾨땲硫?retriever媛 planner/runtime contract 諛붽묑 shape瑜?吏묒뼱?ｌ? ?딄쾶 ?쒕떎.
        """
        if intent_payload is None:
            return None
        normalized_intent = getattr(intent_payload, "normalized_intent", None)
        if normalized_intent is None:
            return None
        payload_version = getattr(intent_payload, "intent_payload_version", None)
        question_analysis = getattr(intent_payload, "question_analysis", None)
        strategy_meta = getattr(intent_payload, "strategy_meta", None) or {}
        return {"intent_payload_version": payload_version or "v3", "normalized_intent": normalized_intent, "question_analysis": question_analysis, "strategy_meta": dict(strategy_meta)}

    @staticmethod
    def _format_aggregation_title(item: Dict[str, Any], metric: str, index: int) -> str:
        """Build a human-readable title for aggregation rows."""
        if metric == "project_participation_count":
            return f"{index}. {item.get('hm_nm') or item.get('hm_id') or item.get('person_key')}"
        project_title = str(item.get("project_title") or item.get("group_key") or "project").strip()
        metric_value = int(item.get("metric_value") or 0)
        return f"{index}. {project_title} ({metric_value})"


    @staticmethod
    def _format_series_title(item: Dict[str, Any], index: int) -> str:
        """Build a human-readable title for series rows."""
        project_title = str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip()
        year = str(item.get("year") or "").strip()
        return f"{index}. {project_title}" + (f" ({year})" if year else "")

    def retrieve(self, query: str) -> Dict[str, Any]:
        """AB 鍮꾧탳 RAG ?ㅽ뻾 寃곌낵?먯꽌 ?ъ슜?먭? 蹂닿린 ?ъ슫 documents/canonical_evidence/render_profile 援ъ“瑜?留뚮뱺??
        aggregation rank_items? ?쇰컲 hit 寃쎈줈瑜?援щ텇???쒕퉬??酉곗뿉 留욌뒗 ?대┛ dict ?뺥깭濡??ы룷?ν븳??
        """
        strategy_meta = getattr(self.intent_payload, "strategy_meta", None) or {}
        followup_message = build_followup_clarification_message(dict(strategy_meta))
        if followup_message:
            return {
                "documents": [],
                "canonical_evidence": [],
                "render_profile": {},
                "no_result_message": followup_message,
            }

        res_map = run_rag_ab_compare(
            query=query,
            model_name=self.model_name,
            intent_payload=self._build_rag_intent_payload(self.intent_payload),
            request_overrides=dict(self.request_overrides or {}),
        )
        res_m = res_map.get("M") or res_map.get("A") or next(iter(res_map.values()))

        hits = getattr(res_m, "reranked_hits", []) or []
        aggregation = getattr(res_m, "aggregation", None) or {}
        series = getattr(res_m, "series", None) or {}
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

        pattern_analysis = getattr(res_m, "pattern_analysis", None) or {}
        multi_hop_bundle = getattr(res_m, "multi_hop_bundle", None) or {}
        series_items = series.get("instance_projects") if isinstance(series, dict) else None
        if isinstance(series_items, list) and series_items:
            documents = []
            for idx, item in enumerate(series_items[: self.top_k], start=1):
                documents.append({
                    "title": self._format_series_title(item, idx),
                    "source_index": idx,
                    "source_type": "series",
                    "series_key_kind": series.get("series_key_kind"),
                    "series_key": series.get("series_key"),
                    "series_item": dict(item),
                    "year_buckets": list(series.get("year_buckets") or []),
                    "candidate_docs": int(series.get("candidate_docs") or 0),
                })
            return {"documents": documents, "canonical_evidence": canonical_evidence, "render_profile": render_profile, "no_result_message": no_result_message}
        if isinstance(multi_hop_bundle, dict) and str(multi_hop_bundle.get("status") or "").strip().lower() in {"ok", "partial"} and (list(multi_hop_bundle.get("projects") or []) or list(multi_hop_bundle.get("bundles") or [])):
            documents = []
            for idx, project in enumerate(list(multi_hop_bundle.get("projects") or [])[: self.top_k] or [None], start=1):
                title = "multi-hop bundle"
                if isinstance(project, dict):
                    title = str(project.get("project_title") or project.get("pjt_id") or project.get("pjt_no") or title).strip()
                documents.append(
                    {
                        "title": title,
                        "source_index": idx,
                        "source_type": "multi_hop_bundle",
                        "bundle_kind": str(multi_hop_bundle.get("bundle_kind") or "project_outputs"),
                        "projects": list(multi_hop_bundle.get("projects") or []),
                        "bundles": list(multi_hop_bundle.get("bundles") or []),
                        "ambiguities": list(multi_hop_bundle.get("ambiguities") or []),
                        "guidance_message": multi_hop_bundle.get("guidance_message"),
                    }
                )
            return {
                "documents": documents,
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
            }

        if isinstance(pattern_analysis, dict) and str(pattern_analysis.get("status") or "").strip().lower() in {"ok", "partial"} and list(pattern_analysis.get("items") or []):
            documents = []
            pattern_kind = str(pattern_analysis.get("pattern_kind") or "pattern_analysis")
            for idx, item in enumerate(list(pattern_analysis.get("items") or [])[: self.top_k], start=1):
                title = pattern_kind
                if pattern_kind == "coauthor_org_repeat":
                    title = f"{idx}. {item.get('org_name')} ({item.get('repeated_author_count', 0)})"
                elif pattern_kind == "perf_mix_gap":
                    title = f"{idx}. {item.get('project_title') or item.get('group_key')}"
                elif pattern_kind == "series_member_change":
                    title = f"{idx}. {item.get('project_title') or item.get('year') or 'series_change'}"
                documents.append(
                    {
                        "title": title,
                        "source_index": idx,
                        "source_type": "pattern_analysis",
                        "pattern_kind": pattern_kind,
                        "pattern_item": dict(item),
                        "candidate_docs": int(pattern_analysis.get("candidate_docs") or 0),
                        "subject_count": int(pattern_analysis.get("subject_count") or 0),
                        "support_doc_count": int(pattern_analysis.get("support_doc_count") or 0),
                    }
                )
            return {
                "documents": documents,
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
            }

        reverse_trace = getattr(res_m, "reverse_trace", None) or {}
        if isinstance(reverse_trace, dict) and (reverse_trace.get("origin_projects") or reverse_trace.get("followup_perf") or reverse_trace.get("origin_perf")):
            documents = []
            for idx, item in enumerate(list(reverse_trace.get("origin_projects") or [])[: self.top_k], start=1):
                documents.append(
                    {
                        "title": str(item.get("project_title") or item.get("pjt_id") or item.get("pjt_no") or "project").strip(),
                        "source_index": idx,
                        "source_type": "reverse_trace",
                        "relation_chain": list(reverse_trace.get("relation_chain") or []),
                        "origin_perf": list(reverse_trace.get("origin_perf") or []),
                        "origin_project": dict(item),
                        "followup_perf": list(reverse_trace.get("followup_perf") or []),
                    }
                )
            if not documents:
                documents.append(
                    {
                        "title": "reverse trace",
                        "source_index": 1,
                        "source_type": "reverse_trace",
                        "relation_chain": list(reverse_trace.get("relation_chain") or []),
                        "origin_perf": list(reverse_trace.get("origin_perf") or []),
                        "origin_project": None,
                        "followup_perf": list(reverse_trace.get("followup_perf") or []),
                    }
                )
            return {
                "documents": documents,
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
            }

        rank_items = aggregation.get("rank_items") if isinstance(aggregation, dict) else None
        if isinstance(rank_items, list) and rank_items:
            agg_metric = str(aggregation.get("metric") or "project_participation_count")
            agg_candidate_docs = int(aggregation.get("candidate_docs") or 0)
            agg_window_years = aggregation.get("window_years") or {}
            agg_group_by = str(aggregation.get("group_by") or "").strip() or None
            agg_threshold = aggregation.get("threshold")
            agg_sort_order = aggregation.get("sort_order")
            documents = []
            for idx, item in enumerate(rank_items[: self.top_k], start=1):
                documents.append(
                    {
                        "title": self._format_aggregation_title(item, agg_metric, idx),
                        "source_index": idx,
                        "source_type": "aggregation",
                        "metric": agg_metric,
                        "group_by": agg_group_by,
                        "threshold": agg_threshold,
                        "sort_order": agg_sort_order,
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
    """retriever 酉?臾몄꽌媛 ?쇰컲 hit source?몄? ?щ?瑜??먮퀎?쒕떎.
    aggregation 寃곌낵? hit 寃곌낵瑜??뚮퉬 痢≪뿉???쎄쾶 援щ텇?섎뒗 吏㏃? ?ы띁??
    """
    return doc.get("source_type", "hit") == "hit"


def resolve_rag_queries(*, state: Any, qa: Any, ks: Any, min_confidence: float) -> tuple[str, str, str, float, bool, list[str], bool]:
    """Resolve the raw query, planner hint query, selected search query, and drift metadata.
    Planner hints remain the default source, but the runtime guard falls back to the raw query
    when semantic-axis drift or confidence failure is detected.
    """
    raw_query = state.question
    normalized_intent = _get_normalized_intent(state)
    hint_query = _pick_attr(ks, normalized_intent, qa, key="retrieval_query", default=raw_query)
    ks_confidence = float(ks.confidence) if getattr(ks, "confidence", None) is not None else None
    qa_confidence = float(qa.confidence) if getattr(qa, "confidence", None) is not None else None
    confidence = ks_confidence if ks_confidence is not None else (qa_confidence if qa_confidence is not None else 0.0)
    drift_detected, drift_reasons = detect_retrieval_query_drift(raw_query=raw_query, hint_query=hint_query)
    fallback_applied = drift_detected or confidence < min_confidence
    search_query = raw_query if fallback_applied else hint_query
    return raw_query, hint_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied




