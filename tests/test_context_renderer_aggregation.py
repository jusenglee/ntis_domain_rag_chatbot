from apps.api.services.context_renderer import refine_documents_rule_based


def test_refine_documents_rule_based_formats_project_comparison_aggregation():
    docs = [
        {
            "source_index": 1,
            "source_type": "aggregation",
            "metric": "paper_count",
            "group_by": "project",
            "threshold": 2,
            "candidate_docs": 7,
            "rank_item": {
                "project_title": "AI Forecast",
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "metric_value": 3,
                "supporting_perf_count": 3,
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
        logger=type("L", (), {"warning": staticmethod(lambda *args, **kwargs: None)})(),
    )

    assert "AI Forecast" in text
    assert "metric: paper_count" in text
    assert "metric_value: 3" in text
    assert "pjt_id: 1711015550" in text
