"""One bounded native-audio exchange, separate from the text agent."""

import asyncio
import io
import os
import wave
from dataclasses import dataclass

from aloy.contracts import Message

MODEL = "gemini-3.8-live"
MAX_SECONDS = 300


@dataclass(frozen=True)
class LiveResult:
    input_text: str
    output_text: str
    output_wav: bytes
    output_seconds: float


def _pcm_from_wav(data: bytes) -> tuple[bytes, float]:
    with wave.open(io.BytesIO(data), "rb") as source:
        if (
            source.getframerate() != 16000
            or source.getnchannels() != 1
            or source.getsampwidth() != 2
        ):
            raise ValueError("Live input requires mono 16 kHz, 16-bit WAV")
        pcm = source.readframes(source.getnframes())
        duration = len(pcm) / (source.getframerate() * source.getsampwidth())
        if not 0 < duration <= MAX_SECONDS:
            raise ValueError("Live recordings must be between 0 and five minutes")
        return pcm, duration


def _wav_from_pcm(data: bytes) -> tuple[bytes, float]:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(24000)
        target.writeframes(data)
    return output.getvalue(), len(data) / 48000


class GeminiLive:
    def __init__(self, api_key: str | None = None, client=None) -> None:
        if client is None:
            from google import genai

            api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is missing")
            client = genai.Client(api_key=api_key)
        self.client = client

    async def exchange(self, history: list[Message], wav: bytes) -> LiveResult:
        from google.genai import types

        pcm, _ = _pcm_from_wav(wav)
        config = {
            "response_modalities": ["AUDIO"],
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "realtime_input_config": {"automatic_activity_detection": {"disabled": True}},
        }
        input_fragments: list[str] = []
        output_fragments: list[str] = []
        audio_parts: list[bytes] = []
        async with asyncio.timeout(MAX_SECONDS):
            async with self.client.aio.live.connect(model=MODEL, config=config) as session:
                if history:
                    await session.send_client_content(
                        turns=[
                            {
                                "role": "user" if item.role == "user" else "model",
                                "parts": [{"text": item.text}],
                            }
                            for item in history
                        ],
                        turn_complete=False,
                    )
                await session.send_realtime_input(activity_start=types.ActivityStart())
                for start in range(0, len(pcm), 32000):
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=pcm[start : start + 32000], mime_type="audio/pcm;rate=16000"
                        )
                    )
                await session.send_realtime_input(activity_end=types.ActivityEnd())
                async for message in session.receive():
                    content = message.server_content
                    if not content:
                        continue
                    if content.input_transcription and content.input_transcription.text:
                        input_fragments.append(content.input_transcription.text)
                    if content.output_transcription and content.output_transcription.text:
                        output_fragments.append(content.output_transcription.text)
                    if content.model_turn:
                        for part in content.model_turn.parts or []:
                            if part.inline_data and part.inline_data.data:
                                audio_parts.append(part.inline_data.data)
                    if content.interrupted:
                        raise RuntimeError("Live response was interrupted")
                    if content.turn_complete:
                        break
                else:
                    raise RuntimeError("Live session closed without a completed turn")
        if not audio_parts:
            raise RuntimeError("Live response contained no audio")
        output_wav, seconds = _wav_from_pcm(b"".join(audio_parts))
        return LiveResult(
            " ".join(input_fragments).strip() or "[voice input]",
            " ".join(output_fragments).strip() or "[audio response]",
            output_wav,
            seconds,
        )
