"""
Combat resolution — dice-based, stat-weighted.
Fully standalone: no hexmap or DB imports.
"""

import random
import math
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CombatUnit:
    name:      str
    side:      str          # "players" | "enemy"
    attack:    int = 10
    defense:   int = 10
    speed:     int = 10
    morale:    int = 10
    supply:    int = 10
    recon:     int = 10
    unit_type: str = ""


@dataclass
class CombatResult:
    attacker:      str
    defender:      str
    attacker_roll: int
    defender_roll: int
    outcome:       str      # "attacker_wins" | "defender_wins" | "draw"
    narrative:     str


def resolve_combat(attacker: CombatUnit, defender: CombatUnit) -> CombatResult:
    """
    Roll 2d10 for each side, add weighted stat bonuses, compare.
    Supply below 5 penalises both attack and defense rolls.
    Recon adds a small scouting bonus to the attacker.
    """
    def _roll(unit: CombatUnit, is_attacker: bool) -> int:
        base      = random.randint(1, 10) + random.randint(1, 10)
        stat_bon  = (unit.attack if is_attacker else unit.defense) // 4
        morale_bon = unit.morale // 5
        supply_pen = max(0, (5 - unit.supply)) * 2
        recon_bon  = (unit.recon // 6) if is_attacker else 0
        return max(1, base + stat_bon + morale_bon - supply_pen + recon_bon)

    a_roll = _roll(attacker, True)
    d_roll = _roll(defender, False)

    if a_roll > d_roll:
        outcome = "attacker_wins"
        narrative = (f"{attacker.name} defeated {defender.name} "
                     f"({a_roll} vs {d_roll}).")
    elif d_roll > a_roll:
        outcome = "defender_wins"
        narrative = (f"{defender.name} repelled {attacker.name} "
                     f"({d_roll} vs {a_roll}).")
    else:
        outcome = "draw"
        narrative = (f"{attacker.name} and {defender.name} fought to a standstill "
                     f"({a_roll} vs {d_roll}).")

    return CombatResult(
        attacker      = attacker.name,
        defender      = defender.name,
        attacker_roll = a_roll,
        defender_roll = d_roll,
        outcome       = outcome,
        narrative     = narrative,
    )
