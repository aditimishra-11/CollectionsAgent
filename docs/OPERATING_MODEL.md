# Operating Model

How Mumbai Bank actually runs this voicebot day-to-day, and who is responsible for what.

## The build–operate split

The original architecture doc and PRD focus on what the bot does. They don't address who runs it. In production this matters because the failure modes from misconfiguration or operational mistakes are different from bot-behaviour failures — and the people responsible are different.

| Owns | GreyLabs (builder) | Mumbai Bank (operator) |
|---|---|---|
| LLM choice and version pinning | ✓ | |
| Prompt files (base, strategies, FSM states, closes) | ✓ | |
| Eval rubric + scenario maintenance | ✓ | |
| Compliance rule encoding (validator regex, NEVER list) | ✓ | |
| Audio stack (STT vendor, TTS vendor, AEC) | ✓ | |
| Model upgrades, regression testing | ✓ | |
| Retry policies (attempts × interval) | | ✓ |
| Dial windows (within TRAI bounds) | | ✓ |
| Segment routing rules | | ✓ |
| Callback SLAs per reason | | ✓ |
| Pre-call filter conditions (block lists) | | ✓ |
| Webhook endpoints (CRM integration) | | ✓ |
| Operator dashboards | | ✓ |
| Manual override decisions | | ✓ |

**Key principle:** the bank configures *policies*, GreyLabs owns *behaviour*. The bank cannot edit prompts or the validator — that's regulatory liability we hold. The bank can configure when and how the bot is used.

## The four internal personas

### 1. Collection Manager (function head)

**Goal:** maximise long-term portfolio recovery + retention.
**Cadence:** daily and weekly review.

**Needs:**
- Recovery and cure rates by tier (Spark / Edge / Apex) over time
- Roll rates DPD 10 → DPD 30 → DPD 60
- 90-day post-call churn (the PRD's true north star)
- Bot vs human-only cohort comparison
- Cost per ₹ recovered, by segment
- Outliers: Apex churn spike, sub-prime worsening, segment-level compliance dips

**Doesn't need:** transcript-level access (that's Ops), config controls (Admin)

### 2. Collection Ops (floor lead)

**Goal:** keep the live queue moving cleanly, escalations handled, exceptions cleared.
**Cadence:** continuous through the working day.

**Needs:**
- Live queue: who's being called now, in hardship hold, scheduled for human callback
- Per-case drill-down: any customer → full call history + per-call transcripts + outcome trajectory + override log
- Manual override controls: pause customer, force route to human, mark deceased/bankrupt/legal-hold, change call window
- Bot platform health: failed calls, webhook delivery errors, latency anomalies
- Daily alerts: hardship cases breaching SLA, broken PTPs needing follow-up

### 3. Collection Admin (configuration / scheduler)

**Goal:** keep retry, scheduling, and routing rules aligned with bank policy + regulation.
**Cadence:** weekly review, monthly tuning.

**Needs:**
- Retry policy editor (attempts × interval, per outcome type)
- Dial window editor (within TRAI 8am–7pm)
- Segment routing rules (e.g., "Apex + sub-prime → always human"; admin can tighten)
- Callback SLAs per reason (medical 24h, waiver 48h, dispute 48h)
- Pre-call filter rule editor (block deceased / bankrupt / legal-hold)
- Webhook endpoint management (sandbox / production CRM URLs)

### 4. Compliance / Risk (added — not in original spec)

**Goal:** ensure every interaction is regulator-defensible. Audit on a sample.
**Cadence:** monthly audit + ad-hoc on complaint.

**Needs:**
- Zero-violation rate trend over time (must be 100%)
- Sampled call review: random N% transcripts with full audit trail per turn
- Override audit log: every manual override (who, when, why, approval)
- Regulatory rule mapping: which prompt section / validator rule maps to which RBI/DPDP/TRAI clause
- Complaint correlation: which calls preceded customer complaints?
- Rule-change deployment timeline: when did we update X after regulator published Y?

## Operational metrics — how each persona's metric feeds the bank's north star

The bank's north star (PRD §4): **payment resolved within 7 days + no 90-day churn, segmented by card tier.**

Each persona has operational metrics that LEAD INTO that north star — they're upstream indicators, not replacements.

| Persona | Operational metric | How it feeds the north star |
|---|---|---|
| Manager | % accounts cured within DPD 30 by bot alone, by tier | Direct: cured early = no 90-day churn risk |
| Ops | % cases routed correctly within SLA (no missed escalations, no over-escalations) | Wrong routing → unhappy customer → churn |
| Admin | Time-to-deploy a regulatory rule change after RBI publishes | Faster = fewer days of compliance risk |
| Compliance | Zero critical violations + sampled audit pass rate | Single violation = regulatory event = reputational + financial cost |

## Manual override — policy

Overrides are necessary (legitimate cases: customer pays via branch but CRM hasn't synced, customer goes into bereavement, legal hold) but represent the largest compliance attack surface in the operating model.

**Three categories:**

| Override type | Who can do it | Approval required | Audit |
|---|---|---|---|
| Operational (pause customer, change call window) | Any Ops | No | Logged |
| Strategy (route Edge customer through Apex strategy) | Senior Ops | Yes — Manager sign-off | Logged + flagged for Compliance review |
| Regulatory (mark legal-hold, mark deceased, override DND) | Senior Ops | Yes — Compliance sign-off | Logged + included in monthly audit |

Every override must capture: who, when, what, why. This is the audit-trail Compliance reviews monthly.

## Configuration vs prompts — the firewall

The single most important boundary in this operating model:

**The bank can NEVER edit:**
- Prompt files (`prompts/base.txt`, strategies, FSM states, closes)
- Validator pattern banks
- FSM transition logic
- Intent classifier patterns

**The bank CAN edit:**
- Retry intervals
- Dial windows
- Segment routing rules
- Callback SLAs per reason
- Pre-call block lists
- Webhook endpoints

This firewall protects both sides: the bank doesn't accidentally introduce a compliance violation by editing a prompt, and GreyLabs doesn't get blamed for an outcome that resulted from an operator override.

Prompt and rule changes go through GreyLabs' release process: change → regression eval → human review → staged rollout. A typical update from regulator publication to production is 5–10 working days.
