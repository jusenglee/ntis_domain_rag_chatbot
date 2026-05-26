from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence


def _clean_text(value: Any) -> str:
    """임의의 값을 공백이 정리된 문자열로 만든다.
    canonical evidence는 빈 값을 넣지 않는다는 가정이 있어 모든 수집 헬퍼가 먼저 이 정규화를 거친다.
    """
    text = str(value or "")
    text = text.replace("_x000D_\n", "\n").replace("_x000D_", "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def _first_non_empty(*values: Any) -> str:
    """여러 후보 값 중 첫 번째 실제 텍스트를 골라낸다.
    payload·meta·fallback 순서로 우선순위를 두고 id/fact 필드를 안정적으로 선택한다.
    """
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _pick_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
    """payload 안의 `meta_basic`·`meta_detail`를 하나의 meta dict로 병합한다.
    source payload가 메타를 두 단으로 나누어 볼 수 있으므로, canonical evidence는 이 차이를 먼저 흡수한다.
    """
    merged: Dict[str, Any] = {}
    for key in ("meta_basic", "meta_detail"):
        value = payload.get(key)
        if isinstance(value, dict):
            merged.update(value)
    return merged


def _normalize_nested_list(items: Any) -> List[Dict[str, Any]]:
    """nested list 필드에서 dict 원소만 걸러낸다.
    participating org/member 구조가 항상 정상적이라고 가정하지 않고, 형식이 맞는 항목만 role 수집에 쓴다.
    """
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _pick_nested_value(items: Any, *keys: str) -> str:
    for item in _normalize_nested_list(items):
        for key in keys:
            value = _clean_text(item.get(key))
            if value:
                return value
    return ""


def _collect_nested_values(items: Any, *keys: str) -> List[str]:
    values: List[str] = []
    for item in _normalize_nested_list(items):
        for key in keys:
            value = _clean_text(item.get(key))
            if value and value not in values:
                values.append(value)
    return values


def _format_period(start: Any, end: Any) -> str:
    start_text = _clean_text(start)
    end_text = _clean_text(end)
    if start_text and end_text:
        return f"{start_text} ~ {end_text}"
    return start_text or end_text


def _collect_output_values(value: Any) -> List[str]:
    outputs: List[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                text = _first_non_empty(item.get("name"), item.get("title"), item.get("value"))
            else:
                text = _clean_text(item)
            if text and text not in outputs:
                outputs.append(text)
        return outputs
    text = _clean_text(value)
    return [text] if text else []


def _normalize_child_ids_map(raw_ids_map: Dict[str, Any]) -> Dict[str, List[str]]:
    normalized: Dict[str, List[str]] = {}
    for key, raw_values in (raw_ids_map or {}).items():
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        items: List[str] = []
        seen: set[str] = set()
        for value in values:
            text = _clean_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
        if items:
            normalized[str(key).strip()] = items
    return normalized


def _build_child_subject_id(
    *,
    kind: str,
    display_name: Optional[str],
    ids_map: Dict[str, List[str]],
    role: Optional[str],
    affiliation: Optional[str],
) -> str:
    if kind == "people" and ids_map.get("person_no"):
        return f"people:id:{ids_map['person_no'][0]}"
    if kind == "org":
        if ids_map.get("org_id"):
            return f"org:org_id:{ids_map['org_id'][0]}"
        if ids_map.get("org_code"):
            return f"org:org_code:{ids_map['org_code'][0]}"
        if ids_map.get("biz_no"):
            return f"org:biz_no:{ids_map['biz_no'][0]}"
    if kind == "perf":
        if ids_map.get("rst_id"):
            return f"perf:rst_id:{ids_map['rst_id'][0]}"
        if ids_map.get("doi"):
            return f"perf:doi:{ids_map['doi'][0]}"
        if ids_map.get("issn"):
            return f"perf:issn:{ids_map['issn'][0]}"
    return (
        f"{kind}:name:{_clean_text(display_name).lower()}"
        f"|aff:{_clean_text(affiliation).lower()}"
        f"|role:{_clean_text(role).lower()}"
    )


def _build_child_entity(
    *,
    kind: str,
    display_name: Optional[str],
    ids_map: Dict[str, List[str]],
    role: Optional[str],
    affiliation: Optional[str],
    parent_relation: str,
) -> Optional[Dict[str, Any]]:
    normalized_ids = _normalize_child_ids_map(ids_map)
    normalized_name = _clean_text(display_name)
    normalized_role = _clean_text(role)
    normalized_affiliation = _clean_text(affiliation)
    if not normalized_name and not normalized_ids:
        return None
    subject_id = _build_child_subject_id(
        kind=kind,
        display_name=normalized_name,
        ids_map=normalized_ids,
        role=normalized_role,
        affiliation=normalized_affiliation,
    )
    fallback_name = ""
    for values in normalized_ids.values():
        if values:
            fallback_name = values[0]
            break
    return {
        "kind": kind,
        "subject_id": subject_id,
        "display_name": normalized_name or fallback_name,
        "ids_map": normalized_ids,
        "role": normalized_role or None,
        "affiliation": normalized_affiliation or None,
        "parent_relation": _clean_text(parent_relation) or None,
        "resolution_state": "resolved" if normalized_ids else "provisional",
    }


def _iter_perf_child_sources(payload: Dict[str, Any]) -> List[tuple[dict[str, Any], str]]:
    candidates: List[tuple[dict[str, Any], str]] = []
    for key, relation in (
        ("outputs", "linked_output"),
        ("linked_outputs", "linked_output"),
        ("perf_items", "linked_output"),
        ("related_outputs", "linked_output"),
        ("related_perf", "related_perf"),
    ):
        items = payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                candidates.append((item, relation))
            else:
                text = _clean_text(item)
                if text:
                    candidates.append(({"name": text}, relation))
    for key, relation in (("origin_perf", "origin_perf"), ("perf", "linked_output")):
        item = payload.get(key)
        if isinstance(item, dict):
            candidates.append((item, relation))
    return candidates


def _collect_child_entities(payload: Dict[str, Any], meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    entities: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def _append(entity: Optional[Dict[str, Any]]) -> None:
        if not entity:
            return
        subject_id = str(entity.get("subject_id") or "").strip()
        if not subject_id or subject_id in seen:
            return
        seen.add(subject_id)
        entities.append(entity)

    _append(
        _build_child_entity(
            kind="people",
            display_name=_first_non_empty(payload.get("hm_nm"), payload.get("person_name"), payload.get("name")),
            ids_map={
                "person_no": _first_non_empty(
                    payload.get("person_no"),
                    payload.get("hm_id"),
                    meta.get("person_no"),
                    meta.get("hm_id"),
                )
            },
            role=_first_non_empty(payload.get("role_slct_nm"), payload.get("role_nm"), payload.get("role")),
            affiliation=_first_non_empty(payload.get("blng_org_nm"), payload.get("affiliation"), payload.get("org_nm")),
            parent_relation="top_level_researcher",
        )
    )
    for member in _normalize_nested_list(payload.get("prtcp_mp")):
        _append(
            _build_child_entity(
                kind="people",
                display_name=_first_non_empty(member.get("hm_nm"), member.get("person_name"), member.get("name")),
                ids_map={
                    "person_no": _first_non_empty(
                        member.get("person_no"),
                        member.get("hm_id"),
                        member.get("prtcp_mp_id"),
                        member.get("mp_id"),
                        member.get("id"),
                    )
                },
                role=_first_non_empty(member.get("role_slct_nm"), member.get("role_nm"), member.get("role")),
                affiliation=_first_non_empty(member.get("blng_org_nm"), member.get("affiliation"), member.get("org_nm")),
                parent_relation="participant_researcher",
            )
        )

    _append(
        _build_child_entity(
            kind="org",
            display_name=_first_non_empty(
                payload.get("org_nm"),
                payload.get("org_name"),
                payload.get("pjt_prfrm_org_nm"),
                meta.get("org_nm"),
                meta.get("pjt_prfrm_org_nm"),
            ),
            ids_map={
                "org_id": _first_non_empty(payload.get("org_id"), meta.get("org_id")),
                "org_code": _first_non_empty(payload.get("org_code"), payload.get("org_cd"), meta.get("org_code"), meta.get("org_cd")),
                "biz_no": _first_non_empty(payload.get("biz_no"), payload.get("org_no"), meta.get("biz_no"), meta.get("org_no")),
            },
            role="lead_org",
            affiliation=None,
            parent_relation="lead_org",
        )
    )
    for org in _normalize_nested_list(payload.get("prtcp_org")):
        _append(
            _build_child_entity(
                kind="org",
                display_name=_first_non_empty(org.get("org_nm"), org.get("org_name")),
                ids_map={
                    "org_id": _first_non_empty(org.get("org_id")),
                    "org_code": _first_non_empty(org.get("org_code"), org.get("org_cd")),
                    "biz_no": _first_non_empty(org.get("biz_no"), org.get("org_no")),
                },
                role=_first_non_empty(org.get("role"), org.get("role_nm"), org.get("role_slct_nm")),
                affiliation=None,
                parent_relation="participant_org",
            )
        )

    for item, parent_relation in _iter_perf_child_sources(payload):
        _append(
            _build_child_entity(
                kind="perf",
                display_name=_first_non_empty(item.get("title"), item.get("title_text"), item.get("name"), item.get("value")),
                ids_map={
                    "rst_id": _first_non_empty(item.get("rst_id")),
                    "doi": _first_non_empty(item.get("doi")),
                    "issn": _first_non_empty(item.get("issn")),
                },
                role=_first_non_empty(item.get("perf_type"), item.get("tag"), item.get("type")),
                affiliation=_first_non_empty(item.get("org_nm"), item.get("affiliation")),
                parent_relation=parent_relation,
            )
        )

    return entities


def _collect_ids(payload: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    """payload와 meta에서 의미 있는 식별자를 canonical id map으로 수집한다.
    `pjt_id`, `pjt_no`, `rst_id`, `doc_id`가 서로 다른 의미를 가진 채 보존되도록 필드를 섞지 않고 정리한다.
    """
    ids = {
        "pjt_id": _first_non_empty(payload.get("pjt_id"), meta.get("pjt_id"), meta.get("project_id")),
        "pjt_no": _first_non_empty(payload.get("pjt_no"), meta.get("pjt_no"), meta.get("project_no")),
        "rst_id": _first_non_empty(payload.get("rst_id"), meta.get("rst_id"), payload.get("perf_id")),
        "doc_id": _first_non_empty(payload.get("doc_id"), payload.get("id"), meta.get("doc_id")),
        "person_no": _first_non_empty(
            payload.get("person_no"),
            payload.get("hm_id"),
            meta.get("person_no"),
            meta.get("hm_id"),
            _pick_nested_value(payload.get("prtcp_mp"), "person_no", "hm_id"),
        ),
        "org_id": _first_non_empty(
            payload.get("org_id"),
            meta.get("org_id"),
            _pick_nested_value(payload.get("prtcp_org"), "org_id"),
        ),
        "org_code": _first_non_empty(
            payload.get("org_code"),
            payload.get("org_cd"),
            meta.get("org_code"),
            meta.get("org_cd"),
            _pick_nested_value(payload.get("prtcp_org"), "org_code", "org_cd"),
        ),
        "biz_no": _first_non_empty(
            payload.get("biz_no"),
            payload.get("org_no"),
            meta.get("biz_no"),
            meta.get("org_no"),
            _pick_nested_value(payload.get("prtcp_org"), "biz_no", "org_no"),
        ),
        "doi": _first_non_empty(payload.get("doi"), meta.get("doi")),
        "issn": _first_non_empty(payload.get("issn"), meta.get("issn")),
    }
    return {key: value for key, value in ids.items() if value}


def _collect_roles(payload: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, List[str]]:
    """payload 내 수행기관·참여기관·참여인력 소속을 role 별로 분리 수집한다.
    lead org, participant org, people affiliation을 같은 기관명 문자열로 통합하지 않고 역할 그대로 보존한다.
    """
    lead_org_name = _first_non_empty(
        payload.get("org_nm"),
        payload.get("pjt_prfrm_org_nm"),
        meta.get("org_nm"),
        meta.get("pjt_prfrm_org_nm"),
    )
    participant_orgs = [
        _clean_text(item.get("org_nm"))
        for item in _normalize_nested_list(payload.get("prtcp_org"))
        if _clean_text(item.get("org_nm"))
    ]
    people_affiliations = [
        _clean_text(item.get("blng_org_nm"))
        for item in _normalize_nested_list(payload.get("prtcp_mp"))
        if _clean_text(item.get("blng_org_nm"))
    ]
    people_names = [
        _clean_text(item.get("hm_nm"))
        for item in _normalize_nested_list(payload.get("prtcp_mp"))
        if _clean_text(item.get("hm_nm"))
    ]

    roles: Dict[str, List[str]] = {}
    if lead_org_name:
        roles["lead_org_name"] = [lead_org_name]
    if participant_orgs:
        roles["participant_org_name"] = participant_orgs
    if people_affiliations:
        roles["people_affiliation_org_name"] = people_affiliations
    if people_names:
        roles["participant_researcher_name"] = people_names
    return roles


def _collect_facts(payload: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """제목·요약·연도·태그·relation 같은 핵심 fact field를 수집한다.
    prompt view가 보게 될 정보와 lookup/filter 보조에 쓸 정보를 raw payload 키 의존 없이 읽을 수 있게 맞춘다.
    """
    title = _first_non_empty(
        payload.get("title1"),
        payload.get("title_text"),
        payload.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
        meta.get("title"),
    )
    summary = _first_non_empty(
        payload.get("summary"),
        meta.get("summary"),
        meta.get("rsch_abstract"),
        payload.get("content2"),
        payload.get("content"),
        payload.get("content_text"),
    )
    goal = _first_non_empty(
        meta.get("goal"),
        meta.get("obj"),
        meta.get("research_goal"),
        meta.get("rsch_goal_abstract"),
        payload.get("content1"),
    )
    period = _first_non_empty(
        meta.get("period"),
        meta.get("research_period"),
        meta.get("date_range"),
        _format_period(
            meta.get("tot_rsch_start_dt") or payload.get("start_dt") or payload.get("dt1"),
            meta.get("tot_rsch_end_dt") or payload.get("end_dt") or payload.get("dt2"),
        ),
    )
    budget = _first_non_empty(
        meta.get("budget"),
        meta.get("research_expense"),
        meta.get("total_budget"),
        meta.get("rndco_tot_amt"),
        payload.get("rndco_tot_amt"),
    )
    outputs = _collect_output_values(meta.get("outputs")) or _collect_output_values(payload.get("outputs"))
    perf_type = _first_non_empty(payload.get("perf_type"), meta.get("perf_type"), payload.get("tag"), meta.get("tag"))
    affiliation = _first_non_empty(
        meta.get("affiliation"),
        meta.get("blng_org_nm"),
        *_collect_nested_values(payload.get("prtcp_mp"), "blng_org_nm"),
    )
    year = _first_non_empty(payload.get("stan_yr"), meta.get("stan_yr"), payload.get("dt1"))
    tag = _first_non_empty(payload.get("tag"), meta.get("tag"))
    relation = _first_non_empty(payload.get("relation"), meta.get("relation"))

    # 2026-05-26: 참여자 이름↔역할 매핑을 facts에 노출. anchor 인물 검색 시 grounding
    # judge가 evidence와 인물의 의미 매칭을 할 수 있게 한다 (이전엔 인명만 있어서 partial verdict).
    participant_role_map: List[Dict[str, str]] = []
    for member in _normalize_nested_list(payload.get("prtcp_mp")):
        name = _clean_text(member.get("hm_nm")) or _clean_text(member.get("person_name"))
        if not name:
            continue
        role = _clean_text(member.get("role_slct_nm")) or _clean_text(member.get("role_nm")) or _clean_text(member.get("role"))
        entry: Dict[str, str] = {"name": name}
        if role:
            entry["role"] = role
        participant_role_map.append(entry)
    # top-level (perf payload 자체가 연구자일 때)
    top_level_name = _clean_text(payload.get("hm_nm")) or _clean_text(payload.get("person_name"))
    if top_level_name:
        top_level_role = _clean_text(payload.get("role_slct_nm")) or _clean_text(payload.get("role_nm")) or _clean_text(payload.get("role"))
        entry = {"name": top_level_name}
        if top_level_role:
            entry["role"] = top_level_role
        # 중복 방지 (top-level 이름이 prtcp_mp에도 있을 수 있음)
        if not any(p["name"] == top_level_name for p in participant_role_map):
            participant_role_map.append(entry)

    facts: Dict[str, Any] = {}
    if title:
        facts["title"] = title
    if summary:
        facts["summary"] = summary
    if goal:
        facts["goal"] = goal
    if period:
        facts["period"] = period
    if budget:
        facts["budget"] = budget
    if outputs:
        facts["outputs"] = outputs
    if perf_type:
        facts["perf_type"] = perf_type
    if affiliation:
        facts["affiliation"] = affiliation
    if year:
        facts["year"] = year
    if tag:
        facts["tag"] = tag
    if relation:
        facts["relation"] = relation
    if participant_role_map:
        facts["participant_role_map"] = participant_role_map
    return facts


@dataclass(frozen=True)
class CanonicalEvidence:
    """원천 payload에서 추출한 identity·roles·facts·provenance를 묶는 canonical evidence 단위다.
    renderer가 source payload shape를 모르더라도 같은 식별자·사실 장바구니를 읽을 수 있게 하는 중간 스키마다.
    """
    identity: str
    source_type: str
    ids: Dict[str, str] = field(default_factory=dict)
    roles: Dict[str, List[str]] = field(default_factory=dict)
    facts: Dict[str, Any] = field(default_factory=dict)
    child_entities: List[Dict[str, Any]] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """CanonicalEvidence를 직렬화 가능한 dict로 펼어준다.
        memory snapshot·debug response·render profile 저장에서 dataclass를 그대로 다루기 어려울 때 쓴다.
        """
        return asdict(self)


def build_canonical_evidence(
    payload: Dict[str, Any],
    *,
    rank: int,
    base_route: str,
    output_type: Optional[str],
) -> CanonicalEvidence:
    """하나의 raw payload를 CanonicalEvidence 항목으로 변환한다.
    source type, identity, ids, roles, facts, provenance를 함께 만들어 raw -> canonical 단계의 source of truth로 쓴다.
    """
    meta = _pick_meta(payload)
    ids = _collect_ids(payload, meta)
    facts = _collect_facts(payload, meta)
    roles = _collect_roles(payload, meta)
    child_entities = _collect_child_entities(payload, meta)
    source_type = _first_non_empty(payload.get("source_type"), base_route, "project")
    identity = _first_non_empty(
        ids.get("doc_id"),
        ids.get("rst_id"),
        ids.get("doi"),
        ids.get("issn"),
        ids.get("pjt_id"),
        ids.get("pjt_no"),
        ids.get("person_no"),
        ids.get("org_id"),
        facts.get("title"),
        f"{source_type}:{rank}",
    )
    evidence = {
        "title_text": _first_non_empty(payload.get("title_text"), facts.get("title")),
        "summary": _first_non_empty(payload.get("summary"), facts.get("summary")),
        "raw_payload_keys": sorted(str(key) for key in payload.keys()),
    }
    provenance = {
        "rank": int(rank),
        "base_route": _clean_text(base_route) or "project",
        "output_type": _clean_text(output_type) or "summary",
    }
    return CanonicalEvidence(
        identity=identity,
        source_type=source_type,
        ids=ids,
        roles=roles,
        facts=facts,
        child_entities=child_entities,
        evidence=evidence,
        provenance=provenance,
    )


def build_canonical_evidence_bundle(
    points: Sequence[Any],
    *,
    base_route: str,
    output_type: Optional[str],
    max_items: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """point 목록을 rank를 유지한 canonical evidence bundle로 바꾼다.
    context builder나 memory save가 여러 문서를 그대로 재사용할 수 있도록 통일 shape로 쌓어 돌려준다.
    """
    bundle: List[Dict[str, Any]] = []
    limit = len(points) if max_items is None else max(0, int(max_items))
    for rank, point in enumerate(list(points or [])[:limit], start=1):
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            continue
        bundle.append(
            build_canonical_evidence(
                payload,
                rank=rank,
                base_route=base_route,
                output_type=output_type,
            ).to_dict()
        )
    return bundle
