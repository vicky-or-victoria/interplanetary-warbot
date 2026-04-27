"""
Warbot — Hex Map Geometry v3
Flat global axial grid. Every hex is identified by a single "gq,gr" string.

Grid layout:
  Outer ring (radius 3, 37 positions) × inner ring (radius 2, 19 positions)
  spaced at SECTOR_SPACING=3 axial units apart → 703 total hexes.
  No sector borders, no level concept, no outer/mid split.

All addressing uses hex_key(gq, gr) = "gq,gr".
"""

import math
from typing import List, Tuple, Set, Dict, Optional

# ── Axial directions (flat-top) ────────────────────────────────────────────────
DIRECTIONS = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]
DIR_NAMES  = ["E", "SE", "SW", "W", "NW", "NE"]   # flat-top orientation

SECTOR_SPACING = 5   # outer hex centres are this many axial units apart (≥5 for zero overlap with 2-ring inner grid)


# ── Ring / disk generators ─────────────────────────────────────────────────────

def hex_ring(radius: int) -> List[Tuple[int, int]]:
    if radius == 0:
        return [(0, 0)]
    results = []
    q, r = 0, -radius
    for d in range(6):
        for _ in range(radius):
            results.append((q, r))
            q += DIRECTIONS[d][0]
            r += DIRECTIONS[d][1]
    return results


def hex_disk(max_radius: int) -> List[Tuple[int, int]]:
    coords = []
    for ring in range(max_radius + 1):
        coords.extend(hex_ring(ring))
    return coords


# ── Build the full 703-hex global grid ────────────────────────────────────────

_OUTER_OFFSETS: List[Tuple[int, int]] = hex_disk(3)   # 37 sector origins
_MID_OFFSETS:   List[Tuple[int, int]] = hex_disk(2)   # 19 per sector

def _build_grid() -> List[Tuple[int, int]]:
    seen = set()
    coords = []
    for oq, or_ in _OUTER_OFFSETS:
        for mq, mr in _MID_OFFSETS:
            gq, gr = oq * SECTOR_SPACING + mq, or_ * SECTOR_SPACING + mr
            if (gq, gr) not in seen:
                seen.add((gq, gr))
                coords.append((gq, gr))
    return coords

GRID_COORDS: List[Tuple[int, int]] = _build_grid()
GRID_SET:    Set[Tuple[int, int]]   = set(GRID_COORDS)
GRID_SIZE = len(GRID_COORDS)   # 703


# ── Key helpers ────────────────────────────────────────────────────────────────

def hex_key(gq: int, gr: int) -> str:
    return f"{gq},{gr}"

def parse_hex(key: str) -> Tuple[int, int]:
    q, r = key.split(",")
    return int(q), int(r)

def is_valid(key: str) -> bool:
    try:
        return parse_hex(key) in GRID_SET
    except Exception:
        return False


# ── Pixel geometry ─────────────────────────────────────────────────────────────

def hex_center(gq: int, gr: int, size: float,
               ox: float = 0, oy: float = 0) -> Tuple[float, float]:
    """Axial → pixel centre, flat-top hex."""
    x = size * (3 / 2 * gq)
    y = size * (math.sqrt(3) / 2 * gq + math.sqrt(3) * gr)
    return x + ox, y + oy


def hex_corners(cx: float, cy: float, size: float) -> List[Tuple[float, float]]:
    return [
        (cx + size * math.cos(math.radians(60 * i)),
         cy + size * math.sin(math.radians(60 * i)))
        for i in range(6)
    ]


# ── Adjacency & distance ───────────────────────────────────────────────────────

def hex_neighbors(key: str) -> List[str]:
    """Valid grid neighbors of a hex."""
    gq, gr = parse_hex(key)
    return [hex_key(gq + dq, gr + dr)
            for dq, dr in DIRECTIONS
            if (gq + dq, gr + dr) in GRID_SET]


def hex_distance(a: str, b: str) -> int:
    """Axial distance between two hex keys."""
    aq, ar = parse_hex(a)
    bq, br = parse_hex(b)
    return (abs(aq - bq) + abs(aq + ar - bq - br) + abs(ar - br)) // 2


