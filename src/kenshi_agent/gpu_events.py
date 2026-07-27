from __future__ import annotations

import re
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime


class GpuTdrDetected(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GpuTdrEvent:
    record_id: int
    observed_at: datetime
    bucket: str
    watchdog_dump: str

    def as_json(self) -> dict[str, object]:
        return asdict(self) | {"observed_at": self.observed_at.isoformat()}


_EVENT_NAMESPACE = "http://schemas.microsoft.com/win/2004/08/events/event"
_EVENT_QUERY = (
    "*[System[Provider[@Name='Windows Error Reporting'] and (EventID=1001)]]"
)


def parse_gpu_tdr_events(payload: str) -> tuple[GpuTdrEvent, ...]:
    without_declarations = re.sub(r"<\?xml[^>]*\?>", "", payload)
    root = ET.fromstring(f"<Events>{without_declarations}</Events>")
    namespace = {"event": _EVENT_NAMESPACE}
    events: list[GpuTdrEvent] = []
    for element in root.findall("event:Event", namespace):
        record_text = element.findtext(
            "event:System/event:EventRecordID",
            namespaces=namespace,
        )
        time_element = element.find(
            "event:System/event:TimeCreated",
            namespace,
        )
        fields = {
            str(data.get("Name")): (data.text or "").strip()
            for data in element.findall(
                "event:EventData/event:Data",
                namespace,
            )
        }
        if (
            record_text is None
            or time_element is None
            or fields.get("EventName") != "LiveKernelEvent"
            or fields.get("P1", "").casefold() != "141"
        ):
            continue
        system_time = time_element.get("SystemTime")
        if system_time is None:
            continue
        attached_files = fields.get("AttachedFiles", "")
        watchdog_dump = next(
            (
                line.strip()
                for line in attached_files.splitlines()
                if "livekernelreports\\watchdog\\" in line.casefold()
                and line.casefold().endswith(".dmp")
            ),
            "",
        )
        events.append(
            GpuTdrEvent(
                record_id=int(record_text),
                observed_at=datetime.fromisoformat(
                    system_time.replace("Z", "+00:00")
                ),
                bucket=fields.get("Bucket", ""),
                watchdog_dump=watchdog_dump,
            )
        )
    return tuple(sorted(events, key=lambda event: event.record_id, reverse=True))


def query_windows_gpu_tdr_events() -> tuple[GpuTdrEvent, ...]:
    result = subprocess.run(
        [
            "wevtutil.exe",
            "qe",
            "Application",
            f"/q:{_EVENT_QUERY}",
            "/rd:true",
            "/c:50",
            "/f:xml",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8-sig",
        errors="strict",
        timeout=15.0,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    return parse_gpu_tdr_events(result.stdout)


class GpuTdrMonitor:
    def __init__(
        self,
        *,
        query_events: Callable[[], tuple[GpuTdrEvent, ...]] = (
            query_windows_gpu_tdr_events
        ),
        monotonic: Callable[[], float] = time.monotonic,
        min_query_interval_seconds: float = 2.0,
    ) -> None:
        self._query_events = query_events
        self._monotonic = monotonic
        self._min_query_interval_seconds = min_query_interval_seconds
        self._baseline_record_id: int | None = None
        self._last_query_at: float | None = None

    def start(self) -> int:
        events = self._query_events()
        self._last_query_at = self._monotonic()
        self._baseline_record_id = max(
            (event.record_id for event in events),
            default=0,
        )
        return self._baseline_record_id

    def raise_if_new(self, *, force: bool = False) -> None:
        if self._baseline_record_id is None:
            raise RuntimeError("GPU TDR monitor has not been started.")
        now = self._monotonic()
        if (
            not force
            and self._last_query_at is not None
            and now - self._last_query_at < self._min_query_interval_seconds
        ):
            return
        new_events = tuple(
            event
            for event in self._query_events()
            if event.record_id > self._baseline_record_id
        )
        self._last_query_at = now
        if not new_events:
            return
        newest = max(new_events, key=lambda event: event.record_id)
        bucket = newest.bucket or "unbucketed LiveKernelEvent 141"
        raise GpuTdrDetected(
            "A new Windows GPU timeout was recorded during the live command "
            f"(record {newest.record_id}, {bucket}). Telemetry recovery does "
            "not make this run valid."
        )
