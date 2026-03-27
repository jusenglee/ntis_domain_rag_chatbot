from __future__ import annotations

"""Stagewise planner and deterministic gate artifact helpers."""

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List, Literal, Optional

from apps.core.schemas import default_target_collections_for_route

Mode = Literal["SEARCH", "LOOKUP", "JOIN"]
Head = Literal["project", "perf", "people", "org", "support"]
Action = Literal["topic", "list", "detail", "stats", "download"]
Relation = Literal["project_perf", "perf_project"]
GateJoinKeyMode = Literal["instance", "group"]

PROJECT_TO_PERF = "project_perf"
PERF_TO_PROJECT = "perf_project"
_PERF_SEED_KEYS = (
    "doi",
    "issn",
    "eissn",
    "pissn",
    "perf_id",
    "rst_id",
    "paper_id",
    "patent_reg_no",
    "patent_app_no",
)
_LOOKUP_FILTER_KEYS = (
    "participant_researcher_name",
    "participant_researcher_names",
    "participant_researcher",
    "participant_researchers",
    "researcher_name",
    "researcher_names",
    "researcher",
    "people_name",
    "org_name",
    "lead_org_name",
    "performing_org_name",
    "participant_org_name",
    "people_affiliation_org_name",
)


@dataclass(frozen=True)
class DeterministicGateStrategy:
    """Deterministic gate artifact fixed after stage 1.

    Stage 2 may fill ids, candidate_keys, filters, retrieval_query, limit, and display_limit only.
    Deferred join is not legal here and is introduced only during assembled question analysis.
    """

    mode: Mode
    head: Head
    action: Action
    relation: Optional[Relation] = None
    join_key_mode: Optional[GateJoinKeyMode] = None
    target_cols: List[str] = field(default_factory=list)
    output_type: str = "summary"
    prev_context_seed: Dict[str, List[str]] = field(default_factory=dict)
    gate_seed_map: Dict[str, List[str]] = field(default_factory=dict)

    def to_prompt_payload(self) -> Dict[str, Any]:
        """Serialize the deterministic gate artifact for the stage-2 planner prompt."""
        return asdict(self)


def _action_to_output_type(action: str, relation: Optional[str]) -> str:
    """Resolve the prompt-view output type from action and relation."""
    action_norm = str(action or "").strip().lower()
    if relation:
        return "relation"
    if action_norm == "detail":
        return "detail"
    if action_norm == "stats":
        return "stats"
    if action_norm == "list":
        return "list"
    return "summary"


def _default_target_cols(head: str, relation: Optional[str]) -> List[str]:
    """Resolve default target collections for the gate artifact."""
    if relation == PROJECT_TO_PERF:
        return ["ntis_project_v1", "ntis_perf_v1"]
    if relation == PERF_TO_PROJECT:
        return ["ntis_perf_v1", "ntis_project_v1"]
    return list(default_target_collections_for_route(head))


def has_join_seed(ids_map: Dict[str, List[str]]) -> bool:
    """Return whether strict JOIN seeds exist in ids_map."""
    ids_map = ids_map if isinstance(ids_map, dict) else {}
    return bool(ids_map.get("pjt_id") or ids_map.get("pjt_no") or any(ids_map.get(k) for k in _PERF_SEED_KEYS))


def collect_regate_seed_map(ids_map: Dict[str, List[str]], *, allowed_keys: set[str]) -> Dict[str, List[str]]:
    """Normalize the subset of seeds that may trigger re-gating after stage 2."""
    out: Dict[str, List[str]] = {}
    for key, values in (ids_map or {}).items():
        if key not in allowed_keys and not str(key).startswith("patent_"):
            continue
        normalized = sorted({str(v).strip() for v in (values or []) if str(v).strip()})
        if normalized:
            out[str(key)] = normalized
    return out


