from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Annotated, Any, Dict, List, Literal, Optional

from apps.platform.langchain_compat import BaseMessage
try:
    from langgraph.graph.message import add_messages
except ModuleNotFoundError:
    def add_messages(left: List[BaseMessage] | None, right: List[BaseMessage] | None) -> List[BaseMessage]:
        """Fallback merger used only when langgraph is unavailable at import time."""
        return list(left or []) + list(right or [])
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.contracts.answer_groundedness import AnswerEvidenceSnapshot, AnswerGroundednessVerdict
from apps.api.contracts.answer_state_consistency import (
    AnswerStateConsistencyVerdict,
    AnswerStateSnapshot,
)
from apps.api.streaming.contracts import AnswerArtifact
from apps.api.streaming.emitter import AsyncStreamEmitter
from apps.platform.schemas import IntentPayloadV3
from apps.platform.settings import MAX_TOP_K_SIZE
from apps.platform.storage import KVStore
from apps.conversation.agent_contracts import AgentDecision
from apps.conversation.agent_observation import AgentObservation
from apps.conversation.session_memory import CurrentContext, SessionMemory
from apps.conversation.view_state import ConversationViewState


PLANNER_SCHEMA_VERSION = "v3"
QUESTION_ANALYSIS_REQUIRED_KEYS = {
    "strategy_version",
    "mode",
    "head",
    "action",
    "relation",
    "join_key_mode",
    "target_cols",
    "ids_map",
    "candidate_keys",
    "project_key_policy",
    "join_resolution_policy",
    "filters",
    "limit",
    "display_limit",
    "retrieval_query",
    "confidence",
}

logger = logging.getLogger("Chatbot_Server")

Mode = Literal["SEARCH", "LOOKUP", "JOIN"]
Head = Literal["project", "perf", "people", "org", "support"]
Action = Literal["topic", "list", "detail", "stats", "download"]


class HardContractV1(BaseModel):
    """Deterministic legality and identifier contract carried with QA."""

    schema_version: Literal["v1"] = "v1"
    explicit_project_id_label: bool = False
    explicit_project_no_label: bool = False
    ambiguous_project_key_label: bool = False
    unsupported_project_key_aliases: list[str] = Field(default_factory=list)
    resolved_project_key_axis: Literal["pjt_id", "pjt_no"] | None = None
    candidate_project_key_count: int = Field(default=0, ge=0)
    project_key_policy: Optional[str] = None
    join_anchor_required: bool = False
    join_anchor_present: bool = False
    join_key_mode: Literal["instance", "group", "deferred"] | None = None
    project_key_axis_locked: bool = False


class SoftStrategyHintsV1(BaseModel):
    """Planner-facing soft signals that must not override hard legality."""

    schema_version: Literal["v1"] = "v1"
    years: list[str] = Field(default_factory=list)
    id_like_terms: list[str] = Field(default_factory=list)
    people_terms: list[str] = Field(default_factory=list)
    org_terms: list[str] = Field(default_factory=list)
    perf_types: list[str] = Field(default_factory=list)
    followup_cues: list[str] = Field(default_factory=list)
    must_keep_terms: list[str] = Field(default_factory=list)
    semantic_kind: Optional[str] = None
    perf_type_policy: Optional[str] = None
    org_role_hint: Optional[str] = None
    candidate_project_key_count: int = Field(default=0, ge=0)
    has_prev_anchor: bool = False


