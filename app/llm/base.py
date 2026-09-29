"""Provider-neutral LLM interface.

Messages and content blocks use the Anthropic Messages API shape as plain dicts,
so any provider (real or fake) produces history the agent loop can append as-is.
"""

from dataclasses import dataclass, field
from typing import Callable, Protocol

MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}


def resolve_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


class LLMError(Exception):
    """The provider could not produce a response (network, auth, rate limit, ...)."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens


@dataclass
class LLMResponse:
    content: list[dict]
    stop_reason: str
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMProvider(Protocol):
    def create(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        effort: str | None = None,
    ) -> LLMResponse: ...


# execute(tool_name, arguments) -> (result text, is_error)
Executor = Callable[[str, dict], tuple[str, bool]]


@dataclass
class RunResult:
    text: str
    messages: list[dict]
    stop_reason: str
    models: set[str] = field(default_factory=set)
    usage: Usage = field(default_factory=Usage)


class AgentRunner(Protocol):
    """Runs one agent (the orchestrator or a module) to completion, calling `execute` for tools.

    Two kinds exist: LoopRunner drives our own loop over a turn-level LLMProvider (API,
    fake), and SubscriptionRunner hands the loop to the Claude Agent SDK.
    """

    def run(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        execute: Executor,
        effort: str | None = None,
        max_turns: int = 12,
    ) -> RunResult: ...
