# Mumbai Bank Collections Voicebot — Submission

**Aditi Mishra · GreyLabs AI PM take-home · {{DATE}}**
**Repository:** [github.com/aditimishra-11/CollectionsAgent](https://github.com/aditimishra-11/CollectionsAgent)
**Demos:** [{{DEMO_LINK_1}}] · [{{DEMO_LINK_2}}] · [{{DEMO_LINK_3}}]

---

## 1 · What was built

An outbound collections voicebot for Mumbai Bank credit card customers in the DPD 1–30 window, plus the operating system around it. Two versions:

- **v1** — the literal starter prompt from the brief, unmodified. Kept as the baseline so the v1 → v2 delta is real, not rhetorical.
- **v2** — production architecture. Pre-filter → intent classifier → 13-state FSM → segment-aware prompt composer → LLM → response validator → TTS. Plus a terminal-style web frontend with continuous Silero VAD, live bot-internals panel, and CRM-shaped outcome posting.

The bot is the product surface. The PRODUCT is the operating system around it: four internal personas (Manager / Ops / Admin / Compliance), the multi-call lifecycle (3 bot attempts → human takeover), and the data contract between bank-side orchestration and the bot. Those are designed in four supporting docs in `docs/`.

## 2 · Headline result

> _The metrics below are the eval result **before** the four structural layers landed this week. A re-run after the layers is in progress; updated numbers will be folded in once it completes._

| Metric | v1 baseline | v2 |
|---|---:|---:|
| Calls that would pass full QA | 13% | **{{V2_FULL_PASS}}%** |
| Zero policy violations | 13% | **{{V2_ZERO_VIOLATION}}%** |
| P0 (zero-tolerance) compliance | 13% | **{{V2_P0_PASS}}%** |
| Apex tone preservation | 0% | **{{V2_APEX_TONE}}%** |
| Right tone for segment | 87% (vacuous) | **{{V2_TONE_RIGHT}}%** |
| Mean cost per call (LLM + STT + TTS) | — | **₹{{V2_COST}}** |
| Estimated voice p95 round-trip | — | **{{V2_LATENCY_P95}} s** (target < 5 s) |

Across **42 scenarios** including 11 adversarial stress tests. Languages: English, Hinglish, Hindi, Tamil, Malayalam. Priority-graded: 16 P0 (zero-tolerance), 26 P1.

## 3 · Why this was hard, in three product tensions

**Tension 1 — Tone vs rigour, by segment.** An Apex customer who forgot to update auto-debit needs concierge; a frequent defaulter at DPD 22 needs firmness. Same script kills retention on the first and recovery on the second. Solved by composing the prompt from three strategies × six modifier dimensions (`app/prompt_builder.py`) — each call gets a freshly assembled prompt that's right for *this* customer.

**Tension 2 — Compliance is non-negotiable, but customers say arbitrary things.** RBI Fair Practices Code, RBI Master Direction on Credit Cards 2022, DPDP Act 2023, TRAI DND. Prompts cannot be relied on to honour these — the LLM can be pushed off-script by a determined customer. Solved by putting compliance in code: a 16-rule validator on every LLM output, a `NEVER` list grounded in the actual regulations, a pre-filter that blocks calls that shouldn't happen, and (this week) a `SegmentPolicy` table where the thresholds live as data.

**Tension 3 — "PTP captured" is gameable.** The PRD calls it out explicitly as an anti-goal. A bot can hit 95% PTP-capture-rate by pressuring customers into vague promises that never materialise. The eval uses **PTP specificity** (date + mode captured, normalised to enums) as the proxy for PTP Kept Rate — which can't be measured in a take-home, but specificity correlates well with kept rate in industry data.

## 4 · Stack

| Layer | Choice | Why |
|---|---|---|
| LLM | OpenAI GPT-4.1 mini | Best cost/quality at this temperature; reliable function-calling. |
| LLM judge | OpenAI GPT-4o (cross-vendor wrt the bot) | Reduces correlated failure between bot and grader. |
| STT | Sarvam Saaras V3 | Handles Hinglish + 10 Indian languages; translate-to-English mode is essential. |
| TTS | Sarvam Bulbul V3 | Authentic Indian accent — *meaningfully* better than Polly/ElevenLabs on Indian English. |
| VAD | Silero (browser + CLI) | Continuous mic stream; end-of-utterance detection good enough for barge-in. |
| AEC | Custom NLMS in numpy (22.7 dB reduction) | Avoids C dependencies; portable across platforms. |
| Orchestration | Plain Python — synchronous loop | The loop is what reviewers should read. No Pipecat. |
| Web frontend | FastAPI + single-page HTML | Terminal aesthetic; live SSE for bot replies + audit events. |
| Outcome sink | webhook.site (production: Salesforce / Leadsquared) | The bot doesn't care; it posts a typed payload. |

## 5 · The four structural decisions that defined v2

Each one moves a behavioural rule out of prose and into deterministic code, on the same principle as compliance: the LLM is the last line of defence, not the only one.

### 5.1 Refusal is two outcomes, not one

The first transcript that exposed this: a frustrated customer said *"I don't want to talk to anyone now"* and the bot closed with *"We will not contact you further."* Outcome went out as `refused/dnd` — TRAI DND permanent suppression. The customer hadn't registered for DND; they were frustrated. By treating in-call refusal the same as regulatory DND, the bot was both lying to the customer (a human collector would still call per RBI FPC) and permanently burning them as a contact opportunity for a 90-second negotiation.

**Fix:** intent classifier now distinguishes `do_not_call` (strict phrasings — "register me on DND", "don't call me again") from `refuse_current_call` (in-the-moment frustration). FSM gives the latter two strikes — first strike offers ONE callback, second strike transitions to `REFUSAL_CLOSE` with `outcome=refused, reason=refused_current_call` (not `dnd`). The orchestrator can retry per cooling-off; the customer is not permanently suppressed.

### 5.2 Segment policy in code, not prose

The bot had segment-aware *tone* (strategies + modifiers) but no segment-aware *thresholds*. So when a DPD-22 frequent defaulter said *"I'll pay next month after my salary,"* the bot confirmed cheerfully — there was nothing in code that knew this segment's PTP horizon cap should be 7 days.

**Fix:** `app/policy.py` resolves a `SegmentPolicy` per call from CRM context. Six priority-ordered rows; each segment maps to thresholds (`max_ptp_days`, `partial_floor_inr`, `abuse_strikes_allowed`, `human_takeover_on_refuse`, `callback_sla_hours`). For frequent-late segments: 7-day PTP cap, 1 abuse strike, 24h SLA, mandatory human takeover on refusal. The policy values are operator-tunable; the resolver logic is GreyLabs-owned.

The policy is stamped onto every outcome (`policy_rationale: frequent_late_strict`) for audit. The orchestrator routes from `outcome_detail.handoff` and `outcome_detail.callback_sla_hours` rather than re-deriving from the outcome string — so a refused call from a high-risk segment correctly upgrades to `human_callback_required` without the bot having to know.

### 5.3 Move ladder per state

Symptom: the bot would loop on *"when will you pay / when will you pay,"* asking the same question across 4–6 turns. The earlier fix was prose — paragraphs in the prompt saying *"be proactive, don't loop."* That's behaviour-via-prose, the exact thing the architecture was supposed to avoid.

**Fix:** each conversational state has an ordered ladder of moves in `app/fsm.py::LADDERS`. For `PTP_PROBE`: `ASK_DATE` → `ASK_MODE` → `CONFIRM_PTP` → `OFFER_APP_LINK` → `OFFER_PARTIAL` → `OFFER_CALLBACK`. The FSM picks the next unplayed move each turn; the prompt composer injects it as a hard directive; the LLM tags its reply with `[MOVE: X]`; the conversation layer records the move so it can't be played again in this state. Ladder exhaustion forces a graceful close. The bot now cannot loop, by construction.

### 5.4 Commitment-overreach validator

The bot was saying *"we will not call you again"* while the structured outcome routed `continue_bot, sla 72h`. Two facts in direct contradiction: the customer was told one thing; the multi-call orchestrator (which reads the structured outcome, not the transcript) would have queued another bot call 72 hours later.

The same logic that keeps balance off the LLM applies to commitments: **the bot cannot promise what it does not control.** A human collector will call regardless of any bot promise — DND suppresses marketing, not legitimate dues. The bot's voice is not authorised to make that commitment.

**Fix:** new `COMMITMENT_OVERREACH` validator rule with 13 phrase patterns covering no-future-contact, manager personal callback, fee reversal, refund, and "your number has been removed." Same shape as every other validator rule: detect → substitute safe fallback → log `commitment_overreach` in `compliance_flags`. Applies in every state, including `DND_ACKNOWLEDGED`. The `DND_ACKNOWLEDGED` state prompt was rewritten to spell out the distinction (preference logged ≠ queue suspended) and lists the banned phrasings so the LLM doesn't drift into them.

### Plus one control-flow guard

The LLM was able to emit `[END_CALL]` and unilaterally close the call, regardless of what the FSM decided. After abuse strike 1, when the FSM said *"stay, calm reset,"* the LLM could still self-terminate. Now: the FSM owns when the call ends; `[END_CALL]` is stripped if `decision.terminal_outcome` isn't set and the state isn't terminal-flavoured. Logged as `guard:unauthorised_end_stripped`.

## 6 · Eval methodology

Re-framed for PMs and bankers, not ML researchers. Four blocks:

- **CAN IT SHIP?** — regulatory and brand-safety gates (P0 compliance, zero-violation rate, Apex tone preservation).
- **DID IT WORK?** — effectiveness on the collections job (right outcome, slot capture, PTP specificity).
- **HOW DID IT FEEL?** — customer experience (empathy, sentiment trajectory, context retention — LLM judge on a Likert 0–5).
- **WAS IT FAST?** — latency (p50 / p95 LLM, estimated voice round-trip).

Each metric is mapped to the production outcome it predicts. Methodology: cross-vendor LLM judge (bot on GPT-4.1-mini, judge on GPT-4o), evidence-required scoring, isolated judge per dimension. P0 scenarios weighted 3×, P1 weighted 2×. Per-bucket / per-difficulty / per-language breakdowns in `eval/results_v2.csv`.

**What this eval cannot measure** (and we said so explicitly): real recovery rate, cure rate, roll rate, 90-day post-call churn, CSAT, RPC rate. Those need live deployment and weeks of payment data. The eval's job is *behavioural assurance* — the metrics that predict good production outcomes.

## 7 · Stress-test coverage

42 scenarios across 8 buckets: factual, compliance, scope, adherence, relationship, privacy, regulatory, adversarial. Highlights:

- **Adversarial (11 scenarios):** prompt injection, fake RBI authority, lawyer threat, harassment, deceased pretext, "tell me my friend's balance," "you sound like an AI scam."
- **Compliance (P0-weighted):** balance disclosure without OTP, third-party debt disclosure, government-body impersonation, role-break refusal, waiver pre-approval.
- **Language:** Hindi (romanised + translated), Tamil, Malayalam handoff.
- **Relationship:** Apex tone preservation under sustained delinquency; first-time-miss respect.

## 8 · Beyond the bot — what the PRD missed

The original PRD treated the bot as a finished product handed to the bank. In reality, four internal personas have to *run* it. v2 fills that gap in four supporting docs:

| Doc | What's in it |
|---|---|
| [`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md) | The 4 internal personas (Manager / Ops / Admin / Compliance), build-vs-operate split, manual override policy. |
| [`docs/MULTI_CALL_DESIGN.md`](docs/MULTI_CALL_DESIGN.md) | Per-customer state machine, outcome → next-action mapping, mandatory human-takeover conditions, repeat-call prompt block. |
| [`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md) | What the bot sees, emits, deliberately doesn't see — the formal contract. |
| [`docs/PRD_v2_DELTAS.md`](docs/PRD_v2_DELTAS.md) | Additions to the original PRD: internal users, operational metrics, multi-call lifecycle, the four structural deltas. |

The PRD's north star (payment in 7 days + no 90-day churn, segmented by tier) is unchanged — it's the bank's master metric. What's added in v2 is each operator persona has their own *leading-indicator* metric: Manager (cohort cure rates), Ops (SLA precision), Admin (rule-deploy cycle time), Compliance (zero-violation trend + sampled audit pass rate). These predict the north star; they don't replace it.

## 9 · What was cut, deliberately

- **No Pipecat.** Overkill for a sequential loop; the loop is what reviewers should read.
- **No real telephony.** Local mic satisfies the deliverable; Exotel is documented as the production path.
- **No mobile app.** Banks call customers; customers don't install collections apps.
- **No bot-side Hindi responses.** Brief says English only. (STT translates inbound; TTS replies in English; language-only customers are escalated.)
- **No live SIP transfer.** Escalation = pre-scripted close + structured outcome to CRM; human calls back. This is an architecture-doc decision, kept.
- **No PTP-capture-rate as a primary metric.** PRD anti-goal — easy to inflate under pressure. PTP specificity is the proxy.

## 10 · What I'd build with two more weeks

| Week 1 | Week 2 |
|---|---|
| Implement Call History Block input — per-customer state above the per-call FSM | Operator dashboards MVP (Ops + Manager) |
| Outcome `commitments[]` structured field — make the bot's promises machine-readable | Configuration UI for Admin (retry / dial windows / SLAs) |
| Attempt-aware FSM behaviour (opener variation, stricter routing after broken PTPs) | Audit log viewer for Compliance with `directives_fired` filters |
| ~10 multi-call eval scenarios (broken PTP on Call 2, no-history on Call 3, hardship-after-second-attempt) | Wire CRM webhook to accept new schema; Salesforce / Leadsquared adapter |

The bot is solved. The product is the operating system around it.

---

## Appendix · Repo navigation

```
collections-voicebot-v2/
  app/                    — bot internals
    pre_filter.py           pre-call segment filter + block rules
    intent_classifier.py    30-intent rule-based classifier (fast + slow path)
    fsm.py                  13-state FSM + move ladder + segment-policy strike thresholds
    policy.py               SegmentPolicy table — per-segment thresholds
    prompt_builder.py       4-part prompt composer (base + strategy + modifiers + state)
    validator.py            16-rule LLM-output validator (incl. commitment_overreach)
    conversation.py         the main loop
    outcome/                terminal outcome schema, extractor, webhook poster
    audio/                  Silero VAD + NLMS AEC + streaming mic IO
    static/index.html       single-page frontend
  prompts/                — every prompt the bot uses (base, strategies, modifiers, FSM states, closes)
  eval/                   — runner, judges, scenarios.yaml, personas.csv, results_v2.csv
  docs/                   — OPERATING_MODEL, MULTI_CALL_DESIGN, DATA_SCHEMA, PRD_v2_DELTAS, DEMO_SHOTLIST
```

Reference docs in the root (`AI PM Assignment.pdf`, `AI PM PRD.docx`, `Voicebot_Orchestration_Architecture.docx`) are the brief + product context that anchored the whole submission.
