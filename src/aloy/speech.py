"""Speech engines around, never inside, the shared text agent."""

import asyncio
import base64
import io
import os
import subprocess
import threading
import wave
from pathlib import Path
from typing import Protocol

from aloy.dispatch import DispatchBudget, gemini_client
from aloy.models import cache_dir, model_path
from aloy.speech_worker import SpeechWorker

os.environ.setdefault("HF_HOME", str(cache_dir().parent))


class Transcriber(Protocol):
    async def transcribe(self, audio_path: Path) -> str: ...


class Synthesizer(Protocol):
    async def synthesize(self, text: str) -> bytes: ...


def pcm_to_wav(samples, sample_rate: int) -> bytes:
    import numpy as np

    array = np.asarray(samples, dtype=np.float32).reshape(-1)
    payload = (np.clip(array, -1, 1) * 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(payload)
    return output.getvalue()


def to_16k_wav(input_path: Path) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return result.stdout


class MLXTranscriber:
    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self.worker = SpeechWorker("asr")

    def _transcribe(self, audio_path: Path) -> str:
        from mlx_audio.stt.utils import load_model

        with self._lock:
            if self._model is None:
                self._model = load_model(model_path("qwen-asr"))
            result = self._model.generate(str(audio_path))
            return result.text.strip()

    async def transcribe(self, audio_path: Path) -> str:
        return await self.worker.request(str(audio_path))

    async def close(self) -> None:
        await self.worker.close()
        self.unload()

    def unload(self) -> None:
        self._model = None


class PocketSynthesizer:
    def __init__(self) -> None:
        self._model = None
        self._voice = None
        self._lock = threading.Lock()
        self.worker = SpeechWorker("tts")

    def _synthesize(self, text: str) -> bytes:
        from pocket_tts import TTSModel

        with self._lock:
            if self._model is None:
                # The packaged German config falls back to its ungated, non-cloning weights.
                self._model = TTSModel.load_model(language="german")
            if self._voice is None:
                self._voice = self._model.get_state_for_audio_prompt("juergen")
            audio = self._model.generate_audio(self._voice, text)
            return pcm_to_wav(audio.numpy(), self._model.sample_rate)

    async def synthesize(self, text: str) -> bytes:
        return base64.b64decode(await self.worker.request(text))

    async def close(self) -> None:
        await self.worker.close()
        self.unload()

    def unload(self) -> None:
        self._model = None
        self._voice = None


class GeminiSynthesizer:
    def __init__(self, api_key: str | None = None, voice: str = "Charon") -> None:

        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing")
        self.client = gemini_client(api_key)
        self.dispatch = DispatchBudget()
        self.voice = voice

    async def close(self):
        await self.client.aio.aclose()
        self.client.close()

    async def synthesize(self, text: str) -> bytes:
        async with asyncio.timeout(45):
            self.dispatch.consume()
            interaction = await self.client.aio.interactions.create(
                model="gemini-3.8-flash-lite-tts",
                input=[
                    {
                        "type": "user_input",
                        "content": [
                            {
                                "type": "text",
                                "text": text,
                                "annotations": [
                                    {
                                        "type": "speech_metadata",
                                        "style": "warm, natural, conversational",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                response_format={"type": "audio"},
                generation_config={
                    "speech_config": [{"voice": self.voice}],
                    "max_output_tokens": 2048,
                },
            )
        if not interaction.output_audio or not interaction.output_audio.data:
            raise RuntimeError("Gemini TTS returned no audio")
        data = base64.b64decode(interaction.output_audio.data)
        if not data.startswith(b"RIFF") or len(data) > 20_000_000:
            raise RuntimeError("Gemini TTS returned unexpected audio")
        return data


def make_synthesizer(name: str) -> Synthesizer:
    if name == "gemini":
        return GeminiSynthesizer()
    if name == "pocket":
        return PocketSynthesizer()
    raise ValueError(f"Unknown TTS engine: {name}")
