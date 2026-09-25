"""Wire-level fixtures for the three provider tool adapters."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace as Object

from aloy.codex_provider import CodexProvider
from aloy.contracts import (
    AgentConfig,
    Message,
    ProviderTurn,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from aloy.dispatch import DispatchBudget
from aloy.providers import GeminiProvider, OpenRouterProvider
from aloy.storage import ConversationStore

TOOL = ToolSpec(
    "memory.search",
    "Search memory",
    {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "additionalProperties": False,
    },
)


def test_openrouter_streamed_tool_call_and_continuation():
    async def scenario():
        requests = []

        async def create(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                chunks = [
                    Object(
                        usage=None,
                        choices=[
                            Object(
                                finish_reason=None,
                                delta=Object(
                                    content=None,
                                    tool_calls=[
                                        Object(
                                            index=0,
                                            id="call-1",
                                            function=Object(
                                                name="memory_search", arguments='{"query":'
                                            ),
                                        )
                                    ],
                                ),
                            )
                        ],
                    ),
                    Object(
                        usage=Object(prompt_tokens=10, completion_tokens=4),
                        choices=[
                            Object(
                                finish_reason="tool_calls",
                                delta=Object(
                                    content=None,
                                    tool_calls=[
                                        Object(
                                            index=0,
                                            id=None,
                                            function=Object(name=None, arguments='"German"}'),
                                        )
                                    ],
                                ),
                            )
                        ],
                    ),
                ]
            else:
                chunks = [
                    Object(
                        usage=Object(prompt_tokens=12, completion_tokens=5),
                        choices=[
                            Object(
                                finish_reason="stop",
                                delta=Object(content="A fact.", tool_calls=None),
                            )
                        ],
                    )
                ]

            async def stream():
                for chunk in chunks:
                    yield chunk

            return stream()

        provider = OpenRouterProvider.__new__(OpenRouterProvider)
        provider.client = Object(chat=Object(completions=Object(create=create)))
        provider.dispatch = DispatchBudget(limit=2)
        config = AgentConfig(provider="openrouter", model="z-ai/glm-5.3-flash")
        first = await provider.step(
            ProviderTurn("c", "Prompt", [Message("user", "Search")], config, (TOOL,))
        )
        assert first.calls == (ToolCall("call-1", "memory.search", {"query": "German"}),)
        second = await provider.step(
            ProviderTurn(
                "c",
                "Prompt",
                [Message("user", "Search")],
                config,
                (TOOL,),
                (ToolResult("call-1", "memory.search", {"results": []}),),
            )
        )
        assert second.text == "A fact."
        assert any(item.get("tool_call_id") == "call-1" for item in requests[1]["messages"])
        assert provider.dispatch.count == 2

    asyncio.run(scenario())


def test_gemini_streamed_function_arguments_and_result():
    async def scenario():
        requests = []

        async def create(**kwargs):
            requests.append(kwargs)
            if len(requests) == 1:
                events = [
                    Object(event_type="interaction.created", interaction=Object(id="i1")),
                    Object(
                        event_type="step.start",
                        index=0,
                        step=Object(type="function_call", id="f1", name="memory_search"),
                    ),
                    Object(
                        event_type="step.delta",
                        index=0,
                        delta=Object(type="arguments_delta", arguments='{"query":"German"}'),
                    ),
                    Object(
                        event_type="interaction.completed",
                        interaction=Object(
                            id="i1",
                            status="requires_action",
                            usage=Object(total_input_tokens=11, total_output_tokens=6),
                        ),
                    ),
                ]
            else:
                events = [
                    Object(
                        event_type="step.delta", index=0, delta=Object(type="text", text="A fact.")
                    ),
                    Object(
                        event_type="interaction.completed",
                        interaction=Object(
                            id="i2",
                            status="completed",
                            usage=Object(total_input_tokens=14, total_output_tokens=5),
                        ),
                    ),
                ]

            async def stream():
                for event in events:
                    yield event

            return stream()

        provider = GeminiProvider.__new__(GeminiProvider)
        provider.client = Object(aio=Object(interactions=Object(create=create)))
        provider.dispatch = DispatchBudget(limit=2)
        config = AgentConfig(provider="gemini", model="gemini-3.5-flash-lite")
        first = await provider.step(
            ProviderTurn("c", "Prompt", [Message("user", "Search")], config, (TOOL,))
        )
        assert first.calls[0].name == "memory.search"
        assert first.continuation_id == "i1"
        second = await provider.step(
            ProviderTurn(
                "c",
                "Prompt",
                [Message("user", "Search")],
                config,
                (TOOL,),
                (ToolResult("f1", "memory.search", {"results": []}),),
                "i1",
            )
        )
        assert second.text == "A fact."
        assert requests[1]["input"][0]["call_id"] == "f1"
        assert requests[1]["previous_interaction_id"] == "i1"

    asyncio.run(scenario())


def test_codex_app_server_dynamic_callback(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("Prompt")
        provider = CodexProvider.__new__(CodexProvider)
        provider.store = store
        provider.home = tmp_path / "home"
        provider.workspace = tmp_path / "workspace"
        provider.home.mkdir()
        provider.workspace.mkdir()
        provider._command = lambda: [
            sys.executable,
            str(Path(__file__).parent / "fixtures" / "fake_codex_server.py"),
        ]

        async def preflight(environment):
            pass

        provider._preflight = preflight
        seen = []

        async def callback(call):
            seen.append(call)
            return ToolResult(call.id, call.name, {"results": []})

        config = AgentConfig(provider="codex", model="gpt-6-sol")
        result = await provider.step(
            ProviderTurn(cid, "Prompt", [Message("user", "Search")], config, (TOOL,)),
            tool_callback=callback,
        )
        assert result.text == "A fact."
        assert seen == [ToolCall("fake-call", "memory.search", {"query": "German"})]
        assert result.session_id == "fake-thread"
        store.close()

    asyncio.run(scenario())
