"""SourceReference — reference.set 출처 단일 진실(SSOT).

Qdrant hit 진입점(`build_context_bundle` survivors 루프)에서 한 번 생성되어
`CanonicalEvidence` → `PromptUnitCandidate` → `CitationRegistry` → `reference.set`
까지 lossless로 운반된다. 중간 계층은 절대 재추론하지 않는다.

프론트 송신 페이로드는 `to_frontend_payload()`로만 직렬화하여 `{id, tag, title}`
3필드만 노출한다. 진단/로그용 필드(rank/doc_id/score 등)는 백엔드 내부에서만 사용.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict

from apps.api.rag_mapper.schema_types import DataTag


@dataclass(frozen=True)
class SourceReference:
    rank: int
    tag: str
    id: str
    title: str
    doc_id: str = ""
    source_type: str = ""
    collection: str = ""
    final_score: float = 0.0

    def to_frontend_payload(self) -> Dict[str, str]:
        return {"id": self.id, "tag": self.tag, "title": self.title}

    def to_debug_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def build_source_reference(
    *,
    payload: Dict[str, Any],
    canonical: Any,
    rank: int,
    final_score: float,
) -> SourceReference:
    """canonical_evidence + payload에서 SourceReference를 확정한다.

    실패 시 `ValueError`를 던진다. 호출자는 이를 `REFERENCE.INVARIANT_VIOLATION`으로
    로깅한 뒤 해당 candidate를 skip 한다(Stage 1 정책). 추후 안정화되면 raise로
    격상하여 RAG 전체 실패로 처리할 수 있다.
    """
    payload_dict = payload if isinstance(payload, dict) else {}

    ids = getattr(canonical, "ids", None)
    if isinstance(canonical, dict):
        ids = canonical.get("ids") if isinstance(canonical.get("ids"), dict) else {}
    if not isinstance(ids, dict):
        ids = {}

    facts = getattr(canonical, "facts", None)
    if isinstance(canonical, dict):
        facts = canonical.get("facts") if isinstance(canonical.get("facts"), dict) else {}
    if not isinstance(facts, dict):
        facts = {}

    evidence = getattr(canonical, "evidence", None)
    if isinstance(canonical, dict):
        evidence = canonical.get("evidence") if isinstance(canonical.get("evidence"), dict) else {}
    if not isinstance(evidence, dict):
        evidence = {}

    source_type = _clean_text(
        getattr(canonical, "source_type", None)
        if not isinstance(canonical, dict)
        else canonical.get("source_type")
    ) or _clean_text(payload_dict.get("source_type"))

    tag = _clean_text(facts.get("tag")) or _clean_text(payload_dict.get("tag"))
    if not tag:
        raise ValueError(
            f"SourceReference 생성 실패: tag 누락 (rank={rank}, source_type={source_type or '?'})"
        )

    if tag == DataTag.PROJECT.value:
        ref_id = _clean_text(ids.get("pjt_id"))
    else:
        ref_id = _clean_text(ids.get("rst_id"))
    if not ref_id:
        # tag와 id 축이 어긋난 경우(예: project tag인데 pjt_id 없음) → 보조 키 fallback.
        # 다만 정상 경로에선 발생하지 않아야 한다는 가정. 발생 시 호출자가 로깅.
        ref_id = _clean_text(
            ids.get("pjt_id")
            or ids.get("rst_id")
            or ids.get("doc_id")
            or payload_dict.get("id")
        )
    if not ref_id:
        raise ValueError(
            f"SourceReference 생성 실패: id 누락 (rank={rank}, tag={tag})"
        )

    title = _clean_text(facts.get("title")) or _clean_text(evidence.get("title_text"))
    if not title:
        title = _clean_text(
            payload_dict.get("title_text")
            or payload_dict.get("title1")
            or payload_dict.get("title2")
        )
    if not title:
        raise ValueError(
            f"SourceReference 생성 실패: title 누락 (rank={rank}, tag={tag}, id={ref_id})"
        )

    doc_id = _clean_text(ids.get("doc_id")) or _clean_text(payload_dict.get("doc_id"))
    collection = _clean_text(payload_dict.get("_collection")) or _clean_text(
        payload_dict.get("collection")
    )

    return SourceReference(
        rank=int(rank),
        tag=tag,
        id=ref_id,
        title=title,
        doc_id=doc_id,
        source_type=source_type,
        collection=collection,
        final_score=float(final_score or 0.0),
    )
