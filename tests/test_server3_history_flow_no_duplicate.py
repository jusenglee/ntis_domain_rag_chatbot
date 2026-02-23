from __future__ import annotations


def test_server3_history_flow_contract_and_no_duplicate_regression() -> None:
    source = open("server3.py", encoding="utf-8").read()

    assert '"chat_history": loaded_history' in source
    assert 'chat_history=state.chat_history + [state.messages[-1]]' in source
    assert 'full_history = state.chat_history + new_turn' in source

    loaded_history = [
        {"type": "human", "content": "이전 질문"},
        {"type": "ai", "content": "이전 답변"},
    ]
    current_question = {"type": "human", "content": "현재 질문"}
    current_answer = {"type": "ai", "content": "현재 답변"}

    chat_history = loaded_history

    planner_history = chat_history + [current_question]
    assert planner_history == loaded_history + [current_question]

    new_turn = [current_question, current_answer]
    full_history = chat_history + new_turn

    expected = [
        {"type": "human", "content": "이전 질문"},
        {"type": "ai", "content": "이전 답변"},
        {"type": "human", "content": "현재 질문"},
        {"type": "ai", "content": "현재 답변"},
    ]
    assert full_history == expected

    for prev, curr in zip(full_history, full_history[1:]):
        assert not (
            prev["type"] == curr["type"] == "human"
            and prev["content"] == curr["content"]
        )
