"""
Warbot — Hex Map Geometry
Two-level addressing:
  Level 1 — Outer hex:  axial coordinate string  "q,r"   (37 total, 4-ring grid)
  Level 2 — Mid hex:    "q,r:mq,mr"              (19 per outer, 2-ring inner grid)

Units stand on mid hexes. No level 3.

Axial coordinate system: flat-top hexes.
"""

import math
from typing import List, Tuple, Optional

# ── Ring generators ────────────────────────────────────────────────────────────

_AXIAL_DIRECTIONS = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]


def hex_ring(radius: int) -> List[Tuple[int, int]]:
    if radius == 0:
        return [(0, 0)]
    results = []
    q, r = 0, -radius
    for d in range(6):
        for _ in range(radius):
            results.append((q, r))
            q += _AXIAL_DIRECTIONS[d][0]
            r += _AXIAL_DIRECTIONS[d][1]
    return results


def hex_disk(max_radius: int) -> List[Tuple[int, int]]:
    coords = []
    for ring in range(max_radius + 1):
        coords.extend(hex_ring(ring))
    return coords


# ── Coordinate sets ────────────────────────────────────────────────────────────

OUTER_COORDS: List[Tuple[int, int]] = hex_disk(3)   # 37 hexes
MID_OFFSETS:  List[Tuple[int, int]] = hex_disk(2)   # 19 per outer

OUTER_SET = set(OUTER_COORDS)
MID_SET   = set(MID_OFFSETS)


# ── Key serialisation ──────────────────────────────────────────────────────────

def outer_key(q: int, r: int) -> str:
    return f"{q},{r}"

def mid_key(oq: int, or_: int, mq: int, mr: int) -> str:
    return f"{oq},{or_}:{mq},{mr}"

def parse_outer(key: str) -> Tuple[int, int]:
    q, r = key.split(",")
    return int(q), int(r)

def parse_mid(key: str) -> Tuple[int, int, int, int]:
    outer_part, mid_part = key.split(":")
    oq, or_ = outer_part.split(",")
    mq, mr  = mid_part.split(",")
    return int(oq), int(or_), int(mq), int(mr)

def outer_of_mid(mk: str) -> str:
    return mk.split(":")[0]

def is_mid_key(s: str) -> bool:
    return ":" in s

def is_outer_key(s: str) -> bool:
    return "," in s and ":" not in s


# ── Pixel geometry ─────────────────────────────────────────────────────────────

def hex_center_flat(q: int, r: int, size: float,
                    ox: float = 0, oy: float = 0) -> Tuple[float, float]:
    x = size * (3 / 2 * q)
    y = size * (math.sqrt(3) / 2 * q + math.sqrt(3) * r)
    return x + ox, y + oy


def hex_corners_flat(cx: float, cy: float, size: float) -> List[Tuple[float, float]]:
    return [
        (cx + size * math.cos(math.radians(60 * i)),
         cy + size * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]


# ── Adjacency ──────────────────────────────────────────────────────────────────

def outer_neighbors(oq: int, or_: int) -> List[Tuple[int, int]]:
    return [(oq + dq, or_ + dr) for dq, dr in _AXIAL_DIRECTIONS
            if (oq + dq, or_ + dr) in OUTER_SET]


def mid_neighbors(mq: int, mr: int) -> List[Tuple[int, int]]:
    return [(mq + dq, mr + dr) for dq, dr in _AXIAL_DIRECTIONS
            if (mq + dq, mr + dr) in MID_SET]


# ── Status constants ───────────────────────────────────────────────────────────

STATUS_PLAYER     = "players"
STATUS_ENEMY      = "enemy"
STATUS_CONTESTED  = "contested"
STATUS_NEUTRAL    = "neutral"
STATUS_MAJ_PLAYER = "majority_player"
STATUS_MAJ_ENEMY  = "majority_enemy"

SAFE_HUB = outer_key(0, 0)   # center hex = PMC forward operating base


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def ensure_hexes(guild_id: int, conn, planet_id: int = 1):
    """Insert all outer + mid hexes for a guild/planet if not present."""
    for oq, or_ in OUTER_COORDS:
        ok = outer_key(oq, or_)
        await conn.execute("""
            INSERT INTO hexes (guild_id, planet_id, address, level, controller, status)
            VALUES ($1, $2, $3, 1, 'neutral', 'neutral')
            ON CONFLICT (guild_id, planet_id, address) DO NOTHING
        """, guild_id, planet_id, ok)
        for mq, mr in MID_OFFSETS:
            mk = mid_key(oq, or_, mq, mr)
            await conn.execute("""
                INSERT INTO hexes (guild_id, planet_id, address, level,
                                   parent_address, controller, status)
                VALUES ($1, $2, $3, 2, $4, 'neutral', 'neutral')
                ON CONFLICT (guild_id, planet_id, address) DO NOTHING
            """, guild_id, planet_id, mk, ok)


async def recompute_hex_statuses(conn, guild_id: int, planet_id: int):
    """Recompute outer hex status from mid hex controllers."""
    for oq, or_ in OUTER_COORDS:
        ok    = outer_key(oq, or_)
        mids  = await conn.fetch(
            "SELECT controller FROM hexes "
            "WHERE guild_id=$1 AND planet_id=$2 AND level=2 AND parent_address=$3",
            guild_id, planet_id, ok)
        if not mids:
            continue
        total   = len(mids)
        p_count = sum(1 for m in mids if m["controller"] == STATUS_PLAYER)
        e_count = sum(1 for m in mids if m["controller"] == STATUS_ENEMY)
        if p_count == total:
            status = STATUS_PLAYER
        elif e_count == total:
            status = STATUS_ENEMY
        elif p_count > 0 and e_count > 0:
            status = STATUS_CONTESTED
        elif p_count > total // 2:
            status = STATUS_MAJ_PLAYER
        elif e_count > total // 2:
            status = STATUS_MAJ_ENEMY
        else:
            status = STATUS_NEUTRAL
        ctrl = (STATUS_PLAYER if p_count > e_count
                else STATUS_ENEMY if e_count > p_count
                else STATUS_NEUTRAL)
        await conn.execute(
            "UPDATE hexes SET status=$1, controller=$2 "
            "WHERE guild_id=$3 AND planet_id=$4 AND address=$5",
            status, ctrl, guild_id, planet_id, ok)
