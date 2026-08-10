#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>

#include <cmath>
#include <exception>
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
#include "ResourceProductionSemantics.h"
#include "WorldTargetProtocol.cpp"
#include "GameplayCapabilities.generated.h"
#include "InventoryScreenSemantics.h"
#include "RuntimeContextMenuSemantics.h"

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
                std::string::npos ||
            baseline.find("\"ui.context_menu.orders\"") ==
                std::string::npos ||
            baseline.find("\"world.context_target_screen_positions\"") ==
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

    int CheckResearchCallSite(
        const std::string& researchRoot,
        const std::string& subsystem,
        const std::string& symbol,
        const std::string& rva)
    {
        const std::string path =
            researchRoot + "/" + subsystem + "/call_sites.json";
        const std::string payload = ReadFile(path);
        if (payload.empty())
            return Fail("could not read canonical research fixture " + path);

        try
        {
            std::istringstream input(payload);
            boost::property_tree::ptree evidence;
            boost::property_tree::read_json(input, evidence);
            if (evidence.get<int>("schema_version") != 1 ||
                evidence.get<std::string>("subsystem") != subsystem ||
                evidence.get<std::string>("executable.version").empty() ||
                evidence.get<std::string>("executable.sha256").size() != 64U)
            {
                return Fail(
                    "canonical research fixture lost its exact identity: " +
                    subsystem);
            }

            bool found = false;
            const boost::property_tree::ptree& sites =
                evidence.get_child("sites");
            for (boost::property_tree::ptree::const_iterator it = sites.begin();
                 it != sites.end();
                 ++it)
            {
                if (it->second.get<std::string>("symbol") == symbol &&
                    it->second.get<std::string>("rva") == rva)
                {
                    found = true;
                    break;
                }
            }
            if (!found)
            {
                return Fail(
                    "canonical research fixture lost native call site " +
                    symbol + " at " + rva);
            }
        }
        catch (const std::exception& error)
        {
            return Fail(
                "could not parse canonical research fixture " + path +
                ": " + error.what());
        }
        return 0;
    }

    int TestResearchEvidence(const std::string& researchRoot)
    {
        int result = CheckResearchCallSite(
            researchRoot,
            "context_menu_orders",
            "ContextMenu::showContextMenu",
            "0x7A5960");
        if (result != 0)
            return result;
        result = CheckResearchCallSite(
            researchRoot,
            "inventory_transfer",
            "ForgottenGUI::showTradeWindow",
            "0x7905D0");
        if (result != 0)
            return result;
        result = CheckResearchCallSite(
            researchRoot,
            "body_shift",
            "PlayerInterface::recruit",
            "0x6920A0");
        if (result != 0)
            return result;
        return CheckResearchCallSite(
            researchRoot,
            "prospecting_window",
            "ProspectingWindow::showT",
            "0x48E260");
    }

    int TestProtocol2WorldModelFixtures(const std::string& fixtureRoot)
    {
        const std::string validPath =
            fixtureRoot + "/valid_multiple_platoons_and_commands.json";
        const std::string validPayload = ReadFile(validPath);
        if (validPayload.empty())
            return Fail("could not read Protocol 2.0 valid fixture");

        try
        {
            std::istringstream input(validPayload);
            boost::property_tree::ptree world;
            boost::property_tree::read_json(input, world);
            if (world.get<std::string>("protocol_version") != "2.0.0" ||
                world.count("squad") != 0U ||
                world.count("native_control") != 0U ||
                world.get_child("platoons.items").size() != 2U ||
                world.get_child(
                    "controller_commands.retained_commands.items").size() != 2U)
            {
                return Fail(
                    "Protocol 2.0 valid fixture lost its breaking topology");
            }
        }
        catch (const std::exception& error)
        {
            return Fail(
                "could not parse Protocol 2.0 valid fixture: " +
                std::string(error.what()));
        }

        const std::string oldPath =
            fixtureRoot + "/invalid_old_1_x_shape.json";
        const std::string oldPayload = ReadFile(oldPath);
        if (oldPayload.empty())
            return Fail("could not read Protocol 2.0 old-shape fixture");
        try
        {
            std::istringstream input(oldPayload);
            boost::property_tree::ptree oldWorld;
            boost::property_tree::read_json(input, oldWorld);
            if (oldWorld.get<std::string>("protocol_version") == "2.0.0" ||
                oldWorld.count("squad") != 1U ||
                oldWorld.get<std::string>("native_control.active_command_id").empty() ||
                oldWorld.count("roster") != 0U ||
                oldWorld.count("controller_commands") != 0U)
            {
                return Fail(
                    "Protocol 2.0 old-shape fixture no longer pins the rejected names");
            }
        }
        catch (const std::exception& error)
        {
            return Fail(
                "could not parse Protocol 2.0 old-shape fixture: " +
                std::string(error.what()));
        }
        return 0;
    }

    int TestPlayerTopologyFixture(const std::string& fixtureRoot)
    {
        const std::string path =
            fixtureRoot + "/valid_player_topology.json";
        const std::string payload = ReadFile(path);
        if (payload.empty())
            return Fail("could not read current player-topology fixture");

        try
        {
            std::istringstream input(payload);
            boost::property_tree::ptree topology;
            boost::property_tree::read_json(input, topology);
            const boost::property_tree::ptree& roster =
                topology.get_child("roster");
            boost::property_tree::ptree::const_iterator rosterIt =
                roster.begin();
            const boost::property_tree::ptree& work =
                rosterIt->second.get_child("work");
            const boost::property_tree::ptree& ordinaryOrders =
                work.get_child("ordinary_orders");
            if (topology.get<std::string>("protocol_version") != "2.0.0" ||
                topology.count("squad") != 0U ||
                topology.count("native_control") != 0U ||
                topology.get_child("controller_commands.commands").size() != 0U ||
                roster.size() != 3U ||
                rosterIt->second.count("selected") != 0U ||
                rosterIt->second.count("task_state") != 0U ||
                work.count("orders") != 0U ||
                work.count("permajobs") != 0U ||
                work.count("ordinary_orders") != 1U ||
                work.count("jobs") != 1U ||
                work.count("permanent_jobs") != 1U ||
                work.count("current_activity") != 1U ||
                ordinaryOrders.get_child("items").size() != 1U ||
                ordinaryOrders.get<std::string>("completeness") != "complete" ||
                ordinaryOrders.get<int>("known_total") != 1 ||
                work.get_child("jobs.items").size() != 0U ||
                work.get<int>("jobs.known_total") != 0 ||
                topology.get_child("platoons").size() != 2U ||
                topology.get<std::string>("active_platoon_id") !=
                    "platoon-beta" ||
                topology.get<std::string>("primary_character_id") !=
                    "character-beta-primary" ||
                topology.get_child("selected_character_ids").size() != 2U ||
                !topology.get<bool>("roster_complete") ||
                !topology.get<bool>("platoons_complete") ||
                !topology.get<bool>("selected_character_ids_complete"))
            {
                return Fail(
                    "current player-topology fixture lost a distinct authority");
            }
            ++rosterIt;
            if (rosterIt == roster.end() ||
                rosterIt->second.get<std::string>("id") !=
                    topology.get<std::string>("primary_character_id"))
            {
                return Fail(
                    "player-topology fixture no longer proves primary is not roster order");
            }
        }
        catch (const std::exception& error)
        {
            return Fail(
                "could not parse current player-topology fixture: " +
                std::string(error.what()));
        }
        return 0;
    }

    int TestInventoryScreenSemantics()
    {
        using KenshiAgentTelemetry::IsRegisteredShopInventoryOpen;
        using KenshiAgentTelemetry::IsTradeInventoryOpen;

        for (int ordinaryOwnerInventory = 0;
             ordinaryOwnerInventory <= 1;
             ++ordinaryOwnerInventory)
        {
            for (int shopInventoryObject = 0;
                 shopInventoryObject <= 1;
                 ++shopInventoryObject)
            {
                const bool expected = shopInventoryObject != 0;
                const bool actual = IsRegisteredShopInventoryOpen(
                    ordinaryOwnerInventory != 0,
                    shopInventoryObject != 0);
                if (actual != expected)
                {
                    return Fail(
                        "shop trade authority did not follow the registered "
                        "inventory object");
                }
            }
        }

        for (int anyInventory = 0; anyInventory <= 1; ++anyInventory)
        {
            for (int transientTrader = 0; transientTrader <= 1; ++transientTrader)
            {
                for (int registeredShop = 0; registeredShop <= 1; ++registeredShop)
                {
                    const bool expected =
                        anyInventory != 0 &&
                        (transientTrader != 0 || registeredShop != 0);
                    const bool actual = IsTradeInventoryOpen(
                        anyInventory != 0,
                        transientTrader != 0,
                        registeredShop != 0);
                    if (actual != expected)
                    {
                        return Fail(
                            "trade classification violated inventory and "
                            "shop-owner authority");
                    }
                }
            }
        }
        return 0;
    }

    int TestRuntimeContextMenuSemantics()
    {
        using namespace KenshiAgentTelemetry;

        RuntimeContextMenuTargetOwnership ownership;
        std::vector<int> taskTypeValues;
        RuntimeContextMenuObservation closed = ResolveRuntimeContextMenu(
            false,
            ownership,
            taskTypeValues,
            2);
        if (closed.open || closed.captured ||
            closed.probe != RUNTIME_CONTEXT_MENU_CLOSED)
        {
            return Fail("a closed context menu produced runtime orders");
        }

        UpdateRuntimeContextMenuTargetOwnership(
            ownership,
            true,
            true,
            "entity-target",
            "Iron Resource");
        taskTypeValues.push_back(87);
        taskTypeValues.push_back(9999);
        RuntimeContextMenuObservation exact = ResolveRuntimeContextMenu(
            true,
            ownership,
            taskTypeValues,
            2);
        if (!exact.open || !exact.captured ||
            exact.probe != RUNTIME_CONTEXT_MENU_CAPTURED ||
            exact.targetId != "entity-target" ||
            exact.taskTypeValues.size() != 2 ||
            exact.taskTypeValues[0] != 87 ||
            exact.taskTypeValues[1] != 9999 ||
            !exact.taskTypeValuesComplete)
        {
            return Fail(
                "an exact context menu did not preserve game-owned task IDs");
        }

        RuntimeContextMenuObservation bounded = ResolveRuntimeContextMenu(
            true,
            ownership,
            taskTypeValues,
            1);
        if (!bounded.captured || bounded.taskTypeValues.size() != 1 ||
            bounded.taskTypeValues[0] != 87 ||
            bounded.taskTypeValuesComplete)
        {
            return Fail("context menu order truncation was not explicit");
        }

        UpdateRuntimeContextMenuTargetOwnership(
            ownership,
            true,
            false,
            "entity-stale-target",
            "Stale Resource");
        RuntimeContextMenuObservation invalidTarget = ResolveRuntimeContextMenu(
            true,
            ownership,
            taskTypeValues,
            2);
        if (invalidTarget.captured ||
            invalidTarget.probe != RUNTIME_CONTEXT_MENU_INVALID_TARGET)
        {
            return Fail("an invalid context-menu target exposed orders");
        }

        UpdateRuntimeContextMenuTargetOwnership(
            ownership,
            true,
            true,
            "entity-target",
            "Iron Resource");
        UpdateRuntimeContextMenuTargetOwnership(
            ownership,
            false,
            true,
            "entity-target",
            "Iron Resource");
        if (ownership.captured || !ownership.targetId.empty())
            return Fail("closing a context menu retained its target authority");

        return 0;
    }

    int TestResourceProductionSemantics()
    {
        using namespace KenshiAgentTelemetry;

        if (EvaluateResourceProduction(false, 0, 5, false, false) !=
            RESOURCE_PRODUCTION_OUTPUT_UNKNOWN)
        {
            return Fail("unknown output inventory did not fail closed");
        }
        if (EvaluateResourceProduction(true, 0, 5, false, false) !=
            RESOURCE_PRODUCTION_APPROACHING)
        {
            return Fail("pre-task resource production did not remain pending");
        }
        if (EvaluateResourceProduction(true, 0, 5, true, false) !=
            RESOURCE_PRODUCTION_WORKING)
        {
            return Fail("an exact operating goal was mistaken for output");
        }
        if (EvaluateResourceProduction(true, 4, 5, true, true) !=
            RESOURCE_PRODUCTION_WORKING)
        {
            return Fail("partial output completed before the requested yield");
        }
        if (EvaluateResourceProduction(true, 5, 5, true, true) !=
            RESOURCE_PRODUCTION_OUTPUT_READY)
        {
            return Fail("produced output did not complete the option");
        }
        if (EvaluateResourceProduction(true, 0, 5, false, true) !=
            RESOURCE_PRODUCTION_TASK_ENDED)
        {
            return Fail("lost work after task observation did not fail");
        }
        if (EvaluateResourceTaskRelease(false, false, false) !=
            RESOURCE_TASK_RELEASE_NOT_OWNED)
        {
            return Fail("adopted resource work was claimed by the command");
        }
        if (EvaluateResourceTaskRelease(true, false, false) !=
            RESOURCE_TASK_RELEASE_REQUESTED)
        {
            return Fail("owned resource work did not request release");
        }
        if (EvaluateResourceTaskRelease(true, true, false) !=
            RESOURCE_TASK_RELEASE_WAITING)
        {
            return Fail("resource work completed without stable inactivity");
        }
        if (EvaluateResourceTaskRelease(true, true, true) !=
            RESOURCE_TASK_RELEASE_CONFIRMED)
        {
            return Fail("stably released resource work did not reach its terminal");
        }

        ResourceTaskReleaseConfirmationWindow releaseWindow;
        ResetResourceTaskReleaseConfirmationWindow(releaseWindow);
        if (ObserveResourceTaskReleaseConfirmation(
                releaseWindow,
                false,
                1000UL))
        {
            return Fail("resource release completed on its first inactive frame");
        }
        if (ObserveResourceTaskReleaseConfirmation(
                releaseWindow,
                false,
                1000UL + RESOURCE_TASK_RELEASE_CONFIRMATION_MS - 1UL))
        {
            return Fail("resource release completed before its stability window");
        }
        if (!ObserveResourceTaskReleaseConfirmation(
                releaseWindow,
                false,
                1000UL + RESOURCE_TASK_RELEASE_CONFIRMATION_MS))
        {
            return Fail("stable resource release did not confirm on time");
        }
        if (ObserveResourceTaskReleaseConfirmation(
                releaseWindow,
                true,
                5000UL))
        {
            return Fail("resumed resource work retained release confirmation");
        }
        return 0;
    }

    int TestNativeCommandRevisionTransportWindow()
    {
        using KenshiAgentTelemetry::IsNativeCommandRevisionWithinTransportWindow;

        if (!IsNativeCommandRevisionWithinTransportWindow(100ULL, 100ULL) ||
            !IsNativeCommandRevisionWithinTransportWindow(100ULL, 102ULL) ||
            !IsNativeCommandRevisionWithinTransportWindow(100ULL, 104ULL))
        {
            return Fail(
                "ordinary cross-process command transit was rejected as stale");
        }
        if (IsNativeCommandRevisionWithinTransportWindow(100ULL, 105ULL))
            return Fail("an expired native command crossed its revision window");
        if (IsNativeCommandRevisionWithinTransportWindow(101ULL, 100ULL))
            return Fail("a future native command basis was accepted");
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

    int TestNativeGroupCharacterCompletion()
    {
        using KenshiAgentTelemetry::HasGroupReachedDestination;
        using KenshiAgentTelemetry::NativeMovementPosition;

        std::vector<NativeMovementPosition> positions(2);
        positions[0].x = 3.0f;
        positions[0].z = 4.0f;
        positions[1].x = 20.0f;
        positions[1].z = 0.0f;
        float farthestX = 0.0f;
        float farthestZ = 0.0f;
        if (HasGroupReachedDestination(
                positions,
                0.0f,
                0.0f,
                farthestX,
                farthestZ))
        {
            return Fail("one arrived member masked a distant squadmate");
        }
        if (farthestX != 20.0f || farthestZ != 0.0f)
            return Fail("group stall ownership did not follow the farthest member");

        positions[1].x = 6.0f;
        positions[1].z = 8.0f;
        if (!HasGroupReachedDestination(
                positions,
                0.0f,
                0.0f,
                farthestX,
                farthestZ))
        {
            return Fail("a fully arrived group did not complete");
        }
        std::vector<NativeMovementPosition> empty;
        if (HasGroupReachedDestination(
                empty,
                0.0f,
                0.0f,
                farthestX,
                farthestZ))
        {
            return Fail("an empty selection completed a group walk");
        }

        positions[0].x = 30.0f;
        positions[0].z = 0.0f;
        positions[1].x = 100.0f;
        positions[1].z = 0.0f;
        if (HasGroupReachedDestination(
                positions,
                30.0f,
                0.0f,
                farthestX,
                farthestZ))
        {
            return Fail(
                "a member beyond another member's destination plane completed group travel");
        }
        if (farthestX != 100.0f || farthestZ != 0.0f)
            return Fail("group travel stall ownership lost its distant member");
        return 0;
    }

    int TestNativeMapTravelEntry()
    {
        using KenshiAgentTelemetry::EvaluateNativeMapTravel;
        using KenshiAgentTelemetry::MAP_TRAVEL_CANCEL_UNCONFIRMED;
        using KenshiAgentTelemetry::MAP_TRAVEL_COMPLETE;
        using KenshiAgentTelemetry::MAP_TRAVEL_CONTINUE;
        using KenshiAgentTelemetry::MAP_TRAVEL_ISSUE_INTERIOR_ORDER;

        if (EvaluateNativeMapTravel(
                false,
                true,
                false,
                true,
                false) != MAP_TRAVEL_ISSUE_INTERIOR_ORDER)
        {
            return Fail(
                "an exterior gate waypoint masqueraded as settlement arrival");
        }
        if (EvaluateNativeMapTravel(
                true,
                true,
                false,
                true,
                false) != MAP_TRAVEL_ISSUE_INTERIOR_ORDER)
        {
            return Fail(
                "town-border membership bypassed the gated entry boundary");
        }
        if (EvaluateNativeMapTravel(
                true,
                true,
                true,
                false,
                true) != MAP_TRAVEL_CONTINUE)
        {
            return Fail(
                "a transient wall crossing completed an unfinished interior leg");
        }
        if (EvaluateNativeMapTravel(
                true,
                true,
                true,
                true,
                false) != MAP_TRAVEL_ISSUE_INTERIOR_ORDER)
        {
            return Fail(
                "a wall crossing bypassed the controller-owned interior leg");
        }
        if (EvaluateNativeMapTravel(
                true,
                true,
                true,
                true,
                true) != MAP_TRAVEL_COMPLETE)
        {
            return Fail(
                "a completed interior leg with exact wall proof did not complete");
        }
        if (EvaluateNativeMapTravel(
                true,
                true,
                false,
                true,
                true) != MAP_TRAVEL_COMPLETE)
        {
            return Fail(
                "a completed exact-town interior leg depended on optional wall geometry");
        }
        if (EvaluateNativeMapTravel(
                true,
                false,
                false,
                false,
                false) != MAP_TRAVEL_COMPLETE)
        {
            return Fail("exact membership did not complete an ungated town");
        }
        if (EvaluateNativeMapTravel(
                false,
                true,
                true,
                false,
                true) != MAP_TRAVEL_CONTINUE)
        {
            return Fail("inside-walls state from another town completed travel");
        }
        if (EvaluateNativeMapTravel(
                false,
                true,
                false,
                true,
                true) != MAP_TRAVEL_CANCEL_UNCONFIRMED)
        {
            return Fail(
                "an exhausted interior leg invented unconfirmed town entry");
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

    int TestTrailingCameraPose()
    {
        using KenshiAgentTelemetry::NativeTrailingCameraPose;
        using KenshiAgentTelemetry::TryComputeTrailingCameraPose;

        NativeTrailingCameraPose north;
        if (!TryComputeTrailingCameraPose(
                0.0f,
                0.0f,
                0.0f,
                100.0f,
                0.0f,
                0.0f,
                30.0f,
                north))
        {
            return Fail("a northbound walk had no trailing camera pose");
        }
        const float quaternionLength =
            static_cast<float>(std::sqrt(
                north.w * north.w +
                north.x * north.x +
                north.y * north.y +
                north.z * north.z));
        if (std::fabs(quaternionLength - 1.0f) > 0.0001f ||
            std::fabs(north.facingX) > 0.0001f ||
            !(north.facingY < 0.0f) ||
            !(north.facingZ > 0.0f) ||
            !(north.w > 0.9f) ||
            !(north.x > 0.0f) ||
            std::fabs(north.y) > 0.0001f ||
            std::fabs(north.z) > 0.0001f ||
            std::fabs(north.zoom + 30.0f) > 0.0001f)
        {
            return Fail(
                "northbound trailing camera was not pitched down from behind");
        }

        NativeTrailingCameraPose turningEast;
        if (!TryComputeTrailingCameraPose(
                0.0f,
                0.0f,
                0.0f,
                1000.0f,
                12.0f,
                0.0f,
                30.0f,
                turningEast) ||
            !(turningEast.facingX > 0.9f) ||
            std::fabs(turningEast.facingZ) > 0.0001f ||
            !(turningEast.y > 0.0f))
        {
            return Fail(
                "trailing camera ignored a live turn toward the east");
        }

        NativeTrailingCameraPose stationary;
        if (TryComputeTrailingCameraPose(
                5.0f,
                5.0f,
                5.0f,
                5.0f,
                0.0f,
                0.0f,
                30.0f,
                stationary))
        {
            return Fail("a zero-length journey invented a camera direction");
        }
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
        nearest.hasScreenPosition = true;
        nearest.screenX = 0.4;
        nearest.screenY = 0.6;
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
        const std::string serialized =
            KenshiAgentTelemetry::SerializeNaturalResourceTarget(selected[0]);
        if (serialized.find(
                "\"screen_position\":{\"x\":0.4,\"y\":0.6}") ==
            std::string::npos)
        {
            return Fail(
                "a current world-target screen position was not serialized");
        }

        if (!KenshiAgentTelemetry::IsWorldTargetScanAtCapacity(128, 128) ||
            KenshiAgentTelemetry::IsWorldTargetScanAtCapacity(127, 128))
        {
            return Fail("world-target scan capacity was reported incorrectly");
        }
        return 0;
    }

    int TestStableCharacterIdentityAcrossContainerChanges()
    {
        const std::string loaded =
            KenshiAgentTelemetry::FormatStableCharacterIdentity(
                0x51ULL,
                0x02ULL,
                1U,
                1U,
                0x4fed2800U,
                1U,
                0x873b1f00U);
        const std::string active =
            KenshiAgentTelemetry::FormatStableCharacterIdentity(
                0x51ULL,
                0x02ULL,
                1U,
                0x14U,
                0x2a7bfc40U,
                1U,
                0x873b1f00U);
        if (loaded != active)
        {
            return Fail(
                "character identity changed when only its handle container changed");
        }

        const std::string firstObject =
            KenshiAgentTelemetry::FormatStableHandleIdentity(
                0x51ULL,
                0x02ULL,
                1U,
                1U,
                0x4fed2800U,
                1U,
                0x873b1f00U);
        const std::string secondObject =
            KenshiAgentTelemetry::FormatStableHandleIdentity(
                0x51ULL,
                0x02ULL,
                1U,
                0x14U,
                0x2a7bfc40U,
                1U,
                0x873b1f00U);
        if (firstObject == secondObject)
        {
            return Fail(
                "ordinary handle identity lost its container generation fence");
        }
        return 0;
    }

    int TestStableCharacterIdentityRegistryAcrossPlatoonMove()
    {
        KenshiAgentTelemetry::StableCharacterIdentityRegistry registry;
        const std::string before = registry.Resolve(
            0x12345000ULL,
            17,
            "entity-before-platoon-move");
        const std::string after = registry.Resolve(
            0x12345000ULL,
            17,
            "entity-after-platoon-move");
        if (before != after)
            return Fail("player identity changed while the live Character object remained valid");

        const std::string reused = registry.Resolve(
            0x12345000ULL,
            18,
            "entity-pointer-reused");
        if (reused != "entity-pointer-reused")
            return Fail("a reused Character address inherited the prior object's identity");

        registry.Clear();
        const std::string nextSession = registry.Resolve(
            0x12345000ULL,
            18,
            "entity-next-session");
        if (nextSession != "entity-next-session")
            return Fail("player identity registry survived an explicit session reset");
        return 0;
    }
}

int main(int argc, char** argv)
{
    if (argc != 5)
    {
        return Fail(
            "expected native-command, research, Protocol 2.0, and current telemetry fixture directories");
    }
    const int topologyResult = TestPlayerTopologyFixture(argv[4]);
    if (topologyResult != 0)
        return topologyResult;
    const int protocol2Result = TestProtocol2WorldModelFixtures(argv[3]);
    if (protocol2Result != 0)
        return protocol2Result;
    const int researchResult = TestResearchEvidence(argv[2]);
    if (researchResult != 0)
        return researchResult;
    const int capabilityResult = TestGameplayCapabilities();
    if (capabilityResult != 0)
        return capabilityResult;
    const int inventoryScreenResult = TestInventoryScreenSemantics();
    if (inventoryScreenResult != 0)
        return inventoryScreenResult;
    const int runtimeContextMenuResult = TestRuntimeContextMenuSemantics();
    if (runtimeContextMenuResult != 0)
        return runtimeContextMenuResult;
    const int resourceProductionResult = TestResourceProductionSemantics();
    if (resourceProductionResult != 0)
        return resourceProductionResult;
    const int revisionWindowResult =
        TestNativeCommandRevisionTransportWindow();
    if (revisionWindowResult != 0)
        return revisionWindowResult;
    const int timingResult = TestNativeMovementPauseTiming();
    if (timingResult != 0)
        return timingResult;
    const int completionResult = TestNativeDirectionCompletion();
    if (completionResult != 0)
        return completionResult;
    const int groupCompletionResult = TestNativeGroupCharacterCompletion();
    if (groupCompletionResult != 0)
        return groupCompletionResult;
    const int mapTravelResult = TestNativeMapTravelEntry();
    if (mapTravelResult != 0)
        return mapTravelResult;
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
    const int trailingCameraResult = TestTrailingCameraPose();
    if (trailingCameraResult != 0)
        return trailingCameraResult;
    const int naturalResourceResult = TestNaturalResourceAssessment();
    if (naturalResourceResult != 0)
        return naturalResourceResult;
    const int stableCharacterResult =
        TestStableCharacterIdentityAcrossContainerChanges();
    if (stableCharacterResult != 0)
        return stableCharacterResult;
    const int stableCharacterRegistryResult =
        TestStableCharacterIdentityRegistryAcrossPlatoonMove();
    if (stableCharacterRegistryResult != 0)
        return stableCharacterRegistryResult;

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
        targeted.selectedCharacterIds.size() != 2 ||
        targeted.selectedCharacterIds[0] != "entity-selected" ||
        targeted.selectedCharacterIds[1] != "entity-companion" ||
        targeted.targetId != "entity-destination" ||
        targeted.bearingDegrees != 0.0 ||
        targeted.distanceUnits != 0.0)
    {
        return Fail("targeted request did not retain its exact identity");
    }

    KenshiAgentTelemetry::NativeCommandRequest mapTravel;
    const std::string mapTravelPayload =
        ReadFile(prefix + "valid_map_travel_request.json");
    if (mapTravelPayload.empty())
        return Fail("could not read valid_map_travel_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            mapTravelPayload,
            mapTravel,
            rejectionReason))
    {
        return Fail(
            "valid map-travel request was rejected as " + rejectionReason);
    }
    if (mapTravel.command != "travel_to_map_destination" ||
        mapTravel.selectedCharacterIds.size() != 2 ||
        mapTravel.selectedCharacterIds[0] != "entity-selected" ||
        mapTravel.selectedCharacterIds[1] != "entity-companion" ||
        mapTravel.targetId != "entity-known-town" ||
        mapTravel.bearingDegrees != 0.0 ||
        mapTravel.distanceUnits != 0.0)
    {
        return Fail("map travel did not retain its exact known destination");
    }

    KenshiAgentTelemetry::NativeCommandRequest squadRegroup;
    const std::string squadRegroupPayload =
        ReadFile(prefix + "valid_squad_regroup_request.json");
    if (squadRegroupPayload.empty())
        return Fail("could not read valid_squad_regroup_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            squadRegroupPayload,
            squadRegroup,
            rejectionReason))
    {
        return Fail(
            "valid squad-regroup request was rejected as " + rejectionReason);
    }
    if (squadRegroup.command != "regroup_with_squad_member" ||
        squadRegroup.selectedCharacterId != "entity-bark" ||
        squadRegroup.targetId != "entity-plant" ||
        squadRegroup.bearingDegrees != 0.0 ||
        squadRegroup.distanceUnits != 0.0)
    {
        return Fail("squad regroup did not retain its exact actor and target");
    }

    KenshiAgentTelemetry::NativeCommandRequest squadSelection;
    const std::string squadSelectionPayload =
        ReadFile(prefix + "valid_squad_selection_request.json");
    if (squadSelectionPayload.empty())
        return Fail("could not read valid_squad_selection_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            squadSelectionPayload,
            squadSelection,
            rejectionReason))
    {
        return Fail(
            "valid squad-selection request was rejected as " + rejectionReason);
    }
    if (squadSelection.command != "select_squad_member" ||
        squadSelection.selectedCharacterIds.size() != 2 ||
        squadSelection.selectedCharacterIds[0] != "entity-bark" ||
        squadSelection.selectedCharacterIds[1] != "entity-plant" ||
        squadSelection.targetId != "entity-plant" ||
        squadSelection.bearingDegrees != 0.0 ||
        squadSelection.distanceUnits != 0.0)
    {
        return Fail("squad selection did not retain its basis and exact target");
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

    // Interface cleanup is game-wide. It cannot depend on a selected
    // character, because a blocking interface may survive selection or roster
    // loss and still needs a planner-reachable exit.
    KenshiAgentTelemetry::NativeCommandRequest interfaceClose;
    const std::string interfaceClosePayload =
        ReadFile(prefix + "valid_close_active_interface_request.json");
    if (interfaceClosePayload.empty())
        return Fail("could not read valid_close_active_interface_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            interfaceClosePayload,
            interfaceClose,
            rejectionReason))
    {
        return Fail(
            "valid empty-selection interface close was rejected as " +
            rejectionReason);
    }
    if (interfaceClose.command != "close_active_interface" ||
        !interfaceClose.selectedCharacterIds.empty() ||
        !interfaceClose.targetId.empty())
    {
        return Fail("interface close lost its game-wide recipient shape");
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
    if (contextAction.command != "perform_context_action" ||
        contextAction.contextAction != "operate" ||
        contextAction.targetId != "entity-natural-resource" ||
        contextAction.bearingDegrees != 0.0 ||
        contextAction.distanceUnits != 0.0)
    {
        return Fail("valid context action did not retain its exact target");
    }

    KenshiAgentTelemetry::NativeCommandRequest firstAid;
    const std::string firstAidPayload =
        ReadFile(prefix + "valid_first_aid_context_action_request.json");
    if (firstAidPayload.empty())
        return Fail("could not read valid_first_aid_context_action_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            firstAidPayload,
            firstAid,
            rejectionReason))
    {
        return Fail(
            "valid first-aid request was rejected as " + rejectionReason);
    }
    if (firstAid.command != "perform_context_action" ||
        firstAid.contextAction != "first_aid" ||
        firstAid.targetId != "entity-injured-squadmate")
    {
        return Fail("first-aid context action lost its exact semantic target");
    }

    // Body shift names its recipient in target_id. The empty selection is the
    // total-loss shape and must cross the native parser, not merely Python's
    // model, or the recovery command is unreachable when it matters.
    KenshiAgentTelemetry::NativeCommandRequest bodyShift;
    const std::string bodyShiftPayload =
        ReadFile(prefix + "valid_body_shift_request.json");
    if (bodyShiftPayload.empty())
        return Fail("could not read valid_body_shift_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            bodyShiftPayload,
            bodyShift,
            rejectionReason))
    {
        return Fail(
            "valid empty-selection body shift was rejected as " +
            rejectionReason);
    }
    if (bodyShift.command != "shift_into_body" ||
        !bodyShift.selectedCharacterIds.empty() ||
        bodyShift.targetId != "entity-body-to-enter")
    {
        return Fail("body shift lost its target-owned recipient shape");
    }

    KenshiAgentTelemetry::NativeCommandRequest resourceProduction;
    const std::string resourceProductionPayload =
        ReadFile(prefix + "valid_resource_production_request.json");
    if (resourceProductionPayload.empty())
        return Fail("could not read valid_resource_production_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            resourceProductionPayload,
            resourceProduction,
            rejectionReason))
    {
        return Fail(
            "valid resource-production request was rejected as " +
            rejectionReason);
    }
    if (resourceProduction.command != "produce_resource_output" ||
        resourceProduction.targetId != "entity-natural-resource" ||
        resourceProduction.minimumOutputQuantity != 5)
    {
        return Fail(
            "valid resource production did not retain its exact target");
    }

    // Parsed here because the Python fixture test parses it too, and only one
    // of those two was catching a rule stated in both languages. A trade window
    // carries Kenshi's own window type in the action field; the parser rejected
    // it as malformed for three live runs while Python happily produced it.
    KenshiAgentTelemetry::NativeCommandRequest tradeWindow;
    const std::string tradeWindowPayload =
        ReadFile(prefix + "valid_open_trade_window_request.json");
    if (tradeWindowPayload.empty())
        return Fail("could not read valid_open_trade_window_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            tradeWindowPayload,
            tradeWindow,
            rejectionReason))
    {
        return Fail(
            "valid trade-window request was rejected as " + rejectionReason);
    }
    if (tradeWindow.command != "open_trade_window" ||
        tradeWindow.destinationId.empty() ||
        tradeWindow.contextAction != "auto")
    {
        return Fail("valid trade window did not retain both parties and its type");
    }

    // Time control, parsed here for the same reason: the rule lives in this
    // parser and in Python's projection, and a command added to one and not the
    // other is how a well-formed request came back `malformed_request` three
    // live runs running.
    KenshiAgentTelemetry::NativeCommandRequest pauseRequest;
    const std::string pausePayload =
        ReadFile(prefix + "valid_pause_request.json");
    if (pausePayload.empty())
        return Fail("could not read valid_pause_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            pausePayload,
            pauseRequest,
            rejectionReason))
    {
        return Fail("valid pause request was rejected as " + rejectionReason);
    }
    if (pauseRequest.command != "pause" ||
        !pauseRequest.pauseRequested ||
        pauseRequest.speedMultiplier != 0.0)
    {
        return Fail("valid pause did not retain its requested state");
    }

    KenshiAgentTelemetry::NativeCommandRequest speedRequest;
    const std::string speedPayload =
        ReadFile(prefix + "valid_set_speed_request.json");
    if (speedPayload.empty())
        return Fail("could not read valid_set_speed_request.json");
    if (!KenshiAgentTelemetry::ParseNativeCommandRequest(
            speedPayload,
            speedRequest,
            rejectionReason))
    {
        return Fail("valid set_speed request was rejected as " + rejectionReason);
    }
    if (speedRequest.command != "set_speed" ||
        speedRequest.speedMultiplier != 3.0)
    {
        return Fail("valid set_speed did not retain its multiplier");
    }

    // A speed with no multiplier names no rate, and a pause carrying one is
    // saying two things at once. Both are malformed rather than defaulted,
    // because a defaulted rate is a guess about what the planner meant.
    KenshiAgentTelemetry::NativeCommandRequest degenerate;
    std::string speedWithoutRate = speedPayload;
    const size_t ratePosition = speedWithoutRate.find("\"speed_multiplier\": 3.0");
    if (ratePosition == std::string::npos)
        return Fail("set_speed fixture no longer carries a readable multiplier");
    speedWithoutRate.replace(
        ratePosition,
        std::string("\"speed_multiplier\": 3.0").size(),
        "\"speed_multiplier\": 0.0");
    if (KenshiAgentTelemetry::ParseNativeCommandRequest(
            speedWithoutRate,
            degenerate,
            rejectionReason))
    {
        return Fail("set_speed without a multiplier was accepted");
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
    naturalResource.operatorCapacityKnown = true;
    naturalResource.operatorCapacity = 2;
    naturalResource.currentOperatorsComplete = true;
    naturalResource.currentOperatorIds.push_back("character-paste");
    naturalResource.outputInventoryComplete = true;
    KenshiAgentTelemetry::NaturalResourceTargetSnapshot::OutputItem outputItem;
    outputItem.name = "Raw Copper";
    outputItem.quantity = 1;
    outputItem.itemType = 23;
    naturalResource.outputInventory.push_back(outputItem);
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

    // An unprobed target must be distinguishable from one that affords
    // nothing, so the probed flag travels beside the list rather than the
    // reader inferring emptiness.
    const std::string probedResourcePayload =
        ReadFile(prefix + "valid_probed_natural_resource.json");
    if (probedResourcePayload.empty())
    {
        return Fail(
            "could not read valid_probed_natural_resource.json");
    }
    KenshiAgentTelemetry::NaturalResourceTargetSnapshot probedResource =
        naturalResource;
    probedResource.advertisedTasksProbed = true;
    probedResource.advertisedTasks.push_back(
        KenshiAgentTelemetry::AdvertisedTask(
            26,
            "LOOT_TARGET",
            KenshiAgentTelemetry::AdvertisedTaskSource::MENU));
    probedResource.advertisedTasks.push_back(
        KenshiAgentTelemetry::AdvertisedTask(
            87,
            "OPERATE_MACHINERY",
            KenshiAgentTelemetry::AdvertisedTaskSource::ODDS));
    const std::string probedResourceSerialized =
        KenshiAgentTelemetry::SerializeNaturalResourceTarget(probedResource);
    std::string probedResourceExpected = probedResourcePayload;
    while (!probedResourceExpected.empty() &&
           (probedResourceExpected[probedResourceExpected.size() - 1] == '\r' ||
            probedResourceExpected[probedResourceExpected.size() - 1] == '\n'))
    {
        probedResourceExpected.erase(probedResourceExpected.size() - 1);
    }
    if (probedResourceSerialized != probedResourceExpected)
    {
        return Fail(
            "serialized probed natural resource diverged from fixture");
    }

    // Two probes answer for the same target and disagree in both directions,
    // so the union has to keep every task while keeping the better evidence.
    {
        std::vector<KenshiAgentTelemetry::AdvertisedTask> merged;
        KenshiAgentTelemetry::MergeAdvertisedTask(
            merged,
            KenshiAgentTelemetry::AdvertisedTask(
                26,
                "LOOT_TARGET",
                KenshiAgentTelemetry::AdvertisedTaskSource::ODDS));
        KenshiAgentTelemetry::MergeAdvertisedTask(
            merged,
            KenshiAgentTelemetry::AdvertisedTask(
                26,
                "LOOT_TARGET",
                KenshiAgentTelemetry::AdvertisedTaskSource::MENU));
        KenshiAgentTelemetry::MergeAdvertisedTask(
            merged,
            KenshiAgentTelemetry::AdvertisedTask(
                16,
                "ATTACK_ENEMIES",
                KenshiAgentTelemetry::AdvertisedTaskSource::MENU));
        KenshiAgentTelemetry::MergeAdvertisedTask(
            merged,
            KenshiAgentTelemetry::AdvertisedTask(
                16,
                "ATTACK_ENEMIES",
                KenshiAgentTelemetry::AdvertisedTaskSource::ODDS));
        if (merged.size() != 2)
            return Fail("advertised task merge did not deduplicate by value");
        if (merged[0].value != 26 ||
            merged[0].source != KenshiAgentTelemetry::AdvertisedTaskSource::MENU)
        {
            return Fail("menu evidence did not upgrade an odds-sourced task");
        }
        if (merged[1].value != 16 ||
            merged[1].source != KenshiAgentTelemetry::AdvertisedTaskSource::MENU)
        {
            return Fail("odds evidence downgraded a menu-sourced task");
        }
    }

    if (!KenshiAgentTelemetry::IsWithinTargetProbeBudget(0, 16) ||
        !KenshiAgentTelemetry::IsWithinTargetProbeBudget(15, 16) ||
        KenshiAgentTelemetry::IsWithinTargetProbeBudget(16, 16) ||
        KenshiAgentTelemetry::IsWithinTargetProbeBudget(17, 16) ||
        KenshiAgentTelemetry::IsWithinTargetProbeBudget(0, 0))
    {
        return Fail("target probe budget did not bound probing exactly");
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
    acknowledgement.contextAction =
        expected.get<std::string>("context_action");
    acknowledgement.bearingDegrees =
        expected.get<double>("bearing_degrees");
    acknowledgement.distanceUnits =
        expected.get<double>("distance_units");
    acknowledgement.minimumOutputQuantity =
        expected.get<unsigned int>("minimum_output_quantity");
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
        actual.get<std::string>("context_action") != "" ||
        !EqualDouble(actual.get<double>("bearing_degrees"), 90.0) ||
        !EqualDouble(actual.get<double>("distance_units"), 250.0) ||
        actual.get<unsigned int>("minimum_output_quantity") != 1 ||
        actual.get_child("selected_character_ids").begin()->second.data() !=
            "entity-selected" ||
        actual.get<unsigned long long>("accepted_at_telemetry_sequence") != 8)
    {
        return Fail(
            "serialized direction acknowledgement diverged from the fixture");
    }

    KenshiAgentTelemetry::NativeCommandAcknowledgement groupAcknowledgement;
    groupAcknowledgement.commandId =
        "cmd-fedcba9876543210fedcba9876543210";
    groupAcknowledgement.command = "travel_to_map_destination";
    groupAcknowledgement.status = "accepted";
    groupAcknowledgement.reason = "issued";
    groupAcknowledgement.targetId = "entity-known-town";
    groupAcknowledgement.selectedCharacterIds.push_back("entity-selected");
    groupAcknowledgement.selectedCharacterIds.push_back("entity-companion");
    groupAcknowledgement.basedOnTelemetrySequence = 20;
    groupAcknowledgement.acknowledgedAtTelemetrySequence = 21;
    groupAcknowledgement.hasAcceptedSequence = true;
    groupAcknowledgement.acceptedAtTelemetrySequence = 21;
    const std::string groupSerialized =
        KenshiAgentTelemetry::SerializeNativeCommandAcknowledgement(
            groupAcknowledgement);
    std::istringstream groupInput(groupSerialized);
    boost::property_tree::ptree groupActual;
    boost::property_tree::read_json(groupInput, groupActual);
    const boost::property_tree::ptree& groupSelected =
        groupActual.get_child("selected_character_ids");
    boost::property_tree::ptree::const_iterator groupSelectedIt =
        groupSelected.begin();
    if (groupSelected.size() != 2 ||
        groupSelectedIt->second.data() != "entity-selected")
    {
        return Fail(
            "serialized group acknowledgement lost its exact selection basis");
    }
    ++groupSelectedIt;
    if (groupSelectedIt->second.data() != "entity-companion")
        return Fail("serialized group acknowledgement reordered its selection basis");

    std::cout
        << "Native protocol fixtures and semantics passed."
        << std::endl;
    return 0;
}
