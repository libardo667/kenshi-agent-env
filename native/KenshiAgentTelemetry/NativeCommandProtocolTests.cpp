#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>

#include <cmath>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

// Compile the exact production implementation into this small console target.
// The VS2010-era solution has no shared-library indirection, and this keeps the
// conformance executable on the same parser and serializer the DLL uses.
#include "NativeCommandProtocol.cpp"
#include "NativeCommandTiming.cpp"
#include "NativeMovementSemantics.cpp"

namespace
{
    std::string ReadFile(const std::string& path)
    {
        std::ifstream input(path.c_str(), std::ios::in | std::ios::binary);
        if (!input)
            return std::string();
        std::ostringstream payload;
        payload << input.rdbuf();
        return payload.str();
    }

    bool EqualDouble(double left, double right)
    {
        return std::fabs(left - right) < 0.000000001;
    }

    int Fail(const std::string& message)
    {
        std::cerr << "Native command protocol conformance failed: "
                  << message << std::endl;
        return 1;
    }

    int TestNativeMovementPauseTiming()
    {
        using KenshiAgentTelemetry::NativeMovementPauseWindow;
        using KenshiAgentTelemetry::ObserveNativeMovementPause;

        if (KenshiAgentTelemetry::NATIVE_MOVEMENT_CONTINUOUS_PAUSE_LIMIT_MS <=
            KenshiAgentTelemetry::TELEMETRY_SNAPSHOT_INTERVAL_MS)
        {
            return Fail(
                "movement pause limit cannot expose one accepted snapshot");
        }

        NativeMovementPauseWindow window;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(window);
        const unsigned long acceptedAt = 1000UL;
        if (ObserveNativeMovementPause(window, true, acceptedAt))
            return Fail("movement cancelled on its first paused update");

        // Simulate player-interface updates until the next 500 ms telemetry
        // publication. The accepted command must remain observable.
        unsigned long now = acceptedAt + 16UL;
        const unsigned long firstSnapshot =
            acceptedAt + KenshiAgentTelemetry::TELEMETRY_SNAPSHOT_INTERVAL_MS;
        while (now <= firstSnapshot)
        {
            if (ObserveNativeMovementPause(window, true, now))
                return Fail("movement cancelled before its accepted snapshot");
            now += 16UL;
        }

        // The controller receives that snapshot, acquires its bounded input
        // turn, and begins the first pulse without cancellation.
        if (ObserveNativeMovementPause(window, false, firstSnapshot + 1250UL))
            return Fail("unpausing did not reset the initial pause window");

        // Re-pausing between two bounded pulses starts a fresh window rather
        // than inheriting time from command acceptance.
        const unsigned long betweenPulses = firstSnapshot + 3250UL;
        if (ObserveNativeMovementPause(window, true, betweenPulses))
            return Fail("movement cancelled at the first bounded re-pause");
        if (ObserveNativeMovementPause(window, true, betweenPulses + 1250UL))
            return Fail("movement cancelled during a bounded pulse gap");
        if (ObserveNativeMovementPause(window, false, betweenPulses + 1750UL))
            return Fail("next pulse did not reset the pause window");

        // A genuinely abandoned continuously paused order still names its
        // terminal reason at the exact wall-clock boundary.
        const unsigned long abandonedAt = betweenPulses + 3000UL;
        if (ObserveNativeMovementPause(window, true, abandonedAt))
            return Fail("abandoned pause cancelled before its limit");
        if (ObserveNativeMovementPause(
                window,
                true,
                abandonedAt +
                    KenshiAgentTelemetry::
                        NATIVE_MOVEMENT_CONTINUOUS_PAUSE_LIMIT_MS -
                    1UL))
        {
            return Fail("abandoned pause cancelled one millisecond early");
        }
        if (!ObserveNativeMovementPause(
                window,
                true,
                abandonedAt +
                    KenshiAgentTelemetry::
                        NATIVE_MOVEMENT_CONTINUOUS_PAUSE_LIMIT_MS))
        {
            return Fail("abandoned pause did not cancel at its limit");
        }

        return 0;
    }

    int TestNativeDirectionCompletion()
    {
        using KenshiAgentTelemetry::HasReachedFixedDirectionDestination;

        if (!HasReachedFixedDirectionDestination(
                0.0f,
                0.0f,
                17.8447f,
                24.1157f,
                16.15f,
                39.099f))
        {
            return Fail(
                "live-calibrated forward overshoot did not reach its plane");
        }
        if (HasReachedFixedDirectionDestination(
                0.0f,
                0.0f,
                0.0f,
                30.0f,
                30.0f,
                0.0f))
        {
            return Fail("purely sideways movement completed a direction");
        }
        if (HasReachedFixedDirectionDestination(
                0.0f,
                0.0f,
                0.0f,
                30.0f,
                0.0f,
                17.9f))
        {
            return Fail("short movement outside tolerance completed a direction");
        }
        if (!HasReachedFixedDirectionDestination(
                0.0f,
                0.0f,
                0.0f,
                30.0f,
                0.0f,
                18.0f))
        {
            return Fail("ordinary destination tolerance was not preserved");
        }
        if (!HasReachedFixedDirectionDestination(
                0.0f,
                0.0f,
                0.0f,
                30.0f,
                20.0f,
                30.0f))
        {
            return Fail("crossing the destination plane did not complete");
        }

        return 0;
    }
}

