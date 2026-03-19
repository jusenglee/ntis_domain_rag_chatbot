from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence


def _clean_text(value: Any) -> str:
    """임의의 값을 공백이 정리된 문자열로 만든다.
    canonical evidence는 빈 값을 넣지 않는다는 가정이 있어 모든 수집 헬퍼가 먼저 이 정규화를 거친다.
    """
    return str(value or "").strip()


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


def _collect_ids(payload: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    """payload와 meta에서 의미 있는 식별자를 canonical id map으로 수집한다.
    `pjt_id`, `pjt_no`, `rst_id`, `doc_id`가 서로 다른 의미를 가진 채 보존되도록 필드를 섞지 않고 정리한다.
    """
    ids = {
        "pjt_id": _first_non_empty(payload.get("pjt_id"), meta.get("pjt_id"), meta.get("project_id")),
        "pjt_no": _first_non_empty(payload.get("pjt_no"), meta.get("pjt_no"), meta.get("project_no")),
        "rst_id": _first_non_empty(payload.get("rst_id"), meta.get("rst_id"), payload.get("perf_id")),
        "doc_id": _first_non_empty(payload.get("doc_id"), payload.get("id"), meta.get("doc_id")),
    }
    return {key: value for key, value in ids.items() if value}


def _collect_roles(payload: Dict[str, Any]) -> Dict[str, List[str]]:
    """payload 내 수행기관·참여기관·참여인력 소속을 role 별로 분리 수집한다.
    lead org, participant org, people affiliation을 같은 기관명 문자열로 통합하지 않고 역할 그대로 보존한다.
    """
    lead_org_name = _clean_text(payload.get("org_nm"))
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
        payload.get("title_text"),
        payload.get("title1"),
        payload.get("title2"),
        meta.get("kor_pjt_nm"),
        meta.get("eng_pjt_nm"),
        meta.get("title"),
    )
    summary = _first_non_empty(payload.get("summary"), meta.get("summary"), payload.get("content"))
    year = _first_non_empty(payload.get("stan_yr"), meta.get("stan_yr"), payload.get("dt1"))
    tag = _first_non_empty(payload.get("tag"), meta.get("tag"))
    relation = _first_non_empty(payload.get("relation"), meta.get("relation"))

    facts: Dict[str, Any] = {}
    if title:
        facts["title"] = title
    if summary:
        facts["summary"] = summary
    if year:
        facts["year"] = year
    if tag:
        facts["tag"] = tag
    if relation:
        facts["relation"] = relation
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
    roles = _collect_roles(payload)
    source_type = _first_non_empty(payload.get("source_type"), base_route, "project")
    identity = _first_non_empty(
        ids.get("doc_id"),
        ids.get("rst_id"),
        ids.get("pjt_id"),
        ids.get("pjt_no"),
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
