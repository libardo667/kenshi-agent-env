from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .core.continuity import MemoryRetrievalPolicy
from .core.operation import (
    ControlMode,
)
from .core.scenario import ScenarioAttestation
from .core.telemetry import ScenarioIdentity
from .core.transport import CalibrationIdentity

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
    objective: str | None = Field(default=None, max_length=1000)
    decision_stream: bool = False
    # Explicit experimental context. Absent means this run cannot support a
    # cross-scenario or cross-save recurrence claim.
    scenario: ScenarioIdentity | None = None
    # Present only when the supported fixture launcher proved the exact staged
    # save and all declared axes from fresh loaded telemetry.
    scenario_attestation: ScenarioAttestation | None = None
    # Log a compact observation digest instead of the whole observation. Full
    # payloads are only needed to replay a run with the replay environment, and
    # writing them every pump tick produced a 112 MB log in ten minutes.
    log_full_observations: bool = False


class ControlConfig(ConfigModel):
    mode: ControlMode = ControlMode.INTERFACE_ONLY
    native_assisted_actions_enabled: bool = False


class PlanningConfig(ConfigModel):
    observation_pump_enabled: bool = True
    # Arrival/threat radii for the contracted semantic approach. The monitored
    # option is not optional for that action, so these are thresholds, not a
    # feature flag.
    semantic_approach_arrival_distance: float = Field(default=5.0, gt=0.0, le=100.0)
    semantic_approach_threat_distance: float = Field(default=15.0, gt=0.0, le=500.0)
    # Mirrors safety.require_paused_between_actions for the executor: with the
    # game running continuously, a per-plan game-time budget measures thinking
    # rather than acting.
    require_paused_between_actions: bool = True
    concurrent_option_planning_enabled: bool = True
    concurrent_option_planning_delay_seconds: float = Field(
        default=20.0,
        ge=0.0,
        le=120.0,
    )
    observation_pump_seconds: float = Field(default=0.1, gt=0.0, le=5.0)
    state_history_limit: int = Field(default=128, ge=8, le=4096)
    state_delta_limit: int = Field(default=128, ge=8, le=4096)
    event_journal_limit: int = Field(default=256, ge=16, le=8192)
    subscriber_queue_limit: int = Field(default=32, ge=2, le=1024)
    max_delta_paths: int = Field(default=128, ge=16, le=2048)
    max_plan_steps: int = Field(default=4, ge=1, le=8)
    max_actions_per_plan: int = Field(default=8, ge=1, le=16)
    max_plan_wall_seconds: float = Field(default=30.0, gt=0.0, le=600.0)
    max_plan_game_seconds: float = Field(default=12.0, gt=0.0, le=3600.0)
    max_pointer_actions_per_plan: int = Field(default=8, ge=0, le=32)
    max_purchase_actions_per_plan: int = Field(default=1, ge=0, le=8)
    max_native_assisted_actions_per_plan: int = Field(default=0, ge=0, le=8)
    max_consecutive_replans: int = Field(default=3, ge=0, le=20)


class PlannerConfig(ConfigModel):
    kind: Literal["heuristic", "scripted", "openai", "openrouter"] = "heuristic"
    model: str = "gpt-5.6-luna"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "low"
    # The OpenRouter adapter sends this. The OpenAI Responses adapter omits it
    # because reasoning-model temperature support is model-specific.
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    max_output_tokens_base: int = Field(default=4096, ge=512, le=100000)
    max_output_tokens_per_plan_step: int = Field(default=2048, ge=256, le=50000)
    max_output_tokens_ceiling: int = Field(default=12288, ge=768, le=100000)
    max_output_continuations: int = Field(default=2, ge=0, le=8)
    include_screenshot: bool = True
    screenshot_detail: Literal["low", "high", "auto"] = "high"
    # Hosted context authority is token capacity for the exact model, not a
    # local character-spending preference. OpenRouter resolves this from model
    # metadata. Set an override only for providers that expose no equivalent.
    context_window_tokens: int | None = Field(
        default=None,
        ge=10_000,
        le=10_000_000,
    )
    model_metadata_timeout_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=60.0,
    )
    openrouter_model: str = "openai/gpt-5.6-luna"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_provider_sort: Literal["latency", "throughput", "price"] = "latency"
    # OpenRouter drops any provider that cannot honour every parameter sent.
    # That is the right setting only when every parameter is load-bearing; with
    # `reasoning_effort` in the request it silently excludes every non-reasoning
    # model, which is most of the fast ones. Off by default so the model choice
    # is ours rather than a side effect of the request shape.
    openrouter_require_parameters: bool = False

    @model_validator(mode="after")
    def output_token_ceiling_covers_base(self) -> PlannerConfig:
        if self.max_output_tokens_ceiling < self.max_output_tokens_base:
            raise ValueError("max_output_tokens_ceiling must cover max_output_tokens_base")
        return self


