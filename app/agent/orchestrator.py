"""The secretary: talks to the user and delegates each request to module subagents.

The orchestrator only sees one `delegate_to_<module>` tool per module, so its context
does not grow with every tool a module adds. Each delegation runs the module's own
subagent loop with the module's prompt, model and tools.
"""

import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from app import clock
from app.llm.base import AgentRunner, RunResult, Usage
from app.sdk.errors import AccessDenied, NotFound, ToolError
from app.sdk.modules import Module
from app.sdk.registry import Registry, ToolCall

PROMPTS = Path(__file__).parent / "prompts"
ORCHESTRATOR_PROMPT = (PROMPTS / "orchestrator.md").read_text(encoding="utf-8").strip()
MODULE_BASE_PROMPT = (PROMPTS / "module_base.md").read_text(encoding="utf-8").strip()

# Errors a tool reports back to the model so it can correct itself. Anything else is a bug.
RECOVERABLE = (NotFound, ValueError, ToolError, AccessDenied, sqlite3.IntegrityError)


@dataclass
class Reply:
    text: str
    history: list[dict]
    tool_calls: list[ToolCall]
    models: set[str] = field(default_factory=set)
    usage: Usage = field(default_factory=Usage)


class Agent:
    def __init__(self, runner: AgentRunner, modules: dict[str, Module],
                 conn: sqlite3.Connection, model: str):
        self.runner = runner
        self.modules = modules
        self.conn = conn
        self.model = model

    def ask(self, text: str, history: list[dict] | None = None) -> Reply:
        registry = Registry(self.modules, self.conn)
        reply = Reply(text="", history=[], tool_calls=registry.calls)

        def delegate(tool_name: str, arguments: dict) -> tuple[str, bool]:
            module = self.modules.get(tool_name.removeprefix("delegate_to_"))
            task = arguments.get("task")
            if module is None or not isinstance(task, str) or not task.strip():
                return "Error: unknown module or empty task.", True
            sub = self._run_module(module, task, registry)
            reply.usage += sub.usage
            reply.models |= sub.models
            return sub.text or "(the module returned no answer)", False

        messages = list(history or [])
        messages.append({"role": "user", "content": f"{_date_context()}\n\n{text}"})
        main = self.runner.run(
            model=self.model,
            system=ORCHESTRATOR_PROMPT,
            messages=messages,
            tools=[self._delegate_schema(m) for m in self.modules.values()],
            execute=delegate,
        )
        reply.text = main.text
        reply.history = main.messages
        reply.usage += main.usage
        reply.models |= main.models
        self._log(text, reply)
        return reply

    def _run_module(self, module: Module, task: str, registry: Registry) -> RunResult:
        def execute(tool_name: str, arguments: dict) -> tuple[str, bool]:
            # One transaction per tool call, including any ctx.call() into other modules.
            try:
                with self.conn:
                    result = registry.call(module.name, tool_name, arguments)
            except RECOVERABLE as exc:
                return f"Error: {exc}", True
            return _to_json(result), False

        return self.runner.run(
            model=module.model,
            system=f"{MODULE_BASE_PROMPT}\n\n{module.prompt}",
            messages=[{"role": "user", "content": f"{_date_context()}\n\nTask: {task}"}],
            tools=[tool.schema() for tool in module.tools.values()],
            execute=execute,
            effort=module.effort,
        )

    @staticmethod
    def _delegate_schema(module: Module) -> dict:
        return {
            "name": f"delegate_to_{module.name}",
            "description": module.description,
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "A complete, self-contained instruction for the module: include every "
                            "detail from the user's request, with dates resolved to YYYY-MM-DD."
                        ),
                    }
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        }

    def _log(self, text: str, reply: Reply) -> None:
        calls = [dataclasses.asdict(call) for call in reply.tool_calls]
        with self.conn:
            self.conn.execute(
                """INSERT INTO core_agent_log
                   (input, tools_called, output, model, input_tokens, output_tokens)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    text,
                    _to_json(calls),
                    reply.text,
                    ", ".join(sorted(reply.models)),
                    reply.usage.total_input_tokens,
                    reply.usage.output_tokens,
                ),
            )


CALENDAR_DAYS = 14


def _date_context() -> str:
    """Today plus the next two weeks, computed here so the model never counts days itself."""
    now = clock.now()
    days = [now.date() + timedelta(days=i) for i in range(1, CALENDAR_DAYS + 1)]
    upcoming = ", ".join(f"{d:%a} {d:%Y-%m-%d}" for d in days)
    return (
        f"<context>Today is {now:%A} {now:%Y-%m-%d}, {now:%H:%M} ({clock.timezone().key}). "
        f"Next days: {upcoming}. \"Friday\" means the next Friday in this list, "
        "unless the user says otherwise.</context>"
    )


def _to_json(value) -> str:
    def default(obj):
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        raise TypeError(f"cannot serialize {type(obj).__name__}")

    return json.dumps(value, default=default, ensure_ascii=False)
