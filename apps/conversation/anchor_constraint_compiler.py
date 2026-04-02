from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import Any, Optional

from apps.conversation.followup_anchor import anchor_to_seed_map


def _normalize_values(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _merge_text_terms(values: Any, additions: Any) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in (values, additions):
        if isinstance(source, str):
            iterable = [source]
        elif isinstance(source, (list, tuple, set)):
            iterable = list(source)
        elif source is None:
            iterable = []
        else:
            iterable = [source]
        for value in iterable:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
    return merged


def merge_seed_into_ids_map(ids_map: Any, seed_map: dict[str, list[str]]) -> dict[str, list[str]]:
    merged = dict(ids_map or {}) if isinstance(ids_map, dict) else {}

    seed_pjt_ids = _normalize_values(seed_map.get("pjt_id"))
    seed_pjt_nos = _normalize_values(seed_map.get("pjt_no"))
    if seed_pjt_ids:
        merged.pop("pjt_no", None)
        merged["pjt_id"] = seed_pjt_ids
    elif seed_pjt_nos:
        merged.pop("pjt_id", None)
        merged["pjt_no"] = seed_pjt_nos

    for key, values in (seed_map or {}).items():
        if key in {"pjt_id", "pjt_no"}:
            continue
        normalized = _normalize_values(values)
        if normalized:
            merged[key] = normalized
    return merged


def apply_anchor_lock(normalized_intent: Any, seed_map: dict[str, list[str]]) -> Any:
    if not seed_map:
        return normalized_intent

    def _resolve_policy(ids_map: dict[str, list[str]]) -> Optional[str]:
        if ids_map.get("pjt_id"):
            return "anchor_locked_pjt_id"
        if ids_map.get("pjt_no"):
            return "anchor_locked_pjt_no"
        return None

    if isinstance(normalized_intent, dict):
        patched = dict(normalized_intent)
        merged_ids_map = merge_seed_into_ids_map(patched.get("ids_map") or {}, seed_map)
        patched["ids_map"] = merged_ids_map
        policy = _resolve_policy(merged_ids_map)
        if policy is not None:
            patched["project_key_policy"] = policy
        return patched

    if is_dataclass(normalized_intent):
        merged_ids_map = merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map)
        project_key_policy = getattr(normalized_intent, "project_key_policy", None)
        policy = _resolve_policy(merged_ids_map)
        if policy is not None:
            project_key_policy = policy
        return replace(normalized_intent, ids_map=merged_ids_map, project_key_policy=project_key_policy)

    if hasattr(normalized_intent, "ids_map"):
        try:
            merged_ids_map = merge_seed_into_ids_map(getattr(normalized_intent, "ids_map", {}) or {}, seed_map)
            setattr(normalized_intent, "ids_map", merged_ids_map)
            policy = _resolve_policy(merged_ids_map)
            if policy is not None and hasattr(normalized_intent, "project_key_policy"):
                setattr(normalized_intent, "project_key_policy", policy)
        except Exception:
            pass
    return normalized_intent


def apply_resolved_anchor_seed(normalized_intent: Any, anchor: Any) -> Any:
    if anchor is None:
        return normalized_intent

    patched = apply_anchor_lock(normalized_intent, anchor_to_seed_map(anchor))
    anchor_kind = str(getattr(anchor, "kind", "") or "").strip().lower()
    title_text = str(getattr(anchor, "title_text", "") or "").strip() or None
    if not title_text:
        return patched

    target_field = None
    if anchor_kind == "people":
        target_field = "people_terms"
    elif anchor_kind == "org":
        target_field = "org_terms"
    elif anchor_kind == "perf":
        target_field = "title"

    if not target_field:
        return patched

    if isinstance(patched, dict):
        patched = dict(patched)
        patched[target_field] = _merge_text_terms(patched.get(target_field) or [], [title_text])
        return patched

    if is_dataclass(patched) and hasattr(patched, target_field):
        return replace(
            patched,
            **{target_field: _merge_text_terms(getattr(patched, target_field, []) or [], [title_text])},
        )

    if hasattr(patched, target_field):
        try:
            setattr(
                patched,
                target_field,
                _merge_text_terms(getattr(patched, target_field, []) or [], [title_text]),
            )
        except Exception:
            pass
    return patched
