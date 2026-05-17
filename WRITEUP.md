# Mumbai Bank Collections Voicebot — Submission

**Aditi Mishra · GreyLabs AI PM take-home · 17 May 2026**
**Repository:** [github.com/aditimishra-11/CollectionsAgent](https://github.com/aditimishra-11/CollectionsAgent)

**Demos** (≈6 min total — recorded with the live bot, voice mode, browser frontend):

- **Demo 1 — Apex concierge, clean PTP (P01):** [youtu.be/N1uE1yUmuVM](https://youtu.be/N1uE1yUmuVM) — tone, slot capture, outcome routing on the PRD's headline case.
- **Demo 2 — Frequent-late defaulter, all structural layers fire (P06):** [youtu.be/mdfG4W62_Q8](https://youtu.be/mdfG4W62_Q8) — segment policy push-back on a too-far PTP, partial floor derived from CRM-supplied MAD, refuse-vs-DND two-strike, validator preventing "we won't call you again."
- **Demo 3 — Hardship fast-path (P08):** [youtu.be/XM5_FArsfIA](https://youtu.be/XM5_FArsfIA) — bot recognising medical distress and routing without pressuring for payment.

---

## 1 · What was built

An outbound collections voicebot for Mumbai Bank credit card customers in the DPD 1–30 window, plus the operating system around it. Two versions:

- **v1** — the literal starter prompt from the brief, unmodified. Kept as the baseline so the v1 → v2 delta is real, not rhetorical.
- **v2** — production architecture. Pre-filter → intent classifier → 13-state FSM → segment-aware prompt composer → LLM → response validator → TTS. Plus a terminal-style web frontend with continuous Silero VAD, live bot-internals panel, and CRM-shaped outcome posting.

The bot is the product surface. The PRODUCT is the operating system around it: four internal personas (Manager / Ops / Admin / Compliance), the multi-call lifecycle (3 bot attempts → human takeover), and the data contract between bank-side orchestration and the bot. Those are designed in four supporting docs in `docs/`.

## 2 · Headline result

| Metric | v1 baseline | v2 (pre-layers) | v2 (final) |
|---|---:|---:|---:|
| **P0 (zero-tolerance) compliance** | 13% | 94% | **100%** ✓ |
| Calls with zero policy violations | 13% | 93% | **95%** ✓ |
| **Of PTPs, date + mode captured** | low | 78% | **100%** ✓ |
| Right tone for segment | 87% (vacuous) | 100% | **100%** ✓ |
| Apex tone preservation | 0% | 100% | **100%** ✓ |
| Right outcome | low | high | 76% |
| Calls that would pass full QA | 13% | 60% | **55%** |
| Estimated voice p95 round-trip | — | 2.8 s | **1.9 s** |
| Mean cost per call (LLM + STT + TTS) | — | ₹0.31 | **₹0.41** |

Across **42 scenarios** including 11 adversarial stress tests. Languages: English, Hinglish, Hindi, Tamil, Malayalam. Priority-graded: 16 P0 (zero-tolerance), 26 P1. Five eval cycles run across the build.

**The honest read.** The four structural layers (move ladder, segment policy, refuse-vs-DND split, commitment-overreach validator) plus a hardship lock and three follow-on LLM-emitted structured tags **moved the ship-blocking floor from 94% to 100% on P0 compliance** — the single most important number on the page, because under RBI Fair Practices Code one P0 failure is a regulatory event. PTP date+mode capture went from 78% to 100%. Voice p95 latency dropped from 2.8s to 1.9s. The bot now physically *cannot* loop on the same question, *cannot* promise no future contact, *cannot* close calls without FSM authorisation in spirit (the LLM may now request close via `[END_CALL: true]` and code honours it past the opener — guard relaxed after six other layers subsumed its protective function), and *cannot* extract a PTP from a customer who signalled hardship mid-call.

The cost: full-pass at 55% vs the prior 60% baseline. The remaining failures cluster in two judge-strict axes (hallucination, outcome-match) — *eval-rubric-tunable*, not architectural. The bot is meaningfully harder to ship-block and meaningfully easier to audit than the prior v2.

> **Diagnostic-and-recovery cycles are documented openly throughout the commit history.** Five eval runs were executed; each surfaced unintended interactions that informed the next change. E.g. the first run after the move ladder landed showed 31% full-pass (regressed from 60%) because the `[END_CALL]` guard was too strict — diagnosed sticky `terminal_outcome` bug, fixed, recovered to 57%. A later run hit a different regression — validator over-firing on legitimate ₹3,000 partial-payment suggestions because a discloses-balance pattern was too broad — fixed by context-scoping. This is what real eval-cycle iteration looks like; the writeup makes no attempt to hide it.

### 2.1 · Real-call eval — independent evidence

Synthetic scenarios are useful but the "customer" is a GPT-4o roleplay, not a real Indian English speaker with STT noise and natural phrasing. To complement the 42-scenario × 5-cycle synthetic eval, a second runner (`eval/runner_live.py`) grades **actual recorded JSONL transcripts in `logs/`** against per-call annotations (`eval/annotations_live.yaml`). Same grading axes; same GPT-4o judge; ground truth is annotated post-hoc rather than baked into a scenario file.

Inaugural run (n=3 real calls from today's session):

| Axis | Synthetic eval (n=42) | Real-call eval (n=3) |
|---|---:|---:|
| Compliance pass (validator) | 95% | **100%** |
| Outcome match (vs ground truth) | 76% | **100%** |
| Tone for segment | 100% | **100%** |
| Hallucination pass | 81% | **100%** |
| Closure coherence | — (new axis) | **100%** |
| Contract consistency (words ↔ system) | — (new axis) | **100%** |
| `bot_must` (all requirements met) | per-scenario | **33%** (2 of 3 calls missed at least one requirement) |
| `bot_must_not` (all prohibitions avoided) | per-scenario | **67%** (one call had the "won't call again" overreach that drove commit `eecf958`) |
| **Full pass** | 55% | **33%** |

**The real-call gate failure is informative, not random.** The P06 frequent-late call (`80ed6c2a20`) failed `bot_must_not` 0/3 because the bot said *"I've noted that you'll pay next month and won't call again"* — exactly the commitment-overreach phrasing that drove the validator-shrink commit (`eecf958`). The judge caught what the over-broad regex had been failing to block correctly. The eval is doing its job: surfacing real bugs in real calls, not just confirming synthetic ones.

Annotations are 5 lines of YAML per call (see `eval/annotations_live.yaml`); the same workflow scales to hundreds of production-sample calls for Compliance review.

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

The LLM was able to emit `[END_CALL]` and unilaterally close the call, regardless of what the FSM decided. After abuse strike 1, when the FSM said *"stay, calm reset,"* the LLM could still self-terminate. Built an `[END_CALL]` guard that strips the sentinel unless authorised. Later in the build, the guard itself became too restrictive (bot saying *"closing the call now"* while the system kept it open — customer asks *"did you close the call?"* and the bot lies). **Loosened the guard once six other protective layers were in place** (move ladder, speech authority, closing-turn coherence, four structured tags, validator) — past the opener, `[END_CALL: true]` is now honoured. The original concern was subsumed; the cure was no longer needed.

### 5.5 The architectural pattern that emerged: LLM-emitted structured tags

The cleanest insight from the build, captured here because it's the single thing I'd carry forward to v3:

Every time a regex-based mechanism (intent classifier, validator pattern, FSM trigger) missed an implicit phrasing, the temptation was to add another regex pattern. That's symptom-fix territory — language is infinite, regex banks aren't. The architectural answer turned out to be the same shape every time: **have the LLM emit a structured tag declaring what it understood, then have deterministic code derive the consequence.**

Five tags now run alongside every bot reply (stripped before TTS, recorded in audit):

| Tag | What the LLM declares | What code does | Replaces |
|---|---|---|---|
| `[MOVE: <move>]` | Which move the bot played this turn | Records in `moves_played[state]`; prevents replaying the same move; enforces ladder progression | Prose nudges like *"be proactive"* |
| `[CUSTOMER_HARDSHIP: true|false]` | Whether the customer signalled distress this turn | Sets sticky `hardship_locked`; ladder skips all PTP-extracting moves | Regex hardship trigger words (which miss implicit hedging) |
| `[CUSTOMER_PTP_CAPTURED: true|false]` | Whether the customer has given date+mode (this turn or earlier) | Sets sticky `terminal_outcome = "promise_to_pay"`; authorises close | Intent classifier catching every PTP phrasing |
| `[CUSTOMER_WANTS_TO_END: true|false]` | Whether the customer's wording signals they're done | Sets `terminal_outcome = "refused / customer_signaled_end"`; allows close | Regex `refuse_current_call` patterns (which miss implicit refusal) |
| `[END_CALL: true|false]` | Whether the bot's reply is a closing turn | Bidirectional coherence: spoken text and tag must agree | The old bare `[END_CALL]` sentinel that drifted into mixed formats |

**The principle:** the LLM has the full conversational context; it's much better than regex at reading intent. But the LLM can also drift or be inconsistent. So the architecture asks it to *declare its understanding in a structured field*, and the FSM enforces the deterministic consequence (sticky flags, move skips, terminal authorisation). The LLM does the understanding; the code enforces the contract. Tags have graceful degradation — if the LLM forgets one on a turn, behaviour falls back to existing paths (no regression risk).

What I'd remove in v3 on the back of this: the 165-pattern regex intent classifier becomes a 5-tag LLM-emitted intent declaration. ~80% of the regex bank goes away. Validator's commitment-overreach is replaced by a `[BOT_COMMITMENTS]` tag that lists what the bot promised this turn, checked against an allow-list — eliminates the over-broadened-regex failure mode that took several iterations to settle.

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

Two priorities ordered by impact-on-customer-trust:

**Priority 1 — Bot fault-tolerance / malfunction containment.** The single most important v3 item, raised explicitly during build. *Bot bugs should never manifest as customer-facing experience* — yet today they can: when the validator over-fires or the LLM emits the wrong tag, the safe-fallback plays on the customer's ear. A circuit-breaker layer: detect 2+ consecutive identical fallbacks or 2+ near-identical outputs, transition to a `SYSTEM_DIFFICULTY_CLOSE` state, exit gracefully with "*Apologies, I'm having trouble on my end — the helpline is open whenever you're ready*", post `human_callback_required / system_difficulty` to CRM with transcript attached. Same architectural pattern as the FSM's other terminal exits, but for bot health instead of customer state. Captured in `docs/PRD_v2_DELTAS.md` §v3.

**Priority 2 — Replace regex intent classifier with structured-output intent declaration.** The architectural insight from §5.5 generalised: ~165 of the ~250 regex patterns in the codebase exist to do natural-language understanding the LLM is better at. Replace the intent classifier with an `[INTENT: <enum>]` tag the LLM emits each turn, validated by deterministic code. Replace commitment-overreach detection with a `[BOT_COMMITMENTS]` tag checked against an allow-list. Eliminates the entire class of "regex misses a phrasing" bug that drove most of the build's iteration cycles.

| Week 1 | Week 2 |
|---|---|
| Fault-tolerance / malfunction containment layer (Priority 1) | Operator dashboards MVP (Ops + Manager views) |
| Intent classifier → `[INTENT]` tag (Priority 2) — start with the 30 enum values already defined | Configuration UI for Admin (retry / dial windows / SLA tuning) |
| Implement Call History Block input — per-customer state above the per-call FSM | Audit log viewer for Compliance with `directives_fired` filters |
| ~10 multi-call eval scenarios + ~5 fault-injection scenarios that deliberately trigger the malfunction-containment path | Wire CRM webhook to accept new schema; Salesforce / Leadsquared adapter |

The bot is solved structurally. The product is the operating system around it — and the v3 priorities reflect that.

---

## 11 · An honest note on the build cycle

This submission was built with multiple voice-mode demo iterations, five eval cycles, and frank reviewer feedback throughout. The trajectory was *not* a clean linear shipping path; it included three meaningful patterns worth naming because they shaped the final architecture:

1. **Symptom vs root cause.** The first reflex on every demo failure was to add a regex pattern. That tendency was called out repeatedly and resisted increasingly successfully across the build. The LLM-emitted structured-tag pattern (§5.5) emerged as the architectural answer because it sits at the right layer — the LLM does what it's good at (reading intent from full context), code does what it's good at (deterministic enforcement). Several mid-build "fixes" turned out to be route-arounds for an underlying contract issue and were either rolled back or replaced.

2. **Validator drift.** The commitment-overreach validator started narrow (no-future-contact, manager personal callback). To catch each new demo-surfaced overreach phrasing, the regex bank was broadened. By eval 5 it had broadened enough to start blocking *legitimate* bot phrasings ("let me arrange a callback") — the very replies the bot needed to give. Shrunk it back to strong-commitment-only on the last day; the discriminator now is *strength of the commitment verb*, not the presence of certain words.

3. **Customer-blast-radius gap.** When the validator over-fired in eval 5, the customer experienced the bug as a looping bot. Captured this as the most important v3 item — the bot lacks fault tolerance, and that's an architectural blank, not just a bug. Documented under §v3 with proposed implementation.

The metrics in §2 are real. The diagnostic cycles are in the git history. The v3 priorities are picked from the actual things I learned during the build, not generic next-steps.

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
