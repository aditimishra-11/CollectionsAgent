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
from app.fsm import FSM, FSMContext, FSMDecision, MOVE_DIRECTIVE
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


# Intent -> scenario category. Behavioural classification of what THIS turn
# actually was — discovered mid-call by the intent classifier, NOT pre-selected.
# Surfaced in the bot-internals panel so reviewers can see the call's character
# emerging in real time. Categories mirror the eval taxonomy.
SCENARIO_BY_INTENT: dict[str, str] = {
    # hardship — real distress signals
    "mental_distress":   "hardship",
    "medical_emergency": "hardship",
    "job_loss":          "hardship",
    "business_failure":  "hardship",
    "natural_disaster":  "hardship",
    "unexpected_expense": "hardship",
    # adversarial / compliance edge cases
    "abuse":               "adversarial",
    "prompt_injection":    "adversarial",
    "off_topic":           "adversarial",
    "third_party_inquiry": "adversarial",
    "third_party_answering": "adversarial",
    "wrong_number":        "adversarial",
    "legitimacy_challenge": "adversarial",
    "do_not_call":         "adversarial",
    "refuse_current_call": "adversarial",
    "balance_inquiry":     "adversarial",   # privacy-edge
    "product_query":       "adversarial",   # off-scope deflect
    "deceased_claim":      "adversarial",
    # language handoff
    "language_preference": "language",
    # standard PTP / collection conversation
    "promise_to_pay":      "ptp",
    "partial_payment":     "ptp",
    "nach_failure":        "ptp",
    "salary_not_credited": "ptp",
    "out_of_town":         "ptp",
    "payment_failed_while_trying": "ptp",
    "already_paid":        "ptp",
    "callback_request":    "ptp",
    "waiver_request":      "ptp",
    "dispute":             "ptp",
    # neutral / probing
    "no_response":         "probing",
    "general":             "probing",
}


def _scenario_for_intent(intent: str | None) -> str | None:
    if not intent:
        return None
    return SCENARIO_BY_INTENT.get(intent, "probing")


# Map FSM decision notes to a concrete per-turn directive the LLM must honour.
# Keeps the FSM emitting symbolic tags (which the audit/logs can match on) while
# the LLM receives prose. Extend this dict as new notes are introduced.
_DIRECTIVE_BY_NOTE_PREFIX: dict[str, str] = {
    "refuse_current_call_first_strike": (
        "The customer has just refused to continue this call (frustration, not regulatory DND). "
        "DO NOT push for a payment date or amount. DO NOT ask another scheduling question. "
        "Acknowledge their frustration in ONE short sentence, then offer exactly ONE callback "
        "option: 'Would tomorrow morning, afternoon, or evening work better for a quick callback?' "
        "If they decline that too, the system will close the call — you do not need to."
    ),
    "abuse_first_strike": (
        "The customer just used hostile language. This is strike one — stay calm, do not "
        "match the tone. One short, professional reset, then return to the conversation."
    ),
}


def _directive_from_notes(notes: str) -> str | None:
    if not notes:
        return None
    for prefix, directive in _DIRECTIVE_BY_NOTE_PREFIX.items():
        if notes.startswith(prefix):
            return directive
    return None


# ----- Layer 1: move-tag extraction --------------------------------------

import re as _re_layer1

_MOVE_TAG_RE = _re_layer1.compile(r"\[MOVE:\s*([A-Z_]+)\s*\]", _re_layer1.IGNORECASE)


def _extract_move_tag(text: str) -> tuple[str | None, str]:
    """Pull the [MOVE: X] sidecar out of bot text. Returns (move_or_None,
    cleaned_text). Idempotent and tolerant of missing tags / extra spaces.
    """
    if not text:
        return (None, text)
    m = _MOVE_TAG_RE.search(text)
    if not m:
        return (None, text)
    move = m.group(1).strip().upper()
    cleaned = _MOVE_TAG_RE.sub("", text).strip()
    return (move, cleaned)


# ----- Layer 2: PTP-horizon detection ------------------------------------
# Tripwire phrases that imply a payment date too far out for our policy.
# Kept deliberately simple — false positives are recoverable (bot still
# negotiates), but false negatives let the bot confirm absurd PTPs.
import re as _re

