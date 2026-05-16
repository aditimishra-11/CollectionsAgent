"""v2 conversation loop — wires every brain component together.

Each turn:
    user speaks
      → intent classifier
      → FSM transition
      → if fast path: play pre-scripted close, mark terminal outcome
      → else: prompt builder assembles state-aware prompt
             → LLM generates response
             → validator scans response
             → if blocked: substitute safe fallback
             → say to customer
    repeat until FSM is terminal

The intent classifier signals. The FSM routes. The LLM speaks within
constraints the FSM provides. The validator catches LLM slips. This is
exactly the architecture doc's separation of concerns.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from app.audit import AuditLogger
from app.fsm import FSM, FSMContext, FSMDecision
from app.intent_classifier import IntentResult, classify
from app.llm.openai_client import LLMTurn, OpenAIClient
from app.outcome.extractor import OutcomeExtractor
from app.outcome.schema import Outcome, OutcomeDetail
from app.outcome.webhook import post_outcome
from app.pre_filter import CRMContext, PreFilterResult, run_prefilter
from app.prompt_builder import PromptBuilder, PromptParts
from app.validator import safe_fallback, validate_response


END_SENTINEL = "[END_CALL]"
MAX_TURNS = 16


@dataclass
class ConversationResult:
    call_id: str
    transcript: list[LLMTurn]
    outcome: Outcome
    duration_seconds: float
    audit_log_path: str
    llm_latencies_ms: list[int] = field(default_factory=list)
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    blocked: bool = False
    block_reason: str | None = None


class Conversation:
    """One v2 call. Caller supplies the IO callbacks."""

    def __init__(
        self,
        ctx: CRMContext,
        get_user_text: Callable[[], str],
        say_bot_text: Callable[[str], None],
        prompt_builder: PromptBuilder | None = None,
        llm: OpenAIClient | None = None,
    ) -> None:
        self.ctx = ctx
        self._get_user_text = get_user_text
        self._say_bot_text = say_bot_text
        self._prompt_builder = prompt_builder or PromptBuilder()
        self._llm = llm or OpenAIClient()
        self.call_id = f"call_{uuid.uuid4().hex[:10]}"
        self.history: list[LLMTurn] = []
        self.audit = AuditLogger(self.call_id)
        self._llm_latencies_ms: list[int] = []
        self._fsm: FSM | None = None
        self._call_parts: PromptParts | None = None
        self._terminal_outcome: str | None = None
        self._terminal_reason: str | None = None

    # --------------------------------------------------------- entry

    def run(self) -> ConversationResult:
        start = time.time()
        # Reset token counters so this call's totals are clean
        self._llm.reset_token_counters()

        # 1. Pre-filter
        pf = run_prefilter(self.ctx)
        self.audit.log_event(
            "call_started",
            customer_id=self.ctx.customer_id,
            card_tier=self.ctx.card_tier,
            dpd=self.ctx.dpd,
            strategy=pf.strategy,
            modifier_keys=pf.modifier_keys,
            blocked=pf.blocked,
            block_reason=pf.block_reason,
        )

        if pf.blocked:
            return self._blocked_result(pf, start)

        # 2. Compose call-level prompt parts
        self._call_parts = self._prompt_builder.build_call_parts(self.ctx, pf)

        # 3. Init FSM
        self._fsm = FSM(FSMContext(card_tier=self.ctx.card_tier, dpd=self.ctx.dpd))

        # 4. Opener (INTRO state — LLM-generated using the segment opener guide)
        opener = self._llm_reply()
        self._emit_bot_turn(opener, fsm_state_before="INTRO", fsm_state_after="INTRO")
        if END_SENTINEL in opener:
            return self._finalise(start)

        # Move out of INTRO unconditionally — opener is one shot.
        self._fsm.state = "COLLECTING"

        # 5. Main loop
        ended_reason = "max_turns"
        for _ in range(MAX_TURNS):
            user_text = self._get_user_text()
            if not user_text.strip():
                # silence — let classifier return no_response
                pass

            self.history.append(LLMTurn(role="user", content=user_text))

            intent = classify(user_text)
            state_before = self._fsm.state
            decision = self._fsm.transition(intent, user_text=user_text)
            state_after = self._fsm.state

            logger.debug(
                f"intent={intent.intent}/{intent.path} state {state_before}→{state_after} "
                f"fast={decision.is_fast_path} term={decision.terminal_outcome}"
            )

            # capture terminal outcome (latest wins — final state of call)
            if decision.terminal_outcome:
                self._terminal_outcome = decision.terminal_outcome
                self._terminal_reason = decision.terminal_reason

            # Fast path → pre-scripted close, end call
            if decision.is_fast_path:
                close_text = self._prompt_builder.get_close_template(
                    decision.close_template or ""
                )
                if close_text:
                    self._emit_bot_turn(
                        close_text,
                        fsm_state_before=state_before,
                        fsm_state_after=state_after,
                        intent=intent.intent,
                        intent_confidence=1.0,
                        validator_result={"passed": True, "violations": [], "fast_path": True},
                    )
                ended_reason = f"fast_path:{intent.intent}"
                break

            # Slow path → LLM generates within FSM constraints
            bot_text_raw = self._llm_reply()
            validation = validate_response(bot_text_raw, state_after)

            if validation.passed:
                bot_text = bot_text_raw
            else:
                bot_text = safe_fallback(state_after)
                logger.warning(
                    f"Validator blocked LLM output (violations={validation.violations}). Substituted fallback."
                )

            ends_now = END_SENTINEL in bot_text
            bot_text_clean = bot_text.replace(END_SENTINEL, "").strip()

            self._emit_bot_turn(
                bot_text_clean,
                fsm_state_before=state_before,
                fsm_state_after=state_after,
                intent=intent.intent,
                intent_confidence=1.0,
                validator_result={
                    "passed": validation.passed,
                    "violations": validation.violations,
                    "evidence": validation.evidence,
                },
            )

            if ends_now or decision.next_state == "TERMINAL":
                ended_reason = "terminal" if decision.next_state == "TERMINAL" else "bot_ended"
                break

        self.audit.log_event("call_ended", reason=ended_reason, turns=len(self.history))

        return self._finalise(start)

    # --------------------------------------------------------- helpers

    def _llm_reply(self) -> str:
        assert self._call_parts is not None and self._fsm is not None
        system_prompt = self._prompt_builder.assemble(self._call_parts, self._fsm.state)
        t0 = time.time()
        reply = self._llm.reply(
            system_prompt=system_prompt,
            history=self.history,
            max_tokens=250,
            temperature=0.55,
        )
        latency_ms = int((time.time() - t0) * 1000)
        self._llm_latencies_ms.append(latency_ms)
        logger.debug(f"LLM reply in {latency_ms}ms (state={self._fsm.state})")
        return reply

    def _emit_bot_turn(
        self,
        text: str,
        fsm_state_before: str,
        fsm_state_after: str,
        intent: str | None = None,
        intent_confidence: float | None = None,
        validator_result: dict | None = None,
    ) -> None:
        self._say_bot_text(text)
        self.history.append(LLMTurn(role="assistant", content=text))
        user_text_for_log = self.history[-2].content if len(self.history) >= 2 else ""
        self.audit.log_turn(
            user_text=user_text_for_log,
            bot_text=text,
            intent=intent,
            intent_confidence=intent_confidence,
            fsm_state_before=fsm_state_before,
            fsm_state_after=fsm_state_after,
            validator_result=validator_result,
        )

    def _finalise(self, start: float) -> ConversationResult:
        # Build outcome. Use FSM-determined terminal if available, else use the
        # post-call classifier (same as v1).
        if self._terminal_outcome:
            outcome = Outcome(
                call_id=self.call_id,
                customer_id=self.ctx.customer_id,
                outcome=self._terminal_outcome,  # type: ignore[arg-type]
                outcome_detail=OutcomeDetail(reason=self._terminal_reason),
                turns=sum(1 for t in self.history if t.role == "user"),
                audit_log_ref=str(self.audit.path),
            )
            # Enrich PTP/already_paid with extractor-derived details
            if self._terminal_outcome in {"promise_to_pay", "already_paid"}:
                extractor = OutcomeExtractor(client=self._llm)
                enriched = extractor.extract(self.call_id, self.ctx.customer_id, self.history)
                outcome.outcome_detail = enriched.outcome_detail
                outcome.transcript_summary = enriched.transcript_summary
                outcome.outcome = enriched.outcome  # trust extractor if it disagrees
        else:
            extractor = OutcomeExtractor(client=self._llm)
            outcome = extractor.extract(self.call_id, self.ctx.customer_id, self.history)
            outcome.audit_log_ref = str(self.audit.path)

        post_outcome(outcome)
        return ConversationResult(
            call_id=self.call_id,
            transcript=self.history,
            outcome=outcome,
            duration_seconds=time.time() - start,
            audit_log_path=str(self.audit.path),
            llm_latencies_ms=self._llm_latencies_ms.copy(),
            llm_input_tokens=getattr(self._llm, "total_input_tokens", 0),
            llm_output_tokens=getattr(self._llm, "total_output_tokens", 0),
        )

    def _blocked_result(self, pf: PreFilterResult, start: float) -> ConversationResult:
        outcome = Outcome(
            call_id=self.call_id,
            customer_id=self.ctx.customer_id,
            outcome="human_callback_required",
            outcome_detail=OutcomeDetail(reason=pf.block_reason),
            turns=0,
            audit_log_ref=str(self.audit.path),
        )
        post_outcome(outcome)
        self.audit.log_event("call_blocked_by_prefilter", reason=pf.block_reason)
        return ConversationResult(
            call_id=self.call_id,
            transcript=[],
            outcome=outcome,
            duration_seconds=time.time() - start,
            audit_log_path=str(self.audit.path),
            blocked=True,
            block_reason=pf.block_reason,
        )
