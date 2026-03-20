from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.core.rag_rerank_support import (
    RerankSupportRuntime,
    build_final_rerank_fn,
    payload_text_bundle,
    soft_title_contains,
)


def _runtime() -> RerankSupportRuntime:
    return RerankSupportRuntime(
        payload_get_fn=lambda payload, path: payload.get("nested_lookup") if path == "prtcp_mp[].hm_nm" else "",
        get_meta_fn=lambda payload: dict(payload.get("meta") or {}),
        payload_title_fn=lambda payload, meta: str(payload.get("title_text") or meta.get("kor_pjt_nm") or ""),
        clip_text_fn=lambda value, max_chars: str(value)[:max_chars],
        log_kv_fn=lambda *args, **kwargs: None,
        log_section_fn=lambda *args, **kwargs: None,
    )


def test_payload_text_bundle_prefers_meta_title_when_payload_title_is_identifier():
    point = SimpleNamespace(
        payload={
            "title_text": "12345678",
            "pjt_id": "12345678",
            "keyword_text": "alpha beta",
            "meta": {"kor_pjt_nm": "Real Project Title", "pjt_id": "12345678"},
        }
    )
    bundle = payload_text_bundle(point, runtime=_runtime())
    assert bundle["title_text"] == "Real Project Title"
    assert "alpha beta" in bundle["flat_text"]


def test_soft_title_contains_uses_normalized_text():
    payload = {"title_text": "AI-Based   System [Overview]"}
    assert soft_title_contains(payload, ["AI Based System"])


def test_build_final_rerank_fn_returns_ranked_points():
    rerank = build_final_rerank_fn(runtime=_runtime())
    top = SimpleNamespace(payload={"doc_id": "1", "_rrf": 1.0, "title_text": "Alpha Project", "keyword_text": "alpha", "meta": {"kor_pjt_nm": "Alpha Project"}, "_collection": "ntis_project"})
    low = SimpleNamespace(payload={"doc_id": "2", "_rrf": 0.2, "title_text": "Beta Project", "keyword_text": "beta", "meta": {"kor_pjt_nm": "Beta Project"}, "_collection": "ntis_project"})
    intent = SimpleNamespace(people_terms=[], title=[], years=[], org_terms=[], perf_tag_filters=[], project_tag_filters=[], tag_filters=[])
    ranked = rerank([low, top], it=intent, kws=["alpha"], lex_w={"title_text": 2.0}, base_route="project", mode="search", keep=1)
    assert ranked[0] is top


def test_rag_pipeline_uses_rerank_support_module():
    source = Path("apps/core/rag_pipeline.py").read_text(encoding="utf-8")
    assert "build_final_rerank_fn" in source
    assert "soft_title_contains_fn=soft_title_contains" in source
    assert "def _payload_text_bundle" not in source
    assert "def _keyword_score" not in source
    assert "def _filter_score" not in source
    assert "def _family_bonus" not in source
    assert "def _final_rerank" not in source
