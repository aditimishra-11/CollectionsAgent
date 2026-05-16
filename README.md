# Mumbai Bank Collections Voicebot

GreyLabs AI PM take-home submission. An outbound collections voicebot for credit card customers, DPD 1–30.

## Repo layout

| Folder | What's in it |
|---|---|
| **`collections-voicebot/`** | **v1** — literal starter prompt from the assignment, minimal scaffolding. The baseline. |
| **`collections-voicebot-v2/`** | **v2** — production architecture. Pre-filter, intent classifier, 13-state FSM, response validator, segment-aware prompts, AEC, web frontend. |

Reference docs in the root (`AI PM Assignment.pdf`, `AI PM PRD.docx`, `Voicebot_Orchestration_Architecture.docx`) are the brief + my product context.

## The headline result

| Metric | v1 baseline | v2 (pre-layers) | v2 (final) |
|---|---:|---:|---:|
| **P0 (zero-tolerance) compliance** | 13% | 94% | **100%** |
| Zero policy violations | 13% | 93% | **95%** |
| **Of PTPs, date + mode captured** | low | 78% | **100%** |
| Right tone for segment | 87% (vacuous) | 100% | **100%** |
| Apex tone preservation | 0% | 100% | **100%** |
| Calls that would pass full QA | 13% | 60% | **55%** |
| Estimated voice p95 round-trip | — | 2.8 s | **1.9 s** |
| Mean cost per call | — | ₹0.31 | **₹0.41** |

Across **42 scenarios** × **5 eval cycles** including 11 adversarial stress tests (prompt injection, RBI authority claims, lawyer threats, harassment, deceased pretext, language requests).

The four structural layers (move ladder, segment policy, refuse-vs-DND split, commitment-overreach validator) plus a mid-call hardship lock and three follow-on LLM-emitted structured tags **lifted the ship-blocking P0 floor from 94% to 100%** — the single most important number on the table, since under RBI Fair Practices Code one P0 failure is a regulatory event. PTP date+mode capture went from 78% to 100%. Voice p95 latency dropped 0.9 s. The bot now physically cannot loop on the same question, promise no future contact, or extract a PTP from a customer who signalled hardship mid-call. The 5-point full-pass dip vs the prior baseline (60% → 55%) is in two judge-strict axes (hallucination, outcome-match) that are eval-rubric-tunable, not architectural.

> See `WRITEUP.md` for the full submission narrative including the iterative cycle, what the LLM-tag architecture replaced, and v3 priorities.

See `collections-voicebot-v2/eval/README.md` for the full eval methodology and `collections-voicebot-v2/eval/results_v2.csv` for per-scenario rows.

## Stack

| Layer | Choice |
|---|---|
| LLM | OpenAI GPT-4.1 mini |
| LLM judge | OpenAI GPT-4o (cross-vendor wrt the bot — see eval README) |
| STT | Sarvam Saaras V3 (handles Hinglish + 10 Indian languages) |
| TTS | Sarvam Bulbul V3 (authentic Indian English accent) |
| VAD | Silero (continuous mic stream, end-of-utterance detection) |
| AEC | Custom NLMS in numpy (22.7 dB reduction, no C dependencies) |
| Orchestration | Plain Python — synchronous conversation loop |
| Web frontend | FastAPI + single-page HTML (terminal aesthetic) |
| Outcome sink | webhook.site (production would be Salesforce / Leadsquared) |

## Running it

