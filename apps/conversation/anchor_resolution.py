from __future__ import annotations

"""Planner-first anchor resolution helpers.

This module never recovers meaning from raw user text. It only turns the
already-normalized planner/runtime intent into deterministic execution inputs.
"""

from dataclasses import dataclass
from typing import List, Optional

from apps.platform.pipeline_steps import NormalizedIntent
from apps.platform.schemas import ResolvedAnchorSet, derive_resolved_anchor_set


@dataclass(frozen=True)
class AnchorExecutionInputs:
    """Execution-ready anchor inputs derived from planner-normalized intent."""

    anchor_set: ResolvedAnchorSet
    people_terms: List[str]
    people_ids: List[str]
    org_role: Optional[str]
    org_terms: List[str]
    lead_org_terms: List[str]
    participant_org_terms: List[str]
    people_affiliation_org_terms: List[str]
    planner_org_filter_present: bool


def build_anchor_execution_inputs(intent: NormalizedIntent) -> AnchorExecutionInputs:
    """Convert normalized intent into deterministic anchor inputs for filters."""

    anchor_set = derive_resolved_anchor_set(intent)
    ids_map = getattr(intent, "ids_map", {}) or {}
    org_role = str(getattr(intent, "org_role", "") or "").strip().lower() or None

    generic_org_terms = list(anchor_set.org_terms_by_role.get("generic", ()))
    lead_org_terms = list(anchor_set.org_terms_by_role.get("lead", ()))
    participant_org_terms = list(anchor_set.org_terms_by_role.get("participant", ()))
    affiliation_org_terms = list(anchor_set.org_terms_by_role.get("affiliation", ()))

    # A role-constrained generic org anchor stays generic in the summary but is
    # routed to the matching filter input so execution does not need to infer it.
    if org_role in {"lead", "performer", "performing"} and generic_org_terms and not lead_org_terms:
        lead_org_terms = list(generic_org_terms)
    if org_role == "participant" and generic_org_terms and not participant_org_terms:
        participant_org_terms = list(generic_org_terms)
    if org_role == "affiliation" and generic_org_terms and not affiliation_org_terms:
        affiliation_org_terms = list(generic_org_terms)

    planner_org_filter_present = bool(
        generic_org_terms or lead_org_terms or participant_org_terms or affiliation_org_terms
    )

    return AnchorExecutionInputs(
        anchor_set=anchor_set,
        people_terms=list(anchor_set.researcher_names),
        people_ids=[str(value).strip() for value in (ids_map.get("person_no") or []) if str(value).strip()],
        org_role=org_role,
        org_terms=list(generic_org_terms),
        lead_org_terms=list(lead_org_terms),
        participant_org_terms=list(participant_org_terms),
        people_affiliation_org_terms=list(affiliation_org_terms),
        planner_org_filter_present=planner_org_filter_present,
    )
