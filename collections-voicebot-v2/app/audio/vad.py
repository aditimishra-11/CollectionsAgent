"""Silero VAD wrapper — speech start/end detection on 16kHz int16 mono.

Why Silero (not WebRTC VAD, not Deepgram endpointing):
- Tunable silence threshold — critical for Hinglish 600-800ms mid-sentence pauses
- Same model Pipecat SmartTurnDetection uses under the hood
- Free, open, runs locally — no API call per chunk

Feed 32ms chunks (512 samples at 16kHz). The iterator returns:
  None              — most chunks, no boundary event
  {'start': float}  — speech just started (with timestamp in seconds)
  {'end':   float}  — speech ended after min_silence_duration_ms
"""

from __future__ import annotations

import numpy as np
import torch
from silero_vad import VADIterator, load_silero_vad


SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512  # 32ms at 16kHz — Silero's native chunk size


class VAD:
    """Stateful iterator. One instance per call — keeps internal speech state."""

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 800,
        speech_pad_ms: int = 100,
    ) -> None:
        self._model = load_silero_vad(onnx=True)
        self._iter = VADIterator(
            self._model,
            threshold=threshold,
            sampling_rate=SAMPLE_RATE,
            min_silence_duration_ms=min_silence_duration_ms,
            speech_pad_ms=speech_pad_ms,
        )

    def process(self, chunk_int16: np.ndarray) -> dict | None:
        """Feed one chunk. Returns event dict or None."""
        if chunk_int16.shape[0] != CHUNK_SAMPLES:
            # Last partial chunk — pad with zeros so VAD can still process
            padded = np.zeros(CHUNK_SAMPLES, dtype=np.int16)
            padded[: chunk_int16.shape[0]] = chunk_int16
            chunk_int16 = padded
        tensor = torch.from_numpy(chunk_int16.astype(np.float32) / 32768.0)
        try:
            return self._iter(tensor, return_seconds=True)
        except Exception:
            return None

    def reset(self) -> None:
        """Reset between utterances if needed."""
        self._iter.reset_states()
