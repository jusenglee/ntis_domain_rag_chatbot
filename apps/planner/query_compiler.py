from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from apps.platform.rag_constants import COL_PERF, COL_PROJECT, COL_SUPPORT


RESEARCHER_FILTER_KEYS = (
    "participant_researcher_name",
    "participant_researcher_names",
    "participant_researcher",
    "participant_researchers",
    "researcher_name",
    "researcher_names",
    "researcher",
    "people_name",
    "people_names",
    "person_name",
    "person_names",
)
GENERIC_ORG_FILTER_KEYS = ("org_name",)
ROLE_SCOPED_ORG_FILTER_KEYS = (
    "lead_org_name",
    "performing_org_name",
    "participant_org_name",
    "people_affiliation_org_name",
)


@dataclass(frozen=True)
class QueryContractViolation:
    code: str
    field: str
    message: str
    severity: str = "error"


@dataclass(frozen=True)
class QueryContractValidation:
    ok: bool
    violations: list[QueryContractViolation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "violations": [asdict(item) for item in self.violations]}


@dataclass(frozen=True)
class QdrantQueryPlan:
    schema_version: str
    mode: str
    head: str
    action: str
    target_collections: list[str]
    vector_query: str | None
    filters: dict[str, Any]
    limit: int
    display_limit: int
    ids_map: dict[str, list[str]]
    candidate_keys: dict[str, Any]
    postprocess: dict[str, Any] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryCompilerResult:
    sanitized_slots: dict[str, Any]
    qdrant_query_plan: QdrantQueryPlan
    validation: QueryContractValidation
    dropped_filters: dict[str, list[str]] = field(default_factory=dict)
    salvaged_terms: list[str] = field(default_factory=list)

    def to_diagnostics(self) -> dict[str, Any]:
        return {
            "validation": self.validation.to_dict(),
            "dropped_filters": dict(self.dropped_filters),
            "salvaged_terms": list(self.salvaged_terms),
            "qdrant_query_plan": self.qdrant_query_plan.to_dict(),
        }