def hex_ring_keys(center: str, radius: int) -> List[str]:
    """All valid grid hexes exactly `radius` steps from center."""
    cq, cr = parse_hex(center)
    results = []
    for coord in hex_ring(radius):
        gq, gr = cq + coord[0], cr + coord[1]
        if (gq, gr) in GRID_SET:
            results.append(hex_key(gq, gr))
    return results


def hexes_within(center: str, radius: int) -> List[str]:
    """All valid grid hexes within `radius` steps of center (inclusive)."""
    results = []
    for r in range(radius + 1):
        results.extend(hex_ring_keys(center, r))
    return results


def nearest_hex(from_key: str, candidates: List[str]) -> Optional[str]:
    """Return the candidate hex closest to from_key."""
    if not candidates:
        return None
    return min(candidates, key=lambda k: hex_distance(from_key, k))


def step_toward(from_key: str, to_key: str) -> str:
    """
    Return the neighbor of from_key that is one step closer to to_key.
    Uses the axial direction with the smallest resulting distance.
    """
    fq, fr = parse_hex(from_key)
    best   = from_key
    best_d = hex_distance(from_key, to_key)
    for dq, dr in DIRECTIONS:
        nq, nr = fq + dq, fr + dr
        if (nq, nr) in GRID_SET:
            nk = hex_key(nq, nr)
            d  = hex_distance(nk, to_key)
            if d < best_d:
                best_d = d
                best   = nk
    return best


# ── Outermost hexes (for enemy spawns) ────────────────────────────────────────

def outermost_hexes() -> List[str]:
    """Hexes on the outermost ring of the grid (max axial distance from 0,0)."""
    max_d = max(
        (abs(gq) + abs(gr) + abs(gq + gr)) // 2
        for gq, gr in GRID_COORDS
    )
    return [hex_key(gq, gr)
            for gq, gr in GRID_COORDS
            if (abs(gq) + abs(gr) + abs(gq + gr)) // 2 == max_d]


# ── Status constants ───────────────────────────────────────────────────────────

STATUS_PLAYER     = "players"
STATUS_ENEMY      = "enemy"
STATUS_CONTESTED  = "contested"
STATUS_NEUTRAL    = "neutral"
STATUS_MAJ_PLAYER = "majority_player"
STATUS_MAJ_ENEMY  = "majority_enemy"


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def ensure_hexes(guild_id: int, conn, planet_id: int = 1):
    """Insert all 703 hexes for a guild/planet if not already present."""
    for gq, gr in GRID_COORDS:
        await conn.execute("""
            INSERT INTO hexes (guild_id, planet_id, address, controller, status)
            VALUES ($1, $2, $3, 'neutral', 'neutral')
            ON CONFLICT (guild_id, planet_id, address) DO NOTHING
        """, guild_id, planet_id, hex_key(gq, gr))


async def recompute_statuses(conn, guild_id: int, planet_id: int):
    """
    Recompute per-hex status from controller.
    In the flat system there is no aggregation — status mirrors controller directly,
    with 'contested' when both player and enemy units share the hex.
    Called after combat resolution.
    """
    # Contested hexes: have both player and enemy units
    p_hexes = {r["hex_address"] for r in await conn.fetch(
        "SELECT hex_address FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
        guild_id, planet_id)}
    e_hexes = {r["hex_address"] for r in await conn.fetch(
        "SELECT hex_address FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)}

    contested = p_hexes & e_hexes
    p_only    = p_hexes - e_hexes
    e_only    = e_hexes - p_hexes

    for addr in contested:
        await conn.execute(
            "UPDATE hexes SET controller='contested', status='contested' "
            "WHERE guild_id=$1 AND planet_id=$2 AND address=$3",
            guild_id, planet_id, addr)
    for addr in p_only:
        await conn.execute(
            "UPDATE hexes SET controller='players', status='players' "
            "WHERE guild_id=$1 AND planet_id=$2 AND address=$3",
            guild_id, planet_id, addr)
    for addr in e_only:
        await conn.execute(
            "UPDATE hexes SET controller='enemy', status='enemy' "
            "WHERE guild_id=$1 AND planet_id=$2 AND address=$3",
            guild_id, planet_id, addr)
