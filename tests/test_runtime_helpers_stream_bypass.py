import pytest

from apps.api.services.runtime_helpers import derive_stream_error_code


def test_non_empty_stream_is_not_classified_as_empty_stream():
    meta = {
        "stream_content_emitted_chunks": 1,
        "content_chars": 42,
    }

    assert derive_stream_error_code(meta) is None


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        (
            {
                "ttft_deadline_exceeded": True,
                "gen_deadline_exceeded": True,
                "deadline_exceeded": True,
                "char_limited": True,
                "stream_content_emitted_chunks": 0,
            },
            "TTFT_DEADLINE_EXCEEDED",
        ),
        (
            {
                "gen_deadline_exceeded": True,
                "deadline_exceeded": True,
                "char_limited": True,
                "stream_content_emitted_chunks": 0,
            },
            "GEN_DEADLINE_EXCEEDED",
        ),
        (
            {
                "deadline_exceeded": True,
                "char_limited": True,
                "stream_content_emitted_chunks": 0,
            },
            "DEADLINE_EXCEEDED",
        ),
        (
            {
                "char_limited": True,
                "stream_content_emitted_chunks": 0,
            },
            "CHAR_LIMITED",
        ),
        (
            {
                "stream_content_emitted_chunks": 0,
                "content_chars": 0,
            },
            "EMPTY_STREAM",
        ),
    ],
)
def test_derive_stream_error_code_precedence(meta, expected):
    assert derive_stream_error_code(meta) == expected
