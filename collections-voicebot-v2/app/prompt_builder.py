"""Assemble the system prompt fresh each turn from 4 parts:

  Part 1: Immutable base       (always loaded)
  Part 2: Segment + modifiers  (per call — fixed at call start)
  Part 3: FSM state constraint (per turn — changes as state moves)
  Part 4: Customer context     (per call — does NOT include balance)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.pre_filter import CRMContext, PreFilterResult


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class PromptParts:
    base: str
    strategy: str
    modifiers: dict[str, str]  # category → text
    customer_context: str


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
        return PromptParts(
            base=self._base,
            strategy=self._strategies[pf.strategy],
            modifiers=modifiers,
            customer_context=customer_ctx,
        )

    def assemble(self, parts: PromptParts, fsm_state: str) -> str:
        fsm_text = self._fsm_states.get(fsm_state.upper(), "")
        modifier_text = "\n\n".join(parts.modifiers.values())
        sections = [
            parts.base,
            "=" * 60,
            "SEGMENT STRATEGY",
            "=" * 60,
            parts.strategy,
            "=" * 60,
            "CUSTOMER PROFILE MODIFIERS",
            "=" * 60,
            modifier_text,
            "=" * 60,
            "CURRENT TURN — FSM STATE INSTRUCTIONS",
            "=" * 60,
            fsm_text,
            "=" * 60,
            parts.customer_context,
        ]
        return "\n\n".join(s for s in sections if s)

    def get_close_template(self, key: str) -> str:
        return self._closes.get(key, "")


def _read(rel_path: str) -> str:
    return (PROMPTS_DIR / rel_path).read_text(encoding="utf-8").strip()
