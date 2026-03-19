from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from apps.core.rag_constants import PERF_TAGS, PROJECT_TAGS


@dataclass
class RankSource:
    """재순위화에 투입할 히트 세트의 출처와 점수 근거를 함께 묶는다.
    태그 보너스, 원본 점수, 컬렉션 명칭을 같이 들고 다니며 final rerank가 출처별 점수 성향을 비교할 수 있게 한다.
    """
    name: str
    weight: float
    points: List[Any]


def normalize_tag_value(tag: object) -> str:
    """성과 태그 값을 대소문자와 공백 차이에 무관한 비교용 문자열로 정규화한다.
    태그 필터와 payload 태그를 같은 기준으로 비교하기 위한 전처리 단계다.
    """
    if tag is None:
        return ""
    text = str(tag).strip().upper()
    return text[4:] if text.startswith("IRD_") else text


PROJECT_TAGS_NORM = {normalize_tag_value(tag) for tag in PROJECT_TAGS}
PERF_TAGS_NORM = {normalize_tag_value(tag) for tag in PERF_TAGS}


def classify_tag_family(tag: object) -> str:
    """정규화된 태그를 논문·특허·보고서 같은 성과 계열로 묶는다.
    런타임은 상세 태그가 조금씩 달라도 같은 family로 보너스를 계산할 수 있어야 한다.
    """
    normalized = normalize_tag_value(tag)
    if not normalized:
        return "other"
    if normalized.startswith("NAI_PJT_") or normalized in PROJECT_TAGS_NORM:
        return "project"
    if normalized.startswith("NAI_RI_") or normalized in PERF_TAGS_NORM:
        return "perf"
    return "other"


def split_tag_filters_by_family(tag_filters: Iterable[object]) -> tuple[list[str], list[str], list[str]]:
    """요청된 tag filter 목록을 family 단위로 재구성한다.
    개별 태그와 family 집합을 함께 만들어 완전 일치와 계열 일치 보너스를 분리해 계산한다.
    """
    project_tags: list[str] = []
    perf_tags: list[str] = []
    other_tags: list[str] = []
    for tag in tag_filters or []:
        tag_text = str(tag).strip()
        if not tag_text:
            continue
        family = classify_tag_family(tag_text)
        if family == "project":
            project_tags.append(tag_text)
        elif family == "perf":
            perf_tags.append(tag_text)
        else:
            other_tags.append(tag_text)
    return project_tags, perf_tags, other_tags


def norm_tag_from_payload(pl: dict) -> str:
    """히트 payload에서 비교에 쓸 성과 태그를 꺼내 정규화한다.
    일부 콜렉션은 `NTIS표준성과항목` 중첩 구조를 쓰므로 payload shape 차이를 이곳에서 흡수한다.
    """
    return normalize_tag_value(pl.get("tag"))


def tag_match_bonus(p: Any, *, tag_filters: Optional[Iterable[str]], boost: float, mismatch_penalty: float = 0.0) -> float:
    """요청 tag filter와 hit payload의 태그 일치 정도를 가산점으로 바꾼다.
    완전 일치에는 큰 보너스를, 같은 family만 맞을 때는 작은 보너스를 줘 rerank가 성과 유형 히트를 앞으로 끌어오게 한다.
    """
    if not tag_filters:
        return 0.0
    payload = getattr(p, "payload", None) or {}
    if not isinstance(payload, dict):
        return 0.0
    payload_tag = norm_tag_from_payload(payload)
    if not payload_tag:
        return 0.0
    filt_norm = {normalize_tag_value(tag) for tag in tag_filters if str(tag).strip()}
    if not filt_norm:
        return 0.0
    if payload_tag in filt_norm:
        return float(boost)
    if mismatch_penalty:
        return -float(mismatch_penalty)
    return 0.0


