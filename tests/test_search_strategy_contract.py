from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from rag_parts.search_strategy import SEARCH_POLICY_VERSION, build_strategy_key, build_rerank_spec


def test_search_strategy_exports_required_contract() -> None:
    assert isinstance(SEARCH_POLICY_VERSION, str) and SEARCH_POLICY_VERSION
    assert build_strategy_key("list", "lookup") == "list:lookup"

    spec = build_rerank_spec("join")
    assert isinstance(spec, dict)
    assert "final_keep" in spec
    assert "rerank_weights" in spec


def test_promotion_uses_canonical_build_strategy_key_import() -> None:
    source = Path("rag_parts/promotion.py").read_text(encoding="utf-8")
    assert "from rag_parts.search_strategy import build_strategy_key" in source