_FAR_OUT_PATTERNS: list[_re.Pattern] = [
    # Explicit multi-month / next-to-next
    _re.compile(r"\bnext\s+to\s+next\s+(month|salary|paycheck)\b", _re.I),
    _re.compile(r"\b(two|three|four|five|six|2|3|4|5|6)\s+months?\b", _re.I),
    _re.compile(r"\bafter\s+(two|three|2|3)\s+months?\b", _re.I),
    _re.compile(r"\b(do|teen|char)\s+(mahine|maheene)\b", _re.I),
    _re.compile(r"\bagle\s+(mahine|maheene)\s+(ke\s+baad|me|salary|ko)\b", _re.I),
    _re.compile(r"\bend\s+of\s+(next|the\s+next)\s+month\b", _re.I),
    # Plain "next month" — common phrasing meaning ≥15 days out. Always
    # exceeds the strictest policies (frequent_late_strict = 7d, late
    # non-frequent = 10d). For the default 21-day policy it's borderline,
    # but the bot pushing back politely is always recoverable.
    _re.compile(r"\bnext\s+month\b", _re.I),
    # Salary-deferred PTP — "my salary will come next month, I'll pay then"
    # / "wait till my salary" / "when I get money".
    _re.compile(r"\bsalary\s+(will\s+come|comes|aayegi|aaye|hogi|hoga)\b", _re.I),
    _re.compile(r"\bwhen(ever)?\s+(my\s+)?(salary|money|paycheck|cash)\b", _re.I),
    _re.compile(r"\bwhen(ever)?\s+i\s+(get|have|earn|receive|find)\b", _re.I),
    _re.compile(r"\bafter\s+(my\s+)?(salary|paycheck)\b", _re.I),
]


def _ptp_horizon_directive(user_text: str, policy) -> str | None:
    """If the user text proposes a date well beyond policy.max_ptp_days,
    return a hard turn directive telling the bot to push back. Otherwise None.
    """
    if not user_text:
        return None
    if not any(p.search(user_text) for p in _FAR_OUT_PATTERNS):
        return None
    return (
        f"POLICY: this customer's segment caps PTP at {policy.max_ptp_days} days "
        "from today. The customer just proposed a date well beyond that. "
        "DO NOT confirm or repeat back the far-out date. Push back ONCE: "
        f"ask if they can commit to anything within {policy.max_ptp_days} days, "
        f"or take a partial of ₹{policy.partial_floor_inr:,} now and the rest later. "
        "Be firm but warm. Do not threaten or shame."
    )


