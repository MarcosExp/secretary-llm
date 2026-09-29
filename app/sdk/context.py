from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from app import clock
from app.sdk.access import ScopedDB

if TYPE_CHECKING:
    from app.sdk.registry import Registry


@dataclass
class ModuleContext:
    """Passed as `ctx` to every tool. Scoped to the module that owns the tool."""

    module: str
    db: ScopedDB
    _registry: "Registry"

    def call(self, qualified_name: str, **arguments: Any) -> Any:
        """Run another module's tool, e.g. ctx.call("core.add_task", title=...).

        This is the only way to change data owned by another module. It runs in the
        same transaction as the calling tool.
        """
        module, _, tool = qualified_name.partition(".")
        return self._registry.call(module, tool, arguments)

    def today(self) -> date:
        return clock.today()
