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

**Four structural changes have shipped beyond what the original PRD specified.** Each one moved a behavioural rule out of prose and into deterministic code, on the same principle the PRD anchored on (compliance lives in code, the LLM is the last line of defence — not the only one).

### §7 Bot core behaviour deltas (NEW — shipped in v2)

| Δ | What the original PRD assumed | What v2 actually does | Why the change |
|---|---|---|---|
| **§7.1** Refusal handling | One outcome: `refused`. Conflated TRAI DND with in-call refusal. | Split into `refused/dnd` (permanent marketing suppression; collections may continue per RBI FPC) and `refused/refused_current_call` (retry after cooling-off). Two-strike state machine. | Customer who said "leave me alone" was being permanently DND-suppressed, which is regulatorily wrong AND lost them as a future contact opportunity. |
| **§7.2** Segment policy in code, not prose | Tone-by-segment via prompt modifiers only. Thresholds (PTP horizon, abuse strikes, when to escalate to human) were prose nudges in prompts. | `app/policy.py` resolves a `SegmentPolicy` per call. Numbers (`max_ptp_days`, `abuse_strikes_allowed`, `callback_sla_hours`, `human_takeover_on_refuse`) are deterministic code. | LLM was confirming "I'll pay in 2 months" from frequent-late defaulters because nothing in code pushed back. Policy table now does. |
| **§7.3** Move ladder per state | LLM generated freely within FSM state. No per-turn move discipline. | Each state has an ordered ladder of "moves" (`ASK_DATE` → `ASK_MODE` → `CONFIRM_PTP` → `OFFER_APP_LINK` → `OFFER_PARTIAL` → `OFFER_CALLBACK`). LLM is told which move to play; it cannot replay a move in the same state; ladder exhaustion forces graceful close. | Bot was looping on "when will you pay / when will you pay" because nothing in code tracked what had already been asked. |
| **§7.4** Commitment overreach validator | Validator caught policy violations (balance disclosure, threats, waivers). Did not police implicit commitments. | New `COMMITMENT_OVERREACH` rule blocks "we won't call you again", "I'll personally call you back", "we'll reverse the fee" etc. — even from DND_ACKNOWLEDGED state. | Bot was promising "we will not call you again" while the structured outcome routed to `continue_bot, sla 72h` — direct contract mismatch with the orchestrator and an RBI FPC violation in production. |
| **§7.5** `[END_CALL]` guard | LLM emitting `[END_CALL]` unconditionally terminated the call. | FSM owns when the call ends. LLM may REQUEST close via `[END_CALL]`; it's only honoured when `decision.terminal_outcome` is set or the state is terminal-flavoured. | LLM was self-closing on hostility cues even when the FSM said "stay, calm reset." |
| **§7.6** Bot-internals panel | Audit panel showed state + intent + validator. | Added `scenario_inferred` (behavioural category, discovered mid-call from intent), `move_played` (which ladder move was forced), `directives_fired[]` (every deterministic guardrail tagged by layer prefix). | Compliance and Ops needed live explainability — not just "the validator passed" but "this turn was a hardship-category turn, the FSM forced the empathy probe, the policy directive on PTP horizon also fired." |

### Still-additive items (v3 scope)

| Change | Files affected | Existing behaviour preserved? |
|---|---|---|
| Add Part 5 to assembled prompt: Call History Block | `prompt_builder.py`, `conversation.py` | Yes — empty block on Call 1 means no change to current behaviour |
| FSM becomes attempt-aware (opener variation; stricter routing after broken PTPs) | `fsm.py`, `prompts/fsm_states/intro.txt` | Yes — default attempt-1 behaviour unchanged |
| Outcome extractor produces `agent_brief` | `outcome/extractor.py`, `outcome/schema.py` | Yes — additive field |

These are additive. The current 42-scenario eval baseline remains valid; v3 work would add ~10 new scenarios specifically testing repeat-call behaviour (Attempt 2 with a broken PTP, Attempt 3 with no history, hardship-after-second-attempt, etc.).

### Persona coverage: segment-matrix audit (NEW)

The demo persona set (`eval/personas.csv`) was audited against the PRD's three pre-call segment axes (tier × DPD × default_history). Five empty cells were filled in commit `e7e7de7` (total 32 → 37 personas):

- **P33** Ritika Bhatt (spark / DPD 26 / first) — first-miss spark drifting late.
- **P34** Manoj Pandey (spark / DPD 28 / frequent) — strictest segment policy: 7-day cap, 1 abuse strike, 24h SLA, mandatory takeover.
- **P35** Rajat Malhotra (apex / DPD 14 / first) — premium customer slipping into mid-DPD.
- **P36** Kavita Iyengar (apex / DPD 9 / frequent) — affluent habitual defaulter; nearprime so not pre-filter-blocked.
- **P37** Vinod Yadav (spark / DPD 8 / frequent) — chronic low-DPD spark; A_reminder tone vs frequent modifier.

The earlier "scenario" filter on the demo picker (`PTP` / `hardship` / `adversarial` / `language`) was dropped — those are *behavioural* categories the bot discovers mid-call via the intent classifier, not pre-call attributes a reviewer should select. The taxonomy is preserved in the eval (`eval/scenarios.yaml`) where it belongs.

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