def dedup_by_doc_id(points: List[Any], max_keep: Optional[int] = None) -> List[Any]:
    """같은 문서가 여러 source에서 들어와도 대표 hit 하나만 남긴다.
    원본 점수가 높은 히트를 우선하여 RRF 이후 단계에서 중복 문서가 점수를 나누어 가지지 않게 한다.
    """
    out: List[Any] = []
    seen_doc: set[str] = set()
    for point in points or []:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        doc_id = str(payload.get("doc_id") or "")
        pjt_id = str(getattr(point, "pjt_id", ""))
        pjt_no = str(getattr(point, "pjt_no", ""))
        rjt_id = str(getattr(point, "rjt_id", ""))
        if doc_id in seen_doc:
            continue
        seen_doc.add(doc_id)
        seen_doc.add(pjt_id)
        seen_doc.add(pjt_no)
        if rjt_id:
            seen_doc.add(rjt_id)
        out.append(point)
        if max_keep is not None and len(out) >= max_keep:
            break
    return out


def resolve_collection(point: Any, payload: Optional[dict] = None) -> str:
    """히트에서 실제 성과 콜렉션 이름을 고정하여 후속 로그와 집계에 쓰게 한다.
    메타데이터가 비어 있으면 default collection으로 돌아가 재순위화 결과의 출처가 사라지지 않게 한다.
    """
    if payload is not None:
        pl = payload
    elif isinstance(point, dict):
        pl = point.get("payload", None)
    else:
        pl = getattr(point, "payload", None)
    pl = pl or {}
    if not isinstance(pl, dict):
        pl = {}
    col = pl.get("_collection")
    if not col:
        col = point.get("_collection") if isinstance(point, dict) else getattr(point, "_collection", None)
    return str(col) if col else ""


def hit_key(point: Any) -> Tuple[str, str]:
    """문서 dedup과 RRF 병합에 쓸 안정적인 히트 식별자를 만든다.
    가능하면 id를 쓰고, 없으면 title·content·collection을 조합해 같은 문서를 일관되게 묶는다.
    """
    payload = getattr(point, "payload", None) or {}
    if not isinstance(payload, dict):
        payload = {}
    col = resolve_collection(point, payload)
    pid = str(payload.get("doc_id") or getattr(point, "id", "") or "")
    return col, pid


def rrf_merge(sources: List[RankSource], *, rrf_k: int = 60, keep: int = 2000) -> List[Any]:
    """여러 ranking source를 Reciprocal Rank Fusion 방식으로 합친다.
    각 source의 순위를 기여도로 바꿔 합산하며, source별 점수 세부내역은 `rrf_components`에 보존한다.
    """
    score: Dict[Tuple[str, str], float] = {}
    best_obj: Dict[Tuple[str, str], Any] = {}
    for src in sources:
        weight = float(src.weight)
        for rank, point in enumerate(src.points or []):
            key = hit_key(point)
            if key not in best_obj:
                best_obj[key] = point
            score[key] = score.get(key, 0.0) + (weight / float(rrf_k + rank + 1))
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0][0], kv[0][1]))
    out: List[Any] = []
    for key, merged_score in ranked[: max(1, int(keep))]:
        point = best_obj.get(key)
        if point is None:
            continue
        payload = getattr(point, "payload", None)
        if isinstance(payload, dict):
            payload["_rrf"] = float(merged_score)
        try:
            setattr(point, "_rrf", float(merged_score))
        except Exception:
            pass
        out.append(point)
    return out


def score_stats(values: List[float]) -> Dict[str, float]:
    """점수 배열의 최소·최대·평균·백분위수를 요약한다.
    로그와 비교 요약에서 점수 분포를 빠르게 읽을 수 있도록 하는 보조 통계다.
    """
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)
    mean = sum(vals) / max(1, n)
    var = sum((v - mean) ** 2 for v in vals) / max(1, n)
    std = var ** 0.5

    def pct(p: float) -> float:
        """백분위수 인덱스를 안전하게 계산하는 내부 헬퍼다.
        빈 배열은 상위 함수에서 걸러지므로 여기서는 경계 인덱스만 고정한다.
        """
        if n == 1:
            return vals[0]
        idx = int(round((n - 1) * p))
        return vals[max(0, min(n - 1, idx))]

    return {
        "min": vals[0],
        "max": vals[-1],
        "mean": mean,
        "std": std,
        "p50": pct(0.5),
        "p90": pct(0.9),
    }


