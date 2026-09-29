import pytest

from app.llm.subscription import build_prompt, describe_error, usage_from


def test_single_message_is_sent_as_is():
    assert build_prompt([{"role": "user", "content": "hello"}]) == "hello"


def test_earlier_turns_become_a_transcript():
    prompt = build_prompt([
        {"role": "user", "content": "add milk"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}},
            {"type": "text", "text": "Added."},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
        {"role": "user", "content": "and eggs"},
    ])
    assert prompt == (
        "<conversation_so_far>\nuser: add milk\n\nassistant: Added.\n</conversation_so_far>\n\nand eggs"
    )


def test_usage_is_summed_across_models():
    usage, models = usage_from({
        "claude-haiku-4-5": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 100,
                             "cacheCreationInputTokens": 20, "costUSD": 0.1},
        "claude-haiku-4-5-20251001": {"inputTokens": 1, "outputTokens": 2},
    })
    assert (usage.input_tokens, usage.output_tokens, usage.total_input_tokens) == (11, 7, 131)
    assert models == {"claude-haiku-4-5"}  # dated snapshot names are folded into the alias
    assert usage_from(None)[1] == set()


@pytest.mark.parametrize("status, expected", [
    (429, "usage limit"),
    (401, "claude setup-token"),
    (500, "HTTP 500"),
])
def test_errors_are_explained(status, expected):
    assert expected in describe_error("error_during_execution", status, None)


sdk = pytest.importorskip("claude_agent_sdk")


def test_bridge_calls_the_executor():
    import anyio

    from app.llm.subscription import _bridge

    calls = []

    def execute(name, arguments):
        calls.append((name, arguments))
        return '{"id": 1}', False

    bridged = _bridge({"name": "add_task", "description": "Add", "input_schema": {
        "type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}}, execute)
    result = anyio.run(bridged.handler, {"title": "x"})
    assert calls == [("add_task", {"title": "x"})]
    assert result == {"content": [{"type": "text", "text": '{"id": 1}'}], "is_error": False}


def test_run_locks_the_sdk_down_and_maps_the_result(monkeypatch):
    from app.llm.subscription import SubscriptionRunner

    seen = {}

    async def fake_query(*, prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        yield sdk.AssistantMessage(content=[sdk.TextBlock(text="Working on it")], model="claude-haiku-4-5")
        yield sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
            session_id="s", result="Added task 7.", stop_reason="end_turn",
            model_usage={"claude-haiku-4-5": {"inputTokens": 30, "outputTokens": 9}},
        )

    monkeypatch.setattr(sdk, "query", fake_query)
    tool = {"name": "add_task", "description": "Add a task",
            "input_schema": {"type": "object", "properties": {}}}
    run = SubscriptionRunner().run(model="haiku", system="sys", messages=[{"role": "user", "content": "hi"}],
                                   tools=[tool], execute=lambda n, a: ("", False), effort="low")

    options = seen["options"]
    assert options.tools == [] and options.setting_sources == []
    assert options.allowed_tools == ["mcp__secretary__add_task"]
    assert options.permission_mode == "dontAsk"
    assert options.model == "claude-haiku-4-5" and options.effort is None  # Haiku takes no effort
    assert (run.text, run.stop_reason, run.usage.output_tokens) == ("Added task 7.", "end_turn", 9)
    assert run.messages[-1] == {"role": "assistant", "content": [{"type": "text", "text": "Added task 7."}]}


def test_max_turns_and_errors(monkeypatch):
    from app.llm import LLMError
    from app.llm.subscription import SubscriptionRunner

    def result(**kwargs):
        async def fake_query(*, prompt, options):
            yield sdk.ResultMessage(duration_ms=1, duration_api_ms=1, num_turns=1, session_id="s", **kwargs)
        return fake_query

    run_args = dict(model="haiku", system="s", messages=[{"role": "user", "content": "x"}],
                    tools=[], execute=lambda n, a: ("", False))
    monkeypatch.setattr(sdk, "query", result(subtype="error_max_turns", is_error=True))
    assert SubscriptionRunner().run(**run_args).stop_reason == "max_turns"
    monkeypatch.setattr(sdk, "query", result(subtype="error_during_execution", is_error=True,
                                              api_error_status=429))
    with pytest.raises(LLMError, match="usage limit"):
        SubscriptionRunner().run(**run_args)
