"""Continuous streaming mic + Silero VAD + playback with audio barge-in.

Echo cancellation
=================
A laptop with speakers (no headphones) creates an echo loop: bot voice goes
out the speaker, travels through air, the mic picks it up, VAD treats it as
"customer speech", STT transcribes it, bot replies to itself, repeat forever.

We solve this with two complementary layers:

1. **NLMS echo cancellation (real AEC)**: while the bot is playing, we keep a
   rolling reference of what the speaker is emitting, run the mic input
   through an adaptive filter that subtracts the echo, and feed the cleaned
   signal to VAD.

2. **Safety heuristics**: even with AEC, brief residual echo can fool VAD.
   Three independent guards:
     - Time gate: ignore VAD events in the first 400 ms after bot starts
       playing (AEC needs a few hundred ms to converge)
     - Sustained-speech requirement: a barge-in is only confirmed after the
       customer has spoken for ≥500 ms continuously (echo is fragmentary)
     - RMS floor: chunks below a minimum volume don't count (quiet residual
       echo is filtered out)

Real telephony (Exotel + LiveKit SIP) handles AEC at the RTP layer.
This module replicates that locally for the demo.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf
from loguru import logger

from app.audio.aec import NLMSEchoCanceller
from app.audio.vad import VAD, CHUNK_SAMPLES, SAMPLE_RATE
from app.config import (
    AEC_ENABLED,
    AEC_FILTER_LENGTH,
    AEC_MU,
    BARGE_IN_RMS_FLOOR,
    MIN_BARGE_IN_DURATION_MS,
    PLAYBACK_INITIAL_MUTE_MS,
)


def _resample(samples: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear-interpolation resample. Good enough for AEC reference signal —
    we don't need pristine quality, just rough time alignment of envelope.
    """
    if src_sr == dst_sr:
        return samples
    ratio = dst_sr / src_sr
    n_out = int(round(len(samples) * ratio))
    x_old = np.arange(len(samples))
    x_new = np.linspace(0, len(samples) - 1, n_out)
    return np.interp(x_new, x_old, samples).astype(samples.dtype)


