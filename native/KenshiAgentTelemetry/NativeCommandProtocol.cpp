#include "NativeCommandProtocol.h"

#include <boost/property_tree/json_parser.hpp>
#include <boost/property_tree/ptree.hpp>

#include <cctype>
#include <exception>
#include <iomanip>
#include <locale>
#include <set>
#include <sstream>

namespace
{
#define ARRAY_COUNT(a) (static_cast<unsigned int>(sizeof(a) / sizeof((a)[0])))

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

    std::string JsonEscape(const std::string& value)
    {
        std::ostringstream escaped;
        for (std::string::const_iterator it = value.begin();
             it != value.end();
             ++it)
        {
            const unsigned char character =
                static_cast<unsigned char>(*it);
            switch (character)
            {
            case '"':
                escaped << "\\\"";
                break;
            case '\\':
                escaped << "\\\\";
                break;
            case '\b':
                escaped << "\\b";
                break;
            case '\f':
                escaped << "\\f";
                break;
            case '\n':
                escaped << "\\n";
                break;
            case '\r':
                escaped << "\\r";
                break;
            case '\t':
                escaped << "\\t";
                break;
            default:
                if (character < 0x20)
                {
                    escaped << "\\u"
                            << std::hex
                            << std::setw(4)
                            << std::setfill('0')
                            << static_cast<unsigned int>(character)
                            << std::dec
                            << std::setfill(' ');
                }
                else
                {
                    escaped << static_cast<char>(character);
                }
                break;
            }
        }
        return escaped.str();
    }
}

namespace KenshiAgentTelemetry
{
    NativeCommandRequest::NativeCommandRequest()
        : basedOnTelemetrySequence(0),
          bearingDegrees(0.0),
          distanceUnits(0.0),
          minimumOutputQuantity(1),
          slotX(0),
          slotY(0)
    {
    }

    NativeCommandAcknowledgement::NativeCommandAcknowledgement()
        : bearingDegrees(0.0),
          distanceUnits(0.0),
          minimumOutputQuantity(1),
          slotX(0),
          slotY(0),
          basedOnTelemetrySequence(0),
          acknowledgedAtTelemetrySequence(0),
          acceptedAtTelemetrySequence(0),
          terminalAtTelemetrySequence(0),
          hasAcceptedSequence(false),
          hasTerminalSequence(false)
    {
    }

    bool IsKnownNativeCommand(const std::string& command)
    {
        return NativeCommandNamesTarget(command) ||
               NativeCommandCarriesDirection(command) ||
               command == "exit_current_building" ||
               command == "survey_local_resources";
    }

    bool NativeCommandCarriesDirection(const std::string& command)
    {
        return command == "move_in_direction";
    }

