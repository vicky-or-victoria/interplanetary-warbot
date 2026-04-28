"""
Warbot — Combat Resolution v5
Both sides always deal damage every exchange — no "only winner deals damage."

Damage formula:
  Each unit's damage = max(1, their_roll - opponent_roll // 2)
  This means even a losing roll still chips the winner, but the winner
  hurts more. On a draw both take equal damage.

Brigade modifiers:
  armoured    — attacker and defender rolls reduced 20% vs Armoured (damage reduction)
  artillery   — only fires if artillery_armed=True; applies splash to adjacent hexes
  infantry    — +4 defense roll bonus if is_dug_in=True
  special_ops — 3d10 drop lowest instead of 2d10 (high variance)
  others      — standard 2d10 + stat bonuses

Dice fairness:
  Both player and enemy units use identical roll formulas (recon bonus included for both).
"""

import random
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class CombatUnit:
    name:            str
    side:            str        # "players" | "enemy"
    attack:          int = 10
    defense:         int = 10
    speed:           int = 10
    morale:          int = 10
    supply:          int = 10
    recon:           int = 10
    brigade:         str = ""
    is_dug_in:       bool = False
    artillery_armed: bool = False
    unit_type:       str = ""
    hp:              int = 100


@dataclass
class CombatResult:
    attacker:        str
    defender:        str
    attacker_roll:   int
    defender_roll:   int
    outcome:         str        # "attacker_wins" | "defender_wins" | "draw"
    narrative:       str
    attacker_damage: int = 0    # HP damage dealt TO the attacker (from defender's roll)
    defender_damage: int = 0    # HP damage dealt TO the defender (from attacker's roll)
    splash_hexes:    List[str] = field(default_factory=list)


def _roll_attacker(unit: CombatUnit) -> int:
    if unit.brigade == "special_ops":
        rolls = [random.randint(1, 10) for _ in range(3)]
        base  = sum(sorted(rolls)[1:])
    else:
        base = random.randint(1, 10) + random.randint(1, 10)

    bonus      = unit.attack // 4
    morale     = unit.morale // 5
    recon      = unit.recon  // 6
    supply_pen = max(0, (5 - unit.supply) * 2)
    return max(1, base + bonus + morale + recon - supply_pen)


def _roll_defender(unit: CombatUnit) -> int:
    if unit.brigade == "special_ops":
        rolls = [random.randint(1, 10) for _ in range(3)]
        base  = sum(sorted(rolls)[1:])
    else:
        base = random.randint(1, 10) + random.randint(1, 10)

    bonus      = unit.defense // 4
    morale     = unit.morale  // 5
    recon      = unit.recon   // 6
    supply_pen = max(0, (5 - unit.supply) * 2)
    dig_in_bon = 4 if (unit.brigade == "infantry" and unit.is_dug_in) else 0
    return max(1, base + bonus + morale + recon + dig_in_bon - supply_pen)


def resolve_combat(
    attacker: CombatUnit,
    defender: CombatUnit,
    attacker_hex: str = "",
    adjacent_enemy_hexes: Optional[List[str]] = None,
) -> CombatResult:
    """
    Resolve one combat engagement.

    Both sides ALWAYS deal damage every exchange:
      - Defender damage (damage dealt TO defender) = max(1, attacker_roll - defender_roll // 2)
      - Attacker damage (damage dealt TO attacker) = max(1, defender_roll - attacker_roll // 2)
    The higher roll always hurts more, but the lower roll still always connects.

    Routing threshold: checked externally. A side routes only when it loses
    an exchange by 10 or more.
    """

    if attacker.brigade == "artillery" and not attacker.artillery_armed:
        return CombatResult(
            attacker         = attacker.name,
            defender         = defender.name,
            attacker_roll    = 0,
            defender_roll    = 0,
            outcome          = "draw",
            narrative        = (f"{attacker.name} (Artillery) is not armed — "
                                f"must use /artillery_hold to arm before firing."),
            attacker_damage  = 0,
            defender_damage  = 0,
            splash_hexes     = [],
        )

    a_roll = _roll_attacker(attacker)
    d_roll = _roll_defender(defender)

    # The turn engine models player units as the attacker in each exchange.
    # Dug-in infantry still needs to blunt incoming counterfire.
    if attacker.brigade == "infantry" and attacker.is_dug_in:
        d_roll = max(1, d_roll - 4)

    # Armoured damage reduction
    if defender.brigade == "armoured":
        a_roll = max(1, int(a_roll * 0.80))
    if attacker.brigade == "armoured":
        d_roll = max(1, int(d_roll * 0.80))

    splash_hexes: List[str] = []
    if attacker.brigade == "artillery" and attacker.artillery_armed:
        splash_hexes = adjacent_enemy_hexes or []

    # Both sides always deal damage — higher roll hurts more, lower roll still connects
    defender_damage = max(1, a_roll - d_roll // 2)
    attacker_damage = max(1, d_roll - a_roll // 2)

    if a_roll > d_roll:
        outcome = "attacker_wins"
    elif d_roll > a_roll:
        outcome = "defender_wins"
    else:
        outcome = "draw"

    narrative = (
        f"{attacker.name} rolled **{a_roll}** vs {defender.name} **{d_roll}** — "
        f"dealt **{defender_damage} dmg**, took **{attacker_damage} dmg**."
    )

    if splash_hexes:
        narrative += f" Artillery splash hits: {', '.join(splash_hexes)}."

    return CombatResult(
        attacker         = attacker.name,
        defender         = defender.name,
        attacker_roll    = a_roll,
        defender_roll    = d_roll,
        outcome          = outcome,
        narrative        = narrative,
        attacker_damage  = attacker_damage,
        defender_damage  = defender_damage,
        splash_hexes     = splash_hexes,
    )
