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
| `fsm_state_before` | enum | |
| `fsm_state_after` | enum | |
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
  reason: enum (medical / job_loss / business_failure / mental_distress
                / natural_disaster / abuse / waiver / dispute / deceased
                / language_callback / dnd)
  urgency: enum (high / medium / low)

refused:
  reason_stated: string (optional, customer's words)

wrong_number, no_answer: {} (no detail)
```

### `cta` — what the CRM should do (NEW)

This is the structured CTA per outcome. Drives the orchestrator's next-action selection.

```
{
  "action": enum (one of:
    "monitor_payment"       — for promise_to_pay
    "verify_payment"        — for already_paid
    "schedule_human_callback" — for callback_request and human_callback_required
    "suppress_number"       — for wrong_number
    "retry_per_policy"      — for no_answer
    "log_and_review"        — for refused
    "log_dnd_no_retry"      — for refused + DND
  ),
  "sla_hours": int (e.g. 24 for medical, 48 for waiver/dispute, 168 for refused-cooldown),
  "do_not_pressure": bool,
  "target_team": string ("hardship desk", "waivers team", "bereavement", "language pool", null),
  "next_call_eligible_at": ISO datetime (when the bot may call this customer again),
  "max_bot_attempts_remaining": int (default 3, decrements per cycle)
}
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

The current code partially implements this schema:
- Input A (CRM context): fully implemented in `app/pre_filter.py` + `app/conversation.py`
- Input B–D (history, overrides, compliance flags): **not yet implemented**; documented here as v3
- Output per-turn: implemented in `app/audit.py`
- Output end-of-call: partially in `app/outcome/schema.py` + `app/outcome/extractor.py` + `app/outcome/webhook.py`; CTA block and agent_brief are v3 additions
