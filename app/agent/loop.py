"""Our own tool-use loop, used with turn-level providers (Anthropic API, fakes)."""

from app.llm.base import Executor, LLMProvider, RunResult

MAX_TURNS = 12


class LoopRunner:
    """AgentRunner that drives run_loop over a turn-level LLMProvider."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def run(self, *, model, system, messages, tools, execute, effort=None,
            max_turns=MAX_TURNS) -> RunResult:
        return run_loop(self.provider, model=model, system=system, messages=messages,
                        tools=tools, execute=execute, effort=effort, max_turns=max_turns)


def run_loop(
    provider: LLMProvider,
    *,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
    execute: Executor,
    effort: str | None = None,
    max_turns: int = MAX_TURNS,
) -> RunResult:
    """Call the model, run the tools it asks for, repeat until it answers."""
    messages = list(messages)
    result = RunResult(text="", messages=messages, stop_reason="")
    for _ in range(max_turns):
        response = provider.create(
            model=model, system=system, messages=messages, tools=tools, effort=effort
        )
        result.usage += response.usage
        result.models.add(response.model)
        result.stop_reason = response.stop_reason
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block["type"] != "tool_use":
                    continue
                output, is_error = execute(block["name"], block["input"])
                tool_result = {"type": "tool_result", "tool_use_id": block["id"], "content": output}
                if is_error:
                    tool_result["is_error"] = True
                tool_results.append(tool_result)
            # All results go back in one message so the model keeps making parallel calls.
            messages.append({"role": "user", "content": tool_results})
            continue

        result.text = "\n".join(b["text"] for b in response.content if b["type"] == "text").strip()
        if response.stop_reason == "refusal":
            result.text = result.text or REFUSAL_TEXT
        elif response.stop_reason == "max_tokens":
            result.text += "\n\n[The answer was cut off.]"
        return result

    result.stop_reason = "max_turns"
    result.text = max_turns_text(max_turns)
    return result


REFUSAL_TEXT = "I can't help with that request."


def max_turns_text(max_turns: int) -> str:
    return (
        f"I stopped after {max_turns} steps without finishing. "
        "Some changes may already have been made; check before retrying."
    )