class QuestionAnalysisV3(BaseModel):
    """Assembled question-analysis contract.

    This is the validated planner payload between the deterministic gate artifact
    and the final execution strategy. Runtime consumes this contract as the
    assembled source of truth before execution-time compilation resolves keys.
    """
    strategy_version: str = Field(default=PLANNER_SCHEMA_VERSION, validate_default=True)
    mode: Mode
    head: Head
    action: Action
    relation: Optional[str] = None
    join_key_mode: Literal["instance", "group", "deferred"] | None = None
    output_type: Optional[str] = Field(default=None, description="summary|list|detail|stats|relation|comparison|series")
    ids_map: dict[str, list[str]] = Field(default_factory=dict)
    candidate_keys: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    project_key_policy: Optional[str] = None
    join_resolution_policy: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    target_cols: list[str] = Field(default_factory=list)
    limit: int = Field(MAX_TOP_K_SIZE, le=MAX_TOP_K_SIZE)
    display_limit: int = Field(MAX_TOP_K_SIZE, ge=1, le=MAX_TOP_K_SIZE)
    retrieval_query: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    hard_contract: HardContractV1 = Field(default_factory=HardContractV1)
    soft_strategy_hints: SoftStrategyHintsV1 = Field(default_factory=SoftStrategyHintsV1)
    planner_source: Optional[Literal["legacy", "stagewise"]] = None

    _ALLOWED_RELATIONS = {"project_perf", "perf_project"}
    _FORBIDDEN_PEOPLE_ORG_RELATIONS = {
        "people_project",
        "project_people",
        "people_perf",
        "perf_people",
        "org_project",
        "project_org",
        "org_perf",
        "perf_org",
        "people_org",
        "org_people",
    }

    @model_validator(mode="before")
    @classmethod
    def normalize_planner_payload(cls, data: Any) -> Any:
        """Normalize planner payload aliases into the v3 assembled contract shape."""
        if not isinstance(data, dict):
            return data

        d = dict(data)

        mode = d.get("mode")
        if isinstance(mode, str):
            d["mode"] = mode.strip().upper()

        head = d.get("head")
        if isinstance(head, str):
            head_norm = head.strip().lower()
            head_alias = {
                "performance": "perf",
                "perf": "perf",
                "result": "perf",
                "results": "perf",
                "paper": "perf",
                "patent": "perf",
                "researcher": "people",
                "person": "people",
                "people": "people",
                "research": "people",
                "organization": "org",
                "org": "org",
                "institution": "org",
                "company": "org",
                "qna": "support",
                "manual": "support",
                "support": "support",
                "project": "project",
            }
            d["head"] = head_alias.get(head_norm, head_norm)

        action = d.get("action")
        if isinstance(action, str):
            act_norm = action.strip().lower()
            action_alias = {
                "details": "detail",
                "detail": "detail",
                "info": "detail",
                "view": "detail",
                "show": "detail",
                "stat": "stats",
                "stats": "stats",
                "statistics": "stats",
                "export": "download",
                "download": "download",
                "list": "list",
                "relation": "list",
                "topic": "topic",
            }
            d["action"] = action_alias.get(act_norm, act_norm)

        relation = d.get("relation")
        if isinstance(relation, (list, tuple)) and len(relation) == 2:
            lhs = str(relation[0]).strip().lower()
            rhs = str(relation[1]).strip().lower()
            d["relation"] = f"{lhs}_{rhs}" if lhs and rhs else None
        elif isinstance(relation, str):
            rel = relation.strip().lower()
            d["relation"] = rel or None

        jkm = d.get("join_key_mode")
        if isinstance(jkm, str):
            d["join_key_mode"] = jkm.strip().lower() or None

        def _coerce_str_list(value: Any) -> list[str]:
            """Coerce planner inputs into a normalized list of non-empty strings."""
            if value is None:
                return []
            if isinstance(value, (list, tuple, set)):
                seq = list(value)
            else:
                seq = [value]
            out: list[str] = []
            for x in seq:
                s = str(x).strip()
                if not s or s.lower() == "none":
                    continue
                out.append(s)
            return out

        raw_ids_map = d.get("ids_map") if isinstance(d.get("ids_map"), dict) else {}
        d["candidate_keys"] = dict(d.get("candidate_keys") or {}) if isinstance(d.get("candidate_keys"), dict) else {}
        normalized_ids_map: dict[str, list[str]] = {}
        for raw_key, raw_value in raw_ids_map.items():
            if raw_key is None:
                continue
            key = str(raw_key).strip()
            if not key:
                continue
            key_low = key.lower().replace("-", "_").replace(" ", "")
            if key_low in {"pjt_id", "pjtid", "project_id", "projectid", "pjtid"}:
                key_low = "pjt_id"
            elif key_low in {"pjt_no", "pjtno", "project_no", "projectno"}:
                key_low = "pjt_no"
            values = _coerce_str_list(raw_value)
            if values:
                normalized_ids_map[key_low] = values
        d["ids_map"] = normalized_ids_map

        if not isinstance(d.get("filters"), dict):
            d["filters"] = {}
        filters = dict(d.get("filters") or {})
        filter_alias_to_canonical = {
            "performing_org_name": "lead_org_name",
            "main_org_name": "lead_org_name",
            "lead_org": "lead_org_name",
            "participant_org": "participant_org_name",
            "co_org_name": "participant_org_name",
            "consortium_org_name": "participant_org_name",
            "participant_researcher": "participant_researcher_name",
            "participant_researcher_names": "participant_researcher_name",
            "researcher_name": "participant_researcher_name",
            "researcher": "participant_researcher_name",
            "participant_researcher_no": "participant_researcher_id",
            "participant_researcher_ids": "participant_researcher_id",
            "researcher_id": "participant_researcher_id",
            "affiliation_org_name": "people_affiliation_org_name",
            "people_affiliation_org": "people_affiliation_org_name",
            "researcher_affiliation_org_name": "people_affiliation_org_name",
        }

        def _append_unique(dst: list[str], values: list[str]) -> list[str]:
            """Append normalized values without duplicates."""
            seen = {str(v).strip() for v in dst if str(v).strip()}
            for item in values:
                norm_item = str(item).strip()
                if not norm_item or norm_item in seen:
                    continue
                dst.append(norm_item)
                seen.add(norm_item)
            return dst

        for raw_key, raw_value in list(filters.items()):
            key_norm = str(raw_key).strip().lower().replace("-", "_")
            canonical_key = filter_alias_to_canonical.get(key_norm)
            if not canonical_key:
                continue
            filters[canonical_key] = _append_unique(_coerce_str_list(filters.get(canonical_key)), _coerce_str_list(raw_value))

        d["filters"] = filters

        tc = d.get("target_cols")
        if isinstance(tc, str):
            d["target_cols"] = [tc]
        elif isinstance(tc, (tuple, set)):
            d["target_cols"] = [str(x) for x in tc]
        elif not isinstance(tc, list):
            d["target_cols"] = []

        if "output_type" in d and isinstance(d.get("output_type"), str):
            output_type = str(d.get("output_type") or "").strip().lower()
            d["output_type"] = output_type or None
        if "limit" in d and not isinstance(d.get("limit"), int):
            try:
                d["limit"] = int(float(str(d.get("limit"))))
            except Exception:
                pass
        if "display_limit" in d and not isinstance(d.get("display_limit"), int):
            try:
                d["display_limit"] = int(float(str(d.get("display_limit"))))
            except Exception:
                pass
        if "confidence" in d and not isinstance(d.get("confidence"), (int, float)):
            try:
                d["confidence"] = float(str(d.get("confidence")))
            except Exception:
                pass
        rq = d.get("retrieval_query")
        if rq is not None and not isinstance(rq, str):
            d["retrieval_query"] = str(rq)
        return d

    @field_validator("strategy_version")
    @classmethod
    def validate_strategy_version(cls, value: str) -> str:
        """Require the current planner schema version exactly."""
        if value != PLANNER_SCHEMA_VERSION:
            raise ValueError(f"strategy_version must be {PLANNER_SCHEMA_VERSION!r}")
        return value

    @model_validator(mode="after")
    def validate_join_contract(self) -> "QuestionAnalysisV3":
        """Validate JOIN relation and join-key semantics after normalization."""
        relation_norm = str(self.relation or "").strip().lower()
        if relation_norm:
            if relation_norm in self._FORBIDDEN_PEOPLE_ORG_RELATIONS:
                raise ValueError(f"PLANNER_RELATION_FORBIDDEN_PEOPLE_ORG:{relation_norm}")
            if relation_norm not in self._ALLOWED_RELATIONS:
                raise ValueError(f"PLANNER_RELATION_INVALID:{relation_norm}")
        if self.mode != "JOIN":
            try:
                object.__setattr__(self, "join_key_mode", None)
            except Exception:
                pass
        if int(self.display_limit or 0) > int(self.limit or 0):
            raise ValueError("PLANNER_DISPLAY_LIMIT_EXCEEDS_LIMIT")
        resolved_axis = "pjt_id" if self.ids_map.get("pjt_id") else ("pjt_no" if self.ids_map.get("pjt_no") else None)
        contract_axis = getattr(self.hard_contract, "resolved_project_key_axis", None)
        if resolved_axis and contract_axis and resolved_axis != contract_axis:
            raise ValueError("PLANNER_HARD_CONTRACT_PROJECT_KEY_AXIS_MISMATCH")
        contract_policy = str(getattr(self.hard_contract, "project_key_policy", "") or "").strip().lower() or None
        question_policy = str(self.project_key_policy or "").strip().lower() or None
        if contract_policy and question_policy and contract_policy != question_policy:
            raise ValueError("PLANNER_HARD_CONTRACT_PROJECT_KEY_POLICY_MISMATCH")
        return self


