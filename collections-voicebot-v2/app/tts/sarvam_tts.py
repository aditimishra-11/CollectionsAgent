"""Sarvam Bulbul V3 — text to speech.

REST endpoint: POST https://api.sarvam.ai/text-to-speech
Sends text, gets base64-encoded WAV chunks back. We concatenate and decode.
"""

from __future__ import annotations

import base64

import httpx
import numpy as np

from app.config import SARVAM_API_KEY

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


class SarvamTTS:
    def __init__(
        self,
        speaker: str = "anushka",  # neutral female Indian English
        model: str = "bulbul:v2",
        target_language_code: str = "en-IN",
    ) -> None:
        self.speaker = speaker
        self.model = model
        self.target_language_code = target_language_code

    def synthesise(self, text: str) -> tuple[np.ndarray, int]:
        """Return (samples_float32, sample_rate)."""
        if not SARVAM_API_KEY:
            raise RuntimeError("SARVAM_API_KEY not set")

        payload = {
            "inputs": [text],
            "target_language_code": self.target_language_code,
            "speaker": self.speaker,
            "model": self.model,
            "speech_sample_rate": 22050,
            "enable_preprocessing": True,
        }
        headers = {
            "api-subscription-key": SARVAM_API_KEY,
            "Content-Type": "application/json",
        }

        with httpx.Client(timeout=60.0) as client:
            r = client.post(SARVAM_TTS_URL, headers=headers, json=payload)
            r.raise_for_status()
            body = r.json()

        audio_b64 = body["audios"][0]
        raw = base64.b64decode(audio_b64)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, 22050
