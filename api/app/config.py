"""Runtime configuration read from environment variables (see docker-compose.yml / .env.example)."""

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# OLLAMA_MODEL=auto (default): the 7B model where the Docker host/VM has room for it, 3B otherwise (DECYZJE.md D4b).
# MemTotal, not MemAvailable: the choice must not flip when the API restarts while Ollama already holds 7B in RAM.
BIG_MODEL, SMALL_MODEL = "qwen2.5:7b", "qwen2.5:3b"
BIG_MODEL_MIN_RAM_GIB = 12


def mem_total_gib(meminfo: str = "/proc/meminfo") -> float | None:
    try:
        with open(meminfo) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024**2
    except (OSError, ValueError):
        pass
    return None


def pick_model(requested: str, ram_gib: float | None) -> str:
    if requested.strip().lower() != "auto":
        return requested
    return BIG_MODEL if ram_gib is not None and ram_gib >= BIG_MODEL_MIN_RAM_GIB else SMALL_MODEL


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
    ollama_model: str = pick_model(os.environ.get("OLLAMA_MODEL", "auto"), mem_total_gib())
    smtp_host: str = os.environ.get("SMTP_HOST", "mailpit")
    smtp_port: int = _int("SMTP_PORT", 1025)
    mail_from: str = os.environ.get("MAIL_FROM", "ai-router@example.com")
    # Seconds a single LLM call may take before the request fails with 504.
    llm_timeout: int = _int("LLM_TIMEOUT", 120)
    # How many times the agent is reminded to call the tool before we give up (502).
    tool_nudges: int = _int("TOOL_NUDGES", 2)


settings = Settings()
