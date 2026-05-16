# PRD v2 — Deltas from the Original

Additive changes to the original Mumbai Bank Collections Voicebot PRD, surfaced once the operating-model and multi-call dimensions were factored in. The original PRD (§1–4 — problem, customer segmentation, customer goals, customer metrics) remains correct and unchanged.

## What the original PRD covered well

- End customer segmentation (Spark / Edge / Apex) with 7 dimensions
- Customer experience as the product (dignity, relationship preservation)
- Anti-goal: never optimise for pressure-extracted commitments
- North star: payment in 7 days + no 90-day churn, segmented by tier
- Per-segment success criteria

## What the original PRD missed

It treated the bot as a finished product handed to the bank. In reality:

1. **No mention of who runs the bot inside the bank.** The bank has Manager / Ops / Admin / Compliance personas, each with their own needs.
2. **No build–operate split.** It's not specified what the bank can configure vs what only GreyLabs can change. This boundary matters for liability and regulatory compliance.
3. **No multi-call lifecycle.** Bot calls are treated as one-shot. Real cycles are 3 calls + human takeover; each call should benefit from prior context.
4. **North star is end-customer-only.** Each operator persona needs an operational metric that feeds the north star, not just the customer metric.
5. **Data schema is implicit.** What the bot receives vs emits vs deliberately doesn't see is never stated formally.

## Proposed additions to the PRD

### §2.4 Internal users (NEW)

Mirror the depth of §2.1–2.3 (end-customer segmentation), but for the four internal personas: Collection Manager, Collection Ops, Collection Admin, Compliance/Risk. For each:

- Role description
- Cadence (continuous / daily / weekly / monthly)
- What they monitor
- What they configure (if anything)
- What they should NEVER touch

Reference: `OPERATING_MODEL.md` has the full content.

### §2.5 Operating model (NEW)

Explicit table of what's owned by GreyLabs (builder) vs Mumbai Bank (operator). The firewall around prompts/validator/FSM (not bank-editable) is the most important point.

### §3.1 Operator goals (NEW)

Distinct from §3 (end-customer goals). Operator goals are upstream of customer outcomes:

- Manager: maximise cure-without-human rate; minimise cost per ₹ recovered
- Ops: keep escalation precision high; resolve exceptions same-day
- Admin: deploy regulatory rule changes within 5 working days of publication
- Compliance: maintain 100% zero-violation rate; defensible audit on every call

### §4.1 Operational metrics (NEW)

Layered under the bank-level north star. Each operator persona has their own dashboard with their own metrics — they roll up to the north star, but day-to-day they look at:

- Manager: cohort recovery rates, roll rates, 90-day churn, cost-per-call
- Ops: live queue size, SLA breaches today, manual overrides last 24h
- Admin: time-to-deploy metric on the latest rule change
- Compliance: zero-violation rate trend; sampled audit pass rate; override audit completeness

Important clarification: the bank's north star (payment in 7 days + no 90-day churn) stays at the **top**. Operator metrics are LEADING INDICATORS — they don't replace the north star, they predict it.

### §5 Multi-call lifecycle (NEW)

The per-customer cycle as a 3-attempt sequence with human takeover at the end (or sooner under hard conditions). Includes:

- Per-customer state machine (sits above the per-call FSM)
- Outcome → next-action mapping
- Mandatory human-takeover conditions
- Call History Block format (the bot's input on attempts 2 and 3)
- Per-attempt opener variation (1 / 2 / 3 = fresh / check-in / final)
- Agent brief format (one paragraph for the human picking up)

Reference: `MULTI_CALL_DESIGN.md` has the full design.

### §6 Data schema (NEW)

Formal contract between bank-side orchestration and the bot:

- Bot input: CRM context, call history, active overrides, compliance context
- Bot output per-turn: audit log with FSM state + intent + validator + latency
- Bot output end-of-call: terminal outcome + structured CTA + agent brief + handoff recommendation
- Fields the bot deliberately does NOT see (defence in depth)

Reference: `DATA_SCHEMA.md` has the full schema.

## Is the north star still appropriate? — direct answer

**Yes.** The original north star (*payment in 7 days + no 90-day churn, segmented by tier*) is the BANK's master metric. It is unchanged.

What's added in v2: each operator persona has their own metric, but those are **leading indicators**, not replacement north stars. The hierarchy:

```
North star (Bank-level):
  Payment in 7 days + no 90-day churn, segmented by tier
                       ▲
                       │  feeds into
        ┌──────────────┼──────────────┐
        │              │              │
   Manager metric   Ops metric    Admin metric    Compliance metric
   (cohort cure    (SLA          (rule-deploy   (zero violations +
    rates, cost)    precision)    cycle time)    audit pass rate)
```

Adding a separate operator north star would dilute the bank-level focus. Keep it singular at the top, add operational metrics as leading indicators below.

## Does the bot's core workflow / prompts need changes? — direct answer

**Yes, in three additive places (no destructive changes):**

| Change | Files affected | Existing behaviour preserved? |
|---|---|---|
| Add Part 5 to assembled prompt: Call History Block | `prompt_builder.py`, `conversation.py` | Yes — empty block on Call 1 means no change to current behaviour |
| FSM becomes attempt-aware (opener variation; stricter routing after broken PTPs) | `fsm.py`, `prompts/fsm_states/intro.txt` | Yes — default attempt-1 behaviour unchanged |
| Outcome extractor produces CTA + agent_brief + handoff_recommendation | `outcome/extractor.py`, `outcome/schema.py` | Yes — additive fields; older CRM integrations ignore them |

These are additive. The current 42-scenario eval baseline remains valid. v3 work would add ~10 new scenarios specifically testing repeat-call behaviour (Attempt 2 with a broken PTP, Attempt 3 with no history, hardship-after-second-attempt, etc.).

## Priority for the next 2 weeks (v3 scope)

If the assignment were "what would you build with 2 more weeks", this is the answer:

| Week 1 | Week 2 |
|---|---|
| Implement Call History Block input | Build the operator dashboards (Ops + Manager MVP) |
| Extend outcome schema with CTA + agent_brief | Wire CRM webhook to accept new schema |
| Add attempt-aware FSM behaviour | Configuration UI for Admin (retry / dial windows / SLAs) |
| Add 10 multi-call scenarios to the eval | Audit log viewer for Compliance |

The bot itself is solved. The product is the operating system around it.

## Document index

| Doc | Purpose |
|---|---|
| `OPERATING_MODEL.md` | The 4 internal personas + build/operate split |
| `MULTI_CALL_DESIGN.md` | Per-customer state machine, handoff rules, repeat-call prompt block |
| `DATA_SCHEMA.md` | Formal contract: what the bot sees, emits, doesn't see |
| `PRD_v2_DELTAS.md` (this doc) | What the original PRD missed, what to add |
