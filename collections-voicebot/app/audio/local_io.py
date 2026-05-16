"""Local microphone + speaker IO for the live-voice demo.

Push-to-talk recorder + interruptible playback. Real telephony would replace
this with the Exotel/LiveKit SIP transport — out of scope for v1.

Interruption model:
- While the bot is speaking, press ANY KEY to cut it off and start recording.
- If you let the bot finish, press ENTER to start recording, ENTER again to stop.
- This mimics barge-in without needing real-time VAD.
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16000  # Sarvam STT is happy at 16k mono

_IS_WIN = sys.platform.startswith("win")
if _IS_WIN:
    import msvcrt


def _drain_keys() -> None:
    """Consume any pending keypresses so they don't leak into the next prompt."""
    if _IS_WIN:
        while msvcrt.kbhit():
            msvcrt.getch()


def play_interruptible(samples: np.ndarray, sample_rate: int) -> bool:
    """Play audio. Return True if the user interrupted, False if it finished."""
    _drain_keys()
    sd.play(samples, sample_rate)
    print("[bot speaking — press any key to interrupt]", flush=True)

    interrupted = False
    if _IS_WIN:
        while sd.get_stream().active:
            if msvcrt.kbhit():
                msvcrt.getch()
                sd.stop()
                interrupted = True
                print("[interrupted — your turn]", flush=True)
                break
            time.sleep(0.05)
    else:
        # Unix fallback: just block (no portable cross-platform non-blocking stdin).
        sd.wait()

    _drain_keys()
    return interrupted


def record_push_to_talk(skip_start_prompt: bool = False) -> bytes:
    """Records until the user hits ENTER. Returns int16 PCM bytes.

    If skip_start_prompt is True, starts recording immediately — used when the
    user already signalled "I want to talk" by interrupting the bot.
    """
    if not skip_start_prompt:
        print("[mic] Press ENTER to start speaking…", end="", flush=True)
        sys.stdin.readline()
    print("[mic] Recording — press ENTER to stop.", flush=True)

    q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"[mic] {status}", file=sys.stderr)
        q.put(indata.copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback):
        sys.stdin.readline()

    chunks = []
    while not q.empty():
        chunks.append(q.get())
    if not chunks:
        return b""
    audio = np.concatenate(chunks, axis=0).flatten()
    return audio.tobytes()


def save_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), samples, sample_rate, subtype="PCM_16")
