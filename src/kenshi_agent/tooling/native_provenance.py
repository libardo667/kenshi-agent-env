"""Tie the native artifact in use back to the source and protocol it implements.

The plug-in is the one component the portable gate cannot compile, run, or
verify: it is built by MSVC on Windows and loaded by a game the test suite never
starts. Everything else in this repository can be reproduced from a fresh
checkout, so the DLL is where a story can quietly stop being true - a build from
older source, a stale copy in the game's mod folder, a protocol the Python side
no longer speaks - and nothing would say so until a live run behaved strangely.

This checks the chain rather than asserting it:

  source .cpp  ->  declared PROTOCOL_VERSION
  built DLL    ->  contains that version string, hashes to X
  installed DLL->  hashes to X as well
  capabilities ->  the generated header, the manifest it came from, and the
                   strings actually compiled into the binary agree

Every link is read from the artefact itself. A hash equality proves the
installed file is the built file; finding the version and capability strings
inside the binary proves the build carried the source's declarations. What it
cannot prove is that the built DLL came from *this* checkout's source rather
than an identical-looking one, so the source hash is recorded alongside for a
human to compare, not silently trusted.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "native" / "KenshiAgentTelemetry" / "KenshiAgentTelemetry.cpp"
CAPABILITY_MANIFEST = ROOT / "native" / "KenshiAgentTelemetry" / "GameplayCapabilities.json"
GENERATED_HEADER = (
    ROOT / "native" / "KenshiAgentTelemetry" / "GameplayCapabilities.generated.h"
)

PROTOCOL_PATTERN = re.compile(
    r'const\s+char\s*\*\s*PROTOCOL_VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"'
)


@dataclass(frozen=True, slots=True)
class ArtifactCheck:
    """One link in the chain, with what was actually observed."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class NativeProvenance:
    declared_protocol: str
    source_sha256: str
    built_path: Path | None
    built_sha256: str | None
    installed_path: Path | None
    installed_sha256: str | None
    checks: tuple[ArtifactCheck, ...] = field(default_factory=tuple)

    @property
    def consistent(self) -> bool:
        return all(check.ok for check in self.checks)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def declared_protocol_version(source: Path = SOURCE) -> str:
    """The protocol the source says it speaks."""

    match = PROTOCOL_PATTERN.search(source.read_text(encoding="utf-8", errors="replace"))
    if match is None:
        raise ValueError(f"no PROTOCOL_VERSION declaration found in {source}")
    return match.group(1)


def binary_strings(path: Path) -> set[str]:
    """Printable strings compiled into a binary.

    Uses `strings` when available and falls back to an in-process scan, so the
    check does not silently pass on a machine lacking binutils.
    """

    try:
        result = subprocess.run(
            ["strings", "-a", str(path)],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120.0,
        )
        if result.returncode == 0:
            return set(result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        pass
    data = path.read_bytes()
    return {
        match.decode("ascii", "replace")
        for match in re.findall(rb"[\x20-\x7e]{4,}", data)
    }


def assess_native_provenance(
    *,
    built: Path | None = None,
    installed: Path | None = None,
    source: Path = SOURCE,
) -> NativeProvenance:
    """Check the artefact chain from source to the DLL Kenshi actually loads."""

    declared = declared_protocol_version(source)
    checks: list[ArtifactCheck] = []

    built_hash = _sha256(built) if built is not None and built.exists() else None
    installed_hash = (
        _sha256(installed) if installed is not None and installed.exists() else None
    )

    if installed_hash is None:
        checks.append(
            ArtifactCheck("installed artefact present", False, "no installed DLL found")
        )
    else:
        assert installed is not None
        compiled = binary_strings(installed)
        checks.append(
            ArtifactCheck(
                "installed DLL declares the source's protocol",
                declared in compiled,
                f"source declares {declared}; "
                + (
                    "found in binary"
                    if declared in compiled
                    else "NOT found in binary - the DLL was built from other source"
                ),
            )
        )
        manifest_caps = _manifest_capabilities()
        missing = sorted(manifest_caps - compiled)
        checks.append(
            ArtifactCheck(
                "installed DLL carries every advertised capability",
                not missing,
                f"{len(manifest_caps)} declared"
                + (f"; MISSING from binary: {missing}" if missing else "; all present"),
            )
        )

    if built_hash is not None and installed_hash is not None:
        checks.append(
            ArtifactCheck(
                "installed DLL is the built DLL",
                built_hash == installed_hash,
                "hashes match"
                if built_hash == installed_hash
                else f"built {built_hash[:16]} != installed {installed_hash[:16]}",
            )
        )
    elif installed_hash is not None:
        checks.append(
            ArtifactCheck(
                "installed DLL is the built DLL",
                False,
                "no build output to compare against; the installed DLL's origin "
                "is unverified",
            )
        )

    checks.append(_capability_header_check())

    return NativeProvenance(
        declared_protocol=declared,
        source_sha256=_sha256(source),
        built_path=built,
        built_sha256=built_hash,
        installed_path=installed,
        installed_sha256=installed_hash,
        checks=tuple(checks),
    )


def _manifest_capabilities() -> set[str]:
    import json

    data = json.loads(CAPABILITY_MANIFEST.read_text(encoding="utf-8"))
    names: set[str] = set(data.get("always", []))
    for entry in data.get("conditional", []) or []:
        if isinstance(entry, str):
            names.add(entry)
        elif isinstance(entry, dict) and "name" in entry:
            names.add(str(entry["name"]))
    return names


def _capability_header_check() -> ArtifactCheck:
    """The generated header must still match the manifest it came from."""

    if not GENERATED_HEADER.exists():
        return ArtifactCheck("generated header matches manifest", False, "header missing")
    header = GENERATED_HEADER.read_text(encoding="utf-8", errors="replace")
    missing = sorted(name for name in _manifest_capabilities() if f'"{name}"' not in header)
    return ArtifactCheck(
        "generated header matches manifest",
        not missing,
        "header is current" if not missing else f"header is stale; missing {missing}",
    )


def render_native_provenance(provenance: NativeProvenance) -> list[str]:
    lines = [
        f"declared protocol     {provenance.declared_protocol}",
        f"source sha256         {provenance.source_sha256}",
        f"built sha256          {provenance.built_sha256 or 'absent'}",
        f"installed sha256      {provenance.installed_sha256 or 'absent'}",
        f"chain consistent      {'YES' if provenance.consistent else 'NO'}",
        "",
        "CHECKS",
    ]
    for check in provenance.checks:
        lines.append(f"  [{'ok' if check.ok else 'XX'}] {check.name}")
        lines.append(f"       {check.detail}")
    return lines
