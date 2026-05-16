# Multi-Call Lifecycle Design

A single bot call resolves a moment in time. A real collections cycle is a sequence of contacts — typically 3 bot attempts within the DPD 1–30 window, then human takeover if the customer hasn't cured. This document defines how that sequence is orchestrated, what the bot needs to know about prior calls, and when human takeover is mandatory.

## Why a per-customer state machine sits ABOVE the per-call FSM

The per-call FSM (in `app/fsm.py`) is **scoped to one call** — INTRO → COLLECTING → PTP_PROBE → TERMINAL. It has no memory of prior contacts.

Real collections needs a **per-customer cycle state machine** that lives in the bank's CRM / scheduler — not in the bot. The bot is stateless across calls; the orchestrator gives it the history each time.

```
                    ┌─ NEW_DELINQUENCY (DPD 4–10) ─┐
                    │                              │
                    ▼                              ▼
            ┌─────────────────┐         (each Call below is
            │  Call 1 (bot)   │          a complete bot session
            └────────┬────────┘          with its own per-call FSM)
                     │
       ┌─────────────┼─────────────────────────────┐
       │             │                             │
  promise_to_pay  already_paid  callback / hardship / refused / no_answer / wrong / DND
       │             │             │
       ▼             ▼             ▼
  PTP_MONITORING  RECON_QUEUE   per-outcome routing rules
  (wait for PTP    (verify in   (next section)
   date + 3-day    bank ledger
   grace window)   within 2 wd)
       │
   no payment received
       │
       ▼
  ┌─────────────────┐
  │  Call 2 (bot)   │ — opener references the broken commitment respectfully
  └────────┬────────┘
       … same routing …
       │
       ▼
  ┌─────────────────┐
  │  Call 3 (bot)   │ — final bot attempt; if no PTP captured, force human takeover
  └────────┬────────┘
       │
       ▼
  HUMAN_TAKEOVER — case routed to agent queue with full call history + transcripts
```

## Outcome → next-action mapping

The orchestrator drives next-action decisions; the bot only emits the outcome. Default rules below; admin can tune per bank policy.

> **NEW (Layer 2 + 3):** Refusal is now two distinct outcomes, and segment policy can upgrade a `refused` into a `human_callback_required` automatically. The orchestrator should read `outcome_detail.handoff` and `outcome_detail.callback_sla_hours` directly — they're populated by `app/policy.py`. Don't re-derive routing from the outcome string alone, or you'll misroute high-risk segments.

| Outcome (+ reason) | Next action | Cooldown before next bot call | Max bot attempts |
|---|---|---|---|
| `promise_to_pay` | Wait until PTP date + 3-day grace | Until grace expires | (Counts as committed; if broken → Call 2 / Call 3 same rules) |
| `already_paid` | Verify in bank ledger within 2 working days | Don't call again unless reconciliation fails | n/a |
| `callback_request` | Schedule call at customer's preferred time | Honour the preferred time | n/a (this IS the call schedule) |
| `human_callback_required` (waiver) | Route to waivers team; bot suspended for this case | 2 working days | Bot does not call again for this issue |
| `human_callback_required` (dispute) | Route to disputes team; bot suspended | 2 working days | Same |
| `human_callback_required` (medical_emergency / job_loss / business_failure / mental_distress / natural_disaster) | Route to hardship desk; bot suspended for 30 days; case marked do_not_pressure | 30 days minimum | None until hardship team clears |
| `human_callback_required` (deceased) | Route to bereavement team; bot permanently suspended for account | Forever | None |
| `human_callback_required` (abuse) | Route to senior agent; bot suspended pending review | 48h hold | Decision case-by-case |
| `human_callback_required` (language_callback) | Route to language-specific queue | 1 working day | Bot resumes if requested language unavailable and customer reverts |
| `human_callback_required` (refused_current_call_high_risk) **[NEW — Layer 2]** | Refusal in a frequent-late or sub-prime-frequent segment. Policy `human_takeover_on_refuse=True` upgraded `refused` to this. Route to senior agent. | Per `outcome_detail.callback_sla_hours` (typically 24h) | Bot does not call again this cycle for this issue |
| `refused` (reason=`refused_current_call`) **[NEW — Layer 3]** | In-the-moment refusal of THIS call. **Not** TRAI DND. Cooldown then Call 2 with a different opener. | Per `outcome_detail.callback_sla_hours` (24–72h based on segment) | Per max-attempts rule |
| `refused` (reason=`dnd`) | TRAI DND asserted. Stop MARKETING-eligible outreach; case to manual review. Legitimate collections contact may continue per RBI Fair Practices Code (DND suppresses marketing, not dues recovery). | Permanent for marketing; collections routed to human only | None for bot |
| `wrong_number` | Suppress number for this customer; route to data correction | Permanent (for that number) | None until number updated |
| `no_answer` | Retry per dialler policy (2h, 6h, next day) | Per policy, max 3 retries/day, 5/cycle | n/a (counted differently) |

