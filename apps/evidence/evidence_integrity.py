from __future__ import annotations

import hashlib
from typing import Any, Dict

from apps.platform.solar_tokenizer_adapter import serialize_json


def immutable_identity_payload(envelope: Dict[str, Any]) -> Dict[str, Any]:
    identity = envelope.get("identity") if isinstance(envelope, dict) else {}
    if not isinstance(identity, dict):
        identity = {}
    ids = identity.get("ids") if isinstance(identity.get("ids"), dict) else {}
    return {
        "doc_id": str(identity.get("doc_id") or ""),
        "collection": str(identity.get("collection") or ""),
        "tag": str(identity.get("tag") or ""),
        "ids": {
            "pjt_id": str(ids.get("pjt_id") or ""),
            "pjt_no": str(ids.get("pjt_no") or ""),
            "rst_id": str(ids.get("rst_id") or ""),
            "doi": str(ids.get("doi") or ""),
            "issn": str(ids.get("issn") or ""),
        },
        "title": str(identity.get("title") or ""),
    }


def identity_hash(envelope: Dict[str, Any]) -> str:
    payload = immutable_identity_payload(envelope)
    return hashlib.sha256(serialize_json(payload).encode("utf-8")).hexdigest()


def validate_identity_copy(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    return immutable_identity_payload(before) == immutable_identity_payload(after)
