from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


OrgRoleHint = Literal["lead_org", "participant_org", "affiliation_org", "unspecified"]
SemanticKind = Literal["broad_history", "explicit_perf", "explicit_relation", "generic_lookup"]
PerfTypePolicy = Literal["explicit_only", "allow_when_explicit"]


class PlannerEntityRolePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people_terms_to_keep: list[str] = Field(default_factory=list)
    org_terms_to_keep: list[str] = Field(default_factory=list)
    org_role_hint: OrgRoleHint | None = None
    perf_type_hints: list[str] = Field(default_factory=list)
    must_keep_terms: list[str] = Field(default_factory=list)
    anchor_required: bool = False
    semantic_kind: SemanticKind | None = None
    perf_type_policy: PerfTypePolicy = "explicit_only"
    notes: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