class AdvisorConfig(ConfigModel):
    """Bounded, read-only strategic-advisor configuration."""

    enabled: bool = False
    provider: Literal["openrouter"] = "openrouter"
    model: str = "openai/gpt-5.4"
    base_url: str = "https://openrouter.ai/api/v1"
    reasoning_effort: Literal[
        "none", "minimal", "low", "medium", "high", "xhigh", "max"
    ] = "medium"
    timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    max_output_tokens: int = Field(default=2500, ge=512, le=20000)
    max_output_continuations: int = Field(default=2, ge=0, le=8)
    corpus_file: Path = Path("../knowledge/kenshi_strategy_v1.yaml")
    max_calls_per_run: int = Field(default=4, ge=0, le=100)
    cooldown_steps: int = Field(default=12, ge=0, le=1000)
    cadence_steps: int = Field(default=20, ge=1, le=1000)
    stall_repeat_threshold: int = Field(default=3, ge=2, le=20)
    stall_window_actions: int = Field(default=12, ge=2, le=100)
    provider_sort: Literal["latency", "throughput", "price"] = "latency"
    require_parameters: bool = False


class MockConfig(ConfigModel):
    seed: int = 7
    start_location: str = "The Hub"
    start_cats: int = Field(default=180, ge=0)
    # Match native Kenshi telemetry: nutrition reserve, 3.0 full to 0 starving.
    start_hunger: float = Field(default=2.5, ge=0.0, le=3.0)
    start_food_items: int = Field(default=1, ge=0)
    start_first_aid_kits: int = Field(default=1, ge=0)
    minutes_per_wait_second: float = Field(default=1.0, gt=0.0, le=120.0)
    random_events: bool = True


class TelemetryConfig(ConfigModel):
    file: Path
    max_age_seconds: float = Field(default=3.0, gt=0.0, le=300.0)
    read_retries: int = Field(default=3, ge=1, le=20)
    retry_delay_seconds: float = Field(default=0.03, ge=0.0, le=2.0)
    require_protocol_major: int = Field(default=1, ge=0, le=100)


class CaptureConfig(ConfigModel):
    enabled: bool = True
    window_title_contains: str = "Kenshi"
    image_format: Literal["png", "jpeg"] = "png"
    jpeg_quality: int = Field(default=90, ge=20, le=100)


class LaunchConfig(ConfigModel):
    require_steam_logged_on: bool = False
    require_graphics_profile: bool = False
    graphics_profile_file: Path | None = None
    require_dual_display_topology: bool = False
    monitor_gpu_tdr: bool = False
    min_free_physical_memory_mib: int = Field(default=0, ge=0, le=1048576)
    reclaim_wsl_cache_on_low_memory: bool = False
    wsl_cache_reclaim_settle_timeout_seconds: float = Field(
        default=45.0,
        gt=0.0,
        le=180.0,
    )
    wsl_cache_reclaim_poll_seconds: float = Field(
        default=1.0,
        gt=0.0,
        le=10.0,
    )
    post_load_health_seconds: float = Field(default=0.0, ge=0.0, le=600.0)

    @model_validator(mode="after")
    def required_profile_has_a_file(self) -> LaunchConfig:
        if self.require_graphics_profile and self.graphics_profile_file is None:
            raise ValueError(
                "require_graphics_profile needs graphics_profile_file"
            )
        if (
            self.reclaim_wsl_cache_on_low_memory
            and self.min_free_physical_memory_mib == 0
        ):
            raise ValueError(
                "reclaim_wsl_cache_on_low_memory needs "
                "min_free_physical_memory_mib"
            )
        return self


