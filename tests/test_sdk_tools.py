from datetime import date
from typing import Annotated, Literal

import pytest
from pydantic import Field

from app.sdk import ToolError, tool


@tool("""
    Example tool.
    Second line.
""")
def example(ctx, title: str, priority: Literal["P1", "P2"] = "P2",
            due: Annotated[date | None, Field(description="Due date")] = None) -> dict:
    return {}


def test_schema_is_generated_from_the_signature():
    schema = example.__tool__.schema()
    assert schema["name"] == "example"
    assert schema["description"] == "Example tool.\nSecond line."
    props = schema["input_schema"]["properties"]
    assert set(props) == {"title", "priority", "due"}  # ctx is not exposed
    assert props["priority"]["enum"] == ["P1", "P2"]
    assert schema["input_schema"]["required"] == ["title"]
    assert schema["input_schema"]["additionalProperties"] is False


def test_validation_parses_types():
    args = example.__tool__.validate({"title": "x", "due": "2027-01-15"})
    assert args == {"title": "x", "priority": "P2", "due": date(2027, 1, 15)}


@pytest.mark.parametrize("raw, message", [
    ({}, "title: Field required"),
    ({"title": "x", "priority": "P9"}, "priority"),
    ({"title": "x", "due": "friday"}, "due"),
    ({"title": "x", "unexpected": 1}, "unexpected"),
])
def test_invalid_arguments_raise_a_readable_tool_error(raw, message):
    with pytest.raises(ToolError, match=message):
        example.__tool__.validate(raw)


def test_tool_needs_ctx_and_annotations():
    with pytest.raises(TypeError, match="first parameter must be ctx"):
        tool("x")(lambda title: None)
    with pytest.raises(TypeError, match="needs a type annotation"):
        def untyped(ctx, title): ...
        tool("x")(untyped)
