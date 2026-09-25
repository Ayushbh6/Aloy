"""The one catalog for Aloy's selectable Standard-mode speech routes."""

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class SpeechOption:
    id: str
    title: str
    detail: str
    provider: str
    model: str | None = None
    voice: str | None = None


OPTIONS = (
    SpeechOption("chatterbox", "Chatterbox · Local", "Offline; no speech API charge", "local"),
    SpeechOption(
        "gemini-lite",
        "Gemini Lite · Direct",
        "Gemini 3.8 Flash-Lite TTS · Achird · paid",
        "gemini",
        "gemini-3.8-flash-lite-tts",
        "Achird",
    ),
    SpeechOption(
        "gemini-flash",
        "Gemini Flash · Direct",
        "Gemini 3.8 Flash TTS · Achird · paid",
        "gemini",
        "gemini-3.8-flash-tts",
        "Achird",
    ),
    SpeechOption(
        "router-gemini-lite",
        "Gemini Lite · OpenRouter",
        "Gemini 3.8 Flash-Lite TTS · Achird · paid",
        "openrouter",
        "google/gemini-3.8-flash-lite-tts",
        "Achird",
    ),
    SpeechOption(
        "router-gemini-flash",
        "Gemini Flash · OpenRouter",
        "Gemini 3.8 Flash TTS · Achird · paid",
        "openrouter",
        "google/gemini-3.8-flash-tts",
        "Achird",
    ),
    SpeechOption(
        "router-grok",
        "Grok Voice · OpenRouter",
        "Grok Voice TTS 1.0 · Leo · paid; German voice unreviewed",
        "openrouter",
        "x-ai/grok-voice-tts-1.0",
        "leo",
    ),
)

BY_ID = {option.id: option for option in OPTIONS}


def selectable_options() -> list[dict[str, str]]:
    return [
        {"id": option.id, "title": option.title, "detail": option.detail} for option in OPTIONS
    ] + [{"id": "none", "title": "Text only", "detail": "No generated speech"}]


def _gemini_rates(option: SpeechOption, day: str | None = None) -> tuple[float, float]:
    day = day or datetime.now(UTC).date().isoformat()
    multiplier = 2 if day >= "2027-01-01" else 1
    output = 9.0 if "flash-tts" in (option.model or "") and "lite" not in option.model else 6.0
    return 0.50 * multiplier, output * multiplier


def speech_reservation(option: SpeechOption, text: str) -> float:
    """Hold a deliberately generous allowance before a paid synthesis request."""
    if option.provider == "local":
        return 0.0
    if option.model == "x-ai/grok-voice-tts-1.0":
        return round(len(text) * 0.00003, 8)  # 2x the current $15/M characters.
    input_rate, output_rate = _gemini_rates(option)
    input_tokens_ceiling = len(text.encode("utf-8")) + 128
    seconds_ceiling = max(20.0, len(text) * 0.25 + 10.0)
    return round(
        input_tokens_ceiling * input_rate / 1_000_000
        + seconds_ceiling * 25 * output_rate / 1_000_000,
        8,
    )


def speech_cost(option: SpeechOption, text: str, duration: float) -> float:
    """Estimate cost when audio APIs return no invoice-grade usage counters."""
    if option.provider == "local":
        return 0.0
    if option.model == "x-ai/grok-voice-tts-1.0":
        return round(len(text) * 15 / 1_000_000, 8)
    input_rate, output_rate = _gemini_rates(option)
    estimated_input_tokens = max(1, len(text.encode("utf-8")) / 4)
    return round(
        (estimated_input_tokens * input_rate + duration * 25 * output_rate) / 1_000_000,
        8,
    )
