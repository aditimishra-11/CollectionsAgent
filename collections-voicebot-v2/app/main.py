"""v2 CLI entry point.

Two modes:
    python -m app.main text  --persona P01     # type customer turns
    python -m app.main voice --persona P01     # mic + Sarvam STT + Sarvam TTS

The persona ID maps to a row in eval/personas.csv — the CRM payload that
drives the pre-filter, the strategy, and the modifiers. v2 cannot run
without a persona (unlike v1, where context was implicit).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from app.audio.streaming_io import StreamingAudioIO, save_wav
from app.config import RECORDINGS_DIR, ROOT, assert_runtime_keys
from app.conversation import Conversation
from app.pre_filter import CRMContext


def _load_persona(persona_id: str) -> CRMContext:
    df = pd.read_csv(ROOT / "eval" / "personas.csv")
    row = df[df["persona_id"] == persona_id]
    if row.empty:
        raise SystemExit(f"persona {persona_id} not found in eval/personas.csv")
    r = row.iloc[0]
    return CRMContext(
        call_id=f"manual_{persona_id}",
        customer_id=persona_id,
        name=str(r["name"]),
        card_tier=str(r["card_tier"]),
        dpd=int(r["dpd"]),
        bureau_score=int(r["bureau_score"]),
        default_history=str(r["default_history"]),
        outstanding_amount=float(r["outstanding_amount"]),
        credit_limit=float(r["credit_limit"]) if r["credit_limit"] else 0.0,
        relationship_years=float(r["relationship_years"]),
        self_cure_history=bool(r["self_cure_history"]) if not isinstance(r["self_cure_history"], str) else str(r["self_cure_history"]).lower() == "true",
    )


def _run_text(ctx: CRMContext) -> None:
    def get_user_text() -> str:
        return input("YOU: ").strip()

    def say_bot_text(text: str) -> None:
        print(f"BOT: {text}\n")

    conv = Conversation(ctx, get_user_text, say_bot_text)
    result = conv.run()
    _print_summary(result)


def _run_voice(ctx: CRMContext) -> None:
    from app.stt.sarvam_stt import SarvamSTT
    from app.tts.sarvam_tts import SarvamTTS

    stt = SarvamSTT()
    tts = SarvamTTS()
    io = StreamingAudioIO()

    bot_samples_all: list[np.ndarray] = []
    print("\n[demo] Use headphones to avoid the bot's voice triggering false barge-in.\n")

    def get_user_text() -> str:
        audio = io.listen_for_utterance()
        if not audio:
            print("YOU [silence]")
            return ""
        text = stt.transcribe(audio)
        print(f"YOU [stt]: {text}")
        return text

    def say_bot_text(text: str) -> None:
        print(f"BOT: {text}")
        samples, sr = tts.synthesise(text)
        bot_samples_all.append(samples)
        interrupted = io.play_bot(samples, sr)
        if interrupted:
            print("[interrupted — listening]")

    try:
        conv = Conversation(ctx, get_user_text, say_bot_text)
        result = conv.run()
    finally:
        io.close()

    if bot_samples_all:
        combined = np.concatenate(bot_samples_all)
        wav_path = RECORDINGS_DIR / f"{result.call_id}.wav"
        save_wav(wav_path, combined, 22050)
        logger.info(f"Bot audio saved: {wav_path}")

    txt_path = RECORDINGS_DIR / f"{result.call_id}.txt"
    txt_path.write_text(
        "\n".join(f"{t.role.upper()}: {t.content}" for t in result.transcript),
        encoding="utf-8",
    )
    _print_summary(result, audio_path=RECORDINGS_DIR / f"{result.call_id}.wav", txt_path=txt_path)


def _print_summary(result, audio_path: Path | None = None, txt_path: Path | None = None) -> None:
    print(
        f"\n--- Call ended ---\n"
        f"call_id: {result.call_id}\n"
        f"outcome: {result.outcome.outcome}\n"
        f"detail:  {result.outcome.outcome_detail.model_dump(exclude_none=True)}\n"
        f"turns:   {result.outcome.turns}\n"
        f"audit:   {result.audit_log_path}\n"
        + (f"audio:   {audio_path}\n" if audio_path else "")
        + (f"trans.:  {txt_path}\n" if txt_path else "")
        + (f"BLOCKED by pre-filter: {result.block_reason}\n" if result.blocked else "")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mumbai Bank Collections Voicebot — v2")
    parser.add_argument("mode", choices=["text", "voice"], help="Interaction mode")
    parser.add_argument(
        "--persona",
        required=True,
        help="Persona ID from eval/personas.csv (e.g., P01). Defines CRM context.",
    )
    args = parser.parse_args(argv)

    assert_runtime_keys()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    ctx = _load_persona(args.persona)
    logger.info(
        f"Loaded persona {ctx.customer_id}: {ctx.name} ({ctx.card_tier}, "
        f"DPD {ctx.dpd}, bureau {ctx.bureau_score}, {ctx.default_history})"
    )

    if args.mode == "text":
        _run_text(ctx)
    else:
        _run_voice(ctx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
