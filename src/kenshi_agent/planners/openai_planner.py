from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any

from ..config import PlannerConfig
from ..models import (
    Observation,
    PlanEnvelope,
    PlannerDecision,
    PlannerOutput,
    PlanningMode,
)
from .base import Planner


class OpenAIPlanner(Planner):
    """Optional vision planner using the OpenAI Responses API and Pydantic output."""

    def __init__(self, config: PlannerConfig, prompt_file: Path) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The OpenAI planner requires the optional dependency: pip install -e '.[openai]'"
            ) from exc
        self.config = config
        self.instructions = prompt_file.read_text(encoding="utf-8")
        self.client: Any = AsyncOpenAI()

    async def decide(self, observation: Observation) -> PlannerOutput:
        output_model = (
            PlanEnvelope
            if observation.planning_mode == PlanningMode.CONTINUOUS
            else PlannerDecision
        )
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    (
                        "Return one bounded PlanEnvelope grounded in the exact world_revision. "
                        if observation.planning_mode == PlanningMode.CONTINUOUS
                        else "Choose exactly one next action from this observation. "
                    )
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
                    "type": "input_image",
                    "image_url": self._data_url(observation.screenshot_path),
                    "detail": self.config.screenshot_detail,
                }
            )
        async with asyncio.timeout(self.config.timeout_seconds):
            response = await self.client.responses.parse(
                model=self.config.model,
                instructions=self.instructions,
                input=[{"role": "user", "content": content}],
                text_format=output_model,
                reasoning={"effort": self.config.reasoning_effort},
            )
        parsed = response.output_parsed
        if parsed is None:
            if not response.output_text:
                raise RuntimeError("OpenAI response contained neither parsed output nor text.")
            parsed = output_model.model_validate_json(response.output_text)
        return output_model.model_validate(parsed)

    @staticmethod
    def _data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
