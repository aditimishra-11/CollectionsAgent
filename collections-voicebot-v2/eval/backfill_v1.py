"""Backfill 6 judge-recoverable axes onto v1's results CSV.

v1 was scored with the older 4-axis runner that predates v2's instrumentation.
Re-grade v1's saved transcripts using v2's GPT-4o judge so v1-vs-v2 numbers
on hallucination, PTP specificity, containment, and the 3 Likerts are
directly comparable.

Writes to: collections-voicebot/eval/results_v1_backfilled.csv
Cost: ~$0.50, ~5 minutes
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from loguru import logger

HERE = Path(__file__).resolve().parent
V1_ROOT = HERE.parent.parent / "collections-voicebot"
V1_TRANSCRIPTS = V1_ROOT / "eval" / "transcripts" / "v1"
V1_CSV = V1_ROOT / "eval" / "results_v1.csv"
OUT_CSV = V1_ROOT / "eval" / "results_v1_backfilled.csv"

# Reuse v2's judge — same prompts, same model (GPT-4o), so the v1 / v2
# numbers on the same axes are scored by exactly the same rubric.
sys.path.insert(0, str(HERE.parent))
from eval.judge import LLMJudge  # noqa: E402


def load_transcript(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_ptp_slots(transcript: str) -> tuple[bool, bool]:
    """Best-effort regex extraction. v1 didn't capture slots so we infer from
    the transcript itself: did the customer say a specific date AND a payment mode?
    Returns (date_captured, mode_captured)."""
    text = transcript.lower()
    # Date markers — month names, weekdays, 'today', 'tomorrow', 'next week',
    # 'X-th', explicit DD/MM, 'by Friday', etc.
    date_patterns = [
        r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b(today|tomorrow|day after tomorrow)\b",
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b",
        r"\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
        r"\bby\s+(this|next)\s+(week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        r"\b\d{1,2}/\d{1,2}\b",
        r"\bnext\s+(week|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    ]
    date_ok = any(re.search(p, text) for p in date_patterns)
    mode_patterns = [
        r"\b(upi|gpay|google pay|phonepe|paytm)\b",
        r"\bnet[\s-]?banking\b",
        r"\b(imps|neft|rtgs)\b",
        r"\b(debit|credit) card\b",
        r"\bauto[\s-]?debit\b",
        r"\bnach\b",
    ]
    mode_ok = any(re.search(p, text) for p in mode_patterns)
    return date_ok, mode_ok


def main() -> int:
    if not V1_TRANSCRIPTS.exists():
        logger.error(f"v1 transcripts not found at {V1_TRANSCRIPTS}")
        return 1

    judge = LLMJudge()
    rows_in: list[dict] = []
    with V1_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows_in = list(reader)
    logger.info(f"Loaded {len(rows_in)} v1 rows")

    rows_out: list[dict] = []
    for i, row in enumerate(rows_in, 1):
        scn = row["scenario_id"]
        # find the matching transcript
        matches = list(V1_TRANSCRIPTS.glob(f"{scn}_call_*.txt"))
        if not matches:
            logger.warning(f"  [{scn}] no transcript found, skipping judge")
            transcript = ""
        else:
            transcript = load_transcript(matches[0])

        logger.info(f"[{i}/{len(rows_in)}] {scn} ({len(transcript)} chars)")

        if not transcript:
            row.update({
                "hallucination_pass": "",
                "slot_date_captured": "",
                "slot_mode_captured": "",
                "is_ptp_specific": "",
                "contained": "",
                "empathy_score": "",
                "sentiment_trajectory": "",
                "context_retention": "",
            })
            rows_out.append(row)
            continue

        # Judge calls — same prompts as v2's runner
        try:
            halluc = judge.judge(transcript, "no_hallucination")
            empathy = judge.likert(transcript, "empathy_score")
            sentiment = judge.likert(transcript, "sentiment_trajectory")
            context = judge.likert(transcript, "context_retention")
        except Exception as e:
            logger.exception(f"  judge failed: {e}")
            halluc = type("X", (), {"passed": None})()
            empathy = type("X", (), {"score": None})()
            sentiment = type("X", (), {"score": None})()
            context = type("X", (), {"score": None})()

        # Slot extraction (only relevant for PTP outcomes)
        is_ptp = row.get("actual_outcome") == "promise_to_pay"
        if is_ptp:
            date_ok, mode_ok = extract_ptp_slots(transcript)
            is_specific = date_ok and mode_ok
        else:
            date_ok, mode_ok, is_specific = False, False, False

        # Containment: any outcome that isn't human_callback_required
        contained = row.get("actual_outcome") != "human_callback_required"

        row.update({
            "hallucination_pass": str(halluc.passed) if halluc.passed is not None else "",
            "slot_date_captured": str(date_ok) if is_ptp else "",
            "slot_mode_captured": str(mode_ok) if is_ptp else "",
            "is_ptp_specific": str(is_specific) if is_ptp else "",
            "contained": str(contained),
            "empathy_score": str(empathy.score) if empathy.score is not None else "",
            "sentiment_trajectory": str(sentiment.score) if sentiment.score is not None else "",
            "context_retention": str(context.score) if context.score is not None else "",
        })
        rows_out.append(row)

        logger.info(
            f"  halluc={halluc.passed} empathy={empathy.score} "
            f"sentiment={sentiment.score} context={context.score} "
            f"ptp_specific={is_specific if is_ptp else 'n/a'} contained={contained}"
        )

    # Write extended CSV
    fieldnames = list(rows_in[0].keys()) + [
        "hallucination_pass",
        "slot_date_captured", "slot_mode_captured", "is_ptp_specific",
        "contained",
        "empathy_score", "sentiment_trajectory", "context_retention",
    ]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows_out:
            w.writerow({k: r.get(k, "") for k in fieldnames})

    logger.info(f"Wrote {OUT_CSV}")

    # Aggregate
    def _tt(v): return str(v).strip().lower() in {"true", "1"}
    def rate(key):
        vals = [r.get(key, "") for r in rows_out if r.get(key, "") != ""]
        if not vals: return None
        return sum(1 for v in vals if _tt(v)) / len(vals)
    def mean(key):
        vals = [float(r[key]) for r in rows_out if r.get(key) not in {"", None}]
        return sum(vals) / len(vals) if vals else None

    print("\n=== v1 backfilled aggregate ===")
    print(f"  Hallucination pass:    {rate('hallucination_pass'):.0%}" if rate('hallucination_pass') is not None else "  Hallucination: n/a")
    ptp_specific = [r for r in rows_out if r.get('actual_outcome') == 'promise_to_pay']
    if ptp_specific:
        n_specific = sum(1 for r in ptp_specific if _tt(r.get('is_ptp_specific', '')))
        print(f"  PTP specificity:       {n_specific}/{len(ptp_specific)} = {100*n_specific/len(ptp_specific):.0f}%")
    print(f"  Containment rate:      {rate('contained'):.0%}")
    print(f"  Empathy (Likert mean): {mean('empathy_score'):.2f}")
    print(f"  Sentiment (mean):      {mean('sentiment_trajectory'):.2f}")
    print(f"  Context retention:     {mean('context_retention'):.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
