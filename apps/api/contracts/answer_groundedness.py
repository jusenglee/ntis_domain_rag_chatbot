from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


GroundednessStatus = Literal[
    "empty_answer",
    "skipped_bypass_kind",
    "no_structured_claims",
    "insufficient_snapshot",
    "supported",
    "unsupported",
    "skipped_fallback",
]

UnsupportedReason = Literal[
    "unsupported_project_id",
    "unsupported_project_no",
    "unsupported_perf_id",
    "unsupported_year",
    "unsupported_org_name",
    "unsupported_budget",
    "unsupported_period",
    "unsupported_count",
]

ClaimKind = Literal["project_id", "project_no", "perf_id", "year", "org_name", "budget", "period", "count"]
GroundednessPolicyName = Literal["default", "detail_structured"]

BYPASS_ANSWER_KINDS = {"detail_cache", "detail_profile", "no_result", "clarification", "direct_answer", "error"}

_PROJECT_ID_PATTERNS = (
    re.compile(r"(?:pjt[_\s-]?id|project[_\s-]?id)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
    re.compile(r"(?:\uACFC\uC81C)\s*id\s*(?:\uC740|\uB294|[:=])?\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
)
_PROJECT_NO_PATTERNS = (
    re.compile(r"(?:pjt[_\s-]?no|project[_\s-]?no)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{2,})", re.IGNORECASE),
    re.compile(r"(?:\uACFC\uC81C)\s*(?:\uBC88\uD638|no)\s*(?:\uC740|\uB294|[:=])?\s*([A-Za-z0-9-]{2,})", re.IGNORECASE),
)
_PERF_ID_PATTERNS = (
    re.compile(r"(?:rst[_\s-]?id|perf[_\s-]?id)\s*(?:[:=]\s*|\()\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
    re.compile(r"(?:\uC131\uACFC)\s*(?:id|\uBC88\uD638)\s*(?:\uC740|\uB294|[:=])?\s*([A-Za-z0-9-]{4,})", re.IGNORECASE),
)
_YEAR_PATTERNS = (
    re.compile(r"(?:\uC5F0\uB3C4|\uB144\uB3C4|year)\s*(?:\uC740|\uB294|[:=])?\s*((?:19|20)\d{2})", re.IGNORECASE),
    re.compile(
        r"((?:19|20)\d{2})\s*(?:\uB144|\uB144\uB3C4)\s*"
        r"(?:\uC5D0|\uBD80\uD130|\uAE4C\uC9C0|\uAE30\uC900|\uC218\uD589|\uC9C4\uD589|\uC2DC\uC791|\uC885\uB8CC|\uC120\uC815|\uBC1C\uD589|\uC785\uB2C8\uB2E4|\uC785\uB2C8\uAE4C|\uC600\uC2B5\uB2C8\uB2E4|\uC774\uC5C8\uC2B5\uB2C8\uB2E4)",
        re.IGNORECASE,
    ),
)
_ORG_PATTERNS = (
    re.compile(
        r"(?:\uC8FC\uAD00\uAE30\uAD00|\uC218\uD589\uAE30\uAD00|\uCC38\uC5EC\uAE30\uAD00|\uC18C\uC18D\uAE30\uAD00|\uC18C\uC18D)\s*"
        r"(?:\uC740|\uB294|[:=])?\s*([^\n\.,;]{2,60}?)(?=\s*(?:\uC785\uB2C8\uB2E4|\uC785\uB2C8\uAE4C|[\.,;\n]|$))",
        re.IGNORECASE,
    ),
)
_COUNT_PATTERNS = (
    re.compile(r"(?:\uCD1D|\uBAA8\uB450)\s*(\d+)\s*(?:\uAC74|\uAC1C|\uBA85|\uC885)", re.IGNORECASE),
    re.compile(r"(?:\uAC74\uC218|\uAC1C\uC218|\uC778\uC6D0)\s*(?:\uC740|\uB294|[:=])?\s*(\d+)", re.IGNORECASE),
    re.compile(r"(?<!\d)(\d+)\s*(?:\uAC74|\uAC1C|\uBA85|\uC885)\s*(?:\uC785\uB2C8\uB2E4|\uC785\uB2C8\uAE4C|(?:\uC774|\uAC00)\s*\uC788\uC2B5\uB2C8\uB2E4)?", re.IGNORECASE),
)
_BUDGET_PATTERNS = (
    re.compile(r"(?:\uC5F0\uAD6C\uBE44|\uC608\uC0B0|budget)\s*(?:\uC740|\uB294|[:=])?\s*([0-9][0-9,\s]*(?:\uC6D0)?)", re.IGNORECASE),
)
_PERIOD_PATTERNS = (
    re.compile(
        r"(?:\uC5F0\uAD6C\uAE30\uAC04|\uAE30\uAC04|period)\s*(?:\uC740|\uB294|[:=])?\s*"
        r"((?:\d{4}[-./]\d{1,2}[-./]\d{1,2})\s*(?:~|[-\u2013]|to|\uBD80\uD130)\s*(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}))",
        re.IGNORECASE,
    ),
)
_CLAIM_VALUE_SUFFIXES = (
    "\uB85C \uD655\uC778\uB429\uB2C8\uB2E4",
    "\uB85C \uBCF4\uC785\uB2C8\uB2E4",
    "\uB85C \uD310\uB2E8\uB429\uB2C8\uB2E4",
    "\uC785\uB2C8\uB2E4\uB9CC",
    "\uC785\uB2C8\uB2E4",
    "\uC785\uB2C8\uAE4C",
    "\uC774\uC5C8\uC2B5\uB2C8\uB2E4",
    "\uC600\uC2B5\uB2C8\uB2E4",
)


class UnsupportedClaim(BaseModel):
    kind: ClaimKind
    value: str
    reason: UnsupportedReason


class AnswerEvidenceSnapshot(BaseModel):
    available: bool = False
    snapshot_source: Literal["canonical_evidence", "none"] = "none"
    supported_project_ids: list[str] = Field(default_factory=list)
    supported_project_nos: list[str] = Field(default_factory=list)
    supported_perf_ids: list[str] = Field(default_factory=list)
    supported_years: list[str] = Field(default_factory=list)
    supported_org_names: list[str] = Field(default_factory=list)
    supported_budgets: list[str] = Field(default_factory=list)
    supported_periods: list[str] = Field(default_factory=list)
    supported_counts: list[int] = Field(default_factory=list)


class AnswerGroundednessVerdict(BaseModel):
    status: GroundednessStatus
    reason_codes: list[str] = Field(default_factory=list)
    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)
    insufficient_axes: list[str] = Field(default_factory=list)
    checked_claims: int = 0
    snapshot_source: str = "none"
    claims: dict[str, list[str]] = Field(default_factory=dict)


def _normalize_token(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _normalize_numeric_text(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalize_period_text(value: Any) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _append_unique_text(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in values:
        values.append(text)


def _dedupe_text(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _clean_claim_value(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip().strip("'\"")
    text = text.strip(" .,:;")
    for suffix in _CLAIM_VALUE_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip(" .,:;")
            break
    return text.strip(" .,:;")


def _extract_claim_values(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            cleaned = _clean_claim_value(match.group(1))
            if cleaned:
                values.append(cleaned)
    return _dedupe_text(values)


def build_groundedness_snapshot_from_canonical_evidence(
    *,
    canonical_evidence: list[dict[str, Any]],
    visible_count: Any = None,
) -> AnswerEvidenceSnapshot:
    project_ids: list[str] = []
    project_nos: list[str] = []
    perf_ids: list[str] = []
    years: list[str] = []
    org_names: list[str] = []
    budgets: list[str] = []
    periods: list[str] = []

    for item in canonical_evidence or []:
        if not isinstance(item, dict):
            continue
        ids = item.get("ids") or {}
        facts = item.get("facts") or {}
        roles = item.get("roles") or {}
        stable_base = item.get("stable_base") or {}
        _append_unique_text(project_ids, ids.get("pjt_id"))
        _append_unique_text(project_nos, ids.get("pjt_no"))
        for key in ("rst_id", "doi", "issn"):
            _append_unique_text(perf_ids, ids.get(key))
        for year_source in (facts.get("year"), stable_base.get("year"), item.get("year")):
            year = str(year_source or "").strip()
            if not year:
                continue
            normalized_year = year[:4]
            _append_unique_text(years, normalized_year)
        for role_key in ("lead_org_name", "participant_org_name", "people_affiliation_org_name"):
            for value in roles.get(role_key) or []:
                _append_unique_text(org_names, value)
        for value in (
            stable_base.get("lead_org_name"),
            item.get("lead_org_name"),
            item.get("lead_org"),
        ):
            _append_unique_text(org_names, value)
        for value in (facts.get("budget"), stable_base.get("budget"), item.get("budget")):
            _append_unique_text(budgets, value)
        for value in (facts.get("period"), stable_base.get("period"), item.get("period")):
            _append_unique_text(periods, value)

    supported_counts: list[int] = []
    try:
        normalized_visible_count = int(visible_count) if visible_count is not None else None
    except Exception:
        normalized_visible_count = None
    if normalized_visible_count is not None and normalized_visible_count >= 0:
        supported_counts.append(normalized_visible_count)
    elif canonical_evidence:
        supported_counts.append(len([item for item in canonical_evidence if isinstance(item, dict)]))

    return AnswerEvidenceSnapshot(
        available=bool(project_ids or project_nos or perf_ids or years or org_names or budgets or periods or supported_counts),
        snapshot_source="canonical_evidence" if canonical_evidence else "none",
        supported_project_ids=project_ids,
        supported_project_nos=project_nos,
        supported_perf_ids=perf_ids,
        supported_years=years,
        supported_org_names=org_names,
        supported_budgets=budgets,
        supported_periods=periods,
        supported_counts=supported_counts,
    )


def evaluate_answer_groundedness(
    *,
    answer_text: str,
    answer_kind: str,
    evidence_snapshot: AnswerEvidenceSnapshot | dict[str, Any] | None,
    groundedness_policy: GroundednessPolicyName | str | None = None,
    output_type: str | None = None,
) -> AnswerGroundednessVerdict:
    answer = (answer_text or "").strip()
    snapshot = (
        evidence_snapshot
        if isinstance(evidence_snapshot, AnswerEvidenceSnapshot)
        else AnswerEvidenceSnapshot.model_validate(evidence_snapshot or {})
    )
    if not answer:
        return AnswerGroundednessVerdict(status="empty_answer", snapshot_source=snapshot.snapshot_source)
    if answer_kind in BYPASS_ANSWER_KINDS:
        return AnswerGroundednessVerdict(status="skipped_bypass_kind", snapshot_source=snapshot.snapshot_source)

    policy_name = str(groundedness_policy or "").strip().lower()
    output_type_name = str(output_type or "").strip().lower()
    detail_structured_only = policy_name == "detail_structured" or output_type_name == "detail"

    claims: dict[str, list[str]] = {
        "project_id": _extract_claim_values(answer, _PROJECT_ID_PATTERNS),
        "project_no": _extract_claim_values(answer, _PROJECT_NO_PATTERNS),
        "perf_id": _extract_claim_values(answer, _PERF_ID_PATTERNS),
        "year": _extract_claim_values(answer, _YEAR_PATTERNS),
        "org_name": _extract_claim_values(answer, _ORG_PATTERNS),
        "budget": _extract_claim_values(answer, _BUDGET_PATTERNS),
        "period": _extract_claim_values(answer, _PERIOD_PATTERNS),
        "count": [] if detail_structured_only else _extract_claim_values(answer, _COUNT_PATTERNS),
    }
    active_claim_keys: tuple[ClaimKind, ...] = (
        ("project_id", "project_no", "budget")
        if detail_structured_only
        else ("project_id", "project_no", "perf_id", "year", "org_name", "budget", "period", "count")
    )
    checked_claims = sum(len(claims.get(key) or []) for key in active_claim_keys)
    if checked_claims == 0:
        return AnswerGroundednessVerdict(
            status="no_structured_claims",
            checked_claims=0,
            snapshot_source=snapshot.snapshot_source,
            claims=claims,
        )
    if not snapshot.available:
        return AnswerGroundednessVerdict(
            status="insufficient_snapshot",
            reason_codes=["insufficient_snapshot"],
            insufficient_axes=list(active_claim_keys),
            checked_claims=checked_claims,
            snapshot_source=snapshot.snapshot_source,
            claims=claims,
        )

    unsupported_claims: list[UnsupportedClaim] = []
    insufficient_axes: list[str] = []

    def compare_claims(
        *,
        claim_key: ClaimKind,
        supported_values: list[str],
        reason_code: UnsupportedReason,
        normalizer: Any = _normalize_token,
    ) -> None:
        claim_values = claims.get(claim_key) or []
        if not claim_values:
            return
        normalized_supported = {normalizer(value) for value in supported_values if normalizer(value)}
        if not normalized_supported:
            insufficient_axes.append(claim_key)
            return
        for claim_value in claim_values:
            normalized_claim = normalizer(claim_value)
            if normalized_claim and normalized_claim not in normalized_supported:
                unsupported_claims.append(UnsupportedClaim(kind=claim_key, value=claim_value, reason=reason_code))

    compare_claims(claim_key="project_id", supported_values=list(snapshot.supported_project_ids), reason_code="unsupported_project_id")
    compare_claims(claim_key="project_no", supported_values=list(snapshot.supported_project_nos), reason_code="unsupported_project_no")
    if not detail_structured_only:
        compare_claims(claim_key="perf_id", supported_values=list(snapshot.supported_perf_ids), reason_code="unsupported_perf_id")
        compare_claims(claim_key="year", supported_values=list(snapshot.supported_years), reason_code="unsupported_year")
        compare_claims(claim_key="org_name", supported_values=list(snapshot.supported_org_names), reason_code="unsupported_org_name")
    compare_claims(
        claim_key="budget",
        supported_values=list(snapshot.supported_budgets),
        reason_code="unsupported_budget",
        normalizer=_normalize_numeric_text,
    )
    if not detail_structured_only:
        compare_claims(
            claim_key="period",
            supported_values=list(snapshot.supported_periods),
            reason_code="unsupported_period",
            normalizer=_normalize_period_text,
        )

    count_claims = claims.get("count") or []
    supported_counts = {int(value) for value in snapshot.supported_counts}
    if count_claims:
        if not supported_counts:
            insufficient_axes.append("count")
        else:
            for claim_value in count_claims:
                try:
                    normalized_claim_value = int(claim_value)
                except Exception:
                    continue
                if normalized_claim_value not in supported_counts:
                    unsupported_claims.append(UnsupportedClaim(kind="count", value=str(normalized_claim_value), reason="unsupported_count"))

    reason_codes = _dedupe_text([claim.reason for claim in unsupported_claims])
    if unsupported_claims:
        status: GroundednessStatus = "unsupported"
    elif insufficient_axes:
        status = "insufficient_snapshot"
        reason_codes = ["insufficient_snapshot"]
    else:
        status = "supported"

    return AnswerGroundednessVerdict(
        status=status,
        reason_codes=reason_codes,
        unsupported_claims=unsupported_claims,
        insufficient_axes=_dedupe_text(insufficient_axes),
        checked_claims=checked_claims,
        snapshot_source=snapshot.snapshot_source,
        claims=claims,
    )
