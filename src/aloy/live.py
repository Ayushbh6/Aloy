"""One bounded native-audio exchange, separate from the text agent."""

import asyncio
import io
import os
import wave
from dataclasses import dataclass

from aloy.budget import live_cost
from aloy.contracts import Message
from aloy.dispatch import DispatchBudget, gemini_client

MODEL = "gemini-3.8-live"
MAX_SECONDS = 300


@dataclass(frozen=True)
class LiveResult:
    input_text: str
    output_text: str
    output_wav: bytes
    output_seconds: float
    estimated_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


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
        self.owns_client = client is None
        self.dispatch = DispatchBudget(limit=1)
        if client is None:
            api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY is missing")
            client = gemini_client(api_key)
        self.client = client

    async def exchange(
        self,
        history: list[Message],
        wav: bytes,
        *,
        system_prompt: str = "You are Aloy. Speak briefly.",
    ) -> LiveResult:
        from google.genai import types

        try:
            pcm, _ = _pcm_from_wav(wav)
            config = {
                "response_modalities": ["AUDIO"],
                "system_instruction": system_prompt,
                "max_output_tokens": 2048,
                "input_audio_transcription": {},
                "output_audio_transcription": {},
                "realtime_input_config": {"automatic_activity_detection": {"disabled": True}},
            }
            input_tokens = output_tokens = None
            input_fragments: list[str] = []
            output_fragments: list[str] = []
            audio_parts: list[bytes] = []
            async with asyncio.timeout(MAX_SECONDS):
                self.dispatch.consume()
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
                        usage = getattr(message, "usage_metadata", None)
                        if usage:
                            input_tokens = getattr(usage, "prompt_token_count", None)
                            output_tokens = getattr(usage, "response_token_count", None)
                            if output_tokens is not None:
                                output_tokens += getattr(usage, "thoughts_token_count", 0) or 0
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
                estimated_usd=live_cost(input_tokens, output_tokens),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        finally:
            if self.owns_client:
                await self.client.aio.aclose()
                self.client.close()
