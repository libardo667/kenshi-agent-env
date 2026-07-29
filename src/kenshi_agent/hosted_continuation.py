"""Shared preservation rules for length-limited hosted structured output."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

TRUNCATED_FINISH_REASONS = frozenset(
    {
        "length",
        "max_tokens",
        "max_output_tokens",
    }
)

CONTINUE_STRUCTURED_JSON_SUFFIX = (
    "Continue the same structured response from the exact next character. "
    "Return only the remaining JSON suffix. Do not repeat any earlier character, "
    "open a code fence, restart the reasoning, or revise the response."
)


def message_field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def assistant_continuation(message: object) -> dict[str, Any]:
    """Preserve the provider's exact prefix and opaque reasoning blocks."""

    content = message_field(message, "content")
    if not isinstance(content, str):
        raise ValueError(  # mutation: diagnostic-only
            "Hosted continuation requires an exact text prefix."
        )
    assistant: dict[str, Any] = {
        "role": "assistant",
        "content": content,
    }
    reasoning_details = message_field(message, "reasoning_details")
    if reasoning_details:
        assistant["reasoning_details"] = reasoning_details
    else:
        reasoning = message_field(message, "reasoning")
        if reasoning:
            assistant["reasoning"] = reasoning
    return assistant


def structured_json_was_truncated(exc: ValidationError) -> bool:
    """Recognize syntax-level EOF, never a merely invalid structured answer."""

    for error in exc.errors():
        if error.get("type") != "json_invalid":
            continue
        context = error.get("ctx")
        if not isinstance(context, dict):
            # Pydantic cannot construct json_invalid without a dict context.
            continue  # pragma: no mutate
        raw_detail = context.get("error")
        if not isinstance(raw_detail, str):
            # Pydantic requires this context member to be a string.
            continue  # pragma: no mutate
        detail = raw_detail.lower()
        if "eof while parsing" in detail or "unterminated" in detail:
            return True
    return False
