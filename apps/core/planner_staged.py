from __future__ import annotations

"""Stagewise planner and deterministic gate helpers."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

from apps.core.schemas import default_target_collections_for_route

Mode = Literal["SEARCH", "LOOKUP", "JOIN"]
Head = Literal["project", "perf", "people", "org", "support"]
Action = Literal["topic", "list", "detail", "stats", "download"]
Relation = Literal["project_perf", "perf_project"]
JoinKeyMode = Literal["instance", "group"]

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


@dataclass(frozen=True)
class LockedStrategy:
    """stage 1에서 고정된 planner 전략 필드를 묶어 두는 구조체다.
    stage 2는 ids·filters·retrieval_query·limit만 채우고, mode·relation·join_key_mode·target_cols는 이 값을 지켜야 한다.
    """

    mode: Mode
    head: Head
    action: Action
    relation: Optional[Relation] = None
    join_key_mode: Optional[JoinKeyMode] = None
    target_cols: List[str] = field(default_factory=list)
    output_type: str = "summary"
    prev_context_seed: Dict[str, List[str]] = field(default_factory=dict)
    gate_seed_map: Dict[str, List[str]] = field(default_factory=dict)

    def to_prompt_payload(self) -> Dict[str, Any]:
        """LockedStrategy를 planner prompt에 다시 넣을 수 있는 dict로 펼어준다.
        stage 2가 보는 locked truth를 dataclass 형태 그대로 직렬화하는 엔트리 포인트다.
        """
        return asdict(self)


def _action_to_output_type(action: str, relation: Optional[str]) -> str:
    """action과 relation으로 prompt view `output_type`를 결정한다.
    relation이 있으면 문서 렌더링은 relation 뷰로 고정되고, 그 외에는 action에 맞는 summary/list/detail/stats를 고른다.
    """
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
    """locked strategy가 기본으로 가리키는 target collection 조합을 정한다.
    JOIN relation은 hop 순서를 따라 project/perf 조합을 고정하고, 그 외에는 route 기본 allowlist를 쓴다.
    """
    if relation == PROJECT_TO_PERF:
        return ["ntis_project_v1", "ntis_perf_v1"]
    if relation == PERF_TO_PROJECT:
        return ["ntis_perf_v1", "ntis_project_v1"]
    return list(default_target_collections_for_route(head))


def has_join_seed(ids_map: Dict[str, List[str]]) -> bool:
    """ids_map에 strict JOIN을 시도할 시드가 있는지 검사한다.
    `pjt_id`·`pjt_no`·성과 식별자 조합을 보고 relation과 함께 JOIN 모드로 고정할 수 있는지 판단한다.
    """
    ids_map = ids_map if isinstance(ids_map, dict) else {}
    return bool(ids_map.get("pjt_id") or ids_map.get("pjt_no") or any(ids_map.get(k) for k in _PERF_SEED_KEYS))


def collect_regate_seed_map(ids_map: Dict[str, List[str]], *, allowed_keys: set[str]) -> Dict[str, List[str]]:
    """regate에 사용할 시드 맵을 allowed key만 남겨 정규화한다.
    stage 2가 새로 뽑은 seed가 planner 재판단을 일으키는지 보려면 키 범위와 값 중복을 먼저 정리해야 한다.
    """
    out: Dict[str, List[str]] = {}
    for key, values in (ids_map or {}).items():
        if key not in allowed_keys and not str(key).startswith("patent_"):
            continue
        normalized = sorted({str(v).strip() for v in (values or []) if str(v).strip()})
        if normalized:
            out[str(key)] = normalized
    return out


def extract_single_project_seed(prev_context: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """이전 context에서 유일한 project seed가 있으면 `pjt_id` 또는 `pjt_no`로 뽑아낸다.
    참조 대화 후속 질의가 명시 id 없이도 JOIN으로 이어질 수 있는지 판단하는 기반 시드다.
    """
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
    """stage 2에서 새로 얻은 seed가 기존 gate seed에 없었는지 검사한다.
    실제로 새 식별자가 생긴 경우에만 regate를 허용해 planner가 불필요하게 모드를 자주 바꾸지 않게 한다.
    """
    for key, values in stage2_seed_map.items():
        base_values = set(base_seed_map.get(key) or [])
        if any(value not in base_values for value in values):
            return True
    return False


def regate_locked_strategy(
    *,
    request_id: Optional[str],
    conversation_id: str,
    stage1: Any,
    stage2: Any,
    locked_strategy: LockedStrategy | Dict[str, Any],
    allowed_keys: set[str],
    log_event: Any,
) -> LockedStrategy | Dict[str, Any]:
    """stage 2 seed가 추가되었을 때 locked strategy를 한 번 더 재계산할지 판정한다.
    SEARCH/LOOKUP에서만 regate를 허용하고, 변경 여부를 로그로 남기어 planner가 언제 JOIN으로 상향되었는지 추적할 수 있게 한다.
    """
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

    updated: LockedStrategy | Dict[str, Any] = locked_strategy
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

    def _locked_field(value: LockedStrategy | Dict[str, Any], field: str) -> Any:
        """dict 또는 LockedStrategy에서 같은 필드를 읽어 비교에 쓰는 내부 헬퍼다.
        regate 전후 로그를 남길 때 구조체 형식 차이를 신경 쓰지 않기 위해 두었다.
        """
        return value.get(field) if isinstance(value, dict) else getattr(value, field)

    changed = any(
        _locked_field(updated, field) != _locked_field(locked_strategy, field)
        for field in ("mode", "relation", "join_key_mode", "target_cols")
    )
    log_event(
        "PLANNER.REGATE",
        request_id=request_id,
        conversation_id=conversation_id,
        regate_eligible=int(can_regate),
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
) -> LockedStrategy:

    """stage 1 결과, 시드, 참조 context 여부로 locked strategy를 조합한다.
    relation과 explicit join seed, previous anchor를 함께 보고 SEARCH/LOOKUP/JOIN 모드를 고정하며, output_type·target_cols도 같이 결정한다.
    """
    action = str(stage1.get("action") or "topic").strip().lower() or "topic"
    head = str(stage1.get("head") or "project").strip().lower() or "project"
    relation = str(stage1.get("relation_candidate") or "").strip().lower() or None

    ids_map = ids_map if isinstance(ids_map, dict) else {}
    has_project_seed = bool(ids_map.get("pjt_id") or ids_map.get("pjt_no"))
    has_perf_seed = any(ids_map.get(k) for k in _PERF_SEED_KEYS)
    explicit_join_seed = has_project_seed or has_perf_seed

    join_key_mode: Optional[JoinKeyMode] = None
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
    else:
        mode = "LOOKUP"
        relation = None
        join_key_mode = None

    locked_head = relation.split("_", 1)[1] if mode == "JOIN" and relation else head

    return LockedStrategy(
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


def merge_locked_strategy_slots(*, schema_version: str, locked: LockedStrategy, slots: Dict[str, Any], default_query: Optional[str] = None) -> Dict[str, Any]:

    """locked strategy와 stage 2 slots를 합친 최종 planner payload를 만든다.
    stage 2가 바꾸면 안 되는 필드는 locked truth에서 가져오고, 가변 슬롯만 붙여 v3-staged schema를 완성한다.
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
        "filters": dict(slots.get("filters") or {}),
        "limit": int(slots.get("limit") or 20),
        "retrieval_query": slots.get("retrieval_query") or default_query,
        "confidence": float(slots.get("confidence") or 0.0),
    }
