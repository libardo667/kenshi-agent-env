from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from kenshi_agent.hosted_continuation import (
    assistant_continuation,
    structured_json_was_truncated,
)


def test_assistant_continuation_preserves_exact_provider_state() -> None:
    opaque_details = [{"type": "reasoning.encrypted", "data": "opaque"}]
    fallback_reasoning = {"opaque": "legacy"}

    for message in (
        {
            "content": '{"summary":"prefix',
            "reasoning_details": opaque_details,
            "reasoning": fallback_reasoning,
        },
        SimpleNamespace(
            content='{"summary":"prefix',
            reasoning_details=opaque_details,
            reasoning=fallback_reasoning,
        ),
    ):
        assert assistant_continuation(message) == {
            "role": "assistant",
            "content": '{"summary":"prefix',
            "reasoning_details": opaque_details,
        }

    for message in (
        {
            "content": '{"summary":"prefix',
            "reasoning_details": [],
            "reasoning": fallback_reasoning,
        },
        SimpleNamespace(
            content='{"summary":"prefix',
            reasoning_details=None,
            reasoning=fallback_reasoning,
        ),
    ):
        assert assistant_continuation(message) == {
            "role": "assistant",
            "content": '{"summary":"prefix',
            "reasoning": fallback_reasoning,
        }

    for message in ({"content": None}, SimpleNamespace(content=None)):
        with pytest.raises(ValueError, match="exact text prefix"):
            assistant_continuation(message)


def _validation_error(*errors: dict[str, object]) -> ValidationError:
    return ValidationError.from_exception_data("hosted response", list(errors))  # type: ignore[arg-type]


def test_truncation_detection_scans_json_errors_and_fails_closed() -> None:
    unrelated = {
        "type": "missing",
        "loc": ("summary",),
        "input": {},
    }
    unterminated = {
        "type": "json_invalid",
        "loc": (),
        "input": '{"summary":"',
        "ctx": {"error": "Unterminated string"},
    }
    invalid_number = {
        "type": "json_invalid",
        "loc": (),
        "input": '{"rank":-}',
        "ctx": {"error": "invalid number"},
    }

    assert structured_json_was_truncated(_validation_error(unrelated, unterminated))
    assert not structured_json_was_truncated(_validation_error(unrelated))
    assert not structured_json_was_truncated(_validation_error(invalid_number))
