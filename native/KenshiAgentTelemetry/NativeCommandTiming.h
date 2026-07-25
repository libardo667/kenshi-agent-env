#pragma once

namespace KenshiAgentTelemetry
{
    // Loaded-game telemetry is the acknowledgement channel for native
    // commands. A paused command must survive at least one publication plus the
    // controller's bounded handoff before it can reasonably be called
    // abandoned.
    const unsigned long TELEMETRY_SNAPSHOT_INTERVAL_MS = 500UL;
    const unsigned long NATIVE_MOVEMENT_CONTINUOUS_PAUSE_LIMIT_MS = 5000UL;

    struct NativeMovementPauseWindow
    {
        bool observingPause;
        unsigned long startedAtMs;
    };

    void ResetNativeMovementPauseWindow(NativeMovementPauseWindow& window);

    // Returns true only after one uninterrupted pause reaches the limit.
    // Any observed unpaused update resets the window for the next bounded pulse.
    bool ObserveNativeMovementPause(
        NativeMovementPauseWindow& window,
        bool worldPaused,
        unsigned long nowMs);
}
