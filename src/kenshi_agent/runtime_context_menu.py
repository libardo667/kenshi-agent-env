"""Cross-layer invariants for game-owned runtime context-menu evidence."""

from __future__ import annotations

from collections.abc import Collection

RUNTIME_CONTEXT_MENU_CAPABILITY = "ui.context_menu.orders"
STABLE_HANDLE_CAPABILITY = "identity.stable_handles"


def context_menu_state_is_consistent(
    *,
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    has_context_menu: bool,
) -> bool:
    """Whether open/probe/payload describe one possible observation.

    A missing probe is backward-compatible only when no new payload is present.
    Every populated probe is exact: captured owns a payload, closed owns a
    confirmed false, and failure probes own an open menu with no payload.
    """

    if context_menu_probe is None:
        return not has_context_menu
    if context_menu_probe == "captured":
        return context_menu_open is True and has_context_menu
    if context_menu_probe == "closed":
        return context_menu_open is False and not has_context_menu
    return context_menu_open is True and not has_context_menu


def context_menu_capability_is_consistent(
    *,
    capabilities: Collection[str],
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    has_context_menu: bool,
) -> bool:
    """Whether the advertised capability is backed by a complete envelope."""

    if RUNTIME_CONTEXT_MENU_CAPABILITY not in capabilities:
        return True
    if context_menu_open is None or context_menu_probe is None:
        return False
    return not has_context_menu or STABLE_HANDLE_CAPABILITY in capabilities


def require_consistent_context_menu_state(
    *,
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    context_menu: object | None,
) -> None:
    """Reject a context-menu envelope that cannot be one game observation."""

    if not context_menu_state_is_consistent(
        context_menu_open=context_menu_open,
        context_menu_probe=context_menu_probe,
        has_context_menu=context_menu is not None,
    ):
        raise ValueError("context menu open, probe, and payload are inconsistent")


def require_truthful_context_menu_capability(
    *,
    capabilities: Collection[str],
    context_menu_open: bool | None,
    context_menu_probe: str | None,
    context_menu: object | None,
) -> None:
    """Reject advertised capture authority without its required evidence."""

    if context_menu_capability_is_consistent(
        capabilities=capabilities,
        context_menu_open=context_menu_open,
        context_menu_probe=context_menu_probe,
        has_context_menu=context_menu is not None,
    ):
        return
    if (
        RUNTIME_CONTEXT_MENU_CAPABILITY in capabilities
        and context_menu is not None
        and STABLE_HANDLE_CAPABILITY not in capabilities
    ):
        raise ValueError("runtime context menu targets require identity.stable_handles")
    raise ValueError("ui.context_menu.orders requires context menu open and probe state")
