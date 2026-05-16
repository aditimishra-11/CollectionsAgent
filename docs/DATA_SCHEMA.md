# Data Schema — Bot's In/Out

Defines what the bot receives, what it emits, and what it deliberately does not see. The schema is the contract between the orchestration layer (bank side) and the bot (GreyLabs side).

## Design principles

1. **Minimum necessary information.** The bot gets what's needed to handle this call well, nothing more. DPDP-aligned.
2. **The bot is stateless across calls.** No call history persists in the bot. The orchestrator gives it context each time.
3. **Sensitive fields never reach the LLM.** Outstanding balance and credit-limit values are read by the pre-filter to derive utilisation, then dropped. The LLM cannot reveal what it doesn't know.
4. **All output is structured and auditable.** Every output field is typed and validated.

## Input — what the bot receives at call start

### A. CRM context (current persona record)

Required at every call. Drives pre-filter, strategy selection, and modifier selection.

| Field | Type | Used by | Required | Notes |
|---|---|---|---|---|
| `call_id` | string (UUID) | All | Yes | Unique per call; goes in audit |
| `customer_id` | string | All | Yes | Stable customer key; used to look up history |
| `name` | string | Prompt | Yes | First name used by bot (sparingly) |
| `card_tier` | enum: spark / edge / apex | Pre-filter | Yes | Drives strategy + tier modifier |
| `dpd` | int | Pre-filter | Yes | Drives strategy (DPD 4–10 vs 11–30) |
| `bureau_score` | int (300–900) | Pre-filter | Yes | Drives bureau modifier; not passed to LLM directly |
| `default_history` | enum: first / occasional / frequent | Modifier | Yes | |
| `outstanding_amount` | float (₹) | Pre-filter only | Yes | **Never passed to LLM.** Used to derive utilisation. |
| `credit_limit` | float (₹) | Pre-filter only | Yes | **Never passed to LLM.** Used to derive utilisation. |
| `relationship_years` | float | Modifier | Yes | Drives age modifier (new / established / tenured) |
| `self_cure_history` | bool | Modifier | Yes | Drives channel modifier |
| `account_status` | enum: active / deceased / bankrupt / legal_hold | Pre-filter | Yes | Blocks call if non-active |

### B. Call history (NEW — for multi-call awareness)

The orchestrator constructs this from the customer's prior bot interactions in the current cycle.

| Field | Type | Required | Notes |
|---|---|---|---|
| `attempt_number` | int (1–3) | Yes | Position in current cycle |
| `max_attempts_in_cycle` | int | Yes | Usually 3; bank-configurable |
| `days_until_forced_handoff` | int | Yes | Days until DPD hits 25 (forced human) |
| `prior_outcomes[]` | list of past outcomes | Yes (can be empty) | Each: date, outcome_type, key detail, follow-up status |
| `broken_ptps_in_cycle` | int | Yes | Drives stricter handling on Call 2/3 |
| `last_contact_at` | timestamp | Yes | For "we spoke last week" framing |

### C. Active overrides (NEW)

Set by Ops if applicable. Influences pre-filter behaviour and FSM routing.

| Field | Type | Notes |
|---|---|---|
| `manual_pause` | bool + reason | If true, bot doesn't dial |
| `legal_hold` | bool | Bot doesn't dial; legal team has the case |
| `deceased_flag` | bool | Bot doesn't dial; bereavement team has it |
| `dnd_acknowledged` | bool | Bot doesn't dial; TRAI logged |
| `language_preference` | enum | If set, route directly to language callback |
| `custom_dial_window` | time range | Overrides default within TRAI bounds |
| `do_not_pressure` | bool | If set, suppress all PTP probing; offer human callback only |

### D. Compliance context (NEW)

Auto-populated from regulatory feeds.

| Field | Type | Notes |
|---|---|---|
| `customer_consent_revoked` | bool | DPDP — must not dial |
| `affected_disaster_area` | bool | RBI special-provision flag — softer handling |
| `existing_complaint_open` | bool | Suspend bot; human-only until resolved |

### E. Live audio (per turn, voice mode only)

| Field | Type | Notes |
|---|---|---|
| `audio_chunk` | bytes (16 kHz int16 PCM) | Streamed from mic, 32 ms chunks |

## Output — what the bot emits

### Per-turn (audit + monitoring)

Written to JSONL by `app/audit.py`. Used by Ops drill-down and Compliance audit.

