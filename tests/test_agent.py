import asyncio

import pytest

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.agent import FakeProvider
from aloy.contracts import ContextOverflowError, Usage


def test_ordered_history_and_restart(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(provider="fake", model="fake-v1", system_prompt="Speak clearly")
        conversation_id = store.create_conversation(config.system_prompt)
        first = FakeProvider("First answer")
        reply = await ChatAgent(config, store, first).reply(conversation_id, "First question")
        assert reply.text == "First answer"
        second = FakeProvider("Second answer")
        config2 = AgentConfig(provider="other-fake", model="fake-v2", system_prompt="Speak clearly")
        reply2 = await ChatAgent(config2, store, second).reply(conversation_id, "Second question")
        assert reply2.text == "Second answer"
        assert [(item.role, item.text) for item in second.calls[0][1]] == [
            ("user", "First question"),
            ("assistant", "First answer"),
            ("user", "Second question"),
        ]
        store.close()
        reopened = ConversationStore(tmp_path)
        assert len(reopened.messages(conversation_id)) == 4
        reopened.close()

    asyncio.run(scenario())


def test_failure_is_not_completed_history(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(provider="fake", model="fake", system_prompt="Prompt")
        conversation_id = store.create_conversation(config.system_prompt)
        failing = ChatAgent(config, store, FakeProvider(fail=ContextOverflowError("too long")))
        with pytest.raises(RuntimeError, match="Start a new conversation"):
            await failing.reply(conversation_id, "Question")
        rows = store.messages(conversation_id)
        assert len(rows) == 1 and rows[0]["role"] == "user"
        assert store.db.execute("SELECT status FROM runs").fetchone()[0] == "failed"

    asyncio.run(scenario())


def test_audio_cleanup_and_prompt_isolation(tmp_path):
    async def scenario():
        store = ConversationStore(tmp_path)
        conversation_id = store.create_conversation("Original")
        asset = store.save_audio(
            conversation_id, b"RIFFexample", direction="input", extension="wav"
        )
        assert store.storage_bytes() > 0
        wrong_agent = ChatAgent(
            AgentConfig(provider="fake", model="fake", system_prompt="Wrong"), store, FakeProvider()
        )
        with pytest.raises(ValueError, match="system prompt"):
            await wrong_agent.reply(conversation_id, "Hello")
        store.delete_conversation(conversation_id)
        from pathlib import Path

        assert not Path(asset["path"]).exists()

    asyncio.run(scenario())


def test_spend_survives_conversation_deletion(tmp_path):
    store = ConversationStore(tmp_path)
    conversation_id = store.create_conversation("P")
    run_id = store.begin_run(conversation_id, "gemini-live", "gemini-3.8-live", "Hello")
    store.finish_run(run_id, "completed", "Hi", Usage(estimated_usd=0.01))
    store.save_audio(
        conversation_id,
        b"RIFFexample",
        direction="output",
        extension="wav",
        estimated_usd=0.02,
    )
    assert store.monthly_spend() == pytest.approx(0.03)
    store.delete_conversation(conversation_id)
    assert store.monthly_spend() == pytest.approx(0.03)
    store.close()
