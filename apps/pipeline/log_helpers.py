"""Pipeline 노드 공용 로깅 헬퍼.

긴 텍스트 미리보기·SearchTask 요약·CanonicalEvidence 제목 추출 같은 공통 포맷팅을 모아두어
각 노드의 로그가 일관되고 grep 가능한 key=value 형태를 유지하도록 한다.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Optional

from apps.pipeline.contracts import (
    CanonicalEvidence,
    FilterBundle,
    IdentifierBundle,
    SearchTask,
    SubjectAnchor,
)


def preview(text: Any, *, limit: int = 80) -> str:
    """긴 텍스트를 한 줄로 요약. 줄바꿈은 공백으로 평탄화한다."""
    if text is None:
        return ""
    s = str(text).replace("\n", " ").replace("\r", " ").strip()
    if len(s) <= limit:
        return s
    return s[:limit] + "…"


def subject_log(subject: Optional[SubjectAnchor]) -> str:
    """SubjectAnchor를 key=value 한 줄 표현."""
    if subject is None:
        return "none"
    parts = [
        f"kind={subject.kind}",
        f"name={subject.display_name!r}",
        f"id_status={subject.identity_status}",
    ]
    if subject.person_no:
        parts.append(f"person_no={subject.person_no!r}")
    if subject.org_id:
        parts.append(f"org_id={subject.org_id!r}")
    if subject.affiliation_org_name:
        parts.append(f"aff={subject.affiliation_org_name!r}")
    return "{" + " ".join(parts) + "}"


def identifiers_log(ids: IdentifierBundle) -> str:
    """IdentifierBundle 요약. 비어있지 않은 축만 표시."""
    parts: List[str] = []
    for axis in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id"):
        values = getattr(ids, axis)
        if values:
            parts.append(f"{axis}={values}")
    return "{" + " ".join(parts) + "}" if parts else "{}"


def filters_log(filters: FilterBundle) -> str:
    """FilterBundle 요약. 비어있지 않은 필드만 표시."""
    parts: List[str] = []
    if filters.year_from is not None:
        parts.append(f"year_from={filters.year_from}")
    if filters.year_to is not None:
        parts.append(f"year_to={filters.year_to}")
    if filters.perf_type:
        parts.append(f"perf_type={filters.perf_type}")
    if filters.lead_org_name:
        parts.append(f"lead_org={filters.lead_org_name}")
    if filters.participant_org_name:
        parts.append(f"part_org={filters.participant_org_name}")
    if filters.domain_keywords:
        parts.append(f"kw={filters.domain_keywords}")
    return "{" + " ".join(parts) + "}" if parts else "{}"


def search_task_log(task: SearchTask) -> str:
    """SearchTask 한 줄 요약 (로그 1줄 분량)."""
    return (
        f"action={task.action} target={task.target} axis={task.axis} strategy={task.strategy} "
        f"limit={task.limit} display_limit={task.display_limit} "
        f"subject={subject_log(task.subject)} ids={identifiers_log(task.identifiers)} "
        f"filters={filters_log(task.filters)} collections={list(task.collections)} "
        f"q={preview(task.retrieval_query, limit=60)!r}"
    )


def evidence_titles_preview(
    evidences: Iterable[CanonicalEvidence],
    *,
    max_items: int = 3,
    title_limit: int = 40,
) -> str:
    """canonical evidence 리스트의 상위 몇 개 제목을 보기 좋게 요약."""
    titles: List[str] = []
    for i, ev in enumerate(evidences):
        if i >= max_items:
            titles.append("…")
            break
        titles.append(f"#{ev.snapshot_rank}({preview(ev.title, limit=title_limit)})")
    return "[" + ", ".join(titles) + "]" if titles else "[]"


def short_id(value: Any, *, prefix_len: int = 8) -> str:
    """request_id 등 긴 ID를 앞 부분만 잘라 표시."""
    if value is None:
        return ""
    s = str(value)
    if len(s) <= prefix_len:
        return s
    return s[:prefix_len] + "…"
