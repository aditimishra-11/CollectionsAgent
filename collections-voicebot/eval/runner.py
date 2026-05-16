"""Text-mode eval harness.

Loads scenarios.yaml, runs each scenario against a chosen prompt, scores it
with rule_checks + LLM judge, and writes a results CSV.

Usage:
    python -m eval.runner --prompt v1_starter.txt --version v1
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pandas as pd
import yaml
from loguru import logger

from app.config import PROMPTS_DIR, ROOT, assert_runtime_keys
from app.conversation import Conversation, ConversationConfig
from app.llm.openai_client import OpenAIClient
from eval.judge import JUDGE_PROMPTS, LLMJudge
from eval.rule_checks import aggregate_transcript_checks

EVAL_DIR = ROOT / "eval"


@dataclass
class ScenarioResult:
    scenario_id: str
    persona_id: str
    bucket: str
    difficulty: str
    expected_outcome: str
    actual_outcome: str
    outcome_match: bool
    compliance_pass: bool
    compliance_failures: list[str]
    tone_pass: bool
    tone_failures: list[str]
    escalation_correct: bool
    turns: int
    transcript_path: str
    notes: str


def _load_scenarios() -> list[dict]:
    with (EVAL_DIR / "scenarios.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def _load_personas() -> dict[str, dict]:
    df = pd.read_csv(EVAL_DIR / "personas.csv")
    return {row["persona_id"]: row.to_dict() for _, row in df.iterrows()}


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _scripted_user(turns: list[str]) -> Iterator[str]:
    yield from turns
    while True:
        yield ""  # signals end-of-input; conversation loop will exit


def run_scenario(scenario: dict, persona: dict, prompt: str, version: str) -> ScenarioResult:
    """Drive a single scenario through the bot in text mode."""
    user_gen = _scripted_user(scenario["customer_turns"])
    bot_lines: list[str] = []
    transcript_lines: list[str] = []

    def get_user_text() -> str:
        try:
            t = next(user_gen)
            transcript_lines.append(f"USER: {t}")
            return t
        except StopIteration:
            return ""

    def say_bot_text(text: str) -> None:
        bot_lines.append(text)
        transcript_lines.append(f"BOT: {text}")

    config = ConversationConfig(
        system_prompt=prompt,
        max_turns=len(scenario["customer_turns"]) + 2,
        customer_id=persona["persona_id"],
    )
    llm = OpenAIClient()
    conv = Conversation(config, get_user_text, say_bot_text, llm=llm)
    result = conv.run()

    # Outcome match
    expected = scenario["expected_outcome"]
    actual = result.outcome.outcome
    outcome_match = expected == actual

    # Compliance — rule-based
    must_pass: list[str] = scenario.get("compliance_must_pass", []) or []
    customer_text = " ".join(scenario["customer_turns"]).lower()
    customer_mentioned_amount = any(c.isdigit() for c in customer_text)
    rule_results = aggregate_transcript_checks(bot_lines, customer_mentioned_amount)

    compliance_failures = [
        name for name in must_pass if name in rule_results and not rule_results[name].passed
    ]
    compliance_pass = not compliance_failures

    # Tone — LLM judge
    tone_must_pass: list[str] = scenario.get("tone_must_pass", []) or []
    transcript_text = "\n".join(transcript_lines)
    tone_failures: list[str] = []
    if tone_must_pass:
        judge = LLMJudge()
        for check in tone_must_pass:
            jr = judge.judge(transcript_text, check)
            if not jr.passed:
                tone_failures.append(f"{check}: {jr.reason}")
    # Also run distress/argument judges if applicable
    judge_extra_keys: list[str] = []
    if "no_payment_pressure_after_distress_signal" in must_pass:
        judge_extra_keys.append("no_payment_pressure_after_distress_signal")
    if "no_argument_back" in must_pass:
        judge_extra_keys.append("no_argument_back")
    if judge_extra_keys:
        judge = LLMJudge()
        for check in judge_extra_keys:
            jr = judge.judge(transcript_text, check)
            if not jr.passed:
                compliance_failures.append(f"{check}: {jr.reason}")
                compliance_pass = False
    tone_pass = not tone_failures

    # Escalation
    expected_esc = bool(scenario.get("should_escalate", False))
    actual_esc = actual == "human_callback_required"
    escalation_correct = expected_esc == actual_esc

    # Save transcript
    transcripts_dir = EVAL_DIR / "transcripts" / version
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    tpath = transcripts_dir / f"{scenario['id']}_{result.call_id}.txt"
    tpath.write_text(transcript_text, encoding="utf-8")

    return ScenarioResult(
        scenario_id=scenario["id"],
        persona_id=persona["persona_id"],
        bucket=scenario["bucket"],
        difficulty=scenario["difficulty"],
        expected_outcome=expected,
        actual_outcome=actual,
        outcome_match=outcome_match,
        compliance_pass=compliance_pass,
        compliance_failures=compliance_failures,
        tone_pass=tone_pass,
        tone_failures=tone_failures,
        escalation_correct=escalation_correct,
        turns=result.outcome.turns,
        transcript_path=str(tpath.relative_to(ROOT)),
        notes=scenario.get("description", "").strip(),
    )


def write_results(results: list[ScenarioResult], version: str) -> Path:
    out_path = EVAL_DIR / f"results_{version}.csv"
    fieldnames = [
        "scenario_id",
        "persona_id",
        "bucket",
        "difficulty",
        "expected_outcome",
        "actual_outcome",
        "outcome_match",
        "compliance_pass",
        "compliance_failures",
        "tone_pass",
        "tone_failures",
        "escalation_correct",
        "turns",
        "transcript_path",
        "notes",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            row = r.__dict__.copy()
            row["compliance_failures"] = "; ".join(r.compliance_failures)
            row["tone_failures"] = "; ".join(r.tone_failures)
            w.writerow(row)
    return out_path


def summary_stats(results: list[ScenarioResult]) -> dict:
    total = len(results)
    outcome = sum(1 for r in results if r.outcome_match)
    comp = sum(1 for r in results if r.compliance_pass)
    tone = sum(1 for r in results if r.tone_pass)
    esc = sum(1 for r in results if r.escalation_correct)
    fully_passed = sum(
        1 for r in results if r.outcome_match and r.compliance_pass and r.tone_pass and r.escalation_correct
    )
    return {
        "total": total,
        "outcome_match_rate": outcome / total if total else 0,
        "compliance_pass_rate": comp / total if total else 0,
        "tone_pass_rate": tone / total if total else 0,
        "escalation_correct_rate": esc / total if total else 0,
        "full_pass_rate": fully_passed / total if total else 0,
        "failure_rate": 1 - (fully_passed / total) if total else 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the scenario eval suite")
    parser.add_argument("--prompt", default="v1_starter.txt", help="Prompt filename in prompts/")
    parser.add_argument("--version", default="v1", help="Tag for results files (v1, v2, etc.)")
    parser.add_argument("--only", default=None, help="Comma-separated scenario IDs to run")
    args = parser.parse_args(argv)

    assert_runtime_keys()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    scenarios = _load_scenarios()
    personas = _load_personas()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s["id"] in wanted]
    prompt = _load_prompt(args.prompt)

    results: list[ScenarioResult] = []
    for scn in scenarios:
        persona = personas.get(scn["persona_id"])
        if persona is None:
            logger.warning(f"Missing persona {scn['persona_id']} for scenario {scn['id']}, skipping.")
            continue
        logger.info(f"Running {scn['id']} ({scn['bucket']}/{scn['difficulty']})…")
        try:
            t0 = time.time()
            r = run_scenario(scn, persona, prompt, args.version)
            logger.info(
                f"  → outcome_match={r.outcome_match} compliance={r.compliance_pass} "
                f"tone={r.tone_pass} esc={r.escalation_correct} ({int((time.time()-t0)*1000)}ms)"
            )
            results.append(r)
        except Exception as e:
            logger.exception(f"Scenario {scn['id']} failed to run: {e}")

    out_path = write_results(results, args.version)
    stats = summary_stats(results)
    print("\n--- Summary ---")
    for k, v in stats.items():
        print(f"  {k:28s} {v:.2%}" if isinstance(v, float) else f"  {k:28s} {v}")
    print(f"\nResults: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
