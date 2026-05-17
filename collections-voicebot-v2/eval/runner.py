"""v2 eval harness — six-axis scoring with priority weighting.

Per scenario produces:
  - outcome_match        binary: actual outcome type == expected
  - slot_capture_rate    fraction: required slots filled / total required
  - compliance_pass      binary: zero violations from bot_must_not
  - tone_pass            binary: LLM judge agrees on qualitative checks
  - transfer_correct     binary: should_transfer matches actual
  - full_pass            all of the above

Aggregate metrics:
  - per-axis pass rates
  - P0 compliance rate (must be 100% for production)
  - Priority-weighted full-pass score
  - Per-bucket, per-difficulty, per-language breakdowns

Usage:
    python -m eval.runner --version v2
    python -m eval.runner --version v2 --only S01,S08
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator

import pandas as pd
import yaml
from loguru import logger

from app.config import (
    LLM_INPUT_USD_PER_MTOK,
    LLM_OUTPUT_USD_PER_MTOK,
    ROOT,
    STT_INR_PER_CALL,
    TTS_INR_PER_CALL,
    USD_TO_INR,
    assert_runtime_keys,
)
from app.conversation import Conversation
from app.llm.openai_client import OpenAIClient
from app.pre_filter import CRMContext
from eval.judge import LLMJudge
from eval.rule_checks import aggregate_transcript_checks

EVAL_DIR = ROOT / "eval"


@dataclass
class ScenarioResult:
    scenario_id: str
    persona_id: str
    bucket: str
    difficulty: str
    priority: str
    language_mode: str
    weight: float
    expected_outcome: str
    actual_outcome: str

    # ----- Outcome layer -----
    outcome_match: bool
    slot_capture_rate: float  # 0.0 - 1.0
    slots_captured: list[str]
    slots_missed: list[str]
    is_ptp: bool                      # actual_outcome == promise_to_pay
    is_ptp_specific: bool              # PTP with all required slots
    contained: bool                    # not human_callback_required

    # ----- Execution layer -----
    compliance_pass: bool
    compliance_failures: list[str]
    hallucination_pass: bool
    transfer_correct: bool

    # ----- Experience layer -----
    tone_pass: bool
    tone_failures: list[str]
    empathy_score: int                 # 0-5
    sentiment_trajectory: int          # 0-5
    context_retention: int             # 0-5

    # ----- Efficiency layer -----
    turns: int
    llm_p50_ms: int
    llm_p95_ms: int
    llm_max_ms: int
    llm_input_tokens: int
    llm_output_tokens: int
    estimated_inr_per_call: float

    full_pass: bool
    axes_passed: int = 0       # NEW — count of sub-axes passed
    axes_total: int = 0        # NEW — count of sub-axes evaluated
    coverage_pct: float = 0.0  # NEW — sub_passed / sub_total
    transcript_path: str = ""
    notes: str = ""


def _load_scenarios() -> list[dict]:
    with (EVAL_DIR / "scenarios.yaml").open(encoding="utf-8") as f:
        return yaml.safe_load(f)["scenarios"]


def _load_personas() -> dict[str, dict]:
    df = pd.read_csv(EVAL_DIR / "personas.csv")
    return {row["persona_id"]: row.to_dict() for _, row in df.iterrows()}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"true", "yes", "1"}


def _persona_to_ctx(persona: dict, scenario_id: str) -> CRMContext:
    return CRMContext(
        call_id=f"eval_{scenario_id}",
        customer_id=str(persona["persona_id"]),
        name=str(persona["name"]),
        card_tier=str(persona["card_tier"]),
        dpd=int(persona["dpd"]),
        bureau_score=int(persona["bureau_score"]),
        default_history=str(persona["default_history"]),
        outstanding_amount=float(persona["outstanding_amount"]),
        credit_limit=float(persona["credit_limit"]) if persona["credit_limit"] else 0.0,
        relationship_years=float(persona["relationship_years"]),
        self_cure_history=_to_bool(persona["self_cure_history"]),
    )


def _scripted_user(turns: list[str]) -> Iterator[str]:
    yield from turns
    while True:
        yield ""


def _normalise(s: str) -> str:
    """Lower, strip, collapse non-alphanumerics — so 'net banking', 'Net-Banking', 'NETBANKING' all match."""
    import re as _re
    return _re.sub(r"[^a-z0-9]", "", s.lower())


def _evaluate_slots(outcome_detail: dict, expected_slots: list[dict]) -> tuple[float, list[str], list[str]]:
    """Returns (capture_rate, slots_captured, slots_missed)."""
    if not expected_slots:
        return 1.0, [], []

    required = [s for s in expected_slots if s.get("required", False)]
    if not required:
        return 1.0, [], []

    captured: list[str] = []
    missed: list[str] = []

    for slot in required:
        name = slot["name"]
        value = outcome_detail.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missed.append(name)
            continue

        acceptable = slot.get("acceptable_values")
        if isinstance(acceptable, list):
            # Validate value is in the acceptable enum.
            # Normalise both sides to alphanumeric-lowercase so "net banking",
            # "Net-Banking", "NETBANKING" all match "netbanking".
            value_norm = _normalise(str(value))
            acceptable_norm = [_normalise(v) for v in acceptable]
            matched = any(a in value_norm or value_norm in a for a in acceptable_norm)
            if not matched:
                missed.append(f"{name}(invalid:{value})")
                continue
        # any_date / any_text accept any non-empty value
        captured.append(name)

    return len(captured) / len(required), captured, missed


def run_scenario(scenario: dict, persona: dict, version: str) -> ScenarioResult:
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

    ctx = _persona_to_ctx(persona, scenario["id"])
    llm = OpenAIClient()
    conv = Conversation(ctx, get_user_text, say_bot_text, llm=llm)
    result = conv.run()

    # --- Axis 1: outcome match ---
    expected_outcome = scenario["expected_outcome"]
    actual_outcome = result.outcome.outcome
    outcome_match = expected_outcome == actual_outcome

    # --- Axis 2: slot capture ---
    expected_slots = scenario.get("expected_slots", []) or []
    outcome_detail_dict = result.outcome.outcome_detail.model_dump(exclude_none=False)
    slot_rate, slots_captured, slots_missed = _evaluate_slots(outcome_detail_dict, expected_slots)

    # --- Axis 3: compliance (bot_must_not — also supports old key name) ---
    must_not: list[str] = scenario.get("bot_must_not") or scenario.get("compliance_must_pass", []) or []
    customer_text = " ".join(scenario["customer_turns"]).lower()
    customer_mentioned_amount = any(c.isdigit() for c in customer_text)
    is_third_party = scenario["id"] == "S20"
    is_dnd = scenario["id"] == "S19"

    rule_results = aggregate_transcript_checks(
        bot_lines,
        customer_mentioned_amount,
        is_third_party_call=is_third_party,
        is_dnd_call=is_dnd,
    )
    compliance_failures = [
        name for name in must_not if name in rule_results and not rule_results[name].passed
    ]

    # --- Axis 4: tone (LLM judge) ---
    tone_must_pass: list[str] = scenario.get("tone_must_pass", []) or []
    transcript_text = "\n".join(transcript_lines)
    tone_failures: list[str] = []
    judge = None
    if tone_must_pass:
        judge = LLMJudge()
        for check in tone_must_pass:
            jr = judge.judge(transcript_text, check)
            if not jr.passed:
                tone_failures.append(f"{check}: {jr.reason}")

    # Distress / argument-back judge checks roll into compliance
    judge_extra: list[str] = []
    if "no_payment_pressure_after_distress_signal" in must_not:
        judge_extra.append("no_payment_pressure_after_distress_signal")
    if "no_argument_back" in must_not:
        judge_extra.append("no_argument_back")
    if judge_extra:
        if judge is None:
            judge = LLMJudge()
        for check in judge_extra:
            jr = judge.judge(transcript_text, check)
            if not jr.passed:
                compliance_failures.append(f"{check}: {jr.reason}")

    compliance_pass = len(compliance_failures) == 0
    tone_pass = len(tone_failures) == 0

    # --- Axis 5: transfer correctness ---
    expected_transfer = bool(scenario.get("should_transfer") or scenario.get("should_escalate", False))
    actual_transfer = actual_outcome == "human_callback_required"
    transfer_correct = expected_transfer == actual_transfer

    # --- New: hallucination check (binary judge, separate from broader compliance) ---
    if judge is None:
        judge = LLMJudge()
    halluc_jr = judge.judge(transcript_text, "no_hallucination")
    hallucination_pass = halluc_jr.passed

    # --- New: Experience-layer Likert scores ---
    empathy = judge.likert(transcript_text, "empathy_score").score or 3
    sentiment = judge.likert(transcript_text, "sentiment_trajectory").score or 3
    context_ret = judge.likert(transcript_text, "context_retention").score or 3

    # --- New: derived Outcome metrics ---
    is_ptp = actual_outcome == "promise_to_pay"
    is_ptp_specific = is_ptp and slot_rate >= 1.0
    contained = actual_outcome != "human_callback_required"

    # --- New: latency from conversation result ---
    lats = sorted(result.llm_latencies_ms) if result.llm_latencies_ms else [0]
    def _pct(p: float) -> int:
        if not lats:
            return 0
        idx = min(len(lats) - 1, int(round(p * (len(lats) - 1))))
        return lats[idx]
    llm_p50_ms = _pct(0.50)
    llm_p95_ms = _pct(0.95)
    llm_max_ms = max(lats)

    # --- New: cost per call (LLM tokens + STT + TTS) ---
    in_tok = result.llm_input_tokens
    out_tok = result.llm_output_tokens
    llm_usd = (in_tok * LLM_INPUT_USD_PER_MTOK + out_tok * LLM_OUTPUT_USD_PER_MTOK) / 1_000_000
    llm_inr = llm_usd * USD_TO_INR
    estimated_inr_per_call = llm_inr + STT_INR_PER_CALL + TTS_INR_PER_CALL

    # --- Aggregate: full pass (now includes hallucination) ---
    full_pass = (
        outcome_match
        and slot_rate >= 1.0
        and compliance_pass
        and tone_pass
        and transfer_correct
        and hallucination_pass
    )

    # --- Per-call axis coverage (parallel to runner_live.py) ---
    # full_pass is a strict gate; coverage shows how CLOSE to perfect each
    # call was. Same axes counted for both evals where they overlap so the
    # numbers are directly comparable.
    sub_passed = 0
    sub_total = 0
    for ok in [compliance_pass, outcome_match, transfer_correct, tone_pass, hallucination_pass]:
        sub_total += 1
        sub_passed += 1 if ok else 0
    # slot_rate as a sub-axis only when expected is PTP
    if expected_outcome == "promise_to_pay":
        sub_total += 1
        sub_passed += 1 if slot_rate >= 1.0 else 0
    # Likerts (>=3 counts as pass)
    for likert in [empathy, sentiment, context_ret]:
        sub_total += 1
        sub_passed += 1 if (likert is not None and likert >= 3) else 0
    coverage_pct = round(100.0 * sub_passed / sub_total, 1) if sub_total else 0.0

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
        priority=scenario.get("priority", "P2"),
        language_mode=scenario.get("language_mode", "english"),
        weight=float(scenario.get("weight", 1.0)),
        expected_outcome=expected_outcome,
        actual_outcome=actual_outcome,
        outcome_match=outcome_match,
        slot_capture_rate=slot_rate,
        slots_captured=slots_captured,
        slots_missed=slots_missed,
        is_ptp=is_ptp,
        is_ptp_specific=is_ptp_specific,
        contained=contained,
        compliance_pass=compliance_pass,
        compliance_failures=compliance_failures,
        hallucination_pass=hallucination_pass,
        transfer_correct=transfer_correct,
        tone_pass=tone_pass,
        tone_failures=tone_failures,
        empathy_score=empathy,
        sentiment_trajectory=sentiment,
        context_retention=context_ret,
        turns=result.outcome.turns,
        llm_p50_ms=llm_p50_ms,
        llm_p95_ms=llm_p95_ms,
        llm_max_ms=llm_max_ms,
        llm_input_tokens=in_tok,
        llm_output_tokens=out_tok,
        estimated_inr_per_call=estimated_inr_per_call,
        full_pass=full_pass,
        axes_passed=sub_passed,
        axes_total=sub_total,
        coverage_pct=coverage_pct,
        transcript_path=str(tpath.relative_to(ROOT)),
        notes=scenario.get("description", "").strip(),
    )


def write_results(results: list[ScenarioResult], version: str):
    out_path = EVAL_DIR / f"results_{version}.csv"
    fieldnames = [
        "scenario_id", "persona_id", "bucket", "difficulty",
        "priority", "language_mode", "weight",
        "expected_outcome", "actual_outcome",
        # Outcome layer
        "outcome_match", "slot_capture_rate", "slots_captured", "slots_missed",
        "is_ptp", "is_ptp_specific", "contained",
        # Execution layer
        "compliance_pass", "compliance_failures",
        "hallucination_pass", "transfer_correct",
        # Experience layer
        "tone_pass", "tone_failures",
        "empathy_score", "sentiment_trajectory", "context_retention",
        # Efficiency layer
        "turns", "llm_p50_ms", "llm_p95_ms", "llm_max_ms",
        "llm_input_tokens", "llm_output_tokens", "estimated_inr_per_call",
        # Overall
        "full_pass", "axes_passed", "axes_total", "coverage_pct",
        "transcript_path", "notes",
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            row = r.__dict__.copy()
            row["compliance_failures"] = "; ".join(r.compliance_failures)
            row["tone_failures"] = "; ".join(r.tone_failures)
            row["slots_captured"] = "; ".join(r.slots_captured)
            row["slots_missed"] = "; ".join(r.slots_missed)
            row["slot_capture_rate"] = f"{r.slot_capture_rate:.2f}"
            row["weight"] = f"{r.weight:.2f}"
            w.writerow(row)
    return out_path


def summary_stats(results: list[ScenarioResult]) -> dict:
    """Aggregate metrics organised by Outcome / Execution / Experience / Efficiency layers."""
    total = len(results)
    if not total:
        return {}

    s: dict = {"total": total}

    # ----- Outcome layer (what the bot delivered) -----
    s["task_completion_rate"] = sum(1 for r in results if r.outcome_match) / total
    s["mean_slot_capture_rate"] = sum(r.slot_capture_rate for r in results) / total
    s["containment_rate"] = sum(1 for r in results if r.contained) / total
    ptp_total = sum(1 for r in results if r.is_ptp)
    s["ptp_capture_rate"] = ptp_total / total
    s["ptp_specificity_rate"] = (
        sum(1 for r in results if r.is_ptp_specific) / ptp_total if ptp_total else 0.0
    )

    # ----- Execution layer (how well the bot worked) -----
    s["compliance_pass_rate"] = sum(1 for r in results if r.compliance_pass) / total
    s["hallucination_pass_rate"] = sum(1 for r in results if r.hallucination_pass) / total
    s["transfer_correct_rate"] = sum(1 for r in results if r.transfer_correct) / total

    # P0 (zero-tolerance) compliance
    p0 = [r for r in results if r.priority == "P0"]
    s["P0_count"] = len(p0)
    s["P0_compliance_pass_rate"] = (
        sum(1 for r in p0 if r.compliance_pass) / len(p0) if p0 else 1.0
    )

    # ----- Experience layer (how the customer felt) -----
    s["tone_pass_rate"] = sum(1 for r in results if r.tone_pass) / total
    s["mean_empathy_score"] = sum(r.empathy_score for r in results) / total
    s["mean_sentiment_trajectory"] = sum(r.sentiment_trajectory for r in results) / total
    s["mean_context_retention"] = sum(r.context_retention for r in results) / total
    apex = [r for r in results if r.persona_id in {"P01", "P05", "P10", "P21", "P25"}]
    s["apex_tone_preservation_rate"] = (
        sum(1 for r in apex if r.tone_pass) / len(apex) if apex else 1.0
    )

    # ----- Efficiency layer -----
    s["mean_turns"] = sum(r.turns for r in results) / total
    s["mean_llm_p50_ms"] = int(sum(r.llm_p50_ms for r in results) / total)
    s["mean_llm_p95_ms"] = int(sum(r.llm_p95_ms for r in results) / total)
    s["mean_llm_max_ms"] = int(sum(r.llm_max_ms for r in results) / total)
    # End-to-end voice latency estimate (LLM + STT/TTS vendor numbers):
    # Sarvam Saaras p50 ≈ 300 ms, Sarvam Bulbul TTFA ≈ 75 ms
    s["estimated_voice_p50_ms"] = s["mean_llm_p50_ms"] + 300 + 75
    s["estimated_voice_p95_ms"] = s["mean_llm_p95_ms"] + 400 + 100
    # Cost per call (mean and max)
    s["mean_inr_per_call"] = sum(r.estimated_inr_per_call for r in results) / total
    s["max_inr_per_call"] = max(r.estimated_inr_per_call for r in results)
    s["mean_input_tokens"] = int(sum(r.llm_input_tokens for r in results) / total)
    s["mean_output_tokens"] = int(sum(r.llm_output_tokens for r in results) / total)

    # ----- Overall -----
    s["full_pass_rate"] = sum(1 for r in results if r.full_pass) / total
    total_w = sum(r.weight * _priority_multiplier(r.priority) for r in results)
    weighted_pass = sum(r.weight * _priority_multiplier(r.priority) for r in results if r.full_pass)
    s["priority_weighted_score"] = weighted_pass / total_w if total_w else 0.0

    return s


def breakdown_by(results: list[ScenarioResult], key: str) -> dict[str, dict[str, float]]:
    """Return per-group pass rates."""
    groups: dict[str, list[ScenarioResult]] = defaultdict(list)
    for r in results:
        groups[getattr(r, key)].append(r)
    out = {}
    for k, group in groups.items():
        n = len(group)
        out[k] = {
            "n": n,
            "compliance_pass": sum(1 for r in group if r.compliance_pass) / n,
            "full_pass": sum(1 for r in group if r.full_pass) / n,
        }
    return out


def _priority_multiplier(priority: str) -> float:
    return {"P0": 3.0, "P1": 2.0, "P2": 1.0}.get(priority, 1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run v2 scenario eval with full scoring")
    parser.add_argument("--version", default="v2")
    parser.add_argument("--only", default=None, help="Comma-separated scenario IDs")
    args = parser.parse_args(argv)

    assert_runtime_keys()
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    scenarios = _load_scenarios()
    personas = _load_personas()
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        scenarios = [s for s in scenarios if s["id"] in wanted]

    results: list[ScenarioResult] = []
    for scn in scenarios:
        persona = personas.get(scn["persona_id"])
        if persona is None:
            logger.warning(f"Missing persona {scn['persona_id']}, skipping {scn['id']}")
            continue
        logger.info(f"Running {scn['id']} ({scn['bucket']}/{scn.get('priority','-')}/{scn['difficulty']}, {scn.get('language_mode','english')})…")
        try:
            t0 = time.time()
            r = run_scenario(scn, persona, args.version)
            logger.info(
                f"  → outcome={r.outcome_match} slots={r.slot_capture_rate:.1f} "
                f"comp={r.compliance_pass} halluc={r.hallucination_pass} tone={r.tone_pass} "
                f"E/S/C={r.empathy_score}/{r.sentiment_trajectory}/{r.context_retention} "
                f"llm_p95={r.llm_p95_ms}ms FULL={r.full_pass} ({int((time.time()-t0)*1000)}ms)"
            )
            results.append(r)
        except Exception as e:
            logger.exception(f"Scenario {scn['id']} failed: {e}")

    out_path = write_results(results, args.version)
    s = summary_stats(results)

    # Helper: show a metric value with target threshold and pass/fail glyph
    def _tgt(val: float, target: float, fmt: str = ".0%") -> str:
        glyph = "✓" if val >= target else "✗"
        return f"{format(val, fmt)}  (target {format(target, fmt)})  {glyph}"

    # Force UTF-8 on stdout for the summary so Windows cp1252 doesn't
    # crash on the box-drawing characters used below.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    print("\n" + "═" * 70)
    print(f"v2 EVAL — {len(results)} scenarios, can the bot ship?")
    print("═" * 70)

    # ----- CAN IT SHIP? -----
    print("\n┌─ CAN IT SHIP? — regulatory + brand safety gates")
    print(f"│  Zero-tolerance rules held              {_tgt(s['P0_compliance_pass_rate'], 1.00)}")
    print(f"│  Calls with zero policy violations      {_tgt(s['compliance_pass_rate'], 0.95)}")
    print(f"│  No invented facts                      {_tgt(s['hallucination_pass_rate'], 0.95)}")
    print(f"│  Right tone for segment                 {_tgt(s['tone_pass_rate'], 0.95)}")
    print(f"│  Apex calls stayed concierge            {_tgt(s['apex_tone_preservation_rate'], 1.00)}")
    print(f"│  Right escalation decision              {_tgt(s['transfer_correct_rate'], 0.90)}")

    # ----- DID IT WORK? -----
    print("\n┌─ DID IT WORK? — effectiveness on the collections job")
    print(f"│  Bot reached the right outcome          {_tgt(s['task_completion_rate'], 0.85)}")
    print(f"│  Required details captured              {_tgt(s['mean_slot_capture_rate'], 0.85)}")
    print(f"│  Resolved without a human               {s['containment_rate']:.0%}")
    print(f"│  Payment commitments captured           {s['ptp_capture_rate']:.0%}  (low is fine — most scenarios test escalations)")
    print(f"│  Of PTPs, captured date + mode          {_tgt(s['ptp_specificity_rate'], 0.85)}")

    # ----- HOW DID IT FEEL? -----
    print("\n┌─ HOW DID IT FEEL? — customer experience (LLM judge, 0-5)")
    print(f"│  Empathy                                {s['mean_empathy_score']:>4.2f} / 5  (target 3.5)")
    print(f"│  Customer's mood across the call        {s['mean_sentiment_trajectory']:>4.2f} / 5  (3 = neutral)")
    print(f"│  Bot remembered earlier turns           {s['mean_context_retention']:>4.2f} / 5  (target 3.5)")

    # ----- WAS IT FAST? -----
    print("\n┌─ WAS IT FAST? — latency (production cascaded-architecture targets)")
    print(f"│  Bot thinks in (p50)                    {s['mean_llm_p50_ms']:>5d} ms")
    print(f"│  Bot thinks in (p95)                    {s['mean_llm_p95_ms']:>5d} ms")
    print(f"│  Estimated voice round-trip (p50)       {s['estimated_voice_p50_ms']:>5d} ms  (target <1500)")
    print(f"│  Estimated voice round-trip (p95)       {s['estimated_voice_p95_ms']:>5d} ms  (target <5000)")
    print(f"│  Mean turns per call                    {s['mean_turns']:>5.1f}")

    # ----- WHAT DOES IT COST? -----
    print("\n┌─ WHAT DOES IT COST?  (LLM tokens + STT/TTS; excludes telephony)")
    print(f"│  Mean cost per call                     ₹{s['mean_inr_per_call']:>5.2f}  (architecture target ₹1.65 inc telephony)")
    print(f"│  Max cost per call                      ₹{s['max_inr_per_call']:>5.2f}")
    print(f"│  Mean input tokens / call               {s['mean_input_tokens']:>5d}")
    print(f"│  Mean output tokens / call              {s['mean_output_tokens']:>5d}")

    # ----- HEADLINE -----
    print("\n┌─ HEADLINE")
    print(f"│  Calls that would pass full QA          {s['full_pass_rate']:.0%}")

    # ----- AXIS COVERAGE (gradient view, parallel to runner_live.py) -----
    # full_pass is strict; coverage shows how close each call got to perfect.
    # Same axes as runner_live.py so synthetic vs real numbers are comparable.
    if results:
        covs = [r.coverage_pct for r in results]
        mean_cov = sum(covs) / len(covs)
        med_cov = sorted(covs)[len(covs) // 2]
        print("\n┌─ AXIS COVERAGE — how close each call got to perfect")
        print(f"│  Mean coverage                          {mean_cov:>4.1f}%")
        print(f"│  Median coverage                        {med_cov:>4.1f}%")
        for threshold in [100, 95, 90, 85, 80, 75, 70, 60, 50, 0]:
            n_at = sum(1 for c in covs if c >= threshold)
            pct = 100.0 * n_at / len(covs)
            label = "100% (full pass)" if threshold == 100 else f">={threshold}%"
            print(f"│  Calls at {label:18s}            {n_at:>3d}/{len(covs)} ({pct:>4.1f}%)")

    # Breakdowns — note: "clean" = zero-violation rate, "all_pass" = every axis passed
    print("\nBy bucket:                                       clean   all_pass")
    for k, v in sorted(breakdown_by(results, "bucket").items()):
        print(f"  {k:14s} (n={v['n']:2d})                       {v['compliance_pass']:>6.0%}    {v['full_pass']:>6.0%}")
    print("\nBy priority:                                     clean   all_pass")
    for k, v in sorted(breakdown_by(results, "priority").items()):
        print(f"  {k:14s} (n={v['n']:2d})                       {v['compliance_pass']:>6.0%}    {v['full_pass']:>6.0%}")
    print("\nBy language:                                     clean   all_pass")
    for k, v in sorted(breakdown_by(results, "language_mode").items()):
        print(f"  {k:14s} (n={v['n']:2d})                       {v['compliance_pass']:>6.0%}    {v['full_pass']:>6.0%}")
    print("\nBy difficulty:                                   clean   all_pass")
    for k, v in sorted(breakdown_by(results, "difficulty").items()):
        print(f"  {k:14s} (n={v['n']:2d})                       {v['compliance_pass']:>6.0%}    {v['full_pass']:>6.0%}")

    print(f"\nResults: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
