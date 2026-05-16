"""Terminal outcome schema. Mirrors the architecture doc's 7 outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

OutcomeType = Literal[
    "promise_to_pay",
    "already_paid",
    "callback_request",
    "human_callback_required",
    "refused",
    "wrong_number",
    "no_answer",
]


class OutcomeDetail(BaseModel):
    # promise_to_pay
    amount: float | None = None
    date: str | None = None  # ISO date
    mode: str | None = None
    # already_paid
    date_paid: str | None = None
    # callback_request
    preferred_time: str | None = None
    # human_callback_required
    reason: str | None = None
    urgency: Literal["high", "medium", "low"] | None = None
    callback_sla_hours: int | None = None
    do_not_pressure: bool | None = None
    agent_brief: str | None = None
    # refused
    reason_stated: str | None = None
    # policy-driven (Layer 2): next-action routing for the CRM/orchestrator
    handoff: Literal["continue_bot", "human_takeover", "route_to_human", "pause"] | None = None
    policy_rationale: str | None = None


class Outcome(BaseModel):
    call_id: str
    customer_id: str | None = None
    outcome: OutcomeType
    outcome_detail: OutcomeDetail = Field(default_factory=OutcomeDetail)
    compliance_flags: list[str] = Field(default_factory=list)
    turns: int = 0
    transcript_summary: str | None = None
    audit_log_ref: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
