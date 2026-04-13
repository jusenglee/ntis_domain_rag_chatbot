from __future__ import annotations

import re
from typing import Any, Dict, Optional

from apps.conversation.entity_reference import ClarificationRequest, ResolvedEntityRef

_ORDINAL_WORD_TO_INDEX = {
    "\uccab": 0,
    "\uccab\ubc88\uc9f8": 0,
    "\uccab \ubc88\uc9f8": 0,
    "\uccab\uc9f8": 0,
    "\ub450": 1,
    "\ub450\ubc88\uc9f8": 1,
    "\ub450 \ubc88\uc9f8": 1,
    "\ub458\uc9f8": 1,
    "\uc138": 2,
    "\uc138\ubc88\uc9f8": 2,
    "\uc138 \ubc88\uc9f8": 2,
    "\uc14b\uc9f8": 2,
}
_EXPLICIT_ORDINAL_PATTERNS = (
    re.compile("(?:제\\s*)?(\\d{1,3})\\s*번째"),
    re.compile("(?:제\\s*)?(\\d{1,3})\\s*번"),
    re.compile("(첫번째|첫\\s*번째|첫째)"),
    re.compile("(두번째|두\\s*번째|둘째)"),
    re.compile("(세번째|세\\s*번째|셋째)"),
)
_SOURCE_REFERENCE_PATTERNS = (
    re.compile("(?:출처|source)\\s*(\\d{1,3})"),
    re.compile("(?:출처|source)\\s*(첫번째|첫\\s*번째|첫째|첫|두번째|두\\s*번째|둘째|두|세번째|세\\s*번째|셋째|세)"),
    re.compile("참고\\s*(?:문헌|자료)\\s*(\\d{1,3})"),
    re.compile("참고\\s*(?:문헌|자료)\\s*(첫번째|첫\\s*번째|첫째|첫|두번째|두\\s*번째|둘째|두|세번째|세\\s*번째|셋째|세)"),
)
_LAST_ITEM_RE = re.compile("(?:맨\\s*마지막|마지막)")
_DEICTIC_PATTERNS = {
    "project": (
        re.compile("그\\s*과제"),
        re.compile("이\\s*과제"),
        re.compile("방금\\s*과제"),
    ),
    "perf": (
        re.compile("그\\s*(?:성과|논문|특허|보고서|기술)"),
        re.compile("이\\s*(?:성과|논문|특허|보고서|기술)"),
    ),
    "people": (
        re.compile("그\\s*(?:연구자|연구원|사람)"),
        re.compile("이\\s*(?:연구자|연구원|사람)"),
    ),
    "org": (
        re.compile("그\\s*(?:기관|회사|조직)"),
        re.compile("이\\s*(?:기관|회사|조직)"),
    ),
}
_SEED_KEYS = ("pjt_id", "pjt_no", "rst_id", "person_no", "org_id", "org_code", "biz_no", "doi", "issn")


def _decode_token(token: str) -> str:
    text = str(token or "")
    return text.encode("utf-8").decode("unicode_escape") if "\\u" in text else text


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def is_ordinal_reference_token(value: Any) -> bool:
    text = _normalize_text(value)
    if not text:
        return False
    if _LAST_ITEM_RE.search(text):
        return True
    for pattern in _SOURCE_REFERENCE_PATTERNS:
        if pattern.search(text):
            return True
    for patterns in _DEICTIC_PATTERNS.values():
        for pattern in patterns:
            if pattern.search(text):
                return True
    for pattern in _EXPLICIT_ORDINAL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def strip_ordinal_reference_terms(values: Any) -> tuple[list[str], list[str]]:
    if values is None:
        return [], []
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set)):
        values = [values]

    kept: list[str] = []
    stripped: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        if is_ordinal_reference_token(text):
            stripped.append(text)
        else:
            kept.append(text)
    return kept, stripped


