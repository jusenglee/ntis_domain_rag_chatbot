from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class RenderProfile:
    """canonical evidence를 어떤 fieldset과 context_kind로 렌더링할지 나타내는 프로필이다."""
    name: str
    fieldset: Tuple[str, ...]
    context_kind: str

    def to_dict(self) -> dict[str, object]:
        """RenderProfile을 직렬화 가능한 dict로 변환한다."""
        return asdict(self)


def resolve_render_profile(
    *,
    output_type: Optional[str],
    action: str,
    base_route: str,
    mode: str,
    fieldset: Tuple[str, ...],
    people_terms_present: bool,
    person_ids_present: bool,
    org_terms_present: bool,
    org_role_present: bool,
) -> RenderProfile:
    """output_type, action, base_route를 바탕으로 최종 render profile을 결정한다.

    lookup project 질의에서 사람/기관 조건이 강하면 context_kind를 people/org로 바꿔 같은 evidence라도 질문 축에 맞는 prompt view를 쓰게 한다.
    """
    normalized_output_type = str(output_type or "").strip().lower() or "summary"
    context_kind = str(base_route or "").strip().lower() or "project"
    mode_norm = str(mode or "").strip().lower()

    if mode_norm == "lookup" and context_kind == "project":
        if people_terms_present or person_ids_present:
            context_kind = "people"
        elif org_terms_present or org_role_present:
            context_kind = "org"

    profile_name = normalized_output_type
    if normalized_output_type == "summary" and str(action or "").strip().lower() in {"list", "stats", "download"}:
        profile_name = str(action or "").strip().lower()

    return RenderProfile(
        name=profile_name,
        fieldset=fieldset,
        context_kind=context_kind,
    )
