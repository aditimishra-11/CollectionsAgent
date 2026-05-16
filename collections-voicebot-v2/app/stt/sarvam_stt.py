"""Sarvam Saaras V3 — speech to text-translate.

The Saaras family is Sarvam's translate model: any Indian language or English
in → English text out. This is what we want, because the bot speaks English
and we'd rather feed English context to the LLM regardless of what the
customer used.

Endpoint: POST https://api.sarvam.ai/speech-to-text-translate
(Saarika models use /speech-to-text — different endpoint for raw transcription.)
"""

from __future__ import annotations

import io

import httpx
import numpy as np
import soundfile as sf
from loguru import logger

from app.config import SARVAM_API_KEY

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/speech-to-text-translate"


class SarvamSTT:
    def __init__(self, model: str = "saaras:v2.5") -> None:
        self.model = model

    def transcribe(self, audio: bytes, sample_rate: int = 16000) -> str:
        if not SARVAM_API_KEY:
            raise RuntimeError("SARVAM_API_KEY not set")

        buf = io.BytesIO()
        sf.write(buf, _bytes_to_float(audio), sample_rate, format="WAV", subtype="PCM_16")
        buf.seek(0)

        files = {"file": ("input.wav", buf, "audio/wav")}
        data = {"model": self.model}
        headers = {"api-subscription-key": SARVAM_API_KEY}

        with httpx.Client(timeout=30.0) as client:
            r = client.post(SARVAM_TRANSLATE_URL, headers=headers, files=files, data=data)
            if r.status_code >= 400:
                logger.error(f"Sarvam STT {r.status_code}: {r.text[:500]}")
                r.raise_for_status()
            body = r.json()
            transcript = body.get("transcript", "").strip()
            if not transcript:
                logger.warning(f"Sarvam STT returned empty transcript. Body: {body}")
            return transcript


def _bytes_to_float(audio: bytes) -> np.ndarray:
    """Convert raw int16 PCM bytes to a numpy float array soundfile expects."""
    return np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
