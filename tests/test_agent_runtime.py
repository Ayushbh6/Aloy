"""Offline end-to-end checks through the production agent, store, and tool registry."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

from aloy.agent import AgentRunner, FakeProvider
from aloy.bridge import clear_capture_request
from aloy.context import ContextAssembler
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall, Usage
from aloy.maintenance import FakeMaintenance
from aloy.memory_index import MemoryIndex
from aloy.storage import ConversationStore
from aloy.tools import ToolContext, ToolRegistry, _grounding_query_count


def test_two_step_memory_tool_and_durable_history(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(provider="fake", model="fake-v1", tools=("memory.remember",))
        cid = store.create_conversation(config.system_prompt)
        provider = FakeProvider(
            script=[
                ProviderStepResult(
                    calls=(
                        ToolCall(
                            "call-1", "memory.remember", {"text": "German B1", "category": "goal"}
                        ),
                    ),
                    usage=Usage(2, 4),
                ),
                ProviderStepResult(text="I'll remember that.", usage=Usage(3, 5)),
            ]
        )
        runner = AgentRunner(config, store, provider, maintenance=FakeMaintenance(store))
        events = [event async for event in runner.stream(AgentInput(cid, "Remember German B1"))]
        assert [
            event.kind
            for event in events
            if event.kind in {"tool_started", "tool_result", "completed"}
        ] == ["tool_started", "tool_result", "completed"]
        assert store.memories()[0]["text"] == "German B1"
        assert [row["text"] for row in store.messages(cid)] == [
            "Remember German B1",
            "I'll remember that.",
        ]
        assert [step["kind"] for step in store.steps(events[0].run_id)] == [
            "context",
            "provider",
            "tool",
            "provider",
        ]
        assert len(store.db.execute("SELECT * FROM provider_calls").fetchall()) == 2
        store.close()

    asyncio.run(scenario())


def test_disabled_tool_never_executes(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(provider="fake", model="fake-v1", tools=())
        cid = store.create_conversation(config.system_prompt)
        provider = FakeProvider(
            script=[
                ProviderStepResult(
                    calls=(
                        ToolCall("x", "memory.remember", {"text": "A fact", "category": "fact"}),
                    )
                ),
                ProviderStepResult(text="I cannot do that."),
            ]
        )
        events = [
            event
            async for event in AgentRunner(config, store, provider).stream(
                AgentInput(cid, "Remember a fact")
            )
        ]
        assert not store.memories()
        assert next(e for e in events if e.kind == "tool_result").data["result"] == {
            "error": "tool_disabled"
        }
        store.close()

    asyncio.run(scenario())


def test_tool_schema_and_capture_authorization(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("prompt")
        run_id = store.begin_run(cid, "fake", "fake-v1", "check this screen")
        registry = ToolRegistry()
        called = False

        async def capture(kind, seconds, conversation_id):
            nonlocal called
            called = True
            path = tmp_path / "tmp" / "capture.png"
            path.parent.mkdir()
            path.write_bytes(b"png fixture")
            return {"path": str(path)}

        context = ToolContext(store, None, cid, run_id, "check this screen", False, capture=capture)
        denied = await registry.execute(ToolCall("1", "screen.snapshot", {}), context)
        assert denied.value["error"] == "authorization_required"
        assert not called
        bad = await registry.execute(
            ToolCall("2", "screen.record_clip", {"seconds": 61}),
            ToolContext(store, None, cid, run_id, "record this video", True, capture=capture),
        )
        assert not bad.success
        assert not called
        accepted = await registry.execute(
            ToolCall("3", "screen.snapshot", {}),
            ToolContext(store, None, cid, run_id, "check this screen", True, capture=capture),
        )
        assert accepted.success and called
        assert Path(accepted.value["media"]["path"]).is_file()
        assert not (tmp_path / "tmp" / "capture.png").exists()
        store.close()

    asyncio.run(scenario())


def test_fts_regex_and_rebuildable_index(tmp_path):
    class FakeEmbeddings:
        fingerprint_value = "fake-v1:768"

        async def fingerprint(self):
            return self.fingerprint_value

        async def embed(self, text):
            return [0.01] * 768

    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("prompt")
        run = store.begin_run(cid, "fake", "fake-v1", "Learn German B1")
        store.finish_run(run, "completed", "Let's study today")
        memory = store.remember("German B1 in spring", "goal")
        assert store.search_text("German", mode="fts")
        assert store.search_text("German B1", mode="substring")
        assert store.search_text("German B1 in spring", mode="exact")
        assert store.search_text("Germ.n", mode="regex")
        embeddings = FakeEmbeddings()
        index = MemoryIndex(store, embeddings)
        assert await index.drain() >= 3
        assert any(row["kind"] == "memory" for row in await index.search("German"))
        store.forget(memory["id"])
        await index.drain()
        assert all(row["id"] != "memory:" + memory["id"] for row in await index.search("German"))
        embeddings.fingerprint_value = "fake-v2:768"
        try:
            await index.search("German")
        except RuntimeError as error:
            assert "rebuild" in str(error)
        else:
            raise AssertionError("Model change must invalidate index")
        assert await index.rebuild() == 2
        store.close()

    asyncio.run(scenario())


def test_proactive_compaction_preserves_raw_messages(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("prompt")
        for number in range(20):
            run = store.begin_run(cid, "fake", "fake-v1", f"user {number}: " + "x" * 320)
            store.finish_run(run, "completed", f"assistant {number}: " + "y" * 320)
        before = store.messages(cid)
        context = ContextAssembler(store, FakeMaintenance(store))
        bundle = await context.assemble(cid, "prompt", "what next?", target=4000)
        assert bundle.revision > 0
        assert store.summary(cid)["revision"] == 1
        assert len(store.messages(cid)) == len(before)
        assert [message.text for message in bundle.messages[-17:-1]] == [
            row["text"] for row in before[-16:]
        ]
        store.close()

    asyncio.run(scenario())


def test_grounding_queries_are_counted_once_per_nonempty_query():
    steps = [
        SimpleNamespace(
            type="google_search_call",
            arguments={"queries": ["Aloy", "Aloy", "  ", "German B1"]},
        ),
        SimpleNamespace(type="model_output", arguments=None),
    ]
    assert _grounding_query_count(steps) == 2


def test_unknown_and_malformed_tools_are_bounded(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("prompt")
        run_id = store.begin_run(cid, "fake", "fake-v1", "test")
        context = ToolContext(store, None, cid, run_id, "test", False)
        registry = ToolRegistry()
        unknown = await registry.execute(ToolCall("1", "filesystem.delete", {}), context)
        malformed = await registry.execute(
            ToolCall("2", "screen.record_clip", {"seconds": "forever"}), context
        )
        assert unknown.value == {"error": "unknown_tool"}
        assert malformed.value["error"] == "ValidationError"
        store.close()

    asyncio.run(scenario())


def test_agent_step_limit_fails_without_unbounded_continuation(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(
            provider="fake", model="fake-v1", max_steps=2, tools=("memory.search",)
        )
        cid = store.create_conversation(config.system_prompt)
        provider = FakeProvider(
            script=[
                ProviderStepResult(calls=(ToolCall("1", "memory.search", {"query": "German"}),)),
                ProviderStepResult(calls=(ToolCall("2", "memory.search", {"query": "B1"}),)),
            ]
        )
        events = [
            event
            async for event in AgentRunner(config, store, provider).stream(
                AgentInput(cid, "Find memories")
            )
        ]
        assert events[-1].kind == "failed"
        assert "step limit" in events[-1].error
        assert len(provider.calls) == 2
        assert store.run(events[0].run_id)["status"] == "failed"
        store.close()

    asyncio.run(scenario())


def test_capture_authorization_is_current_turn_and_voice_compatible():
    assert clear_capture_request("Check this video, please")
    assert clear_capture_request("Look at my screen")
    assert not clear_capture_request("Can you help with something?")
    assert not clear_capture_request("Do you record my screen continuously?")
