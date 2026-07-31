from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kenshi_agent import wave_player


def test_windows_wave_player_uses_synchronous_file_playback_in_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    imported: list[str] = []
    calls: list[tuple[str, int]] = []
    fake_winsound = SimpleNamespace(
        SND_FILENAME=0x00020000,
        PlaySound=lambda path, flags: calls.append((path, flags)),
    )

    def load_module(name: str) -> object:
        imported.append(name)
        return fake_winsound

    monkeypatch.setattr(wave_player, "import_module", load_module)
    path = tmp_path / "natural voice.wav"
    path.write_bytes(b"RIFF" + bytes(41))

    wave_player.play_windows_wave(path)

    assert imported == ["winsound"]
    assert calls == [(str(path), fake_winsound.SND_FILENAME)]


@pytest.mark.parametrize("size", [0, 44])
def test_windows_wave_player_refuses_files_without_audio_data(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    size: int,
) -> None:
    monkeypatch.setattr(
        wave_player,
        "import_module",
        lambda name: pytest.fail("an invalid wave must not reach Windows audio"),
    )
    path = tmp_path / "empty.wav"
    path.write_bytes(bytes(size))

    with pytest.raises(ValueError) as raised:
        wave_player.play_windows_wave(path)

    assert str(raised.value) == (
        f"Refusing to play a wave without audio data: {path} is {size} bytes."
    )
