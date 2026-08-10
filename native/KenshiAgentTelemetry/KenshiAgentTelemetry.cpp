#include <Debug.h>
#include <core/Functions.h>
#include <kenshi/CharStats.h>
#include <kenshi/Character.h>
#include <kenshi/CharMovement.h>
#define CharacterMessage KenshiAgentAICharacterMessage
#include <kenshi/AI/AI.h>
#undef CharacterMessage
#include <kenshi/AI/AITaskSystem.h>
// The pinned KenshiLib headers declare taskPriority independently in
// AITaskSystem.h and Tasker.h, byte-identical in both. Rename the Tasker copy
// in this one translation unit, exactly as Platoon.h/Building.h's
// BuildingDesignation is handled below; the plugin passes neither across its
// boundary. Tasker is needed for the order and Job entries - key(), subject,
// and getDescription() all live on it.
#define taskPriority KenshiAgentTaskPriority
#define TP_JUST_ACTION KENSHI_AGENT_TP_JUST_ACTION
#define TP_FLUFF KENSHI_AGENT_TP_FLUFF
#define TP_NON_URGENT KENSHI_AGENT_TP_NON_URGENT
#define TP_URGENT KENSHI_AGENT_TP_URGENT
#define TP_OBEDIENCE KENSHI_AGENT_TP_OBEDIENCE
#define TP_MAX_SIZE KENSHI_AGENT_TP_MAX_SIZE
#include <kenshi/Tasker.h>
#undef taskPriority
#undef TP_JUST_ACTION
#undef TP_FLUFF
#undef TP_NON_URGENT
#undef TP_URGENT
#undef TP_OBEDIENCE
#undef TP_MAX_SIZE
#include <kenshi/Item.h>
#include <kenshi/Inventory.h>
#include <kenshi/MedicalSystem.h>
#include <kenshi/CameraClass.h>
#include <kenshi/Dialogue.h>
#include <kenshi/Faction.h>
#include <kenshi/GameWorld.h>
#include <kenshi/ZoneManager.h>
#include <kenshi/Globals.h>
#include <kenshi/Platoon.h>
// The pinned KenshiLib headers declare BuildingDesignation independently in
// Platoon.h and Building.h. Rename the building-header copy in this one
// translation unit; both are ABI-identical enums and this plugin does not pass
// either type across its boundary.
#define BuildingDesignation KenshiAgentBuildingDesignation
#define BD_NONE KENSHI_AGENT_BD_NONE
#define BD_SHOP KENSHI_AGENT_BD_SHOP
#define BD_BARRACKS KENSHI_AGENT_BD_BARRACKS
#define BD_BAR KENSHI_AGENT_BD_BAR
#define BD_HOSPITAL KENSHI_AGENT_BD_HOSPITAL
#define BD_ARMOURY KENSHI_AGENT_BD_ARMOURY
#define BD_TREASURE KENSHI_AGENT_BD_TREASURE
#define BD_PRISON KENSHI_AGENT_BD_PRISON
#define BD_HQ KENSHI_AGENT_BD_HQ
#define BD_RESIDENTIAL KENSHI_AGENT_BD_RESIDENTIAL
#define BD_SLAVE_STORAGE KENSHI_AGENT_BD_SLAVE_STORAGE
#define BD_RESIDENTIAL_SMALL KENSHI_AGENT_BD_RESIDENTIAL_SMALL
#include <kenshi/Building/Building.h>
#undef BuildingDesignation
#undef BD_NONE
#undef BD_SHOP
#undef BD_BARRACKS
#undef BD_BAR
#undef BD_HOSPITAL
#undef BD_ARMOURY
#undef BD_TREASURE
#undef BD_PRISON
#undef BD_HQ
#undef BD_RESIDENTIAL
#undef BD_SLAVE_STORAGE
#undef BD_RESIDENTIAL_SMALL
#include <kenshi/Building/DoorStuff.h>
#include <kenshi/Building/ProductionBuilding.h>
#include <kenshi/SharedKing.h>
#include <kenshi/Town.h>
#include <kenshi/PlayerInterface.h>
#include <kenshi/RootObject.h>
#include <kenshi/SaveManager.h>
#include <kenshi/ShopTrader.h>
#include <kenshi/gui/DialogueWindow.h>
#include <kenshi/gui/ProspectingWindow.h>
#include <kenshi/gui/ForgottenGUI.h>
#include <kenshi/gui/InventoryGUI.h>
#include <kenshi/gui/ManagementScreen.h>
#include <kenshi/gui/TitleScreen.h>
#include <kenshi/gui/ToolTip.h>
#include <kenshi/util/UtilityT.h>
#include <mygui/MyGUI_Button.h>
#include <mygui/MyGUI_ImageBox.h>
#include <mygui/MyGUI_Window.h>
#include <mygui/MyGUI_Gui.h>
#include <mygui/MyGUI_TextBox.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <exception>
#include <iomanip>
#include <deque>
#include <locale>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "AtomicJsonWriter.h"
#include "GameplayCapabilities.generated.h"
#include "ItemTypeVocabulary.generated.h"
#include "TaskTypeVocabulary.generated.h"
#include "InventoryScreenSemantics.h"
#include "NativeCommandProtocol.h"
#include "NativeCommandTiming.h"
#include "NativeMovementSemantics.h"
#include "ResourceProductionSemantics.h"
#include "RuntimeContextMenuSemantics.h"
#include "WorldTargetProtocol.h"

// The declaration `kenshi/gui/ContextMenu.h` would have supplied, restated here
// because that header cannot be included.
//
// It defines `class ContextMenu` a second time, byte-identical to the one in
// `kenshi/PlayerInterface.h`, and two identical definitions in one translation
// unit is still a redefinition. Only `show` is needed, and only to take its
// address: `GetRealAddress(&ContextMenuGUI::show)` resolves the linker stub
// KenshiLib.lib exports, and MSVC mangles that from the class name, the
// function name, and the parameter types alone -- not from the bases or the
// members. Nothing here is ever constructed or dereferenced; the pointer is
// taken once at hook time and passed straight back through afterwards.
class ContextMenuGUI
{
public:
    void show(
        const lektor<int>& ordersList,
        const std::string& _name,
        bool offset);
};

namespace
{
    using KenshiAgentTelemetry::IsValidCommandId;
    using KenshiAgentTelemetry::NativeCommandAcknowledgement;
    using KenshiAgentTelemetry::NativeCommandRequest;
    using KenshiAgentTelemetry::NaturalResourceAssessment;
    using KenshiAgentTelemetry::NaturalResourceTargetSnapshot;
    using KenshiAgentTelemetry::ParseNativeCommandRequest;
    using KenshiAgentTelemetry::AssessNaturalResource;
    using KenshiAgentTelemetry::IsWorldTargetScanAtCapacity;
    using KenshiAgentTelemetry::SelectNearestNaturalResourceTargets;
    using KenshiAgentTelemetry::SerializeNativeCommandAcknowledgement;
    using KenshiAgentTelemetry::SerializeNaturalResourceTarget;

    const unsigned int MAX_TRACKED_SHOP_TRADERS = 256;
    const float NEARBY_CHARACTER_RADIUS = 400.0f;
    const int MAX_NEARBY_CHARACTERS = 64;
    const float NEAR_WORLD_CONTEXT_TARGET_RADIUS = 400.0f;
    const float WORLD_CONTEXT_TARGET_RADIUS = 2000.0f;
    const int MAX_NEAR_WORLD_CONTEXT_BUILDINGS = 128;
    const int MAX_OUTER_WORLD_CONTEXT_BUILDINGS = 256;
    const unsigned int MAX_WORLD_CONTEXT_TARGETS = 128;
    // Nearest targets asked what they afford, per snapshot. The frontier
    // needs each (kind, task) pair once, not every target every 500ms, and
    // the agent can only act on what is near it. Unprobed targets say so.
    const unsigned int MAX_PROBED_WORLD_TARGETS = 16;
    // Probing builds a context menu and walks the whole task vocabulary per
    // target, so nearby people get a tighter budget than world targets: there
    // are more of them, and only the close ones are actionable anyway.
    const unsigned int MAX_PROBED_NEARBY_CHARACTERS = 8;
    // Full-discovery scan. Every object category Kenshi declares is asked, so
    // a category nobody anticipated is found rather than excluded by omission.
    // It reuses the existing radii rather than introducing its own, so
    // "nearby" means one thing across telemetry, discovery, and command lookup.
    const int MAX_DISCOVERED_PER_CATEGORY = 8;
    // Objects probed per 500ms snapshot. The frontier needs each (category,
    // task) pair once, not every object every snapshot.
    const unsigned int MAX_DISCOVERY_PROBES_PER_SNAPSHOT = 48;
    // Categories examined per snapshot, advancing each time so every one is
    // reached within a few snapshots instead of the later ones starving.
    const unsigned int DISCOVERY_CATEGORIES_PER_SNAPSHOT = 24;
    // Characters live in their own index and are the richest target
    // class, so they are scanned every snapshot rather than by rotation.
    const int MAX_DISCOVERED_CHARACTERS = 16;
    const unsigned int MAX_KNOWN_MAP_DESTINATIONS = 64;
    // Raised from 64 after button-priority passes pushed dialogue options -
    // which are TextBox widgets, walked last - out of the export entirely.
    // Prioritizing one role only helps if the cap is not the binding
    // constraint, so give the whole set room rather than trading roles off.
    const unsigned int MAX_VISIBLE_UI_CONTROLS = 224;
    const unsigned int MAX_INVENTORY_ITEMS = 64;
    const unsigned int MAX_VISITED_UI_WIDGETS = 2048;
    const unsigned int MAX_UI_WIDGET_DEPTH = 32;
    const unsigned int MAX_NATIVE_COMMAND_BYTES = 16384;
    const unsigned int MAX_NATIVE_ACKNOWLEDGEMENTS = 16;
    const unsigned int MAX_RUNTIME_CONTEXT_MENU_TASK_TYPES = 64;
    // Kenshi opens at most a handful of inventory windows at once, and a
    // trade is two. These bound the export without bounding real play.
    const unsigned int MAX_OPEN_INVENTORIES = 8;
    const unsigned int MAX_INVENTORY_SECTIONS = 16;
    const unsigned int MAX_INVENTORY_ITEMS_PER_SECTION = 128;
    const unsigned int MAX_RESOURCE_OUTPUT_ITEMS = 128;
    const wchar_t* NATIVE_COMMAND_REQUEST_FILE_W =
        L"native_command.request.json";
    const char* PROTOCOL_VERSION = "2.0.0";

    typedef void (*PlayerInterfaceUpdateFunction)(PlayerInterface*);
    typedef void (*TitleScreenUpdateFunction)(TitleScreen*);
    typedef void (*GameWorldResetFunction)(GameWorld*);
    typedef void (*ContextMenuShowFunction)(ContextMenu*, bool, RootObject*);
    typedef void (*ContextMenuGUIShowFunction)(
        ContextMenuGUI*,
        const lektor<int>&,
        const std::string&,
        bool);
    typedef ShopTrader* (*ShopTraderConstructorFunction)(ShopTrader*, Character*);
    typedef void (*ShopTraderDestructorFunction)(ShopTrader*);

    struct TrackedShopTrader
    {
        ShopTrader* object;
        Character* owner;
    };

    // What the active command's target handle actually is.
    //
    // This was inferred, not recorded: monitoring asked
    // `expectedTask == FIRST_AID_ORDER` to decide whether the target was a
    // squad character or a building, so a task value stood in for a target
    // kind. A character order is neither, and it inherited the building branch
    // -- `targetHandle.getBuilding()` on a character handle returns NULL, and
    // the order was cancelled as `target_lifetime_changed` on the very update
    // after Kenshi had accepted and obeyed it. The two characters really did
    // enter combat; the plug-in then reported the target had ceased to exist.
    //
    // Recording the kind removes the inference. A new command that names a new
    // kind of target has to say so, and monitoring dispatches on what it said.
    enum NativeCommandTargetKind
    {
        NATIVE_TARGET_NONE,
        NATIVE_TARGET_BUILDING,
        NATIVE_TARGET_SQUAD_CHARACTER,
        NATIVE_TARGET_NEARBY_CHARACTER
    };

    struct ActiveNativeCommand
    {
        bool active;
        std::string commandId;
        std::string targetId;
        // The other side of a two-sided command. A trade window is only open
        // when *both* requested owners are, so the pair has to be remembered to
        // be checked.
        std::string destinationId;
        std::string selectedCharacterId;
        std::vector<std::string> selectedCharacterIds;
        hand targetHandle;
        hand selectedHandle;
        // A walk has no conversation to open, so it cannot be judged finished
        // the way an approach is. It finishes by arriving.
        bool isWalk;
        bool hasFixedDestination;
        bool isMapTravel;
        bool isSquadRegroup;
        bool mapInteriorOrderIssued;
        // A parameter-free building exit succeeds after the selected character
        // remains outside every building or tightly reaches the native-resolved
        // outside-door point. Kenshi can retain its indoor handle across a
        // visually complete doorway traversal.
        bool isBuildingExit;
        bool isContextAction;
        // A trade window is asked for, not opened on the spot: `showTradeWindow`
        // records the request and the GUI pairs the windows on a later update.
        bool isTradeWindowPending;
        // One exact reply from the current ordered conversation. Completion
        // belongs to a later UI update that proves the conversation advanced.
        bool isDialogueOptionPending;
        std::string dialogueOptionText;
        std::vector<std::string> dialogueOptionsBefore;
        DWORD dialogueOptionStartedTick;
        // `showT` begins Kenshi's timed prospecting lifecycle. The result
        // window appears only after its progress bar finishes, so capture and
        // close belong to later update frames rather than request dispatch.
        bool isProspectingSurveyPending;
        bool prospectingResultObserved;
        // Prospecting progress advances with Kenshi's world clock. When this
        // command finds a paused world it owns the temporary 1x resume and
        // must restore pause before publishing any terminal.
        bool prospectingPlaybackOwned;
        DWORD prospectingStartedTick;
        DWORD prospectingHiddenSinceTick;
        NativeCommandTargetKind targetKind;
        bool isResourceProduction;
        bool resourceTaskObserved;
        bool resourceTaskIssuedByCommand;
        bool resourceTaskReleaseRequested;
        KenshiAgentTelemetry::ResourceTaskReleaseConfirmationWindow
            resourceTaskReleaseWindow;
        unsigned int minimumOutputQuantity;
        TaskType expectedTask;
        // One uninterrupted pause may mean an abandoned movement order, but
        // short paused gaps are how the stop-motion controller safely pulses.
        KenshiAgentTelemetry::NativeMovementPauseWindow pauseWindow;
        KenshiAgentTelemetry::NativeMovementStallWindow stallWindow;
        KenshiAgentTelemetry::NativeOutdoorConfirmationWindow outdoorWindow;
        float originX;
        float originZ;
        float destinationX;
        float destinationZ;
    };

    PlayerInterfaceUpdateFunction g_originalPlayerInterfaceUpdate = NULL;
    TitleScreenUpdateFunction g_originalTitleScreenUpdate = NULL;
    GameWorldResetFunction g_originalGameWorldReset = NULL;
    ContextMenuShowFunction g_originalContextMenuShow = NULL;
    ContextMenuGUIShowFunction g_originalContextMenuGUIShow = NULL;
    // Set only for the duration of one silent probe, and only ever on the
    // thread already inside `PlayerInterface::update`.
    bool g_contextMenuProbeActive = false;
    bool g_contextMenuProbeInstalled = false;
    ShopTraderConstructorFunction g_originalShopTraderConstructor = NULL;
    ShopTraderDestructorFunction g_originalShopTraderDestructor = NULL;
    TrackedShopTrader g_trackedShopTraders[MAX_TRACKED_SHOP_TRADERS];
    unsigned int g_trackedShopTraderCount = 0;
    bool g_shopTraderRegistryReady = false;
    bool g_shopTraderRegistryOverflow = false;
    unsigned long long g_sequence = 0;
    DWORD g_lastSnapshotTick = 0;
    bool g_sampling = false;
    unsigned long long g_processGeneration = 0;
    unsigned long long g_sessionGeneration = 0;
    KenshiAgentTelemetry::StableCharacterIdentityRegistry
        g_playerCharacterIdentityRegistry;
    unsigned long long g_nativeCommandSequence = 0;
    std::string g_lastNativeCommand;
    std::string g_lastNativeCommandResult;
    std::string g_lastNativeCommandTarget;
    std::string g_lastNativeCommandTargetId;
    NativeCommandAcknowledgement
        g_nativeAcknowledgements[MAX_NATIVE_ACKNOWLEDGEMENTS];
    unsigned int g_nativeAcknowledgementCount = 0;
    // A title transition changes identity sessions before the ordinary
    // snapshot cadence can publish its accepted record. Retain exactly that
    // one record through reset(s) until the first loaded-world frame emits it.
    bool g_titleTransitionAcknowledgementPending = false;
    ActiveNativeCommand g_activeNativeCommand;
    // Body-shift probe only: the last body released out of the active
    // roster, so it can be seized back without searching a roster it left.
    hand g_shiftProbeReleased;
    KenshiAgentTelemetry::RuntimeContextMenuTargetOwnership
        g_runtimeContextMenuTarget;
    std::wstring g_outputDirectory;
    // Last-write stamp of the native command request file, so the plug-in can
    // notice a new request without the agent pressing a key at the game.
    DWORD g_lastRequestWriteLow = 0;
    DWORD g_lastRequestWriteHigh = 0;
    // The stamp seen last frame. A request is only read once its write stamp
    // has held still for a frame, so the plug-in cannot catch a partial write.
    DWORD g_pendingRequestWriteLow = 0;
    DWORD g_pendingRequestWriteHigh = 0;

    class SamplingGuard
    {
    public:
        explicit SamplingGuard(bool& sampling)
            : sampling_(sampling)
        {
            sampling_ = true;
        }

        ~SamplingGuard()
        {
            sampling_ = false;
        }

    private:
        SamplingGuard(const SamplingGuard&);
        SamplingGuard& operator=(const SamplingGuard&);
        bool& sampling_;
    };

