"""CitationRegistry — 인용 등기부.

LLM prompt에 박힌 prompt_units(envelope)와 SourceReference(SSOT) 사이를 rank로
연결한 단일 진실 객체. reference.set 발행과 LLM `[N]` 검증이 모두 이 객체에서
파생된다.

- by_rank: rank(=identity.rank, 패킹 최종 단계에서 dense 1..K로 재부여된 발행 번호) → SourceReference 매핑.
- prompt_units: serialize_json 입력 그대로(LLM이 본 JSON envelope 리스트).

스트리밍 정합 계약: LLM이 본문에 그대로 흘려보내는 인용 번호는 사후 재작성이 불가능하므로,
프롬프트가 보여주는 번호(identity.rank=cite, dense 1..K)가 곧 reference.set의 발행 위치와
1:1로 일치하도록 BudgetedContextPacker._finalize_context 가 직렬화 직전에 dense 번호를 부여한다.
따라서 `all_refs_in_rank_order()`(rank 오름차순)가 곧 프론트가 1..K로 번호 매기는 발행 순서다.

invariant: `set(by_rank.keys()) == {pu["identity"]["rank"] for pu in prompt_units}`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List

from apps.api.runtime_helpers import log_event
from apps.evidence.source_reference import SourceReference

if TYPE_CHECKING:
    from apps.evidence.context_packer import PackedContextResult


@dataclass(frozen=True)
class CitationRegistry:
    by_rank: Dict[int, SourceReference] = field(default_factory=dict)
    prompt_units: List[Dict[str, Any]] = field(default_factory=list)

    def validate_invariants(self) -> None:
        envelope_ranks = set()
        for pu in self.prompt_units:
            identity = pu.get("identity") if isinstance(pu, dict) else None
            if isinstance(identity, dict) and identity.get("rank") is not None:
                try:
                    envelope_ranks.add(int(identity.get("rank")))
                except (TypeError, ValueError):
                    continue
        registry_ranks = set(self.by_rank.keys())
        if envelope_ranks != registry_ranks:
            log_event(
                "REFERENCE.INVARIANT_VIOLATION",
                stage="citation_registry.validate",
                reason="prompt_rank_mismatch",
                envelope_ranks=sorted(envelope_ranks),
                registry_ranks=sorted(registry_ranks),
            )
        for rank, sref in self.by_rank.items():
            if not (sref.tag and sref.id and sref.title):
                log_event(
                    "REFERENCE.INVARIANT_VIOLATION",
                    stage="citation_registry.validate",
                    reason="empty_source_field",
                    rank=rank,
                    has_tag=bool(sref.tag),
                    has_id=bool(sref.id),
                    has_title=bool(sref.title),
                )

    def filter_by_cited_ranks(self, cited_ranks: List[int]) -> List[SourceReference]:
        """본문 등장 순서 + dedupe로 SourceReference 부분집합 반환.

        registry에 없는 rank는 그냥 건너뛴다(호출자가 별도 로그 처리).
        """
        seen: set[int] = set()
        out: List[SourceReference] = []
        for r in cited_ranks:
            if r in seen:
                continue
            ref = self.by_rank.get(r)
            if ref is None:
                continue
            seen.add(r)
            out.append(ref)
        return out

    def all_refs_in_rank_order(self) -> List[SourceReference]:
        return [self.by_rank[k] for k in sorted(self.by_rank.keys())]


def build_citation_registry(packed: "PackedContextResult") -> CitationRegistry:
    """packed.prompt_units + packed.source_refs를 rank로 zip해 registry 생성.

    rank는 _finalize_context 가 직렬화 직전에 dense 1..K로 재부여한 발행 번호이므로,
    by_rank 키는 곧 LLM이 보는 [cite] 번호이자 reference.set 의 발행 위치다.
    """
    by_rank: Dict[int, SourceReference] = {}
    prompt_units = list(packed.prompt_units or [])
    source_refs = list(packed.source_refs or [])
    for envelope, sref in zip(prompt_units, source_refs):
        identity = envelope.get("identity") if isinstance(envelope, dict) else None
        try:
            rank = int(
                (identity or {}).get("rank") if isinstance(identity, dict) else None
            )
        except (TypeError, ValueError):
            rank = int(getattr(sref, "rank", 0) or 0)
        if rank <= 0:
            rank = int(getattr(sref, "rank", 0) or 0)
        if rank <= 0:
            continue
        by_rank[rank] = sref
    registry = CitationRegistry(by_rank=by_rank, prompt_units=prompt_units)
    registry.validate_invariants()
    return registry


def derive_cited_source_refs(
    registry: CitationRegistry,
    *,
    answer_text: str,
    request_id: str = "",
    conversation_id: str = "",
) -> List[SourceReference]:
    """LLM 답변 본문에서 `[N]` 인용을 파싱해 SourceReference 부분집합으로 반환.

    - LLM이 `[N]`을 하나도 안 쓴 경우: registry 전체를 rank 순으로 송신(확정 정책).
    - registry에 없는 rank를 본문이 사용한 경우: `REFERENCE.UNKNOWN_RANK_CITED` 로그 후 건너뜀.
    - 기존 `_extract_rank_citations`(answer_state_consistency)는 max_rank로 미리 컷하므로,
      여기서는 raw 정규식으로 한 번 더 훑어 unknown rank를 탐지한다.
    """
    import re

    from apps.api.contracts.answer_state_consistency import _extract_rank_citations

    text = str(answer_text or "")
    if not text.strip() or not registry.by_rank:
        return registry.all_refs_in_rank_order()

    max_rank = max(registry.by_rank.keys())
    cited_in_range = _extract_rank_citations(text, max_rank=max_rank)

    # 본문에서 마주친 raw rank 토큰(범위 밖 포함) 탐지.
    raw_pattern = re.compile(r"\[\s*(\d+(?:\s*,\s*\d+)*)\s*\]")
    raw_ranks: list[int] = []
    for match in raw_pattern.finditer(text):
        for token in re.findall(r"\d+", match.group(1)):
            try:
                raw_ranks.append(int(token))
            except ValueError:
                continue
    unknown = [r for r in raw_ranks if r not in registry.by_rank]
    if unknown:
        log_event(
            "REFERENCE.UNKNOWN_RANK_CITED",
            request_id=request_id,
            conversation_id=conversation_id,
            stage="derive_cited_source_refs",
            unknown_ranks=sorted(set(unknown)),
            known_ranks=sorted(registry.by_rank.keys()),
        )

    if not cited_in_range:
        # LLM 미인용 정책: registry 전체 송신.
        return registry.all_refs_in_rank_order()

    return registry.filter_by_cited_ranks(cited_in_range)