int main(int argc, char** argv)
{
    if (argc != 2)
        return Fail("expected the native fixture directory as one argument");
    const int timingResult = TestNativeMovementPauseTiming();
    if (timingResult != 0)
        return timingResult;
    const int completionResult = TestNativeDirectionCompletion();
    if (completionResult != 0)
        return completionResult;

    const std::string fixtureDirectory = argv[1];
    const std::string separator =
        (!fixtureDirectory.empty() &&
         (fixtureDirectory[fixtureDirectory.size() - 1] == '\\' ||
          fixtureDirectory[fixtureDirectory.size() - 1] == '/'))
            ? ""
            : "\\";
    const std::string prefix = fixtureDirectory + separator;

    KenshiAgentTelemetry::NativeCommandRequest direction;
    std::string rejectionReason;
    const std::string directionPayload =
        ReadFile(prefix + "valid_direction_request.json");
    if (directionPayload.empty())
        return Fail("could not read valid_direction_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            directionPayload,
            direction,
            rejectionReason))
    {
        return Fail(
            "valid targetless direction was rejected as " + rejectionReason);
    }
    if (direction.command != "move_in_direction" ||
        !direction.targetId.empty() ||
        !EqualDouble(direction.bearingDegrees, 90.0) ||
        !EqualDouble(direction.distanceUnits, 250.0))
    {
        return Fail("valid direction did not retain its targetless vector");
    }

    KenshiAgentTelemetry::NativeCommandRequest targeted;
    const std::string targetedPayload =
        ReadFile(prefix + "valid_targeted_request.json");
    if (targetedPayload.empty())
        return Fail("could not read valid_targeted_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            targetedPayload,
            targeted,
            rejectionReason))
    {
        return Fail("valid targeted request was rejected as " + rejectionReason);
    }
    if (targeted.command != "move_to_character" ||
        targeted.targetId != "entity-destination" ||
        targeted.bearingDegrees != 0.0 ||
        targeted.distanceUnits != 0.0)
    {
        return Fail("targeted request did not retain its exact identity");
    }

    KenshiAgentTelemetry::NativeCommandRequest invalid;
    const std::string invalidPayload =
        ReadFile(prefix + "invalid_direction_target_request.json");
    if (invalidPayload.empty())
        return Fail("could not read invalid_direction_target_request.json");
    if (KenshiAgentTelemetry::ParseNativeCommandRequest(
            invalidPayload,
            invalid,
            rejectionReason))
    {
        return Fail("direction request carrying a target was accepted");
    }
    if (rejectionReason != "malformed_request")
        return Fail("invalid direction did not fail as malformed_request");

    const std::string acknowledgementPayload =
        ReadFile(prefix + "valid_direction_acknowledgement.json");
    if (acknowledgementPayload.empty())
        return Fail("could not read valid_direction_acknowledgement.json");
    std::istringstream expectedInput(acknowledgementPayload);
    boost::property_tree::ptree expected;
    boost::property_tree::read_json(expectedInput, expected);

    KenshiAgentTelemetry::NativeCommandAcknowledgement acknowledgement;
    acknowledgement.commandId =
        expected.get<std::string>("command_id");
    acknowledgement.command =
        expected.get<std::string>("command");
    acknowledgement.status =
        expected.get<std::string>("status");
    acknowledgement.reason =
        expected.get<std::string>("reason");
    acknowledgement.targetId =
        expected.get<std::string>("target_id");
    acknowledgement.bearingDegrees =
        expected.get<double>("bearing_degrees");
    acknowledgement.distanceUnits =
        expected.get<double>("distance_units");
    acknowledgement.selectedCharacterId =
        expected.get_child("selected_character_ids").begin()->second.data();
    acknowledgement.basedOnTelemetrySequence =
        expected.get<unsigned long long>("based_on_telemetry_sequence");
    acknowledgement.acknowledgedAtTelemetrySequence =
        expected.get<unsigned long long>(
            "acknowledged_at_telemetry_sequence");
    acknowledgement.hasAcceptedSequence = true;
    acknowledgement.acceptedAtTelemetrySequence =
        expected.get<unsigned long long>("accepted_at_telemetry_sequence");

    const std::string serialized =
        KenshiAgentTelemetry::SerializeNativeCommandAcknowledgement(
            acknowledgement);
    std::istringstream actualInput(serialized);
    boost::property_tree::ptree actual;
    boost::property_tree::read_json(actualInput, actual);
    if (actual.get<std::string>("command_id") !=
            expected.get<std::string>("command_id") ||
        actual.get<std::string>("command") !=
            expected.get<std::string>("command") ||
        actual.get<std::string>("target_id") != "" ||
        !EqualDouble(actual.get<double>("bearing_degrees"), 90.0) ||
        !EqualDouble(actual.get<double>("distance_units"), 250.0) ||
        actual.get_child("selected_character_ids").begin()->second.data() !=
            "entity-selected" ||
        actual.get<unsigned long long>("accepted_at_telemetry_sequence") != 8)
    {
        return Fail(
            "serialized direction acknowledgement diverged from the fixture");
    }

    std::cout
        << "Native command protocol fixtures and movement semantics passed."
        << std::endl;
    return 0;
}
