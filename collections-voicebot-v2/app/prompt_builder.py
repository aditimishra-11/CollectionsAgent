"""Assemble the system prompt fresh each turn from 4 parts:

  Part 1: Immutable base       (always loaded)
  Part 2: Segment + modifiers  (per call — fixed at call start)
  Part 3: FSM state constraint (per turn — changes as state moves)
  Part 4: Customer context     (per call — does NOT include balance)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.config import (
    LATE_FEE_INR, LATE_FEE_APPLIES_ABOVE_INR, MONTHLY_INTEREST_PCT,
)
from app.policy import SegmentPolicy, resolve_partial_floor
from app.pre_filter import CRMContext, PreFilterResult


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class PromptParts:
    base: str
    strategy: str
    modifiers: dict[str, str]  # category → text
    customer_context: str
    policy_block: str = ""           # NEW: per-call deterministic facts (max PTP days, partial floor)
    bank_facts: str = ""             # NEW: bank-level constants (late fee, interest rate)


class PromptBuilder:
    """Loads and caches the prompt fragments. One per process."""

    def __init__(self) -> None:
        self._base = (PROMPTS_DIR / "base.txt").read_text(encoding="utf-8").strip()
        self._strategies = {
            "apex_concierge": _read("strategy_apex_concierge.txt"),
            "A_reminder": _read("strategy_a_reminder.txt"),
            "B_problem_solving": _read("strategy_b_problem_solving.txt"),
        }
        self._modifiers = {
            # Tier first — it sets the dominant tone register before any other dimension fine-tunes
            "tier": {
                "spark": _read("modifiers/tier_spark.txt"),
                "edge": _read("modifiers/tier_edge.txt"),
                "apex": _read("modifiers/tier_apex.txt"),
            },
            "history": {
                "first": _read("modifiers/history_first.txt"),
                "occasional": _read("modifiers/history_occasional.txt"),
                "frequent": _read("modifiers/history_frequent.txt"),
            },
            "bureau": {
                "prime": _read("modifiers/bureau_prime.txt"),
                "nearprime": _read("modifiers/bureau_nearprime.txt"),
                "subprime": _read("modifiers/bureau_subprime.txt"),
            },
            "util": {
                "low": _read("modifiers/util_low.txt"),
                "medium": _read("modifiers/util_medium.txt"),
                "high": _read("modifiers/util_high.txt"),
            },
            "age": {
                "new": _read("modifiers/age_new.txt"),
                "established": _read("modifiers/age_established.txt"),
                "tenured": _read("modifiers/age_tenured.txt"),
            },
            "channel": {
                "selfcures": _read("modifiers/channel_selfcures.txt"),
                "never": _read("modifiers/channel_never.txt"),
            },
        }
        self._fsm_states = {
            p.stem.upper(): p.read_text(encoding="utf-8").strip()
            for p in (PROMPTS_DIR / "fsm_states").glob("*.txt")
        }
        self._closes = {
            p.stem: p.read_text(encoding="utf-8").strip()
            for p in (PROMPTS_DIR / "closes").glob("*.txt")
        }

    def build_call_parts(self, ctx: CRMContext, pf: PreFilterResult) -> PromptParts:
        """Build the call-level (constant-per-call) parts of the prompt."""
        assert pf.strategy is not None, "build_call_parts called on a blocked call"
        modifiers = {
            cat: self._modifiers[cat][pf.modifier_keys[cat]]
            for cat in self._modifiers
            if cat in pf.modifier_keys
        }
        customer_ctx = (
            "CUSTOMER CONTEXT (your reference — do not read aloud)\n"
            f"- Name: {ctx.name}\n"
            f"- Card: {ctx.card_tier}\n"
            f"- DPD: {ctx.dpd}\n"
            f"- Default history: {ctx.default_history}\n"
            f"- Relationship: {ctx.relationship_years} years\n"
            f"- Self-cure history: {'yes' if ctx.self_cure_history else 'no'}\n"
            "(Outstanding balance, credit limit, and bureau score are NOT shown to you. "
            "You cannot reveal what you do not know.)"
        )
        policy_block = self._build_policy_block(ctx, pf.policy) if pf.policy else ""
        bank_facts = self._build_bank_facts()
        return PromptParts(
            base=self._base,
            strategy=self._strategies[pf.strategy],
            modifiers=modifiers,
            customer_context=customer_ctx,
            policy_block=policy_block,
            bank_facts=bank_facts,
        )

    @staticmethod
    def _build_policy_block(ctx: CRMContext, policy: SegmentPolicy) -> str:
        """Render the resolved SegmentPolicy as plain English for the LLM.

        This is the root-cause fix for the "bot accepted a 2-month-out PTP"
        bug — the LLM was being asked to make threshold decisions about
        dates and partials without being told what the thresholds are.
        Now it reads its own per-segment rules upstream and the validator
        is just a wall, not the primary mechanism.
        """
        today_iso = date.today().isoformat()
        today_obj = date.today()
        # Pre-compute the latest acceptable PTP date so the LLM sees it
        # rendered as an actual calendar day, not a relative count. Makes
        # "is May 23rd within bounds?" a literal comparison for the model.
        from datetime import timedelta
        latest_acceptable = (today_obj + timedelta(days=policy.max_ptp_days)).isoformat()
        partial_floor = resolve_partial_floor(ctx, policy)
        lines = [
            f"SEGMENT POLICY FOR THIS CALL (today is {today_iso})",
            f"- You may confirm a PTP date up to {policy.max_ptp_days} days from today — "
            f"i.e. through {latest_acceptable}. Any date beyond that, push back ONCE: "
            f"ask for sooner, OR a partial of at least ₹{partial_floor:,} today.",
            f"- The smallest partial you will suggest is ₹{partial_floor:,} (this is the bank's "
            f"Minimum Amount Due for this cycle{', plus a segment overlay' if policy.partial_floor_overlay_inr > 0 else ''}). "
            f"Do NOT suggest a smaller token amount.",
            # CRITICAL: explicit date-specificity rule — was buried in base.txt
            # and was getting de-prioritised behind the policy negotiation. The
            # outcome extractor needs an ISO date or it can't fill the date slot.
            f"- DATE-SPECIFICITY (mandatory for CRM): when the customer commits to a date, "
            f"ALWAYS resolve to the actual calendar day. If they say 'tomorrow' (today is "
            f"{today_iso}), say back the resolved day name + date. If 'this weekend', ask "
            f"'Saturday or Sunday — which one?'. If 'Friday', confirm WHICH Friday by date. "
            f"NEVER confirm a PTP with just 'tomorrow' or 'next week' — the CRM stores ISO "
            f"calendar days only.",
        ]
        if policy.firm_hold:
            lines.append(
                "- HOLD THE LINE: this customer's segment is high-risk. Do not retreat to "
                "a callback offer until you have explicitly offered both a sooner PTP date "
                "AND a partial today. Be firm but warm — never threaten or shame."
            )
        if not policy.empathy_probe_eligible:
            lines.append(
                "- The empathy probe (\"is everything alright?\") is NOT for this segment. "
                "Only acknowledge hardship if the customer explicitly volunteers it."
            )
        if policy.human_takeover_on_refuse:
            lines.append(
                "- If the customer refuses outright, the call will route to a human "
                "colleague. You do not need to keep pushing — close warmly and end."
            )
        return "\n".join(lines)

    @staticmethod
    def _build_bank_facts() -> str:
        """Bank-policy constants the bot may quote IF the customer asks."""
        return (
            "BANK FACTS (state ONLY if the customer asks — never volunteer)\n"
            f"- Interest rate: {MONTHLY_INTEREST_PCT}% per month.\n"
            f"- Late fee: ₹{LATE_FEE_INR:,.0f}, applicable only when outstanding is "
            f"above ₹{LATE_FEE_APPLIES_ABOVE_INR:,.0f}.\n"
            "- These numbers are bank-published. Do not quote anything else."
        )

    def assemble(self, parts: PromptParts, fsm_state: str, turn_directive: str | None = None) -> str:
        """Assemble the system prompt. ``turn_directive`` is an optional, deterministic
        per-turn instruction injected by the FSM (e.g., "customer just refused —
        offer ONE callback and stop pushing payment"). It overrides nothing, but
        sits at the bottom so it's the freshest instruction the LLM reads.
        """
        fsm_text = self._fsm_states.get(fsm_state.upper(), "")
        modifier_text = "\n\n".join(parts.modifiers.values())
        sections = [
            parts.base,
            "=" * 60,
            "BANK FACTS",
            "=" * 60,
            parts.bank_facts,
            "=" * 60,
            "SEGMENT STRATEGY",
            "=" * 60,
            parts.strategy,
            "=" * 60,
            "CUSTOMER PROFILE MODIFIERS",
            "=" * 60,
            modifier_text,
            "=" * 60,
            "SEGMENT POLICY (deterministic thresholds for this call)",
            "=" * 60,
            parts.policy_block,
            "=" * 60,
            "CURRENT TURN — FSM STATE INSTRUCTIONS",
            "=" * 60,
            fsm_text,
            "=" * 60,
            parts.customer_context,
        ]
        if turn_directive:
            sections.extend([
                "=" * 60,
                "THIS TURN — FSM OVERRIDE (highest priority)",
                "=" * 60,
                turn_directive,
            ])
        return "\n\n".join(s for s in sections if s)

    def get_close_template(self, key: str) -> str:
        return self._closes.get(key, "")


def _read(rel_path: str) -> str:
    return (PROMPTS_DIR / rel_path).read_text(encoding="utf-8").strip()