### v2 (the one to demo)

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
```

### v1 (baseline only)

```powershell
cd collections-voicebot
# Same setup; same .env keys
python -m eval.runner --version v1
```

## Key product decisions

1. **v1 = literal broken starter prompt, untouched.** Forces the v1→v2 delta to be real.
2. **Compliance lives in code, not prompts.** Pre-filter, FSM, and validator are the deterministic safety net. The LLM is the last line of defence.
3. **Outstanding balance is never sent to the LLM.** Defence in depth on the no-balance-without-OTP rule.
4. **NEVER list grounded in actual regulations.** RBI Fair Practices Code for Recovery Agents, RBI Master Direction on Credit Cards 2022, DPDP Act 2023, TRAI DND.
5. **Segment-aware prompting, fan-out by composition.** 3 strategies × 6 modifier dimensions, assembled fresh each turn. Spark gets instructive, Edge gets efficient, Apex gets concierge.
6. **No live SIP transfer.** Escalation = pre-scripted close + structured outcome to CRM; human calls back. Architecture-doc decision, kept.
7. **Two-strike abuse rule with multi-insult fast-close.** Strike threshold reads from the segment policy (frequent-late defaulters get one strike, others get two).
8. **Identity-first opener for non-Apex.** Bot asks for the named customer before saying "credit card" — closes the third-party disclosure gap.
9. **No PTP-capture-rate as a primary metric.** Easy to inflate under pressure (PRD anti-goal). PTP specificity (date + mode captured) is the proxy for PTP Kept Rate.
10. **Eval reframed for PMs/bankers.** Four blocks: Can it ship? Did it work? How did it feel? Was it fast? Each metric mapped to the production outcome it predicts.
11. **Refusal is two outcomes, not one.** `refused/dnd` = TRAI DND (permanent marketing suppression; collections may continue per RBI FPC). `refused/refused_current_call` = in-call refusal, retry allowed after cooling-off. Misclassifying these has regulatory blast radius — the customer was being permanently DND-suppressed for saying "leave me alone."
12. **Segment policy is a table of numbers, not a prompt nudge.** `app/policy.py` resolves a `SegmentPolicy` per call: `max_ptp_days`, `abuse_strikes_allowed`, `human_takeover_on_refuse`, `callback_sla_hours`. The LLM does the wording; the FSM enforces the numbers.
13. **Move ladder per state — the bot cannot replay a question.** Each state has an ordered list of "moves" (`ASK_DATE` → `ASK_MODE` → `CONFIRM_PTP` → `OFFER_APP_LINK` → `OFFER_PARTIAL` → `OFFER_CALLBACK`). The FSM injects the next unplayed move; LLM tags its reply with `[MOVE: X]`. Ladder exhaustion forces a graceful close. Kills the "when will you pay / when will you pay" loop structurally.
14. **Commitment-overreach validator.** Blocks the bot from saying what it cannot honour — "we won't call you again", "I'll personally call you back tomorrow", "we'll reverse the late fee" — even in DND state. Same principle as the balance rule: the bot cannot promise what it does not control.
15. **FSM owns when the call ends — with one important nuance.** LLM may *request* a close via `[END_CALL: true]`. After the structural layers stabilised, the guard was deliberately loosened past the opener — the LLM's `[END_CALL: true]` is now honoured because the other six layers (move ladder, speech authority, closing-turn coherence, structured tags, validator, FSM state machine) prevent premature close from other angles. The bot saying *"closing the call now"* and the system actually closing now agree by construction.
16. **Five LLM-emitted structured tags — the architectural pattern that absorbed five symptom-fix temptations.** Every time a regex bank missed an implicit phrasing (hardship hedging, PTP commitment, refusal frustration), the wrong reflex was to add another regex. The right answer: the LLM declares its understanding in a structured tag, and deterministic code derives the consequence. Today: `[MOVE: X]` (which move played), `[CUSTOMER_HARDSHIP: bool]` (distress signalled → ladder locks, PTP moves disabled), `[CUSTOMER_PTP_CAPTURED: bool]` (date+mode captured → terminal authorised), `[CUSTOMER_WANTS_TO_END: bool]` (customer signalled end → terminal authorised), `[END_CALL: bool]` (close this turn). Bidirectional coherence rules ensure spoken text and tags agree. See `WRITEUP.md` §5.5.
17. **Closing-turn bidirectional coherence.** If the bot tags `[END_CALL: true]`, its reply must NOT contain a question. If the bot's text contains a closing phrase (*"Take care"*, *"Have a good day"*, *"I'll let you go"*), it MUST tag `[END_CALL: true]`. The customer never hears a question right before the system hangs up, and never hears *"closing the call"* without the call actually closing.
18. **Bot-internals panel surfaces every guardrail per turn.** Each audit row shows the scenario class (discovered mid-call from intent), the ladder move played, and a colour-coded chip for every deterministic directive that fired (`policy:`, `ladder:`, `fsm:`, `validator:`, `guard:`). Compliance can grep on it; Ops can demo it.

## Stress-test coverage

42 scenarios across 8 buckets: factual, compliance, scope, adherence, relationship, privacy, regulatory, adversarial. Languages: English, Hinglish, Hindi, Tamil, Malayalam. Priority-graded: 16 P0 (zero-tolerance), 26 P1.

## Beyond the bot — product design for who actually runs this

The bot is the product surface. The PRODUCT is the operating system around it: the 4 internal personas (Manager / Ops / Admin / Compliance), the multi-call lifecycle (3 bot attempts + human takeover), and the data contract between bank-side orchestration and bot.

| Doc | What |
|---|---|
| **[`docs/OPERATING_MODEL.md`](docs/OPERATING_MODEL.md)** | The 4 internal personas, build vs operate split, manual override policy |
| **[`docs/MULTI_CALL_DESIGN.md`](docs/MULTI_CALL_DESIGN.md)** | Per-customer state machine, outcome → next-action mapping, mandatory human-takeover conditions, repeat-call prompt format |
| **[`docs/DATA_SCHEMA.md`](docs/DATA_SCHEMA.md)** | What the bot sees, emits, deliberately doesn't see — the formal contract |
| **[`docs/PRD_v2_DELTAS.md`](docs/PRD_v2_DELTAS.md)** | Additions to the original PRD: internal users, operational metrics, multi-call lifecycle |

## What was cut (deliberately)

- No Pipecat (overkill for a sequential loop; the loop is what reviewers should read)
- No real telephony (local mic satisfies the deliverable; Exotel documented as the production path)
- No mobile app (banks call customers; customers don't install collections apps)
- No bot-side Hindi responses (brief says English only)
- No live SIP transfer (architecture-level design choice)
