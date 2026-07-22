from pathlib import Path

from PIL import Image, ImageGrab

from kenshi_agent.control.capture import WindowCapture
from kenshi_agent.control.noop import NoopInputController


class RecordingController(NoopInputController):
    def __init__(self) -> None:
        super().__init__()
        self.focus_calls = 0

    def focus_window(self) -> None:
        self.focus_calls += 1


def test_capture_focuses_target_window_first(
    tmp_path: Path, monkeypatch
) -> None:
    controller = RecordingController()
    monkeypatch.setattr("kenshi_agent.control.capture.os.name", "nt")
    monkeypatch.setattr(
        ImageGrab,
        "grab",
        lambda **_: Image.new("RGB", (1920, 1080), color="black"),
    )

    frame = WindowCapture(controller, tmp_path).capture(1)

    assert controller.focus_calls == 1
    assert frame.path.is_file()
