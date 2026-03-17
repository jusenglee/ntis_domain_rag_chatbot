#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
from typing import Iterable, List, Tuple

from apps.core.rag_pipeline import run_rag_once


def _load_queries(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _reciprocal_rank(doc_ids: List[str], expect: Iterable[str], k: int) -> float:
    expect_set = {str(x).strip() for x in expect if str(x).strip()}
    if not expect_set:
        return 0.0
    for idx, doc_id in enumerate(doc_ids[:k], start=1):
        if doc_id in expect_set:
            return 1.0 / float(idx)
    return 0.0


def _recall(doc_ids: List[str], expect: Iterable[str], k: int) -> float:
    expect_set = {str(x).strip() for x in expect if str(x).strip()}
    if not expect_set:
        return 0.0
    return 1.0 if any(doc_id in expect_set for doc_id in doc_ids[:k]) else 0.0


def _extract_doc_ids(result) -> List[str]:
    doc_ids: List[str] = []
    for p in getattr(result, "reranked_hits", []) or []:
        pl = getattr(p, "payload", None) or {}
        if not isinstance(pl, dict):
            continue
        doc_id = str(pl.get("doc_id") or "").strip()
        if doc_id:
            doc_ids.append(doc_id)
    return doc_ids


def run_eval(data_path: str, k: int) -> Tuple[float, float]:
    rows = _load_queries(data_path)
    if not rows:
        print("No queries found.")
        return 0.0, 0.0

    recall_sum = 0.0
    mrr_sum = 0.0
    valid = 0

    for row in rows:
        query = str(row.get("query") or "").strip()
        expect = row.get("expect_doc_ids") or []
        if not query:
            continue
        result = run_rag_once(query=query)
        doc_ids = _extract_doc_ids(result)
        recall_sum += _recall(doc_ids, expect, k)
        mrr_sum += _reciprocal_rank(doc_ids, expect, k)
        valid += 1

    if valid == 0:
        print("No valid queries.")
        return 0.0, 0.0

    recall = recall_sum / float(valid)
    mrr = mrr_sum / float(valid)
    print(f"Recall@{k}: {recall:.4f}")
    print(f"MRR@{k}: {mrr:.4f}")
    return recall, mrr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="JSONL file with query/expect_doc_ids")
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args()
    run_eval(args.data, args.k)


if __name__ == "__main__":
    main()
