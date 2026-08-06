from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class DisplayLeaseError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DisplayScreen:
    device_name: str
    width: int
    height: int
    primary: bool


@dataclass(frozen=True, slots=True)
class DisplayTopology:
    screens: tuple[DisplayScreen, ...]
    internal_connected: bool
    external_connected: bool


_INTERNAL_OUTPUT_TECHNOLOGIES = {
    6,  # LVDS
    11,  # embedded DisplayPort
    13,  # embedded UDI
    0x80000000,  # internal panel
}

_DISPLAY_QUERY = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
$screens = @(
  [System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
    [pscustomobject]@{
      device_name = $_.DeviceName
      width = $_.Bounds.Width
      height = $_.Bounds.Height
      primary = $_.Primary
    }
  }
)
$connections = @(
  Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams |
    Where-Object { $_.Active } |
    ForEach-Object {
      [pscustomobject]@{
        technology = [uint32]$_.VideoOutputTechnology
      }
    }
)
[pscustomobject]@{
  screens = $screens
  technologies = @($connections | ForEach-Object { $_.technology })
} | ConvertTo-Json -Depth 4 -Compress
""".strip()


def _hidden_process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def query_windows_display_topology() -> DisplayTopology:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _DISPLAY_QUERY,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="strict",
        timeout=15.0,
        creationflags=_hidden_process_flags(),
    )
    payload: dict[str, Any] = json.loads(result.stdout)
    raw_screens = payload.get("screens")
    raw_technologies = payload.get("technologies")
    if not isinstance(raw_screens, list) or not isinstance(raw_technologies, list):
        raise DisplayLeaseError("Windows returned an invalid display-topology payload.")
    screens = tuple(
        DisplayScreen(
            device_name=str(screen["device_name"]),
            width=int(screen["width"]),
            height=int(screen["height"]),
            primary=bool(screen["primary"]),
        )
        for screen in raw_screens
    )
    technologies = tuple(int(value) for value in raw_technologies)
    internal_connected = any(
        technology in _INTERNAL_OUTPUT_TECHNOLOGIES
        for technology in technologies
    )
    external_connected = any(
        technology not in _INTERNAL_OUTPUT_TECHNOLOGIES
        for technology in technologies
    )
    return DisplayTopology(
        screens=screens,
        internal_connected=internal_connected,
        external_connected=external_connected,
    )


def switch_windows_display_topology(mode: str) -> None:
    if mode not in {"external", "extend"}:
        raise ValueError(f"Unsupported Windows display topology: {mode!r}")
    subprocess.run(
        ["DisplaySwitch.exe", f"/{mode}"],
        check=True,
        capture_output=True,
        timeout=15.0,
        creationflags=_hidden_process_flags(),
    )


class DisplayTopologyController:
    def __init__(
        self,
        *,
        query_state: Callable[[], DisplayTopology] = query_windows_display_topology,
        switch_topology: Callable[[str], None] = switch_windows_display_topology,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = 15.0,
        poll_interval_seconds: float = 0.25,
    ) -> None:
        self._query_state = query_state
        self._switch_topology = switch_topology
        self._sleep = sleep
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    @staticmethod
    def _has_1080p_screen(state: DisplayTopology) -> bool:
        return any(
            (screen.width, screen.height) == (1920, 1080)
            for screen in state.screens
        )

    def validate_ready(self) -> DisplayTopology:
        state = self._query_state()
        if (
            len(state.screens) == 1
            and state.external_connected
            and self._has_1080p_screen(state)
        ):
            # Already exactly where the lease wants to be. Requiring an active
            # internal panel first turned a correct operator setup into a
            # precondition failure, which is the only route left when
            # `DisplaySwitch.exe /external` is being ignored by the driver -
            # observed doing nothing for ten seconds while both screens stayed
            # up. The switch below is then a no-op and the wait returns at once.
            return state
        if (
            len(state.screens) < 2
            or not state.internal_connected
            or not state.external_connected
            or not self._has_1080p_screen(state)
        ):
            raise DisplayLeaseError(
                "Calibrated live mode requires an active internal panel and an "
                "external 1920x1080 display before it can switch safely."
            )
        return state

    def _wait_for(
        self,
        predicate: Callable[[DisplayTopology], bool],
        failure: str,
    ) -> DisplayTopology:
        deadline = time.monotonic() + self._timeout_seconds
        while True:
            state = self._query_state()
            if predicate(state):
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._poll_interval_seconds >= remaining:
                raise DisplayLeaseError(failure)
            self._sleep(self._poll_interval_seconds)

    def enable_external_only(self) -> DisplayTopology:
        self._switch_topology("external")
        return self._wait_for(
            lambda state: (
                len(state.screens) == 1
                and state.external_connected
                and self._has_1080p_screen(state)
            ),
            (
                "Windows did not reach verified external-only 1920x1080 mode; "
                "the live command was not started."
            ),
        )

    def restore_extended(self) -> DisplayTopology:
        self._switch_topology("extend")
        return self._wait_for(
            lambda state: (
                len(state.screens) >= 2
                and state.internal_connected
                and state.external_connected
                and self._has_1080p_screen(state)
            ),
            (
                "Windows did not restore the internal panel. Use Win+P, then "
                "choose Extend."
            ),
        )

    def restore_if_stranded(self) -> tuple[DisplayTopology, bool]:
        """Idempotently recover the exact external-only topology leased here."""

        state = self._query_state()
        if (
            len(state.screens) == 1
            and state.internal_connected
            and state.external_connected
            and self._has_1080p_screen(state)
        ):
            return self.restore_extended(), True
        return state, False


@contextmanager
def external_display_lease(
    controller: DisplayTopologyController,
) -> Iterator[None]:
    controller.validate_ready()
    switch_requested = False
    try:
        switch_requested = True
        controller.enable_external_only()
        print("Display lease active: internal panel off; external 1920x1080 only.")
        yield
    finally:
        if switch_requested:
            controller.restore_extended()
            print("Display lease released: internal panel on; extended mode restored.")
