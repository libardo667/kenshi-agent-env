from __future__ import annotations

import subprocess
from pathlib import Path
from threading import Event, Thread
from typing import Any

import pytest

from kenshi_agent.speech import (
    PiperSpeaker,
    QueuedSpeechNarrator,
    SpeechUnavailableError,
    installed_piper_voice,
    piper_narrator,
)


class BlockingSpeaker:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.spoken: list[str] = []
        self.closed = False

    def speak(self, text: str) -> None:
        self.spoken.append(text)
        if len(self.spoken) == 1:
            self.started.set()
            assert self.release.wait(2.0)

    def close(self) -> None:
        self.closed = True


def test_narration_never_blocks_the_gameplay_caller_and_coalesces_pending_state() -> None:
    speaker = BlockingSpeaker()
    narrator = QueuedSpeechNarrator(speaker, queue_limit=2)
    narrator.say("First utterance.")
    assert speaker.started.wait(1.0)

    returned = Event()

    def enqueue_while_speaker_is_busy() -> None:
        narrator.say("Old state.", key="state")
        narrator.say("Current state.", key="state")
        returned.set()

    caller = Thread(target=enqueue_while_speaker_is_busy, daemon=True)
    caller.start()
    assert returned.wait(0.25)

    speaker.release.set()
    narrator.close(drain=True, timeout_seconds=2.0)
    caller.join(timeout=1.0)

    assert speaker.spoken == ["First utterance.", "Current state."]
    assert speaker.closed is True


def test_narration_bounds_text_before_it_reaches_the_speaker() -> None:
    speaker = BlockingSpeaker()
    speaker.release.set()
    narrator = QueuedSpeechNarrator(speaker, max_utterance_chars=40)

    narrator.say("  This   is a\nlong human sentence " + "with extra words " * 10)
    narrator.close(drain=True, timeout_seconds=2.0)

    assert len(speaker.spoken) == 1
    assert "\n" not in speaker.spoken[0]
    assert "  " not in speaker.spoken[0]
    assert len(speaker.spoken[0]) <= 40


def _piper_home(root: Path, *, executable: bool = True, model: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if executable:
        (root / "piper").mkdir(parents=True, exist_ok=True)
        (root / "piper" / "piper.exe").write_bytes(b"")
    if model:
        (root / "en_US-lessac-medium.onnx").write_bytes(b"")
    return root


def test_installed_piper_voice_finds_a_complete_install(tmp_path: Path) -> None:
    home = _piper_home(tmp_path)

    voice = installed_piper_voice(home)

    assert voice is not None
    assert voice[0] == home / "piper" / "piper.exe"
    assert voice[1] == home / "en_US-lessac-medium.onnx"


def test_a_half_downloaded_piper_install_is_absent_not_broken(tmp_path: Path) -> None:
    """A model still downloading must fall back, not fail the run."""

    no_model = _piper_home(tmp_path / "a", model=False)
    no_executable = _piper_home(tmp_path / "b", executable=False)

    assert installed_piper_voice(no_model) is None
    assert installed_piper_voice(no_executable) is None


def test_piper_speaker_refuses_to_start_without_its_voice(tmp_path: Path) -> None:
    home = _piper_home(tmp_path, model=False)

    with pytest.raises(SpeechUnavailableError, match="voice model is missing"):
        PiperSpeaker(
            home / "piper" / "piper.exe",
            home / "absent.onnx",
            player=lambda path: None,
        )


def test_piper_hands_synthesized_audio_to_its_owned_wave_player(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _piper_home(tmp_path)
    executable = home / "piper" / "piper.exe"
    model = home / "en_US-lessac-medium.onnx"
    wave_bytes = b"RIFF-owned-wave-player"
    subprocess_calls: list[list[Any]] = []

    def synthesize(
        command: list[Any],
        **kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        subprocess_calls.append(command)
        assert command[0] == str(executable)
        output_index = command.index("--output_file") + 1
        Path(command[output_index]).write_bytes(wave_bytes)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("kenshi_agent.speech.subprocess.run", synthesize)
    played: list[bytes] = []
    speaker = PiperSpeaker(
        executable,
        model,
        player=lambda path: played.append(path.read_bytes()),
    )

    speaker.speak("The natural voice must use the accepted audio path.")
    speaker.close()

    assert len(subprocess_calls) == 1
    assert "--length_scale" in subprocess_calls[0]
    length_scale_index = subprocess_calls[0].index("--length_scale") + 1
    assert subprocess_calls[0][length_scale_index] == "0.70"
    assert played == [wave_bytes]


def test_piper_defers_cleanup_while_windows_still_owns_the_wave(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = _piper_home(tmp_path)
    playback_started = Event()
    release_playback = Event()
    synthesized_directory: Path | None = None

    def synthesize(command: list[Any], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal synthesized_directory
        output_index = command.index("--output_file") + 1
        wave_path = Path(command[output_index])
        synthesized_directory = wave_path.parent
        wave_path.write_bytes(b"RIFF-owned-wave-player")
        return subprocess.CompletedProcess(command, 0, "", "")

    def play(_path: Path) -> None:
        playback_started.set()
        assert release_playback.wait(2.0)

    monkeypatch.setattr("kenshi_agent.speech.subprocess.run", synthesize)
    speaker = PiperSpeaker(
        home / "piper" / "piper.exe",
        home / "en_US-lessac-medium.onnx",
        player=play,
    )
    worker = Thread(target=lambda: speaker.speak("Still playing."), daemon=True)
    worker.start()
    assert playback_started.wait(1.0)

    speaker.close()

    assert synthesized_directory is not None
    assert synthesized_directory.exists()
    release_playback.set()
    worker.join(timeout=2.0)
    assert not worker.is_alive()
    assert not synthesized_directory.exists()


def test_piper_narration_keeps_only_the_latest_pending_update(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    speaker = BlockingSpeaker()
    monkeypatch.setattr(
        "kenshi_agent.speech.PiperSpeaker",
        lambda executable, model: speaker,
    )
    narrator = piper_narrator(tmp_path / "piper.exe", tmp_path / "voice.onnx")
    narrator.say("First update.")
    assert speaker.started.wait(1.0)

    narrator.say("Old plan.", key="decision")
    narrator.say("Intermediate action.", key="action")
    narrator.say("Newest result.", key="result")
    speaker.release.set()
    narrator.close(drain=True, timeout_seconds=2.0)

    assert speaker.spoken == ["First update.", "Newest result."]
