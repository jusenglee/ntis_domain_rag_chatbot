from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping

from apps.platform.rag_constants import COL_PERF, COL_PROJECT, COL_SUPPORT
from apps.retrieval.rag_rank_runtime import final_rerank as final_rerank_runtime, normalize_tag_value, resolve_collection


@dataclass(frozen=True)
class RerankSupportRuntime:
    """RAG rerank 보조 함수가 공유하는 runtime 의존성입니다."""

    payload_get_fn: Callable[..., Any]
    get_meta_fn: Callable[[dict], dict]
    payload_title_fn: Callable[[Dict[str, Any], Dict[str, Any]], str]
    clip_text_fn: Callable[[object, int], str]
    log_kv_fn: Callable[..., None]
    log_section_fn: Callable[..., None]


def _to_text(value: object) -> str:
    """값을 빈칸 안전한 문자열로 바꿉니다."""

    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_title_match(text: object) -> str:
    """제목 비교용 텍스트를 느슨하게 정규화합니다."""

    if text is None:
        return ""
    value = unicodedata.normalize("NFKC", str(text))
    value = value.replace("\u00A0", " ")
    value = re.sub(r"[\u2000-\u200B\u202F\u205F\u3000]", " ", value)
    value = re.sub(r"[\[\]{}()<>-]", " ", value)
    value = re.sub(r"[\"'`]", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def soft_title_contains(doc_payload: Mapping[str, Any], title_terms: List[str]) -> bool:
    """제목 필드에 title term이 포함되는지 느슨하게 판정합니다."""

    if not isinstance(doc_payload, Mapping):
        return False
    normalized_terms: list[str] = []
    seen_terms: set[str] = set()
    for raw in title_terms or []:
        term = normalize_for_title_match(raw)
        if len(term) <= 2:
            continue
        lowered = term.lower()
        if lowered and lowered not in seen_terms:
            seen_terms.add(lowered)
            normalized_terms.append(lowered)
    if not normalized_terms:
        return False
    for field in ("title1", "title2", "title_text"):
        title_val = normalize_for_title_match(doc_payload.get(field, "")).lower()
        if not title_val:
            continue
        if any(term in title_val for term in normalized_terms):
            return True
    return False


def soft_title_match_count(doc_payload: Mapping[str, Any], title_terms: List[str]) -> int:
    """제목 단서가 몇 개 매칭되는지 계산합니다."""

    if not isinstance(doc_payload, Mapping):
        return 0
    titles = [normalize_for_title_match(doc_payload.get(field, "")).lower() for field in ("title1", "title2", "title_text")]
    titles = [value for value in titles if value]
    if not titles:
        return 0
    hits: set[str] = set()
    for raw in title_terms or []:
        term = normalize_for_title_match(raw).lower()
        if term and any(term in title for title in titles):
            hits.add(term)
    return len(hits)


def prefer_meta_title(payload: Dict[str, Any], meta: Dict[str, Any]) -> str:
    """payload 제목이 식별자일 때 meta 제목을 우선합니다."""

    title = _to_text(payload.get("title_text") or payload.get("title1") or payload.get("title2") or "")
    meta_title = _to_text(meta.get("kor_pjt_nm") or meta.get("eng_pjt_nm") or "")
    if not title:
        return meta_title
    pjt_id = _to_text(payload.get("pjt_id") or meta.get("pjt_id") or "")
    if meta_title and (title.isdigit() or title.lower().startswith("ntis:") or (pjt_id and title == pjt_id)):
        return meta_title
    return title or meta_title


def payload_text_bundle(point: Any, *, runtime: RerankSupportRuntime) -> Dict[str, str]:
    """rerank 계산에 사용할 주요 텍스트 번들을 조립합니다."""

    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    meta = runtime.get_meta_fn(payload)
    title = prefer_meta_title(payload, meta)
    flat_text = _to_text(payload.get("flat_text") or payload.get("keyword_text") or payload.get("summary") or "")
    content_text = _to_text(payload.get("content_text") or payload.get("content") or payload.get("content1") or payload.get("content2") or "")
    keyword_text = _to_text(payload.get("keyword_text") or payload.get("keyword1") or payload.get("keyword2") or "")
    category_text = _to_text(payload.get("category") or payload.get("cetegory") or "")
    prtcp_mp_names = _to_text(runtime.payload_get_fn(payload, "prtcp_mp[].hm_nm"))
    meta_kv = []
    for key, value in (meta or {}).items():
        value_text = _to_text(value)
        if value_text:
            meta_kv.append(f"{key}:{value_text}")
    if prtcp_mp_names:
        meta_kv.append(f"prtcp_mp_hm_nm:{prtcp_mp_names}")
    return {
        "title_text": title,
        "flat_text": flat_text[:2000],
        "content_text": content_text[:2000],
        "keyword_text": keyword_text[:1200],
        "category": category_text[:200],
        "meta_kv": _to_text('; '.join(meta_kv))[:800],
    }


def _count_term_hits(text: str, term: str) -> int:
    """주어진 텍스트에서 단서가 모7 번 등장하는지 계산합니다."""

    if not text or not term:
        return 0
    return text.lower().count(term.lower())


def _flatten_ids_from_intent(intent: Any) -> List[str]:
    """intent에서 평탄한 식별자 목록을 꼴아냅니다."""

    flat = getattr(intent, "ids_flat", None)
    if isinstance(flat, list) and flat:
        out: List[str] = []
        for value in flat:
            text = str(value).strip()
            if text and text not in out:
                out.append(text)
        return out
    ids_map = getattr(intent, "ids_map", None)
    if not isinstance(ids_map, dict):
        ids_map = getattr(intent, "ids", None)
    if not isinstance(ids_map, dict):
        return []
    out: List[str] = []
    for values in ids_map.values():
        if not isinstance(values, list):
            continue
        for value in values:
            text = str(value).strip()
            if text and text not in out:
                out.append(text)
    return out


def keyword_score(point: Any, keywords: List[str], weights: Dict[str, float], *, runtime: RerankSupportRuntime) -> float:
    """키워드 분포를 기반으로 rerank 점수를 계산합니다."""

    weights = weights or {}
    bundle = payload_text_bundle(point, runtime=runtime)
    score = 0.0
    for keyword in (keywords or [])[:30]:
        keyword = keyword.strip()
        if not keyword:
            continue
        score += float(weights.get("title_text", 2.0)) * min(_count_term_hits(bundle["title_text"], keyword), 2)
        score += float(weights.get("flat_text", 0.6)) * min(_count_term_hits(bundle["flat_text"], keyword), 4)
        score += float(weights.get("content_text", 1.0)) * min(_count_term_hits(bundle["content_text"], keyword), 3)
        score += float(weights.get("keyword_text", 0.8)) * min(_count_term_hits(bundle["keyword_text"], keyword), 3)
        score += float(weights.get("category", 0.2)) * min(_count_term_hits(bundle["category"], keyword), 2)
        score += float(weights.get("meta_kv", 0.2)) * min(_count_term_hits(bundle["meta_kv"], keyword), 2)
    return float(score)


def keyword_exact_match_hits(point: Any, keywords: List[str], *, runtime: RerankSupportRuntime) -> int:
    """제목과 키워드 필드에서 exact match hit 수를 센다."""

    bundle = payload_text_bundle(point, runtime=runtime)
    title = (bundle.get("title_text") or "").lower()
    keyword_text = (bundle.get("keyword_text") or "").lower()
    hits = 0
    for keyword in (keywords or [])[:30]:
        lowered = keyword.strip().lower()
        if not lowered:
            continue
        if lowered in title:
            hits += 1
        if lowered in keyword_text:
            hits += 1
    return hits


def filter_score(point: Any, intent: Any, base_route: str, *, strict_ids: bool, mode: str, runtime: RerankSupportRuntime) -> float:
    """identifier, 연도, 기관, 사람, tag 조건 기반 보정점수를 계산합니다."""

    bundle = payload_text_bundle(point, runtime=runtime)
    hay = ' | '.join([bundle["title_text"], bundle["flat_text"], bundle["meta_kv"], bundle["content_text"]]).lower()
    score = 0.0
    for value in _flatten_ids_from_intent(intent)[:10]:
        score += 120.0 if value.lower() in hay else (-45.0 if strict_ids else -12.0)
    for year in [str(y).strip() for y in (getattr(intent, "years", []) or []) if str(y).strip()][:6]:
        if year.lower() in hay:
            score += 35.0
    is_search_mode = str(mode or '').strip().lower() == 'search'
    for term in [t.strip() for t in (getattr(intent, "title", None) or []) if t.strip()][:6]:
        if term.lower() in hay:
            score += 50.0 if is_search_mode else 32.0
        elif strict_ids:
            score -= 4.0
    for term in [t.strip() for t in (getattr(intent, "org_terms", None) or []) if t.strip()][:4]:
        if term.lower() in hay:
            score += 72.0 if is_search_mode else 60.0
        elif strict_ids:
            score -= 10.0
    for term in [t.strip() for t in (getattr(intent, "people_terms", None) or []) if t.strip()][:4]:
        if term.lower() in hay:
            score += 82.0 if is_search_mode else 70.0
        elif strict_ids:
            score -= 15.0
    payload = getattr(point, 'payload', None) or {}
    if not isinstance(payload, dict):
        payload = {}
    tag = normalize_tag_value(payload.get('tag'))
    col = resolve_collection(point, payload)
    if getattr(intent, 'perf_tag_filters', None) and col == COL_PERF:
        if any(normalize_tag_value(value) == tag for value in list(getattr(intent, 'perf_tag_filters', None) or [])[:8]):
            score += 108.0 if is_search_mode else 90.0
    if getattr(intent, 'project_tag_filters', None) and col == COL_PROJECT:
        if any(normalize_tag_value(value) == tag for value in list(getattr(intent, 'project_tag_filters', None) or [])[:8]):
            score += 96.0 if is_search_mode else 80.0
    return float(score)


def family_bonus(point: Any, base_route: str) -> float:
    """base route와 후보 collection family가 맞는지 보너스를 계산합니다."""

    payload = getattr(point, 'payload', None) or {}
    col = resolve_collection(point, payload if isinstance(payload, dict) else {})
    route = str(base_route or '').strip().lower()
    if route == 'project' and col == COL_PROJECT:
        return 1.0
    if route == 'perf' and col == COL_PERF:
        return 1.0
    if route == 'support' and col == COL_SUPPORT:
        return 1.0
    return 0.0


def must_contain_terms(point: Any, terms: List[str], *, runtime: RerankSupportRuntime) -> bool:
    """핵심 사람명/기관명 단서가 후보 텍스트에 모두 포함되는지 확인합니다."""

    bundle = payload_text_bundle(point, runtime=runtime)
    hay = ' | '.join([bundle["title_text"], bundle["flat_text"], bundle["meta_kv"], bundle["content_text"]]).lower()
    for term in terms or []:
        lowered = term.strip().lower()
        if lowered and lowered not in hay:
            return False
    return True


def build_final_rerank(
    *,
    payload_get: Callable[..., Any],
    get_meta: Callable[[dict], dict],
    payload_title: Callable[[Dict[str, Any], Dict[str, Any]], str],
    clip_text: Callable[[object, int], str],
    log_kv: Callable[..., None],
    log_section: Callable[..., None],
) -> Callable[..., List[Any]]:
    """final rerank가 필요한 collaborator를 직접 묶어 실행 함수를 생성합니다."""

    runtime = RerankSupportRuntime(
        payload_get_fn=payload_get,
        get_meta_fn=get_meta,
        payload_title_fn=payload_title,
        clip_text_fn=clip_text,
        log_kv_fn=log_kv,
        log_section_fn=log_section,
    )

    def _final_rerank(cands: List[Any], *, it: Any, kws: List[str], lex_w: Dict[str, float], base_route: str, mode: str, keep: int, tag_boost: float = 0.0, tag_mismatch_penalty: float = 0.0, title_soft_terms: List[str] | None = None, title_soft_boost: float = 0.0) -> List[Any]:
        """고정된 runtime 의존성으로 final rerank를 실행합니다."""
        return final_rerank_runtime(
            cands,
            it=it,
            kws=kws,
            lex_w=lex_w,
            base_route=base_route,
            mode=mode,
            keep=keep,
            tag_boost=tag_boost,
            tag_mismatch_penalty=tag_mismatch_penalty,
            title_soft_terms=title_soft_terms,
            title_soft_boost=title_soft_boost,
            must_contain_terms=lambda point, terms: must_contain_terms(point, terms, runtime=runtime),
            keyword_score=lambda point, keywords, weights: keyword_score(point, keywords, weights, runtime=runtime),
            keyword_exact_match_hits=lambda point, keywords: keyword_exact_match_hits(point, keywords, runtime=runtime),
            filter_score=lambda point, intent, route, strict_ids, mode='search': filter_score(point, intent, route, strict_ids=strict_ids, mode=mode, runtime=runtime),
            family_bonus=family_bonus,
            log_kv=runtime.log_kv_fn,
            log_section=runtime.log_section_fn,
            clip_text=runtime.clip_text_fn,
            payload_title=runtime.payload_title_fn,
            get_meta=runtime.get_meta_fn,
        )

    return _final_rerank
