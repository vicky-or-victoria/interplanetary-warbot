"""
Warbot — Brigade Definitions
All brigade stat templates, transit times, and mechanical rules in one place.
Import this anywhere brigade logic is needed.
"""

# ── Brigade registry ───────────────────────────────────────────────────────────

BRIGADES = {
    "aerial": {
        "name":        "Aerial Brigade",
        "emoji":       "✈",
        "ascii_icon":  ">>",   # wings / fast mover
        "description": (
            "Fast-moving air assault unit. Inserts directly from orbit. "
            "High speed and recon, light on armour."
        ),
        "stats": {
            "attack":  10, "defense":  7, "speed": 16,
            "morale":  10, "supply":   8, "recon": 14,
        },
        "transit_turns":  1,    # fastest — flies directly
        "move_steps":     2,    # moves 2 hexes per move action
        "can_scavenge":   True,
        "scavenge_bonus": 0,
        "supply_drain":   1,
        "specials": [
            "Transit in 1 turn (direct orbital insertion)",
            "Moves 2 hexes per action",
            "/recon_sweep — reveals enemy units within 2 hexes",
        ],
    },
    "armoured": {
        "name":        "Armoured Brigade",
        "emoji":       "🛡",
        "ascii_icon":  "⬢",   # tank/armour block
        "description": (
            "Heavy mechanised unit. Devastating in assault and nearly "
            "impenetrable — but slow to deploy and resupply."
        ),
        "stats": {
            "attack":  14, "defense": 14, "speed":  6,
            "morale":  10, "supply":   8, "recon":  8,
        },
        "transit_turns":  3,
        "move_steps":     1,
        "can_scavenge":   False,   # too heavy to forage
        "scavenge_bonus": 0,
        "supply_drain":   2,       # burns more supply
        "specials": [
            "20% damage reduction on both attack and defense rolls",
            "Cannot scavenge",
            "Transit takes 3 turns",
        ],
    },
    "infantry": {
        "name":        "Infantry Brigade",
        "emoji":       "⚔",
        "ascii_icon":  "XX",   # boots on the ground
        "description": (
            "Versatile ground forces. High morale and solid defense. "
            "Can dig in to hold a position under heavy fire."
        ),
        "stats": {
            "attack":  10, "defense": 12, "speed": 10,
            "morale":  16, "supply":  10, "recon":  8,
        },
        "transit_turns":  2,
        "move_steps":     1,
        "can_scavenge":   True,
        "scavenge_bonus": 0,
        "supply_drain":   1,
        "specials": [
            "/dig_in — +4 defense roll bonus until next move",
            "Highest base morale of all brigades",
        ],
    },
    "ranger": {
        "name":        "Ranger Brigade",
        "emoji":       "🎯",
        "ascii_icon":  "/\\",  # recon / sight triangle
        "description": (
            "Light reconnaissance and survival specialists. Masters of "
            "living off the land and moving unseen."
        ),
        "stats": {
            "attack":  10, "defense":  9, "speed": 13,
            "morale":  11, "supply":  14, "recon": 15,
        },
        "transit_turns":  2,
        "move_steps":     1,
        "can_scavenge":   True,
        "scavenge_bonus": 3,    # +3 flat supply gain on scavenge
        "supply_drain":   1,
        "specials": [
            "Can scavenge twice per turn",
            "+3 supply bonus per scavenge action",
            "/recon_sweep — reveals enemy units within 3 hexes",
        ],
    },
    "artillery": {
        "name":        "Artillery Brigade",
        "emoji":       "💥",
        "ascii_icon":  "✹",   # bang / shell burst
        "description": (
            "Long-range fire support. Devastating when armed and stationary. "
            "Useless on the move."
        ),
        "stats": {
            "attack":  17, "defense":  7, "speed":  5,
            "morale":   9, "supply":   8, "recon": 10,
        },
        "transit_turns":  3,
        "move_steps":     1,
        "can_scavenge":   True,
        "scavenge_bonus": 0,
        "supply_drain":   1,
        "specials": [
            "Must use /artillery_hold to arm before firing in combat",
            "Armed artillery deals splash damage to adjacent enemy hexes",
            "Moving disarms the unit",
            "Transit takes 3 turns",
        ],
    },
    "engineering": {
        "name":        "Engineering Brigade",
        "emoji":       "🔧",
        "ascii_icon":  "⊞",   # plus/wrench / engineer cross
        "description": (
            "Field engineers and logistics specialists. They build, repair, "
            "and sustain — turning the tide through infrastructure."
        ),
        "stats": {
            "attack":   8, "defense": 10, "speed": 10,
            "morale":  10, "supply":  16, "recon": 10,
        },
        "transit_turns":  2,
        "move_steps":     1,
        "can_scavenge":   True,
        "scavenge_bonus": 0,
        "supply_drain":   0,    # self-sufficient, no supply drain
        "specials": [
            "/fortify — permanently changes current hex terrain to Fort",
            "/repair — restores supply to all friendly units on adjacent hexes",
            "No supply drain (self-sufficient logistics)",
        ],
    },
    "special_ops": {
        "name":        "Special Operations",
        "emoji":       "🕵",
        "ascii_icon":  "◇",   # unknown / ghost
        "description": (
            "Elite covert insertion unit. Unpredictable and deadly. "
            "Goes where other brigades cannot."
        ),
        "stats": {
            "attack":  11, "defense":  8, "speed": 12,
            "morale":  14, "supply":   9, "recon": 14,
        },
        "transit_turns":  1,    # direct insertion anywhere, no transit path
        "move_steps":     1,
        "can_scavenge":   True,
        "scavenge_bonus": 0,
        "supply_drain":   1,
        "specials": [
            "Can deploy to ANY hex on the map instantly (direct insertion)",
            "3d10 drop-lowest combat rolls — higher variance, higher ceiling",
            "/recon_sweep — reveals enemy units within 2 hexes",
            "Transit in 1 turn, any destination",
        ],
    },
}

BRIGADE_KEYS = list(BRIGADES.keys())


def get_brigade(key: str) -> dict:
    return BRIGADES.get(key, BRIGADES["infantry"])


def brigade_stats(key: str) -> dict:
    """Return base stat dict for a brigade."""
    return dict(BRIGADES.get(key, BRIGADES["infantry"])["stats"])


def transit_turns(brigade: str) -> int:
    return BRIGADES.get(brigade, BRIGADES["infantry"])["transit_turns"]


def move_steps(brigade: str) -> int:
    return BRIGADES.get(brigade, BRIGADES["infantry"])["move_steps"]


def supply_drain(brigade: str) -> int:
    return BRIGADES.get(brigade, BRIGADES["infantry"])["supply_drain"]


def can_scavenge_twice(brigade: str) -> bool:
    return brigade == "ranger"


def scavenge_bonus(brigade: str) -> int:
    return BRIGADES.get(brigade, BRIGADES["infantry"]).get("scavenge_bonus", 0)


def can_direct_insert(brigade: str) -> bool:
    """Aerial and Special Ops can deploy to any hex without transit path."""
    return brigade in ("aerial", "special_ops")


def brigade_ascii_icon(key: str) -> str:
    """Return the 2-character ASCII icon for a brigade (ASCII-safe, monospace-friendly)."""
    return BRIGADES.get(key, BRIGADES["infantry"]).get("ascii_icon", "XX")


# ── Discord-friendly brigade choices list ─────────────────────────────────────

def brigade_choices():
    """Returns list of (label, value) for Discord select menus."""
    return [(f"{v['emoji']} {v['name']}", k) for k, v in BRIGADES.items()]
