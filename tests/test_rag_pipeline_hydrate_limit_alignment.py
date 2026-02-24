from pathlib import Path


def _hydrate_block() -> str:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")
    anchor = "# ✅ 최종 컨텍스트에 들어갈 애들만 payload를 두껍게 채움"
    start = source.index(anchor)
    return source[start:start + 1200]


def test_requested_limit_reflects_hinted_limit() -> None:
    block = _hydrate_block()

    assert "_coerce_int(_get_attr(intent_payload, \"limit\", 0), 0)" in block
    assert "_coerce_int(hinted_limit, 0)" in block
    assert "hydrate_upper = min(ctx_hard_limit, max(max_items, requested_limit, 1))" in block


def test_hydrate_check_topk_uses_requested_limit_baseline() -> None:
    block = _hydrate_block()

    assert "check_top_k = min(len(reranked), max(1, requested_limit))" in block
