from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict

from apps.evidence.evidence_integrity import identity_hash


@dataclass(frozen=True)
class CompressionLineage:
    parent_anchor_key: str
    turn_id: str
    parent_doc_id: str
    identity_hash_before: str
    identity_hash_after: str
    model_name: str
    prompt_version: str
    before_tokens: int
    after_tokens: int
    valid_identity_copy: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompressedEnvelopeArtifact:
    parent_doc_id: str
    envelope: Dict[str, Any]
    compression: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_compression_lineage(
    *,
    original_envelope: Dict[str, Any],
    compressed_envelope: Dict[str, Any],
    model_name: str,
    prompt_version: str,
    before_tokens: int,
    after_tokens: int,
    valid_identity_copy: bool,
    parent_anchor_key: str = "",
    turn_id: str = "",
) -> CompressionLineage:
    identity = original_envelope.get("identity") if isinstance(original_envelope, dict) else {}
    if not isinstance(identity, dict):
        identity = {}
    ids = identity.get("ids") if isinstance(identity.get("ids"), dict) else {}
    anchor_key = str(parent_anchor_key or "").strip()
    if not anchor_key:
        pjt_id = str(ids.get("pjt_id") or "").strip()
        rst_id = str(ids.get("rst_id") or "").strip()
        pjt_no = str(ids.get("pjt_no") or "").strip()
        collection = str(identity.get("collection") or "").strip()
        doc_id = str(identity.get("doc_id") or "").strip()
        if pjt_id:
            anchor_key = f"project:{pjt_id}"
        elif rst_id:
            anchor_key = f"result:{rst_id}"
        elif pjt_no:
            anchor_key = f"project_no:{pjt_no}"
        elif collection and doc_id:
            anchor_key = f"{collection}:{doc_id}"
    return CompressionLineage(
        parent_anchor_key=anchor_key,
        turn_id=str(turn_id or "").strip(),
        parent_doc_id=str(identity.get("doc_id") or ""),
        identity_hash_before=identity_hash(original_envelope),
        identity_hash_after=identity_hash(compressed_envelope),
        model_name=str(model_name or ""),
        prompt_version=str(prompt_version or ""),
        before_tokens=int(before_tokens),
        after_tokens=int(after_tokens),
        valid_identity_copy=bool(valid_identity_copy),
    )