def extract_single_project_seed(prev_context: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Extract a single project seed from prior context when available."""
    pjt_ids, pjt_nos = set(), set()
    for doc in prev_context or []:
        payload = doc if isinstance(doc, dict) else {}
        for key in ("pjt_id", "project_id"):
            val = str(payload.get(key) or "").strip()
            if val:
                pjt_ids.add(val)
        for key in ("pjt_no", "project_no"):
            val = str(payload.get(key) or "").strip()
            if val:
                pjt_nos.add(val)
    if len(pjt_ids) == 1:
        return {"pjt_id": [next(iter(pjt_ids))]}
    if len(pjt_nos) == 1:
        return {"pjt_no": [next(iter(pjt_nos))]}
    return {}


def has_new_regate_seed(*, base_seed_map: Dict[str, List[str]], stage2_seed_map: Dict[str, List[str]]) -> bool:
    """Return whether stage 2 introduced seeds not present in the original gate map."""
    for key, values in stage2_seed_map.items():
        base_values = set(base_seed_map.get(key) or [])
        if any(value not in base_values for value in values):
            return True
    return False


def _normalize_terms(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _has_stage2_lookup_gate(stage2: Any) -> bool:
    filters = getattr(stage2, "filters", None) if not isinstance(stage2, dict) else stage2.get("filters")
    if not isinstance(filters, dict):
        return False
    return any(_normalize_terms(filters.get(key)) for key in _LOOKUP_FILTER_KEYS)


def regate_locked_strategy(
    *,
    request_id: Optional[str],
    conversation_id: str,
    stage1: Any,
    stage2: Any,
    locked_strategy: DeterministicGateStrategy | Dict[str, Any],
    allowed_keys: set[str],
    log_event: Any,
) -> DeterministicGateStrategy | Dict[str, Any]:
    """Recompute the deterministic gate artifact when stage 2 reveals new legal seeds."""
    stage2_ids_map = getattr(stage2, "ids_map", None) if not isinstance(stage2, dict) else stage2.get("ids_map")
    stage2_seed_map = collect_regate_seed_map(stage2_ids_map or {}, allowed_keys=allowed_keys)
    locked_mode = locked_strategy.get("mode") if isinstance(locked_strategy, dict) else locked_strategy.mode
    locked_relation = locked_strategy.get("relation") if isinstance(locked_strategy, dict) else locked_strategy.relation
    locked_join_key_mode = locked_strategy.get("join_key_mode") if isinstance(locked_strategy, dict) else locked_strategy.join_key_mode
    locked_target_cols = locked_strategy.get("target_cols") if isinstance(locked_strategy, dict) else locked_strategy.target_cols
    locked_prev_context_seed = locked_strategy.get("prev_context_seed") if isinstance(locked_strategy, dict) else locked_strategy.prev_context_seed
    base_seed_map = dict((locked_strategy.get("gate_seed_map") if isinstance(locked_strategy, dict) else locked_strategy.gate_seed_map) or {})
    relation_candidate = getattr(stage1, "relation_candidate", None) if not isinstance(stage1, dict) else stage1.get("relation_candidate")
    can_regate = bool(relation_candidate) and locked_mode in {"SEARCH", "LOOKUP"} and has_new_regate_seed(
        base_seed_map=base_seed_map,
        stage2_seed_map=stage2_seed_map,
    )
    can_upgrade_search_to_lookup = locked_mode == "SEARCH" and _has_stage2_lookup_gate(stage2)

    updated: DeterministicGateStrategy | Dict[str, Any] = locked_strategy
    if can_regate:
        merged_seed_map = {**base_seed_map}
        for key, values in stage2_seed_map.items():
            merged = set(merged_seed_map.get(key) or [])
            merged.update(values)
            merged_seed_map[key] = sorted(merged)
        stage1_payload = stage1 if isinstance(stage1, dict) else {
            "action": getattr(stage1, "action", None),
            "head": getattr(stage1, "head", None),
            "relation_candidate": getattr(stage1, "relation_candidate", None),
            "referential_followup": getattr(stage1, "referential_followup", None),
            "confidence": getattr(stage1, "confidence", None),
        }
        updated = compose_locked_strategy(
            stage1=stage1_payload,
            ids_map=merged_seed_map,
            has_prev_anchor=bool(locked_prev_context_seed),
            prev_context_seed=locked_prev_context_seed,
            gate_seed_map=merged_seed_map,
        )
        if isinstance(locked_strategy, dict):
            updated = updated.to_prompt_payload()
    elif can_upgrade_search_to_lookup:
        if isinstance(locked_strategy, dict):
            updated = dict(locked_strategy)
            updated["mode"] = "LOOKUP"
            updated["relation"] = None
            updated["join_key_mode"] = None
            updated["target_cols"] = _default_target_cols(updated.get("head"), None)
        else:
            updated = replace(
                locked_strategy,
                mode="LOOKUP",
                relation=None,
                join_key_mode=None,
                target_cols=_default_target_cols(locked_strategy.head, None),
            )

    def _locked_field(value: DeterministicGateStrategy | Dict[str, Any], field: str) -> Any:
        """Read a field from either dataclass or dict form for before/after comparison."""
        return value.get(field) if isinstance(value, dict) else getattr(value, field)

    changed = any(
        _locked_field(updated, field) != _locked_field(locked_strategy, field)
        for field in ("mode", "relation", "join_key_mode", "target_cols")
    )
    log_event(
        "PLANNER.REGATE",
        request_id=request_id,
        conversation_id=conversation_id,
        regate_eligible=int(can_regate or can_upgrade_search_to_lookup),
        regate_changed=int(changed),
        before_mode=locked_mode,
        after_mode=_locked_field(updated, "mode"),
        before_relation=locked_relation,
        after_relation=_locked_field(updated, "relation"),
        before_join_key_mode=locked_join_key_mode,
        after_join_key_mode=_locked_field(updated, "join_key_mode"),
        before_target_cols=locked_target_cols,
        after_target_cols=_locked_field(updated, "target_cols"),
    )
    return updated


def compose_locked_strategy(
    *,
    stage1: Dict[str, Any],
    ids_map: Dict[str, List[str]],
    has_prev_anchor: bool,
    prev_context_seed: Optional[Dict[str, List[str]]] = None,
    gate_seed_map: Optional[Dict[str, List[str]]] = None,
) -> DeterministicGateStrategy:
    """Build the deterministic gate artifact from resolved seeds and prior context.

    This step fixes SEARCH, LOOKUP, or JOIN only from resolved seeds.
    Deferred join for ambiguous exact project keys is introduced later during assembly.
    """
    action = str(stage1.get("action") or "topic").strip().lower() or "topic"
    head = str(stage1.get("head") or "project").strip().lower() or "project"
    relation = str(stage1.get("relation_candidate") or "").strip().lower() or None

    ids_map = ids_map if isinstance(ids_map, dict) else {}
    has_project_seed = bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))
    has_perf_seed = any(ids_map.get(k) for k in _PERF_SEED_KEYS)
    explicit_join_seed = has_project_seed or has_perf_seed

    join_key_mode: Optional[GateJoinKeyMode] = None
    if ids_map.get("pjt_no"):
        join_key_mode = "group"
    elif ids_map.get("pjt_id") or has_perf_seed:
        join_key_mode = "instance"

    if relation in (PROJECT_TO_PERF, PERF_TO_PROJECT) and (explicit_join_seed or has_prev_anchor):
        mode: Mode = "JOIN"
    elif action == "topic" and not explicit_join_seed:
        mode = "SEARCH"
        relation = None
        join_key_mode = None
    elif action in {"list", "stats"} and not explicit_join_seed:
        mode = "SEARCH"
        relation = None
        join_key_mode = None
    else:
        mode = "LOOKUP"
        relation = None
        join_key_mode = None

    locked_head = relation.split("_", 1)[1] if mode == "JOIN" and relation else head

    return DeterministicGateStrategy(
        mode=mode,
        head=locked_head,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        relation=relation if mode == "JOIN" else None,  # type: ignore[arg-type]
        join_key_mode=join_key_mode if mode == "JOIN" else None,
        target_cols=_default_target_cols(head, relation if mode == "JOIN" else None),
        output_type=_action_to_output_type(action, relation if mode == "JOIN" else None),
        prev_context_seed=dict(prev_context_seed or {}),
        gate_seed_map=dict(gate_seed_map or {}),
    )


def merge_locked_strategy_slots(
    *,
    schema_version: str,
    locked: DeterministicGateStrategy,
    slots: Dict[str, Any],
    default_query: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge deterministic gate fields with stage-2 slots into assembled planner payload.

    Gate fields stay fixed, while v3 slot fields such as candidate_keys,
    project_key_policy, and join_resolution_policy are appended.
    This produces assembled planner payload/question analysis input,
    not final execution strategy.
    """
    return {
        "strategy_version": schema_version,
        "mode": locked.mode,
        "head": locked.head,
        "action": locked.action,
        "relation": locked.relation,
        "join_key_mode": locked.join_key_mode,
        "target_cols": list(locked.target_cols),
        "output_type": locked.output_type,
        "ids_map": dict(slots.get("ids_map") or {}),
        "candidate_keys": dict(slots.get("candidate_keys") or {}),
        "project_key_policy": slots.get("project_key_policy"),
        "join_resolution_policy": slots.get("join_resolution_policy"),
        "filters": dict(slots.get("filters") or {}),
        "limit": int(slots.get("limit") or 20),
        "display_limit": int(slots.get("display_limit") or slots.get("limit") or 20),
        "retrieval_query": slots.get("retrieval_query") or default_query,
        "confidence": float(slots.get("confidence") or 0.0),
    }


