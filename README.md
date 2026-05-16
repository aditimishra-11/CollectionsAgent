# Mumbai Bank Collections Voicebot

GreyLabs AI PM take-home submission. An outbound collections voicebot for credit card customers, DPD 1–30.

## Repo layout

| Folder | What's in it |
|---|---|
| **`collections-voicebot/`** | **v1** — literal starter prompt from the assignment, minimal scaffolding. The baseline. |
| **`collections-voicebot-v2/`** | **v2** — production architecture. Pre-filter, intent classifier, 13-state FSM, response validator, segment-aware prompts, AEC, web frontend. |

Reference docs in the root (`AI PM Assignment.pdf`, `AI PM PRD.docx`, `Voicebot_Orchestration_Architecture.docx`) are the brief + my product context.

## The headline result

| Metric | v1 baseline | v2 |
|---|---:|---:|
| Calls that would pass full QA | 13% | **60%** |
| Zero policy violations | 13% | **93%** |
| P0 (zero-tolerance) compliance | 13% | **94%** |
| Apex tone preservation | 0% | **100%** |
| Right tone for segment | 87% (vacuous) | **100%** |
| Mean cost per call | — | **₹0.31** |
| Estimated voice p95 round-trip | — | **2.8 s** (target <5 s) |

Across **42 scenarios** including 11 adversarial stress tests (prompt injection, RBI authority claims, lawyer threats, harassment, deceased pretext, language requests).

> **Note:** the numbers above are the eval result from before the four structural layers landed (move ladder, segment policy, refuse/DND split, commitment validator). A re-run after those changes is the definition-of-done for the next eval pass — expect movement on full-QA-pass and zero-violation rates, possibly with 1–2 regressions to chase.

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
15. **FSM owns when the call ends.** LLM may *request* a close via `[END_CALL]`, but it's only honoured when the FSM authorised the close. Stops the LLM from self-terminating on hostility cues when the FSM said "stay, calm reset."
16. **Bot-internals panel surfaces every guardrail per turn.** Each audit row shows the scenario class (discovered mid-call from intent), the ladder move played, and a colour-coded chip for every deterministic directive that fired (`policy:`, `ladder:`, `fsm:`, `validator:`, `guard:`). Compliance can grep on it; Ops can demo it.

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
