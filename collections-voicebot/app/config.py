"""Environment configuration. Loaded once at import."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
OUTCOME_WEBHOOK_URL = os.getenv("OUTCOME_WEBHOOK_URL", "")

LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4.1-mini")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "gpt-4o")

LOGS_DIR = ROOT / "logs"
RECORDINGS_DIR = ROOT / "recordings"
PROMPTS_DIR = ROOT / "prompts"

LOGS_DIR.mkdir(exist_ok=True)
RECORDINGS_DIR.mkdir(exist_ok=True)


def assert_runtime_keys() -> None:
    missing = [
        name
        for name, value in [
            ("OPENAI_API_KEY", OPENAI_API_KEY),
            ("SARVAM_API_KEY", SARVAM_API_KEY),
            ("OUTCOME_WEBHOOK_URL", OUTCOME_WEBHOOK_URL),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing env vars: {', '.join(missing)}. Copy .env.example to .env and fill them in."
        )
