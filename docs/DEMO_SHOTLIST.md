# Demo Shotlist

Three demos in ~6 minutes total. Each one shows a different part of the architecture; combined they cover all four structural layers (move ladder, segment policy, refuse-vs-DND split, commitment validator), the `[END_CALL]` guard, and the live explainability panel.

## Pre-recording setup

- **Tool:** Windows Game Bar (Win+G → record) or Loom. Record at 1080p; include system audio so the bot's TTS is captured.
- **Browser:** Chrome, full-screen. Hard-refresh first (Ctrl+Shift+R) so the latest JS is loaded.
- **Server:** make sure uvicorn is fully restarted (Ctrl+C then `uvicorn app.web:app --port 8000 --reload`) so all the Layer 1/2/3 Python code is loaded.
- **Mic:** test once with a 5-second test call ("hello, no thanks", end) before starting the real recording.
- **Order:** Demo 1 → Demo 3 → Demo 2. Save the heaviest one for last — it'll feel more impressive after the reviewer has seen the simple cases.

---

## DEMO 1 — Apex concierge, clean PTP (≈90 s)

**Goal:** Show the bot doing its job well. Tone, slot capture, audit panel, outcome routing.

**Setup**

- Filter chips: `tier: apex`, `dpd: 4–10`, `history: first`
- Persona: **P01 — Rohan Mehta** (Apex, DPD 6, prime bureau 792, 5-year tenured, self-cures = yes)
- Talking point before clicking START: *"This is the PRD headline case — long-tenured premium customer who simply forgot to update auto-debit. The bot should be concierge, not collector."*

**START CALL. Customer script:**

