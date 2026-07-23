from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    Action,
    ControlMode,
    LiveContinuousPolicy,
    PlanningMode,
    parse_action,
)

_ENV_DEFAULT_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^}]*)\}")


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(ConfigModel):
    runs_dir: Path
    prompt_file: Path
    memory_db: Path


class RuntimeConfig(ConfigModel):
    max_steps: int = Field(default=32, ge=1, le=100000)
    settle_seconds: float = Field(default=0.25, ge=0.0, le=60.0)
    observation_memory_limit: int = Field(default=12, ge=0, le=100)
    stop_when_terminated: bool = True
    objective: str | None = Field(default=None, max_length=1000)
    decision_stream: bool = False


class ControlConfig(ConfigModel):
    mode: ControlMode = ControlMode.INTERFACE_ONLY
    native_assisted_actions_enabled: bool = False


class PlanningConfig(ConfigModel):
    mode: PlanningMode = PlanningMode.SINGLE_STEP
    live_execution_policy: LiveContinuousPolicy = LiveContinuousPolicy.DISABLED
    observation_pump_enabled: bool = True
    stateful_movement_options_enabled: bool = True
    concurrent_option_planning_enabled: bool = True
    observation_pump_seconds: float = Field(default=0.1, gt=0.0, le=5.0)
    state_history_limit: int = Field(default=128, ge=8, le=4096)
    state_delta_limit: int = Field(default=128, ge=8, le=4096)
    event_journal_limit: int = Field(default=256, ge=16, le=8192)
    subscriber_queue_limit: int = Field(default=32, ge=2, le=1024)
    max_delta_paths: int = Field(default=128, ge=16, le=2048)
    max_plan_steps: int = Field(default=4, ge=1, le=8)
    max_actions_per_plan: int = Field(default=8, ge=1, le=16)
    max_plan_wall_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    max_plan_game_seconds: float = Field(default=12.0, gt=0.0, le=3600.0)
    max_pointer_actions_per_plan: int = Field(default=8, ge=0, le=32)
    max_purchase_actions_per_plan: int = Field(default=1, ge=0, le=8)
    max_native_assisted_actions_per_plan: int = Field(default=0, ge=0, le=8)
    max_consecutive_replans: int = Field(default=3, ge=0, le=20)


class PlannerConfig(ConfigModel):
    kind: Literal["heuristic", "scripted", "subprocess", "openai", "openrouter"] = "heuristic"
    model: str = "gpt-5.6-luna"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "low"
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    max_output_tokens_base: int = Field(default=4096, ge=512, le=100000)
    max_output_tokens_per_plan_step: int = Field(default=2048, ge=256, le=50000)
    max_output_tokens_ceiling: int = Field(default=12288, ge=768, le=100000)
    include_screenshot: bool = True
    screenshot_detail: Literal["low", "high", "auto"] = "high"
    max_observation_chars: int = Field(default=24000, ge=1000, le=200000)
    openrouter_model: str = "openai/gpt-5.6-luna"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_provider_sort: Literal["latency", "throughput", "price"] = "latency"

    @model_validator(mode="after")
    def output_token_ceiling_covers_base(self) -> PlannerConfig:
        if self.max_output_tokens_ceiling < self.max_output_tokens_base:
            raise ValueError("max_output_tokens_ceiling must cover max_output_tokens_base")
        return self


class MockConfig(ConfigModel):
    seed: int = 7
    start_location: str = "The Hub"
    start_cats: int = Field(default=180, ge=0)
    start_hunger: float = Field(default=250.0, ge=0.0, le=300.0)
    start_food_items: int = Field(default=1, ge=0)
    start_first_aid_kits: int = Field(default=1, ge=0)
    minutes_per_wait_second: float = Field(default=1.0, gt=0.0, le=120.0)
    random_events: bool = True


class TelemetryConfig(ConfigModel):
    file: Path
    max_age_seconds: float = Field(default=3.0, gt=0.0, le=300.0)
    read_retries: int = Field(default=3, ge=1, le=20)
    retry_delay_seconds: float = Field(default=0.03, ge=0.0, le=2.0)
    require_protocol_major: int = Field(default=0, ge=0, le=100)


class CaptureConfig(ConfigModel):
    enabled: bool = True
    window_title_contains: str = "Kenshi"
    image_format: Literal["png", "jpeg"] = "png"
    jpeg_quality: int = Field(default=90, ge=20, le=100)
    crop_client_area: bool = True


class ControlsConfig(ConfigModel):
    pause_key: str = "space"
    pause_skill: str | None = Field(default=None, min_length=1, max_length=80)
    unpause_skill: str | None = Field(default=None, min_length=1, max_length=80)
    speed_keys: dict[int, str] = Field(default_factory=lambda: {1: "f2", 2: "f3", 3: "f4"})
    focus_before_input: bool = True
    post_input_delay_seconds: float = Field(default=0.08, ge=0.0, le=2.0)
    polite_input_enabled: bool = True
    idle_seconds_before_input: float = Field(default=1.25, ge=0.0, le=30.0)
    max_wait_for_input_turn_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    restore_foreground_after_input: bool = True
    restore_cursor_after_input: bool = True
    alt_tab_after_input: bool = True
    pointer_mode: Literal["absolute", "relative"] = "absolute"
    relative_pointer_max_step_pixels: int = Field(default=12, ge=1, le=100)
    relative_pointer_tolerance_pixels: int = Field(default=1, ge=0, le=10)
    relative_pointer_settle_seconds: float = Field(default=0.006, ge=0.0, le=0.1)
    relative_pointer_max_attempts: int = Field(default=500, ge=1, le=2000)

    @field_validator("speed_keys")
    @classmethod
    def all_speeds_present(cls, value: dict[int, str]) -> dict[int, str]:
        missing = {1, 2, 3} - set(value)
        if missing:
            raise ValueError(f"speed_keys is missing mappings for: {sorted(missing)}")
        return value


