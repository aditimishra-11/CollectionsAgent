# Mumbai Bank Collections Voicebot — v2

Outbound collections voicebot for credit card customers, DPD 1–30. **v2: full architecture.**

This is the demo / submission version. v1 lives in the sibling folder `../collections-voicebot/`.

## What v2 adds on top of v1

| Component | Purpose |
|---|---|
| **Pre-filter** | Reads CRM payload, blocks Apex+sub-prime, picks call strategy, derives modifier keys |
| **Segment-aware prompts** | 3 base strategies (Apex concierge / A_reminder / B_problem_solving) + 14 customer-dimension modifiers |
| **Intent classifier** | 29 intents, 8 fast-path (escalate immediately, no LLM) + 21 slow-path (FSM transitions) |
| **FSM** | 15 states — owns ALL routing. Three are ladder-managed (`COLLECTING`, `PTP_PROBE`, `HARDSHIP_PROBE`); the others are terminal / deflection / identity states |
| **Move ladder** | Ordered moves per ladder-managed state; LLM tags reply with `[MOVE: X]`; bot cannot loop on the same question by construction |
| **Segment policy** | `app/policy.py` resolves 1 of 6 policy rows from CRM context; sets PTP horizon, partial floor, abuse strikes, callback SLA |
| **Response validator** | 17 rules, pre-TTS, grounded in RBI Fair Practices Code, RBI Master Direction on Credit Cards 2022, DPDP Act 2023, TRAI DND |
| **Pre-scripted closes** | 8 fast-path closing templates (medical, job loss, business failure, mental distress, natural disaster, abuse, deceased, language preference) |
| **Outcome schema** | 7 top-level outcomes posted to CRM webhook (`promise_to_pay`, `already_paid`, `callback_request`, `human_callback_required`, `refused`, `wrong_number`, `no_answer`) |
| **Structured tags** | 5 LLM-emitted tags (`[MOVE]`, `[CUSTOMER_HARDSHIP]`, `[CUSTOMER_PTP_CAPTURED]`, `[CUSTOMER_WANTS_TO_END]`, `[END_CALL]`) — LLM declares its understanding, code derives the consequence |
| **Real-call eval** | `eval/runner_live.py` grades JSONL transcripts in `logs/`; auto-annotation at call end persists ground truth to disk |

## Stack

| Layer | Choice |
|---|---|
| LLM | GPT-4.1 mini |
| STT | Sarvam Saaras V3 (speech-to-text-translate, en-IN output) |
| TTS | Sarvam Bulbul V3 |
| Outcome sink | webhook.site |
| Orchestration | Plain Python — sequential loop. Pipecat noted as production path. |
| Eval judge | GPT-4o |

## Setup

```powershell
cd "C:\Users\Aditi\Desktop\GreyLabs AI PM\collections-voicebot-v2"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# .env was copied from v1 — already has keys
```

## Run a single call

v2 cannot run without a persona (the CRM payload drives everything).

Text mode:
```powershell
python -m app.main text --persona P01    # Rohan Mehta — the PRD's headline Apex case
python -m app.main text --persona P08    # medical emergency — fast path test
python -m app.main text --persona P19    # TRAI DND — must respect immediately
```

Voice mode (mic + Sarvam):
```powershell
python -m app.main voice --persona P01
```

## Run the eval

```powershell
python -m eval.runner --version v2
# writes eval/results_v2.csv + eval/transcripts/v2/*.txt
```

Generate the comparison vs v1:
```powershell
python -m eval.compare
# writes eval/comparison_v1_v2.csv + prints summary
```

## Layout

```
app/
  main.py                 CLI entry (text | voice) — requires --persona
  web.py                  FastAPI server + SSE event stream for the browser frontend
  conversation.py         v2 conversation loop, orchestrates all components
  config.py               env loader, model + cost constants
  audit.py                per-turn JSONL audit logger
  auto_annotator.py       writes eval ground truth to disk at call end
  pre_filter.py           CRM context → strategy + modifier keys + block rules
  intent_classifier.py    29-intent rule-based classifier
  fsm.py                  15-state FSM + move ladder + sticky context flags
  policy.py               SegmentPolicy table (6 rows) + resolver
  validator.py            17-rule pre-TTS validator + safe fallback templates
  prompt_builder.py       4-part prompt composer
  llm/openai_client.py
  stt/sarvam_stt.py
  tts/sarvam_tts.py
  audio/local_io.py       Silero VAD + NLMS AEC + streaming mic IO
  outcome/*.py            schema, extractor, webhook poster
  static/                 single-page browser frontend (HTML / JS / CSS)

prompts/
  base.txt                          Immutable facts + NEVER list + escalation signals
  strategy_apex_concierge.txt
  strategy_a_reminder.txt
  strategy_b_problem_solving.txt
  modifiers/                        Per-customer-dimension modifiers (tier × history × bureau × util × age × channel)
  fsm_states/                       Per-FSM-state instruction files
  closes/                           Pre-scripted close templates (8 fast-path)

eval/
  personas.csv                      37 personas
  scenarios.yaml                    42 scenarios across 8 buckets
  rule_checks.py                    Deterministic compliance scan (regex; RBI-grounded)
  judge.py                          GPT-4o judge for tone, hallucination, Likert axes
  runner.py                         Synthetic scenario runner
  runner_live.py                    Grades JSONL transcripts in logs/ (auto-annotated)
  backfill_v1.py                    Re-grades v1 transcripts with v2's judge
  annotations_live.yaml             Hand-written ground truth (3 calls); takes precedence
  compare.py                        v1 vs v2 side-by-side
  results_v2.csv                    42-scenario synthetic results
  results_live.csv                  24-call real-recording results
  comparison_v1_v2.csv              Apples-to-apples on shared 15 scenarios
```
