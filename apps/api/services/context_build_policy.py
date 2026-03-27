from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

from apps.api.services.render_profile import resolve_render_profile
from apps.core.canonical_evidence import build_canonical_evidence_bundle
from apps.core.retrieval import build_context_mixed


_OUTPUT_TYPE_FIELDSETS: Dict[str, Tuple[str, ...]] = {
    "list": ("title_text", "org", "year", "id"),
    "detail": ("title_text", "meta_detail", "summary"),
    "stats": ("aggregation_keys",),
    "summary": ("title_text", "meta_basic", "summary", "content"),
    "relation": ("title_text", "relation", "id"),
    "comparison": ("title_text", "aggregation_keys", "meta_basic", "summary"),
    "series": ("title_text", "year", "relation", "meta_basic", "summary"),
}


def _clean_one_line(value: object, max_len: int = 160) -> str:
    """여러 줄 텍스트를 한 줄로 정리하고 길이를 제한한다."""
    text = str(value or "").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "..."
    return text


def _get_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    """payload에서 meta_basic과 meta_detail을 합쳐 통합 meta로 만든다."""
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _approx_token_len(text: str) -> int:
    """context line의 토큰 길이를 거칠게 추정한다."""
    if not text:
        return 0
    words = len(text.split())
    return max(words, int(len(text) / 4))


def _get_list_ctx_token_budget() -> int:
    """list/stat output에 쓰는 전용 context token budget을 환경변수에서 가져온다."""
    return int(os.getenv("RAG_LIST_CTX_TOKEN_BUDGET", os.getenv("CTX_TOKEN_BUDGET", "2048")))


