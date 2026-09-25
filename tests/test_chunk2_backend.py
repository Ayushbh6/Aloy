"""Offline behavioral checks for safe canvas, interactions, search, and actions."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import aloy.bridge as bridge_module
from aloy.agent import AgentRunner, FakeProvider
from aloy.bridge import Bridge
from aloy.canvas import validate_canvas_input
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall
from aloy.maintenance import FakeMaintenance
from aloy.storage import ConversationStore

EXAMPLE = {
    "title": "Accusative articles",
    "blocks": [
        {
            "id": "sentence-1",
            "kind": "sentence",
            "source": "Ich sehe der Hund.",
            "target": "Ich sehe den Hund.",
            "explanation": "sehen takes the accusative.",
        },
        {
            "id": "choice-1",
            "kind": "choice",
            "prompt": "Which article fits?",
            "options": [{"id": "der", "label": "der"}, {"id": "den", "label": "den"}],
            "correct_option_id": "den",
        },
    ],
}
ACTION_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "chunk2_action_contract.json").read_text()
)


def test_canvas_schema_rejects_markup_fields_and_bad_coordinates():
    assert validate_canvas_input(EXAMPLE) == EXAMPLE
    with pytest.raises(ValueError, match="fields"):
        validate_canvas_input(
            {
                "title": "Unsafe",
                "blocks": [{"id": "x", "kind": "text", "text": "ok", "html": "<script/>"}],
            }
        )
    with pytest.raises(ValueError, match="coordinate"):
        validate_canvas_input(
            {
                "title": "Drawing",
                "blocks": [
                    {
                        "id": "drawing",
                        "kind": "drawing",
                        "strokes": [
                            {
                                "color": "#3A86FF",
                                "width": 2,
                                "points": [{"x": 1.1, "y": 0}, {"x": 0, "y": 1}],
                            }
                        ],
                    }
                ],
            }
        )


def test_canvas_tool_schema_describes_kind_payloads_and_rejects_unknown_input():
    from aloy.tools import TOOL_SPECS

    spec = next(item for item in TOOL_SPECS if item.name == "canvas.present")
    block = spec.input_schema["properties"]["blocks"]["items"]
    assert "heading/text=text" in block["properties"]["kind"]["description"]
    assert "chart=chart_kind,title,labels,series" in block["properties"]["kind"]["description"]
    assert block["additionalProperties"] is False
    with pytest.raises(ValueError):
        validate_canvas_input({**EXAMPLE, "unexpected": True})


def test_canvas_tool_persists_event_and_reopens_from_history(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(
            provider="fake", model="fake-v1", tools=("canvas.present",), max_steps=2
        )
        conversation_id = store.create_conversation(config.system_prompt)
        provider = FakeProvider(
            script=[
                ProviderStepResult(calls=(ToolCall("canvas-1", "canvas.present", EXAMPLE),)),
                ProviderStepResult(text="Notice the masculine accusative article."),
            ]
        )
        events = [
            event
            async for event in AgentRunner(
                config, store, provider, maintenance=FakeMaintenance(store)
            ).stream(AgentInput(conversation_id, "Explain this sentence", operation_id="op-canvas"))
        ]
        artifact = next(event for event in events if event.kind == "artifact")
        envelope = artifact.data
        assert envelope["schema_version"] == 1
        assert envelope["conversation_id"] == conversation_id
        assert envelope["run_id"] == events[0].run_id
        assert envelope["sequence"] == 1
        assert store.run(envelope["run_id"])["status"] == "completed"
        store.close()

        reopened = ConversationStore(tmp_path)
        assert reopened.canvas_artifacts(conversation_id) == [envelope]
        reopened.close()

    asyncio.run(scenario())


def test_legacy_tool_list_migration_adds_canvas_once_without_enabling_actions(tmp_path):
    legacy = ["web.search", "screen.snapshot"]
    store = ConversationStore(tmp_path)
    store.set_setting("tools", json.dumps(legacy))
    store.db.execute("DELETE FROM schema_migrations WHERE version=14")
    store.close()

    upgraded = ConversationStore(tmp_path)
    upgraded_tools = json.loads(upgraded.settings()["tools"])
    assert upgraded_tools == [*legacy, "canvas.present"]
    assert not any(name.startswith("desktop.") for name in upgraded_tools)
    upgraded.set_setting("tools", json.dumps(legacy))
    upgraded.close()

    reopened = ConversationStore(tmp_path)
    assert json.loads(reopened.settings()["tools"]) == legacy
    reopened.close()


def test_bridge_canvas_interaction_uses_runner_with_only_allowlisted_fields(tmp_path, monkeypatch):
    async def scenario():
        store = ConversationStore(tmp_path)
        conversation_id = store.create_conversation(bridge_module.PROMPT)
        source_run = store.begin_run(conversation_id, "fake", "fake-v1", "Explain this sentence")
        artifact = store.save_canvas_artifact(source_run, EXAMPLE)
        store.finish_run(source_run, "completed", "Choose the correct article.")
        tools = ["desktop.click", "memory.remember", "screen.snapshot"]
        store.set_setting("tools", json.dumps(tools))
        store.set_setting("agent_policy", "approval_required")

        provider = FakeProvider(
            script=[
                ProviderStepResult(
                    calls=(
                        ToolCall(
                            "click-attempt",
                            "desktop.click",
                            {"bundle_id": "com.apple.Safari", "window_id": 123, "x": 0.5, "y": 0.5},
                        ),
                    )
                ),
                ProviderStepResult(text="You selected den."),
            ]
        )
        monkeypatch.setattr(bridge_module, "make_provider", lambda *_args: provider)
        monkeypatch.setattr(bridge_module, "MaintenanceService", FakeMaintenance)
        monkeypatch.setattr(Bridge, "speak", lambda *_args: asyncio.sleep(0, result=True))
        received = []

        class RecordingRunner(AgentRunner):
            async def stream(self, request):
                received.append(request)
                async for event in super().stream(request):
                    yield event

        monkeypatch.setattr(bridge_module, "AgentRunner", RecordingRunner)
        bridge = Bridge.__new__(Bridge)
        bridge.store = store
        bridge.index = None
        bridge.registry = bridge_module.ToolRegistry()
        bridge.index_task = asyncio.current_task()
        bridge.capture_waiters = {}
        bridge.action_waiters = {}
        bridge.active_text = ""
        bridge.active = None
        bridge.epoch = 0
        bridge.capture = None
        emitted = []
        bridge.emit = lambda event, **fields: emitted.append((event, fields))

        await bridge.handle(
            {
                "v": 2,
                "id": "interaction-op",
                "operation_id": "interaction-op",
                "action": "canvas_interact",
                "conversation_id": conversation_id,
                "artifact_id": artifact["artifact_id"],
                "run_id": source_run,
                "block_id": "choice-1",
                "interaction": "choose",
                "value": "den",
            }
        )
        await bridge.active
        request = received[0]
        assert set(request.canvas_interaction) == {
            "artifact_id",
            "conversation_id",
            "run_id",
            "block_id",
            "interaction",
            "value",
        }
        assert request.input_origin == "canvas_interaction"
        assert request.capture_authorized is False
        assert store.messages(conversation_id)[-2]["origin"] == "canvas_interaction"
        assert not store.memories()
        tool_results = [
            fields["data"]
            for event, fields in emitted
            if event == "agent_event" and fields.get("agent_kind") == "tool_result"
        ]
        assert tool_results[0]["success"] is False
        assert tool_results[0]["result"]["error"] == "untrusted_interaction"
        assert not any(
            event == "agent_event" and fields.get("agent_kind") == "action_requested"
            for event, fields in emitted
        )
        store.close()

    asyncio.run(scenario())


def test_canvas_interaction_is_scoped_and_has_no_new_authority(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(
            provider="fake",
            model="fake-v1",
            tools=("canvas.present", "memory.remember", "screen.snapshot", "desktop.click"),
            policy="approval_required",
            max_steps=3,
        )
        conversation_id = store.create_conversation(config.system_prompt)
        provider = FakeProvider(
            script=[
                ProviderStepResult(calls=(ToolCall("canvas-1", "canvas.present", EXAMPLE),)),
                ProviderStepResult(text="Choose the correct article."),
                ProviderStepResult(
                    calls=(
                        ToolCall(
                            "memory-1",
                            "memory.remember",
                            {"text": "I want desktop actions", "category": "preference"},
                        ),
                        ToolCall("screen-1", "screen.snapshot", {}),
                        ToolCall(
                            "click-1",
                            "desktop.click",
                            {"bundle_id": "com.apple.Safari", "window_id": 12, "x": 0.5, "y": 0.5},
                        ),
                    )
                ),
                ProviderStepResult(text="Thanks, you selected den."),
            ]
        )
        action_calls = []

        async def broker(*args):
            action_calls.append(args)
            return {"status": "completed"}

        runner = AgentRunner(
            config,
            store,
            provider,
            maintenance=FakeMaintenance(store),
            action_broker=broker,
        )
        first = [
            event
            async for event in runner.stream(
                AgentInput(conversation_id, "Explain this sentence", operation_id="op-1")
            )
        ]
        artifact = next(event.data for event in first if event.kind == "artifact")
        interaction = {
            "artifact_id": artifact["artifact_id"],
            "run_id": artifact["run_id"],
            "block_id": "choice-1",
            "interaction": "choose",
            "value": "den",
        }
        second = [
            event
            async for event in runner.stream(
                AgentInput(
                    conversation_id,
                    "Please evaluate my selected lesson answer.",
                    operation_id="op-2",
                    canvas_interaction=interaction,
                    input_origin="canvas_interaction",
                    capture_authorized=True,
                )
            )
        ]
        results = [event.data for event in second if event.kind == "tool_result"]
        assert len(results) == 3
        assert all(not item["success"] for item in results)
        assert results[0]["result"]["error"] == "ValueError"
        assert results[1]["result"]["error"] == "authorization_required"
        assert results[2]["result"]["error"] == "untrusted_interaction"
        assert not action_calls
        assert not store.memories()
        user_message = store.messages(conversation_id)[-2]
        assert user_message["origin"] == "canvas_interaction"
        assert "den" not in user_message["text"]
        assert any(
            '"selected_label": "den"' in message.text
            for message in provider.calls[2][1]
            if message.role == "user"
        )
        assert "untrusted literal data" in provider.calls[2][0]
        compacted = await FakeMaintenance(store).compact(None, conversation_id, [user_message])
        assert (
            "canvas_interaction: untrusted selected content, not user-authored" in compacted["text"]
        )
        third = [
            event
            async for event in runner.stream(
                AgentInput(conversation_id, "What did I select?", operation_id="op-3")
            )
        ]
        assert any(event.kind == "completed" for event in third)
        assert any(
            message.origin == "canvas_interaction"
            and "untrusted user-selected data, not an authored user statement" in message.text
            for message in provider.calls[-1][1]
        )
        store.close()

    asyncio.run(scenario())


def test_interaction_must_match_completed_artifact_and_exact_choice(tmp_path):
    store = ConversationStore(tmp_path)
    conversation_id = store.create_conversation("prompt")
    run_id = store.begin_run(conversation_id, "fake", "fake-v1", "hello")
    artifact = store.save_canvas_artifact(run_id, EXAMPLE)
    store.finish_run(run_id, "completed", "Done")
    with pytest.raises(ValueError, match="option"):
        store.resolve_canvas_interaction(
            conversation_id, artifact["artifact_id"], run_id, "choice-1", "choose", "unknown"
        )
    with pytest.raises(ValueError, match="available"):
        store.resolve_canvas_interaction(
            "wrong-conversation", artifact["artifact_id"], run_id, "choice-1", "choose", "den"
        )
    store.close()


def test_action_broker_rejects_stale_cancelled_and_mismatched_responses():
    async def scenario():
        bridge = Bridge.__new__(Bridge)
        bridge.action_waiters = {}
        emitted = []
        bridge.emit = lambda event, **fields: emitted.append((event, fields))
        stream_events = []
        persisted = {}

        class Store:
            def run(self, _run_id):
                return {"conversation_id": "conversation"}

            def begin_step(self, _run_id, _kind, _name, arguments):
                step_id = str(len(persisted) + 1)
                persisted[step_id] = {"arguments": arguments, "status": "running"}
                return step_id

            def finish_step(self, step_id, status, result):
                persisted[step_id].update(status=status, result=result)

        bridge.store = Store()
        call = ToolCall(
            ACTION_FIXTURE["tool_call_id"],
            ACTION_FIXTURE["action"],
            {
                **ACTION_FIXTURE["target"],
                **ACTION_FIXTURE["parameters"],
            },
        )

        task = asyncio.create_task(
            bridge.request_action(
                call, "run-1", "op-1", lambda kind, data: stream_events.append((kind, data))
            )
        )
        await asyncio.sleep(0)
        proposal = stream_events[-1][1]
        wire_proposal = {"event": "action_requested", **proposal}
        assert set(wire_proposal) == set(ACTION_FIXTURE)
        assert wire_proposal["action"] == ACTION_FIXTURE["action"] == "desktop.click"
        assert wire_proposal["target"] == ACTION_FIXTURE["target"]
        assert wire_proposal["parameters"] == ACTION_FIXTURE["parameters"]
        pending = bridge.action_waiters[proposal["proposal_id"]]
        pending["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        await bridge.handle(
            {
                "v": 2,
                "action": "action_decision",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "op-1",
                "run_id": "run-1",
                "decision": "approve",
            }
        )
        assert (await task)["status"] == "denied"
        assert all(kind != "action_execute" for kind, _ in stream_events)

        stream_events.clear()
        task = asyncio.create_task(
            bridge.request_action(
                call, "run-2", "op-2", lambda kind, data: stream_events.append((kind, data))
            )
        )
        await asyncio.sleep(0)
        proposal = stream_events[-1][1]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(kind == "action_cancelled" for kind, _ in stream_events)
        await bridge.handle(
            {
                "v": 2,
                "action": "action_decision",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "op-2",
                "run_id": "run-2",
                "decision": "approve",
            }
        )
        assert all(kind != "action_execute" for kind, _ in stream_events)

        stream_events.clear()
        task = asyncio.create_task(
            bridge.request_action(
                call, "run-3", "op-3", lambda kind, data: stream_events.append((kind, data))
            )
        )
        await asyncio.sleep(0)
        proposal = stream_events[-1][1]
        await bridge.handle(
            {
                "v": 2,
                "action": "action_decision",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "wrong-op",
                "run_id": "run-3",
                "decision": "approve",
            }
        )
        assert not any(kind == "action_execute" for kind, _ in stream_events)
        await bridge.handle(
            {
                "v": 2,
                "action": "action_decision",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "op-3",
                "run_id": "run-3",
                "decision": "approve",
            }
        )
        await asyncio.sleep(0)
        execute = next(data for kind, data in stream_events if kind == "action_execute")
        assert execute["action"] == ACTION_FIXTURE["action"]
        assert execute["target"] == {"bundle_id": "com.apple.Safari", "window_id": 123}
        assert execute["parameters"] == {"x": 0.25, "y": 0.75}
        await bridge.handle(
            {
                "v": 2,
                "action": "action_result",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "wrong-op",
                "run_id": "run-3",
                "status": "completed",
                "result": {},
            }
        )
        assert not task.done()
        await bridge.handle(
            {
                "v": 2,
                "action": "action_result",
                "proposal_id": proposal["proposal_id"],
                "operation_id": "op-3",
                "run_id": "run-3",
                "status": "completed",
                "result": {"performed": "click"},
            }
        )
        assert await task == {
            "performed": "click",
            "status": "completed",
            "proposal_id": proposal["proposal_id"],
        }
        assert [row["status"] for row in persisted.values()] == [
            "cancelled",
            "cancelled",
            "completed",
        ]
        assert all("proposal_id" in row["arguments"] for row in persisted.values())
        assert any(event == "action_response_rejected" for event, _ in emitted)

    asyncio.run(scenario())


def test_action_broker_finishes_steps_on_invalid_results_and_publish_errors():
    async def scenario():
        bridge = Bridge.__new__(Bridge)
        bridge.action_waiters = {}
        persisted = []

        class Store:
            def run(self, _run_id):
                return {"conversation_id": "conversation"}

            def begin_step(self, _run_id, _kind, _name, arguments):
                persisted.append({"arguments": arguments, "status": "running"})
                return len(persisted) - 1

            def finish_step(self, step_id, status, result):
                persisted[step_id].update(status=status, result=result)

        bridge.store = Store()
        call = ToolCall(
            ACTION_FIXTURE["tool_call_id"],
            ACTION_FIXTURE["action"],
            {**ACTION_FIXTURE["target"], **ACTION_FIXTURE["parameters"]},
        )
        emitted = []
        task = asyncio.create_task(
            bridge.request_action(
                call, "run-invalid", "op-invalid", lambda kind, data: emitted.append((kind, data))
            )
        )
        await asyncio.sleep(0)
        pending = next(iter(bridge.action_waiters.values()))
        pending["decision"].set_result("approve")
        await asyncio.sleep(0)
        pending["result"].set_result({"status": "unexpected", "result": {}})
        outcome = await task
        assert outcome["status"] == "failed"
        assert persisted[0]["status"] == "failed"
        assert [kind for kind, _ in emitted].count("action_execute") == 1

        def fail_publish(kind, _data):
            if kind == "action_requested":
                raise RuntimeError("wire closed")

        outcome = await bridge.request_action(call, "run-publish", "op-publish", fail_publish)
        assert outcome["status"] == "failed"
        assert persisted[1]["status"] == "failed"
        assert len([kind for kind, _ in emitted if kind == "action_execute"]) == 1

    asyncio.run(scenario())


def test_memory_and_conversation_search_keep_durable_records_distinct(tmp_path):
    class Index:
        async def search(self, query, limit):
            return [
                {"id": "message:random", "kind": "message", "text": "German B1"},
                {"id": "memory:" + memory["id"], "kind": "memory", "text": memory["text"]},
            ]

    store = ConversationStore(tmp_path)
    first = store.create_conversation("prompt")
    other = store.create_conversation("prompt")
    run_id = store.begin_run(first, "fake", "fake-v1", "German B1 assessment")
    store.finish_run(run_id, "completed", "We will practice again")
    memory = store.remember(
        "German B1 assessment in March", "goal", scope="conversation", conversation_id=first
    )
    bridge = Bridge.__new__(Bridge)
    bridge.store, bridge.index = store, Index()
    items = asyncio.run(bridge.search_memories("German", "hybrid"))
    assert [item["id"] for item in items] == [memory["id"]]
    assert items[0]["scope"] == "conversation" and items[0]["conversation_id"] == first
    assert [item["id"] for item in store.search_conversations("assessment")] == [first]
    renamed = store.rename_conversation(first, "B1 practice")
    assert renamed["title"] == "B1 practice"
    assert store.conversation(other)["title"] == "New conversation"
    store.close()
