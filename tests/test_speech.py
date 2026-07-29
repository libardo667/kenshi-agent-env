from __future__ import annotations

from threading import Event, Thread

from kenshi_agent.speech import QueuedSpeechNarrator


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
