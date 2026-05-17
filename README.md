# Mumbai Bank Collections Voicebot

GreyLabs AI PM Assignment submission, Aditi Mishra · 17 May 2026. An outbound collections voicebot for credit card customers, DPD 1–30.

## Submission deliverables

| File | What it is |
|---|---|
| **`Aditi Mishra - Assignment Writeup.docx`** / **`.pdf`** | The submission writeup (main body + appendices) |
| **`Aditi Mishra - Eval Results.xlsx`** | All eval data: v1 baseline, v1 backfilled, v2 synthetic, v2 real calls, v1↔v2 side-by-side. Index tab explains every column |
| **`WRITEUP.md`** | The source markdown the docx is rendered from |
| **`AI PM Assignment.pdf`** | Original brief (reference) |
| **`AI PM PRD.docx`** | PRD I wrote against (reference) |
| **`Voicebot_Orchestration_Architecture.docx`** | Architecture context doc (reference) |
| **`docs/`** | Six supporting docs: OPERATING_MODEL, MULTI_CALL_DESIGN, DATA_SCHEMA, PRD_v2_DELTAS, DEMO_SHOTLIST, HARDCODED_VALUES_AUDIT |

## Demo recordings

Three calls, about 6 minutes total. Recorded with the live bot in voice mode through the browser frontend.

