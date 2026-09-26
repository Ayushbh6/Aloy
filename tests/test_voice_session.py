"""Conversation-mode behavior through the production segmenter, bridge and speech path."""

import asyncio
import base64
from types import SimpleNamespace

import pytest

from aloy.bridge import PROMPT, Bridge
from aloy.dispatch import DispatchBudget
from aloy.speech import SpeechAudio
from aloy.voice_session import VoiceSegmenter, pcm_wav


class LevelDetector:
    """Deterministic speech probabilities, using the real hysteresis classifier."""

    def __init__(self):
        from aloy.vad import StreamingVoiceDetector

        self.detector = StreamingVoiceDetector()
        self.active = False
        self.hot_frames = 0

    def start(self, *args):
        pass

    def stop(self, *args):
        pass

    def feed(self, session, pcm):
        self.detector.speech_frames = self.speech_frames
        self.detector.silence_frames = self.silence_frames
        return self.detector._classify(0.9 if pcm[0] else 0.05)


def feed(segmenter, speech, seconds):
    events = []
    block = (b"\x01\x00" if speech else b"\0\0") * 512
    for _ in range(round(seconds / 0.032)):
        events.extend(segmenter.feed(block))
    return events


def test_segmenter_keeps_hesitations_preroll_and_separates_turns():
    segmenter = VoiceSegmenter("session", LevelDetector())
    segmenter.start()
    assert not feed(segmenter, False, 2)
    assert [e.kind for e in feed(segmenter, True, 0.4)] == ["speech"]
    assert not feed(segmenter, False, 0.6)  # thinking pause, not a new turn
    assert not feed(segmenter, True, 0.5)
    events = feed(segmenter, False, 1.2)
    assert [e.kind for e in events] == ["utterance"]
    assert len(events[0].pcm) / 32000 > 2.5  # includes onset and interior hesitation
    assert [e.kind for e in feed(segmenter, True, 0.4)] == ["speech"]
    assert segmenter.close()  # interrupted capture is retained, not submitted


def test_segmenter_rejects_clicks_and_bounds_idle_memory():
    segmenter = VoiceSegmenter("session", LevelDetector())
    segmenter.start()
    assert not feed(segmenter, True, 0.032)
    assert not feed(segmenter, False, 100)
    assert len(segmenter.prefix) <= 12800
    assert segmenter.close() == b""
    with pytest.raises(ValueError):
        segmenter.feed(b"odd")


class StreamSpeech:
    async def preflight(self):
        pass

    async def stream(self, text):
        yield b"\x01\x00" * 2400
        await asyncio.sleep(0)
        yield b"\x02\x00" * 2400


def test_audio_arrives_before_archive_and_memory_maintenance(tmp_path, monkeypatch):
    from aloy.maintenance import FakeMaintenance

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        cid = bridge.store.create_conversation(PROMPT)
        released = asyncio.Event()
        events = []

        class SlowMemory(FakeMaintenance):
            async def extract(self, *args):
                await asyncio.wait_for(released.wait(), 1)
                return []

        monkeypatch.setattr("aloy.bridge.FakeMaintenance", SlowMemory)
        bridge.synthesizers[("gemini-lite", "Achird")] = StreamSpeech()

        def emit(kind, **fields):
            events.append(kind)
            if kind == "audio_chunk":
                assert not bridge.store.audio_assets(cid)
                released.set()

        bridge.emit = emit
        await bridge.run_turn(cid, "hello", "fake", "gemini-lite", 0, speech_voice="Achird")
        assert released.is_set()
        assert (
            events.index("audio_chunk") < events.index("audio") < events.index("audio_stream_end")
        )
        assert len(bridge.store.audio_assets(cid)) == 1
        assert bridge.store.monthly_spend() > 0
        await bridge.stop(False)
        if bridge.index_task:
            await bridge.index_task
        bridge.store.close()

    asyncio.run(scenario())


