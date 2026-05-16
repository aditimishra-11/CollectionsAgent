"""LLM-as-judge for the qualitative checks rule_checks can't cover.

Used for:
  - apex_no_collections_register  (semantic — not just keywords)
  - no_payment_pressure_after_distress_signal  (was bot still pushing PTP?)
  - no_argument_back  (did the bot defend itself instead of de-escalating?)

Bot runs on gpt-4.1-mini; judge runs on gpt-4o — a stronger model than the
one under test. Standard LLM-as-judge practice. Returns passed/failed + reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import JUDGE_MODEL
from app.llm.openai_client import OpenAIClient

_JUDGE_SYSTEM = """You are an evaluator for a bank collections voicebot.
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
}


@dataclass
class JudgeResult:
    name: str
    passed: bool
    reason: str


class LLMJudge:
    def __init__(self, model: str = JUDGE_MODEL) -> None:
        # Judge runs on a different (stronger) model than the bot.
        self._client = OpenAIClient(model=model)
        self.model = model

    def judge(self, transcript_text: str, check_name: str) -> JudgeResult:
        question = JUDGE_PROMPTS.get(check_name)
        if not question:
            return JudgeResult(check_name, True, "no judge prompt — defaulted to pass")

        user = f"TRANSCRIPT:\n{transcript_text}\n\nQUESTION: {question}\n\nReturn JSON."
        data = self._client.reply_json(
            system_prompt=_JUDGE_SYSTEM,
            user_content=user,
            max_tokens=200,
            temperature=0.0,
        )
        return JudgeResult(
            name=check_name,
            passed=bool(data.get("passed", False)),
            reason=str(data.get("reason", "")),
        )
