#include "NativeCommandTiming.h"

namespace KenshiAgentTelemetry
{
    void ResetNativeMovementPauseWindow(NativeMovementPauseWindow& window)
    {
        window.observingPause = false;
        window.startedAtMs = 0UL;
    }

    bool ObserveNativeMovementPause(
        NativeMovementPauseWindow& window,
        bool worldPaused,
        unsigned long nowMs)
    {
        if (!worldPaused)
        {
            ResetNativeMovementPauseWindow(window);
            return false;
        }
        if (!window.observingPause)
        {
            window.observingPause = true;
            window.startedAtMs = nowMs;
            return false;
        }
        // Unsigned subtraction intentionally remains correct across the
        // GetTickCount wrap boundary.
        return nowMs - window.startedAtMs >=
            NATIVE_MOVEMENT_CONTINUOUS_PAUSE_LIMIT_MS;
    }
}
