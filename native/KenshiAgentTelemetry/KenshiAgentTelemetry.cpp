#include <Debug.h>
#include <core/Functions.h>
#include <kenshi/Character.h>
#include <kenshi/CameraClass.h>
#include <kenshi/Faction.h>
#include <kenshi/GameWorld.h>
#include <kenshi/Globals.h>
#include <kenshi/Platoon.h>
#include <kenshi/PlayerInterface.h>
#include <kenshi/RootObject.h>
#include <kenshi/ShopTrader.h>
#include <kenshi/gui/DialogueWindow.h>
#include <kenshi/gui/ForgottenGUI.h>
#include <kenshi/util/UtilityT.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

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
    const char* PROTOCOL_VERSION = "0.2.0";

    typedef void (*PlayerInterfaceUpdateFunction)(PlayerInterface*);
    typedef void (*GameWorldResetFunction)(GameWorld*);
    typedef ShopTrader* (*ShopTraderConstructorFunction)(ShopTrader*, Character*);
    typedef void (*ShopTraderDestructorFunction)(ShopTrader*);

    struct TrackedShopTrader
    {
        ShopTrader* object;
        Character* owner;
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

    Character* FindNearestConfirmedVendor(PlayerInterface* player)
    {
        Character* selected =
            player != NULL ? player->selectedCharacter.getCharacter() : NULL;
        if (ou == NULL || selected == NULL || !selected->isValid())
            return NULL;

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

        Character* nearest = NULL;
        float nearestDistance = 0.0f;
        for (lektor<RootObject*>::iterator it = nearbyCharacters.begin();
             it != nearbyCharacters.end();
             ++it)
        {
            Character* candidate = reinterpret_cast<Character*>(*it);
            if (!IsConfirmedVendor(selected, candidate))
                continue;
            const float candidateDistance =
                Distance(selectedPosition, candidate->getPosition());
            if (nearest == NULL || candidateDistance < nearestDistance)
            {
                nearest = candidate;
                nearestDistance = candidateDistance;
            }
        }
        return nearest;
    }

    void IssueApproachConfirmedVendor(PlayerInterface* player)
    {
        ++g_nativeCommandSequence;
        g_lastNativeCommand = "approach_confirmed_vendor";
        g_lastNativeCommandTarget.clear();
        g_lastNativeCommandTargetId.clear();

        Character* target = FindNearestConfirmedVendor(player);
        if (target == NULL)
        {
            g_lastNativeCommandResult = "no_confirmed_vendor";
            return;
        }

        const hand& targetHandle = target->getHandle();
        const std::string targetId = StableEntityId(targetHandle);
        if (targetId.empty())
        {
            g_lastNativeCommandResult = "invalid_target_handle";
            return;
        }
        Building* destinationIndoors = target->isIndoors().getBuilding();
        player->newPlayerTaskSelectedCharacters(
            PLAYER_TALK_TO,
            targetHandle,
            destinationIndoors,
            target->getPosition(),
            false);
        g_lastNativeCommandResult = "issued";
        g_lastNativeCommandTarget = target->getName();
        g_lastNativeCommandTargetId = targetId;
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
             << "\"game.pause\",\"game.speed\",\"game.money\","
             << "\"camera.position\",\"squad.basic\","
             << "\"ui.inventory\",\"ui.dialogue\","
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
        json << "\"money\":" << money;
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

        json << "\"ui\":{";
        json << "\"active_screen\":\""
             << (dialogueOpen ? "dialogue" : (tradeOpen ? "trade" : (inventoryOpen ? "inventory" : "world")))
             << "\",";
        json << "\"modal_open\":" << JsonBool(dialogueOpen || inventoryOpen) << ",";
        json << "\"dialogue_open\":" << JsonBool(dialogueOpen) << ",";
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
        const bool approachVendorHotkeyDown =
            (GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0 &&
            (GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0 &&
            (GetAsyncKeyState(VK_F10) & 0x8000) != 0;
        if (approachVendorHotkeyDown && !g_approachVendorHotkeyWasDown)
            IssueApproachConfirmedVendor(player);
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