def test_interruption_stops_playback_before_waiting_for_cancel_and_stale_frames(
    tmp_path, monkeypatch
):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        cid = bridge.store.create_conversation(PROMPT)
        segmenter = VoiceSegmenter("s", LevelDetector())
        segmenter.start()
        bridge.voice_session = {"segmenter": segmenter, "conversation_id": cid}
        events = []
        entered = asyncio.Event()

        async def pending():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                assert events[0] == ("voice_activity", "speech")

        bridge.emit = lambda kind, **fields: events.append((kind, fields.get("state")))
        bridge.active = asyncio.create_task(pending())
        await entered.wait()
        await bridge.voice_frames({"session_id": "old", "pcm": "invalid ignored"})
        assert not events
        await bridge.voice_frames(
            {"session_id": "s", "pcm": base64.b64encode(b"\x01\x00" * 2048).decode()}
        )
        assert bridge.active is None
        bridge.close_voice_capture()
        assert len(bridge.store.audio_assets(cid)) == 1
        assert not bridge.store.messages(cid)
        bridge.store.close()

    asyncio.run(scenario())


def test_gemini_stream_is_one_dispatch_and_requires_completion(monkeypatch):
    from aloy.paid_speech import GeminiSynthesizer
    from aloy.speech_catalog import BY_ID

    async def scenario():
        calls = []
        complete = False

        async def events():
            yield SimpleNamespace(
                event_type="step.delta",
                delta=SimpleNamespace(
                    type="audio",
                    mime_type="audio/l16;rate=24000",
                    data=base64.b64encode(b"\1\0" * 240).decode(),
                ),
            )
            if complete:
                yield SimpleNamespace(event_type="interaction.completed")

        async def create(**kwargs):
            calls.append(kwargs)
            return events()

        monkeypatch.setattr(
            "aloy.paid_speech.gemini_client",
            lambda _: SimpleNamespace(
                aio=SimpleNamespace(interactions=SimpleNamespace(create=create))
            ),
        )
        synth = GeminiSynthesizer(BY_ID["gemini-lite"], api_key="synthetic")
        synth.dispatch = DispatchBudget(1)
        with pytest.raises(RuntimeError, match="complete response"):
            _ = [chunk async for chunk in synth.stream("Hello")]
        assert synth.dispatch.count == 1 and calls[0]["stream"] is True
        with pytest.raises(RuntimeError, match="cap"):
            _ = [chunk async for chunk in synth.stream("No retry")]
        complete = True
        synth.dispatch = DispatchBudget(1)
        assert len([chunk async for chunk in synth.stream("Hello")]) == 1

    asyncio.run(scenario())


def test_cancelled_stream_retains_conservative_spend_and_no_late_audio(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        cid = bridge.store.create_conversation(PROMPT)
        first = asyncio.Event()
        events = []

        class Waiting(StreamSpeech):
            async def stream(self, text):
                yield b"\0\0" * 2400
                await asyncio.Event().wait()

        bridge.synthesizers[("gemini-lite", "Achird")] = Waiting()

        def emit(kind, **fields):
            events.append(kind)
            if kind == "audio_chunk":
                first.set()

        bridge.emit = emit
        queue = asyncio.Queue()
        queue.put_nowait("Hello")
        task = asyncio.create_task(
            bridge.speak(queue, cid, "gemini-lite", "Achird", 0, {"id": None})
        )
        await asyncio.wait_for(first.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "audio" not in events and "audio_stream_end" not in events
        assert bridge.store.monthly_spend() > 0
        bridge.store.close()

    asyncio.run(scenario())


def test_cached_greeting_has_no_second_dispatch(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        cid = bridge.store.create_conversation(PROMPT)
        calls = []

        class Speech:
            async def synthesize(self, text):
                calls.append(text)
                return SpeechAudio(pcm_wav(b"\0\0" * 1600), "wav", 0.1)

        bridge.synthesizers[("chatterbox", "warm-male")] = Speech()
        bridge.emit = lambda *a, **k: None
        for _ in range(2):
            await bridge.voice_cue(cid, "chatterbox", "warm-male", "Welcome back")
        assert calls == ["Welcome back"]
        bridge.store.close()

    asyncio.run(scenario())


def test_synthetic_voice_checks_remain_offline(tmp_path):
    from aloy.voice_smoke import run

    async def scenario():
        compare = await run(output=tmp_path / "compare")
        pipeline = await run(scenario="pipeline", output=tmp_path / "pipeline")
        assert compare["dispatches"] == 2
        assert pipeline["result"] == "passed"
        assert (tmp_path / "compare" / "compare-stream.pcm").stat().st_size > 0

    asyncio.run(scenario())
