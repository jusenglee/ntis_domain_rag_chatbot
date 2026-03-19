from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from apps.core.rag_join_runtime import extract_join_keys, is_valid_join_key


@dataclass(frozen=True)
class ExecutorSupport:
    """Executor 단계가 공통으로 쓰는 보조 함수 묶음이다.

    planner/contract/runtime 경계를 넘나드는 로직을 한 객체에 모아, 실행기 본체는 전략 판단보다 조립과 검증에 집중하게 한다.
    """
    get_meta_fn: Callable[[dict], dict]
    count_missing_join_keys_fn: Callable[..., Dict[str, int]]
    resolve_group_pjt_ids_fn: Callable[..., List[str]]
    payload_title_fn: Callable[[Dict[str, Any], Dict[str, Any]], str]
    serialize_filter_for_log_fn: Callable[[Any], Any]


def serialize_filter_for_log(filter_obj: Any) -> Any:
    """복잡한 filter 객체를 로그에 남길 수 있는 순수 JSON 비슷한 구조로 바꾼다.

    Pydantic 모델이나 커스텀 객체도 가능한 범위에서 dict로 풀어 로그에 남기되, 실행 의미 자체는 바꾸지 않는다.
    """
    if filter_obj is None:
        return None
    if isinstance(filter_obj, (str, int, float, bool)):
        return filter_obj
    if isinstance(filter_obj, dict):
        return {str(k): serialize_filter_for_log(v) for k, v in filter_obj.items()}
    if isinstance(filter_obj, (list, tuple, set)):
        return [serialize_filter_for_log(v) for v in filter_obj]

    for method_name in ("model_dump", "dict"):
        method = getattr(filter_obj, method_name, None)
        if callable(method):
            try:
                dumped = method(exclude_none=True)
            except TypeError:
                dumped = method()
            return serialize_filter_for_log(dumped)

    return str(filter_obj)


def get_meta(payload: dict) -> dict:
    """payload 안의 meta_basic과 meta_detail을 합쳐 title 탐색용 메타 뷰를 만든다."""
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def count_missing_join_keys(points: Iterable[Any], *, join_key_mode: str = "instance") -> Dict[str, int]:
    """검색 결과 payload에서 join key 누락과 이상 징후를 집계한다.

    instance/group 모드를 모두 훑어 invalid 값과 swap 의심 건수를 계산해, JOIN 실패 원인을 로그로 바로 읽을 수 있게 한다.
    """
    stats = {
        "total": 0,
        "missing_pjt_id": 0,
        "missing_pjt_no": 0,
        "missing_tag": 0,
        "missing_pjt_any": 0,
        "invalid_pjt_id": 0,
        "invalid_pjt_no": 0,
        "suspected_swap": 0,
        "same_id_no": 0,
    }
    mode = str(join_key_mode or "instance").strip().lower()
    payload_points: List[Any] = []
    for point in points or []:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        payload_points.append(point)
        stats["total"] += 1
        pjt_id = str(payload.get("pjt_id") or "").strip()
        pjt_no = str(payload.get("pjt_no") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        if not pjt_id:
            stats["missing_pjt_id"] += 1
        if not pjt_no:
            stats["missing_pjt_no"] += 1
        if not tag:
            stats["missing_tag"] += 1
        if not pjt_id and not pjt_no:
            stats["missing_pjt_any"] += 1
        if pjt_id and pjt_no and pjt_id == pjt_no:
            stats["same_id_no"] += 1

    instance_keys = extract_join_keys(payload_points, mode="instance", max_ids=max(1, len(payload_points)))
    group_keys = extract_join_keys(payload_points, mode="group", max_ids=max(1, len(payload_points)))
    stats["invalid_pjt_id"] = len(instance_keys.invalid_values)
    stats["invalid_pjt_no"] = len(group_keys.invalid_values)
    stats["suspected_swap"] = instance_keys.suspected_swap_count if mode == "instance" else group_keys.suspected_swap_count
    return stats


def resolve_group_pjt_ids(points: Iterable[Any], *, max_ids: int) -> List[str]:
    """group join 결과에서 hop2 fallback에 쓸 pjt_id 후보를 수집한다.

    payload, meta_basic, point id 순으로 보되 instance key 규칙을 통과한 값만 받아들이고, 중복은 제거한다.
    """
    resolved: List[str] = []
    seen: set[str] = set()
    for point in points or []:
        payload = getattr(point, "payload", None)
        if not isinstance(payload, dict):
            continue
        meta_basic = payload.get("meta_basic") if isinstance(payload.get("meta_basic"), dict) else {}
        candidates = [
            payload.get("pjt_id"),
            meta_basic.get("pjt_id"),
            getattr(point, "id", None),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text or text in seen:
                continue
            if not is_valid_join_key(text, mode="instance"):
                continue
            seen.add(text)
            resolved.append(text)
            break
        if len(resolved) >= max_ids:
            break
    return resolved


def payload_title(payload: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """payload와 합성 메타에서 사람이 읽을 제목 후보를 우선순위대로 고른다."""
    for value in (
        payload.get("title_text"),
        payload.get("title1"),
        payload.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def build_executor_support() -> ExecutorSupport:
    """실행기에 주입할 ExecutorSupport 인스턴스를 조립한다."""
    return ExecutorSupport(
        get_meta_fn=get_meta,
        count_missing_join_keys_fn=count_missing_join_keys,
        resolve_group_pjt_ids_fn=resolve_group_pjt_ids,
        payload_title_fn=payload_title,
        serialize_filter_for_log_fn=serialize_filter_for_log,
    )
