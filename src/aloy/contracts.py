"""Small, provider-neutral contracts for Aloy's first text agent."""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class AgentConfig:
    name: str = "Aloy"
    system_prompt: str = "You are Aloy. Speak naturally and concisely. Ask one question at a time."
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    max_output_tokens: int = 512
    timeout_seconds: float = 45.0

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.system_prompt.strip():
            raise ValueError("Agent name and system prompt are required")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("Provider and model are required")
        if not 1 <= self.max_output_tokens <= 4096 or self.timeout_seconds <= 0:
            raise ValueError("Invalid output or timeout limit")


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_usd: float | None = None


@dataclass(frozen=True)
class ProviderEvent:
    kind: Literal["delta", "usage", "session"]
    text: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class AgentEvent:
    kind: Literal["started", "delta", "completed", "failed", "cancelled"]
    conversation_id: str
    run_id: str
    text: str = ""
    error: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class Reply:
    text: str
    conversation_id: str
    run_id: str
    usage: Usage


class TextProvider(Protocol):
    async def stream(
        self,
        *,
        conversation_id: str,
        system_prompt: str,
        messages: list[Message],
        config: AgentConfig,
    ) -> AsyncIterator[ProviderEvent]: ...


class ContextOverflowError(RuntimeError):
    """The selected conversation no longer fits the model context."""
