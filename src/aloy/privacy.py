"""Shared conservative boundary for durable memory, not conversation history."""

import re

PRIVATE_PATTERN = re.compile(
    r"(?i)(password|api[_ -]?key|secret|bearer\s+[a-z0-9]|sk-[a-z0-9]|"
    r"\b(?:\d[ -]*?){13,19}\b|bank account|credit card|iban\s*[a-z]{2}\d{2}|"
    r"\b(?:AKIA|AIza|gh[pousr]_)[A-Za-z0-9_-]+|\b[A-Za-z0-9_+/=-]{32,}\b)"
)
