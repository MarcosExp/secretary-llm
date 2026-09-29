"""Providers that never call a real model: for tests and for running the app offline."""

import itertools
from typing import Callable

from app.llm.base import LLMResponse, Usage

_ids = itertools.count(1)


def text(message: str, usage: Usage | None = None) -> LLMResponse:
    return LLMResponse([{"type": "text", "text": message}], "end_turn", "fake", usage or Usage(10, 5))


def tool_call(name: str, arguments: dict, usage: Usage | None = None) -> LLMResponse:
    block = {"type": "tool_use", "id": f"toolu_fake_{next(_ids)}", "name": name, "input": arguments}
    return LLMResponse([block], "tool_use", "fake", usage or Usage(10, 5))


Step = LLMResponse | Callable[[dict], LLMResponse]


class ScriptedProvider:
    """Returns the scripted responses in order and records every request.

    A step can be a callable that receives the request, to answer based on it.
    """

    def __init__(self, steps: list[Step]):
        self.steps = list(steps)
        self.requests: list[dict] = []

    def create(self, *, model, system, messages, tools, effort=None) -> LLMResponse:
        request = {"model": model, "system": system, "messages": list(messages),
                   "tools": tools, "effort": effort}
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("ScriptedProvider ran out of responses")
        step = self.steps.pop(0)
        return step(request) if callable(step) else step


class EchoProvider:
    """LLM_PROVIDER=fake: answers without tools, so the app runs with no API key."""

    def create(self, *, model, system, messages, tools, effort=None) -> LLMResponse:
        return text("(fake provider) No model is configured. Set LLM_PROVIDER=subscription or api to use Claude.")
