#ifndef KENSHI_AGENT_ITEM_TYPE_VOCABULARY_GENERATED_H
#define KENSHI_AGENT_ITEM_TYPE_VOCABULARY_GENERATED_H

// Generated from game_sources/kenshi/ItemType.h; edits are overwritten.
// Every scannable object category Kenshi declares. Most have no spatial
// instances and simply return nothing, which is the game answering
// rather than this plug-in assuming.

namespace KenshiAgentTelemetry
{
    struct ItemTypeVocabularyEntry
    {
        int value;
        const char* name;
    };

    inline const ItemTypeVocabularyEntry* ItemTypeVocabulary(
        unsigned int& count)
    {
        static const ItemTypeVocabularyEntry entries[] =
        {
            { 0, "BUILDING" },
            { 1, "CHARACTER" },
            { 2, "WEAPON" },
            { 3, "ARMOUR" },
            { 4, "ITEM" },
            { 5, "ANIMAL_ANIMATION" },
            { 6, "ATTACHMENT" },
            { 7, "RACE" },
            { 8, "LOCATION" },
            { 9, "WAR_SAVESTATE" },
            { 10, "FACTION" },
            { 11, "NULL_ITEM" },
            { 12, "ZONE_MAP" },
            { 13, "TOWN" },
            { 14, "WORLDMAP_CHARACTER" },
            { 15, "CHARACTER_APPEARANCE_OLD" },
            { 16, "LOCATIONAL_DAMAGE" },
            { 17, "COMBAT_TECHNIQUE" },
            { 18, "DIALOGUE" },
            { 19, "DIALOGUE_LINE" },
            { 20, "TECHTREE" },
            { 21, "RESEARCH" },
            { 22, "AI_TASK" },
            { 23, "AI_STATE" },
            { 24, "ANIMATION" },
            { 25, "STATS" },
            { 26, "PERSONALITY" },
            { 27, "CONSTANTS" },
            { 28, "BIOMES" },
            { 29, "BUILDING_PART" },
            { 30, "INSTANCE_COLLECTION" },
            { 31, "DIALOG_ACTION" },
            { 32, "TEMPORARY_INFO" },
            { 33, "MOD_FILENAME" },
            { 34, "PLATOON" },
            { 35, "GAMESTATE_BUILDING" },
            { 36, "GAMESTATE_CHARACTER" },
            { 37, "GAMESTATE_FACTION" },
            { 38, "GAMESTATE_TOWN_INSTANCE_LIST" },
            { 39, "STATE" },
            { 40, "SAVED_STATE" },
            { 41, "INVENTORY_STATE" },
            { 42, "INVENTORY_ITEM_STATE" },
            { 43, "REPEATABLE_BUILDING_PART_SLOT" },
            { 44, "MATERIAL_SPEC" },
            { 45, "MATERIAL_SPECS_COLLECTION" },
            { 46, "CONTAINER" },
            { 47, "MATERIAL_SPECS_CLOTHING" },
            { 48, "GAMESTATE_BUILDING_INTERIOR" },
            { 49, "VENDOR_LIST" },
            { 50, "MATERIAL_SPECS_WEAPON" },
            { 51, "WEAPON_MANUFACTURER" },
            { 52, "SQUAD_TEMPLATE" },
            { 53, "ROAD" },
            { 54, "LOCATION_NODE" },
            { 55, "COLOR_DATA" },
            { 56, "CAMERA" },
            { 57, "MEDICAL_STATE" },
            { 58, "MEDICAL_PART_STATE" },
            { 59, "FOLIAGE_LAYER" },
            { 60, "FOLIAGE_MESH" },
            { 61, "GRASS" },
            { 62, "BUILDING_FUNCTIONALITY" },
            { 63, "DAY_SCHEDULE" },
            { 64, "NEW_GAME_STARTOFF" },
            { 65, "GAMESTATE_CRAFTING" },
            { 66, "CHARACTER_APPEARANCE" },
            { 67, "GAMESTATE_AI" },
            { 68, "WILDLIFE_BIRDS" },
            { 69, "MAP_FEATURES" },
            { 70, "DIPLOMATIC_ASSAULTS" },
            { 71, "SINGLE_DIPLOMATIC_ASSAULT" },
            { 72, "AI_PACKAGE" },
            { 73, "DIALOGUE_PACKAGE" },
            { 74, "GUN_DATA" },
            { 75, "HUMAN_CHARACTER" },
            { 76, "ANIMAL_CHARACTER" },
            { 77, "UNIQUE_SQUAD_TEMPLATE" },
            { 78, "FACTION_TEMPLATE" },
            { 79, "AI_SCHEDULE" },
            { 80, "WEATHER" },
            { 81, "SEASON" },
            { 82, "EFFECT" },
            { 83, "ITEM_PLACEMENT_GROUP" },
            { 84, "WORD_SWAPS" },
            { 85, "NEST" },
            { 86, "NEST_ITEM" },
            { 87, "CHARACTER_PHYSICS_ATTACHMENT" },
            { 88, "LIGHT" },
            { 89, "HEAD" },
            { 90, "BLUEPRINT" },
            { 91, "SHOP_TRADER_CLASS" },
            { 92, "FOLIAGE_BUILDING" },
            { 93, "FACTION_CAMPAIGN" },
            { 94, "GAMESTATE_TOWN" },
            { 95, "BIOME_GROUP" },
            { 96, "EFFECT_FOG_VOLUME" },
            { 97, "FARM_DATA" },
            { 98, "FARM_PART" },
            { 99, "ENVIRONMENT_RESOURCES" },
            { 100, "RACE_GROUP" },
            { 101, "ARTIFACTS" },
            { 102, "MAP_ITEM" },
            { 103, "BUILDINGS_SWAP" },
            { 104, "ITEMS_CULTURE" },
            { 105, "ANIMATION_EVENT" },
            { 106, "TUTORIAL" },
            { 107, "CROSSBOW" },
            { 108, "TERRAIN_DECALS" },
            { 109, "AMBIENT_SOUND" },
            { 110, "WORLD_EVENT_STATE" },
            { 111, "LIMB_REPLACEMENT" },
            { 112, "ANIMATION_FILE" },
        };
        count = sizeof(entries) / sizeof(entries[0]);
        return entries;
    }
}

#endif
