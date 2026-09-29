"""Claude through the Anthropic API (API key, or an `ant auth login` profile)."""

import anthropic

from app.llm.base import LLMError, LLMResponse, Usage, resolve_model

MAX_TOKENS = 16000
# Models whose safety classifiers may decline a request: let the API retry it on a
# fallback model it picks by refusal category instead of stopping.
SERVER_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Haiku 4.5 does not accept the effort parameter.
NO_EFFORT_MODELS = {"claude-haiku-4-5"}


class ApiProvider:
    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()

    def create(self, *, model, system, messages, tools, effort=None) -> LLMResponse:
        model = resolve_model(model)
        params = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system,
            "messages": messages,
            # Caches tools + system + history; the per-turn date lives in the user message,
            # so the prefix stays stable between calls.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = tools
        if effort and model not in NO_EFFORT_MODELS:
            params["output_config"] = {"effort": effort}

        try:
            if model in SERVER_FALLBACK_MODELS:
                response = self.client.beta.messages.create(
                    **params, betas=[FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = self.client.messages.create(**params)
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic API authentication failed; check ANTHROPIC_API_KEY") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic API rate limit reached; try again later") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("could not reach the Anthropic API") from exc

        usage = response.usage
        return LLMResponse(
            content=[block.to_dict() for block in response.content],
            stop_reason=response.stop_reason,
            model=response.model,
            usage=Usage(
                input_tokens=usage.input_tokens or 0,
                output_tokens=usage.output_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )
