#include <Debug.h>
#include <core/Functions.h>
#include <kenshi/Character.h>
#include <kenshi/Item.h>
#include <kenshi/Inventory.h>
#include <kenshi/MedicalSystem.h>
#include <kenshi/CameraClass.h>
#include <kenshi/Dialogue.h>
#include <kenshi/Faction.h>
#include <kenshi/GameWorld.h>
#include <kenshi/Globals.h>
#include <kenshi/Platoon.h>
#include <kenshi/PlayerInterface.h>
#include <kenshi/RootObject.h>
#include <kenshi/ShopTrader.h>
#include <kenshi/gui/DialogueWindow.h>
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

#include <cmath>
#include <exception>
#include <iomanip>
#include <locale>
#include <sstream>
#include <string>

#include "AtomicJsonWriter.h"
#include "NativeCommandProtocol.h"
#include "NativeCommandTiming.h"

namespace
{
    using KenshiAgentTelemetry::IsValidCommandId;
    using KenshiAgentTelemetry::NativeCommandAcknowledgement;
    using KenshiAgentTelemetry::NativeCommandRequest;
    using KenshiAgentTelemetry::ParseNativeCommandRequest;
    using KenshiAgentTelemetry::SerializeNativeCommandAcknowledgement;

    const unsigned int MAX_TRACKED_SHOP_TRADERS = 256;
    const float NEARBY_CHARACTER_RADIUS = 400.0f;
    const int MAX_NEARBY_CHARACTERS = 64;
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
    const wchar_t* NATIVE_COMMAND_REQUEST_FILE_W =
        L"native_command.request.json";
    const char* PROTOCOL_VERSION = "0.6.1";

    typedef void (*PlayerInterfaceUpdateFunction)(PlayerInterface*);
    typedef void (*TitleScreenUpdateFunction)(TitleScreen*);
    typedef void (*GameWorldResetFunction)(GameWorld*);
    typedef ShopTrader* (*ShopTraderConstructorFunction)(ShopTrader*, Character*);
    typedef void (*ShopTraderDestructorFunction)(ShopTrader*);

    struct TrackedShopTrader
    {
        ShopTrader* object;
        Character* owner;
    };

    struct ActiveNativeCommand
    {
        bool active;
        std::string commandId;
        std::string targetId;
        std::string selectedCharacterId;
        hand targetHandle;
        hand selectedHandle;
        // A walk has no conversation to open, so it cannot be judged finished
        // the way an approach is. It finishes by arriving.
        bool isWalk;
        bool hasFixedDestination;
        // One uninterrupted pause may mean an abandoned movement order, but
        // short paused gaps are how the stop-motion controller safely pulses.
        KenshiAgentTelemetry::NativeMovementPauseWindow pauseWindow;
        float destinationX;
        float destinationZ;
    };

    // How close counts as arrived. Kenshi stops a walk short of the exact point
    // whenever anything is in the way, so an exact match would never fire.
    const float WALK_ARRIVAL_TOLERANCE = 12.0f;

    PlayerInterfaceUpdateFunction g_originalPlayerInterfaceUpdate = NULL;
    TitleScreenUpdateFunction g_originalTitleScreenUpdate = NULL;
    GameWorldResetFunction g_originalGameWorldReset = NULL;
    ShopTraderConstructorFunction g_originalShopTraderConstructor = NULL;
    ShopTraderDestructorFunction g_originalShopTraderDestructor = NULL;
    TrackedShopTrader g_trackedShopTraders[MAX_TRACKED_SHOP_TRADERS];
    unsigned int g_trackedShopTraderCount = 0;
    bool g_shopTraderRegistryReady = false;
    bool g_shopTraderRegistryOverflow = false;
    unsigned long long g_sequence = 0;
    DWORD g_lastSnapshotTick = 0;
    bool g_sampling = false;
    bool g_approachVendorHotkeyWasDown = false;
    unsigned long long g_processGeneration = 0;
    unsigned long long g_sessionGeneration = 0;
    unsigned long long g_nativeCommandSequence = 0;
    std::string g_lastNativeCommand;
    std::string g_lastNativeCommandResult;
    std::string g_lastNativeCommandTarget;
    std::string g_lastNativeCommandTargetId;
    NativeCommandAcknowledgement
        g_nativeAcknowledgements[MAX_NATIVE_ACKNOWLEDGEMENTS];
    unsigned int g_nativeAcknowledgementCount = 0;
    ActiveNativeCommand g_activeNativeCommand;
    std::wstring g_outputDirectory;

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
        ++g_sessionGeneration;
        if (g_sessionGeneration == 0)
            g_sessionGeneration = 1;
        g_trackedShopTraderCount = 0;
        g_shopTraderRegistryOverflow = false;
        g_approachVendorHotkeyWasDown = false;
        g_nativeCommandSequence = 0;
        g_lastNativeCommand.clear();
        g_lastNativeCommandResult.clear();
        g_lastNativeCommandTarget.clear();
        g_lastNativeCommandTargetId.clear();
        g_nativeAcknowledgementCount = 0;
        g_activeNativeCommand.active = false;
        g_activeNativeCommand.commandId.clear();
        g_activeNativeCommand.targetId.clear();
        g_activeNativeCommand.selectedCharacterId.clear();
        // Cleared with the rest, or a finished walk would leave the next
        // approach being judged by arrival instead of by dialogue.
        g_activeNativeCommand.isWalk = false;
        g_activeNativeCommand.hasFixedDestination = false;
        g_activeNativeCommand.destinationX = 0.0f;
        g_activeNativeCommand.destinationZ = 0.0f;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
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

