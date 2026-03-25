from __future__ import annotations

from typing import Any

from apps.core.canonical_evidence import build_canonical_evidence, build_canonical_evidence_bundle


def _rehydrate_participant_members(roles: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild participant members while preserving affiliation-org semantics."""
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


def render_canonical_evidence_text(
    canonical_evidence: list[dict[str, Any]],
    render_profile: dict[str, Any],
    *,
    max_chars: int = 0,
) -> str:
    """canonical_evidence와 render_profile을 사람이 읽는 context text로 렌더링한다.

    여기서는 ids, facts, roles를 구조적으로 풀어 쓰되 raw retrieval metadata 전체를 노출하지 않는다.
    """
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
                "prtcp_org": [{"org_nm": value} for value in (roles.get("participant_org_name") or []) if str(value).strip()],
                "prtcp_mp": _rehydrate_participant_members(roles),
            }
        )
    return prev_context
