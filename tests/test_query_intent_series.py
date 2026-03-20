from apps.core.query_intent import classify_query


def test_query_intent_marks_series_for_project_group_flow_question():
    intent = classify_query("\ub3d9\uc77c\ud55c \uacfc\uc81c\ubc88\ud638 \uacc4\uc5f4 \uc5f0\ucc28 \uacfc\uc81c\ub97c \ubcf4\uc5ec\uc918", [])

    assert intent.output_type == "series"
    assert intent.action == "list"


def test_query_intent_keeps_series_output_for_series_plus_comparison_cue():
    intent = classify_query("\uc5f0\ucc28\ubcc4 \ub17c\ubb38 \uc131\uacfc\ub97c \ube44\uad50\ud574\uc918", [])

    assert intent.output_type == "series"