def _infer_context_kind(item: Dict[str, Any], default_context_kind: str) -> str:
    ids = item.get("ids") or item
    if _normalize_text(ids.get("person_no")) or _normalize_text(ids.get("hm_id")):
        return "people"
    if _normalize_text(ids.get("org_id")) or _normalize_text(ids.get("org_code")) or _normalize_text(ids.get("biz_no")):
        return "org"
    if _normalize_text(ids.get("rst_id")) or _normalize_text(ids.get("doi")) or _normalize_text(ids.get("issn")):
        return "perf"
    if _normalize_text(ids.get("pjt_id")) or _normalize_text(ids.get("pjt_no")):
        return "project"
    source_type = _normalize_text(item.get("source_type") or item.get("doc_type") or item.get("context_kind")).lower()
    if source_type in {"people", "researcher"}:
        return "people"
    if source_type in {"org", "organization"}:
        return "org"
    if "perf" in source_type or source_type in {"paper", "patent", "report", "software"}:
        return "perf"
    return default_context_kind


def _build_seed_map(item: Dict[str, Any]) -> dict[str, list[str]]:
    for key in _SEED_KEYS:
        value = _normalize_text(item.get(key))
        if value:
            return {key: [value]}
    return {}


def _compact_candidate(item: Dict[str, Any]) -> Dict[str, Any]:
    compact = {"index": item.get("index"), "entity_kind": item.get("context_kind"), "title": item.get("title"), "doc_id": item.get("doc_id")}
    for key in _SEED_KEYS:
        if item.get(key):
            compact[key] = item.get(key)
    return {key: value for key, value in compact.items() if value is not None and value != ""}


def build_reference_items(*, canonical_evidence: list[dict[str, Any]], prev_context: list[dict[str, Any]], default_context_kind: str = "project") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if canonical_evidence:
        for index, item in enumerate(canonical_evidence, start=1):
            if not isinstance(item, dict):
                continue
            ids = item.get("ids") or {}
            facts = item.get("facts") or {}
            roles = item.get("roles") or {}
            participant_orgs = list(roles.get("participant_org_name") or [])
            participants = list(roles.get("participant_researcher_name") or [])
            items.append({
                "index": index,
                "doc_id": _normalize_text(ids.get("doc_id")) or None,
                "pjt_id": _normalize_text(ids.get("pjt_id")) or None,
                "pjt_no": _normalize_text(ids.get("pjt_no")) or None,
                "rst_id": _normalize_text(ids.get("rst_id")) or None,
                "person_no": _normalize_text(ids.get("person_no")) or None,
                "org_id": _normalize_text(ids.get("org_id")) or None,
                "org_code": _normalize_text(ids.get("org_code")) or None,
                "biz_no": _normalize_text(ids.get("biz_no")) or None,
                "doi": _normalize_text(ids.get("doi")) or None,
                "issn": _normalize_text(ids.get("issn")) or None,
                "title": _normalize_text(facts.get("title") or item.get("identity")) or None,
                "lead_org": _normalize_text((roles.get("lead_org_name") or [None])[0]) or None,
                "participant_org": participant_orgs[0] if participant_orgs else None,
                "participant_name": participants[0] if participants else None,
                "context_kind": _infer_context_kind(item, default_context_kind),
                "source": "canonical_evidence",
            })
        return items

    for index, item in enumerate(prev_context or [], start=1):
        if not isinstance(item, dict):
            continue
        first_org = None
        if isinstance(item.get("prtcp_org"), list) and item.get("prtcp_org"):
            first_org = _normalize_text((item.get("prtcp_org") or [{}])[0].get("org_nm")) or None
        first_person = None
        if isinstance(item.get("prtcp_mp"), list) and item.get("prtcp_mp"):
            first_person = _normalize_text((item.get("prtcp_mp") or [{}])[0].get("hm_nm")) or None
        items.append({
            "index": index,
            "doc_id": _normalize_text(item.get("doc_id") or item.get("id")) or None,
            "pjt_id": _normalize_text(item.get("pjt_id")) or None,
            "pjt_no": _normalize_text(item.get("pjt_no")) or None,
            "rst_id": _normalize_text(item.get("rst_id")) or None,
            "person_no": _normalize_text(item.get("person_no") or item.get("hm_id")) or None,
            "org_id": _normalize_text(item.get("org_id")) or None,
            "org_code": _normalize_text(item.get("org_code") or item.get("org_cd")) or None,
            "biz_no": _normalize_text(item.get("biz_no") or item.get("org_no")) or None,
            "doi": _normalize_text(item.get("doi")) or None,
            "issn": _normalize_text(item.get("issn")) or None,
            "title": _normalize_text(item.get("title_text") or item.get("title")) or None,
            "lead_org": _normalize_text(item.get("org_nm")) or None,
            "participant_org": first_org,
            "participant_name": first_person,
            "context_kind": _infer_context_kind(item, default_context_kind),
            "source": "prev_context",
        })
    return items


