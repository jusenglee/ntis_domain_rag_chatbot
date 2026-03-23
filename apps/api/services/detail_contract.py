from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

from apps.api.services.view_state import DetailCoverage, FocusEntity

CORE_FIELDS = ("title", "pjt_id", "pjt_no", "year", "lead_org", "participant_org", "researchers")
RICH_FIELDS = ("summary", "goal", "period", "budget", "outputs")
FIELD_ALIASES = {
    "researchers": ["연구자", "참여연구원", "참여자"],
    "lead_org": ["주관기관", "주관", "수행기관"],
    "participant_org": ["참여기관", "공동기관"],
    "year": ["연도", "년도"],
    "summary": ["요약", "개요", "설명"],
    "goal": ["목표", "연구목표"],
    "period": ["기간", "수행기간"],
    "budget": ["예산", "연구비"],
    "outputs": ["성과", "결과물"],
}
REFUSAL_PATTERNS = (
    "확인할 수 없습니다",
    "제공된 자료에서 확인되지 않습니다",
    "안내가 어렵습니다",
    "포함되어 있지 않습니다",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def make_entity_cache_key(anchor: FocusEntity) -> str:
    if anchor.pjt_id:
        return f"project:pjt_id:{anchor.pjt_id}"
    if anchor.pjt_no:
        return f"project:pjt_no:{anchor.pjt_no}"
    if anchor.doc_id:
        return f"project:doc_id:{anchor.doc_id}"
    return f"project:title:{anchor.title_text or 'unknown'}"


def extract_requested_fields(question: str) -> Set[str]:
    text = _text(question)
    found: Set[str] = set()
    for field, aliases in FIELD_ALIASES.items():
        if any(alias in text for alias in aliases):
            found.add(field)
    if not found:
        found.update({"summary", "goal", "period", "budget", "outputs"} if "상세" in text else {"title"})
    return found


def _pick_nested(items: Any, key: str) -> list[str]:
    out: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                text = _text(item.get(key))
            else:
                text = _text(item)
            if text and text not in out:
                out.append(text)
    return out


def compute_detail_coverage(document: Dict[str, Any]) -> DetailCoverage:
    doc = document if isinstance(document, dict) else {}
    ids = doc.get("ids") or {}
    facts = doc.get("facts") or {}
    roles = doc.get("roles") or {}
    meta_detail = doc.get("meta_detail") or {}
    meta_basic = doc.get("meta_basic") or {}
    core_profile = {
        "title": _text(doc.get("title") or facts.get("title") or meta_basic.get("kor_pjt_nm")),
        "pjt_id": _text(doc.get("pjt_id") or ids.get("pjt_id") or meta_detail.get("pjt_id") or meta_basic.get("pjt_id")),
        "pjt_no": _text(doc.get("pjt_no") or ids.get("pjt_no") or meta_detail.get("pjt_no") or meta_basic.get("pjt_no")),
        "year": _text(facts.get("year") or doc.get("stan_yr") or meta_basic.get("stan_yr")),
        "lead_org": _text(doc.get("org_nm") or meta_detail.get("org_nm") or ((roles.get("lead_org_name") or [None])[0])),
        "participant_org": _pick_nested(doc.get("prtcp_org"), "org_nm") or [str(v).strip() for v in (roles.get("participant_org_name") or []) if str(v).strip()],
        "researchers": _pick_nested(doc.get("prtcp_mp"), "hm_nm") or [str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()],
    }
    rich_detail = {
        "summary": _text(doc.get("summary") or facts.get("summary") or meta_detail.get("smry")),
        "goal": _text(meta_detail.get("goal") or meta_detail.get("obj") or meta_detail.get("research_goal")),
        "period": _text(meta_detail.get("period") or meta_detail.get("research_period") or meta_detail.get("date_range")),
        "budget": _text(meta_detail.get("budget") or meta_detail.get("research_expense") or meta_detail.get("total_budget")),
        "outputs": _pick_nested(meta_detail.get("outputs"), "name")
        or _pick_nested(doc.get("outputs"), "name")
        or ([str(meta_detail.get("outputs")).strip()] if _text(meta_detail.get("outputs")) else []),
    }
    available = [key for key, value in {**core_profile, **rich_detail}.items() if _present(value)]
    missing = [key for key in list(CORE_FIELDS) + list(RICH_FIELDS) if key not in available]
    entity_found = any(_present(core_profile.get(key)) for key in ("title", "pjt_id", "pjt_no"))
    if not entity_found:
        detail_level = "not_found"
    elif any(_present(rich_detail.get(key)) for key in RICH_FIELDS):
        detail_level = "rich_detail"
    else:
        detail_level = "profile_only"
    return DetailCoverage(
        entity_found=entity_found,
        detail_level=detail_level,
        available_fields=available,
        missing_fields=missing,
        core_profile=core_profile,
        rich_detail=rich_detail,
    )


def coverage_satisfies_fields(coverage: DetailCoverage, requested_fields: Iterable[str]) -> bool:
    requested = set(requested_fields or [])
    if not coverage.entity_found:
        return False
    if not requested:
        return True
    if requested <= set(coverage.available_fields):
        return True
    if requested <= {"researchers", "lead_org", "participant_org", "year", "title"} and coverage.detail_level in {"profile_only", "rich_detail"}:
        return True
    return False


def render_detail_answer(coverage: DetailCoverage, *, requested_fields: Optional[Iterable[str]] = None) -> str:
    if not coverage.entity_found:
        return "요청하신 대상을 현재 자료에서 확인하지 못했습니다."
    core = coverage.core_profile
    lines = []
    if _present(core.get("title")):
        lines.append(f"과제명: {core.get('title')}")
    if _present(core.get("pjt_id")):
        lines.append(f"PJT_ID: {core.get('pjt_id')}")
    if _present(core.get("pjt_no")):
        lines.append(f"PJT_NO: {core.get('pjt_no')}")
    if _present(core.get("year")):
        lines.append(f"연도: {core.get('year')}")
    if _present(core.get("lead_org")):
        lines.append(f"주관기관: {core.get('lead_org')}")
    if _present(core.get("participant_org")):
        lines.append(f"참여기관: {', '.join(core.get('participant_org') or [])}")
    if _present(core.get("researchers")):
        lines.append(f"연구자: {', '.join(core.get('researchers') or [])}")
    if coverage.detail_level == "profile_only":
        lines.append("현재 자료에는 연구목표, 예산, 수행기간, 세부성과 같은 추가 상세 항목이 충분하지 않습니다.")
        return "\n".join(lines)
    rich = coverage.rich_detail
    if _present(rich.get("summary")):
        lines.append(f"요약: {rich.get('summary')}")
    if _present(rich.get("goal")):
        lines.append(f"목표: {rich.get('goal')}")
    if _present(rich.get("period")):
        lines.append(f"기간: {rich.get('period')}")
    if _present(rich.get("budget")):
        lines.append(f"예산: {rich.get('budget')}")
    if _present(rich.get("outputs")):
        outputs = rich.get("outputs") or []
        if isinstance(outputs, list):
            lines.append(f"성과: {', '.join(str(v) for v in outputs if str(v).strip())}")
        else:
            lines.append(f"성과: {outputs}")
    return "\n".join(lines)


def is_false_refusal(answer_text: str, coverage: DetailCoverage) -> bool:
    if not coverage.entity_found or not coverage.available_fields:
        return False
    text = _text(answer_text)
    return any(pattern in text for pattern in REFUSAL_PATTERNS)
