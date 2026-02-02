from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

KEY_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "title1", "title2", "title_text"),
    "summary": ("summary", "content_text"),
    "year": ("year", "stan_yr", "stanYr"),
    "pjt_id": ("pjt_id", "PJT_ID"),
}

HEADER_KEYS = ("doc_id", "tag", "_collection", "score")

LIST_META_BASIC_ALLOW = {
    "pjt_id",
    "pjt_no",
    "kor_pjt_nm",
    "eng_pjt_nm",
    "pjt_prfrm_org_nm",
    "year",
}

STATS_META_BASIC_ALLOW = {
    "year",
    "pjt_id",
    "pjt_no",
    "pjt_prfrm_org_nm",
    "kor_pjt_nm",
}

PRTCP_MP_ALLOW = {
    "hm_nm",
    "blng_org_nm",
    "hm_id",
    "role",
    "role_nm",
    "rsch_role_nm",
    "gender",
    "sex",
}

PRTCP_ORG_ALLOW = {
    "org_nm",
    "org_id",
    "role",
    "role_nm",
    "org_role_nm",
}


def canonicalize_keys(payload: Any) -> Any:
    if isinstance(payload, list):
        return [canonicalize_keys(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    out: Dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            out[key] = canonicalize_keys(value)
        else:
            out[key] = value

    for canonical, variants in KEY_ALIASES.items():
        if canonical in out and out[canonical] not in (None, "", []):
            continue
        for variant in variants:
            if variant == canonical:
                continue
            if variant in out and out[variant] not in (None, "", []):
                out[canonical] = out[variant]
                break

    for canonical, variants in KEY_ALIASES.items():
        for variant in variants:
            if variant != canonical:
                out.pop(variant, None)

    return out


def _build_header(payload: Dict[str, Any], include_collection_score: bool) -> Dict[str, Any]:
    header: Dict[str, Any] = {}
    for key in HEADER_KEYS:
        if key in ("_collection", "score") and not include_collection_score:
            continue
        if key in payload:
            header[key] = payload.get(key)
    return header


def _pick_fields(data: Any, allowlist: Iterable[str]) -> Dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    allowed = set(allowlist)
    return {key: value for key, value in data.items() if key in allowed}


def _filter_list_entries(values: Any, allowlist: Iterable[str]) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [_pick_fields(item, allowlist) for item in values if isinstance(item, dict)]


def _append_basic_fields(result: Dict[str, Any], payload: Dict[str, Any]) -> None:
    for key in ("title", "summary", "year", "pjt_id", "pjt_no"):
        if key in payload:
            result[key] = payload.get(key)


def meta_basic_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)
    _append_basic_fields(result, data)

    meta_basic = data.get("meta_basic")
    if isinstance(meta_basic, dict):
        result["meta_basic"] = meta_basic

    return result


def mp_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)
    _append_basic_fields(result, data)

    meta_basic = _pick_fields(data.get("meta_basic"), LIST_META_BASIC_ALLOW)
    if meta_basic:
        result["meta_basic"] = meta_basic

    result["prtcp_mp"] = _filter_list_entries(data.get("prtcp_mp"), PRTCP_MP_ALLOW)
    return result


def org_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)
    _append_basic_fields(result, data)

    meta_basic = _pick_fields(data.get("meta_basic"), LIST_META_BASIC_ALLOW)
    if meta_basic:
        result["meta_basic"] = meta_basic

    result["prtcp_org"] = _filter_list_entries(data.get("prtcp_org"), PRTCP_ORG_ALLOW)
    return result


def project_detail_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)
    _append_basic_fields(result, data)

    for key in ("meta_basic", "meta_detail", "prtcp_mp", "prtcp_org"):
        if key in data:
            result[key] = data.get(key)

    return result


def perf_detail_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    return project_detail_view(payload, include_collection_score=include_collection_score)


def support_detail_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    return project_detail_view(payload, include_collection_score=include_collection_score)


def stats_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)
    _append_basic_fields(result, data)

    meta_basic = _pick_fields(data.get("meta_basic"), STATS_META_BASIC_ALLOW)
    if meta_basic:
        result["meta_basic"] = meta_basic

    return result


def export_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score)

    for key, value in data.items():
        if key in HEADER_KEYS:
            if key in ("_collection", "score") and not include_collection_score:
                continue
            continue
        result[key] = value

    return result


def make_payload_view(
    payload: Dict[str, Any],
    view_type: Optional[str],
    *,
    include_collection_score: bool = True,
) -> Dict[str, Any]:
    view_type_normalized = (view_type or "").strip().lower()
    if view_type_normalized.endswith("_view"):
        view_type_normalized = view_type_normalized[:-5]

    if view_type_normalized == "meta_basic":
        return meta_basic_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "mp":
        return mp_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "org":
        return org_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "project_detail":
        return project_detail_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "perf_detail":
        return perf_detail_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "support_detail":
        return support_detail_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "stats":
        return stats_view(payload, include_collection_score=include_collection_score)
    if view_type_normalized == "export":
        return export_view(payload, include_collection_score=include_collection_score)

    return meta_basic_view(payload, include_collection_score=include_collection_score)
