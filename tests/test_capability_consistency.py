"""The cross-layer evidence axis, as a gate rather than a paragraph.

A capability climbs four rungs. A contract **declares** it in
`required_capabilities`; a producer **advertises** it in a telemetry capability
list; the wire format **serializes** it; and the far side **accepts** it end to
end. The rungs are independent, and the interesting failures live in the gaps
between them.

Commit `8993b10` is the failure this file exists to catch. Protocol 0.8.1
advertised `roster.indoors` and serialized `indoors: true`, and the native command
fence still rejected the matching exit as `not_indoors`. Declared, advertised,
serialized — never accepted. The old vocabulary had no name for that state, so it
was reported as "supported" until a live run said otherwise.

That axis used to live in a prose document. The document drifted and was deleted,
which is what prose does. These checks hold the two rungs a portable test can
reach without a running game:

* *advertised* — every declared capability is emitted by a real producer;
* *accepted* — every native command a contract can dispatch has a pinned wire
  document in the fixture corpus, so its shape is conformance-checked rather
  than discovered live.

The fourth rung, whether the far side honours what it advertised, is only
reachable from a supervised live run. `docs/ADR_EVIDENCE_VOCABULARY_V2.md` records
that it is enforced by review, not by code, and must not be collapsed into
"supported".
"""

from __future__ import annotations

import asyncio
import filecmp
import json
import tempfile
from pathlib import Path

import pytest

from kenshi_agent.config import MockConfig
from kenshi_agent.core.operation import ControlMode
from kenshi_agent.env.mock import MockEnvironment
from kenshi_agent.native_commands import (
    NATIVE_APPROACH_WIRE_COMMAND,
    NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
    NATIVE_CLOSE_INTERFACE_WIRE_COMMAND,
    NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
    NATIVE_DIRECTION_WIRE_COMMAND,
    NATIVE_EXIT_BUILDING_WIRE_COMMAND,
    NATIVE_MAP_TRAVEL_WIRE_COMMAND,
    NATIVE_MOVE_WIRE_COMMAND,
    NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
    NATIVE_RESOURCE_SURVEY_WIRE_COMMAND,
    NATIVE_SHIFT_BODY_WIRE_COMMAND,
    NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
    NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
    NATIVE_TRADE_WINDOW_WIRE_COMMAND,
    NATIVE_TRANSFER_WIRE_COMMAND,
)
from kenshi_agent.operation_definitions import (
    NATIVE_APPROACH_CAPABILITY,
    NATIVE_CHARACTER_ORDER_CAPABILITY,
    NATIVE_CLOSE_INTERFACE_CAPABILITY,
    NATIVE_CONTEXT_ACTION_CAPABILITY,
    NATIVE_DIRECTION_CAPABILITY,
    NATIVE_EXIT_BUILDING_CAPABILITY,
    NATIVE_MAP_TRAVEL_CAPABILITY,
    NATIVE_MOVE_CAPABILITY,
    NATIVE_PRODUCE_RESOURCE_CAPABILITY,
    NATIVE_RESOURCE_SURVEY_CAPABILITY,
    NATIVE_SHIFT_BODY_CAPABILITY,
    NATIVE_SQUAD_REGROUP_CAPABILITY,
    NATIVE_SQUAD_SELECTION_CAPABILITY,
    NATIVE_TRADE_WINDOW_CAPABILITY,
    NATIVE_TRANSFER_CAPABILITY,
    OPERATION_DEFINITIONS,
)
from kenshi_agent.tooling.native_contract_export import (
    export_gameplay_capabilities_header,
    load_gameplay_capabilities,
)

ROOT = Path(__file__).resolve().parents[1]
NATIVE_CAPABILITY_MANIFEST = (
    ROOT / "native" / "KenshiAgentTelemetry" / "GameplayCapabilities.json"
)
GENERATED_NATIVE_CAPABILITIES = (
    ROOT
    / "native"
    / "KenshiAgentTelemetry"
    / "GameplayCapabilities.generated.h"
)
FIXTURES = Path(__file__).parent / "fixtures" / "native_commands"

