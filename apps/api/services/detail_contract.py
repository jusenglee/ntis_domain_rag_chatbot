from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

from apps.api.services.view_state import DetailCoverage, FocusEntity

CORE_FIELDS = ("title", "pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn", "year", "lead_org", "participant_org", "researchers")
RICH_FIELDS = ("summary", "goal", "period", "budget", "outputs", "perf_type", "affiliation")
FIELD_ALIASES = {
    "researchers": ["researcher", "researchers", "\uc5f0\uad6c\uc790", "\uc5f0\uad6c\uc6d0"],
    "lead_org": ["lead_org", "performing_org", "\uc8fc\uad00\uae30\uad00", "\uc218\ud589\uae30\uad00"],
    "participant_org": ["participant_org", "\ucc38\uc5ec\uae30\uad00"],
    "year": ["year", "\uc5f0\ub3c4"],
    "summary": ["summary", "\uc694\uc57d", "\uac1c\uc694"],
    "goal": ["goal", "\ubaa9\ud45c"],
    "period": ["period", "\uae30\uac04"],
    "budget": ["budget", "\uc608\uc0b0"],
    "outputs": ["outputs", "\uc131\uacfc"],
    "perf_type": ["perf_type", "paper", "patent", "report", "\uc131\uacfc\uc720\ud615"],
    "doi": ["doi"],
    "issn": ["issn"],
    "affiliation": ["affiliation", "\uc18c\uc18d", "\uc18c\uc18d\uae30\uad00"],
}
REFUSAL_PATTERNS = (
    "\ud655\uc778\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4",
    "\uc81c\uacf5\ub41c \uc790\ub8cc\uc5d0\uc11c \ud655\uc778\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4",
    "\uc548\ub0b4\uac00 \uc5b4\ub835\uc2b5\ub2c8\ub2e4",
    "\ud3ec\ud568\ub418\uc5b4 \uc788\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4",
)
_ENTITY_LABELS = {
    "project": "\uacfc\uc81c",
    "perf": "\uc131\uacfc",
    "people": "\uc5f0\uad6c\uc790",
    "org": "\uae30\uad00",
}
_FIELD_LABELS = {
    "pjt_id": "\uacfc\uc81c ID",
    "pjt_no": "\uacfc\uc81c \ubc88\ud638",
    "rst_id": "\uc131\uacfc ID",
    "person_no": "\uc5f0\uad6c\uc790 \ubc88\ud638",
    "org_id": "\uae30\uad00 ID",
    "org_code": "\uae30\uad00 \ucf54\ub4dc",
    "biz_no": "\uc0ac\uc5c5\uc790\ub4f1\ub85d\ubc88\ud638",
    "doi": "DOI",
    "issn": "ISSN",
    "year": "\uc5f0\ub3c4",
    "lead_org": "\uc218\ud589\uae30\uad00",
    "participant_org": "\ucc38\uc5ec\uae30\uad00",
    "researchers": "\uc5f0\uad6c\uc790",
    "summary": "\uc694\uc57d",
    "goal": "\ubaa9\ud45c",
    "period": "\uc5f0\uad6c\uae30\uac04",
    "budget": "\uc5f0\uad6c\ube44",
    "perf_type": "\uc131\uacfc \uc720\ud615",
    "affiliation": "\uc18c\uc18d\uae30\uad00",
    "outputs": "\uc131\uacfc\ubb3c",
}


def _decode_text(value: str) -> str:
    text = str(value or "")
    return text.encode("utf-8").decode("unicode_escape") if "\\u" in text else text


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


def _pick_nested(items: Any, key: str) -> list[str]:
    out: list[str] = []
    if isinstance(items, list):
        for item in items:
            text = _text(item.get(key)) if isinstance(item, dict) else _text(item)
            if text and text not in out:
                out.append(text)
    return out


def _infer_entity_kind(anchor: Optional[FocusEntity], doc: Dict[str, Any], ids: Dict[str, Any]) -> str:
    if anchor is not None and getattr(anchor, "kind", None):
        return str(anchor.kind).strip().lower() or "project"
    if _text(ids.get("person_no")) or _text(doc.get("person_no")) or _text(doc.get("hm_id")):
        return "people"
    if _text(ids.get("org_id")) or _text(ids.get("org_code")) or _text(ids.get("biz_no")) or _text(doc.get("org_id")):
        return "org"
    if _text(ids.get("rst_id")) or _text(ids.get("doi")) or _text(ids.get("issn")) or _text(doc.get("rst_id")):
        return "perf"
    return "project"


