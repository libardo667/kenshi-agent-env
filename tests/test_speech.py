from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import pytest

from kenshi_agent.speech import (
    PiperSpeaker,
    QueuedSpeechNarrator,
    SpeechUnavailableError,
    installed_piper_voice,
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
            player="/bin/true",
        )
