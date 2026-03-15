"""OpenAI LLM client with structured JSON parsing and retry/repair logic."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from app.config import settings
from app.scanner.prompts import REPAIR_SYSTEM_SUFFIX

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Lazy-initialise the OpenAI client."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


class LLMParseError(Exception):
    """Raised when the model output cannot be parsed into valid JSON."""


def _extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from model output."""
    cleaned = text.strip()

    if cleaned.startswith("```"):
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1 :]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].rstrip()

    return json.loads(cleaned)


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    max_retries: int | None = None,
    *,
    role_name: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Call the OpenAI chat API and return parsed JSON."""
    if max_retries is None:
        max_retries = settings.max_file_retries

    client = _get_client()
    selected_model = model or settings.openai_model
    selected_temperature = 0.2 if temperature is None else temperature
    last_error: str = ""
    last_raw: str = ""
    role_label = role_name or "unknown_role"

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(1 + max_retries):
        try:
            if attempt > 0:
                repair_msg = REPAIR_SYSTEM_SUFFIX.format(parse_error=last_error)
                call_messages = messages + [
                    {"role": "assistant", "content": last_raw},
                    {"role": "user", "content": repair_msg},
                ]
            else:
                call_messages = messages

            logger.debug(
                "LLM request role=%s attempt=%d/%d model=%s temperature=%s user_chars=%d",
                role_label,
                attempt + 1,
                1 + max_retries,
                selected_model,
                selected_temperature,
                len(user_prompt),
            )

            response = client.chat.completions.create(
                model=selected_model,
                messages=call_messages,  # type: ignore[arg-type]
                temperature=selected_temperature,
                max_tokens=settings.llm_max_tokens,
            )

            raw = response.choices[0].message.content or ""
            last_raw = raw
            parsed = _extract_json(raw)
            logger.debug(
                "LLM response role=%s attempt=%d parsed_keys=%s raw_chars=%d",
                role_label,
                attempt + 1,
                sorted(parsed.keys()),
                len(raw),
            )
            return parsed

        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "LLM parse attempt %d/%d failed for role=%s model=%s: %s",
                attempt + 1,
                1 + max_retries,
                role_label,
                selected_model,
                last_error,
            )

    raise LLMParseError(
        f"Failed to parse LLM output after {1 + max_retries} attempts for role={role_label}. Last error: {last_error}"
    )
