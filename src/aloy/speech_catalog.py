"""The one catalog for Aloy's selectable Standard-mode speech routes."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aloy.storage import data_root


@dataclass(frozen=True)
class SpeechOption:
    id: str
    title: str
    detail: str
    provider: str
    model: str | None = None
    voice: str | None = None


OPTIONS = (
    SpeechOption(
        "chatterbox",
        "Chatterbox · Local",
        "Offline; reference WAV voices",
        "local",
        voice="warm-male",
    ),
    SpeechOption(
        "gemini-lite",
        "Gemini Lite · Direct",
        "Gemini 3.8 Flash-Lite TTS · Achird · paid",
        "gemini",
        "gemini-3.8-flash-lite-tts",
        "Achird",
    ),
    SpeechOption(
        "router-grok",
        "Grok Voice · OpenRouter",
        "Grok Voice TTS 1.0 · paid; German voice unreviewed",
        "openrouter",
        "x-ai/grok-voice-tts-1.0",
        "sal",
    ),
)

BY_ID = {option.id: option for option in OPTIONS}


GEMINI_VOICES = (
    ("Achird", "Friendly"),
    ("Zephyr", "Bright"),
    ("Puck", "Upbeat"),
    ("Charon", "Informative"),
    ("Kore", "Firm"),
    ("Fenrir", "Excitable"),
    ("Leda", "Youthful"),
    ("Orus", "Firm"),
    ("Aoede", "Breezy"),
    ("Callirrhoe", "Easy-going"),
    ("Autonoe", "Bright"),
    ("Enceladus", "Breathy"),
    ("Iapetus", "Clear"),
    ("Umbriel", "Easy-going"),
    ("Algieba", "Smooth"),
    ("Despina", "Smooth"),
    ("Erinome", "Clear"),
    ("Algenib", "Gravelly"),
    ("Rasalgethi", "Informative"),
    ("Laomedeia", "Upbeat"),
    ("Achernar", "Soft"),
    ("Alnilam", "Firm"),
    ("Schedar", "Even"),
    ("Gacrux", "Mature"),
    ("Pulcherrima", "Forward"),
    ("Zubenelgenubi", "Casual"),
    ("Vindemiatrix", "Gentle"),
    ("Sadachbia", "Lively"),
    ("Sadaltager", "Knowledgeable"),
    ("Sulafat", "Warm"),
)

GROK_VOICES = (
    ("sal", "Smooth, balanced"),
    ("ara", "Warm, friendly"),
    ("eve", "Energetic, upbeat"),
    ("rex", "Confident, clear"),
    ("leo", "Authoritative, strong"),
)


def voice_options(engine: str, root: Path | None = None) -> list[dict[str, str]]:
    option = BY_ID[engine]
    if option.provider == "gemini":
        return [{"id": name, "title": f"{name} · {style}"} for name, style in GEMINI_VOICES]
    if option.provider == "openrouter":
        return [{"id": name, "title": f"{name.title()} · {style}"} for name, style in GROK_VOICES]
    voices = [{"id": "warm-male", "title": "Warm male · Local"}]
    folder = (root or data_root()) / "models" / "voices"
    if folder.is_dir():
        for path in sorted(folder.glob("*.wav")):
            if path.is_file() and not path.is_symlink() and path.stem != "warm-male":
                voices.append(
                    {
                        "id": f"local:{path.stem}",
                        "title": path.stem.replace("_", " ").title() + " · Local",
                    }
                )
    return voices


def resolve_voice(engine: str, voice: str | None = None, root: Path | None = None) -> str:
    if engine not in BY_ID:
        raise ValueError("Unknown speech engine")
    selected = voice if voice is not None else BY_ID[engine].voice
    if selected not in {choice["id"] for choice in voice_options(engine, root)}:
        raise ValueError("Voice is unavailable for the selected speech engine")
    return selected


def local_reference(voice: str, root: Path | None = None) -> Path:
    root = root or data_root()
    resolve_voice("chatterbox", voice, root)
    if voice == "warm-male":
        return root / "models" / "chatterbox-reference.wav"
    return root / "models" / "voices" / (voice.removeprefix("local:") + ".wav")


def selectable_options() -> list[dict[str, object]]:
    return [
        {
            "id": option.id,
            "title": option.title,
            "detail": option.detail,
            "default_voice": option.voice,
            "voices": voice_options(option.id),
        }
        for option in OPTIONS
    ]


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
