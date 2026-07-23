#include <Debug.h>
#include <core/Functions.h>
#include <kenshi/Character.h>
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
#include <kenshi/gui/ToolTip.h>
#include <kenshi/util/UtilityT.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>

#include <cctype>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>
#include <string>

#include "AtomicJsonWriter.h"

namespace
{
    const DWORD SNAPSHOT_INTERVAL_MS = 500;
    const unsigned int MAX_TRACKED_SHOP_TRADERS = 256;
    const float NEARBY_CHARACTER_RADIUS = 400.0f;
    const int MAX_NEARBY_CHARACTERS = 64;
    const unsigned int MAX_NATIVE_COMMAND_BYTES = 16384;
    const unsigned int MAX_NATIVE_ACKNOWLEDGEMENTS = 16;
    const wchar_t* NATIVE_COMMAND_REQUEST_FILE_W =
        L"native_command.request.json";
    const char* PROTOCOL_VERSION = "0.4.0";

    typedef void (*PlayerInterfaceUpdateFunction)(PlayerInterface*);
    typedef void (*GameWorldResetFunction)(GameWorld*);
    typedef ShopTrader* (*ShopTraderConstructorFunction)(ShopTrader*, Character*);
    typedef void (*ShopTraderDestructorFunction)(ShopTrader*);

    struct TrackedShopTrader
    {
        ShopTrader* object;
        Character* owner;
    };

    struct NativeCommandRequest
    {
        std::string commandId;
        std::string command;
        std::string controlMode;
        std::string identitySessionId;
        unsigned long long basedOnTelemetrySequence;
        std::string selectedCharacterId;
        std::string targetId;
    };

    struct NativeCommandAcknowledgement
    {
        std::string commandId;
        std::string command;
        std::string status;
        std::string reason;
        std::string targetId;
        std::string selectedCharacterId;
        unsigned long long basedOnTelemetrySequence;
        unsigned long long acknowledgedAtTelemetrySequence;
        unsigned long long acceptedAtTelemetrySequence;
        unsigned long long terminalAtTelemetrySequence;
        bool hasAcceptedSequence;
        bool hasTerminalSequence;
    };

    struct ActiveNativeCommand
    {
        bool active;
        std::string commandId;
        std::string targetId;
        std::string selectedCharacterId;
        hand targetHandle;
        hand selectedHandle;
    };

    PlayerInterfaceUpdateFunction g_originalPlayerInterfaceUpdate = NULL;
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

    bool IsValidCommandId(const std::string& value)
    {
        if (value.size() != 36 || value.compare(0, 4, "cmd-") != 0)
            return false;
        for (size_t index = 4; index < value.size(); ++index)
        {
            const unsigned char character =
                static_cast<unsigned char>(value[index]);
            if (!std::isdigit(character) &&
                !(character >= 'a' && character <= 'f'))
            {
                return false;
            }
        }
        return true;
    }

    bool HasOnlyKeys(
        const boost::property_tree::ptree& tree,
        const char* const* allowed,
        unsigned int allowedCount)
    {
        unsigned int count = 0;
        for (boost::property_tree::ptree::const_iterator it = tree.begin();
             it != tree.end();
             ++it)
        {
            bool found = false;
            for (unsigned int index = 0; index < allowedCount; ++index)
            {
                if (it->first == allowed[index])
                {
                    found = true;
                    break;
                }
            }
            if (!found)
                return false;
            ++count;
        }
        return count == allowedCount;
    }

    bool IsLeaf(const boost::property_tree::ptree& tree)
    {
        return tree.empty() && !tree.data().empty();
    }

