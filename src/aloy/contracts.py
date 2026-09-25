"""Provider-neutral contracts for Aloy's single agent execution path."""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

CONTEXT_TARGET_TOKENS = 30_000


@dataclass(frozen=True)
class AgentConfig:
    name: str = "Aloy"
    system_prompt: str = "You are Aloy. Speak naturally and concisely. Ask one question at a time."
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    # Tool arguments share the output budget with prose. The former text-only
    # 512-token ceiling truncates even small multi-block native artifacts.
    max_output_tokens: int = 2048
    timeout_seconds: float = 45.0
    tools: tuple[str, ...] = ()
    max_steps: int = 8
    context_target_tokens: int = CONTEXT_TARGET_TOKENS
    output_schema: dict[str, Any] | None = None
    policy: str = "read_only"

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.system_prompt.strip():
            raise ValueError("Agent name and system prompt are required")
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("Provider and model are required")
        if not 1 <= self.max_output_tokens <= 4096 or self.timeout_seconds <= 0:
            raise ValueError("Invalid output or timeout limit")
        if not 1 <= self.max_steps <= 32 or self.context_target_tokens < 1000:
            raise ValueError("Invalid step or context limit")
        if self.policy not in {"read_only", "no_capture", "approval_required"}:
            raise ValueError("Unsupported agent policy")
        if self.output_schema is not None:
            from jsonschema import Draft202012Validator

            Draft202012Validator.check_schema(self.output_schema)


AgentSpec = AgentConfig


@dataclass(frozen=True)
class AgentInput:
    conversation_id: str
    text: str
    media_ids: tuple[str, ...] = ()
    capture_authorized: bool = False
    operation_id: str = ""
    canvas_interaction: dict[str, Any] | None = None
    input_origin: Literal["user", "canvas_interaction"] = "user"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission: Literal["read", "memory_write", "capture", "artifact_write", "action"] = "read"
    max_output_chars: int = 8000


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    value: dict[str, Any]
    success: bool = True


ActionRequest = Callable[[ToolCall, str, str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ProviderTurn:
    conversation_id: str
    system_prompt: str
    messages: list["Message"]
    config: AgentSpec
    tools: tuple[ToolSpec, ...] = ()
    prior_results: tuple[ToolResult, ...] = ()
    continuation_id: str | None = None
    media: tuple[dict[str, Any], ...] = ()
    context_revision: int = 0
    tool_hash: str = ""
    on_event: Callable[["ProviderEvent"], None] | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ProviderStepResult:
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    usage: "Usage" = field(default_factory=lambda: Usage())
    session_id: str | None = None
    continuation_id: str | None = None


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant", "tool"]
    text: str
    tool_call_id: str = ""
    name: str = ""
    origin: str = ""


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
    kind: Literal[
        "started",
        "context",
        "memory",
        "tool_started",
        "tool_result",
        "source",
        "media",
        "artifact",
        "artifact_presented",
        "action_requested",
        "action_execute",
        "action_cancelled",
        "delta",
        "completed",
        "failed",
        "cancelled",
    ]
    conversation_id: str
    run_id: str
    text: str = ""
    error: str = ""
    usage: Usage = field(default_factory=Usage)
    data: dict[str, Any] = field(default_factory=dict)


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
