"""The only text conversation execution path."""

import asyncio
from collections.abc import AsyncIterator

from aloy.budget import MONTHLY_LIMIT_USD, estimated_cost, maximum_reservation
from aloy.contracts import (
    AgentConfig,
    AgentEvent,
    ContextOverflowError,
    Message,
    ProviderEvent,
    Reply,
    TextProvider,
    Usage,
)
from aloy.storage import ConversationStore


class ChatAgent:
    def __init__(
        self, config: AgentConfig, store: ConversationStore, provider: TextProvider
    ) -> None:
        self.config = config
        self.store = store
        self.provider = provider
        self._locks: dict[str, asyncio.Lock] = {}

    async def reply(self, conversation_id: str, text: str) -> Reply:
        result: AgentEvent | None = None
        failure: str | None = None
        async for event in self.stream(conversation_id, text):
            if event.kind == "completed":
                result = event
            elif event.kind == "failed":
                failure = event.error
        if result is None:
            raise RuntimeError(failure or "Provider did not complete a reply")
        return Reply(result.text, conversation_id, result.run_id, result.usage)

    async def stream(self, conversation_id: str, text: str) -> AsyncIterator[AgentEvent]:
        if not text.strip():
            raise ValueError("Message cannot be empty")
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            raise RuntimeError("Conversation already has an active turn")
        async with lock:
            conversation = self.store.conversation(conversation_id)
            if conversation["system_prompt"] != self.config.system_prompt:
                raise ValueError("Conversation system prompt differs from this agent")
            history = self.store.completed_history(conversation_id)
            if (
                self.store.monthly_spend() + maximum_reservation(self.config, history, text)
                > MONTHLY_LIMIT_USD
            ):
                raise RuntimeError("Aloy's monthly estimated spend limit has been reached")
            run_id = self.store.begin_run(
                conversation_id, self.config.provider, self.config.model, text
            )
            pieces: list[str] = []
            usage = Usage()
            session_id: str | None = None
            try:
                yield AgentEvent("started", conversation_id, run_id)
                async with asyncio.timeout(self.config.timeout_seconds):
                    async for event in self.provider.stream(
                        conversation_id=conversation_id,
                        system_prompt=self.config.system_prompt,
                        messages=[*history, Message("user", text)],
                        config=self.config,
                    ):
                        if event.kind == "delta":
                            pieces.append(event.text)
                            yield AgentEvent("delta", conversation_id, run_id, text=event.text)
                        elif event.kind == "usage":
                            usage = event.usage
                        elif event.kind == "session":
                            session_id = event.text
                answer = "".join(pieces).strip()
                if not answer:
                    raise RuntimeError("Provider returned an empty response")
                usage = Usage(
                    usage.input_tokens,
                    usage.output_tokens,
                    estimated_cost(self.config.model, usage),
                )
                self.store.finish_run(run_id, "completed", answer, usage)
                if session_id:
                    self.store.set_session(
                        conversation_id,
                        f"{self.config.provider}:{self.config.model}",
                        session_id,
                        len(history) + 2,
                    )
                yield AgentEvent("completed", conversation_id, run_id, text=answer, usage=usage)
            except asyncio.CancelledError:
                self.store.finish_run(run_id, "cancelled", "".join(pieces), usage)
                raise
            except Exception as exc:
                message = str(exc)
                if isinstance(exc, ContextOverflowError):
                    message = "Conversation context is full. Start a new conversation."
                self.store.finish_run(run_id, "failed", "".join(pieces), usage, message)
                yield AgentEvent("failed", conversation_id, run_id, error=message)
            finally:
                if self.store.run(run_id)["status"] == "running":
                    self.store.finish_run(run_id, "cancelled", "".join(pieces), usage)


class FakeProvider:
    """Deterministic injectable provider; never consults environment or network."""

    def __init__(self, answer: str = "Hello from Aloy.", *, fail: Exception | None = None) -> None:
        self.answer = answer
        self.fail = fail
        self.calls: list[tuple[str, list[Message], AgentConfig]] = []

    async def stream(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        messages: list[Message],
        config: AgentConfig,
    ) -> AsyncIterator[ProviderEvent]:
        self.calls.append((system_prompt, messages, config))
        if self.fail:
            raise self.fail
        for word in self.answer.split(" "):
            yield ProviderEvent("delta", word + " ")
        yield ProviderEvent("usage", usage=Usage(2, 3, 0.0))