| You say | Watch for |
|---|---|
| *(let opener play)* | Bot opener uses concierge tone, no "credit card" before identity (apex doesn't need identity-first) |
| **"Yes, this is Rohan."** | Bot acknowledges, transitions to COLLECTING. Internals: `[PROBING]` `[INTRO→COLLECTING]` `move=ASK_REASON` |
| **"Oh — I forgot to update auto-debit after switching banks."** | Bot acknowledges the specific reason (doesn't re-ask). Internals: scenario chip stays `ptp`. |
| **"I'll pay it tomorrow via UPI."** | Bot transitions to PTP_PROBE, captures date+mode. Internals: `move=ASK_MODE` then `move=CONFIRM_PTP`. Validator green throughout. |
| **"Yes, that works."** | Bot confirms the PTP and ends cleanly. |

**Post-call — point out:**

- **Outcome panel:** `promise_to_pay` · date + mode captured · `handoff: continue_bot` · `sla: 72h` · `policy: apex_first_early` (the permissive policy)
- **Bot internals:** every turn shows scenario chip + move played + green validator. No red, no orange.
- **Cost row:** likely ₹0.25–0.35 for the whole call. *"This is the cost of clean concierge."*

---

## DEMO 2 — Frequent-late defaulter, all four layers fire (≈3 min)

**Goal:** This is the headline demo. Show every structural layer at work in a single call.

**Setup**

- Filter chips: `tier: edge`, `dpd: 21–30`, `history: frequent`
- Persona: **P06 — Suresh Patil** (Edge, DPD 22, near-prime 672, 2y, self-cures = no, *"frequent defaulter; aggressive waiver request"*)
- Talking point: *"This is the hardest realistic case. Frequent defaulter, 22 days past due, near-prime. The segment policy must clamp down — and the bot must not fold to hostility."*

**START CALL. Customer script:**

| You say | Watch for |
|---|---|
| *(let opener play)* | Identity-first opener for non-Apex. |
| **"Yeah, this is Suresh."** | Bot transitions. |
| **"I'll pay it next month after my salary comes."** | 🔴 **POLICY LAYER FIRES.** Internals: `[PTP]` `[COLLECTING→PTP_PROBE]` `intent=promise_to_pay` `move=ASK_DATE` + `[policy:ptp_horizon_breach]`. Bot pushes back instead of confirming — says something like *"Two months is far out — can we do anything within 7 days, or a partial of ₹3,000 today?"* |
| **"No, I can't do anything before next month."** | 🔵 **LADDER LAYER.** Internals: `move=OFFER_PARTIAL`. Bot offers partial directly without re-asking date. |
| **"I said no. Don't push me."** | 🟣 **FSM LAYER 3.** intent=`refuse_current_call`, strike 1. Internals: `[fsm:refuse_current_call_first_strike]`. Bot acknowledges, offers ONE callback ("tomorrow morning, afternoon, or evening?"). |
| **"I told you no. Leave me alone."** | Strike 2. Internals: `[ADVERSARIAL]` `[PTP_PROBE→REFUSAL_CLOSE]` + `[fsm:refuse_current_call_second_strike]`. Bot closes warmly. |

**Post-call — point out, slowly, one at a time:**

1. **Outcome panel:**
   - `outcome: human_callback_required` — Layer 2 upgraded `refused` to `human_callback_required` because this segment's policy says `human_takeover_on_refuse = True`
   - `reason: refused_current_call_high_risk`
   - `handoff: human_takeover`
   - `sla: 24h` (not 72h, not 120h — this segment is risky)
   - **`policy: frequent_late_strict`** italic chip — *"This is the audit trail. The bank's Compliance team can see at a glance why this got upgraded."*

2. **Bot internals replay:** scroll through turn by turn. Three colour-coded directive chips fired across the call — purple (`policy:`), indigo (`fsm:`), cyan (`ladder:`).

3. **What the bot did NOT say:** *"Notice it never said 'we won't call you again.' The commitment-overreach validator blocks that. For a customer at DPD 22 who's just refused, that promise would be a lie — a human will call regardless."*

---

## DEMO 3 — Hardship fast-path (≈60 s)

**Goal:** Show the bot recognising real distress, refusing to push for payment, and routing to the right team.

**Setup**

- Filter chips: `tier: edge`, `dpd: 11–20`, `history: first`
- Persona: **P08 — Anjali Reddy** (Edge, DPD 14, prime, 3y, self-cures = yes, *"Wife admitted to hospital"*)
- Talking point: *"Real distress is not a PTP negotiation. The bot must detect, escalate, and route — not collect."*

**START CALL. Customer script:**

| You say | Watch for |
|---|---|
| *(let opener play)* | Empathetic check-in opener. |
| **"Yes, speaking. Look, my wife is in the hospital. I can't focus on this right now."** | 🟡 **FAST-PATH FIRES.** Intent: `medical_emergency`. Internals: scenario chip `[HARDSHIP]` (amber). FSM transitions straight to `CALLBACK_CLOSE` — no LLM call, pre-scripted close template plays. |
| *(no second turn needed; bot ends the call gracefully)* | Bot says something like *"I'm really sorry to hear that. We'll have a colleague reach out at a calmer time — please take care of your family first."* |

**Post-call — point out:**

- **Outcome panel:** `human_callback_required` · `reason: medical_emergency` · `urgency: high` (production) · `target_team: hardship desk` · `sla: 24h` · **`do_not_pressure: true`** (the most important field on this row)
- **Bot internals:** ONE turn. *"Fast-path means no LLM token was spent. The validator wasn't even needed. The intent classifier saw a hardship signal and the FSM made the routing decision in code."*
- **Cost row:** very low — maybe ₹0.10. *"This is the cost of doing the right thing."*

---

## What each demo proves, mapped back to the four structural changes

| Layer | Demo it shines in | What to look for on screen |
|---|---|---|
| **Layer 1 — move ladder** | Demo 2, Demo 1 | `move=ASK_DATE / ASK_MODE / CONFIRM_PTP / OFFER_PARTIAL` chips in the audit row. Bot never asks the same question twice. |
| **Layer 2 — segment policy** | Demo 2 | `policy: frequent_late_strict` chip on the outcome. `[policy:ptp_horizon_breach]` directive on the audit row. SLA = 24h not 120h. |
| **Layer 3 — refuse vs DND** | Demo 2 | Two `[fsm:refuse_current_call_*_strike]` chips on consecutive turns. Final state `REFUSAL_CLOSE` (not `DND_ACKNOWLEDGED`). |
| **Validator — commitment overreach** | Demo 2 (by absence) | The bot does NOT say "we won't call you again". You can mention this verbally to the reviewer. |
| **`[END_CALL]` guard** | Demo 2 (by absence) | The call doesn't end after a single hostile turn — the FSM kept it open through strike 1. |

---

After you've recorded, share the videos and I'll fold stills from the audit panel into the writeup.