QuestionAnalysis = QuestionAnalysisV3


class PlannerParseError(ValueError):
    """Strict parse error for missing planner schema fields."""
    pass


def validate_question_analysis_required_keys(payload: Dict[str, Any]) -> None:
    """Fail closed when the planner payload misses required assembled keys."""
    missing_keys = sorted(QUESTION_ANALYSIS_REQUIRED_KEYS - set(payload.keys()))
    if missing_keys:
        raise PlannerParseError(f"missing required keys: {missing_keys}")


def planner_backoff_seconds(*, attempt_no: int, retry_backoff_sec: float, backoff_cap_sec: float) -> float:
    """Compute exponential backoff for planner retries."""
    backoff = retry_backoff_sec * (2 ** max(0, attempt_no - 1))
    return min(backoff, backoff_cap_sec)



def merge_bool_flag(existing: bool, new: bool) -> bool:
    """Merge boolean stream flags with OR semantics."""
    return bool(existing) or bool(new)


class KnowledgeSufficiency(BaseModel):
    """Shared planner/runtime model for pre-retrieval knowledge sufficiency."""
    requires_new_knowledge: Literal["low", "medium", "high"]
    search_intent: str
    retrieval_query: str
    prefer_fresh_retrieval: bool = False
    confidence: float = Field(ge=0.0, le=1.0)