def _pick_first(*values: object) -> str:
    """여러 후보 값 중 첫 번째 유효 문자열을 고른다."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _pick_nested_first(payload: Dict[str, Any], list_key: str, field_key: str) -> str:
    """nested list payload에서 지정한 field의 첫 유효 값을 꼭 집어 온다."""
    items = payload.get(list_key)
    if not isinstance(items, list):
        return ""
    for item in items:
        if isinstance(item, dict):
            value = item.get(field_key)
            if value not in (None, ""):
                return str(value).strip()
    return ""


def _normalize_terms(values: Optional[List[str]]) -> List[str]:
    """힌트 리스트를 빈 값 없는 문자열 목록으로 정리한다."""
    normalized: List[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def _match_any_term(value: Any, terms: Optional[List[str]]) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(str(term or "").strip().lower() in text for term in (terms or []) if str(term or "").strip())


def _pick_matching_prtcp_mp(
    payload: Dict[str, Any],
    *,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    affiliation_org_terms: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """참여인력 list에서 사람명과 person id 힌트에 가장 맞는 member를 고른다."""
    members = payload.get("prtcp_mp")
    if not isinstance(members, list):
        return {}

    norm_terms = _normalize_terms(people_terms)
    norm_ids = set(_normalize_terms(person_ids))
    norm_affiliations = _normalize_terms(affiliation_org_terms)
    if not norm_terms and not norm_ids:
        for member in members:
            if isinstance(member, dict) and (
                not norm_affiliations or _match_any_term(member.get("blng_org_nm"), norm_affiliations)
            ):
                return member
        return {}

    id_fields = ("hm_id", "person_no", "prtcp_mp_id", "mp_id", "id")
    for member in members:
        if not isinstance(member, dict):
            continue
        hm_nm = str(member.get("hm_nm") or "").strip()
        affiliation_ok = not norm_affiliations or _match_any_term(member.get("blng_org_nm"), norm_affiliations)
        if hm_nm and any(term in hm_nm for term in norm_terms) and affiliation_ok:
            return member
        for key in id_fields:
            sid = str(member.get(key) or "").strip()
            if sid and sid in norm_ids and affiliation_ok:
                return member

    for member in members:
        if not isinstance(member, dict):
            continue
        hm_nm = str(member.get("hm_nm") or "").strip()
        if hm_nm and any(term in hm_nm for term in norm_terms):
            return member
        for key in id_fields:
            sid = str(member.get(key) or "").strip()
            if sid and sid in norm_ids:
                return member

    for member in members:
        if isinstance(member, dict):
            return member
    return {}


def _payload_title(payload: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """payload와 meta에서 보여줄 title 후보를 우선순위대로 고른다."""
    return _pick_first(
        payload.get("title_text"),
        payload.get("title1"),
        payload.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
    )


def build_context_list_light(
    points: List[Any],
    *,
    kind: str,
    max_items: int,
    query_text: str = "",
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    people_org_terms: Optional[List[str]] = None,
    org_role: Optional[str] = None,
    token_budget: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """list/stats 응답용으로 가벼운 line-oriented context를 만든다.
    
    project, people, org, perf kind별로 보여줄 핵심 필드만 추려 token budget 안에 맞춘다."""
    items: List[str] = []
    kind = (kind or "").lower().strip() or "project"
    date_year_pattern = re.compile(r"^(\d{4})-\d{2}-\d{2}$")
    token_budget = _get_list_ctx_token_budget() if token_budget is None else int(token_budget)
    total_tok = 0

    def _normalize_year(value: Any) -> Any:
        """YYYY-MM-DD 형태의 값을 리스트 context에서는 연도만 보이도록 정리한다."""
        if value is None:
            return value
        text = str(value).strip()
        if not text:
            return value
        match = date_year_pattern.match(text)
        if match:
            return match.group(1)
        return value

    def _pjt_id(payload: Dict[str, Any]) -> str:
        """payload에서 pjt_id를 안전하게 꼭 집어 온다."""
        return _pick_first(payload.get("pjt_id"))

    for point in (points or [])[: max(0, int(max_items))]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        meta = _get_meta(payload)

        if kind == "project":
            title = _payload_title(payload, meta)
            pjt_id = _pjt_id(payload)
            org = _pick_first(payload.get("org_nm"))
            year = _normalize_year(_pick_first(payload.get("stan_yr"), meta.get("stan_yr")))
            line = f"- {_clean_one_line(title, 180)}"
            extra: List[str] = []
            if pjt_id:
                extra.append(f"PJT_ID={pjt_id}")
            if org:
                extra.append(_clean_one_line(org, 60))
            if year:
                extra.append(str(year))
            if extra:
                line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        if kind == "people":
            member = _pick_matching_prtcp_mp(
                payload,
                people_terms=people_terms,
                person_ids=person_ids,
                affiliation_org_terms=people_org_terms,
            )
            name = _pick_first(member.get("hm_nm")) if member else _pick_nested_first(payload, "prtcp_mp", "hm_nm")
            role = _pick_first(member.get("role_slct_nm")) if member else _pick_nested_first(payload, "prtcp_mp", "role_slct_nm")
            org = _pick_first(member.get("blng_org_nm")) if member else _pick_nested_first(payload, "prtcp_mp", "blng_org_nm")
            pjt_id = _pjt_id(payload)
            line = f"- {_clean_one_line(name or '(name unavailable)', 80)}"
            extra = []
            if role:
                extra.append(_clean_one_line(role, 30))
            if org:
                extra.append(_clean_one_line(org, 50))
            if pjt_id:
                extra.append(f"PJT_ID={pjt_id}")
            if extra:
                line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        if kind == "org":
            org_role_norm = str(org_role or "").strip().lower()
            if org_role_norm in ("lead", "performer", "performing"):
                org = _pick_first(payload.get("org_nm"), _pick_nested_first(payload, "prtcp_org", "org_nm"))
                role = _pick_nested_first(payload, "prtcp_org", "org_slct_nm")
            elif org_role_norm == "participant":
                org = _pick_first(_pick_nested_first(payload, "prtcp_org", "org_nm"), payload.get("org_nm"))
                role = _pick_nested_first(payload, "prtcp_org", "org_slct_nm")
            elif org_role_norm == "affiliation":
                org = _pick_first(
                    _pick_matching_prtcp_mp(
                        payload,
                        people_terms=people_terms,
                        person_ids=person_ids,
                        affiliation_org_terms=people_org_terms,
                    ).get("blng_org_nm"),
                    _pick_nested_first(payload, "prtcp_mp", "blng_org_nm"),
                    payload.get("org_nm"),
                    _pick_nested_first(payload, "prtcp_org", "org_nm"),
                )
                role = _pick_nested_first(payload, "prtcp_mp", "role_slct_nm")
            else:
                org = _pick_first(payload.get("org_nm"), _pick_nested_first(payload, "prtcp_org", "org_nm"))
                role = _pick_nested_first(payload, "prtcp_org", "org_slct_nm")
            pjt_id = _pjt_id(payload)
            line = f"- {_clean_one_line(org or '(org unavailable)', 100)}"
            extra = []
            if role:
                extra.append(_clean_one_line(role, 30))
            if pjt_id:
                extra.append(f"PJT_ID={pjt_id}")
            if extra:
                line += " (" + ", ".join(extra) + ")"
            tok = _approx_token_len(line)
            if total_tok + tok > token_budget:
                break
            items.append(line)
            total_tok += tok
            continue

        title = _payload_title(payload, meta)
        pjt_name = _pick_first(meta.get("kor_pjt_nm"), meta.get("eng_pjt_nm"))
        pjt_id = _pjt_id(payload)
        perf_type = _pick_first(payload.get("tag"))
        year = _pick_first(payload.get("dt1"), payload.get("dt2"), payload.get("stan_yr"), meta.get("stan_yr"))

        line = f"- {_clean_one_line(title, 180)}"
        extra = []
        if perf_type:
            extra.append(_clean_one_line(perf_type, 32))
        if year:
            extra.append(str(year))
        if pjt_name or pjt_id:
            extra.append(_clean_one_line(pjt_name or f"PJT_ID={pjt_id}", 60))
        if extra:
            line += " (" + ", ".join(extra) + ")"
        tok = _approx_token_len(line)
        if total_tok + tok > token_budget:
            break
        items.append(line)
        total_tok += tok

    header = f"질의: {_clean_one_line(query_text, 120)}\n" if query_text else ""
    if header:
        header_tok = _approx_token_len(header)
        if header_tok + total_tok > token_budget and items:
            header = ""
    empty_notice = "결과 없음" if kind == "perf" else "(정보 없음)"
    context = header + ("\n".join(items) if items else empty_notice)
    return context, points


def normalize_output_type(output_type: Optional[str]) -> Optional[str]:
    """output_type 문자열을 소문자로 정리한 뒤 None을 정리한다."""
    normalized = (output_type or "").strip().lower()
    return normalized or None


def resolve_output_fieldset(output_type: Optional[str]) -> Tuple[str, ...]:
    """output_type별로 허용된 context fieldset을 반환한다."""
    normalized = normalize_output_type(output_type) or "summary"
    return _OUTPUT_TYPE_FIELDSETS.get(normalized, _OUTPUT_TYPE_FIELDSETS["summary"])


def should_use_list_context(*, action: str, base_route: str, output_type: Optional[str]) -> bool:
    """action, route, output_type를 보고 line-oriented list context를 썼지 판단한다."""
    normalized = normalize_output_type(output_type)
    if normalized in ("list", "stats", "comparison", "series"):
        return base_route in ("project", "perf", "people", "org")
    return action in ("list", "stats", "download") and base_route in ("project", "perf", "people", "org")


def build_context_with_output_type(
    points: List[Any],
    *,
    action: str,
    base_route: str,
    mode: str,
    output_type: Optional[str],
    max_items: int,
    query_text: str,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    people_org_terms: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
    org_role: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], Tuple[str, ...]]:
    """render_profile과 output_type에 따라 list context와 mixed context 중 하나를 선택해 조립한다."""
    fieldset = resolve_output_fieldset(output_type)
    render_profile = resolve_render_profile(
        output_type=output_type,
        action=action,
        base_route=base_route,
        mode=mode,
        fieldset=fieldset,
        people_terms_present=bool(people_terms or []),
        person_ids_present=bool(person_ids or []),
        org_terms_present=bool(org_terms or []),
        org_role_present=bool(str(org_role or "").strip()),
    )
    context_kind = render_profile.context_kind

    if should_use_list_context(action=action, base_route=base_route, output_type=output_type):
        context, refs = build_context_list_light(
            points,
            kind=context_kind,
            max_items=max_items,
            query_text=query_text,
            people_terms=people_terms,
            person_ids=person_ids,
            people_org_terms=people_org_terms,
            org_role=org_role,
        )
        return context, refs, fieldset

    is_detail = normalize_output_type(output_type) == "detail"
    context, refs = build_context_mixed(
        points,
        max_items=max_items,
        query_text=query_text,
        output_type=output_type,
        fieldset_keys=fieldset,
        meta_source="detail_only" if is_detail else None,
        include_meta_long=is_detail,
    )
    return context, refs, fieldset


def build_context_bundle(
    reranked: List[Any],
    *,
    min_ctx_items: int,
    preset_max_ctx_items: int,
    ctx_hard_limit: int,
    action: str,
    base_route: str,
    mode: str,
    output_type: Optional[str],
    query_text: str,
    people_terms: Optional[List[str]] = None,
    person_ids: Optional[List[str]] = None,
    people_org_terms: Optional[List[str]] = None,
    org_terms: Optional[List[str]] = None,
    org_role: Optional[str] = None,
) -> Dict[str, Any]:
    """reranked hit를 canonical_evidence, render_profile, refs, context로 묶은 완성된 context bundle로 만든다."""
    max_items = min(ctx_hard_limit, max(min_ctx_items, int(preset_max_ctx_items)))
    canonical_evidence = build_canonical_evidence_bundle(
        reranked,
        base_route=base_route,
        output_type=output_type,
        max_items=max_items,
    )

    if reranked:
        reranked_for_ctx = reranked[: max(1, max_items)]
        context, refs, fieldset = build_context_with_output_type(
            reranked_for_ctx,
            action=action,
            base_route=base_route,
            output_type=output_type,
            mode=mode,
            max_items=max_items,
            query_text=query_text,
            people_terms=people_terms,
            person_ids=person_ids,
            people_org_terms=people_org_terms,
            org_terms=org_terms,
            org_role=org_role,
        )
    else:
        context = ""
        refs = []
        fieldset = resolve_output_fieldset(output_type)

    kept_ctx = min(len(reranked or []), max_items)
    discarded_ctx = max(0, len(reranked or []) - kept_ctx)
    render_profile = resolve_render_profile(
        output_type=output_type,
        action=action,
        base_route=base_route,
        mode=mode,
        fieldset=fieldset,
        people_terms_present=bool(people_terms or []),
        person_ids_present=bool(person_ids or []),
        org_terms_present=bool(org_terms or []),
        org_role_present=bool(str(org_role or "").strip()),
    )
    return {
        "context": context,
        "refs": refs,
        "fieldset": fieldset,
        "render_profile": render_profile.to_dict(),
        "canonical_evidence": canonical_evidence,
        "max_items": max_items,
        "kept_ctx": kept_ctx,
        "discarded_ctx": discarded_ctx,
    }