def make_entity_cache_key(anchor: FocusEntity) -> str:
    kind = str(getattr(anchor, "kind", "") or "").strip().lower() or "project"
    for kind, key, value in (
        (kind, "pjt_id", getattr(anchor, "pjt_id", None)),
        (kind, "pjt_no", getattr(anchor, "pjt_no", None)),
        (kind, "rst_id", getattr(anchor, "rst_id", None)),
        (kind, "person_no", getattr(anchor, "person_no", None)),
        (kind, "org_id", getattr(anchor, "org_id", None)),
        (kind, "org_code", getattr(anchor, "org_code", None)),
        (kind, "biz_no", getattr(anchor, "biz_no", None)),
        (kind, "doi", getattr(anchor, "doi", None)),
        (kind, "issn", getattr(anchor, "issn", None)),
        (kind, "doc_id", getattr(anchor, "doc_id", None)),
    ):
        if value:
            return f"{kind}:{key}:{value}"
    return f"{kind}:title:{getattr(anchor, 'title_text', None) or 'unknown'}"


def extract_requested_fields(question: str) -> Set[str]:
    text = _text(question).lower()
    found: Set[str] = set()
    for field, aliases in FIELD_ALIASES.items():
        decoded_aliases = [_decode_text(alias).lower() for alias in aliases]
        if any(alias in text for alias in decoded_aliases):
            found.add(field)
    if not found:
        if _decode_text('\uc0c1\uc138') in question:
            found.update({"summary", "goal", "period", "budget", "outputs"})
        else:
            found.add("title")
    return found


def compute_detail_coverage(document: Dict[str, Any], *, anchor: Optional[FocusEntity] = None) -> DetailCoverage:
    doc = document if isinstance(document, dict) else {}
    ids = doc.get("ids") or {}
    facts = doc.get("facts") or {}
    roles = doc.get("roles") or {}
    meta_detail = doc.get("meta_detail") or {}
    meta_basic = doc.get("meta_basic") or {}
    entity_kind = _infer_entity_kind(anchor, doc, ids)
    core_profile = {
        "entity_kind": entity_kind,
        "title": _text(getattr(anchor, "title_text", None) or doc.get("title") or doc.get("title_text") or facts.get("title") or meta_basic.get("kor_pjt_nm") or meta_basic.get("title")),
        "pjt_id": _text(getattr(anchor, "pjt_id", None) or doc.get("pjt_id") or ids.get("pjt_id") or meta_detail.get("pjt_id") or meta_basic.get("pjt_id")),
        "pjt_no": _text(getattr(anchor, "pjt_no", None) or doc.get("pjt_no") or ids.get("pjt_no") or meta_detail.get("pjt_no") or meta_basic.get("pjt_no")),
        "rst_id": _text(getattr(anchor, "rst_id", None) or doc.get("rst_id") or ids.get("rst_id") or meta_detail.get("rst_id") or meta_basic.get("rst_id")),
        "person_no": _text(getattr(anchor, "person_no", None) or doc.get("person_no") or doc.get("hm_id") or ids.get("person_no")),
        "org_id": _text(getattr(anchor, "org_id", None) or doc.get("org_id") or meta_detail.get("org_id") or meta_basic.get("org_id")),
        "org_code": _text(getattr(anchor, "org_code", None) or doc.get("org_code") or doc.get("org_cd") or meta_detail.get("org_code") or meta_basic.get("org_code")),
        "biz_no": _text(getattr(anchor, "biz_no", None) or doc.get("biz_no") or doc.get("org_no") or meta_detail.get("biz_no") or meta_basic.get("biz_no")),
        "doi": _text(getattr(anchor, "doi", None) or doc.get("doi") or ids.get("doi") or meta_detail.get("doi") or meta_basic.get("doi")),
        "issn": _text(getattr(anchor, "issn", None) or doc.get("issn") or ids.get("issn") or meta_detail.get("issn") or meta_basic.get("issn")),
        "year": _text(facts.get("year") or doc.get("stan_yr") or meta_basic.get("stan_yr") or meta_detail.get("stan_yr") or getattr(anchor, "year", None)),
        "lead_org": _text(doc.get("org_nm") or meta_detail.get("org_nm") or ((roles.get("lead_org_name") or [None])[0]) or getattr(anchor, "lead_org", None)),
        "participant_org": _pick_nested(doc.get("prtcp_org"), "org_nm") or [str(v).strip() for v in (roles.get("participant_org_name") or []) if str(v).strip()] or [str(v).strip() for v in (getattr(anchor, "participant_org", None) or []) if str(v).strip()],
        "researchers": _pick_nested(doc.get("prtcp_mp"), "hm_nm") or [str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip()] or [str(v).strip() for v in (getattr(anchor, "researchers", None) or []) if str(v).strip()],
    }
    rich_detail = {
        "summary": _text(doc.get("summary") or facts.get("summary") or meta_detail.get("smry") or meta_basic.get("summary")),
        "goal": _text(meta_detail.get("goal") or meta_detail.get("obj") or meta_detail.get("research_goal")),
        "period": _text(meta_detail.get("period") or meta_detail.get("research_period") or meta_detail.get("date_range")),
        "budget": _text(meta_detail.get("budget") or meta_detail.get("research_expense") or meta_detail.get("total_budget")),
        "outputs": _pick_nested(meta_detail.get("outputs"), "name") or _pick_nested(doc.get("outputs"), "name") or ([_text(meta_detail.get("outputs"))] if _text(meta_detail.get("outputs")) else []),
        "perf_type": _text(meta_detail.get("perf_type") or facts.get("tag") or doc.get("tag")),
        "affiliation": _text(meta_detail.get("affiliation") or meta_detail.get("blng_org_nm") or meta_basic.get("affiliation") or ((roles.get("people_affiliation_org_name") or [None])[0]) or getattr(anchor, "lead_org", None)),
    }
    available = [key for key, value in {**core_profile, **rich_detail}.items() if _present(value)]
    missing = [key for key in list(CORE_FIELDS) + list(RICH_FIELDS) if key not in available]
    if entity_kind == "perf":
        entity_found = any(_present(core_profile.get(key)) for key in ("title", "rst_id", "doi", "issn"))
    elif entity_kind == "people":
        entity_found = any(_present(core_profile.get(key)) for key in ("title", "person_no", "researchers"))
    elif entity_kind == "org":
        entity_found = any(_present(core_profile.get(key)) for key in ("title", "org_id", "org_code", "biz_no", "lead_org"))
    else:
        entity_found = any(_present(core_profile.get(key)) for key in ("title", "pjt_id", "pjt_no"))
    detail_level = "not_found" if not entity_found else ("rich_detail" if any(_present(rich_detail.get(key)) for key in RICH_FIELDS) else "profile_only")
    return DetailCoverage(entity_found=entity_found, detail_level=detail_level, available_fields=available, missing_fields=missing, core_profile=core_profile, rich_detail=rich_detail)


