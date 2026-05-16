"""Pre-call segment filter.

Reads the CRM context payload, applies block rules, picks the call strategy,
and derives modifier keys for the prompt composer.

Block conditions (from architecture doc):
- card_tier=apex AND bureau_score <= 649  → anomalous, human review
- dpd > 30                                → out of scope
- account_status in (deceased, bankrupt, legal_hold) → do not call
- outstanding_amount <= 0                 → nothing to collect
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from app.policy import SegmentPolicy

CallStrategy = Literal["apex_concierge", "A_reminder", "B_problem_solving"]
BlockReason = Literal[
    "apex_subprime_review",
    "dpd_out_of_scope",
    "account_status",
    "nothing_to_collect",
]


@dataclass
class CRMContext:
    """Mirror of the CRM payload defined in the architecture doc.

    Collections-essential fields (minimum_amount_due, billed_amount, dates,
    last payment) are the bank's source of truth for what the customer owes
    THIS CYCLE. Without them, the bot has no way to suggest a meaningful
    partial — it has to guess. See ``app/policy.py::resolve_partial_floor``
    for how MAD is used.

    All amount fields stay OUT of the LLM's prompt by default (defence in
    depth on the no-balance-without-OTP rule). The bot sees the DERIVED
    ``partial_floor_inr`` only, via the SegmentPolicy block.
    """

    call_id: str
    customer_id: str
    name: str
    card_tier: Literal["spark", "edge", "apex"]
    dpd: int
    bureau_score: int
    default_history: Literal["first", "occasional", "frequent"]
    outstanding_amount: float  # used ONLY by pre-filter, never passed to LLM
    credit_limit: float
    relationship_years: float
    self_cure_history: bool
    account_status: str = "active"
    # --- Collections-essential cycle data (NEW) ---
    # MAD = bank-calculated minimum to keep the account current. Typically 5%
    # of outstanding under RBI Master Direction on Credit Cards 2022, but
    # banks may set higher. Defaults to 5% if not supplied.
    minimum_amount_due: float | None = None
    billed_amount: float | None = None      # this cycle's statement total
    statement_date: str | None = None       # ISO date — when the bill was generated
    payment_due_date: str | None = None     # ISO date — when payment was due
    # Last-payment continuity (the "I see you usually pay via UPI" frame).
    # All three nullable — a first-time defaulter may have none.
    last_payment_amount: float | None = None
    last_payment_date: str | None = None    # ISO date
    last_payment_mode: str | None = None    # upi / netbanking / card / imps / neft / autodebit


@dataclass
class PreFilterResult:
    blocked: bool
    block_reason: BlockReason | None
    strategy: CallStrategy | None
    modifier_keys: dict[str, str]  # e.g. {"history": "first", "bureau": "prime", ...}
    # Deterministic segment policy resolved once per call. Drives FSM thresholds
    # (max PTP horizon, abuse strikes, human-takeover routing, callback SLA).
    # None only for blocked calls.
    policy: "SegmentPolicy | None" = None


def run_prefilter(ctx: CRMContext) -> PreFilterResult:
    # 1. Block rules
    if ctx.account_status in {"deceased", "bankrupt", "legal_hold"}:
        return PreFilterResult(True, "account_status", None, {})
    if ctx.outstanding_amount <= 0:
        return PreFilterResult(True, "nothing_to_collect", None, {})
    if ctx.dpd > 30:
        return PreFilterResult(True, "dpd_out_of_scope", None, {})
    if ctx.card_tier == "apex" and ctx.bureau_score < 650:
        return PreFilterResult(True, "apex_subprime_review", None, {})

    # 2. Strategy
    if ctx.card_tier == "apex":
        strategy: CallStrategy = "apex_concierge"
    elif ctx.dpd <= 10:
        strategy = "A_reminder"
    else:
        strategy = "B_problem_solving"

    # 3. Modifier keys
    util_pct = (
        100.0 * ctx.outstanding_amount / ctx.credit_limit if ctx.credit_limit > 0 else 0.0
    )

    modifiers: dict[str, str] = {
        "tier": ctx.card_tier,  # spark | edge | apex — drives tone beyond strategy
        "history": ctx.default_history,
        "bureau": _bureau_band(ctx.bureau_score),
        "util": _util_band(util_pct),
        "age": _age_band(ctx.relationship_years),
        "channel": "selfcures" if ctx.self_cure_history else "never",
    }

    # Resolve deterministic policy thresholds for this segment.
    from app.policy import resolve as _resolve_policy  # local import avoids cycle at module load
    policy = _resolve_policy(ctx)

    return PreFilterResult(False, None, strategy, modifiers, policy=policy)


def _bureau_band(score: int) -> str:
    if score >= 750:
        return "prime"
    if score >= 650:
        return "nearprime"
    return "subprime"


def _util_band(pct: float) -> str:
    if pct < 20:
        return "low"
    if pct <= 70:
        return "medium"
    return "high"


def _age_band(years: float) -> str:
    if years < 0.5:
        return "new"
    if years < 3:
        return "established"
    return "tenured"
