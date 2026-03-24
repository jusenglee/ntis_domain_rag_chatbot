"""RAG retrieval helpers for execution-layer query dispatch and shaping."""

from __future__ import annotations

import re
from typing import Any, Dict, Optional
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict

from apps.core.rag_pipeline import run_rag_ab_compare
from apps.core.pipeline_steps import NormalizedIntent
from apps.core.schemas import IntentPayloadV3
from apps.core.followup_resolution import build_followup_clarification_message, build_followup_clarification_payload

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
    question_text = str(question or "").strip()
    requested_fields = extract_requested_fields(question_text)
    preferred_fields = [
        field
        for field in ("researchers", "lead_org", "participant_org", "year")
        if field in requested_fields
    ]
    lowered_question = question_text.lower()
    terms: list[str] = []
    seen: set[str] = set()
    for field in preferred_fields:
        aliases = [str(alias or "").strip() for alias in (FIELD_ALIASES.get(field) or []) if str(alias or "").strip()]
        term = next((alias for alias in aliases if alias.lower() in lowered_question), None)
        if term is None:
            term = next((alias for alias in aliases if any(ord(ch) > 127 for ch in alias)), None)
        if term is None:
            term = aliases[0] if aliases else ""
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
            "entity_kind": None,
            "title_text": None,
        }

    ids_map = getattr(normalized_intent, "ids_map", None) or {}
    if isinstance(normalized_intent, dict):
        ids_map = normalized_intent.get("ids_map") or {}
    selected_prev_item = dict(strategy_meta.get("selected_prev_item") or {})
    focus_entity = dict(strategy_meta.get("focus_entity") or {})
    entity_kind = _first_non_empty_text(
        selected_prev_item.get("context_kind"),
        focus_entity.get("kind"),
        strategy_meta.get("selected_prev_context_kind"),
    )
    resolved_ids: Dict[str, Optional[str]] = {}
    for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        values = _normalize_id_values((ids_map or {}).get(key))
        resolved_ids[key] = _first_non_empty_text(*(values[:1] or []), selected_prev_item.get(key), focus_entity.get(key))
    title_text = _first_non_empty_text(selected_prev_item.get("title"), focus_entity.get("title_text"))
    entity_key = _first_non_empty_text(
        *(resolved_ids.get(key) for key in ("rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn", "pjt_id", "pjt_no")),
        strategy_meta.get("focus_entity_key"),
    )
    payload: Dict[str, Any] = {
        "present": bool(entity_key or title_text),
        "anchor_source": strategy_meta.get("anchor_source") or strategy_meta.get("seed_source"),
        "entity_key": entity_key,
        "entity_kind": entity_kind,
        "title_text": title_text,
    }
    payload.update(resolved_ids)
    return payload


