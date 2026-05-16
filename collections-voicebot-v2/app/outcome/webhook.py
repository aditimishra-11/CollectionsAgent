"""POST terminal outcome to a webhook. webhook.site for the demo; swap URL for prod."""

from __future__ import annotations

import time

import httpx
from loguru import logger

from app.config import OUTCOME_WEBHOOK_URL
from app.outcome.schema import Outcome


def post_outcome(outcome: Outcome, retries: int = 3, base_backoff: float = 0.5) -> bool:
    if not OUTCOME_WEBHOOK_URL:
        logger.warning("OUTCOME_WEBHOOK_URL not set — outcome not posted.")
        return False

    payload = outcome.model_dump(mode="json")
    headers = {"Idempotency-Key": outcome.call_id}

    for attempt in range(1, retries + 1):
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.post(OUTCOME_WEBHOOK_URL, json=payload, headers=headers)
                r.raise_for_status()
                logger.info(f"Outcome posted (call_id={outcome.call_id}, status={r.status_code})")
                return True
        except Exception as e:
            logger.warning(f"Webhook attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(base_backoff * (2 ** (attempt - 1)))

    logger.error(f"Webhook failed after {retries} attempts. Outcome stored locally only.")
    return False