# Which native wire command each `control.*` capability authorizes. A capability
# is the permission; the command is the thing performed with it.
# `test_every_native_capability_names_its_wire_command` fails if a new native
# capability is added without deciding what it dispatches.
NATIVE_WIRE_COMMANDS: dict[str, str] = {
    NATIVE_APPROACH_CAPABILITY: NATIVE_APPROACH_WIRE_COMMAND,
    NATIVE_MOVE_CAPABILITY: NATIVE_MOVE_WIRE_COMMAND,
    NATIVE_SHIFT_BODY_CAPABILITY: NATIVE_SHIFT_BODY_WIRE_COMMAND,
    NATIVE_DIRECTION_CAPABILITY: NATIVE_DIRECTION_WIRE_COMMAND,
    NATIVE_MAP_TRAVEL_CAPABILITY: NATIVE_MAP_TRAVEL_WIRE_COMMAND,
    NATIVE_EXIT_BUILDING_CAPABILITY: NATIVE_EXIT_BUILDING_WIRE_COMMAND,
    NATIVE_CONTEXT_ACTION_CAPABILITY: NATIVE_CONTEXT_ACTION_WIRE_COMMAND,
    NATIVE_CHARACTER_ORDER_CAPABILITY: NATIVE_CHARACTER_ORDER_WIRE_COMMAND,
    NATIVE_PRODUCE_RESOURCE_CAPABILITY: NATIVE_PRODUCE_RESOURCE_WIRE_COMMAND,
    NATIVE_TRANSFER_CAPABILITY: NATIVE_TRANSFER_WIRE_COMMAND,
    NATIVE_TRADE_WINDOW_CAPABILITY: NATIVE_TRADE_WINDOW_WIRE_COMMAND,
    NATIVE_SQUAD_REGROUP_CAPABILITY: NATIVE_SQUAD_REGROUP_WIRE_COMMAND,
    NATIVE_SQUAD_SELECTION_CAPABILITY: NATIVE_SQUAD_SELECTION_WIRE_COMMAND,
    NATIVE_RESOURCE_SURVEY_CAPABILITY: NATIVE_RESOURCE_SURVEY_WIRE_COMMAND,
    NATIVE_CLOSE_INTERFACE_CAPABILITY: NATIVE_CLOSE_INTERFACE_WIRE_COMMAND,
}

# Contracts the mock cannot yet exercise, because the mock world has no inventory,
# no shop, and no stable native identity to advertise. Each entry is a real hole:
# the contract is reachable in `interface_only`, so a mock run can propose it and
# will always be refused for missing capabilities. Shrink this; never grow it.
# Closing an entry means giving the mock the state the capability names, which is
# a behaviour change and belongs in its own slice.
MOCK_UNEXERCISABLE: dict[str, str] = {}


def _mock_capabilities() -> frozenset[str]:
    """What the mock actually advertises, read from a real observation.

    Read from the environment rather than from `mock.py`'s source, so this is the
    advertised set and not a restatement of the literal that produces it.
    """

    async def observe(run_dir: Path) -> frozenset[str]:
        environment = MockEnvironment(MockConfig(random_events=False), run_dir, "caps")
        try:
            observation = await environment.reset()
        finally:
            await environment.close()
        assert observation.telemetry is not None
        return frozenset(observation.telemetry.capabilities)

    with tempfile.TemporaryDirectory() as run_dir:
        return asyncio.run(observe(Path(run_dir)))


def _native_capabilities() -> frozenset[str]:
    """What the generated native producer can advertise in gameplay."""

    manifest = load_gameplay_capabilities(NATIVE_CAPABILITY_MANIFEST)
    return frozenset((*manifest.always, *manifest.conditional))


