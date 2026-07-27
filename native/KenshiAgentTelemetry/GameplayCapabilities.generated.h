#ifndef KENSHI_AGENT_GAMEPLAY_CAPABILITIES_GENERATED_H
#define KENSHI_AGENT_GAMEPLAY_CAPABILITIES_GENERATED_H

#include <ostream>

namespace KenshiAgentTelemetry
{
    inline void AppendGameplayCapabilities(
        std::ostream& json,
        bool includeConditional)
    {
        static const char* const always[] =
        {
            "game.pause",
            "game.speed",
            "game.money",
            "game.time",
            "camera.position",
            "squad.basic",
            "squad.hunger",
            "squad.health",
            "squad.inventory",
            "squad.indoors",
            "squad.current_goal",
            "ui.inventory",
            "ui.dialogue",
            "ui.dialogue.target",
            "ui.dialogue.options",
            "ui.tooltip",
            "ui.visible_controls",
            "nearby.characters",
            "nearby.roles",
            "control.approach_vendor",
            "control.move_to_character",
            "control.move_in_direction",
            "control.exit_current_building",
            "world.context_targets",
            "control.perform_context_action",
            "identity.stable_handles",
        };
        static const char* const conditional[] =
        {
            "nearby.shop_owners",
        };
        json << "[";
        bool first = true;
        unsigned int index = 0;
        for (index = 0; index < sizeof(always) / sizeof(always[0]); ++index)
        {
            if (!first)
                json << ",";
            first = false;
            json << "\"" << always[index] << "\"";
        }
        if (includeConditional)
        {
            for (index = 0;
                 index < sizeof(conditional) / sizeof(conditional[0]);
                 ++index)
            {
                if (!first)
                    json << ",";
                first = false;
                json << "\"" << conditional[index] << "\"";
            }
        }
        json << "]";
    }
}

#endif
