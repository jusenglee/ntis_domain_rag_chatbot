from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Sequence

from apps.evidence.evidence_integrity import validate_identity_copy
from apps.evidence.evidence_lineage import build_compression_lineage
from apps.platform.settings import RAG_CONTEXT_COMPRESS_MAX_DOCS
from apps.platform.solar_tokenizer_adapter import count_json


@dataclass(frozen=True)
class CompressedEnvelopeResult:
    rank: int
    final_score: float
    envelope: Dict[str, Any] | None
    lineage: Dict[str, Any]
    valid_identity_copy: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _query_terms(query_text: str) -> List[str]:
    terms: List[str] = []
    for token in re.split(r"[\s,./:;()\\[\\]{}<>]+", str(query_text or "").lower()):
        token = token.strip()
        if len(token) < 2 or token in terms:
            continue
        terms.append(token)
    return terms[:12]


def _split_sentences(text: str) -> List[str]:
    normalized = str(text or "").replace("\r", "\n")
    parts = re.split(r"(?<=[.!?])\s+|\n+", normalized)
    sentences: List[str] = []
    for part in parts:
        cleaned = part.strip()
        if not cleaned:
            continue
        words = cleaned.split()
        if len(words) <= 40:
            sentences.append(cleaned)
            continue
        for index in range(0, len(words), 40):
            chunk = " ".join(words[index : index + 40]).strip()
            if chunk:
                sentences.append(chunk)
    return sentences


def _compress_body(body: Dict[str, Any], query_text: str) -> Dict[str, Any]:
    terms = _query_terms(query_text)
    compressed: Dict[str, Any] = {}

    for fixed_key in ("year", "org_nm", "lead_org_name", "period", "budget", "perf_type"):
        value = str(body.get(fixed_key) or "").strip()
        if value:
            compressed[fixed_key] = value

    for key in ("summary", "keyword_text", "summary_candidate", "content_candidate", "flat_text"):
        raw = str(body.get(key) or "").strip()
        if not raw:
            continue
        sentences = _split_sentences(raw)
        if not sentences:
            continue
        matched = [sentence for sentence in sentences if any(term in sentence.lower() for term in terms)]
        if matched:
            compressed[key] = " ".join(matched[:4]).strip()
            continue
        if key in {"summary", "keyword_text", "summary_candidate"}:
            compressed[key] = " ".join(sentences[:2]).strip()

    if not compressed:
        for key in ("summary", "summary_candidate", "keyword_text", "year", "org_nm", "lead_org_name"):
            value = str(body.get(key) or "").strip()
            if value:
                compressed[key] = value
    return compressed


def _compress_one(candidate: Dict[str, Any], query_text: str) -> CompressedEnvelopeResult:
    raw_envelope = dict(candidate.get("envelope") or {})
    stable_base = raw_envelope.get("stable_base") if isinstance(raw_envelope.get("stable_base"), dict) else {}
    query_bonus = raw_envelope.get("query_bonus") if isinstance(raw_envelope.get("query_bonus"), dict) else {}
    compressed_envelope = {
        "identity": dict(raw_envelope.get("identity") or {}),
        "stable_base": _compress_body(stable_base, query_text),
        "facts": dict(raw_envelope.get("facts") or {}),
        "query_bonus": _compress_body(query_bonus, query_text),
        "previews": dict(raw_envelope.get("previews") or {}),
        "compression": "compressed",
    }
    valid_identity_copy = validate_identity_copy(raw_envelope, compressed_envelope)
    before_tokens = count_json(raw_envelope)
    after_tokens = count_json(compressed_envelope)
    lineage = build_compression_lineage(
        original_envelope=raw_envelope,
        compressed_envelope=compressed_envelope,
        model_name="deterministic_query_projection",
        prompt_version="evidence_compress_v1",
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        valid_identity_copy=valid_identity_copy,
        parent_anchor_key=str(candidate.get("parent_anchor_key") or ""),
        turn_id=str(candidate.get("turn_id") or ""),
    ).to_dict()
    return CompressedEnvelopeResult(
        rank=int(candidate.get("rank") or 0),
        final_score=float(candidate.get("final_score") or 0.0),
        envelope=compressed_envelope if valid_identity_copy else None,
        lineage=lineage,
        valid_identity_copy=valid_identity_copy,
    )


async def compress_many(
    candidates: Sequence[Dict[str, Any]],
    *,
    query_text: str,
    max_docs: int = RAG_CONTEXT_COMPRESS_MAX_DOCS,
) -> List[CompressedEnvelopeResult]:
    selected = list(candidates or [])[: max(0, int(max_docs))]
    if not selected:
        return []
    tasks = [asyncio.to_thread(_compress_one, candidate, query_text) for candidate in selected]
    return list(await asyncio.gather(*tasks))


def compress_many_sync(
    candidates: Sequence[Dict[str, Any]],
    *,
    query_text: str,
    max_docs: int = RAG_CONTEXT_COMPRESS_MAX_DOCS,
) -> List[CompressedEnvelopeResult]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(compress_many(candidates, query_text=query_text, max_docs=max_docs))

    result: List[CompressedEnvelopeResult] = []
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            result.extend(asyncio.run(compress_many(candidates, query_text=query_text, max_docs=max_docs)))
        except BaseException as exc:  # pragma: no cover - thread bridge
            error.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return result
