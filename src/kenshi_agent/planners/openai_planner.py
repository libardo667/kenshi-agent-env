from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..config import PlannerConfig, PlanningConfig
from ..core.observation import Observation
from ..core.planning import PlannerOutput
from ..planner_context import render_planner_payload
from .base import (
    Planner,
    PreparedPlannerInput,
    hosted_proposal_model,
    output_token_budget,
    prepared_budgeted_input,
)
from .context_capacity import (
    HostedModelCapacity,
    conservative_text_token_estimate,
    hosted_context_envelope,
)
from .plan_proposal import PlanProposal, compile_hosted_plan_proposal


def _planner_request_text(output_model: type[BaseModel]) -> str:
    if output_model is PlanProposal:
        request = (
            "Choose one short objective and exactly one current affordance selection. "
            "The runtime will observe again after it completes. "
        )
    else:
        request = "Choose exactly one current affordance from this observation. "
    return request + f"Return the {output_model.__name__} schema only.\n\n"


class OpenAIPlanner(Planner):
    """Optional vision planner using the OpenAI Responses API and Pydantic output."""

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
                "The OpenAI planner requires the optional dependency: pip install -e '.[openai]'"
            ) from exc
        self.config = config
        self.instructions = prompt_file.read_text(encoding="utf-8")
        self.client: Any = AsyncOpenAI()
        self.max_plan_steps = max_plan_steps
        self.planning = planning or PlanningConfig(max_plan_steps=max_plan_steps)

    def prepare_input(
        self,
        observation: Observation,
        *,
        context_id: str,
    ) -> PreparedPlannerInput:
        response_model = hosted_proposal_model(observation)
        system_text = self.instructions
        capacity = HostedModelCapacity(
            requested_model=self.config.model,
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
                else "OpenAI model metadata exposes no context capacity"
            ),
        )
        envelope = hosted_context_envelope(
            capacity,
            output_tokens=output_token_budget(
                self.config,
                observation,
                max_plan_steps=self.max_plan_steps,
            ),
            system_text=system_text,
            schema_text=json.dumps(response_model.model_json_schema()),
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
        observation = prepared.context.observation
        if prepared.payload is None:
            raise RuntimeError("OpenAI planner input has no budgeted payload.")
        response_model = hosted_proposal_model(observation)
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
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
                text_format=response_model,
                reasoning={"effort": self.config.reasoning_effort},
                max_output_tokens=output_token_budget(
                    self.config,
                    observation,
                    max_plan_steps=self.max_plan_steps,
                ),
            )
        parsed = response.output_parsed
        if parsed is None:
            if not response.output_text:
                raise RuntimeError("OpenAI response contained neither parsed output nor text.")
            parsed = response_model.model_validate_json(response.output_text)
        output: PlannerOutput
        if isinstance(parsed, BaseModel):
            document = parsed.model_dump(mode="json")
        else:
            document = parsed
        output = compile_hosted_plan_proposal(
            document,
            observation=observation,
            context_id=prepared.context.manifest.context_id,
            planning=getattr(
                self,
                "planning",
                PlanningConfig(max_plan_steps=self.max_plan_steps),
            ),
        ).output
        return output

    @staticmethod
    def _data_url(path: Path) -> str:
        mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "image/png"
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
