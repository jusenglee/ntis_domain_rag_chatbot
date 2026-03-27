from apps.api.services.runtime_helpers import derive_stream_error_code


def test_non_empty_stream_is_not_classified_as_empty_stream():
    meta = {
        "stream_content_emitted_chunks": 1,
        "content_chars": 42,
    }

    assert derive_stream_error_code(meta) is None
