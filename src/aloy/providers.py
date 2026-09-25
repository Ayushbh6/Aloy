"""Direct text transports. They only translate provider wire events."""

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aloy.contracts import (
    AgentConfig,
    ContextOverflowError,
    Message,
    ProviderEvent,
    ProviderStepResult,
    ProviderTurn,
    ToolCall,
    Usage,
)
from aloy.dispatch import DispatchBudget, gemini_client
from aloy.storage import ConversationStore, data_root

MODEL_PRESETS = {
    "openrouter": "z-ai/glm-5.3-flash",
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
        self, api_key: str | None = None, *, dispatch: DispatchBudget | None = None, client=None
    ) -> None:
        self.dispatch = dispatch or DispatchBudget()
        if client is not None:
            self.client = client
            return
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

    async def step(self, turn: ProviderTurn) -> ProviderStepResult:
        if not hasattr(self, "_tool_history") or not turn.prior_results:
            self._tool_history = [{"role": "system", "content": turn.system_prompt}]
            self._tool_history.extend(
                {"role": item.role, "content": item.text}
                for item in turn.messages
                if item.role != "tool"
            )
        else:
            for result in turn.prior_results:
                self._tool_history.append(
                    {
                        "role": "tool",
                        "tool_call_id": result.call_id,
                        "content": json.dumps(result.value, ensure_ascii=False),
                    }
                )
        wire_tools = [
            {
                "type": "function",
                "function": {
                    "name": spec.name.replace(".", "_"),
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
            }
            for spec in turn.tools
        ]
        kwargs = {
            "model": turn.config.model,
            "messages": self._tool_history,
            "stream": True,
            "max_tokens": turn.config.max_output_tokens,
            "stream_options": {"include_usage": True},
            "extra_body": {"reasoning": {"effort": "low"}, "provider": {"allow_fallbacks": False}},
        }
        if wire_tools:
            kwargs["tools"] = wire_tools
            kwargs["tool_choice"] = "auto"
        if turn.config.output_schema:
            kwargs["extra_body"]["provider"]["require_parameters"] = True
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "aloy_output",
                    "strict": True,
                    "schema": turn.config.output_schema,
                },
            }
        self.dispatch.consume()
        stream = await self.client.chat.completions.create(**kwargs)
        text = ""
        usage = Usage()
        calls: dict[int, dict] = {}
        reason = None
        async for chunk in stream:
            if chunk.usage:
                usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
            for choice in chunk.choices:
                if choice.finish_reason:
                    reason = choice.finish_reason
                delta = choice.delta
                text += delta.content or ""
                if delta.content and turn.on_event:
                    turn.on_event(ProviderEvent("delta", delta.content))
                for fragment in getattr(delta, "tool_calls", None) or []:
                    entry = calls.setdefault(
                        fragment.index, {"id": "", "name": "", "arguments": ""}
                    )
                    if fragment.id:
                        entry["id"] = fragment.id
                    function = fragment.function
                    if function:
                        entry["name"] += function.name or ""
                        entry["arguments"] += function.arguments or ""
        if reason not in {"stop", "tool_calls"}:
            raise RuntimeError(f"OpenRouter turn ended without final or tool call: {reason}")
        parsed = []
        for entry in calls.values():
            if not entry["id"] or not entry["name"]:
                raise ValueError("Incomplete OpenRouter tool call")
            arguments = json.loads(entry["arguments"])
            if not isinstance(arguments, dict):
                raise ValueError("OpenRouter tool arguments must be an object")
            parsed.append(ToolCall(entry["id"], entry["name"].replace("_", ".", 1), arguments))
        if parsed:
            self._tool_history.append(
                {
                    "role": "assistant",
                    "content": text or None,
                    "tool_calls": [
                        {
                            "id": item.id,
                            "type": "function",
                            "function": {
                                "name": item.name.replace(".", "_"),
                                "arguments": json.dumps(item.arguments),
                            },
                        }
                        for item in parsed
                    ],
                }
            )
        else:
            self._tool_history.append({"role": "assistant", "content": text})
        return ProviderStepResult(text=text, calls=tuple(parsed), usage=usage)

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
                    "reasoning": {"effort": "low"},
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

    async def step(self, turn: ProviderTurn) -> ProviderStepResult:
        if turn.prior_results:
            if not turn.continuation_id:
                raise RuntimeError("Gemini tool result has no interaction to continue")
            request_input = [
                {
                    "type": "function_result",
                    "name": result.name.replace(".", "_"),
                    "call_id": result.call_id,
                    "result": [
                        {"type": "text", "text": json.dumps(result.value, ensure_ascii=False)}
                    ],
                }
                for result in turn.prior_results
            ]
            previous_id = turn.continuation_id
        elif turn.continuation_id:
            request_input = turn.messages[-1].text
            previous_id = turn.continuation_id
        else:
            request_input = [
                {
                    "type": "user_input" if item.role == "user" else "model_output",
                    "content": [{"type": "text", "text": item.text}],
                }
                for item in turn.messages
                if item.role != "tool"
            ]
            previous_id = None
        wire_tools = [
            {
                "type": "function",
                "name": spec.name.replace(".", "_"),
                "description": spec.description,
                "parameters": spec.input_schema,
            }
            for spec in turn.tools
        ]
        self.dispatch.consume()
        stream = await self.client.aio.interactions.create(
            model=turn.config.model,
            input=request_input,
            previous_interaction_id=previous_id,
            system_instruction=turn.system_prompt,
            tools=wire_tools or None,
            generation_config={
                "max_output_tokens": turn.config.max_output_tokens,
                "thinking_level": "low" if turn.config.model == "gemini-3.8-flash" else "minimal",
            },
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": turn.config.output_schema,
            }
            if turn.config.output_schema
            else None,
            stream=True,
            store=True,
        )
        text = ""
        usage = Usage()
        call_fragments: dict[int, dict] = {}
        interaction_id = None
        completed = False
        for_type = None
        async for event in stream:
            if event.event_type == "interaction.created":
                interaction_id = event.interaction.id
            elif event.event_type == "step.start" and event.step.type == "function_call":
                call_fragments[event.index] = {
                    "id": event.step.id,
                    "name": event.step.name,
                    "arguments": "",
                }
            elif event.event_type == "step.delta":
                if event.delta.type == "text":
                    text += event.delta.text or ""
                    if event.delta.text and turn.on_event:
                        turn.on_event(ProviderEvent("delta", event.delta.text))
                elif event.delta.type == "arguments_delta" and event.index in call_fragments:
                    call_fragments[event.index]["arguments"] += event.delta.arguments or ""
            elif event.event_type == "interaction.completed":
                completed = True
                interaction = event.interaction
                interaction_id = interaction.id or interaction_id
                for_type = getattr(interaction, "status", "completed")
                if interaction.usage:
                    usage = Usage(
                        interaction.usage.total_input_tokens, interaction.usage.total_output_tokens
                    )
            elif event.event_type in {"interaction.failed", "error"}:
                raise RuntimeError("Gemini interaction failed")
        if not completed or for_type not in {"completed", "requires_action"}:
            # Expose bounded protocol diagnostics, never provider content or error payloads.
            status = (
                for_type
                if for_type in {"incomplete", "cancelled", "failed", "in_progress"}
                else "unknown"
            )
            raise RuntimeError(
                "Gemini stream ended without completion "
                f"(status={status}, output_tokens={usage.output_tokens})"
            )
        parsed = []
        for entry in call_fragments.values():
            arguments = json.loads(entry["arguments"] or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Gemini tool arguments must be an object")
            parsed.append(ToolCall(entry["id"], entry["name"].replace("_", ".", 1), arguments))
        if for_type == "requires_action" and not parsed:
            raise RuntimeError("Gemini requires an unrecognized action")
        return ProviderStepResult(
            text=text,
            calls=tuple(parsed),
            usage=usage,
            continuation_id=interaction_id,
            session_id=interaction_id,
        )

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
