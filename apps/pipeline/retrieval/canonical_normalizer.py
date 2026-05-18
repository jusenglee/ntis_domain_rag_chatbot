"""Qdrant point → pipeline.contracts.CanonicalEvidence 변환.

기존 apps.evidence.canonical_evidence 모듈의 build_canonical_evidence 로직을 그대로 활용하되,
pipeline의 Pydantic CanonicalEvidence 모델로 감싸 검증을 보강한다.
"""

from __future__ import annotations

from typing import Any, List, Sequence

from apps.evidence.canonical_evidence import build_canonical_evidence
from apps.pipeline.contracts import CanonicalEvidence


def normalize_qdrant_points(
    points: Sequence[Any],
    *,
    base_route: str,
    action: str,
) -> List[CanonicalEvidence]:
    """Qdrant scored point 시퀀스를 CanonicalEvidence 리스트로 변환.

    Args:
        points: Qdrant client가 돌려준 scored_points 또는 records. 각 원소는 ``payload`` 속성을 가짐.
        base_route: target에 대응되는 base route ("project" / "perf" / "people" / "org")
        action: 액션 문자열 (list/detail/...) — provenance.output_type으로 기록

    Returns:
        snapshot_rank가 1부터 채워진 CanonicalEvidence 리스트.
    """

    evidences: List[CanonicalEvidence] = []
    for rank, point in enumerate(list(points or []), start=1):
        payload = getattr(point, "payload", None)
        if not isinstance(payload, dict) or not payload:
            continue

        # 기존 helper로 raw → canonical dataclass 변환 후 pipeline 모델로 감싸기
        raw = build_canonical_evidence(
            payload,
            rank=rank,
            base_route=base_route,
            output_type=action,
        )
        raw_dict = raw.to_dict()

        score = float(getattr(point, "score", 0.0) or 0.0)
        tag = raw_dict.get("facts", {}).get("tag") or payload.get("tag")

        evidences.append(
            CanonicalEvidence(
                identity=raw_dict.get("identity", ""),
                source_type=raw_dict.get("source_type", base_route),
                tag=tag,
                ids=raw_dict.get("ids", {}),
                title=raw_dict.get("facts", {}).get("title", "") or "",
                summary=raw_dict.get("facts", {}).get("summary", "") or "",
                facts=raw_dict.get("facts", {}),
                roles=raw_dict.get("roles", {}),
                child_entities=raw_dict.get("child_entities", []),
                provenance=raw_dict.get("provenance", {}),
                score=score,
                snapshot_rank=rank,
            )
        )

    return evidences
