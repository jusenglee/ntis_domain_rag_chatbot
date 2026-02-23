from pathlib import Path


def test_rag_pipeline_contains_promotion_reexecute_flow() -> None:
    source = Path("rag_pipeline.py").read_text(encoding="utf-8")

    assert "RAG_PROMOTION_MODE" in source
    assert "RAG.PROMOTION.DECISION" in source
    assert "RAG.PROMOTION.REEXECUTE" in source
    assert "promoted_mode in (\"lookup\", \"join\")" in source
    assert "promotion_depth=promotion_depth + 1" in source