def _get_field(value: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def _slots_payload(slots: Any) -> dict[str, Any]:
    if hasattr(slots, "model_dump"):
        return dict(slots.model_dump())
    if isinstance(slots, dict):
        return dict(slots)
    return dict(getattr(slots, "__dict__", {}) or {})


def normalize_terms(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]

    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() in {"none", "null"} or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def append_terms_to_query(query: str, terms: list[str]) -> str:
    merged: list[str] = []
    base = str(query or "").strip()
    if base:
        merged.append(base)
    haystack = base.lower()
    for term in terms:
        text = str(term or "").strip()
        if not text or text in merged:
            continue
        if text.lower() in haystack:
            continue
        merged.append(text)
        haystack = f"{haystack} {text.lower()}".strip()
    return " ".join(merged).strip()


def _target_collections_for(head: str, target_cols: Any) -> list[str]:
    explicit = normalize_terms(target_cols)
    if explicit:
        return explicit

    head_norm = str(head or "").strip().lower()
    if head_norm == "perf":
        return [COL_PERF]
    if head_norm in {"people", "org"}:
        return [COL_PROJECT, COL_PERF]
    if head_norm == "support":
        return [COL_SUPPORT]
    return [COL_PROJECT]


def _org_filter_key_for_role(org_role_hint: str | None) -> str:
    role = str(org_role_hint or "").strip().lower()
    if role == "lead_org":
        return "lead_org_name"
    if role == "participant_org":
        return "participant_org_name"
    if role == "affiliation_org":
        return "people_affiliation_org_name"
    return "org_name"


def _is_people_discovery(*, head: str, semantic_kind: str | None, allowed_people_terms: list[str]) -> bool:
    return (
        str(head or "").strip().lower() == "people"
        and str(semantic_kind or "").strip().lower() == "broad_history"
        and not allowed_people_terms
    )


def _compile_researcher_filters(
    *,
    filters: dict[str, Any],
    allowed_people_terms: list[str],
) -> tuple[dict[str, Any], dict[str, list[str]], list[QueryContractViolation]]:
    compiled = dict(filters)
    dropped: dict[str, list[str]] = {}
    violations: list[QueryContractViolation] = []
    accepted_people: list[str] = []
    allowed_set = set(allowed_people_terms)

    for key in RESEARCHER_FILTER_KEYS:
        values = normalize_terms(compiled.pop(key, None))
        if not values:
            continue
        if not allowed_people_terms:
            dropped[key] = values
            violations.append(
                QueryContractViolation(
                    code="researcher_filter_without_identity_terms",
                    field=key,
                    message="researcher-name filters require entity_role_plan.people_terms_to_keep",
                    severity="repaired",
                )
            )
            continue

        accepted = [value for value in values if value in allowed_set]
        removed = [value for value in values if value not in allowed_set]
        accepted_people.extend(accepted)
        if removed:
            dropped[key] = removed
            violations.append(
                QueryContractViolation(
                    code="researcher_filter_outside_identity_terms",
                    field=key,
                    message="researcher-name filter value was not present in people_terms_to_keep",
                    severity="repaired",
                )
            )

    accepted_people = normalize_terms(accepted_people)
    if accepted_people:
        compiled["participant_researcher_name"] = accepted_people
    return compiled, dropped, violations


def _compile_org_filters(
    *,
    filters: dict[str, Any],
    allowed_org_terms: list[str],
    org_role_hint: str | None,
) -> tuple[dict[str, Any], dict[str, list[str]]]:
    compiled = dict(filters)
    dropped: dict[str, list[str]] = {}
    accepted_orgs: list[str] = []

    for key in (*GENERIC_ORG_FILTER_KEYS, *ROLE_SCOPED_ORG_FILTER_KEYS):
        values = normalize_terms(compiled.pop(key, None))
        if not values:
            continue
        if not allowed_org_terms:
            dropped[key] = values
            continue
        # Org abbreviations and full names often differ (e.g. ETRI vs full Korean
        # name). Once Stage 1.5 confirms an org axis, preserve the value and only
        # normalize the role-scoped key.
        accepted_orgs.extend(values)

    accepted_orgs = normalize_terms(accepted_orgs)
    if accepted_orgs:
        compiled[_org_filter_key_for_role(org_role_hint)] = accepted_orgs
    return compiled, dropped


def _postprocess_policy(*, head: str, semantic_kind: str | None, allowed_people_terms: list[str]) -> dict[str, Any]:
    if _is_people_discovery(head=head, semantic_kind=semantic_kind, allowed_people_terms=allowed_people_terms):
        return {
            "kind": "people_discovery",
            "group_by": "participant_researcher",
            "rank_signal": ["project_relevance", "recent_year", "national_project_history"],
        }
    if str(head or "").strip().lower() == "people" and allowed_people_terms:
        return {"kind": "subject_activity", "anchor": "participant_researcher"}
    return {}


def compile_stage2_to_qdrant_query(
    *,
    stage2_slots: Any,
    entity_role_plan: Any,
    locked_strategy: Any,
    question: str,
    signals: Any = None,
) -> QueryCompilerResult:
    """Compile Stage2 slots into a sanitized Qdrant-facing query plan.

    The compiler does not decide user intent. It translates the already locked
    Agent/Planner contract into executable query text, canonical filters, and
    post-processing hints while repairing unsafe slot hallucinations.
    """
    payload = _slots_payload(stage2_slots)
    filters = dict(payload.get("filters") or {})
    retrieval_query = str(payload.get("retrieval_query") or question or "").strip()

    allowed_people_terms = normalize_terms(_get_field(entity_role_plan, "people_terms_to_keep", []))
    allowed_org_terms = normalize_terms(_get_field(entity_role_plan, "org_terms_to_keep", []))
    must_keep_terms = normalize_terms(_get_field(entity_role_plan, "must_keep_terms", []))
    org_role_hint = str(_get_field(entity_role_plan, "org_role_hint", "") or "").strip().lower() or None
    semantic_kind = str(_get_field(entity_role_plan, "semantic_kind", "") or "").strip().lower() or None

    filters, people_dropped, people_violations = _compile_researcher_filters(
        filters=filters,
        allowed_people_terms=allowed_people_terms,
    )
    filters, org_dropped = _compile_org_filters(
        filters=filters,
        allowed_org_terms=allowed_org_terms,
        org_role_hint=org_role_hint,
    )

    dropped_filters = {**people_dropped}
    for key, values in org_dropped.items():
        dropped_filters[key] = normalize_terms([*dropped_filters.get(key, []), *values])

    # Preserve search meaning in vector text. Researcher-name drops are not
    # salvaged because they are often topic/role words misread as people names.
    query_terms = normalize_terms([*must_keep_terms, *allowed_people_terms, *allowed_org_terms, *normalize_terms(_get_field(signals, "years", []))])
    org_salvage_terms = [term for key, values in org_dropped.items() for term in values if key not in RESEARCHER_FILTER_KEYS]
    retrieval_query = append_terms_to_query(retrieval_query, normalize_terms([*query_terms, *org_salvage_terms]))

    payload["filters"] = {key: value for key, value in filters.items() if value not in (None, [], {}, "")}
    payload["retrieval_query"] = retrieval_query or str(question or "").strip()

    mode = str(_get_field(locked_strategy, "mode", "") or "").strip().upper()
    head = str(_get_field(locked_strategy, "head", "") or "").strip().lower()
    action = str(_get_field(locked_strategy, "action", "") or "").strip().lower()
    target_cols = _get_field(locked_strategy, "target_cols", None)
    if not target_cols:
        target_cols = payload.get("target_cols")

    violations = list(people_violations)
    if mode == "SEARCH" and not payload["retrieval_query"]:
        violations.append(
            QueryContractViolation(
                code="missing_vector_query",
                field="retrieval_query",
                message="SEARCH query plan requires non-empty retrieval_query",
            )
        )

    validation = QueryContractValidation(
        ok=not any(item.severity == "error" for item in violations),
        violations=violations,
    )

    postprocess = _postprocess_policy(
        head=head,
        semantic_kind=semantic_kind,
        allowed_people_terms=allowed_people_terms,
    )
    qdrant_query_plan = QdrantQueryPlan(
        schema_version="qdrant_query_plan:v1",
        mode=mode,
        head=head,
        action=action,
        target_collections=_target_collections_for(head, target_cols),
        vector_query=payload["retrieval_query"],
        filters=dict(payload["filters"]),
        limit=int(payload.get("limit") or 20),
        display_limit=int(payload.get("display_limit") or payload.get("limit") or 20),
        ids_map=dict(payload.get("ids_map") or {}),
        candidate_keys=dict(payload.get("candidate_keys") or {}),
        postprocess=postprocess,
        diagnostics={
            "compiler": "stage2_qdrant_query_compiler",
            "semantic_kind": semantic_kind,
            "org_role_hint": org_role_hint,
            "dropped_filter_keys": sorted(key for key, values in dropped_filters.items() if values),
        },
    )

    return QueryCompilerResult(
        sanitized_slots=payload,
        qdrant_query_plan=qdrant_query_plan,
        validation=validation,
        dropped_filters=dropped_filters,
        salvaged_terms=normalize_terms(org_salvage_terms),
    )
