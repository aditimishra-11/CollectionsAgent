"""Thin wrapper around the OpenAI Chat Completions API.

v1 keeps this minimal: one synchronous call per turn. Streaming and tool-use
are intentionally not used here — v1 must remain a clean baseline.

Two flavours of call:
- `reply()` — free-form text for the bot's conversational turn.
- `reply_json()` — JSON-mode for structured outputs (outcome extractor, judge).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from openai import OpenAI

from app.config import LLM_MODEL, OPENAI_API_KEY


@dataclass
class LLMTurn:
    role: str  # "user" or "assistant"
    content: str


class OpenAIClient:
    def __init__(self, model: str = LLM_MODEL) -> None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set")
        self._client = OpenAI(api_key=OPENAI_API_KEY)
        self.model = model

    def reply(
        self,
        system_prompt: str,
        history: Iterable[LLMTurn],
        max_tokens: int = 300,
        temperature: float = 0.4,
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": t.role, "content": t.content} for t in history)
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()

    def reply_json(
        self,
        system_prompt: str,
        user_content: str,
        max_tokens: int = 400,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Force a JSON object response. Use this for extractors and judges."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        response = self._client.chat.completions.create(
            model=model or self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start : end + 1])
            return {}
