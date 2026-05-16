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
7. **Two-strike abuse rule with multi-insult fast-close.**
8. **Identity-first opener for non-Apex.** Bot asks for the named customer before saying "credit card" — closes the third-party disclosure gap.
9. **No PTP-capture-rate as a primary metric.** Easy to inflate under pressure (PRD anti-goal). PTP specificity (date + mode captured) is the proxy for PTP Kept Rate.
10. **Eval reframed for PMs/bankers.** Four blocks: Can it ship? Did it work? How did it feel? Was it fast? Each metric mapped to the production outcome it predicts.

## Stress-test coverage

42 scenarios across 8 buckets: factual, compliance, scope, adherence, relationship, privacy, regulatory, adversarial. Languages: English, Hinglish, Hindi, Tamil, Malayalam. Priority-graded: 16 P0 (zero-tolerance), 26 P1.

## What was cut (deliberately)

- No Pipecat (overkill for a sequential loop; the loop is what reviewers should read)
- No real telephony (local mic satisfies the deliverable; Exotel documented as the production path)
- No mobile app (banks call customers; customers don't install collections apps)
- No bot-side Hindi responses (brief says English only)
- No live SIP transfer (architecture-level design choice)
