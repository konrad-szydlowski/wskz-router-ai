"""Runtime configuration read from environment variables (see compose.yaml / .env.example)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
    ollama_model: str = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    smtp_host: str = os.environ.get("SMTP_HOST", "mailpit")
    smtp_port: int = _int("SMTP_PORT", 1025)
    mail_from: str = os.environ.get("MAIL_FROM", "ai-router@example.com")
    # Seconds a single LLM call may take before the request fails with 504.
    llm_timeout: int = _int("LLM_TIMEOUT", 120)
    # How many times the agent is reminded to call the tool before we give up (502).
    tool_nudges: int = _int("TOOL_NUDGES", 2)


settings = Settings()
