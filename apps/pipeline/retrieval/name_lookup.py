"""NTIS 이름 lookup — 사람/기관 실명을 Qdrant payload 검색으로 검증.

EntityResolverAgent가 DialogueAgent(LLM)의 subject_name 추출 결과를 NTIS 도메인에 매핑할 때
사용한다. LLM이 "LLM" 같은 기술 약어를 사람으로 오추출하는 경우, 이 lookup이 0건을 반환해
subject가 retrieval에 흘러가지 않게 한다.

설계:
    - 별도 person/org 컬렉션이 없으므로 ntis_project_v1 / ntis_perf_v1의 indexed nested 필드를 사용.
        * person: prtcp_mp_hm_nm_list (top-level 평탄화 indexed array) + prtcp_mp[].hm_nm (nested)
        * org:    prtcp_org[].org_nm (nested indexed)
    - 스코어가 아닌 존재만 확인 — scroll 호출, dense 임베딩 없음. 빠름 (~100ms).
    - 매칭된 point의 payload에서 nested 항목을 풀어 person_no/org_id 추출.
    - 휴리스틱 없음. 정확 매칭만 (substring 매칭 안 함 — 동명 부분일치 회피).

반환:
    - PersonHit(person_no, display_name, affiliation, source_collection)
    - OrgHit(org_id, org_code, display_name, source_collection)
    - 0건이면 빈 리스트.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger
from qdrant_client.models import FieldCondition, Filter, MatchAny


@dataclass(frozen=True)
class PersonHit:
    """이름으로 lookup된 사람 1명. dedup 키는 person_no."""

    person_no: str
    display_name: str
    affiliation: Optional[str] = None
    source_collection: Optional[str] = None


@dataclass(frozen=True)
class OrgHit:
    """이름으로 lookup된 기관 1곳. dedup 키는 org_id 또는 org_code."""

    org_id: Optional[str]
    org_code: Optional[str]
    display_name: str
    source_collection: Optional[str] = None


_DEFAULT_COLLECTIONS: Sequence[str] = ("ntis_project_v1", "ntis_perf_v1")


def lookup_person_by_name(
    *,
    qdrant_client: Any,
    name: str,
    collections: Sequence[str] = _DEFAULT_COLLECTIONS,
    scan_limit: int = 30,
    max_hits: int = 10,
) -> List[PersonHit]:
    """이름으로 사람 검색.

    Args:
        qdrant_client: QdrantClient 인스턴스.
        name: 검증할 이름 (DialogueAgent가 LLM에서 추출한 subject_name).
        collections: 검색 대상 컬렉션 목록.
        scan_limit: 컬렉션당 scroll 최대 point 수 (성능 가드).
        max_hits: dedup 후 최대 반환 person 수.

    Returns:
        매칭된 사람의 dedup 리스트 (person_no 기준). 0건이면 빈 리스트.
    """
    name_norm = (name or "").strip()
    if not name_norm:
        return []

    found: Dict[str, PersonHit] = {}
    for collection in collections:
        try:
            qfilter = Filter(must=[FieldCondition(
                key="prtcp_mp_hm_nm_list",
                match=MatchAny(any=[name_norm]),
            )])
            points, _ = qdrant_client.scroll(
                collection_name=collection,
                scroll_filter=qfilter,
                limit=scan_limit,
                with_payload=["prtcp_mp"],
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[name_lookup] person_scroll_failure(인물 이름 lookup Qdrant scroll 실패) "
                f"collection={collection}(컬렉션) name={name_norm!r}(검색 이름) error={exc}"
            )
            continue

        for pt in (points or []):
            payload = getattr(pt, "payload", None) or {}
            members = payload.get("prtcp_mp") or []
            if not isinstance(members, list):
                continue
            for member in members:
                if not isinstance(member, dict):
                    continue
                hm_nm = (member.get("hm_nm") or "").strip()
                if hm_nm != name_norm:
                    continue
                pid = (
                    (member.get("hm_id") or "").strip()
                    or (member.get("person_no") or "").strip()
                )
                if not pid or pid in found:
                    continue
                found[pid] = PersonHit(
                    person_no=pid,
                    display_name=hm_nm,
                    affiliation=(member.get("blng_org_nm") or "").strip() or None,
                    source_collection=collection,
                )
                if len(found) >= max_hits:
                    break
            if len(found) >= max_hits:
                break
        if len(found) >= max_hits:
            break
    return list(found.values())


def lookup_org_by_name(
    *,
    qdrant_client: Any,
    name: str,
    collections: Sequence[str] = _DEFAULT_COLLECTIONS,
    scan_limit: int = 30,
    max_hits: int = 10,
) -> List[OrgHit]:
    """이름으로 기관 검색. prtcp_org[].org_nm nested 매칭.

    Returns:
        매칭된 기관의 dedup 리스트 (org_id/org_code 기준). 0건이면 빈 리스트.
    """
    name_norm = (name or "").strip()
    if not name_norm:
        return []

    found: Dict[str, OrgHit] = {}
    for collection in collections:
        try:
            qfilter = Filter(must=[FieldCondition(
                key="prtcp_org.org_nm",
                match=MatchAny(any=[name_norm]),
            )])
            points, _ = qdrant_client.scroll(
                collection_name=collection,
                scroll_filter=qfilter,
                limit=scan_limit,
                with_payload=["prtcp_org", "org_nm", "org_id", "org_cd"],
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"[name_lookup] org_scroll_failure(기관 이름 lookup Qdrant scroll 실패) "
                f"collection={collection}(컬렉션) name={name_norm!r}(검색 이름) error={exc}"
            )
            continue

        for pt in (points or []):
            payload = getattr(pt, "payload", None) or {}
            orgs = payload.get("prtcp_org") or []
            if not isinstance(orgs, list):
                continue
            for org in orgs:
                if not isinstance(org, dict):
                    continue
                org_nm = (org.get("org_nm") or "").strip()
                if org_nm != name_norm:
                    continue
                oid = (org.get("org_id") or "").strip()
                ocd = (org.get("org_cd") or org.get("org_code") or "").strip()
                key = oid or ocd or org_nm
                if key in found:
                    continue
                found[key] = OrgHit(
                    org_id=oid or None,
                    org_code=ocd or None,
                    display_name=org_nm,
                    source_collection=collection,
                )
                if len(found) >= max_hits:
                    break
            if len(found) >= max_hits:
                break
        if len(found) >= max_hits:
            break
    return list(found.values())