# (_apply_policy_to_outcome lives on the Conversation class — see above.)


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
        self._policy = None  # SegmentPolicy | None — populated in run() after pre-filter
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

        # 3. Init FSM — policy threads through so the FSM enforces segment
        #    thresholds (abuse strikes, takeover-on-refuse) in code, not prose.
        self._policy = pf.policy
        self._fsm = FSM(FSMContext(
            card_tier=self.ctx.card_tier,
            dpd=self.ctx.dpd,
            policy=pf.policy,
        ))

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

            # Slow path → LLM generates within FSM constraints. We also build
            # a parallel `directives_fired` list — pure metadata so the bot-
            # internals panel can show WHICH deterministic guardrails ran on
            # this turn (not just the final state transition).
            directives_fired: list[str] = []
            turn_directive = _directive_from_notes(decision.notes)
            if turn_directive:
                # Layer 3 directive (refuse_current_call_first_strike, abuse_first_strike)
                directives_fired.append(f"fsm:{decision.notes.split(' ')[0]}")

            # Layer 2: PTP-horizon push-back
            if state_after in {"PTP_PROBE", "COLLECTING"} and self._policy is not None:
                horizon_directive = _ptp_horizon_directive(user_text, self._policy)
                if horizon_directive:
                    turn_directive = (
                        (turn_directive + "\n\n" + horizon_directive)
                        if turn_directive else horizon_directive
                    )
                    directives_fired.append("policy:ptp_horizon_breach")

            # Layer 1: move-ladder enforcement
            move, ladder_exhausted = self._fsm.next_move()
            if ladder_exhausted:
                turn_directive = (
                    (turn_directive + "\n\n" if turn_directive else "")
                    + "LADDER EXHAUSTED: you have already tried every move in this state. "
                    "Acknowledge briefly, offer ONE final callback ('would tomorrow work?'), "
                    "and end with [END_CALL] if they decline."
                )
                directives_fired.append("ladder:exhausted")
            elif move is not None:
                move_block = (
                    f"REQUIRED MOVE: {move}\n"
                    f"{MOVE_DIRECTIVE[move]}\n"
                    f"You MUST end your reply with the tag [MOVE: {move}] — it will be stripped "
                    "before TTS but is required so the system can record what was played. "
                    "Do not play any other move; do not repeat a move from earlier in this state."
                )
                turn_directive = (turn_directive + "\n\n" + move_block) if turn_directive else move_block
                directives_fired.append(f"ladder:next_move={move}")

            bot_text_raw = self._llm_reply(turn_directive=turn_directive)
            validation = validate_response(bot_text_raw, state_after)

            if validation.passed:
                bot_text = bot_text_raw
            else:
                bot_text = safe_fallback(state_after)
                logger.warning(
                    f"Validator blocked LLM output (violations={validation.violations}). Substituted fallback."
                )

            # Layer 1: extract the [MOVE: X] sidecar before anything else
            # touches the text. The marker is required when a move was
            # requested; if missing, log and move on (the next turn's ladder
            # will still see the move as unplayed, which is the right
            # conservative default).
            played_move, bot_text = _extract_move_tag(bot_text)
            if played_move:
                # Best-effort: trust the LLM's tag; the directive specifies the
                # required move so this should almost always match `move`.
                self._fsm.record_move(played_move)
            elif move is not None and not ladder_exhausted:
                logger.warning(
                    f"LLM did not emit [MOVE: {move}] tag in state={state_after}. "
                    "Recording the required move anyway so we don't loop."
                )
                self._fsm.record_move(move)

            # [END_CALL] guard — the FSM owns when the call ends. The LLM may
            # REQUEST a close via [END_CALL], but it's only honoured when the
            # FSM has authorised an end at any point in this call.
            # STICKY rule: once a terminal_outcome was ever set on this call
            # (e.g. on entering PTP_PROBE / WAIVER_NOTED / ALREADY_PAID), the
            # LLM may close on this OR any later turn — those states are
            # "do one thing, then end on next confirmation" by design.
            # Plus a small list of one-and-done deflect states that are
            # also allowed to end after their single move.
            llm_requested_end = END_SENTINEL in bot_text
            fsm_authorised_end = (
                decision.terminal_outcome is not None
                or self._terminal_outcome is not None   # sticky once ever set
                or decision.next_state in {
                    "TERMINAL", "REFUSAL_CLOSE", "DND_ACKNOWLEDGED", "CALLBACK_CLOSE",
                    # one-and-done deflects: bot is supposed to deflect ONCE and end
                    "OUT_OF_SCOPE_DEFLECT", "THIRD_PARTY",
                }
            )
            if llm_requested_end and not fsm_authorised_end:
                logger.warning(
                    f"LLM emitted [END_CALL] without FSM authorisation in state={state_after} "
                    f"(intent={intent.intent}). Stripping sentinel and continuing."
                )
                ends_now = False
                directives_fired.append("guard:unauthorised_end_stripped")
            else:
                ends_now = llm_requested_end
            bot_text_clean = bot_text.replace(END_SENTINEL, "").strip()
            if not validation.passed:
                directives_fired.append(f"validator:{','.join(validation.violations)}")

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
                move_played=played_move or move,
                directives_fired=directives_fired,
                scenario_inferred=_scenario_for_intent(intent.intent),
            )

            if ends_now or decision.next_state == "TERMINAL":
                ended_reason = "terminal" if decision.next_state == "TERMINAL" else "bot_ended"
                break

        self.audit.log_event("call_ended", reason=ended_reason, turns=len(self.history))

        return self._finalise(start)

    # --------------------------------------------------------- helpers

    def _llm_reply(self, turn_directive: str | None = None) -> str:
        assert self._call_parts is not None and self._fsm is not None
        system_prompt = self._prompt_builder.assemble(
            self._call_parts, self._fsm.state, turn_directive=turn_directive
        )
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
        move_played: str | None = None,
        directives_fired: list[str] | None = None,
        scenario_inferred: str | None = None,
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
            move_played=move_played,
            directives_fired=directives_fired,
            scenario_inferred=scenario_inferred,
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

        # Layer 2: stamp policy-driven next-action routing onto the outcome
        # so the CRM/orchestrator sees segment-aware handoff + SLA rather
        # than guessing from outcome type alone.
        if self._policy is not None:
            self._apply_policy_to_outcome(outcome)

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

    def _apply_policy_to_outcome(self, outcome: Outcome) -> None:
        """Stamp segment-policy-derived next-action fields onto the outcome.

        Sets ``handoff`` and ``callback_sla_hours`` per the resolved policy,
        and records ``policy_rationale`` for audit. For ``refused`` outcomes
        in segments where the policy says ``human_takeover_on_refuse``, the
        handoff field is upgraded so the CRM/orchestrator routes correctly
        — even if the FSM itself already returned ``refused``.
        """
        policy = self._policy
        if policy is None:
            return
        detail = outcome.outcome_detail
        detail.policy_rationale = policy.rationale
        if detail.callback_sla_hours is None:
            detail.callback_sla_hours = policy.callback_sla_hours
        handoff_map = {
            "promise_to_pay": "continue_bot",
            "already_paid": "continue_bot",
            "callback_request": "route_to_human",
            "human_callback_required": "route_to_human",
            "wrong_number": "pause",
            "no_answer": "continue_bot",
            "refused": "human_takeover" if policy.human_takeover_on_refuse else "continue_bot",
        }
        detail.handoff = handoff_map.get(outcome.outcome, "continue_bot")  # type: ignore[assignment]

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
