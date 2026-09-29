import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from app.sdk.access import Guard, ScopedDB
from app.sdk.context import ModuleContext
from app.sdk.errors import ToolError
from app.sdk.modules import Module

AWAITING = "awaiting_user_confirmation"


@dataclass
class ToolCall:
    module: str
    tool: str
    arguments: dict
    ok: bool = True
    pending_action: int | None = None  # set when the call was stored for confirmation


class Registry:
    """Runs module tools over one shared connection, each with its module's permissions.

    Every call, including nested ctx.call()s, is recorded in `calls` for the agent log.
    A call to a tool that needs confirmation is not run: it is stored in
    core_pending_actions and the model gets back an "awaiting confirmation" result.
    """

    def __init__(self, modules: dict[str, Module], conn: sqlite3.Connection,
                 services: dict[str, Any] | None = None):
        self.modules = modules
        self.conn = conn
        self.services = services or {}
        self.guard = Guard(conn)
        self.calls: list[ToolCall] = []

    def context(self, module: str) -> ModuleContext:
        spec = self.modules[module]
        return ModuleContext(module, ScopedDB(self.guard, module, spec.reads), self)

    def call(self, module: str, tool: str, raw_arguments: dict, *, confirmed: bool = False) -> Any:
        spec = self.modules.get(module)
        if spec is None:
            raise ToolError(f"unknown or disabled module '{module}'")
        if tool not in spec.tools:
            raise ToolError(f"module '{module}' has no tool '{tool}'")
        record = ToolCall(module, tool, dict(raw_arguments))
        self.calls.append(record)
        try:
            definition = spec.tools[tool]
            arguments = definition.validate(raw_arguments)
            ctx = self.context(module)
            if not confirmed and definition.needs_confirmation(arguments):
                summary = (definition.summarize(ctx, **arguments) if definition.summarize
                           else f"{module}.{tool}({json.dumps(raw_arguments, default=str)})")
                record.pending_action = self._store_pending(module, tool, raw_arguments, summary)
                return {
                    "status": AWAITING,
                    "action_id": record.pending_action,
                    "summary": summary,
                    "note": "Nothing has been done yet. Tell the user exactly what will happen; "
                            "it runs only if the user confirms it in the app.",
                }
            return definition.func(ctx, **arguments)
        except Exception:
            record.ok = False
            raise

    def _store_pending(self, module: str, tool: str, arguments: dict, summary: str) -> int:
        # Written as system code (no module acting), inside the caller's transaction.
        return self.conn.execute(
            "INSERT INTO core_pending_actions (module, tool, arguments, summary) VALUES (?, ?, ?, ?)",
            (module, tool, json.dumps(arguments, default=str), summary),
        ).lastrowid
