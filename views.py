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


def _normalize_allowlist(output_fields: Optional[Iterable[str]]) -> List[str]:
    return [str(k).strip() for k in (output_fields or []) if str(k).strip()]


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


def _apply_output_allowlist(result: Dict[str, Any], allowlist: Iterable[str]) -> Dict[str, Any]:
    allowed = _normalize_allowlist(allowlist)
    if not allowed:
        return result
    filtered = dict(result)
    if isinstance(filtered.get("meta_detail"), dict):
        filtered["meta_detail"] = _pick_fields(filtered["meta_detail"], allowed)
    for key in ("prtcp_mp", "prtcp_org"):
        if isinstance(filtered.get(key), list):
            filtered[key] = _filter_list_entries(filtered[key], allowed)
    return filtered


def _ensure_meta_basic(result: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    if "meta_basic" in result:
        return result
    meta_basic = payload.get("meta_basic")
    if isinstance(meta_basic, dict):
        updated = dict(result)
        updated["meta_basic"] = meta_basic
        return updated
    return result


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
    result = _build_header(data, include_collection_score=False)
    for key in ("title", "year"):
        if key in data:
            result[key] = data.get(key)
    result["prtcp_mp"] = _filter_list_entries(data.get("prtcp_mp"), PRTCP_MP_ALLOW)
    return result


def org_view(payload: Dict[str, Any], *, include_collection_score: bool = True) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    result = _build_header(data, include_collection_score=False)
    for key in ("title", "year"):
        if key in data:
            result[key] = data.get(key)
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
    allowed = ("dimension", "metrics", "top_items")
    return {key: data.get(key) for key in allowed if key in data}


def export_view(
    payload: Dict[str, Any],
    *,
    include_collection_score: bool = True,
    selected_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    data = canonicalize_keys(payload)
    selected = [str(k).strip() for k in (selected_fields or []) if str(k).strip()]
    result: Dict[str, Any] = {}

    if selected:
        selected_set = set(selected)
        for key in selected:
            if key.startswith("prtcp_"):
                continue
            if key in data:
                result[key] = data.get(key)
            elif key in HEADER_KEYS and key in data:
                if key in ("_collection", "score") and not include_collection_score:
                    continue
                result[key] = data.get(key)
        return _ensure_meta_basic(result, data)

    result = _build_header(data, include_collection_score)
    for key, value in data.items():
        if key in HEADER_KEYS:
            if key in ("_collection", "score") and not include_collection_score:
                continue
            continue
        if key.startswith("prtcp_"):
            continue
        result[key] = value
    return _ensure_meta_basic(result, data)


def make_payload_view(
    payload: Dict[str, Any],
    view_type: Optional[str],
    *,
    include_collection_score: bool = True,
    output_fields: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    def _text_length(value: Any) -> Optional[int]:
        if isinstance(value, str):
            return len(value)
        if isinstance(value, list):
            return len(", ".join(str(item) for item in value if item is not None))
        return None

    def _enforce_text_range(data: Dict[str, Any], key: str) -> None:
        if key not in data:
            return
        length = _text_length(data.get(key))
        if length is None or length < 200 or length > 400:
            data.pop(key, None)

    view_type_normalized = (view_type or "").strip().lower()
    if view_type_normalized.endswith("_view"):
        view_type_normalized = view_type_normalized[:-5]

    if view_type_normalized == "meta_basic":
        result = meta_basic_view(payload, include_collection_score=include_collection_score)
        for key in ("prtcp_mp", "prtcp_org", "meta_detail"):
            result.pop(key, None)
        _enforce_text_range(result, "summary")
        _enforce_text_range(result, "keywords")
        meta_basic = result.get("meta_basic")
        if isinstance(meta_basic, dict):
            _enforce_text_range(meta_basic, "summary")
            _enforce_text_range(meta_basic, "keywords")
        return _ensure_meta_basic(
            _apply_output_allowlist(result, output_fields),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "mp":
        result = mp_view(payload, include_collection_score=include_collection_score)
        allowed = {"doc_id", "tag", "title", "year", "prtcp_mp"}
        return _ensure_meta_basic(
            _apply_output_allowlist(
                {key: value for key, value in result.items() if key in allowed},
                output_fields,
            ),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "org":
        result = org_view(payload, include_collection_score=include_collection_score)
        allowed = {"doc_id", "tag", "title", "year", "prtcp_org"}
        return _ensure_meta_basic(
            _apply_output_allowlist(
                {key: value for key, value in result.items() if key in allowed},
                output_fields,
            ),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "project_detail":
        result = project_detail_view(payload, include_collection_score=include_collection_score)
        return _ensure_meta_basic(
            _apply_output_allowlist(result, output_fields),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "perf_detail":
        result = perf_detail_view(payload, include_collection_score=include_collection_score)
        return _ensure_meta_basic(
            _apply_output_allowlist(result, output_fields),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "support_detail":
        result = support_detail_view(payload, include_collection_score=include_collection_score)
        return _ensure_meta_basic(
            _apply_output_allowlist(result, output_fields),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "stats":
        result = stats_view(payload, include_collection_score=include_collection_score)
        allowed = {"dimension", "metrics", "top_items"}
        return _ensure_meta_basic(
            _apply_output_allowlist(
                {key: value for key, value in result.items() if key in allowed},
                output_fields,
            ),
            canonicalize_keys(payload),
        )
    if view_type_normalized == "export":
        result = export_view(
            payload,
            include_collection_score=include_collection_score,
            selected_fields=output_fields,
        )
        for key in tuple(result.keys()):
            if key.startswith("prtcp_"):
                result.pop(key, None)
        if output_fields:
            selected_set = {str(k).strip() for k in output_fields if str(k).strip()}
            filtered = {key: value for key, value in result.items() if key in selected_set}
            return _ensure_meta_basic(filtered, canonicalize_keys(payload))
        return _ensure_meta_basic(
            _apply_output_allowlist(result, output_fields),
            canonicalize_keys(payload),
        )

    result = meta_basic_view(payload, include_collection_score=include_collection_score)
    for key in ("prtcp_mp", "prtcp_org", "meta_detail"):
        result.pop(key, None)
    _enforce_text_range(result, "summary")
    _enforce_text_range(result, "keywords")
    meta_basic = result.get("meta_basic")
    if isinstance(meta_basic, dict):
        _enforce_text_range(meta_basic, "summary")
        _enforce_text_range(meta_basic, "keywords")
    return _ensure_meta_basic(
        _apply_output_allowlist(result, output_fields),
        canonicalize_keys(payload),
    )
