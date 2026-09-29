"""The secretary: talks to the user and delegates each request to module subagents.

The orchestrator only sees one `delegate_to_<module>` tool per module, so its context
does not grow with every tool a module adds. Each delegation runs the module's own
subagent loop with the module's prompt, model and tools.

Changes that need the user's approval come back as pending actions. The interface
shows them and calls confirm() or reject(); confirm() runs the stored call directly,
without the model.
"""

import dataclasses
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel

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
PENDING_EXPIRY = "-24 hours"  # SQLite datetime modifier: older proposals can no longer be confirmed
MEMORY_PAGES = ("index.md", "profile.md", "rules.md")  # read on every request
MEMORY_PAGE_CHARS = 6000


@dataclass
class PendingAction:
    id: int
    summary: str
    module: str
    tool: str
    created_at: str


@dataclass
class Reply:
    text: str
    history: list[dict]
    tool_calls: list[ToolCall]
    models: set[str] = field(default_factory=set)
    usage: Usage = field(default_factory=Usage)
    pending: list[PendingAction] = field(default_factory=list)


class Agent:
    def __init__(self, runner: AgentRunner, modules: dict[str, Module],
                 conn: sqlite3.Connection, model: str, services: dict | None = None):
        self.runner = runner
        self.modules = modules
        self.conn = conn
        self.model = model
        self.services = services or {}

    def ask(self, text: str, history: list[dict] | None = None) -> Reply:
        registry = Registry(self.modules, self.conn, self.services)
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
            system=self._system_prompt(),
            messages=messages,
            tools=[self._delegate_schema(m) for m in self.modules.values()],
            execute=delegate,
        )
        reply.text = main.text
        reply.history = main.messages
        reply.usage += main.usage
        reply.models |= main.models
        proposed = {call.pending_action for call in registry.calls if call.pending_action}
        reply.pending = [a for a in self.pending_actions() if a.id in proposed]
        self._log(text, reply)
        return reply

    # --- Confirmation (called by the interface, never by the model) ---

    def pending_actions(self) -> list[PendingAction]:
        with self.conn:
            self.conn.execute(
                "UPDATE core_pending_actions SET status = 'expired', resolved_at = CURRENT_TIMESTAMP "
                "WHERE status = 'pending' AND created_at < datetime('now', ?)",
                (PENDING_EXPIRY,),
            )
        rows = self.conn.execute(
            "SELECT id, summary, module, tool, created_at FROM core_pending_actions "
            "WHERE status = 'pending' ORDER BY id"
        )
        return [PendingAction(**dict(row)) for row in rows]

    def confirm(self, action_id: int) -> str:
        """Run a pending action exactly as it was proposed. Returns a message for the user."""
        action = self._pending(action_id)
        row = self.conn.execute(
            "SELECT arguments FROM core_pending_actions WHERE id = ?", (action_id,)
        ).fetchone()
        registry = Registry(self.modules, self.conn, self.services)
        try:
            with self.conn:
                result = registry.call(action.module, action.tool, json.loads(row["arguments"]),
                                       confirmed=True)
        except RECOVERABLE as exc:
            self._resolve(action_id, "failed", str(exc))
            message = f"Could not complete #{action_id}: {exc}"
        else:
            self._resolve(action_id, "done", _to_json(result))
            message = f"Done #{action_id}: {action.summary}"
        self._log(f"[confirm #{action_id}]", Reply(message, [], registry.calls))
        return message

    def reject(self, action_id: int) -> str:
        action = self._pending(action_id)
        self._resolve(action_id, "rejected", None)
        self._log(f"[reject #{action_id}]", Reply("rejected", [], []))
        return f"Discarded #{action_id}: {action.summary}"

    def _pending(self, action_id: int) -> PendingAction:
        for action in self.pending_actions():
            if action.id == action_id:
                return action
        raise NotFound(f"no pending action #{action_id} (it may be done, rejected or expired)")

    def _resolve(self, action_id: int, status: str, result: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE core_pending_actions SET status = ?, result = ?, resolved_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (status, result, action_id),
            )

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
            system=self._module_system_prompt(module),
            messages=[{"role": "user", "content": f"{_date_context()}\n\nTask: {task}"}],
            tools=[tool.schema() for tool in module.tools.values()],
            execute=execute,
            effort=module.effort,
        )

    def _system_prompt(self) -> str:
        """The orchestrator prompt plus the user's always-on memory pages from the wiki.

        These pages change rarely, so the whole system prompt stays cacheable.
        """
        memory = self._memory(MEMORY_PAGES)
        if not memory:
            return ORCHESTRATOR_PROMPT
        return (
            f"{ORCHESTRATOR_PROMPT}\n\n"
            "The user's own notes from their wiki follow. Before answering anything about "
            "plans, times or priorities, check it against every rule in rules.md and point out "
            "any rule it breaks, even if the calendar is free.\n"
            f"<user_memory>\n{memory}\n</user_memory>"
        )

    def _module_system_prompt(self, module: Module) -> str:
        """Module subagents get the user's rules too: they are the ones that act."""
        base = f"{MODULE_BASE_PROMPT}\n\n{module.prompt}"
        rules = self._memory(("rules.md",))
        if not rules:
            return base
        return (
            f"{base}\n\nThe user's rules (from their wiki). If the task conflicts with one, "
            f"say which in your report instead of ignoring it.\n<user_memory>\n{rules}\n</user_memory>"
        )

    def _memory(self, pages: tuple[str, ...]) -> str:
        wiki = self.services.get("wiki")
        if wiki is None:
            return ""
        sections = []
        for page in pages:
            # Template guidance lives in HTML comments; the model doesn't need it.
            text = re.sub(r"<!--.*?-->", "", wiki.read_optional(page) or "", flags=re.S).strip()
            if _has_content(text):
                if len(text) > MEMORY_PAGE_CHARS:
                    text = text[:MEMORY_PAGE_CHARS] + "\n[… truncated; read the full page through the wiki module]"
                sections.append(f'<page path="{page}">\n{text}\n</page>')
        return "\n".join(sections)

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


def _has_content(page: str) -> bool:
    """False for an untouched template: only headings and empty list items."""
    return any(
        line.strip() and not line.lstrip().startswith("#") and line.strip() not in ("-", "*", "1.")
        for line in page.splitlines()
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
        if isinstance(obj, BaseModel):
            return obj.model_dump(mode="json")
        if isinstance(obj, date):
            return obj.isoformat()
        raise TypeError(f"cannot serialize {type(obj).__name__}")

    return json.dumps(value, default=default, ensure_ascii=False)