def coverage_satisfies_fields(coverage: DetailCoverage, requested_fields: Iterable[str]) -> bool:
    requested = set(requested_fields or [])
    if not coverage.entity_found:
        return False
    if not requested:
        return True
    if requested <= set(coverage.available_fields):
        return True
    if requested <= {"researchers", "lead_org", "participant_org", "year", "title", "person_no", "org_id", "rst_id", "doi", "issn"} and coverage.detail_level in {"profile_only", "rich_detail"}:
        return True
    return False


def render_detail_answer(coverage: DetailCoverage, *, requested_fields: Optional[Iterable[str]] = None) -> str:
    if not coverage.entity_found:
        return _decode_text('\uc694\uccad\ud558\uc2e0 \ub300\uc0c1\uc744 \ud604\uc7ac \uc790\ub8cc\uc5d0\uc11c \ud655\uc778\ud558\uc9c0 \ubabb\ud588\uc2b5\ub2c8\ub2e4.')
    core = coverage.core_profile
    entity_kind = str(core.get("entity_kind") or "project").strip().lower() or "project"
    label = _decode_text(_ENTITY_LABELS.get(entity_kind, "\ud56d\ubaa9"))
    lines = []
    if _present(core.get("title")):
        lines.append(f"{label}: {core.get('title')}")
    for field in ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn"):
        if _present(core.get(field)):
            lines.append(f"{_decode_text(_FIELD_LABELS[field])}: {core.get(field)}")
    for field in ("year", "lead_org"):
        if _present(core.get(field)):
            lines.append(f"{_decode_text(_FIELD_LABELS[field])}: {core.get(field)}")
    for field in ("participant_org", "researchers"):
        if _present(core.get(field)):
            values = core.get(field) or []
            rendered = ", ".join(str(v) for v in values if str(v).strip()) if isinstance(values, list) else str(values)
            if rendered:
                lines.append(f"{_decode_text(_FIELD_LABELS[field])}: {rendered}")
    if coverage.detail_level == "profile_only":
        lines.append(_decode_text('\ud604\uc7ac \uc790\ub8cc\ub85c\ub294 \uc694\uccad\ud558\uc2e0 \uc0c1\uc138 \ud56d\ubaa9\uc744 \ucda9\ubd84\ud788 \ud655\uc778\ud558\uae30 \uc5b4\ub835\uc2b5\ub2c8\ub2e4.'))
        return "\n".join(lines)
    rich = coverage.rich_detail
    for field in ("summary", "goal", "period", "budget", "perf_type", "affiliation"):
        if _present(rich.get(field)):
            lines.append(f"{_decode_text(_FIELD_LABELS[field])}: {rich.get(field)}")
    if _present(rich.get("outputs")):
        outputs = rich.get("outputs") or []
        rendered_outputs = ", ".join(str(v) for v in outputs if str(v).strip()) if isinstance(outputs, list) else str(outputs)
        if rendered_outputs:
            lines.append(f"{_decode_text(_FIELD_LABELS['outputs'])}: {rendered_outputs}")
    return "\n".join(lines)


def is_false_refusal(answer_text: str, coverage: DetailCoverage) -> bool:
    if not coverage.entity_found or not coverage.available_fields:
        return False
    text = _text(answer_text)
    return any(_decode_text(pattern) in text for pattern in REFUSAL_PATTERNS)