def _parse_explicit_ordinal(question: str) -> Optional[dict[str, Any]]:
    text = _normalize_text(question)
    if not text:
        return None
    last_match = _LAST_ITEM_RE.search(text)
    if last_match:
        return {"kind": "last", "index": -1, "token": last_match.group(0)}
    for pattern in _EXPLICIT_ORDINAL_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        token = _normalize_text(match.group(0))
        raw = _normalize_text(match.group(1))
        if raw.isdigit():
            ordinal = int(raw)
            if ordinal <= 0:
                return None
            return {"kind": "index", "index": ordinal - 1, "token": token}
        normalized_raw = raw.replace(" ", "")
        for ordinal_token, mapped in _ORDINAL_WORD_TO_INDEX.items():
            if _decode_token(ordinal_token).replace(" ", "") == normalized_raw:
                return {"kind": "index", "index": mapped, "token": token}
    return None


def _parse_source_reference(question: str) -> Optional[dict[str, Any]]:
    text = _normalize_text(question)
    if not text:
        return None
    for pattern in _SOURCE_REFERENCE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        token = _normalize_text(match.group(0))
        raw = _normalize_text(match.group(1))
        if raw.isdigit():
            ordinal = int(raw)
            if ordinal <= 0:
                return None
            return {"kind": "source_reference", "index": ordinal - 1, "token": token}
        normalized_raw = raw.replace(" ", "")
        for ordinal_token, mapped in _ORDINAL_WORD_TO_INDEX.items():
            if _decode_token(ordinal_token).replace(" ", "") == normalized_raw:
                return {"kind": "source_reference", "index": mapped, "token": token}
    return None


def _parse_deictic_followup(question: str, *, default_context_kind: str) -> Optional[dict[str, Any]]:
    text = _normalize_text(question)
    if not text:
        return None
    kinds = [str(default_context_kind or "project").strip().lower(), "project", "perf", "people", "org"]
    seen: set[str] = set()
    for kind in kinds:
        if kind in seen:
            continue
        seen.add(kind)
        for pattern in _DEICTIC_PATTERNS.get(kind, ()):
            match = pattern.search(text)
            if match:
                return {"kind": "deictic", "index": None, "token": _normalize_text(match.group(0)), "context_kind": kind}
    return None




def _select_deictic_candidate_items(items: list[dict[str, Any]], *, context_kind: Optional[str]) -> list[dict[str, Any]]:
    typed_items = [item for item in items if not context_kind or item.get("context_kind") == context_kind]
    if context_kind != "org" or typed_items:
        return typed_items
    project_org_carriers = [
        item
        for item in items
        if item.get("context_kind") == "project" and (item.get("lead_org") or item.get("participant_org"))
    ]
    return project_org_carriers

