# Mumbai Bank Collections Voicebot

Outbound collections voicebot for credit card customers, DPD 1–30. v1 baseline.

This repo is the build for the GreyLabs AI PM take-home assignment.

## Stack

| Layer | Choice |
|---|---|
| LLM | GPT-4.1 mini |
| STT | Sarvam Saaras V3 |
| TTS | Sarvam Bulbul V3 |
| Outcome sink | `webhook.site` (swap URL for prod) |
| Orchestration | Plain Python — synchronous loop (no Pipecat in v1) |
| Eval judge | GPT-4o |

v1 is intentionally bare: one LLM, the literal starter prompt from the brief, no FSM, no validator, no segment routing. The starter prompt has six baked-in policy violations; the purpose of v1 is to establish a true failure baseline.

## Setup

```powershell
# Python 3.11+
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# fill in API keys
Copy-Item .env.example .env
# edit .env and add OPENAI_API_KEY, SARVAM_API_KEY, OUTCOME_WEBHOOK_URL
```

Get the webhook URL from [webhook.site](https://webhook.site) — open the site, copy the unique URL it generates for you.

## Run a single call

Text mode (fast iteration, no audio):
```powershell
python -m app.main text
```

Voice mode (mic + speaker — for the demo recordings):
```powershell
python -m app.main voice
```

In voice mode: press ENTER to start speaking, ENTER again to stop. The bot's audio plays automatically; the WAV is saved under `recordings/`.

## Run the eval suite

```powershell
python -m eval.runner --prompt v1_starter.txt --version v1
```

Writes:
- `eval/results_v1.csv` — per-scenario scoring
- `eval/transcripts/v1/*.txt` — full transcript per scenario
- prints summary stats

To re-run with a different prompt (for v2):
```powershell
python -m eval.runner --prompt v2_master.txt --version v2
```

## Layout

```
app/
  main.py                 # CLI entry point (text | voice)
  conversation.py         # The conversation loop — v1's only orchestration
  config.py               # Env vars
  audit.py                # Per-turn JSONL audit log
  llm/openai_client.py    # OpenAI client
  stt/sarvam_stt.py       # Saaras
  tts/sarvam_tts.py       # Bulbul
  audio/local_io.py       # Mic + speaker for the demo
  outcome/
    schema.py             # 7 terminal outcomes
    extractor.py          # End-of-call classifier
    webhook.py            # POST to webhook.site

prompts/
  v1_starter.txt          # The literal broken starter prompt (unmodified)

eval/
  personas.csv            # 15 customer profiles (CRM context)
  scenarios.yaml          # 15 scripted scenarios
  rule_checks.py          # Deterministic compliance scan
  judge.py                # LLM-as-judge for qualitative checks
  runner.py               # Runs all scenarios, writes results CSV
  results_v1.csv          # Generated

recordings/               # Demo call WAVs + transcripts
logs/                     # Per-call audit JSONL
```

## What v1 deliberately does not include

- No FSM / policy layer — compliance is left entirely to the prompt
- No intent classifier — the LLM interprets everything
- No segment-aware prompts — Apex, Edge, Spark all get the same treatment
- No response validator — whatever the LLM says goes to TTS
- No retry scheduler, no real telephony — local mic for the demo

These are exactly the things v2 adds.
