from __future__ import annotations

import shutil
import subprocess
from collections import deque
from dataclasses import dataclass
from threading import Condition, Lock, Thread
from typing import Protocol


class SpeechUnavailableError(RuntimeError):
    """The requested local speech backend cannot be started."""


class BlockingSpeaker(Protocol):
    """One blocking speech engine, isolated behind the narration worker."""

    def speak(self, text: str) -> None: ...

    def close(self) -> None: ...


class SpeechNarrator(Protocol):
    """Non-blocking human-facing narration accepted by decision reporters."""

    def say(self, text: str, *, key: str | None = None) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Utterance:
    text: str
    key: str | None


class QueuedSpeechNarrator:
    """Keep speech entirely off the planner and gameplay execution threads."""

    def __init__(
        self,
        speaker: BlockingSpeaker,
        *,
        queue_limit: int = 6,
        max_utterance_chars: int = 280,
    ) -> None:
        if queue_limit < 1:
            raise ValueError("queue_limit must be at least 1")
        if max_utterance_chars < 20:
            raise ValueError("max_utterance_chars must be at least 20")
        self._speaker = speaker
        self._queue_limit = queue_limit
        self._max_utterance_chars = max_utterance_chars
        self._pending: deque[_Utterance] = deque()
        self._condition = Condition()
        self._closed = False
        self._speaker_closed = False
        self._speaker_close_lock = Lock()
        self._thread = Thread(
            target=self._run,
            name="kenshi-agent-tts",
            daemon=True,
        )
        self._thread.start()

    def say(self, text: str, *, key: str | None = None) -> None:
        normalized = " ".join(text.split())
        if not normalized:
            return
        normalized = normalized[: self._max_utterance_chars].rstrip()
        with self._condition:
            if self._closed:
                return
            if key is not None:
                self._pending = deque(
                    utterance
                    for utterance in self._pending
                    if utterance.key != key
                )
            while len(self._pending) >= self._queue_limit:
                self._pending.popleft()
            self._pending.append(_Utterance(normalized, key))
            self._condition.notify()

    def close(
        self,
        *,
        drain: bool = True,
        timeout_seconds: float = 2.0,
    ) -> None:
        with self._condition:
            if not self._closed:
                self._closed = True
                if not drain:
                    self._pending.clear()
                self._condition.notify_all()
        self._thread.join(timeout=max(0.0, timeout_seconds))
        if self._thread.is_alive():
            self._close_speaker()
            self._thread.join(timeout=0.25)
        else:
            self._close_speaker()

    def _run(self) -> None:
        try:
            while True:
                with self._condition:
                    self._condition.wait_for(
                        lambda: bool(self._pending) or self._closed
                    )
                    if not self._pending:
                        return
                    utterance = self._pending.popleft()
                try:
                    self._speaker.speak(utterance.text)
                except (OSError, RuntimeError, ValueError):
                    with self._condition:
                        self._pending.clear()
                        self._closed = True
                    return
        finally:
            self._close_speaker()

    def _close_speaker(self) -> None:
        with self._speaker_close_lock:
            if self._speaker_closed:
                return
            self._speaker_closed = True
            self._speaker.close()


_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new()
try {
    while (($line = [Console]::In.ReadLine()) -ne $null) {
        $speaker.Speak($line)
        [Console]::Out.WriteLine('OK')
        [Console]::Out.Flush()
    }
}
finally {
    $speaker.Dispose()
}
"""


class WindowsSapiSpeaker:
    """One persistent offline Windows SAPI process with per-utterance acknowledgement."""

    def __init__(self) -> None:
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if executable is None:
            raise SpeechUnavailableError(
                "Windows PowerShell is not available for offline speech."
            )
        self._state_lock = Lock()
        self._closed = False
        self._speaking = False
        try:
            self._process = subprocess.Popen(
                [
                    executable,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _SAPI_SCRIPT,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise SpeechUnavailableError(
                f"Windows speech process could not start: {exc}"
            ) from exc
        if self._process.stdin is None or self._process.stdout is None:
            self.close()
            raise SpeechUnavailableError(
                "Windows speech process did not expose its input and status pipes."
            )
    def speak(self, text: str) -> None:
        with self._state_lock:
            if self._closed:
                raise RuntimeError("Windows speech process is closed.")
            stdin = self._process.stdin
            stdout = self._process.stdout
            if stdin is None or stdout is None:
                raise RuntimeError("Windows speech process pipes are unavailable.")
            self._speaking = True
        try:
            stdin.write(text + "\n")
            stdin.flush()
            acknowledgement = stdout.readline().strip()
        except (BrokenPipeError, OSError) as exc:
            raise RuntimeError("Windows speech process ended unexpectedly.") from exc
        finally:
            with self._state_lock:
                self._speaking = False
        if acknowledgement != "OK":
            raise RuntimeError("Windows speech process did not acknowledge speech.")

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            speaking = self._speaking
        if speaking:
            if self._process.poll() is None:
                self._process.terminate()
        elif self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=0.5)


def windows_sapi_narrator() -> QueuedSpeechNarrator:
    """Build the supported offline narration mode for Windows or WSL."""

    return QueuedSpeechNarrator(WindowsSapiSpeaker())