def test_generated_native_capabilities_are_not_stale(tmp_path: Path) -> None:
    fresh = export_gameplay_capabilities_header(
        NATIVE_CAPABILITY_MANIFEST,
        tmp_path,
    )

    assert filecmp.cmp(fresh, GENERATED_NATIVE_CAPABILITIES, shallow=False), (
        "GameplayCapabilities.generated.h is stale; run "
        "python scripts/export_native_capabilities.py"
    )


def _fixture_commands() -> frozenset[str]:
    commands = set()
    for path in sorted(FIXTURES.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        command = document.get("command")
        if isinstance(command, str):
            commands.add(command)
    return frozenset(commands)


def test_every_declared_capability_is_advertised_by_a_producer() -> None:
    """Declared but produced by nobody is a contract that can never fire.

    Alias-aware, because a capability renamed in the contract vocabulary is still
    satisfied by a proven DLL that emits the legacy name.
    """

    advertised = _mock_capabilities() | _native_capabilities()
    unproduced = {
        kind: missing
        for kind, contract in sorted(OPERATION_DEFINITIONS.items())
        if (missing := contract.missing_capabilities(advertised))
    }
    assert not unproduced, (
        f"contracts requiring capabilities no producer emits: {unproduced}. "
        "Either a producer must advertise the name, or the contract is declaring "
        "a capability that does not exist."
    )


def test_every_native_capability_names_its_wire_command() -> None:
    declared = {
        name
        for contract in OPERATION_DEFINITIONS.values()
        for name in contract.required_capabilities
        if name.startswith("control.")
    }
    unmapped = sorted(declared - set(NATIVE_WIRE_COMMANDS))
    assert not unmapped, (
        f"native capabilities with no recorded wire command: {unmapped}. A "
        "capability that authorizes nothing nameable cannot be proven accepted."
    )


def test_every_native_wire_command_is_pinned_by_a_fixture() -> None:
    """Serialized is not accepted; a pinned document is how the two stay married.

    The fixtures are shared with the C++ conformance target, so a command with no
    fixture has no agreed wire shape on either side of the boundary — the shape
    is discovered by a live run failing, which is the expensive way to find out.
    """

    pinned = _fixture_commands()
    unpinned = {
        kind: NATIVE_WIRE_COMMANDS[name]
        for kind, contract in sorted(OPERATION_DEFINITIONS.items())
        for name in sorted(contract.required_capabilities)
        if name in NATIVE_WIRE_COMMANDS and NATIVE_WIRE_COMMANDS[name] not in pinned
    }
    assert not unpinned, (
        f"native commands a contract can dispatch with no fixture: {unpinned}. "
        f"Add a golden request under {FIXTURES.relative_to(ROOT).as_posix()}/."
    )


@pytest.mark.parametrize(
    "kind",
    sorted(
        kind
        for kind, contract in OPERATION_DEFINITIONS.items()
        if contract.allows_control_mode(ControlMode.INTERFACE_ONLY)
    ),
)
def test_interface_only_contracts_are_exercisable_in_the_mock(kind: str) -> None:
    """A contract offered off-game must be satisfiable off-game.

    Without this, a contract can be planner-visible in every mock run and refused
    in every mock run, and the suite stays green because nothing ever asked
    whether the deterministic world can produce what the contract needs.
    """

    missing = OPERATION_DEFINITIONS[kind].missing_capabilities(_mock_capabilities())
    if kind in MOCK_UNEXERCISABLE:
        assert missing, (
            f"{kind} is listed in MOCK_UNEXERCISABLE ({MOCK_UNEXERCISABLE[kind]}) "
            "but the mock now advertises everything it needs. Remove the entry."
        )
        pytest.skip(f"{kind}: {MOCK_UNEXERCISABLE[kind]}")
    assert not missing, (
        f"{kind} is offered in interface_only but the mock never advertises "
        f"{missing}, so every mock run refuses it. Advertise it, or record the "
        "gap in MOCK_UNEXERCISABLE with its reason."
    )
