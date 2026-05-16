"""Segment policy table — the deterministic counterpart to segment-aware prompts.

Strategies + modifiers control *tone*. This table controls *thresholds*: how
long a PTP horizon we'll accept, when a refusal must escalate to a human,
whether the empathy probe is segment-eligible, how many abuse strikes a
particular segment gets, the smallest partial we'd suggest, and the callback
SLA the outcome ships to the CRM.

Lives in code, not prompts. The LLM does the wording; the FSM enforces the
numbers.

NOTE on `partial_floor_inr`:
The partial floor is NOT a hardcoded segment constant. The legitimate floor
is the bank's Minimum Amount Due (MAD) on this cycle's statement — that's
the actual amount that keeps the account current under RBI rules. SegmentPolicy
keeps a `partial_floor_overlay_inr` which is the SEGMENT OVERLAY — the
absolute minimum we'd suggest even if MAD is lower (used for risky segments
where we don't want symbolic ₹500 partials). Effective floor for a call is:

    effective_floor = max(ctx.minimum_amount_due, policy.partial_floor_overlay_inr)

Computed by ``resolve_partial_floor(ctx, policy)`` at call time.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import MAD_PCT_OF_OUTSTANDING
from app.pre_filter import CRMContext


@dataclass(frozen=True)
class SegmentPolicy:
    max_ptp_days: int                  # cap on how far out we'll confirm a PTP
    partial_floor_overlay_inr: int     # segment overlay floor — used only if higher than MAD
    empathy_probe_eligible: bool       # whether the empathy probe fires for this segment
    human_takeover_on_refuse: bool     # upgrade refused outcome to human handoff
    firm_hold: bool                    # hold the line before retreating to callback
    abuse_strikes_allowed: int         # default 2; lowered for frequent late defaulters
    callback_sla_hours: int            # SLA for the outcome's next-action

    # Audit / debug — never read by logic, just stamped on the outcome.
    rationale: str = ""


# Default for first-time near-prime customers in the early window. The
# overlay is intentionally low — we let MAD be the natural floor for
# co-operative customers; the overlay only kicks in for risky segments.
DEFAULT_POLICY = SegmentPolicy(
    max_ptp_days=21,
    partial_floor_overlay_inr=0,
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
            partial_floor_overlay_inr=3000,   # reject symbolic partials
            empathy_probe_eligible=False,
            human_takeover_on_refuse=True,
            firm_hold=True,
            abuse_strikes_allowed=1,
            callback_sla_hours=24,
            rationale="frequent_late_strict",
        )

    # --- sub-prime + frequent (any DPD): mandatory takeover on refusal ---
    if bureau < 650 and history == "frequent":
        return SegmentPolicy(
            max_ptp_days=10,
            partial_floor_overlay_inr=2000,
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
            partial_floor_overlay_inr=2000,
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
            partial_floor_overlay_inr=0,      # MAD is fine for non-frequent
            empathy_probe_eligible=True,
            human_takeover_on_refuse=True,
            firm_hold=False,
            abuse_strikes_allowed=2,
            callback_sla_hours=48,
            rationale="late_non_frequent",
        )

    # --- apex, first, early: the headline case — be flexible ------------
    if tier == "apex" and history == "first" and dpd <= 10:
        return SegmentPolicy(
            max_ptp_days=21,
            partial_floor_overlay_inr=0,      # MAD is fine; apex customers self-determine
            empathy_probe_eligible=True,
            human_takeover_on_refuse=False,
            firm_hold=False,
            abuse_strikes_allowed=2,
            callback_sla_hours=72,
            rationale="apex_first_early",
        )

    # --- everyone else: default -----------------------------------------
    return DEFAULT_POLICY


def resolve_partial_floor(ctx: CRMContext, policy: SegmentPolicy) -> int:
    """Return the effective partial-payment floor for this call.

    Bank's Minimum Amount Due (MAD) is the LEGITIMATE floor — it's the
    amount the bank's own ledger says keeps the account current. If the
    CRM payload includes ``minimum_amount_due``, we use it. Otherwise we
    fall back to ``MAD_PCT_OF_OUTSTANDING`` of the outstanding amount
    (RBI Master Direction on Credit Cards 2022 caps the upper bound;
    banks set their own within that).

    The segment overlay only ever RAISES the floor — used for risky
    segments where we don't want symbolic ₹500 token payments from
    chronic defaulters. For default segments the overlay is 0 and MAD
    wins.

    Result is rounded to the nearest ₹100 for clean phrasing in the bot's
    suggestion.
    """
    if ctx.minimum_amount_due is not None and ctx.minimum_amount_due > 0:
        mad = ctx.minimum_amount_due
    else:
        # Fallback for partial CRM payloads / older personas.csv
        mad = max(ctx.outstanding_amount * (MAD_PCT_OF_OUTSTANDING / 100.0), 250)
    effective = max(mad, policy.partial_floor_overlay_inr)
    return int(round(effective / 100.0) * 100)
