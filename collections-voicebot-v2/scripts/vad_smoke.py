"""Pure VAD smoke test — no STT, no TTS, no LLM, no cost.

Verifies:
  - Microphone permissions / device access
  - Silero VAD model loads
  - Speech start/end events fire correctly
  - Continuous stream works on Windows

Speak short phrases with pauses. You should see [speech_start] and [speech_end]
events as you speak. Press Ctrl+C to stop.
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

# Add parent dir to sys.path so we can import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd

from app.audio.vad import VAD, CHUNK_SAMPLES, SAMPLE_RATE


def main() -> None:
    print("Loading Silero VAD model… ", end="", flush=True)
    t0 = time.time()
    vad = VAD(threshold=0.5, min_silence_duration_ms=800)
    print(f"loaded in {int((time.time() - t0) * 1000)}ms")

    print(f"Sample rate: {SAMPLE_RATE} Hz, chunk size: {CHUNK_SAMPLES} samples")
    print(f"Default input device: {sd.query_devices(sd.default.device[0])['name']}")
    print("\nSpeak into the mic. You should see speech_start / speech_end events.")
    print("Press Ctrl+C to stop.\n")

    speech_chunks_count = 0
    in_speech = False
    start_time = None

    def callback(indata, frames, time_info, status):
        nonlocal speech_chunks_count, in_speech, start_time
        if status:
            print(f"[status] {status}", file=sys.stderr)
        chunk = indata.flatten().astype(np.int16)
        event = vad.process(chunk)

        if in_speech:
            speech_chunks_count += 1

        if event is None:
            return

        if "start" in event:
            in_speech = True
            speech_chunks_count = 0
            start_time = time.time()
            print(f"[{time.strftime('%H:%M:%S')}] [speech_start] at t={event['start']:.2f}s", flush=True)
        elif "end" in event:
            duration_ms = int((time.time() - start_time) * 1000) if start_time else 0
            seconds_of_audio = speech_chunks_count * CHUNK_SAMPLES / SAMPLE_RATE
            in_speech = False
            print(
                f"[{time.strftime('%H:%M:%S')}] [speech_end]   "
                f"speech_duration={seconds_of_audio:.2f}s wall_time={duration_ms}ms",
                flush=True,
            )

    try:
        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=CHUNK_SAMPLES,
            callback=callback,
        ):
            while True:
                sd.sleep(100)
    except KeyboardInterrupt:
        print("\n\nStopping. VAD smoke test complete.")


if __name__ == "__main__":
    main()
