"""Speech engines around, never inside, the shared text agent."""

import base64
import io
import os
import re
import subprocess
import threading
import wave
from pathlib import Path
from typing import Protocol

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


def speech_window(timestamps: list[dict], sample_rate: int, total_samples: int) -> slice:
    """Trim only outside the spoken span; keep hesitations and pauses inside it."""
    margin = int(sample_rate * 0.25)
    start = max(0, int(timestamps[0]["start"]) - margin)
    end = min(total_samples, int(timestamps[-1]["end"]) + margin)
    return slice(start, end)


def pcm_for_transcription(audio_path: Path, sample_rate: int):
    import numpy as np

    def read(source):
        with wave.open(source, "rb") as reader:
            if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (
                1,
                2,
                sample_rate,
            ):
                raise ValueError("Audio needs conversion before transcription")
            return np.frombuffer(reader.readframes(reader.getnframes()), dtype="<i2")

    try:
        return read(str(audio_path))
    except (ValueError, wave.Error):
        if sample_rate != 16000:
            raise ValueError("Unsupported VAD sample rate") from None
        return read(io.BytesIO(to_16k_wav(audio_path)))


def audio_for_transcription(audio_path: Path):
    import mlx.core as mx
    import numpy as np

    samples = pcm_for_transcription(audio_path, 16000)
    return mx.array(samples.astype(np.float32) / 32768), len(samples)


class MLXTranscriber:
    def __init__(self, model_name: str = "qwen-asr") -> None:
        self.model_name = model_name
        self._model = None
        self._vad = None
        self._lock = threading.Lock()
        self.worker = SpeechWorker("asr:" + model_name)

    def _prewarm(self) -> None:
        from mlx_audio.stt.utils import load_model
        from mlx_audio.vad.utils import load_model as load_vad

        with self._lock:
            if self._vad is None:
                self._vad = load_vad(model_path("vad"))
            if self._model is None:
                self._model = load_model(model_path(self.model_name))

    def _transcribe(self, audio_path: Path) -> str:
        from mlx_audio.stt.utils import load_model

        with self._lock:
            # Detect actual speech before asking a generative recognizer to decode it.
            # A failure to load VAD must fail closed, never bypass the gate.
            if self._vad is None:
                from mlx_audio.vad.utils import load_model as load_vad

                self._vad = load_vad(model_path("vad"))
            audio, sample_count = audio_for_transcription(audio_path)
            detection = self._vad.generate(
                audio,
                sample_rate=16000,
                threshold=0.6,
                min_speech_duration_ms=180,
                min_silence_duration_ms=160,
                speech_pad_ms=100,
            )
            if not detection.timestamps:
                return ""
            if self._model is None:
                self._model = load_model(model_path(self.model_name))
            window = speech_window(detection.timestamps, detection.sample_rate, sample_count)
            spoken_audio = audio[window]
            options = (
                {"system_prompt": "Aloy, Ayush. English and German conversation."}
                if self.model_name == "qwen-asr"
                else {}
            )
            result = self._model.generate(spoken_audio, **options)
            return result.text.strip()

    async def transcribe(self, audio_path: Path) -> str:
        return await self.worker.request(str(audio_path))

    async def prewarm(self) -> None:
        await self.worker.request({"command": "prewarm"})

    async def close(self) -> None:
        await self.worker.close()
        self.unload()

    def unload(self) -> None:
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

    def _load(self) -> None:
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

    def _synthesize(self, text: str) -> bytes:
        self._load()
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

    async def prewarm(self) -> None:
        await self.worker.request({"command": "prewarm"})

    async def close(self) -> None:
        await self.worker.close()
        self._model = None
        self._conditionals = None


def make_synthesizer(name: str) -> Synthesizer:
    if name == "chatterbox":
        return ChatterboxSynthesizer()
    raise ValueError(f"Unknown TTS engine: {name}")
