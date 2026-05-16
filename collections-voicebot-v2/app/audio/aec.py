"""Acoustic Echo Cancellation — NLMS adaptive filter.

Why this exists
===============
On a laptop with speakers (no headphones), the bot's voice plays through the
speaker, travels through air, and reaches the mic. The mic captures the bot's
own voice. Without cancellation, VAD treats that as "customer speech", STT
transcribes it, and the bot replies to itself in an infinite loop.

Real telephony (Exotel + LiveKit SIP) solves this at the RTP layer with
hardware/network AEC. Locally we have to do it in software.

How NLMS works
==============
We know EXACTLY what the bot is playing (we have those samples). NLMS learns,
sample by sample, the impulse response from the speaker through air to the
mic — modelling room reverb, speaker frequency response, and mic colouration.
Once learned, we predict what the mic *should* be hearing from the bot, and
subtract it from the raw mic signal. The residual = whatever isn't the bot =
the customer.

Reference
=========
S. Haykin, "Adaptive Filter Theory", 5th ed. Chapter 6 (NLMS algorithm).
The textbook update rule:

    e[n] = d[n] - w[n]ᵀ · x[n]               # error / cleaned signal
    w[n+1] = w[n] + (μ · e[n] · x[n]) / (||x[n]||² + ε)

where d[n] is the mic sample, x[n] is the reference (what the speaker is
playing right now, after delay alignment), w[n] is the adaptive filter, and
μ is the step size (typically 0.05–0.3).
"""

from __future__ import annotations

import numpy as np


class NLMSEchoCanceller:
    """Normalised-LMS echo canceller. Per-chunk processing.

    Args:
        filter_length: number of past reference samples the filter remembers.
            At 16 kHz, 2048 samples ≈ 128 ms — enough to model typical room
            reverb on a laptop. Longer = better in reverberant rooms, slower.
        mu: step size. 0.1 is a safe default. Higher = faster convergence
            but more instability.
        eps: regularisation term to prevent divide-by-zero when reference
            is silent.
    """

    def __init__(
        self,
        filter_length: int = 2048,
        mu: float = 0.1,
        eps: float = 1e-6,
    ) -> None:
        self.filter_length = filter_length
        self.mu = mu
        self.eps = eps
        self.w = np.zeros(filter_length, dtype=np.float32)
        self.ref_buffer = np.zeros(filter_length, dtype=np.float32)

    def reset(self) -> None:
        """Reset filter coefficients — call between calls or after long silences."""
        self.w[:] = 0.0
        self.ref_buffer[:] = 0.0

    def process_chunk(
        self,
        mic_chunk: np.ndarray,
        ref_chunk: np.ndarray,
    ) -> np.ndarray:
        """Process one chunk of audio. Returns the echo-cancelled mic chunk.

        Both arrays must be the same length (typically 512 samples = 32 ms at 16 kHz),
        same sample rate, and time-aligned (reference is what the speaker is
        playing during the same window the mic is capturing).

        Inputs are int16 PCM; output is also int16 PCM.
        """
        mic_f = mic_chunk.astype(np.float32)
        ref_f = ref_chunk.astype(np.float32)

        cleaned = np.zeros_like(mic_f)
        for n in range(len(mic_f)):
            # Shift reference buffer left, prepend new sample at index 0
            # (the newest sample is at position 0; w[0] models the direct path)
            self.ref_buffer = np.roll(self.ref_buffer, 1)
            self.ref_buffer[0] = ref_f[n]

            # Predicted echo at this sample
            y = np.dot(self.w, self.ref_buffer)

            # Error / cleaned signal
            e = mic_f[n] - y
            cleaned[n] = e

            # NLMS coefficient update
            norm = np.dot(self.ref_buffer, self.ref_buffer) + self.eps
            self.w += (self.mu * e * self.ref_buffer) / norm

        # Clip back to int16 range
        cleaned = np.clip(cleaned, -32768, 32767)
        return cleaned.astype(np.int16)

    def divergence_check(self) -> bool:
        """Return True if the filter has diverged (coefficients exploded).

        Sign that we should reset — usually caused by very sudden changes in
        room acoustics or sustained double-talk.
        """
        return bool(np.max(np.abs(self.w)) > 100.0)
