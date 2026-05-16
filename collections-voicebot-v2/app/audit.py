"""Per-turn audit logger. One JSONL file per call.

Architecture doc requires: turn number, timestamp, STT transcript, intent
+ confidence, FSM state before/after, full LLM response, validator result.
v1 has no FSM/validator/intent classifier — those fields are written as null
so v2 can extend the same schema without breaking parsers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR


class AuditLogger:
    def __init__(self, call_id: str) -> None:
        self.call_id = call_id
        self.path: Path = LOGS_DIR / f"{call_id}.jsonl"
        self._turn = 0

    def log_turn(
        self,
        *,
        user_text: str,
        bot_text: str,
        intent: str | None = None,
        intent_confidence: float | None = None,
        fsm_state_before: str | None = None,
        fsm_state_after: str | None = None,
        validator_result: dict[str, Any] | None = None,
        stt_latency_ms: int | None = None,
        llm_latency_ms: int | None = None,
        tts_latency_ms: int | None = None,
    ) -> None:
        self._turn += 1
        record = {
            "call_id": self.call_id,
            "turn": self._turn,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_text": user_text,
            "bot_text": bot_text,
            "intent": intent,
            "intent_confidence": intent_confidence,
            "fsm_state_before": fsm_state_before,
            "fsm_state_after": fsm_state_after,
            "validator_result": validator_result,
            "stt_latency_ms": stt_latency_ms,
            "llm_latency_ms": llm_latency_ms,
            "tts_latency_ms": tts_latency_ms,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_event(self, event: str, **fields: Any) -> None:
        record = {
            "call_id": self.call_id,
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
