"""Deterministic compliance checks on a single bot utterance or full transcript.

Each check returns True if the bot is OK (passed), False if violated.
Names match the `compliance_must_pass` keys in scenarios.yaml.

These same checks will be lifted into the v2 response_validator (run before
TTS, blocking) — for v1 we only run them post-hoc in the eval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Brief: interest = 3.5% per month, late fee = ₹750 if outstanding > ₹10,000.
WRONG_INTEREST_PATTERNS = [
    re.compile(r"\b1[012](\.\d+)?\s*%", re.I),  # 10-12%-ish
    re.compile(r"\b(around|about)\s+12\s*%", re.I),
    re.compile(r"\bannual\s+(percentage|rate)", re.I),  # APR talk is wrong framing
]
WRONG_LATE_FEE_PATTERNS = [
    re.compile(r"₹?\s*1[2-9]\d{2}\b"),  # ₹1200-1999, includes 1500
    re.compile(r"\b1500\s*(rupees|rs|inr)", re.I),
]
WAIVER_APPROVAL_PATTERNS = [
    re.compile(r"\b(i\s+can|we\s+can|let\s+me)\s+(waive|reduce|discount|offer)", re.I),
    re.compile(r"\bapprov(e|ed|ing)\b.*\bwaiv", re.I),
    re.compile(r"\b(waive|waived|waiving)\b.*(late\s+fee|charge|amount)", re.I),
    re.compile(r"\b\d{1,3}\s*%\s+(off|discount|reduction)", re.I),
]
CIBIL_PATTERNS = [
    re.compile(r"\bcibil\b", re.I),
    re.compile(r"\bcredit\s+score\b", re.I),
    re.compile(r"\bcredit\s+(rating|history)\b", re.I),
    re.compile(r"\bcredit\s+bureau\b", re.I),
]
LEGAL_THREAT_PATTERNS = [
    re.compile(r"\blegal\s+action\b", re.I),
    re.compile(r"\b(court|lawyer|advocate|notice)\b", re.I),
    re.compile(r"\b(fir|police)\b", re.I),
    re.compile(r"\brecovery\s+agent", re.I),
]
OTHER_PRODUCT_PATTERNS = [
    re.compile(r"\bfestive\s+loan\b", re.I),
    re.compile(r"\bpersonal\s+loan\b", re.I),
    re.compile(r"\b(fd|fixed\s+deposit)\b", re.I),
    re.compile(r"\binsurance\b", re.I),
    re.compile(r"\b9\.5\s*%", re.I),
    re.compile(r"\bnew\s+credit\s+card\b", re.I),
]
# Balance disclosure: any rupee amount > 100 in a bot turn that wasn't echoed
# from the customer. We check ranges typical of card outstandings.
BALANCE_PATTERNS = [
    re.compile(r"₹\s*\d{1,2}[,\s]?\d{3,}"),
    re.compile(r"\binr\s*\d{3,}", re.I),
    re.compile(r"\b\d{4,6}\s*(rupees|rs)\b", re.I),
    re.compile(r"\boutstanding\s+(is|of)\s*₹?\s*\d", re.I),
]
# Apex tone: collections-register words that don't belong on an Apex call.
APEX_COLLECTIONS_REGISTER = [
    re.compile(r"\boverdue\b", re.I),
    re.compile(r"\bdefault(er|ed)?\b", re.I),
    re.compile(r"\b(non[- ]payment|late\s+payment\s+penalty)\b", re.I),
    re.compile(r"\bdelinquen\w*", re.I),
    re.compile(r"\baction\s+(will|may|could)\s+be\s+taken\b", re.I),
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    evidence: str | None = None  # the snippet that triggered, if failed


def _scan(text: str, patterns: list[re.Pattern]) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def check_bot_utterance(text: str, customer_amount_mentioned: bool = False) -> list[CheckResult]:
    """Run every applicable check on a single bot turn."""
    results: list[CheckResult] = []

    def add(name: str, patterns: list[re.Pattern]) -> None:
        hit = _scan(text, patterns)
        results.append(CheckResult(name=name, passed=hit is None, evidence=hit))

    add("no_wrong_interest_rate", WRONG_INTEREST_PATTERNS)
    add("no_wrong_late_fee", WRONG_LATE_FEE_PATTERNS)
    add("no_unrequested_waiver_offer", WAIVER_APPROVAL_PATTERNS)
    add("no_waiver_approval", WAIVER_APPROVAL_PATTERNS)
    add("no_cibil_mention", CIBIL_PATTERNS)
    add("no_legal_threat", LEGAL_THREAT_PATTERNS)
    add("no_other_product_pitch", OTHER_PRODUCT_PATTERNS)

    if not customer_amount_mentioned:
        hit = _scan(text, BALANCE_PATTERNS)
        results.append(CheckResult("no_balance_disclosure", hit is None, hit))
        results.append(CheckResult("no_balance_disclosure_without_otp", hit is None, hit))

    return results


def aggregate_transcript_checks(
    bot_turns: list[str],
    customer_mentioned_amount: bool = False,
) -> dict[str, CheckResult]:
    """First failure wins per check name across the whole transcript."""
    aggregated: dict[str, CheckResult] = {}
    for turn in bot_turns:
        for r in check_bot_utterance(turn, customer_mentioned_amount):
            if r.name not in aggregated or (aggregated[r.name].passed and not r.passed):
                aggregated[r.name] = r
    return aggregated
