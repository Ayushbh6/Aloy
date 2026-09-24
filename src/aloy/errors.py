"""User-visible diagnostics never contain configured credentials."""

import os


def safe_error(error: Exception, limit: int = 160) -> str:
    message = str(error)
    for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "[redacted]")
    return f"{type(error).__name__}: {message[:limit]}"
