"""Paid speech transports only; the bridge owns turns, persistence and spending."""

import asyncio
import base64
import os

from aloy.dispatch import DispatchBudget, gemini_client
from aloy.speech import SpeechAudio, mp3_audio, wav_audio
from aloy.speech_catalog import SpeechOption


class GeminiSynthesizer:
    def __init__(self, option: SpeechOption, api_key: str | None = None) -> None:
        api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is missing")
        self.option = option
        self.client = gemini_client(api_key)
        self.dispatch = DispatchBudget()
        self.checked = False

    async def preflight(self) -> None:
        if not self.checked:
            await self.client.aio.models.get(model=self.option.model)
            self.checked = True

    def _request(self, text: str) -> dict:
        return {
            "model": self.option.model,
            "input": [
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "annotations": [
                                {
                                    "type": "speech_metadata",
                                    "style": (
                                        "Warm, natural, conversational; speak as one flowing "
                                        "utterance. Pronounce the name Aloy as Ah-loy "
                                        "(two syllables, Ah then loy), not Ee-loy or Eli. "
                                        "Do not speak these pronunciation instructions."
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ],
            "response_format": {"type": "audio"},
            "generation_config": {
                "speech_config": [{"voice": self.option.voice}],
                "max_output_tokens": 4096,
            },
        }

    async def synthesize(self, text: str) -> SpeechAudio:
        async with asyncio.timeout(45):
            self.dispatch.consume()
            interaction = await self.client.aio.interactions.create(**self._request(text))
        if not interaction.output_audio or not interaction.output_audio.data:
            raise RuntimeError("Gemini TTS returned no audio")
        return wav_audio(base64.b64decode(interaction.output_audio.data))

    async def stream(self, text: str):
        """One continuous utterance, delivered as playable PCM before completion."""
        self.dispatch.consume()
        async with asyncio.timeout(45):
            stream = await self.client.aio.interactions.create(**self._request(text), stream=True)
            completed, total = False, 0
            try:
                async for event in stream:
                    if event.event_type == "step.delta" and event.delta.type == "audio":
                        mime = getattr(event.delta, "mime_type", None)
                        if mime and not mime.startswith(("audio/l16", "audio/pcm")):
                            raise RuntimeError("Unexpected streaming speech format")
                        data = base64.b64decode(event.delta.data, validate=True)
                        total += len(data)
                        if total > 20_000_000 or len(data) % 2 or data.startswith(b"RIFF"):
                            raise RuntimeError("Invalid streaming speech PCM")
                        if data:
                            yield data
                    elif event.event_type == "interaction.completed":
                        completed = True
                    elif event.event_type in {"error", "interaction.failed"}:
                        raise RuntimeError("Streaming speech provider failed")
                if not completed or not total:
                    raise RuntimeError("Streaming speech ended without a complete response")
            finally:
                if hasattr(stream, "aclose"):
                    await stream.aclose()

    async def close(self) -> None:
        await self.client.aio.aclose()
        self.client.close()


class OpenRouterSynthesizer:
    def __init__(self, option: SpeechOption, api_key: str | None = None) -> None:
        import httpx

        api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY is missing")
        self.option = option
        self.client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=45,
        )
        self.dispatch = DispatchBudget()
        self.checked = False

    async def preflight(self) -> None:
        if self.checked:
            return
        response = await self.client.get(
            "/models", params={"output_modalities": "speech", "q": self.option.model}
        )
        if response.status_code != 200:
            raise RuntimeError(f"OpenRouter TTS catalog failed (HTTP {response.status_code})")
        model = next(
            (
                item
                for item in response.json().get("data", [])
                if item.get("id") == self.option.model
            ),
            None,
        )
        if not model or self.option.voice not in (model.get("supported_voices") or []):
            raise ValueError(f"OpenRouter TTS model or voice unavailable: {self.option.model}")
        pricing = model.get("pricing", {})
        try:
            input_rate = float(pricing["prompt"])
            output_rate = float(pricing["completion"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("OpenRouter TTS pricing unavailable") from exc
        within_limit = input_rate <= 0.00003 and output_rate == 0
        if not within_limit:
            raise ValueError("OpenRouter TTS price exceeds Aloy's configured budget rate")
        self.checked = True

    async def synthesize(self, text: str) -> SpeechAudio:
        payload = {
            "model": self.option.model,
            "input": text,
            "voice": self.option.voice,
            "response_format": "mp3",
        }
        async with asyncio.timeout(45):
            self.dispatch.consume()
            response = await self.client.post(
                "/audio/speech",
                json=payload,
            )
        if response.status_code != 200:
            raise RuntimeError(f"OpenRouter TTS failed (HTTP {response.status_code})")
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        if content_type == "audio/mpeg":
            return await asyncio.to_thread(mp3_audio, response.content)
        raise RuntimeError("OpenRouter TTS returned unexpected content type")

    async def close(self) -> None:
        await self.client.aclose()