        // This deliberately encodes Kenshi's validated handle generations, not
        // an address. Consumers must treat the complete value as opaque.
        std::ostringstream value;
        value << "entity-"
              << std::hex << std::setfill('0')
              << std::setw(16) << g_processGeneration
              << "-" << std::setw(16) << g_sessionGeneration
              << "-" << std::setw(8) << static_cast<unsigned int>(handle.type)
              << "-" << std::setw(8) << handle.container
              << "-" << std::setw(8) << handle.containerSerial
              << "-" << std::setw(8) << handle.index
              << "-" << std::setw(8) << handle.serial;
        return value.str();
    }

    std::string StableEntityId(Character* character)
    {
        if (character == NULL || !character->isValid())
            return "";
        return StableEntityId(character->getHandle());
    }

    bool SameHandleIdentity(const hand& left, const hand& right)
    {
        return left.type == right.type &&
               left.container == right.container &&
               left.containerSerial == right.containerSerial &&
               left.index == right.index &&
               left.serial == right.serial;
    }

    bool IsSelected(PlayerInterface* player, const hand& handle)
    {
        if (player == NULL)
            return false;
        for (ogre_unordered_set<hand>::type::const_iterator it =
                 player->selectedCharacters.begin();
             it != player->selectedCharacters.end();
             ++it)
        {
            if (SameHandleIdentity(*it, handle))
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
        acknowledgement.bearingDegrees = request.bearingDegrees;
        acknowledgement.distanceUnits = request.distanceUnits;
        acknowledgement.selectedCharacterId =
            request.selectedCharacterId;
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
        g_activeNativeCommand.isWalk = false;
        g_activeNativeCommand.hasFixedDestination = false;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
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
            !SameHandleIdentity(*it, player->selectedCharacter))
        {
            return false;
        }
        selectedId = StableEntityId(*it);
        if (selectedId.empty())
            return false;
        selectedHandle = *it;
        return true;
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
        targetId = StableEntityId(conversationTarget);
        return !targetId.empty();
    }

    void AppendDialogueOptions(std::ostringstream& json)
    {
        if (gui == NULL ||
            gui->dialogue == NULL ||
            !gui->dialogue->isVisible())
        {
            json << "null";
            return;
        }

        json << "[";
        const Ogre::FastArray<MyGUI::EditBox*>& replyTexts =
            gui->dialogue->replyTexts;
        for (size_t index = 0; index < replyTexts.size(); ++index)
        {
            if (index > 0)
                json << ",";
            MyGUI::EditBox* reply = replyTexts[index];
            const std::string caption =
                reply != NULL ? reply->getCaption().asUTF8() : std::string();
            json << "\"" << JsonEscape(caption) << "\"";
        }
        json << "]";
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
    void AppendItemFacts(std::ostringstream& json, Item* item)
    {
        json << "\"item_name\":\"" << JsonEscape(item->getName()) << "\",";
        json << "\"item_value\":" << item->getValueSingle(true) << ",";
        json << "\"item_quantity\":" << item->quantity << ",";
        json << "\"item_type\":" << static_cast<int>(item->getItemType()) << ",";
    }

    // Inventory and shop cells, named. Walking the MyGUI tree can only report
    // that *a* cell exists at some bounds, which left the agent hovering cells
    // one at a time to discover what each held - a model call per cell, while a
    // human simply sees the bread. The icons themselves know their Item, so
    // walk the inventory structure instead and say what is actually there.
    void AppendNamedItemCells(std::ostringstream& json, bool& first, unsigned int& appended)
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
        UIControlPass pass)
    {
        if (widget == NULL ||
            depth > MAX_UI_WIDGET_DEPTH ||
            visited >= MAX_VISITED_UI_WIDGETS ||
            appended >= MAX_VISIBLE_UI_CONTROLS)
        {
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
                pass);
        }
    }

    void AppendVisibleUIControls(std::ostringstream& json, bool includeItemCells)
    {
        MyGUI::Gui* myGui = MyGUI::Gui::getInstancePtr();
        MyGUI::RenderManager* renderManager =
            MyGUI::RenderManager::getInstancePtr();
        if (myGui == NULL || renderManager == NULL)
        {
            json << "null";
            return;
        }
        const MyGUI::IntSize view = renderManager->getViewSize();
        if (view.width <= 0 || view.height <= 0)
        {
            json << "null";
            return;
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
                    AppendNamedItemCells(json, first, appended);
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
                    passes[index]);
            }
        }
        json << "]";
    }

    void MonitorActiveNativeCommand(PlayerInterface* player)
    {
        if (!g_activeNativeCommand.active)
            return;

        if (KenshiAgentTelemetry::ObserveNativeMovementPause(
                g_activeNativeCommand.pauseWindow,
                ou != NULL && ou->isPaused(),
                GetTickCount()))
        {
            FinishActiveNativeCommand("cancelled", "world_paused");
            return;
        }
        if (ou != NULL && ou->isPaused())
            return;

        std::string selectedId;
        hand selectedHandle;
        if (!TryGetExactSelection(player, selectedId, selectedHandle) ||
            selectedId != g_activeNativeCommand.selectedCharacterId ||
            !SameHandleIdentity(
                selectedHandle,
                g_activeNativeCommand.selectedHandle))
        {
            FinishActiveNativeCommand("cancelled", "selection_mismatch");
            return;
        }

        Character* walker = selectedHandle.getCharacter();
        if (g_activeNativeCommand.isWalk)
        {
            if (walker == NULL || !walker->isValid())
            {
                FinishActiveNativeCommand("cancelled", "selection_mismatch");
                return;
            }
            float destinationX = g_activeNativeCommand.destinationX;
            float destinationZ = g_activeNativeCommand.destinationZ;
            if (!g_activeNativeCommand.hasFixedDestination)
            {
                // Walking to somebody who is walking themselves: aim at where
                // they are now, not where they were when the order was given.
                Character* follow =
                    g_activeNativeCommand.targetHandle.getCharacter();
                if (follow == NULL ||
                    !follow->isValid() ||
                    StableEntityId(follow) != g_activeNativeCommand.targetId)
                {
                    FinishActiveNativeCommand("cancelled", "target_lifetime_changed");
                    return;
                }
                const Ogre::Vector3 followPosition = follow->getPosition();
                destinationX = followPosition.x;
                destinationZ = followPosition.z;
            }
            const Ogre::Vector3 here = walker->getPosition();
            const float dx = here.x - destinationX;
            const float dz = here.z - destinationZ;
            if (dx * dx + dz * dz <=
                WALK_ARRIVAL_TOLERANCE * WALK_ARRIVAL_TOLERANCE)
            {
                FinishActiveNativeCommand("completed", "walk_destination_reached");
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
            const bool hasCommandIdentity =
                isDirection
                    ? (request.targetId.empty() &&
                       request.bearingDegrees >= 0.0 &&
                       request.bearingDegrees < 360.0 &&
                       request.distanceUnits > 0.0 &&
                       request.distanceUnits <= 2000.0)
                    : (!request.targetId.empty() &&
                       request.bearingDegrees == 0.0 &&
                       request.distanceUnits == 0.0);
            if (IsValidCommandId(request.commandId) &&
                (request.command == "approach_confirmed_vendor" ||
                 request.command == "move_to_character" ||
                 request.command == "move_in_direction") &&
                hasCommandIdentity &&
                !request.selectedCharacterId.empty() &&
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
        const bool isDirection = request.command == "move_in_direction";
        if (isApproach || isMove || isDirection)
            g_lastNativeCommand = request.command;
        if (!isApproach && !isMove && !isDirection)
        {
            // The telemetry acknowledgement schema is intentionally limited
            // to reviewed commands. Do not publish an unparseable ack.
            g_lastNativeCommandResult = "unsupported_command";
            return;
        }
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
        // based_on_revision.telemetry_sequence is an exact issue-time fence,
        // not a minimum. A newer snapshot requires a newly planned command.
        if (request.basedOnTelemetrySequence != g_sequence)
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

        std::string selectedId;
        hand selectedHandle;
        if (!TryGetExactSelection(player, selectedId, selectedHandle) ||
            selectedId != request.selectedCharacterId)
        {
            RejectNativeCommand(request, "selection_mismatch");
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
            Ogre::Vector3 destination = walker->getPosition();
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
            KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
                g_activeNativeCommand.pauseWindow);
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
        Character* target = isMove
            ? FindExactNearbyCharacter(player, request.targetId, exactIdentityFound)
            : FindExactDialogueTarget(player, request.targetId, exactIdentityFound);
        if (target == NULL)
        {
            RejectNativeCommand(
                request,
                exactIdentityFound
                    ? "target_role_invalid"
                    : "target_lifetime_changed");
            return;
        }

        const hand& targetHandle = target->getHandle();
        Building* destinationIndoors = target->isIndoors().getBuilding();
        // The same order a player issues by right-clicking: walk there, and
        // enter the destination's building if it is inside one. Unlike
        // PLAYER_TALK_TO it opens no conversation on arrival, which is what
        // makes it usable for going somewhere rather than talking to someone.
        player->newPlayerTaskSelectedCharacters(
            isMove ? MOVE_CUS_ORDERED : PLAYER_TALK_TO,
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
            request.selectedCharacterId;
        g_activeNativeCommand.targetHandle = targetHandle;
        g_activeNativeCommand.selectedHandle = selectedHandle;
        g_activeNativeCommand.isWalk = isMove;
        g_activeNativeCommand.hasFixedDestination = false;
        KenshiAgentTelemetry::ResetNativeMovementPauseWindow(
            g_activeNativeCommand.pauseWindow);
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

        int money = 0;
        if (selected != NULL)
            money = selected->getMoney();
        else if (characters != NULL && characters->size() > 0 && (*characters)[0] != NULL)
            money = (*characters)[0]->getMoney();

        json << "{";
        json << "\"protocol_version\":\"" << PROTOCOL_VERSION << "\",";
        json << "\"sequence\":" << ++g_sequence << ",";
        json << "\"captured_at\":\"" << UtcNowIso8601() << "\",";
        json << "\"source\":\"kenshilib-plugin\",";
        json << "\"identity_session_id\":\""
             << IdentitySessionId() << "\",";
        json << "\"capabilities\":["
             << "\"game.pause\",\"game.speed\",\"game.money\",\"game.time\","
             << "\"camera.position\",\"squad.basic\","
             // Now genuinely emitted, so advertise them: a capability the
             // agent cannot rely on is worse than one it knows is absent.
             << "\"squad.hunger\",\"squad.health\",\"squad.inventory\","
             << "\"ui.inventory\",\"ui.dialogue\","
             << "\"ui.dialogue.target\",\"ui.dialogue.options\","
             << "\"ui.tooltip\",\"ui.visible_controls\","
             << "\"nearby.characters\",\"nearby.roles\","
             << "\"control.approach_vendor\","
             << "\"control.move_to_character\","
             << "\"control.move_in_direction\","
             << "\"identity.stable_handles\"";
        if (g_shopTraderRegistryReady)
            json << ",\"nearby.shop_owners\"";
        json << "],";

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
        // The trade handles are not cleared when the window closes, so testing
        // them alone reported `trade` indefinitely after a single trade - the
        // agent could never observe leaving the shop. Trade is a trading
        // *window* being open, so require an actually open inventory window too.
        const bool tradeOpen =
            gui != NULL &&
            inventoryOpen &&
            (gui->inventoryWindowTrader.getCharacter() != NULL ||
             gui->tradeA.getCharacter() != NULL ||
             gui->tradeB.getCharacter() != NULL);
        const bool statsWindowOpen = gui != NULL && gui->characterStatsWindowVisible();
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
        ToolTip* tooltip = gui != NULL ? gui->getToolTip() : NULL;
        const bool tooltipVisible =
            tooltip != NULL && tooltip->getVisible();

        json << "\"ui\":{";
        json << "\"active_screen\":\""
             << (dialogueOpen ? "dialogue" : (tradeOpen ? "trade" : (inventoryOpen ? "inventory" : "world")))
             << "\",";
        json << "\"modal_open\":" << JsonBool(dialogueOpen || inventoryOpen) << ",";
        json << "\"stats_window_open\":" << JsonBool(statsWindowOpen) << ",";
        json << "\"management_screen_open\":" << JsonBool(managementOpen) << ",";
        json << "\"management_tab\":" << managementTab << ",";
        json << "\"open_inventory_windows\":" << openInventoryWindows << ",";
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
        json << "\"tooltip_text\":";
        if (tooltipVisible)
            json << "\"" << JsonEscape(CurrentToolTipText(tooltip)) << "\"";
        else
            json << "null";
        json << ",";
        json << "\"tooltip_source_bounds\":";
        if (!tooltipVisible || !AppendToolTipSourceBounds(json, tooltip))
            json << "null";
        json << ",";
        json << "\"visible_controls\":";
        AppendVisibleUIControls(json, inventoryOpen || tradeOpen);
        json << ",";
        const std::string selectedId = StableEntityId(selected);
        if (!selectedId.empty() &&
            IsSelected(player, selected->getHandle()))
        {
            json << "\"selected_character_id\":\""
                 << selectedId << "\",";
        }
        json << "\"selected_character_ids\":[";
        if (player != NULL)
        {
            bool firstSelected = true;
            for (ogre_unordered_set<hand>::type::const_iterator it =
                     player->selectedCharacters.begin();
                 it != player->selectedCharacters.end();
                 ++it)
            {
                Character* selectedCharacter = it->getCharacter();
                const std::string id = StableEntityId(selectedCharacter);
                if (id.empty() || !selectedCharacter->isPlayerCharacter())
                    continue;
                if (!firstSelected)
                    json << ",";
                firstSelected = false;
                json << "\"" << id << "\"";
            }
        }
        json << "]";
        json << "},";

        json << "\"active_shop_trader_count\":";
        if (g_shopTraderRegistryReady)
            json << g_trackedShopTraderCount;
        else
            json << "null";
        json << ",";

        json << "\"native_control\":{";
        json << "\"available\":true,";
        if (g_activeNativeCommand.active)
        {
            json << "\"active_command_id\":\""
                 << JsonEscape(g_activeNativeCommand.commandId)
                 << "\",";
        }
        json << "\"acknowledgements\":[";
        for (unsigned int index = 0;
             index < g_nativeAcknowledgementCount;
             ++index)
        {
            if (index > 0)
                json << ",";
            const NativeCommandAcknowledgement& acknowledgement =
                g_nativeAcknowledgements[index];
            json << SerializeNativeCommandAcknowledgement(acknowledgement);
        }
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

        json << "\"squad\":[";
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
                json << "\"selected\":"
                     << JsonBool(IsSelected(player, character->getHandle())) << ",";
                json << "\"alive\":" << JsonBool(!character->isDestroyed()) << ",";
                json << "\"conscious\":" << JsonBool(!character->isUnconcious()) << ",";
                json << "\"down\":" << JsonBool(character->isDown()) << ",";
                json << "\"crippled\":" << JsonBool(character->isCrippled()) << ",";
                json << "\"position\":";
                AppendVector3(json, position);
                json << ",\"movement_speed\":" << character->getMovementSpeed() << ",";
                json << "\"food_items\":" << character->getNumFoodItems() << ",";
                // Whether anyone is currently fighting this character. The
                // field existed in the schema and was never filled, so it read
                // None forever: an agent could be beaten unconscious without a
                // single observation saying a fight had started.
                json << "\"in_combat\":"
                     << JsonBool(character->isInCombatMode(true, true)) << ",";
                // The agent set itself the goal of feeding this character while
                // unable to read whether it was hungry. Hunger and blood are
                // the two numbers the survival loop actually turns on.
                MedicalSystem* medical = character->getMedical();
                if (medical != NULL)
                {
                    json << "\"hunger\":" << medical->hunger << ",";
                    json << "\"blood\":" << medical->blood << ",";
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
                json << "]";
                json << "}";
            }
        }
        json << "],";
        json << "\"nearby_entities\":[";
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
                float talkTaskProbability = 0.0f;
                const bool talkTaskAvailable =
                    player->getPlayerTaskProbability(
                        PLAYER_TALK_TO,
                        target,
                        talkTaskProbability);
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
                json << "\"talk_task_available\":"
                     << JsonBool(talkTaskAvailable) << ",";
                json << "\"talk_task_probability\":"
                     << talkTaskProbability << ",";
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
                json << "}";
            }
        }
        json << "],";
        json << "\"warnings\":["
             << "\"Partial telemetry only: body-part wounds, bleeding rate, "
             << "getting-eaten state, imprisonment/enslavement, current tasks, "
             << "location name, distant world state, and click-target occlusion "
             << "are not exported or validated. "
             << "The food_items scalar is not authoritative over the named inventory list. "
             << "A visible nearby entity is rendered inside the current viewport, but "
             << "geometry can still occlude it or intercept a click.\"";
        if (g_shopTraderRegistryOverflow)
        {
            json << ",\"The live ShopTrader registry exceeded its bounded capacity; "
                 << "shop_inventory_owner is incomplete.\"";
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
        json << "\"capabilities\":[\"ui.visible_controls\"],";
        json << "\"game\":{\"loaded\":false},";
        json << "\"ui\":{";
        json << "\"active_screen\":\"title\",";
        json << "\"visible_controls\":";
        AppendVisibleUIControls(json, false);
        json << "},";
        json << "\"native_control\":{";
        json << "\"available\":false,";
        json << "\"acknowledgements\":[],";
        json << "\"last_command_sequence\":0";
        json << "},";
        json << "\"squad\":[],";
        json << "\"active_shop_trader_count\":null,";
        json << "\"nearby_entities\":[],";
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

    void PlayerInterfaceUpdateHook(PlayerInterface* player)
    {
        g_originalPlayerInterfaceUpdate(player);
        MonitorActiveNativeCommand(player);
        const bool approachVendorHotkeyDown =
            (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0 &&
            (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0 &&
            (GetAsyncKeyState(VK_F10) & 0x8000) != 0;
        if (approachVendorHotkeyDown && !g_approachVendorHotkeyWasDown)
            ProcessNativeCommandRequest(player);
        g_approachVendorHotkeyWasDown = approachVendorHotkeyDown;

        const DWORD now = GetTickCount();
        if (ou != NULL &&
            ou->initialized &&
            player != NULL &&
            now - g_lastSnapshotTick >=
                KenshiAgentTelemetry::TELEMETRY_SNAPSHOT_INTERVAL_MS)
        {
            g_lastSnapshotTick = now;
            Sample(player, false);
        }
    }

    void TitleScreenUpdateHook(TitleScreen* titleScreen)
    {
        g_originalTitleScreenUpdate(titleScreen);
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
        "Installing Kenshi title/player telemetry and ShopTrader lifecycle hooks.");

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
        "KenshiAgentTelemetry: Kenshi title/player telemetry hooks installed.");
    WriteStatus(
        "ready",
        g_shopTraderRegistryReady
            ? "Kenshi title/player telemetry and session-scoped ShopTrader lifecycle hooks installed."
            : "Kenshi title/player telemetry installed; exact session-scoped ShopTrader registry unavailable.");
}
