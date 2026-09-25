from aloy.speech import ChatterboxSynthesizer, make_synthesizer, speech_window


def test_chatterbox_uses_the_local_worker_and_fixed_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))

    synthesizer = make_synthesizer("chatterbox")

    assert synthesizer.worker.engine == "chatterbox-tts:warm-male"
    assert synthesizer.reference_path == tmp_path / "models" / "chatterbox-reference.wav"


def test_removed_speech_engines_cannot_be_selected():
    import pytest

    for engine in ("qwen", "pocket", "gemini", "gemini-flash", "router-gemini-lite"):
        with pytest.raises(ValueError, match="Unknown TTS engine"):
            make_synthesizer(engine)


def test_voice_catalog_and_local_reference_selection(tmp_path, monkeypatch):
    import pytest

    from aloy.speech_catalog import resolve_voice, selectable_options

    monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
    folder = tmp_path / "models" / "voices"
    folder.mkdir(parents=True)
    (folder / "second_warm.wav").write_bytes(b"RIFF")
    options = {item["id"]: item for item in selectable_options()}
    assert len(options) == 3
    assert len(options["gemini-lite"]["voices"]) == 30
    assert len(options["router-grok"]["voices"]) == 5
    assert options["gemini-lite"]["default_voice"] == "Achird"
    assert options["router-grok"]["default_voice"] == "sal"
    assert resolve_voice("chatterbox", "local:second_warm") == "local:second_warm"
    assert (
        make_synthesizer("chatterbox", "local:second_warm").reference_path
        == folder / "second_warm.wav"
    )
    with pytest.raises(ValueError, match="unavailable"):
        make_synthesizer("router-grok", "Achird")


def test_selected_voice_reaches_shared_turn_and_invalid_voice_cannot_dispatch(
    tmp_path, monkeypatch
):
    import asyncio

    from aloy.bridge import PROMPT, Bridge
    from aloy.speech import SpeechAudio

    class Synth:
        async def preflight(self):
            pass

        async def synthesize(self, text):
            return SpeechAudio(b"ID3" + b"x" * 100, "mp3", 1)

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        selected = []

        def factory(engine, voice):
            selected.append((engine, voice))
            return Synth()

        monkeypatch.setattr("aloy.bridge.make_synthesizer", factory)
        bridge = Bridge()
        bridge.emit = lambda *args, **kwargs: None
        cid = bridge.store.create_conversation(PROMPT)
        await bridge.run_turn(cid, "Hello", "fake", "router-grok", bridge.epoch, speech_voice="ara")
        assert selected == [("router-grok", "ara")]
        assert len(bridge.store.audio_assets(cid)) == 1
        await bridge.handle(
            {
                "v": 1,
                "action": "send",
                "conversation_id": cid,
                "text": "Hi",
                "provider": "fake",
                "speech": "router-grok",
                "voice": "Achird",
            }
        )
        assert selected == [("router-grok", "ara")]
        bridge.store.close()

    asyncio.run(scenario())


def test_retired_voice_preference_migrates_to_chatterbox(tmp_path, monkeypatch):
    from aloy.bridge import Bridge
    from aloy.storage import ConversationStore

    monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
    store = ConversationStore(tmp_path)
    store.set_setting("speech", "qwen-aiden")
    store.close()

    bridge = Bridge()
    assert bridge.store.settings()["speech"] == "chatterbox"
    bridge.store.close()


def test_chatterbox_uses_english_for_english_and_german_for_lesson_sentences():
    assert ChatterboxSynthesizer.language_code("Hello from Aloy.") == "en"
    assert ChatterboxSynthesizer.language_code("Heute üben wir den Akkusativ.") == "de"
    assert ChatterboxSynthesizer.language_code("Hallo Ayush.") == "de"


def test_speech_window_retains_pause_and_soft_edges():
    # Speech at 2–3 s and 5–6 s; the entire pause between phrases survives.
    window = speech_window(
        [{"start": 32_000, "end": 48_000}, {"start": 80_000, "end": 96_000}],
        sample_rate=16_000,
        total_samples=160_000,
    )
    assert window == slice(28_000, 100_000)