- **D1, Apex concierge, clean promise-to-pay (P01)** — [youtu.be/N1uE1yUmuVM](https://youtu.be/N1uE1yUmuVM). PRD's headline case. Long-tenured premium customer who forgot to update auto-debit. Watch for: concierge tone (no "credit card" before identity), specific date + mode capture, the audit panel showing each guardrail firing.
- **D2, Frequent-late defaulter, every guardrail fires (P06)** — [youtu.be/mdfG4W62_Q8](https://youtu.be/mdfG4W62_Q8). The structural-layer demo. Watch for: segment policy pushing back on a too-far PTP, partial-payment floor derived from MAD, the two-strike refusal split, the commitment-overreach validator preventing "we won't call you again".
- **D3, Hardship fast-path, no payment pressure (P08)** — [youtu.be/XM5_FArsfIA](https://youtu.be/XM5_FArsfIA). Watch for: the bot recognising medical distress and routing to `CALLBACK_CLOSE` without pressuring for payment, fast-path pre-scripted close (no LLM call), outcome posted as `human_callback_required, reason=medical_emergency`.

## Repo layout

| Folder | What's in it |
|---|---|
| **`collections-voicebot/`** | **v1** — literal starter prompt from the assignment, minimal scaffolding. The baseline. |
| **`collections-voicebot-v2/`** | **v2** — production architecture: pre-filter, intent classifier, 15-state FSM, 17-rule response validator, segment-aware prompts, AEC, web frontend |
| **`docs/`** | Product context docs (operating model, multi-call design, data schema, PRD deltas, demo shotlist, hardcoded values audit) |

Build helpers at the root: `build_writeup_docx.py` (md → docx) and `build_results_xlsx.py` (CSVs → xlsx).

## The headline result

v1 was run through the same eval harness as v2 on 15 scripted scenarios. v2 was also run on the full 42-scenario stress suite. Detail in `Aditi Mishra - Eval Results.xlsx`.

**Apples-to-apples on the same 15 scenarios:**

| Metric | v1 | v2 | Delta |
|---|---:|---:|---:|
| Calls with zero policy violations | 13% | 100% | +87 pp |
| Right tone for segment | 87% | 100% | +13 pp |
| Right escalation / transfer decision | 100% | 87% | −13 pp |
| Outcome matches expected | 87% | 67% | −20 pp |
| Full pass (all 4 axes both honour) | 13% | 67% | +54 pp |

The two metrics that move the wrong way at first glance are explained in the writeup. v1 hits 87% outcome_match because it over-promises ("I can approve a 50% waiver") and customers cheerfully agree, but the same v1 commits 20 policy violations across 15 calls (5 CIBIL breaches, 3 wrong fee, 2 waiver pre-approvals, 2 legal threats, etc.). v2 drops to 67% outcome_match because segment policy pushes back on too-far PTPs — three of the five outcome mismatches are correct behaviour the eval rubric was written before existing.

**v2 on the full 42-scenario stress suite:**

| Metric | v2 |
|---|---:|
| P0 (zero-tolerance) compliance | **100%** ✓ |
| Calls with zero policy violations | 95% |
| PTP completion rate | 9 / 13 = 69% |
| PTP specificity (of captured PTPs) | **100%** |
| Apex tone preservation | 100% |
| Calls passing all 6 axes | 52% |
| Estimated voice p95 round-trip | 1.9 s |
| Mean cost per call (LLM + STT + TTS) | ₹0.41 |

42 scenarios, priority-graded P0 (16) / P1 (26). Languages: English, Hinglish, romanised Hindi, translated Hindi, Tamil and Malayalam handoffs. P0 compliance is 100% — the single most important number, since under RBI Fair Practices Code a single P0 failure is a regulatory event.

A second eval runner (`collections-voicebot-v2/eval/runner_live.py`) grades 24 real recorded calls from `logs/`. Mean coverage tracks synthetic within 3 points (84.8% real vs 87.2% synthetic).

## Stack

| Layer | Choice |
|---|---|
| LLM (bot) | OpenAI GPT-4.1-mini |
| LLM (judge) | OpenAI GPT-4o (cross-vendor against the bot) |
| STT | Sarvam Saaras V3 (Hinglish + 10 Indian languages) |
| TTS | Sarvam Bulbul V3 (authentic Indian English accent) |
| VAD | Silero (continuous mic stream, barge-in) |
| AEC | Custom NLMS in numpy (22.7 dB reduction, no C dependencies) |
| Orchestration | Plain Python, synchronous conversation loop |
| Web frontend | FastAPI + single-page HTML |
| Outcome sink | webhook.site (production path: Salesforce or Leadsquared) |

## Running it

### v2 (the production architecture)

```powershell
cd collections-voicebot-v2
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # then fill in OPENAI_API_KEY, SARVAM_API_KEY, OUTCOME_WEBHOOK_URL

# Terminal-style web frontend (text + voice)
uvicorn app.web:app --port 8000 --reload

# OR command-line voice
python -m app.main voice --persona P01

# Run the full eval
python -m eval.runner --version v2

# Grade real recorded calls
python -m eval.runner_live
```

### v1 (baseline only)

```powershell
cd collections-voicebot
python -m eval.runner --version v1
```

### Build the deliverables

```powershell
# Regenerate the writeup docx from WRITEUP.md
python build_writeup_docx.py

# Regenerate the eval-results xlsx from the CSVs
python build_results_xlsx.py
```

## Five structural decisions that defined v2

Each moves a rule out of prompt prose and into deterministic code. Detail in `WRITEUP.md` Appendix D.

1. **Move ladder per FSM state** — ordered moves per state; LLM tags reply with `[MOVE: X]`; the bot cannot loop on the same question by construction.
2. **Segment policy table** (`app/policy.py`) — 6 priority-ordered rows, per-call thresholds (max PTP days, partial floor, abuse strikes, callback SLA) resolved from CRM context.
3. **Refusal split into two outcomes** — `do_not_call` (TRAI DND, permanent) vs `refuse_current_call` (in-call frustration, two strikes). v1 conflated them.
4. **Commitment-overreach validator** — 14 phrase patterns block the bot from saying what it cannot honour ("we won't call you again", "manager will personally call you").
5. **LLM-emitted structured tags** — five tags (`[MOVE]`, `[CUSTOMER_HARDSHIP]`, `[CUSTOMER_PTP_CAPTURED]`, `[CUSTOMER_WANTS_TO_END]`, `[END_CALL]`) let the LLM declare its understanding in a structured field; deterministic code derives the consequence. This is the architectural pattern I would carry forward to v3.

## Beyond the bot — product design for who actually runs this

The bot is the surface. The product is the operating system around it: four internal personas (Manager / Ops / Admin / Compliance), the multi-call lifecycle (3 bot attempts → human takeover), and the data contract between bank-side orchestration and the bot.

| Doc | What |
|---|---|
| **[`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md)** | Four internal personas, build vs operate split, manual override policy |
| **[`docs/MULTI_CALL_DESIGN.md`](docs/MULTI_CALL_DESIGN.md)** | Per-customer state machine, outcome → next-action mapping, mandatory human-takeover conditions |
| **[`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md)** | What the bot sees, emits, and deliberately doesn't see — the formal contract |
| **[`docs/PRD_v2_DELTAS.md`](docs/PRD_v2_DELTAS.md)** | Additions to the original PRD: internal users, operational metrics, multi-call lifecycle |
| **[`docs/DEMO_SHOTLIST.md`](docs/DEMO_SHOTLIST.md)** | Shot-by-shot script for the three demo recordings above |
| **[`docs/HARDCODED_VALUES_AUDIT.md`](docs/HARDCODED_VALUES_AUDIT.md)** | Inventory of every magic number and string; tags what is operator-tunable vs hard-coded |

## What was cut, deliberately

- No Pipecat or LiveKit framework — a sequential conversation loop should read as a loop
- No real telephony — local mic satisfies the deliverable; Exotel is documented as the production path
- No bot-side Hindi or regional responses — brief says English only
- No live in-call transfer to a human agent — escalation ends the bot's call and posts `human_callback_required`
- No mobile app — banks call customers; customers don't install collections apps
- No PTP-capture-rate as a primary metric — PRD anti-goal, easy to inflate. PTP specificity (date + mode captured) is the proxy
- No customer-facing waiver negotiation — brief says the bot cannot approve

The "no" I'd say tomorrow: bot-driven payment-link tokenisation inside the call. Needs PCI boundary integration, RBI Payment Aggregator alignment, tokenised session handling. Six-week project that blocks the bot's release timeline. The brief already captures the right thing — PTP mode of payment goes into the structured outcome and the orchestrator sends the right payment link via existing post-call SMS infrastructure.
