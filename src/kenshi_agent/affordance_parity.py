"""Game-derived affordance inventories and explicit wiring decisions.

An affordance denominator must come from Kenshi, not from the actions this
project already happens to expose. ``controls.cfg`` is the first such source:
it is authoritative for named input bindings, though not for widgets or world
context actions. Other game-derived sources can join this module without being
collapsed into a single pretend-complete number.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

CONTROLS_SNAPSHOT = (
    Path(__file__).resolve().parents[2] / "game_sources" / "kenshi" / "controls.cfg"
)


@dataclass(frozen=True, slots=True)
class ControlBinding:
    name: str
    inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ControlSource:
    version: int
    bindings: tuple[ControlBinding, ...]
    sha256: str

    @property
    def names(self) -> frozenset[str]:
        return frozenset(binding.name for binding in self.bindings)


class BindingStatus(StrEnum):
    WIRED = "wired"
    EXEMPT = "exempt"
    MISSING = "missing"


class ExemptionKind(StrEnum):
    """Why a player affordance is deliberately not reachable.

    Every member must name a constraint of *this system*. The categories this
    replaces were `safety` and `debug_only`, and both were used to dress
    preference as constraint: `quicksave` was withheld because "unattended input
    may not overwrite persistent saves" on saves the project itself designates
    disposable, and the editor bindings because they are "outside ordinary
    player control" on a machine that exists to develop against this game. Two
    different agents produced that same list independently, inherited from a
    docstring, which is why the fix is to make the bad reasoning unexpressible
    rather than to ask for more care.

    An exemption is not a place to record that something looks unwise. If the
    only objection is judgement about what the agent ought to want, it belongs
    in the queue and the operator decides.
    """

    # The runtime already provides this affordance by a better-attributed route,
    # so wiring the game's version would add a second unattributable path.
    SUPERSEDED = "superseded"
    # Its effect reaches outside Kenshi, so no game observation could confirm or
    # bound it.
    HOST_EFFECT = "host_effect"
    # Nothing in telemetry changes when it fires, so no causal terminal exists
    # and success could only ever be assumed.
    NO_OBSERVABLE_EFFECT = "no_observable_effect"


@dataclass(frozen=True, slots=True)
class AffordanceRoute:
    action_kind: str
    argument: str | None = None


@dataclass(frozen=True, slots=True)
class BindingDecision:
    status: BindingStatus
    route: AffordanceRoute | None = None
    exemption: ExemptionKind | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class BindingParityReport:
    source: ControlSource
    decisions: dict[str, BindingDecision]

    def unclassified(self) -> tuple[str, ...]:
        return tuple(sorted(self.source.names - self.decisions.keys()))

    def stale_decisions(self) -> tuple[str, ...]:
        return tuple(sorted(self.decisions.keys() - self.source.names))

    def with_status(self, status: BindingStatus) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name, decision in self.decisions.items()
                if name in self.source.names and decision.status is status
            )
        )

    def decision_errors(self) -> tuple[str, ...]:
        """Invalid claims, including a wired route absent from real code."""

        from .action_contracts import ACTION_CONTRACTS
        from .models import (
            PLANNER_CONTROL_ACTION_KINDS,
            TIME_GAME_BINDINGS,
            GameBinding,
        )

        errors: list[str] = []
        planner_routes = {
            kind
            for kind, contract in ACTION_CONTRACTS.items()
            if contract.planner_visible
        } | set(PLANNER_CONTROL_ACTION_KINDS)
        for name in sorted(self.source.names & self.decisions.keys()):
            decision = self.decisions[name]
            if decision.status is BindingStatus.WIRED:
                if decision.route is None:
                    errors.append(f"{name}: wired without a route")
                    continue
                if decision.exemption is not None:
                    errors.append(f"{name}: wired with an exemption")
                if decision.route.action_kind not in planner_routes:
                    errors.append(
                        f"{name}: route {decision.route.action_kind!r} "
                        "is not planner-visible"
                    )
                if decision.route.action_kind == "use_game_binding":
                    try:
                        binding = GameBinding(name)
                    except ValueError:
                        errors.append(f"{name}: no matching GameBinding enum member")
                    else:
                        if binding in TIME_GAME_BINDINGS:
                            errors.append(
                                f"{name}: raw time binding is not planner-authorable"
                            )
                    if decision.route.argument != name:
                        errors.append(
                            f"{name}: use_game_binding route does not name itself"
                        )
            elif decision.status is BindingStatus.EXEMPT:
                if decision.route is not None:
                    errors.append(f"{name}: exempt entry has a route")
                if decision.exemption is None:
                    errors.append(f"{name}: exemption has no typed kind")
                if not decision.reason:
                    errors.append(f"{name}: exemption has no reason")
            else:
                if decision.route is not None or decision.exemption is not None:
                    errors.append(f"{name}: missing entry claims a route or exemption")
                if not decision.reason:
                    errors.append(f"{name}: missing entry has no queue description")
        return tuple(errors)

    def as_lines(self) -> list[str]:
        wired = self.with_status(BindingStatus.WIRED)
        exempt = self.with_status(BindingStatus.EXEMPT)
        missing = self.with_status(BindingStatus.MISSING)
        lines = [
            f"source      controls.cfg v{self.source.version}",
            f"source sha  {self.source.sha256}",
            f"enumerated  {len(self.source.bindings):3d}",
            f"wired       {len(wired):3d}",
            f"exempt      {len(exempt):3d}",
            f"missing     {len(missing):3d}",
            f"unclassified {len(self.unclassified()):2d}",
            "",
            "MISSING — implementation queue",
        ]
        for name in missing:
            binding = next(item for item in self.source.bindings if item.name == name)
            lines.append(
                f"  {name:24s} [{', '.join(binding.inputs)}]  "
                f"{self.decisions[name].reason}"
            )
        lines.extend(["", "EXEMPT — deliberate non-affordances"])
        for name in exempt:
            decision = self.decisions[name]
            assert decision.exemption is not None
            lines.append(
                f"  {name:24s} [{decision.exemption.value}]  {decision.reason}"
            )
        lines.extend(["", "WIRED — real planner routes"])
        for name in wired:
            route = self.decisions[name].route
            assert route is not None
            detail = route.action_kind
            if route.argument is not None:
                detail += f"({route.argument})"
            lines.append(f"  {name:24s} {detail}")
        return lines


def parse_controls_cfg(text: str) -> ControlSource:
    """Parse Kenshi's version line and preserve every alternate binding."""

    lines = [
        line.strip()
        for line in text.removeprefix("\ufeff").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError("controls.cfg is empty")
    try:
        version = int(lines[0])
    except ValueError as exc:
        raise ValueError("controls.cfg must start with an integer version") from exc

    inputs_by_name: dict[str, list[str]] = {}
    for line in lines[1:]:
        if "=" not in line:
            raise ValueError(f"controls.cfg row has no '=' separator: {line!r}")
        name, input_name = line.split("=", 1)
        if not name or not input_name:
            raise ValueError(f"controls.cfg row has an empty side: {line!r}")
        inputs_by_name.setdefault(name, []).append(input_name)

    bindings = tuple(
        ControlBinding(name=name, inputs=tuple(inputs))
        for name, inputs in inputs_by_name.items()
    )
    return ControlSource(
        version=version,
        bindings=bindings,
        sha256=sha256(text.encode("utf-8")).hexdigest(),
    )


def load_controls_cfg(path: Path = CONTROLS_SNAPSHOT) -> ControlSource:
    return parse_controls_cfg(path.read_text(encoding="utf-8"))


def _wired_route(name: str) -> BindingDecision:
    return BindingDecision(
        status=BindingStatus.WIRED,
        route=AffordanceRoute("use_game_binding", name),
    )


def _missing(reason: str) -> BindingDecision:
    return BindingDecision(status=BindingStatus.MISSING, reason=reason)


_WIRED_GAME_BINDINGS = frozenset(
    {
        "build_apply",
        "build_move_down",
        "build_move_up",
        "build_rotate_left",
        "build_rotate_right",
        "build_tilt_decrease",
        "build_tilt_increase",
        "build_undo",
        "camera_back",
        "camera_forward",
        "camera_left",
        "camera_right",
        "camera_rotate_left",
        "camera_rotate_right",
        "camera_tilt+",
        "camera_tilt-",
        "camera_zoom_in",
        "camera_zoom_out",
        "cycle_run_speed",
        "editor_delete",
        "editor_toggle",
        "floor_down",
        "floor_up",
        "gizmo_move",
        "gizmo_rotate",
        "gizmo_scale",
        "highlight",
        "medic",
        "change_squad",
        "character_next",
        "character_prev",
        "focus_char",
        "select_all",
        "stop_movement",
        "toggle_crafting",
        "toggle_help",
        "toggle_inventory",
        "toggle_map",
        "toggle_research",
        "toggle_stats",
    }
)

_MISSING_GROUPS: dict[str, frozenset[str]] = {
    (
        "Save-state control is wanted, not refused: it lets a run checkpoint "
        "before a risky experiment and recover from death without a human. "
        "Blocked on the world store treating a load as a session boundary "
        "rather than a revision regression, which it currently rejects."
    ): frozenset({"quicksave", "quickload"}),
    (
        "World-editor and world-data control on a development host. Not "
        "ordinary play, but the operator's own machine and the operator's "
        "call; rebuild_navmesh may even answer the movement_stalled failures "
        "that ended live-trade-surface-20260729-r1."
    ): frozenset(
        {
            "rebuild_navmesh",
            "reload_biomes",
        }
    ),
    (
        "Construction and placement have no observed build state or "
        "contracted semantic action."
    ): frozenset(
        {
            "toggle_build",
        }
    ),
    (
        "This camera or locomotion mode is not exposed by a planner-visible "
        "semantic route."
    ): frozenset(
        {
            "toggle_fps_camera",
        }
    ),
    "No contracted squad-care action exposes this order.": frozenset({"rescue"}),
    "No planner route selects this exact squad group.": frozenset(
        {f"select_{index}" for index in range(10)}
    ),
    "No contracted action exposes this combat or AI stance.": frozenset(
        {
            "toggle_bar",
            "toggle_block",
            "toggle_hold",
            "toggle_passive",
            "toggle_ranged",
            "toggle_sneak",
            "toggle_taunt",
        }
    ),
}


def _binding_decisions() -> dict[str, BindingDecision]:
    decisions = {name: _wired_route(name) for name in _WIRED_GAME_BINDINGS}
    decisions.update(
        {
            "pause": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("pause"),
            ),
            "speed_1": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("set_speed", "1"),
            ),
            "speed_2": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("set_speed", "2"),
            ),
            "speed_3": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("set_speed", "3"),
            ),
            "mouse_command": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("command_world_target"),
            ),
            "mouse_rotate": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("rotate_camera"),
            ),
            "mouse_select": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("select_squad_member"),
            ),
            "screenshot": BindingDecision(
                status=BindingStatus.EXEMPT,
                exemption=ExemptionKind.SUPERSEDED,
                reason="The observation pipeline captures attributable game frames directly.",
            ),
        }
    )
    for reason, names in _MISSING_GROUPS.items():
        decisions.update({name: _missing(reason) for name in names})
    return decisions


BINDING_DECISIONS = _binding_decisions()


def audit_binding_parity(
    source: ControlSource | None = None,
    decisions: dict[str, BindingDecision] = BINDING_DECISIONS,
) -> BindingParityReport:
    return BindingParityReport(
        source=source or load_controls_cfg(),
        decisions=decisions,
    )
