"""FSM / policy layer — the compliance brain of v2.

Receives intent + call context, decides:
- The next FSM state
- Whether this turn is fast path (pre-scripted close, no LLM) or slow path (LLM generates)
- The terminal outcome when the call ends

The FSM owns ALL routing. The intent classifier only signals. This is the
single most important separation in the architecture: compliance routing
is code, not prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING

from app.intent_classifier import FAST_PATH_INTENTS, IntentResult

if TYPE_CHECKING:
    from app.policy import SegmentPolicy

State = Literal[
    "INTRO",
    "COLLECTING",
    "PTP_PROBE",
    "ALREADY_PAID",
    "WAIVER_NOTED",
    "BALANCE_GUARD",
    "PRODUCT_DEFLECT",
    "OUT_OF_SCOPE_DEFLECT",  # new: prompt injection, off-topic, role-break, third-party
    "HARDSHIP_PROBE",
    "THIRD_PARTY",
    "DND_ACKNOWLEDGED",         # regulatory DND — explicit "do not call" — locks future contact
    "REFUSAL_CLOSE",            # in-the-moment refusal — closes this call, future contact allowed
    "LEGITIMACY_REASSURE",
    "CALLBACK_CLOSE",  # fast path — no LLM, plays a pre-scripted close
    "TERMINAL",  # call has ended
]

# Maps fast-path intent → which pre-scripted close template to play
FAST_PATH_CLOSE: dict[str, str] = {
    "mental_distress": "mental_distress",
    "medical_emergency": "medical_emergency",
    "job_loss": "job_loss",
    "business_failure": "business_failure",
    "natural_disaster": "natural_disaster",
    "abuse": "abuse",
    "deceased_claim": "deceased",
    "language_preference": "language_callback",
}

# Maps fast-path intent → terminal outcome reason field
FAST_PATH_OUTCOME_REASON: dict[str, str] = {
    "mental_distress": "mental_distress",
    "medical_emergency": "medical_emergency",
    "job_loss": "job_loss",
    "business_failure": "business_failure",
    "natural_disaster": "natural_disaster",
    "abuse": "abuse",
    "deceased_claim": "deceased",
    "language_preference": "language_callback",
}


@dataclass
class FSMContext:
    """Per-call mutable state that the FSM uses for routing decisions."""

    card_tier: str
    dpd: int
    turn_count: int = 0
    abuse_strikes: int = 0
    hardship_probed_already: bool = False
    # Tracks in-call refusal pressure WITHOUT promoting to regulatory DND.
    # First strike: stay slow-path, let the LLM acknowledge + offer ONE callback.
    # Second strike: terminal REFUSAL_CLOSE (outcome=refused, reason=refused_current_call).
    refuse_current_call_strikes: int = 0
    # Deterministic segment policy — drives abuse-strike threshold, takeover
    # routing, and (in Layer 1) the move ladder. None only in legacy tests.
    policy: "SegmentPolicy | None" = None


@dataclass
class FSMDecision:
    next_state: State
    is_fast_path: bool
    close_template: str | None = None  # set only when state == CALLBACK_CLOSE
    terminal_outcome: str | None = None  # set when ending the call
    terminal_reason: str | None = None
    notes: str = ""


class FSM:
    """Stateful FSM for a single call. Not thread-safe, one-per-call."""

    def __init__(self, context: FSMContext) -> None:
        self.context = context
        self.state: State = "INTRO"

    # ----- main transition ---------------------------------------------------

    def transition(self, intent_result: IntentResult, user_text: str = "") -> FSMDecision:
        """Return the routing decision for this turn."""
        self.context.turn_count += 1
        intent = intent_result.intent

        # === Fast path resolution ===
        if intent in FAST_PATH_INTENTS:
            return self._handle_fast_path(intent, last_user_text=user_text)

        # === Slow path: state changes based on intent ===
        if intent == "do_not_call":
            self.state = "DND_ACKNOWLEDGED"
            return FSMDecision(
                next_state="DND_ACKNOWLEDGED",
                is_fast_path=False,
                terminal_outcome="refused",
                terminal_reason="dnd",
            )

        if intent == "refuse_current_call":
            # Two-strike: first frustrated refusal gets ONE callback offer,
            # second closes the call without burning future-contact rights.
            self.context.refuse_current_call_strikes += 1
            if self.context.refuse_current_call_strikes >= 2:
                self.state = "REFUSAL_CLOSE"
                # Policy-driven: high-risk segments escalate to human handoff
                # instead of letting the bot retry next cycle.
                if self.context.policy and self.context.policy.human_takeover_on_refuse:
                    return FSMDecision(
                        next_state="REFUSAL_CLOSE",
                        is_fast_path=False,
                        terminal_outcome="human_callback_required",
                        terminal_reason="refused_current_call_high_risk",
                        notes="refuse_current_call_second_strike — escalating to human (policy)",
                    )
                return FSMDecision(
                    next_state="REFUSAL_CLOSE",
                    is_fast_path=False,
                    terminal_outcome="refused",
                    terminal_reason="refused_current_call",
                    notes="refuse_current_call_second_strike — closing without DND lock",
                )
            # First strike — stay in current conversational state but signal
            # the LLM via the next-turn prompt that it should offer one callback
            # and stop pushing payment. (Move-ladder enforcement comes in Layer 1.)
            return FSMDecision(
                next_state=self.state,
                is_fast_path=False,
                notes="refuse_current_call_first_strike — offer callback, no payment push",
            )

        if intent == "wrong_number":
            self.state = "TERMINAL"
            return FSMDecision(
                next_state="TERMINAL",
                is_fast_path=False,
                terminal_outcome="wrong_number",
            )

        if intent == "third_party_answering":
            self.state = "THIRD_PARTY"
            return FSMDecision(next_state="THIRD_PARTY", is_fast_path=False)

        if intent == "legitimacy_challenge":
            self.state = "LEGITIMACY_REASSURE"
            return FSMDecision(next_state="LEGITIMACY_REASSURE", is_fast_path=False)

        if intent == "balance_inquiry":
            self.state = "BALANCE_GUARD"
            return FSMDecision(next_state="BALANCE_GUARD", is_fast_path=False)

        if intent == "product_query":
            self.state = "PRODUCT_DEFLECT"
            return FSMDecision(next_state="PRODUCT_DEFLECT", is_fast_path=False)

        if intent in {"prompt_injection", "off_topic", "third_party_inquiry"}:
            self.state = "OUT_OF_SCOPE_DEFLECT"
            return FSMDecision(next_state="OUT_OF_SCOPE_DEFLECT", is_fast_path=False)

        if intent == "waiver_request":
            self.state = "WAIVER_NOTED"
            return FSMDecision(
                next_state="WAIVER_NOTED",
                is_fast_path=False,
                terminal_outcome="human_callback_required",
                terminal_reason="waiver",
            )

        if intent == "dispute":
            self.state = "WAIVER_NOTED"  # reuse the "noted, human callback" path
            return FSMDecision(
                next_state="WAIVER_NOTED",
                is_fast_path=False,
                terminal_outcome="human_callback_required",
                terminal_reason="dispute",
            )

        if intent == "already_paid":
            self.state = "ALREADY_PAID"
            return FSMDecision(
                next_state="ALREADY_PAID",
                is_fast_path=False,
                terminal_outcome="already_paid",
            )

        if intent in {"promise_to_pay", "partial_payment", "out_of_town", "nach_failure",
                       "salary_not_credited", "payment_failed_while_trying"}:
            self.state = "PTP_PROBE"
            return FSMDecision(
                next_state="PTP_PROBE",
                is_fast_path=False,
                terminal_outcome="promise_to_pay",
            )

        if intent == "unexpected_expense":
            # Could be hardship-adjacent. Probe once if not yet, otherwise PTP.
            if not self.context.hardship_probed_already:
                self.context.hardship_probed_already = True
                self.state = "HARDSHIP_PROBE"
                return FSMDecision(next_state="HARDSHIP_PROBE", is_fast_path=False)
            self.state = "PTP_PROBE"
            return FSMDecision(next_state="PTP_PROBE", is_fast_path=False)

        if intent == "callback_request":
            self.state = "TERMINAL"
            return FSMDecision(
                next_state="TERMINAL",
                is_fast_path=False,
                terminal_outcome="callback_request",
            )

        if intent == "no_response":
            # Customer silent. After 2 turns, escalate to callback.
            if self.context.turn_count >= 2:
                self.state = "TERMINAL"
                return FSMDecision(
                    next_state="TERMINAL",
                    is_fast_path=False,
                    terminal_outcome="no_answer",
                )
            self.state = "COLLECTING"
            return FSMDecision(next_state="COLLECTING", is_fast_path=False)

        # general / catch-all
        if self.state == "INTRO":
            self.state = "COLLECTING"
        return FSMDecision(next_state=self.state, is_fast_path=False)

    # ----- fast path -----

    def _handle_fast_path(self, intent: str, last_user_text: str = "") -> FSMDecision:
        # Abuse has a two-strike rule for SINGLE insults.
        # Multi-insult turns (2+ separate hostility markers in one utterance)
        # count as both strikes consumed — escalate to close immediately.
        if intent == "abuse":
            multi_insult = self._count_insults(last_user_text) >= 2
            self.context.abuse_strikes += 1
            strikes_allowed = (
                self.context.policy.abuse_strikes_allowed if self.context.policy else 2
            )
            if not multi_insult and self.context.abuse_strikes < strikes_allowed:
                # Within strike budget for this segment — stay in current state,
                # let LLM de-escalate. Frequent late defaulters get 1 strike.
                return FSMDecision(
                    next_state=self.state,
                    is_fast_path=False,
                    notes="abuse_first_strike — calm reset, continue",
                )
            # Either second strike OR multi-insult opener — close.
            self.state = "CALLBACK_CLOSE"
            return FSMDecision(
                next_state="CALLBACK_CLOSE",
                is_fast_path=True,
                close_template=FAST_PATH_CLOSE["abuse"],
                terminal_outcome="human_callback_required",
                terminal_reason="abuse",
                notes=("abuse_multi_insult" if multi_insult else "abuse_second_strike"),
            )

        # Job loss on Apex DPD<=10 — hardship probe instead of immediate close.
        # This is the architecture doc's special case.
        if intent == "job_loss" and self.context.card_tier == "apex" and self.context.dpd <= 10:
            if not self.context.hardship_probed_already:
                self.context.hardship_probed_already = True
                self.state = "HARDSHIP_PROBE"
                return FSMDecision(next_state="HARDSHIP_PROBE", is_fast_path=False)

        # All other fast-path intents → pre-scripted close.
        self.state = "CALLBACK_CLOSE"
        return FSMDecision(
            next_state="CALLBACK_CLOSE",
            is_fast_path=True,
            close_template=FAST_PATH_CLOSE[intent],
            terminal_outcome="human_callback_required",
            terminal_reason=FAST_PATH_OUTCOME_REASON[intent],
        )

    # ----- helpers -----

    def is_terminal(self) -> bool:
        return self.state in {"TERMINAL", "CALLBACK_CLOSE"}

    @staticmethod
    def _count_insults(text: str) -> int:
        """Count distinct hostility markers in a single user turn.
        Used to detect multi-insult openers that bypass the two-strike rule.
        """
        if not text:
            return 0
        import re as _re
        markers = [
            r"\b(bloody|bastard|shut\s+up|get\s+lost|idiot|stupid|moron|dumb)\b",
            r"\b(chutiya|saala|bhosadi|madarchod|behenchod|kutta|kamine|gadha)\b",
            r"\b(mc|bc)\b",
            r"\bf(\*+|uck)\b",
            r"\baccent\s+is\s+(terrible|bad|awful|horrible)\b",
            r"\bsound\s+(stupid|dumb|terrible|awful|robotic)\b",
            r"\b(what\s+kind\s+of|kya)\s+(idiot|stupid|garbage)\s+(job|kaam)\b",
            r"\bget\s+lost\b",
            r"\bsend\s+me\s+(your\s+)?(number|photo|pic|nudes)\b",
        ]
        return sum(1 for p in markers if _re.search(p, text, _re.I))