class CameraRecoveryConfig(ConfigModel):
    """Fixed, bounded controller policy for ``recover_camera_view``."""

    candidate_settle_seconds: float = Field(default=0.30, ge=0.0, le=3.0)
    clear_score_threshold: float = Field(default=0.72, ge=0.0, le=1.0)
    anchor_max_distance: float = Field(default=30.0, gt=0.0, le=1000.0)
    max_lower_floors: int = Field(default=2, ge=0, le=2)
    portrait_click_hold_seconds: float = Field(default=0.12, ge=0.0, le=1.0)
    portrait_click_interval_seconds: float = Field(default=0.08, ge=0.0, le=1.0)
    floor_click_hold_seconds: float = Field(default=0.12, ge=0.0, le=1.0)
    zoom_out_key: str = Field(default="end", min_length=1, max_length=32)
    zoom_out_hold_seconds: float = Field(default=0.30, ge=0.0, le=2.0)
    rotate_left_key: str = Field(default="q", min_length=1, max_length=32)
    rotate_right_key: str = Field(default="e", min_length=1, max_length=32)
    orbit_hold_seconds: float = Field(default=0.35, gt=0.0, le=2.0)
    tilt_up_key: str = Field(default="comma", min_length=1, max_length=32)
    tilt_down_key: str = Field(default="period", min_length=1, max_length=32)
    tilt_hold_seconds: float = Field(default=0.35, gt=0.0, le=2.0)