### Why split `refused` two ways?

Before Layer 3, every refusal — "I don't want to talk right now" or "register me on DND" — collapsed to `refused/dnd`, then the customer was excluded from all future bot contact. That's wrong on two counts:

1. **The bot has no authority to promise no future contact.** Per RBI Fair Practices Code, legitimate dues collection is not suspended by TRAI DND. A human collector still calls.
2. **In-the-moment refusal isn't a permanent suppression signal.** A customer frustrated by a 3-minute call has not registered for DND. They've said "not now." The orchestrator should retry per cooling-off policy.

The validator (`app/validator.py::COMMITMENT_OVERREACH`) blocks the bot from ever phrasing a refusal close as "we won't call you again" — even in DND state — because the bot does not control the collections queue.

## When human takeover is mandatory (not optional)

Hard conditions — admin cannot override these without senior approval:

| Condition | Action |
|---|---|
| DPD reaches 25 with no PTP captured | Next interaction is human, not bot |
| 3 completed bot calls in current cycle with no PTP captured | Human takeover |
| 2 broken PTPs in current cycle | Human takeover, mark for senior agent |
| Any fast-path escalation (medical, job_loss, business, mental, disaster, abuse, deceased) | Immediate human, suspend bot for this customer per cooldown table |
| Customer asserts DND | Stop all bot calls; log per TRAI |
| Pre-filter rejects (Apex + sub-prime) | Human-only from cycle start |
| Customer files a complaint about a prior bot call | Human-only for at least 90 days; case to Compliance review |

## What the bot needs to know about prior calls

The orchestrator gives the bot a **Call History Block** at the start of each call. This becomes Part 5 of the assembled system prompt.

### Block format

```
PRIOR CALL HISTORY (last 30 days, most recent first)

Attempt 3 of 3 — today
Attempt 2  —  2026-05-15  outcome: no_answer  (3 retries, no pickup)
Attempt 1  —  2026-05-12  outcome: promise_to_pay  ₹18,500 by 2026-05-15 via UPI  →  not received (broken PTP)

GUIDANCE FOR REPEAT CALLS
- This customer broke a prior PTP. Acknowledge respectfully — do not lecture.
- Phrasing: "Last time you mentioned paying by the 15th — did something come up that made it difficult?"
- This is the final bot attempt this cycle. If no clean resolution today, the call will route to a human colleague.
- If the customer indicates real hardship, escalate via standard HARDSHIP_PROBE.
```

### Why guidance is provided, not just data

Without explicit guidance, the LLM might lecture ("you said you would pay") or under-react (treat the broken PTP as new). The orchestrator (bank-side) constructs this guidance block based on call history.

The bot does NOT need to know:
- The customer's complete CRM history beyond the active 30-day window
- The customer's behaviour on OTHER products
- Internal bank notes that aren't directly relevant to this call

This is intentional — minimum-necessary information principle (DPDP-aligned).

## Per-attempt opener variation

