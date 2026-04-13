from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from apps.evidence.canonical_evidence import build_canonical_evidence
from apps.evidence.context_packer import BudgetedContextPacker, PromptUnitCandidate
from apps.evidence.context_score_gate import normalize_reranked_hits
from apps.evidence.prompt_evidence_envelope import build_prompt_evidence_envelope, build_prompt_reference
from apps.evidence.render_profile import resolve_render_profile
from apps.platform.settings import RAG_EVIDENCE_TOKEN_BUDGET


_OUTPUT_TYPE_FIELDSETS: Dict[str, Tuple[str, ...]] = {
    "list": ("title_text", "org", "year", "id"),
    "detail": ("title_text", "meta_detail", "summary"),
    "stats": ("aggregation_keys",),
    "summary": ("title_text", "meta_basic", "summary", "content"),
    "relation": ("title_text", "relation", "id"),
    "comparison": ("title_text", "aggregation_keys", "meta_basic", "summary"),
    "series": ("title_text", "year", "relation", "meta_basic", "summary"),
}

def normalize_output_type(output_type: Optional[str]) -> Optional[str]:
    text = str(output_type or "").strip().lower()
    return text or None


def resolve_output_fieldset(output_type: Optional[str]) -> Tuple[str, ...]:
    normalized = normalize_output_type(output_type) or "summary"
    return _OUTPUT_TYPE_FIELDSETS.get(normalized, _OUTPUT_TYPE_FIELDSETS["summary"])


def _resolve_profile(
    *,
    action: str,
    base_route: str,
    mode: str,
    output_type: Optional[str],
    people_terms: Optional[List[str]],
    person_ids: Optional[List[str]],
    org_terms: Optional[List[str]],
    org_role: Optional[str],
) -> tuple[Tuple[str, ...], dict[str, Any]]:
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
    return fieldset, render_profile.to_dict()


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
    bundle = build_context_bundle(
        list(points or []),
        min_ctx_items=max(1, int(max_items)),
        preset_max_ctx_items=max(1, int(max_items)),
        ctx_hard_limit=max(1, int(max_items)),
        action=action,
        base_route=base_route,
        mode=mode,
        output_type=output_type,
        query_text=query_text,
        people_terms=people_terms,
        person_ids=person_ids,
        people_org_terms=people_org_terms,
        org_terms=org_terms,
        org_role=org_role,
    )
    return (
        str(bundle.get("context") or "[]"),
        list(bundle.get("refs") or []),
        tuple(bundle.get("fieldset") or ()),
    )


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
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    max_items = min(ctx_hard_limit, max(min_ctx_items, int(preset_max_ctx_items)))
    fieldset, render_profile = _resolve_profile(
        action=action,
        base_route=base_route,
        mode=mode,
        output_type=output_type,
        people_terms=people_terms,
        person_ids=person_ids,
        org_terms=org_terms,
        org_role=org_role,
    )

    normalized_hits = normalize_reranked_hits(list(reranked or []))
    survivors = [item for item in normalized_hits if item.gate_passed]
    dropped_by_floor = max(0, len(normalized_hits) - len(survivors))

    canonical_evidence: List[Dict[str, Any]] = []
    candidates: List[PromptUnitCandidate] = []
    for item in survivors:
        payload = getattr(item.point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        canonical_item = build_canonical_evidence(
            payload,
            rank=item.rank,
            base_route=base_route,
            output_type=output_type,
        ).to_dict()
        canonical_evidence.append(canonical_item)
        envelope = build_prompt_evidence_envelope(
            point=item.point,
            canonical_item=canonical_item,
            base_route=base_route,
            output_type=output_type,
            rank=item.rank,
            final_score=item.final_score,
            people_terms=people_terms,
            person_ids=person_ids,
            org_terms=(org_terms or people_org_terms),
            org_role=org_role,
            query_text=query_text,
        ).to_dict()
        ref = build_prompt_reference(
            point=item.point,
            canonical_item=canonical_item,
            final_score=item.final_score,
        )
        identity = envelope.get("identity") if isinstance(envelope.get("identity"), dict) else {}
        identity_ids = identity.get("ids") if isinstance(identity.get("ids"), dict) else {}
        parent_anchor_key = ""
        if str(identity_ids.get("pjt_id") or "").strip():
            parent_anchor_key = f"project:{str(identity_ids.get('pjt_id') or '').strip()}"
        elif str(identity_ids.get("rst_id") or "").strip():
            parent_anchor_key = f"result:{str(identity_ids.get('rst_id') or '').strip()}"
        elif str(identity_ids.get("pjt_no") or "").strip():
            parent_anchor_key = f"project_no:{str(identity_ids.get('pjt_no') or '').strip()}"
        elif str(identity.get("collection") or "").strip() and str(identity.get("doc_id") or "").strip():
            parent_anchor_key = f"{str(identity.get('collection') or '').strip()}:{str(identity.get('doc_id') or '').strip()}"
        candidates.append(
            PromptUnitCandidate(
                rank=item.rank,
                final_score=item.final_score,
                envelope=envelope,
                ref=ref,
                parent_anchor_key=parent_anchor_key,
                turn_id=str(turn_id or "").strip(),
            )
        )

    packed = BudgetedContextPacker(
        query_text=query_text,
        budget_tokens=RAG_EVIDENCE_TOKEN_BUDGET,
    ).pack(candidates, dropped_by_floor=dropped_by_floor)
    kept_ctx = int(packed.kept_count)
    discarded_ctx = int(packed.discarded_count)

    return {
        "context": packed.context,
        "prompt_units": packed.prompt_units,
        "refs": packed.refs,
        "fieldset": fieldset,
        "render_profile": render_profile,
        "canonical_evidence": canonical_evidence,
        "max_items": max_items,
        "kept_ctx": kept_ctx,
        "discarded_ctx": discarded_ctx,
        "used_tokens": int(packed.used_tokens),
        "dropped_by_floor": int(packed.dropped_by_floor),
        "dropped_by_budget": int(packed.dropped_by_budget),
        "compressed_count": int(packed.compressed_count),
        "lineages": list(packed.lineages or []),
        "anchor_hit": False,
        "followup_resolved_by_facts": False,
    }