class RuleDecision(BaseModel):
    """Model for direct answer, skip, or proceed decisions."""
    action: Literal["direct_answer", "skip", "proceed"]
    direct_response: Optional[str] = None
    reason: str


class AgentState(BaseModel):
    """Shared execution state container for the LangGraph workflow."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: Annotated[List[BaseMessage], add_messages]
    kv_store: Optional[KVStore] = None
    chat_history: List[BaseMessage] = Field(default_factory=list)
    prev_context: List[Dict] = Field(default_factory=list)
    context: List[Dict] = Field(default_factory=list)
    canonical_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    render_profile: Dict[str, Any] = Field(default_factory=dict)
    session_memory: SessionMemory = Field(default_factory=SessionMemory)
    next_current_context: Optional[CurrentContext] = None
    view_state: ConversationViewState = Field(default_factory=ConversationViewState)
    no_result_message: Optional[str] = None
    rendered_context_used: Annotated[bool, merge_bool_flag] = False
    rendered_context_used_gemma: bool = False
    rendered_context_used_solar: bool = False
    degraded: Annotated[bool, merge_bool_flag] = False
    answer_gemma: Optional[str] = None
    answer_solar: Optional[str] = None
    answer_gemma_meta: Dict[str, Any] = Field(default_factory=dict)
    answer_solar_meta: Dict[str, Any] = Field(default_factory=dict)
    answer_solar_raw: Optional[str] = None
    answer_artifact: Optional[AnswerArtifact] = None
    answer_artifact_gemma: Optional[AnswerArtifact] = None
    answer_artifact_solar: Optional[AnswerArtifact] = None
    final_answer_text: Optional[str] = None
    final_answer_artifact: Optional[AnswerArtifact] = None
    merge_debug: Dict[str, Any] = Field(default_factory=dict)
    selected_answer_meta: Dict[str, Any] = Field(default_factory=dict)
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    retrieval_runtime_meta: Dict[str, Any] = Field(default_factory=dict)
    answer_groundedness_snapshot: Optional[AnswerEvidenceSnapshot] = None
    answer_groundedness_verdict: Optional[AnswerGroundednessVerdict] = None
    answer_state_consistency_snapshot: Optional[AnswerStateSnapshot] = None
    answer_state_consistency_verdict: Optional[AnswerStateConsistencyVerdict] = None
    clarification: Optional[Dict[str, Any]] = None
    retrieval_bundle: Optional[Any] = None
    evidence_projection_bundle: Optional[Any] = None
    answer_context_text: str = ""
    debug_answer_context_text: str = ""
    resolved_retrieval_query: Optional[str] = None
    strategy: Optional[Any] = None
    stream_emitter: Optional[AsyncStreamEmitter] = None
    conversation_id: str = ""
    request_id: str = ""
    turn_id: str = ""
    raw_payload_memory: Dict[str, Any] = Field(default_factory=dict)
    anchor_hit: bool = False
    followup_resolved_by_facts: bool = False
    request_started_at: Optional[float] = None
    request_overrides: Dict[str, Any] = Field(default_factory=dict)
    question: str = ""
    rule_decision: Optional[RuleDecision] = None
    question_analysis: Optional[QuestionAnalysis] = None
    knowledge_sufficiency: Optional[KnowledgeSufficiency] = None
    intent_payload: Optional[IntentPayloadV3] = None
    conversation_state_card: str = ""
    agent_context_meta: Dict[str, Any] = Field(default_factory=dict)
    agent_decision: Optional[AgentDecision] = None
    agent_observation: Optional[AgentObservation] = None
    agent_tool_intent_payload: Optional[IntentPayloadV3] = None
    agent_tool_question_analysis: Optional[QuestionAnalysis] = None
    agent_loop_guard_triggered: bool = False
    search_retry_count: int = 0

    def merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
        """Merge latency fields into the aggregated execution metadata."""
        result = existing.copy()
        result.update(new)
        return result

    latencies: Annotated[Dict[str, float], merge_latencies] = Field(default_factory=dict)

    def merge_stream_meta(existing: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Merge per-stream metadata into the accumulated execution state."""
        result = existing.copy()
        result.update(new)
        return result

    stream_meta: Annotated[Dict[str, Dict[str, Any]], merge_stream_meta] = Field(default_factory=dict)


def measure_latency(node_name: str, *, logger_obj: Any):
    """Create a decorator that records node latency and logs it."""
    def decorator(func):
        """Wrap an async node with latency measurement."""
        @wraps(func)
        async def wrapper(state: AgentState, *args, **kwargs):
            """Execute the coroutine, attach latency metadata, and return the result."""
            start = time.perf_counter()
            result = await func(state, *args, **kwargs)
            elapsed = time.perf_counter() - start
            if isinstance(result, dict):
                latencies = result.get("latencies", state.latencies.copy())
                latencies[node_name] = round(elapsed, 3)
                result["latencies"] = latencies
            logger_obj.info(f"[latency]{node_name}: {elapsed:.3f}s")
            return result
        return wrapper
    return decorator






