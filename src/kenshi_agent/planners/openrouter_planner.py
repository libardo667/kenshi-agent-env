from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import PlannerConfig
from ..models import (
    Observation,
    PlanEnvelope,
    PlannerOutput,
    PlanPatch,
)
from .base import Planner, instructions_for_policy, structured_output_model
from .schema_dialect import portable_response_format

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


class OpenRouterPlanner(Planner):
    """Vision planner using OpenRouter's OpenAI-compatible Chat API."""

    # Flipped for the rest of the run the first time a provider refuses to
    # compile the schema, so the cost of discovering it is paid once.
    _schema_in_prompt: bool = False

    def __init__(self, config: PlannerConfig, prompt_file: Path) -> None:
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

    async def decide(self, observation: Observation) -> PlannerOutput:
        output_model = structured_output_model(observation)
        if output_model is PlanPatch:
            request = (
                "Return one PlanPatch grounded in active_plan and the exact "
                "world_revision; preserve the active step unless an exact guarded "
                "interrupt is warranted. "
            )
        elif output_model is PlanEnvelope:
            request = (
                "Return one bounded PlanEnvelope grounded in the exact world_revision. "
            )
        else:
            request = "Choose exactly one next action from this observation. "
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    request
                    + f"Return the {output_model.__name__} schema only.\n\n"
                    + observation.planner_payload(
                        max_chars=self.config.max_observation_chars,
                        max_context_chars=self.config.max_context_chars,
                    )
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

        # A non-reasoning model has no effort to set, and sending the parameter
        # anyway either errors or - with require_parameters on - quietly routes
        # the request nowhere. `none` means "do not ask for reasoning at all".
        extra: dict[str, Any] = {}
        if self.config.reasoning_effort != "none":
            extra["reasoning_effort"] = self.config.reasoning_effort

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": instructions_for_policy(
                    self.instructions,
                    observation.live_execution_policy,
                ),
            },
            {"role": "user", "content": content},
        ]

        async with asyncio.timeout(self.config.timeout_seconds):
            if self._schema_in_prompt:
                response = await self._request(messages, output_model, extra)
            else:
                try:
                    response = await self._request(
                        messages, output_model, extra, constrained=True
                    )
                except Exception as exc:
                    if not _is_schema_refusal(exc):
                        raise
                    # Some providers cap how large a schema they will compile
                    # into a decoding grammar, and this catalog is over the cap.
                    # Asking in the prompt still gets a conforming answer, and
                    # the reply is validated against the model either way.
                    self._schema_in_prompt = True
                    response = await self._request(messages, output_model, extra)

        message = response.choices[0].message
        if not message.content:
            raise RuntimeError("OpenRouter response contained no text.")
        return output_model.model_validate_json(_json_body(message.content))

    async def _request(
        self,
        messages: list[dict[str, Any]],
        output_model: type[BaseModel],
        extra: dict[str, Any],
        *,
        constrained: bool = False,
    ) -> Any:
        """Ask for `output_model`, constraining decoding only if asked to."""
        response_format = portable_response_format(output_model)
        kwargs: dict[str, Any] = {}
        if constrained:
            # Sending the model class instead would let the SDK build a schema
            # only OpenAI accepts; every other provider 400s on it.
            kwargs["response_format"] = response_format
        else:
            schema = json.dumps(response_format["json_schema"]["schema"])
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
                        },
                        *messages[-1]["content"],
                    ],
                },
            ]
        return await self.client.chat.completions.create(
            model=self.config.openrouter_model,
            messages=messages,
            extra_body={
                "provider": {
                    "sort": self.config.openrouter_provider_sort,
                    "require_parameters": self.config.openrouter_require_parameters,
                }
            },
            **kwargs,
            **extra,
        )

    @staticmethod
    def _data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
