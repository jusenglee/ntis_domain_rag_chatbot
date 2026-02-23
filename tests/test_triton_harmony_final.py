import sys
import types


# triton_client import 의존성 최소 스텁
fake_np = types.ModuleType("numpy")
fake_np.array = lambda *args, **kwargs: args[0] if args else []
fake_np.bool_ = bool
sys.modules.setdefault("numpy", fake_np)

fake_transformers = types.ModuleType("transformers")
fake_transformers.AutoTokenizer = object
sys.modules.setdefault("transformers", fake_transformers)

fake_triton_pkg = types.ModuleType("tritonclient")
fake_triton_grpc = types.ModuleType("tritonclient.grpc")
fake_triton_grpc.InferenceServerClient = object
fake_triton_grpc.InferInput = object
fake_triton_grpc.InferRequestedOutput = object
sys.modules.setdefault("tritonclient", fake_triton_pkg)
sys.modules.setdefault("tritonclient.grpc", fake_triton_grpc)

from triton_client import (  # noqa: E402
    _extract_harmony_final,
    _extract_harmony_visible_stream_text,
    _should_apply_harmony_final,
)


def test_extract_harmony_final_full_pattern_returns_final_only() -> None:
    text = (
        "<|start|>assistant<|channel|>analysis<|message|>think"
        "<|return|><|start|>assistant<|channel|>final<|message|>최종 답변"
        "<|return|>/<|end|>"
    )

    assert _extract_harmony_final(text) == "최종 답변"


def test_extract_harmony_final_fallback_pattern_returns_final_only() -> None:
    text = "prefix<|channel|>final<|message|>답변 내용<|return|>tail"

    assert _extract_harmony_final(text) == "답변 내용"


def test_extract_harmony_final_plain_text_passthrough() -> None:
    text = "일반 텍스트 응답입니다."

    assert _extract_harmony_final(text) == "일반 텍스트 응답입니다."


def test_extract_harmony_visible_stream_text_hides_until_final_channel() -> None:
    analysis_only = "<|start|>assistant<|channel|>analysis<|message|>생각중"
    with_final = analysis_only + "<|return|><|start|>assistant<|channel|>final<|message|>결론"

    assert _extract_harmony_visible_stream_text(analysis_only) == ""
    assert _extract_harmony_visible_stream_text(with_final) == "결론"


def test_model_guard_applies_only_to_target_model() -> None:
    assert _should_apply_harmony_final("gpt_oss_triton_0") is True
    assert _should_apply_harmony_final("other_model") is False
