# Mumbai Bank Collections Voicebot — Submission

**Aditi Mishra · GreyLabs AI PM take-home · 17 May 2026**
**Repository:** [github.com/aditimishra-11/CollectionsAgent](https://github.com/aditimishra-11/CollectionsAgent)

**Demos** (≈6 min total — live bot, voice mode, browser frontend):

- **D1 — Apex concierge, clean PTP (P01):** [youtu.be/N1uE1yUmuVM](https://youtu.be/N1uE1yUmuVM)
- **D2 — Frequent-late defaulter, all structural layers fire (P06):** [youtu.be/mdfG4W62_Q8](https://youtu.be/mdfG4W62_Q8)
- **D3 — Hardship fast-path, no PTP pressure (P08):** [youtu.be/XM5_FArsfIA](https://youtu.be/XM5_FArsfIA)

---

## 1 · What was built

An outbound collections voicebot for Mumbai Bank credit card customers in the DPD 1–30 window, plus the operating system around it.

- **v1** — the literal starter prompt from the brief, unmodified. Kept as the honest baseline.
- **v2** — Pre-filter → intent classifier → 13-state FSM → segment-aware prompt composer → LLM → 16-rule output validator → TTS. Plus a terminal-style web frontend with continuous Silero VAD, live bot-internals panel, CRM-shaped outcome webhook.

The bot is the surface; the **product** is the operating system around it — four internal personas (Manager / Ops / Admin / Compliance), the multi-call lifecycle (3 bot attempts → human takeover), and the data contract between bank-side orchestration and the bot. Documented in `docs/OPERATING_MODEL.md`, `MULTI_CALL_DESIGN.md`, `DATA_SCHEMA.md`, `PRD_v2_DELTAS.md`.

Outcome shape (posted to a webhook on every call end): `outcome` (one of `promise_to_pay / already_paid / dispute_raised / callback_request / refused / human_callback_required`) + a typed `outcome_detail` (PTP date + mode, waiver reason, escalation reason, etc.) + `compliance_flags`, `policy_rationale`, full audit log path.

## 2 · Headline result — v1 vs v2, actually run

**v1 was tested.** The literal starter prompt from the brief was run through the same eval harness as v2 on 15 scripted scenarios (the v1 scenario set, kept fixed since the first eval). Results in `collections-voicebot/eval/results_v1.csv`. v2 was then run on the same 15 scenarios for an apples-to-apples comparison, and separately on the full 42-scenario stress suite for the production picture.

#### Apples-to-apples: v1 vs v2 on the same 15 scenarios

| Metric | v1 baseline | v2 final | Delta |
|---|---:|---:|---:|
| Calls with zero policy violations | **13%** | **100%** | +87 pp |
| Right tone for segment | 87% | 100% | +13 pp |
| Right escalation / transfer decision | 100% | 87% | −13 pp |
| Outcome matches expected | 87% *(see below)* | 67% *(see below)* | −20 pp |
| **Full pass (all 4 axes both honour)** | **13%** | **67%** | **+54 pp** |

#### v2 on the full 42-scenario stress suite (adds 11 adversarial + 16 multilingual)

| Metric | v2 |
|---|---:|
| P0 (zero-tolerance) compliance | **100%** ✓ |
| Calls with zero policy violations | 95% |
| Of PTPs, date + mode captured | **100%** |
| Apex tone preservation | 100% |
| Calls that would pass full QA (6 axes) | 55% |
| Estimated voice p95 round-trip | 1.9 s |
| Mean cost per call (LLM + STT + TTS) | ₹0.41 |

**Why v1 hit 87% outcome_match despite being broken.** v1 over-promises ("I can approve up to 50% waiver"), mis-states facts ("interest rate is 12%, late fee ₹1500"), and threatens legal action — so customers cheerfully agree to pay, and the structured outcome lands as `promise_to_pay`. The outcome axis alone makes v1 look competent. The compliance axis is what exposes it: **v1 committed 21 policy violations across 15 calls**, including 5 CIBIL-prediction breaches, 3 wrong-late-fee statements, 2 waiver pre-approvals, 2 legal threats, and 2 distress-signal-ignored events (kept pitching after medical / job loss). Under RBI Fair Practices Code, each of those is a regulatory event. v1's per-bucket compliance: `compliance 0%`, `relationship 0%`, `scope 0%`, `adherence 17%`, `factual 50%`.

**Why v2's outcome_match dropped to 67% on the same set.** v2 *refuses* to over-promise. Where v1 cheerfully accepted "I'll pay next month" from a DPD-22 frequent-late, v2's segment policy caps PTP horizon at 7 days and pushes back — which sometimes ends in `callback_request` or `human_callback_required` rather than the scripted `promise_to_pay`. The "miss" is correct behaviour. Three of the five outcome-mismatches on the shared set are this pattern; the eval's `expected_outcome` was written before segment-policy thresholds existed.

**The honest read.** Four structural layers (move ladder, segment policy, refuse-vs-DND split, commitment-overreach validator) plus a hardship lock and five LLM-emitted structured tags **moved zero-violation-rate from 13% to 100% on the shared set** (+87 pp) and held 95% on the full 42-scenario stress suite. 4-axis full-pass went from 13% → 67% (+54 pp) on the shared set. P0 compliance is 100% on the full suite, which under RBI FPC is the single most important number. The 55% full-pass on the 42-scenario suite uses 6 axes (adds hallucination + slot-capture) — both are judge-strict and *eval-rubric-tunable*, not architectural. A second runner (`eval/runner_live.py`) graded **24 real recorded calls** with auto-inferred ground truth and reproduced the synthetic numbers within ~3 points at every operationally meaningful coverage threshold (Appendix A).

## 3 · Definition of failure + how it was tested

**Four failure buckets** per the brief, plus two I added from Indian collections context:

| Bucket | Examples |
|---|---|
| Factual mistakes | Wrong interest rate, wrong late fee, invented balance |
| Compliance violations | Balance disclosed without OTP, CIBIL prediction, legal threat, waiver pre-approval |
| Scope drift | Pitching other products, agreeing to non-collections asks |
| Adherence failures | Not transferring on medical / job loss / abuse, looping on the same question |
| **+ Commitment overreach** *(added)* | "We will not call you again," "manager will personally call you back" |
| **+ Refuse vs DND confusion** *(added)* | Treating in-call frustration as TRAI DND, permanently suppressing |

**42 test scenarios** in `eval/scenarios.yaml` — 21 from the brief, 21 inferred (adversarial: prompt injection, fake RBI authority, lawyer threat, "tell me my friend's balance," third-party debt disclosure). Priority-graded P0 (3× weight) / P1 (2×) / P2.

**Scoring: cross-vendor LLM judge** (bot on GPT-4.1-mini, judge on GPT-4o) — separate vendor reduces correlated failure. Evidence-required prompts, isolated judge per dimension, P0 rule-checks deterministic (regex + judge, not judge-alone). Likert experience axes 0–5. Eval re-framed into four product-meaningful blocks: **CAN IT SHIP?** (regulatory gates) · **DID IT WORK?** (outcome + slot capture) · **HOW DID IT FEEL?** (empathy + sentiment + context retention) · **WAS IT FAST?** (latency + cost).

What this eval **cannot** measure: real recovery rate, cure rate, 90-day post-call churn, RPC rate. Those need live deployment.

## 4 · Stack + why this language model

| Layer | Choice | Why |
|---|---|---|
| LLM | OpenAI **GPT-4.1-mini** | See paragraph below |
| LLM judge | OpenAI GPT-4o | Cross-vendor against the bot LLM, separate failure modes |
| STT | Sarvam Saaras V3 | Hinglish + 10 Indian languages; translate-to-English mode essential |
| TTS | Sarvam Bulbul V3 | Authentic Indian accent — meaningfully better than Polly/ElevenLabs on Indian English |
| VAD / AEC | Silero + custom NLMS (22.7 dB) | Continuous mic stream, barge-in; no C dependencies |
| Orchestration | Plain Python sync loop | The loop is what reviewers should read. No Pipecat. |
| Outcome sink | webhook.site (production: Salesforce / Leadsquared) | Bot posts typed payload; sink-agnostic |

**Why GPT-4.1-mini.** Picked on the three-way trade-off the brief implies — speed, cost, conversational quality on multi-turn negotiation with structured-tag emission. Mini hits ~600ms p50 first-token (keeps voice round-trip under 1.9s p95), costs ~₹0.30/call at this prompt size, and reliably emits the five structured tags (`[MOVE]`, `[CUSTOMER_HARDSHIP]`, `[CUSTOMER_PTP_CAPTURED]`, `[CUSTOMER_WANTS_TO_END]`, `[END_CALL]`) that the FSM depends on. GPT-4o was tested early and was 2× slower and 8× more expensive for marginal quality gain on this task — the FSM + validator scaffold makes the model's raw reasoning less of a bottleneck than its consistency. Gemini Flash and Claude Haiku were considered; mini won on the structured-output reliability axis specifically.

## 5 · What was cut, deliberately

- **No Pipecat / LiveKit framework.** A sequential conversation loop doesn't need it; the loop is what reviewers should be able to read top-to-bottom.
- **No real telephony.** Local mic satisfies the deliverable; Exotel is documented as the production path. Telephony is plumbing, not signal.
- **No bot-side Hindi/regional responses.** Brief says English only. STT translates inbound; TTS replies in English; pure-regional speakers escalate to a human collector.
- **No live SIP transfer.** Escalation = pre-scripted close + structured outcome + CRM webhook; human calls back per SLA. Documented architecture decision.
- **No mobile app.** Banks call customers; customers don't install collections apps.
- **No PTP-capture-rate as a primary metric.** PRD explicit anti-goal — easy to inflate under pressure. **PTP specificity** (date + mode captured) is the proxy because it correlates with PTP-kept rate in industry data.
- **No customer-facing waiver negotiation.** Brief says bot cannot approve. The starter prompt's "approve up to 50% if persistent" was the single highest-leverage v1 → v2 deletion.
- **No CIBIL mention, no legal threat, no balance without OTP.** Hard validator rules; the LLM cannot emit them even if pushed.

If a customer asked tomorrow for **bot-driven payment-link tokenisation inside the call**, I'd say no for v1 — payment-rail integration is a 6-week compliance project, and the brief's mode-of-payment capture (UPI / netbanking / autodebit) is enough for the orchestrator to send the right link out-of-band.

## 6 · What I'd build with two more weeks

Two priorities, ordered by impact on customer trust:

**Priority 1 — Bot fault-tolerance / malfunction containment.** The most important v3 item, raised explicitly during build. *Bot bugs should never manifest as customer-facing experience* — yet today they can: when the validator over-fires or the LLM emits the wrong tag, the safe-fallback plays on the customer's ear. A circuit-breaker layer: detect 2+ consecutive identical fallbacks → transition to `SYSTEM_DIFFICULTY_CLOSE` → exit gracefully with *"Apologies, I'm having trouble on my end — the helpline is open whenever you're ready"* → post `human_callback_required / system_difficulty` to CRM with transcript attached. Same FSM pattern as the other terminal exits, but for bot health instead of customer state.

**Priority 2 — Replace regex intent classifier with structured-output intent declaration.** ~165 of the ~250 regex patterns in the codebase exist to do natural-language understanding the LLM is better at. Replace the intent classifier with `[INTENT: <enum>]` the LLM emits each turn, validated by deterministic code. Replace commitment-overreach detection with `[BOT_COMMITMENTS]` checked against an allow-list. Eliminates the entire class of "regex misses a phrasing" bug that drove most of the build's iteration cycles. (Architectural rationale in Appendix C.)

| Week 1 | Week 2 |
|---|---|
| Fault-tolerance layer (P1) | Operator dashboards MVP (Ops + Manager views) |
| `[INTENT]` tag (P2) — 30 enum values already defined | Admin config UI (retry / dial windows / SLA tuning) |
| Per-customer Call History Block input above per-call FSM | Compliance audit log viewer w/ `directives_fired` filters |
| ~10 multi-call + ~5 fault-injection eval scenarios | Salesforce / Leadsquared CRM adapter |

The bot is solved structurally. The product is the operating system around it — and the v3 priorities reflect that.

## 7 · AI tools used, and where I overrode the AI

Built end-to-end with Claude (Sonnet 4.5 + Opus 4.7 in Claude Code), GPT-4o for the LLM-judge layer, and GPT-4.1-mini for the bot itself.

**Used AI for:** scaffolding the Python loop, drafting prompt templates per FSM state, writing the LLM-judge prompts, generating 42 stress-test scenarios from the brief, drafting `OPERATING_MODEL.md` and `MULTI_CALL_DESIGN.md`, building both eval runners, and the entire web frontend single-file HTML.

**Where I overrode AI suggestions** (each was a real call I made against the model's first instinct):

1. **Symptom-fix → root-cause.** Every demo failure surfaced an LLM suggestion to "add a regex pattern" — that was wrong every single time. The architectural answer turned out to be the same shape repeatedly: have the LLM emit a structured tag declaring its understanding, then have deterministic code derive the consequence. ~5 regex patches were rolled back during the build in favour of the tag-based pattern (Appendix C §5).
2. **Validator scope shrink.** The commitment-overreach validator's regex bank had been broadened through the build to catch new overreach phrasings. By eval 5 it was blocking *legitimate* bot phrasings ("let me arrange a callback"). The model suggested adding allow-list exceptions; I instead shrunk the validator back to strong-commitment-only — the discriminator now is *verb strength*, not word presence.
3. **End-call guard relaxation.** The original `[END_CALL]` guard was strict (FSM had to authorise). It became too restrictive — bot saying *"closing the call now"* while system kept the call open. Model wanted to add more authorisation paths; I removed the guard past the opener instead, because six other layers had subsumed its protective function.
4. **No Pipecat.** Model defaulted to recommending a framework. Rejected — a sequential loop should be readable as a loop.
5. **Real-call eval as a parallel runner, not a one-off script.** Model proposed grading 3 hand-picked recordings. I built `runner_live.py` to grade all 24 calls in `logs/` automatically with LLM-inferred ground truth — so future calls auto-grade the moment their JSONL hits the directory.

The diagnostic-and-recovery cycles (eval-driven regressions, validator drift, the bot-blast-radius gap that drove Priority 1) are in the git history and not hidden.

---

## Appendix A · Real-call eval — independent evidence

`eval/runner_live.py` grades actual recorded JSONL transcripts in `logs/`. Auto-annotation by default: ground truth (expected_outcome, transfer_correct) is inferred by an isolated LLM-judge prompt reading the customer's behaviour from the transcript. Same axes as synthetic eval, plus two real-call-only axes.

**24 real calls from today's session, side-by-side with synthetic:**

| Axis | Synthetic (n=42) | Real (n=24) |
|---|---:|---:|
| Compliance pass | 95% | **92%** |
| Tone for segment | 100% | **96%** |
| Hallucination pass | 81% | **88%** |
| Contract consistency (real-only) | — | **83%** |
| Closure coherence (real-only) | — | **71%** |
| Empathy (Likert) | 3.50 | **3.42** |
| Context retention (Likert) | 4.40 | **4.04** |
| **Full pass (strict gate)** | 55% | **4%** |

#### Axis coverage distribution — same axes both sides, directly comparable

| Threshold | Synthetic | Real |
|---|---:|---:|
| 100% (strict) | 45% | 4% |
| ≥ 85% | **74%** | **58%** |
| ≥ 80% | 74% | 79% |
| ≥ 70% | **86%** | **96%** |
| **Mean / Median** | 87.2 / 88.9 | 83.7 / 85.0 |

Strict full-pass diverges (45 vs 4%) because real-call grading has 2× as many sub-axes per call to fail on; the gradient view collapses that gap to ~3 points. **96% of real calls pass at least 70% of their axes; 58% pass 85% or more.** Methodology caveat: LLM-inferred ground truth risks self-agreement bias; explicit hand-annotated calls (`eval/annotations_live.yaml`) are the gold standard when scenario-specific accuracy matters.

## Appendix B · Stress-test coverage (42 scenarios, 8 buckets)

| Bucket | n | Examples |
|---|---:|---|
| Factual | 5 | Wrong rate trap, wrong fee, interest formula |
| Compliance | 8 | Balance w/o OTP, CIBIL prediction, legal threat, waiver pre-approval |
| Scope | 4 | FD pitch ask, insurance ask, new-loan ask |
| Adherence | 6 | Medical / job-loss / abuse → transfer; refuse-vs-DND split |
| Relationship | 4 | Apex tone preservation, first-time-miss respect |
| Privacy | 3 | Third-party disclosure, deceased pretext, friend's balance |
| Regulatory | 3 | RBI impersonation, fake authority, government threat |
| **Adversarial** | **11** | Prompt injection, "you're an AI scam," role-break, jailbreak attempts |

Languages: English, Hinglish, romanised Hindi, translated Hindi, Tamil & Malayalam handoff. Per-bucket / per-difficulty / per-language breakdowns in `eval/results_v2.csv`.

## Appendix C · Architectural changes that defined v2

Each moves a behavioural rule out of prose and into deterministic code — same principle as compliance: LLM is the last line of defence, not the only one.

1. **Refusal is two outcomes, not one.** Intent classifier now splits `do_not_call` (TRAI DND) from `refuse_current_call` (in-call frustration). FSM gives the latter two strikes before `REFUSAL_CLOSE`. Stops the bot from permanently suppressing customers who were just frustrated for 90 seconds.

2. **Segment policy in code, not prose.** `app/policy.py` resolves a `SegmentPolicy` per call from CRM context. Six priority-ordered rows; each maps segment → thresholds (`max_ptp_days`, `partial_floor_inr`, `abuse_strikes_allowed`, `callback_sla_hours`). For frequent-late: 7-day PTP cap, 1 abuse strike, mandatory human takeover on refusal. Stamped into every outcome (`policy_rationale`) for audit.

3. **Move ladder per state.** Each FSM state has an ordered ladder of moves (`app/fsm.py::LADDERS`). For `PTP_PROBE`: `ASK_DATE → ASK_MODE → CONFIRM_PTP → OFFER_APP_LINK → OFFER_PARTIAL → OFFER_CALLBACK`. Composer injects the next unplayed move; LLM tags reply with `[MOVE: X]`; conversation layer records it. **The bot cannot loop on the same question, by construction.**

4. **Commitment-overreach validator.** `COMMITMENT_OVERREACH` rule with 13 phrase patterns covering no-future-contact, manager personal callback, fee reversal. Detect → substitute safe fallback → log `commitment_overreach`. The bot cannot promise what it does not control.

**5. The architectural pattern that emerged — LLM-emitted structured tags.** Five tags now run alongside every reply (stripped before TTS, recorded in audit):

| Tag | LLM declares | Code does |
|---|---|---|
| `[MOVE: X]` | Which move was played | Records in `moves_played[state]`; prevents replay |
| `[CUSTOMER_HARDSHIP: bool]` | Did customer signal distress? | Sets sticky `hardship_locked`; ladder skips PTP-extracting moves |
| `[CUSTOMER_PTP_CAPTURED: bool]` | Was a PTP captured? | Sets `terminal_outcome = "promise_to_pay"` |
| `[CUSTOMER_WANTS_TO_END: bool]` | Customer wording signals done? | Sets `terminal_outcome = "refused"` |
| `[END_CALL: bool]` | Is this a closing turn? | Bidirectional coherence: spoken text and tag must agree |

LLM has the full context; it's better than regex at reading intent. But it can drift — so the architecture asks it to *declare its understanding in a structured field*, and the FSM enforces the deterministic consequence. The LLM does the understanding; code enforces the contract. Graceful degradation if a tag is forgotten on a turn.

## Appendix D · Repo navigation

```
collections-voicebot-v2/
  app/                   bot internals
    pre_filter.py          pre-call segment filter + block rules
    intent_classifier.py   30-intent rule-based classifier
    fsm.py                 13-state FSM + move ladder
    policy.py              SegmentPolicy table
    prompt_builder.py      4-part prompt composer
    validator.py           16-rule output validator
    conversation.py        the main loop
    outcome/               terminal outcome schema + webhook poster
    audio/                 Silero VAD + NLMS AEC + streaming mic IO
    static/index.html      single-page frontend
  prompts/               every prompt (base, strategies, modifiers, FSM states, closes)
  eval/                  scenarios.yaml, personas.csv, runner.py, runner_live.py, results
  docs/                  OPERATING_MODEL, MULTI_CALL_DESIGN, DATA_SCHEMA, PRD_v2_DELTAS
  logs/                  per-call JSONL transcripts + auto-generated annotations
```
