import asyncio
import base64
import io
import shutil
import subprocess
import wave
from types import SimpleNamespace

import pytest

from aloy.speech import SpeechAudio, mp3_audio, wav_audio
from aloy.speech_catalog import BY_ID, selectable_options, speech_cost, speech_reservation


def silent_wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(24000)
        writer.writeframes(b"\0\0" * 24000)
    return output.getvalue()


def test_catalog_has_distinct_local_direct_and_router_choices():
    ids = {item["id"] for item in selectable_options()}
    assert {
        "chatterbox",
        "gemini-lite",
        "gemini-flash",
        "router-gemini-lite",
        "router-gemini-flash",
        "router-grok",
        "none",
    } == ids
    assert speech_cost(BY_ID["chatterbox"], "Hallo", 5) == 0
    for name in ids - {"chatterbox", "none"}:
        assert speech_reservation(BY_ID[name], "Hallo Ayush.") > speech_cost(
            BY_ID[name], "Hallo Ayush.", 2
        )


def test_gemini_interactions_uses_selected_voice_and_one_dispatch(monkeypatch):
    from aloy.paid_speech import GeminiSynthesizer

    calls = []

    class Aio:
        models = SimpleNamespace(get=lambda **kwargs: checked(kwargs))
        interactions = SimpleNamespace(create=lambda **kwargs: generated(kwargs))

        async def aclose(self):
            pass

    async def checked(kwargs):
        calls.append(("preflight", kwargs))

    async def generated(kwargs):
        calls.append(("generate", kwargs))
        return SimpleNamespace(output_audio=SimpleNamespace(data=base64.b64encode(silent_wav())))

    client = SimpleNamespace(aio=Aio(), close=lambda: None)
    monkeypatch.setattr("aloy.paid_speech.gemini_client", lambda _: client)

    async def scenario():
        synth = GeminiSynthesizer(BY_ID["gemini-lite"], api_key="synthetic")
        await synth.preflight()
        result = await synth.synthesize("Hallo Ayush.")
        assert result.extension == "wav"
        assert result.duration_seconds == 1
        assert synth.dispatch.count == 1
        assert calls[0][1]["model"] == "gemini-3.8-flash-lite-tts"
        payload = calls[1][1]
        assert payload["generation_config"]["speech_config"] == [{"voice": "Achird"}]
        assert payload["input"][0]["content"][0]["text"] == "Hallo Ayush."
        await synth.close()

    asyncio.run(scenario())


def test_openrouter_uses_speech_endpoint_and_exact_model_voice(monkeypatch):
    import httpx

    from aloy.paid_speech import OpenRouterSynthesizer

    calls = []

    class Client:
        async def get(self, path, **kwargs):
            calls.append(("get", path, kwargs))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "x-ai/grok-voice-tts-1.0",
                            "supported_voices": ["leo"],
                            "pricing": {"prompt": "0.000015", "completion": "0"},
                        }
                    ]
                },
            )

        async def post(self, path, **kwargs):
            calls.append(("post", path, kwargs))
            return httpx.Response(200, content=b"mp3-bytes", headers={"content-type": "audio/mpeg"})

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    monkeypatch.setattr(
        "aloy.paid_speech.mp3_audio",
        lambda data: SpeechAudio(data, "mp3", 1.0),
    )

    async def scenario():
        synth = OpenRouterSynthesizer(BY_ID["router-grok"], api_key="synthetic")
        await synth.preflight()
        result = await synth.synthesize("Guten Morgen.")
        assert result.extension == "mp3"
        assert synth.dispatch.count == 1
        assert calls[1][1] == "/audio/speech"
        assert calls[1][2]["json"] == {
            "model": "x-ai/grok-voice-tts-1.0",
            "input": "Guten Morgen.",
            "voice": "leo",
            "response_format": "mp3",
        }
        await synth.close()

    asyncio.run(scenario())


def test_openrouter_gemini_uses_pcm_and_speech_metadata(monkeypatch):
    import httpx

    from aloy.paid_speech import OpenRouterSynthesizer

    calls = []

    class Client:
        async def get(self, path, **kwargs):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "google/gemini-3.8-flash-lite-tts",
                            "supported_voices": ["Achird"],
                            "pricing": {"prompt": "0.0000005", "completion": "0.000006"},
                        }
                    ]
                },
            )

        async def post(self, path, **kwargs):
            calls.append(kwargs["json"])
            return httpx.Response(
                200,
                content=b"\0\0" * 24000,
                headers={"content-type": "audio/pcm"},
            )

        async def aclose(self):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())

    async def scenario():
        synth = OpenRouterSynthesizer(BY_ID["router-gemini-lite"], api_key="synthetic")
        await synth.preflight()
        result = await synth.synthesize("Guten Morgen.")
        assert result.extension == "wav"
        assert result.duration_seconds == 1
        assert calls[0]["response_format"] == "pcm"
        assert calls[0]["provider"]["options"]["google-ai-studio"]["speech_metadata"]
        await synth.close()

    asyncio.run(scenario())


def test_paid_reply_uses_shared_storage_and_spend_ledger(tmp_path, monkeypatch):
    from aloy.bridge import PROMPT, Bridge

    class Speech:
        def __init__(self):
            self.calls = 0

        async def preflight(self):
            pass

        async def synthesize(self, text):
            self.calls += 1
            return SpeechAudio(b"ID3" + b"x" * 100, "mp3", 2)

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        synth = Speech()
        monkeypatch.setattr("aloy.bridge.make_synthesizer", lambda _: synth)
        bridge = Bridge()
        bridge.emit = lambda *args, **kwargs: None
        cid = bridge.store.create_conversation(PROMPT)
        await bridge.run_turn(cid, "Q", "fake", "router-grok", bridge.epoch)
        assets = bridge.store.audio_assets(cid)
        assert synth.calls == 1
        assert len(assets) == 1 and assets[0]["path"].endswith(".mp3")
        assert assets[0]["provider"] == "router-grok"
        assert bridge.store.monthly_spend() == pytest.approx(assets[0]["estimated_usd"])
        bridge.store.close()

    asyncio.run(scenario())


def test_budget_blocks_paid_speech_before_dispatch(tmp_path, monkeypatch):
    from aloy.bridge import PROMPT, Bridge

    class Speech:
        called = False

        async def preflight(self):
            pass

        async def synthesize(self, text):
            self.called = True
            raise AssertionError("Paid synthesis was dispatched")

    async def scenario():
        monkeypatch.setenv("ALOY_DATA_DIR", str(tmp_path))
        synth = Speech()
        monkeypatch.setattr("aloy.bridge.make_synthesizer", lambda _: synth)
        bridge = Bridge()
        events = []
        bridge.emit = lambda kind, **fields: events.append(kind)
        cid = bridge.store.create_conversation(PROMPT)
        bridge.store.reserve(30)
        await bridge.run_turn(cid, "Q", "fake", "router-grok", bridge.epoch)
        assert "speech_error" in events
        assert not synth.called
        assert not bridge.store.audio_assets(cid)
        bridge.store.close()

    asyncio.run(scenario())


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg unavailable")
def test_mp3_keeps_compressed_audio_and_measures_playback_duration():
    encoded = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "pipe:1",
        ],
        input=silent_wav(),
        capture_output=True,
        check=True,
    ).stdout
    result = mp3_audio(encoded)
    assert result.extension == "mp3"
    assert result.data == encoded
    assert result.duration_seconds == pytest.approx(1, abs=0.1)
    assert wav_audio(silent_wav()).duration_seconds == 1
