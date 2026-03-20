from __future__ import annotations

import logging
import time
from typing import Annotated, Any, Dict, List, Literal, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.core.schemas import IntentPayloadV3
from apps.core.settings import MAX_TOP_K_SIZE
from apps.core.storage import KVStore


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
    "retrieval_query",
    "confidence",
}

logger = logging.getLogger("Chatbot_Server")

Mode = Literal["SEARCH", "LOOKUP", "JOIN"]
Head = Literal["project", "perf", "people", "org", "support"]
Action = Literal["topic", "list", "detail", "stats", "download"]


class QuestionAnalysisV3(BaseModel):
    """planner가 내놓은 question analysis payload를 엄격한 타입과 validator로 고정한다.
    모드·head·relation·ids_map·filters가 source of truth인 구조체로 정리되어 runtime으로 넘어가게 하는 계약 계층이다.
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
    retrieval_query: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
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
        """planner가 내린 반정형 payload를 `QuestionAnalysisV3`가 받을 정규형으로 정리한다.
        alias head/action, relation tuple, ids_map 키, filter alias를 이 단계에서 수렴해 후속 validator가 의미만 검사하게 만든다.
        """
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
            """입력값을 planner contract에서 쓸 문자열 리스트로 정규화한다.
            `None`, 빈 문자열, 하나의 scalar 값을 다 흡수해 filters와 ids_map 정리가 같은 방식으로 흘러가게 한다.
            """
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
            """기존 리스트에 중복 없이 새 값을 병합한다.
            filter alias가 여러 개 들어와도 결국 canonical filter에는 중복 없이 남게 하는 보조 헬퍼다.
            """
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
        """question analysis가 현재 planner schema version과 맞는지 검사한다.
        구버전 payload가 섞여 들어오면 runtime이 구현하지 않는 필드 의미를 가정할 수 있으므로 여기서 fail-close한다.
        """
        if value != PLANNER_SCHEMA_VERSION:
            raise ValueError(f"strategy_version must be {PLANNER_SCHEMA_VERSION!r}")
        return value

    @model_validator(mode="after")
    def validate_join_contract(self) -> "QuestionAnalysisV3":
        """JOIN 모드에서 relation, join_key_mode, ids_map의 정합성을 검사한다.
        `pjt_id`와 `pjt_no`를 섞거나 non-join에 join key를 심는 코드 경로를 이 validator가 초기에 차단한다.
        """
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
        return self


QuestionAnalysis = QuestionAnalysisV3
QuestionAnalysisV2 = QuestionAnalysisV3


class PlannerV3ParseError(ValueError):
    """Strict parse error for missing planner schema fields."""
    pass


PlannerV2ParseError = PlannerV3ParseError

def validate_question_analysis_required_keys(payload: Dict[str, Any]) -> None:
    """planner raw payload에 필수 키가 전부 들어 있는지 점검한다.
    스테이지워이즈 planner가 누락한 사항을 runtime이 보정하려 들기 전에 계약 위반으로 잡아낸다.
    """
    missing_keys = sorted(QUESTION_ANALYSIS_REQUIRED_KEYS - set(payload.keys()))
    if missing_keys:
        raise PlannerV3ParseError(f"missing required keys: {missing_keys}")


def planner_backoff_seconds(*, attempt_no: int, retry_backoff_sec: float, backoff_cap_sec: float) -> float:
    """planner 재시도 사이의 exponential backoff 대기 시간을 계산한다.
    연속 실패 시 planner provider를 과도하게 두드리지 않으면서도 초기 재시도는 빠르게 시도하도록 한다.
    """
    backoff = retry_backoff_sec * (2 ** max(0, attempt_no - 1))
    return min(backoff, backoff_cap_sec)


planner_v2_backoff_seconds = planner_backoff_seconds


def merge_bool_flag(existing: bool, new: bool) -> bool:
    """streaming 누적 메타에서 boolean flag를 OR semantics으로 병합한다.
    한 번이라도 참이 된 상태를 놓치면 안 되는 deadline 부류 meta를 합치는 데 쓴다.
    """
    return bool(existing) or bool(new)


class KnowledgeSufficiency(BaseModel):
    """Shared planner/runtime model for pre-retrieval knowledge sufficiency."""
    requires_new_knowledge: Literal["low", "medium", "high"]
    search_intent: str
    retrieval_query: str
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
    merge_debug: Dict[str, Any] = Field(default_factory=dict)
    conversation_id: str = ""
    request_id: str = ""
    request_started_at: Optional[float] = None
    question: str = ""
    rule_decision: Optional[RuleDecision] = None
    question_analysis: Optional[QuestionAnalysis] = None
    knowledge_sufficiency: Optional[KnowledgeSufficiency] = None
    intent_payload: Optional[IntentPayloadV3] = None

    def merge_latencies(existing: Dict[str, float], new: Dict[str, float]) -> Dict[str, float]:
        """여러 latency field를 누적 meta에 병합하되 가장 의미 있는 값을 남긴다.
        최대치가 맞는 항목과 마지막 값이 맞는 항목을 구분해 stream summary가 실제 체감 latency를 가까운 값으로 보존하게 한다.
        """
        result = existing.copy()
        result.update(new)
        return result

    latencies: Annotated[Dict[str, float], merge_latencies] = Field(default_factory=dict)

    def merge_stream_meta(existing: Dict[str, Dict[str, Any]], new: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """청크별 stream meta를 하나의 누적 구조로 합친다.
        error/deadline/latency/usage 같은 후속 로그 항목이 이 함수를 통해 일관된 shape를 유지한다.
        """
        result = existing.copy()
        result.update(new)
        return result

    stream_meta: Annotated[Dict[str, Dict[str, Any]], merge_stream_meta] = Field(default_factory=dict)


def measure_latency(node_name: str, *, logger_obj: Any):
    """비동기 함수의 실행 시간을 재고 로그를 남기는 decorator를 만든다.
    워크플로우 node나 planner call의 목병을 찾을 때 별도 계측 코드를 안 심고도 시간 로그를 붙일 수 있게 한다.
    """
    def decorator(func):
        """실제 비동기 wrapper를 생성하는 데코레이터 팩토리다.
        로거와 메시지 템플릿을 바깥 스코프에서 고정한 뒤 wrapper에 전달한다.
        """
        async def wrapper(state: AgentState, *args, **kwargs):
            """대상 coroutine를 실행하고 경과 시간을 로그로 남긴다.
            정상 종료와 예외 종료 모두에서 시간을 계측해, 실패한 경로도 눈에 들어오게 한다.
            """
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