The bot's INTRO state already varies by `call_strategy` (apex_concierge / A_reminder / B_problem_solving). Add an attempt-number layer on top:

| Attempt | Opener variation |
|---|---|
| 1 | Standard segment opener (current behaviour) |
| 2 | "Hi, [name] — calling back about the card payment. Just wanted to check in on what we discussed." |
| 3 (final bot attempt) | "Hi, [name] — calling about your card. We've spoken a couple of times this cycle; wanted to give it one more try to find a solution that works." |

For human takeover after Call 3, the human agent gets a one-paragraph case brief generated by the outcome extractor:

```
Case brief — Rohan Mehta (P01, Apex, DPD 24)
- 3 bot calls this cycle. Call 1: PTP for ₹18,500 via UPI by 15th — broken. Calls 2 + 3: no answer.
- No hardship signals raised.
- Customer history: 5 years on the card, first missed payment ever, prime bureau (792).
- Suggested approach: warm follow-up — likely a temporary issue rather than intent to default.
```

## Orchestrator-side data the bot doesn't see

The bot is given the minimum it needs. The orchestrator (CRM-side) holds the rest:

- Lifetime customer payment history
- Cross-product relationships (savings account, FD, other cards)
- Internal credit risk score
- Marketing flags (do-not-cross-sell, premium customer)
- Litigation status, dispute history beyond current cycle

This separation matters: if the bot's training data or model changes, it cannot accidentally reason about customer information it shouldn't.

## What changes in the bot's code to support this

Additive only, no destructive changes.

**Done already (commits 92a1daa … ac917d8):**

- ✅ `app/policy.py` — SegmentPolicy resolved per call; drives `human_takeover_on_refuse`, `max_ptp_days`, `abuse_strikes_allowed`, `callback_sla_hours`.
- ✅ `app/outcome/schema.py` — `handoff`, `callback_sla_hours`, `policy_rationale` fields added to `OutcomeDetail`.
- ✅ `app/conversation.py::_apply_policy_to_outcome` — stamps policy-driven routing onto every outcome before posting.
- ✅ `app/fsm.py` — new `REFUSAL_CLOSE` state + two-strike `refuse_current_call` intent; abuse strikes read from policy.
- ✅ `app/web.py` + frontend — bot internals panel shows `move_played`, `directives_fired`, `scenario_inferred` per turn; outcome panel surfaces `handoff` and `policy_rationale`.

**Still TODO for full multi-call support:**

1. `app/conversation.py` — accept a `call_history_block` parameter, inject it as a 5th prompt part.
2. `app/prompt_builder.py` — add `assemble_with_history` method (or append history block to existing assembly).
3. `app/main.py` — CLI flag `--prior-calls path/to/history.json` for demoing repeat-call behaviour.
4. `app/outcome/extractor.py` — produce `agent_brief` field (1–2 sentence summary for the human picking up).
5. Orchestrator (CRM side, not in this repo) — read `outcome.outcome_detail.handoff` directly; don't re-derive from `outcome` string alone.

None of these touch the FSM, validator, or compliance rules. The bot's behaviour stays exactly the same per-call; orchestration adds context.

## What this looks like in production

A single customer's lifecycle, illustrative:

```
Day 4   (DPD 4)   — Bot Call 1.  PTP captured for day 7 via UPI.  ₹18,500.
Day 7              — No payment received. Grace window starts.
Day 10             — Still no payment. Grace expires. Schedule Call 2.
Day 12  (DPD 12)  — Bot Call 2. Customer says cash flow tight, asks for 7 days.
                    PTP captured for day 19. Outcome: promise_to_pay (specific).
Day 19             — No payment. Grace expires.
Day 23  (DPD 23)  — Bot Call 3 (final). Customer doesn't answer.
Day 24             — Automatic human takeover. Case brief generated.
                    Routed to senior agent queue with full call history.
```

The bot did three calls. The orchestrator made the routing decisions. Together they cover what one human agent would have done in roughly 3× the time and 5× the cost.
