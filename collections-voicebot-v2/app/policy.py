"""Segment policy table — the deterministic counterpart to segment-aware prompts.

Strategies + modifiers control *tone*. This table controls *thresholds*: how
long a PTP horizon we'll accept, when a refusal must escalate to a human,
whether the empathy probe is segment-eligible, how many abuse strikes a
particular segment gets, the smallest partial payment we'd suggest, and the
callback SLA the outcome ships to the CRM.

Lives in code, not prompts. The LLM does the wording; the FSM enforces the
numbers.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.pre_filter import CRMContext


@dataclass(frozen=True)
class SegmentPolicy:
    max_ptp_days: int               # cap on how far out we'll confirm a PTP
    partial_floor_inr: int          # smallest meaningful partial we'd suggest
    empathy_probe_eligible: bool    # whether the empathy-probe fires for this segment
    human_takeover_on_refuse: bool  # upgrade refused outcome to human handoff
    firm_hold: bool                 # hold the line before retreating to callback (Layer 1 hook)
    abuse_strikes_allowed: int      # default 2; lowered for frequent late defaulters
    callback_sla_hours: int         # SLA for the outcome's next-action

    # Audit / debug — never read by logic, just stamped on the outcome.
    rationale: str = ""


# Sensible default for first-time near-prime customers in the early window.
DEFAULT_POLICY = SegmentPolicy(
    max_ptp_days=21,
    partial_floor_inr=2000,
    empathy_probe_eligible=True,
    human_takeover_on_refuse=False,
    firm_hold=False,
    abuse_strikes_allowed=2,
    callback_sla_hours=72,
    rationale="default_first_or_occasional_early",
)


def resolve(ctx: CRMContext) -> SegmentPolicy:
    """Pick the policy for this customer.

    Rules are evaluated in priority order — the first match wins. Order
    matters: the strictest segments (frequent + late, sub-prime + frequent)
    are checked first so they aren't shadowed by softer rules.
    """
    dpd = ctx.dpd
    history = ctx.default_history
    tier = ctx.card_tier
    bureau = ctx.bureau_score

    # --- strictest: frequent defaulter, late window ----------------------
    if history == "frequent" and dpd >= 20:
        return SegmentPolicy(
            max_ptp_days=7,
            partial_floor_inr=3000,
            empathy_probe_eligible=False,   # frequent: hold the line, not the hand
            human_takeover_on_refuse=True,  # past bot's lane on refusal
            firm_hold=True,
            abuse_strikes_allowed=1,        # one warning, then close
            callback_sla_hours=24,
            rationale="frequent_late_strict",
        )

    # --- sub-prime + frequent (any DPD): mandatory takeover on refusal ---
    if bureau < 650 and history == "frequent":
        return SegmentPolicy(
            max_ptp_days=10,
            partial_floor_inr=2000,
            empathy_probe_eligible=False,
            human_takeover_on_refuse=True,
            firm_hold=True,
            abuse_strikes_allowed=1,
            callback_sla_hours=24,
            rationale="subprime_frequent",
        )

    # --- any frequent defaulter ------------------------------------------
    if history == "frequent":
        return SegmentPolicy(
            max_ptp_days=10,
            partial_floor_inr=2000,
            empathy_probe_eligible=False,
            human_takeover_on_refuse=False,
            firm_hold=True,
            abuse_strikes_allowed=2,
            callback_sla_hours=48,
            rationale="frequent_any_dpd",
        )

    # --- late-window, non-frequent: shorter horizon, harder takeover ----
    if dpd >= 20:
        return SegmentPolicy(
            max_ptp_days=10,
            partial_floor_inr=2000,
            empathy_probe_eligible=True,
            human_takeover_on_refuse=True,  # DPD 20+ refusal warrants human
            firm_hold=False,
            abuse_strikes_allowed=2,
            callback_sla_hours=48,
            rationale="late_non_frequent",
        )

    # --- apex, first, early: the headline case — be flexible ------------
    if tier == "apex" and history == "first" and dpd <= 10:
        return SegmentPolicy(
            max_ptp_days=21,
            partial_floor_inr=5000,
            empathy_probe_eligible=True,
            human_takeover_on_refuse=False,
            firm_hold=False,
            abuse_strikes_allowed=2,
            callback_sla_hours=72,
            rationale="apex_first_early",
        )

    # --- everyone else: default -----------------------------------------
    return DEFAULT_POLICY