def resolve_reference_context_followup(*, question: str, canonical_evidence: list[dict[str, Any]], prev_context: list[dict[str, Any]], default_context_kind: str = "project") -> dict[str, Any]:
    parsed = _parse_source_reference(question)
    if parsed is None:
        parsed = _parse_explicit_ordinal(question)
    if parsed is None:
        parsed = _parse_deictic_followup(question, default_context_kind=default_context_kind)
    items = build_reference_items(canonical_evidence=canonical_evidence, prev_context=prev_context, default_context_kind=default_context_kind)
    candidate_items = [_compact_candidate(item) for item in items[:5]]
    if parsed is None:
        return {"followup_resolution_status": "none", "explicit_ordinal": False, "explicit_followup": False, "requested_token": None, "requested_index": None, "available_count": len(items), "selected_prev_item": None, "seed_map": {}, "seed_source": None, "followup_reference_kind": None, "candidate_items": []}
    if not items:
        return {"followup_resolution_status": "missing_context", "explicit_ordinal": parsed["kind"] != "deictic", "explicit_followup": True, "requested_token": parsed["token"], "requested_index": parsed.get("index"), "available_count": 0, "selected_prev_item": None, "seed_map": {}, "seed_source": None, "followup_reference_kind": parsed["kind"], "candidate_items": []}
    if parsed["kind"] == "deictic":
        context_kind = parsed.get("context_kind")
        typed_items = _select_deictic_candidate_items(items, context_kind=context_kind)
        if len(typed_items) != 1:
            return {"followup_resolution_status": "unresolved", "explicit_ordinal": False, "explicit_followup": True, "requested_token": parsed["token"], "requested_index": None, "available_count": len(typed_items) if context_kind else len(items), "selected_prev_item": None, "seed_map": {}, "seed_source": None, "followup_reference_kind": parsed["kind"], "candidate_items": [_compact_candidate(item) for item in (typed_items or items)[:5]]}
        selected_item = dict(typed_items[0])
        index = int(selected_item.get("index") or 1) - 1
    else:
        index = len(items) - 1 if parsed["kind"] == "last" else int(parsed["index"])
        if index < 0 or index >= len(items):
            return {"followup_resolution_status": "out_of_range", "explicit_ordinal": True, "explicit_followup": True, "requested_token": parsed["token"], "requested_index": index, "available_count": len(items), "selected_prev_item": None, "seed_map": {}, "seed_source": None, "followup_reference_kind": parsed["kind"], "candidate_items": candidate_items}
        selected_item = dict(items[index])
    seed_map = _build_seed_map(selected_item)
    status = "resolved" if seed_map else "unresolved"
    seed_source = None
    if status == "resolved":
        if parsed["kind"] == "deictic":
            seed_source = "reference_context_deictic"
        elif parsed["kind"] == "source_reference":
            seed_source = "reference_context_source_reference"
        else:
            seed_source = "reference_context_ordinal"
    return {"followup_resolution_status": status, "explicit_ordinal": parsed["kind"] != "deictic", "explicit_followup": True, "requested_token": parsed["token"], "requested_index": index, "available_count": len(items), "selected_prev_item": selected_item if seed_map else None, "seed_map": seed_map, "seed_source": seed_source, "followup_reference_kind": parsed["kind"], "candidate_items": candidate_items if status != "resolved" else []}


