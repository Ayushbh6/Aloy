"""Production harness checks on disposable host files and synthetic conversations."""

import asyncio
import json
import shlex
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aloy.agent import AgentRunner, FakeProvider
from aloy.context import ContextAssembler
from aloy.contracts import AgentConfig, AgentInput, ProviderStepResult, ToolCall
from aloy.harness_state import compact, task_for
from aloy.harness_tools import HARNESS_NAMES
from aloy.maintenance import FakeMaintenance
from aloy.storage import ConversationStore
from aloy.tools import ToolContext, ToolRegistry


def setup(root):
    store = ConversationStore(root / "state")
    cid = store.create_conversation("Synthetic harness")
    rid = store.begin_run(cid, "fake", "fake-v1", "Ich sehe der Hund.")
    return (
        store,
        ToolRegistry(),
        ToolContext(
            store, None, cid, rid, "Synthetic request", False, media_types=("image", "video")
        ),
    )


def test_host_files_patch_search_and_stale_edit(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        outside = tmp_path / "outside-the-data-root" / "lesson.txt"

        async def call(name, args):
            return await registry.execute(ToolCall(name, name, args), c)

        assert (
            await call("edit", {"path": str(outside), "content": "der Hund\ndie Katze\n"})
        ).success
        read = await call("read", {"path": str(outside), "offset": 1, "limit": 1})
        assert read.value["next_offset"] == 2
        assert read.value["lines"][0]["text"] == "der Hund"
        before = read.value["sha256"]
        assert (
            await call("edit", {"path": str(outside), "old": "der Hund", "new": "den Hund"})
        ).success
        stale = await call(
            "edit", {"path": str(outside), "content": "lost work", "expected_sha256": before}
        )
        assert not stale.success and "changed" in stale.value["message"]
        patch = (
            f"*** Begin Patch\n*** Update File: {outside}\n@@\n"
            f"-den Hund\n+der Hund\n die Katze\n*** Add File: {tmp_path}/new.txt\n"
            "+new file\n*** End Patch\n"
        )
        assert (await call("apply_patch", {"patch": patch})).success
        assert outside.read_text() == "der Hund\ndie Katze\n"
        assert (tmp_path / "new.txt").read_text() == "new file\n"
        assert store.db.execute("SELECT count(*) FROM file_changes").fetchone()[0] == 4
        found = await call(
            "grep",
            {"path": str(tmp_path), "pattern": "die Katze", "literal": True, "glob": "*.txt"},
        )
        assert found.success and found.value["matches"][0]["line"] == 2
        paths = await call("glob", {"path": str(tmp_path), "pattern": "*.txt", "limit": 1})
        assert paths.success and paths.value["next_cursor"]
        page = await call(
            "glob",
            {
                "path": str(tmp_path),
                "pattern": "*.txt",
                "limit": 1,
                "cursor": paths.value["next_cursor"],
            },
        )
        assert page.value["matches"] != paths.value["matches"]
        (tmp_path / ".ignore").write_text("ignored.txt\n")
        (tmp_path / "ignored.txt").write_text("find this too")
        ignored = await call("glob", {"path": str(tmp_path), "pattern": "ignored.txt"})
        assert ignored.success and ignored.value["matches"]
        invalid = await call("grep", {"path": str(tmp_path), "pattern": "["})
        assert not invalid.success
        forbidden = await registry.execute(
            ToolCall("x", "terminal", {"command": "echo forbidden"}),
            replace(c, input_origin="canvas_interaction"),
        )
        assert not forbidden.success
        store.set_setting("host_access", "disabled")
        assert not (await call("read", {"path": str(outside)})).success
        await registry.harness.close()
        store.close()

    asyncio.run(run())


def test_terminal_interactive_output_control_and_recovery(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)

        async def call(name, args):
            result = await registry.execute(ToolCall(name, name, args), c)
            assert result.success, result.value
            return result.value

        started = await call(
            "terminal",
            {
                "command": f"{shlex.quote(sys.executable)} -u -c 'print(input().upper())'",
                "shell": "/bin/bash",
                "tty": True,
                "yield_ms": 20,
                "cwd": str(tmp_path),
            },
        )
        assert started["status"] == "running"
        sid = started["session_id"]
        output = await call(
            "terminal_control",
            {"action": "write", "session_id": sid, "text": "hallo\n", "wait_ms": 2000},
        )
        assert "HALLO" in output["output"] and output["exit_code"] == 0
        sleepy = await call(
            "terminal", {"command": "sleep 30", "shell": "/bin/bash", "yield_ms": 0}
        )
        stopped = await call(
            "terminal_control", {"action": "stop", "session_id": sleepy["session_id"]}
        )
        assert stopped["status"] == "exited"
        replay = await call("terminal_control", {"action": "read", "session_id": sid, "offset": 0})
        assert "HALLO" in replay["output"]
        await registry.harness.close()
        store.close()
        reopened = ConversationStore(tmp_path / "state")
        reopened.recover()
        assert reopened.db.execute("SELECT count(*) FROM terminal_sessions").fetchone()[0] == 2
        reopened.close()

    asyncio.run(run())


def test_native_read_delivers_actual_media_and_reports_unsupported(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        image = tmp_path / "page.png"
        # Minimal fixture for transport bytes; full rendered images are verified in PDF integration.
        image.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
        result = await registry.execute(ToolCall("i", "read", {"path": str(image)}), c)
        assert result.success and Path(result.media[0]["path"]).read_bytes() == image.read_bytes()
        denied = await registry.execute(
            ToolCall("i2", "read", {"path": str(image)}), replace(c, media_types=())
        )
        assert not denied.success and not denied.media
        await registry.harness.close()
        store.close()

    asyncio.run(run())


def test_compaction_retrieval_resume_and_learning_evidence(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        store.finish_run(c.run_id, "completed", "Use den Hund in this sentence.")
        task = task_for(store, c.conversation_id, c.run_id, "Practise accusative articles")

        async def call(name, args):
            result = await registry.execute(ToolCall(name, name, args), c)
            assert result.success, result.value
            return result.value

        await call("capability_control", {"name": "task.update", "action": "activate"})
        await call(
            "capability_control",
            {
                "name": "task.update",
                "action": "call",
                "arguments": {
                    "checkpoint": {
                        "constraints": ["Ask one question at a time"],
                        "outstanding_requests": ["Explain adjective endings"],
                        "next_steps": ["Review accusative articles"],
                    }
                },
            },
        )
        source = store.message_id(c.run_id, "user")
        await call("capability_control", {"name": "learning.record", "action": "activate"})
        await call(
            "capability_control",
            {
                "name": "learning.record",
                "action": "call",
                "arguments": {
                    "skill": "accusative articles",
                    "evidence_type": "observed_answer",
                    "answer": "Ich sehe der Hund.",
                    "feedback": "Requires den Hund.",
                    "source_message_id": source,
                },
            },
        )
        bad = await registry.execute(
            ToolCall(
                "bad",
                "capability_control",
                {
                    "name": "learning.record",
                    "action": "call",
                    "arguments": {
                        "skill": "German",
                        "evidence_type": "observed_answer",
                        "answer": "I am fluent.",
                        "feedback": "Mastered",
                        "source_message_id": source,
                    },
                },
            ),
            c,
        )
        assert not bad.success
        first = await compact(
            store,
            FakeMaintenance(store),
            None,
            c.conversation_id,
            store.completed_messages(c.conversation_id),
        )
        second = await compact(
            store,
            None,
            None,
            c.conversation_id,
            store.completed_messages(c.conversation_id),
            first["text"],
        )
        assert second and len(store.messages(c.conversation_id)) == 2
        search = await call(
            "context_retrieve", {"action": "search", "query": "der Hund", "match": "exact"}
        )
        detail = await call(
            "context_retrieve", {"action": "inspect", "ref": search["results"][0]["ref"]}
        )
        assert "Ich sehe der Hund." in detail["text"]
        await registry.harness.close()
        store.close()
        reopened = ConversationStore(tmp_path / "state")
        reopened.recover()
        resumed = await ContextAssembler(reopened).assemble(
            c.conversation_id, "Synthetic harness", "Continue"
        )
        assert "Explain adjective endings" in resumed.system_prompt
        assert "Ask one question at a time" in resumed.system_prompt
        assert (
            reopened.db.execute(
                "SELECT status FROM task_states WHERE id=?", (task["id"],)
            ).fetchone()[0]
            == "interrupted"
        )
        assert (
            reopened.db.execute("SELECT evidence_type FROM learning_evidence").fetchone()[0]
            == "observed_answer"
        )
        reopened.close()

    asyncio.run(run())


def test_runner_uses_real_tools_and_keeps_audit(tmp_path):
    async def run():
        store = ConversationStore(tmp_path / "state")
        config = AgentConfig(provider="fake", model="fake-v1", tools=HARNESS_NAMES)
        cid = store.create_conversation(config.system_prompt)
        target = tmp_path / "lesson.txt"
        provider = FakeProvider(
            script=[
                ProviderStepResult(
                    calls=(
                        ToolCall(
                            "w", "edit", {"path": str(target), "content": "Ich lerne Deutsch."}
                        ),
                    )
                ),
                ProviderStepResult(calls=(ToolCall("r", "read", {"path": str(target)}),)),
                ProviderStepResult(text="Your lesson is ready."),
            ]
        )
        runner = AgentRunner(config, store, provider)
        events = [e async for e in runner.stream(AgentInput(cid, "Create a German lesson."))]
        assert any(e.kind == "completed" for e in events)
        assert target.read_text() == "Ich lerne Deutsch."
        assert store.db.execute("SELECT count(*) FROM tool_evidence").fetchone()[0] == 2
        assert store.db.execute("SELECT count(*) FROM task_states").fetchone()[0] == 1
        await runner.registry.harness.close()
        store.close()

    asyncio.run(run())


def test_capability_discovery_mcp_pagination_and_goal_link(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        (store.root / "harness.json").write_text(
            json.dumps(
                {
                    "mcp": [
                        {
                            "name": "synthetic-mcp",
                            "description": "Synthetic echo",
                            "command": sys.executable,
                            "args": [str(Path(__file__).parent / "fixtures/fake_mcp.py")],
                        }
                    ]
                }
            )
        )

        async def call(name, args):
            result = await registry.execute(ToolCall(name, name, args), c)
            assert result.success, result.value
            return result.value

        catalog = await call("capability_search", {"query": "echo"})
        assert catalog["capabilities"][0]["name"] == "synthetic-mcp"
        active = await call("capability_control", {"action": "activate", "name": "synthetic-mcp"})
        assert json.loads(active["instructions"])["total_tools"] == 2
        loaded = await call(
            "capability_control", {"action": "activate", "name": "synthetic-mcp", "tool": "echo"}
        )
        assert json.loads(loaded["instructions"])["tool"]["inputSchema"]["required"] == ["text"]
        echoed = await call(
            "capability_control",
            {
                "action": "call",
                "name": "synthetic-mcp",
                "tool": "echo",
                "arguments": {"text": "Hallo"},
            },
        )
        assert echoed["content"][0]["text"] == "Hallo"
        await call("capability_control", {"action": "activate", "name": "goal.update"})
        goal = await call(
            "capability_control",
            {
                "action": "call",
                "name": "goal.update",
                "arguments": {"title": "German", "objective": "Learn German with evidence"},
            },
        )
        await call("capability_control", {"action": "activate", "name": "task.update"})
        await call(
            "capability_control",
            {
                "action": "call",
                "name": "task.update",
                "arguments": {"goal_id": goal["id"], "objective": "Practise articles"},
            },
        )
        browse = await call("context_retrieve", {"action": "browse"})
        assert (
            browse["tasks"][0]["goal_id"] == goal["id"] and browse["goals"][0]["title"] == "German"
        )
        waiting = asyncio.create_task(
            call(
                "capability_control",
                {
                    "action": "call",
                    "name": "synthetic-mcp",
                    "tool": "echo",
                    "arguments": {"text": "WAIT_FOREVER"},
                },
            )
        )
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert (
            registry.harness.capabilities.servers["synthetic-mcp"]["process"].returncode is not None
        )
        await registry.harness.close()
        store.close()

    asyncio.run(run())


def test_cancelled_runner_stops_owned_host_process(tmp_path):
    async def run():
        store = ConversationStore(tmp_path / "state")
        config = AgentConfig(provider="fake", model="fake-v1", tools=HARNESS_NAMES)
        cid = store.create_conversation(config.system_prompt)
        started = asyncio.Event()

        class Waiting(FakeProvider):
            async def step(self, turn):
                if not turn.prior_results:
                    return ProviderStepResult(
                        calls=(
                            ToolCall(
                                "sleep",
                                "terminal",
                                {"command": "sleep 30", "yield_ms": 0, "shell": "/bin/bash"},
                            ),
                        )
                    )
                started.set()
                await asyncio.Event().wait()

        runner = AgentRunner(config, store, Waiting())

        async def consume():
            return [
                e async for e in runner.stream(AgentInput(cid, "Synthetic terminal cancellation"))
            ]

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.db.execute("SELECT status FROM terminal_sessions").fetchone()[0] == "exited"
        await runner.registry.harness.close()
        store.close()

    asyncio.run(run())


def test_deadline_stops_child_after_shell_exits(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        started = await registry.execute(
            ToolCall(
                "child",
                "terminal",
                {
                    "command": "sleep 30 & exit 0",
                    "shell": "/bin/bash",
                    "yield_ms": 0,
                    "timeout_seconds": 1,
                },
            ),
            c,
        )
        sid = started.value["session_id"]
        await asyncio.wait_for(registry.harness.terminal.sessions[sid]["pump"], 5)
        assert store.db.execute("SELECT status FROM terminal_sessions").fetchone()[0] == "exited"
        await registry.harness.close()
        store.close()

    asyncio.run(run())


def test_compactor_failure_keeps_previous_constraints_and_sources(tmp_path):
    async def run():
        store, registry, c = setup(tmp_path)
        store.finish_run(c.run_id, "completed", "Try again")
        source = store.message_id(c.run_id, "user")
        prior = json.dumps(
            {
                "summary": "German",
                "constraints": ["One question at a time"],
                "decisions": [],
                "outstanding_requests": [{"source_id": source, "quote": "Ich sehe der Hund."}],
                "next_steps": ["Ask about accusative"],
            }
        )
        for _ in range(3):
            saved = await compact(
                store,
                None,
                c.run_id,
                c.conversation_id,
                store.completed_messages(c.conversation_id),
                prior,
            )
            prior = saved["text"]
        assert "One question at a time" in prior and "Ask about accusative" in prior
        assert len(store.messages(c.conversation_id)) == 2
        await registry.harness.close()
        store.close()

    asyncio.run(run())


def test_pronunciation_requires_inspected_matching_audio(tmp_path):
    from aloy.harness_state import evidence, learning_record

    store, _, c = setup(tmp_path)
    audio = store.save_audio(
        c.conversation_id, b"synthetic audio", direction="input", extension="wav", run_id=c.run_id
    )
    args = {
        "skill": "pronunciation",
        "evidence_type": "pronunciation",
        "answer": "Hund",
        "feedback": "Synthetic audio-backed feedback",
        "audio_id": audio["id"],
        "source_message_id": store.message_id(c.run_id, "user"),
    }
    with pytest.raises(ValueError, match="inspection_ref"):
        learning_record(c, args)
    media = store.save_media(
        c.conversation_id, b"synthetic audio", kind="audio", mime_type="audio/wav", run_id=c.run_id
    )
    ref = evidence(
        store,
        c.conversation_id,
        c.run_id,
        "media.inspect",
        {
            "media_id": media["id"],
            "observations": [{"modality": "audio", "description": "fixture"}],
        },
    )
    saved = learning_record(c, {**args, "inspection_ref": "e:" + ref})
    assert saved["evidence_type"] == "pronunciation"
    assert (
        store.db.execute("SELECT inspection_ref FROM learning_evidence").fetchone()[0] == "e:" + ref
    )
    store.close()


def test_long_tool_run_rebuilds_context_with_retrievable_evidence(tmp_path):
    async def run():
        store = ConversationStore(tmp_path / "state")
        target = tmp_path / "long.txt"
        target.write_text("detail " * 500 + "\n" + "fact " * 700 + "\n")
        config = AgentConfig(provider="fake", model="fake-v1", tools=HARNESS_NAMES, max_steps=16)
        cid = store.create_conversation(config.system_prompt)
        turns = []

        class Repeating(FakeProvider):
            async def step(self, turn):
                turns.append(turn)
                return (
                    ProviderStepResult(text="Done")
                    if len(turns) == 13
                    else ProviderStepResult(
                        calls=(ToolCall(str(len(turns)), "read", {"path": str(target)}),),
                        continuation_id=str(len(turns)),
                    )
                )

        agent = AgentRunner(config, store, Repeating())
        events = [e async for e in agent.stream(AgentInput(cid, "Read synthetic evidence"))]
        assert any(e.kind == "completed" for e in events), events[-1]
        assert sum(t.continuation_id is None for t in turns) >= 2
        assert store.db.execute("SELECT count(*) FROM tool_evidence").fetchone()[0] == 12
        await agent.registry.harness.close()
        store.close()

    asyncio.run(run())


def test_native_media_wire_formats_and_underscore_tool_names(tmp_path):
    import base64

    from aloy.harness_tools import HARNESS_SPECS
    from aloy.multimodal import data_url, gemini_blocks, router_blocks, wire_name

    path = tmp_path / "synthetic.png"
    path.write_bytes(b"synthetic bytes")
    asset = {"path": str(path), "kind": "image", "mime_type": "image/png"}
    encoded = base64.b64encode(path.read_bytes()).decode()
    assert gemini_blocks([asset]) == [{"type": "image", "mime_type": "image/png", "data": encoded}]
    assert router_blocks([asset]) == [{"type": "image_url", "image_url": {"url": data_url(asset)}}]
    video = {**asset, "kind": "video", "mime_type": "video/mp4"}
    assert router_blocks([video])[0]["type"] == "video_url"
    assert wire_name("context_retrieve", HARNESS_SPECS) == "context_retrieve"
    assert wire_name("terminal_control", HARNESS_SPECS) == "terminal_control"


def test_run_budget_includes_helpers_and_uncertain_dispatches(tmp_path):
    from aloy.budget import RUN_BUDGET

    store = ConversationStore(tmp_path / "state")
    token = RUN_BUDGET.set((store.root, store.monthly_spend(), 0.01))
    try:
        ident = store.reserve(0.008, provider="gemini")
        store.mark_dispatched(ident)
        store.settle(ident)
        with pytest.raises(RuntimeError, match="Per-run"):
            store.reserve(0.003, provider="openrouter")
        assert store.monthly_spend() >= 0.008
    finally:
        RUN_BUDGET.reset(token)
        store.close()
