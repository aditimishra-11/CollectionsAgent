# Mumbai Bank Collections Voicebot — v2

Outbound collections voicebot for credit card customers, DPD 1–30. **v2: full architecture.**

This is the demo / submission version. v1 lives in the sibling folder `../collections-voicebot/`.

## What v2 adds on top of v1

| Component | Purpose |
|---|---|
| **Pre-filter** | Reads CRM payload, blocks Apex+sub-prime, picks call strategy, derives modifier keys |
| **Segment-aware prompts** | 3 base strategies (Apex concierge / A_reminder / B_problem_solving) + 14 customer-dimension modifiers |
| **Intent classifier** | 25 intents, 6 fast-path (escalate immediately, no LLM) + 19 slow-path (FSM transitions) |
| **FSM** | 13 states (10 from architecture doc + 3 India-specific: third-party, DND, legitimacy) — owns ALL routing |
| **Response validator** | Pre-TTS scan grounded in RBI Fair Practices Code, RBI Master Direction on Credit Cards 2022, DPDP Act 2023, TRAI DND |
| **Pre-scripted closes** | 6 fast-path closing templates (medical, job loss, business failure, mental distress, natural disaster, abuse) |
| **Outcome schema** | 7 terminal outcomes (vs the 4 named in the brief — split out from architecture doc) |

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
  conversation.py         v2 conversation loop, orchestrates all components
  config.py               env loader
  audit.py                per-turn JSONL audit
  pre_filter.py           CRM context → strategy + modifier keys
  intent_classifier.py    25-intent classifier (rule-based)
  fsm.py                  13-state FSM + routing
  validator.py            BFSI-grounded response validator + fallback templates
  prompt_builder.py       4-part prompt composer
  llm/openai_client.py
  stt/sarvam_stt.py
  tts/sarvam_tts.py
  audio/local_io.py
  outcome/*.py

prompts/
  base.txt                          Immutable facts + NEVER list + escalation signals
  strategy_apex_concierge.txt
  strategy_a_reminder.txt
  strategy_b_problem_solving.txt
  modifiers/                        14 modifier paragraphs (history × bureau × util × age × channel)
  fsm_states/                       13 FSM state instruction files
  closes/                           6 pre-scripted close templates

eval/
  personas.csv         27 personas (15 original + 12 new for v2 coverage)
  scenarios.yaml       27 scenarios across 6 buckets
  rule_checks.py       Deterministic compliance scan (BFSI/RBI grounded)
  judge.py             LLM-as-judge for qualitative tone checks
  runner.py            Text-mode eval harness
  compare.py           v1 vs v2 comparison table
  results_v2.csv       Generated
  comparison_v1_v2.csv Generated
```