| Field | Type | Notes |
|---|---|---|
| `call_id`, `customer_id`, `turn_number`, `timestamp` | metadata | |
| `user_text` | string | STT transcript (post-AEC) |
| `bot_text` | string | What was actually said (post-validator) |
| `intent_classified` | enum (1 of 30) | From `intent_classifier.py` |
| `intent_confidence` | float | (Currently 1.0 for rule-based; ML version v3) |
| `scenario_inferred` | enum: ptp / hardship / adversarial / language / probing | Behavioural classification of the turn — derived from intent. **Discovered mid-call, not pre-selected.** Drives the bot-internals chip in the demo UI. |
| `fsm_state_before` | enum | |
| `fsm_state_after` | enum | |
| `move_played` | enum (1 of 9 ladder moves) | Which ladder move the FSM forced the bot to play this turn (e.g. `ASK_DATE`, `OFFER_PARTIAL`, `OFFER_CALLBACK`). Null when in a non-ladder-managed state. See `app/fsm.py::LADDERS`. |
| `directives_fired[]` | list of "layer:detail" strings | Every deterministic guardrail that ran this turn. Prefix tells you which layer fired: `policy:` (segment-policy thresholds), `ladder:` (move ladder), `fsm:` (FSM strikes / notes), `validator:` (rule violations), `guard:` (END_CALL stripped, etc.). Compliance can grep on this. |
| `fast_path` | bool | True if this turn bypassed the LLM |
| `validator_passed` | bool | |
| `validator_violations[]` | list | If any prohibited pattern was caught |
| `validator_fallback_used` | bool | If we substituted the safe template |
| `stt_latency_ms` | int | |
| `llm_latency_ms` | int | |
| `tts_latency_ms` | int | |
| `tokens_in`, `tokens_out` | int | For cost tracking |

### End-of-call (terminal outcome → CRM webhook)

Posted by `app/outcome/webhook.py` to the bank's CRM endpoint. Idempotency key = `call_id`.

| Field | Type | Required | Notes |
|---|---|---|---|
| `call_id` | string | Yes | |
| `customer_id` | string | Yes | |
| `outcome` | enum (1 of 7) | Yes | promise_to_pay / already_paid / callback_request / human_callback_required / refused / wrong_number / no_answer |
| `outcome_detail` | object | Yes | Per-outcome structured fields (see below) |
| `cta` | object (NEW) | Yes | Actionable next step the CRM acts on (see below) |
| `transcript_summary` | string (1–2 sentences) | Yes | LLM-generated, for Ops drill-down |
| `agent_brief` | string (1 paragraph, NEW) | If outcome = human_callback_required | For the human picking up |
| `handoff_recommendation` | enum (NEW): continue_bot / route_to_human / pause / mark_legal_hold | Yes | What the orchestrator should do next |
| `compliance_flags[]` | list | Yes (often empty) | Any violations the validator caught + substituted |
| `turns` | int | Yes | |
| `duration_seconds` | float | Yes | |
| `estimated_inr_per_call` | float | Yes | LLM + STT + TTS cost |
| `audit_log_ref` | string (path) | Yes | Pointer to per-turn JSONL |
| `timestamp` | ISO-8601 | Yes | |

### `outcome_detail` — per outcome type

```
promise_to_pay:
  amount: float
  date: ISO date (specific, not "tomorrow")
  mode: enum (upi / netbanking / card / imps / neft / autodebit)

already_paid:
  mode: enum (same as above)
  date_paid: ISO date

callback_request:
  preferred_time: ISO datetime range (e.g. "2026-05-18T15:00:00 to 18:00:00")

human_callback_required:
  reason: enum (medical_emergency / job_loss / business_failure / mental_distress
                / natural_disaster / abuse / waiver / dispute / deceased
                / language_callback / refused_current_call_high_risk)
  urgency: enum (high / medium / low)

refused:
  reason: enum — IMPORTANT, drives orchestrator behaviour:
    - "dnd"                  → TRAI DND registered; permanent suppression of MARKETING.
                               Collections contact may continue per RBI Fair Practices Code
                               (DND suppresses marketing, not legitimate dues).
    - "refused_current_call" → Customer refused THIS call (frustration, not regulatory DND).
                               Retry allowed after cooling-off per policy.callback_sla_hours.
  reason_stated: string (optional, customer's words)

wrong_number, no_answer: {} (no detail)

# All outcome types ALSO carry the following policy-driven routing fields,
# stamped onto outcome_detail by `_apply_policy_to_outcome` in conversation.py.
# The SegmentPolicy (see app/policy.py) is resolved once per call from the
# CRM context and drives these values — they are not free-form LLM output.
+ handoff: enum (continue_bot / human_takeover / route_to_human / pause)
+ callback_sla_hours: int
+ policy_rationale: string (audit — which policy row resolved, e.g. "frequent_late_strict")
```

### `cta` — what the CRM should do (NEW)

This is the structured CTA per outcome. Drives the orchestrator's next-action selection. The bot fills the fields below from a combination of outcome type and the resolved SegmentPolicy — the orchestrator should NOT re-derive these from the outcome string alone, because the policy can upgrade `refused` to `human_takeover` for high-risk segments (e.g. frequent defaulter at DPD 22+).