class SafetyConfig(ConfigModel):
    live_actions_enabled: bool = False
    require_cli_execute_flag: bool = True
    emergency_stop_key: str = "f12"
    supervisor_enabled: bool = True
    supervisor_max_sequence_stalls: int = Field(default=3, ge=1, le=100)
    supervisor_sequence_stall_min_age_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=30.0,
    )
    supervisor_pause_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    max_primitive_actions_per_step: int = Field(default=12, ge=1, le=100)
    max_actions_per_minute: int = Field(default=90, ge=1, le=1000)
    max_wait_seconds: float = Field(default=10.0, ge=0.0, le=60.0)
    block_clicks_when_telemetry_stale: bool = True
    allow_live_unpause_actions: bool = False
    max_purchase_price: int = Field(default=1000, ge=1)
    min_money_after_purchase: int = Field(default=0, ge=0)
    max_purchases_per_run: int = Field(default=1, ge=0, le=20)
    allow_action_kinds: list[str] = Field(default_factory=list)
    allow_skills: list[str] = Field(default_factory=list)


class MemoryConfig(ConfigModel):
    enabled: bool = True
    run_namespace: str = "default"
    max_recalled_memories: int = Field(default=12, ge=0, le=100)
    minimum_salience: float = Field(default=0.15, ge=0.0, le=1.0)


class NormalizedPointerBoundsConfig(ConfigModel):
    min_x: float = Field(ge=0.0, le=1.0)
    max_x: float = Field(ge=0.0, le=1.0)
    min_y: float = Field(ge=0.0, le=1.0)
    max_y: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def ordered_bounds(self) -> NormalizedPointerBoundsConfig:
        if self.min_x > self.max_x:
            raise ValueError("min_x must not exceed max_x")
        if self.min_y > self.max_y:
            raise ValueError("min_y must not exceed max_y")
        return self

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y


class MacroConfig(ConfigModel):
    description: str = ""
    arguments: dict[str, str] = Field(default_factory=dict)
    visual_precondition: str | None = None
    normalized_pointer_bounds: NormalizedPointerBoundsConfig | None = None
    movement_pulse_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    movement_pulse_min_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    movement_pulse_max_seconds: float | None = Field(default=None, gt=0.0, le=10.0)
    requires_native_assisted: bool = False
    actions: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_movement_pulse_bounds(self) -> MacroConfig:
        if self.movement_pulse_seconds is None:
            if (
                self.movement_pulse_min_seconds is not None
                or self.movement_pulse_max_seconds is not None
            ):
                raise ValueError("movement pulse bounds require movement_pulse_seconds")
            return self
        minimum = self.movement_pulse_min_seconds or self.movement_pulse_seconds
        maximum = self.movement_pulse_max_seconds or self.movement_pulse_seconds
        if minimum > self.movement_pulse_seconds or self.movement_pulse_seconds > maximum:
            raise ValueError("movement pulse duration must satisfy min <= default <= max")
        return self

    def parsed_actions(self) -> list[Action]:
        return [parse_action(item) for item in self.actions]


class AppConfig(ConfigModel):
    version: int = 1
    mode: Literal["mock", "live", "replay"] = "mock"
    control: ControlConfig = Field(default_factory=ControlConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    paths: PathsConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    mock: MockConfig = Field(default_factory=MockConfig)
    telemetry: TelemetryConfig
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    controls: ControlsConfig = Field(default_factory=ControlsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    macros: dict[str, MacroConfig] = Field(default_factory=dict)

    @model_validator(mode="after")
    def planning_risk_matches_control_mode(self) -> AppConfig:
        if (
            self.control.mode == ControlMode.INTERFACE_ONLY
            and self.planning.max_native_assisted_actions_per_plan != 0
        ):
            raise ValueError(
                "interface_only control requires planning.max_native_assisted_actions_per_plan=0"
            )
        return self


def _expand_env_string(value: str) -> str:
    def replace_default(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        return os.environ.get(name, default)

    return os.path.expandvars(_ENV_DEFAULT_PATTERN.sub(replace_default, value))


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_env_string(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


def _resolve_path(path: Path, base: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    expanded = _expand_env(raw)
    config = AppConfig.model_validate(expanded)
    base = config_path.parent
    return config.model_copy(
        update={
            "paths": config.paths.model_copy(
                update={
                    "runs_dir": _resolve_path(config.paths.runs_dir, base),
                    "prompt_file": _resolve_path(config.paths.prompt_file, base),
                    "memory_db": _resolve_path(config.paths.memory_db, base),
                }
            ),
            "telemetry": config.telemetry.model_copy(
                update={"file": _resolve_path(config.telemetry.file, base)}
            ),
        }
    )
