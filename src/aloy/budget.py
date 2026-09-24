"""Conservative direct text cost estimates; the owner ceiling lives here."""

from datetime import UTC, datetime

from aloy.contracts import AgentConfig, Message, Usage

MONTHLY_LIMIT_USD = 30.0
MONTHLY_WARNING_USD = 20.0


def tts_cost(seconds: float, characters: int) -> float:
    """Estimate Gemini 3.8 Flash-Lite TTS standard tier with the dated price step."""
    new_price = datetime.now(UTC).date().isoformat() >= "2027-01-01"
    audio_per_second = 0.00030 if new_price else 0.00015
    text_per_million = 1.0 if new_price else 0.5
    return round(seconds * audio_per_second + characters / 4 * text_per_million / 1e6, 8)


# USD per million tokens. These are estimates, not provider invoices.
RATES = {
    "deepseek/deepseek-v4.1-flash": (0.30, 1.20),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
}


def estimated_cost(model: str, usage: Usage) -> float | None:
    rate = RATES.get(model)
    if rate is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    return round((usage.input_tokens * rate[0] + usage.output_tokens * rate[1]) / 1_000_000, 8)


def maximum_reservation(config: AgentConfig, history: list[Message], text: str) -> float:
    rate = RATES.get(config.model)
    if rate is None:
        return 0.0  # Codex subscription has no metered API estimate here.
    characters = len(config.system_prompt) + len(text) + sum(len(item.text) for item in history)
    input_ceiling = characters + 128  # one token per character plus protocol overhead
    return (input_ceiling * rate[0] + config.max_output_tokens * rate[1]) / 1_000_000
