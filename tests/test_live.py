import asyncio
import io
import wave
from contextlib import aclosing

import pytest

from aloy.agent import ChatAgent
from aloy.bridge import Bridge
from aloy.contracts import AgentConfig, ProviderEvent
from aloy.live import LiveResult, _pcm_from_wav
from aloy.storage import ConversationStore


def wav_bytes(seconds: int = 1, rate: int = 16000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\0\0" * rate * seconds)
    return output.getvalue()


def test_live_recording_bypasses_stt_and_preserves_cross_mode_history(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        events = []
        bridge.emit = lambda kind, **data: events.append((kind, data))
        conversation_id = bridge.store.create_conversation("P")
        bridge.store.begin_run(conversation_id, "fake", "fake", "Hallo")
        previous_run = bridge.store.db.execute(
            "SELECT id FROM runs WHERE conversation_id=?", (conversation_id,)
        ).fetchone()[0]
        bridge.store.finish_run(previous_run, "completed", "Guten Tag")
        record = tmp_path / "tmp" / "sample.m4a"
        record.parent.mkdir()
        record.write_bytes(b"synthetic recorded bytes")
        monkeypatch.setattr("aloy.bridge.to_16k_wav", lambda _: wav_bytes())

        class FakeLive:
            async def exchange(self, history, wav):
                assert [(item.role, item.text) for item in history] == [
                    ("user", "Hallo"),
                    ("assistant", "Guten Tag"),
                ]
                assert _pcm_from_wav(wav)[1] == 1
                return LiveResult("Wie geht es dir?", "Mir geht es gut.", wav_bytes(), 1)

        monkeypatch.setattr("aloy.bridge.GeminiLive", FakeLive)
        monkeypatch.setattr(
            bridge.transcriber,
            "transcribe",
            lambda _: (_ for _ in ()).throw(AssertionError("STT must not run")),
        )
        await bridge.run_live(conversation_id, record, bridge.epoch)
        assert not record.exists()
        assert [(m.role, m.text) for m in bridge.store.completed_history(conversation_id)][-2:] == [
            ("user", "Wie geht es dir?"),
            ("assistant", "Mir geht es gut."),
        ]
        assert len(bridge.store.audio_assets(conversation_id)) == 2
        assert [kind for kind, _ in events][-6:] == [
            "transcript",
            "started",
            "delta",
            "completed",
            "audio",
            "turn_done",
        ]
        bridge.store.close()

    asyncio.run(scenario())


def test_live_recording_limit():
    with pytest.raises(ValueError, match="five minutes"):
        _pcm_from_wav(wav_bytes(301))


def test_bridge_fake_transport_is_same_agent_pipeline(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        events = []
        bridge.emit = lambda kind, **data: events.append((kind, data))
        await bridge.handle({"v": 1, "action": "new", "id": "create"})
        conversation_id = events[-1][1]["conversation_id"]
        await bridge.handle(
            {
                "v": 1,
                "action": "send",
                "conversation_id": conversation_id,
                "text": "Hello",
                "provider": "fake",
                "speech": None,
            }
        )
        await bridge.active
        assert [kind for kind, _ in events if kind in ("started", "completed", "turn_done")] == [
            "started",
            "completed",
            "turn_done",
        ]
        assert bridge.store.messages(conversation_id)[-1]["text"] == "Hello from Aloy."
        await bridge.handle({"v": 1, "action": "select", "conversation_id": conversation_id})
        assert len(events[-1][1]["messages"]) == 2
        bridge.store.close()

    asyncio.run(scenario())


def test_bridge_settings_persist_and_reject_unknown_values(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        events = []
        bridge.emit = lambda kind, **data: events.append((kind, data))
        await bridge.handle({"v": 1, "action": "set_setting", "key": "provider", "value": "fake"})
        await bridge.handle({"v": 1, "action": "set_setting", "key": "speech", "value": "none"})
        await bridge.handle({"v": 1, "action": "set_setting", "key": "mode", "value": "live"})
        await bridge.handle({"v": 1, "action": "set_setting", "key": "mode", "value": "unknown"})
        assert events[-1][0] == "error"
        assert bridge.store.settings() == {
            "provider": "fake",
            "speech": "none",
            "mode": "live",
        }
        bridge.store.close()
        reopened = ConversationStore(tmp_path)
        assert reopened.settings()["provider"] == "fake"
        reopened.close()

    asyncio.run(scenario())


def test_cancelled_stream_is_not_completed(tmp_path):
    class SlowProvider:
        async def stream(self, **kwargs):
            yield ProviderEvent("delta", "partial")
            await asyncio.sleep(100)

    async def scenario():
        store = ConversationStore(tmp_path)
        config = AgentConfig(provider="fake", model="fake", system_prompt="P")
        conversation_id = store.create_conversation("P")
        agent = ChatAgent(config, store, SlowProvider())

        async def consume():
            async with aclosing(agent.stream(conversation_id, "question")) as events:
                async for event in events:
                    if event.kind == "delta":
                        await asyncio.sleep(100)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.db.execute("SELECT status FROM runs").fetchone()[0] == "cancelled"
        assert not any(
            m["status"] == "complete" and m["role"] == "assistant"
            for m in store.messages(conversation_id)
        )
        store.close()

    asyncio.run(scenario())
