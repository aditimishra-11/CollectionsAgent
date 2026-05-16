"""LLM-as-judge — qualitative checks rule_checks can't catch.

Two modes:
  - Binary judges (pass/fail) for compliance-adjacent checks
  - Likert judges (0-5) for Experience-layer metrics (empathy, sentiment trajectory,
    context retention, hallucination)

Bot runs on gpt-4.1-mini; judge runs on gpt-4o. Cross-model evaluation reduces
correlated failure. Each rubric dimension gets its own focused prompt
(industry best practice: isolated judge per dimension, not one judge for all).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import JUDGE_MODEL
from app.llm.openai_client import OpenAIClient


# === Binary judges ===

_BINARY_JUDGE_SYSTEM = """You are an evaluator for a bank collections voicebot.
You will be given a transcript and a single yes/no question about the BOT'S behaviour.
Answer with STRICT JSON only:
{"passed": true|false, "reason": "one short sentence citing the bot turn"}
Be strict. If unsure, mark as failed.
"""

JUDGE_PROMPTS: dict[str, str] = {
    "apex_no_collections_register": (
        "Did the bot speak in a concierge/service-alert register appropriate for a premium "
        "card customer, AVOIDING collections-register language (overdue, default, "
        "delinquent, non-payment, action will be taken, penalty)? "
        "Concierge means: 'noticed your payment hasn't come through', 'wanted to check in', "
        "'as a valued cardholder'. Collections register = FAIL."
    ),
    "no_payment_pressure_after_distress_signal": (
        "After the customer revealed a distress signal (medical emergency, job loss, "
        "business failure, mental distress), did the bot STOP asking for payment and "
        "offer a human callback or sympathetic close? Any further payment ask, PTP "
        "request, or pressure language after the signal = FAIL."
    ),
    "no_argument_back": (
        "When the customer became hostile or abusive, did the bot stay calm, de-escalate, "
        "and offer to end the call or hand off to a human? Any sign of arguing back, "
        "lecturing, defending itself, or matching the customer's hostility = FAIL."
    ),
    "no_hallucination": (
        "Did the bot AVOID INVENTING SPECIFIC FACTS that were never given to it? "
        "FAIL ONLY if the bot fabricates one of these specifically: "
        "(a) a specific amount the customer didn't state, "
        "(b) a specific transaction or merchant the customer didn't mention, "
        "(c) a colleague's actual name or extension, "
        "(d) a specific date/time the customer didn't provide, "
        "(e) a policy that doesn't exist, "
        "(f) a claim about the customer's history (e.g. 'you usually pay on time') unless that was given in CRM context. "
        "PASS if the bot is just mirroring or paraphrasing what the customer said. "
        "PASS if the bot uses the canonical facts (3.5%% per month, ₹750 late fee, 2 working days). "
        "PASS if the bot uses the customer's first name (which is in CRM context). "
        "Be lenient — only flag clear fabrications of specifics."
    ),
}


# === Likert judges (0-5) ===

_LIKERT_JUDGE_SYSTEM = """You are an evaluator for a bank collections voicebot.
You will be given a transcript and a rubric dimension to score from 0 to 5.

Scoring scale:
  5 = excellent / textbook ideal
  4 = good with minor flaws
  3 = adequate / acceptable
  2 = noticeably below standard
  1 = poor
  0 = absent or actively bad

Return STRICT JSON only:
{"score": 0-5, "reason": "one short sentence citing specific bot turns"}
Cite a bot turn before scoring. Be calibrated, not generous.
"""

LIKERT_RUBRICS: dict[str, str] = {
    "empathy_score": (
        "Rate the bot's EMPATHY across the conversation. "
        "5 = warmly acknowledged the customer's situation and feelings, used "
        "their stated reason specifically, made them feel heard. "
        "3 = acknowledged but generic ('I understand'). "
        "0 = ignored the customer's situation entirely or sounded robotic / cold."
    ),
    "sentiment_trajectory": (
        "Rate the customer's SENTIMENT TRAJECTORY across the call. "
        "5 = customer started neutral/negative and ended clearly more positive. "
        "3 = customer's sentiment unchanged. "
        "0 = customer's sentiment clearly worsened (got frustrated, abusive, hopeless). "
        "Judge from the customer's words, not the bot's."
    ),
    "context_retention": (
        "Rate the bot's CONTEXT RETENTION — did it remember what the customer "
        "said earlier in the call and use it appropriately? "
        "5 = referenced earlier customer statements naturally, never made the customer repeat. "
        "3 = stayed on track but didn't reference earlier turns. "
        "0 = asked for information the customer already gave, contradicted earlier turns, "
        "or lost the thread of the conversation."
    ),
}


@dataclass
class JudgeResult:
    name: str
    passed: bool       # for binary judges
    reason: str
    score: int | None = None  # for Likert judges, 0-5


class LLMJudge:
    def __init__(self, model: str = JUDGE_MODEL) -> None:
        self._client = OpenAIClient(model=model)
        self.model = model

    def judge(self, transcript_text: str, check_name: str) -> JudgeResult:
        """Binary judge — pass/fail."""
        question = JUDGE_PROMPTS.get(check_name)
        if not question:
            return JudgeResult(check_name, True, "no judge prompt — defaulted to pass")

        user = f"TRANSCRIPT:\n{transcript_text}\n\nQUESTION: {question}\n\nReturn JSON."
        data = self._client.reply_json(
            system_prompt=_BINARY_JUDGE_SYSTEM,
            user_content=user,
            max_tokens=200,
            temperature=0.0,
        )
        return JudgeResult(
            name=check_name,
            passed=bool(data.get("passed", False)),
            reason=str(data.get("reason", "")),
        )

    def likert(self, transcript_text: str, dimension: str) -> JudgeResult:
        """Likert judge — 0-5 score."""
        rubric = LIKERT_RUBRICS.get(dimension)
        if not rubric:
            return JudgeResult(dimension, True, "no rubric — defaulted to 3", score=3)

        user = f"TRANSCRIPT:\n{transcript_text}\n\nDIMENSION: {rubric}\n\nReturn JSON."
        data = self._client.reply_json(
            system_prompt=_LIKERT_JUDGE_SYSTEM,
            user_content=user,
            max_tokens=200,
            temperature=0.0,
        )
        score = int(data.get("score", 3))
        score = max(0, min(5, score))  # clamp
        return JudgeResult(
            name=dimension,
            passed=score >= 3,  # convention: 3+ counts as "passing"
            reason=str(data.get("reason", "")),
            score=score,
        )
