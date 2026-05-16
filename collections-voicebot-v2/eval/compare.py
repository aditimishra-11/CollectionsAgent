"""Compare v1 vs v2 eval results.

Reads v1 results (from sibling collections-voicebot folder) and v2 results,
writes a side-by-side comparison CSV, and prints the metrics table.

v1 has the old schema (15 scenarios, 4 axes). v2 has the new schema
(42 scenarios, 5 axes + priority + language). The shared metrics are:
outcome_match, compliance_pass, tone_pass, escalation/transfer, full_pass.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

THIS_DIR = Path(__file__).resolve().parent
V2_ROOT = THIS_DIR.parent
V1_ROOT = V2_ROOT.parent / "collections-voicebot"


def _rate(df: pd.DataFrame, col: str) -> float:
    if col not in df.columns:
        return 0.0
    s = df[col].astype(str).str.lower().isin({"true", "1"})
    return float(s.mean()) if len(df) else 0.0


def _full_pass(df: pd.DataFrame) -> float:
    """Compute full-pass from whichever schema is present."""
    if "full_pass" in df.columns:
        s = df["full_pass"].astype(str).str.lower().isin({"true", "1"})
        return float(s.mean()) if len(df) else 0.0
    # legacy: AND outcome + compliance + tone + escalation/transfer
    candidates = ["outcome_match", "compliance_pass", "tone_pass"]
    transfer_col = "transfer_correct" if "transfer_correct" in df.columns else "escalation_correct"
    if transfer_col in df.columns:
        candidates.append(transfer_col)
    ok = df.apply(
        lambda r: all(str(r[c]).strip().lower() in {"true", "1"} for c in candidates),
        axis=1,
    )
    return float(ok.mean()) if len(df) else 0.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare v1 and v2 eval results")
    parser.add_argument("--v1", default=str(V1_ROOT / "eval" / "results_v1.csv"))
    parser.add_argument("--v2", default=str(THIS_DIR / "results_v2.csv"))
    parser.add_argument("--out", default=str(THIS_DIR / "comparison_v1_v2.csv"))
    args = parser.parse_args(argv)

    v1 = pd.read_csv(args.v1)
    v2 = pd.read_csv(args.v2)

    # Side-by-side per scenario (where both exist)
    merged = v2.merge(v1, on="scenario_id", how="left", suffixes=("_v2", "_v1"))
    cols = [
        c
        for c in [
            "scenario_id", "bucket_v2", "priority", "language_mode", "difficulty_v2",
            "expected_outcome_v2",
            "actual_outcome_v1", "actual_outcome_v2",
            "outcome_match_v1", "outcome_match_v2",
            "compliance_pass_v1", "compliance_pass_v2",
            "compliance_failures_v1", "compliance_failures_v2",
            "slot_capture_rate", "slots_missed",
            "tone_pass_v1", "tone_pass_v2",
            "escalation_correct_v1", "transfer_correct",
            "full_pass",
        ]
        if c in merged.columns
    ]
    merged[cols].to_csv(args.out, index=False)

    # Aggregate side-by-side
    print(f"\n{'Metric':30s} {'v1':>12s} {'v2':>12s} {'Delta':>12s}")
    print("-" * 70)

    for metric in ["outcome_match", "compliance_pass", "tone_pass"]:
        v1r, v2r = _rate(v1, metric), _rate(v2, metric)
        print(f"{metric:30s} {v1r:>12.2%} {v2r:>12.2%} {v2r-v1r:>+12.2%}")

    # escalation in v1 vs transfer in v2
    v1_xfer = _rate(v1, "escalation_correct")
    v2_xfer = _rate(v2, "transfer_correct")
    print(f"{'transfer/escalation_correct':30s} {v1_xfer:>12.2%} {v2_xfer:>12.2%} {v2_xfer-v1_xfer:>+12.2%}")

    # Full pass
    v1_full = _full_pass(v1)
    v2_full = _full_pass(v2)
    print(f"{'full_pass':30s} {v1_full:>12.2%} {v2_full:>12.2%} {v2_full-v1_full:>+12.2%}")

    # v2-only metrics
    if "priority" in v2.columns:
        p0 = v2[v2["priority"] == "P0"]
        print("\nv2-only metrics:")
        print(f"  P0 scenarios:                  {len(p0)}")
        print(f"  P0 compliance pass rate:       {_rate(p0, 'compliance_pass'):.2%}  ← must be 100%")
        print(f"  P0 full pass rate:             {_full_pass(p0):.2%}")

    if "slot_capture_rate" in v2.columns:
        v2["_slot"] = pd.to_numeric(v2["slot_capture_rate"], errors="coerce").fillna(0.0)
        print(f"  Mean slot_capture_rate:        {v2['_slot'].mean():.2%}")

    # Per-bucket breakdown
    print("\nv2 per-bucket (compliance / full):")
    for bucket, g in v2.groupby("bucket"):
        n = len(g)
        c = _rate(g, "compliance_pass")
        f = _full_pass(g)
        print(f"  {bucket:14s} (n={n:2d})  {c:.0%} / {f:.0%}")

    if "language_mode" in v2.columns:
        print("\nv2 per-language (compliance / full):")
        for lang, g in v2.groupby("language_mode"):
            n = len(g)
            c = _rate(g, "compliance_pass")
            f = _full_pass(g)
            print(f"  {lang:14s} (n={n:2d})  {c:.0%} / {f:.0%}")

    print(f"\nDetailed comparison: {args.out}")
    print(f"Note: v2 has {len(v2)} scenarios, v1 has {len(v1)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
