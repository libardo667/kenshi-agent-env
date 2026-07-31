from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote
from urllib.request import Request, urlopen

CapacitySource = Literal[
    "openrouter_models_api",
    "configured_override",
    "provider_metadata_unavailable",
]
JsonFetcher = Callable[[str, dict[str, str], float], Any]


@dataclass(frozen=True, slots=True)
class HostedModelCapacity:
    """Provider-owned context facts for the exact requested model."""

    requested_model: str
    context_window_tokens: int | None
    max_completion_tokens: int | None
    source: CapacitySource
    lookup_error: str | None = None


@dataclass(frozen=True, slots=True)
class HostedContextEnvelope:
    """Token reservations applied before rendering one observation."""

    capacity: HostedModelCapacity
    compaction_target_tokens: int | None
    hard_observation_tokens: int | None
    reserved_output_tokens: int
    reserved_static_tokens: int
    reserved_image_tokens: int
    proactive_headroom_tokens: int
    estimator: str = "utf8_bytes_upper_bound"


def conservative_text_token_estimate(text: str) -> int:
    """Pessimistic tokenizer-independent estimate: one token per UTF-8 byte."""

    return len(text.encode("utf-8"))


def _fetch_json(url: str, headers: dict[str, str], timeout: float) -> Any:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.load(response)


def _positive_integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def resolve_openrouter_model_capacity(
    *,
    base_url: str,
    model: str,
    api_key: str,
    timeout_seconds: float,
    configured_context_window_tokens: int | None = None,
    fetch_json: JsonFetcher | None = None,
) -> HostedModelCapacity:
    """Resolve model capacity once from OpenRouter's model metadata endpoint.

    A configured override is explicit authority and avoids a metadata request.
    Metadata failure is non-fatal: inference may still work, so the planner
    sends the full current observation and lets the provider own rejection.
    """

    if configured_context_window_tokens is not None:
        return HostedModelCapacity(
            requested_model=model,
            context_window_tokens=configured_context_window_tokens,
            max_completion_tokens=None,
            source="configured_override",
        )

    fetch = _fetch_json if fetch_json is None else fetch_json
    url = f"{base_url.rstrip('/')}/model/{quote(model)}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        document = fetch(url, headers, timeout_seconds)
        data = document["data"]
        if not isinstance(data, dict):
            raise TypeError("model metadata data must be an object")
        context_window_tokens = _positive_integer(data.get("context_length"))
        provider = data.get("top_provider")
        max_completion_tokens = (
            _positive_integer(provider.get("max_completion_tokens"))
            if isinstance(provider, dict)
            else None
        )
        if context_window_tokens is None:
            raise ValueError("model metadata has no positive context_length")
    except (OSError, TimeoutError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        return HostedModelCapacity(
            requested_model=model,
            context_window_tokens=None,
            max_completion_tokens=None,
            source="provider_metadata_unavailable",
            lookup_error=f"{type(exc).__name__}: {exc}"[:300],
        )

    return HostedModelCapacity(
        requested_model=model,
        context_window_tokens=context_window_tokens,
        max_completion_tokens=max_completion_tokens,
        source="openrouter_models_api",
    )


def hosted_context_envelope(
    capacity: HostedModelCapacity,
    *,
    output_tokens: int,
    system_text: str,
    schema_text: str,
    request_text: str,
    screenshot_included: bool,
) -> HostedContextEnvelope:
    """Reserve the non-observation request before proactive compaction.

    The output allowance is reserved once for the response and once again as
    proactive headroom. A screenshot reserves the same bounded allowance
    because provider image tokenization is not available before the request.
    Text uses the deliberately pessimistic UTF-8-byte estimator.
    """

    static_tokens = conservative_text_token_estimate(
        system_text + schema_text + request_text
    )
    image_tokens = output_tokens if screenshot_included else 0
    context_tokens = capacity.context_window_tokens
    if context_tokens is None:
        return HostedContextEnvelope(
            capacity=capacity,
            compaction_target_tokens=None,
            hard_observation_tokens=None,
            reserved_output_tokens=output_tokens,
            reserved_static_tokens=static_tokens,
            reserved_image_tokens=image_tokens,
            proactive_headroom_tokens=output_tokens,
        )
    if (
        capacity.max_completion_tokens is not None
        and output_tokens > capacity.max_completion_tokens
    ):
        raise ValueError(
            f"requested output allowance {output_tokens} exceeds "
            f"{capacity.requested_model} maximum completion allowance "
            f"{capacity.max_completion_tokens}"
        )

    hard_observation_tokens = (
        context_tokens - output_tokens - static_tokens - image_tokens
    )
    if hard_observation_tokens <= 0:
        raise ValueError(
            f"static request reservations consume the {context_tokens}-token "
            f"context window for {capacity.requested_model}"
        )
    proactive_headroom = min(output_tokens, hard_observation_tokens)
    compaction_target_tokens = max(
        1,
        hard_observation_tokens - proactive_headroom,
    )
    return HostedContextEnvelope(
        capacity=capacity,
        compaction_target_tokens=compaction_target_tokens,
        hard_observation_tokens=hard_observation_tokens,
        reserved_output_tokens=output_tokens,
        reserved_static_tokens=static_tokens,
        reserved_image_tokens=image_tokens,
        proactive_headroom_tokens=proactive_headroom,
    )
