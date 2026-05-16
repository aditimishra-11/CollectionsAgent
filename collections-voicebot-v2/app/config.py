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

# Acoustic echo cancellation — relevant in voice mode on speakers.
# Override via env vars to tune for a specific laptop/room.
AEC_ENABLED = os.getenv("AEC_ENABLED", "true").lower() in {"true", "1", "yes"}
AEC_FILTER_LENGTH = int(os.getenv("AEC_FILTER_LENGTH", "2048"))  # ~128 ms at 16 kHz
AEC_MU = float(os.getenv("AEC_MU", "0.1"))  # NLMS step size
PLAYBACK_INITIAL_MUTE_MS = int(os.getenv("PLAYBACK_INITIAL_MUTE_MS", "400"))
MIN_BARGE_IN_DURATION_MS = int(os.getenv("MIN_BARGE_IN_DURATION_MS", "500"))
BARGE_IN_RMS_FLOOR = float(os.getenv("BARGE_IN_RMS_FLOOR", "800"))

# Cost estimation — USD per million tokens.
# Approximate published rates (May 2026) for OpenAI gpt-4.1-mini.
# Override via env if pricing changes.
LLM_INPUT_USD_PER_MTOK = float(os.getenv("LLM_INPUT_USD_PER_MTOK", "0.15"))
LLM_OUTPUT_USD_PER_MTOK = float(os.getenv("LLM_OUTPUT_USD_PER_MTOK", "0.60"))
USD_TO_INR = float(os.getenv("USD_TO_INR", "84.0"))
# Vendor-published flat per-call costs (Sarvam Saaras + Bulbul, INR per call)
STT_INR_PER_CALL = float(os.getenv("STT_INR_PER_CALL", "0.05"))
TTS_INR_PER_CALL = float(os.getenv("TTS_INR_PER_CALL", "0.02"))
# Telephony (Exotel India). Documented for completeness — not incurred in
# text-mode eval, only in voice mode.
TELEPHONY_INR_PER_MINUTE = float(os.getenv("TELEPHONY_INR_PER_MINUTE", "0.75"))

# --- Bank-policy constants (Mumbai Bank specifics) ---
# These were previously baked into prompt files, invisible to the rest of
# the system. Moved here so the orchestrator and audit can reference them,
# and so a different bank deploying the same bot can override via env.
# Numbers are representative of mid-tier Indian private-bank credit-card
# terms in 2026; real bank sets their own.
LATE_FEE_INR = float(os.getenv("LATE_FEE_INR", "750"))
LATE_FEE_APPLIES_ABOVE_INR = float(os.getenv("LATE_FEE_APPLIES_ABOVE_INR", "10000"))
MONTHLY_INTEREST_PCT = float(os.getenv("MONTHLY_INTEREST_PCT", "3.5"))
MAD_PCT_OF_OUTSTANDING = float(os.getenv("MAD_PCT_OF_OUTSTANDING", "5.0"))  # RBI is upper bound

# --- Conversation runtime ---
MAX_TURNS = int(os.getenv("MAX_TURNS", "16"))
LLM_REPLY_MAX_TOKENS = int(os.getenv("LLM_REPLY_MAX_TOKENS", "250"))
LLM_REPLY_TEMPERATURE = float(os.getenv("LLM_REPLY_TEMPERATURE", "0.55"))

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
