import sqlite3
from dataclasses import dataclass
from typing import Any

from app.sdk.access import Guard, ScopedDB
from app.sdk.context import ModuleContext
from app.sdk.errors import ToolError
from app.sdk.modules import Module


@dataclass
class ToolCall:
    module: str
    tool: str
    arguments: dict
    ok: bool = True


class Registry:
    """Runs module tools over one shared connection, each with its module's permissions.

    Every call, including nested ctx.call()s, is recorded in `calls` for the agent log.
    """

    def __init__(self, modules: dict[str, Module], conn: sqlite3.Connection):
        self.modules = modules
        self.conn = conn
        self.guard = Guard(conn)
        self.calls: list[ToolCall] = []

    def context(self, module: str) -> ModuleContext:
        spec = self.modules[module]
        return ModuleContext(module, ScopedDB(self.guard, module, spec.reads), self)

    def call(self, module: str, tool: str, raw_arguments: dict) -> Any:
        spec = self.modules.get(module)
        if spec is None:
            raise ToolError(f"unknown or disabled module '{module}'")
        if tool not in spec.tools:
            raise ToolError(f"module '{module}' has no tool '{tool}'")
        record = ToolCall(module, tool, dict(raw_arguments))
        self.calls.append(record)
        try:
            arguments = spec.tools[tool].validate(raw_arguments)
            return spec.tools[tool].func(self.context(module), **arguments)
        except Exception:
            record.ok = False
            raise
