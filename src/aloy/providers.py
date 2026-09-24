"""Direct text transports. They only translate provider wire events."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aloy.contracts import AgentConfig, ContextOverflowError, Message, ProviderEvent, Usage
from aloy.dispatch import DispatchBudget, gemini_client
from aloy.storage import ConversationStore, data_root

MODEL_PRESETS = {
    "openrouter": "deepseek/deepseek-v4.1-flash",
    "gemini": "gemini-3.5-flash-lite",
    "gemini-quality": "gemini-3.8-flash",
    "codex": "gpt-6-sol",
    "fake": "fake-v1",
}


def load_local_env(path: Path | None = None) -> None:
    """Read local credentials without overriding existing process values."""
    path = path or (data_root() / "credentials.env")
    if not path.exists():
        path = Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() in {"GEMINI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"}:
            os.environ.setdefault(name.strip(), value.strip())


class OpenRouterProvider:
    def __init__(
        self, api_key: str | None = None, *, dispatch: DispatchBudget | None = None
    ) -> None:
        self.dispatch = dispatch or DispatchBudget()
        from openai import AsyncOpenAI

        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is missing")
        self.client = AsyncOpenAI(
            api_key=api_key, base_url="https://openrouter.ai/api/v1", max_retries=0
        )

    async def close(self):
        await self.client.close()

    async def preflight(self, model: str):
        catalog = await self.client.models.list()
        if model not in {item.id for item in catalog.data}:
            raise ValueError(f"OpenRouter model unavailable: {model}")

    async def stream(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        messages: list[Message],
        config: AgentConfig,
    ) -> AsyncIterator[ProviderEvent]:
        payload = [{"role": "system", "content": system_prompt}]
        payload.extend({"role": item.role, "content": item.text} for item in messages)
        try:
            completed = False
            self.dispatch.consume()
            stream = await self.client.chat.completions.create(
                model=config.model,
                messages=payload,
                stream=True,
                max_tokens=config.max_output_tokens,
                stream_options={"include_usage": True},
                extra_body={
                    "reasoning": {"effort": "none"},
                    "provider": {"allow_fallbacks": False},
                },
            )
            async for chunk in stream:
                if chunk.choices:
                    reason = chunk.choices[0].finish_reason
                    if reason == "stop":
                        completed = True
                    elif reason is not None:
                        raise RuntimeError(
                            f"OpenRouter ended without a complete text answer: {reason}"
                        )
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield ProviderEvent("delta", delta)
                if chunk.usage:
                    yield ProviderEvent(
                        "usage",
                        usage=Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens),
                    )
            if not completed:
                raise RuntimeError("OpenRouter stream ended without completion")
        except Exception as exc:
            if "context" in str(exc).lower() and (
                "length" in str(exc).lower() or "window" in str(exc).lower()
            ):
                raise ContextOverflowError() from exc
            status = getattr(exc, "status_code", None)
            if status == 429:
                raise RuntimeError(
                    "OpenRouter is rate limited upstream. Choose another provider or try later."
                ) from exc
            if status is not None:
                raise RuntimeError(f"OpenRouter request failed (HTTP {status})") from exc
            raise


class GeminiProvider:
    def __init__(
        self,
        store: ConversationStore,
        api_key: str | None = None,
        *,
        dispatch: DispatchBudget | None = None,
    ) -> None:
        self.dispatch = dispatch or DispatchBudget()

        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing")
        self.client = gemini_client(api_key)
        self.store = store

    async def close(self):
        await self.client.aio.aclose()
        self.client.close()

    async def preflight(self, model: str):
        await self.client.aio.models.get(model=model)

    async def stream(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        messages: list[Message],
        config: AgentConfig,
    ) -> AsyncIterator[ProviderEvent]:
        session = self.store.get_session(conversation_id, f"{config.provider}:{config.model}")
        fresh = bool(
            session
            and session["updated_at"]
            and datetime.fromisoformat(session["updated_at"])
            > datetime.now(UTC) - timedelta(hours=20)
        )
        if fresh and session["synced_sequence"] == len(messages) - 1:
            request_input = messages[-1].text
            previous_id = session["session_id"]
        else:
            request_input = [
                {
                    "type": "user_input" if item.role == "user" else "model_output",
                    "content": [{"type": "text", "text": item.text}],
                }
                for item in messages
            ]
            previous_id = None
        try:
            self.dispatch.consume()
            stream = await self.client.aio.interactions.create(
                model=config.model,
                input=request_input,
                previous_interaction_id=previous_id,
                system_instruction=system_prompt,
                generation_config={
                    "max_output_tokens": config.max_output_tokens,
                    "thinking_level": "low" if config.model == "gemini-3.8-flash" else "minimal",
                },
                stream=True,
                store=True,
            )
            completed = False
            async for event in stream:
                if event.event_type == "step.delta" and event.delta.type == "text":
                    yield ProviderEvent("delta", event.delta.text or "")
                elif event.event_type == "interaction.completed":
                    completed = True
                    interaction = event.interaction
                    if interaction.usage:
                        yield ProviderEvent(
                            "usage",
                            usage=Usage(
                                interaction.usage.total_input_tokens,
                                interaction.usage.total_output_tokens,
                            ),
                        )
                    if interaction.id:
                        yield ProviderEvent("session", interaction.id)
                elif event.event_type in ("interaction.failed", "error"):
                    raise RuntimeError("Gemini interaction failed")
            if not completed:
                raise RuntimeError("Gemini stream ended without completion")
        except Exception as exc:
            if "context" in str(exc).lower() and (
                "length" in str(exc).lower() or "window" in str(exc).lower()
            ):
                raise ContextOverflowError() from exc
            raise


def make_provider(
    provider: str, store: ConversationStore | None = None, *, dispatch: DispatchBudget | None = None
):
    if provider == "openrouter":
        return OpenRouterProvider(dispatch=dispatch)
    if provider in ("gemini", "gemini-quality"):
        if store is None:
            raise ValueError("Gemini provider requires a conversation store")
        return GeminiProvider(store, dispatch=dispatch)
    if provider == "fake":
        from aloy.agent import FakeProvider

        return FakeProvider()
    if provider == "codex":
        from aloy.codex_provider import CodexProvider

        if store is None:
            raise ValueError("Codex provider requires a conversation store")
        return CodexProvider(store)
    raise ValueError(f"Unknown provider: {provider}")