def build_followup_clarification_message(strategy_meta: Dict[str, Any]) -> Optional[str]:
    status = _normalize_text((strategy_meta or {}).get("followup_resolution_status")).lower()
    if not status or status in {"none", "resolved"}:
        return None
    if status == "clarification_required":
        payload = dict((strategy_meta or {}).get("clarification_payload") or {})
        message = _normalize_text(payload.get("message") or (strategy_meta or {}).get("clarification_message"))
        if message:
            return message
        reason = _normalize_text(payload.get("reason") or (strategy_meta or {}).get("clarification_reason")).lower()
        if reason == "child_entity_ambiguity":
            return "현재 상세 안에서 어떤 대상을 뜻하는지 다시 지정해 주세요."
        if reason == "child_entity_missing_id":
            return "현재 상세 안의 대상은 보이지만 정확한 식별자를 확인할 수 없습니다. 다른 기준으로 다시 지정해 주세요."
        if reason == "refinement_target_missing":
            return "무엇을 기준으로 좁힐지 먼저 목록이나 상세 대상을 정해 주세요."
        if reason == "reference_missing_context":
            return "이전 결과 목록이나 상세 맥락이 없어 무엇을 가리키는지 판단하기 어렵습니다. 먼저 목록을 확인해 주세요."
        return "무엇을 가리키는지 다시 확인해 주세요."
    reference_kind = _normalize_text((strategy_meta or {}).get("followup_reference_kind")).lower()
    available_count = int((strategy_meta or {}).get("available_count") or 0)
    if reference_kind == "source_reference":
        if status == "missing_context":
            return "\uc774\uc804 \ucd9c\ucc98 \ubaa9\ub85d\uc774 \uc5c6\uc5b4 \uba87 \ubc88\uc9f8 \ucd9c\ucc98\uc778\uc9c0 \ud310\ub2e8\ud558\uae30 \uc5b4\ub835\uc2b5\ub2c8\ub2e4. \uba3c\uc800 \ubaa9\ub85d\uc744 \ud655\uc778\ud55c \ub4a4 \ub2e4\uc2dc \uc9c8\ubb38\ud574 \uc8fc\uc138\uc694."
        if status == "out_of_range":
            return f"\uc774\uc804 \ucd9c\ucc98 \ubaa9\ub85d\uc5d0\ub294 {available_count}\uac1c\ub9cc \uc788\uc2b5\ub2c8\ub2e4. \uba87 \ubc88\uc9f8 \ucd9c\ucc98\ub97c \ub9d0\uc500\ud558\uc2dc\ub294\uc9c0 \ub2e4\uc2dc \uc54c\ub824\uc8fc\uc138\uc694."
        if status == "unresolved":
            return "\uc774\uc804 \ubaa9\ub85d\uc5d0\uc11c \uc5b4\ub290 \ucd9c\ucc98\ub97c \ub9d0\uc500\ud558\uc2dc\ub294\uc9c0 \ud655\uc778\ud574 \uc8fc\uc138\uc694."
    selected_prev_item = (strategy_meta or {}).get("selected_prev_item") or {}
    context_kind = _normalize_text(selected_prev_item.get("context_kind") or (strategy_meta or {}).get("selected_prev_context_kind") or "project").lower()
    subject_map = {"project": "과제", "perf": "성과", "people": "연구자", "org": "소속기관"}
    subject = _decode_token(subject_map.get(context_kind, "항목"))
    available_count = int((strategy_meta or {}).get("available_count") or 0)
    if status == "missing_context":
        return f"이전 목록이 없어 몇 번째 {subject}인지 판단하기 어렵습니다. 먼저 목록을 확인한 뒤 다시 질문해 주세요."
    if status == "out_of_range":
        return f"이전 목록에는 {available_count}개만 있습니다. 몇 번째 {subject}를 말씀하시는지 다시 알려주세요."
    if status == "unresolved":
        return f"이전 목록에서 어느 {subject}를 말씀하시는지 확인해 주세요."
    return None