class ControlsConfig(ConfigModel):
    pause_key: str = "space"
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
    # Off by default, and kept only for absolute-mode experiments. Warping the
    # OS cursor is invisible to Kenshi, which tracks its drawn cursor from
    # relative motion, so in relative mode a warp desynchronizes the two and the
    # correction loop then believes it has arrived while Kenshi's cursor sits
    # elsewhere. Cursor traversal is instead kept short by not restoring the
    # cursor between actions.
    relative_pointer_warp_enabled: bool = False
    relative_pointer_warp_threshold_pixels: int = Field(default=24, ge=1, le=500)
    relative_pointer_warp_offset_pixels: int = Field(default=6, ge=1, le=100)
    calibrated_client_width: int | None = Field(default=None, gt=0)
    calibrated_client_height: int | None = Field(default=None, gt=0)
    # Total wall time the semantic approach may advance across bounded pulses
    # before giving up. The pathing order is issued once; this budgets how long
    # the option is allowed to keep walking toward it.
    native_approach_max_seconds: float = Field(default=30.0, gt=0.0, le=120.0)
    # One bounded interval between issuing a native movement order and reading
    # its next terminal evidence. This absorbs the proven two-second macro
    # pulse into the typed native-operation adapter.
    native_movement_pulse_seconds: float = Field(default=2.0, gt=0.0, le=10.0)
    # Mirrors safety.require_paused_between_actions for the live environment.
    # With the world already running, a movement "pulse" - unpause, wait,
    # re-pause - is neither possible nor wanted: the character simply walks.
    require_paused_between_actions: bool = True
    # Kenshi's MyGUI ignores a zero-duration press: an instantaneous down/up
    # moves the cursor but activates nothing. Keep the proven semantic-control
    # hold time as an ordinary controller knob.
    control_activation_hold_seconds: float = Field(default=0.12, ge=0.0, le=1.0)
    # An inventory or shop cell resolves what it holds from the hovered widget,
    # so the pointer has to land and be seen there before the button goes down.
    # A ClickAction moves and presses with only `relative_pointer_settle_seconds`
    # between them - six milliseconds - and six live purchases in a row did
    # nothing at all while the same click with a third of a second in front of
    # it transferred the item every time.
    item_cell_hover_seconds: float = Field(default=0.35, ge=0.0, le=2.0)
    # Key that backs out of an open screen. Coordinate-independent, so it works
    # regardless of resolution or calibration.
    dismiss_screen_key: str = Field(default="escape", min_length=1, max_length=32)
    camera_recovery: CameraRecoveryConfig = Field(default_factory=CameraRecoveryConfig)

    def expected_calibration_identity(self) -> CalibrationIdentity:
        return CalibrationIdentity(
            client_width=self.calibrated_client_width,
            client_height=self.calibrated_client_height,
        )
    startup_continue_control_labels: list[str] = Field(
        default_factory=lambda: ["Continue"],
        min_length=1,
        max_length=8,
    )
    startup_new_game_control_labels: list[str] = Field(
        default_factory=lambda: ["New Game"],
        min_length=1,
        max_length=8,
    )
    startup_begin_control_labels: list[str] = Field(
        default_factory=lambda: ["Begin"],
        min_length=1,
        max_length=8,
    )
    startup_confirm_control_labels: list[str] = Field(
        default_factory=lambda: ["Confirm"],
        min_length=1,
        max_length=8,
    )
    startup_warning_confirm_control_labels: list[str] = Field(
        default_factory=lambda: ["Yes"],
        min_length=1,
        max_length=8,
    )
    # The Game Start picker is a carousel with no stable ordering contract.
    # Traverse by the currently rendered label and stop if the carousel cycles.
    startup_game_start_max_carousel_steps: int = Field(default=64, ge=1, le=256)
    startup_load_control_labels: list[str] = Field(
        default_factory=lambda: ["Load Game", "Load"],
        min_length=1,
        max_length=8,
    )
    startup_save_control_labels: list[str] = Field(
        default_factory=lambda: ["autosave1"],
        min_length=1,
        max_length=8,
    )

    @field_validator("speed_keys")
    @classmethod
    def all_speeds_present(cls, value: dict[int, str]) -> dict[int, str]:
        missing = {1, 2, 3} - set(value)
        if missing:
            raise ValueError(f"speed_keys is missing mappings for: {sorted(missing)}")
        return value

    @model_validator(mode="after")
    def calibrated_client_size_is_complete(self) -> ControlsConfig:
        if (self.calibrated_client_width is None) != (
            self.calibrated_client_height is None
        ):
            raise ValueError(
                "calibrated_client_width and calibrated_client_height must be set together"
            )
        return self

    @field_validator(
        "startup_continue_control_labels",
        "startup_new_game_control_labels",
        "startup_begin_control_labels",
        "startup_confirm_control_labels",
        "startup_warning_confirm_control_labels",
        "startup_load_control_labels",
        "startup_save_control_labels",
    )
    @classmethod
    def startup_control_labels_are_nonempty(
        cls,
        value: list[str],
    ) -> list[str]:
        if any(not label.strip() for label in value):
            raise ValueError("startup control labels must be non-empty")
        return value


