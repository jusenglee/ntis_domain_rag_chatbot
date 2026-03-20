from apps.api.services.context_renderer import refine_documents_rule_based


def _logger():
    return type("L", (), {"warning": staticmethod(lambda *args, **kwargs: None)})()


def test_refine_documents_rule_based_formats_pattern_analysis_rows():
    docs = [
        {
            "source_index": 1,
            "source_type": "pattern_analysis",
            "pattern_kind": "coauthor_org_repeat",
            "pattern_item": {
                "org_name": "KISTI",
                "repeated_author_count": 2,
                "author_names": ["Kim", "Lee"],
                "supporting_perf_titles": ["Paper A", "Paper B"],
            },
        }
    ]

    text = refine_documents_rule_based(
        docs,
        is_detail=False,
        researchers=None,
        organizations=None,
        org_filters=None,
        ids_map=None,
        max_matches=5,
        relax_limits=False,
        max_doc_sentences=None,
        max_doc_tokens=None,
        priority_context_fields=(),
        max_field_sentences=3,
        max_field_tokens=120,
        default_max_doc_sentences=5,
        default_max_doc_tokens=200,
        logger=_logger(),
    )

    assert "KISTI" in text
    assert "repeated_author_count: 2" in text
    assert "Paper A" in text
