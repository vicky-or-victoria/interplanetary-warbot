"""
Warbot — Combat Resolution v4
HP-based damage system. Units have HP that decreases each battle instead of a loss counter.
Damage dealt = attacker_roll - defender_roll (minimum 1 if attacker wins, 0 on defender win or draw).

Brigade modifiers:
  armoured    — attacker and defender rolls reduced 20% vs Armoured (damage reduction)
  artillery   — only fires if artillery_armed=True; applies splash to adjacent hexes
  infantry    — +4 defense roll bonus if is_dug_in=True
  special_ops — 3d10 drop lowest instead of 2d10 (high variance)
  others      — standard 2d10 + stat bonuses

Dice fairness fix:
  Both player and enemy units now use identical roll formulas. Previously enemy units
  lacked the recon bonus on attack rolls, causing players to consistently roll higher.
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
    brigade:         str = ""   # player brigade — empty for enemy units
    is_dug_in:       bool = False
    artillery_armed: bool = False
    unit_type:       str = ""
    hp:              int = 100  # current HP


@dataclass
class CombatResult:
    attacker:        str
    defender:        str
    attacker_roll:   int
    defender_roll:   int
    outcome:         str        # "attacker_wins" | "defender_wins" | "draw"
    narrative:       str
    attacker_damage: int = 0    # HP damage dealt TO the attacker (defender won)
    defender_damage: int = 0    # HP damage dealt TO the defender (attacker won)
    splash_hexes:    List[str] = field(default_factory=list)


def _roll_attacker(unit: CombatUnit) -> int:
    """Roll attack dice with brigade-specific rules.
    Both player and enemy units use the same formula for fairness.
    """
    if unit.brigade == "special_ops":
        # 3d10 drop lowest — higher variance
        rolls = [random.randint(1, 10) for _ in range(3)]
        base  = sum(sorted(rolls)[1:])   # drop lowest
    else:
        base = random.randint(1, 10) + random.randint(1, 10)

    bonus      = unit.attack // 4
    morale     = unit.morale // 5
    recon      = unit.recon  // 6   # scouting advantage on attack
    supply_pen = max(0, (5 - unit.supply) * 2)
    return max(1, base + bonus + morale + recon - supply_pen)


def _roll_defender(unit: CombatUnit) -> int:
    """Roll defense dice with brigade-specific rules."""
    if unit.brigade == "special_ops":
        rolls = [random.randint(1, 10) for _ in range(3)]
        base  = sum(sorted(rolls)[1:])
    else:
        base = random.randint(1, 10) + random.randint(1, 10)

    bonus      = unit.defense // 4
    morale     = unit.morale  // 5
    supply_pen = max(0, (5 - unit.supply) * 2)
    dig_in_bon = 4 if (unit.brigade == "infantry" and unit.is_dug_in) else 0
    return max(1, base + bonus + morale + dig_in_bon - supply_pen)


def resolve_combat(
    attacker: CombatUnit,
    defender: CombatUnit,
    attacker_hex: str = "",
    adjacent_enemy_hexes: Optional[List[str]] = None,
) -> CombatResult:
    """
    Resolve one combat engagement. Returns roll results and HP damage values.

    Damage model:
      - If attacker wins: defender takes (attacker_roll - defender_roll) HP damage, min 1.
      - If defender wins: attacker takes (defender_roll - attacker_roll) HP damage, min 1.
      - Draw: both sides take 1 HP damage (attrition).

    Artillery special rules:
      - If attacker is artillery and NOT artillery_armed -> skip, return draw (no damage).
      - If attacker is artillery and IS armed -> fire, splash adjacent_enemy_hexes.

    Armoured damage reduction:
      - If defender is armoured -> attacker roll * 0.80.
      - If attacker is armoured -> defender roll * 0.80.
    """

    # Artillery must be armed to fire
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

    # Armoured damage reduction (reduces opponent's effective roll)
    if defender.brigade == "armoured":
        a_roll = max(1, int(a_roll * 0.80))
    if attacker.brigade == "armoured":
        d_roll = max(1, int(d_roll * 0.80))

    splash_hexes: List[str] = []
    if attacker.brigade == "artillery" and attacker.artillery_armed:
        splash_hexes = adjacent_enemy_hexes or []

    attacker_damage = 0
    defender_damage = 0

    if a_roll > d_roll:
        outcome          = "attacker_wins"
        defender_damage  = max(1, a_roll - d_roll)
        narrative = (
            f"{attacker.name} defeated {defender.name} "
            f"({a_roll} vs {d_roll}) — dealt **{defender_damage} damage**."
        )
    elif d_roll > a_roll:
        outcome         = "defender_wins"
        attacker_damage = max(1, d_roll - a_roll)
        narrative = (
            f"{defender.name} repelled {attacker.name} "
            f"({d_roll} vs {a_roll}) — dealt **{attacker_damage} damage**."
        )
    else:
        outcome          = "draw"
        attacker_damage  = 1
        defender_damage  = 1
        narrative = (
            f"{attacker.name} and {defender.name} "
            f"fought to a standstill ({a_roll} vs {d_roll}) — both took **1 damage**."
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