class StreamingAudioIO:
    def __init__(
        self,
        vad_threshold: float = 0.5,
        min_silence_duration_ms: int = 800,
        utterance_timeout_s: float = 15.0,
        # Barge-in safety (defaults from config — env-tunable)
        playback_initial_mute_ms: int = PLAYBACK_INITIAL_MUTE_MS,
        min_barge_in_duration_ms: int = MIN_BARGE_IN_DURATION_MS,
        barge_in_rms_floor: float = BARGE_IN_RMS_FLOOR,
        # AEC (defaults from config — env-tunable)
        aec_filter_length: int = AEC_FILTER_LENGTH,
        aec_mu: float = AEC_MU,
        enable_aec: bool = AEC_ENABLED,
    ) -> None:
        self._vad = VAD(
            threshold=vad_threshold,
            min_silence_duration_ms=min_silence_duration_ms,
        )
        self._utterance_timeout = utterance_timeout_s

        # Echo cancellation
        self._enable_aec = enable_aec
        self._aec = NLMSEchoCanceller(filter_length=aec_filter_length, mu=aec_mu)
        # Reference buffer holds the most recent ~500 ms of bot playback at
        # the mic's sample rate. We pull from the tail when a mic chunk arrives.
        self._ref_buffer = deque(maxlen=int(SAMPLE_RATE * 0.5))
        self._ref_lock = threading.Lock()

        # Barge-in safety
        self._playback_initial_mute_ms = playback_initial_mute_ms
        self._min_barge_in_duration_ms = min_barge_in_duration_ms
        self._barge_in_rms_floor = barge_in_rms_floor

        # Threading
        self._utterance_q: queue.Queue[bytes] = queue.Queue()
        self._stop = threading.Event()
        self._bot_speaking = threading.Event()
        self._barge_in = threading.Event()
        self._playback_started_at = 0.0
        self._barge_in_tracking_start: float | None = None

        self._mic_stream: sd.InputStream | None = None
        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._mic_thread.start()
        time.sleep(0.3)  # let model + stream warm up
        logger.info(
            f"[audio] StreamingAudioIO ready — Silero VAD active, AEC={'on' if enable_aec else 'off'}, mic open"
        )

    # ---------------------------------------------------------------- mic loop

    def _mic_loop(self) -> None:
        speech_chunks: list[np.ndarray] = []
        in_speech = False

        def callback(indata, frames, time_info, status):
            nonlocal in_speech, speech_chunks
            if status:
                logger.debug(f"[mic] status: {status}")
            chunk = indata.flatten().astype(np.int16)

            # ---- Layer 1: echo cancellation ----
            if self._enable_aec and self._bot_speaking.is_set():
                ref_chunk = self._pull_reference(len(chunk))
                cleaned = self._aec.process_chunk(chunk, ref_chunk)
                if self._aec.divergence_check():
                    logger.debug("[aec] filter diverged, resetting")
                    self._aec.reset()
            else:
                cleaned = chunk

            # ---- VAD on cleaned signal ----
            event = self._vad.process(cleaned)

            # ---- Layer 2: time gate during playback ----
            playback_active = self._bot_speaking.is_set()
            if playback_active:
                ms_since_playback = (time.time() - self._playback_started_at) * 1000.0
                if ms_since_playback < self._playback_initial_mute_ms:
                    # Drop everything in the initial mute window — AEC is converging
                    return

            # ---- Process VAD events ----
            if event is None:
                if in_speech:
                    speech_chunks.append(cleaned)
                # Sustained-speech check during playback
                if playback_active and self._barge_in_tracking_start is not None:
                    rms = float(np.sqrt(np.mean(cleaned.astype(np.float32) ** 2)))
                    elapsed_ms = (time.time() - self._barge_in_tracking_start) * 1000.0
                    if rms < self._barge_in_rms_floor:
                        # Quiet — probably residual echo, not real speech.
                        # Reset tracking; will start over if VAD fires again.
                        if elapsed_ms < 100:
                            self._barge_in_tracking_start = None
                    elif elapsed_ms >= self._min_barge_in_duration_ms:
                        # Confirmed: sustained, loud speech during playback → barge-in
                        sd.stop()
                        self._barge_in.set()
                        self._barge_in_tracking_start = None
                return

            if "start" in event:
                in_speech = True
                speech_chunks = [cleaned]
                if playback_active:
                    # Start the barge-in confirmation timer
                    self._barge_in_tracking_start = time.time()
            elif "end" in event:
                in_speech = False
                self._barge_in_tracking_start = None  # cancel pending barge-in
                if speech_chunks:
                    combined = np.concatenate(speech_chunks)
                    # If we're still in playback, the speech likely wasn't real
                    # — only emit if sustained AND loud (we already gated start)
                    if not playback_active:
                        self._utterance_q.put(combined.tobytes())
                    speech_chunks = []

        try:
            self._mic_stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=CHUNK_SAMPLES,
                callback=callback,
            )
            self._mic_stream.start()
            while not self._stop.is_set():
                sd.sleep(50)
        except Exception:
            logger.exception("[mic] stream failed")
        finally:
            if self._mic_stream is not None:
                try:
                    self._mic_stream.stop()
                    self._mic_stream.close()
                except Exception:
                    pass

    # ------------------------------------------------------- reference signal

    def _push_reference(self, samples: np.ndarray, sample_rate: int) -> None:
        """Called by play_bot() to feed the AEC reference buffer."""
        if not self._enable_aec:
            return
        # Resample to mic rate
        resampled = _resample(samples, sample_rate, SAMPLE_RATE)
        # Convert to int16 if needed
        if resampled.dtype != np.int16:
            resampled = (resampled * 32767).astype(np.int16) if resampled.dtype.kind == "f" else resampled.astype(np.int16)
        with self._ref_lock:
            self._ref_buffer.extend(resampled.tolist())

    def _pull_reference(self, n_samples: int) -> np.ndarray:
        """Pull the most recent n_samples from the reference buffer.

        We use the tail of the buffer (most recent samples) because the
        speaker is playing the latest samples right now. NLMS handles fine
        time alignment via the filter coefficients.
        """
        with self._ref_lock:
            if len(self._ref_buffer) < n_samples:
                # Not enough reference yet — pad with zeros (no echo to cancel)
                ref = np.zeros(n_samples, dtype=np.int16)
                if self._ref_buffer:
                    avail = np.array(list(self._ref_buffer), dtype=np.int16)
                    ref[-len(avail):] = avail
                return ref
            return np.array(list(self._ref_buffer)[-n_samples:], dtype=np.int16)

    # ------------------------------------------------------------- public API

    def listen_for_utterance(self) -> bytes:
        """Block until VAD completes the next utterance. Returns int16 PCM bytes."""
        try:
            return self._utterance_q.get(timeout=self._utterance_timeout)
        except queue.Empty:
            return b""

    def play_bot(self, samples: np.ndarray, sample_rate: int) -> bool:
        """Play bot audio. Returns True if interrupted by customer voice."""
        # Drain any leftover utterances from before this turn
        while not self._utterance_q.empty():
            try:
                self._utterance_q.get_nowait()
            except queue.Empty:
                break

        # Reset AEC filter at start of every bot turn — different prosody/levels
        self._aec.reset()

        # Push reference into the AEC buffer (resampled to mic rate)
        self._push_reference(samples, sample_rate)

        self._barge_in.clear()
        self._barge_in_tracking_start = None
        self._playback_started_at = time.time()
        self._bot_speaking.set()

        sd.play(samples, sample_rate)
        try:
            while sd.get_stream().active:
                if self._barge_in.is_set():
                    break
                time.sleep(0.02)
        finally:
            self._bot_speaking.clear()
            sd.stop()
            self._barge_in_tracking_start = None

        return self._barge_in.is_set()

    def close(self) -> None:
        self._stop.set()
        self._mic_thread.join(timeout=2.0)


def save_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")