class SafetyConfig(ConfigModel):
    live_actions_enabled: bool = False
    emergency_stop_key: str = "f12"
    supervisor_enabled: bool = True
    supervisor_max_sequence_stalls: int = Field(default=3, ge=1, le=100)
    supervisor_sequence_stall_min_age_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=30.0,
    )
    supervisor_pause_timeout_seconds: float = Field(default=2.0, gt=0.0, le=30.0)
    # Treat an unpaused game with nothing in flight as an anomaly. True suits a
    # careful stop-motion run, where time passing unattended means the character
    # can be hurt while the agent thinks. An agent meant to play continuously
    # should set it False: there, an unpaused game is simply Kenshi running.
    require_paused_between_actions: bool = True
    automatic_takeover_enabled: bool = False
    human_control_quiet_seconds: float = Field(default=3.0, ge=0.0, le=300.0)
    takeover_countdown_seconds: float = Field(default=5.0, gt=0.0, le=300.0)
    takeover_poll_seconds: float = Field(default=0.1, gt=0.0, le=5.0)
    max_primitive_actions_per_step: int = Field(default=12, ge=1, le=100)
    # Controller-verified transactions own their full bounded sequence and
    # terminal evidence. Keeping their ceiling separate avoids loosening the
    # ordinary primitive allowance merely to admit camera recovery.
    max_controller_verified_primitive_actions_per_step: int = Field(
        default=15, ge=1, le=100
    )
    max_actions_per_minute: int = Field(default=90, ge=1, le=1000)
    max_wait_seconds: float = Field(default=10.0, ge=0.0, le=60.0)
    block_clicks_when_telemetry_stale: bool = True
    allow_live_unpause_actions: bool = False
    # Spending limits are opt-in. It is a game the operator is choosing to let
    # an agent play, so how freely it trades is a preference, not a safety
    # boundary — null means unlimited. The fences that stop it buying the
    # *wrong* thing (the cell must bind, the tooltip must name this item at this
    # price, the seller must be the one verified trader) are not configurable
    # and always apply.
    max_purchase_price: int | None = Field(default=None, ge=1)
    min_money_after_purchase: int | None = Field(default=None, ge=0)
    max_purchases_per_run: int | None = Field(default=None, ge=0)
    # Task intent, deliberately separate from purchase safety: a food run sets
    # ["[Food]"] so nothing else can be bought, while the generic purchase
    # contract itself stays indifferent to what an item is.
    required_purchase_tooltip_markers: list[str] = Field(default_factory=list, max_length=8)
    allow_action_kinds: list[str] = Field(default_factory=list)


class MemoryConfig(ConfigModel):
    enabled: bool = True
    # Whose memories these are. A profile name is not a campaign identity and
    # neither is a character's display name: both let two unrelated saves share
    # one memory. Live runs must state one or opt into `ephemeral`.
    campaign_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,79}$",
    )
    ephemeral: bool = False
    # Deterministic recall is the only implemented canonical treatment.
    # Semantic retrieval cannot be selected until its bounded, disposable
    # provider path and honest fallback exist end to end.
    retrieval_policy: MemoryRetrievalPolicy = (
        MemoryRetrievalPolicy.DETERMINISTIC
    )
    # Recall is tiered, and each tier has its own budget so that the loudest
    # one cannot eat the others. Open commitments and memories bound to an
    # entity in the fresh current observation are what a plan cannot safely
    # proceed without, so they never compete with general knowledge for a slot.
    max_recalled_memories: int = Field(default=12, ge=0, le=100)
    max_entity_recalled_memories: int = Field(default=8, ge=0, le=100)
    max_commitment_memories: int = Field(default=4, ge=0, le=32)
    max_hypothesis_memories: int = Field(default=2, ge=0, le=32)
    max_fieldbook_projects: int = Field(default=8, ge=0, le=8)
    # Applied to general recall only. A survival constraint is not less
    # important for being unexciting.
    minimum_salience: float = Field(default=0.15, ge=0.0, le=1.0)


class AppConfig(ConfigModel):
    version: int = 1
    mode: Literal["mock", "live", "replay"] = "mock"
    control: ControlConfig = Field(default_factory=ControlConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    paths: PathsConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    planner: PlannerConfig = Field(default_factory=PlannerConfig)
    advisor: AdvisorConfig = Field(default_factory=AdvisorConfig)
    mock: MockConfig = Field(default_factory=MockConfig)
    telemetry: TelemetryConfig
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
    launch: LaunchConfig = Field(default_factory=LaunchConfig)
    controls: ControlsConfig = Field(default_factory=ControlsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

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
            "launch": config.launch.model_copy(
                update={
                    "graphics_profile_file": (
                        _resolve_path(config.launch.graphics_profile_file, base)
                        if config.launch.graphics_profile_file is not None
                        else None
                    )
                }
            ),
            "advisor": config.advisor.model_copy(
                update={"corpus_file": _resolve_path(config.advisor.corpus_file, base)}
            ),
        }
    )