def build_followup_clarification_payload(strategy_meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    status = _normalize_text((strategy_meta or {}).get("followup_resolution_status")).lower()
    if not status or status in {"none", "resolved"}:
        return None
    if status == "clarification_required":
        payload = dict((strategy_meta or {}).get("clarification_payload") or {})
        return {
            "clarification_type": payload.get("clarification_type") or (strategy_meta or {}).get("clarification_type") or "followup_reference",
            "status": status,
            "requested_token": (strategy_meta or {}).get("requested_token"),
            "available_count": int((strategy_meta or {}).get("available_count") or 0),
            "selection_hint": payload.get("reason") or (strategy_meta or {}).get("clarification_reason"),
            "candidates": [item for item in (payload.get("candidates") or (strategy_meta or {}).get("candidate_items") or []) if isinstance(item, dict)],
            "resume_token": {
                **dict(payload.get("resume_token") or {}),
                "followup_reference_kind": (strategy_meta or {}).get("followup_reference_kind"),
                "requested_token": (strategy_meta or {}).get("requested_token"),
                "display_view_id": (strategy_meta or {}).get("display_view_id"),
            },
        }
    reference_kind = _normalize_text((strategy_meta or {}).get("followup_reference_kind")).lower()
    candidates = [item for item in ((strategy_meta or {}).get("candidate_items") or []) if isinstance(item, dict)]
    if not candidates and status != "missing_context":
        return None
    return {
        "clarification_type": "followup_reference",
        "status": status,
        "requested_token": (strategy_meta or {}).get("requested_token"),
        "available_count": int((strategy_meta or {}).get("available_count") or 0),
        "selection_hint": "source_reference_or_entity_reference" if reference_kind == "source_reference" else "ordinal_or_entity_reference",
        "candidates": candidates,
        "resume_token": {
            "followup_reference_kind": (strategy_meta or {}).get("followup_reference_kind"),
            "requested_token": (strategy_meta or {}).get("requested_token"),
            "display_view_id": (strategy_meta or {}).get("display_view_id"),
        },
    }


def should_short_circuit_followup_clarification(strategy_meta: Dict[str, Any]) -> bool:
    status = _normalize_text((strategy_meta or {}).get("followup_resolution_status")).lower()
    explicit_followup = bool((strategy_meta or {}).get("explicit_followup"))
    return explicit_followup and status in {"missing_context", "out_of_range", "unresolved", "clarification_required"}


def resolve_entity_ref_from_strategy_meta(strategy_meta: Dict[str, Any]) -> ResolvedEntityRef | ClarificationRequest | None:
    status = _normalize_text((strategy_meta or {}).get("followup_resolution_status")).lower()
    if status in {"missing_context", "out_of_range", "unresolved", "clarification_required"}:
        message = build_followup_clarification_message(strategy_meta)
        payload = build_followup_clarification_payload(strategy_meta) or {}
        return ClarificationRequest(
            clarification_type=str(payload.get("clarification_type") or "followup_reference"),
            message=str(message or ""),
            candidates=list(payload.get("candidates") or []),
            resume_token=dict(payload.get("resume_token") or {}),
        )
    if status != "resolved":
        return None
    seed_map = dict((strategy_meta or {}).get("seed_map") or {})
    if not seed_map:
        return None
    selected_prev_item = dict((strategy_meta or {}).get("selected_prev_item") or {})
    focus_entity = dict((strategy_meta or {}).get("focus_entity") or {})
    entity_kind = _normalize_text(
        selected_prev_item.get("context_kind")
        or focus_entity.get("kind")
        or (strategy_meta or {}).get("selected_prev_context_kind")
        or "project"
    ).lower()
    source = _normalize_text((strategy_meta or {}).get("seed_source") or "display_snapshot").lower()
    allowed_sources = {
        "display_snapshot",
        "detail_lookup",
        "detail_participant_match",
        "detail_org_match",
        "detail_perf_match",
        "reference_context_ordinal",
        "reference_context_source_reference",
        "reference_context_deictic",
        "explicit_id",
    }
    resolved_source = source if source in allowed_sources else "display_snapshot"
    return ResolvedEntityRef(
        entity_kind=entity_kind if entity_kind in {"project", "perf", "people", "org"} else "project",
        seed_map={str(key): [str(v).strip() for v in (values or []) if str(v).strip()] for key, values in seed_map.items()},
        source=resolved_source,  # type: ignore[arg-type]
        reference_kind=_normalize_text((strategy_meta or {}).get("followup_reference_kind")).lower() or None,
        display_view_id=_normalize_text((strategy_meta or {}).get("display_view_id")),
        display_rank=((selected_prev_item.get("index") or focus_entity.get("display_rank")) if isinstance(selected_prev_item, dict) else None),
        anchor_fields=selected_prev_item or focus_entity,
    )
