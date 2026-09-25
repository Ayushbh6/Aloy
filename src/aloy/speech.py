"""Speech engines around, never inside, the shared text agent."""

import asyncio
import base64
import io
import os
import re
import subprocess
import threading
import wave
from pathlib import Path
from typing import Protocol

from aloy.dispatch import DispatchBudget, gemini_client
from aloy.models import MODELS, cache_dir, model_path
from aloy.speech_worker import SpeechWorker
from aloy.storage import data_root

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
    def __init__(self, model_name: str = "qwen-asr") -> None:
        self.model_name = model_name
        self._model = None
        self._vad = None
        self._lock = threading.Lock()
        self.worker = SpeechWorker("asr:" + model_name)

    def _transcribe(self, audio_path: Path) -> str:
        from mlx_audio.stt.utils import load_model

        with self._lock:
            # Detect actual speech before asking a generative recognizer to decode it.
            # A failure to load VAD must fail closed, never bypass the gate.
            if self._vad is None:
                from mlx_audio.vad.utils import load_model as load_vad

                self._vad = load_vad(model_path("vad"))
            detection = self._vad.generate(
                str(audio_path),
                threshold=0.6,
                min_speech_duration_ms=180,
                min_silence_duration_ms=160,
                speech_pad_ms=100,
            )
            if not detection.timestamps:
                return ""
            if self._model is None:
                self._model = load_model(model_path(self.model_name))
            options = (
                {"system_prompt": "Aloy, Ayush. English and German conversation."}
                if self.model_name == "qwen-asr"
                else {}
            )
            result = self._model.generate(str(audio_path), **options)
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


class QwenSynthesizer:
    """One reusable local TTS adapter; voice variants share weights and code."""

    def __init__(self, voice: str = "Ryan") -> None:
        self.voice = voice
        self._model = None
        self.worker = SpeechWorker("qwen-tts:" + voice)

    def _synthesize(self, text: str) -> bytes:
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model

        if self._model is None:
            self._model = load_model(model_path("qwen-tts"))
        chunks = list(
            self._model.generate_custom_voice(
                text=text,
                speaker=self.voice,
                language="auto",
                instruct=(
                    "Speak naturally and clearly, like a friendly young adult. "
                    "Warm conversational tone, no theatrical delivery."
                ),
                temperature=0.65,
                max_tokens=2048,
            )
        )
        if not chunks:
            raise RuntimeError("Local TTS returned no audio")
        return pcm_to_wav(mx.concatenate([chunk.audio for chunk in chunks]), chunks[0].sample_rate)

    async def synthesize(self, text: str) -> bytes:
        return base64.b64decode(await self.worker.request(text))

    async def close(self) -> None:
        await self.worker.close()
        self._model = None


class ChatterboxSynthesizer:
    """One fixed local voice, conditioned once per worker lifetime."""

    def __init__(self, reference_path: Path | None = None) -> None:
        self.reference_path = reference_path or data_root() / "models" / "chatterbox-reference.wav"
        self._model = None
        self._conditionals = None
        self.worker = SpeechWorker("chatterbox-tts")

    @staticmethod
    def language_code(text: str) -> str:
        """Pick between the two conversation languages for this sentence."""
        words = set(re.findall(r"[\wäöüß]+", text.casefold()))
        german = words & {
            "aber",
            "bitte",
            "das",
            "dem",
            "den",
            "der",
            "die",
            "du",
            "ein",
            "eine",
            "guten",
            "hallo",
            "heute",
            "ich",
            "ist",
            "ja",
            "jetzt",
            "nicht",
            "sehr",
            "und",
            "was",
            "wie",
            "wir",
        }
        english = words & {
            "and",
            "are",
            "from",
            "good",
            "hello",
            "how",
            "is",
            "please",
            "the",
            "today",
            "what",
            "you",
            "your",
        }
        if german or english:
            return "de" if len(german) > len(english) else "en"
        return "de" if words & {"für", "schön", "über", "üben"} else "en"

    def _synthesize(self, text: str) -> bytes:
        from mlx_audio.tts.models.chatterbox.chatterbox import Model

        if self._model is None:
            if not self.reference_path.is_file():
                raise RuntimeError("Chatterbox reference voice is missing")
            checkpoint = model_path("chatterbox")
            tokenizer = model_path("chatterbox-tokenizer")
            # MLX Audio requests S3TokenizerV2 at its default revision. In Aloy's
            # offline worker, point that cache alias at our verified pinned snapshot.
            alias = tokenizer.parent.parent / "refs" / "main"
            alias.parent.mkdir(parents=True, exist_ok=True)
            alias.write_text(MODELS["chatterbox-tokenizer"][1])
            model = Model.from_pretrained(checkpoint)
            conditionals = model.prepare_conditionals(
                str(self.reference_path), 24000, exaggeration=0.5
            )
            self._model, self._conditionals = model, conditionals
        chunks = list(
            self._model.generate(
                text=text,
                conds=self._conditionals,
                lang_code=self.language_code(text),
                exaggeration=0.5,
                cfg_weight=0.3,
                max_new_tokens=1200,
                verbose=False,
            )
        )
        if not chunks:
            raise RuntimeError("Chatterbox returned no audio")
        return pcm_to_wav(chunks[0].audio, chunks[0].sample_rate)

    async def synthesize(self, text: str) -> bytes:
        return base64.b64decode(await self.worker.request(text))

    async def close(self) -> None:
        await self.worker.close()
        self._model = None
        self._conditionals = None


class GeminiSynthesizer:
    def __init__(self, api_key: str | None = None, voice: str = "Achird") -> None:

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
    if name in {"qwen", "qwen-aiden"}:
        return QwenSynthesizer("Aiden" if name == "qwen-aiden" else "Ryan")
    if name == "gemini":
        return GeminiSynthesizer()
    if name == "chatterbox":
        return ChatterboxSynthesizer()
    if name == "pocket":
        return PocketSynthesizer()
    raise ValueError(f"Unknown TTS engine: {name}")
