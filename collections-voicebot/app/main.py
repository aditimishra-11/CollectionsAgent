"""CLI entry point. Two modes:

    python -m app.main text         # type customer turns, see bot replies — fast iteration
    python -m app.main voice        # mic + Sarvam STT + Sarvam TTS — for demo recordings

The system prompt is loaded from prompts/v1_starter.txt (the literal broken
prompt from the assignment). v2 will swap in a different prompt and add an
FSM/validator before this layer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from loguru import logger

from app.audio.local_io import play_interruptible, record_push_to_talk, save_wav
from app.config import PROMPTS_DIR, RECORDINGS_DIR, assert_runtime_keys
from app.conversation import Conversation, ConversationConfig


def _load_prompt(name: str) -> str:
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def _run_text(customer_id: str | None, prompt_name: str) -> None:
    system_prompt = _load_prompt(prompt_name)
    config = ConversationConfig(system_prompt=system_prompt, customer_id=customer_id)

    def get_user_text() -> str:
        return input("YOU: ").strip()

    def say_bot_text(text: str) -> None:
        print(f"BOT: {text}\n")

    conv = Conversation(config, get_user_text, say_bot_text)
    result = conv.run()
    print(
        f"\n--- Call ended ---\n"
        f"call_id: {result.call_id}\n"
        f"outcome: {result.outcome.outcome}\n"
        f"detail:  {result.outcome.outcome_detail.model_dump(exclude_none=True)}\n"
        f"turns:   {result.outcome.turns}\n"
        f"audit:   {result.audit_log_path}\n"
    )


def _run_voice(customer_id: str | None, prompt_name: str) -> None:
    from app.stt.sarvam_stt import SarvamSTT
    from app.tts.sarvam_tts import SarvamTTS

    system_prompt = _load_prompt(prompt_name)
    stt = SarvamSTT()
    tts = SarvamTTS()
    config = ConversationConfig(system_prompt=system_prompt, customer_id=customer_id)

    # Accumulate the call as a single WAV for the deliverable.
    bot_samples_all: list[np.ndarray] = []
    # Shared barge-in state: if the user interrupted the bot's last utterance,
    # the next get_user_text skips the "press ENTER to start" prompt and goes
    # straight to recording — because the interrupt itself was the signal.
    state = {"interrupted": False}

    def get_user_text() -> str:
        audio = record_push_to_talk(skip_start_prompt=state["interrupted"])
        state["interrupted"] = False
        if not audio:
            return ""
        text = stt.transcribe(audio)
        print(f"YOU [stt]: {text}")
        return text

    def say_bot_text(text: str) -> None:
        print(f"BOT: {text}")
        samples, sr = tts.synthesise(text)
        bot_samples_all.append(samples)
        state["interrupted"] = play_interruptible(samples, sr)

    conv = Conversation(config, get_user_text, say_bot_text)
    result = conv.run()

    # Save the bot's combined audio for the recordings deliverable.
    if bot_samples_all:
        combined = np.concatenate(bot_samples_all)
        out_path = RECORDINGS_DIR / f"{result.call_id}.wav"
        save_wav(out_path, combined, 22050)
        logger.info(f"Bot audio saved: {out_path}")

    # Save transcript next to the WAV.
    txt_path = RECORDINGS_DIR / f"{result.call_id}.txt"
    txt_path.write_text(
        "\n".join(f"{t.role.upper()}: {t.content}" for t in result.transcript),
        encoding="utf-8",
    )
    print(
        f"\n--- Call ended ---\n"
        f"call_id: {result.call_id}\n"
        f"outcome: {result.outcome.outcome}\n"
        f"detail:  {result.outcome.outcome_detail.model_dump(exclude_none=True)}\n"
        f"turns:   {result.outcome.turns}\n"
        f"audio:   {RECORDINGS_DIR / (result.call_id + '.wav')}\n"
        f"trans.:  {txt_path}\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mumbai Bank Collections Voicebot — v1 baseline")
    parser.add_argument("mode", choices=["text", "voice"], help="Interaction mode")
    parser.add_argument("--customer-id", default=None, help="Optional customer identifier")
    parser.add_argument("--prompt", default="v1_starter.txt", help="Prompt filename in prompts/")
    args = parser.parse_args(argv)

    assert_runtime_keys()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    if args.mode == "text":
        _run_text(args.customer_id, args.prompt)
    else:
        _run_voice(args.customer_id, args.prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
