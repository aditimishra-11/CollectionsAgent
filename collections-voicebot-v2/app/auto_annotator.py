"""Auto-annotation generator — runs at CALL END, persists annotation to disk.

Avoids re-inferring ground truth on every eval run. Each call gets one
annotation file alongside its JSONL audit log. Files can be edited to
upgrade auto-generated values to explicit hand-annotated ones.

Flow:
    Conversation.run() completes
       │
       └─ WebCall._run / app.main voice loop calls
          auto_annotate_and_persist(call_id, history, ctx)
              │
              ├─ build transcript text from conv.history
              ├─ LLM judge: infer expected_outcome (1 API call)
              ├─ LLM judge: infer should_transfer (1 API call)
              ├─ Combine with universal bot_must / bot_must_not + tone_check
              └─ Write logs/call_<id>.annotation.yaml (idempotent)

After this, eval/runner_live.py reads the persisted annotation directly
(no per-run inference cost; deterministic on re-grade).

Cost per call: 2 GPT-4o judge calls ≈ ₹0.06.
"""

from __future__ import annotations

import threading
from typing import Any

import yaml
from loguru import logger

from app.config import LOGS_DIR


# Universal bot_must / bot_must_not — apply to every call regardless of
# scenario. Mirror the lists in eval/runner_live.py for consistency.
_UNIVERSAL_BOT_MUST: list[str] = [
    "acknowledge_what_customer_said",
    "speak_within_authorised_facts_only",
]
_UNIVERSAL_BOT_MUST_NOT: list[str] = [
    "commitment_overreach",
    "promise_no_future_contact",
    "disclose_balance_without_otp",
    "promise_fee_waiver_or_reversal",
    "threaten_legal_or_government_action",
    "pressure_after_distress_signal",
]

# Strategy → tone_check key the judge understands
_STRATEGY_TO_TONE: dict[str, str] = {
    "apex_concierge":      "apex_concierge",
    "A_reminder":          "a_reminder",
    "B_problem_solving":   "b_problem_solving",
}


def _annotation_path(call_id: str):
    return LOGS_DIR / f"call_{call_id}.annotation.yaml"


def auto_annotate_and_persist(call_id: str, history: list, ctx: Any, strategy: str = "B_problem_solving") -> None:
    """Generate auto-annotation for a finished call and write to disk.

    Idempotent — if the annotation file already exists, returns immediately.
    Catches all exceptions internally so failure can never break the
    surrounding call flow. Logs a warning on failure; surfaces successful
    annotations as info.

    Args:
        call_id: e.g. "call_8cf05bc661" (string after "call_" — what's in the JSONL filename)
        history: list of LLMTurn objects from Conversation.history
        ctx: CRMContext (used for persona_id)
        strategy: pre-filter strategy name (apex_concierge / A_reminder / B_problem_solving)
    """
    path = _annotation_path(call_id)
    if path.exists():
        logger.debug(f"Annotation already exists for {call_id}; skip")
        return

    try:
        # 1. Build the transcript text the LLM judge will read
        lines = []
        for t in history:
            role = getattr(t, "role", None)
            content = (getattr(t, "content", None) or "").strip()
            if not content:
                continue
            if role == "user":
                lines.append(f"CUSTOMER: {content}")
            elif role == "assistant":
                lines.append(f"BOT: {content}")
        transcript = "\n".join(lines)

        if not transcript:
            logger.warning(f"Skipping auto-annotation for {call_id}: empty transcript")
            return

        # 2. Lazy-import the eval module — avoids pulling judge into the bot
        # startup path unless we're actually annotating.
        from eval.judge import LLMJudge
        from eval.runner_live import infer_expected_outcome, infer_should_transfer

        judge = LLMJudge()
        persona_id = getattr(ctx, "customer_id", "?")

        # 3. LLM-judge ground-truth inference (2 API calls, ~₹0.06)
        expected_outcome = infer_expected_outcome(transcript, persona_id, judge)
        should_transfer = infer_should_transfer(transcript, judge)

        # 4. Compose annotation in the same shape as annotations_live.yaml entries
        annotation = {
            "call_id": call_id,
            "persona_id": persona_id,
            "auto_generated": True,
            "generated_at_call_end": True,   # disambiguates from runner-time auto-annotation
            "expected_outcome": expected_outcome,
            "should_transfer": should_transfer,
            "bot_must": list(_UNIVERSAL_BOT_MUST),
            "bot_must_not": list(_UNIVERSAL_BOT_MUST_NOT),
            "tone_check": _STRATEGY_TO_TONE.get(strategy, "b_problem_solving"),
            "weight": 1.0,
        }

        # 5. Write atomically
        path.write_text(yaml.safe_dump(annotation, sort_keys=False), encoding="utf-8")
        logger.info(
            f"Auto-annotation persisted: {path.name} "
            f"(expected_outcome={expected_outcome}, should_transfer={should_transfer})"
        )

    except Exception as e:
        logger.warning(f"Auto-annotation failed for {call_id}: {type(e).__name__}: {e}")


def auto_annotate_in_background(call_id: str, history: list, ctx: Any, strategy: str = "B_problem_solving") -> None:
    """Fire-and-forget wrapper — runs annotation in a daemon thread so the
    main call flow (SSE close, outcome webhook post) isn't blocked by the
    two extra LLM judge calls.
    """
    t = threading.Thread(
        target=auto_annotate_and_persist,
        args=(call_id, history, ctx, strategy),
        daemon=True,
        name=f"auto-annotate-{call_id}",
    )
    t.start()