```
{
  "action": enum (one of:
    "monitor_payment"       — for promise_to_pay
    "verify_payment"        — for already_paid
    "schedule_human_callback" — for callback_request and human_callback_required
    "suppress_number"       — for wrong_number
    "retry_per_policy"      — for no_answer
    "log_and_review"        — for refused / refused_current_call (low-risk segments)
    "schedule_human_callback" — for refused / refused_current_call (high-risk segments,
                                upgraded by policy.human_takeover_on_refuse)
    "log_dnd_no_retry"      — for refused / dnd
  ),
  "sla_hours": int               # from outcome_detail.callback_sla_hours (policy-driven)
  "handoff_recommendation": enum # from outcome_detail.handoff (policy-driven)
  "do_not_pressure": bool,
  "target_team": string ("hardship desk", "waivers team", "bereavement", "language pool", null),
  "next_call_eligible_at": ISO datetime (when the bot may call this customer again),
  "max_bot_attempts_remaining": int (default 3, decrements per cycle)
}
```

### SegmentPolicy — deterministic thresholds resolved per call (NEW — Layer 2)

Not part of the outbound payload directly (the resolved values are written into `outcome_detail`), but documented here because every Ops/Compliance reader needs to know it exists. Defined in `app/policy.py`.

```
SegmentPolicy (resolved at call start by app/policy.py::resolve(ctx)):
  max_ptp_days: int                # PTP horizon cap. >this from today = bot pushes back instead of confirming.
  partial_floor_inr: int           # Smallest meaningful partial the bot will suggest.
  empathy_probe_eligible: bool     # Whether the gentle hardship probe fires for this segment.
  human_takeover_on_refuse: bool   # If true, refused → upgraded to human_callback_required.
  firm_hold: bool                  # Hold the line longer before retreating to callback.
  abuse_strikes_allowed: int       # 2 default; 1 for frequent_late_strict.
  callback_sla_hours: int          # Drives outcome_detail.callback_sla_hours.
  rationale: string                # Audit — which row of the table matched.

Rows (priority order, first match wins):
  frequent_late_strict   — history=frequent AND dpd>=20 →  7d cap, 24h SLA, 1 strike, takeover
  subprime_frequent      — bureau<650 AND history=frequent → 10d cap, 24h SLA, 1 strike, takeover
  frequent_any_dpd       — history=frequent → 10d cap, 48h SLA, 2 strikes, firm hold, no takeover
  late_non_frequent      — dpd>=20 (not frequent) → 10d cap, 48h SLA, 2 strikes, takeover
  apex_first_early       — tier=apex AND first AND dpd<=10 → 21d cap, 72h SLA, permissive
  default_first_or_occasional_early — everyone else → 21d cap, 72h SLA, 2 strikes
```

## What the bot deliberately does NOT see

| Field | Why excluded |
|---|---|
| Outstanding amount as a number (only as utilisation band) | Defence-in-depth on balance-without-OTP rule |
| Credit limit raw value | Same |
| Customer's lifetime payment history | Bias risk; only current-cycle history needed |
| Other-product relationships (FD, savings, loans) | Out of scope for collections call |
| Internal credit risk score | Not relevant to call behaviour |
| Customer's birthday, address, family details | Not needed |
| Litigation status (only `legal_hold` boolean) | Bot shouldn't reason about legal cases |
| Internal CRM notes from previous human agents | Bias risk |

## Schema versioning + migration

When fields are added (e.g., the new `cta` block), the change is **additive**. Existing CRM integrations continue to receive the previous fields unchanged; new fields are ignored by older consumers.

Breaking schema changes (renaming, dropping fields) require a major version bump and a migration window. The schema is versioned via a `schema_version: "2.1"` field in every webhook payload.

## Reference implementation

The current code implements the schema as follows:

| Section | Status | File(s) |
|---|---|---|
| Input A — CRM context | **fully implemented** | `app/pre_filter.py`, `app/conversation.py` |
| Input B — Call history | not yet | documented here as v3 |
| Input C — Active overrides | not yet | documented here as v3 |
| Input D — Compliance context | not yet | documented here as v3 |
| Input E — Live audio | **fully implemented** | `app/audio/streaming_io.py`, `app/static/index.html` (browser VAD) |
| Per-turn audit (incl. `move_played`, `directives_fired`, `scenario_inferred`) | **fully implemented** | `app/audit.py` |
| Per-turn audit — STT/LLM/TTS latency breakdowns | partial | per-call total only; per-turn split is v3 |
| End-of-call `outcome` + `outcome_detail.{reason, amount, date, mode}` | **fully implemented** | `app/outcome/schema.py`, `app/outcome/extractor.py`, `app/outcome/webhook.py` |
| End-of-call `outcome_detail.{handoff, callback_sla_hours, policy_rationale}` | **fully implemented (Layer 2)** | `app/policy.py`, `app/conversation.py::_apply_policy_to_outcome` |
| `refused` reason split (`dnd` vs `refused_current_call`) | **fully implemented (Layer 3)** | `app/fsm.py`, `app/intent_classifier.py` |
| `cta` block | partial | derived on demo UI from outcome + policy-driven detail; v3 will return the full block from the bot |
| `agent_brief` | not yet | v3 |
| `transcript_summary` | **fully implemented** | `app/outcome/extractor.py` |
| `compliance_flags[]` | **fully implemented** | flows from `app/validator.py` violations |
