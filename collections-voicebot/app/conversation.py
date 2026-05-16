"""The v1 conversation loop.

v1 is intentionally bare:
- Single system prompt (the literal starter from the assignment PDF — broken on purpose)
- No FSM, no intent classifier, no segment routing, no response validator
- The LLM handles everything, including deciding when to end the call

We end the loop on three conditions:
1. The LLM produces text containing the sentinel "[END_CALL]"
2. The user/customer says "bye", "goodbye", "thanks bye" or similar
3. Turn budget exhausted (default 20 turns — calls should close in 2-3 min)

Outcome extraction runs after the loop ends.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from app.audit import AuditLogger
from app.llm.openai_client import LLMTurn, OpenAIClient
from app.outcome.extractor import OutcomeExtractor
from app.outcome.schema import Outcome
from app.outcome.webhook import post_outcome

END_SENTINEL = "[END_CALL]"
# Whole-word match, anywhere in the user's utterance, punctuation-insensitive.
USER_END_WORDS = {"bye", "goodbye", "byebye", "alvida"}


def _user_wants_to_end(text: str) -> bool:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return any(w in USER_END_WORDS for w in words)


@dataclass
class ConversationConfig:
    system_prompt: str
    max_turns: int = 20
    customer_id: str | None = None
    opening_line: str | None = (
        "Hello, this is calling from Mumbai Bank regarding your credit card account. "
        "Is this a good time to talk?"
    )


@dataclass
class ConversationResult:
    call_id: str
    transcript: list[LLMTurn]
    outcome: Outcome
    duration_seconds: float
    audit_log_path: str


class Conversation:
    """Holds state for a single call. Caller drives the loop via .run()."""

    def __init__(
        self,
        config: ConversationConfig,
        get_user_text: Callable[[], str],
        say_bot_text: Callable[[str], None],
        llm: OpenAIClient | None = None,
    ) -> None:
        self.config = config
        self._get_user_text = get_user_text
        self._say_bot_text = say_bot_text
        self._llm = llm or OpenAIClient()
        self.call_id = f"call_{uuid.uuid4().hex[:10]}"
        self.history: list[LLMTurn] = []
        self.audit = AuditLogger(self.call_id)

    def run(self) -> ConversationResult:
        start = time.time()
        self.audit.log_event(
            "call_started",
            customer_id=self.config.customer_id,
            system_prompt_chars=len(self.config.system_prompt),
        )

        # Opening line — synthesised or printed by the caller's say_bot_text.
        opening = self.config.opening_line
        if opening:
            self._say_bot_text(opening)
            self.history.append(LLMTurn(role="assistant", content=opening))

        ended_reason = "max_turns"
        for turn_idx in range(self.config.max_turns):
            user_text = self._get_user_text()
            if not user_text.strip():
                logger.info("Empty user turn — treating as silence, ending call.")
                ended_reason = "user_silence"
                break

            self.history.append(LLMTurn(role="user", content=user_text))

            if _user_wants_to_end(user_text):
                # Let the bot give a closing line.
                bot_text = self._llm_reply()
                self._say_bot_text(bot_text)
                self.history.append(LLMTurn(role="assistant", content=bot_text))
                self.audit.log_turn(user_text=user_text, bot_text=bot_text)
                ended_reason = "user_ended"
                break

            bot_text = self._llm_reply()
            ends_now = END_SENTINEL in bot_text
            bot_text_clean = bot_text.replace(END_SENTINEL, "").strip()

            self._say_bot_text(bot_text_clean)
            self.history.append(LLMTurn(role="assistant", content=bot_text_clean))
            self.audit.log_turn(user_text=user_text, bot_text=bot_text_clean)

            if ends_now:
                ended_reason = "bot_ended"
                break

        self.audit.log_event("call_ended", reason=ended_reason, turns=len(self.history))

        # Post-call: extract outcome and post to webhook.
        extractor = OutcomeExtractor(client=self._llm)
        outcome = extractor.extract(self.call_id, self.config.customer_id, self.history)
        outcome.audit_log_ref = str(self.audit.path)
        post_outcome(outcome)

        return ConversationResult(
            call_id=self.call_id,
            transcript=self.history,
            outcome=outcome,
            duration_seconds=time.time() - start,
            audit_log_path=str(self.audit.path),
        )

    def _llm_reply(self) -> str:
        t0 = time.time()
        reply = self._llm.reply(
            system_prompt=self.config.system_prompt,
            history=self.history,
            max_tokens=250,
            temperature=0.4,
        )
        logger.debug(f"LLM reply in {int((time.time() - t0) * 1000)}ms")
        return reply
