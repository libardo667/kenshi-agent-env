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
#include "WorldTargetProtocol.cpp"
#include "GameplayCapabilities.generated.h"

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

    int TestGameplayCapabilities()
    {
        std::ostringstream withoutConditional;
        KenshiAgentTelemetry::AppendGameplayCapabilities(
            withoutConditional,
            false);
        const std::string baseline = withoutConditional.str();
        if (baseline.empty() ||
            baseline[0] != '[' ||
            baseline[baseline.size() - 1] != ']')
        {
            return Fail("gameplay capabilities were not serialized as an array");
        }
        if (baseline.find("\"game.pause\"") == std::string::npos ||
            baseline.find("\"control.perform_context_action\"") ==
                std::string::npos)
        {
            return Fail("required gameplay capabilities were not serialized");
        }
        if (baseline.find("\"nearby.shop_owners\"") != std::string::npos)
        {
            return Fail("conditional capability leaked into the baseline set");
        }

        std::ostringstream withConditional;
        KenshiAgentTelemetry::AppendGameplayCapabilities(
            withConditional,
            true);
        if (withConditional.str().find("\"nearby.shop_owners\"") ==
            std::string::npos)
        {
            return Fail("conditional gameplay capability was not serialized");
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

    int TestNativeMovementStallTiming()
    {
        using KenshiAgentTelemetry::NativeMovementStallWindow;
        using KenshiAgentTelemetry::ObserveNativeMovementStall;

        NativeMovementStallWindow window;
        KenshiAgentTelemetry::ResetNativeMovementStallWindow(window);
        if (ObserveNativeMovementStall(window, false, 0.0f, 0.0f, 1000UL))
            return Fail("movement stalled on its first observation");
        if (ObserveNativeMovementStall(
                window,
                false,
                0.5f,
                0.0f,
                1000UL +
                    KenshiAgentTelemetry::NATIVE_MOVEMENT_STALL_LIMIT_MS -
                    1UL))
        {
            return Fail("sub-threshold movement stalled one millisecond early");
        }
        if (!ObserveNativeMovementStall(
                window,
                false,
                0.5f,
                0.0f,
                1000UL +
                    KenshiAgentTelemetry::NATIVE_MOVEMENT_STALL_LIMIT_MS))
        {
            return Fail("blocked movement did not stall at its exact limit");
        }

        KenshiAgentTelemetry::ResetNativeMovementStallWindow(window);
        ObserveNativeMovementStall(window, false, 0.0f, 0.0f, 2000UL);
        if (ObserveNativeMovementStall(window, false, 1.0f, 0.0f, 7000UL))
            return Fail("meaningful progress incorrectly stalled");
        if (ObserveNativeMovementStall(
                window,
                false,
                1.0f,
                0.0f,
                7000UL +
                    KenshiAgentTelemetry::NATIVE_MOVEMENT_STALL_LIMIT_MS -
                    1UL))
        {
            return Fail("progress did not reset the stall interval");
        }

        // A deliberate controller pause does not count against movement time.
        if (ObserveNativeMovementStall(window, true, 1.0f, 0.0f, 50000UL))
            return Fail("paused movement was classified as stalled");
        if (ObserveNativeMovementStall(window, false, 1.0f, 0.0f, 51000UL))
            return Fail("movement stalled immediately after a pause");

        return 0;
    }

    int TestNativeOutdoorConfirmation()
    {
        using KenshiAgentTelemetry::NativeOutdoorConfirmationWindow;
        using KenshiAgentTelemetry::ObserveNativeOutdoorConfirmation;

        NativeOutdoorConfirmationWindow window;
        KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(window);
        if (ObserveNativeOutdoorConfirmation(window, false, 1000UL))
            return Fail("first outdoor frame completed a building exit");
        if (ObserveNativeOutdoorConfirmation(
                window,
                false,
                1000UL +
                    KenshiAgentTelemetry::NATIVE_OUTDOOR_CONFIRMATION_MS -
                    1UL))
        {
            return Fail("building exit completed before outdoor confirmation");
        }
        if (!ObserveNativeOutdoorConfirmation(
                window,
                false,
                1000UL +
                    KenshiAgentTelemetry::NATIVE_OUTDOOR_CONFIRMATION_MS))
        {
            return Fail("stable outdoor state did not complete at its limit");
        }

        // A nested/intermediate indoor handle resets a transient outdoor gap.
        if (ObserveNativeOutdoorConfirmation(window, true, 2000UL))
            return Fail("an indoor state completed a building exit");
        if (ObserveNativeOutdoorConfirmation(window, false, 2100UL))
            return Fail("outdoor confirmation did not restart after indoors");
        return 0;
    }

    int TestResolvedIndoorMembership()
    {
        using KenshiAgentTelemetry::HasResolvedIndoorBuilding;

        if (!HasResolvedIndoorBuilding(true, true, true))
            return Fail("a valid resolved building was not classified indoors");
        if (HasResolvedIndoorBuilding(true, false, false))
        {
            return Fail(
                "a stale valid handle without a building was classified indoors");
        }
        if (HasResolvedIndoorBuilding(true, true, false))
            return Fail("an invalid resolved building was classified indoors");
        if (HasResolvedIndoorBuilding(false, true, true))
            return Fail("an invalid handle was classified indoors");
        return 0;
    }

    int TestNativeExitDestinationCompletion()
    {
        using KenshiAgentTelemetry::HasReachedResolvedExitDestination;

        if (HasReachedResolvedExitDestination(
                0.0f,
                0.0f,
                100.0f,
                50.0f,
                0.5f,
                0.0f))
        {
            return Fail("an exit completed without meaningful movement");
        }
        if (HasReachedResolvedExitDestination(
                0.0f,
                0.0f,
                100.0f,
                50.0f,
                96.9f,
                50.0f))
        {
            return Fail("an exit completed outside its tight door tolerance");
        }
        if (!HasReachedResolvedExitDestination(
                0.0f,
                0.0f,
                100.0f,
                50.0f,
                97.0f,
                50.0f))
        {
            return Fail("a reached native outside-door point did not complete");
        }
        if (!HasReachedResolvedExitDestination(
                0.0f,
                0.0f,
                100.0f,
                50.0f,
                100.0f,
                50.0f))
        {
            return Fail("an exact native outside-door point did not complete");
        }
        return 0;
    }

    int TestNativeCameraFollowPolicy()
    {
        using KenshiAgentTelemetry::ShouldMaintainCameraFollow;

        if (!ShouldMaintainCameraFollow(true, true, true))
            return Fail("an exact active selection did not maintain camera follow");
        if (ShouldMaintainCameraFollow(false, true, true))
            return Fail("an inactive command maintained camera follow");
        if (ShouldMaintainCameraFollow(true, false, true))
            return Fail("an unresolved selection maintained camera follow");
        if (ShouldMaintainCameraFollow(true, true, false))
            return Fail("a mismatched selection maintained camera follow");
        return 0;
    }

    int TestNaturalResourceAssessment()
    {
        using KenshiAgentTelemetry::AssessNaturalResource;
        using KenshiAgentTelemetry::NaturalResourceAssessment;
        using KenshiAgentTelemetry::NaturalResourceTargetSnapshot;
        using KenshiAgentTelemetry::SelectNearestNaturalResourceTargets;

        const NaturalResourceAssessment constructedMine =
            AssessNaturalResource(true, true, false, true);
        const NaturalResourceAssessment naturalMine =
            AssessNaturalResource(true, false, true, true);
        if (!constructedMine.structurallyRecognized ||
            !naturalMine.structurallyRecognized)
        {
            return Fail("a reviewed mining resource disappeared");
        }

        if (AssessNaturalResource(false, true, true, true)
                .structurallyRecognized ||
            AssessNaturalResource(true, false, false, true)
                .structurallyRecognized ||
            AssessNaturalResource(true, true, false, false)
                .structurallyRecognized)
        {
            return Fail("a structurally invalid resource was recognized");
        }

        std::vector<NaturalResourceTargetSnapshot> candidates;
        NaturalResourceTargetSnapshot far;
        far.id = "far";
        far.distance = 300.0;
        candidates.push_back(far);
        NaturalResourceTargetSnapshot nearest;
        nearest.id = "nearest";
        nearest.distance = 10.0;
        candidates.push_back(nearest);
        NaturalResourceTargetSnapshot duplicateNearest = nearest;
        duplicateNearest.distance = 20.0;
        candidates.push_back(duplicateNearest);
        NaturalResourceTargetSnapshot middle;
        middle.id = "middle";
        middle.distance = 100.0;
        candidates.push_back(middle);

        const std::vector<NaturalResourceTargetSnapshot> selected =
            SelectNearestNaturalResourceTargets(candidates, 2);
        if (selected.size() != 2 ||
            selected[0].id != "nearest" ||
            selected[1].id != "middle")
        {
            return Fail(
                "world targets were not deduplicated and retained nearest-first");
        }

        if (!KenshiAgentTelemetry::IsWorldTargetScanAtCapacity(128, 128) ||
            KenshiAgentTelemetry::IsWorldTargetScanAtCapacity(127, 128))
        {
            return Fail("world-target scan capacity was reported incorrectly");
        }
        return 0;
    }
}

int main(int argc, char** argv)
{
    if (argc != 2)
        return Fail("expected the native fixture directory as one argument");
    const int capabilityResult = TestGameplayCapabilities();
    if (capabilityResult != 0)
        return capabilityResult;
    const int timingResult = TestNativeMovementPauseTiming();
    if (timingResult != 0)
        return timingResult;
    const int completionResult = TestNativeDirectionCompletion();
    if (completionResult != 0)
        return completionResult;
    const int stallResult = TestNativeMovementStallTiming();
    if (stallResult != 0)
        return stallResult;
    const int outdoorResult = TestNativeOutdoorConfirmation();
    if (outdoorResult != 0)
        return outdoorResult;
    const int indoorMembershipResult = TestResolvedIndoorMembership();
    if (indoorMembershipResult != 0)
        return indoorMembershipResult;
    const int exitDestinationResult = TestNativeExitDestinationCompletion();
    if (exitDestinationResult != 0)
        return exitDestinationResult;
    const int cameraFollowResult = TestNativeCameraFollowPolicy();
    if (cameraFollowResult != 0)
        return cameraFollowResult;
    const int naturalResourceResult = TestNaturalResourceAssessment();
    if (naturalResourceResult != 0)
        return naturalResourceResult;

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

    KenshiAgentTelemetry::NativeCommandRequest approach;
    const std::string approachPayload =
        ReadFile(prefix + "valid_approach_request.json");
    if (approachPayload.empty())
        return Fail("could not read valid_approach_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            approachPayload,
            approach,
            rejectionReason))
    {
        return Fail("valid approach request was rejected as " + rejectionReason);
    }
    if (approach.command != "approach_confirmed_vendor" ||
        approach.targetId != "entity-dialogue-target" ||
        approach.bearingDegrees != 0.0 ||
        approach.distanceUnits != 0.0)
    {
        return Fail("approach request did not retain its exact target");
    }

    KenshiAgentTelemetry::NativeCommandRequest buildingExit;
    const std::string buildingExitPayload =
        ReadFile(prefix + "valid_exit_building_request.json");
    if (buildingExitPayload.empty())
        return Fail("could not read valid_exit_building_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            buildingExitPayload,
            buildingExit,
            rejectionReason))
    {
        return Fail(
            "valid building-exit request was rejected as " + rejectionReason);
    }
    if (buildingExit.command != "exit_current_building" ||
        !buildingExit.targetId.empty() ||
        buildingExit.bearingDegrees != 0.0 ||
        buildingExit.distanceUnits != 0.0)
    {
        return Fail("valid building exit did not remain parameterless");
    }

    KenshiAgentTelemetry::NativeCommandRequest contextAction;
    const std::string contextActionPayload =
        ReadFile(prefix + "valid_context_action_request.json");
    if (contextActionPayload.empty())
        return Fail("could not read valid_context_action_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            contextActionPayload,
            contextAction,
            rejectionReason))
    {
        return Fail(
            "valid context-action request was rejected as " + rejectionReason);
    }
    if (contextAction.command != "operate_natural_resource" ||
        contextAction.targetId != "entity-natural-resource" ||
        contextAction.bearingDegrees != 0.0 ||
        contextAction.distanceUnits != 0.0)
    {
        return Fail("valid context action did not retain its exact target");
    }

    const std::string naturalResourcePayload =
        ReadFile(prefix + "valid_natural_resource.json");
    if (naturalResourcePayload.empty())
    {
        return Fail(
            "could not read valid_natural_resource.json");
    }
    KenshiAgentTelemetry::NaturalResourceTargetSnapshot naturalResource;
    naturalResource.id = "entity-natural-resource";
    naturalResource.name = "Copper Resource";
    naturalResource.positionX = 10.0;
    naturalResource.positionY = 0.0;
    naturalResource.positionZ = 20.0;
    naturalResource.distance = 30.0;
    naturalResource.miningResourceLevel = 0.8;
    const std::string naturalResourceSerialized =
        KenshiAgentTelemetry::SerializeNaturalResourceTarget(
            naturalResource);
    std::string naturalResourceExpected = naturalResourcePayload;
    while (!naturalResourceExpected.empty() &&
           (naturalResourceExpected[naturalResourceExpected.size() - 1] == '\r' ||
            naturalResourceExpected[naturalResourceExpected.size() - 1] == '\n'))
    {
        naturalResourceExpected.erase(naturalResourceExpected.size() - 1);
    }
    if (naturalResourceSerialized != naturalResourceExpected)
    {
        return Fail(
            "serialized natural resource diverged from fixture");
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
        << "Native protocol fixtures and semantics passed."
        << std::endl;
    return 0;
}
