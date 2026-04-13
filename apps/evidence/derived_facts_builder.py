from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _normalize_text(value)
        if text:
            return text
    return ""


def _normalize_terms(values: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for value in values or []:
        text = _normalize_text(value).lower()
        if text and text not in out:
            out.append(text)
    return out


def _match_any(text: Any, terms: List[str]) -> bool:
    lowered = _normalize_text(text).lower()
    return bool(lowered and any(term in lowered for term in terms))


def _normalize_people(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    items = payload.get("prtcp_mp")
    if not isinstance(items, list):
        return []
    people: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        person = {
            "name": _first_text(item.get("hm_nm"), item.get("person_name"), item.get("name")),
            "person_no": _first_text(
                item.get("person_no"),
                item.get("hm_id"),
                item.get("prtcp_mp_id"),
                item.get("mp_id"),
                item.get("id"),
            ),
            "role": _first_text(item.get("role_slct_nm"), item.get("role_nm"), item.get("role")),
            "affiliation": _first_text(item.get("blng_org_nm"), item.get("affiliation"), item.get("org_nm")),
        }
        if any(person.values()):
            people.append(person)
    return people


def _normalize_orgs(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    items = payload.get("prtcp_org")
    if not isinstance(items, list):
        return []
    orgs: List[Dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        org = {
            "org_nm": _first_text(item.get("org_nm"), item.get("org_name")),
            "org_id": _first_text(item.get("org_id")),
            "org_code": _first_text(item.get("org_code"), item.get("org_cd")),
            "biz_no": _first_text(item.get("biz_no"), item.get("org_no")),
            "role": _first_text(item.get("role"), item.get("role_nm"), item.get("role_slct_nm")),
        }
        if any(org.values()):
            orgs.append(org)
    return orgs


def _is_lead_role(role: str) -> bool:
    normalized = _normalize_text(role).lower()
    if not normalized:
        return False
    return any(token in normalized for token in ("책임", "총괄", "principal", "lead", "pi"))


def _rank_people_preview(
    people: List[Dict[str, str]],
    *,
    people_terms: Optional[List[str]],
    person_ids: Optional[List[str]],
) -> List[Dict[str, str]]:
    normalized_terms = _normalize_terms(people_terms)
    normalized_ids = set(_normalize_terms(person_ids))

    def _score(item: Dict[str, str]) -> tuple[int, int, str]:
        matched = int(
            _match_any(item.get("name"), normalized_terms)
            or _match_any(item.get("affiliation"), normalized_terms)
            or (_normalize_text(item.get("person_no")).lower() in normalized_ids if item.get("person_no") else False)
        )
        lead = int(_is_lead_role(item.get("role", "")))
        role_present = int(bool(_normalize_text(item.get("role"))))
        return (-matched, -lead, -role_present)

    return sorted(list(people), key=_score)


def _rank_org_preview(
    orgs: List[Dict[str, str]],
    *,
    org_terms: Optional[List[str]],
    lead_org_name: str,
) -> List[Dict[str, str]]:
    normalized_terms = _normalize_terms(org_terms)
    lead_org_normalized = _normalize_text(lead_org_name).lower()

    def _score(item: Dict[str, str]) -> tuple[int, int, int]:
        matched = int(_match_any(item.get("org_nm"), normalized_terms) or _match_any(item.get("role"), normalized_terms))
        lead = int(bool(lead_org_normalized and _normalize_text(item.get("org_nm")).lower() == lead_org_normalized))
        role_present = int(bool(_normalize_text(item.get("role"))))
        return (-matched, -lead, -role_present)

    return sorted(list(orgs), key=_score)


def _lead_researcher_names(people: List[Dict[str, str]]) -> List[str]:
    names = [item.get("name", "") for item in people if _is_lead_role(item.get("role", "")) and _normalize_text(item.get("name"))]
    if names:
        return list(dict.fromkeys(names))
    fallback = [item.get("name", "") for item in people if _normalize_text(item.get("name"))]
    return list(dict.fromkeys(fallback[:1]))


def needs_lazy_facts(
    *,
    base_route: str,
    people_terms: Optional[List[str]],
    person_ids: Optional[List[str]],
    org_terms: Optional[List[str]],
    org_role: Optional[str],
    question: str = "",
) -> bool:
    route = _normalize_text(base_route).lower()
    question_text = _normalize_text(question)
    return bool(
        route in {"people", "org"}
        or people_terms
        or person_ids
        or org_terms
        or _normalize_text(org_role)
        or any(token in question_text for token in ("역할", "소속", "기관", "연구자"))
    )


def build_eager_facts(
    payload: Dict[str, Any],
    *,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    meta_basic = payload.get("meta_basic") if isinstance(payload.get("meta_basic"), dict) else {}
    meta_detail = payload.get("meta_detail") if isinstance(payload.get("meta_detail"), dict) else {}
    lead_org_name = _first_text(payload.get("org_nm"), meta_basic.get("org_nm"), meta_detail.get("org_nm"), payload.get("pjt_prfrm_org_nm"))
    people = _normalize_people(payload)
    orgs = _normalize_orgs(payload)
    ranked_people = _rank_people_preview(people, people_terms=people_terms, person_ids=person_ids)
    ranked_orgs = _rank_org_preview(orgs, org_terms=org_terms, lead_org_name=lead_org_name)
    return {
        "participant_count": len(people),
        "participant_org_count": len(orgs),
        "lead_researcher_names": _lead_researcher_names(people),
        "people_preview": ranked_people[:3],
        "org_preview": ranked_orgs[:3],
        "lead_org_name": lead_org_name or None,
    }


def build_lazy_facts(
    payload: Dict[str, Any],
    *,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
    org_role: Optional[str] = None,
) -> Dict[str, Any]:
    people = _normalize_people(payload)
    orgs = _normalize_orgs(payload)
    normalized_people_terms = _normalize_terms(people_terms)
    normalized_person_ids = set(_normalize_terms(person_ids))
    normalized_org_terms = _normalize_terms(org_terms)
    normalized_org_role = _normalize_text(org_role).lower()

    role_histogram = Counter(_normalize_text(item.get("role")) for item in people if _normalize_text(item.get("role")))
    affiliation_histogram = Counter(_normalize_text(item.get("affiliation")) for item in people if _normalize_text(item.get("affiliation")))

    matched_people_subset = [
        item
        for item in people
        if _match_any(item.get("name"), normalized_people_terms)
        or _match_any(item.get("affiliation"), normalized_people_terms)
        or (_normalize_text(item.get("person_no")).lower() in normalized_person_ids if item.get("person_no") else False)
    ]
    matched_org_subset = [
        item
        for item in orgs
        if _match_any(item.get("org_nm"), normalized_org_terms)
        or _match_any(item.get("role"), normalized_org_terms)
        or (normalized_org_role and normalized_org_role in _normalize_text(item.get("role")).lower())
    ]

    return {
        "role_histogram": dict(role_histogram.most_common(6)),
        "affiliation_topk": [name for name, _ in affiliation_histogram.most_common(5)],
        "matched_people_subset": matched_people_subset[:3],
        "matched_org_subset": matched_org_subset[:3],
    }
