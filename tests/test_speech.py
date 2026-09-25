from aloy.speech import ChatterboxSynthesizer, make_synthesizer


def test_selected_gemini_voice_uses_existing_speech_adapter(monkeypatch):
    client = object()
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr("aloy.speech.gemini_client", lambda _: client)

    synthesizer = make_synthesizer("gemini")

    assert synthesizer.voice == "Achird"
    assert synthesizer.client is client


def test_chatterbox_uses_the_local_worker_and_fixed_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))

    synthesizer = make_synthesizer("chatterbox")

    assert synthesizer.worker.engine == "chatterbox-tts"
    assert synthesizer.reference_path == tmp_path / "models" / "chatterbox-reference.wav"


def test_chatterbox_uses_english_for_english_and_german_for_lesson_sentences():
    assert ChatterboxSynthesizer.language_code("Hello from Aloy.") == "en"
    assert ChatterboxSynthesizer.language_code("Heute üben wir den Akkusativ.") == "de"
    assert ChatterboxSynthesizer.language_code("Hallo Ayush.") == "de"
