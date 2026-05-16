"""Extract a structured terminal outcome from a finished conversation.

v1 approach: ask GPT-4.1 mini (in JSON mode) to classify the full transcript
into one of the 7 outcome types, with required fields filled where stated.
This is intentionally a single LLM call at end-of-call, not a per-turn
classifier — v1 is a baseline.
"""

from __future__ import annotations

from app.llm.openai_client import LLMTurn, OpenAIClient
from app.outcome.schema import Outcome, OutcomeDetail

_SYSTEM = """You classify a finished collections call transcript into a structured outcome.

Return STRICT JSON only. Schema:
{
  "outcome": "one of: promise_to_pay | already_paid | callback_request | human_callback_required | refused | wrong_number | no_answer",
  "outcome_detail": {
    "amount": number or null,
    "date": "YYYY-MM-DD or null",
    "mode": "upi | netbanking | card | cash | autodebit | null",
    "date_paid": "YYYY-MM-DD or null",
    "preferred_time": "free text or null",
    "reason": "medical_emergency | job_loss | abuse | waiver | dispute | null",
    "urgency": "high | medium | low or null",
    "reason_stated": "free text or null"
  },
  "transcript_summary": "one sentence"
}

Rules:
- promise_to_pay requires amount and date both present. If either is vague, prefer callback_request.
- already_paid requires a payment mode mentioned by the customer.
- If customer mentions medical emergency, job loss, or abusive language → human_callback_required with that reason.
- If customer asks for late-fee waiver or disputes an amount → human_callback_required with reason 'waiver' or 'dispute'.
- If customer explicitly asks a human to call back at a later time → callback_request.
- If person who answered says wrong number / not me → wrong_number.
- Default unclear case → refused.
"""


class OutcomeExtractor:
    def __init__(self, client: OpenAIClient | None = None) -> None:
        self._llm = client or OpenAIClient()

    def extract(self, call_id: str, customer_id: str | None, transcript: list[LLMTurn]) -> Outcome:
        formatted = "\n".join(f"{t.role.upper()}: {t.content}" for t in transcript)
        data = self._llm.reply_json(
            system_prompt=_SYSTEM,
            user_content=f"Transcript:\n{formatted}\n\nReturn JSON.",
            max_tokens=400,
            temperature=0.0,
        )
        detail = OutcomeDetail(**(data.get("outcome_detail") or {}))
        return Outcome(
            call_id=call_id,
            customer_id=customer_id,
            outcome=data.get("outcome", "refused"),
            outcome_detail=detail,
            turns=sum(1 for t in transcript if t.role == "user"),
            transcript_summary=data.get("transcript_summary"),
        )
