from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kenshi_agent.gpu_events import (
    GpuTdrDetected,
    GpuTdrEvent,
    GpuTdrMonitor,
    parse_gpu_tdr_events,
)


def _event(record_id: int, *, event_name: str = "LiveKernelEvent", p1: str = "141") -> str:
    return rf"""
    <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
      <System>
        <Provider Name="Windows Error Reporting"/>
        <EventID>1001</EventID>
        <TimeCreated SystemTime="2026-07-27T02:02:33.7756643Z"/>
        <EventRecordID>{record_id}</EventRecordID>
      </System>
      <EventData>
        <Data Name="Bucket">LKD_0x141_Tdr:6_IMAGE_igdkmdn64.sys_GEN12LP_DX10_BBHANG</Data>
        <Data Name="EventName">{event_name}</Data>
        <Data Name="P1">{p1}</Data>
        <Data Name="AttachedFiles">
          \\?\C:\WINDOWS\LiveKernelReports\WATCHDOG\WATCHDOG-20260726-1901.dmp
        </Data>
      </EventData>
    </Event>
    """


def test_gpu_event_parser_selects_only_kernel_141_and_preserves_dump_identity() -> None:
    events = parse_gpu_tdr_events(
        _event(10)
        + _event(11, event_name="StoreAgentInstallFailure1")
        + _event(12, p1="117")
    )

    assert len(events) == 1
    event = events[0]
    assert event.record_id == 10
    assert event.observed_at == datetime(
        2026, 7, 27, 2, 2, 33, 775664, tzinfo=UTC
    )
    assert event.bucket.endswith("GEN12LP_DX10_BBHANG")
    assert event.watchdog_dump.endswith("WATCHDOG-20260726-1901.dmp")


def test_gpu_monitor_rejects_a_recovered_tdr_after_its_baseline() -> None:
    snapshots = iter(
        (
            parse_gpu_tdr_events(_event(20)),
            parse_gpu_tdr_events(_event(21) + _event(20)),
        )
    )
    monitor = GpuTdrMonitor(
        query_events=lambda: next(snapshots),
        min_query_interval_seconds=0,
    )

    monitor.start()
    with pytest.raises(GpuTdrDetected, match=r"record 21.*igdkmdn64"):
        monitor.raise_if_new()


def test_gpu_monitor_rate_limits_event_log_processes_between_health_polls() -> None:
    query_count = 0
    times = iter((0.0, 0.1, 2.1))

    def query_events() -> tuple[GpuTdrEvent, ...]:
        nonlocal query_count
        query_count += 1
        return ()

    monitor = GpuTdrMonitor(
        query_events=query_events,
        monotonic=lambda: next(times),
        min_query_interval_seconds=2.0,
    )

    monitor.start()
    monitor.raise_if_new()
    assert query_count == 1
    monitor.raise_if_new()
    assert query_count == 2
