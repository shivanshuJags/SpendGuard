"""Build the configured agent adapter (Gemini or Claude) and own its client."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from app.config import Settings

from .base import AgentRunner

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "anthropic": "claude-opus-5-5"}


def _gemini_http_options():
    from google.genai import types

    # Retrying a model call is safe: it only asks for the next step, it never runs
    # a tool or moves money. Overload (503) and rate limits (429) are usually brief.
    return types.HttpOptions(
        timeout=60_000,  # milliseconds, per attempt
        retry_options=types.HttpRetryOptions(
            attempts=4, initial_delay=1.0, max_delay=8.0,
            http_status_codes=[408, 429, 500, 502, 503, 504],
        ),
    )


@asynccontextmanager
async def agent_runner(settings: Settings, llm: Any = None) -> AsyncIterator[AgentRunner | None]:
    """Yield a runner, or None when the chosen provider has no API key.

    `llm` lets tests pass a fake client; a client we create here is closed here.
    """
    provider = settings.agent_provider
    model = settings.agent_model or DEFAULT_MODELS[provider]

    if provider == "gemini":
        from google import genai

        from .gemini_runner import GeminiAgentRunner

        owned = None
        if llm is None and settings.gemini_api_key:
            owned = genai.Client(
                api_key=settings.gemini_api_key.get_secret_value(),
                http_options=_gemini_http_options(),
            )
        client = llm or owned
        try:
            yield None if client is None else GeminiAgentRunner(
                client, model=model, max_steps=settings.agent_max_steps
            )
        finally:
            if owned is not None:
                await owned.aio.aclose()

    else:
        import anthropic

        from .claude_runner import ClaudeAgentRunner

        owned = None
        if llm is None and settings.anthropic_api_key:
            owned = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
        client = llm or owned
        try:
            yield None if client is None else ClaudeAgentRunner(
                client, model=model, effort=settings.agent_effort,
                max_steps=settings.agent_max_steps,
            )
        finally:
            if owned is not None:
                await owned.close()
