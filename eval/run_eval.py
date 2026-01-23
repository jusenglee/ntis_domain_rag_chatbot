#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
간단한 검색 평가 스크립트.
- Recall@k, MRR 계산
- SearchPreset 튜닝 전/후 비교를 위해 로그 포맷 고정
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from rag_store import build_rag_objects_dual
from retrieval import (
    normalize_query,
    extract_keywords,
    dense_retrieve_hybrid_multi,
    rrf_rerank_multi,
)
from rag_parts.query_intent import classify_query
from rag_parts.search_preset import build_search_preset
from rag_parts.vecsets import named_vectors_in_collection
from rag_parts.constants import COL_PROJECT, COL_PERF, COL_SUPPORT


@dataclass
class EvalItem:
    qid: str
    category: str
    query: str
    expected_doc_ids: List[str]


def _load_json(path: str) -> List[EvalItem]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    items: List[EvalItem] = []
    for i, row in enumerate(raw or []):
        qid = str(row.get("id") or f"row-{i}")
        category = str(row.get("category") or "")
        query = str(row.get("query") or "").strip()
        expected = _parse_expected(row.get("expected_doc_id"))
        items.append(EvalItem(qid=qid, category=category, query=query, expected_doc_ids=expected))
    return items


def _load_csv(path: str) -> List[EvalItem]:
    items: List[EvalItem] = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            qid = str(row.get("id") or f"row-{i}")
            category = str(row.get("category") or "")
            query = str(row.get("query") or "").strip()
            expected = _parse_expected(row.get("expected_doc_id"))
            items.append(EvalItem(qid=qid, category=category, query=query, expected_doc_ids=expected))
    return items


def _parse_expected(raw: object) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, (tuple, set)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except Exception:
            pass
    if ";" in s:
        return [part.strip() for part in s.split(";") if part.strip()]
    if "|" in s:
        return [part.strip() for part in s.split("|") if part.strip()]
    return [s]


def _load_dataset(path: str) -> List[EvalItem]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        return _load_json(path)
    if ext == ".csv":
        return _load_csv(path)
    raise ValueError(f"Unsupported eval file extension: {ext}")


def _infer_collection(base_route: str) -> str:
    if base_route == "support":
        return COL_SUPPORT
    if base_route == "perf":
        return COL_PERF
    return COL_PROJECT


def _build_emb_map(qdr, collection: str, emb_a, emb_b) -> Dict[str, object]:
    vecset = named_vectors_in_collection(qdr, collection)
    default_map = {"e5i_qa": emb_a, "e5_qa": emb_b}
    if not vecset:
        return default_map
    return {name: emb for name, emb in default_map.items() if name in vecset} or default_map


def _extract_doc_id(point: object) -> str:
    pl = getattr(point, "payload", None) or {}
    if not isinstance(pl, dict):
        pl = {}
    doc_id = pl.get("doc_id")
    if doc_id is None:
        doc_id = getattr(point, "id", "")
    return str(doc_id).strip()


def _ranked_doc_ids(points: Sequence[object]) -> List[str]:
    return [_extract_doc_id(p) for p in points if _extract_doc_id(p)]


def _evaluate_item(
    *,
    qdr,
    emb_a,
    emb_b,
    item: EvalItem,
    max_k: int,
) -> Tuple[int | None, List[str]]:
    qtext = normalize_query(item.query)
    kws = extract_keywords(qtext)
    intent = classify_query(qtext, kws)
    preset = build_search_preset(intent)
    collection = _infer_collection(intent.base_route)

    emb_map = _build_emb_map(qdr, collection, emb_a, emb_b)

    search_res = dense_retrieve_hybrid_multi(
        client=qdr,
        emb_map=emb_map,
        expanded_text=qtext,
        keywords=kws,
        collection_name=collection,
        lexical_fields=preset.lexical_fields,
        lexical_field_weights=preset.lexical_field_weights,
        top_k_dense=preset.top_k_dense,
        top_k_lexical_candidates=preset.top_k_lex_cand,
        top_k_lexical=preset.top_k_lex,
        query_filter=None,
        timings={},
    )

    ranked = rrf_rerank_multi(
        search_res,
        k=max_k,
        rrf_k=int(os.getenv("RAG_RRF_K", "60")),
        w_dense_map={"e5i_qa": 1.0, "e5_qa": 0.8},
        w_lex=preset.w_lex,
        query_text=qtext,
    )

    doc_ids = _ranked_doc_ids(ranked)
    expected = {doc_id.lower() for doc_id in (item.expected_doc_ids or [])}
    if not expected:
        return None, doc_ids

    rank_hit = None
    for idx, doc_id in enumerate(doc_ids, start=1):
        if doc_id.lower() in expected:
            rank_hit = idx
            break

    return rank_hit, doc_ids


def _recall_at_k(ranks: Iterable[int | None], k: int) -> float:
    hits = 0
    total = 0
    for r in ranks:
        if r is None:
            continue
        total += 1
        if r <= k:
            hits += 1
    return hits / max(1, total)


def _mrr(ranks: Iterable[int | None]) -> float:
    total = 0
    acc = 0.0
    for r in ranks:
        if r is None:
            continue
        total += 1
        if r > 0:
            acc += 1.0 / float(r)
    return acc / max(1, total)


def _print_summary(label: str, dataset_name: str, k: int, recall: float, mrr: float, n: int) -> None:
    print(
        "EVAL|label={label}|set={dataset}|k={k}|recall={recall:.4f}|mrr={mrr:.4f}|n={n}".format(
            label=label,
            dataset=dataset_name,
            k=k,
            recall=recall,
            mrr=mrr,
            n=n,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG retrieval evaluation")
    parser.add_argument("--data", default="eval/queries.json", help="JSON/CSV evaluation set")
    parser.add_argument("--k", default="1,3,5,10", help="comma-separated k list")
    parser.add_argument("--label", default="default", help="label for comparison (e.g., before/after)")
    parser.add_argument("--details", action="store_true", help="print per-query details")
    args = parser.parse_args()

    ks = sorted({int(x) for x in args.k.split(",") if x.strip()})
    if not ks:
        raise ValueError("k list is empty")

    items = _load_dataset(args.data)
    if not items:
        raise ValueError("evaluation dataset is empty")

    dataset_name = os.path.basename(args.data)

    qdr, emb_a, _, _, emb_b, _ = build_rag_objects_dual()

    ranks: List[int | None] = []
    t0 = time.time()

    for item in items:
        rank_hit, doc_ids = _evaluate_item(
            qdr=qdr,
            emb_a=emb_a,
            emb_b=emb_b,
            item=item,
            max_k=max(ks),
        )
        ranks.append(rank_hit)
        if args.details:
            hit = "NA" if rank_hit is None else str(rank_hit)
            top_docs = ",".join(doc_ids[: max(ks)])
            print(
                "EVAL-QUERY|label={label}|id={qid}|cat={cat}|hit_rank={hit}|top={top}".format(
                    label=args.label,
                    qid=item.qid,
                    cat=item.category,
                    hit=hit,
                    top=top_docs,
                )
            )

    elapsed = time.time() - t0

    valid_n = sum(1 for r in ranks if r is not None)
    for k in ks:
        recall = _recall_at_k(ranks, k)
        mrr = _mrr(ranks)
        _print_summary(args.label, dataset_name, k, recall, mrr, valid_n)

    print("EVAL|label={label}|set={dataset}|elapsed_sec={elapsed:.2f}".format(
        label=args.label,
        dataset=dataset_name,
        elapsed=elapsed,
    ))


if __name__ == "__main__":
    main()
