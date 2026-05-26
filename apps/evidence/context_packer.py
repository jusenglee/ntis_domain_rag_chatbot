from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from apps.evidence.context_compression_service import compress_many_sync
from apps.evidence.source_reference import SourceReference
from apps.platform.settings import (
    RAG_CONTEXT_COMPRESS_MAX_DOCS,
    RAG_EVIDENCE_EXACT_VERIFY_FLOOR,
    RAG_EVIDENCE_EXACT_VERIFY_RATIO,
    RAG_EVIDENCE_TOKEN_BUDGET,
    RAG_OVERFLOW_QUEUE_SIZE,
)
from apps.platform.solar_tokenizer_adapter import count_json, count_serialized_units, serialize_json


@dataclass(frozen=True)
class PromptUnitCandidate:
    rank: int
    final_score: float
    envelope: Dict[str, Any]
    ref: Dict[str, Any]
    parent_anchor_key: str = ""
    turn_id: str = ""
    source_ref: Optional[SourceReference] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PackedContextResult:
    context: str
    prompt_units: List[Dict[str, Any]]
    refs: List[Dict[str, Any]]
    used_tokens: int
    kept_count: int
    discarded_count: int
    dropped_by_floor: int
    dropped_by_budget: int
    compressed_count: int
    lineages: List[Dict[str, Any]]
    source_refs: List[SourceReference] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BudgetedContextPacker:
    def __init__(
        self,
        *,
        query_text: str,
        budget_tokens: int = RAG_EVIDENCE_TOKEN_BUDGET,
        overflow_queue_size: int = RAG_OVERFLOW_QUEUE_SIZE,
        compress_max_docs: int = RAG_CONTEXT_COMPRESS_MAX_DOCS,
        exact_verify_floor: int = RAG_EVIDENCE_EXACT_VERIFY_FLOOR,
        exact_verify_ratio: float = RAG_EVIDENCE_EXACT_VERIFY_RATIO,
    ) -> None:
        self.query_text = str(query_text or "")
        self.budget_tokens = max(0, int(budget_tokens))
        self.overflow_queue_size = max(0, int(overflow_queue_size))
        self.compress_max_docs = max(0, int(compress_max_docs))
        self.exact_verify_floor = max(1, int(exact_verify_floor))
        self.exact_verify_ratio = max(0.0, float(exact_verify_ratio))

    def _candidate_tokens(self, envelope: Dict[str, Any]) -> int:
        return count_json(envelope)

    def _exact_used_tokens(self, prompt_units: List[Dict[str, Any]]) -> int:
        if not prompt_units:
            return 0
        return count_serialized_units(prompt_units)

    def _should_exact_verify(self, *, remaining_budget: int, next_candidate_tokens: int) -> bool:
        if self.budget_tokens <= 0:
            return True
        ratio = float(remaining_budget) / float(self.budget_tokens)
        return remaining_budget <= max(self.exact_verify_floor, next_candidate_tokens * 2) or ratio <= self.exact_verify_ratio

    def _try_insert(
        self,
        *,
        prompt_units: List[Dict[str, Any]],
        refs: List[Dict[str, Any]],
        source_refs: List[SourceReference],
        candidate: PromptUnitCandidate,
        provisional_used_tokens: int,
        force_exact_verify: bool = False,
    ) -> tuple[bool, int]:
        candidate_tokens = self._candidate_tokens(candidate.envelope)
        provisional_after = provisional_used_tokens + candidate_tokens
        if provisional_after > self.budget_tokens:
            return False, provisional_used_tokens
        remaining_budget = max(0, self.budget_tokens - provisional_after)
        next_prompt_units = list(prompt_units) + [candidate.envelope]
        if force_exact_verify or self._should_exact_verify(remaining_budget=remaining_budget, next_candidate_tokens=candidate_tokens):
            if self._exact_used_tokens(next_prompt_units) > self.budget_tokens:
                return False, provisional_used_tokens
        prompt_units.append(candidate.envelope)
        refs.append(candidate.ref)
        if candidate.source_ref is not None:
            source_refs.append(candidate.source_ref)
        return True, provisional_after

    def _finalize_context(
        self,
        prompt_units: List[Dict[str, Any]],
        refs: List[Dict[str, Any]],
        source_refs: List[SourceReference],
    ) -> tuple[str, int]:
        while prompt_units and self._exact_used_tokens(prompt_units) > self.budget_tokens:
            prompt_units.pop()
            if refs:
                refs.pop()
            if source_refs:
                source_refs.pop()
        context = serialize_json(prompt_units)
        used_tokens = self._exact_used_tokens(prompt_units)
        return context, used_tokens

    def pack(
        self,
        candidates: List[PromptUnitCandidate],
        *,
        dropped_by_floor: int = 0,
    ) -> PackedContextResult:
        prompt_units: List[Dict[str, Any]] = []
        refs: List[Dict[str, Any]] = []
        source_refs: List[SourceReference] = []
        overflow: List[PromptUnitCandidate] = []
        provisional_used_tokens = 0
        dropped_by_budget = 0

        for candidate in list(candidates or []):
            inserted, provisional_used_tokens = self._try_insert(
                prompt_units=prompt_units,
                refs=refs,
                source_refs=source_refs,
                candidate=candidate,
                provisional_used_tokens=provisional_used_tokens,
            )
            if inserted:
                continue
            if len(overflow) < self.overflow_queue_size:
                overflow.append(candidate)
                continue
            dropped_by_budget += 1

        lineages: List[Dict[str, Any]] = []
        compressed_count = 0
        if overflow and self.compress_max_docs > 0:
            compressed = compress_many_sync(
                [
                    {
                        "rank": candidate.rank,
                        "final_score": candidate.final_score,
                        "envelope": candidate.envelope,
                        "parent_anchor_key": candidate.parent_anchor_key,
                        "turn_id": candidate.turn_id,
                    }
                    for candidate in overflow[: self.compress_max_docs]
                ],
                query_text=self.query_text,
                max_docs=self.compress_max_docs,
            )
            for candidate, compressed_result in zip(overflow[: self.compress_max_docs], compressed):
                lineages.append(dict(compressed_result.lineage or {}))
                if not compressed_result.valid_identity_copy or compressed_result.envelope is None:
                    dropped_by_budget += 1
                    continue
                compressed_candidate = PromptUnitCandidate(
                    rank=candidate.rank,
                    final_score=candidate.final_score,
                    envelope=compressed_result.envelope,
                    ref=candidate.ref,
                    parent_anchor_key=candidate.parent_anchor_key,
                    turn_id=candidate.turn_id,
                    source_ref=candidate.source_ref,
                )
                inserted, provisional_used_tokens = self._try_insert(
                    prompt_units=prompt_units,
                    refs=refs,
                    source_refs=source_refs,
                    candidate=compressed_candidate,
                    provisional_used_tokens=provisional_used_tokens,
                    force_exact_verify=True,
                )
                if inserted:
                    compressed_count += 1
                else:
                    dropped_by_budget += 1
            dropped_by_budget += max(0, len(overflow) - self.compress_max_docs)
        else:
            dropped_by_budget += len(overflow)

        context, used_tokens = self._finalize_context(prompt_units, refs, source_refs)
        return PackedContextResult(
            context=context,
            prompt_units=list(prompt_units),
            refs=list(refs),
            used_tokens=used_tokens,
            kept_count=len(prompt_units),
            discarded_count=int(dropped_by_floor) + int(dropped_by_budget),
            dropped_by_floor=int(dropped_by_floor),
            dropped_by_budget=int(dropped_by_budget),
            compressed_count=compressed_count,
            lineages=lineages,
            source_refs=list(source_refs),
        )
