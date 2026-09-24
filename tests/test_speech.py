from aloy.speech import make_synthesizer


def test_selected_gemini_voice_uses_existing_speech_adapter(monkeypatch):
    client = object()
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-key")
    monkeypatch.setattr("aloy.speech.gemini_client", lambda _: client)

    synthesizer = make_synthesizer("gemini")

    assert synthesizer.voice == "Achird"
    assert synthesizer.client is client