    bool ParseNativeCommandRequest(
        const std::string& payload,
        NativeCommandRequest& request,
        std::string& rejectionReason)
    {
        static const char* const rootKeys[] = {
            "schema_version",
            "command_id",
            "command",
            "control_mode",
            "identity_session_id",
            "based_on_revision",
            "selected_character_ids",
            "target_id"
        };
        static const char* const revisionKeys[] = {
            "telemetry_sequence",
            "frame_sequence",
            "capability_epoch",
            "observed_at_monotonic"
        };

        request.basedOnTelemetrySequence = 0;
        request.selectedCharacterId.clear();
        try
        {
            std::istringstream input(payload);
            boost::property_tree::ptree root;
            boost::property_tree::read_json(input, root);

            request.commandId = root.get<std::string>("command_id", "");
            request.command = root.get<std::string>("command", "");
            request.controlMode = root.get<std::string>("control_mode", "");
            request.identitySessionId =
                root.get<std::string>("identity_session_id", "");
            request.targetId = root.get<std::string>("target_id", "");
            request.basedOnTelemetrySequence =
                root.get<unsigned long long>(
                    "based_on_revision.telemetry_sequence",
                    0);
            const boost::property_tree::ptree& selectedIds =
                root.get_child("selected_character_ids");
            if (selectedIds.size() == 1)
            {
                boost::property_tree::ptree::const_iterator selected =
                    selectedIds.begin();
                if (selected->first.empty() && IsLeaf(selected->second))
                    request.selectedCharacterId = selected->second.data();
            }

            if (!HasOnlyKeys(root, rootKeys, 8))
            {
                rejectionReason = "malformed_request";
                return false;
            }
            if (root.get<std::string>("schema_version") != "1.0" ||
                !IsValidCommandId(request.commandId) ||
                request.command.empty() ||
                request.command.size() > 80 ||
                request.controlMode.empty() ||
                request.controlMode.size() > 80 ||
                request.identitySessionId.empty() ||
                request.identitySessionId.size() > 200 ||
                request.targetId.empty() ||
                request.targetId.size() > 200)
            {
                rejectionReason = "malformed_request";
                return false;
            }

            const boost::property_tree::ptree& revision =
                root.get_child("based_on_revision");
            if (!HasOnlyKeys(revision, revisionKeys, 4))
            {
                rejectionReason = "malformed_request";
                return false;
            }
            request.basedOnTelemetrySequence =
                revision.get<unsigned long long>("telemetry_sequence");
            revision.get<unsigned int>("capability_epoch");
            revision.get<double>("observed_at_monotonic");
            const boost::property_tree::ptree& frameSequence =
                revision.get_child("frame_sequence");
            if (!IsLeaf(frameSequence))
            {
                rejectionReason = "malformed_request";
                return false;
            }

            if (selectedIds.size() != 1)
            {
                rejectionReason = "malformed_request";
                return false;
            }
            boost::property_tree::ptree::const_iterator selected =
                selectedIds.begin();
            if (!selected->first.empty() ||
                !IsLeaf(selected->second) ||
                selected->second.data().empty() ||
                selected->second.data().size() > 200)
            {
                rejectionReason = "malformed_request";
                return false;
            }
            request.selectedCharacterId = selected->second.data();
        }
        catch (const std::exception&)
        {
            rejectionReason = "malformed_request";
            return false;
        }
        return true;
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

    bool IsConfirmedVendor(Character* selected, Character* target)
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

        ActivePlatoon* platoon = target->getPlatoon();
        return platoon != NULL &&
               platoon->getHasVendorList() &&
               platoon->getSquadLeader() == target &&
               target->hasDialogue();
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

    Character* FindExactConfirmedVendor(
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
            return IsConfirmedVendor(selected, candidate) ? candidate : NULL;
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

    void MonitorActiveNativeCommand(PlayerInterface* player)
    {
        if (!g_activeNativeCommand.active)
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
        if (!IsConfirmedVendor(selected, target))
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
            if (IsValidCommandId(request.commandId) &&
                request.command == "approach_confirmed_vendor" &&
                !request.targetId.empty() &&
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
        if (request.command != "approach_confirmed_vendor")
        {
            // The telemetry acknowledgement schema is intentionally limited
            // to the one reviewed command. Do not publish an unparseable ack.
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

        bool exactIdentityFound = false;
        Character* target = FindExactConfirmedVendor(
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

        const hand& targetHandle = target->getHandle();
        Building* destinationIndoors = target->isIndoors().getBuilding();
        player->newPlayerTaskSelectedCharacters(
            PLAYER_TALK_TO,
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
             << "\"ui.inventory\",\"ui.dialogue\","
             << "\"ui.dialogue.target\",\"ui.dialogue.options\","
             << "\"ui.tooltip\","
             << "\"nearby.characters\",\"nearby.roles\","
             << "\"control.approach_vendor\","
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
        const bool tradeOpen =
            gui != NULL &&
            (gui->inventoryWindowTrader.getCharacter() != NULL ||
             gui->tradeA.getCharacter() != NULL ||
             gui->tradeB.getCharacter() != NULL);
        ToolTip* tooltip = gui != NULL ? gui->getToolTip() : NULL;
        const bool tooltipVisible =
            tooltip != NULL && tooltip->getVisible();

        json << "\"ui\":{";
        json << "\"active_screen\":\""
             << (dialogueOpen ? "dialogue" : (tradeOpen ? "trade" : (inventoryOpen ? "inventory" : "world")))
             << "\",";
        json << "\"modal_open\":" << JsonBool(dialogueOpen || inventoryOpen) << ",";
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
            json << "{";
            json << "\"command_id\":\""
                 << JsonEscape(acknowledgement.commandId) << "\",";
            json << "\"command\":\""
                 << JsonEscape(acknowledgement.command) << "\",";
            json << "\"status\":\""
                 << JsonEscape(acknowledgement.status) << "\",";
            json << "\"reason\":\""
                 << JsonEscape(acknowledgement.reason) << "\",";
            json << "\"target_id\":\""
                 << JsonEscape(acknowledgement.targetId) << "\",";
            json << "\"selected_character_ids\":[\""
                 << JsonEscape(acknowledgement.selectedCharacterId)
                 << "\"],";
            json << "\"based_on_telemetry_sequence\":"
                 << acknowledgement.basedOnTelemetrySequence << ",";
            json << "\"acknowledged_at_telemetry_sequence\":"
                 << acknowledgement.acknowledgedAtTelemetrySequence;
            if (acknowledgement.hasAcceptedSequence)
            {
                json << ",\"accepted_at_telemetry_sequence\":"
                     << acknowledgement.acceptedAtTelemetrySequence;
            }
            if (acknowledgement.hasTerminalSequence)
            {
                json << ",\"terminal_at_telemetry_sequence\":"
                     << acknowledgement.terminalAtTelemetrySequence;
            }
            json << "}";
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
                json << "\"food_items\":" << character->getNumFoodItems();
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
             << "\"Partial telemetry only: hunger, wounds, getting-eaten state, inventory "
             << "detail, and click-target occlusion are not yet exported. A visible nearby "
             << "entity is rendered inside the current viewport, but geometry can still "
             << "occlude it or intercept a click.\"";
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

    void Sample(PlayerInterface* player)
    {
        if (g_sampling)
            return;
        g_sampling = true;
        std::string error;
        const std::string snapshot = BuildSnapshot(player);
        if (!KenshiAgentTelemetry::AtomicWriteUtf8(
                g_outputDirectory,
                L"telemetry.latest.json",
                snapshot,
                error))
        {
            ErrorLog(std::string("KenshiAgentTelemetry write failed: ") + error);
        }
        g_sampling = false;
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
        if (now - g_lastSnapshotTick >= SNAPSHOT_INTERVAL_MS)
        {
            g_lastSnapshotTick = now;
            Sample(player);
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
        "Installing telemetry and ShopTrader lifecycle hooks.");

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

    DebugLog("KenshiAgentTelemetry: telemetry hook installed.");
    WriteStatus(
        "ready",
        g_shopTraderRegistryReady
            ? "Telemetry and session-scoped ShopTrader lifecycle hooks installed."
            : "Telemetry hook installed; exact session-scoped ShopTrader registry unavailable.");
}
