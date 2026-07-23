from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from ..config import PlannerConfig
from ..models import (
    Observation,
    PlanEnvelope,
    PlannerOutput,
    PlanPatch,
)
from .base import Planner, structured_output_model


class OpenRouterPlanner(Planner):
    """Vision planner using OpenRouter's OpenAI-compatible Chat API."""

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
                "Return one future-only PlanPatch grounded in active_plan and the "
                "exact world_revision. "
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
                    + observation.planner_payload(max_chars=self.config.max_observation_chars)
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

        async with asyncio.timeout(self.config.timeout_seconds):
            response = await self.client.chat.completions.parse(
                model=self.config.openrouter_model,
                messages=[
                    {"role": "system", "content": self.instructions},
                    {"role": "user", "content": content},
                ],
                response_format=output_model,
                reasoning_effort=self.config.reasoning_effort,
                extra_body={
                    "provider": {
                        "sort": self.config.openrouter_provider_sort,
                        "require_parameters": True,
                    }
                },
            )

        message = response.choices[0].message
        parsed = message.parsed
        if parsed is None:
            if not message.content:
                raise RuntimeError("OpenRouter response contained neither parsed output nor text.")
            parsed = output_model.model_validate_json(message.content)
        return output_model.model_validate(parsed)

    @staticmethod
    def _data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