def _query_mentions_anchor(query: Any, anchor_context: Dict[str, Any]) -> bool:
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return False
    anchor_tokens = [
        str(anchor_context.get(key) or "").strip().lower()
        for key in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")
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


def _build_anchor_preserving_query_from_context(*, state: Any, query: Any, anchor_context: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    metadata = {
        "anchor_present": anchor_context.get("present", False),
        "anchor_source": anchor_context.get("anchor_source"),
        "anchor_entity_key": anchor_context.get("entity_key"),
        "anchor_query_repaired": False,
        "anchor_repair_reason": None,
    }
    if not anchor_context.get("present"):
        return str(query or ""), metadata

    anchor_phrase = _first_non_empty_text(
        anchor_context.get("title_text"),
        anchor_context.get("pjt_id"),
        anchor_context.get("pjt_no"),
        anchor_context.get("rst_id"),
        anchor_context.get("person_no"),
        anchor_context.get("org_id"),
        anchor_context.get("org_code"),
        query,
    ) or ""
    requested_terms = _build_anchor_requested_terms(getattr(state, "question", ""))
    repaired_terms: list[str] = []
    seen: set[str] = set()
    for part in [anchor_phrase, *requested_terms]:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        repaired_terms.append(text)
    repaired_query = " ".join(repaired_terms) or str(query or "")
    metadata["anchor_query_repaired"] = repaired_query != str(query or "")
    metadata["anchor_repair_reason"] = "anchor_axis_lost" if metadata["anchor_query_repaired"] else None
    return repaired_query, metadata


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
    return _build_anchor_preserving_query_from_context(state=state, query=query, anchor_context=anchor_context)


_QUERY_TOKEN_RE = re.compile("[A-Za-z0-9_-]+|[?-?]{2,}")
_GENERIC_QUERY_TERMS = {
    "알려줘",
    "보여줘",
    "조회",
    "목록",
    "리스트",
    "정보",
    "내용",
    "건",
    "개",
    "top",
    "the",
    "and",
    "for",
}
_PROJECT_AXIS_TERMS = {"과제", "project", "projects"}
_PERF_AXIS_TERMS = {"성과", "output", "outputs", "논문", "특허", "보고서"}
_DETAIL_AXIS_TERMS = {"상세", "detail", "details"}
_STATS_AXIS_TERMS = {"통계", "추이", "trend", "trends", "집계"}

_ORG_ROLE_TERMS = {"\uc8fc\uad00\uae30\uad00", "\uc218\ud589\uae30\uad00", "\ucc38\uc5ec\uae30\uad00", "\uc18c\uc18d\uae30\uad00", "lead_org", "participant_org", "affiliation"}
_ORG_SUFFIXES = ("\uae30\uad00", "\ub300\ud559", "\uc5f0\uad6c\uc6d0", "\uc5f0\uad6c\uc18c", "\uc13c\ud130", "\ud559\uad50", "\ub7a9", "lab")
_QUOTED_TERM_RE = re.compile(r"[\"']([^\"']{2,80})[\"']")
_ORG_TOKEN_RE = re.compile(r'([A-Za-z][A-Za-z0-9&._-]{1,31}|[\uac00-\ud7a3A-Za-z0-9]{2,32}(?:\ub300\ud559|\uc5f0\uad6c\uc6d0|\uc5f0\uad6c\uc18c|\uc13c\ud130|\ud559\uad50|\uae30\uad00))')
_PEOPLE_TOKEN_RE = re.compile(r'([\uac00-\ud7a3]{2,8}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s*(?:\uc5f0\uad6c\uc790|\uc5f0\uad6c\uc6d0|\uad50\uc218)?')


def _extract_org_role_terms(text: Any) -> set[str]:
    normalized = _normalize_query_text(text)
    return {term for term in _ORG_ROLE_TERMS if term in normalized}


def _extract_org_terms(text: Any) -> set[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return set()
    results: set[str] = set()
    for match in _ORG_TOKEN_RE.finditer(normalized):
        token = str(match.group(1) or "").strip()
        lowered = token.lower()
        if len(token) < 2:
            continue
        if any(token.endswith(suffix) for suffix in _ORG_SUFFIXES) or token.isupper():
            results.add(lowered)
    return results


def _extract_people_terms(text: Any) -> set[str]:
    normalized = str(text or "").strip()
    if not normalized or not any(marker in normalized for marker in ("\uc5f0\uad6c\uc790", "\uc5f0\uad6c\uc6d0", "\uad50\uc218", "\ucc45\uc784\uc790")):
        return set()
    results: set[str] = set()
    for match in _PEOPLE_TOKEN_RE.finditer(normalized):
        token = str(match.group(1) or "").strip()
        if len(token) >= 2 and not token.isdigit():
            results.add(token.lower())
    return results


def _extract_title_terms(text: Any) -> set[str]:
    normalized = str(text or "")
    return {str(match.group(1) or "").strip().lower() for match in _QUOTED_TERM_RE.finditer(normalized) if str(match.group(1) or "").strip()}



def _normalize_query_text(text: Any) -> str:
    return str(text or "").strip().lower()


def _is_korean_char(ch: str) -> bool:
    code = ord(ch)
    return 0xAC00 <= code <= 0xD7A3


def _extract_query_tokens(text: Any) -> list[str]:
    normalized = _normalize_query_text(text)
    tokens: list[str] = []
    current: list[str] = []
    current_kind: Optional[str] = None

    def flush() -> None:
        nonlocal current, current_kind
        if current:
            tokens.append("".join(current))
            current = []
            current_kind = None

    for ch in normalized:
        if _is_korean_char(ch):
            kind = "ko"
        elif ch.isalnum() or ch in {"_", "-"}:
            kind = "ascii"
        else:
            flush()
            continue
        if current_kind is not None and kind != current_kind:
            flush()
        current.append(ch)
        current_kind = kind
    flush()
    return [token for token in tokens if token]


def _extract_identifier_like_tokens(text: Any) -> set[str]:
    protected: set[str] = set()
    for token in _extract_query_tokens(text):
        if re.fullmatch(r"\d{4,}", token):
            protected.add(token)
        elif any(ch.isdigit() for ch in token) and any(("a" <= ch.lower() <= "z") for ch in token):
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

    raw_org_terms = _extract_org_terms(raw)
    hint_org_terms = _extract_org_terms(hint)
    if raw_org_terms - hint_org_terms:
        reasons.append("org_terms_lost")

    raw_people_terms = _extract_people_terms(raw)
    hint_people_terms = _extract_people_terms(hint)
    if raw_people_terms - hint_people_terms:
        reasons.append("people_terms_lost")

    raw_title_terms = _extract_title_terms(raw)
    hint_title_terms = _extract_title_terms(hint)
    if raw_title_terms - hint_title_terms:
        reasons.append("title_terms_lost")

    raw_role_terms = _extract_org_role_terms(raw)
    hint_role_terms = _extract_org_role_terms(hint)
    if raw_role_terms - hint_role_terms:
        reasons.append("org_role_lost")

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
        clarification = build_followup_clarification_payload(dict(strategy_meta))
        followup_message = build_followup_clarification_message(dict(strategy_meta))
        if followup_message:
            return {
                "documents": [],
                "canonical_evidence": [],
                "render_profile": {},
                "no_result_message": followup_message,
                "clarification": clarification,
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
            return {"documents": documents, "canonical_evidence": canonical_evidence, "render_profile": render_profile, "no_result_message": no_result_message, "clarification": clarification}
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
                "clarification": clarification,
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
                "clarification": clarification,
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
                "clarification": clarification,
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
                "clarification": clarification,
            }

        if not hits:
            return {
                "documents": [],
                "canonical_evidence": canonical_evidence,
                "render_profile": render_profile,
                "no_result_message": no_result_message,
                "clarification": clarification,
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
            "clarification": clarification,
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
    action = str(_pick_attr(normalized_intent, qa, key="action", default="") or "").strip().lower()
    output_type = str(_pick_attr(normalized_intent, qa, key="output_type", default="") or "").strip().lower()
    anchor_context = _resolve_followup_anchor_context(state)
    ks_confidence = float(ks.confidence) if getattr(ks, "confidence", None) is not None else None
    qa_confidence = float(qa.confidence) if getattr(qa, "confidence", None) is not None else None
    confidence = ks_confidence if ks_confidence is not None else (qa_confidence if qa_confidence is not None else 0.0)
    drift_detected, drift_reasons = detect_retrieval_query_drift(raw_query=raw_query, hint_query=hint_query)
    fallback_applied = drift_detected or confidence < min_confidence
    search_query = raw_query if fallback_applied else hint_query

    if anchor_context.get("present") and action == "detail" and output_type == "detail":
        anchor_kind = str(anchor_context.get("entity_kind") or "").strip().lower()
        if anchor_kind == "project" and "perf_axis_added" in drift_reasons:
            fallback_applied = True
            drift_detected = True
        if anchor_kind == "project" and (
            fallback_applied
            or "perf_axis_added" in drift_reasons
            or "project_axis_removed" in drift_reasons
            or not _query_mentions_anchor(search_query, anchor_context)
        ):
            search_query, _ = _build_anchor_preserving_query_from_context(
                state=state,
                query=search_query,
                anchor_context=anchor_context,
            )
    return raw_query, hint_query, search_query, confidence, drift_detected, drift_reasons, fallback_applied




