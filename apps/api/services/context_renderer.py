"""Context rendering helpers extracted from the legacy app entry module."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document

from apps.api.rag_mapper.rag_mapper import RagMapper, MappingError

from apps.api.services.context_helpers import apply_title_preference, collect_priority_field_lines


def normalize_researcher_token(value: Optional[str]) -> str:
    """연구자/기관 비교용 토큰을 알파넘릭 중심으로 정규화한다."""
    if not value:
        return ""
    return re.sub(r"[^0-9a-zA-Z?-?]", "", str(value)).lower()


def normalize_hint_terms(values: Any) -> list[str]:
    """LLM/planner에서 온 hint 값을 빈 값·none·중복 없는 문자열 목록으로 정리한다."""
    if values is None:
        return []
    if isinstance(values, (str, int, float)):
        items = [values]
    elif isinstance(values, list):
        items = values
    else:
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text or text.lower() == "none":
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
    return result


def extract_researcher_fields(researcher: Any) -> tuple[str, str, str]:
    """dict 또는 object 형태의 researcher에서 name, affiliation, researcher_id를 꼭 집어 온다."""
    if isinstance(researcher, dict):
        name = researcher.get("name")
        affiliation = researcher.get("affiliation")
        researcher_id = researcher.get("researcher_id")
    else:
        name = getattr(researcher, "name", None)
        affiliation = getattr(researcher, "affiliation", None)
        researcher_id = getattr(researcher, "researcher_id", None)
    return (
        str(name).strip() if name else "",
        str(affiliation).strip() if affiliation else "",
        str(researcher_id).strip() if researcher_id else "",
    )


def extract_org_fields(org: Any) -> tuple[str, str, str]:
    """dict 또는 object 형태의 org에서 이름, id, role를 읽어 온다."""
    if isinstance(org, dict):
        name = org.get("org_nm") or org.get("org_name") or org.get("name")
        org_id = org.get("org_id") or org.get("org_cd") or org.get("org_code") or org.get("org_no")
        role = org.get("org_slct_nm") or org.get("role") or org.get("org_role")
    else:
        name = getattr(org, "org_nm", None) or getattr(org, "name", None)
        org_id = getattr(org, "org_id", None) or getattr(org, "org_cd", None) or getattr(org, "org_code", None) or getattr(org, "org_no", None)
        role = getattr(org, "org_slct_nm", None) or getattr(org, "role", None)
    return (
        str(name).strip() if name else "",
        str(org_id).strip() if org_id else "",
        str(role).strip() if role else "",
    )


def collect_org_hints(
    organizations: Optional[List[Any]],
    filters: Optional[Dict[str, Any]],
    ids_map: Optional[Dict[str, Any]],
) -> tuple[list[str], list[str], Optional[str]]:
    """organizations, filters, ids_map에서 기관 이름/id/role 힌트를 한데 모은다."""
    org_terms: list[str] = []
    org_ids: list[str] = []
    role_hint: Optional[str] = None

    for org in organizations or []:
        if isinstance(org, dict):
            name = org.get("name") or org.get("org_nm") or org.get("org_name")
            org_id = org.get("org_id") or org.get("org_cd") or org.get("org_code")
            if name:
                org_terms.append(str(name).strip())
            if org_id:
                org_ids.append(str(org_id).strip())
        else:
            org_terms.append(str(org).strip())

    if isinstance(filters, dict):
        org_terms += normalize_hint_terms(filters.get("org_name") or filters.get("org"))
        org_ids += normalize_hint_terms(filters.get("org_id"))
        role_hint = filters.get("org_role") or role_hint

    if isinstance(ids_map, dict):
        org_ids += normalize_hint_terms(ids_map.get("org_id"))

    return normalize_hint_terms(org_terms), normalize_hint_terms(org_ids), str(role_hint).strip() if role_hint else None


def match_prtcp_orgs(
    prtcp_orgs: List[Dict[str, Any]],
    org_terms: list[str],
    org_ids: list[str],
    role_hint: Optional[str],
    *,
    max_matches: int = 5,
) -> List[Dict[str, Any]]:
    """참여기관 list에서 이름, id, role 힌트와 가장 잘 맞는 org를 선별한다."""
    if not prtcp_orgs or (not org_terms and not org_ids):
        return []

    org_terms_exact = {term.strip() for term in org_terms if term.strip()}
    org_terms_norm = {normalize_researcher_token(term) for term in org_terms_exact}
    org_ids_set = {str(org_id).strip() for org_id in org_ids if str(org_id).strip()}
    role_hint_norm = normalize_researcher_token(role_hint) if role_hint else ""

    candidates: list[dict[str, Any]] = []
    for org in prtcp_orgs:
        org_nm, org_id, role = extract_org_fields(org)
        org_nm_norm = normalize_researcher_token(org_nm)
        role_norm = normalize_researcher_token(role)

        score = 0.0
        match_type = None
        if org_id and org_id in org_ids_set:
            score = 3.0
            match_type = "id_exact"
        if org_nm and org_nm in org_terms_exact and score < 2.5:
            score = 2.5
            match_type = "name_exact"
        if org_nm_norm and org_nm_norm in org_terms_norm and score < 2.0:
            score = 2.0
            match_type = "name_norm"
        if score <= 0:
            continue

        role_confirmed = match_type in {"id_exact", "name_exact"}
        if role_hint_norm and role_norm and role_norm == role_hint_norm:
            role_confirmed = True
            score += 0.1

        candidates.append({
            "org_nm": org_nm or "org_unknown",
            "org_id": org_id,
            "org_slct_nm": role,
            "match_type": match_type,
            "role_confirmed": role_confirmed,
            "score": score,
        })

    candidates.sort(key=lambda item: item.get("score", 0), reverse=True)
    seen_keys: set[tuple[str, str]] = set()
    matches: list[dict[str, Any]] = []
    for item in candidates:
        key = (str(item.get("org_id") or ""), normalize_researcher_token(item.get("org_nm")))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        matches.append(item)
        if len(matches) >= max_matches:
            break
    return matches


def format_org_entry(org_nm: str, role: str, role_confirmed: bool) -> str:
    """org 이름과 role 확신도를 포함한 표시 문구로 만든다."""
    if role:
        if role_confirmed:
            return f"{org_nm}({role})"
        return f"{org_nm}(candidate_role: {role})"
    return org_nm


def match_prtcp_members(
    prtcp_members: List[Dict[str, Any]],
    researchers: Optional[List[Any]],
    *,
    max_matches: int = 5,
) -> List[Dict[str, Any]]:
    """참여인력 list에서 연구자 힌트와 가장 맞는 member를 점수 기반으로 선별한다."""
    if not prtcp_members or not researchers:
        return []

    matches: List[Dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for researcher in researchers:
        name, affiliation, researcher_id = extract_researcher_fields(researcher)
        name_norm = normalize_researcher_token(name)
        affiliation_norm = normalize_researcher_token(affiliation)
        best_member = None
        best_score = 0.0

        for member in prtcp_members:
            hm_id = str(member.get("hm_id") or "").strip()
            hm_nm = str(member.get("hm_nm") or "").strip()
            org_nm = str(member.get("blng_org_nm") or "").strip()

            score = 0.0
            match_type = ""
            if researcher_id and hm_id and researcher_id == hm_id:
                score = 3.0
                match_type = "id_exact"
            else:
                hm_nm_norm = normalize_researcher_token(hm_nm)
                if name_norm and hm_nm_norm and name_norm == hm_nm_norm:
                    score = 2.0
                    match_type = "name_exact"
                    if affiliation_norm:
                        org_norm = normalize_researcher_token(org_nm)
                        if org_norm and org_norm == affiliation_norm:
                            score = 2.5
                            match_type = "name_affiliation_exact"
                elif name_norm and hm_nm_norm and (name_norm in hm_nm_norm or hm_nm_norm in name_norm):
                    score = 1.2
                    match_type = "name_partial"
                    if affiliation_norm:
                        org_norm = normalize_researcher_token(org_nm)
                        if org_norm and (affiliation_norm in org_norm or org_norm in affiliation_norm):
                            score = 1.6
                            match_type = "name_affiliation_partial"

            if score > best_score:
                best_score = score
                best_member = dict(member)
                if match_type:
                    best_member["match_type"] = match_type
                best_member["match_score"] = score

        if best_member and best_score > 0:
            dedup_key = (
                str(best_member.get("hm_id") or normalize_researcher_token(best_member.get("hm_nm"))),
                normalize_researcher_token(best_member.get("blng_org_nm")),
            )
            if dedup_key not in seen_keys:
                seen_keys.add(dedup_key)
                matches.append(best_member)
                if len(matches) >= max_matches:
                    break

    return matches


def format_researcher_line(
    matched_members: List[Dict[str, Any]],
    fallback_lines: List[str],
    is_detail: bool = False,
    max_matches: int = 5,
) -> str:
    """매칭된 연구자를 context 한 줄 문구로 만들고, 없으면 fallback 라인을 사용한다."""
    if matched_members:
        names = []
        for member in matched_members[:max_matches]:
            name = str(member.get("hm_nm") or "name_unknown").strip()
            org = str(member.get("blng_org_nm") or "org_unknown").strip()
            match_type = str(member.get("match_type") or "").strip()
            names.append(f"{name}({org}{', ' + match_type if match_type else ''})")
        return f"- researchers: {', '.join(names)}"

    fallback_names = []
    source_lines = fallback_lines if is_detail else fallback_lines[:max_matches]
    for line in source_lines:
        cleaned = line.lstrip("- ").strip()
        if cleaned:
            fallback_names.append(cleaned)
    if fallback_names:
        suffix = "" if is_detail else " (truncated)"
        return f"- researchers: {', '.join(fallback_names)}{suffix}"
    return "- researchers: unavailable"


def format_org_line(
    matched_orgs: List[Dict[str, Any]],
    prtcp_orgs: List[Dict[str, Any]],
    *,
    max_matches: int = 5,
) -> str:
    """매칭된 기관 또는 fallback 참여기관을 context 한 줄 문구로 만든다."""
    if matched_orgs:
        entries = []
        for org in matched_orgs[:max_matches]:
            org_nm = str(org.get("org_nm") or "org_unknown").strip()
            role = str(org.get("org_slct_nm") or "").strip()
            role_confirmed = bool(org.get("role_confirmed"))
            entries.append(format_org_entry(org_nm, role, role_confirmed))
        return f"- participant_orgs(matched): {', '.join(entries)}"

    if prtcp_orgs:
        entries = []
        for org in prtcp_orgs[:max_matches]:
            org_nm, _, role = extract_org_fields(org)
            entries.append(format_org_entry(org_nm or "org_unknown", role, False))
        return f"- participant_orgs: {', '.join(entries)}"
    return "- participant_orgs: unavailable"


def safe_map_doc(doc: Document, *, context: str, logger: Any) -> Optional[Dict[str, Any]]:
    """Document를 RagMapper로 매핑하되 실패 시 경고를 남기고 None을 돌려준다."""
    try:
        return RagMapper.map(doc)
    except MappingError as exc:
        source_idx = doc.get("source_index")
        logger.warning("document mapping failed (%s): source_index=%s error=%s", context, source_idx, exc)
        return None


def split_sentences(text: str) -> List[str]:
    """문단 텍스트를 문장 단위로 나눠 후속 요약 제한에 쓴다."""
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    sentences: List[str] = []
    for line in raw_lines:
        parts = re.split(r"(?<=[.!?])\s+", line)
        cleaned = [part.strip() for part in parts if part.strip()]
        sentences.extend(cleaned or [line])
    return sentences


def limit_text_by_sentences_and_tokens(text: str, *, max_sentences: Optional[int], max_tokens: Optional[int]) -> str:
    """최대 문장 수와 토큰 수를 넘지 않는 선에서 텍스트를 잘라낸다."""
    if not text:
        return ""
    if max_sentences is None or max_tokens is None:
        return text.strip()
    sentences = split_sentences(text)
    limited: List[str] = []
    token_count = 0
    for sentence in sentences:
        next_tokens = len(sentence.split())
        if limited and (len(limited) >= max_sentences or token_count + next_tokens > max_tokens):
            break
        limited.append(sentence)
        token_count += next_tokens
        if len(limited) >= max_sentences:
            break
    return "\n".join(limited)


def format_metadata(metadata: Dict[str, Any], *, max_sentences: Optional[int] = None, max_tokens: Optional[int] = None) -> str:
    """metadata dict를 `- key: value` 묶음으로 바꾸고 필요하면 값을 더 줄인다."""
    lines = []
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, list):
            value = ", ".join(map(str, value))
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        if max_sentences is not None and max_tokens is not None:
            value = limit_text_by_sentences_and_tokens(str(value), max_sentences=max_sentences, max_tokens=max_tokens)
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) if lines else ""


def refine_documents_rule_based(
    docs: List[Document],
    is_detail: bool = False,
    *,
    researchers: Optional[List[Any]] = None,
    organizations: Optional[List[Any]] = None,
    org_filters: Optional[Dict[str, Any]] = None,
    ids_map: Optional[Dict[str, Any]] = None,
    max_matches: int = 5,
    relax_limits: bool = False,
    max_doc_sentences: Optional[int] = None,
    max_doc_tokens: Optional[int] = None,
    priority_context_fields: tuple[str, ...],
    max_field_sentences: int,
    max_field_tokens: int,
    default_max_doc_sentences: int,
    default_max_doc_tokens: int,
    logger: Any,
) -> str:
    """문서를 rule-based로 정리해 context chunk 묶음으로 만든다."""
    context_chunks: List[str] = []
    field_max_sentences = None if relax_limits else max_field_sentences
    field_max_tokens = None if relax_limits else max_field_tokens
    if relax_limits:
        doc_max_sentences = None
        doc_max_tokens = None
    else:
        doc_max_sentences = max_doc_sentences if max_doc_sentences is not None else default_max_doc_sentences
        doc_max_tokens = max_doc_tokens if max_doc_tokens is not None else default_max_doc_tokens

    for doc in docs:
        if str(doc.get("source_type", "")).strip().lower() == "aggregation":
            rank_item = doc.get("rank_item") or {}
            metric = str(doc.get("metric") or "project_participation_count")
            metric_value = rank_item.get(metric, rank_item.get("score", 0))
            perf_count = rank_item.get("performance_count", 0)
            candidate_docs = int(doc.get("candidate_docs") or 0)
            window_years = doc.get("window_years") or {}
            year_from = (window_years.get("from") if isinstance(window_years, dict) else None) or "-"
            year_to = (window_years.get("to") if isinstance(window_years, dict) else None) or "-"
            person_name = rank_item.get("hm_nm") or rank_item.get("hm_id") or rank_item.get("person_key") or "unknown"
            context_chunks.append(
                f"## Source {doc.get('source_index')}. {person_name}\n"
                f"- {metric}: {metric_value}\n"
                f"- performance_count: {perf_count}\n"
                f"- candidate_docs: {candidate_docs}\n"
                f"- window_years: {year_from} ~ {year_to}\n"
            )
            continue

        mapped_doc = safe_map_doc(doc, context="refine_documents_rule_based", logger=logger)
        if not mapped_doc:
            continue
        apply_title_preference(mapped_doc)

        source_idx = doc.get("source_index")
        title = mapped_doc.get("title", "title_unavailable")
        meta_basic_text = format_metadata(mapped_doc.get("meta_basic", {}), max_sentences=field_max_sentences, max_tokens=field_max_tokens)
        meta_detail_text = format_metadata(mapped_doc.get("meta_detail", {}), max_sentences=field_max_sentences, max_tokens=field_max_tokens) if is_detail else ""
        refined_text = "\n".join([text for text in [meta_basic_text, meta_detail_text] if text])

        prtcp_members = mapped_doc.get("prtcp_mp", []) if isinstance(mapped_doc, dict) else []
        matched_members = match_prtcp_members(prtcp_members, researchers, max_matches=max_matches)
        fallback_lines = RagMapper.get_researcher_info(mapped_doc)
        researcher_line = format_researcher_line(matched_members, fallback_lines, is_detail, max_matches=max_matches)

        prtcp_orgs = mapped_doc.get("prtcp_org", []) if isinstance(mapped_doc, dict) else []
        org_terms, org_ids, role_hint = collect_org_hints(organizations, org_filters, ids_map)
        matched_orgs = match_prtcp_orgs(prtcp_orgs, org_terms, org_ids, role_hint, max_matches=max_matches)
        org_line = format_org_line(matched_orgs, prtcp_orgs, max_matches=max_matches)
        priority_lines = collect_priority_field_lines(mapped_doc, priority_context_fields)

        limited_body = limit_text_by_sentences_and_tokens(refined_text, max_sentences=doc_max_sentences, max_tokens=doc_max_tokens)
        body_sentences = split_sentences(limited_body)
        body_token_counts = [len(sentence.split()) for sentence in body_sentences]
        body_token_count = sum(body_token_counts)
        extra_lines = [line for line in priority_lines + [researcher_line, org_line] if line]
        extra_text = "\n".join(extra_lines)
        extra_sentences = split_sentences(extra_text)
        extra_token_count = len(extra_text.split())

        if not relax_limits and doc_max_sentences is not None and doc_max_tokens is not None:
            while body_sentences and (len(body_sentences) + len(extra_sentences) > doc_max_sentences or body_token_count + extra_token_count > doc_max_tokens):
                body_token_count -= body_token_counts.pop()
                body_sentences.pop()

        limited_body = "\n".join(body_sentences).strip()
        limited_text = "\n".join([part for part in [limited_body] + extra_lines if part]).strip()
        context_chunks.append(f"## Source {source_idx}. {title}\n{limited_text}\n")

    return "\n\n".join(context_chunks)

