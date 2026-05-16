"""FastAPI web frontend — single-page demo wrapper around the bot.

Supports two modes:
  TEXT mode:  browser sends text turns; bot replies are text only.
  VOICE mode: browser captures mic (WAV via Web Audio), uploads to /audio_in
              which runs Sarvam STT; bot replies stream as text + base64
              Sarvam TTS audio over SSE; browser plays the audio.

Architecture
------------
The bot's Conversation class is synchronous and blocks on get_user_text().
We run each web call in a background thread, with two queues bridging the
conversation to the HTTP layer:

  user_q  ─▶ Conversation.get_user_text() pulls from this
  bot_q   ◀── Conversation.say_bot_text() puts here, SSE forwards to browser

In VOICE mode, the say_bot_text callback ALSO synthesises TTS server-side and
the SSE event carries base64 audio so the browser can play it.

Run with:
  uvicorn app.web:app --port 8000 --reload
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
from queue import Empty, Queue

import numpy as np
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel

from app.config import ROOT
from app.conversation import Conversation
from app.llm.openai_client import OpenAIClient
from app.pre_filter import CRMContext, run_prefilter


# ----------------------------------------------------------------- web call

class WebCall:
    """Wraps a Conversation in a thread, bridges turns through queues."""

    def __init__(self, ctx: CRMContext, prefilter_result, voice_mode: bool = False) -> None:
        self.ctx = ctx
        self.prefilter = prefilter_result
        self.voice_mode = voice_mode
        self.user_q: Queue = Queue()
        self.bot_q: Queue = Queue()
        self.ended = False
        self.outcome_payload: dict | None = None
        self._tts = None
        if voice_mode:
            # Lazy-import so text-mode callers don't trigger Sarvam config errors
            from app.tts.sarvam_tts import SarvamTTS
            self._tts = SarvamTTS()

        def get_user_text() -> str:
            return self.user_q.get()

        def say_bot_text(text: str) -> None:
            event = {"type": "bot", "text": text}
            if self.voice_mode and self._tts and text.strip():
                try:
                    samples, sample_rate = self._tts.synthesise(text)
                    # int16 PCM bytes → base64 (browser decodes via Web Audio)
                    pcm = (samples * 32767).astype(np.int16).tobytes()
                    event["audio_b64"] = base64.b64encode(pcm).decode("ascii")
                    event["sample_rate"] = sample_rate
                except Exception as e:
                    logger.exception(f"TTS failed: {e}")
                    event["tts_error"] = str(e)
            self.bot_q.put(event)

        self._llm = OpenAIClient()
        self.conv = Conversation(ctx, get_user_text, say_bot_text, llm=self._llm)
        self.call_id = self.conv.call_id

        # Patch the audit logger so every per-turn entry ALSO streams to the
        # browser as an "audit" SSE event — without touching the Conversation
        # class. This is what powers the "Bot internals" panel.
        self._patch_audit_logger()

        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.bot_q.put(
            {
                "type": "fsm",
                "state": "INTRO",
                "strategy": prefilter_result.strategy,
                "modifiers": prefilter_result.modifier_keys,
            }
        )

    def _patch_audit_logger(self) -> None:
        original = self.conv.audit.log_turn
        bot_q = self.bot_q

        def wrapped(**kwargs):
            original(**kwargs)
            try:
                bot_q.put(
                    {
                        "type": "audit",
                        "user_text": kwargs.get("user_text", ""),
                        "bot_text": kwargs.get("bot_text", ""),
                        "intent": kwargs.get("intent"),
                        "state_before": kwargs.get("fsm_state_before"),
                        "state_after": kwargs.get("fsm_state_after"),
                        "validator": kwargs.get("validator_result"),
                        # Layer 1 / 2 / 3 explainability — surfaced in the bot-internals panel
                        "move_played": kwargs.get("move_played"),
                        "directives_fired": kwargs.get("directives_fired") or [],
                        "scenario_inferred": kwargs.get("scenario_inferred"),
                    }
                )
            except Exception:
                pass  # never let audit instrumentation break the call

        self.conv.audit.log_turn = wrapped  # type: ignore[assignment]

    def _run(self) -> None:
        try:
            result = self.conv.run()
            cost_inr = self._estimate_cost(result)
            self.outcome_payload = {
                "outcome": result.outcome.model_dump(mode="json"),
                "duration_seconds": result.duration_seconds,
                "llm_input_tokens": result.llm_input_tokens,
                "llm_output_tokens": result.llm_output_tokens,
                "estimated_inr_per_call": cost_inr,
                "llm_latencies_ms": result.llm_latencies_ms,
            }
            self.bot_q.put({"type": "end", "payload": self.outcome_payload})
        except Exception as e:
            logger.exception("WebCall thread error")
            self.bot_q.put({"type": "error", "message": str(e)})
        finally:
            self.ended = True

    def _estimate_cost(self, result) -> float:
        from app.config import (
            LLM_INPUT_USD_PER_MTOK, LLM_OUTPUT_USD_PER_MTOK,
            STT_INR_PER_CALL, TTS_INR_PER_CALL, USD_TO_INR,
        )
        llm_usd = (
            result.llm_input_tokens * LLM_INPUT_USD_PER_MTOK
            + result.llm_output_tokens * LLM_OUTPUT_USD_PER_MTOK
        ) / 1_000_000
        return round(llm_usd * USD_TO_INR + STT_INR_PER_CALL + TTS_INR_PER_CALL, 2)

    def send_user(self, text: str) -> None:
        self.user_q.put(text)

    def end_now(self) -> None:
        self.user_q.put("")


# ----------------------------------------------------------------- app

app = FastAPI(title="Mumbai Bank Collections Voicebot — Demo")
active_calls: dict[str, WebCall] = {}

STATIC_DIR = ROOT / "app" / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/personas")
def list_personas():
    df = pd.read_csv(ROOT / "eval" / "personas.csv")
    return df.to_dict(orient="records")


class StartCallBody(BaseModel):
    persona_id: str
    voice_mode: bool = False


@app.post("/api/call/start")
def start_call(body: StartCallBody):
    df = pd.read_csv(ROOT / "eval" / "personas.csv")
    rows = df[df["persona_id"] == body.persona_id]
    if rows.empty:
        raise HTTPException(404, f"Persona {body.persona_id} not found")
    p = rows.iloc[0]
    ctx = CRMContext(
        call_id=f"web_{body.persona_id}",
        customer_id=str(p["persona_id"]),
        name=str(p["name"]),
        card_tier=str(p["card_tier"]),
        dpd=int(p["dpd"]),
        bureau_score=int(p["bureau_score"]),
        default_history=str(p["default_history"]),
        outstanding_amount=float(p["outstanding_amount"]),
        credit_limit=float(p["credit_limit"]) if p["credit_limit"] else 0.0,
        relationship_years=float(p["relationship_years"]),
        self_cure_history=str(p["self_cure_history"]).strip().lower() in {"true", "1", "yes"},
    )
    pf = run_prefilter(ctx)
    if pf.blocked:
        raise HTTPException(
            400,
            f"Pre-filter blocked this call: {pf.block_reason}. (Correct behaviour for some personas.)",
        )
    call = WebCall(ctx, pf, voice_mode=body.voice_mode)
    active_calls[call.call_id] = call
    return {
        "call_id": call.call_id,
        "voice_mode": body.voice_mode,
        "strategy": pf.strategy,
        "modifiers": pf.modifier_keys,
        "customer": {
            "name": ctx.name,
            "card_tier": ctx.card_tier,
            "dpd": ctx.dpd,
            "bureau_score": ctx.bureau_score,
            "default_history": ctx.default_history,
            "relationship_years": ctx.relationship_years,
            "self_cure_history": ctx.self_cure_history,
        },
    }


class SendBody(BaseModel):
    text: str


@app.post("/api/call/{call_id}/send")
def send_message(call_id: str, body: SendBody):
    call = active_calls.get(call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.ended:
        raise HTTPException(400, "Call already ended")
    call.send_user(body.text)
    return {"ok": True}


@app.post("/api/call/{call_id}/audio_in")
async def audio_in(call_id: str, audio: UploadFile = File(...)):
    """Browser uploads a WAV blob; we transcribe via Sarvam and queue the turn."""
    call = active_calls.get(call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    if call.ended:
        raise HTTPException(400, "Call already ended")
    from app.stt.sarvam_stt import SarvamSTT
    stt = SarvamSTT()
    raw = await audio.read()
    # The browser sends a fully-formed WAV (we encode in JS), so we strip the
    # 44-byte WAV header to get raw PCM bytes that the STT module expects.
    pcm = raw[44:] if raw[:4] == b"RIFF" else raw
    try:
        transcript = stt.transcribe(pcm)
    except Exception as e:
        logger.exception("STT failed")
        raise HTTPException(500, f"STT failed: {e}")
    call.send_user(transcript)
    return {"transcript": transcript}


@app.post("/api/call/{call_id}/end")
def end_call(call_id: str):
    call = active_calls.get(call_id)
    if call is None:
        raise HTTPException(404, "Call not found")
    call.end_now()
    return {"ok": True}


@app.get("/api/call/{call_id}/stream")
async def stream_call(call_id: str):
    call = active_calls.get(call_id)
    if call is None:
        raise HTTPException(404, "Call not found")

    async def event_generator():
        loop = asyncio.get_event_loop()
        while True:
            try:
                msg = await loop.run_in_executor(None, call.bot_q.get, True, 30)
            except Empty:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(msg)}\n\n"
            if msg.get("type") in {"end", "error"}:
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")