def normalize_values(values: List[float], policy: str) -> List[float]:
    """비교 요약을 위해 source 별 점수 지표를 0~1 구간으로 선형 정규화한다.
    각 ranking source의 절대 점수 스케일이 다를 때 상대 편차만 비교하기 위한 보조 계산이다.
    """
    if not values:
        return []
    policy = (policy or "minmax").strip().lower()
    if policy == "none":
        return list(values)
    if policy == "zscore":
        mean = sum(values) / max(1, len(values))
        var = sum((v - mean) ** 2 for v in values) / max(1, len(values))
        std = var ** 0.5
        if std == 0:
            return [0.5 for _ in values]
        return [1 / (1 + pow(2.718281828, -((v - mean) / std))) for v in values]

    vmin = min(values)
    vmax = max(values)
    if vmax == vmin:
        return [0.5 for _ in values]
    return [(v - vmin) / (vmax - vmin) for v in values]


def rerank_compare_summary(
    points: List[Any],
    total_key: str,
    *,
    topn: int,
    clip_text: Any,
    payload_title: Any,
    get_meta: Any,
) -> List[Dict[str, Any]]:
    """재순위화 전후의 score/rank 차이를 로그에 남길 수 있는 구조로 정리한다.
    상위 문서의 이동, source별 기여도, 점수 분포를 함께 실어 후속 triage에 쓸 수 있게 한다.
    """
    out = []
    for point in (points or [])[: max(1, topn)]:
        payload = getattr(point, "payload", None) or {}
        if not isinstance(payload, dict):
            payload = {}
        out.append({
            "doc_id": payload.get("doc_id") or getattr(point, "id", None),
            "col": resolve_collection(point, payload),
            "tag": payload.get("tag"),
            total_key: payload.get(total_key),
            "_final_total": payload.get("_final_total"),
            "title": clip_text(payload_title(payload, get_meta(payload)), 120),
        })
    return out


