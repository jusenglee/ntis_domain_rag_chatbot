from apps.core.query_intent import classify_query


def test_query_intent_marks_comparison_for_project_paper_count_question():
    intent = classify_query("\uacfc\uc81c\ubcc4 \ub17c\ubb38 \uc218 \ube44\uad50\ud574\uc918", [])

    assert intent.action == "stats"
    assert intent.output_type == "comparison"
    assert intent.stats_metric == "paper_count"


def test_query_intent_extracts_min_metric_count_for_threshold_question():
    intent = classify_query("\ub17c\ubb38\uc774 2\uac74 \uc774\uc0c1 \ub098\uc628 \uacfc\uc81c\ub9cc \ubcf4\uc5ec\uc918", [])

    assert intent.action == "stats"
    assert intent.output_type == "comparison"
    assert intent.stats_metric == "paper_count"
    assert intent.min_metric_count == 2


def test_query_intent_marks_perf_total_count_for_superlative_question():
    intent = classify_query("\uc131\uacfc\uac00 \uac00\uc7a5 \ub9ce\uc740 \uacfc\uc81c\ub97c \uc54c\ub824\uc918", [])

    assert intent.action == "stats"
    assert intent.output_type == "comparison"
    assert intent.stats_metric == "perf_total_count"
