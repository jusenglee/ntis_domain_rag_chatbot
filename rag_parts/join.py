# -*- coding: utf-8 -*-
"""
rag_parts/join.py

JOIN(2-hop)에서 Hop1 결과(예: 과제/참여인력/기관 등)로부터 join-key(PJT_ID)를 추출하고,
질의에서 불필요한 용어를 제거(sanitize)하기 위한 유틸.

- extract_join_keys(points, mode, max_ids): mode(instance/group)에 맞는 JOIN key를 추출/검증
- extract_pjt_ids(points): extract_join_keys(instance) 하위호환 래퍼
- sanitize_query_by_terms(q, remove_terms): 제거 대상 용어를 질의에서 제거(공백 정리)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

_PJT_NO_ALLOWED_RE = re.compile(os.getenv("RAG_JOIN_PJT_NO_ALLOWED_RE", r"^[A-Za-z0-9_-]{4,40}$"))


@dataclass(frozen=True)
class JoinKeySwapHint:
    index: int
    expected_key: str
    candidate: str
    swap_candidate: str


@dataclass(frozen=True)
class JoinKeyExtractionResult:
    keys: List[str]
    invalid_values: List[str]
    suspected_swaps: List[JoinKeySwapHint]

    @property
    def suspected_swap_count(self) -> int:
        return len(self.suspected_swaps)

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "keys": list(self.keys),
            "invalid_values": list(self.invalid_values),
            "suspected_swaps": [
                {
                    "index": hint.index,
                    "expected_key": hint.expected_key,
                    "candidate": hint.candidate,
                    "swap_candidate": hint.swap_candidate,
                }
                for hint in self.suspected_swaps
            ],
            "suspected_swap_count": self.suspected_swap_count,
        }


def _as_dict(x: Any) -> Optional[Dict[str, Any]]:
    return x if isinstance(x, dict) else None


def _get_payload(point: Any) -> Optional[Dict[str, Any]]:
    # qdrant_client ScoredPoint: .payload
    pl = getattr(point, "payload", None)
    if isinstance(pl, dict):
        return pl
    # dict-like (테스트/모킹)
    d = _as_dict(point)
    if d is not None:
        pl2 = d.get("payload")
        if isinstance(pl2, dict):
            return pl2
        # 혹시 point 자체가 payload인 경우
        return d
    return None


def is_valid_join_key(value: Any, *, mode: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    mode_norm = str(mode or "instance").strip().lower()
    if mode_norm == "group":
        return bool(_PJT_NO_ALLOWED_RE.fullmatch(text))
    # instance(pjt_id)는 존재 여부만 확인하고 형식 검증은 하지 않는다.
    return True


def extract_join_keys(points: Iterable[Any], *, mode: str = "instance", max_ids: int = 80) -> JoinKeyExtractionResult:
    """JOIN key 추출 SSOT.

    Returns:
        {
            "keys": List[str],
            "invalid_values": List[str],
            "suspected_swaps": List[dict],
            "suspected_swap_count": int,
        }
    """
    mode_norm = str(mode or "instance").strip().lower()
    if mode_norm not in {"instance", "group"}:
        raise ValueError(f"unsupported join key mode: {mode_norm}")
    target_key = "pjt_no" if mode_norm == "group" else "pjt_id"
    other_key = "pjt_id" if target_key == "pjt_no" else "pjt_no"

    keys: List[str] = []
    seen: set[str] = set()
    invalid_values: List[str] = []
    suspected_swaps: List[JoinKeySwapHint] = []

    if not points:
        return JoinKeyExtractionResult(keys=keys, invalid_values=invalid_values, suspected_swaps=suspected_swaps)

    for idx, p in enumerate(points):
        payload = _get_payload(p)
        if not payload:
            continue

        target_value = str(payload.get(target_key) or "").strip()
        other_value = str(payload.get(other_key) or "").strip()

        if target_value and not is_valid_join_key(target_value, mode=mode_norm):
            invalid_values.append(target_value)
            if other_value and is_valid_join_key(other_value, mode=mode_norm):
                suspected_swaps.append(
                    JoinKeySwapHint(
                        index=idx,
                        expected_key=target_key,
                        candidate=target_value,
                        swap_candidate=other_value,
                    )
                )
            continue

        if not target_value:
            continue
        if target_value in seen:
            continue
        keys.append(target_value)
        seen.add(target_value)
        if len(keys) >= max_ids:
            break

    return JoinKeyExtractionResult(keys=keys, invalid_values=invalid_values, suspected_swaps=suspected_swaps)


def extract_pjt_ids(points: Iterable[Any], *, max_ids: int = 80) -> List[str]:
    return extract_join_keys(points, mode="instance", max_ids=max_ids).keys

def normalize_relation_hint(value: Any) -> Optional[Tuple[str, str]]:
    if not value:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (str(value[0]).strip().lower(), str(value[1]).strip().lower())
    text = str(value).strip().lower()
    if not text:
        return None
    if "_" in text:
        parts = [p.strip() for p in text.split("_") if p.strip()]
        if len(parts) == 2:
            return (parts[0], parts[1])
    return None
