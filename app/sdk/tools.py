"""@tool: turns a typed function into a tool the agent can call.

    @tool("Add a task")
    def add_task(ctx: ModuleContext, title: str, due: date | None = None) -> Task: ...

The input schema sent to the model is generated from the signature (use
Annotated[..., Field(description=...)] to describe a parameter), and every call
is validated against it with Pydantic before the function runs.

Tools with side effects the user should approve first take `confirm`: True, or a
function of the validated arguments. Such a call is stored as a pending action and
only runs once the user confirms it (see Registry). `summarize(ctx, **arguments)`
writes the text the user is asked to confirm.
"""

import copy
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from app.sdk.errors import ToolError

ConfirmPolicy = bool | Callable[[dict], bool]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    input_model: type[BaseModel]
    confirm: ConfirmPolicy = False
    summarize: Callable[..., str] | None = None

    def schema(self) -> dict:
        input_schema = _inline_refs(self.input_model.model_json_schema())
        input_schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": input_schema}

    def validate(self, raw: dict) -> dict:
        try:
            parsed = self.input_model.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}"
                for err in exc.errors()
            )
            raise ToolError(f"invalid arguments for {self.name}: {problems}") from None
        return {name: getattr(parsed, name) for name in self.input_model.model_fields}

    def needs_confirmation(self, arguments: dict) -> bool:
        return self.confirm(arguments) if callable(self.confirm) else bool(self.confirm)


def tool(description: str, *, confirm: ConfirmPolicy = False,
         summarize: Callable[..., str] | None = None):
    def register(func):
        params = list(inspect.signature(func).parameters.values())
        if not params or params[0].name != "ctx":
            raise TypeError(f"{func.__name__}: the first parameter must be ctx")
        fields = {}
        for param in params[1:]:
            if param.annotation is inspect.Parameter.empty:
                raise TypeError(f"{func.__name__}: parameter '{param.name}' needs a type annotation")
            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[param.name] = (param.annotation, default)
        input_model = create_model(
            f"{func.__name__}_input", __config__=ConfigDict(extra="forbid"), **fields
        )
        func.__tool__ = Tool(func.__name__, inspect.cleandoc(description), func, input_model,
                             confirm, summarize)
        return func

    return register


def tools_in(namespace: dict) -> dict[str, Tool]:
    """All @tool functions defined in a module's namespace, by name."""
    return {
        value.__tool__.name: value.__tool__
        for value in namespace.values()
        if callable(value) and isinstance(getattr(value, "__tool__", None), Tool)
    }


def _inline_refs(schema: dict) -> dict:
    """Replace Pydantic's $defs/$ref with the definitions themselves.

    Nested models (a list of events, say) are then plain JSON Schema that every
    tool-use client accepts.
    """
    definitions = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target = copy.deepcopy(definitions[node["$ref"].rsplit("/", 1)[-1]])
                target.pop("title", None)
                extra = {k: v for k, v in node.items() if k != "$ref"}
                return resolve({**target, **extra})
            return {key: resolve(value) for key, value in node.items()}
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
