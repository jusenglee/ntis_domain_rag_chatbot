from pathlib import Path


def test_rag_pipeline_no_lower_layer_promotion_branch():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "RAG.PROMOTION.DECISION" not in source
    assert "phase.promotion_reexecute" not in source
    assert "promotion_depth" not in source

def test_search_policy_has_no_search_hit_mode_promotion_helper():
    source = Path("apps/core/rag_search_policy.py").read_text(encoding="utf-8")
    assert "promote_mode_from_search_hits" not in source
    assert "search_hit_promotion" not in source
    assert "search_to_lookup" not in source
    assert "search_to_join" not in source