    bool NativeCommandNamesTarget(const std::string& command)
    {
        return command == "approach_confirmed_vendor" ||
               command == "move_to_character" ||
               // Diagnostic probe for the body-shift plan: moves one character
               // between the player's active platoon and the dead squad, which
               // is the same (faction, platoon) rewrite the engine performs on
               // every death. Proves whether that rewrite works on the living.
               command == "shift_body_platoon" ||
               command == "shift_into_body" ||
               command == "select_squad_member" ||
               command == "regroup_with_squad_member" ||
               command == "travel_to_map_destination" ||
               command == "perform_context_action" ||
               // One order, on one nearby person, named by Kenshi's own task
               // vocabulary. Deliberately not a verb per gameplay action: the
               // engine decides which orders a target affords, so attacking,
               // looting, and first aid arrive through the same command rather
               // than as three hand-written whitelists.
               command == "perform_character_order" ||
               command == "produce_resource_output" ||
               // One transfer between two open inventories, wherever they came
               // from. Kenshi adjudicates it and says why not, so looting,
               // buying, selling, giving and harvesting are one command rather
               // than five that each drive a mouse.
               command == "transfer_item" ||
               command == "open_context_inventory";
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

    bool IsLowerSnakeCaseName(const std::string& value)
    {
        if (value.empty() || value.size() > 80)
            return false;
        if (value[0] < 'a' || value[0] > 'z')
            return false;
        for (size_t index = 0; index < value.size(); ++index)
        {
            const char character = value[index];
            const bool lower = character >= 'a' && character <= 'z';
            const bool digit = character >= '0' && character <= '9';
            if (!lower && !digit && character != '_')
                return false;
        }
        return true;
    }

    std::string FormatStableHandleIdentity(
        unsigned long long processGeneration,
        unsigned long long sessionGeneration,
        unsigned int type,
        unsigned int container,
        unsigned int containerSerial,
        unsigned int index,
        unsigned int serial)
    {
        std::ostringstream value;
        value << "entity-"
              << std::hex << std::setfill('0')
              << std::setw(16) << processGeneration
              << "-" << std::setw(16) << sessionGeneration
              << "-" << std::setw(8) << type
              << "-" << std::setw(8) << container
              << "-" << std::setw(8) << containerSerial
              << "-" << std::setw(8) << index
              << "-" << std::setw(8) << serial;
        return value.str();
    }

    std::string FormatStableCharacterIdentity(
        unsigned long long processGeneration,
        unsigned long long sessionGeneration,
        unsigned int type,
        unsigned int container,
        unsigned int containerSerial,
        unsigned int index,
        unsigned int serial)
    {
        // Kenshi shifts a live character between handle containers when zones
        // load or body state changes. The character's type/index/serial tuple
        // remains the same; container generations describe residency, not the
        // squad member. Keep the seven-field opaque shape while zeroing those
        // two non-identity fields so every consumer sees one stable ID.
        (void)container;
        (void)containerSerial;
        return FormatStableHandleIdentity(
            processGeneration,
            sessionGeneration,
            type,
            0U,
            0U,
            index,
            serial);
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
            "target_id",
            "context_action",
            "bearing_degrees",
            "distance_units",
            "minimum_output_quantity",
            "destination_id",
            "section_name",
            "slot_x",
            "slot_y"
        };
        static const char* const revisionKeys[] = {
            "telemetry_sequence",
            "frame_sequence",
            "capability_epoch",
            "observed_at_monotonic"
        };

        request = NativeCommandRequest();
        rejectionReason.clear();
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
            request.contextAction =
                root.get<std::string>("context_action", "");
            request.bearingDegrees =
                root.get<double>("bearing_degrees", 0.0);
            request.distanceUnits =
                root.get<double>("distance_units", 0.0);
            request.minimumOutputQuantity =
                root.get<unsigned int>("minimum_output_quantity", 0);
            request.destinationId =
                root.get<std::string>("destination_id", "");
            request.sectionName = root.get<std::string>("section_name", "");
            request.slotX = root.get<int>("slot_x", -1);
            request.slotY = root.get<int>("slot_y", -1);
            request.basedOnTelemetrySequence =
                root.get<unsigned long long>(
                    "based_on_revision.telemetry_sequence",
                    0);
            const boost::property_tree::ptree& selectedIds =
                root.get_child("selected_character_ids");
            if (!HasOnlyKeys(root, rootKeys, ARRAY_COUNT(rootKeys)))
            {
                rejectionReason = "malformed_request";
                return false;
            }
            if (root.get<std::string>("schema_version") != "1.2" ||
                !IsValidCommandId(request.commandId) ||
                request.command.empty() ||
                request.command.size() > 80 ||
                request.controlMode.empty() ||
                request.controlMode.size() > 80 ||
                request.identitySessionId.empty() ||
                request.identitySessionId.size() > 200 ||
                request.targetId.size() > 200 ||
                request.contextAction.size() > 80 ||
                request.minimumOutputQuantity < 1 ||
                request.minimumOutputQuantity > 5 ||
                request.destinationId.size() > 200 ||
                request.sectionName.size() > 80 ||
                request.slotX < 0 ||
                request.slotY < 0)
            {
                rejectionReason = "malformed_request";
                return false;
            }

            const boost::property_tree::ptree& revision =
                root.get_child("based_on_revision");
            if (!HasOnlyKeys(
                    revision,
                    revisionKeys,
                    ARRAY_COUNT(revisionKeys)))
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

            // Recipient cardinality is not decided here. The operation
            // registry owns recipient scope; this parser validates only that a
            // dispatch basis is structurally coherent.
            //
            // A command-name exception set used to live here, the native twin
            // of one deleted from the Python transport. Keeping it meant two
            // edges could disagree with the definition they were both meant to
            // enforce - and it silently rejected any group-scoped command that
            // had not been added to both lists.
            if (selectedIds.empty() || selectedIds.size() > 64)
            {
                rejectionReason = "malformed_request";
                return false;
            }
            std::set<std::string> uniqueSelectedIds;
            for (boost::property_tree::ptree::const_iterator selected =
                     selectedIds.begin();
                 selected != selectedIds.end();
                 ++selected)
            {
                const std::string selectedId = selected->second.data();
                if (!selected->first.empty() ||
                    !IsLeaf(selected->second) ||
                    selectedId.empty() ||
                    selectedId.size() > 200 ||
                    !uniqueSelectedIds.insert(selectedId).second)
                {
                    rejectionReason = "malformed_request";
                    return false;
                }
                request.selectedCharacterIds.push_back(selectedId);
            }
            if (request.selectedCharacterIds.size() == 1)
                request.selectedCharacterId = request.selectedCharacterIds[0];

            const bool isDirection =
                NativeCommandCarriesDirection(request.command);
            const bool isTargeted =
                NativeCommandNamesTarget(request.command);
            const bool isBuildingExit =
                !isDirection && !isTargeted &&
                IsKnownNativeCommand(request.command);
            if (isDirection)
            {
                if (!request.targetId.empty() ||
                    !(request.bearingDegrees >= 0.0) ||
                    !(request.bearingDegrees < 360.0) ||
                    !(request.distanceUnits > 0.0) ||
                    !(request.distanceUnits <= 2000.0))
                {
                    rejectionReason = "malformed_request";
                    return false;
                }
            }
            else if (isBuildingExit)
            {
                if (!request.targetId.empty() ||
                    request.bearingDegrees != 0.0 ||
                    request.distanceUnits != 0.0)
                {
                    rejectionReason = "malformed_request";
                    return false;
                }
            }
            else if (isTargeted &&
                     (request.targetId.empty() ||
                      request.bearingDegrees != 0.0 ||
                      request.distanceUnits != 0.0))
            {
                rejectionReason = "malformed_request";
                return false;
            }
            else if (!isTargeted)
            {
                rejectionReason = "malformed_request";
                return false;
            }
            if (request.command != "produce_resource_output" &&
                request.minimumOutputQuantity != 1)
            {
                rejectionReason = "malformed_request";
                return false;
            }
            const bool isContextAction =
                request.command == "perform_context_action";
            const bool isCharacterOrder =
                request.command == "perform_character_order";
            if (isContextAction)
            {
                if (request.contextAction != "operate" &&
                    request.contextAction != "first_aid")
                {
                    rejectionReason = "malformed_request";
                    return false;
                }
            }
            else if (isCharacterOrder)
            {
                // The order is named, not enumerated here: the plugin resolves
                // it against Kenshi's task vocabulary and then asks Kenshi
                // whether it applies to this target. Validating the shape is
                // this layer's job; validating the meaning is the engine's.
                if (request.contextAction.empty() ||
                    !IsLowerSnakeCaseName(request.contextAction))
                {
                    rejectionReason = "malformed_request";
                    return false;
                }
            }
            else if (!request.contextAction.empty())
            {
                rejectionReason = "malformed_request";
                return false;
            }
        }
        catch (const std::exception&)
        {
            rejectionReason = "malformed_request";
            return false;
        }
        return true;
    }

    std::string SerializeNativeCommandAcknowledgement(
        const NativeCommandAcknowledgement& acknowledgement)
    {
        std::ostringstream json;
        json.imbue(std::locale::classic());
        json << std::setprecision(17);
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
        json << "\"context_action\":\""
             << JsonEscape(acknowledgement.contextAction) << "\",";
        json << "\"bearing_degrees\":"
             << acknowledgement.bearingDegrees << ",";
        json << "\"distance_units\":"
             << acknowledgement.distanceUnits << ",";
        json << "\"minimum_output_quantity\":"
             << acknowledgement.minimumOutputQuantity << ",";
        json << "\"destination_id\":\""
             << JsonEscape(acknowledgement.destinationId) << "\",";
        json << "\"section_name\":\""
             << JsonEscape(acknowledgement.sectionName) << "\",";
        json << "\"slot_x\":" << acknowledgement.slotX << ",";
        json << "\"slot_y\":" << acknowledgement.slotY << ",";
        json << "\"selected_character_ids\":[";
        if (!acknowledgement.selectedCharacterIds.empty())
        {
            for (unsigned int index = 0;
                 index < acknowledgement.selectedCharacterIds.size();
                 ++index)
            {
                if (index > 0)
                    json << ",";
                json << "\""
                     << JsonEscape(acknowledgement.selectedCharacterIds[index])
                     << "\"";
            }
        }
        else
        {
            json << "\""
                 << JsonEscape(acknowledgement.selectedCharacterId)
                 << "\"";
        }
        json << "],";
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
        return json.str();
    }
}
