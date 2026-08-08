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

from ..affordances import AFFORDANCE_ADAPTERS
from ..core.operation import GameBinding
from ..core.planning import GAME_BINDING_TERMINALS, UNWITNESSED_BINDINGS

CONTROLS_SNAPSHOT = Path(__file__).resolve().parents[3] / "game_sources" / "kenshi" / "controls.cfg"


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
    adapter: str
    semantic: str | None = None


@dataclass(frozen=True, slots=True)
class BindingDecision:
    status: BindingStatus
    route: AffordanceRoute | None = None
    exemption: ExemptionKind | None = None
    reason: str = ""


def _witnessed_names() -> frozenset[str]:
    return frozenset(binding.value for binding in GAME_BINDING_TERMINALS)


def _unwitnessed_reasons() -> dict[str, str]:
    return {binding.value: reason for binding, reason in UNWITNESSED_BINDINGS.items()}


def _binding_or_none(name: str) -> object | None:
    """The enum member for a binding name, or None when Kenshi names no model."""

    try:
        return GameBinding(name)
    except ValueError:
        return None


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

        errors: list[str] = []
        adapter_names = {adapter.name for adapter in AFFORDANCE_ADAPTERS}
        for name in sorted(self.source.names & self.decisions.keys()):
            decision = self.decisions[name]
            if decision.status is BindingStatus.WIRED:
                if decision.route is None:
                    errors.append(f"{name}: wired without a route")
                    continue
                if decision.exemption is not None:
                    errors.append(f"{name}: wired with an exemption")
                if decision.route.adapter not in adapter_names:
                    errors.append(
                        f"{name}: route adapter {decision.route.adapter!r} is not registered"
                    )
                if decision.route.adapter == "game_bindings":
                    try:
                        GameBinding(name)
                    except ValueError:
                        errors.append(f"{name}: no matching GameBinding enum member")
                    if decision.route.semantic != name:
                        errors.append(f"{name}: game-binding route does not name itself")
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

    def witnessed(self) -> tuple[str, ...]:
        """Wired bindings a later observation can actually prove landed."""

        return tuple(
            name for name in self.with_status(BindingStatus.WIRED) if name in _witnessed_names()
        )

    def unwitnessed(self) -> tuple[str, ...]:
        """Wired bindings whose gameplay effect has no observable terminal."""

        return tuple(
            name for name in self.with_status(BindingStatus.WIRED) if name in _unwitnessed_reasons()
        )

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
            # Wired means the binding is reachable through its source adapter.
            # Witnessed bindings have effect-level completion. Unwitnessed ones
            # preserve the narrower delivery boundary instead of pretending a
            # resulting gameplay effect was observed.
            f"witnessed   {len(self.witnessed()):3d}  (wired AND has an "
            "observable completion terminal)",
            f"unwitnessed {len(self.unwitnessed()):3d}  (wired but no "
            "observation proves it landed)",
            "",
            "UNWITNESSED — offered at delivery boundary; gameplay effect unproved",
        ]
        reasons = _unwitnessed_reasons()
        grouped: dict[str, list[str]] = {}
        for name in self.unwitnessed():
            grouped.setdefault(reasons[name], []).append(name)
        for reason, names in sorted(grouped.items()):
            lines.append(f"  {len(names):2d}  {reason}")
            lines.append(f"      {', '.join(sorted(names))}")
        if missing:
            lines.extend(["", "MISSING — implementation queue"])
        for name in missing:
            binding = next(item for item in self.source.bindings if item.name == name)
            lines.append(
                f"  {name:24s} [{', '.join(binding.inputs)}]  {self.decisions[name].reason}"
            )
        lines.extend(["", "EXEMPT — deliberate non-affordances"])
        for name in exempt:
            decision = self.decisions[name]
            assert decision.exemption is not None
            lines.append(f"  {name:24s} [{decision.exemption.value}]  {decision.reason}")
        lines.extend(["", "WIRED — affordance adapter or runtime-owned routes"])
        for name in wired:
            route = self.decisions[name].route
            assert route is not None
            detail = route.adapter
            if route.semantic is not None:
                detail += f"({route.semantic})"
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
        ControlBinding(name=name, inputs=tuple(inputs)) for name, inputs in inputs_by_name.items()
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
        route=AffordanceRoute("game_bindings", name),
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
        "rescue",
        "quickload",
        "quicksave",
        "rebuild_navmesh",
        "reload_biomes",
        "change_squad",
        "character_next",
        "character_prev",
        "select_0",
        "select_1",
        "select_2",
        "select_3",
        "select_4",
        "select_5",
        "select_6",
        "select_7",
        "select_8",
        "select_9",
        "focus_char",
        "select_all",
        "stop_movement",
        "toggle_crafting",
        "toggle_help",
        "toggle_inventory",
        "toggle_bar",
        "toggle_block",
        "toggle_build",
        "toggle_fps_camera",
        "toggle_hold",
        "toggle_map",
        "toggle_passive",
        "toggle_ranged",
        "toggle_research",
        "toggle_sneak",
        "toggle_stats",
        "toggle_taunt",
    }
)

_MISSING_GROUPS: dict[str, frozenset[str]] = {}


def _binding_decisions() -> dict[str, BindingDecision]:
    decisions = {name: _wired_route(name) for name in _WIRED_GAME_BINDINGS}
    decisions.update(
        {
            "toggle_inventory": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("screens", "open_inventory"),
            ),
            "toggle_stats": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("screens", "open_stats"),
            ),
            "toggle_map": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("screens", "open_map"),
            ),
            "toggle_research": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("screens", "open_research"),
            ),
            "toggle_crafting": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("screens", "open_crafting"),
            ),
            "camera_rotate_left": BindingDecision(
                status=BindingStatus.MISSING,
                reason=(
                    "The closure pass retired planner-visible camera rotation; "
                    "no surviving operation owns this binding."
                ),
            ),
            "camera_rotate_right": BindingDecision(
                status=BindingStatus.MISSING,
                reason=(
                    "The closure pass retired planner-visible camera rotation; "
                    "no surviving operation owns this binding."
                ),
            ),
            "pause": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("runtime", "playback_pause"),
            ),
            "speed_1": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("runtime", "playback_speed_1"),
            ),
            "speed_2": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("runtime", "playback_speed_2"),
            ),
            "speed_3": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("runtime", "playback_speed_3"),
            ),
            "mouse_command": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("context_orders"),
            ),
            "mouse_rotate": BindingDecision(
                status=BindingStatus.MISSING,
                reason=(
                    "The closure pass retired planner-visible camera rotation; "
                    "no surviving operation owns this binding."
                ),
            ),
            "mouse_select": BindingDecision(
                status=BindingStatus.WIRED,
                route=AffordanceRoute("characters", "select"),
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
