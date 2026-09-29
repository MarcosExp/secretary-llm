"""Claude through the user's Claude subscription, via the Claude Agent SDK.

The SDK runs Claude Code's agent loop in a subprocess. It is locked down to behave
like a plain model with our tools only:
- built-in tools (files, shell, web) are removed (`tools=[]`),
- no user or project settings, memory or skills are loaded (`setting_sources=[]`),
- only our tools are allowed, and anything else is denied without prompting.

Our tools are served by an in-process MCP server whose handlers call the same
executor as the API path, so module permissions and transactions are unchanged.
Each handler runs the executor in a worker thread: a delegate call starts a nested
SDK run for the module, which needs its own event loop.

Authentication: CLAUDE_CODE_OAUTH_TOKEN, created once with `claude setup-token`
(valid for one year). Usage counts against the subscription's limits.
"""

import tempfile

from app.agent.loop import MAX_TURNS, REFUSAL_TEXT, max_turns_text
from app.llm.base import Executor, LLMError, RunResult, Usage, display_model, resolve_model

SERVER = "secretary"
NO_EFFORT_MODELS = {"claude-haiku-4-5"}


class SubscriptionRunner:
    def run(self, *, model, system, messages, tools, execute, effort=None,
            max_turns=MAX_TURNS) -> RunResult:
        import anyio

        return anyio.run(self._run, model, system, messages, tools, execute, effort, max_turns)

    async def _run(self, model, system, messages, tools, execute, effort, max_turns) -> RunResult:
        from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKError,
                                      ResultMessage, TextBlock, create_sdk_mcp_server, query)

        model_id = resolve_model(model)
        texts: list[str] = []
        result = None
        with tempfile.TemporaryDirectory(prefix="secretary-agent-") as workdir:
            options = ClaudeAgentOptions(
                system_prompt=system,
                model=model_id,
                tools=[],
                mcp_servers=(
                    {SERVER: create_sdk_mcp_server(SERVER, tools=[_bridge(t, execute) for t in tools])}
                    if tools else {}
                ),
                allowed_tools=[f"mcp__{SERVER}__{t['name']}" for t in tools],
                permission_mode="dontAsk",
                setting_sources=[],
                max_turns=max_turns,
                cwd=workdir,
                effort=effort if effort and model_id not in NO_EFFORT_MODELS else None,
                # Claude Code prefers ANTHROPIC_API_KEY over CLAUDE_CODE_OAUTH_TOKEN; blank it
                # so this runner always bills the subscription, never API credits.
                env={"ANTHROPIC_API_KEY": ""},
            )
            try:
                async for message in query(prompt=build_prompt(messages), options=options):
                    if isinstance(message, AssistantMessage):
                        text = "".join(b.text for b in message.content if isinstance(b, TextBlock))
                        if text.strip():
                            texts.append(text.strip())
                    elif isinstance(message, ResultMessage):
                        result = message
            except ClaudeSDKError as exc:
                raise LLMError(f"Claude Agent SDK failed: {exc}") from exc

        if result is None:
            raise LLMError("Claude Agent SDK returned no result")
        usage, models = usage_from(result.model_usage)
        run = RunResult(text="", messages=list(messages), stop_reason="", models=models, usage=usage)

        if result.subtype == "error_max_turns":
            run.stop_reason, run.text = "max_turns", max_turns_text(max_turns)
        elif result.is_error:
            raise LLMError(describe_error(result.subtype, result.api_error_status, result.errors))
        else:
            run.stop_reason = result.stop_reason or "end_turn"
            run.text = (result.result or (texts[-1] if texts else "")).strip()
            if run.stop_reason == "refusal":
                run.text = run.text or REFUSAL_TEXT
        run.messages.append({"role": "assistant", "content": [{"type": "text", "text": run.text}]})
        return run


def _bridge(schema: dict, execute: Executor):
    from claude_agent_sdk import tool

    name = schema["name"]

    async def handler(arguments: dict) -> dict:
        import anyio

        output, is_error = await anyio.to_thread.run_sync(execute, name, arguments)
        return {"content": [{"type": "text", "text": output}], "is_error": is_error}

    return tool(name, schema["description"], schema["input_schema"])(handler)


def build_prompt(messages: list[dict]) -> str:
    """The SDK takes one prompt string: earlier turns become a transcript before the new message."""
    *earlier, last = messages
    transcript = [
        f"{m['role']}: {text}" for m in earlier if (text := _text(m["content"]))
    ]
    current = _text(last["content"])
    if not transcript:
        return current
    return "<conversation_so_far>\n" + "\n\n".join(transcript) + "\n</conversation_so_far>\n\n" + current


def _text(content) -> str:
    if isinstance(content, str):
        return content.strip()
    return "\n".join(
        block["text"] for block in content if block.get("type") == "text" and block.get("text")
    ).strip()


def usage_from(model_usage: dict | None) -> tuple[Usage, set[str]]:
    usage = Usage()
    models = set()
    for model, stats in (model_usage or {}).items():
        models.add(display_model(model))
        usage += Usage(
            input_tokens=stats.get("inputTokens", 0),
            output_tokens=stats.get("outputTokens", 0),
            cache_read_input_tokens=stats.get("cacheReadInputTokens", 0),
            cache_creation_input_tokens=stats.get("cacheCreationInputTokens", 0),
        )
    return usage, models


def describe_error(subtype: str, status: int | None, errors: list[str] | None) -> str:
    if status == 429:
        return "Claude subscription usage limit reached; try again later"
    if status in (401, 403):
        return ("Claude subscription authentication failed; "
                "create a token with `claude setup-token` and set CLAUDE_CODE_OAUTH_TOKEN")
    detail = "; ".join(errors or []) or (f"HTTP {status}" if status else "")
    return f"Claude Agent SDK error ({subtype}){': ' + detail if detail else ''}"
