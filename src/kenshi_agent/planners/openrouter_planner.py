from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..affordances import offered_affordances, selection_for
from ..config import PlannerConfig, PlanningConfig
from ..core.observation import Observation
from ..core.planning import PlannerOutput
from ..hosted_continuation import (
    CONTINUE_STRUCTURED_JSON_SUFFIX,
    TRUNCATED_FINISH_REASONS,
    assistant_continuation,
)
from ..planner_context import render_planner_payload
from .base import (
    HostedPlannerCallDiagnostics,
    HostedPlannerResponseError,
    Planner,
    PreparedPlannerInput,
    hosted_proposal_model,
    output_token_budget,
    prepared_budgeted_input,
    validate_planner_prompt_budget,
)
from .context_capacity import (
    HostedModelCapacity,
    conservative_text_token_estimate,
    hosted_context_envelope,
    resolve_openrouter_model_capacity,
)
from .plan_proposal import PlanProposal, compile_hosted_plan_proposal
from .schema_dialect import projected_response_format

# Phrases providers use when the request was fine but the schema was not. They
# are worth matching narrowly: a 400 for any other reason is a real failure and
# must not be retried as if it were this one.
_SCHEMA_REFUSAL_PHRASES = (
    "compiled grammar",
    "response_json_schema",
    "output_config.format",
    "response_format",
    "json_schema",
    "schema",
)


def _is_schema_refusal(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) != 400:
        return False
    text = str(exc).lower()
    return any(phrase in text for phrase in _SCHEMA_REFUSAL_PHRASES)


def _json_body(content: str) -> str:
    """Return the JSON object in a reply, tolerating a code fence around it.

    Providers honouring the schema return bare JSON, but not every one does, and
    a fenced reply is otherwise a whole wasted planning round trip.
    """
    text = content.strip()
    if text.startswith("```"):
        _, _, remainder = text.partition("\n")
        text = remainder.rpartition("```")[0].strip() or remainder.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start > 0 and end > start:
        text = text[start : end + 1]
    return text


def _field(value: object, name: str) -> object | None:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _integer_field(value: object, name: str) -> int | None:
    candidate = _field(value, name)
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return candidate
    return None