def final_rerank(
    cands: List[Any],
    *,
    it: Any,
    kws: List[str],
    lex_w: Dict[str, float],
    base_route: str,
    mode: str,
    keep: int,
    tag_boost: float = 0.0,
    tag_mismatch_penalty: float = 0.0,
    title_soft_terms: Optional[List[str]] = None,
    title_soft_boost: float = 0.0,
    must_contain_terms: Any,
    keyword_score: Any,
    keyword_exact_match_hits: Any,
    filter_score: Any,
    family_bonus: Any,
    log_kv: Any,
    log_section: Any,
    clip_text: Any,
    payload_title: Any,
    get_meta: Any,
) -> List[Any]:
    """여러 source 히트를 합치고 tag bonus까지 반영한 최종 순위를 만든다.
    결과는 dedup -> tag bonus -> RRF merge -> top-k cutoff 순서로 정리되며, 필요하면 비교 요약을 함께 돌려준다.
    """
    if not cands:
        return []

    if mode in ("search", "lookup") and getattr(it, 'people_terms', None) and base_route == "people":
        terms = [t.strip() for t in (getattr(it, 'people_terms', None) or []) if t.strip()][:2]
        if terms:
            matched = [p for p in cands if must_contain_terms(p, terms)]
            if matched:
                min_keep = max(2, min(int(keep), 5))
                if len(matched) >= min_keep:
                    cands = matched

    legacy_weights = {
        "lookup": (0.55, 0.70, 1.35),
        "join": (0.45, 0.65, 1.60),
        "search": (0.85, 1.00, 0.75),
    }
    compare_legacy = str(os.getenv("RAG_RERANK_COMPARE", "0")).strip().lower() in ("1", "true", "yes", "y")
    score_norm_policy = os.getenv("RAG_SCORE_NORM", "minmax")
    score_sample = int(os.getenv("RAG_SCORE_SAMPLE", "200"))

    if mode == "lookup":
        w_rrf, w_kw, w_f, w_fam, w_tag = 0.30, 0.25, 0.35, 0.05, 0.05
        strict_ids = True
    elif mode == "join":
        w_rrf, w_kw, w_f, w_fam, w_tag = 0.25, 0.25, 0.40, 0.05, 0.05
        strict_ids = True
    else:
        w_rrf, w_kw, w_f, w_fam, w_tag = 0.45, 0.35, 0.15, 0.025, 0.025
        strict_ids = False

    raw_rrf = []
    raw_kw = []
    raw_f = []
    raw_fam = []
    raw_tag = []
    raw_exact_hits = []
    raw_items = []
    for idx, point in enumerate(cands):
        payload = getattr(point, "payload", None)
        rrf_sc = float(payload.get("_rrf", 0.0)) if isinstance(payload, dict) else 0.0
        kw_sc = keyword_score(point, kws, lex_w)
        if title_soft_terms and title_soft_boost > 0:
            payload_dict = getattr(point, "payload", None) or {}
            title_hits = 0
            if isinstance(payload_dict, Mapping):
                titles = []
                for field in ("title1", "title2", "title_text"):
                    value = str(payload_dict.get(field, "") or "").strip().lower()
                    if value:
                        titles.append(value)
                for term in title_soft_terms:
                    normalized = str(term or "").strip().lower()
                    if normalized and any(normalized in title for title in titles):
                        title_hits += 1
            if title_hits > 0:
                kw_sc += title_soft_boost * float(title_hits)
        exact_hits = keyword_exact_match_hits(point, kws)
        f_sc = filter_score(point, it, base_route, strict_ids=strict_ids, mode=mode)
        fam = family_bonus(point, base_route)
        tag_sc = tag_match_bonus(
            point,
            tag_filters=getattr(it, "tag_filters", None),
            boost=tag_boost,
            mismatch_penalty=tag_mismatch_penalty,
        )
        raw_rrf.append(rrf_sc)
        raw_kw.append(kw_sc)
        raw_f.append(f_sc)
        raw_fam.append(fam)
        raw_tag.append(tag_sc)
        raw_exact_hits.append(float(exact_hits))
        raw_items.append({
            "idx": idx,
            "p": point,
            "rrf": rrf_sc,
            "kw": kw_sc,
            "f": f_sc,
            "fam": fam,
            "tag": tag_sc,
            "exact_hits": exact_hits,
        })

    if raw_kw and max(raw_kw) <= 0 and any(raw_exact_hits):
        exact_bonus = float(os.getenv("RAG_KW_EXACT_MATCH_BONUS", "2.0"))
        for idx, item in enumerate(raw_items):
            bonus = exact_bonus * float(item["exact_hits"])
            if bonus > 0:
                raw_kw[idx] += bonus
                item["kw"] = raw_kw[idx]
        log_kv("RAG.RERANK.KEYWORD_EXACT_MATCH_BONUS", applied=True, bonus=exact_bonus, hits_total=sum(raw_exact_hits), tier="debug")
    else:
        log_kv("RAG.RERANK.KEYWORD_EXACT_MATCH_BONUS", applied=False, hits_total=sum(raw_exact_hits), tier="debug")

    sample_slice = slice(0, max(0, min(score_sample, len(raw_items))))
    log_section(
        "RAG.RERANK.SCORE_RANGE_RAW",
        {
            "_rrf": score_stats(raw_rrf[sample_slice]),
            "_keyword_score": score_stats(raw_kw[sample_slice]),
            "keyword_exact_match_hits": score_stats(raw_exact_hits[sample_slice]),
            "_filter_score": score_stats(raw_f[sample_slice]),
            "_family_bonus": score_stats(raw_fam[sample_slice]),
            "_tag_match_bonus": score_stats(raw_tag[sample_slice]),
        },
        tier="debug",
    )

    norm_rrf = normalize_values(raw_rrf, score_norm_policy)
    norm_kw = normalize_values(raw_kw, score_norm_policy)
    norm_f = normalize_values(raw_f, score_norm_policy)
    norm_fam = normalize_values(raw_fam, score_norm_policy)
    norm_tag = normalize_values(raw_tag, score_norm_policy)

    log_section(
        "RAG.RERANK.SCORE_RANGE_NORM",
        {
            "policy": score_norm_policy,
            "_rrf": score_stats(norm_rrf[sample_slice]),
            "_keyword_score": score_stats(norm_kw[sample_slice]),
            "_filter_score": score_stats(norm_f[sample_slice]),
            "_family_bonus": score_stats(norm_fam[sample_slice]),
            "_tag_match_bonus": score_stats(norm_tag[sample_slice]),
            "_family_bonus_weighted": score_stats([(w_fam * v) for v in norm_fam[sample_slice]]),
            "_tag_match_bonus_weighted": score_stats([(w_tag * v) for v in norm_tag[sample_slice]]),
        },
        tier="debug",
    )

    legacy_w_rrf, legacy_w_kw, legacy_w_f = legacy_weights.get(mode, legacy_weights["search"])
    scored = []
    legacy_scored = []
    for idx, item in enumerate(raw_items):
        point = item["p"]
        rrf_sc = item["rrf"]
        kw_sc = item["kw"]
        f_sc = item["f"]
        fam = item["fam"]
        tag_sc = item["tag"]
        total = (w_rrf * norm_rrf[idx]) + (w_kw * norm_kw[idx]) + (w_f * norm_f[idx]) + (w_fam * norm_fam[idx]) + (w_tag * norm_tag[idx])
        legacy_total = (legacy_w_rrf * rrf_sc) + (legacy_w_kw * kw_sc) + (legacy_w_f * f_sc) + fam + tag_sc

        payload = getattr(point, "payload", None)
        if isinstance(payload, dict):
            payload["_raw_rrf"] = rrf_sc
            payload["_raw_kw"] = kw_sc
            payload["_raw_f"] = f_sc
            payload["_raw_family"] = fam
            payload["_raw_tag"] = tag_sc
            payload["_final_rrf"] = norm_rrf[idx]
            payload["_final_kw"] = norm_kw[idx]
            payload["_final_f"] = norm_f[idx]
            payload["_final_family"] = norm_fam[idx]
            payload["_final_tag"] = norm_tag[idx]
            payload["_final_total"] = total
            payload["_legacy_total"] = legacy_total

        scored.append((-total, item["idx"], point))
        legacy_scored.append((-legacy_total, item["idx"], point))

    scored.sort(key=lambda item: (item[0], item[1]))
    out = [item[2] for item in scored[: max(1, int(keep))]]

    if compare_legacy:
        legacy_scored.sort(key=lambda item: (item[0], item[1]))
        compare_topn = int(os.getenv("RAG_RERANK_COMPARE_TOPN", "8"))
        log_section(
            "RAG.RERANK.COMPARE_LEGACY_TOP",
            rerank_compare_summary([item[2] for item in legacy_scored], "_legacy_total", topn=compare_topn, clip_text=clip_text, payload_title=payload_title, get_meta=get_meta),
            tier="debug",
        )
        log_section(
            "RAG.RERANK.COMPARE_NEW_TOP",
            rerank_compare_summary(out, "_final_total", topn=compare_topn, clip_text=clip_text, payload_title=payload_title, get_meta=get_meta),
            tier="debug",
        )

    return out
