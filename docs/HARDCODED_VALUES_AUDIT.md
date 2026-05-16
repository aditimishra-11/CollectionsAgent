# Hardcoded Values Audit

Catalog of every magic number, hardcoded string, and "should-be-data-driven" constant in the v2 codebase, plus the principle that decides whether each one is **a bug** (move it) or **a tuning knob** (keep it).

## Decision rule

A constant is a **bug** if it represents knowledge the orchestrator/CRM/operator should own. Move it to data (CRM payload) or config (env-var). A constant is **acceptable** if it represents a design choice of the bot itself (architecture parameter, tuning constant) that doesn't change per call, per customer, or per bank.

| Category | Bug or knob? |
|---|---|
| Per-customer cycle data (MAD, billed, dates, last payment) | **Bug** → CRM payload |
| Per-bank policy values (late fee, interest rate, MAD %) | **Bug** → config / env |
| Per-segment thresholds (max_ptp_days, abuse_strikes, SLA) | **Bug** → policy table (already done) |
| Per-call runtime (MAX_TURNS, max_tokens, temperature) | **Bug** → config / env |
| Conversation cost rates (USD/MTok, INR/STT call) | Knob (already in config) |
| AEC + VAD tuning constants | Knob (acceptable) |

## Status by item

### P0 — fixed in this audit (commit pending)

| # | Item | Where it was | Where it is now |
|---|---|---|---|
| 1 | `partial_floor_inr` = ₹2,000 / ₹3,000 / ₹5,000 per segment | `app/policy.py` (hardcoded constants per row) | Replaced with `resolve_partial_floor(ctx, policy)` = `max(MAD_from_CRM, segment_overlay)`. MAD comes from the CRM payload, overlay is the segment-specific minimum we'd raise the floor to. |
| 2 | `minimum_amount_due`, `billed_amount`, `statement_date`, `payment_due_date`, `last_payment_*` | Not in `CRMContext`. Not in `personas.csv`. | Added as fields on `CRMContext` (defaults to None for backwards compat). `personas.csv` backfilled for all 37 personas with derived realistic values (MAD = 5% of outstanding, dates relative to DPD). |
| 3 | Late fee ₹750, interest 3.5%/month, threshold ₹10,000 | Hardcoded in `prompts/base.txt`, `prompts/strategy_b_problem_solving.txt` | Moved to `app/config.py`: `LATE_FEE_INR`, `LATE_FEE_APPLIES_ABOVE_INR`, `MONTHLY_INTEREST_PCT`. Rendered via the new BANK_FACTS prompt section. Prompts now reference "see BANK FACTS" instead of literal numbers. |
| 4 | `MAX_TURNS = 16`, `max_tokens = 250`, `temperature = 0.55` | `app/conversation.py` module constants | Moved to `app/config.py` as env-overridable: `MAX_TURNS`, `LLM_REPLY_MAX_TOKENS`, `LLM_REPLY_TEMPERATURE`. |
| 5 | Move-ladder OFFER_PARTIAL hint ("₹2,000–5,000") | `app/fsm.py` `MOVE_DIRECTIVE` dict | Replaced with a reference to the resolved policy block ("use the floor figure from SEGMENT POLICY above"). |
| 6 | Speech-authority commitments in close templates | `prompts/closes/*.txt` (all 8 templates) | Rewritten to remove specific-clock / specific-person / scope commitments. New pattern: "I've logged this; the team handles cases like this; the helpline is open." Validator's `COMMITMENT_OVERREACH` rule broadened to catch the variants. Fast-path closes now pass through `validate_response()` before TTS (was bypassed before). |
| 7 | LLM SPEECH AUTHORITY rule | Not stated anywhere | New section in `prompts/base.txt` enumerating what the bot may/may not commit (parallels the no-balance-without-OTP rule). |

### P1 — acceptable for v2 demo, documented for v3

| # | Item | Why it's a knob (or not) |
|---|---|---|
| 8 | Pre-filter band cutoffs: bureau prime/nearprime/subprime at 750/650, util low/medium/high at 20/70 | These ARE the bank's risk-band definitions. Belong in config eventually so a different bank can override; out of demo scope. |
| 9 | Age modifier cutoffs: 0.5y / 3y | Same as above — a customer-tenure taxonomy decision, not a per-call decision. |
| 10 | Pre-filter blocks: `dpd > 30`, apex `bureau_score < 650` | Architecture choice (what's in scope for THIS bot). Configurable for production deployment. |
| 11 | Browser VAD tuning: `positiveSpeechThreshold: 0.65`, `minSpeechFrames: 10`, `BARGEIN_HOLD_MS: 350` | Hardware/UX constants tuned for typical laptop + mic. Knob, not data. |
| 12 | AEC: `AEC_FILTER_LENGTH = 2048`, `AEC_MU = 0.1` | Audio-stack tuning. Already env-var-overridable in config. |
| 13 | Cost rates: `LLM_INPUT_USD_PER_MTOK`, `STT_INR_PER_CALL`, `USD_TO_INR` | Vendor pricing. Already env-var-overridable. |

### P2 — known-acceptable trade-offs

| # | Item | Note |
|---|---|---|
| 14 | OTP-protected balance rule | Hardcoded in base.txt and validator. INTENTIONAL — this is the architecture's core principle, must not be configurable. |
| 15 | Outcome enum (7 types) | Hardcoded across schema + FSM + extractor. Versioned via the data-schema contract; would require a coordinated migration to change. |
| 16 | Intent classifier (30 intents) | Each intent's regex bank is hand-tuned. Better long-term: structured-output extraction from the LLM. Captured as v3 work in [`docs/PRD_v2_DELTAS.md`](PRD_v2_DELTAS.md). |

## Principle (the underlying take-home from this audit)

The pattern of every P0 item above is the same:

> *Behaviour the orchestrator/CRM/operator should own ended up in code or prompts because the data schema didn't include the input.*

Fix the schema first, then derive at pre-filter, then inject the derived facts into the prompt. Where I shortcut that sequence and dropped a constant directly into a prompt or a policy row, you got: hardcoded ₹3,000 partial floors, hardcoded "24-hour callback" commitments, hardcoded ₹750 late fee literals scattered across three files. Each one became a future maintenance bug.

The cleaner discipline going forward:

1. **What does the bot need to reason correctly?** → CRM schema input
2. **What does the bank set as policy?** → config / env
3. **What does the segment decide?** → policy table
4. **What does the LLM see?** → derived prompt block from steps 1–3
5. **What does the validator wall off?** → the rare cases where the LLM still slips

If a constant appears outside that chain, it's wrong.
