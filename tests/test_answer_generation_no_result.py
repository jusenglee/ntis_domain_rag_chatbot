import asyncio
from types import SimpleNamespace

from apps.api.services.answer_generation import generate_answer


def test_generate_answer_short_circuits_no_result_message():
    events = []
    result = asyncio.run(
        generate_answer(
            SimpleNamespace(
                no_result_message="조건에 맞는 조회 결과를 찾지 못했습니다.",
                request_id="rid",
                conversation_id="cid",
            ),
            model_name="gemma_triton_0",
            final_field="answer_gemma",
            build_llm_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("LLM should not run")),
            build_answer_context_fn=lambda **kwargs: (_ for _ in ()).throw(AssertionError("context builder should not run")),
            load_system_prompt_fn=lambda path: (_ for _ in ()).throw(AssertionError("prompt should not load")),
            system_prompt_path=None,
            log_section_fn=lambda *args, **kwargs: None,
            select_max_tokens_hint_fn=lambda qa: 0,
            run_llm_streaming_fn=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("streaming should not run")),
            log_event=lambda event, **fields: events.append((event, fields)),
            logger=SimpleNamespace(info=lambda *args, **kwargs: None),
            solar_ttft_deadline_ms=0,
            solar_gen_deadline_ms=0,
            solar_stream_max_chars=0,
        )
    )

    assert result["answer_gemma"] == "조건에 맞는 조회 결과를 찾지 못했습니다."
    assert result["answer_gemma_meta"]["no_result_short_circuit"] is True
    assert events[0][0] == "LLM.GENERATE"
    assert events[0][1]["stage"] == "generate_answer_no_result"
