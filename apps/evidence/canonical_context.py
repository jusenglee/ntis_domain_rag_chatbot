from __future__ import annotations

"""
정규화된 문맥(Canonical Context) 관리 모듈입니다.
이 모듈은 RAG 시스템에서 LLM에게 전달할 지식 데이터를 사람이 읽기 좋은 형태(자연어)로 변환하거나,
이전 대화의 문맥을 다시 복원하는 역할을 수행합니다.

주요 역할:
1. 텍스트 렌더링: 구조화된 증거(Evidence) 데이터를 '# 출처 N. 제목' 형태의 깔끔한 텍스트로 바꿉니다.
2. 필드 라벨링: 데이터베이스의 키 값(pjt_id, rst_id 등)을 '과제 ID', '성과 ID'와 같은 사용자 친화적인 한글 이름으로 매핑합니다.
3. 문맥 복원(Rehydration): 이전 턴에서 사용했던 정규화된 증거를 다시 딕셔너리 형태로 되돌려 대화의 연속성을 유지합니다.
"""

from typing import Any

from apps.evidence.canonical_evidence import build_canonical_evidence, build_canonical_evidence_bundle

# 프롬프트에 표시될 필드들의 한글 라벨 설정입니다.
# 사용자가 답변의 근거를 쉽게 이해할 수 있도록 친숙한 용어를 사용합니다.
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
    """텍스트 내의 불필요한 공백이나 특수 줄바꿈 문자를 제거하여 깔끔하게 정리합니다.
    데이터 전처리 과정에서 발생할 수 있는 노이즈를 제거하여 LLM이 더 잘 이해하도록 돕습니다.
    """
    text = str(value or "")
    text = text.replace("_x000D_\n", "\n").replace("_x000D_", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return " ".join(part for part in text.split() if part).strip()


def _join_unique(values: list[Any]) -> str:
    """리스트 형태의 값들을 중복 없이 하나로 합쳐진 문자열로 변환합니다.
    여러 필드에 흩어져 있는 중복된 정보를 하나로 묶어 프롬프트 길이를 줄입니다.
    """
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
    """단일 값을 가진 필드를 프롬프트 줄 리스트에 추가합니다.
    값이 비어 있는 경우에는 불필요한 줄을 생성하지 않습니다.
    """
    rendered = _clean_prompt_value(value)
    if not rendered:
        return
    lines.append(f"- {_PROMPT_FIELD_LABELS[key]}: {rendered}")


def _append_prompt_list_field(lines: list[str], *, key: str, values: list[Any]) -> None:
    """리스트 형태의 값을 가진 필드를 프롬프트 줄 리스트에 추가합니다.
    여러 개의 기관이나 연구자 이름을 쉼표로 구분하여 한 줄로 표시합니다.
    """
    rendered = _join_unique(list(values or []))
    if not rendered:
        return
    lines.append(f"- {_PROMPT_FIELD_LABELS[key]}: {rendered}")


def _first_child_id(entity: dict[str, Any], key: str) -> str:
    """엔티티의 ID 맵(ids_map)에서 첫 번째 유효한 ID 값을 추출합니다.
    복합적인 ID 구조에서 대표 값을 안전하게 가져오기 위해 사용합니다.
    """
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
    """참여 연구자 정보를 이전의 구조화된 데이터 형태로 복원합니다.
    대화 이력에 저장된 텍스트나 엔티티 정보를 바탕으로 원래의 연구자 정보를 재구성합니다.
    """
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

    # 엔티티 정보가 없는 경우, 역할 맵(roles)에 저장된 정보로 대체합니다.
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
    """참여 기관 정보를 이전의 구조화된 데이터 형태로 복원합니다.
    참여 기관명뿐만 아니라 기관 ID나 사업자 번호 등 메타데이터도 함께 복원합니다.
    """
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
    """관련 성과 정보를 이전의 구조화된 데이터 형태로 복원합니다.
    논문, 특허 등 과제와 연결된 성과물들의 상세 정보를 재구성합니다.
    """
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
    """
    정규화된 증거(Evidence) 데이터를 사람이 읽기 좋은 텍스트(Context)로 변환합니다.
    이 텍스트는 프롬프트에 직접 포함되어 LLM이 답변을 생성하는 핵심 근거가 됩니다.
    
    매개변수:
        canonical_evidence: 정규화된 증거 객체 리스트
        render_profile: 렌더링 방식(이름, 종류 등)을 정의한 프로필
        max_chars: 결과 텍스트의 최대 글자 수 (0이면 제한 없음)
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

        # 설정된 필드들을 하나씩 줄로 추가합니다.
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

        # 최대 글자 수를 초과하면 즉시 반환합니다.
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
    """운영 모니터링 및 디버깅을 위해 정규화된 증거의 상세 정보를 요약된 한 줄 텍스트로 만듭니다.
    주요 ID값과 참여자 정보를 한눈에 파악할 수 있는 형식을 제공합니다.
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
    """이전 대화에서 사용된 문맥(prev_context)을 다시 사람이 읽기 좋은 텍스트로 변환합니다.
    대화 흐름 유지를 위해 이전 턴의 정보를 프롬프트에 다시 포함시킬 때 사용합니다.
    """
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
    """정규화된 증거 목록을 다시 구조화된 딕셔너리 리스트로 복원(Rehydrate)합니다.
    이렇게 복원된 데이터는 대화 상태(State)의 일부로 저장되어 다음 턴에서 참조됩니다.
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