def _text_characters(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(_text_characters(item) for item in value)
    if not isinstance(value, dict):
        return 0
    text = value.get("text")
    if isinstance(text, str):
        return len(text)
    return sum(
        _text_characters(item)
        for key, item in value.items()
        if key != "image_url"
    )


def _contains_screenshot(value: object) -> bool:
    if isinstance(value, list):
        return any(_contains_screenshot(item) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get("type") == "image_url":
        return True
    return any(_contains_screenshot(item) for item in value.values())


def _planner_request_text(output_model: type[BaseModel]) -> str:
    if output_model is PlanProposal:
        request = (
            "Choose one short objective and exactly one current affordance selection. "
            "The runtime will observe again after it completes. "
        )
    else:
        request = "Choose exactly one current affordance from this observation. "
    return request + f"Return the {output_model.__name__} schema only.\n\n"


def _observe_selection(observation: Observation) -> dict[str, Any]:
    offer = next(
        offer
        for offer in offered_affordances(observation)
        if offer.semantic == "observe"
    )
    return selection_for(offer).model_dump(mode="json")


def _sum_optional(
    diagnostics: list[HostedPlannerCallDiagnostics],
    field: str,
) -> int | None:
    values = [
        value
        for item in diagnostics
        if isinstance(value := getattr(item, field), int)
    ]
    return sum(values) if values else None


def _aggregate_diagnostics(
    segments: list[HostedPlannerCallDiagnostics],
    *,
    response_characters: int,
    schema_refusal_count: int,
) -> HostedPlannerCallDiagnostics:
    final = segments[-1]
    return replace(
        final,
        prompt_tokens=_sum_optional(segments, "prompt_tokens"),
        completion_tokens=_sum_optional(segments, "completion_tokens"),
        reasoning_tokens=_sum_optional(segments, "reasoning_tokens"),
        total_tokens=_sum_optional(segments, "total_tokens"),
        cached_tokens=_sum_optional(segments, "cached_tokens"),
        cache_write_tokens=_sum_optional(segments, "cache_write_tokens"),
        response_characters=response_characters,
        system_characters=segments[0].system_characters,
        observation_characters=segments[0].observation_characters,
        schema_characters=segments[0].schema_characters,
        request_text_characters=sum(
            item.request_text_characters for item in segments
        ),
        schema_in_prompt=any(item.schema_in_prompt for item in segments),
        screenshot_included=any(item.screenshot_included for item in segments),
        continuation_count=len(segments) - 1,
        segment_finish_reasons=tuple(item.finish_reason for item in segments),
        response_ids=tuple(
            item.response_id for item in segments if item.response_id is not None
        ),
        schema_refusal_count=schema_refusal_count,
        segment_native_finish_reasons=tuple(
            item.native_finish_reason for item in segments
        ),
    )


class OpenRouterPlanner(Planner):
    """Vision planner using OpenRouter's OpenAI-compatible Chat API."""

    max_plan_steps: int = 4

    _last_call_diagnostics: HostedPlannerCallDiagnostics | None = None
    _last_sent_messages: list[dict[str, Any]] | None = None

    def __init__(
        self,
        config: PlannerConfig,
        prompt_file: Path,
        *,
        max_plan_steps: int = 4,
        planning: PlanningConfig | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The OpenRouter planner requires the optional dependency: "
                "pip install -e '.[openai]'"
            ) from exc
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for the OpenRouter planner.")
        self.config = config
        self.instructions = prompt_file.read_text(encoding="utf-8")
        self.client: Any = AsyncOpenAI(api_key=api_key, base_url=config.openrouter_base_url)
        self.max_plan_steps = max_plan_steps
        self.planning = planning or PlanningConfig(max_plan_steps=max_plan_steps)
        self._model_capacity = resolve_openrouter_model_capacity(
            base_url=config.openrouter_base_url,
            model=config.openrouter_model,
            api_key=api_key,
            timeout_seconds=config.model_metadata_timeout_seconds,
            configured_context_window_tokens=config.context_window_tokens,
        )
        self._schema_prompt_fallbacks: set[str] = set()

    def prepare_input(
        self,
        observation: Observation,
        *,
        context_id: str,
    ) -> PreparedPlannerInput:
        response_model = hosted_proposal_model(observation)
        schema_text = json.dumps(
            projected_response_format(response_model)["json_schema"]["schema"]
        )
        system_text = self.instructions
        output_tokens = output_token_budget(
            self.config,
            observation,
            max_plan_steps=self.max_plan_steps,
        )
        capacity = getattr(
            self,
            "_model_capacity",
            HostedModelCapacity(
                requested_model=self.config.openrouter_model,
                context_window_tokens=self.config.context_window_tokens,
                max_completion_tokens=None,
                source=(
                    "configured_override"
                    if self.config.context_window_tokens is not None
                    else "provider_metadata_unavailable"
                ),
                lookup_error=(
                    None
                    if self.config.context_window_tokens is not None
                    else "planner constructed without provider metadata"
                ),
            ),
        )
        envelope = hosted_context_envelope(
            capacity,
            output_tokens=output_tokens,
            system_text=system_text,
            schema_text=schema_text,
            request_text=_planner_request_text(response_model),
            screenshot_included=(
                self.config.include_screenshot
                and observation.screenshot_path is not None
                and observation.screenshot_path.exists()
            ),
        )
        if envelope.compaction_target_tokens is None:
            payload = render_planner_payload(observation)
        else:
            assert envelope.hard_observation_tokens is not None
            payload = render_planner_payload(
                observation,
                max_chars=envelope.compaction_target_tokens,
                max_context_chars=envelope.hard_observation_tokens,
                measure=conservative_text_token_estimate,
                measurement=envelope.estimator,
            )
        return prepared_budgeted_input(
            observation,
            context_id=context_id,
            payload=payload,
            context_capacity_source=envelope.capacity.source,
            context_window_tokens=envelope.capacity.context_window_tokens,
            compaction_target_tokens=envelope.compaction_target_tokens,
            hard_observation_tokens=envelope.hard_observation_tokens,
            context_token_estimator=envelope.estimator,
            reserved_output_tokens=envelope.reserved_output_tokens,
            reserved_static_tokens=envelope.reserved_static_tokens,
            reserved_image_tokens=envelope.reserved_image_tokens,
            proactive_headroom_tokens=envelope.proactive_headroom_tokens,
        )

    async def decide(self, observation: Observation) -> PlannerOutput:
        return await self.decide_prepared(
            self.prepare_input(observation, context_id="pc-1")
        )

    async def decide_prepared(self, prepared: PreparedPlannerInput) -> PlannerOutput:
        self._last_call_diagnostics = None
        self._last_sent_messages = None
        observation = prepared.context.observation
        if prepared.payload is None:
            raise RuntimeError("OpenRouter planner input has no budgeted payload.")
        response_model = hosted_proposal_model(observation)
        schema_surface = response_model.__name__
        schema_prompt_fallbacks = getattr(
            self,
            "_schema_prompt_fallbacks",
            None,
        )
        if schema_prompt_fallbacks is None:
            schema_prompt_fallbacks = set()
            self._schema_prompt_fallbacks = schema_prompt_fallbacks
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    _planner_request_text(response_model)
                    + prepared.payload
                ),
            }
        ]
        if (
            self.config.include_screenshot
            and observation.screenshot_path is not None
            and observation.screenshot_path.exists()
        ):
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._data_url(observation.screenshot_path),
                        "detail": self.config.screenshot_detail,
                    },
                }
            )

        # Generation limits belong to the request actually sent, not only the
        # local config. Reasoning is added to OpenRouter's provider-neutral
        # `reasoning` object in `_request`; `none` omits it entirely.
        extra: dict[str, Any] = {}
        extra["max_tokens"] = output_token_budget(
            self.config,
            observation,
            max_plan_steps=self.max_plan_steps,
        )
        extra["temperature"] = self.config.temperature

        system_instructions = self.instructions
        schema_characters = len(
            json.dumps(
                projected_response_format(response_model)["json_schema"]["schema"]
            )
        )
        validate_planner_prompt_budget(
            system_characters=len(system_instructions),
            schema_characters=schema_characters,
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_instructions,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": content},
        ]

        schema_refusal_count = 0
        async with asyncio.timeout(self.config.timeout_seconds):
            if schema_surface in schema_prompt_fallbacks:
                response = await self._request(
                    messages,
                    response_model,
                    extra,
                    session_id=observation.run_id,
                    observation_characters=len(prepared.payload),
                )
            else:
                try:
                    response = await self._request(
                        messages,
                        response_model,
                        extra,
                        session_id=observation.run_id,
                        constrained=True,
                        observation_characters=len(prepared.payload),
                    )
                except Exception as exc:
                    if not _is_schema_refusal(exc):
                        raise
                    schema_refusal_count += 1
                    # Some providers cap how large a schema they will compile
                    # into a decoding grammar, and this catalog is over the cap.
                    # Asking in the prompt still gets a conforming answer, and
                    # the reply is validated against the model either way.
                    schema_prompt_fallbacks.add(schema_surface)
                    response = await self._request(
                        messages,
                        response_model,
                        extra,
                        session_id=observation.run_id,
                        observation_characters=len(prepared.payload),
                    )

        segments: list[HostedPlannerCallDiagnostics] = []
        response_parts: list[str] = []
        while True:
            message = response.choices[0].message
            diagnostics = self._last_call_diagnostics
            sent_messages = self._last_sent_messages
            if diagnostics is None or sent_messages is None:
                raise RuntimeError("OpenRouter response had no transport diagnostics.")
            segments.append(diagnostics)
            response_parts.append(
                message.content if isinstance(message.content, str) else ""
            )
            if diagnostics.finish_reason not in TRUNCATED_FINISH_REASONS:
                break
            aggregate = _aggregate_diagnostics(
                segments,
                response_characters=sum(len(part) for part in response_parts),
                schema_refusal_count=schema_refusal_count,
            )
            self._last_call_diagnostics = aggregate
            if len(segments) - 1 >= self.config.max_output_continuations:
                raise HostedPlannerResponseError("output_truncated", aggregate)
            continuation_messages = [
                *sent_messages,
                assistant_continuation(message),
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": CONTINUE_STRUCTURED_JSON_SUFFIX,
                        }
                    ],
                },
            ]
            async with asyncio.timeout(self.config.timeout_seconds):
                response = await self._request(
                    continuation_messages,
                    response_model,
                    extra,
                    session_id=observation.run_id,
                    continuation=True,
                    observation_characters=len(prepared.payload),
                )

        combined_response = "".join(response_parts)
        diagnostics = _aggregate_diagnostics(
            segments,
            response_characters=len(combined_response),
            schema_refusal_count=schema_refusal_count,
        )
        self._last_call_diagnostics = diagnostics
        if not combined_response:
            raise HostedPlannerResponseError("empty_response", diagnostics)
        planning = getattr(
            self,
            "planning",
            PlanningConfig(max_plan_steps=self.max_plan_steps),
        )
        try:
            document = json.loads(_json_body(combined_response))
            compiled = compile_hosted_plan_proposal(
                document,
                observation=observation,
                context_id=prepared.context.manifest.context_id,
                planning=planning,
            )
            output = compiled.output
            if compiled.rejected_sidecars:
                self._last_call_diagnostics = replace(
                    diagnostics,
                    proposal_sidecar_rejections=tuple(
                        f"{item.surface}[{item.index}]: {item.detail}"
                        for item in compiled.rejected_sidecars
                    ),
                )
        except ValueError as exc:
            output = compile_hosted_plan_proposal(
                {
                    "objective": "Regain a fresh planning turn after an unusable proposal.",
                    "steps": [
                        {
                            "selection": _observe_selection(observation),
                        }
                    ],
                },
                observation=observation,
                context_id=prepared.context.manifest.context_id,
                planning=planning,
            ).output
            self._last_call_diagnostics = replace(
                diagnostics,
                proposal_fallback_reason=str(exc)[:1000],
            )
        return output

    async def _request(
        self,
        messages: list[dict[str, Any]],
        output_model: type[BaseModel],
        extra: dict[str, Any],
        *,
        session_id: str,
        constrained: bool = False,
        continuation: bool = False,
        observation_characters: int,
    ) -> Any:
        """Ask for `output_model`, constraining decoding only if asked to."""
        response_format = projected_response_format(output_model)
        schema = json.dumps(response_format["json_schema"]["schema"])
        kwargs: dict[str, Any] = {}
        if constrained:
            # Sending the model class instead would let the SDK build a schema
            # only OpenAI accepts; every other provider 400s on it.
            kwargs["response_format"] = response_format
        elif not continuation:
            messages = [
                *messages[:-1],
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Reply with JSON only, no prose, conforming exactly to "
                                f"this {output_model.__name__} JSON Schema:\n\n{schema}"
                            ),
                            "cache_control": {"type": "ephemeral"},
                        },
                        *messages[-1]["content"],
                    ],
                },
            ]
        extra_body: dict[str, Any] = {
            "session_id": f"kenshi:{session_id}"[:256],
            "provider": {
                "sort": self.config.openrouter_provider_sort,
                "require_parameters": self.config.openrouter_require_parameters,
            }
        }
        if self.config.reasoning_effort != "none":
            extra_body["reasoning"] = {"effort": self.config.reasoning_effort}
        self._last_sent_messages = messages
        response = await self.client.chat.completions.create(
            model=self.config.openrouter_model,
            messages=messages,
            extra_body=extra_body,
            **kwargs,
            **extra,
        )
        choice = response.choices[0]
        message = choice.message
        response_content = message.content if isinstance(message.content, str) else ""
        usage = _field(response, "usage")
        prompt_details = _field(usage, "prompt_tokens_details")
        completion_details = _field(usage, "completion_tokens_details")
        response_model = _field(response, "model")
        provider_name = _field(response, "provider")
        response_id = _field(response, "id")
        finish_reason = _field(choice, "finish_reason")
        native_finish_reason = _field(choice, "native_finish_reason")
        response_error = _field(response, "error")
        error_metadata = _field(response_error, "metadata")
        provider_error_type = _field(error_metadata, "error_type")
        self._last_call_diagnostics = HostedPlannerCallDiagnostics(
            provider_kind="openrouter",
            output_model=output_model.__name__,
            requested_model=self.config.openrouter_model,
            response_model=response_model if isinstance(response_model, str) else None,
            provider_name=provider_name if isinstance(provider_name, str) else None,
            response_id=response_id if isinstance(response_id, str) else None,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            max_output_tokens=int(extra["max_tokens"]),
            prompt_tokens=_integer_field(usage, "prompt_tokens"),
            completion_tokens=_integer_field(usage, "completion_tokens"),
            reasoning_tokens=_integer_field(completion_details, "reasoning_tokens"),
            total_tokens=_integer_field(usage, "total_tokens"),
            response_characters=len(response_content),
            system_characters=_text_characters(messages[0].get("content")),
            observation_characters=observation_characters,
            schema_characters=len(schema),
            request_text_characters=sum(
                _text_characters(candidate.get("content")) for candidate in messages
            ),
            schema_in_prompt=not constrained and not continuation,
            screenshot_included=any(
                _contains_screenshot(candidate.get("content")) for candidate in messages
            ),
            segment_finish_reasons=(
                finish_reason if isinstance(finish_reason, str) else None,
            ),
            response_ids=(
                (response_id,) if isinstance(response_id, str) else ()
            ),
            native_finish_reason=(
                native_finish_reason
                if isinstance(native_finish_reason, str)
                else None
            ),
            segment_native_finish_reasons=(
                (
                    native_finish_reason
                    if isinstance(native_finish_reason, str)
                    else None
                ),
            ),
            provider_error_type=(
                provider_error_type
                if isinstance(provider_error_type, str)
                else None
            ),
            cached_tokens=_integer_field(prompt_details, "cached_tokens"),
            cache_write_tokens=_integer_field(
                prompt_details,
                "cache_write_tokens",
            ),
        )
        return response

    def take_call_diagnostics(self) -> HostedPlannerCallDiagnostics | None:
        diagnostics = getattr(self, "_last_call_diagnostics", None)
        self._last_call_diagnostics = None
        return diagnostics

    @staticmethod
    def _data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
