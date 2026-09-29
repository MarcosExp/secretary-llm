import os

from app.llm.base import AgentRunner, LLMError, LLMProvider, LLMResponse, RunResult, Usage, resolve_model

PROVIDERS = ("fake", "subscription", "api")


def get_runner(name: str | None = None) -> AgentRunner:
    """Runner selected by LLM_PROVIDER.

    fake (default): no model, runs offline. subscription: Claude Agent SDK with the
    user's Claude plan. api: Anthropic API with an API key.
    """
    name = name or os.getenv("LLM_PROVIDER", "fake")
    if name == "fake":
        from app.agent.loop import LoopRunner
        from app.llm.fake import EchoProvider

        return LoopRunner(EchoProvider())
    if name == "subscription":
        from app.llm.subscription import SubscriptionRunner

        return SubscriptionRunner()
    if name == "api":
        from app.agent.loop import LoopRunner
        from app.llm.anthropic_api import ApiProvider

        return LoopRunner(ApiProvider())
    raise LLMError(f"unknown LLM_PROVIDER '{name}' (use one of: {', '.join(PROVIDERS)})")


__all__ = ["AgentRunner", "LLMError", "LLMProvider", "LLMResponse", "RunResult", "Usage",
           "get_runner", "resolve_model"]
