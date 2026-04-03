from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from apps.evidence.derived_facts_builder import build_eager_facts, build_lazy_facts, needs_lazy_facts


_QUERY_BONUS_FIELDS_BY_OUTPUT: dict[str, tuple[str, ...]] = {
    "list": ("keyword_text", "summary_candidate"),
    "detail": ("keyword_text", "summary_candidate", "content_candidate", "flat_text"),
    "stats": ("summary_candidate",),
    "summary": ("keyword_text", "summary_candidate", "content_candidate"),
    "relation": ("summary_candidate",),
    "comparison": ("keyword_text", "summary_candidate"),
    "series": ("summary_candidate",),
}


@dataclass(frozen=True)
class EvidenceIdentity:
    doc_id: str
    collection: str
    tag: str
    ids: Dict[str, str]
    title: str
    rank: int
    final_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptEvidenceEnvelope:
    identity: EvidenceIdentity
    stable_base: Dict[str, Any]
    facts: Dict[str, Any]
    query_bonus: Dict[str, Any]
    previews: Dict[str, Any]
    compression: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["identity"] = self.identity.to_dict()
        return payload


def _clean_text(value: Any) -> str:
    text = str(value or "")
    text = text.replace("_x000D_\n", "\n").replace("_x000D_", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _merge_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _normalize_output_type(output_type: Optional[str]) -> str:
    return _clean_text(output_type).lower() or "summary"


def _select_stable_base(payload: Dict[str, Any], canonical_item: Dict[str, Any]) -> Dict[str, Any]:
    facts = canonical_item.get("facts") if isinstance(canonical_item.get("facts"), dict) else {}
    roles = canonical_item.get("roles") if isinstance(canonical_item.get("roles"), dict) else {}
    meta = _merge_meta(payload)
    stable_base = {
        "year": _first_non_empty(facts.get("year"), payload.get("stan_yr"), meta.get("stan_yr"), payload.get("dt1")),
        "lead_org_name": _first_non_empty(
            ((roles.get("lead_org_name") or [None])[0]),
            payload.get("org_nm"),
            meta.get("org_nm"),
            meta.get("pjt_prfrm_org_nm"),
        ),
        "period": _first_non_empty(facts.get("period"), meta.get("period")),
        "budget": _first_non_empty(facts.get("budget"), payload.get("ttl_rsch_exp"), meta.get("ttl_rsch_exp")),
        "perf_type": _first_non_empty(facts.get("perf_type"), payload.get("perf_type"), meta.get("perf_type")),
        "summary": _first_non_empty(facts.get("summary"), payload.get("summary"), meta.get("summary"), meta.get("rsch_abstract")),
    }
    return {key: value for key, value in stable_base.items() if _clean_text(value)}


def _select_query_bonus(
    *,
    payload: Dict[str, Any],
    canonical_item: Dict[str, Any],
    output_type: Optional[str],
) -> Dict[str, Any]:
    facts = canonical_item.get("facts") if isinstance(canonical_item.get("facts"), dict) else {}
    meta = _merge_meta(payload)
    output_name = _normalize_output_type(output_type)
    body = {
        "keyword_text": _first_non_empty(payload.get("keyword_text"), payload.get("keyword1"), payload.get("keyword2"), meta.get("keyword_text")),
        "summary_candidate": _first_non_empty(facts.get("summary"), payload.get("summary"), meta.get("summary"), meta.get("rsch_abstract")),
        "content_candidate": _first_non_empty(
            payload.get("content_text"),
            payload.get("content2"),
            payload.get("content1"),
            payload.get("content"),
            facts.get("goal"),
            meta.get("rsch_abstract"),
        ),
        "flat_text": _first_non_empty(payload.get("flat_text")),
    }
    allowed = _QUERY_BONUS_FIELDS_BY_OUTPUT.get(output_name, _QUERY_BONUS_FIELDS_BY_OUTPUT["summary"])
    return {key: value for key, value in body.items() if key in allowed and _clean_text(value)}


def _select_previews(
    eager_facts: Dict[str, Any],
    lazy_facts: Dict[str, Any],
) -> Dict[str, Any]:
    previews = {
        "people_preview": [dict(item) for item in list(eager_facts.get("people_preview") or []) if isinstance(item, dict)],
        "org_preview": [dict(item) for item in list(eager_facts.get("org_preview") or []) if isinstance(item, dict)],
    }
    matched_people = [dict(item) for item in list(lazy_facts.get("matched_people_subset") or []) if isinstance(item, dict)]
    matched_org = [dict(item) for item in list(lazy_facts.get("matched_org_subset") or []) if isinstance(item, dict)]
    if matched_people:
        previews["matched_people_subset"] = matched_people
    if matched_org:
        previews["matched_org_subset"] = matched_org
    return previews


def build_prompt_reference(
    *,
    point: Any,
    canonical_item: Dict[str, Any],
    final_score: float,
) -> Dict[str, Any]:
    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    ids = canonical_item.get("ids") if isinstance(canonical_item.get("ids"), dict) else {}
    facts = canonical_item.get("facts") if isinstance(canonical_item.get("facts"), dict) else {}
    urls = payload.get("urls") if isinstance(payload.get("urls"), list) else []
    systems = payload.get("systems") if isinstance(payload.get("systems"), list) else []
    return {
        "doc_id": _first_non_empty(ids.get("doc_id"), payload.get("doc_id"), payload.get("id")),
        "title": _first_non_empty(facts.get("title"), payload.get("title_text"), payload.get("title1"), payload.get("title2")),
        "score": float(final_score),
        "urls": list(urls),
        "systems": list(systems),
        "source_table": _first_non_empty(facts.get("tag"), payload.get("tag")),
    }


def build_prompt_evidence_envelope(
    *,
    point: Any,
    canonical_item: Dict[str, Any],
    base_route: str,
    output_type: Optional[str],
    rank: int,
    final_score: float,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
    org_role: Optional[str] = None,
    query_text: str = "",
) -> PromptEvidenceEnvelope:
    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}

    ids = canonical_item.get("ids") if isinstance(canonical_item.get("ids"), dict) else {}
    facts = canonical_item.get("facts") if isinstance(canonical_item.get("facts"), dict) else {}
    identity = EvidenceIdentity(
        doc_id=_first_non_empty(ids.get("doc_id"), payload.get("doc_id"), payload.get("id")),
        collection=_first_non_empty(payload.get("_collection"), payload.get("collection")),
        tag=_first_non_empty(facts.get("tag"), payload.get("tag")),
        ids={
            "pjt_id": _first_non_empty(ids.get("pjt_id")),
            "pjt_no": _first_non_empty(ids.get("pjt_no")),
            "rst_id": _first_non_empty(ids.get("rst_id")),
            "doi": _first_non_empty(ids.get("doi")),
            "issn": _first_non_empty(ids.get("issn")),
        },
        title=_first_non_empty(facts.get("title"), payload.get("title_text"), payload.get("title1"), payload.get("title2")),
        rank=int(rank),
        final_score=float(final_score),
    )
    eager_facts = build_eager_facts(
        payload,
        people_terms=people_terms,
        person_ids=person_ids,
        org_terms=org_terms,
    )
    lazy_facts = (
        build_lazy_facts(
            payload,
            people_terms=people_terms,
            person_ids=person_ids,
            org_terms=org_terms,
            org_role=org_role,
        )
        if needs_lazy_facts(
            base_route=base_route,
            people_terms=people_terms,
            person_ids=person_ids,
            org_terms=org_terms,
            org_role=org_role,
            question=query_text,
        )
        else {}
    )
    facts_payload = {
        "participant_count": int(eager_facts.get("participant_count") or 0),
        "participant_org_count": int(eager_facts.get("participant_org_count") or 0),
        "lead_researcher_names": list(eager_facts.get("lead_researcher_names") or []),
    }
    if lazy_facts.get("role_histogram"):
        facts_payload["role_histogram"] = dict(lazy_facts.get("role_histogram") or {})
    if lazy_facts.get("affiliation_topk"):
        facts_payload["affiliation_topk"] = list(lazy_facts.get("affiliation_topk") or [])
    return PromptEvidenceEnvelope(
        identity=identity,
        stable_base=_select_stable_base(payload, canonical_item),
        facts=facts_payload,
        query_bonus=_select_query_bonus(payload=payload, canonical_item=canonical_item, output_type=output_type),
        previews=_select_previews(eager_facts, lazy_facts),
        compression="none",
    )
