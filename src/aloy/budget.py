"""Conservative direct text cost estimates; the owner ceiling lives here."""

from contextvars import ContextVar
from datetime import UTC, datetime

from aloy.contracts import AgentConfig, Message, Usage

RUN_BUDGET = ContextVar("aloy_run_budget", default=None)

MONTHLY_LIMIT_USD = 30.0
MONTHLY_WARNING_USD = 20.0


# USD per million tokens. These are estimates, not provider invoices.
RATES = {
    "deepseek/deepseek-v4.1-flash": (0.30, 1.20),
    "z-ai/glm-5.3-flash": (0.15, 0.60),
    "qwen/qwen3.8-omni-flash": (0.15, 0.47),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
}


def model_rates(model: str, day: str | None = None):
    rate = RATES.get(model)
    day = day or datetime.now(UTC).date().isoformat()
    if model == "gemini-3.8-flash" and day >= "2027-01-01":
        return (1.50, 7.50)
    return rate


def live_cost(input_tokens: int | None, output_tokens: int | None) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    # Highest audio/text rates bound mixed-modality totals conservatively.
    return round((input_tokens * 3.0 + output_tokens * 12.0) / 1e6, 8)


def estimated_cost(model: str, usage: Usage) -> float | None:
    rate = model_rates(model)
    if rate is None or usage.input_tokens is None or usage.output_tokens is None:
        return None
    return round((usage.input_tokens * rate[0] + usage.output_tokens * rate[1]) / 1_000_000, 8)


def maximum_reservation(config: AgentConfig, history: list[Message], text: str) -> float:
    rate = model_rates(config.model)
    if rate is None:
        if config.provider in {"codex", "fake", "other-fake"}:
            return 0.0
        raise ValueError("No verified price configured for this model")
    characters = len(config.system_prompt) + len(text) + sum(len(item.text) for item in history)
    characters = len(
        (config.system_prompt + text + "".join(item.text for item in history)).encode("utf-8")
    )
    input_ceiling = characters + 128  # one token per character plus protocol overhead
    return (input_ceiling * rate[0] + config.max_output_tokens * rate[1]) / 1_000_000
