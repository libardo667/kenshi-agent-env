#include <Debug.h>
#include <core/Functions.h>
#include <kenshi/Character.h>
#include <kenshi/GameWorld.h>
#include <kenshi/Globals.h>
#include <kenshi/PlayerInterface.h>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <Windows.h>

#include <iomanip>
#include <locale>
#include <sstream>
#include <string>

#include "AtomicJsonWriter.h"

namespace
{
    const DWORD SNAPSHOT_INTERVAL_MS = 500;
    const char* PROTOCOL_VERSION = "0.1.0";

    typedef void (*PlayerInterfaceUpdateFunction)(PlayerInterface*);
    PlayerInterfaceUpdateFunction g_originalPlayerInterfaceUpdate = NULL;
    unsigned long long g_sequence = 0;
    DWORD g_lastSnapshotTick = 0;
    bool g_sampling = false;
    std::wstring g_outputDirectory;

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
        json << "\"capabilities\":["
             << "\"game.pause\",\"game.speed\",\"game.money\","
             << "\"camera.position\",\"squad.basic\"],";

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

        json << "\"ui\":{";
        if (selected != NULL && characters != NULL)
        {
            for (unsigned int index = 0; index < characters->size(); ++index)
            {
                if ((*characters)[index] == selected)
                {
                    json << "\"selected_character_id\":\"squad:" << index << "\"";
                    break;
                }
            }
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
                if (!first)
                    json << ",";
                first = false;
                const Ogre::Vector3 position = character->getPosition();
                json << "{";
                json << "\"id\":\"squad:" << index << "\",";
                json << "\"name\":\"" << JsonEscape(character->getName()) << "\",";
                json << "\"selected\":" << JsonBool(character == selected) << ",";
                json << "\"alive\":" << JsonBool(!character->isDestroyed()) << ",";
                json << "\"conscious\":" << JsonBool(!character->isUnconcious()) << ",";
                json << "\"down\":" << JsonBool(character->isDown()) << ",";
                json << "\"crippled\":" << JsonBool(character->isCrippled()) << ",";
                json << "\"getting_eaten\":" << JsonBool(character->isGettingEaten != 0) << ",";
                json << "\"position\":";
                AppendVector3(json, position);
                json << ",\"movement_speed\":" << character->getMovementSpeed() << ",";
                json << "\"food_items\":" << character->getNumFoodItems();
                json << "}";
            }
        }
        json << "],";
        json << "\"nearby_entities\":[],";
        json << "\"warnings\":["
             << "\"Partial telemetry only: hunger, wounds, inventory detail, UI modals, "
             << "and nearby entities are not yet exported.\""
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
    g_outputDirectory = KenshiAgentTelemetry::ResolveTelemetryDirectory();
    WriteStatus("starting", "Installing PlayerInterface::update telemetry hook.");

    const KenshiLib::HookStatus status = KenshiLib::AddHook(
        KenshiLib::GetRealAddress(&PlayerInterface::update),
        PlayerInterfaceUpdateHook,
        &g_originalPlayerInterfaceUpdate);

    if (status != KenshiLib::SUCCESS)
    {
        ErrorLog("KenshiAgentTelemetry: could not hook PlayerInterface::update.");
        WriteStatus("error", "Could not hook PlayerInterface::update.");
        return;
    }

    DebugLog("KenshiAgentTelemetry: telemetry hook installed.");
    WriteStatus("ready", "Telemetry hook installed. Waiting for game/UI updates.");
}
