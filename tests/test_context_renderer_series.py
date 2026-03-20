from apps.api.services.context_renderer import refine_documents_rule_based


def test_refine_documents_rule_based_formats_series_rows():
    docs = [
        {
            "source_index": 1,
            "source_type": "series",
            "series_item": {
                "project_title": "AI Forecast Y1",
                "year": "2021",
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
                "lead_org_name": "KISTI",
            },
            "year_buckets": [{"year": "2021", "project_count": 1, "paper_count": 2, "patent_count": 0, "report_count": 0}],
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

    assert "AI Forecast Y1" in text
    assert "pjt_no: PJT-2020-1234-5678" in text
    assert "bucket 2021" in text


def test_refine_documents_rule_based_formats_reverse_trace_rows():
    docs = [
        {
            "source_index": 1,
            "source_type": "reverse_trace",
            "origin_project": {
                "project_title": "Digital Twin Project",
                "pjt_id": "1711015550",
                "pjt_no": "PJT-2020-1234-5678",
            },
            "origin_perf": [{"perf_title": "Digital Twin Paper"}],
            "followup_perf": [{"perf_title": "Digital Twin Patent"}],
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

    assert "Digital Twin Project" in text
    assert "origin_perf: Digital Twin Paper" in text
    assert "followup_perf: Digital Twin Patent" in text
