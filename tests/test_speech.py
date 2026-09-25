from aloy.speech import ChatterboxSynthesizer, make_synthesizer, speech_window


def test_chatterbox_uses_the_local_worker_and_fixed_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))

    synthesizer = make_synthesizer("chatterbox")

    assert synthesizer.worker.engine == "chatterbox-tts"
    assert synthesizer.reference_path == tmp_path / "models" / "chatterbox-reference.wav"


def test_removed_speech_engines_cannot_be_selected():
    import pytest

    for engine in ("qwen", "pocket", "gemini"):
        with pytest.raises(ValueError, match="Unknown TTS engine"):
            make_synthesizer(engine)


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
