from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional


VisibleAnswerManifestPublicationStatus = Literal[
    "approved",
    "withheld_partial",
    "blocked_groundedness",
    "blocked_state_consistency",
    "blocked_projection_lineage",
    "not_applicable",
]


@dataclass(frozen=True)
class VisibleAnswerManifestPublication:
    publication_status: VisibleAnswerManifestPublicationStatus
    published_manifest: Optional[dict[str, Any]] = None
    projection_id: Optional[str] = None
    request_id: Optional[str] = None
    turn_id: Optional[str] = None
    gate_reason_codes: list[str] = field(default_factory=list)
    required_visible_count: int = 0
    accepted_visible_count: int = 0
    groundedness_status: Optional[str] = None
    state_consistency_status: Optional[str] = None
    projection_lineage_status: Optional[str] = None

    def to_meta_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "publication_status": self.publication_status,
            "published_manifest": dict(self.published_manifest) if isinstance(self.published_manifest, dict) else None,
            "projection_id": self.projection_id,
            "request_id": self.request_id,
            "turn_id": self.turn_id,
            "gate_reason_codes": list(self.gate_reason_codes or []),
            "required_visible_count": int(self.required_visible_count or 0),
            "accepted_visible_count": int(self.accepted_visible_count or 0),
            "groundedness_status": self.groundedness_status,
            "state_consistency_status": self.state_consistency_status,
            "projection_lineage_status": self.projection_lineage_status,
        }
        return {key: value for key, value in data.items() if value is not None}


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            payload = value.model_dump()
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        try:
            return dict(vars(value))
        except Exception:
            return {}
    return {}


def _status(value: Any, *, default: str = "not_applicable") -> str:
    return str(value or "").strip().lower() or default


def _coerce_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _lineage_value(
    *,
    key: str,
    projection_payload: Mapping[str, Any],
    snapshot_payload: Mapping[str, Any],
) -> Optional[str]:
    value = projection_payload.get(key) or snapshot_payload.get(key)
    text = str(value or "").strip()
    return text or None


def build_visible_answer_manifest_publication(
    *,
    publication_applicable: bool,
    snapshot_payload: Optional[Mapping[str, Any]],
    projection_payload: Optional[Mapping[str, Any]],
    projection_lineage_ok: bool,
    projection_lineage_status: str,
    groundedness: Optional[Mapping[str, Any]],
    state_consistency: Optional[Mapping[str, Any]],
) -> VisibleAnswerManifestPublication:
    snapshot = _as_dict(snapshot_payload)
    projection = _as_dict(projection_payload)
    groundedness_payload = _as_dict(groundedness)
    state_payload = _as_dict(state_consistency)
    groundedness_status = _status(groundedness_payload.get("status"))
    state_status = _status(state_payload.get("status"))
    lineage_status = _status(projection_lineage_status)
    required_visible_count = (
        _coerce_count(state_payload.get("required_visible_count"))
        or _coerce_count(snapshot.get("visible_count"))
    )
    accepted_visible_count = (
        _coerce_count(state_payload.get("accepted_item_count"))
        or _coerce_count(snapshot.get("visible_count"))
    )

    gate_reason_codes: list[str] = []
    gate_reason_codes.extend(str(code) for code in groundedness_payload.get("reason_codes") or [])
    gate_reason_codes.extend(str(code) for code in state_payload.get("reason_codes") or [])

    status: VisibleAnswerManifestPublicationStatus = "not_applicable"
    published_manifest: Optional[dict[str, Any]] = None
    if publication_applicable:
        if projection and not projection_lineage_ok:
            status = "blocked_projection_lineage"
            gate_reason_codes.append("projection_lineage_mismatch")
        elif not snapshot:
            status = "not_applicable"
        elif groundedness_status == "unsupported":
            status = "blocked_groundedness"
        # ADR-0017: supported_remapped 도 발행 허용. 매핑 정보(rank_remap)는 호출자가 state_payload에서 직접 꺼내 활용한다.
        elif state_status not in ("supported", "supported_remapped"):
            status = "blocked_state_consistency"
        elif bool(state_payload.get("subset_accepted")) and not bool(state_payload.get("manifest_publish_allowed")):
            status = "withheld_partial"
        else:
            status = "approved"
            published_manifest = dict(snapshot)

    return VisibleAnswerManifestPublication(
        publication_status=status,
        published_manifest=published_manifest,
        projection_id=_lineage_value(key="projection_id", projection_payload=projection, snapshot_payload=snapshot),
        request_id=_lineage_value(key="request_id", projection_payload=projection, snapshot_payload=snapshot),
        turn_id=_lineage_value(key="turn_id", projection_payload=projection, snapshot_payload=snapshot),
        gate_reason_codes=gate_reason_codes,
        required_visible_count=required_visible_count,
        accepted_visible_count=accepted_visible_count,
        groundedness_status=groundedness_status,
        state_consistency_status=state_status,
        projection_lineage_status=lineage_status,
    )
