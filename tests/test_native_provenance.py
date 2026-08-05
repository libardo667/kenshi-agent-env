"""The native artefact is the one link the portable gate cannot rebuild.

Everything else here reproduces from a fresh checkout. The DLL is built by MSVC
on Windows and loaded by a game the suite never starts, so it is where the story
can quietly stop being true: a build from older source, a stale copy in the mod
folder, a protocol the Python side no longer speaks. Nothing said so until a
live run behaved strangely.

These tests use synthetic binaries so they run anywhere, including a fresh
checkout with no Kenshi installation.
"""

from __future__ import annotations

from pathlib import Path

from kenshi_agent.tooling.native_provenance import (
    SOURCE,
    assess_native_provenance,
    binary_strings,
    declared_protocol_version,
    render_native_provenance,
)


def _fake_dll(path: Path, strings: list[str]) -> Path:
    """A binary carrying exactly the given printable strings."""

    payload = b"\x00\x01\x02" + b"\x00".join(s.encode("ascii") for s in strings) + b"\x00\xff"
    path.write_bytes(payload)
    return path


def _capabilities() -> set[str]:
    from kenshi_agent.tooling.native_provenance import _manifest_capabilities

    return _manifest_capabilities()


def test_the_declared_protocol_is_read_from_the_real_source() -> None:
    version = declared_protocol_version()

    assert version.count(".") == 2
    assert SOURCE.exists()


def test_a_matching_pair_reports_a_consistent_chain(tmp_path: Path) -> None:
    version = declared_protocol_version()
    strings = [version, *sorted(_capabilities())]
    built = _fake_dll(tmp_path / "built.dll", strings)
    installed = _fake_dll(tmp_path / "installed.dll", strings)

    provenance = assess_native_provenance(built=built, installed=installed)

    assert provenance.consistent, render_native_provenance(provenance)
    assert provenance.built_sha256 == provenance.installed_sha256


def test_a_stale_install_is_caught_by_hash(tmp_path: Path) -> None:
    """The mod folder holding a different file than the build output."""

    version = declared_protocol_version()
    strings = [version, *sorted(_capabilities())]
    built = _fake_dll(tmp_path / "built.dll", strings)
    installed = _fake_dll(tmp_path / "installed.dll", [*strings, "padding-differs"])

    provenance = assess_native_provenance(built=built, installed=installed)

    assert not provenance.consistent
    failed = [check.name for check in provenance.checks if not check.ok]
    assert "installed DLL is the built DLL" in failed


def test_a_dll_built_from_other_source_is_caught_by_protocol(tmp_path: Path) -> None:
    strings = ["0.0.1-not-the-declared-version", *sorted(_capabilities())]
    dll = _fake_dll(tmp_path / "installed.dll", strings)

    provenance = assess_native_provenance(built=dll, installed=dll)

    assert not provenance.consistent
    failed = [check.name for check in provenance.checks if not check.ok]
    assert "installed DLL declares the source's protocol" in failed


def test_a_capability_the_binary_does_not_implement_is_named(tmp_path: Path) -> None:
    """The real failure this caught: an install predating a new capability."""

    version = declared_protocol_version()
    capabilities = sorted(_capabilities())
    dropped = capabilities[-1]
    dll = _fake_dll(tmp_path / "installed.dll", [version, *capabilities[:-1]])

    provenance = assess_native_provenance(built=dll, installed=dll)

    assert not provenance.consistent
    detail = next(
        check.detail
        for check in provenance.checks
        if check.name == "installed DLL carries every advertised capability"
    )
    assert dropped in detail


def test_a_missing_install_is_reported_rather_than_assumed(tmp_path: Path) -> None:
    provenance = assess_native_provenance(
        built=None, installed=tmp_path / "nothing-here.dll"
    )

    assert not provenance.consistent
    assert provenance.installed_sha256 is None


def test_binary_strings_finds_embedded_text(tmp_path: Path) -> None:
    """Guards the probe against silently returning nothing."""

    dll = _fake_dll(tmp_path / "x.dll", ["control.example_capability", "1.2.3"])

    found = binary_strings(dll)

    assert "control.example_capability" in found
