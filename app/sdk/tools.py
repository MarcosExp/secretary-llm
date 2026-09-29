"""@tool: turns a typed function into a tool the agent can call.

    @tool("Add a task")
    def add_task(ctx: ModuleContext, title: str, due: date | None = None) -> Task: ...

The input schema sent to the model is generated from the signature (use
Annotated[..., Field(description=...)] to describe a parameter), and every call
is validated against it with Pydantic before the function runs.
"""

import inspect
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from app.sdk.errors import ToolError


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    func: Callable[..., Any]
    input_model: type[BaseModel]

    def schema(self) -> dict:
        input_schema = self.input_model.model_json_schema()
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


def tool(description: str):
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
        func.__tool__ = Tool(func.__name__, inspect.cleandoc(description), func, input_model)
        return func

    return register


def tools_in(namespace: dict) -> dict[str, Tool]:
    """All @tool functions defined in a module's namespace, by name."""
    return {
        value.__tool__.name: value.__tool__
        for value in namespace.values()
        if callable(value) and isinstance(getattr(value, "__tool__", None), Tool)
    }
