from aloy.vad import StreamingVoiceDetector


def test_short_speech_and_natural_pause_do_not_end_recording():
    detector = StreamingVoiceDetector()
    assert detector._classify(0.72) is None
    assert detector._classify(0.72) == "speech"
    assert all(detector._classify(0.1) is None for _ in range(37))
    assert detector.active
    assert detector._classify(0.1) == "pause"
    assert detector._classify(0.8) is None
    assert detector._classify(0.8) == "speech"


def test_stale_microphone_frames_are_ignored():
    detector = StreamingVoiceDetector()
    assert detector.feed("old-session", b"\0\0" * 512) is None


def test_voice_activity_transport_does_not_dispatch_agent(tmp_path, monkeypatch):
    import asyncio
    import base64

    from aloy.bridge import Bridge

    class Detector:
        def start(self, session_id, sample_rate):
            assert (session_id, sample_rate) == ("one", 16000)

        def feed(self, session_id, pcm):
            assert (session_id, pcm) == ("one", b"\x00\x00")
            return "speech"

        def stop(self, session_id):
            assert session_id == "one"

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        bridge = Bridge()
        bridge.voice_monitor = Detector()
        events = []
        bridge.emit = lambda kind, **fields: events.append((kind, fields))
        await bridge.handle(
            {"v": 1, "action": "vad_start", "session_id": "one", "sample_rate": 16000}
        )
        await bridge.handle(
            {
                "v": 1,
                "action": "vad_audio",
                "session_id": "one",
                "pcm": base64.b64encode(b"\0\0").decode(),
            }
        )
        await bridge.handle({"v": 1, "action": "vad_end", "session_id": "one"})
        assert [event for event, _ in events] == ["speech_activity"]
        assert bridge.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        bridge.store.close()

    asyncio.run(scenario())