    void ResetSessionState()
    {
        NativeCommandAcknowledgement titleTransitionAcknowledgement;
        const bool preserveTitleTransitionAcknowledgement =
            g_titleTransitionAcknowledgementPending &&
            g_nativeAcknowledgementCount > 0;
        if (preserveTitleTransitionAcknowledgement)
        {
            titleTransitionAcknowledgement =
                g_nativeAcknowledgements[g_nativeAcknowledgementCount - 1];
        }
        ++g_sessionGeneration;
        if (g_sessionGeneration == 0)
            g_sessionGeneration = 1;
        g_playerCharacterIdentityRegistry.Clear();
        g_trackedShopTraderCount = 0;
        g_shopTraderRegistryOverflow = false;
        g_nativeCommandSequence = 0;
        g_lastNativeCommand.clear();
        g_lastNativeCommandResult.clear();
        g_lastNativeCommandTarget.clear();
        g_lastNativeCommandTargetId.clear();
        g_nativeAcknowledgementCount = 0;
        if (preserveTitleTransitionAcknowledgement)
        {
            g_nativeAcknowledgements[0] = titleTransitionAcknowledgement;
            g_nativeAcknowledgementCount = 1;
            g_nativeCommandSequence = 1;
            g_lastNativeCommand = titleTransitionAcknowledgement.command;
            g_lastNativeCommandResult = titleTransitionAcknowledgement.reason;
            g_lastNativeCommandTargetId =
                titleTransitionAcknowledgement.targetId;
        }
        KenshiAgentTelemetry::UpdateRuntimeContextMenuTargetOwnership(
            g_runtimeContextMenuTarget,
            false,
            false,
            "",
            "");
        g_activeNativeCommand.active = false;
        g_activeNativeCommand.commandId.clear();
        g_activeNativeCommand.targetId.clear();
        g_activeNativeCommand.destinationId.clear();
        g_activeNativeCommand.selectedCharacterId.clear();
        g_activeNativeCommand.selectedCharacterIds.clear();
        // Cleared with the rest, or a finished walk would leave the next
        // approach being judged by arrival instead of by dialogue.
        g_activeNativeCommand.isWalk = false;
        g_activeNativeCommand.hasFixedDestination = false;
        g_activeNativeCommand.isMapTravel = false;
        g_activeNativeCommand.isSquadRegroup = false;
        g_activeNativeCommand.mapInteriorOrderIssued = false;
        g_activeNativeCommand.isBuildingExit = false;
        g_activeNativeCommand.isContextAction = false;
        g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.isDialogueOptionPending = false;
        g_activeNativeCommand.dialogueOptionText.clear();
        g_activeNativeCommand.dialogueOptionsBefore.clear();
        g_activeNativeCommand.dialogueOptionStartedTick = 0;
        g_activeNativeCommand.isProspectingSurveyPending = false;
        g_activeNativeCommand.prospectingResultObserved = false;
        g_activeNativeCommand.prospectingPlaybackOwned = false;
        g_activeNativeCommand.prospectingStartedTick = 0;
        g_activeNativeCommand.prospectingHiddenSinceTick = 0;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
        g_activeNativeCommand.isResourceProduction = false;
        g_activeNativeCommand.resourceTaskObserved = false;
        g_activeNativeCommand.resourceTaskIssuedByCommand = false;
        g_activeNativeCommand.resourceTaskReleaseRequested = false;
        KenshiAgentTelemetry::ResetResourceTaskReleaseConfirmationWindow(
            g_activeNativeCommand.resourceTaskReleaseWindow);
        g_activeNativeCommand.minimumOutputQuantity = 1;
        g_activeNativeCommand.expectedTask = NULL_TASK;
        g_activeNativeCommand.originX = 0.0f;
        g_activeNativeCommand.originZ = 0.0f;
        g_activeNativeCommand.destinationX = 0.0f;
        g_activeNativeCommand.destinationZ = 0.0f;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
        KenshiAgentTelemetry::ResetNativeMovementStallWindow(
            g_activeNativeCommand.stallWindow);
        KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
            g_activeNativeCommand.outdoorWindow);
    }

    std::string JsonEscape(const std::string& input)
    {
        std::ostringstream output;
        for (std::string::const_iterator it = input.begin(); it != input.end(); ++it)
        {
            const unsigned char c = static_cast<unsigned char>(*it);
            switch (c)
            {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if (c < 0x20)
                {
                    output << "\\u"
                           << std::hex << std::setw(4) << std::setfill('0')
                           << static_cast<int>(c)
                           << std::dec << std::setw(0);
                }
                else
                    output << static_cast<char>(c);
                break;
            }
        }
        return output.str();
    }

    const char* JsonBool(bool value)
    {
        return value ? "true" : "false";
    }

    unsigned long long CreateProcessGeneration()
    {
        FILETIME creationTime;
        FILETIME exitTime;
        FILETIME kernelTime;
        FILETIME userTime;
        if (!GetProcessTimes(
                GetCurrentProcess(),
                &creationTime,
                &exitTime,
                &kernelTime,
                &userTime))
        {
            GetSystemTimeAsFileTime(&creationTime);
        }

        ULARGE_INTEGER created;
        created.LowPart = creationTime.dwLowDateTime;
        created.HighPart = creationTime.dwHighDateTime;
        unsigned long long generation =
            created.QuadPart ^
            (static_cast<unsigned long long>(GetCurrentProcessId()) << 32);
        generation ^= generation >> 33;
        generation *= 0xff51afd7ed558ccdULL;
        generation ^= generation >> 33;
        return generation != 0 ? generation : 1;
    }

    std::string IdentitySessionId()
    {
        std::ostringstream value;
        value << "session-"
              << std::hex << std::setfill('0')
              << std::setw(16) << g_processGeneration
              << "-" << std::setw(16) << g_sessionGeneration;
        return value.str();
    }

    std::string StableEntityId(const hand& handle)
    {
        if (!handle.isValid())
            return "";
        return KenshiAgentTelemetry::FormatStableHandleIdentity(
            g_processGeneration,
            g_sessionGeneration,
            static_cast<unsigned int>(handle.type),
            handle.container,
            handle.containerSerial,
            handle.index,
            handle.serial);
    }

    std::string StableEntityId(Character* character)
    {
        if (character == NULL || !character->isValid())
            return "";
        const hand& handle = character->getHandle();
        const std::string candidate =
            KenshiAgentTelemetry::FormatStableCharacterIdentity(
            g_processGeneration,
            g_sessionGeneration,
            static_cast<unsigned int>(handle.type),
            handle.container,
            handle.containerSerial,
            handle.index,
            handle.serial);
        if (!character->isPlayerCharacter())
            return candidate;
        // A live management-screen drag keeps the Character object and
        // validKey but replaces the player's handle index and serial. Preserve
        // the first session-scoped identity for that engine object; a world
        // reset clears the registry before any new session can reuse a pointer.
        return g_playerCharacterIdentityRegistry.Resolve(
            reinterpret_cast<unsigned long long>(character),
            character->validKey,
            candidate);
    }

    std::string StablePlatoonId(ActivePlatoon* platoon)
    {
        if (platoon == NULL || platoon->me == NULL || !platoon->me->isValid())
            return "";
        const std::string engineId = platoon->me->getPlatoonStringID();
        if (engineId.empty())
            return "";
        return "platoon-" + engineId;
    }

    struct PlayerPlatoonTopology
    {
        std::string id;
        std::string name;
        std::vector<std::string> memberIds;
        ActivePlatoon* active;

        PlayerPlatoonTopology()
            : active(NULL)
        {
        }
    };

    KenshiAgentTelemetry::RuntimeContextMenuObservation
    ObserveRuntimeContextMenu(PlayerInterface* player)
    {
        using KenshiAgentTelemetry::ResolveRuntimeContextMenu;
        std::vector<int> taskTypeValues;
        if (player == NULL)
        {
            return ResolveRuntimeContextMenu(
                false,
                g_runtimeContextMenuTarget,
                taskTypeValues,
                MAX_RUNTIME_CONTEXT_MENU_TASK_TYPES);
        }

        ContextMenu& contextMenu = player->contextMenu;
        const bool menuOpen = contextMenu.isVisible();
        if (!menuOpen)
        {
            KenshiAgentTelemetry::UpdateRuntimeContextMenuTargetOwnership(
                g_runtimeContextMenuTarget,
                false,
                false,
                "",
                "");
        }
        if (menuOpen)
        {
            for (unsigned int orderIndex = 0;
                 orderIndex < contextMenu.orders.size();
                 ++orderIndex)
            {
                taskTypeValues.push_back(contextMenu.orders[orderIndex]);
            }
        }
        return ResolveRuntimeContextMenu(
            menuOpen,
            g_runtimeContextMenuTarget,
            taskTypeValues,
            MAX_RUNTIME_CONTEXT_MENU_TASK_TYPES);
    }

    void ContextMenuShowHook(ContextMenu* menu, bool on, RootObject* target)
    {
        std::string targetId;
        std::string targetName;
        const bool targetValid = target != NULL && target->isValid();
        if (targetValid)
        {
            Character* character = target->getHandle().getCharacter();
            targetId = character != NULL && character->isValid()
                ? StableEntityId(character)
                : StableEntityId(target->getHandle());
            targetName = target->getName();
        }
        KenshiAgentTelemetry::UpdateRuntimeContextMenuTargetOwnership(
            g_runtimeContextMenuTarget,
            on,
            targetValid,
            targetId,
            targetName);
        g_originalContextMenuShow(menu, on, target);
    }

    // The one line that turns Kenshi's menu into a question instead of an act.
    //
    // `ContextMenu::showContextMenu` decides which orders apply to a target and
    // then hands them to `ContextMenuGUI::show` to draw. Kenshi already keeps
    // those two apart, so muting the second leaves the first intact: during a
    // probe the orders are computed exactly as a right-click computes them, and
    // no widget is ever built, shown, or destroyed.
    void ContextMenuGUIShowHook(
        ContextMenuGUI* gui,
        const lektor<int>& ordersList,
        const std::string& name,
        bool offset)
    {
        if (g_contextMenuProbeActive)
            return;
        g_originalContextMenuGUIShow(gui, ordersList, name, offset);
    }

    bool SameHandleIdentity(const hand& left, const hand& right)
    {
        return left.type == right.type &&
               left.container == right.container &&
               left.containerSerial == right.containerSerial &&
               left.index == right.index &&
               left.serial == right.serial;
    }

    // Whether two handles name the same character, tolerating a container move.
    //
    // `SameHandleIdentity` compares all five fields of a `hand`, two of which -
    // `container` and `containerSerial` - describe where the object currently
    // lives rather than who it is. A character who changes platoon keeps its
    // `type`, `index` and `serial` and gets a new container, measured live:
    //
    //   stored = 1/ 1/2795410176/2/931152320
    //   current= 1/39/2860473600/2/931152320
    //
    // Selection survives that move: the released body stayed selected and stayed
    // controllable, taking orders while absent from the squad menu. So asking
    // membership with the strict compare returns a false negative, which is what
    // made `selected_character_ids` and the per-character `selected` flag
    // contradict each other and fail the telemetry snapshot's own validator.
    //
    // Only for membership - "is this character in this set". Target identity and
    // object lifetime still use the strict compare, because there the question
    // really is whether this exact reference is still the one.
    bool SameCharacterIdentity(const hand& left, const hand& right)
    {
        if (SameHandleIdentity(left, right))
            return true;
        Character* leftCharacter = left.getCharacter();
        Character* rightCharacter = right.getCharacter();
        if (leftCharacter == NULL || rightCharacter == NULL)
            return false;
        const std::string leftId = StableEntityId(leftCharacter);
        if (leftId.empty())
            return false;
        return leftId == StableEntityId(rightCharacter);
    }

    bool IsSelected(PlayerInterface* player, const hand& handle)
    {
        // Kenshi's two answers to "is this character selected" disagree, and
        // this reconciles them onto the one that decides who receives orders.
        //
        // `isObjectSelected` looks the character up by its current handle.
        // `selected_character_ids` walks `selectedCharacters` and resolves each
        // stored entry forward. A `hand` carries its container, so a character
        // who changes platoon while selected keeps a stored entry naming the
        // old container: the walk still finds them, the lookup no longer does.
        //
        // Both squad members being knocked unconscious does exactly that --
        // measured live, stored container 1 against current container 45 for
        // both -- so every member reported `selected: false` while
        // `selected_character_ids` listed them all. The snapshot's own
        // consistency validator then rejected the whole thing, and telemetry
        // went dark at the moment the squad was down and recovery needed it
        // most.
        //
        // The set is the authority because it is what the engine delivers to:
        // `newPlayerTaskSelectedCharacters` orders every member of the raw set,
        // so being in it is what "selected" operationally means. Asking it the
        // same way the id list does is what makes the two unable to disagree.
        if (player == NULL)
            return false;
        Character* character = handle.getCharacter();
        if (character == NULL)
            return false;
        if (player->isObjectSelected(character))
            return true;
        const hand current = character->getHandle();
        for (ogre_unordered_set<hand>::type::const_iterator it =
                 player->selectedCharacters.begin();
             it != player->selectedCharacters.end();
             ++it)
        {
            if (SameCharacterIdentity(*it, current))
                return true;
        }
        return false;
    }

    int FindNativeAcknowledgement(const std::string& commandId)
    {
        for (unsigned int index = 0;
             index < g_nativeAcknowledgementCount;
             ++index)
        {
            if (g_nativeAcknowledgements[index].commandId == commandId)
                return static_cast<int>(index);
        }
        return -1;
    }

    const NativeCommandAcknowledgement* PublishedNativeCommandRecord()
    {
        // Protocol 2.0 is plural from its first frame. Until the captured-
        // recipient registry replaces g_activeNativeCommand, the producer can
        // truthfully publish only the one active record or, while idle, the
        // latest terminal record. This producer-side cardinality limit must be
        // deleted by 2026-09-20; consumers must never depend on it.
        if (g_activeNativeCommand.active)
        {
            const int index =
                FindNativeAcknowledgement(g_activeNativeCommand.commandId);
            return index >= 0 ? &g_nativeAcknowledgements[index] : NULL;
        }
        if (g_nativeAcknowledgementCount == 0)
            return NULL;
        return &g_nativeAcknowledgements[g_nativeAcknowledgementCount - 1];
    }

    NativeCommandAcknowledgement& AddNativeAcknowledgement(
        const NativeCommandRequest& request,
        const char* status,
        const char* reason,
        bool accepted,
        bool terminal)
    {
        if (g_nativeAcknowledgementCount >= MAX_NATIVE_ACKNOWLEDGEMENTS)
        {
            unsigned int removeIndex = 0;
            if (g_activeNativeCommand.active &&
                g_nativeAcknowledgements[removeIndex].commandId ==
                    g_activeNativeCommand.commandId)
            {
                for (unsigned int index = 1;
                     index < g_nativeAcknowledgementCount;
                     ++index)
                {
                    if (g_nativeAcknowledgements[index].commandId !=
                        g_activeNativeCommand.commandId)
                    {
                        removeIndex = index;
                        break;
                    }
                }
            }
            for (unsigned int index = removeIndex + 1;
                 index < g_nativeAcknowledgementCount;
                 ++index)
            {
                g_nativeAcknowledgements[index - 1] =
                    g_nativeAcknowledgements[index];
            }
            --g_nativeAcknowledgementCount;
        }

        NativeCommandAcknowledgement& acknowledgement =
            g_nativeAcknowledgements[g_nativeAcknowledgementCount++];
        acknowledgement.commandId = request.commandId;
        acknowledgement.command = request.command;
        acknowledgement.status = status;
        acknowledgement.reason = reason;
        acknowledgement.targetId = request.targetId;
        acknowledgement.contextAction = request.contextAction;
        acknowledgement.bearingDegrees = request.bearingDegrees;
        acknowledgement.distanceUnits = request.distanceUnits;
        acknowledgement.minimumOutputQuantity =
            request.minimumOutputQuantity;
        // Echoed so an acknowledgement carries everything that identifies its
        // request. A transfer is named by two owners and a slot, and matching
        // on the source alone would let one transfer satisfy a wait for another
        // out of the same inventory.
        acknowledgement.destinationId = request.destinationId;
        acknowledgement.sectionName = request.sectionName;
        acknowledgement.saveName = request.saveName;
        acknowledgement.gameStartId = request.gameStartId;
        acknowledgement.slotX = request.slotX;
        acknowledgement.slotY = request.slotY;
        acknowledgement.dialogueOptionIndex = request.dialogueOptionIndex;
        acknowledgement.dialogueOptionText = request.dialogueOptionText;
        acknowledgement.selectedCharacterId =
            request.selectedCharacterId;
        acknowledgement.selectedCharacterIds =
            request.selectedCharacterIds;
        acknowledgement.basedOnTelemetrySequence =
            request.basedOnTelemetrySequence;
        acknowledgement.acknowledgedAtTelemetrySequence =
            g_sequence + 1;
        acknowledgement.hasAcceptedSequence = accepted;
        acknowledgement.acceptedAtTelemetrySequence =
            accepted ? g_sequence + 1 : 0;
        acknowledgement.hasTerminalSequence = terminal;
        acknowledgement.terminalAtTelemetrySequence =
            terminal ? g_sequence + 1 : 0;
        return acknowledgement;
    }

    void RejectNativeCommand(
        const NativeCommandRequest& request,
        const char* reason)
    {
        AddNativeAcknowledgement(
            request,
            "rejected",
            reason,
            false,
            true);
        g_lastNativeCommandResult = reason;
        g_lastNativeCommandTargetId = request.targetId;
    }

    void FinishActiveNativeCommand(
        const char* status,
        const char* reason)
    {
        // `showT` does not finish while the simulation is paused. A survey
        // that temporarily resumed a paused world owns that clock state all
        // the way through success, cancellation, and timeout.
        if (g_activeNativeCommand.prospectingPlaybackOwned)
        {
            if (ou != NULL && !ou->isPaused())
                ou->userPause(true);
            if (ou == NULL || !ou->isPaused())
            {
                status = "cancelled";
                reason = "prospecting_repause_failed";
            }
        }
        const int index =
            FindNativeAcknowledgement(g_activeNativeCommand.commandId);
        if (index >= 0)
        {
            NativeCommandAcknowledgement& acknowledgement =
                g_nativeAcknowledgements[index];
            acknowledgement.status = status;
            acknowledgement.reason = reason;
            acknowledgement.hasTerminalSequence = true;
            acknowledgement.terminalAtTelemetrySequence =
                g_sequence + 1;
        }
        g_lastNativeCommandResult = reason;
        g_activeNativeCommand.active = false;
        g_activeNativeCommand.commandId.clear();
        g_activeNativeCommand.selectedCharacterId.clear();
        g_activeNativeCommand.selectedCharacterIds.clear();
        g_activeNativeCommand.isWalk = false;
        g_activeNativeCommand.hasFixedDestination = false;
        g_activeNativeCommand.isMapTravel = false;
        g_activeNativeCommand.isSquadRegroup = false;
        g_activeNativeCommand.mapInteriorOrderIssued = false;
        g_activeNativeCommand.isBuildingExit = false;
        g_activeNativeCommand.isContextAction = false;
        g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.isDialogueOptionPending = false;
        g_activeNativeCommand.dialogueOptionText.clear();
        g_activeNativeCommand.dialogueOptionsBefore.clear();
        g_activeNativeCommand.dialogueOptionStartedTick = 0;
        g_activeNativeCommand.isProspectingSurveyPending = false;
        g_activeNativeCommand.prospectingResultObserved = false;
        g_activeNativeCommand.prospectingPlaybackOwned = false;
        g_activeNativeCommand.prospectingStartedTick = 0;
        g_activeNativeCommand.prospectingHiddenSinceTick = 0;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
        g_activeNativeCommand.isResourceProduction = false;
        g_activeNativeCommand.resourceTaskObserved = false;
        g_activeNativeCommand.resourceTaskIssuedByCommand = false;
        g_activeNativeCommand.resourceTaskReleaseRequested = false;
        KenshiAgentTelemetry::ResetResourceTaskReleaseConfirmationWindow(
            g_activeNativeCommand.resourceTaskReleaseWindow);
        g_activeNativeCommand.minimumOutputQuantity = 1;
        g_activeNativeCommand.expectedTask = NULL_TASK;
        g_activeNativeCommand.originX = 0.0f;
        g_activeNativeCommand.originZ = 0.0f;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
        KenshiAgentTelemetry::ResetNativeMovementStallWindow(
            g_activeNativeCommand.stallWindow);
        KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
            g_activeNativeCommand.outdoorWindow);
    }

    void CompletePendingTitleTransitionAcknowledgement()
    {
        if (!g_titleTransitionAcknowledgementPending ||
            g_nativeAcknowledgementCount == 0)
        {
            return;
        }
        NativeCommandAcknowledgement& acknowledgement =
            g_nativeAcknowledgements[g_nativeAcknowledgementCount - 1];
        acknowledgement.status = "completed";
        acknowledgement.reason = "world_session_loaded";
        acknowledgement.hasTerminalSequence = true;
        acknowledgement.terminalAtTelemetrySequence = g_sequence + 1;
        g_lastNativeCommandResult = acknowledgement.reason;
    }

    std::string UtcNowIso8601()
    {
        SYSTEMTIME now;
        GetSystemTime(&now);
        char buffer[64];
        sprintf_s(
            buffer,
            sizeof(buffer),
            "%04u-%02u-%02uT%02u:%02u:%02u.%03uZ",
            now.wYear,
            now.wMonth,
            now.wDay,
            now.wHour,
            now.wMinute,
            now.wSecond,
            now.wMilliseconds);
        return std::string(buffer);
    }

    void AppendVector3(std::ostringstream& json, const Ogre::Vector3& vector)
    {
        json << "{\"x\":" << vector.x
             << ",\"y\":" << vector.y
             << ",\"z\":" << vector.z << "}";
    }

    bool TryGetScreenPosition(
        PlayerInterface* player,
        const Ogre::Vector3& position,
        float& x,
        float& y)
    {
        CameraClass* cameraClass = player != NULL ? player->getCamera() : NULL;
        Ogre::Camera* camera = cameraClass != NULL ? cameraClass->camera : NULL;
        if (camera == NULL)
            return false;

        UtilityT utility;
        utility.cachedViewMatrix = camera->getViewMatrix();
        if (!utility.worldToScreenRel(position, x, y))
            return false;
        return x >= 0.0f && x <= 1.0f && y >= 0.0f && y <= 1.0f;
    }

    bool TryGetCameraBearing(
        PlayerInterface* player,
        const Ogre::Vector3& position,
        float& bearingDegrees)
    {
        CameraClass* cameraClass = player != NULL ? player->getCamera() : NULL;
        Ogre::Camera* camera = cameraClass != NULL ? cameraClass->camera : NULL;
        if (camera == NULL)
            return false;

        const Ogre::Matrix4& viewMatrix = camera->getViewMatrix();
        const float* view = reinterpret_cast<const float*>(&viewMatrix);
        const float cameraX =
            view[0] * position.x +
            view[1] * position.y +
            view[2] * position.z +
            view[3];
        const float cameraZ =
            view[8] * position.x +
            view[9] * position.y +
            view[10] * position.z +
            view[11];
        const float radians =
            static_cast<float>(std::atan2(cameraX, -cameraZ));
        bearingDegrees =
            radians * static_cast<float>(180.0 / 3.14159265358979323846);
        return true;
    }

    const char* GetDisposition(Character* observer, Character* target)
    {
        if (observer == NULL || target == NULL)
            return "unknown";
        if (observer->isEnemy(target, false))
            return "hostile";
        if (observer->isAlly(target, false))
            return "friendly";
        return "neutral";
    }

    float Distance(const Ogre::Vector3& a, const Ogre::Vector3& b)
    {
        const float dx = a.x - b.x;
        const float dy = a.y - b.y;
        const float dz = a.z - b.z;
        return static_cast<float>(std::sqrt(dx * dx + dy * dy + dz * dz));
    }

    // The authorization fact for an approach order is "this is a valid current
    // dialogue target", not "this is a vendor". Kenshi's PLAYER_TALK_TO order
    // works on any non-hostile character with dialogue, so requiring a vendor
    // list and squad leadership excluded ordinary people for no safety reason.
    // Vendor status remains a separate, narrower question (IsConfirmedVendor)
    // for callers that genuinely mean commerce.
    bool IsValidDialogueTarget(Character* selected, Character* target)
    {
        if (selected == NULL ||
            target == NULL ||
            !target->isValid() ||
            target == selected ||
            target->isPlayerCharacter() ||
            target->isAnimal() != NULL ||
            target->isUnconcious() ||
            selected->isEnemy(target, false))
        {
            return false;
        }

        return target->hasDialogue();
    }

    bool IsConfirmedVendor(Character* selected, Character* target)
    {
        if (!IsValidDialogueTarget(selected, target))
            return false;

        ActivePlatoon* platoon = target->getPlatoon();
        return platoon != NULL &&
               platoon->getHasVendorList() &&
               platoon->getSquadLeader() == target;
    }

    bool TryGetExactSelection(
        PlayerInterface* player,
        std::string& selectedId,
        hand& selectedHandle)
    {
        selectedId.clear();
        if (player == NULL || player->selectedCharacters.size() != 1)
            return false;
        ogre_unordered_set<hand>::type::const_iterator it =
            player->selectedCharacters.begin();
        Character* selected = it->getCharacter();
        if (selected == NULL ||
            !selected->isValid() ||
            !selected->isPlayerCharacter() ||
            !SameCharacterIdentity(*it, player->selectedCharacter))
        {
            return false;
        }
        selectedId = StableEntityId(selected);
        if (selectedId.empty())
            return false;
        selectedHandle = *it;
        return true;
    }

    // Why an exact selection basis could not be resolved, in the plug-in's own
    // words. This was one token - "selection_mismatch" - for six unrelated
    // conditions, so a live cancellation said only that something about the
    // selection was wrong and never which thing.
    //
    // The one that matters most is `selection_size_differs`. Telemetry exports
    // `selected_character_ids` by *filtering* player->selectedCharacters: an
    // entry with no stable id, or that is not a player character, or that is
    // absent from the current squad, is skipped. This check compares against
    // the raw, unfiltered set. Python can only ever authorize what it was
    // shown, so a hidden member makes the two authorities disagree about what
    // "the selection" is, permanently and with nothing changing.
    //
    // Refusing is still correct - newPlayerTaskSelectedCharacters delivers to
    // the raw set, so a hidden member would receive an order nobody authorized.
    // What was wrong was being unable to say so. `selected_character_ids_withheld`
    // now carries the hidden entries, and this names the exact condition.
    const char* const SELECTION_BASIS_OK = NULL;

    const char* ResolveExactSelectionBasis(
        PlayerInterface* player,
        const std::vector<std::string>& expectedIds,
        std::string& primaryId,
        hand& primaryHandle,
        std::vector<hand>& selectedHandles)
    {
        primaryId.clear();
        selectedHandles.clear();
        if (player == NULL)
            return "selection_player_absent";
        if (expectedIds.empty())
            return "selection_none_authorized";
        if (player->selectedCharacters.size() != expectedIds.size())
            return "selection_size_differs";

        std::set<std::string> expected(
            expectedIds.begin(),
            expectedIds.end());
        if (expected.size() != expectedIds.size())
            return "selection_authorized_duplicate";

        std::set<std::string> observed;
        for (ogre_unordered_set<hand>::type::const_iterator it =
                 player->selectedCharacters.begin();
             it != player->selectedCharacters.end();
             ++it)
        {
            Character* selected = it->getCharacter();
            if (selected == NULL || !selected->isValid())
                return "selection_member_invalid";
            if (!selected->isPlayerCharacter())
                return "selection_member_not_player_character";
            const std::string id = StableEntityId(selected);
            if (id.empty())
                return "selection_member_unidentified";
            if (expected.find(id) == expected.end())
                return "selection_member_unauthorized";
            if (!observed.insert(id).second)
                return "selection_member_duplicate";
            selectedHandles.push_back(*it);
            if (SameCharacterIdentity(*it, player->selectedCharacter))
            {
                primaryId = id;
                primaryHandle = *it;
            }
        }
        if (observed != expected)
            return "selection_incomplete";
        if (primaryId.empty())
            return "selection_primary_absent";
        return SELECTION_BASIS_OK;
    }

    bool TryGetExactSelectionBasis(
        PlayerInterface* player,
        const std::vector<std::string>& expectedIds,
        std::string& primaryId,
        hand& primaryHandle,
        std::vector<hand>& selectedHandles)
    {
        return ResolveExactSelectionBasis(
                   player,
                   expectedIds,
                   primaryId,
                   primaryHandle,
                   selectedHandles) == SELECTION_BASIS_OK;
    }

    bool IsKnownMapDestination(TownBase* town)
    {
        return town != NULL &&
               town->isValid() &&
               town->isTown() != NULL &&
               town->isDiscovered() &&
               !town->isDead();
    }

    struct KnownMapDestinationSnapshot
    {
        std::string id;
        std::string name;
        float distance;
        bool hasGates;
    };

    bool KnownMapDestinationNearestFirst(
        const KnownMapDestinationSnapshot& left,
        const KnownMapDestinationSnapshot& right)
    {
        if (left.distance != right.distance)
            return left.distance < right.distance;
        return left.id < right.id;
    }

    void CollectKnownMapDestinations(
        const std::vector<KenshiAgentTelemetry::NativeMovementPosition>&
            selectedPositions,
        std::vector<KnownMapDestinationSnapshot>& destinations)
    {
        destinations.clear();
        if (selectedPositions.empty() ||
            shou == NULL ||
            shou->townList == NULL)
            return;

        lektor<RootObject*>& towns = shou->townList->getAllTowns();
        for (lektor<RootObject*>::iterator it = towns.begin();
             it != towns.end();
             ++it)
        {
            RootObject* root = *it;
            if (root == NULL)
                continue;
            const hand& handle = root->getHandle();
            TownBase* town = handle.getTown();
            if (!IsKnownMapDestination(town))
                continue;
            const std::string id = StableEntityId(handle);
            const std::string name = town->getKnownName();
            if (id.empty() || name.empty())
                continue;
            KnownMapDestinationSnapshot snapshot;
            snapshot.id = id;
            snapshot.name = name;
            const Ogre::Vector3 townPosition = town->getPosition();
            float farthestX = selectedPositions[0].x;
            float farthestZ = selectedPositions[0].z;
            KenshiAgentTelemetry::HasGroupReachedDestination(
                selectedPositions,
                townPosition.x,
                townPosition.z,
                farthestX,
                farthestZ);
            const float dx = farthestX - townPosition.x;
            const float dz = farthestZ - townPosition.z;
            snapshot.distance = std::sqrt(dx * dx + dz * dz);
            snapshot.hasGates = town->hasGates();
            destinations.push_back(snapshot);
        }
        std::sort(
            destinations.begin(),
            destinations.end(),
            KnownMapDestinationNearestFirst);
    }

    TownBase* FindExactKnownMapDestination(
        const std::string& targetId,
        bool& exactIdentityFound)
    {
        exactIdentityFound = false;
        if (targetId.empty() ||
            shou == NULL ||
            shou->townList == NULL)
        {
            return NULL;
        }

        lektor<RootObject*>& towns = shou->townList->getAllTowns();
        for (lektor<RootObject*>::iterator it = towns.begin();
             it != towns.end();
             ++it)
        {
            RootObject* root = *it;
            if (root == NULL)
                continue;
            const hand& handle = root->getHandle();
            if (StableEntityId(handle) != targetId)
                continue;
            exactIdentityFound = true;
            TownBase* town = handle.getTown();
            return IsKnownMapDestination(town) ? town : NULL;
        }
        return NULL;
    }

    bool TryResolveActiveCameraDestination(
        float& destinationX,
        float& destinationZ)
    {
        if (g_activeNativeCommand.hasFixedDestination)
        {
            destinationX = g_activeNativeCommand.destinationX;
            destinationZ = g_activeNativeCommand.destinationZ;
            return true;
        }
        if (g_activeNativeCommand.isContextAction)
        {
            if (g_activeNativeCommand.expectedTask == FIRST_AID_ORDER)
            {
                Character* target =
                    g_activeNativeCommand.targetHandle.getCharacter();
                if (target == NULL || !target->isValid())
                    return false;
                const Ogre::Vector3 position = target->getPosition();
                destinationX = position.x;
                destinationZ = position.z;
                return true;
            }
            Building* target =
                g_activeNativeCommand.targetHandle.getBuilding();
            if (target == NULL || !target->isValid())
                return false;
            const Ogre::Vector3 position = target->getPosition();
            destinationX = position.x;
            destinationZ = position.z;
            return true;
        }
        Character* target =
            g_activeNativeCommand.targetHandle.getCharacter();
        if (target == NULL || !target->isValid())
            return false;
        const Ogre::Vector3 position = target->getPosition();
        destinationX = position.x;
        destinationZ = position.z;
        return true;
    }

    void MaintainCameraFollowForActiveCommand(PlayerInterface* player)
    {
        std::string selectedId;
        hand selectedHandle;
        std::vector<hand> selectedHandles;
        const bool exactSelectionResolved =
            g_activeNativeCommand.selectedCharacterIds.size() > 1
                ? TryGetExactSelectionBasis(
                    player,
                    g_activeNativeCommand.selectedCharacterIds,
                    selectedId,
                    selectedHandle,
                    selectedHandles)
                : TryGetExactSelection(player, selectedId, selectedHandle);
        const bool selectionIdentityMatches =
            exactSelectionResolved &&
            selectedId == g_activeNativeCommand.selectedCharacterId &&
            SameHandleIdentity(
                selectedHandle,
                g_activeNativeCommand.selectedHandle);
        if (!KenshiAgentTelemetry::ShouldMaintainCameraFollow(
                g_activeNativeCommand.active,
                exactSelectionResolved,
                selectionIdentityMatches))
        {
            return;
        }

        CameraClass* camera = player->getCamera();
        if (camera == NULL)
            return;
        camera->followObject(selectedHandle);

        Character* selected = selectedHandle.getCharacter();
        float destinationX = 0.0f;
        float destinationZ = 0.0f;
        if (selected == NULL ||
            !selected->isValid() ||
            !TryResolveActiveCameraDestination(
                destinationX,
                destinationZ))
        {
            return;
        }
        const Ogre::Vector3 origin = selected->getPosition();
        Ogre::Vector3 motion = Ogre::Vector3::ZERO;
        CharMovement* movement = selected->getMovement();
        if (movement != NULL)
            motion = movement->getCurrentMotion();
        const Ogre::Vector3 cameraPosition = camera->getCameraPos();
        const Ogre::Vector3 cameraCenter = camera->getCenter();
        KenshiAgentTelemetry::NativeTrailingCameraPose pose;
        if (!KenshiAgentTelemetry::TryComputeTrailingCameraPose(
                origin.x,
                origin.z,
                destinationX,
                destinationZ,
                motion.x,
                motion.z,
                Distance(cameraPosition, cameraCenter),
                pose))
        {
            return;
        }
        const Ogre::Quaternion orientation(
            pose.w,
            pose.x,
            pose.y,
            pose.z);
        camera->manuallySetOrientationAndZoom(
            orientation,
            pose.zoom);
    }

    Character* FindExactDialogueTarget(
        PlayerInterface* player,
        const std::string& targetId,
        bool& exactIdentityFound)
    {
        exactIdentityFound = false;
        Character* selected =
            player != NULL ? player->selectedCharacter.getCharacter() : NULL;
        if (ou == NULL ||
            selected == NULL ||
            !selected->isValid() ||
            targetId.empty())
        {
            return NULL;
        }

        lektor<RootObject*> nearbyCharacters;
        const Ogre::Vector3 selectedPosition = selected->getPosition();
        ou->getCharactersWithinSphere(
            nearbyCharacters,
            selectedPosition,
            NEARBY_CHARACTER_RADIUS,
            0.0f,
            30.0f,
            MAX_NEARBY_CHARACTERS,
            0,
            selected);

        for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
             it != nearbyCharacters.end();
             ++it)
        {
            Character* candidate = reinterpret_cast<Character*>(*it);
            if (candidate == NULL || !candidate->isValid())
                continue;
            if (StableEntityId(candidate) != targetId)
                continue;
            exactIdentityFound = true;
            return IsValidDialogueTarget(selected, candidate) ? candidate : NULL;
        }
        return NULL;
    }

    Character* FindExactSquadMember(
        PlayerInterface* player,
        const std::string& targetId,
        bool& exactIdentityFound)
    {
        exactIdentityFound = false;
        if (player == NULL || targetId.empty())
            return NULL;
        const lektor<Character*>& characters =
            player->getAllPlayerCharacters();
        for (unsigned int index = 0; index < characters.size(); ++index)
        {
            Character* candidate = characters[index];
            if (candidate == NULL ||
                !candidate->isValid() ||
                !candidate->isPlayerCharacter())
            {
                continue;
            }
            if (StableEntityId(candidate) != targetId)
                continue;
            exactIdentityFound = true;
            return candidate;
        }
        return NULL;
    }

    // Same exact-identity lookup as the dialogue target, without requiring the
    // character be talkable. Movement was only ever ordered toward someone the
    // agent could hold a conversation with, so inside a building containing two
    // people that was the entire reachable world: it sold what it could, asked
    // both for work, noticed it was repeating itself, and had no action that
    // could take it anywhere else.
    Character* FindExactNearbyCharacter(
        PlayerInterface* player,
        const std::string& targetId,
        bool& exactIdentityFound)
    {
        exactIdentityFound = false;
        Character* selected =
            player != NULL ? player->selectedCharacter.getCharacter() : NULL;
        if (ou == NULL ||
            selected == NULL ||
            !selected->isValid() ||
            targetId.empty())
        {
            return NULL;
        }

        lektor<RootObject*> nearbyCharacters;
        const Ogre::Vector3 selectedPosition = selected->getPosition();
        ou->getCharactersWithinSphere(
            nearbyCharacters,
            selectedPosition,
            NEARBY_CHARACTER_RADIUS,
            0.0f,
            30.0f,
            MAX_NEARBY_CHARACTERS,
            0,
            selected);

        for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
             it != nearbyCharacters.end();
             ++it)
        {
            Character* candidate = reinterpret_cast<Character*>(*it);
            if (candidate == NULL || !candidate->isValid())
                continue;
            if (StableEntityId(candidate) != targetId)
                continue;
            exactIdentityFound = true;
            return candidate;
        }
        return NULL;
    }

    NaturalResourceAssessment InspectNaturalResource(Building* candidate)
    {
        const bool candidateValid =
            candidate != NULL && candidate->isValid();
        const bool isMine =
            candidateValid &&
            candidate->getSpecialFunction() == BF_MINE;
        const bool isNaturalMine =
            candidateValid &&
            candidate->getSpecialFunction() == BF_MINE_NATURAL;
        const bool defaultTaskOperatesMachinery =
            (isMine || isNaturalMine) &&
            candidate->getDefaultTask() == OPERATE_MACHINERY;
        return AssessNaturalResource(
            candidateValid,
            isMine,
            isNaturalMine,
            defaultTaskOperatesMachinery);
    }

    // Resolve one order name from the same source-derived vocabulary used to
    // label Kenshi's context-menu values. The wire names an order the way the
    // telemetry advertised it: the
    // vocabulary name, lowercased. Resolving here keeps raw task numbers off
    // the wire, so a command stays readable in a run bundle a year from now.
    bool ResolveAdvertisedTaskName(const std::string& name, TaskType& resolved)
    {
        if (name.empty())
            return false;
        unsigned int vocabularyCount = 0;
        const KenshiAgentTelemetry::TaskTypeVocabularyEntry* vocabulary =
            KenshiAgentTelemetry::TaskTypeVocabulary(vocabularyCount);
        for (unsigned int index = 0; index < vocabularyCount; ++index)
        {
            std::string candidate = vocabulary[index].name;
            for (size_t offset = 0; offset < candidate.size(); ++offset)
            {
                candidate[offset] = static_cast<char>(
                    std::tolower(
                        static_cast<unsigned char>(candidate[offset])));
            }
            if (candidate == name)
            {
                resolved = static_cast<TaskType>(vocabulary[index].value);
                return true;
            }
        }
        return false;
    }

    // The name Kenshi's own vocabulary gives a task value, or empty.
    //
    // The context menu answers in raw ints. A value with no vocabulary entry is
    // reported by number rather than dropped: an order this build has never
    // heard of is exactly the discovery the probe exists to make.
    std::string TaskTypeVocabularyName(int value)
    {
        unsigned int vocabularyCount = 0;
        const KenshiAgentTelemetry::TaskTypeVocabularyEntry* vocabulary =
            KenshiAgentTelemetry::TaskTypeVocabulary(vocabularyCount);
        for (unsigned int index = 0; index < vocabularyCount; ++index)
        {
            if (vocabulary[index].value == value)
                return vocabulary[index].name;
        }
        std::ostringstream unknown;
        unknown << "UNKNOWN_TASK_" << value;
        return unknown.str();
    }

    // Restores the probe flag however the probe leaves.
    struct ContextMenuProbeGuard
    {
        ContextMenuProbeGuard() { g_contextMenuProbeActive = true; }
        ~ContextMenuProbeGuard() { g_contextMenuProbeActive = false; }
    };

    // Ask Kenshi which orders apply to a target, by having it build the menu it
    // would build for a right-click and then not drawing it.
    //
    // This replaced three failed attempts at a side-effect-free predicate, and
    // the reason all three failed is that none of them was asking this
    // question:
    //
    //   `isOrderValidForSelection` returned true for all 291 vocabulary
    //   entries measured live, so it discriminates nothing.
    //
    //   `getPlayerTaskProbability` is an odds-getter. Against a cannibal it
    //   returned exactly KIDNAP_ORDER and STEALTH_KNOCKOUT -- the two orders
    //   Kenshi renders a success percentage beside -- while the game's own menu
    //   on that same cannibal offered ATTACK_ENEMIES and FOCUSED_MELEE_ATTACK
    //   and neither of those. It reports false for any order with no odds to
    //   display, so it hides attacking and looting alike.
    //
    //   `Character::checkPlayerOrderForProblems` is not a query at all. Called
    //   speculatively it made Kenshi float "I don't have a medkit" over a
    //   character -- the game's response to an *attempted* first aid order. It
    //   crashed world load twice, and answered "no problem" for 285 of 291.
    //
    // The menu is not a fourth proxy. It is the definition: what it lists is
    // what a player sees, so an agent offered those orders is offered the human
    // move set rather than one this plug-in invented.
    //
    // Two guards keep the probe from being felt. It refuses to run while a menu
    // is already open, because building one would replace what the player is
    // looking at; and it calls through `g_originalContextMenuShow` rather than
    // our own hook, so the record of which target the *player* opened a menu on
    // is never overwritten by a probe.
    bool ProbeMenuOrders(
        PlayerInterface* player,
        RootObject* target,
        std::vector<KenshiAgentTelemetry::AdvertisedTask>& advertised)
    {
        if (!g_contextMenuProbeInstalled ||
            g_originalContextMenuShow == NULL ||
            player == NULL ||
            target == NULL ||
            !target->isValid())
        {
            return false;
        }
        ContextMenu& menu = player->contextMenu;
        if (menu.isVisible() || g_contextMenuProbeActive)
            return false;

        {
            ContextMenuProbeGuard guard;
            g_originalContextMenuShow(&menu, true, target);
            for (unsigned int index = 0; index < menu.orders.size(); ++index)
            {
                const int value = menu.orders[index];
                KenshiAgentTelemetry::MergeAdvertisedTask(
                    advertised,
                    KenshiAgentTelemetry::AdvertisedTask(
                        value,
                        TaskTypeVocabularyName(value),
                        KenshiAgentTelemetry::AdvertisedTaskSource::MENU));
            }
            g_originalContextMenuShow(&menu, false, target);
        }
        return true;
    }

    bool HasAdvertisedTask(
        const std::vector<KenshiAgentTelemetry::AdvertisedTask>& advertised,
        TaskType expected)
    {
        for (unsigned int index = 0; index < advertised.size(); ++index)
        {
            if (advertised[index].value == static_cast<int>(expected))
                return true;
        }
        return false;
    }

    // One completed prospecting survey, held until the next replaces it.
    //
    // Read from Kenshi's own Prospecting window rather than from the terrain
    // field underneath it. An earlier attempt sampled ZoneManager::getResource
    // directly and crashed: it needs an AreaBiomeGroup that nothing reachable
    // provides. It was also the wrong target - the window is what a player
    // sees, so reading it is faithful by construction instead of by
    // reimplementation.
    //
    // Captions are reported verbatim. The window builds each line from a
    // resource name and a value, but exposes only the button, so the exact
    // split is unproven; inventing a parse would be asserting a format nobody
    // has confirmed. The agent gets what the window says.
    std::string StripColourTags(const std::string& value);

    struct ProspectReading
    {
        std::string label;
        std::string value;
    };

    // Kenshi_ProspectingWindowResourceLine.layout, shipped in the game's own
    // data/gui/layout directory, defines each line exactly:
    //
    //   Root (PanelEmpty)
    //     CheckboxButton (Button)  - the resource name
    //     ValueText      (TextBox) - the reading
    //
    // So the reading is found by name rather than by collecting every caption
    // and guessing which one it is. MyGUI prefixes layout-loaded widget names,
    // so match the declared suffix rather than the bare name.
    const char* PROSPECT_VALUE_WIDGET_SUFFIX = "ValueText";

    bool WidgetNameEndsWith(MyGUI::Widget* widget, const char* suffix)
    {
        if (widget == NULL)
            return false;
        const std::string name = widget->getName();
        const std::string wanted(suffix);
        return name.size() >= wanted.size() &&
               name.compare(
                   name.size() - wanted.size(),
                   wanted.size(),
                   wanted) == 0;
    }

    std::string FindProspectLineValue(MyGUI::Widget* panel)
    {
        if (panel == NULL)
            return std::string();
        for (size_t index = 0; index < panel->getChildCount(); ++index)
        {
            MyGUI::Widget* child = panel->getChildAt(index);
            if (!WidgetNameEndsWith(child, PROSPECT_VALUE_WIDGET_SUFFIX))
                continue;
            MyGUI::TextBox* textBox = child->castType<MyGUI::TextBox>(false);
            if (textBox == NULL)
                continue;
            return StripColourTags(textBox->getCaption().asUTF8());
        }
        return std::string();
    }

    struct ProspectSurveyRecord
    {
        bool valid;
        bool windowVisible;
        std::string commandId;
        double centerX;
        double centerZ;
        double skill;
        std::string surveyedName;
        std::vector<ProspectReading> readings;

        ProspectSurveyRecord()
            : valid(false), windowVisible(false),
              centerX(0.0), centerZ(0.0), skill(0.0)
        {
        }
    };

    ProspectSurveyRecord g_prospectSurvey;

    bool BeginProspectSurvey(Character* surveyor, const std::string& commandId)
    {
        if (surveyor == NULL || !surveyor->isValid())
            return false;
        ProspectingWindow* window = ProspectingWindow::getSingleton();
        if (window == NULL)
            return false;

        CharStats* stats = surveyor->getStats();
        const float skill = stats != NULL ? stats->science : 0.0f;
        const Ogre::Vector3 position = surveyor->getPosition();

        g_prospectSurvey = ProspectSurveyRecord();
        g_prospectSurvey.commandId = commandId;
        g_prospectSurvey.centerX = position.x;
        g_prospectSurvey.centerZ = position.z;
        g_prospectSurvey.skill = skill;
        g_prospectSurvey.surveyedName = surveyor->getName();

        // The same call the game's own prospecting button ends in. It starts a
        // timed progress lifecycle; it does not mean the result rows exist
        // yet. The monitor waits for the concrete result window instead of
        // calling `_show` early and leaving Kenshi's scheduled show behind.
        window->showT(position, skill, surveyor->getName());
        return true;
    }

    bool CaptureVisibleProspectSurvey(ProspectingWindow* window)
    {
        if (window == NULL ||
            window->window == NULL ||
            !window->window->getVisible())
        {
            return false;
        }
        g_prospectSurvey.readings.clear();
        const unsigned int lineCount =
            static_cast<unsigned int>(window->lines.size());
        for (unsigned int index = 0; index < lineCount; ++index)
        {
            ProspectingWindow::ResourceLinePanel* line = window->lines[index];
            if (line == NULL || line->button == NULL)
                continue;
            ProspectReading reading;
            reading.label = StripColourTags(line->button->getCaption().asUTF8());
            if (reading.label.empty())
                continue;
            // ResourceLinePanel::getWidget is declared but not exported, so
            // reach the line's Root panel through the button's parent instead -
            // MyGUI's own symbol, and the same widget either way.
            reading.value = FindProspectLineValue(line->button->getParent());
            g_prospectSurvey.readings.push_back(reading);
        }
        // Historical fact: the concrete result widget was visible when these
        // rows were read. Current obstruction is exported independently as
        // ui.prospecting_window_open.
        g_prospectSurvey.windowVisible = true;
        g_prospectSurvey.valid = true;
        return true;
    }

    void AppendProspectSurvey(std::ostringstream& json)
    {
        if (!g_prospectSurvey.valid)
        {
            json << "null";
            return;
        }
        json << "{";
        json << "\"command_id\":\"" << g_prospectSurvey.commandId << "\",";
        json << "\"center\":{\"x\":" << g_prospectSurvey.centerX
             << ",\"z\":" << g_prospectSurvey.centerZ << "},";
        json << "\"skill\":" << g_prospectSurvey.skill << ",";
        json << "\"surveyed_name\":\""
             << JsonEscape(g_prospectSurvey.surveyedName) << "\",";
        json << "\"window_visible\":"
             << JsonBool(g_prospectSurvey.windowVisible) << ",";
        json << "\"readings\":[";
        for (unsigned int index = 0;
             index < g_prospectSurvey.readings.size();
             ++index)
        {
            if (index > 0)
                json << ",";
            json << "{\"label\":\""
                 << JsonEscape(g_prospectSurvey.readings[index].label)
                 << "\",\"value\":\""
                 << JsonEscape(g_prospectSurvey.readings[index].value)
                 << "\"}";
        }
        json << "]}";
    }

    // Kenshi's own name for a task value, from the generated vocabulary.
    // A bare integer would make every consumer keep its own copy of the enum.
    const char* TaskTypeName(int value)
    {
        unsigned int count = 0;
        const KenshiAgentTelemetry::TaskTypeVocabularyEntry* vocabulary =
            KenshiAgentTelemetry::TaskTypeVocabulary(count);
        for (unsigned int index = 0; index < count; ++index)
        {
            if (vocabulary[index].value == value)
                return vocabulary[index].name;
        }
        return "UNKNOWN";
    }

    // Four channels Kenshi keeps apart, exported apart.
    //
    // An ordinary order, a Job, a permajob, and the AI's current goal are
    // different things with different lifetimes, and the controller previously
    // read none of them. That blindness had teeth: an `operate` order was
    // accepted and retained by Kenshi while the controller reported it failed
    // and retried it eight times, and a character with a retained mining Job
    // walked out of a trade conversation because the Job pulled him back to the
    // node. Neither was visible in telemetry.
    //
    // Nothing here infers one channel from another. A mining animation is not
    // evidence of a Job, and an entry in the Jobs list is not evidence of an
    // ordinary order. Each is reported from its own accessor or not at all.
    const unsigned int MAX_EXPORTED_TASK_ENTRIES = 8;

    struct TaskExportEntry
    {
        Tasker* task;
        int position;

        TaskExportEntry(Tasker* taskValue, int positionValue)
            : task(taskValue), position(positionValue)
        {
        }
    };

    void AppendTaskEntry(
        std::ostringstream& json,
        Tasker* task,
        int position)
    {
        json << "{";
        if (task == NULL)
        {
            json << "\"task_value\":null,\"task_name\":null,";
            json << "\"subject_id\":null,\"description\":null,";
            json << "\"position\":";
            if (position >= 0)
                json << position;
            else
                json << "null";
            json << "}";
            return;
        }
        const int value = static_cast<int>(task->key());
        json << "\"task_value\":" << value << ",";
        json << "\"task_name\":\"" << TaskTypeName(value) << "\",";
        const std::string subjectId = StableEntityId(task->subject);
        json << "\"subject_id\":";
        if (!subjectId.empty())
            json << "\"" << JsonEscape(subjectId) << "\"";
        else
            json << "null";
        json << ",";
        json << "\"description\":\""
             << JsonEscape(task->getDescription()) << "\",";
        json << "\"position\":";
        if (position >= 0)
            json << position;
        else
            json << "null";
        json << "}";
    }

    // Serialize one bounded channel with a single non-contradictory statement
    // about completeness. `knownTotal` is exact or null; the sample length is
    // never substituted for an unknown queue total.
    void AppendTaskCollection(
        std::ostringstream& json,
        const char* key,
        const std::vector<TaskExportEntry>& tasks,
        bool wholeListKnown,
        bool totalKnown,
        unsigned int knownTotal)
    {
        const unsigned int sampled = static_cast<unsigned int>(tasks.size());
        const unsigned int retained =
            sampled < MAX_EXPORTED_TASK_ENTRIES
                ? sampled
                : MAX_EXPORTED_TASK_ENTRIES;
        const bool complete =
            wholeListKnown && totalKnown && retained == knownTotal;
        json << "\"" << key << "\":{\"items\":[";
        for (unsigned int index = 0; index < retained; ++index)
        {
            if (index > 0)
                json << ",";
            AppendTaskEntry(json, tasks[index].task, tasks[index].position);
        }
        json << "],";
        json << "\"completeness\":\""
             << (complete ? "complete" : "truncated") << "\",";
        json << "\"known_total\":";
        if (totalKnown)
            json << knownTotal;
        else
            json << "null";
        json << "}";
    }

    void AppendCharacterWorkState(std::ostringstream& json, Character* character)
    {
        AI* ai = character != NULL ? character->getAI() : NULL;
        AITaskSytem* tasks = ai != NULL ? ai->getTaskSystem() : NULL;
        if (tasks == NULL)
        {
            // No task system reachable. Absent, not empty - an empty order list
            // and an unreadable one are different facts.
            json << "\"work\":null";
            return;
        }

        json << "\"work\":{";

        // Ordinary orders: the queue a player fills by right-clicking.
        //
        // Read through the game's own accessors rather than by walking
        // ActionDeque::list. That member is a std::deque in a decompiled
        // header, and iterating it means trusting that this plug-in's idea of
        // std::deque's internal layout matches the one Kenshi was built with.
        // getFirstTask/getSecondTask/getLastTask are exported functions and
        // cost nothing but completeness, which the collection reports
        // honestly. Any queue whose exact total cannot be established is
        // marked truncated rather than guessed at.
        std::vector<TaskExportEntry> orders;
        Tasker* firstOrder = tasks->orders.getFirstTask();
        if (firstOrder != NULL)
            orders.push_back(TaskExportEntry(firstOrder, 0));
        Tasker* secondOrder = tasks->orders.getSecondTask();
        if (secondOrder != NULL && secondOrder != firstOrder)
            orders.push_back(TaskExportEntry(secondOrder, 1));
        Tasker* lastOrder = tasks->orders.getLastTask();
        if (lastOrder != NULL && lastOrder != firstOrder && lastOrder != secondOrder)
            orders.push_back(TaskExportEntry(lastOrder, -1));
        json << "\"has_player_orders\":"
             << JsonBool(tasks->hasPlayerOrders()) << ",";
        // The accessors reach the head and the tail, never the middle, and
        // ActionDeque exports no size(). A queue of two or more is therefore
        // reported as "at least these, proven" - never as a total, which is
        // the misreport that makes a bounded list look like a short one.
        const bool ordersFullyKnown =
            tasks->orders.isEmpty() || tasks->orders.isOnlyOne();
        AppendTaskCollection(
            json,
            "ordinary_orders",
            orders,
            ordersFullyKnown,
            ordersFullyKnown,
            static_cast<unsigned int>(orders.size()));
        json << ",";

        // Jobs: the repeating assignments the Jobs panel lists, with their own
        // enabled switch. A character can hold Jobs while they are switched off.
        std::vector<TaskExportEntry> jobs;
        for (unsigned int index = 0; index < tasks->jobs.size(); ++index)
            jobs.push_back(TaskExportEntry(tasks->jobs[index], index));
        json << "\"jobs_enabled\":" << JsonBool(tasks->isJobsEnabled()) << ",";
        AppendTaskCollection(
            json,
            "jobs",
            jobs,
            true,
            true,
            static_cast<unsigned int>(jobs.size()));
        json << ",";

        // Permajobs: a separate list with its own slot API and its own clear.
        std::vector<TaskExportEntry> permajobs;
        for (unsigned int index = 0; index < tasks->permajobs.size(); ++index)
            permajobs.push_back(TaskExportEntry(tasks->permajobs[index], index));
        AppendTaskCollection(
            json,
            "permanent_jobs",
            permajobs,
            true,
            true,
            static_cast<unsigned int>(permajobs.size()));
        json << ",";

        // Current activity: what the AI settled on doing, which is neither an
        // order nor a Job and must not be read as either.
        const TaskMatch& goal = tasks->getCurrentGoal();
        const int goalValue = static_cast<int>(goal.key());
        json << "\"current_activity\":";
        if (goalValue == static_cast<int>(NULL_TASK))
        {
            json << "null";
        }
        else
        {
            const std::string subjectId = StableEntityId(goal.subject);
            json << "{";
            json << "\"task_value\":" << goalValue << ",";
            json << "\"task_name\":\"" << TaskTypeName(goalValue) << "\",";
            json << "\"subject_id\":";
            if (!subjectId.empty())
                json << "\"" << JsonEscape(subjectId) << "\"";
            else
                json << "null";
            json << ",\"description\":null,\"position\":null";
            json << "}";
        }

        json << "}";
    }

    // Which object categories were examined last snapshot, so the next one
    // continues rather than restarting and starving the tail of the list.
    unsigned int g_discoveryCategoryCursor = 0;

    // The object's own declared type, not the type we happened to query with.
    //
    // getObjectsWithinSphere does not honour its itemType argument for most
    // values: measured live, one object came back under 22 different category
    // labels depending only on which query found it. Labelling from the query
    // parameter produced confident fiction. getDataType() is the object
    // answering for itself.
    const char* ObjectCategoryName(RootObject* object)
    {
        if (object == NULL)
            return "UNKNOWN";
        const int declared = static_cast<int>(object->getDataType());
        unsigned int count = 0;
        const KenshiAgentTelemetry::ItemTypeVocabularyEntry* categories =
            KenshiAgentTelemetry::ItemTypeVocabulary(count);
        for (unsigned int index = 0; index < count; ++index)
        {
            if (categories[index].value == declared)
                return categories[index].name;
        }
        return "UNKNOWN";
    }

    // Ask Kenshi, for every object category it declares and every object near
    // the selection, what the selection may order that object to do.
    //
    // This is the full-discovery pass. The earlier world-target export asked
    // two curated questions - resources and injured teammates - and so could
    // only ever confirm what had already been written down. Here the category
    // list, the task list, and the answers all come from the game; the
    // plug-in contributes only bounds.
    //
    // Nothing here authorizes anything. It reports what exists so the
    // operation registry has something truthful to route.
    void AppendDiscoveredObjects(
        std::ostringstream& json,
        PlayerInterface* player,
        GameWorld* ou,
        RootObject* selected,
        const Ogre::Vector3& selectedPosition,
        bool& complete)
    {
        // Reported, not assumed. Each category scan is bounded, and a scan that
        // filled its budget has not seen the category -- it has stopped looking
        // at it. Saying so is what lets an agent tell an empty world from an
        // exhausted budget.
        complete = true;
        unsigned int categoryCount = 0;
        const KenshiAgentTelemetry::ItemTypeVocabularyEntry* categories =
            KenshiAgentTelemetry::ItemTypeVocabulary(categoryCount);
        if (categoryCount == 0)
            return;

        unsigned int probes = 0;
        bool first = true;
        std::set<std::string> emitted;
        for (unsigned int step = 0;
             step < DISCOVERY_CATEGORIES_PER_SNAPSHOT && step < categoryCount;
             ++step)
        {
            const unsigned int categoryIndex =
                (g_discoveryCategoryCursor + step) % categoryCount;
            // A query hint only. Kenshi does not filter reliably on it, so
            // it decides which objects this pass happens to reach, never what
            // they are; each object reports its own type below.
            const KenshiAgentTelemetry::ItemTypeVocabularyEntry& category =
                categories[categoryIndex];

            // Both bands the world-target scan already uses. Discovery that
            // only saw the near band was structurally blind to objects the
            // rest of the system routes on - the iron resource at 1687 units
            // was in world_targets and absent from discovery.
            lektor<RootObject*> found;
            ou->getObjectsWithinSphere(
                found,
                selectedPosition,
                WORLD_CONTEXT_TARGET_RADIUS,
                static_cast<itemType>(category.value),
                MAX_DISCOVERED_PER_CATEGORY,
                selected);
            if (static_cast<int>(found.size()) >= MAX_DISCOVERED_PER_CATEGORY)
                complete = false;

            for (lektor<RootObject*>::iterator it = found.begin();
                 it != found.end();
                 ++it)
            {
                if (!KenshiAgentTelemetry::IsWithinTargetProbeBudget(
                        probes,
                        MAX_DISCOVERY_PROBES_PER_SNAPSHOT))
                {
                    break;
                }
                RootObject* object = *it;
                if (object == NULL || object == selected)
                    continue;
                const std::string objectId = StableEntityId(object->getHandle());
                if (objectId.empty() || emitted.count(objectId) != 0)
                    continue;
                emitted.insert(objectId);
                ++probes;

                std::vector<KenshiAgentTelemetry::AdvertisedTask> advertised;
                const bool advertisedTasksProbed =
                    ProbeMenuOrders(player, object, advertised);

                if (!first)
                    json << ",";
                first = false;
                json << "{";
                json << "\"id\":\"" << objectId << "\",";
                json << "\"name\":\"" << JsonEscape(object->getName()) << "\",";
                json << "\"category\":\"" << ObjectCategoryName(object) << "\",";
                json << "\"distance\":"
                     << Distance(object->getPosition(), selectedPosition) << ",";
                KenshiAgentTelemetry::AppendAdvertisedTasks(
                    json,
                    advertisedTasksProbed,
                    advertised);
                json << "}";
            }
        }
        // Characters are not reachable through getObjectsWithinSphere at all.
        // Measured live: 25 characters stood within the scan radius while the
        // object scan returned zero of them across six full category cycles.
        // Kenshi keeps them in a separate index with its own accessor, so the
        // richest target class in the game would have been silently absent
        // from discovery - the exact failure this scan exists to prevent.
        lektor<RootObject*> nearbyCharacters;
        ou->getCharactersWithinSphere(
            nearbyCharacters,
            selectedPosition,
            NEARBY_CHARACTER_RADIUS,
            0.0f,
            30.0f,
            MAX_DISCOVERED_CHARACTERS,
            0,
            selected);
        if (static_cast<int>(nearbyCharacters.size()) >= MAX_DISCOVERED_CHARACTERS)
            complete = false;
        for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
             it != nearbyCharacters.end();
             ++it)
        {
            if (!KenshiAgentTelemetry::IsWithinTargetProbeBudget(
                    probes,
                    MAX_DISCOVERY_PROBES_PER_SNAPSHOT))
            {
                break;
            }
            RootObject* object = *it;
            if (object == NULL || object == selected)
                continue;
            const std::string objectId = StableEntityId(object->getHandle());
            if (objectId.empty() || emitted.count(objectId) != 0)
                continue;
            emitted.insert(objectId);
            ++probes;

            std::vector<KenshiAgentTelemetry::AdvertisedTask> advertised;
            const bool advertisedTasksProbed =
                ProbeMenuOrders(player, object, advertised);

            if (!first)
                json << ",";
            first = false;
            json << "{";
            json << "\"id\":\"" << objectId << "\",";
            json << "\"name\":\"" << JsonEscape(object->getName()) << "\",";
            json << "\"category\":\"" << ObjectCategoryName(object) << "\",";
            json << "\"distance\":"
                 << Distance(object->getPosition(), selectedPosition) << ",";
            KenshiAgentTelemetry::AppendAdvertisedTasks(
                json,
                advertisedTasksProbed,
                advertised);
            json << "}";
        }

        g_discoveryCategoryCursor =
            (g_discoveryCategoryCursor + DISCOVERY_CATEGORIES_PER_SNAPSHOT) %
            categoryCount;
    }

    void PopulateNaturalResourceState(
        Building* target,
        NaturalResourceTargetSnapshot& snapshot);

    void AppendNaturalResourceCandidates(
        PlayerInterface* player,
        lektor<RootObject*>& buildings,
        const Ogre::Vector3& selectedPosition,
        std::vector<NaturalResourceTargetSnapshot>& candidates,
        std::map<std::string, RootObject*>& candidateObjects)
    {
        for (lektor<RootObject*>::iterator it = buildings.begin();
             it != buildings.end();
             ++it)
        {
            Building* target = reinterpret_cast<Building*>(*it);
            const NaturalResourceAssessment resource =
                InspectNaturalResource(target);
            if (!resource.structurallyRecognized)
                continue;
            const std::string targetId =
                StableEntityId(target->getHandle());
            if (targetId.empty())
                continue;

            const Ogre::Vector3 targetPosition = target->getPosition();
            NaturalResourceTargetSnapshot snapshot;
            snapshot.id = targetId;
            snapshot.name = target->getName();
            snapshot.positionX = targetPosition.x;
            snapshot.positionY = targetPosition.y;
            snapshot.positionZ = targetPosition.z;
            snapshot.distance =
                Distance(targetPosition, selectedPosition);
            snapshot.miningResourceLevel =
                target->getMiningResourceLevel();
            PopulateNaturalResourceState(target, snapshot);
            float screenX = 0.0f;
            float screenY = 0.0f;
            snapshot.hasScreenPosition =
                target->getVisible() &&
                TryGetScreenPosition(
                    player,
                    targetPosition,
                    screenX,
                    screenY);
            if (snapshot.hasScreenPosition)
            {
                snapshot.screenX = screenX;
                snapshot.screenY = screenY;
            }
            candidateObjects[targetId] = *it;
            candidates.push_back(snapshot);
        }
    }

    Building* FindExactNaturalResource(
        PlayerInterface* player,
        const std::string& targetId)
    {
        Character* selected =
            player != NULL ? player->selectedCharacter.getCharacter() : NULL;
        if (ou == NULL ||
            selected == NULL ||
            !selected->isValid() ||
            targetId.empty())
        {
            return NULL;
        }

        lektor<RootObject*> nearBuildings;
        ou->getObjectsWithinSphere(
            nearBuildings,
            selected->getPosition(),
            NEAR_WORLD_CONTEXT_TARGET_RADIUS,
            BUILDING,
            MAX_NEAR_WORLD_CONTEXT_BUILDINGS,
            selected);
        for (lektor<RootObject*>::iterator it = nearBuildings.begin();
             it != nearBuildings.end();
             ++it)
        {
            Building* candidate = reinterpret_cast<Building*>(*it);
            if (candidate == NULL || !candidate->isValid())
                continue;
            if (StableEntityId(candidate->getHandle()) == targetId)
                return candidate;
        }

        lektor<RootObject*> outerBuildings;
        ou->getObjectsWithinSphere(
            outerBuildings,
            selected->getPosition(),
            WORLD_CONTEXT_TARGET_RADIUS,
            BUILDING,
            MAX_OUTER_WORLD_CONTEXT_BUILDINGS,
            selected);
        for (lektor<RootObject*>::iterator it = outerBuildings.begin();
             it != outerBuildings.end();
             ++it)
        {
            Building* candidate = reinterpret_cast<Building*>(*it);
            if (candidate == NULL || !candidate->isValid())
                continue;
            if (StableEntityId(candidate->getHandle()) != targetId)
                continue;
            return candidate;
        }
        return NULL;
    }

    // One stable id to one engine handle, without caring what kind of thing it
    // names.
    //
    // Every lookup beside this one is typed -- `FindExactNaturalResource`,
    // `FindExactSquadMember`, `FindExactNearbyCharacter` -- and each was written
    // for the one operation that needed it. That is why opening an inventory
    // could only ever open a mining crate: `open_context_inventory` resolves
    // through the natural-resource finder, so a looted body was unreachable by
    // construction rather than by policy.
    //
    // Kenshi does not have this problem. `ForgottenGUI::showInventory` takes a
    // `hand`, `inventoryWindowsOpen` is keyed by `hand`, and
    // `newPlayerTaskSelectedCharacters` takes a `hand`. The engine's own
    // vocabulary for "a thing" is the handle, so a bridge that speaks in
    // typed finders is speaking a narrower language than the game it bridges.
    //
    // Searched nearest-population first: the squad, then nearby characters,
    // then the object bands the world scan already walks.
    bool FindExactOwnerHandle(
        PlayerInterface* player,
        const std::string& targetId,
        hand& ownerHandle)
    {
        if (player == NULL || targetId.empty())
            return false;
        Character* selected = player->selectedCharacter.getCharacter();
        if (selected == NULL || !selected->isValid())
            return false;

        bool exactIdentityFound = false;
        Character* squadMember =
            FindExactSquadMember(player, targetId, exactIdentityFound);
        if (squadMember != NULL && squadMember->isValid())
        {
            ownerHandle = squadMember->getHandle();
            return true;
        }
        Character* nearby =
            FindExactNearbyCharacter(player, targetId, exactIdentityFound);
        if (nearby != NULL && nearby->isValid())
        {
            ownerHandle = nearby->getHandle();
            return true;
        }
        if (StableEntityId(selected) == targetId)
        {
            ownerHandle = selected->getHandle();
            return true;
        }

        if (ou == NULL)
            return false;
        const Ogre::Vector3 selectedPosition = selected->getPosition();
        const float radii[2] =
        {
            NEAR_WORLD_CONTEXT_TARGET_RADIUS,
            WORLD_CONTEXT_TARGET_RADIUS
        };
        const int limits[2] =
        {
            MAX_NEAR_WORLD_CONTEXT_BUILDINGS,
            MAX_OUTER_WORLD_CONTEXT_BUILDINGS
        };
        unsigned int categoryCount = 0;
        const KenshiAgentTelemetry::ItemTypeVocabularyEntry* categories =
            KenshiAgentTelemetry::ItemTypeVocabulary(categoryCount);
        for (unsigned int band = 0; band < 2; ++band)
        {
            for (unsigned int index = 0; index < categoryCount; ++index)
            {
                lektor<RootObject*> found;
                ou->getObjectsWithinSphere(
                    found,
                    selectedPosition,
                    radii[band],
                    static_cast<itemType>(categories[index].value),
                    limits[band],
                    selected);
                for (lektor<RootObject*>::iterator it = found.begin();
                     it != found.end();
                     ++it)
                {
                    RootObject* candidate = *it;
                    if (candidate == NULL || !candidate->isValid())
                        continue;
                    if (StableEntityId(candidate->getHandle()) != targetId)
                        continue;
                    ownerHandle = candidate->getHandle();
                    return true;
                }
            }
        }
        return false;
    }

    bool HasExactContextGoal(
        Character* selected,
        TaskType expectedTask,
        const hand& targetHandle)
    {
        if (selected == NULL || !selected->isValid())
            return false;
        AI* ai = selected->getAI();
        AITaskSytem* tasks = ai != NULL ? ai->getTaskSystem() : NULL;
        if (tasks == NULL)
            return false;
        const TaskMatch& goal = tasks->getCurrentGoal();
        return goal.key() == expectedTask &&
               SameHandleIdentity(goal.subject, targetHandle);
    }

    bool HasExactContextGoal(Character* selected)
    {
        return HasExactContextGoal(
            selected,
            g_activeNativeCommand.expectedTask,
            g_activeNativeCommand.targetHandle);
    }

    // Whether Kenshi is holding nothing at all for this character.
    //
    // An order that does not take leaves the character idle, and the position
    // stall detector then reports it ten seconds later as `movement_stalled` --
    // blaming the pathfinder for an order the engine dropped. Measured across
    // five stalls in one live run, every one of them had `hasPlayerOrders`
    // false, an empty ordinary-order collection and null activity: nobody was stuck,
    // nobody was walking, there was simply no order left to carry out.
    bool HoldsNoOrderAtAll(Character* character)
    {
        if (character == NULL || !character->isValid())
            return false;
        AI* ai = character->getAI();
        AITaskSytem* tasks = ai != NULL ? ai->getTaskSystem() : NULL;
        if (tasks == NULL)
            return false;
        return !tasks->hasPlayerOrders() &&
               tasks->getCurrentGoal().key() == NULL_TASK;
    }

    bool TryGetInventorySectionQuantity(
        Building* target,
        const std::string& sectionName,
        int& quantity)
    {
        quantity = 0;
        if (target == NULL || !target->isValid())
            return false;
        Inventory* inventory = target->getInventory();
        if (inventory == NULL)
            return false;
        InventorySection* section = inventory->getSection(sectionName);
        if (section == NULL)
            return false;
        const Ogre::vector<InventorySection::SectionItem>::type& items =
            section->getItems();
        for (size_t index = 0; index < items.size(); ++index)
        {
            Item* item = items[index].item;
            if (item == NULL || !item->isValid() || item->quantity <= 0)
                continue;
            quantity += item->quantity;
        }
        return true;
    }

    // Populate the engine-owned resource state that distinguishes an order
    // recipient from an accepted operator.
    //
    // `newPlayerTaskSelectedCharacters` only places work on the selected
    // characters. Kenshi accepts a character into a machine separately:
    // `UseableStuff::tryOperate` inserts its handle into `currentOperators`
    // only while that set is below `numOperatorsMax`. These fields are the
    // acceptance boundary. Selection size, task queues, current activity, and
    // animation are deliberately absent from this read.
    void PopulateNaturalResourceState(
        Building* target,
        NaturalResourceTargetSnapshot& snapshot)
    {
        snapshot.operatorCapacityKnown = false;
        snapshot.currentOperatorsComplete = false;
        snapshot.currentOperatorIds.clear();
        snapshot.outputInventoryComplete = false;
        snapshot.outputInventory.clear();
        if (target == NULL || !target->isValid())
            return;

        UseableStuff* useable = target->getUseableStuff();
        if (useable != NULL && useable->numOperatorsMax >= 0)
        {
            snapshot.operatorCapacityKnown = true;
            snapshot.operatorCapacity = useable->numOperatorsMax;
            snapshot.currentOperatorsComplete = true;
            std::set<std::string> seenOperatorIds;
            typedef std::set<hand, std::less<hand>,
                Ogre::STLAllocator<hand, Ogre::GeneralAllocPolicy > >
                OperatorSet;
            for (OperatorSet::const_iterator it =
                     useable->currentOperators.begin();
                 it != useable->currentOperators.end();
                 ++it)
            {
                Character* current = it->getCharacter();
                const std::string operatorId = StableEntityId(current);
                if (operatorId.empty() ||
                    !seenOperatorIds.insert(operatorId).second)
                {
                    snapshot.currentOperatorsComplete = false;
                    continue;
                }
                snapshot.currentOperatorIds.push_back(operatorId);
            }
            std::sort(
                snapshot.currentOperatorIds.begin(),
                snapshot.currentOperatorIds.end());
            if (snapshot.currentOperatorIds.size() >
                static_cast<unsigned int>(snapshot.operatorCapacity))
            {
                snapshot.currentOperatorsComplete = false;
            }
        }

        Inventory* inventory = target->getInventory();
        InventorySection* output =
            inventory != NULL ? inventory->getSection("out") : NULL;
        if (output == NULL)
            return;
        snapshot.outputInventoryComplete = true;
        const Ogre::vector<InventorySection::SectionItem>::type& items =
            output->getItems();
        for (size_t index = 0; index < items.size(); ++index)
        {
            if (snapshot.outputInventory.size() >= MAX_RESOURCE_OUTPUT_ITEMS)
            {
                snapshot.outputInventoryComplete = false;
                break;
            }
            Item* item = items[index].item;
            if (item == NULL || !item->isValid())
            {
                snapshot.outputInventoryComplete = false;
                continue;
            }
            NaturalResourceTargetSnapshot::OutputItem outputItem;
            outputItem.name = item->getName();
            outputItem.quantity = item->quantity;
            outputItem.itemType = static_cast<int>(item->getItemType());
            snapshot.outputInventory.push_back(outputItem);
        }
    }

    // True only when Kenshi's accepted-operator set intersects the exact
    // command recipients. A queued order or a selected character is not an
    // operator and is intentionally unable to make this true.
    bool HasSelectedResourceOperator(
        Building* target,
        const std::vector<std::string>& selectedCharacterIds)
    {
        if (target == NULL || !target->isValid() ||
            selectedCharacterIds.empty())
        {
            return false;
        }
        UseableStuff* useable = target->getUseableStuff();
        if (useable == NULL)
            return false;
        typedef std::set<hand, std::less<hand>,
            Ogre::STLAllocator<hand, Ogre::GeneralAllocPolicy > > OperatorSet;
        for (OperatorSet::const_iterator it =
                 useable->currentOperators.begin();
             it != useable->currentOperators.end();
             ++it)
        {
            const std::string operatorId = StableEntityId(it->getCharacter());
            if (!operatorId.empty() &&
                std::find(
                    selectedCharacterIds.begin(),
                    selectedCharacterIds.end(),
                    operatorId) != selectedCharacterIds.end())
            {
                return true;
            }
        }
        return false;
    }

    bool IsExactDialogueTargetOpen(const hand& targetHandle)
    {
        if (gui == NULL ||
            gui->dialogue == NULL ||
            !gui->dialogue->isVisible() ||
            gui->dialogue->dialogue == NULL)
        {
            return false;
        }
        Dialogue* dialogue = gui->dialogue->dialogue;
        Character* dialogueOwner = dialogue->getCharacter();
        if (dialogueOwner != NULL &&
            dialogueOwner->isValid() &&
            SameHandleIdentity(dialogueOwner->getHandle(), targetHandle))
        {
            return true;
        }
        const hand conversationTarget = dialogue->getConversationTarget();
        return conversationTarget.isValid() &&
               SameHandleIdentity(conversationTarget, targetHandle);
    }

    bool TryGetDialogueTargetId(std::string& targetId)
    {
        targetId.clear();
        if (gui == NULL ||
            gui->dialogue == NULL ||
            !gui->dialogue->isVisible() ||
            gui->dialogue->dialogue == NULL)
        {
            return false;
        }

        Dialogue* dialogue = gui->dialogue->dialogue;
        Character* dialogueOwner = dialogue->getCharacter();
        targetId = StableEntityId(dialogueOwner);
        if (!targetId.empty())
            return true;

        const hand conversationTarget = dialogue->getConversationTarget();
        targetId = StableEntityId(conversationTarget.getCharacter());
        return !targetId.empty();
    }

    bool TryGetDialogueOptions(std::vector<std::string>& options)
    {
        options.clear();
        if (gui == NULL ||
            gui->dialogue == NULL ||
            !gui->dialogue->isVisible())
        {
            return false;
        }

        const Ogre::FastArray<MyGUI::EditBox*>& replyTexts =
            gui->dialogue->replyTexts;
        for (size_t index = 0; index < replyTexts.size(); ++index)
        {
            MyGUI::EditBox* reply = replyTexts[index];
            options.push_back(
                reply != NULL
                    ? reply->getCaption().asUTF8()
                    : std::string());
        }
        return true;
    }

    void AppendDialogueOptions(std::ostringstream& json)
    {
        std::vector<std::string> options;
        if (!TryGetDialogueOptions(options))
        {
            json << "null";
            return;
        }

        json << "[";
        for (size_t index = 0; index < options.size(); ++index)
        {
            if (index > 0)
                json << ",";
            json << "\"" << JsonEscape(options[index]) << "\"";
        }
        json << "]";
    }

    // There was an attempt here to read Kenshi's item tooltip through
    // `InventoryGUI::toolTip`, a static the linker cannot bind, at its
    // recorded module offset. It crashed the game on every save load: the
    // pointer dangles across "Reset game" while the GUI is rebuilt, and a 2 Hz
    // snapshot lands in that window reliably.
    //
    // It is gone because it was never needed. A hovered tooltip is already
    // rendered as ordinary MyGUI widgets, so the widget walk below exports its
    // text without touching raw memory - the Katana's "Value c.5,165" and
    // "Sell value c.1,291" rows arrive as EditBox captions. Better still, the
    // cells carry `item_base_value` themselves, so a price costs no hover at
    // all. Reaching for a memory read to fetch what the safe instrument
    // already reported bought nothing but the crash.
    //
    // Reporting only a bool would fold "nothing to look at", "the address held
    // nothing", "reading it faulted" and "there is a tooltip and it is hidden"
    // into one indistinguishable false - which is precisely how a tooltip
    // sensor went a thousand observations without anyone being able to tell it
    // was aimed at the wrong object. Each outcome gets its own name.
    enum ToolTipProbe
    {
        TOOLTIP_PROBE_NOT_LOOKED,
        TOOLTIP_PROBE_ABSENT,
        TOOLTIP_PROBE_FAULT,
        TOOLTIP_PROBE_HIDDEN,
        TOOLTIP_PROBE_VISIBLE
    };

    const char* ToolTipProbeName(int probe)
    {
        switch (probe)
        {
        case TOOLTIP_PROBE_ABSENT: return "absent";
        case TOOLTIP_PROBE_FAULT: return "fault";
        case TOOLTIP_PROBE_HIDDEN: return "hidden";
        case TOOLTIP_PROBE_VISIBLE: return "visible";
        default: return "not_looked";
        }
    }

    // Kept free of C++ objects on purpose - SEH cannot coexist with unwinding
    // in one frame.
    int ProbeToolTipVisible(ToolTip* tooltip)
    {
        if (tooltip == NULL)
            return TOOLTIP_PROBE_ABSENT;
        __try
        {
            return tooltip->getVisible()
                ? TOOLTIP_PROBE_VISIBLE
                : TOOLTIP_PROBE_HIDDEN;
        }
        __except (EXCEPTION_EXECUTE_HANDLER)
        {
            return TOOLTIP_PROBE_FAULT;
        }
    }

    std::string CurrentToolTipText(ToolTip* tooltip)
    {
        std::ostringstream text;
        if (tooltip == NULL)
            return text.str();

        bool first = true;
        for (Ogre::vector<ToolTip::ToolTipLine*>::type::const_iterator it =
                 tooltip->lines.begin();
             it != tooltip->lines.end();
             ++it)
        {
            ToolTip::ToolTipLine* line = *it;
            if (line == NULL)
                continue;
            const std::string left =
                line->leftBox != NULL
                    ? line->leftBox->getCaption().asUTF8()
                    : std::string();
            const std::string right =
                line->rightBox != NULL
                    ? line->rightBox->getCaption().asUTF8()
                    : std::string();
            if (left.empty() && right.empty())
                continue;
            if (!first)
                text << "\n";
            first = false;
            text << left;
            if (!left.empty() && !right.empty())
                text << " ";
            text << right;
        }
        return text.str();
    }

    // MyGUI encodes colour inline as #RRGGBB, and "##" escapes a literal '#'.
    // Left in place the tags become part of the caption, so a label arrives as
    // "#FFFFFFSell value" and no comparison against it can match.
    std::string StripColourTags(const std::string& value)
    {
        std::string out;
        out.reserve(value.size());
        for (size_t i = 0; i < value.size(); ++i)
        {
            if (value[i] != '#')
            {
                out.push_back(value[i]);
                continue;
            }
            if (i + 1 < value.size() && value[i + 1] == '#')
            {
                out.push_back('#');
                ++i;
                continue;
            }
            bool hexTag = (i + 6) < value.size();
            for (size_t j = 1; hexTag && j <= 6; ++j)
            {
                if (!isxdigit(static_cast<unsigned char>(value[i + j])))
                    hexTag = false;
            }
            if (hexTag)
                i += 6;
            else
                out.push_back('#');
        }
        return out;
    }

    // The game already splits a tooltip row into a label and a value -
    // ToolTipLine holds them in two separate EditBoxes - and flattening them
    // into one string discards that, leaving every consumer to re-derive the
    // structure with a regex over prose. An item shows "Sell value" / "c.62"
    // and "Value" / "c.248" as distinct rows; shipped as pairs, reading a
    // price is a lookup rather than a parse.
    void AppendToolTipLines(std::ostringstream& json, ToolTip* tooltip)
    {
        json << "[";
        if (tooltip == NULL)
        {
            json << "]";
            return;
        }

        bool first = true;
        for (Ogre::vector<ToolTip::ToolTipLine*>::type::const_iterator it =
                 tooltip->lines.begin();
             it != tooltip->lines.end();
             ++it)
        {
            ToolTip::ToolTipLine* line = *it;
            if (line == NULL)
                continue;
            const std::string left =
                line->leftBox != NULL
                    ? StripColourTags(line->leftBox->getCaption().asUTF8())
                    : std::string();
            const std::string right =
                line->rightBox != NULL
                    ? StripColourTags(line->rightBox->getCaption().asUTF8())
                    : std::string();
            if (left.empty() && right.empty())
                continue;
            if (!first)
                json << ",";
            first = false;
            json << "{\"label\":\"" << JsonEscape(left) << "\",";
            json << "\"value\":\"" << JsonEscape(right) << "\"}";
        }
        json << "]";
    }

    bool AppendToolTipSourceBounds(
        std::ostringstream& json,
        ToolTip* tooltip)
    {
        if (tooltip == NULL || tooltip->caller == NULL)
            return false;
        const MyGUI::IntCoord bounds =
            tooltip->caller->getAbsoluteCoord();
        const MyGUI::IntSize view =
            MyGUI::RenderManager::getInstance().getViewSize();
        if (view.width <= 0 ||
            view.height <= 0 ||
            bounds.width <= 0 ||
            bounds.height <= 0)
        {
            return false;
        }

        const double minX =
            static_cast<double>(bounds.left) / static_cast<double>(view.width);
        const double maxX =
            static_cast<double>(bounds.left + bounds.width) /
            static_cast<double>(view.width);
        const double minY =
            static_cast<double>(bounds.top) / static_cast<double>(view.height);
        const double maxY =
            static_cast<double>(bounds.top + bounds.height) /
            static_cast<double>(view.height);
        if (minX < 0.0 || minY < 0.0 || maxX > 1.0 || maxY > 1.0)
            return false;

        json << "{";
        json << "\"min_x\":" << minX << ",";
        json << "\"max_x\":" << maxX << ",";
        json << "\"min_y\":" << minY << ",";
        json << "\"max_y\":" << maxY;
        json << "}";
        return true;
    }

    // Which widgets a pass is allowed to emit. The export is capped, and a
    // saturated cap spent on static HUD text hides the very affordances a
    // caller needs, so buttons are collected before labels rather than
    // whichever the widget tree happens to reach first.
    enum UIControlPass
    {
        UI_PASS_BUTTONS_ONLY,
        UI_PASS_ITEMS_ONLY,
        UI_PASS_TEXT_ONLY
    };

    // Inventory and shop cells are ImageBox icons rather than TextBox widgets,
    // so a text-only walk could never see the item grid at all - it looked
    // empty rather than crowded out. They carry no caption, so a caller
    // identifies one by its ordinal within this deterministic walk and then
    // confirms what it actually is from the tooltip after hovering it.
    unsigned int g_itemCellOrdinal = 0;

    // Which MyGUI window a control belongs to. Without this, several windows'
    // controls arrive as one flat list and identical labels - every window has
    // a close button - are indistinguishable, so closing "the shop" cannot be
    // expressed at all.
    std::string OwningWindowCaption(MyGUI::Widget* widget)
    {
        MyGUI::Widget* current = widget;
        unsigned int depth = 0;
        while (current != NULL && depth < MAX_UI_WIDGET_DEPTH)
        {
            MyGUI::Window* window = current->castType<MyGUI::Window>(false);
            if (window != NULL)
            {
                const std::string caption = window->getCaption().asUTF8();
                if (!caption.empty())
                    return caption;
            }
            current = current->getParent();
            ++depth;
        }
        return std::string();
    }

    // One item, described well enough to decide about without touching it.
    // Name alone still forces "is this food? can I afford it?" through a hover
    // and a tooltip parse, which is a model round-trip per cell.
    //
    // `getValueSingle(isPlayer)` answers two different questions, and the old
    // export shipped one of them under the neutral name `item_value`. With
    // isPlayer=true it is what the trader pays the player - the "sell value
    // c.62" line of the in-game tooltip - and never what the player is
    // charged; the same item shows "value c.248" beside it. A plan that read
    // the neutral name as an asking price declared 300 for Bread and was
    // billed 549. Both sides ship under names that say which is which, so
    // nothing downstream has to remember which meaning it holds.
    void AppendItemFacts(std::ostringstream& json, Item* item)
    {
        json << "\"item_name\":\"" << JsonEscape(item->getName()) << "\",";
        json << "\"item_sell_value\":" << item->getValueSingle(true) << ",";
        json << "\"item_base_value\":" << item->getValueSingle(false) << ",";
        json << "\"item_quantity\":" << item->quantity << ",";
        json << "\"item_type\":" << static_cast<int>(item->getItemType()) << ",";
    }

    // The title screen's own menu handlers.
    //
    // `./dev launch` reaches the main menu by clicking its pixels, which is the
    // last thing in this project that synthesizes a mouse. The handlers are
    // right here: `continueGame` and `loadGame` are the same functions the
    // buttons call, private on `TitleScreen` and reachable the way every other
    // protected member here is. They return void, so the return-convention trap
    // that cost four crashes on `RClickAutoTrade` cannot apply.
    //
    // `newGame` is deliberately absent: KenshiLib marks it `no_addr`, so there
    // is no symbol to call and pretending otherwise would be inventing one.
    typedef void (*TitleScreenActionFunction)(TitleScreen*, MyGUI::Widget*);

    struct TitleScreenReach : public TitleScreen
    {
        static TitleScreenActionFunction ResolveContinue()
        {
            return reinterpret_cast<TitleScreenActionFunction>(
                KenshiLib::GetRealAddress(&TitleScreenReach::continueGame));
        }

        static TitleScreenActionFunction ResolveLoad()
        {
            return reinterpret_cast<TitleScreenActionFunction>(
                KenshiLib::GetRealAddress(&TitleScreenReach::loadGame));
        }

    private:
        ~TitleScreenReach();
    };

    typedef bool (*WithinRangeToTradeFunction)(
        InventoryGUI*,
        RootObject*,
        bool);

    // Kenshi's range predicate is protected. A derived class may name the
    // member without constructing an `InventoryGUI`, which lets telemetry ask
    // the same reach question before a transfer is attempted.
    struct InventoryTradeRangeReach : public InventoryGUI
    {
        static WithinRangeToTradeFunction ResolveRange()
        {
            return reinterpret_cast<WithinRangeToTradeFunction>(
                KenshiLib::GetRealAddress(
                    &InventoryTradeRangeReach::isWithinRangeToTrade));
        }

    private:
        ~InventoryTradeRangeReach();
    };

    // Whether Kenshi considers these two close enough to trade, asked rather
    // than assumed.
    //
    // `RClickAutoTrade` already answers `OUT_OF_RANGE`, but only once an item is
    // being moved -- so the agent could open a trade window with a shopkeeper
    // across town, see two healthy inventories, and learn about the distance
    // only by failing. `isWithinRangeToTrade` is the same predicate the engine
    // consults, available before anything is attempted, which makes the reach
    // of an open window a fact the agent can read instead of a rule invented
    // above the game.
    bool InventoryWindowIsWithinTradeRange(
        InventoryGUI* window,
        Character* other,
        bool& known)
    {
        known = false;
        if (window == NULL || other == NULL || !other->isValid())
            return false;
        WithinRangeToTradeFunction withinRange =
            InventoryTradeRangeReach::ResolveRange();
        if (withinRange == NULL)
            return false;
        known = true;
        return withinRange(window, static_cast<RootObject*>(other), false);
    }

    // The one id an open inventory's owner is advertised under.
    //
    // Both the telemetry that offers a window and the command that acts on one
    // must derive the id here, or they are naming different things with the
    // same string.
    std::string OpenInventoryOwnerId(const hand& owner)
    {
        Character* ownerCharacter = owner.getCharacter();
        if (ownerCharacter != NULL && ownerCharacter->isValid())
            return StableEntityId(ownerCharacter);
        return StableEntityId(owner);
    }

    // An open inventory window, found the way it was advertised: by walking the
    // engine's own map and comparing ids.
    //
    // The obvious implementation -- rebuild a `hand` from the id and call
    // `getInventoryWindow` -- is wrong, and was. A `hand` is not a pointer; it
    // carries `container` and `containerSerial` beside `index` and `serial`, so
    // two handles to one character compare unequal once the character's
    // container changes. `Character::getHandle()` returns a handle built from
    // where the character is *now*, while the map still holds the key it was
    // opened under. Measured live: telemetry advertised Barman and Fish as open
    // inventories on the same frames that three `transfer_item` calls were
    // refused `inventory_not_open`, because the rebuilt handles missed keys
    // sitting in the map.
    //
    // This is the same defect that broke selection when unconscious characters
    // changed platoon: identity reconstructed from an id is not identity. So
    // nothing is reconstructed here. If an engine handle is ever needed for one
    // of these windows, take `it->first` -- the key the engine itself stored --
    // rather than building a new one.
    InventoryGUI* FindOpenInventoryWindow(
        ForgottenGUI* gui,
        const std::string& ownerId)
    {
        if (gui == NULL || ownerId.empty())
            return NULL;
        for (ogre_unordered_map<hand, InventoryGUI*>::type::const_iterator it =
                 gui->inventoryWindowsOpen.begin();
             it != gui->inventoryWindowsOpen.end();
             ++it)
        {
            InventoryGUI* window = it->second;
            if (window == NULL || !window->isVisible())
                continue;
            if (window->getInventory() == NULL)
                continue;
            if (OpenInventoryOwnerId(it->first) != ownerId)
                continue;
            return window;
        }
        return NULL;
    }

    // Every inventory Kenshi currently has open, whatever owns it.
    //
    // `context_inventory_target_id` reads `ForgottenGUI::inventoryWindowBuilding`
    // -- one of four typed slots beside Character, Trader and NPC -- so a looted
    // body's window had no exported owner at all. Measured live, the agent
    // ordered `loot_target`, Kenshi opened the window, and the agent stood there
    // waiting for a completion no field reported, because nothing told it an
    // inventory was open or what was in it.
    //
    // `inventoryWindowsOpen` is the map the engine actually keeps: owner handle
    // to window, with no opinion about what kind of thing the owner is. Reading
    // that instead means a body, a crate, a shop and a squadmate arrive through
    // one path, and the transfer built on it does not need to know which it is
    // looking at.
    //
    // Positions are exported because the inventory model resolves an item by
    // section and x/y. The slot names the item to `InventorySection::getItemAt`;
    // a cell label scraped off a MyGUI widget does not.
    void AppendOpenInventories(
        std::ostringstream& json,
        ForgottenGUI* gui,
        Character* selected)
    {
        json << "\"open_inventories\":[";
        bool complete = true;
        if (gui != NULL)
        {
            unsigned int windowCount = 0;
            bool firstWindow = true;
            for (ogre_unordered_map<hand, InventoryGUI*>::type::const_iterator
                     it = gui->inventoryWindowsOpen.begin();
                 it != gui->inventoryWindowsOpen.end();
                 ++it)
            {
                InventoryGUI* window = it->second;
                if (window == NULL || !window->isVisible())
                    continue;
                if (windowCount >= MAX_OPEN_INVENTORIES)
                {
                    complete = false;
                    break;
                }
                Inventory* inventory = window->getInventory();
                if (inventory == NULL)
                    continue;
                ++windowCount;
                if (!firstWindow)
                    json << ",";
                firstWindow = false;

                const hand owner = it->first;
                Character* ownerCharacter = owner.getCharacter();
                Building* ownerBuilding = owner.getBuilding();
                Item* ownerItem = owner.getItem();
                const char* ownerKind =
                    ownerCharacter != NULL
                        ? "character"
                        : (ownerBuilding != NULL
                               ? "building"
                               : (ownerItem != NULL ? "item" : "unknown"));
                // The id a transfer will later be asked to resolve, derived by
                // the one function that resolves it.
                const std::string ownerId = OpenInventoryOwnerId(owner);
                std::string ownerName;
                if (ownerCharacter != NULL && ownerCharacter->isValid())
                {
                    ownerName = ownerCharacter->getName();
                }
                else
                {
                    RootObject* ownerObject = owner.getRootObject();
                    if (ownerObject != NULL && ownerObject->isValid())
                        ownerName = ownerObject->getName();
                }

                json << "{\"owner_id\":\"" << JsonEscape(ownerId) << "\",";
                json << "\"owner_name\":\"" << JsonEscape(ownerName) << "\",";
                json << "\"owner_kind\":\"" << ownerKind << "\",";
                // Whether this side is the player's own is the fact that
                // decides which direction a transfer even means, and it is the
                // engine's answer rather than a guess from faction or squad.
                json << "\"player_owned\":"
                     << JsonBool(
                            ownerCharacter != NULL &&
                            ownerCharacter->isValid() &&
                            ownerCharacter->isPlayerCharacter())
                     << ",";
                json << "\"money\":" << inventory->getMoney() << ",";
                json << "\"total_weight\":" << inventory->getTotalWeight() << ",";
                // Kenshi's own reach test between this window and the selected
                // character. Null when the engine could not be asked, which is
                // silence rather than a denial.
                bool rangeKnown = false;
                const bool withinRange =
                    InventoryWindowIsWithinTradeRange(window, selected, rangeKnown);
                json << "\"within_trade_range\":"
                     << (rangeKnown ? JsonBool(withinRange) : "null") << ",";
                // The exact model sections and items exposed by this window.
                json << "\"sections\":[";
                bool firstSection = true;
                unsigned int sectionCount = 0;
                for (unsigned int sectionIndex = 0;
                     sectionIndex < inventory->sectionsInSearchOrder.size();
                     ++sectionIndex)
                {
                    InventorySection* section =
                        inventory->sectionsInSearchOrder[sectionIndex];
                    if (section == NULL)
                        continue;
                    if (sectionCount >= MAX_INVENTORY_SECTIONS)
                    {
                        complete = false;
                        break;
                    }
                    ++sectionCount;
                    if (!firstSection)
                        json << ",";
                    firstSection = false;
                    json << "{\"name\":\"" << JsonEscape(section->name) << "\",";
                    // Worn or wielded, rather than carried. Kenshi's transfer
                    // takes a different path for an equipped item, and calling
                    // it on one crashed the game -- both characters' only items
                    // sat in `hip` and `legs`, which are equipment slots.
                    json << "\"equipped\":"
                         << JsonBool(section->isAnEquippedItemSection) << ",";
                    json << "\"width\":" << section->width << ",";
                    json << "\"height\":" << section->height << ",";
                    json << "\"items\":[";
                    const Ogre::vector<InventorySection::SectionItem>::type&
                        items = section->getItems();
                    bool firstItem = true;
                    for (unsigned int itemIndex = 0;
                         itemIndex < items.size();
                         ++itemIndex)
                    {
                        Item* item = items[itemIndex].item;
                        if (item == NULL || !item->isValid())
                            continue;
                        if (itemIndex >= MAX_INVENTORY_ITEMS_PER_SECTION)
                        {
                            complete = false;
                            break;
                        }
                        if (!firstItem)
                            json << ",";
                        firstItem = false;
                        json << "{";
                        AppendItemFacts(json, item);
                        json << "\"x\":" << items[itemIndex].x << ",";
                        json << "\"y\":" << items[itemIndex].y << ",";
                        json << "\"w\":" << items[itemIndex].w << ",";
                        json << "\"h\":" << items[itemIndex].h;
                        json << "}";
                    }
                    json << "]}";
                }
                json << "]}";
            }
        }
        json << "],";
        // A bounded window is not an empty one. Every consumer of this has to
        // be able to tell "nothing is here" from "we stopped counting".
        json << "\"open_inventories_complete\":" << JsonBool(complete) << ",";
    }

    // Kenshi's own word for how a transfer went.
    //
    // Reported verbatim rather than collapsed into success or failure. The
    // engine already distinguishes "no room" from "cannot afford" from "that is
    // mine" from "a thief was spotted", and every one of those is a different
    // thing for a planner to do next. Inventing a coarser vocabulary on top of
    // a finer one is how `selection_mismatch` came to mean six conditions.
    const char* TradeResultName(InventoryGUI::TradeResult::Enum value)
    {
        switch (value)
        {
        case InventoryGUI::TradeResult::OK: return "ok";
        case InventoryGUI::TradeResult::OUT_OF_RANGE: return "out_of_range";
        case InventoryGUI::TradeResult::NO_ROOM: return "no_room";
        case InventoryGUI::TradeResult::CANT_AFFORD: return "cant_afford";
        case InventoryGUI::TradeResult::CANT_AFFORD_SHOPKEPPER:
            return "shopkeeper_cant_afford";
        case InventoryGUI::TradeResult::CANT_WEAR_ITEM: return "cant_wear_item";
        case InventoryGUI::TradeResult::INCOMPATIBLE_ITEM:
            return "incompatible_item";
        case InventoryGUI::TradeResult::LOCKED: return "locked";
        case InventoryGUI::TradeResult::THIEF_DETECTED: return "thief_detected";
        case InventoryGUI::TradeResult::SELLING_STOLEN_ITEM_DETECTED:
            return "selling_stolen_item_detected";
        case InventoryGUI::TradeResult::ERROR_ITEM_POSITION:
            return "bad_item_position";
        case InventoryGUI::TradeResult::ERROR_INVALID: return "invalid";
        case InventoryGUI::TradeResult::ERROR_THATS_MINE: return "thats_mine";
        case InventoryGUI::TradeResult::ERROR_TARGET_CONSCIOUS:
            return "target_conscious";
        case InventoryGUI::TradeResult::SMUGGLING_ONLY: return "smuggling_only";
        case InventoryGUI::TradeResult::ILLEGAL_GOODS: return "illegal_goods";
        case InventoryGUI::TradeResult::UNIFORMS: return "uniforms";
        case InventoryGUI::TradeResult::CONTAINER_NOT_EMPTY:
            return "container_not_empty";
        default: return "unknown_trade_result";
        }
    }

    // Kenshi's own name for "put these two inventories side by side".
    //
    // `showInventory(hand, ...)` opens one window: a character's personal gear,
    // which is the view a player gets for stealing. Measured live against the
    // Barman it reported his `armour` and `legs` sections -- his worn kit, not
    // shop stock -- and only ever one window, so a transfer had nothing to move
    // between.
    //
    // `showTradeWindow` takes *two* hands and a type, and the type enum is the
    // engine saying that trading and looting are one mechanism with a flag:
    // TW_MONEY_TRADING, TW_LOOTING, TW_AUTO. Every attempt here to decide from
    // the outside whether looting and buying were the same problem was
    // re-deriving a distinction Kenshi had already made and named.
    const char* TradeWindowTypeName(TradeWindowType type)
    {
        switch (type)
        {
        case TW_MONEY_TRADING: return "money_trading";
        case TW_LOOTING: return "looting";
        case TW_AUTO: return "auto";
        case TW_OFF:
        default: return "off";
        }
    }

    bool ResolveTradeWindowType(const std::string& name, TradeWindowType& type)
    {
        if (name == "money_trading") { type = TW_MONEY_TRADING; return true; }
        if (name == "looting") { type = TW_LOOTING; return true; }
        if (name == "auto") { type = TW_AUTO; return true; }
        return false;
    }


    void AppendSelectedInventoryFit(
        std::ostringstream& json,
        Item* item,
        Character* selected)
    {
        json << "\"selected_inventory_accepts_item\":";
        GameData* itemData = item->getGameData();
        if (selected != NULL && selected->isValid() && itemData != NULL)
            json << JsonBool(selected->hasRoomForItem(itemData));
        else
            json << "null";
        json << ",";
    }

    // Inventory and shop cells, named. Walking the MyGUI tree can only report
    // that *a* cell exists at some bounds, which left the agent hovering cells
    // one at a time to discover what each held - a model call per cell, while a
    // human simply sees the bread. The icons themselves know their Item, so
    // walk the inventory structure instead and say what is actually there.
    void AppendNamedItemCells(
        std::ostringstream& json,
        bool& first,
        unsigned int& appended,
        bool& complete,
        Character* selected)
    {
        if (gui == NULL)
            return;
        const MyGUI::IntSize view =
            MyGUI::RenderManager::getInstancePtr() != NULL
                ? MyGUI::RenderManager::getInstancePtr()->getViewSize()
                : MyGUI::IntSize(0, 0);
        if (view.width <= 0 || view.height <= 0)
            return;

        ogre_unordered_map<hand, InventoryGUI*>::type::const_iterator windowIt =
            gui->inventoryWindowsOpen.begin();
        for (; windowIt != gui->inventoryWindowsOpen.end(); ++windowIt)
        {
            InventoryGUI* inventory = windowIt->second;
            if (inventory == NULL || appended >= MAX_VISIBLE_UI_CONTROLS)
                continue;

            std::map<std::string, InventorySectionGUI*, std::less<std::string>,
                     Ogre::STLAllocator<std::pair<std::string const, InventorySectionGUI*>,
                                        Ogre::GeneralAllocPolicy> >::const_iterator sectionIt =
                inventory->inventorySections.begin();
            for (; sectionIt != inventory->inventorySections.end(); ++sectionIt)
            {
                InventorySectionGUI* section = sectionIt->second;
                if (section == NULL)
                    continue;
                for (size_t i = 0;
                     i < section->itemsIcons.size() && appended < MAX_VISIBLE_UI_CONTROLS;
                     ++i)
                {
                    InventoryIcon* icon = section->itemsIcons[i];
                    if (icon == NULL || icon->item == NULL || icon->image == NULL)
                        continue;
                    if (!icon->image->getInheritedVisible())
                        continue;
                    const std::string name = icon->item->getName();
                    if (name.empty())
                        continue;
                    const MyGUI::IntCoord bounds = icon->image->getAbsoluteCoord();
                    if (bounds.width <= 0 || bounds.height <= 0 ||
                        bounds.left < 0 || bounds.top < 0 ||
                        bounds.left + bounds.width > view.width ||
                        bounds.top + bounds.height > view.height)
                    {
                        continue;
                    }
                    if (!first)
                        json << ",";
                    first = false;
                    ++appended;
                    json << "{";
                    json << "\"label\":\"" << JsonEscape(name) << "\",";
                    json << "\"role\":\"item\",";
                    AppendItemFacts(json, icon->item);
                    AppendSelectedInventoryFit(json, icon->item, selected);
                    json << "\"window\":\""
                         << JsonEscape(OwningWindowCaption(icon->image)) << "\",";
                    json << "\"section\":\"" << JsonEscape(sectionIt->first) << "\",";
                    json << "\"bounds\":{";
                    json << "\"min_x\":"
                         << static_cast<double>(bounds.left) / static_cast<double>(view.width)
                         << ",";
                    json << "\"max_x\":"
                         << static_cast<double>(bounds.left + bounds.width) /
                                static_cast<double>(view.width)
                         << ",";
                    json << "\"min_y\":"
                         << static_cast<double>(bounds.top) / static_cast<double>(view.height)
                         << ",";
                    json << "\"max_y\":"
                         << static_cast<double>(bounds.top + bounds.height) /
                                static_cast<double>(view.height);
                    json << "}}";
                }
            }
        }
        if (appended >= MAX_VISIBLE_UI_CONTROLS)
            complete = false;
    }

    bool IsItemCellIcon(MyGUI::Widget* widget)
    {
        MyGUI::ImageBox* image = widget->castType<MyGUI::ImageBox>(false);
        if (image == NULL)
            return false;
        // Item icons sit inside the inventory layout and are big enough to
        // click; this filters out decorative chrome and 1px spacers.
        const MyGUI::IntCoord bounds = widget->getAbsoluteCoord();
        if (bounds.width < 8 || bounds.height < 8)
            return false;
        return widget->getParent() != NULL;
    }

    void AppendVisibleUIControlTree(
        std::ostringstream& json,
        MyGUI::Widget* widget,
        const MyGUI::IntSize& view,
        unsigned int depth,
        unsigned int& visited,
        unsigned int& appended,
        bool& first,
        UIControlPass pass,
        bool& complete)
    {
        if (widget == NULL ||
            depth > MAX_UI_WIDGET_DEPTH ||
            visited >= MAX_VISITED_UI_WIDGETS ||
            appended >= MAX_VISIBLE_UI_CONTROLS)
        {
            if (widget != NULL)
                complete = false;
            return;
        }
        ++visited;

        if (widget->getInheritedVisible() && widget->getInheritedEnabled())
        {
            MyGUI::TextBox* textBox = widget->castType<MyGUI::TextBox>(false);
            const bool isButton =
                widget->castType<MyGUI::Button>(false) != NULL;

            std::string label;
            const char* role = NULL;
            if (pass == UI_PASS_ITEMS_ONLY)
            {
                if (textBox == NULL && IsItemCellIcon(widget))
                {
                    std::ostringstream ordinal;
                    ordinal << "item_" << g_itemCellOrdinal++;
                    label = ordinal.str();
                    role = "item";
                }
            }
            else if (pass == UI_PASS_BUTTONS_ONLY && isButton)
            {
                // A caption-less button - a window's close box, an icon button -
                // was previously invisible, because only TextBox-derived widgets
                // were emitted. Fall back to the widget's own name so it can
                // still be named and acted on.
                label = textBox != NULL ? textBox->getCaption().asUTF8() : std::string();
                if (label.empty())
                    label = widget->getName();
                role = "button";
            }
            else if (pass == UI_PASS_TEXT_ONLY && textBox != NULL && !isButton)
            {
                label = textBox->getCaption().asUTF8();
                role = "text";
            }

            if (role != NULL)
            {
                const MyGUI::IntCoord bounds = widget->getAbsoluteCoord();
                if (!label.empty() &&
                    bounds.width > 0 &&
                    bounds.height > 0 &&
                    bounds.left >= 0 &&
                    bounds.top >= 0 &&
                    bounds.left + bounds.width <= view.width &&
                    bounds.top + bounds.height <= view.height)
                {
                    if (!first)
                        json << ",";
                    first = false;
                    ++appended;
                    json << "{";
                    json << "\"label\":\"" << JsonEscape(label) << "\",";
                    json << "\"role\":\"" << role << "\",";
                    // `label` carries whatever a human reads, which is a
                    // caption for most widgets and the widget's own name for a
                    // caption-less button. One field answering both "what does
                    // this say" and "which control is this" cannot be joined
                    // against Kenshi's own layout files, so the identity is
                    // emitted separately and always.
                    //
                    // MyGUI prefixes every widget it instantiates from a layout
                    // with a per-loadLayout instance id, so the prefix names the
                    // open window instance and the suffix names the widget
                    // inside its layout. That is the only window identity this
                    // protocol has ever had; captions collide and are absent on
                    // most controls.
                    json << "\"widget_name\":\""
                         << JsonEscape(widget->getName()) << "\",";
                    json << "\"widget_type\":\""
                         << JsonEscape(widget->getTypeName()) << "\",";
                    json << "\"window\":\""
                         << JsonEscape(OwningWindowCaption(widget)) << "\",";
                    json << "\"bounds\":{";
                    json << "\"min_x\":"
                         << static_cast<double>(bounds.left) /
                                static_cast<double>(view.width)
                         << ",";
                    json << "\"max_x\":"
                         << static_cast<double>(bounds.left + bounds.width) /
                                static_cast<double>(view.width)
                         << ",";
                    json << "\"min_y\":"
                         << static_cast<double>(bounds.top) /
                                static_cast<double>(view.height)
                         << ",";
                    json << "\"max_y\":"
                         << static_cast<double>(bounds.top + bounds.height) /
                                static_cast<double>(view.height);
                    json << "}}";
                }
            }
        }

        for (size_t index = 0;
             index < widget->getChildCount() &&
             visited < MAX_VISITED_UI_WIDGETS &&
             appended < MAX_VISIBLE_UI_CONTROLS;
             ++index)
        {
            AppendVisibleUIControlTree(
                json,
                widget->getChildAt(index),
                view,
                depth + 1,
                visited,
                appended,
                first,
                pass,
                complete);
        }
    }

    bool AppendVisibleUIControls(
        std::ostringstream& json,
        bool includeItemCells,
        bool& complete,
        Character* selected)
    {
        complete = true;
        MyGUI::Gui* myGui = MyGUI::Gui::getInstancePtr();
        MyGUI::RenderManager* renderManager =
            MyGUI::RenderManager::getInstancePtr();
        if (myGui == NULL || renderManager == NULL)
        {
            json << "null";
            complete = false;
            return false;
        }
        const MyGUI::IntSize view = renderManager->getViewSize();
        if (view.width <= 0 || view.height <= 0)
        {
            json << "null";
            complete = false;
            return false;
        }

        json << "[";
        bool first = true;
        unsigned int appended = 0;
        g_itemCellOrdinal = 0;
        // Item cells are emitted only while an inventory or trade window is
        // open. Everywhere else the world is full of decorative images, and
        // exporting them would spend the cap on things nothing can act on.
        const UIControlPass passes[3] = {
            UI_PASS_BUTTONS_ONLY,
            UI_PASS_ITEMS_ONLY,
            UI_PASS_TEXT_ONLY};
        // Always walk every pass; only the item pass is conditional. Sizing the
        // loop by a count instead of skipping by role previously dropped the
        // text pass entirely whenever no inventory was open, which silently
        // removed dialogue options from the export.
        for (unsigned int index = 0; index < 3; ++index)
        {
            if (passes[index] == UI_PASS_ITEMS_ONLY)
            {
                // Named cells come from the inventory structure, not the widget
                // tree, so this pass does not walk widgets at all.
                if (includeItemCells)
                    AppendNamedItemCells(
                        json,
                        first,
                        appended,
                        complete,
                        selected);
                continue;
            }
            // Each pass re-walks the tree with its own visit budget so a wide
            // text-heavy HUD cannot exhaust the walk before buttons are seen.
            unsigned int visited = 0;
            MyGUI::EnumeratorWidgetPtr roots = myGui->getEnumerator();
            while (roots.next() &&
                   visited < MAX_VISITED_UI_WIDGETS &&
                   appended < MAX_VISIBLE_UI_CONTROLS)
            {
                AppendVisibleUIControlTree(
                    json,
                    roots.current(),
                    view,
                    0,
                    visited,
                    appended,
                    first,
                    passes[index],
                    complete);
            }
            if (visited >= MAX_VISITED_UI_WIDGETS ||
                appended >= MAX_VISIBLE_UI_CONTROLS)
            {
                complete = false;
            }
        }
        json << "]";
        return true;
    }

    void MonitorActiveNativeCommand(PlayerInterface* player)
    {
        if (!g_activeNativeCommand.active)
            return;

        if (g_activeNativeCommand.isDialogueOptionPending)
        {
            if (gui == NULL ||
                gui->dialogue == NULL ||
                !gui->dialogue->isVisible() ||
                gui->dialogue->dialogue == NULL)
            {
                FinishActiveNativeCommand("completed", "dialogue_closed");
                return;
            }
            std::string currentTargetId;
            if (!TryGetDialogueTargetId(currentTargetId))
            {
                FinishActiveNativeCommand(
                    "cancelled", "dialogue_target_unavailable");
                return;
            }
            if (currentTargetId != g_activeNativeCommand.targetId)
            {
                FinishActiveNativeCommand(
                    "completed", "dialogue_target_changed");
                return;
            }
            std::vector<std::string> currentOptions;
            if (!TryGetDialogueOptions(currentOptions))
            {
                FinishActiveNativeCommand(
                    "cancelled", "dialogue_options_unavailable");
                return;
            }
            if (currentOptions != g_activeNativeCommand.dialogueOptionsBefore)
            {
                FinishActiveNativeCommand(
                    "completed", "dialogue_options_changed");
                return;
            }
            if (GetTickCount() -
                    g_activeNativeCommand.dialogueOptionStartedTick >=
                10000)
            {
                FinishActiveNativeCommand(
                    "cancelled", "dialogue_option_timeout");
            }
            return;
        }

        if (g_activeNativeCommand.isProspectingSurveyPending)
        {
            ProspectingWindow* prospecting = ProspectingWindow::getSingleton();
            if (prospecting == NULL || prospecting->window == NULL)
            {
                FinishActiveNativeCommand(
                    "cancelled", "prospecting_window_unavailable");
                return;
            }
            const DWORD now = GetTickCount();
            const bool rendered = prospecting->window->getVisible();
            if (!g_activeNativeCommand.prospectingResultObserved)
            {
                if (!rendered)
                {
                    if (now - g_activeNativeCommand.prospectingStartedTick >= 30000)
                    {
                        FinishActiveNativeCommand(
                            "cancelled", "prospecting_results_timeout");
                    }
                    return;
                }
                if (!CaptureVisibleProspectSurvey(prospecting))
                {
                    FinishActiveNativeCommand(
                        "cancelled", "prospecting_results_unavailable");
                    return;
                }
                g_activeNativeCommand.prospectingResultObserved = true;
                prospecting->hide();
                prospecting->window->setVisible(false);
                g_activeNativeCommand.prospectingHiddenSinceTick = now;
                return;
            }
            if (rendered)
            {
                prospecting->hide();
                prospecting->window->setVisible(false);
                g_activeNativeCommand.prospectingHiddenSinceTick = now;
                return;
            }
            if (now - g_activeNativeCommand.prospectingHiddenSinceTick < 250)
                return;
            // The real results were visible and read, then the concrete widget
            // remained hidden across later game updates.
            FinishActiveNativeCommand(
                "completed", "resource_survey_published");
            return;
        }

        if (g_activeNativeCommand.isTradeWindowPending)
        {
            // The pair is observed, not counted. "Two windows are open" was the
            // old terminal, and it reported `trade_window_open` for a pairing a
            // transfer then could not find on either side -- a count cannot say
            // *whose* windows those are.
            //
            // The terminal is now the state the next command actually needs:
            // both requested owners resolvable by the same advertised id the
            // agent will send back. Completion evidence and the agent's own view
            // of the world come from one function, so a window that this reports
            // as open is a window `transfer_item` can act on.
            InventoryGUI* firstWindow =
                gui != NULL
                    ? FindOpenInventoryWindow(
                          gui, g_activeNativeCommand.targetId)
                    : NULL;
            InventoryGUI* secondWindow =
                gui != NULL
                    ? FindOpenInventoryWindow(
                          gui, g_activeNativeCommand.destinationId)
                    : NULL;
            if (firstWindow == NULL || secondWindow == NULL)
                return;

            // Open is not the same as reachable, and `showTradeWindow` does not
            // care: it draws whichever two hands it is given, from any distance.
            // Measured twice by Levi -- a trade screen against a shopkeeper on
            // the far side of The Hub, refusing every transfer in it, because
            // the window has no proximity opinion and the refusal only arrives
            // once an item is moved.
            //
            // So the engine is asked the moment there is something to ask it
            // about. `isWithinRangeToTrade` is the same predicate the transfer
            // consults, and a pairing that fails it is closed rather than left
            // standing: a trade window that cannot trade is a worse state to
            // hand back than no window at all.
            Character* selected =
                player != NULL ? player->selectedCharacter.getCharacter() : NULL;
            bool rangeKnown = false;
            const bool reachable =
                InventoryWindowIsWithinTradeRange(
                    secondWindow, selected, rangeKnown);
            if (rangeKnown && !reachable)
            {
                gui->closeTradeWindow();
                FinishActiveNativeCommand("cancelled", "out_of_trade_range");
                return;
            }
            FinishActiveNativeCommand("completed", "trade_window_open");
            return;
        }

        // PLAYER_TALK_TO can open a nearby conversation while the world stays
        // paused. That exact target is success, not a pause stall. Check the
        // native terminal before the generic movement-pause watchdog so a
        // dialogue modal (which removes the pause control) can never turn a
        // completed talk order into `world_paused`.
        if (!g_activeNativeCommand.isWalk &&
            (!g_activeNativeCommand.isContextAction ||
             g_activeNativeCommand.expectedTask == PLAYER_TALK_TO) &&
            IsExactDialogueTargetOpen(g_activeNativeCommand.targetHandle))
        {
            FinishActiveNativeCommand(
                "completed",
                "exact_dialogue_target_open");
            return;
        }

        std::string selectedId;
        hand selectedHandle;
        std::vector<hand> selectedHandles;
        const char* selectionReason =
            g_activeNativeCommand.selectedCharacterIds.size() > 1
                ? ResolveExactSelectionBasis(
                    player,
                    g_activeNativeCommand.selectedCharacterIds,
                    selectedId,
                    selectedHandle,
                    selectedHandles)
                : (TryGetExactSelection(player, selectedId, selectedHandle)
                       ? SELECTION_BASIS_OK
                       : "selection_singleton_unresolved");
        if (selectionReason != SELECTION_BASIS_OK)
        {
            FinishActiveNativeCommand("cancelled", selectionReason);
            return;
        }
        if (selectedId != g_activeNativeCommand.selectedCharacterId)
        {
            // Resolvable, but for someone else: the order outlived the party it
            // was authored for. Distinct from being unable to resolve at all.
            FinishActiveNativeCommand("cancelled", "selection_primary_changed");
            return;
        }
        if (selectedHandles.empty())
            selectedHandles.push_back(selectedHandle);
        Character* walker = selectedHandle.getCharacter();
        bool resourceTaskActive = false;
        if (g_activeNativeCommand.isContextAction)
        {
            // Dispatched on the recorded target kind, not on a task value.
            // Asking `expectedTask == FIRST_AID_ORDER` made a task stand in for
            // a target kind, so a character order silently inherited the
            // building branch below and was cancelled for having no building.
            if (g_activeNativeCommand.targetKind ==
                NATIVE_TARGET_NEARBY_CHARACTER)
            {
                // An order at a person. The target is looked up in the same
                // nearby sphere it was issued from, because that is the
                // population the order names -- squad membership is not
                // required and is exactly the wrong fence: the people orders
                // are for are hostiles, the unconscious, and the dead.
                Character* target =
                    g_activeNativeCommand.targetHandle.getCharacter();
                bool exactIdentityFound = false;
                Character* current = FindExactNearbyCharacter(
                    player,
                    g_activeNativeCommand.targetId,
                    exactIdentityFound);
                if (target == NULL ||
                    !target->isValid() ||
                    current == NULL ||
                    !SameHandleIdentity(
                        target->getHandle(),
                        current->getHandle()))
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_lifetime_changed");
                    return;
                }
                // Goal adoption is the terminal proof, the same one first aid
                // and mining already rely on. Kenshi holding the exact ordered
                // task against the exact ordered person is the only evidence
                // that the order took; nothing else here can say so.
                if (HasExactContextGoal(
                        walker,
                        g_activeNativeCommand.expectedTask,
                        current->getHandle()))
                {
                    FinishActiveNativeCommand(
                        "completed",
                        "context_task_started");
                    return;
                }
            }
            else if (g_activeNativeCommand.targetKind ==
                     NATIVE_TARGET_SQUAD_CHARACTER)
            {
                Character* target =
                    g_activeNativeCommand.targetHandle.getCharacter();
                bool exactIdentityFound = false;
                Character* current = FindExactSquadMember(
                    player,
                    g_activeNativeCommand.targetId,
                    exactIdentityFound);
                if (target == NULL ||
                    !target->isValid() ||
                    current == NULL ||
                    !SameHandleIdentity(
                        target->getHandle(),
                        current->getHandle()))
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_lifetime_changed");
                    return;
                }
                if (HasExactContextGoal(
                        walker,
                        g_activeNativeCommand.expectedTask,
                        current->getHandle()))
                {
                    FinishActiveNativeCommand(
                        "completed",
                        "context_task_started");
                    return;
                }
            }
            else if (g_activeNativeCommand.targetKind != NATIVE_TARGET_BUILDING)
            {
                // No fall-through default. Being the last branch is how the
                // building case acquired a character order in the first place,
                // and a target kind nobody has written monitoring for must say
                // so rather than be resolved as whatever happens to be last.
                FinishActiveNativeCommand(
                    "cancelled",
                    "target_kind_unmonitored");
                return;
            }
            else
            {
                Building* contextTarget =
                    g_activeNativeCommand.targetHandle.getBuilding();
                if (contextTarget == NULL ||
                    !contextTarget->isValid() ||
                    StableEntityId(contextTarget->getHandle()) !=
                        g_activeNativeCommand.targetId)
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_lifetime_changed");
                    return;
                }
                if (!InspectNaturalResource(contextTarget)
                        .structurallyRecognized ||
                    contextTarget->getDefaultTask() !=
                        g_activeNativeCommand.expectedTask)
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_role_invalid");
                    return;
                }
                if (g_activeNativeCommand.isResourceProduction)
                {
                    int outputQuantity = 0;
                    const bool outputKnown =
                        TryGetInventorySectionQuantity(
                            contextTarget,
                            "out",
                            outputQuantity);
                    resourceTaskActive = HasSelectedResourceOperator(
                        contextTarget,
                        g_activeNativeCommand.selectedCharacterIds);
                    const KenshiAgentTelemetry::ResourceProductionState state =
                        KenshiAgentTelemetry::EvaluateResourceProduction(
                            outputKnown,
                            outputQuantity,
                            static_cast<int>(
                                g_activeNativeCommand.minimumOutputQuantity),
                            resourceTaskActive,
                            g_activeNativeCommand.resourceTaskObserved);
                    if (state == KenshiAgentTelemetry::
                            RESOURCE_PRODUCTION_OUTPUT_UNKNOWN)
                    {
                        FinishActiveNativeCommand(
                            "cancelled",
                            "resource_output_unknown");
                        return;
                    }
                    if (state == KenshiAgentTelemetry::
                            RESOURCE_PRODUCTION_OUTPUT_READY)
                    {
                        bool selectedOrdersActive = false;
                        bool resourceReleaseUnavailable = false;
                        std::vector<Character*> resourceRecipients;
                        for (std::vector<hand>::const_iterator it =
                                 selectedHandles.begin();
                             it != selectedHandles.end();
                             ++it)
                        {
                            Character* recipient = it->getCharacter();
                            AI* resourceAI =
                                recipient != NULL ? recipient->getAI() : NULL;
                            AITaskSytem* resourceTasks =
                                resourceAI != NULL
                                    ? resourceAI->getTaskSystem()
                                    : NULL;
                            CharMovement* resourceMovement =
                                recipient != NULL
                                    ? recipient->getMovement()
                                    : NULL;
                            if (recipient == NULL || resourceTasks == NULL ||
                                resourceMovement == NULL)
                            {
                                resourceReleaseUnavailable = true;
                                continue;
                            }
                            resourceRecipients.push_back(recipient);
                            if (resourceTasks->hasPlayerOrders())
                                selectedOrdersActive = true;
                        }
                        if (g_activeNativeCommand.
                                resourceTaskIssuedByCommand &&
                            resourceReleaseUnavailable)
                        {
                            FinishActiveNativeCommand(
                                "cancelled",
                                "resource_task_release_unavailable");
                            return;
                        }
                        const bool releaseStillActive =
                            resourceTaskActive || selectedOrdersActive;
                        const bool releaseConfirmed =
                            g_activeNativeCommand.resourceTaskReleaseRequested &&
                            KenshiAgentTelemetry::
                                ObserveResourceTaskReleaseConfirmation(
                                    g_activeNativeCommand.
                                        resourceTaskReleaseWindow,
                                    releaseStillActive,
                                    GetTickCount());
                        const KenshiAgentTelemetry::ResourceTaskReleaseState
                            releaseState = KenshiAgentTelemetry::
                                EvaluateResourceTaskRelease(
                                    g_activeNativeCommand.
                                        resourceTaskIssuedByCommand,
                                    g_activeNativeCommand.
                                        resourceTaskReleaseRequested,
                                    releaseConfirmed);
                        if (releaseState == KenshiAgentTelemetry::
                                RESOURCE_TASK_RELEASE_REQUESTED)
                        {
                            // newPlayerTaskSelectedCharacters(..., false)
                            // replaced every recipient's prior ordinary order
                            // queue, so this command owns those queues. Release
                            // all recipients, not merely the primary; selection
                            // is a dispatch basis and the accepted-operator set
                            // may contain any subset of it.
                            for (std::vector<Character*>::const_iterator it =
                                     resourceRecipients.begin();
                                 it != resourceRecipients.end();
                                 ++it)
                            {
                                Character* recipient = *it;
                                AI* resourceAI = recipient->getAI();
                                AITaskSytem* resourceTasks =
                                    resourceAI->getTaskSystem();
                                CharMovement* resourceMovement =
                                    recipient->getMovement();
                                recipient->removeJob(
                                    g_activeNativeCommand.expectedTask);
                                resourceTasks->clearOrders();
                                resourceMovement->halt();
                            }
                            g_activeNativeCommand.
                                resourceTaskReleaseRequested = true;
                            KenshiAgentTelemetry::
                                ResetResourceTaskReleaseConfirmationWindow(
                                    g_activeNativeCommand.
                                        resourceTaskReleaseWindow);
                            return;
                        }
                        if (releaseState == KenshiAgentTelemetry::
                                RESOURCE_TASK_RELEASE_WAITING)
                        {
                            return;
                        }
                        FinishActiveNativeCommand(
                            "completed",
                            releaseState == KenshiAgentTelemetry::
                                    RESOURCE_TASK_RELEASE_CONFIRMED
                                ? "resource_output_ready_task_released"
                                : "resource_output_ready");
                        return;
                    }
                    if (state == KenshiAgentTelemetry::
                            RESOURCE_PRODUCTION_TASK_ENDED)
                    {
                        FinishActiveNativeCommand(
                            "cancelled",
                            "resource_task_ended_without_output");
                        return;
                    }
                    if (resourceTaskActive)
                        g_activeNativeCommand.resourceTaskObserved = true;
                }
                else if (HasSelectedResourceOperator(
                             contextTarget,
                             g_activeNativeCommand.selectedCharacterIds))
                {
                    FinishActiveNativeCommand(
                        "completed",
                        "resource_operator_accepted");
                    return;
                }
            }
        }

        const bool worldPaused = ou != NULL && ou->isPaused();
        if (worldPaused)
        {
            // Paused gaps are controller/human thinking time, not evidence
            // that Kenshi's pathfinder has failed to advance.
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
        }
        if (KenshiAgentTelemetry::ObserveNativeMovementPause(
                g_activeNativeCommand.pauseWindow,
                worldPaused,
                GetTickCount()))
        {
            FinishActiveNativeCommand("cancelled", "world_paused");
            return;
        }
        if (worldPaused)
            return;

        if (g_activeNativeCommand.isContextAction)
        {
            if (
                g_activeNativeCommand.isResourceProduction &&
                resourceTaskActive)
            {
                KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                    g_activeNativeCommand.stallWindow);
                return;
            }
            if (walker == NULL || !walker->isValid())
            {
                FinishActiveNativeCommand("cancelled", "selection_mismatch");
                return;
            }
            const Ogre::Vector3 here = walker->getPosition();
            if (KenshiAgentTelemetry::ObserveNativeMovementStall(
                    g_activeNativeCommand.stallWindow,
                    false,
                    here.x,
                    here.z,
                    GetTickCount()))
            {
                // Say which of the two silences this was. A character that holds
                // no order and no goal is not stuck: Kenshi took the order and
                // let it go, and reporting that as `movement_stalled` sends the
                // agent to look at pathing for a problem that was never there.
                FinishActiveNativeCommand(
                    "cancelled",
                    HoldsNoOrderAtAll(walker)
                        ? "order_not_retained"
                        : "movement_stalled");
            }
            return;
        }

        if (g_activeNativeCommand.isWalk)
        {
            if (walker == NULL || !walker->isValid())
            {
                FinishActiveNativeCommand("cancelled", "selection_mismatch");
                return;
            }
            TownBase* mapTown = NULL;
            if (g_activeNativeCommand.isMapTravel)
            {
                mapTown =
                    g_activeNativeCommand.targetHandle.getTown();
                if (mapTown == NULL ||
                    !mapTown->isValid() ||
                    StableEntityId(mapTown->getHandle()) !=
                        g_activeNativeCommand.targetId)
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_lifetime_changed");
                    return;
                }
                if (!IsKnownMapDestination(mapTown))
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "target_role_invalid");
                    return;
                }
            }
            float destinationX = g_activeNativeCommand.destinationX;
            float destinationZ = g_activeNativeCommand.destinationZ;
            if (!g_activeNativeCommand.hasFixedDestination)
            {
                // Walking to somebody who is walking themselves: aim at where
                // they are now, not where they were when the order was given.
                bool exactSquadIdentityFound = false;
                Character* follow = g_activeNativeCommand.isSquadRegroup
                    ? FindExactSquadMember(
                        player,
                        g_activeNativeCommand.targetId,
                        exactSquadIdentityFound)
                    : g_activeNativeCommand.targetHandle.getCharacter();
                if (follow == NULL ||
                    !follow->isValid() ||
                    StableEntityId(follow) != g_activeNativeCommand.targetId)
                {
                    FinishActiveNativeCommand("cancelled", "target_lifetime_changed");
                    return;
                }
                if (g_activeNativeCommand.isSquadRegroup)
                    g_activeNativeCommand.targetHandle = follow->getHandle();
                const Ogre::Vector3 followPosition = follow->getPosition();
                destinationX = followPosition.x;
                destinationZ = followPosition.z;
            }
            const Ogre::Vector3 here = walker->getPosition();
            if (mapTown != NULL)
            {
                bool currentTownIdentityMatches = true;
                bool insideTownWalls = true;
                std::vector<KenshiAgentTelemetry::NativeMovementPosition>
                    positions;
                for (unsigned int index = 0;
                     index < selectedHandles.size();
                     ++index)
                {
                    Character* member =
                        selectedHandles[index].getCharacter();
                    if (member == NULL || !member->isValid())
                    {
                        FinishActiveNativeCommand(
                            "cancelled",
                            "selection_mismatch");
                        return;
                    }
                    TownBase* currentTown =
                        member->getCurrentTownLocation();
                    currentTownIdentityMatches =
                        currentTownIdentityMatches &&
                        currentTown != NULL &&
                        currentTown->isValid() &&
                        StableEntityId(currentTown->getHandle()) ==
                            g_activeNativeCommand.targetId;
                    insideTownWalls =
                        insideTownWalls &&
                        member->amInsideTownWalls() != 0;
                    const Ogre::Vector3 memberPosition =
                        member->getPosition();
                    KenshiAgentTelemetry::NativeMovementPosition position;
                    position.x = memberPosition.x;
                    position.z = memberPosition.z;
                    positions.push_back(position);
                }
                float stallX = here.x;
                float stallZ = here.z;
                const bool currentLegReached = KenshiAgentTelemetry::
                    HasGroupReachedDestination(
                        positions,
                        destinationX,
                        destinationZ,
                        stallX,
                        stallZ);
                const KenshiAgentTelemetry::NativeMapTravelDecision decision =
                    KenshiAgentTelemetry::EvaluateNativeMapTravel(
                        currentTownIdentityMatches,
                        mapTown->hasGates(),
                        insideTownWalls,
                        currentLegReached,
                        g_activeNativeCommand.mapInteriorOrderIssued);
                if (decision == KenshiAgentTelemetry::MAP_TRAVEL_COMPLETE)
                {
                    if (ou != NULL && !ou->isPaused())
                    {
                        // Long travel is one controller-owned option. Return
                        // the newly usable town state deliberately to planning.
                        ou->togglePause(true);
                    }
                    FinishActiveNativeCommand(
                        "completed",
                        "map_destination_reached");
                    return;
                }
                if (decision ==
                    KenshiAgentTelemetry::MAP_TRAVEL_ISSUE_INTERIOR_ORDER)
                {
                    const Ogre::Vector3 interior =
                        mapTown->getPosition();
                    player->newPlayerTaskSelectedCharacters(
                        MOVE_CUS_ORDERED,
                        hand(),
                        NULL,
                        interior,
                        false);
                    g_activeNativeCommand.mapInteriorOrderIssued = true;
                    g_activeNativeCommand.originX = here.x;
                    g_activeNativeCommand.originZ = here.z;
                    g_activeNativeCommand.destinationX = interior.x;
                    g_activeNativeCommand.destinationZ = interior.z;
                    g_lastNativeCommandResult =
                        "map_destination_entry_issued";
                    KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                        g_activeNativeCommand.stallWindow);
                    return;
                }
                if (decision ==
                    KenshiAgentTelemetry::MAP_TRAVEL_CANCEL_UNCONFIRMED)
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "map_destination_entry_unconfirmed");
                    return;
                }
                if (KenshiAgentTelemetry::ObserveNativeMovementStall(
                        g_activeNativeCommand.stallWindow,
                        false,
                        stallX,
                        stallZ,
                        GetTickCount()))
                {
                    FinishActiveNativeCommand(
                        "cancelled",
                        "movement_stalled");
                }
                return;
            }
            bool buildingExitIndoors = false;
            if (g_activeNativeCommand.isBuildingExit)
            {
                const hand& currentBuilding = walker->isIndoors();
                Building* resolvedBuilding = currentBuilding.getBuilding();
                buildingExitIndoors =
                    KenshiAgentTelemetry::HasResolvedIndoorBuilding(
                        currentBuilding.isValid(),
                        resolvedBuilding != NULL,
                        resolvedBuilding != NULL &&
                            resolvedBuilding->isValid());
                if (KenshiAgentTelemetry::ObserveNativeOutdoorConfirmation(
                        g_activeNativeCommand.outdoorWindow,
                        buildingExitIndoors,
                        GetTickCount()))
                {
                    FinishActiveNativeCommand(
                        "completed",
                        "left_current_building");
                    return;
                }
            }
            bool arrived = false;
            float stallX = here.x;
            float stallZ = here.z;
            if (g_activeNativeCommand.isBuildingExit)
            {
                arrived =
                    KenshiAgentTelemetry::HasReachedResolvedExitDestination(
                        g_activeNativeCommand.originX,
                        g_activeNativeCommand.originZ,
                        destinationX,
                        destinationZ,
                        here.x,
                        here.z);
            }
            else if (g_activeNativeCommand.hasFixedDestination)
            {
                arrived =
                    KenshiAgentTelemetry::HasReachedFixedDirectionDestination(
                        g_activeNativeCommand.originX,
                        g_activeNativeCommand.originZ,
                        destinationX,
                        destinationZ,
                        here.x,
                        here.z);
            }
            else
            {
                const float toleranceSquared =
                    KenshiAgentTelemetry::WALK_DESTINATION_TOLERANCE *
                    KenshiAgentTelemetry::WALK_DESTINATION_TOLERANCE;
                if (selectedHandles.size() > 1)
                {
                    std::vector<KenshiAgentTelemetry::NativeMovementPosition>
                        positions;
                    for (unsigned int index = 0;
                         index < selectedHandles.size();
                         ++index)
                    {
                        Character* member =
                            selectedHandles[index].getCharacter();
                        if (member == NULL || !member->isValid())
                        {
                            FinishActiveNativeCommand(
                                "cancelled",
                                "selection_mismatch");
                            return;
                        }
                        const Ogre::Vector3 memberPosition =
                            member->getPosition();
                        KenshiAgentTelemetry::NativeMovementPosition position;
                        position.x = memberPosition.x;
                        position.z = memberPosition.z;
                        positions.push_back(position);
                    }
                    arrived = KenshiAgentTelemetry::
                        HasGroupReachedDestination(
                            positions,
                            destinationX,
                            destinationZ,
                            stallX,
                            stallZ);
                }
                else
                {
                    const float dx = here.x - destinationX;
                    const float dz = here.z - destinationZ;
                    arrived = dx * dx + dz * dz <= toleranceSquared;
                }
            }
            if (arrived)
            {
                if (g_activeNativeCommand.isSquadRegroup &&
                    ou != NULL &&
                    !ou->isPaused())
                {
                    ou->togglePause(true);
                }
                FinishActiveNativeCommand(
                    "completed",
                    g_activeNativeCommand.isBuildingExit
                        ? "outside_door_destination_reached"
                        : (g_activeNativeCommand.isSquadRegroup
                            ? "squad_member_reached"
                            : "walk_destination_reached"));
                return;
            }
            if (KenshiAgentTelemetry::ObserveNativeMovementStall(
                    g_activeNativeCommand.stallWindow,
                    false,
                    stallX,
                    stallZ,
                    GetTickCount()))
            {
                FinishActiveNativeCommand("cancelled", "movement_stalled");
            }
            return;
        }

        Character* target = g_activeNativeCommand.targetHandle.getCharacter();
        if (target == NULL ||
            !target->isValid() ||
            StableEntityId(target) != g_activeNativeCommand.targetId)
        {
            FinishActiveNativeCommand(
                "cancelled",
                "target_lifetime_changed");
            return;
        }
        Character* selected = selectedHandle.getCharacter();
        if (!IsValidDialogueTarget(selected, target))
        {
            FinishActiveNativeCommand(
                "cancelled",
                "target_role_invalid");
            return;
        }

        if (IsExactDialogueTargetOpen(g_activeNativeCommand.targetHandle))
        {
            FinishActiveNativeCommand(
                "completed",
                "exact_dialogue_target_open");
        }
    }

    void ProcessNativeCommandRequest(PlayerInterface* player)
    {
        ++g_nativeCommandSequence;
        // Only a default until the request is parsed; it is overwritten with
        // what was actually asked for, or every move would report as an
        // approach now that more than one command exists.
        g_lastNativeCommand = "approach_confirmed_vendor";
        g_lastNativeCommandTarget.clear();
        g_lastNativeCommandTargetId.clear();

        std::string payload;
        std::string error;
        if (!KenshiAgentTelemetry::ReadUtf8Bounded(
                g_outputDirectory,
                NATIVE_COMMAND_REQUEST_FILE_W,
                MAX_NATIVE_COMMAND_BYTES,
                payload,
                error))
        {
            g_lastNativeCommandResult = "malformed_request";
            ErrorLog(
                std::string("KenshiAgentTelemetry request read failed: ") +
                error);
            return;
        }

        NativeCommandRequest request;
        std::string rejectionReason;
        if (!ParseNativeCommandRequest(payload, request, rejectionReason))
        {
            g_lastNativeCommandResult = rejectionReason;
            const bool isDirection =
                request.command == "move_in_direction";
            const bool isBuildingExit =
                request.command == "exit_current_building" ||
                request.command == "survey_local_resources";
            const bool hasCommandIdentity =
                isDirection
                    ? (request.targetId.empty() &&
                       request.bearingDegrees >= 0.0 &&
                       request.bearingDegrees < 360.0 &&
                       request.distanceUnits > 0.0 &&
                       request.distanceUnits <= 2000.0)
                    : (isBuildingExit
                        ? (request.targetId.empty() &&
                           request.bearingDegrees == 0.0 &&
                           request.distanceUnits == 0.0)
                        : (!request.targetId.empty() &&
                       request.bearingDegrees == 0.0 &&
                       request.distanceUnits == 0.0));
            if (IsValidCommandId(request.commandId) &&
                KenshiAgentTelemetry::IsKnownNativeCommand(request.command) &&
                hasCommandIdentity &&
                !request.selectedCharacterIds.empty() &&
                FindNativeAcknowledgement(request.commandId) < 0)
            {
                RejectNativeCommand(request, "malformed_request");
            }
            ErrorLog(
                "KenshiAgentTelemetry rejected malformed native command request.");
            return;
        }

        if (FindNativeAcknowledgement(request.commandId) >= 0)
        {
            // Keep the original bounded acknowledgement unchanged so a
            // duplicate command_id can never look like a new acceptance.
            g_lastNativeCommandResult = "duplicate_command_id";
            return;
        }
        if (g_activeNativeCommand.active)
        {
            RejectNativeCommand(request, "command_already_active");
            return;
        }
        const bool isApproach = request.command == "approach_confirmed_vendor";
        const bool isMove = request.command == "move_to_character";
        const bool isSquadSelection =
            request.command == "select_squad_member";
        const bool isSquadRegroup =
            request.command == "regroup_with_squad_member";
        const bool isDirection = request.command == "move_in_direction";
        const bool isMapTravel =
            request.command == "travel_to_map_destination";
        const bool isBuildingExit =
            request.command == "exit_current_building";
        const bool isContextAction =
            request.command == "perform_context_action";
        const bool isCharacterOrder =
            request.command == "perform_character_order";
        const bool isResourceProduction =
            request.command == "produce_resource_output";
        const bool isTransfer = request.command == "transfer_item";
        const bool isTradeWindow = request.command == "open_trade_window";
        const bool isResourceSurvey =
            request.command == "survey_local_resources";
        const bool isBodyShiftProbe =
            request.command == "shift_body_platoon";
        const bool isBodyShift =
            request.command == "shift_into_body";
        // Time control. Kenshi owns the clock through `GameWorld`, so pausing
        // and setting speed stopped being keystrokes.
        const bool isCloseActiveInterface =
            request.command == "close_active_interface";
        const bool isDialogueOption =
            request.command == "select_dialogue_option";
        const bool isPause = request.command == "pause";
        const bool isSetSpeed = request.command == "set_speed";
        if (isCloseActiveInterface || isDialogueOption || isPause || isSetSpeed || isBodyShift || isBodyShiftProbe || isApproach || isMove || isSquadSelection || isSquadRegroup ||
            isDirection || isMapTravel || isBuildingExit || isContextAction ||
            isCharacterOrder ||
            isResourceProduction || isResourceSurvey ||
            isTransfer || isTradeWindow)
            g_lastNativeCommand = request.command;
        if (!isApproach &&
            !isMove &&
            !isSquadSelection &&
            !isSquadRegroup &&
            !isDirection &&
            !isMapTravel &&
            !isBuildingExit &&
            !isContextAction &&
            !isCharacterOrder &&
            !isResourceProduction &&
            !isResourceSurvey &&
            !isTransfer &&
            !isTradeWindow &&
            !isBodyShiftProbe &&
            !isBodyShift &&
            !isPause &&
            !isSetSpeed &&
            !isCloseActiveInterface &&
            !isDialogueOption)
        {
            // The telemetry acknowledgement schema is intentionally limited
            // to reviewed commands. Do not publish an unparseable ack.
            g_lastNativeCommandResult = "unsupported_command";
            return;
        }
        g_activeNativeCommand.isSquadRegroup = false;
        if (request.controlMode != "native_assisted")
        {
            RejectNativeCommand(request, "wrong_control_mode");
            return;
        }
        if (request.identitySessionId != IdentitySessionId())
        {
            RejectNativeCommand(request, "identity_session_mismatch");
            return;
        }
        // Python has already re-proved authority against the request basis,
        // but the atomic file + hotkey + UI-hook transport can span telemetry
        // publications. Admit only that small measured window, then revalidate
        // every command-specific identity and authority fact below.
        if (!KenshiAgentTelemetry::
                IsNativeCommandRevisionWithinTransportWindow(
                    request.basedOnTelemetrySequence,
                    g_sequence))
        {
            if (request.basedOnTelemetrySequence > g_sequence)
            {
                // Do not serialize an acknowledgement whose claimed request
                // basis is in the future; that would poison strict telemetry.
                g_lastNativeCommandResult = "future_revision";
                return;
            }
            RejectNativeCommand(request, "stale_revision");
            return;
        }

        if (isDialogueOption)
        {
            if (gui == NULL ||
                gui->dialogue == NULL ||
                !gui->dialogue->isVisible() ||
                gui->dialogue->dialogue == NULL)
            {
                RejectNativeCommand(request, "dialogue_not_open");
                return;
            }
            std::string currentTargetId;
            if (!TryGetDialogueTargetId(currentTargetId) ||
                currentTargetId != request.targetId)
            {
                RejectNativeCommand(request, "dialogue_target_changed");
                return;
            }
            std::vector<std::string> currentOptions;
            if (!TryGetDialogueOptions(currentOptions))
            {
                RejectNativeCommand(request, "dialogue_options_unavailable");
                return;
            }
            const size_t optionIndex =
                static_cast<size_t>(request.dialogueOptionIndex);
            if (optionIndex >= currentOptions.size() ||
                currentOptions[optionIndex] != request.dialogueOptionText)
            {
                RejectNativeCommand(request, "dialogue_option_changed");
                return;
            }

            Dialogue* dialogue = gui->dialogue->dialogue;
            dialogue->replyClicked(request.dialogueOptionIndex);
            AddNativeAcknowledgement(
                request,
                "accepted",
                "dialogue_option_selected",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.destinationId.clear();
            g_activeNativeCommand.selectedCharacterId.clear();
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.targetHandle = hand();
            g_activeNativeCommand.selectedHandle = hand();
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.isSquadRegroup = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isTradeWindowPending = false;
            g_activeNativeCommand.isDialogueOptionPending = true;
            g_activeNativeCommand.dialogueOptionText =
                request.dialogueOptionText;
            g_activeNativeCommand.dialogueOptionsBefore = currentOptions;
            g_activeNativeCommand.dialogueOptionStartedTick = GetTickCount();
            g_activeNativeCommand.isProspectingSurveyPending = false;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            g_lastNativeCommandResult = "dialogue_option_selected";
            g_lastNativeCommandTargetId = request.targetId;
            return;
        }

        if (isCloseActiveInterface)
        {
            if (gui == NULL)
            {
                RejectNativeCommand(request, "gui_unavailable");
                return;
            }
            ProspectingWindow* prospecting = ProspectingWindow::getSingleton();
            if (prospecting != NULL && prospecting->getVisible())
                prospecting->hide();
            if (prospecting != NULL && prospecting->window != NULL)
                prospecting->window->setVisible(false);
            if (gui->hasModalMessage())
                gui->hideMessageBox(false);
            if (gui->dialogue != NULL && gui->dialogue->getVisible())
                gui->dialogue->hide(gui->dialogue->dialogue);
            gui->closeTradeWindow();
            gui->closeAllInventories();
            gui->closeAllCharacterStatsWindows();
            ManagementScreen* management = ManagementScreen::getSingleton();
            if (management != NULL && management->getVisible())
                management->setVisible(false, -1);
            gui->closeAllWindows();

            const bool prospectingVisible =
                prospecting != NULL &&
                prospecting->window != NULL &&
                prospecting->window->getVisible();
            const bool dialogueVisible =
                gui->dialogue != NULL && gui->dialogue->getVisible();
            const bool managementVisible =
                management != NULL && management->getVisible();
            if (prospectingVisible ||
                gui->hasModalMessage() ||
                dialogueVisible ||
                gui->getNumOpenInventoryWindows() != 0 ||
                gui->isStatsWindowOpen() ||
                managementVisible)
            {
                RejectNativeCommand(request, "interface_close_incomplete");
                return;
            }
            AddNativeAcknowledgement(
                request, "completed", "active_interface_closed", true, true);
            g_lastNativeCommand = request.command;
            g_lastNativeCommandResult = "active_interface_closed";
            return;
        }

        if (isPause || isSetSpeed)
        {
            // The clock, from the engine that owns it.
            //
            // These were the last two operations reaching Kenshi through a
            // keystroke, and the keystroke was not a neutral delivery detail:
            // the speed keys select a rate without resuming, so setting gear 2
            // on a paused world needed two presses in a fixed order, and that
            // ordering lived in the controller as a rule about Kenshi rather
            // than as something Kenshi said. `setGameSpeed` takes the
            // multiplier and `userPause` takes the state, so the composite
            // disappears.
            //
            // Completed on the spot rather than monitored: `isPaused` and
            // `getFrameSpeedMultiplier` are the same fields telemetry already
            // publishes, so the terminal is read back here from the engine
            // instead of being inferred from the request having been sent.
            if (ou == NULL)
            {
                RejectNativeCommand(request, "game_world_unavailable");
                return;
            }
            if (isSetSpeed)
            {
                ou->setGameSpeed(
                    static_cast<float>(request.speedMultiplier), false);
                // A speed is a *running* state. Kenshi keeps the rate while
                // paused, so selecting one without resuming would report a
                // gear the world is not moving at.
                ou->userPause(false);
            }
            else
            {
                ou->userPause(request.pauseRequested);
            }
            const bool pausedNow = ou->isPaused();
            const bool reachedPause =
                isPause && pausedNow == request.pauseRequested;
            const bool reachedSpeed = isSetSpeed && !pausedNow;
            if (!reachedPause && !reachedSpeed)
            {
                AddNativeAcknowledgement(
                    request, "cancelled", "time_control_refused", false, true);
                g_lastNativeCommandResult = "time_control_refused";
                return;
            }
            AddNativeAcknowledgement(
                request,
                "completed",
                isPause ? (pausedNow ? "world_paused" : "world_running")
                        : "world_speed_set",
                true,
                true);
            g_lastNativeCommandResult =
                isPause ? (pausedNow ? "world_paused" : "world_running")
                        : "world_speed_set";
            g_lastNativeCommandTargetId.clear();
            return;
        }

        if (isBodyShift)
        {
            // Become another body. Control in Kenshi follows *selection*, not
            // roster membership - proven live: a character released from the
            // active platoon kept taking orders while absent from the squad
            // menu. So entering a body is two things: belong to the player
            // faction, and be the selected primary.
            //
            // `recruit` is the engine's own join path and is used in preference
            // to rewriting the (faction, platoon) coordinate by hand, because
            // it owns whatever bookkeeping that transition needs. The manual
            // rewrite is what the diagnostic probe does, and it is deliberately
            // not what this does.
            Faction* playerFaction = player->getFaction();
            if (playerFaction == NULL)
            {
                RejectNativeCommand(request, "shift_player_faction_unavailable");
                return;
            }

            bool exactIdentityFound = false;
            Character* target = FindExactNearbyCharacter(
                player,
                request.targetId,
                exactIdentityFound);
            if (target == NULL)
            {
                // Shifting between bodies already held is legitimate and does
                // not go through the nearby sphere.
                target = FindExactSquadMember(
                    player,
                    request.targetId,
                    exactIdentityFound);
            }
            if (target == NULL || !target->isValid())
            {
                RejectNativeCommand(request, "shift_target_absent");
                return;
            }
            if (target->isDestroyed())
            {
                RejectNativeCommand(request, "shift_target_dead");
                return;
            }
            if (target->isUnconcious())
            {
                RejectNativeCommand(request, "shift_target_unconscious");
                return;
            }

            Character* observer = player->selectedCharacter.getCharacter();
            if (observer != NULL &&
                observer->isValid() &&
                observer->isEnemy(target, false))
            {
                // Refuse rather than press-gang an enemy: a hostile body is the
                // case most likely to have consequences nobody has measured.
                RejectNativeCommand(request, "shift_target_hostile");
                return;
            }

            const char* joinReason = "shift_body_already_held";
            if (target->getFaction() != playerFaction)
            {
                if (player->recruit(target, false))
                    joinReason = "shift_body_recruited";
                else if (player->recruit(target, true))
                    joinReason = "shift_body_recruited_forced";
                else
                {
                    RejectNativeCommand(request, "shift_recruit_refused");
                    return;
                }
            }

            // The body gets its own squad rather than joining the one already
            // held. Inhabiting a body is not gaining a follower: the bodies
            // left behind stay their own unit, and the squad menu shows who is
            // currently being worn instead of a growing retinue of former
            // hosts.
            ActivePlatoon* ownSquad = player->createSquad();
            if (ownSquad == NULL)
            {
                RejectNativeCommand(request, "shift_squad_unavailable");
                return;
            }
            // Moving containers changes the handle, so the selection has to be
            // carried across it - the engine's own repair, not a tolerated
            // mismatch.
            const hand beforeSquadMove = target->getHandle();
            target->setFaction(playerFaction, ownSquad);
            player->updatePlayerSelection(beforeSquadMove, target->getHandle());
            if (ownSquad->me != NULL)
                player->setCurrentPlatoon(ownSquad->me);

            // Exclusive selection, primary, and camera follow in one call:
            // modifier=false replaces the selection rather than adding to it,
            // which is what "I am now this character" means, and track=true
            // moves the view to the body being entered.
            player->_selectPlayerCharacter(target, false, true);
            if (!player->isObjectSelected(target))
            {
                RejectNativeCommand(request, "shift_selection_refused");
                return;
            }

            AddNativeAcknowledgement(request, "completed", joinReason, true, true);
            g_lastNativeCommandResult = joinReason;
            return;
        }

        if (isBodyShiftProbe)
        {
            // Diagnostic probe for game_sources/research/body_shift/. Kenshi moves
            // a dead player character out of the active roster by rewriting its
            // (faction, platoon) coordinate - same faction, the dead squad - so
            // the corpse stays inspectable while leaving the squad. This asks
            // whether that same rewrite works on someone still using their body.
            //
            // Deliberately the smallest possible question: one player character,
            // both platoons inside the player faction, so nothing about faction
            // relations moves and the platoon coordinate is the only variable.
            // Issuing it twice for the same character is a round trip.
            //
            // The released handle is remembered rather than searched for,
            // because getAllPlayerCharacters() is the active roster - a
            // released body leaves it, which is the whole point and also why it
            // cannot be found again by the usual lookup.
            Faction* playerFaction = player->getFaction();
            RootObjectContainer* activeSquad =
                player->getCurrentActivePlatoon();
            ActivePlatoon* deadSquad = player->getDeadSquad();
            if (playerFaction == NULL || activeSquad == NULL || deadSquad == NULL)
            {
                RejectNativeCommand(request, "shift_platoons_unavailable");
                return;
            }

            Character* released = g_shiftProbeReleased.getCharacter();
            if (released != NULL &&
                released->isValid() &&
                StableEntityId(released) == request.targetId)
            {
                // Moving containers gives the body a new handle, so tell the
                // engine to carry the selection across rather than leaving a
                // selection entry pointing at the old address.
                const hand beforeSeize = released->getHandle();
                released->setFaction(
                    playerFaction,
                    static_cast<ActivePlatoon*>(activeSquad));
                player->updatePlayerSelection(beforeSeize, released->getHandle());
                // Then make it selected outright. Control follows selection, so
                // this is the step that actually hands the body over.
                if (!player->isObjectSelected(released))
                    player->objectSelected(released, true);
                g_shiftProbeReleased = hand();
                AddNativeAcknowledgement(
                    request,
                    "completed",
                    "shift_seized_into_active_squad",
                    true,
                    true);
                g_lastNativeCommandResult = "shift_seized_into_active_squad";
                return;
            }

            bool exactIdentityFound = false;
            Character* subject = FindExactSquadMember(
                player,
                request.targetId,
                exactIdentityFound);
            if (subject == NULL || !subject->isValid())
            {
                RejectNativeCommand(request, "shift_subject_absent");
                return;
            }
            g_shiftProbeReleased = subject->getHandle();
            // Releasing is the same coordinate rewrite, and the selection has to
            // be carried across it the same way. `updatePlayerSelection` is the
            // engine's own repair for a handle that moved; three workarounds
            // were written here first - a tolerant identity compare,
            // `RootObject::unselect()`, and `unselectAll()` with a hand-rebuilt
            // selection - before this was found one grep away in
            // PlayerInterface.h.
            const hand beforeRelease = subject->getHandle();
            subject->setFaction(playerFaction, deadSquad);
            player->updatePlayerSelection(beforeRelease, subject->getHandle());
            AddNativeAcknowledgement(
                request,
                "completed",
                "shift_released_from_active_squad",
                true,
                true);
            g_lastNativeCommandResult = "shift_released_from_active_squad";
            return;
        }

        std::string selectedId;
        hand selectedHandle;
        std::vector<hand> selectedHandles;
        const char* submitSelectionReason = ResolveExactSelectionBasis(
            player,
            request.selectedCharacterIds,
            selectedId,
            selectedHandle,
            selectedHandles);
        if (submitSelectionReason != SELECTION_BASIS_OK)
        {
            RejectNativeCommand(request, submitSelectionReason);
            return;
        }

        if (isSquadSelection)
        {
            bool exactIdentityFound = false;
            Character* target = FindExactSquadMember(
                player,
                request.targetId,
                exactIdentityFound);
            if (target == NULL)
            {
                RejectNativeCommand(request, "target_lifetime_changed");
                return;
            }
            if (request.selectedCharacterIds.size() != 1 ||
                selectedId != request.targetId)
            {
                player->_selectPlayerCharacter(target, false, false);
                std::string resultingId;
                hand resultingHandle;
                if (!TryGetExactSelection(player, resultingId, resultingHandle) ||
                    resultingId != request.targetId)
                {
                    Character* original = selectedHandle.getCharacter();
                    if (original != NULL && original->isValid())
                        player->_selectPlayerCharacter(original, false, false);
                    RejectNativeCommand(request, "selection_not_changed");
                    return;
                }
            }
            AddNativeAcknowledgement(
                request,
                "completed",
                "exact_squad_member_selected",
                true,
                true);
            g_lastNativeCommandResult = "exact_squad_member_selected";
            g_lastNativeCommandTarget = target->getName();
            g_lastNativeCommandTargetId = request.targetId;
            return;
        }

        if (isResourceSurvey)
        {
            // A survey reads Kenshi's resource field where the character
            // stands. It issues no order and changes no world state, so it
            // completes as soon as the reading exists - there is nothing to
            // monitor and nothing that could later be cancelled.
            //
            // It is a command rather than ambient telemetry so the knowledge
            // stays earned: the agent learns what it surveyed, where it
            // surveyed, not what exists everywhere.
            Character* surveyor = selectedHandle.getCharacter();
            if (surveyor == NULL || !surveyor->isValid())
            {
                RejectNativeCommand(request, "selection_not_available");
                return;
            }
            if (!BeginProspectSurvey(surveyor, request.commandId))
            {
                RejectNativeCommand(request, "resource_field_unavailable");
                return;
            }
            AddNativeAcknowledgement(
                request,
                "accepted",
                "resource_survey_captured",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId.clear();
            g_activeNativeCommand.destinationId.clear();
            g_activeNativeCommand.selectedCharacterId = selectedId;
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.isSquadRegroup = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isTradeWindowPending = false;
            g_activeNativeCommand.isProspectingSurveyPending = true;
            g_activeNativeCommand.prospectingResultObserved = false;
            g_activeNativeCommand.prospectingPlaybackOwned =
                ou != NULL && ou->isPaused();
            g_activeNativeCommand.prospectingStartedTick = GetTickCount();
            g_activeNativeCommand.prospectingHiddenSinceTick = 0;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.resourceTaskIssuedByCommand = false;
            g_activeNativeCommand.resourceTaskReleaseRequested = false;
            KenshiAgentTelemetry::ResetResourceTaskReleaseConfirmationWindow(
                g_activeNativeCommand.resourceTaskReleaseWindow);
            g_activeNativeCommand.minimumOutputQuantity = 1;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            g_activeNativeCommand.originX = 0.0f;
            g_activeNativeCommand.originZ = 0.0f;
            if (g_activeNativeCommand.prospectingPlaybackOwned)
            {
                // The normal prospect action is a timed world operation, not
                // an instant GUI read. Advance it natively at 1x, then let the
                // terminal path above restore the previously paused state.
                ou->setGameSpeed(1.0f, false);
                ou->userPause(false);
            }
            g_lastNativeCommandResult = "resource_survey_captured";
            return;
        }

        if (isCharacterOrder)
        {
            TaskType orderedTask = NULL_TASK;
            if (!ResolveAdvertisedTaskName(request.contextAction, orderedTask))
            {
                RejectNativeCommand(request, "context_action_unavailable");
                return;
            }
            bool exactIdentityFound = false;
            // Deliberately the nearby-character finder, not the dialogue-target
            // one: an order is not a conversation. Hostiles, the unconscious,
            // and the dead are all legitimately orderable-at, and which of them
            // afford which order is the next check's business, not this one's.
            Character* target = FindExactNearbyCharacter(
                player,
                request.targetId,
                exactIdentityFound);
            if (target == NULL)
            {
                RejectNativeCommand(
                    request,
                    exactIdentityFound
                        ? "target_role_invalid"
                        : "target_lifetime_changed");
                return;
            }
            // Kenshi's context menu is the authority on whether this order
            // applies to this target, and it is asked again here rather than
            // trusting the offer. Publication and dispatch deliberately consult
            // the same game-owned answer.
            std::vector<KenshiAgentTelemetry::AdvertisedTask> dispatchTasks;
            const bool menuAsked =
                ProbeMenuOrders(player, target, dispatchTasks);
            const bool stillAdvertised =
                HasAdvertisedTask(dispatchTasks, orderedTask);
            if (!stillAdvertised)
            {
                // A probe that could not run is not a denial. Saying so keeps
                // "Kenshi withdrew this order" apart from "nobody could ask",
                // which are different bugs with the same symptom.
                RejectNativeCommand(
                    request,
                    menuAsked
                        ? "context_action_unavailable"
                        : "order_probe_unavailable");
                return;
            }
            Character* orderingActor = selectedHandle.getCharacter();
            const hand& targetHandle = target->getHandle();
            const bool exactTaskAlreadyActive =
                HasExactContextGoal(
                    orderingActor,
                    orderedTask,
                    targetHandle);
            if (!exactTaskAlreadyActive)
            {
                player->newPlayerTaskSelectedCharacters(
                    orderedTask,
                    targetHandle,
                    target->isIndoors().getBuilding(),
                    target->getPosition(),
                    false);
            }
            AddNativeAcknowledgement(
                request,
                "accepted",
                exactTaskAlreadyActive
                    ? "adopted_existing_task"
                    : "issued",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.selectedCharacterId = selectedId;
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.targetHandle = targetHandle;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = true;
            g_activeNativeCommand.isTradeWindowPending = false;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_NEARBY_CHARACTER;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.minimumOutputQuantity = 1;
            g_activeNativeCommand.expectedTask = orderedTask;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = 0.0f;
            g_activeNativeCommand.originZ = 0.0f;
            if (ou != NULL && ou->isPaused())
            {
                // Context goals such as PLAYER_TALK_TO need world time before
                // HasExactContextGoal can observe adoption. This command owns
                // that resume directly; making the Python monitor race a
                // speed key against the next native update leaked desktop
                // input into an otherwise native operation.
                ou->setGameSpeed(1.0f, false);
                ou->userPause(false);
            }
            g_lastNativeCommandResult =
                exactTaskAlreadyActive
                    ? "adopted_existing_task"
                    : "issued";
            return;
        }

        if (isContextAction && request.contextAction == "first_aid")
        {
            bool exactIdentityFound = false;
            Character* target = FindExactSquadMember(
                player,
                request.targetId,
                exactIdentityFound);
            if (target == NULL)
            {
                RejectNativeCommand(
                    request,
                    exactIdentityFound
                        ? "target_role_invalid"
                        : "target_lifetime_changed");
                return;
            }
            std::vector<KenshiAgentTelemetry::AdvertisedTask> advertised;
            const bool menuAsked =
                ProbeMenuOrders(player, target, advertised);
            if (!HasAdvertisedTask(advertised, FIRST_AID_ORDER))
            {
                RejectNativeCommand(
                    request,
                    menuAsked
                        ? "context_action_unavailable"
                        : "order_probe_unavailable");
                return;
            }
            Character* selected = selectedHandle.getCharacter();
            const hand& targetHandle = target->getHandle();
            const bool exactTaskAlreadyActive =
                HasExactContextGoal(
                    selected,
                    FIRST_AID_ORDER,
                    targetHandle);
            if (!exactTaskAlreadyActive)
            {
                player->newPlayerTaskSelectedCharacters(
                    FIRST_AID_ORDER,
                    targetHandle,
                    target->isIndoors().getBuilding(),
                    target->getPosition(),
                    false);
            }
            AddNativeAcknowledgement(
                request,
                "accepted",
                exactTaskAlreadyActive
                    ? "adopted_existing_task"
                    : "issued",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.selectedCharacterId =
                selectedId;
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.targetHandle = targetHandle;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = true;
            g_activeNativeCommand.isTradeWindowPending = false;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_SQUAD_CHARACTER;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.minimumOutputQuantity = 1;
            g_activeNativeCommand.expectedTask = FIRST_AID_ORDER;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = 0.0f;
            g_activeNativeCommand.originZ = 0.0f;
            g_lastNativeCommandResult =
                exactTaskAlreadyActive
                    ? "adopted_existing_task"
                    : "issued";
            g_lastNativeCommandTarget = target->getName();
            g_lastNativeCommandTargetId = request.targetId;
            return;
        }

        if (isTradeWindow)
        {
            // Both sides at once, which is the state a transfer needs and the
            // one `showInventory` cannot produce.
            if (gui == NULL || gui->inDialogue() || gui->hasModalMessage())
            {
                RejectNativeCommand(request, "conflicting_modal_open");
                return;
            }
            TradeWindowType windowType = TW_AUTO;
            if (!ResolveTradeWindowType(request.contextAction, windowType))
            {
                RejectNativeCommand(request, "unsupported_trade_window_type");
                return;
            }
            hand firstHandle;
            hand secondHandle;
            if (!FindExactOwnerHandle(player, request.targetId, firstHandle) ||
                !FindExactOwnerHandle(
                    player, request.destinationId, secondHandle))
            {
                RejectNativeCommand(request, "target_lifetime_changed");
                return;
            }
            // Asked for, not opened here. `showTradeWindow` records the pair
            // and the GUI opens both windows on a later update, so checking the
            // count one instruction later reported `trade_window_not_opened`
            // about a pairing that did in fact appear -- telemetry showed both
            // windows moments after the refusal.
            gui->showTradeWindow(firstHandle, secondHandle, windowType);
            AddNativeAcknowledgement(
                request, "accepted", "trade_window_requested", true, false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.destinationId = request.destinationId;
            g_activeNativeCommand.selectedCharacterId = selectedId;
            g_activeNativeCommand.selectedCharacterIds = request.selectedCharacterIds;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.isTradeWindowPending = true;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            g_lastNativeCommandResult = "trade_window_requested";
            g_lastNativeCommandTargetId = request.targetId;
            return;
        }

        if (isTransfer)
        {
            // One item, one slot, between two open inventories -- whatever owns
            // them. This is the whole of looting, buying, selling, giving and
            // harvesting: five operations in this project each simulated it
            // with a mouse, and `harvest_resource` spent twelve pointer actions
            // doing so.
            //
            // The inventory model moves it. Capacity comes from the destination
            // inventory; shop affordability and payment follow the deliberately
            // simplified rule below. This path does not claim the richer trade
            // and theft adjudication carried by Kenshi's mouse handler.
            if (gui == NULL || gui->inDialogue() || gui->hasModalMessage())
            {
                RejectNativeCommand(request, "conflicting_modal_open");
                return;
            }
            // Resolved out of the window map by advertised id, never by a
            // rebuilt handle. Both sides have to be open, which is why opening
            // one no longer refuses because another already is -- and each side
            // is named separately, because "one of the two was missing" sent me
            // looking at range and window lifetime for a run that had both
            // windows sitting open the whole time.
            InventoryGUI* source =
                FindOpenInventoryWindow(gui, request.targetId);
            if (source == NULL)
            {
                RejectNativeCommand(request, "source_inventory_not_open");
                return;
            }
            InventoryGUI* destination =
                FindOpenInventoryWindow(gui, request.destinationId);
            if (destination == NULL)
            {
                RejectNativeCommand(request, "destination_inventory_not_open");
                return;
            }
            if (source == destination)
            {
                RejectNativeCommand(request, "same_inventory");
                return;
            }
            // Both non-NULL by construction: `FindOpenInventoryWindow` skips
            // windows without an inventory, so a window it returns has one.
            Inventory* sourceInventory = source->getInventory();
            Inventory* destinationInventory = destination->getInventory();
            InventorySection* section =
                sourceInventory->getSection(request.sectionName);
            if (section == NULL)
            {
                RejectNativeCommand(request, "section_absent");
                return;
            }
            // Equipped sections are transferable, and refusing them made
            // looting nearly useless: a body's whole estate is what it is
            // wearing. Measured live -- an unconscious character offered a
            // Katana and a pair of ragged Halfpants, both worn, and nothing
            // else at all.
            //
            // The refusal was inherited from a misdiagnosis. It was added
            // because `RClickAutoTrade` crashed on an equipped item, and that
            // crash turned out to be the return-convention mismatch that
            // crashed it on *every* item regardless of section. Nothing calls
            // that function now; transfers go through the inventory model,
            // which unequips as part of removing.
            Item* item = section->getItemAt(request.slotX, request.slotY);
            if (item == NULL || !item->isValid())
            {
                RejectNativeCommand(request, "slot_empty");
                return;
            }
            // Conservation, measured across the call rather than inferred from
            // its verdict. An engine that answers OK has said the transfer was
            // permitted, not that an item moved, and those are separable -- the
            // operation this replaces already knew that a click receipt is
            // never enough.
            // Counted before anything moves, for the price and the proof.
            GameData* const pricedGameData = item->getGameData();
            const int destinationHeldBefore =
                pricedGameData != NULL
                    ? destinationInventory->getNumItems(pricedGameData)
                    : 0;
            const unsigned int destinationBefore =
                static_cast<unsigned int>(
                    destinationInventory->getAllItems().size());
            const int sourceMoneyBefore = sourceInventory->getMoney();

            // Moved through the inventory, not through the mouse handler.
            //
            // `InventoryGUI::RClickAutoTrade` crashed Kenshi on every call --
            // three live runs, faulting inside itself reading -1. It crashed on
            // an equipped item, on a shopkeeper's stock, and finally on a plain
            // give of one carried item between two player characters standing
            // together, with no money and no shop involved. State was never the
            // variable; the call was. It is a protected member of the *GUI*
            // class, reached normally from `sectionMouseButtonPressed`, and it
            // consults the window's live mouse state (`hasMouse`,
            // `getSlotWithMouse`, `getMouseItem`). Invoked out of band with the
            // pointer nowhere near the window, that state reads as -1.
            //
            // `Inventory` is the authority on items; `InventoryGUI` renders
            // them and handles clicks on them. So the move happens on the
            // model: `removeItemDontDestroy_returnsItem` off the source and
            // `addItem` onto the destination, both public, both virtual, both
            // dispatched through Kenshi's own vtable.
            //
            // A model-level move carries none of Kenshi's adjudication, so
            // running one against a shopkeeper's stock would take the goods
            // rather than buy them. `getNPCTrader` is Kenshi's own answer to
            // "is a shop trade open", and it routes those to the adjudicator
            // below instead.
            // Money moves with the item, priced from the values Kenshi puts on
            // it, because the engine's own adjudicator turned out not to be
            // safely callable.
            //
            // `RClickAutoTrade` is what Kenshi uses, and it carries price,
            // markup, faction standing and theft rules with it. Its declared
            // signature does not match the shipped binary: the prologue takes
            // (this, ptr, ptr, int, int, ptr) while the header says
            // (const std::string&, int, int, InventoryGUI*, bool, bool).
            // Handing it a slot index where it wanted a pointer made it read
            // 15 as a `std::string` -- the fault address was 0x27, exactly
            // 0x0F + 0x18, the offset of the string's length field. Calling it
            // correctly means inferring the rest of a parameter list from
            // disassembly, at a crash per wrong guess.
            //
            // So the price is computed here instead, from `getValueSingle`,
            // which is the same pair of numbers the item shows in its own
            // tooltip and that telemetry already exports: base value to buy,
            // sell value to sell. Whoever receives the item pays. That is a
            // simpler economics than Kenshi's -- no haggling, no stolen-goods
            // penalty -- and it is ours to keep honest, which is stated plainly
            // rather than left to be discovered.
            const bool shopTradeOpen = InventoryGUI::getNPCTrader() != NULL;
            // How many, chosen by the agent rather than implied by the stack.
            // Zero means the whole stack, which is what this always silently
            // did -- one buy took every Dried Fish the Barman had, and nothing
            // in the request said so.
            const int available = item->quantity > 0 ? item->quantity : 1;
            const int requested =
                request.quantity > 0 ? request.quantity : available;
            const int transferQuantity =
                requested < available ? requested : available;
            int price = 0;
            int unitCost = 0;
            Inventory* payer = NULL;
            Inventory* payee = NULL;
            if (shopTradeOpen)
            {
                Character* destinationOwner = destination->getCallbackCharacter();
                const bool destinationIsPlayer =
                    destinationOwner != NULL &&
                    destinationOwner->isValid() &&
                    destinationOwner->isPlayerCharacter();
                // Buying is the player receiving; selling is the player giving.
                // Kenshi asks more to buy than it pays to sell, and those are
                // two different numbers on the same item.
                const int unitPrice =
                    item->getValueSingle(!destinationIsPlayer);
                unitCost = unitPrice;
                payer = destinationInventory;
                payee = sourceInventory;
                // Affordability is checked against the most this can cost; the
                // charge itself is levied on the count that actually moved.
                price = unitPrice * transferQuantity;
                if (price > 0 && payer->getMoney() < price)
                {
                    RejectNativeCommand(request, "cant_afford");
                    return;
                }
            }
            const int movedQuantity = transferQuantity;
            if (!destinationInventory->hasRoomForItem(item->getGameData()))
            {
                RejectNativeCommand(request, "no_room");
                return;
            }
            Item* removed =
                sourceInventory->removeItemDontDestroy_returnsItem(
                    item, movedQuantity, false);
            if (removed == NULL)
            {
                RejectNativeCommand(request, "source_would_not_release_item");
                return;
            }
            // `tryAddItem` rather than `addItem`: the four-argument form takes
            // `dropOnFail` and `destroyOnFail`, and neither is an acceptable
            // outcome for a refused transfer -- one puts the item on the floor
            // and the other deletes it.
            if (!destinationInventory->tryAddItem(removed, movedQuantity))
            {
                // Put it back rather than leaving it nowhere. An item removed
                // from one inventory and refused by the other is destroyed by
                // silence.
                sourceInventory->tryAddItem(removed, movedQuantity);

                // Then check, because "refused" was a lie. Measured live: two
                // `destination_refused_item` rejections left two Building
                // Material and one Iron Plates sitting in the buyer's pack with
                // no money charged, because `tryAddItem` can place part of a
                // stack, still answer false, and leave the rollback unable to
                // reclaim what it already placed. Reporting a rejection while
                // goods moved is a silent transfer of someone else's property.
                //
                // What is true is whatever the destination now holds. If it
                // gained anything the move partly happened, so it is charged
                // for and reported as partial rather than denied.
                const int strandedGain =
                    pricedGameData != NULL
                        ? destinationInventory->getNumItems(pricedGameData) -
                              destinationHeldBefore
                        : 0;
                if (strandedGain <= 0)
                {
                    RejectNativeCommand(request, "destination_refused_item");
                    return;
                }
                if (shopTradeOpen && unitCost > 0 && payer != NULL && payee != NULL)
                {
                    const int strandedCharge = unitCost * strandedGain;
                    payer->takeMoney(strandedCharge);
                    payee->takeMoney(-strandedCharge);
                }
                AddNativeAcknowledgement(
                    request, "completed", "item_partly_transferred", true, true);
                g_lastNativeCommandResult = "item_partly_transferred";
                g_lastNativeCommandTargetId = request.targetId;
                return;
            }
            const unsigned int destinationAfter =
                static_cast<unsigned int>(
                    destinationInventory->getAllItems().size());
            const bool destinationGained = destinationAfter > destinationBefore;
            const bool sourceSlotReleased =
                section->getItemAt(request.slotX, request.slotY) != item;

            g_lastNativeCommandTargetId = request.targetId;
            if (!destinationGained && !sourceSlotReleased)
            {
                // Both sides say nothing happened. Reported as its own
                // condition rather than as success, because a transfer that
                // silently does nothing is the failure the conservation check
                // exists to catch.
                AddNativeAcknowledgement(
                    request, "cancelled", "moved_without_moving", false, true);
                g_lastNativeCommandResult = "moved_without_moving";
                return;
            }
            (void)sourceMoneyBefore;
            if (shopTradeOpen && unitCost > 0 && payer != NULL && payee != NULL)
            {
                // Priced on what the destination actually gained, counted by
                // the engine on both sides of the move.
                //
                // Measured twice live: a stack the shop displayed as 2 moved 3
                // items, and the charge was 2. The source cannot be trusted for
                // this -- a shopkeeper's inventory is a `ShopTraderInventory`,
                // an aggregated view over several backing inventories, so its
                // displayed stack under-reports what a removal will yield.
                // Counting the source's loss reproduces the same lie. The
                // destination is a plain `Inventory`, and `getNumItems` on it
                // is the engine's own answer to how many arrived.
                const int actuallyMoved =
                    pricedGameData != NULL
                        ? destinationInventory->getNumItems(pricedGameData) -
                              destinationHeldBefore
                        : 0;
                const int charge =
                    actuallyMoved > 0 ? unitCost * actuallyMoved : 0;
                if (charge > 0)
                {
                    payer->takeMoney(charge);
                    payee->takeMoney(-charge);
                }
            }
            AddNativeAcknowledgement(
                request, "completed", "item_transferred", true, true);
            g_lastNativeCommandResult = "item_transferred";
            return;
        }


        if (isContextAction || isResourceProduction)
        {
            if (isContextAction && request.contextAction != "operate")
            {
                RejectNativeCommand(request, "unsupported_context_action");
                return;
            }
            Building* target = FindExactNaturalResource(
                player,
                request.targetId);
            if (target == NULL)
            {
                RejectNativeCommand(
                    request,
                    "target_lifetime_changed");
                return;
            }
            const NaturalResourceAssessment resource =
                InspectNaturalResource(target);
            if (!resource.structurallyRecognized)
            {
                RejectNativeCommand(request, "target_role_invalid");
                return;
            }
            const hand& targetHandle = target->getHandle();
            g_lastNativeCommandTarget = target->getName();
            g_lastNativeCommandTargetId = request.targetId;

            const bool acceptedOperatorAlreadyActive =
                HasSelectedResourceOperator(
                    target,
                    request.selectedCharacterIds);
            if (isResourceProduction)
            {
                int outputQuantity = 0;
                if (!TryGetInventorySectionQuantity(
                        target,
                        "out",
                        outputQuantity))
                {
                    RejectNativeCommand(
                        request,
                        "resource_output_unknown");
                    return;
                }
                if (outputQuantity >=
                    static_cast<int>(request.minimumOutputQuantity))
                {
                    AddNativeAcknowledgement(
                        request,
                        "completed",
                        "resource_output_ready",
                        true,
                        true);
                    g_lastNativeCommandResult =
                        "resource_output_ready";
                    return;
                }
            }
            Building* destinationIndoors =
                target->isIndoors().getBuilding();
            if (!acceptedOperatorAlreadyActive)
            {
                player->newPlayerTaskSelectedCharacters(
                    OPERATE_MACHINERY,
                    targetHandle,
                    destinationIndoors,
                    target->getPosition(),
                    false);
            }
            AddNativeAcknowledgement(
                request,
                "accepted",
                acceptedOperatorAlreadyActive
                    ? "adopted_existing_operator"
                    : "issued",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            // The whole authorized party, not just its primary. Submit accepts a
            // multi-character basis, but this path recorded only the primary and
            // left the id list cleared, so the monitor read size 0, took the
            // singleton branch, and cancelled every order the moment a second
            // character was selected - reported as `selection_mismatch` with no
            // way to tell that from a genuinely changed selection. Every other
            // submit path already carried the list; this one is where a
            // two-character party's mining order died.
            g_activeNativeCommand.selectedCharacterId = selectedId;
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.targetHandle = targetHandle;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = false;
            g_activeNativeCommand.hasFixedDestination = false;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = true;
            g_activeNativeCommand.isTradeWindowPending = false;
            g_activeNativeCommand.targetKind = NATIVE_TARGET_BUILDING;
            g_activeNativeCommand.isResourceProduction =
                isResourceProduction;
            g_activeNativeCommand.resourceTaskObserved =
                acceptedOperatorAlreadyActive;
            g_activeNativeCommand.resourceTaskIssuedByCommand =
                isResourceProduction && !acceptedOperatorAlreadyActive;
            g_activeNativeCommand.resourceTaskReleaseRequested = false;
            KenshiAgentTelemetry::ResetResourceTaskReleaseConfirmationWindow(
                g_activeNativeCommand.resourceTaskReleaseWindow);
            g_activeNativeCommand.minimumOutputQuantity =
                request.minimumOutputQuantity;
            g_activeNativeCommand.expectedTask = OPERATE_MACHINERY;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = 0.0f;
            g_activeNativeCommand.originZ = 0.0f;
            g_lastNativeCommandResult =
                acceptedOperatorAlreadyActive
                    ? "adopted_existing_operator"
                    : "issued";
            return;
        }

        if (isMapTravel)
        {
            bool exactIdentityFound = false;
            TownBase* target = FindExactKnownMapDestination(
                request.targetId,
                exactIdentityFound);
            if (target == NULL)
            {
                RejectNativeCommand(
                    request,
                    exactIdentityFound
                        ? "target_role_invalid"
                        : "target_lifetime_changed");
                return;
            }
            Character* walker = selectedHandle.getCharacter();
            if (walker == NULL || !walker->isValid())
            {
                RejectNativeCommand(request, "selection_mismatch");
                return;
            }
            bool allSelectedPresentlyReached = true;
            for (unsigned int index = 0;
                 index < selectedHandles.size();
                 ++index)
            {
                Character* member = selectedHandles[index].getCharacter();
                if (member == NULL || !member->isValid())
                {
                    RejectNativeCommand(request, "selection_mismatch");
                    return;
                }
                TownBase* currentTown = member->getCurrentTownLocation();
                const bool currentTownIdentityMatches =
                    currentTown != NULL &&
                    currentTown->isValid() &&
                    StableEntityId(currentTown->getHandle()) ==
                        request.targetId;
                if (!KenshiAgentTelemetry::
                        IsNativeMapDestinationPresentlyReached(
                            currentTownIdentityMatches,
                            target->hasGates(),
                            member->amInsideTownWalls() != 0))
                {
                    allSelectedPresentlyReached = false;
                }
            }
            if (allSelectedPresentlyReached)
            {
                RejectNativeCommand(
                    request,
                    "target_already_reached");
                return;
            }

            const Ogre::Vector3 origin = walker->getPosition();
            const Ogre::Vector3 waypoint =
                target->getPositionForWaypoint(origin);
            Ogre::Vector3 destination = origin;
            destination.x = waypoint.x;
            destination.y = waypoint.y;
            destination.z = waypoint.z;
            player->newPlayerTaskSelectedCharacters(
                MOVE_CUS_ORDERED,
                hand(),
                NULL,
                destination,
                false);
            AddNativeAcknowledgement(
                request,
                "accepted",
                "issued",
                true,
                false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.selectedCharacterId =
                selectedId;
            g_activeNativeCommand.selectedCharacterIds =
                request.selectedCharacterIds;
            g_activeNativeCommand.targetHandle = target->getHandle();
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = true;
            g_activeNativeCommand.hasFixedDestination = true;
            g_activeNativeCommand.isMapTravel = true;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.minimumOutputQuantity = 1;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = origin.x;
            g_activeNativeCommand.originZ = origin.z;
            g_activeNativeCommand.destinationX = destination.x;
            g_activeNativeCommand.destinationZ = destination.z;
            g_lastNativeCommandResult = "issued";
            g_lastNativeCommandTarget = target->getKnownName();
            g_lastNativeCommandTargetId = request.targetId;
            return;
        }

        if (isDirection)
        {
            if (!(request.distanceUnits > 0.0) || request.distanceUnits > 2000.0)
            {
                RejectNativeCommand(request, "distance_out_of_range");
                return;
            }
            Character* walker = player->selectedCharacter.getCharacter();
            if (walker == NULL || !walker->isValid())
            {
                RejectNativeCommand(request, "selection_mismatch");
                return;
            }
            // Kenshi's world is x/z with y up, and bearing is measured
            // clockwise from +z so that 0 is "north" in the way the map reads.
            const double radians = request.bearingDegrees * 3.14159265358979323846 / 180.0;
            // Copied and offset rather than constructed: Ogre's three-float
            // Vector3 constructor is dllimport and is not in the libraries this
            // plug-in links against.
            const Ogre::Vector3 origin = walker->getPosition();
            Ogre::Vector3 destination = origin;
            destination.x += static_cast<float>(sin(radians) * request.distanceUnits);
            destination.z += static_cast<float>(cos(radians) * request.distanceUnits);
            // No target handle and no destination building: this is a walk to a
            // bare point, the same order a player gives by right-clicking the
            // ground, and it is available wherever the character is standing.
            player->newPlayerTaskSelectedCharacters(
                MOVE_CUS_ORDERED,
                hand(),
                NULL,
                destination,
                false);
            AddNativeAcknowledgement(request, "accepted", "issued", true, false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId = request.targetId;
            g_activeNativeCommand.selectedCharacterId = request.selectedCharacterId;
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = true;
            g_activeNativeCommand.hasFixedDestination = true;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = false;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = origin.x;
            g_activeNativeCommand.originZ = origin.z;
            g_activeNativeCommand.destinationX = destination.x;
            g_activeNativeCommand.destinationZ = destination.z;
            g_lastNativeCommandResult = "issued";
            g_lastNativeCommandTarget.clear();
            g_lastNativeCommandTargetId.clear();
            return;
        }

        if (isBuildingExit)
        {
            Character* walker = player->selectedCharacter.getCharacter();
            if (walker == NULL || !walker->isValid())
            {
                RejectNativeCommand(request, "selection_mismatch");
                return;
            }
            const hand& buildingHandle = walker->isIndoors();
            Building* building = buildingHandle.getBuilding();
            if (!KenshiAgentTelemetry::HasResolvedIndoorBuilding(
                    buildingHandle.isValid(),
                    building != NULL,
                    building != NULL && building->isValid()))
            {
                RejectNativeCommand(request, "not_indoors");
                return;
            }

            const Ogre::Vector3 origin = walker->getPosition();
            Ogre::Vector3 destination = origin;
            bool foundDoor = false;
            float bestDistanceSquared = 0.0f;
            for (lektor<Building*>::iterator doorIt = building->doors.begin();
                 doorIt != building->doors.end();
                 ++doorIt)
            {
                Building* doorBuilding = *doorIt;
                if (doorBuilding == NULL || !doorBuilding->isValid())
                    continue;
                DoorStuff* door = doorBuilding->getDoor();
                if (door == NULL ||
                    !door->isValid() ||
                    door->isDisabled() ||
                    door->isLocked())
                {
                    continue;
                }
                const Ogre::Vector3 candidate =
                    door->getDoorPosOutside_extraFarOut(1.0f);
                const float dx = candidate.x - origin.x;
                const float dz = candidate.z - origin.z;
                const float distanceSquared = dx * dx + dz * dz;
                if (!foundDoor || distanceSquared < bestDistanceSquared)
                {
                    foundDoor = true;
                    bestDistanceSquared = distanceSquared;
                    // Component copies avoid Ogre's imported Vector3
                    // assignment operator, which the KenshiLib import library
                    // does not provide to this plug-in.
                    destination.x = candidate.x;
                    destination.y = candidate.y;
                    destination.z = candidate.z;
                }
            }
            if (!foundDoor)
            {
                RejectNativeCommand(request, "no_usable_exit");
                return;
            }

            player->newPlayerTaskSelectedCharacters(
                MOVE_CUS_ORDERED,
                hand(),
                NULL,
                destination,
                false);
            AddNativeAcknowledgement(request, "accepted", "issued", true, false);
            g_activeNativeCommand.active = true;
            g_activeNativeCommand.commandId = request.commandId;
            g_activeNativeCommand.targetId.clear();
            g_activeNativeCommand.destinationId.clear();
            g_activeNativeCommand.selectedCharacterId =
                request.selectedCharacterId;
            g_activeNativeCommand.targetHandle = hand();
            g_activeNativeCommand.selectedHandle = selectedHandle;
            g_activeNativeCommand.isWalk = true;
            g_activeNativeCommand.hasFixedDestination = true;
            g_activeNativeCommand.isMapTravel = false;
            g_activeNativeCommand.mapInteriorOrderIssued = false;
            g_activeNativeCommand.isBuildingExit = true;
            g_activeNativeCommand.isContextAction = false;
            g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
            g_activeNativeCommand.isResourceProduction = false;
            g_activeNativeCommand.resourceTaskObserved = false;
            g_activeNativeCommand.expectedTask = NULL_TASK;
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
            KenshiAgentTelemetry::ResetNativeMovementStallWindow(
                g_activeNativeCommand.stallWindow);
            KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
                g_activeNativeCommand.outdoorWindow);
            g_activeNativeCommand.originX = origin.x;
            g_activeNativeCommand.originZ = origin.z;
            g_activeNativeCommand.destinationX = destination.x;
            g_activeNativeCommand.destinationZ = destination.z;
            g_lastNativeCommandResult = "issued";
            g_lastNativeCommandTarget.clear();
            g_lastNativeCommandTargetId.clear();
            return;
        }

        bool exactIdentityFound = false;
        // Moving somewhere does not require the destination be talkable; only
        // that it is exactly the character the caller named, still present.
        Character* target = isSquadRegroup
            ? FindExactSquadMember(player, request.targetId, exactIdentityFound)
            : (isMove
                ? FindExactNearbyCharacter(
                    player,
                    request.targetId,
                    exactIdentityFound)
                : FindExactDialogueTarget(
                    player,
                    request.targetId,
                    exactIdentityFound));
        if (target == NULL)
        {
            RejectNativeCommand(
                request,
                exactIdentityFound
                    ? "target_role_invalid"
                    : "target_lifetime_changed");
            return;
        }
        if (isSquadRegroup && StableEntityId(target) == selectedId)
        {
            RejectNativeCommand(request, "target_role_invalid");
            return;
        }

        const hand& targetHandle = target->getHandle();
        Building* destinationIndoors = target->isIndoors().getBuilding();
        // The same order a player issues by right-clicking: walk there, and
        // enter the destination's building if it is inside one. Unlike
        // PLAYER_TALK_TO it opens no conversation on arrival, which is what
        // makes it usable for going somewhere rather than talking to someone.
        player->newPlayerTaskSelectedCharacters(
            (isMove || isSquadRegroup) ? MOVE_CUS_ORDERED : PLAYER_TALK_TO,
            targetHandle,
            destinationIndoors,
            target->getPosition(),
            false);

        AddNativeAcknowledgement(
            request,
            "accepted",
            "issued",
            true,
            false);
        g_activeNativeCommand.active = true;
        g_activeNativeCommand.commandId = request.commandId;
        g_activeNativeCommand.targetId = request.targetId;
        g_activeNativeCommand.selectedCharacterId =
            selectedId;
        g_activeNativeCommand.selectedCharacterIds =
            request.selectedCharacterIds;
        g_activeNativeCommand.targetHandle = targetHandle;
        g_activeNativeCommand.selectedHandle = selectedHandle;
        g_activeNativeCommand.isWalk = isMove || isSquadRegroup;
        g_activeNativeCommand.hasFixedDestination = false;
        g_activeNativeCommand.isMapTravel = false;
        g_activeNativeCommand.isSquadRegroup = isSquadRegroup;
        g_activeNativeCommand.mapInteriorOrderIssued = false;
        g_activeNativeCommand.isBuildingExit = false;
        g_activeNativeCommand.isContextAction = false;
        g_activeNativeCommand.isTradeWindowPending = false;
        g_activeNativeCommand.targetKind = NATIVE_TARGET_NONE;
        g_activeNativeCommand.isResourceProduction = false;
        g_activeNativeCommand.resourceTaskObserved = false;
        g_activeNativeCommand.expectedTask = NULL_TASK;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
        KenshiAgentTelemetry::ResetNativeMovementStallWindow(
            g_activeNativeCommand.stallWindow);
        KenshiAgentTelemetry::ResetNativeOutdoorConfirmationWindow(
            g_activeNativeCommand.outdoorWindow);
        g_activeNativeCommand.originX = 0.0f;
        g_activeNativeCommand.originZ = 0.0f;
        g_lastNativeCommandResult = "issued";
        g_lastNativeCommandTarget = target->getName();
        g_lastNativeCommandTargetId = request.targetId;
    }

    void RegisterShopTrader(ShopTrader* object, Character* owner)
    {
        if (object == NULL || owner == NULL)
            return;

        for (unsigned int index = 0; index < g_trackedShopTraderCount; ++index)
        {
            if (g_trackedShopTraders[index].object == object)
            {
                g_trackedShopTraders[index].owner = owner;
                return;
            }
        }

        if (g_trackedShopTraderCount >= MAX_TRACKED_SHOP_TRADERS)
        {
            g_shopTraderRegistryOverflow = true;
            return;
        }

        g_trackedShopTraders[g_trackedShopTraderCount].object = object;
        g_trackedShopTraders[g_trackedShopTraderCount].owner = owner;
        ++g_trackedShopTraderCount;
    }

    void UnregisterShopTrader(ShopTrader* object)
    {
        for (unsigned int index = 0; index < g_trackedShopTraderCount; ++index)
        {
            if (g_trackedShopTraders[index].object == object)
            {
                --g_trackedShopTraderCount;
                g_trackedShopTraders[index] =
                    g_trackedShopTraders[g_trackedShopTraderCount];
                return;
            }
        }
    }

    bool IsTrackedShopOwner(Character* candidate)
    {
        if (!g_shopTraderRegistryReady || candidate == NULL)
            return false;
        for (unsigned int index = 0; index < g_trackedShopTraderCount; ++index)
        {
            if (g_trackedShopTraders[index].owner == candidate)
                return true;
        }
        return false;
    }

    ShopTrader* ShopTraderConstructorHook(
        ShopTrader* self,
        Character* trader)
    {
        ShopTrader* result = g_originalShopTraderConstructor(self, trader);
        RegisterShopTrader(result, trader);
        return result;
    }

    void ShopTraderDestructorHook(ShopTrader* self)
    {
        UnregisterShopTrader(self);
        g_originalShopTraderDestructor(self);
    }

    void GameWorldResetHook(GameWorld* world)
    {
        // Kenshi can retain the same GameWorld and plugin DLL across New Game or
        // Load Game. Clear pointers and command acknowledgements from the
        // outgoing session before the original reset constructs the next one.
        ResetSessionState();
        g_originalGameWorldReset(world);
    }

    std::string BuildSnapshot(PlayerInterface* player)
    {
        std::ostringstream json;
        json.imbue(std::locale::classic());
        json << std::setprecision(7);

        Character* selected = player != NULL ? player->selectedCharacter.getCharacter() : NULL;
        const lektor<Character*>* characters = NULL;
        if (player != NULL)
            characters = &player->getAllPlayerCharacters();
        bool rosterComplete = characters != NULL;
        bool platoonsComplete = characters != NULL;
        std::set<std::string> rosterIds;
        std::map<std::string, std::string> characterPlatoonIds;
        std::map<std::string, PlayerPlatoonTopology> platoonsById;
        if (characters != NULL)
        {
            for (unsigned int index = 0; index < characters->size(); ++index)
            {
                Character* character = (*characters)[index];
                if (character == NULL || !character->isValid())
                {
                    rosterComplete = false;
                    platoonsComplete = false;
                    continue;
                }
                const std::string id = StableEntityId(character);
                if (id.empty())
                {
                    rosterComplete = false;
                    platoonsComplete = false;
                    continue;
                }
                rosterIds.insert(id);
                ActivePlatoon* characterPlatoon = character->getPlatoon();
                const std::string platoonId = StablePlatoonId(characterPlatoon);
                if (platoonId.empty())
                {
                    platoonsComplete = false;
                    continue;
                }
                characterPlatoonIds[id] = platoonId;
                PlayerPlatoonTopology& topology = platoonsById[platoonId];
                topology.id = platoonId;
                topology.active = characterPlatoon;
                topology.name = characterPlatoon->getName();
                topology.memberIds.push_back(id);
            }
        }

        ActivePlatoon* activePlatoon = NULL;
        if (player != NULL)
        {
            RootObjectContainer* current = player->getCurrentActivePlatoon();
            if (current != NULL &&
                current->getType() == DataObjectContainer::TYPE_PLATOON)
            {
                activePlatoon = static_cast<ActivePlatoon*>(current);
                const std::string activeId = StablePlatoonId(activePlatoon);
                if (!activeId.empty())
                {
                    PlayerPlatoonTopology& topology = platoonsById[activeId];
                    topology.id = activeId;
                    topology.active = activePlatoon;
                    topology.name = activePlatoon->getName();
                }
                else
                {
                    platoonsComplete = false;
                }
            }
            else if (!rosterIds.empty())
            {
                // A loaded non-empty roster with no resolvable current platoon
                // does not answer which tab is active. Preserve the null value,
                // but mark the topology incomplete rather than laundering it as
                // a known absence.
                platoonsComplete = false;
            }
        }
        for (std::map<std::string, PlayerPlatoonTopology>::iterator it =
                 platoonsById.begin();
             it != platoonsById.end();
             ++it)
        {
            std::sort(it->second.memberIds.begin(), it->second.memberIds.end());
        }

        bool selectedCharacterIdsComplete = player != NULL;
        std::vector<std::string> selectedCharacterIds;
        if (player != NULL)
        {
            for (ogre_unordered_set<hand>::type::const_iterator it =
                     player->selectedCharacters.begin();
                 it != player->selectedCharacters.end();
                 ++it)
            {
                Character* selectedCharacter = it->getCharacter();
                const std::string id = StableEntityId(selectedCharacter);
                if (id.empty() || selectedCharacter == NULL ||
                    !selectedCharacter->isPlayerCharacter() ||
                    rosterIds.find(id) == rosterIds.end())
                {
                    selectedCharacterIdsComplete = false;
                    continue;
                }
                selectedCharacterIds.push_back(id);
            }
        }
        std::sort(selectedCharacterIds.begin(), selectedCharacterIds.end());
        if (std::adjacent_find(
                selectedCharacterIds.begin(),
                selectedCharacterIds.end()) != selectedCharacterIds.end())
        {
            selectedCharacterIdsComplete = false;
            selectedCharacterIds.erase(
                std::unique(
                    selectedCharacterIds.begin(),
                    selectedCharacterIds.end()),
                selectedCharacterIds.end());
        }
        const std::string selectedId = StableEntityId(selected);
        const bool primaryIsExportable =
            !selectedId.empty() &&
            rosterIds.find(selectedId) != rosterIds.end() &&
            std::binary_search(
                selectedCharacterIds.begin(),
                selectedCharacterIds.end(),
                selectedId);
        if (selected != NULL && !primaryIsExportable)
            selectedCharacterIdsComplete = false;

        int money = 0;
        if (selected != NULL)
            money = selected->getMoney();
        else if (characters != NULL && characters->size() > 0 && (*characters)[0] != NULL)
            money = (*characters)[0]->getMoney();

        TownBase* currentTown = NULL;
        std::string currentTownId;
        std::string currentTownName;
        bool insideTownWalls = false;
        if (selected != NULL && selected->isValid())
        {
            TownBase* candidate = selected->getCurrentTownLocation();
            if (candidate != NULL && candidate->isValid())
            {
                const std::string candidateId =
                    StableEntityId(candidate->getHandle());
                const std::string candidateName = candidate->getKnownName();
                if (!candidateId.empty() && !candidateName.empty())
                {
                    currentTown = candidate;
                    currentTownId = candidateId;
                    currentTownName = candidateName;
                    insideTownWalls = selected->amInsideTownWalls() != 0;
                }
            }
        }

        json << "{";
        json << "\"protocol_version\":\"" << PROTOCOL_VERSION << "\",";
        json << "\"sequence\":" << ++g_sequence << ",";
        json << "\"captured_at\":\"" << UtcNowIso8601() << "\",";
        json << "\"source\":\"kenshilib-plugin\",";
        json << "\"identity_session_id\":\""
             << IdentitySessionId() << "\",";
        json << "\"capabilities\":";
        KenshiAgentTelemetry::AppendGameplayCapabilities(
            json,
            g_shopTraderRegistryReady);
        json << ",";

        json << "\"game\":{";
        json << "\"loaded\":" << JsonBool(ou != NULL && ou->initialized) << ",";
        json << "\"paused\":" << JsonBool(ou != NULL && ou->isPaused()) << ",";
        json << "\"speed_multiplier\":"
             << (ou != NULL ? ou->getFrameSpeedMultiplier() : 0.0f) << ",";
        json << "\"money\":" << money << ",";
        json << "\"elapsed_minutes\":";
        if (ou != NULL)
            json << ou->getTimeStamp_inGameHours().getTotalMinutes();
        else
            json << "null";
        json << ",\"location_id\":";
        if (currentTown != NULL)
            json << "\"" << JsonEscape(currentTownId) << "\"";
        else
            json << "null";
        json << ",\"location_name\":";
        if (currentTown != NULL)
            json << "\"" << JsonEscape(currentTownName) << "\"";
        else
            json << "null";
        json << ",\"inside_town_walls\":";
        if (currentTown != NULL)
            json << JsonBool(insideTownWalls);
        else
            json << "null";
        json << "},";

        json << "\"camera\":{";
        if (ou != NULL)
        {
            json << "\"position\":";
            AppendVector3(json, ou->getCameraPos());
            json << ",\"center\":";
            AppendVector3(json, ou->getCameraCenter());
        }
        json << "},";

        const bool dialogueOpen =
            gui != NULL && gui->dialogue != NULL && gui->dialogue->isVisible();
        const bool inventoryOpen = gui != NULL && gui->isAnyInventoryWindowOpen();
        // Trade handles survive their windows. Requiring only some other
        // inventory to be open still mislabeled later character, resource, and
        // corpse inventories as commerce. The trader's exact inventory window
        // must itself still be among the open windows.
        const hand* inventoryTraderHandle =
            gui != NULL ? &gui->inventoryWindowTrader : NULL;
        const bool traderInventoryOpen =
            gui != NULL &&
            inventoryTraderHandle != NULL &&
            !inventoryTraderHandle->isNull() &&
            gui->hasInventoryWindowOpen(*inventoryTraderHandle);
        bool registeredShopInventoryOpen = false;
        if (gui != NULL && g_shopTraderRegistryReady)
        {
            for (unsigned int index = 0;
                 index < g_trackedShopTraderCount;
                 ++index)
            {
                Character* owner = g_trackedShopTraders[index].owner;
                ShopTrader* inventoryObject =
                    g_trackedShopTraders[index].object;
                const bool ownerCharacterInventoryOpen =
                    owner != NULL &&
                    gui->hasInventoryWindowOpen(owner->getHandle());
                const bool shopInventoryObjectOpen =
                    inventoryObject != NULL &&
                    gui->hasInventoryWindowOpen(
                        inventoryObject->getHandle());
                if (KenshiAgentTelemetry::IsRegisteredShopInventoryOpen(
                        ownerCharacterInventoryOpen,
                        shopInventoryObjectOpen))
                {
                    registeredShopInventoryOpen = true;
                    break;
                }
            }
        }
        const bool tradeOpen =
            KenshiAgentTelemetry::IsTradeInventoryOpen(
                inventoryOpen,
                traderInventoryOpen,
                registeredShopInventoryOpen);
        const bool statsWindowOpen = gui != NULL && gui->characterStatsWindowVisible();
        const bool modalMessageOpen = gui != NULL && gui->hasModalMessage();
        ProspectingWindow* prospectingWindow = ProspectingWindow::getSingleton();
        const bool prospectingWindowOpen =
            prospectingWindow != NULL &&
            prospectingWindow->window != NULL &&
            prospectingWindow->window->getVisible();
        // Map, squad, research and factions are not separate screens: they are
        // tabs of one ManagementScreen. Reporting only `active_screen` left all
        // four indistinguishable from the plain world view, so the agent could
        // not tell it had opened the map. Report the window and its current tab
        // and let the caller name the tabs.
        ManagementScreen* management = ManagementScreen::getSingleton();
        const bool managementOpen = management != NULL && management->getVisible();
        const int managementTab = managementOpen ? management->getCurrentTab() : -1;
        const int openInventoryWindows =
            gui != NULL ? gui->getNumOpenInventoryWindows() : 0;
        // Kenshi runs five ToolTip subclasses, and `ForgottenGUI::getToolTip`
        // owns exactly one of them. Hovering an item raises a different
        // object entirely - the static `InventoryGUI::toolTip`, a
        // ToolTipInventory - so the old read reported "no tooltip" truthfully
        // about a tooltip nobody was looking at, while the one carrying the
        // price sat on screen. `tooltip_visible` was false for every step of
        // every run ever recorded, which reads exactly like a game that has
        // no tooltips. Prefer the item tooltip and fall back to the generic
        // one, so a hovered item is seen and non-item tooltips still report.
        ToolTip* tooltip = gui != NULL ? gui->getToolTip() : NULL;
        const int tooltipProbe = ProbeToolTipVisible(tooltip);
        const bool tooltipVisible = tooltipProbe == TOOLTIP_PROBE_VISIBLE;

        json << "\"ui\":{";
        json << "\"active_screen\":\""
             << (dialogueOpen
                    ? "dialogue"
                    : (modalMessageOpen
                        ? "message_box"
                        : (tradeOpen
                            ? "trade"
                            : (inventoryOpen ? "inventory" : "world"))))
             << "\",";
        json << "\"modal_open\":"
             << JsonBool(dialogueOpen || inventoryOpen || modalMessageOpen)
             << ",";
        json << "\"stats_window_open\":" << JsonBool(statsWindowOpen) << ",";
        json << "\"prospecting_window_open\":"
             << JsonBool(prospectingWindowOpen) << ",";
        json << "\"management_screen_open\":" << JsonBool(managementOpen) << ",";
        json << "\"management_tab\":" << managementTab << ",";
        json << "\"open_inventory_windows\":" << openInventoryWindows << ",";
        // Whether Kenshi considers a shop trade to be in progress, by its own
        // static answer rather than by our inference from who owns a window.
        // The transfer routes a priced trade through the engine's adjudicator
        // and an unpriced one through the inventory model, and this is the
        // switch between them -- so it is exported, because a switch that
        // silently reads NULL would move a shopkeeper's goods for free.
        {
            Character* npcTrader = InventoryGUI::getNPCTrader();
            json << "\"shop_trader_name\":";
            if (npcTrader != NULL && npcTrader->isValid())
                json << "\"" << JsonEscape(npcTrader->getName()) << "\"";
            else
                json << "null";
            json << ",";
        }
        AppendOpenInventories(json, gui, selected);
        json << "\"dialogue_open\":" << JsonBool(dialogueOpen) << ",";
        std::string dialogueTargetId;
        json << "\"dialogue_target_id\":";
        if (TryGetDialogueTargetId(dialogueTargetId))
            json << "\"" << JsonEscape(dialogueTargetId) << "\"";
        else
            json << "null";
        json << ",";
        json << "\"dialogue_options\":";
        AppendDialogueOptions(json);
        json << ",";
        json << "\"tooltip_visible\":" << JsonBool(tooltipVisible) << ",";
        json << "\"tooltip_probe\":\"" << ToolTipProbeName(tooltipProbe) << "\",";
        json << "\"tooltip_text\":";
        if (tooltipVisible)
            json << "\"" << JsonEscape(CurrentToolTipText(tooltip)) << "\"";
        else
            json << "null";
        json << ",";
        json << "\"tooltip_lines\":";
        if (tooltipVisible)
            AppendToolTipLines(json, tooltip);
        else
            json << "null";
        json << ",";
        // What this trader charges over an item's base value. Static and
        // argument-free, so every visible cell can carry a usable price
        // without hovering any of them - a hover costs a model round-trip per
        // cell, which is why the cells started carrying their own facts in the
        // first place. Only meaningful while a trade partner is set.
        json << "\"trader_price_multiplier\":";
        if (tradeOpen)
            json << InventoryGUI::getTraderPriceMultiplier();
        else
            json << "null";
        json << ",";
        json << "\"tooltip_source_bounds\":";
        if (!tooltipVisible || !AppendToolTipSourceBounds(json, tooltip))
            json << "null";
        json << ",";
        json << "\"visible_controls\":";
        bool visibleControlsComplete = false;
        const bool visibleControlsAvailable =
            AppendVisibleUIControls(
                json,
                inventoryOpen || tradeOpen,
                visibleControlsComplete,
                selected);
        json << ",\"visible_controls_complete\":";
        if (visibleControlsAvailable)
            json << JsonBool(visibleControlsComplete);
        else
            json << "null";
        json << ",\"context_inventory_target_id\":";
        Building* contextInventoryTarget =
            gui != NULL
                ? gui->inventoryWindowBuilding.getBuilding()
                : NULL;
        if (inventoryOpen &&
            contextInventoryTarget != NULL &&
            contextInventoryTarget->isValid() &&
            gui->hasInventoryWindowOpen(
                contextInventoryTarget->getHandle()))
        {
            json << "\""
                 << JsonEscape(
                        StableEntityId(
                            contextInventoryTarget->getHandle()))
                 << "\"";
        }
        else
        {
            json << "null";
        }
        const KenshiAgentTelemetry::RuntimeContextMenuObservation
            contextMenuObservation = ObserveRuntimeContextMenu(player);
        json << ",\"context_menu_open\":"
             << JsonBool(contextMenuObservation.open);
        json << ",\"context_menu_probe\":\""
             << KenshiAgentTelemetry::RuntimeContextMenuProbeName(
                    contextMenuObservation.probe)
             << "\"";
        json << ",\"context_menu\":";
        if (contextMenuObservation.captured)
        {
            json << "{";
            json << "\"target_id\":\""
                 << JsonEscape(contextMenuObservation.targetId) << "\",";
            json << "\"target_name\":";
            if (!contextMenuObservation.targetName.empty())
            {
                json << "\""
                     << JsonEscape(contextMenuObservation.targetName)
                     << "\"";
            }
            else
            {
                json << "null";
            }
            json << ",\"task_type_values\":[";
            for (unsigned int index = 0;
                 index < contextMenuObservation.taskTypeValues.size();
                 ++index)
            {
                if (index > 0)
                    json << ",";
                json << contextMenuObservation.taskTypeValues[index];
            }
            json << "],\"task_type_values_complete\":"
                 << JsonBool(
                        contextMenuObservation.taskTypeValuesComplete);
            json << "}";
        }
        else
        {
            json << "null";
        }
        json << ",";

        // Diagnostic: a `hand` is (type, container, containerSerial, index,
        // serial), so `container` is part of an object's identity and a
        // character who changes platoon gets a different handle. A selection
        // entry captured before such a move still names the old container, so
        // it resolves to the right character while failing an identity compare
        // against that character's current handle - which is exactly the state
        // where `selected_character_ids` lists someone the squad flags and the
        // game itself do not consider selected.
        //
        // This prints, per selection entry, the stored handle beside the same
        // character's current one, so which field actually moved is a fact
        // rather than an inference.
        json << "\"selection_handle_audit\":[";
        if (player != NULL)
        {
            bool firstAudit = true;
            for (ogre_unordered_set<hand>::type::const_iterator it =
                     player->selectedCharacters.begin();
                 it != player->selectedCharacters.end();
                 ++it)
            {
                Character* entryCharacter = it->getCharacter();
                if (entryCharacter == NULL)
                {
                    if (!firstAudit)
                        json << ",";
                    firstAudit = false;
                    json << "\"unresolvable-entry\"";
                    continue;
                }
                const hand current = entryCharacter->getHandle();
                std::ostringstream row;
                row << StableEntityId(entryCharacter)
                    << " stored=" << it->type
                    << "/" << it->container
                    << "/" << it->containerSerial
                    << "/" << it->index
                    << "/" << it->serial
                    << " current=" << current.type
                    << "/" << current.container
                    << "/" << current.containerSerial
                    << "/" << current.index
                    << "/" << current.serial
                    << " match="
                    << (SameHandleIdentity(*it, current) ? "yes" : "no");
                if (!firstAudit)
                    json << ",";
                firstAudit = false;
                json << "\"" << JsonEscape(row.str()) << "\"";
            }
        }
        json << "]";
        json << "},";

        json << "\"active_platoon_id\":";
        const std::string activePlatoonId = StablePlatoonId(activePlatoon);
        if (!activePlatoonId.empty())
            json << "\"" << JsonEscape(activePlatoonId) << "\"";
        else
            json << "null";
        json << ",\"primary_character_id\":";
        if (primaryIsExportable)
            json << "\"" << JsonEscape(selectedId) << "\"";
        else
            json << "null";
        json << ",\"selected_character_ids\":[";
        for (size_t index = 0; index < selectedCharacterIds.size(); ++index)
        {
            if (index > 0)
                json << ",";
            json << "\"" << JsonEscape(selectedCharacterIds[index]) << "\"";
        }
        json << "],\"selected_character_ids_complete\":"
             << JsonBool(selectedCharacterIdsComplete) << ",";
        json << "\"platoons\":[";
        bool firstPlatoon = true;
        for (std::map<std::string, PlayerPlatoonTopology>::const_iterator it =
                 platoonsById.begin();
             it != platoonsById.end();
             ++it)
        {
            if (!firstPlatoon)
                json << ",";
            firstPlatoon = false;
            json << "{\"id\":\"" << JsonEscape(it->second.id) << "\",";
            json << "\"name\":";
            if (!it->second.name.empty())
                json << "\"" << JsonEscape(it->second.name) << "\"";
            else
                json << "null";
            json << ",\"member_ids\":[";
            for (size_t memberIndex = 0;
                 memberIndex < it->second.memberIds.size();
                 ++memberIndex)
            {
                if (memberIndex > 0)
                    json << ",";
                json << "\""
                     << JsonEscape(it->second.memberIds[memberIndex])
                     << "\"";
            }
            json << "]}";
        }
        json << "],\"platoons_complete\":"
             << JsonBool(platoonsComplete) << ",";

        json << "\"active_shop_trader_count\":";
        if (g_shopTraderRegistryReady)
            json << g_trackedShopTraderCount;
        else
            json << "null";
        json << ",";

        json << "\"controller_commands\":{";
        json << "\"available\":true,";
        json << "\"commands\":[";
        const NativeCommandAcknowledgement* publishedCommand =
            PublishedNativeCommandRecord();
        if (publishedCommand != NULL)
            json << SerializeNativeCommandAcknowledgement(*publishedCommand);
        json << "],";
        json << "\"last_command_sequence\":" << g_nativeCommandSequence;
        if (!g_lastNativeCommand.empty())
        {
            json << ",\"last_command\":\""
                 << JsonEscape(g_lastNativeCommand) << "\"";
        }
        if (!g_lastNativeCommandResult.empty())
        {
            json << ",\"last_result\":\""
                 << JsonEscape(g_lastNativeCommandResult) << "\"";
        }
        if (!g_lastNativeCommandTarget.empty())
        {
            json << ",\"last_target\":\""
                 << JsonEscape(g_lastNativeCommandTarget) << "\"";
        }
        if (!g_lastNativeCommandTargetId.empty())
        {
            json << ",\"last_target_id\":\""
                 << g_lastNativeCommandTargetId << "\"";
        }
        json << "},";

        json << "\"roster\":[";
        if (characters != NULL)
        {
            bool first = true;
            for (unsigned int index = 0; index < characters->size(); ++index)
            {
                Character* character = (*characters)[index];
                if (character == NULL || !character->isValid())
                    continue;
                const std::string characterId = StableEntityId(character);
                if (characterId.empty())
                    continue;
                if (!first)
                    json << ",";
                first = false;
                const Ogre::Vector3 position = character->getPosition();
                json << "{";
                json << "\"id\":\"" << characterId << "\",";
                json << "\"name\":\"" << JsonEscape(character->getName()) << "\",";
                json << "\"platoon_id\":";
                std::map<std::string, std::string>::const_iterator platoonIt =
                    characterPlatoonIds.find(characterId);
                if (platoonIt != characterPlatoonIds.end())
                    json << "\"" << JsonEscape(platoonIt->second) << "\"";
                else
                    json << "null";
                json << ",";
                json << "\"alive\":" << JsonBool(!character->isDestroyed()) << ",";
                json << "\"conscious\":" << JsonBool(!character->isUnconcious()) << ",";
                json << "\"down\":" << JsonBool(character->isDown()) << ",";
                json << "\"crippled\":" << JsonBool(character->isCrippled()) << ",";
                json << "\"position\":";
                AppendVector3(json, position);
                json << ",\"movement_speed\":" << character->getMovementSpeed() << ",";
                const hand& indoorHandle = character->isIndoors();
                Building* indoorBuilding = indoorHandle.getBuilding();
                json << "\"indoors\":"
                     << JsonBool(
                            KenshiAgentTelemetry::HasResolvedIndoorBuilding(
                                indoorHandle.isValid(),
                                indoorBuilding != NULL,
                                indoorBuilding != NULL &&
                                    indoorBuilding->isValid()))
                     << ",";
                json << "\"food_items\":" << character->getNumFoodItems() << ",";
                // Whether anyone is currently fighting this character. The
                // field existed in the schema and was never filled, so it read
                // None forever: an agent could be beaten unconscious without a
                // single observation saying a fight had started.
                json << "\"in_combat\":"
                     << JsonBool(character->isInCombatMode(true, true)) << ",";
                AI* characterAI = character->getAI();
                AITaskSytem* characterTasks =
                    characterAI != NULL
                        ? characterAI->getTaskSystem()
                        : NULL;
                if (characterTasks != NULL)
                {
                    const std::string currentGoal =
                        characterTasks->getCurrentGoalString();
                    if (!currentGoal.empty())
                    {
                        json << "\"current_goal\":\""
                             << JsonEscape(currentGoal) << "\",";
                    }
                }
                // The agent set itself the goal of feeding this character while
                // unable to read whether it was hungry. Hunger and blood are
                // the two numbers the survival loop actually turns on.
                MedicalSystem* medical = character->getMedical();
                if (medical != NULL)
                {
                    json << "\"hunger\":" << medical->hunger << ",";
                    json << "\"blood\":" << medical->blood << ",";
                    // Blood alone cannot tell recovering from dying: both look
                    // like a low number. The rate is the sign. A downed pair
                    // bled out while the agent waited for them to wake up,
                    // because waiting is correct for an unconscious character
                    // and fatal for a bleeding one, and this field - already
                    // modelled Python-side - read null on all 887 samples of
                    // that run. Null meant "never exported", and nothing said so.
                    json << "\"bleeding_rate\":"
                         << medical->currentBleedRate << ",";
                    // Per-limb condition. `currentBleedRate` is whole-body
                    // blood loss and says nothing about a wound getting worse:
                    // Kenshi degenerates untreated damage per part
                    // (`HealthPartStatus::update` takes a degenerationRate), so
                    // a leg can be deteriorating while the bleed rate reads
                    // zero. Watched happening live with no way to see it.
                    //
                    // This is also what distinguishes "cannot move" from "slow":
                    // legs damaged past the KO point make a character crawl
                    // until bandaged, which is movement, not immobility.
                    json << "\"body_parts\":[";
                    bool firstPart = true;
                    for (unsigned int partIndex = 0;
                         partIndex < medical->anatomy.size();
                         ++partIndex)
                    {
                        MedicalSystem::HealthPartStatus* part =
                            medical->anatomy[partIndex];
                        if (part == NULL)
                            continue;
                        GameData* partData = part->getData();
                        if (partData == NULL)
                            continue;
                        if (!firstPart)
                            json << ",";
                        firstPart = false;
                        json << "{";
                        json << "\"name\":\"" << JsonEscape(partData->name) << "\",";
                        json << "\"current_hp\":" << part->flesh << ",";
                        json << "\"max_hp\":" << part->_maxHealth << ",";
                        json << "\"wear_damage\":" << part->wearDamage << ",";
                        json << "\"bleeding_rate\":"
                             << part->getExtraBleedingAmount() << ",";
                        json << "\"missing\":" << JsonBool(part->isDead());
                        json << "}";
                    }
                    json << "],";
                }
                // What it already carries, so it does not go shopping for what
                // is in its own pack.
                json << "\"inventory\":[";
                Inventory* inventory = character->getInventory();
                if (inventory != NULL)
                {
                    // Walk sections rather than the flat item list: equipped
                    // gear lives in its own slots, so a flat walk reported a
                    // character wearing trousers and holding a stick as
                    // carrying nothing. The section name *is* the answer to
                    // "what is it wearing and wielding".
                    unsigned int emitted = 0;
                    lektor<InventorySection*>::iterator sectionIt =
                        inventory->sectionsInSearchOrder.begin();
                    for (; sectionIt != inventory->sectionsInSearchOrder.end() &&
                           emitted < MAX_INVENTORY_ITEMS;
                         ++sectionIt)
                    {
                        InventorySection* section = *sectionIt;
                        if (section == NULL)
                            continue;
                        const Ogre::vector<InventorySection::SectionItem>::type& items =
                            section->getItems();
                        for (size_t i = 0;
                             i < items.size() && emitted < MAX_INVENTORY_ITEMS;
                             ++i)
                        {
                            Item* carried = items[i].item;
                            if (carried == NULL || !carried->isValid())
                                continue;
                            if (emitted > 0)
                                json << ",";
                            ++emitted;
                            json << "{";
                            AppendItemFacts(json, carried);
                            json << "\"section\":\"" << JsonEscape(section->name)
                                 << "\",";
                            json << "\"equipped\":"
                                 << JsonBool(section->isAnEquippedItemSection) << "}";
                        }
                    }
                }
                json << "],";
                json << "\"inventory_complete\":";
                if (inventory != NULL)
                {
                    json << JsonBool(
                        inventory->getAllItems().size() <=
                        MAX_INVENTORY_ITEMS);
                }
                else
                {
                    json << "null";
                }
                json << ",";
                AppendCharacterWorkState(json, character);
                json << "}";
            }
        }
        json << "],\"roster_complete\":" << JsonBool(rosterComplete) << ",";
        json << "\"nearby_entities\":[";
        // Hoisted so the completeness answer outlives the scan that produced it.
        int nearbyCharacterCount = 0;
        if (ou != NULL && selected != NULL && selected->isValid())
        {
            lektor<RootObject*> nearbyCharacters;
            const Ogre::Vector3 selectedPosition = selected->getPosition();
            ou->getCharactersWithinSphere(
                nearbyCharacters,
                selectedPosition,
                NEARBY_CHARACTER_RADIUS,
                0.0f,
                30.0f,
                MAX_NEARBY_CHARACTERS,
                0,
                selected);

            // Ask Kenshi which orders it would actually offer on each nearby
            // person, exactly as world targets are already probed. Legality is
            // the game's answer rather than ours, which is what makes
            // attacking, looting, and first aid reachable without a
            // hand-written whitelist and a new disposition fence per verb.
            // Nearest first, because the budget is smaller than the crowd.
            std::vector<std::pair<float, std::string> > probeOrder;
            for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
                 it != nearbyCharacters.end();
                 ++it)
            {
                Character* candidate = reinterpret_cast<Character*>(*it);
                if (candidate == NULL || !candidate->isValid() ||
                    candidate == selected || candidate->isPlayerCharacter())
                    continue;
                const std::string candidateId = StableEntityId(candidate);
                if (candidateId.empty())
                    continue;
                probeOrder.push_back(
                    std::pair<float, std::string>(
                        Distance(candidate->getPosition(), selectedPosition),
                        candidateId));
            }
            std::sort(probeOrder.begin(), probeOrder.end());
            std::set<std::string> probeIds;
            for (unsigned int probed = 0;
                 probed < probeOrder.size() &&
                     KenshiAgentTelemetry::IsWithinTargetProbeBudget(
                         probed,
                         MAX_PROBED_NEARBY_CHARACTERS);
                 ++probed)
            {
                probeIds.insert(probeOrder[probed].second);
            }

            bool first = true;
            for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
                 it != nearbyCharacters.end();
                 ++it)
            {
                Character* target = reinterpret_cast<Character*>(*it);
                if (target == NULL || !target->isValid() || target == selected || target->isPlayerCharacter())
                    continue;
                const std::string targetId = StableEntityId(target);
                if (targetId.empty())
                    continue;

                if (!first)
                    json << ",";
                first = false;

                const Faction* faction = target->getFaction();
                const Ogre::Vector3 targetPosition = target->getPosition();
                CharacterAnimal* animal = target->isAnimal();
                ActivePlatoon* platoon = target->getPlatoon();
                const bool traderSquad =
                    platoon != NULL && platoon->getIsTrader();
                const bool hasVendorList =
                    platoon != NULL && platoon->getHasVendorList();
                const bool isSquadLeader =
                    platoon != NULL && platoon->getSquadLeader() == target;
                const bool isShopInventoryOwner =
                    IsTrackedShopOwner(target);
                float screenX = 0.0f;
                float screenY = 0.0f;
                float cameraBearingDegrees = 0.0f;
                const bool hasCameraBearing =
                    TryGetCameraBearing(
                        player,
                        targetPosition,
                        cameraBearingDegrees);
                const bool hasScreenPosition =
                    target->isOnScreen && target->getVisible() &&
                    TryGetScreenPosition(player, targetPosition, screenX, screenY);
                json << "{";
                json << "\"id\":\"" << targetId << "\",";
                json << "\"name\":\"" << JsonEscape(target->getName()) << "\",";
                json << "\"kind\":\""
                     << (animal != NULL ? "animal" : "character")
                     << "\",";
                json << "\"is_animal\":" << JsonBool(animal != NULL) << ",";
                json << "\"trader_squad\":" << JsonBool(traderSquad) << ",";
                json << "\"has_vendor_list\":" << JsonBool(hasVendorList) << ",";
                json << "\"is_squad_leader\":" << JsonBool(isSquadLeader) << ",";
                json << "\"has_dialogue\":" << JsonBool(target->hasDialogue()) << ",";
                json << "\"shop_inventory_owner\":";
                if (g_shopTraderRegistryReady)
                    json << JsonBool(isShopInventoryOwner);
                else
                    json << "null";
                json << ",";
                if (faction != NULL)
                    json << "\"faction\":\"" << JsonEscape(const_cast<Faction*>(faction)->getName()) << "\",";
                json << "\"disposition\":\"" << GetDisposition(selected, target) << "\",";
                json << "\"distance\":" << Distance(targetPosition, selectedPosition) << ",";
                json << "\"position\":";
                AppendVector3(json, targetPosition);
                json << ",";
                if (hasCameraBearing)
                {
                    json << "\"camera_bearing_degrees\":"
                         << cameraBearingDegrees << ",";
                }
                if (hasScreenPosition)
                {
                    json << "\"screen_position\":{\"x\":" << screenX
                         << ",\"y\":" << screenY << "},";
                }
                json << "\"visible\":" << JsonBool(hasScreenPosition) << ",";
                json << "\"conscious\":" << JsonBool(!target->isUnconcious());
                json << ",";
                const bool probeThisTarget =
                    probeIds.find(targetId) != probeIds.end();
                std::vector<KenshiAgentTelemetry::AdvertisedTask> advertised;
                bool advertisedTasksProbed = false;
                if (probeThisTarget)
                {
                    advertisedTasksProbed =
                        ProbeMenuOrders(player, target, advertised);
                }
                KenshiAgentTelemetry::AppendAdvertisedTasks(
                    json,
                    advertisedTasksProbed,
                    advertised);
                json << "}";
            }
            nearbyCharacterCount = static_cast<int>(nearbyCharacters.size());
        }
        json << "],";
        // Whether that list is everything, said out loud.
        //
        // The sphere is genuinely unbounded -- a crowd can be larger than any
        // budget -- so a cap here is honest. Reporting the result as though it
        // were the whole world is not: an agent cannot tell "nobody else is
        // near" from "we stopped counting", and those call for opposite
        // decisions. The bounded lists that already do this
        // (`open_inventories_complete`) are the pattern; these were the ones
        // that stayed quiet.
        json << "\"nearby_entities_complete\":"
             << JsonBool(nearbyCharacterCount < MAX_NEARBY_CHARACTERS)
             << ",";
        // `selection_orderable_tasks` was published here and has been removed.
        //
        // It existed to split the order question in half -- "may this selection
        // issue this order at all" versus "does this order apply to that
        // target" -- because a wrong combined answer was unattributable. It did
        // its job in one live snapshot: `isOrderValidForSelection` returned true
        // for all 291 vocabulary entries, so the selection half discriminates
        // nothing and no call now consults it. Keeping the field meant
        // serializing 291 entries every 500ms to re-answer a settled question.
        // The finding survives in interaction_proof_status.json and beside
        // `ProbeMenuOrders`, which is where a finding belongs.
        json << "\"prospect_survey\":";
        AppendProspectSurvey(json);
        json << ",";
        bool discoveredObjectsComplete = true;
        json << "\"discovered_objects\":[";
        if (ou != NULL && player != NULL && selected != NULL && selected->isValid())
        {
            AppendDiscoveredObjects(
                json,
                player,
                ou,
                selected,
                selected->getPosition(),
                discoveredObjectsComplete);
        }
        json << "],";
        bool worldTargetScanAtCapacity = false;
        json << "\"world_targets\":[";
        bool firstWorldTarget = true;
        if (player != NULL &&
            selected != NULL &&
            selected->isValid() &&
            characters != NULL)
        {
            const Ogre::Vector3 selectedPosition = selected->getPosition();
            for (unsigned int index = 0; index < characters->size(); ++index)
            {
                Character* target = (*characters)[index];
                if (target == NULL ||
                    !target->isValid() ||
                    !target->isPlayerCharacter())
                {
                    continue;
                }
                std::vector<KenshiAgentTelemetry::AdvertisedTask> advertised;
                const bool menuAsked =
                    ProbeMenuOrders(player, target, advertised);
                if (!menuAsked ||
                    !HasAdvertisedTask(advertised, FIRST_AID_ORDER))
                {
                    continue;
                }
                const std::string targetId = StableEntityId(target);
                if (targetId.empty())
                    continue;
                if (!firstWorldTarget)
                    json << ",";
                firstWorldTarget = false;
                const Ogre::Vector3 targetPosition = target->getPosition();
                json << "{";
                json << "\"id\":\"" << targetId << "\",";
                json << "\"name\":\""
                     << JsonEscape(target->getName()) << "\",";
                json << "\"kind\":\"squad_character\",";
                json << "\"position\":";
                AppendVector3(json, targetPosition);
                json << ",\"distance\":"
                     << Distance(targetPosition, selectedPosition) << ",";
                json << "\"context_actions\":[\"first_aid\"],";
                json << "\"default_task\":\"first_aid\",";
                KenshiAgentTelemetry::AppendAdvertisedTasks(
                    json,
                    true,
                    advertised);
                json << "}";
            }
        }
        if (ou != NULL && selected != NULL && selected->isValid())
        {
            lektor<RootObject*> nearBuildings;
            lektor<RootObject*> outerBuildings;
            const Ogre::Vector3 selectedPosition = selected->getPosition();
            ou->getObjectsWithinSphere(
                nearBuildings,
                selectedPosition,
                NEAR_WORLD_CONTEXT_TARGET_RADIUS,
                BUILDING,
                MAX_NEAR_WORLD_CONTEXT_BUILDINGS,
                selected);
            ou->getObjectsWithinSphere(
                outerBuildings,
                selectedPosition,
                WORLD_CONTEXT_TARGET_RADIUS,
                BUILDING,
                MAX_OUTER_WORLD_CONTEXT_BUILDINGS,
                selected);
            worldTargetScanAtCapacity =
                IsWorldTargetScanAtCapacity(
                    static_cast<unsigned int>(nearBuildings.size()),
                    static_cast<unsigned int>(
                        MAX_NEAR_WORLD_CONTEXT_BUILDINGS)) ||
                IsWorldTargetScanAtCapacity(
                    static_cast<unsigned int>(outerBuildings.size()),
                    static_cast<unsigned int>(
                        MAX_OUTER_WORLD_CONTEXT_BUILDINGS));
            std::vector<NaturalResourceTargetSnapshot> candidates;
            std::map<std::string, RootObject*> candidateObjects;
            AppendNaturalResourceCandidates(
                player,
                nearBuildings,
                selectedPosition,
                candidates,
                candidateObjects);
            AppendNaturalResourceCandidates(
                player,
                outerBuildings,
                selectedPosition,
                candidates,
                candidateObjects);
            std::vector<NaturalResourceTargetSnapshot> targets =
                SelectNearestNaturalResourceTargets(
                    candidates,
                    MAX_WORLD_CONTEXT_TARGETS);
            unsigned int probedTargets = 0;
            for (std::vector<NaturalResourceTargetSnapshot>::iterator it =
                     targets.begin();
                 it != targets.end();
                 ++it)
            {
                if (!KenshiAgentTelemetry::IsWithinTargetProbeBudget(
                        probedTargets,
                        MAX_PROBED_WORLD_TARGETS))
                {
                    break;
                }
                const std::map<std::string, RootObject*>::const_iterator found =
                    candidateObjects.find(it->id);
                if (found == candidateObjects.end())
                    continue;
                ++probedTargets;
                it->advertisedTasksProbed = ProbeMenuOrders(
                    player,
                    found->second,
                    it->advertisedTasks);
            }
            for (std::vector<NaturalResourceTargetSnapshot>::const_iterator it =
                     targets.begin();
                 it != targets.end();
                 ++it)
            {
                if (!firstWorldTarget)
                    json << ",";
                firstWorldTarget = false;
                json << SerializeNaturalResourceTarget(*it);
            }
        }
        json << "],";
        bool knownMapDestinationsTruncated = false;
        json << "\"known_map_destinations\":[";
        if (selected != NULL && selected->isValid())
        {
            std::vector<KenshiAgentTelemetry::NativeMovementPosition>
                selectedPositions;
            if (characters != NULL)
            {
                for (unsigned int index = 0;
                     index < characters->size();
                     ++index)
                {
                    Character* member = (*characters)[index];
                    if (member == NULL || !member->isValid() ||
                        !IsSelected(player, member->getHandle()))
                    {
                        continue;
                    }
                    const Ogre::Vector3 memberPosition =
                        member->getPosition();
                    KenshiAgentTelemetry::NativeMovementPosition position;
                    position.x = memberPosition.x;
                    position.z = memberPosition.z;
                    selectedPositions.push_back(position);
                }
            }
            if (selectedPositions.empty())
            {
                const Ogre::Vector3 selectedPosition =
                    selected->getPosition();
                KenshiAgentTelemetry::NativeMovementPosition position;
                position.x = selectedPosition.x;
                position.z = selectedPosition.z;
                selectedPositions.push_back(position);
            }
            std::vector<KnownMapDestinationSnapshot> destinations;
            CollectKnownMapDestinations(
                selectedPositions,
                destinations);
            knownMapDestinationsTruncated =
                destinations.size() > MAX_KNOWN_MAP_DESTINATIONS;
            const size_t emitCount =
                destinations.size() < MAX_KNOWN_MAP_DESTINATIONS
                    ? destinations.size()
                    : MAX_KNOWN_MAP_DESTINATIONS;
            for (size_t index = 0; index < emitCount; ++index)
            {
                if (index > 0)
                    json << ",";
                const KnownMapDestinationSnapshot& destination =
                    destinations[index];
                json << "{";
                json << "\"id\":\""
                     << JsonEscape(destination.id) << "\",";
                json << "\"name\":\""
                     << JsonEscape(destination.name) << "\",";
                json << "\"has_gates\":"
                     << JsonBool(destination.hasGates) << ",";
                json << "\"distance\":"
                     << destination.distance;
                json << "}";
            }
        }
        json << "],";
        json << "\"warnings\":["
             << "\"Partial telemetry only: body-part wounds, "
             << "getting-eaten state, imprisonment/enslavement, "
             << "distant world state, and click-target occlusion "
             << "are not exported or validated. "
             << "The food_items scalar is not authoritative over the named inventory list. "
             << "A visible nearby entity is rendered inside the current viewport, but "
             << "geometry can still occlude it or intercept a click.\"";
        if (g_shopTraderRegistryOverflow)
        {
            json << ",\"The live ShopTrader registry exceeded its bounded capacity; "
                 << "shop_inventory_owner is incomplete.\"";
        }
        if (worldTargetScanAtCapacity)
        {
            json << ",\"The nearby building scan reached its bounded capacity; "
                 << "world_targets may be incomplete.\"";
        }
        if (knownMapDestinationsTruncated)
        {
            json << ",\"The discovered settlement list exceeded its bounded "
                 << "capacity; known_map_destinations contains the nearest "
                 << "markers only.\"";
        }
        json
             << "]";
        json << "}";
        return json.str();
    }

    std::string BuildTitleSnapshot()
    {
        std::ostringstream json;
        json.imbue(std::locale::classic());
        json << std::setprecision(7);

        json << "{";
        json << "\"protocol_version\":\"" << PROTOCOL_VERSION << "\",";
        json << "\"sequence\":" << ++g_sequence << ",";
        json << "\"captured_at\":\"" << UtcNowIso8601() << "\",";
        json << "\"source\":\"kenshilib-plugin-title\",";
        // Published here too, because a command sent to the menu has to be
        // fenced against the same session as one sent in game. Omitting it
        // meant `continue_game` could not be addressed at all: the request
        // needs an identity to carry and telemetry offered none.
        json << "\"identity_session_id\":\""
             << IdentitySessionId() << "\",";
        json << "\"capabilities\":[\"ui.visible_controls\","
                "\"control.continue_game\",\"control.load_game\","
                "\"control.new_game\"],";
        json << "\"game\":{\"loaded\":false},";
        json << "\"ui\":{";
        json << "\"active_screen\":\"title\",";
        json << "\"visible_controls\":";
        bool visibleControlsComplete = false;
        const bool visibleControlsAvailable =
            AppendVisibleUIControls(
                json,
                false,
                visibleControlsComplete,
                NULL);
        json << ",\"visible_controls_complete\":";
        if (visibleControlsAvailable)
            json << JsonBool(visibleControlsComplete);
        else
            json << "null";
        json << ",\"context_inventory_target_id\":null";
        json << "},";
        // The real block, not a placeholder. The menu accepts commands now,
        // so reporting `available: false` with no command records here would
        // hide both the capability and every verdict it produces -- a command
        // could be pressed and nothing would say whether it was.
        json << "\"controller_commands\":{";
        json << "\"available\":true,";
        json << "\"commands\":[";
        const NativeCommandAcknowledgement* publishedCommand =
            PublishedNativeCommandRecord();
        if (publishedCommand != NULL)
            json << SerializeNativeCommandAcknowledgement(*publishedCommand);
        json << "],";
        json << "\"last_command_sequence\":" << g_nativeCommandSequence;
        if (!g_lastNativeCommand.empty())
        {
            json << ",\"last_command\":\""
                 << JsonEscape(g_lastNativeCommand) << "\"";
        }
        if (!g_lastNativeCommandResult.empty())
        {
            json << ",\"last_result\":\""
                 << JsonEscape(g_lastNativeCommandResult) << "\"";
        }
        json << "},";
        json << "\"active_platoon_id\":null,";
        json << "\"primary_character_id\":null,";
        json << "\"selected_character_ids\":[],";
        json << "\"selected_character_ids_complete\":false,";
        json << "\"platoons\":[],";
        json << "\"platoons_complete\":false,";
        json << "\"roster\":[],";
        json << "\"roster_complete\":false,";
        json << "\"active_shop_trader_count\":null,";
        json << "\"nearby_entities\":[],";
        json << "\"prospect_survey\":null,";
        json << "\"discovered_objects\":[],";
        json << "\"world_targets\":[],";
        json << "\"warnings\":[";
        json << "\"Title-screen snapshot: loaded-game, entity, command, and "
             << "world capabilities are intentionally unavailable.\"";
        json << "]";
        json << "}";
        return json.str();
    }

    void WriteStatus(const char* state, const char* message)
    {
        std::ostringstream json;
        json << "{\"state\":\"" << JsonEscape(state)
             << "\",\"message\":\"" << JsonEscape(message)
             << "\",\"captured_at\":\"" << UtcNowIso8601() << "\"}";
        std::string error;
        KenshiAgentTelemetry::AtomicWriteUtf8(
            g_outputDirectory,
            L"plugin_status.json",
            json.str(),
            error);
    }

    void Sample(PlayerInterface* player, bool titleOnly)
    {
        if (g_sampling)
            return;
        SamplingGuard samplingGuard(g_sampling);
        try
        {
            std::string error;
            const std::string snapshot =
                titleOnly ? BuildTitleSnapshot() : BuildSnapshot(player);
            if (!KenshiAgentTelemetry::AtomicWriteUtf8(
                    g_outputDirectory,
                    L"telemetry.latest.json",
                    snapshot,
                    error))
            {
                ErrorLog(std::string("KenshiAgentTelemetry write failed: ") + error);
            }
        }
        catch (const std::exception& exception)
        {
            ErrorLog(
                std::string("KenshiAgentTelemetry sample failed: ") +
                exception.what());
        }
        catch (...)
        {
            ErrorLog("KenshiAgentTelemetry sample failed: unknown exception.");
        }
    }

    // Whether the request file has been replaced since the last look.
    //
    // The transport used to be "write the file, then press ctrl+shift+f10", so
    // dispatching a native command required sending keystrokes to the game.
    // That one keystroke was the last reason the agent needed the desktop at
    // all: every operation that moved a pointer has been retired, and without
    // this the input subsystem exists solely to tap a hotkey at itself.
    //
    // Watching the file is the same handshake with the middleman removed. The
    // update hook already runs every frame on the game thread, which is exactly
    // where a request has to be read anyway.
    bool NativeCommandRequestChanged()
    {
        if (g_outputDirectory.empty())
            return false;
        std::wstring path = g_outputDirectory;
        if (!path.empty() && path[path.size() - 1] != L'\\')
            path += L'\\';
        path += NATIVE_COMMAND_REQUEST_FILE_W;

        WIN32_FILE_ATTRIBUTE_DATA attributes;
        if (!GetFileAttributesExW(
                path.c_str(),
                GetFileExInfoStandard,
                &attributes))
        {
            return false;
        }
        const FILETIME& written = attributes.ftLastWriteTime;
        if (written.dwLowDateTime != g_pendingRequestWriteLow ||
            written.dwHighDateTime != g_pendingRequestWriteHigh)
        {
            // Changed this frame. Note it and wait: reading now can catch a
            // half-written file, which the hotkey handshake used to make
            // impossible by only firing once the writer had finished. Measured
            // live, an otherwise valid request came back `malformed_request`
            // three times in a row for exactly this reason.
            g_pendingRequestWriteLow = written.dwLowDateTime;
            g_pendingRequestWriteHigh = written.dwHighDateTime;
            return false;
        }
        if (written.dwLowDateTime == g_lastRequestWriteLow &&
            written.dwHighDateTime == g_lastRequestWriteHigh)
        {
            return false;
        }
        // A first sighting is not a new request: the file may have been left by
        // an earlier session, and replaying it would issue a command nobody
        // asked for this run.
        const bool firstSighting =
            g_lastRequestWriteLow == 0 && g_lastRequestWriteHigh == 0;
        g_lastRequestWriteLow = written.dwLowDateTime;
        g_lastRequestWriteHigh = written.dwHighDateTime;
        return !firstSighting;
    }

    void PlayerInterfaceUpdateHook(PlayerInterface* player)
    {
        g_originalPlayerInterfaceUpdate(player);
        // Engine-native follow is reasserted because vanilla camera panning
        // clears it. Keeping the camera center on the command owner also keeps
        // terrain and interactable streaming aligned with native movement.
        MaintainCameraFollowForActiveCommand(player);
        MonitorActiveNativeCommand(player);
        if (NativeCommandRequestChanged())
            ProcessNativeCommandRequest(player);

        const DWORD now = GetTickCount();
        if (ou != NULL &&
            ou->initialized &&
            player != NULL &&
            now - g_lastSnapshotTick >=
                KenshiAgentTelemetry::TELEMETRY_SNAPSHOT_INTERVAL_MS)
        {
            g_lastSnapshotTick = now;
            CompletePendingTitleTransitionAcknowledgement();
            Sample(player, false);
            // The snapshot just published the title-transition acknowledgement
            // beside the new session. Later resets must not carry it again.
            g_titleTransitionAcknowledgementPending = false;
        }
    }

    // One menu press, from the main menu, with no world yet.
    //
    // The in-game path processes commands from `PlayerInterface::update`, which
    // does not run here -- which is why `controller_commands.available` read false on
    // the title screen and every launch had to be clicked.
    void ProcessTitleScreenCommandRequest(TitleScreen* titleScreen)
    {
        std::string payload;
        std::string readError;
        if (!KenshiAgentTelemetry::ReadUtf8Bounded(
                g_outputDirectory,
                NATIVE_COMMAND_REQUEST_FILE_W,
                MAX_NATIVE_COMMAND_BYTES,
                payload,
                readError))
        {
            return;
        }
        NativeCommandRequest request;
        std::string rejection;
        if (!ParseNativeCommandRequest(payload, request, rejection))
            return;
        if (!KenshiAgentTelemetry::NativeCommandDrivesTitleScreen(request.command))
            return;
        if (FindNativeAcknowledgement(request.commandId) >= 0)
            return;
        if (request.controlMode != "native_assisted")
        {
            RejectNativeCommand(request, "wrong_control_mode");
            return;
        }
        if (request.identitySessionId != IdentitySessionId())
        {
            RejectNativeCommand(request, "identity_session_mismatch");
            return;
        }
        if (titleScreen == NULL)
        {
            RejectNativeCommand(request, "title_screen_absent");
            return;
        }
        TitleScreenActionFunction press = NULL;
        SaveManager* saves = NULL;
        if (request.command == "continue_game")
        {
            press = TitleScreenReach::ResolveContinue();
            if (press == NULL)
            {
                RejectNativeCommand(request, "title_screen_action_unavailable");
                return;
            }
        }
        else
        {
            saves = SaveManager::getSingleton();
            if (saves == NULL)
            {
                RejectNativeCommand(request, "save_manager_unavailable");
                return;
            }
        }
        // Accepted means Kenshi received the transition request. The next
        // loaded-world telemetry is the proof that it completed; GameWorld
        // reset intentionally starts a new identity session. This exact record
        // is carried into the first loaded-world frame, then retired.
        AddNativeAcknowledgement(
            request, "accepted", "title_screen_action_issued", true, false);
        g_titleTransitionAcknowledgementPending = true;
        g_lastNativeCommand = request.command;
        g_lastNativeCommandResult = "title_screen_action_issued";
        if (press != NULL)
        {
            // The handler takes the widget that raised the event. There is no
            // synthetic widget here, so NULL is the exact honest argument.
            press(titleScreen, NULL);
        }
        else if (request.command == "load_game")
            saves->load(request.saveName);
        else
            saves->newGame(request.gameStartId);
    }

    void TitleScreenUpdateHook(TitleScreen* titleScreen)
    {
        g_originalTitleScreenUpdate(titleScreen);
        if (NativeCommandRequestChanged())
            ProcessTitleScreenCommandRequest(titleScreen);
        const DWORD now = GetTickCount();
        if ((ou == NULL || !ou->initialized) &&
            now - g_lastSnapshotTick >=
                KenshiAgentTelemetry::TELEMETRY_SNAPSHOT_INTERVAL_MS)
        {
            g_lastSnapshotTick = now;
            Sample(NULL, true);
        }
    }
}

__declspec(dllexport) void startPlugin()
{
    g_processGeneration = CreateProcessGeneration();
    ResetSessionState();
    g_outputDirectory = KenshiAgentTelemetry::ResolveTelemetryDirectory();
    WriteStatus(
        "starting",
        "Installing Kenshi title/player/context telemetry and ShopTrader lifecycle hooks.");

    const KenshiLib::HookStatus contextMenuStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&ContextMenu::showContextMenu),
        ContextMenuShowHook,
        &g_originalContextMenuShow);
    if (contextMenuStatus != KenshiLib::SUCCESS)
    {
        ErrorLog(
            "KenshiAgentTelemetry: could not hook ContextMenu::showContextMenu.");
        WriteStatus("error", "Could not hook ContextMenu::showContextMenu.");
        return;
    }

    // Not fatal. Without this hook a menu probe would draw a real menu over the
    // player's screen twice a second, so `ProbeMenuOrders` refuses to run at
    // all and every target reports `advertised_tasks_probed` false -- "not
    // asked", which the binding and the affordance already fail closed on.
    // Losing the orders is the correct trade against taking over the display.
    const KenshiLib::HookStatus contextMenuGUIStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&ContextMenuGUI::show),
        ContextMenuGUIShowHook,
        &g_originalContextMenuGUIShow);
    g_contextMenuProbeInstalled = contextMenuGUIStatus == KenshiLib::SUCCESS;
    if (!g_contextMenuProbeInstalled)
    {
        ErrorLog(
            "KenshiAgentTelemetry: could not hook ContextMenuGUI::show; "
            "silent order probing is unavailable this session.");
    }

    const KenshiLib::HookStatus updateStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&PlayerInterface::update),
        PlayerInterfaceUpdateHook,
        &g_originalPlayerInterfaceUpdate);

    if (updateStatus != KenshiLib::SUCCESS)
    {
        ErrorLog("KenshiAgentTelemetry: could not hook PlayerInterface::update.");
        WriteStatus("error", "Could not hook PlayerInterface::update.");
        return;
    }

    const KenshiLib::HookStatus titleStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&TitleScreen::_NV_update),
        TitleScreenUpdateHook,
        &g_originalTitleScreenUpdate);
    if (titleStatus != KenshiLib::SUCCESS)
    {
        ErrorLog("KenshiAgentTelemetry: could not hook TitleScreen::update.");
        WriteStatus("error", "Could not hook TitleScreen::update.");
        return;
    }

    const KenshiLib::HookStatus constructorStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&ShopTrader::_CONSTRUCTOR),
        ShopTraderConstructorHook,
        &g_originalShopTraderConstructor);
    const KenshiLib::HookStatus destructorStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&ShopTrader::_DESTRUCTOR),
        ShopTraderDestructorHook,
        &g_originalShopTraderDestructor);
    const KenshiLib::HookStatus worldResetStatus = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&GameWorld::resetGame),
        GameWorldResetHook,
        &g_originalGameWorldReset);
    g_shopTraderRegistryReady =
        constructorStatus == KenshiLib::SUCCESS &&
        destructorStatus == KenshiLib::SUCCESS &&
        worldResetStatus == KenshiLib::SUCCESS;
    if (!g_shopTraderRegistryReady)
    {
        ErrorLog(
            "KenshiAgentTelemetry: ShopTrader lifecycle hooks unavailable; "
            "exact session-scoped shop-owner telemetry disabled.");
    }

    DebugLog(
        "KenshiAgentTelemetry: Kenshi title/player/context telemetry hooks installed.");
    WriteStatus(
        "ready",
        g_shopTraderRegistryReady
            ? "Kenshi title/player/context telemetry and session-scoped ShopTrader lifecycle hooks installed."
            : "Kenshi title/player/context telemetry installed; exact session-scoped ShopTrader registry unavailable.");
}
