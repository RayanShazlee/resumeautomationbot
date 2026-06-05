"""Configuration loaded from environment variables / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (two levels up from this file).
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

OUTPUT_DIR = PROJECT_ROOT / "output"
UPLOAD_DIR = PROJECT_ROOT / "uploads"


@dataclass
class Config:
    """Runtime configuration for the bot."""

    api_key: str
    model: str
    pdflatex_path: str
    port: int

    @classmethod
    def load(cls, api_key: str | None = None) -> "Config":
        """Build configuration.

        ``api_key`` lets the caller supply the Anthropic key directly (e.g. from a
        web form in a multi-user deployment). When omitted, it falls back to the
        ``ANTHROPIC_API_KEY`` environment variable.
        """
        api_key = (api_key or os.getenv("ANTHROPIC_API_KEY", "")).strip()
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6").strip()
        pdflatex_path = os.getenv("PDFLATEX_PATH", "pdflatex").strip()
        try:
            port = int(os.getenv("PORT", "5000"))
        except ValueError:
            port = 5000

        if not api_key or api_key.startswith("sk-ant-xxxx"):
            raise RuntimeError(
                "No Anthropic API key provided. Enter your key (starts with "
                "'sk-ant-') from https://console.anthropic.com/settings/keys"
            )

        OUTPUT_DIR.mkdir(exist_ok=True)
        UPLOAD_DIR.mkdir(exist_ok=True)

        return cls(
            api_key=api_key,
            model=model,
            pdflatex_path=pdflatex_path,
            port=port,
        )
