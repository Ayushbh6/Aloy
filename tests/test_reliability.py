import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from aloy import AgentConfig, ChatAgent, ConversationStore
from aloy.agent import FakeProvider
from aloy.bridge import Bridge
from aloy.contracts import Usage
from aloy.dispatch import DispatchBudget
from aloy.lifecycle import RunLifecycle


def test_atomic_begin_and_finish(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("P")
    store.db.execute(
        "CREATE TRIGGER fault BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.begin_run(cid, "fake", "fake", "Q")
    assert store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    store.db.execute("DROP TRIGGER fault")
    run = store.begin_run(cid, "fake", "fake", "Q")
    store.db.execute(
        "CREATE TRIGGER fault BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.finish_run(run, "completed", "A", Usage(estimated_usd=1))
    assert store.run(run)["status"] == "running"
    assert store.monthly_spend() == 0
    store.close()


def test_migration_rollback(tmp_path, monkeypatch):
    migrations = tmp_path / "package" / "migrations"
    migrations.mkdir(parents=True)
    (migrations / "001.sql").write_text("CREATE TABLE probe(id INTEGER);\nINVALID SQL;\n")
    monkeypatch.setattr("aloy.storage.files", lambda _: tmp_path / "package")
    with pytest.raises(sqlite3.OperationalError):
        ConversationStore(tmp_path / "data")
    db = sqlite3.connect(tmp_path / "data/aloy.sqlite3")
    assert not db.execute("SELECT name FROM sqlite_master WHERE name='probe'").fetchall()
    assert not db.execute("SELECT * FROM schema_migrations").fetchall()
    db.close()


def test_reservations_cross_connection_recovery_and_deletion(tmp_path):
    store = ConversationStore(tmp_path)
    held = store.reserve(29)
    second = ConversationStore(tmp_path)
    with pytest.raises(RuntimeError, match="limit"):
        second.reserve(2)
    store.settle(held)
    assert store.monthly_spend() == 0
    cid = store.create_conversation("P")
    with pytest.raises(asyncio.CancelledError):
        with RunLifecycle(store, cid, "gemini", "gemini-3.5-flash-lite", "Q", 0.1) as run:
            run.dispatched()
            raise asyncio.CancelledError
    assert store.run(run.run_id)["status"] == "cancelled"
    assert store.monthly_spend() == pytest.approx(0.1)
    store.delete_conversation(cid)
    assert store.monthly_spend() == pytest.approx(0.1)
    reservation = store.reserve(0.2)
    store.mark_dispatched(reservation)
    store.reserve(0.3)
    store.recover()
    assert store.monthly_spend() == pytest.approx(0.3)
    store.close()
    second.close()


def test_audio_relations_recovery_and_deletion_retry(tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    first = store.create_conversation("P")
    second = store.create_conversation("P")
    run = store.begin_run(first, "fake", "fake", "Q")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_audio(second, b"sample", direction="input", extension="wav", run_id=run)
    asset = store.save_audio(first, b"sample", direction="input", extension="wav", run_id=run)
    store.attach_run_audio(run)
    assert store.audio_assets(first)[0]["message_id"]
    store.mark_playback(asset["id"], "played")
    assert store.audio_assets(first)[0]["playback_status"] == "played"
    original = Path.unlink

    def failed_unlink(path, **kwargs):
        if str(path) == asset["path"]:
            raise PermissionError("synthetic")
        return original(path, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed_unlink)
    store.delete_conversation(first)
    assert Path(asset["path"]).exists()
    assert store.db.execute("SELECT COUNT(*) FROM pending_deletions").fetchone()[0] == 1
    monkeypatch.setattr(Path, "unlink", original)
    store.recover()
    assert not Path(asset["path"]).exists()
    orphan = tmp_path / "tmp" / "retained.wav"
    orphan.parent.mkdir()
    orphan.write_bytes(b"recording")
    store.recover()
    assert orphan in store.orphan_audio()
    assert orphan.exists()
    store.close()


def test_restart_does_not_interrupt_other_store_until_recovery(tmp_path):
    store = ConversationStore(tmp_path)
    cid = store.create_conversation("P")
    run = store.begin_run(cid, "fake", "fake", "Q")
    second = ConversationStore(tmp_path)
    assert second.run(run)["status"] == "running"
    with pytest.raises(sqlite3.IntegrityError):
        second.begin_run(cid, "fake", "fake", "Concurrent Q")
    second.recover()
    assert store.run(run)["status"] == "interrupted"
    store.close()
    second.close()


@pytest.mark.parametrize("kind", ["empty", "timeout", "unavailable"])
def test_agent_failures_and_preflight(tmp_path, kind):
    class Provider(FakeProvider):
        async def preflight(self, model):
            if kind == "unavailable":
                raise ValueError("Model unavailable")

        async def stream(self, **kwargs):
            if kind == "timeout":
                await asyncio.sleep(1)
            async for event in super().stream(**kwargs):
                yield event

    async def scenario():
        store = ConversationStore(tmp_path)
        cid = store.create_conversation("P")
        config = AgentConfig(system_prompt="P", provider="fake", model="fake", timeout_seconds=0.01)
        with pytest.raises(RuntimeError):
            await ChatAgent(config, store, Provider(answer="")).reply(cid, "Q")
        assert store.db.execute("SELECT status FROM runs").fetchone()[0] == "failed"
        assert not [m for m in store.completed_history(cid) if m.role == "assistant"]
        store.close()

    asyncio.run(scenario())


def test_operation_ids_follow_async_events(tmp_path, monkeypatch, capsys):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        cid = bridge.store.create_conversation(
            __import__("aloy.bridge", fromlist=["PROMPT"]).PROMPT
        )
        await bridge.handle(
            {
                "v": 1,
                "action": "send",
                "conversation_id": cid,
                "text": "Q",
                "provider": "fake",
                "speech": None,
                "operation_id": "first",
            }
        )
        await bridge.active
        await bridge.handle({"v": 1, "action": "stop", "operation_id": "second"})
        packets = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert all(
            p["operation_id"] == "first" for p in packets if p["event"] in {"delta", "completed"}
        )
        assert packets[-1]["operation_id"] == "second"
        bridge.store.close()

    asyncio.run(scenario())


def test_dispatch_cap():
    guard = DispatchBudget(limit=1)
    guard.consume()
    with pytest.raises(RuntimeError, match="cap"):
        guard.consume()
    assert guard.count == 1


def test_standard_audio_links_and_speech_error_cleanup(tmp_path, monkeypatch):
    import io
    import wave

    from aloy.bridge import PROMPT
    from aloy.speech import wav_audio

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 160)

    class Speech:
        async def synthesize(self, text):
            return wav_audio(buffer.getvalue())

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("aloy.bridge.make_synthesizer", lambda *_: Speech())
        bridge = Bridge()
        bridge.emit = lambda *args, **kwargs: None
        cid = bridge.store.create_conversation(PROMPT)
        await bridge.run_turn(cid, "Q", "fake", "chatterbox", bridge.epoch)
        assets = bridge.store.audio_assets(cid)
        assert assets and all(a["run_id"] and a["message_id"] for a in assets)
        assert bridge.store.monthly_spend() == 0
        bridge.store.close()

    asyncio.run(scenario())


def test_recording_prewarm_reuses_the_reply_synthesizer(tmp_path, monkeypatch):
    import io
    import wave

    from aloy.bridge import PROMPT
    from aloy.speech import wav_audio

    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 160)

    class Speech:
        def __init__(self):
            self.warms = 0
            self.replies = 0

        async def prewarm(self):
            self.warms += 1

        async def synthesize(self, text):
            self.replies += 1
            return wav_audio(output.getvalue())

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        created = []

        def factory(*_):
            created.append(Speech())
            return created[-1]

        monkeypatch.setattr("aloy.bridge.make_synthesizer", factory)
        bridge = Bridge()

        async def warm_asr():
            return None

        bridge.transcriber.prewarm = warm_asr
        bridge.emit = lambda *args, **kwargs: None
        cid = bridge.store.create_conversation(PROMPT)
        await bridge.handle({"v": 1, "action": "prewarm_speech", "speech": "chatterbox"})
        await bridge.warm_task
        await bridge.run_turn(cid, "Q", "fake", "chatterbox", bridge.epoch)
        assert len(created) == 1
        assert created[0].warms == 1
        assert created[0].replies == 1
        bridge.store.close()

    asyncio.run(scenario())


def test_stop_cancels_local_speech_without_audio(tmp_path, monkeypatch):
    from aloy.bridge import PROMPT

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        started, cancelled = asyncio.Event(), asyncio.Event()

        class SlowSpeech:
            async def synthesize(self, text):
                started.set()
                try:
                    await asyncio.sleep(100)
                finally:
                    cancelled.set()

        monkeypatch.setattr("aloy.bridge.make_synthesizer", lambda *_: SlowSpeech())
        bridge = Bridge()
        events = []
        bridge.emit = lambda event, **kw: events.append(event)
        cid = bridge.store.create_conversation(PROMPT)
        await bridge.handle(
            {
                "v": 1,
                "action": "send",
                "conversation_id": cid,
                "text": "Q",
                "provider": "fake",
                "speech": "chatterbox",
            }
        )
        await started.wait()
        await bridge.stop()
        assert cancelled.is_set()
        assert "audio" not in events
        assert bridge.store.monthly_spend() == 0
        assert bridge.active is None
        bridge.store.close()

    asyncio.run(scenario())


def test_cancel_recording_retains_audio_without_dispatch(tmp_path, monkeypatch):
    async def scenario():
        from aloy.bridge import PROMPT

        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        bridge.emit = lambda *args, **kwargs: None
        cid = bridge.store.create_conversation(PROMPT)
        path = tmp_path / "tmp" / "cancelled.m4a"
        path.parent.mkdir()
        path.write_bytes(b"synthetic capture")
        await bridge.handle(
            {
                "v": 1,
                "action": "cancel_recording",
                "conversation_id": cid,
                "path": str(path),
                "duration": 1.5,
            }
        )
        assets = bridge.store.audio_assets(cid)
        assert len(assets) == 1
        assert Path(assets[0]["path"]).read_bytes() == b"synthetic capture"
        assert not path.exists()
        assert not bridge.store.completed_history(cid)
        assert bridge.store.run(assets[0]["run_id"])["status"] == "cancelled"
        assert bridge.store.monthly_spend() == 0
        bridge.store.close()

    asyncio.run(scenario())


def test_live_rejects_non_capture_file_without_deleting_it(tmp_path, monkeypatch):
    async def scenario():
        from aloy.bridge import PROMPT

        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        events = []
        bridge.emit = lambda event, **kw: events.append(event)
        cid = bridge.store.create_conversation(PROMPT)
        protected = tmp_path / "credentials.env"
        protected.write_text("synthetic sentinel, not a credential")
        await bridge.run_live(cid, protected, bridge.epoch)
        assert protected.read_text() == "synthetic sentinel, not a credential"
        assert "error" in events
        assert not bridge.store.audio_assets(cid)
        bridge.store.close()

    asyncio.run(scenario())


def test_concurrent_budget_reservation_is_atomic(tmp_path):
    import concurrent.futures
    import threading

    store = ConversationStore(tmp_path)
    store.close()
    barrier = threading.Barrier(2)

    def reserve():
        local = ConversationStore(tmp_path)
        try:
            barrier.wait()
            try:
                local.reserve(20)
                return True
            except RuntimeError:
                return False
        finally:
            local.close()

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sorted(results) == [False, True]


def test_bad_transport_does_not_crash_bridge(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        events = []
        bridge.emit = lambda event, **kw: events.append(event)
        await bridge.handle([])
        await bridge.handle({"v": 1, "action": "new"})
        assert events[0] == "error" and events[-1] == "conversation"
        bridge.store.close()

    asyncio.run(scenario())


def test_dated_rates_and_live_usage():
    from aloy.budget import live_cost, model_rates

    assert model_rates("gemini-3.8-flash", "2026-12-31") == (0.75, 3.75)
    assert model_rates("gemini-3.8-flash", "2027-01-01") == (1.5, 7.5)
    assert live_cost(100, 100) == pytest.approx(0.0015)
    assert live_cost(None, 100) is None


def test_silence_does_not_dispatch_or_enter_history(tmp_path, monkeypatch):
    import io
    import wave

    payload = io.BytesIO()
    with wave.open(payload, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 16000)

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("aloy.bridge.to_16k_wav", lambda _: payload.getvalue())
        bridge = Bridge()
        events = []
        bridge.emit = lambda event, **kw: events.append(event)

        async def silence(_):
            return ""

        async def forbidden(*args, **kwargs):
            raise AssertionError("Silent capture dispatched a turn")

        bridge.transcriber.transcribe = silence
        bridge.run_turn = forbidden
        cid = bridge.store.create_conversation("Synthetic")
        path = tmp_path / "tmp" / "silence.wav"
        path.parent.mkdir()
        path.write_bytes(payload.getvalue())
        await bridge.transcribe_and_run(cid, path, "fake", "qwen", bridge.epoch)
        assert "no_speech" in events
        assert "transcript" not in events
        assert not bridge.store.completed_history(cid)
        assert bridge.store.monthly_spend() == 0
        assert len(bridge.store.audio_assets(cid)) == 1
        bridge.store.close()

    asyncio.run(scenario())


def test_vad_rejection_precedes_asr_load(monkeypatch, tmp_path):
    import sys
    from types import ModuleType, SimpleNamespace

    from aloy.speech import MLXTranscriber

    module = ModuleType("mlx_audio.stt.utils")

    def forbidden(*args, **kwargs):
        raise AssertionError("ASR loaded for non-speech")

    module.load_model = forbidden
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.utils", module)
    monkeypatch.setattr("aloy.speech.audio_for_transcription", lambda _: (object(), 512))
    transcriber = MLXTranscriber()
    transcriber._vad = SimpleNamespace(
        generate=lambda *args, **kwargs: SimpleNamespace(timestamps=[])
    )
    assert transcriber._transcribe(tmp_path / "synthetic.wav") == ""


def test_vad_failure_cannot_bypass_gate(monkeypatch, tmp_path):
    import sys
    from types import ModuleType, SimpleNamespace

    from aloy.speech import MLXTranscriber

    module = ModuleType("mlx_audio.stt.utils")

    def forbidden(*args, **kwargs):
        raise AssertionError("ASR loaded after detector failure")

    def detector_failure(*args, **kwargs):
        raise RuntimeError("Detector unavailable")

    module.load_model = forbidden
    monkeypatch.setitem(sys.modules, "mlx_audio.stt.utils", module)
    monkeypatch.setattr("aloy.speech.audio_for_transcription", lambda _: (object(), 512))
    transcriber = MLXTranscriber()
    transcriber._vad = SimpleNamespace(generate=detector_failure)
    with pytest.raises(RuntimeError, match="Detector unavailable"):
        transcriber._transcribe(tmp_path / "synthetic.wav")
