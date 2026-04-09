from __future__ import annotations

from typing import Any

from apps.evidence.canonical_evidence import build_canonical_evidence, build_canonical_evidence_bundle


_PROMPT_FIELD_LABELS = {
    "pjt_id": "과제 ID",
    "pjt_no": "과제 번호",
    "rst_id": "성과 ID",
    "person_no": "연구자 번호",
    "org_id": "기관 ID",
    "org_code": "기관 코드",
    "biz_no": "사업자등록번호",
    "doi": "DOI",
    "issn": "ISSN",
    "year": "연도",
    "lead_org": "수행기관",
    "participant_org": "참여기관",
    "affiliation_org": "소속기관",
    "researchers": "연구자",
    "summary": "요약",
    "goal": "목표",
    "period": "연구기간",
    "budget": "연구비",
    "perf_type": "성과 유형",
    "outputs": "성과물",
}


def _clean_prompt_value(value: Any) -> str:
    text = str(value or "")
    text = text.replace("_x000D_\n", "\n").replace("_x000D_", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(part for part in text.split() if part).strip()


def _join_unique(values: list[Any]) -> str:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_prompt_value(value)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return ", ".join(items)


def _append_prompt_field(lines: list[str], *, key: str, value: Any) -> None:
    rendered = _clean_prompt_value(value)
    if not rendered:
        return
    lines.append(f"- {_PROMPT_FIELD_LABELS[key]}: {rendered}")


def _append_prompt_list_field(lines: list[str], *, key: str, values: list[Any]) -> None:
    rendered = _join_unique(list(values or []))
    if not rendered:
        return
    lines.append(f"- {_PROMPT_FIELD_LABELS[key]}: {rendered}")


def _first_child_id(entity: dict[str, Any], key: str) -> str:
    ids_map = entity.get("ids_map") or {}
    values = ids_map.get(key) or []
    if isinstance(values, str):
        return values.strip()
    if isinstance(values, list):
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
    return ""


def _rehydrate_participant_members(roles: dict[str, Any], child_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild participant members while preserving affiliation-org semantics."""
    child_members: list[dict[str, Any]] = []
    for entity in child_entities or []:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("kind") or "").strip().lower() != "people":
            continue
        member_name = str(entity.get("display_name") or "").strip()
        if not member_name:
            continue
        member: dict[str, Any] = {"hm_nm": member_name}
        person_no = _first_child_id(entity, "person_no")
        if person_no:
            member["hm_id"] = person_no
            member["person_no"] = person_no
        affiliation = str(entity.get("affiliation") or "").strip()
        if affiliation:
            member["blng_org_nm"] = affiliation
        role = str(entity.get("role") or "").strip()
        if role:
            member["role_slct_nm"] = role
        child_members.append(member)
    if child_members:
        return child_members

    researcher_names = [
        str(value).strip()
        for value in (roles.get("participant_researcher_name") or [])
        if str(value).strip()
    ]
    affiliation_orgs = [
        str(value).strip()
        for value in (roles.get("people_affiliation_org_name") or [])
        if str(value).strip()
    ]

    members: list[dict[str, Any]] = []
    for index, name in enumerate(researcher_names):
        member: dict[str, Any] = {"hm_nm": name}
        if index < len(affiliation_orgs):
            member["blng_org_nm"] = affiliation_orgs[index]
        members.append(member)
    return members


def _rehydrate_participant_orgs(roles: dict[str, Any], child_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    child_orgs: list[dict[str, Any]] = []
    for entity in child_entities or []:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("kind") or "").strip().lower() != "org":
            continue
        if str(entity.get("parent_relation") or "").strip().lower() != "participant_org":
            continue
        org_name = str(entity.get("display_name") or "").strip()
        if not org_name:
            continue
        org_payload: dict[str, Any] = {"org_nm": org_name}
        org_id = _first_child_id(entity, "org_id")
        org_code = _first_child_id(entity, "org_code")
        biz_no = _first_child_id(entity, "biz_no")
        if org_id:
            org_payload["org_id"] = org_id
        if org_code:
            org_payload["org_code"] = org_code
        if biz_no:
            org_payload["biz_no"] = biz_no
        role = str(entity.get("role") or "").strip()
        if role:
            org_payload["role"] = role
        child_orgs.append(org_payload)
    if child_orgs:
        return child_orgs
    return [{"org_nm": value} for value in (roles.get("participant_org_name") or []) if str(value).strip()]


def _rehydrate_related_perf(child_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    related: list[dict[str, Any]] = []
    for entity in child_entities or []:
        if not isinstance(entity, dict):
            continue
        if str(entity.get("kind") or "").strip().lower() != "perf":
            continue
        title = str(entity.get("display_name") or "").strip()
        if not title:
            continue
        item: dict[str, Any] = {"title": title, "title_text": title, "name": title}
        rst_id = _first_child_id(entity, "rst_id")
        doi = _first_child_id(entity, "doi")
        issn = _first_child_id(entity, "issn")
        if rst_id:
            item["rst_id"] = rst_id
        if doi:
            item["doi"] = doi
        if issn:
            item["issn"] = issn
        role = str(entity.get("role") or "").strip()
        if role:
            item["perf_type"] = role
        affiliation = str(entity.get("affiliation") or "").strip()
        if affiliation:
            item["org_nm"] = affiliation
        related.append(item)
    return related


def render_canonical_evidence_text(
    canonical_evidence: list[dict[str, Any]],
    render_profile: dict[str, Any],
    *,
    max_chars: int = 0,
) -> str:
    """canonical_evidence와 render_profile을 사람이 읽는 context text로 렌더링한다.

    모델이 그대로 따라 말할 수 있으므로 내부 schema 키 대신 `# 출처 N.`와 사용자 친화 라벨만 쓴다.
    """
    if not canonical_evidence:
        return "NONE"

    lines: list[str] = []
    for index, item in enumerate(canonical_evidence, start=1):
        ids = item.get("ids") or {}
        facts = item.get("facts") or {}
        roles = item.get("roles") or {}
        title = _clean_prompt_value(facts.get("title") or item.get("identity") or "항목")
        lines.append(f"# 출처 {index}. {title}")

        _append_prompt_field(lines, key="pjt_id", value=ids.get("pjt_id"))
        _append_prompt_field(lines, key="pjt_no", value=ids.get("pjt_no"))
        _append_prompt_field(lines, key="rst_id", value=ids.get("rst_id"))
        _append_prompt_field(lines, key="person_no", value=ids.get("person_no"))
        _append_prompt_field(lines, key="org_id", value=ids.get("org_id"))
        _append_prompt_field(lines, key="org_code", value=ids.get("org_code"))
        _append_prompt_field(lines, key="biz_no", value=ids.get("biz_no"))
        _append_prompt_field(lines, key="doi", value=ids.get("doi"))
        _append_prompt_field(lines, key="issn", value=ids.get("issn"))
        _append_prompt_field(lines, key="year", value=facts.get("year"))
        _append_prompt_field(lines, key="lead_org", value=((roles.get("lead_org_name") or [None])[0]))
        _append_prompt_list_field(lines, key="participant_org", values=list(roles.get("participant_org_name") or []))
        _append_prompt_list_field(lines, key="affiliation_org", values=list(roles.get("people_affiliation_org_name") or []))
        _append_prompt_list_field(lines, key="researchers", values=list(roles.get("participant_researcher_name") or []))
        _append_prompt_field(lines, key="summary", value=facts.get("summary"))
        _append_prompt_field(lines, key="goal", value=facts.get("goal"))
        _append_prompt_field(lines, key="period", value=facts.get("period"))
        _append_prompt_field(lines, key="budget", value=facts.get("budget"))
        _append_prompt_field(lines, key="perf_type", value=facts.get("perf_type"))
        outputs = facts.get("outputs") or []
        if isinstance(outputs, list):
            _append_prompt_list_field(lines, key="outputs", values=outputs)
        else:
            _append_prompt_field(lines, key="outputs", value=outputs)
        lines.append("")

        joined = "\n".join(lines)
        if max_chars > 0 and len(joined) >= max_chars:
            return joined[:max_chars].rstrip()

    return "\n".join(lines).strip()


def render_canonical_evidence_debug_text(
    canonical_evidence: list[dict[str, Any]],
    render_profile: dict[str, Any],
    *,
    max_chars: int = 0,
) -> str:
    """운영 로그용 canonical context를 구조적으로 렌더링한다."""
    if not canonical_evidence:
        return "NONE"

    profile_name = str((render_profile or {}).get("name") or "summary").strip() or "summary"
    context_kind = str((render_profile or {}).get("context_kind") or "project").strip() or "project"
    lines = [f"[RenderProfile] name={profile_name} kind={context_kind}"]

    for index, item in enumerate(canonical_evidence, start=1):
        ids = item.get("ids") or {}
        facts = item.get("facts") or {}
        roles = item.get("roles") or {}
        title = str(facts.get("title") or item.get("identity") or "item").strip()
        summary = str(facts.get("summary") or "").strip()
        year = str(facts.get("year") or "").strip()
        pjt_id = str(ids.get("pjt_id") or "").strip()
        pjt_no = str(ids.get("pjt_no") or "").strip()
        rst_id = str(ids.get("rst_id") or "").strip()
        lead_org = ", ".join(str(v).strip() for v in (roles.get("lead_org_name") or []) if str(v).strip())
        participant_org = ", ".join(str(v).strip() for v in (roles.get("participant_org_name") or []) if str(v).strip())
        affiliation_org = ", ".join(str(v).strip() for v in (roles.get("people_affiliation_org_name") or []) if str(v).strip())
        participant_name = ", ".join(str(v).strip() for v in (roles.get("participant_researcher_name") or []) if str(v).strip())

        line_parts = [f"{index}. {title}"]
        if pjt_id:
            line_parts.append(f"PJT_ID={pjt_id}")
        if pjt_no:
            line_parts.append(f"PJT_NO={pjt_no}")
        if rst_id:
            line_parts.append(f"RST_ID={rst_id}")
        if year:
            line_parts.append(f"YEAR={year}")
        if lead_org:
            line_parts.append(f"LEAD_ORG={lead_org}")
        if participant_org:
            line_parts.append(f"PARTICIPANT_ORG={participant_org}")
        if affiliation_org:
            line_parts.append(f"AFFILIATION_ORG={affiliation_org}")
        if participant_name:
            line_parts.append(f"RESEARCHERS={participant_name}")
        lines.append(" | ".join(line_parts))
        if summary:
            lines.append(f"SUMMARY: {summary}")

        joined = "\n".join(lines)
        if max_chars > 0 and len(joined) >= max_chars:
            return joined[:max_chars]

    return "\n".join(lines)


def build_prev_context_canonical_text(
    prev_context: list[dict[str, Any]],
    *,
    base_route: str = "project",
    output_type: str = "summary",
    render_profile_name: str = "summary",
    render_profile_kind: str = "project",
    max_chars: int = 0,
) -> str:
    """이전 prev_context 문서를 canonical_evidence로 재구성한 뒤 같은 renderer로 텍스트를 만든다."""
    canonical_evidence: list[dict[str, Any]] = []
    for index, item in enumerate(prev_context or [], start=1):
        if not isinstance(item, dict):
            continue
        canonical_evidence.append(
            build_canonical_evidence(
                item,
                rank=index,
                base_route=base_route,
                output_type=output_type,
            ).to_dict()
        )
    return render_canonical_evidence_text(
        canonical_evidence,
        {"name": render_profile_name, "context_kind": render_profile_kind},
        max_chars=max_chars,
    )


def rehydrate_prev_context_from_canonical_evidence(
    canonical_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """canonical_evidence를 이전 prev_context와 비슷한 얕은 dict 목록으로 복원한다.

    필요한 title, 요약, 역할 정보만 되살려 다음 요청이 메모리 snapshot을 이용해 문맥을 이어가게 한다.
    """
    prev_context: list[dict[str, Any]] = []
    for item in canonical_evidence or []:
        if not isinstance(item, dict):
            continue
        ids = item.get("ids") or {}
        facts = item.get("facts") or {}
        roles = item.get("roles") or {}
        child_entities = [entity for entity in (item.get("child_entities") or []) if isinstance(entity, dict)]
        prev_context.append(
            {
                "pjt_id": ids.get("pjt_id"),
                "pjt_no": ids.get("pjt_no"),
                "rst_id": ids.get("rst_id"),
                "person_no": ids.get("person_no"),
                "org_id": ids.get("org_id"),
                "org_code": ids.get("org_code"),
                "biz_no": ids.get("biz_no"),
                "doi": ids.get("doi"),
                "issn": ids.get("issn"),
                "title_text": facts.get("title"),
                "summary": facts.get("summary"),
                "stan_yr": facts.get("year"),
                "tag": facts.get("tag"),
                "org_nm": ((roles.get("lead_org_name") or [None])[0]),
                "prtcp_org": _rehydrate_participant_orgs(roles, child_entities),
                "prtcp_mp": _rehydrate_participant_members(roles, child_entities),
                "related_perf": _rehydrate_related_perf(child_entities),
            }
        )
    return prev_context
