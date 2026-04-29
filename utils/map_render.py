"""
Warbot — Map Renderer v3
Flat global hex grid. Each hex label shows its unique global "gq,gr".
Option A unit markers: small colored squares at bottom edge of hex.
All text ASCII-safe (DejaVu / Liberation fonts only).
"""

import io
import math
import random
from PIL import Image, ImageDraw, ImageFont

from utils.hexmap import GRID_COORDS, hex_key, hex_center, hex_corners, GRID_SET, hexes_within, hex_distance
from utils.brigades import BRIGADES

# ── Brigade colors (temporary visual markers) ────────────────────────────────
BRIGADE_COLORS = {
    "infantry":     (220, 220, 220),
    "armoured":     (120, 180, 255),
    "artillery":    (255, 120, 120),
    "aerial":       (180, 255, 255),
    "ranger":       (140, 220, 140),
    "engineering":  (255, 200, 120),
    "special_ops":  (200, 140, 255),
}

BRIGADE_ORDER = {key: idx for idx, key in enumerate(BRIGADES.keys())}

PLAYER_MARKER_FILL = (35, 72, 170)
PLAYER_MARKER_EDGE = (170, 210, 255)
ENEMY_MARKER_FILL = (205, 35, 35)

# ── Fog of War ─────────────────────────────────────────────────────────────────
# Enemy units are only visible to players if they are within this many hexes
# of any friendly (player) unit.  Increase to widen player vision.
FOG_VISION_RADIUS = 3

# ── Font loader ────────────────────────────────────────────────────────────────

def _font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _outlined_text(draw, xy, text, font, fill, outline=(0, 0, 0, 230)):
    x, y = xy
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def _centered_text(draw, box, text, font, fill, outline=(0, 0, 0, 230)):
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        x1, y1, x2, y2 = box
        x = x1 + ((x2 - x1) - tw) / 2
        y = y1 + ((y2 - y1) - th) / 2 - 1
        _outlined_text(draw, (x, y), text, font, fill, outline)
    except Exception:
        pass


def _brigade_badges(brigades_map, player_count):
    if brigades_map:
        return [
            (bk, cnt)
            for bk, cnt in sorted(
                brigades_map.items(),
                key=lambda item: (BRIGADE_ORDER.get(item[0], 999), item[0]),
            )
            if cnt > 0
        ]
    return [("infantry", player_count)] if player_count > 0 else []


def _has_unit_markers(unit_data, key):
    units = unit_data.get(key, {})
    brigades_map = units.get("brigades", {})
    player_count = sum(brigades_map.values()) if brigades_map else units.get("players", 0)
    enemy_count = units.get("enemy", 0)
    return player_count > 0 or enemy_count > 0


def _hex_label_positions(cx, cy, occupied=False):
    if occupied:
        return {
            "terrain_x": cx,
            "terrain_y": cy - HEX_SIZE * 0.72,
            "coord_x": cx,
            "coord_y": cy - HEX_SIZE * 0.24,
            "cornered": False,
        }
    return {
        "terrain_x": cx,
        "terrain_y": cy - HEX_SIZE * 0.52,
        "coord_x": cx,
        "coord_y": cy,
        "cornered": False,
    }


def _draw_brigade_icon(draw, brigade_key, box, fill):
    x1, y1, x2, y2 = box
    w = x2 - x1
    h = y2 - y1
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    stroke = max(1, int(min(w, h) / 5))
    dark = (18, 18, 18, 255)

    if brigade_key in ("armoured", "armor"):
        draw.ellipse((x1 + w*.08, y1 + h*.28, x2 - w*.08, y2 - h*.20), fill=fill, outline=dark, width=stroke)
        draw.line((x1 + w*.25, cy, x2 - w*.25, cy), fill=dark, width=stroke)
    elif brigade_key == "infantry":
        draw.line((x1 + w*.18, y1 + h*.18, x2 - w*.18, y2 - h*.18), fill=fill, width=stroke)
        draw.line((x2 - w*.18, y1 + h*.18, x1 + w*.18, y2 - h*.18), fill=fill, width=stroke)
    elif brigade_key == "artillery":
        draw.ellipse((x1 + w*.22, y1 + h*.18, x2 - w*.22, y2 - h*.18), outline=fill, width=stroke)
        draw.line((x1 + w*.12, cy, x2 - w*.12, cy), fill=fill, width=stroke)
    elif brigade_key in ("ranger", "recon"):
        draw.line((x1 + w*.15, y2 - h*.25, cx, y1 + h*.20), fill=fill, width=stroke)
        draw.line((cx, y1 + h*.20, x2 - w*.15, y2 - h*.25), fill=fill, width=stroke)
    elif brigade_key in ("command", "hq"):
        pole_x = x1 + w*.28
        draw.line((pole_x, y1 + h*.15, pole_x, y2 - h*.15), fill=fill, width=stroke)
        draw.polygon([(pole_x, y1 + h*.18), (x2 - w*.12, y1 + h*.28), (pole_x, y1 + h*.50)], fill=fill)
    elif brigade_key == "engineering":
        draw.rectangle((x1 + w*.18, y1 + h*.45, x2 - w*.18, y2 - h*.18), fill=fill, outline=dark, width=stroke)
        draw.line((x1 + w*.26, y1 + h*.22, x2 - w*.18, y2 - h*.18), fill=fill, width=stroke)
    elif brigade_key == "aerial":
        draw.polygon([(cx, y1 + h*.12), (x2 - w*.12, y2 - h*.22), (cx, y2 - h*.42), (x1 + w*.12, y2 - h*.22)], fill=fill, outline=dark)
    elif brigade_key == "special_ops":
        draw.polygon([(cx, y1 + h*.10), (x2 - w*.12, cy), (cx, y2 - h*.10), (x1 + w*.12, cy)], fill=fill, outline=dark)
    else:
        draw.polygon([(cx, y1 + h*.12), (x2 - w*.12, y2 - h*.12), (x1 + w*.12, y2 - h*.12)], fill=fill, outline=dark)


def _draw_stack_count(draw, x, y, count, radius, font):
    if count <= 0:
        return
    text = str(count)
    r = max(5, int(radius))
    draw.ellipse((x - r, y - r, x + r, y + r), fill=(20, 20, 20, 245), outline=(245, 245, 210, 255), width=1)
    _centered_text(draw, (x - r, y - r, x + r, y + r), text, font, fill=(255, 255, 215, 255), outline=(0, 0, 0, 240))


def _draw_enemy_presence(draw, x, y, enemy_count, size, font):
    if enemy_count <= 0:
        return
    r = max(5, int(size))
    pts = [(x, y - r), (x - r, y + r), (x + r, y + r)]
    draw.polygon(pts, fill=(*ENEMY_MARKER_FILL, 255), outline=(255, 200, 200, 255))
    if enemy_count > 1:
        _centered_text(draw, (x - r, y - r * 0.45, x + r, y + r * 1.1), str(enemy_count), font, fill=(255, 255, 255, 255), outline=(80, 0, 0, 240))


def _draw_unit_stack_marker(draw, cx, cy, brigades_map, player_count, enemy_count, font, size=None):
    if size is None:
        size = HEX_SIZE
    badges = _brigade_badges(brigades_map, player_count)
    has_players = player_count > 0
    marker_w = max(28, int(size * 1.35))
    marker_h = max(18, int(size * 0.66))
    marker_x = cx - marker_w / 2
    marker_y = cy + size * 0.17

    if has_players:
        draw.rounded_rectangle(
            (marker_x, marker_y, marker_x + marker_w, marker_y + marker_h),
            radius=max(3, int(size * 0.10)),
            fill=(*PLAYER_MARKER_FILL, 245),
            outline=(*PLAYER_MARKER_EDGE, 255),
            width=2,
        )
        inner_pad = max(3, int(size * 0.10))
        icon_area = (marker_x + inner_pad, marker_y + inner_pad, marker_x + marker_w - inner_pad, marker_y + marker_h - inner_pad)
        visible = badges[:5]
        overflow = len(badges) > 5
        if overflow:
            visible = badges[:4]

        if marker_w < 28 or marker_h < 18:
            # Tight fallback: simple mixed stack dot plus count.
            draw.ellipse((cx - 4, marker_y + 5, cx + 4, marker_y + 13), fill=(235, 235, 235, 255))
            if len(badges) > 1:
                draw.line((cx - 5, marker_y + 14, cx + 5, marker_y + 4), fill=(20, 20, 20, 255), width=1)
        else:
            n = max(1, len(visible) + (1 if overflow else 0))
            gap = max(1, int(size * 0.04))
            cell_w = ((icon_area[2] - icon_area[0]) - gap * (n - 1)) / n
            cell_h = icon_area[3] - icon_area[1]
            for idx, (brigade_key, _cnt) in enumerate(visible):
                ix1 = icon_area[0] + idx * (cell_w + gap)
                ix2 = ix1 + cell_w
                color = BRIGADE_COLORS.get(brigade_key, (220, 220, 220))
                _draw_brigade_icon(draw, brigade_key, (ix1, icon_area[1], ix2, icon_area[1] + cell_h), (*color, 255))
            if overflow:
                plus_idx = len(visible)
                px1 = icon_area[0] + plus_idx * (cell_w + gap)
                px2 = px1 + cell_w
                _centered_text(draw, (px1, icon_area[1], px2, icon_area[3]), "+", font, fill=(255, 255, 255, 255), outline=(0, 0, 0, 240))

        count_r = max(5, size * 0.16)
        _draw_stack_count(draw, marker_x + marker_w - count_r, marker_y + marker_h - count_r, player_count, count_r, font)
        if enemy_count > 0:
            _draw_enemy_presence(draw, marker_x + marker_w - max(7, size * 0.18), marker_y + max(6, size * 0.16), enemy_count, max(5, size * 0.15), font)
    elif enemy_count > 0:
        # Enemy-only hex: keep the red indicator inside the lower-right interior.
        _draw_enemy_presence(draw, cx + size * 0.22, cy + size * 0.44, enemy_count, max(6, size * 0.20), font)

_SANS    = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
_SERIF   = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
_MONO    = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
_SANSREG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]

# ── Terrain ────────────────────────────────────────────────────────────────────

HEX_DIRECTIONS = [
    (1, 0), (1, -1), (0, -1),
    (-1, 0), (-1, 1), (0, 1),
]

TERRAIN_TYPES = ["plains", "forest", "hills", "mountain", "fort", "city", "military", "water"]

TERRAIN_ALIASES = {
    "flat": "plains",
    "plain": "plains",
    "plains": "plains",
    "forest": "forest",
    "hill": "hills",
    "hills": "hills",
    "mountain": "mountain",
    "mtn": "mountain",
    "fort": "fort",
    "city": "city",
    "military": "military",
    "water": "water",
    "ocean": "water",
    "sea": "water",
}

TERRAIN_DEFS = {
    "plains":   {"color": (200, 200, 200), "label": "Plains"},
    "forest":   {"color": (170, 170, 170), "label": "Forest"},
    "hills":    {"color": (150, 150, 150), "label": "Hills"},
    "mountain": {"color": (110, 110, 110), "label": "Mountain"},
    "city":     {"color": (180, 180, 180), "label": "City"},
    "fort":     {"color": (160, 160, 160), "label": "Fort"},
    "military": {"color": (140, 140, 140), "label": "Military"},
    "water":    {"color": (90, 90, 100),   "label": "Water"},
}

COASTLINE = (188, 188, 176, 255)
COASTLINE_DARK = (82, 82, 76, 230)

STATUS_TINTS = {
    "players":         (70,  90, 210, 78),
    "majority_player": (100, 120, 195, 52),
    "enemy":           (210,  55,  55, 78),
    "majority_enemy":  (190,  90,  90, 52),
    "contested":       (155,  75, 155, 68),
    "neutral":         (0,     0,   0,  0),
}


def _terrain_key(terrain_type):
    return TERRAIN_ALIASES.get(str(terrain_type or "plains").lower(), "plains")


def get_neighbors(q, r):
    return [(q + dq, r + dr) for dq, dr in HEX_DIRECTIONS]


def _axial_distance(a, b):
    aq, ar = a
    bq, br = b
    return max(abs(aq - bq), abs(ar - br), abs((aq + ar) - (bq + br)))


def _edge_scores(coords):
    return {
        c: max(abs(c[0]), abs(c[1]), abs(c[0] + c[1]))
        for c in coords
    }


def generate_biome_cluster(
    grid_coords,
    terrain_type,
    seed_count,
    target_size,
    edge_bias=False,
    seed=None,
    blocked=None,
    seed_candidates=None,
):
    coords = list(grid_coords)
    coord_set = set(coords)
    blocked = set(blocked or [])
    available = [c for c in coords if c not in blocked]
    if not available or target_size <= 0:
        return set()

    rng = random.Random(seed if seed is not None else len(coords) * 3251 + len(terrain_type))
    target = min(len(available), max(1, int(target_size)))
    edge_score = _edge_scores(coords)
    candidates = [c for c in (seed_candidates or available) if c in coord_set and c not in blocked]
    if not candidates:
        candidates = available
    if edge_bias:
        candidates = sorted(candidates, key=lambda c: edge_score[c], reverse=True)
    else:
        rng.shuffle(candidates)

    seeds = []
    for candidate in candidates:
        if len(seeds) >= max(1, seed_count):
            break
        min_gap = 4 if len(coords) > 200 else 2
        if not seeds or all(_axial_distance(candidate, s) >= min_gap for s in seeds):
            seeds.append(candidate)
    while len(seeds) < max(1, seed_count):
        seeds.append(rng.choice(candidates))

    cluster = set(seeds[:target])
    frontier = list(cluster)
    while frontier and len(cluster) < target:
        origin = rng.choice(frontier)
        neighbors = [n for n in get_neighbors(*origin) if n in coord_set and n not in blocked]
        weighted = []
        for n in neighbors:
            same_neighbors = sum((nn in cluster) for nn in get_neighbors(*n))
            weight = 1 + same_neighbors * 5
            if edge_bias:
                weight += max(0, edge_score[n] - 10)
            weighted.extend([n] * weight)
        if not weighted:
            try:
                frontier.remove(origin)
            except ValueError:
                pass
            continue
        picked = rng.choice(weighted)
        if picked not in cluster:
            cluster.add(picked)
            frontier.append(picked)
        elif all(n in cluster or n in blocked for n in neighbors):
            try:
                frontier.remove(origin)
            except ValueError:
                pass
    return cluster


def generate_water_bodies(grid_coords, water_ratio=0.06, body_count=2, seed=None):
    coords = list(grid_coords)
    coord_set = set(coords)
    if not coords:
        return set()
    rng = random.Random(seed if seed is not None else len(coords) * 7919)
    target = max(1, int(len(coords) * water_ratio))
    edge_score = _edge_scores(coords)
    edge_candidates = sorted(coords, key=lambda c: edge_score[c], reverse=True)
    seeds = []
    for candidate in edge_candidates:
        if len(seeds) >= body_count:
            break
        if not seeds or all(_axial_distance(candidate, s) > 5 for s in seeds):
            seeds.append(candidate)
    while len(seeds) < body_count:
        seeds.append(rng.choice(edge_candidates))

    water = set(seeds)
    frontier = list(seeds)
    while frontier and len(water) < target:
        q, r = rng.choice(frontier)
        neighbors = [(nq, nr) for nq, nr in get_neighbors(q, r) if (nq, nr) in coord_set]
        weighted = []
        for n in neighbors:
            adjacent_water = sum((nn in water) for nn in get_neighbors(*n))
            edge_bias = max(0, edge_score[n] - 10)
            weight = 1 + adjacent_water * 4 + edge_bias
            weighted.extend([n] * weight)
        if not weighted:
            frontier.remove((q, r))
            continue
        picked = rng.choice(weighted)
        if picked not in water:
            water.add(picked)
            frontier.append(picked)
        elif all(n in water for n in neighbors):
            try:
                frontier.remove((q, r))
            except ValueError:
                pass

    cleaned = set()
    for q, r in water:
        water_neighbors = sum((n in water) for n in get_neighbors(q, r))
        if water_neighbors > 1:
            cleaned.add((q, r))
    return cleaned or water


def cleanup_isolated_tiles(terrain_map, protected=None):
    protected = set(protected or [])
    coords = set(terrain_map.keys())
    cleaned = dict(terrain_map)
    for coord, terrain in terrain_map.items():
        if terrain == "plains" or terrain in protected:
            continue
        neighbors = [n for n in get_neighbors(*coord) if n in coords]
        same = sum(cleaned.get(n, "plains") == terrain for n in neighbors)
        if same > 0:
            continue
        neighbor_counts = {}
        for n in neighbors:
            nt = cleaned.get(n, "plains")
            if nt in protected:
                continue
            neighbor_counts[nt] = neighbor_counts.get(nt, 0) + 1
        if neighbor_counts:
            cleaned[coord] = max(neighbor_counts.items(), key=lambda item: item[1])[0]
        else:
            cleaned[coord] = "plains"
    return cleaned


def smooth_biome_terrain(terrain_map, passes=1, protected=None):
    protected = set(protected or [])
    coords = set(terrain_map.keys())
    smoothed = dict(terrain_map)
    for _ in range(max(0, passes)):
        next_map = dict(smoothed)
        for coord, terrain in smoothed.items():
            if terrain in protected:
                continue
            counts = {}
            for n in get_neighbors(*coord):
                if n not in coords:
                    continue
                nt = smoothed.get(n, "plains")
                if nt in protected:
                    continue
                counts[nt] = counts.get(nt, 0) + 1
            if not counts:
                continue
            dominant, count = max(counts.items(), key=lambda item: item[1])
            if count >= 3 and dominant in {"plains", "forest", "hills", "mountain", "water"}:
                next_map[coord] = dominant
        smoothed = next_map
    return smoothed


def generate_biome_terrain_map(grid_coords, seed=None):
    coords = list(grid_coords)
    if not coords:
        return {}
    rng = random.Random(seed if seed is not None else len(coords) * 104729)
    total = len(coords)
    terrain = {coord: "plains" for coord in coords}

    water = generate_water_bodies(coords, water_ratio=0.06, body_count=rng.randint(1, 3), seed=rng.randint(1, 10**9))
    for coord in water:
        terrain[coord] = "water"

    blocked = set(water)
    forests = generate_biome_cluster(coords, "forest", seed_count=5, target_size=total * 0.16, seed=rng.randint(1, 10**9), blocked=blocked)
    for coord in forests:
        terrain[coord] = "forest"

    blocked |= forests
    hills = generate_biome_cluster(coords, "hills", seed_count=4, target_size=total * 0.13, seed=rng.randint(1, 10**9), blocked=set(water))
    for coord in hills:
        if terrain[coord] != "water":
            terrain[coord] = "hills"

    hill_neighbors = {
        n
        for coord in hills
        for n in get_neighbors(*coord)
        if n in terrain and terrain[n] not in {"water", "forest"}
    } or {coord for coord in coords if terrain[coord] == "plains"}
    mountains = generate_biome_cluster(
        coords,
        "mountain",
        seed_count=3,
        target_size=total * 0.055,
        seed=rng.randint(1, 10**9),
        blocked=set(water) | forests,
        seed_candidates=hill_neighbors,
    )
    for coord in mountains:
        if terrain[coord] != "water":
            terrain[coord] = "mountain"

    terrain = cleanup_isolated_tiles(terrain)
    terrain = smooth_biome_terrain(terrain, passes=1, protected={"city", "fort", "military"})
    for coord in water:
        terrain[coord] = "water"

    land = [coord for coord in coords if terrain[coord] != "water"]
    rng.shuffle(land)
    special_plan = [
        ("city", max(2, total // 95)),
        ("military", max(2, total // 120)),
        ("fort", max(2, total // 140)),
    ]
    used_special = set()
    for special, count in special_plan:
        placed = 0
        for coord in land:
            if placed >= count:
                break
            if coord in used_special:
                continue
            if any(_axial_distance(coord, other) < 3 for other in used_special):
                continue
            terrain[coord] = special
            used_special.add(coord)
            placed += 1
    return terrain


def _shade_color(color, amount):
    return tuple(max(0, min(255, int(c + amount))) for c in color)


def _terrain_variation(key):
    # Deterministic per-hex variation, so regenerated maps do not shimmer.
    seed = sum((idx + 1) * ord(ch) for idx, ch in enumerate(key))
    rng = random.Random(seed)
    value = rng.randint(-7, 7)
    return (value, value, value)


def _terrain_border(fill):
    return tuple(max(0, int(c * 0.68)) for c in fill)


def _edge_points(hex_points, direction_index):
    # PIL hex corner order from hex_corners is clockwise-ish; this maps axial
    # directions to the corresponding visible side well enough for coastlines.
    edge_map = {
        0: (5, 0),  # east
        1: (4, 5),  # north-east
        2: (3, 4),  # north-west
        3: (2, 3),  # west
        4: (1, 2),  # south-west
        5: (0, 1),  # south-east
    }
    a, b = edge_map[direction_index]
    return hex_points[a], hex_points[b]


def draw_terrain_icon(draw, center, terrain_type, size):
    terrain = _terrain_key(terrain_type)
    cx, cy = center
    s = max(6, int(size * 0.22))

    if terrain == "forest":
        return
    elif terrain == "mountain":
        draw.polygon(
            [(cx - s * .58, cy + s * .30), (cx - s * .08, cy - s * .55), (cx + s * .22, cy + s * .30)],
            fill=(42, 42, 42, 82),
        )
        draw.polygon(
            [(cx - s * .12, cy + s * .32), (cx + s * .36, cy - s * .46), (cx + s * .68, cy + s * .32)],
            fill=(58, 58, 58, 76),
        )
        draw.polygon([(cx - s * .16, cy - s * .42), (cx - s * .08, cy - s * .55), (cx, cy - s * .36)], fill=(220, 220, 220, 58))
        draw.polygon([(cx + s * .28, cy - s * .34), (cx + s * .36, cy - s * .46), (cx + s * .44, cy - s * .30)], fill=(220, 220, 220, 58))
    elif terrain == "hills":
        return
    elif terrain == "city":
        col = (52, 52, 52, 78)
        draw.rectangle((cx - s * .45, cy - s * .10, cx - s * .18, cy + s * .28), fill=col)
        draw.rectangle((cx - s * .10, cy - s * .32, cx + s * .12, cy + s * .28), fill=(48, 48, 48, 84))
        draw.rectangle((cx + s * .20, cy - s * .02, cx + s * .46, cy + s * .28), fill=col)
    elif terrain == "fort":
        draw.rectangle((cx - s * .42, cy - s * .12, cx + s * .42, cy + s * .18), fill=(45, 45, 45, 66))
        draw.line((cx - s * .28, cy - s * .22, cx + s * .28, cy - s * .22), fill=(70, 70, 70, 58), width=1)
    elif terrain == "military":
        draw.arc((cx - s * .38, cy - s * .32, cx + s * .38, cy + s * .40), start=195, end=345, fill=(42, 42, 42, 56), width=1)
        draw.rectangle((cx - s * .34, cy + s * .08, cx + s * .34, cy + s * .20), fill=(42, 42, 42, 54))
    elif terrain == "water":
        wave = (190, 190, 200, 64)
        for oy in (s * .02,):
            pts = [
                (cx - s * .42, cy + oy),
                (cx - s * .16, cy + oy - s * .10),
                (cx + s * .10, cy + oy),
                (cx + s * .36, cy + oy - s * .10),
            ]
            draw.line(pts, fill=wave, width=1, joint="curve")
    elif terrain == "plains":
        return


def draw_terrain_hex(draw, hex_points, terrain_type, center):
    terrain = _terrain_key(terrain_type)
    base = TERRAIN_DEFS[terrain]["color"]
    key = f"{int(center[0])},{int(center[1])}:{terrain}"
    variation = _terrain_variation(key)
    fill = tuple(max(0, min(255, base[idx] + variation[idx])) for idx in range(3))
    border = _terrain_border(fill)

    draw.polygon(hex_points, fill=(*fill, 255))

    draw_terrain_icon(draw, center, terrain, HEX_SIZE)
    draw.polygon(hex_points, outline=(*border, 255), width=1)


def draw_coastline(draw, hex_points, neighbors, terrain_type):
    if _terrain_key(terrain_type) == "water":
        return
    for idx, neighbor_terrain in enumerate(neighbors):
        if _terrain_key(neighbor_terrain) != "water":
            continue
        p1, p2 = _edge_points(hex_points, idx)
        draw.line((p1[0], p1[1], p2[0], p2[1]), fill=COASTLINE_DARK, width=3)
        draw.line((p1[0], p1[1], p2[0], p2[1]), fill=COASTLINE, width=2)

# ── Layout ─────────────────────────────────────────────────────────────────────

HEX_SIZE  = 32
PADDING   = 28
TITLE_H   = 54
LEGEND_H  = 100


# ── Planet map ─────────────────────────────────────────────────────────────────

def render_planet_map(
    planet_name:     str,
    contractor:      str,
    enemy_type:      str,
    hex_data:        dict,   # {"gq,gr": {"terrain": str, "status": str}}
    unit_data:       dict,   # {"gq,gr": {"players": N, "enemy": N}}
    turn_number:     int = 1,
    theme:           dict = None,
    movement_arrows: list = None,  # [(from_addr, to_addr, "player"|"enemy"), ...]
) -> io.BytesIO:

    if theme is None:
        theme = _default_theme()

    # Canvas from bounding box
    px_all = [hex_center(gq, gr, HEX_SIZE) for gq, gr in GRID_COORDS]
    xs = [p[0] for p in px_all]; ys = [p[1] for p in px_all]
    grid_w = int(max(xs) - min(xs) + HEX_SIZE*2 + PADDING*2)
    grid_h = int(max(ys) - min(ys) + HEX_SIZE*2 + PADDING*2)
    img_w  = grid_w
    img_h  = grid_h + TITLE_H + LEGEND_H

    ox = int(img_w/2 - (max(xs)+min(xs))/2)
    oy = int(TITLE_H + PADDING + grid_h/2 - (max(ys)+min(ys))/2)

    BG  = 20
    img = Image.new("RGBA", (img_w, img_h), (BG, BG, BG, 255))
    draw = ImageDraw.Draw(img)

    f_title  = _font(_SERIF,   20)
    f_abbr   = _font(_SANS,     8)
    f_coord  = _font(_MONO,     6)
    f_pip    = _font(_SANS,     7)
    f_legend = _font(_SANSREG, 11)

    # Draw all hexes: terrain, status tint, coastline, then coordinates.
    for gq, gr in GRID_COORDS:
        key     = hex_key(gq, gr)
        cx, cy  = hex_center(gq, gr, HEX_SIZE, ox, oy)
        info    = hex_data.get(key, {})
        terrain = _terrain_key(info.get("terrain", "plains"))
        status  = info.get("status",  "neutral")
        corners = hex_corners(cx, cy, HEX_SIZE - 0.8)
        occupied = _has_unit_markers(unit_data, key)
        label_pos = _hex_label_positions(cx, cy, occupied)

        draw_terrain_hex(draw, corners, terrain, (cx, cy))

        tint = STATUS_TINTS.get(status, (0,0,0,0))
        if tint[3] > 0:
            tl = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
            ImageDraw.Draw(tl).polygon(corners, fill=tint)
            img  = Image.alpha_composite(img, tl)
            draw = ImageDraw.Draw(img)

        neighbor_terrains = []
        for nq, nr in get_neighbors(gq, gr):
            nkey = hex_key(nq, nr)
            neighbor_terrains.append(hex_data.get(nkey, {}).get("terrain", "plains"))
        draw_coastline(draw, corners, neighbor_terrains, terrain)

        lbl = key
        try:
            bb  = draw.textbbox((0,0), lbl, font=f_coord)
            lw  = (bb[2]-bb[0])/2
            lh  = (bb[3]-bb[1])/2
            lx2 = label_pos["coord_x"] if label_pos["cornered"] else cx - lw
            label_cy = label_pos["coord_y"]
            ly2 = label_cy - lh
            light_tile = terrain in ("plains", "city", "hills", "forest", "fort")
            shadow = (255,255,255,24) if not light_tile else (0,0,0,18)
            fill = (26, 26, 26, 78) if light_tile else (225, 225, 225, 84)
            draw.text((lx2+1, ly2+1), lbl, font=f_coord, fill=shadow)
            draw.text((lx2, ly2), lbl, font=f_coord, fill=fill)
        except Exception:
            pass

    # ── Draw unit markers in a separate pass so tint compositing can't erase them ──
    draw = ImageDraw.Draw(img)
    for gq, gr in GRID_COORDS:
        key    = hex_key(gq, gr)
        cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
        units  = unit_data.get(key, {})
        # brigades: dict of {brigade_key: count}  (new format)
        # Fall back gracefully if caller passes old-style {"players": N}
        brigades_map = units.get("brigades", {})
        p_ct         = sum(brigades_map.values()) if brigades_map else units.get("players", 0)
        e_ct         = units.get("enemy", 0)

        if p_ct > 0 or e_ct > 0:
            _draw_unit_stack_marker(draw, cx, cy, brigades_map, p_ct, e_ct, f_pip)


    # ── Title ─────────────────────────────────────────────────────────────────
    draw.rectangle((0,0,img_w,TITLE_H), fill=(10,10,10,255))
    draw.line((28,TITLE_H-2,img_w-28,TITLE_H-2), fill=(75,75,75,255), width=2)
    title = (f"{theme.get('bot_name','WARBOT')}  |  {planet_name}  |  "
             f"Contractor: {contractor}  |  Enemy: {enemy_type}  |  Turn {turn_number}")
    try:
        bb = draw.textbbox((0,0), title, font=f_title)
        tw,th = bb[2]-bb[0],bb[3]-bb[1]
        draw.text(((img_w-tw)//2,(TITLE_H-th)//2), title, font=f_title,
                  fill=(210,210,210,255))
    except Exception:
        pass

    # ── Legend ────────────────────────────────────────────────────────────────
    ly = img_h - LEGEND_H
    draw.rectangle((0,ly,img_w,img_h), fill=(10,10,10,255))
    draw.line((28,ly+1,img_w-28,ly+1), fill=(75,75,75,255), width=1)

    t_items = [(TERRAIN_DEFS[t]["color"], TERRAIN_DEFS[t]["label"], t) for t in TERRAIN_TYPES]
    s_items = [
        (72, 90,205, f"{theme.get('player_faction','PMC')} ctrl"),
        (205,52, 52, f"{theme.get('enemy_faction','Enemy')} ctrl"),
        (152,72,152, "Contested"),
        (115,115,115,"Neutral"),
    ]
    lx   = 28
    cw_t = (img_w-56) // len(t_items)
    cw_s = (img_w-56) // len(s_items)

    for i,(color,lbl,tkey) in enumerate(t_items):
        x = lx+i*cw_t
        draw.rectangle((x,ly+10,x+14,ly+24), fill=(*color,255), outline=(*_terrain_border(color),255))
        draw_terrain_icon(draw, (x+7, ly+17), tkey, 20)
        try: draw.text((x+18,ly+10), lbl, font=f_legend, fill=(162,162,162,255))
        except Exception: pass

    for i,(ri,gi,bi,lbl) in enumerate(s_items):
        x = lx+i*cw_s
        draw.rectangle((x,ly+46,x+14,ly+60), fill=(ri,gi,bi,255), outline=(105,105,105,255))
        try: draw.text((x+18,ly+46), lbl, font=f_legend, fill=(162,162,162,255))
        except Exception: pass

    _draw_unit_stack_marker(
        draw,
        lx + 18,
        ly + 58,
        {"infantry": 1, "armoured": 1, "artillery": 1},
        3,
        0,
        f_pip,
        size=24,
    )
    tri_x = lx + 380
    tri_y = ly + 72
    _draw_enemy_presence(draw, tri_x, tri_y, 2, 6, f_pip)
    try:
        draw.text((lx+44,    ly+65),
                  f"= {theme.get('player_unit','PMC')} units",
                  font=f_legend, fill=(162,162,162,255))
        draw.text((tri_x+14, ly+65),
                  f"= {theme.get('enemy_unit','Enemy')} units",
                  font=f_legend, fill=(162,162,162,255))
    except Exception:
        pass

    try:
        fl = (f"Hex label = global coord (gq,gr)  |  "
              f"{theme.get('flavor_text','')}")
        bb  = draw.textbbox((0,0), fl, font=f_legend)
        fw  = bb[2]-bb[0]
        draw.text(((img_w-fw)//2, ly+86), fl, font=f_legend, fill=(90,90,90,255))
    except Exception:
        pass

    # ── Movement arrows overlay ───────────────────────────────────────────────
    if movement_arrows:
        draw = ImageDraw.Draw(img)
        f_arrow = _font(_SANS, 7)
        for (from_addr, to_addr, side) in movement_arrows:
            try:
                fq, fr = map(int, from_addr.split(","))
                tq, tr = map(int, to_addr.split(","))
                fcx, fcy = hex_center(fq, fr, HEX_SIZE, ox, oy)
                tcx, tcy = hex_center(tq, tr, HEX_SIZE, ox, oy)
                dx = tcx - fcx
                dy = tcy - fcy
                dist = math.sqrt(dx*dx + dy*dy) or 1
                ux, uy = dx/dist, dy/dist
                # Offset start/end so arrows don't overlap unit markers
                sx = fcx + ux * HEX_SIZE * 0.45
                sy = fcy + uy * HEX_SIZE * 0.45
                ex = tcx - ux * HEX_SIZE * 0.45
                ey = tcy - uy * HEX_SIZE * 0.45
                # Color: cyan for player, orange for enemy
                arrow_col = (80, 220, 255, 220) if side == "player" else (255, 160, 40, 220)
                outline_col = (0, 0, 0, 180)
                # Shaft
                for w, col in [(5, outline_col), (3, arrow_col)]:
                    draw.line((sx, sy, ex, ey), fill=col, width=w)
                # Arrowhead
                perp_x, perp_y = -uy, ux
                hs = HEX_SIZE * 0.32
                tip_x = tcx - ux * HEX_SIZE * 0.28
                tip_y = tcy - uy * HEX_SIZE * 0.28
                lx2 = ex + perp_x * hs * 0.5
                ly2 = ey + perp_y * hs * 0.5
                rx2 = ex - perp_x * hs * 0.5
                ry2 = ey - perp_y * hs * 0.5
                draw.polygon([(tip_x, tip_y), (lx2, ly2), (rx2, ry2)],
                             fill=arrow_col, outline=outline_col)
            except Exception:
                pass

    _compass(draw, img_w-50, TITLE_H+46, 18, f_legend)
    draw.rectangle((1,1,img_w-2,img_h-2), outline=(65,65,65,255), width=3)

    bg = Image.new("RGB", (img_w, img_h), (BG,BG,BG))
    bg.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    bg.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ── Planetary system overview ────────────────────────────────────────────────────────────

def _overview_value(planet, key, default="---"):
    if planet is None:
        return default
    try:
        return planet.get(key, default)
    except Exception:
        try:
            return planet[key]
        except Exception:
            return default


def _overview_text(draw, xy, text, font, fill, max_width=None):
    text = str(text or "---")
    if max_width:
        try:
            while len(text) > 3 and draw.textbbox((0, 0), text, font=font)[2] > max_width:
                text = text[:-4] + "..."
        except Exception:
            pass
    draw.text(xy, text, font=font, fill=fill)


def _draw_overview_starfield(draw, width, height, seed):
    rng = random.Random((seed or 1) * 7919 + width + height)
    count = max(70, int(width * height / 12000))
    for _ in range(count):
        x = rng.randint(18, width - 18)
        y = rng.randint(58, height - 24)
        lum = rng.randint(34, 92)
        if rng.random() < 0.12:
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(lum, lum, lum))
        else:
            draw.point((x, y), fill=(lum, lum, lum))


def _draw_overview_top_bar(draw, width, theme, active_planet, turn_number, fonts):
    draw.rectangle((0, 0, width, 58), fill=(10, 10, 11))
    draw.line((24, 56, width - 24, 56), fill=(74, 74, 74), width=1)
    title = f"{theme.get('bot_name', 'WARBOT')} | Interplanetary Theatre Overview"
    _overview_text(draw, (28, 17), title, fonts["title"], (218, 218, 218), max_width=560)

    info = [
        ("Contractor", _overview_value(active_planet, "contractor", theme.get("player_faction", "---"))),
        ("Enemy", _overview_value(active_planet, "enemy_type", theme.get("enemy_faction", "---"))),
        ("Turn", str(turn_number or 0)),
    ]
    box_w, box_h, gap = 158, 34, 8
    start_x = width - 28 - (box_w * len(info) + gap * (len(info) - 1))
    for idx, (label, value) in enumerate(info):
        x = start_x + idx * (box_w + gap)
        draw.rectangle((x, 12, x + box_w, 12 + box_h), fill=(15, 15, 16), outline=(56, 56, 56))
        _overview_text(draw, (x + 9, 16), label.upper(), fonts["mono"], (112, 112, 112), max_width=box_w - 18)
        _overview_text(draw, (x + 9, 29), value, fonts["small"], (205, 205, 205), max_width=box_w - 18)


def _draw_overview_active_panel(draw, box, active_planet, fonts):
    x1, y1, x2, y2 = box
    draw.rectangle(box, fill=(12, 12, 13), outline=(66, 66, 66))
    draw.rectangle((x1, y1, x1 + 5, y2), fill=(138, 138, 138))
    _overview_text(draw, (x1 + 18, y1 + 16), "ACTIVE THEATRE", fonts["mono"], (145, 145, 145), max_width=x2 - x1 - 36)
    _overview_text(draw, (x1 + 18, y1 + 42), _overview_value(active_planet, "name", "No theatre"), fonts["title"], (232, 232, 232), max_width=x2 - x1 - 36)

    fields = [
        ("Status", "ACTIVE" if active_planet else "NO SIGNAL"),
        ("Control", _overview_value(active_planet, "contractor")),
        ("Enemy Presence", _overview_value(active_planet, "enemy_type")),
        ("Fleets Deployed", str(_overview_value(active_planet, "player_units", 0))),
        ("Hostile Contacts", str(_overview_value(active_planet, "enemy_units", 0))),
    ]
    y = y1 + 96
    for label, value in fields:
        draw.line((x1 + 18, y - 8, x2 - 18, y - 8), fill=(34, 34, 34), width=1)
        _overview_text(draw, (x1 + 18, y), label.upper(), fonts["mono"], (104, 104, 104), max_width=110)
        _overview_text(draw, (x1 + 132, y - 1), value, fonts["body"], (206, 206, 206), max_width=x2 - x1 - 150)
        y += 42

    log_y = y2 - 92
    draw.rectangle((x1 + 18, log_y, x2 - 18, y2 - 18), fill=(8, 8, 9), outline=(40, 40, 40))
    _overview_text(draw, (x1 + 30, log_y + 12), "SYSTEM LOG", fonts["mono"], (110, 110, 110), max_width=x2 - x1 - 60)
    log = "Theatre telemetry stable. Awaiting next command cycle."
    _overview_text(draw, (x1 + 30, log_y + 36), log, fonts["small"], (172, 172, 172), max_width=x2 - x1 - 60)


def _draw_overview_node(draw, x, y, planet, is_active, fonts):
    r = 16 if is_active else 10
    if is_active:
        for grow, lum in [(22, 34), (16, 52), (10, 76)]:
            draw.ellipse((x - r - grow, y - r - grow, x + r + grow, y + r + grow), outline=(lum, lum, lum), width=1)
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(198, 198, 198), outline=(245, 245, 245), width=2)
        draw.ellipse((x - r - 7, y - r - 7, x + r + 7, y + r + 7), outline=(218, 218, 218), width=2)
    else:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(8, 8, 9), outline=(112, 112, 112), width=2)

    tick = 7 if is_active else 5
    col = (180, 180, 180) if is_active else (78, 78, 78)
    draw.line((x - r - tick, y, x - r - 2, y), fill=col, width=1)
    draw.line((x + r + 2, y, x + r + tick, y), fill=col, width=1)
    draw.line((x, y - r - tick, x, y - r - 2), fill=col, width=1)
    draw.line((x, y + r + 2, x, y + r + tick), fill=col, width=1)

    name = _overview_value(planet, "name", "Unknown")
    label_col = (224, 224, 224) if is_active else (126, 126, 126)
    _overview_text(draw, (x + r + 11, y - 10), name, fonts["body"], label_col, max_width=150)
    status = "ACTIVE" if is_active else "STANDBY"
    _overview_text(draw, (x + r + 11, y + 7), status, fonts["mono"], (150, 150, 150) if is_active else (78, 78, 78), max_width=120)


def _draw_overview_orbit_map(draw, planets, active_planet_id, box, fonts):
    x1, y1, x2, y2 = box
    draw.rectangle(box, outline=(38, 38, 38))
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2 + 4
    max_rx = max(170, int((x2 - x1) * 0.40))
    max_ry = max(125, int((y2 - y1) * 0.36))
    ring_fracs = [0.38, 0.62, 0.84]

    for frac in ring_fracs:
        rx = int(max_rx * frac)
        ry = int(max_ry * frac)
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), outline=(48, 48, 48), width=1)

    draw.line((x1 + 20, cy, x2 - 20, cy), fill=(22, 22, 22), width=1)
    draw.line((cx, y1 + 20, cx, y2 - 20), fill=(22, 22, 22), width=1)
    draw.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), fill=(110, 110, 110), outline=(220, 220, 220), width=1)
    draw.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), outline=(62, 62, 62), width=1)
    _overview_text(draw, (cx + 38, cy - 8), "PRIMARY NODE", fonts["mono"], (120, 120, 120), max_width=120)

    if not planets:
        _overview_text(draw, (cx - 72, cy + 48), "NO THEATRES LINKED", fonts["body"], (130, 130, 130), max_width=180)
        return

    n = len(planets)
    for idx, planet in enumerate(planets):
        ring_idx = idx % len(ring_fracs)
        angle = -math.pi / 2 + (2 * math.pi * idx / max(n, 1)) + (ring_idx * 0.17)
        rx = int(max_rx * ring_fracs[ring_idx])
        ry = int(max_ry * ring_fracs[ring_idx])
        px = int(cx + math.cos(angle) * rx)
        py = int(cy + math.sin(angle) * ry)
        is_active = _overview_value(planet, "id") == active_planet_id
        if is_active:
            draw.line((cx, cy, px, py), fill=(74, 74, 74), width=1)
        _draw_overview_node(draw, px, py, planet, is_active, fonts)


def _draw_overview_bottom_cards(draw, planets, active_planet_id, box, fonts):
    x1, y1, x2, y2 = box
    draw.rectangle(box, fill=(8, 8, 9), outline=(50, 50, 50))
    _overview_text(draw, (x1 + 12, y1 + 10), "THEATRE STATUS ARRAY", fonts["mono"], (112, 112, 112), max_width=220)
    if not planets:
        _overview_text(draw, (x1 + 12, y1 + 42), "No planets configured.", fonts["body"], (170, 170, 170), max_width=260)
        return

    gap = 10
    top = y1 + 32
    card_h = y2 - top - 8
    card_w = max(124, min(220, int((x2 - x1 - 24 - gap * (len(planets) - 1)) / max(len(planets), 1))))
    start_x = x1 + 12
    for idx, planet in enumerate(planets):
        cx = start_x + idx * (card_w + gap)
        if cx + card_w > x2 - 12:
            break
        active = _overview_value(planet, "id") == active_planet_id
        fill = (20, 20, 21) if active else (12, 12, 13)
        edge = (148, 148, 148) if active else (50, 50, 50)
        draw.rectangle((cx, top, cx + card_w, top + card_h), fill=fill, outline=edge, width=2 if active else 1)
        if active:
            draw.rectangle((cx, top, cx + 4, top + card_h), fill=(184, 184, 184))
        _overview_text(draw, (cx + 12, top + 8), _overview_value(planet, "name", "Unknown"), fonts["head"], (224, 224, 224) if active else (150, 150, 150), max_width=card_w - 24)
        _overview_text(draw, (cx + 12, top + 31), "ACTIVE" if active else "STANDBY", fonts["mono"], (200, 200, 200) if active else (88, 88, 88), max_width=card_w - 24)
        _overview_text(draw, (cx + 12, top + 51), f"C: {_overview_value(planet, 'contractor')}", fonts["small"], (160, 160, 160), max_width=card_w - 24)
        _overview_text(draw, (cx + 12, top + 67), f"E: {_overview_value(planet, 'enemy_type')}", fonts["small"], (145, 145, 145), max_width=card_w - 24)


def render_planetary_system_overview(
    planets:          list,
    active_planet_id: int,
    theme:            dict = None,
    turn_number:      int = 0,
) -> io.BytesIO:
    if theme is None:
        theme = _default_theme()

    planets = planets or []
    active_planet = next((p for p in planets if _overview_value(p, "id") == active_planet_id), None)
    if active_planet is None and planets:
        active_planet = planets[0]

    N = max(len(planets), 1)
    W = max(1280, min(1880, 1080 + N * 95))
    H = 760
    img = Image.new("RGB", (W, H), (5, 6, 7))
    draw = ImageDraw.Draw(img)

    fonts = {
        "title": _font(_SERIF, 22),
        "head": _font(_SANS, 16),
        "body": _font(_SANSREG, 12),
        "small": _font(_SANSREG, 10),
        "mono": _font(_MONO, 9),
    }

    _draw_overview_starfield(draw, W, H, seed=active_planet_id or N)
    _draw_overview_top_bar(draw, W, theme, active_planet, turn_number, fonts)

    top = 70
    bottom_cards_h = 158
    left_panel = (28, top, 318, H - bottom_cards_h - 24)
    orbit_bounds = (350, top, W - 28, H - bottom_cards_h - 24)

    _draw_overview_active_panel(draw, left_panel, active_planet, fonts)
    _draw_overview_orbit_map(draw, planets, active_planet_id, orbit_bounds, fonts)
    _draw_overview_bottom_cards(draw, planets, active_planet_id, (28, H - bottom_cards_h + 8, W - 28, H - 24), fonts)

    draw.rectangle((1, 1, W - 2, H - 2), outline=(64, 64, 64), width=2)
    draw.rectangle((7, 7, W - 8, H - 8), outline=(24, 24, 24), width=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ── DB-backed helpers ──────────────────────────────────────────────────────────

async def render_map_for_guild(guild_id: int, conn, planet_id: int = None,
                               movement_arrows: list = None) -> io.BytesIO:
    from utils.db import get_theme, get_active_planet_id

    if planet_id is None:
        planet_id = await get_active_planet_id(conn, guild_id)

    planet      = await conn.fetchrow(
        "SELECT * FROM planets WHERE guild_id=$1 AND id=$2", guild_id, planet_id)
    planet_name = planet["name"]       if planet else "Unknown"
    contractor  = planet["contractor"] if planet else "Unknown"
    enemy_type  = planet["enemy_type"] if planet else "Unknown"

    hex_rows     = await conn.fetch(
        "SELECT address, status FROM hexes WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)
    terrain_rows = await conn.fetch(
        "SELECT address, terrain FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)

    hex_data: dict = {}
    for r in hex_rows:
        hex_data[r["address"]] = {"status": r["status"], "terrain": "plains"}
    for r in terrain_rows:
        if r["address"] in hex_data:
            hex_data[r["address"]]["terrain"] = r["terrain"]
        else:
            hex_data[r["address"]] = {"terrain": r["terrain"], "status": "neutral"}

    sq_rows = await conn.fetch(
        "SELECT hex_address, brigade FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
        guild_id, planet_id)
    en_rows = await conn.fetch(
        "SELECT hex_address FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)

    # ── Fog of War ─────────────────────────────────────────────────────────────
    # Build the set of hexes visible to friendly units.
    # Each player unit on the map illuminates all hexes within FOG_VISION_RADIUS.
    player_positions = [r["hex_address"] for r in sq_rows]
    visible_hexes: set = set()
    for addr in player_positions:
        for h in hexes_within(addr, FOG_VISION_RADIUS):
            visible_hexes.add(h)

    unit_data: dict = {}
    for r in sq_rows:
        addr    = r["hex_address"]
        brigade = r["brigade"] or "infantry"
        entry   = unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
        entry["brigades"][brigade] = entry["brigades"].get(brigade, 0) + 1
    for r in en_rows:
        addr = r["hex_address"]
        # Only show this enemy unit if it falls inside a player's vision cone
        if addr in visible_hexes:
            unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
            unit_data[addr]["enemy"] += 1

    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1", guild_id) or 0
    theme = await get_theme(conn, guild_id)

    # Filter movement arrows by fog of war — enemy arrows only if visible
    filtered_arrows = []
    for (fa, ta, side) in (movement_arrows or []):
        if side == "player":
            filtered_arrows.append((fa, ta, side))
        elif side == "enemy" and (fa in visible_hexes or ta in visible_hexes):
            filtered_arrows.append((fa, ta, side))

    return render_planet_map(
        planet_name     = planet_name,
        contractor      = contractor,
        enemy_type      = enemy_type,
        hex_data        = hex_data,
        unit_data       = unit_data,
        turn_number     = int(turn_count) + 1,
        theme           = theme,
        movement_arrows = filtered_arrows,
    )


def render_movement_map(
    hex_data:   dict,
    unit_data:  dict,
    from_addr:  str,
    to_addr:    str,
    unit_name:  str,
    theme:      dict = None,
    zoom_radius: int = 5,
    remaining:  int = None,   # hexes left in turn budget — drives range ring
    budget:     int = None,   # total hex budget this turn
    show_arrow: bool = True,  # False = range-only view, no arrow or movement label
) -> io.BytesIO:
    """
    Renders a cropped map centered on the movement path, with a colored arrow
    showing the unit's movement from from_addr to to_addr.
    zoom_radius controls how many hex-rings around the movement are visible.
    When remaining/budget are supplied, a range ring overlay is drawn showing
    reachable hexes (teal) and already-used range (faint red wash).
    When show_arrow=False the arrow and movement label are suppressed (initial
    range-only view shown when the player first opens the move pad).
    """
    if theme is None:
        theme = _default_theme()

    px_all = [hex_center(gq, gr, HEX_SIZE) for gq, gr in GRID_COORDS]
    xs = [p[0] for p in px_all]; ys = [p[1] for p in px_all]
    grid_w = int(max(xs) - min(xs) + HEX_SIZE*2 + PADDING*2)
    grid_h = int(max(ys) - min(ys) + HEX_SIZE*2 + PADDING*2)
    img_w  = grid_w
    img_h  = grid_h + TITLE_H + LEGEND_H

    ox = int(img_w/2 - (max(xs)+min(xs))/2)
    oy = int(TITLE_H + PADDING + grid_h/2 - (max(ys)+min(ys))/2)

    BG  = 20
    img = Image.new("RGBA", (img_w, img_h), (BG, BG, BG, 255))
    draw = ImageDraw.Draw(img)

    f_abbr  = _font(_SANS,  8)
    f_coord = _font(_MONO,  6)
    f_title = _font(_SERIF, 16)
    f_pip   = _font(_SANS,  7)

    # Parse from/to centers
    try:
        fq, fr = map(int, from_addr.split(","))
        tq, tr = map(int, to_addr.split(","))
        from_cx, from_cy = hex_center(fq, fr, HEX_SIZE, ox, oy)
        to_cx,   to_cy   = hex_center(tq, tr, HEX_SIZE, ox, oy)
    except Exception:
        fq, fr = 0, 0
        from_cx, from_cy = ox, oy
        to_cx,   to_cy   = ox, oy

    # Draw all hexes
    for gq, gr in GRID_COORDS:
        key     = hex_key(gq, gr)
        cx, cy  = hex_center(gq, gr, HEX_SIZE, ox, oy)
        info    = hex_data.get(key, {})
        terrain = _terrain_key(info.get("terrain", "plains"))
        status  = info.get("status",  "neutral")
        corners = hex_corners(cx, cy, HEX_SIZE - 0.8)
        occupied = _has_unit_markers(unit_data, key)
        label_pos = _hex_label_positions(cx, cy, occupied)

        draw_terrain_hex(draw, corners, terrain, (cx, cy))

        tint = STATUS_TINTS.get(status, (0,0,0,0))
        if tint[3] > 0:
            tl = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
            ImageDraw.Draw(tl).polygon(corners, fill=tint)
            img  = Image.alpha_composite(img, tl)
            draw = ImageDraw.Draw(img)

        neighbor_terrains = []
        for nq, nr in get_neighbors(gq, gr):
            nkey = hex_key(nq, nr)
            neighbor_terrains.append(hex_data.get(nkey, {}).get("terrain", "plains"))
        draw_coastline(draw, corners, neighbor_terrains, terrain)

        lbl = key
        try:
            bb  = draw.textbbox((0,0), lbl, font=f_coord)
            lw2 = (bb[2]-bb[0])/2
            lh2 = (bb[3]-bb[1])/2
            label_cy = label_pos["coord_y"]
            lx2 = label_pos["coord_x"] if label_pos["cornered"] else cx - lw2
            ly2 = label_cy-lh2
            light_tile = terrain in ("plains", "city", "hills", "forest", "fort")
            shadow = (255,255,255,24) if not light_tile else (0,0,0,18)
            fill = (26, 26, 26, 78) if light_tile else (225, 225, 225, 84)
            draw.text((lx2+1, ly2+1), lbl, font=f_coord, fill=shadow)
            draw.text((lx2, ly2), lbl, font=f_coord, fill=fill)
        except Exception:
            pass

    # ── Range ring overlay (drawn before unit markers so markers sit on top) ──
    if remaining is not None and budget is not None:
        used = budget - remaining
        # Hexes reachable from current position (to_addr) within remaining steps
        reachable_set  = set(hexes_within(to_addr, remaining))   if remaining > 0 else set()
        # Hexes that WERE reachable from the starting hex but are now spent
        spent_set      = set(hexes_within(from_addr, used))      if used > 0 else set()
        spent_only_set = spent_set - reachable_set - {to_addr}

        range_layer = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
        rd = ImageDraw.Draw(range_layer)

        for gq, gr in GRID_COORDS:
            key    = hex_key(gq, gr)
            cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
            corners = hex_corners(cx, cy, HEX_SIZE - 1.5)
            if key == to_addr:
                # Current position — bright teal fill
                rd.polygon(corners, fill=(40, 210, 180, 90))
                rd.polygon(corners, outline=(40, 220, 190, 230), width=2)
            elif key in reachable_set:
                # Within remaining range — soft teal tint
                rd.polygon(corners, fill=(20, 180, 150, 48))
                rd.polygon(corners, outline=(40, 200, 170, 140), width=1)
            elif key in spent_only_set:
                # Used-up range — faint red wash, no outline
                rd.polygon(corners, fill=(200, 50, 50, 28))

        img  = Image.alpha_composite(img, range_layer)
        draw = ImageDraw.Draw(img)

        # ── Range budget bar drawn at top of cropped area ──────────────────
        # Will be rendered after cropping; store values for later
        _range_bar_data = (remaining, budget)
    else:
        _range_bar_data = None

    # Unit markers pass
    draw = ImageDraw.Draw(img)
    for gq, gr in GRID_COORDS:
        key    = hex_key(gq, gr)
        cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
        units  = unit_data.get(key, {})
        brigades_map = units.get("brigades", {})
        p_ct         = sum(brigades_map.values()) if brigades_map else units.get("players", 0)
        e_ct         = units.get("enemy", 0)
        if p_ct > 0 or e_ct > 0:
            _draw_unit_stack_marker(draw, cx, cy, brigades_map, p_ct, e_ct, f_pip)


    # ── Draw movement arrow ────────────────────────────────────────────────────
    draw = ImageDraw.Draw(img)

    if show_arrow:
        # Highlight from hex (green outline) and to hex (yellow outline)
        try:
            from_corners = hex_corners(from_cx, from_cy, HEX_SIZE - 0.8)
            draw.polygon(from_corners, outline=(80, 220, 80, 255), width=3)
        except Exception:
            pass
        try:
            to_corners = hex_corners(to_cx, to_cy, HEX_SIZE - 0.8)
            draw.polygon(to_corners, outline=(255, 220, 40, 255), width=3)
        except Exception:
            pass

        # Draw arrow line
        dx = to_cx - from_cx
        dy = to_cy - from_cy
        dist = math.sqrt(dx*dx + dy*dy) or 1
        ux, uy = dx/dist, dy/dist

        # Arrow shaft — thick cyan line
        shaft_end_x = to_cx - ux * HEX_SIZE * 0.55
        shaft_end_y = to_cy - uy * HEX_SIZE * 0.55
        shaft_start_x = from_cx + ux * HEX_SIZE * 0.6
        shaft_start_y = from_cy + uy * HEX_SIZE * 0.6

        for w in [7, 5, 3]:
            col = (0,0,0,200) if w == 7 else (80,240,200,220) if w == 5 else (160,255,230,255)
            draw.line(
                (shaft_start_x, shaft_start_y, shaft_end_x, shaft_end_y),
                fill=col, width=w)

        # Arrowhead
        perp_x, perp_y = -uy, ux
        head_size = HEX_SIZE * 0.45
        tip_x = to_cx - ux * HEX_SIZE * 0.3
        tip_y = to_cy - uy * HEX_SIZE * 0.3
        left_x  = shaft_end_x + perp_x * head_size * 0.5
        left_y  = shaft_end_y + perp_y * head_size * 0.5
        right_x = shaft_end_x - perp_x * head_size * 0.5
        right_y = shaft_end_y - perp_y * head_size * 0.5
        draw.polygon(
            [(tip_x, tip_y), (left_x, left_y), (right_x, right_y)],
            fill=(80,240,200,240), outline=(0,0,0,200))

        # Label on arrow
        f_arrow = _font(_SANS, 9)
        mid_x = (from_cx + to_cx) / 2
        mid_y = (from_cy + to_cy) / 2 - 12
        arrow_label = f"{unit_name}: {from_addr} → {to_addr}"
        try:
            bb   = draw.textbbox((0,0), arrow_label, font=f_arrow)
            lw2  = (bb[2]-bb[0])/2
            for dx2, dy2 in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
                draw.text((mid_x-lw2+dx2, mid_y+dy2), arrow_label, font=f_arrow, fill=(0,0,0,230))
            draw.text((mid_x-lw2, mid_y), arrow_label, font=f_arrow, fill=(80,240,200,255))
        except Exception:
            pass

    # ── Title bar ─────────────────────────────────────────────────────────────
    draw.rectangle((0, 0, img_w, TITLE_H), fill=(10,10,10,255))
    title_text = (
        f"Movement — {unit_name}  |  {from_addr} → {to_addr}"
        if show_arrow else
        f"Range — {unit_name}  |  Position: {from_addr}"
    )
    try:
        bb  = draw.textbbox((0,0), title_text, font=f_title)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        draw.text(((img_w-tw)//2, (TITLE_H-th)//2), title_text, font=f_title,
                  fill=(80,240,200,255))
    except Exception:
        pass

    # ── Crop to movement zone ─────────────────────────────────────────────────
    mid_map_x = int((from_cx + to_cx) / 2)
    mid_map_y = int((from_cy + to_cy) / 2)
    crop_r    = int((zoom_radius + 1) * HEX_SIZE * 2.2)
    crop_x1   = max(0, mid_map_x - crop_r)
    crop_y1   = max(TITLE_H, mid_map_y - crop_r)
    crop_x2   = min(img_w, mid_map_x + crop_r)
    crop_y2   = min(img_h - LEGEND_H, mid_map_y + crop_r)

    cropped = img.crop((crop_x1, 0, crop_x2, crop_y2))

    final = Image.new("RGB", cropped.size, (BG, BG, BG))
    final.paste(cropped, mask=cropped.split()[3])

    # ── Movement budget bar — drawn onto the final cropped image ──────────────
    if _range_bar_data is not None:
        rem_b, bud_b = _range_bar_data
        fd   = ImageDraw.Draw(final)
        fw   = final.width
        f_hud = _font(_SANS, 9)

        # Bar geometry
        bar_x    = 10
        bar_y    = TITLE_H + 6
        bar_h    = 10
        bar_maxw = fw - 20
        cell_gap = 3

        if bud_b > 0:
            cell_w = max(8, (bar_maxw - cell_gap * (bud_b - 1)) // bud_b)
            total_bar_w = cell_w * bud_b + cell_gap * (bud_b - 1)

            for i in range(bud_b):
                cx2 = bar_x + i * (cell_w + cell_gap)
                filled = i < rem_b
                # Color shifts: green → amber → red as remaining drops
                ratio = rem_b / bud_b if bud_b > 0 else 0
                if ratio > 0.5:
                    fill_col  = (29, 158, 117, 255)   # teal/green
                    out_col   = (60, 200, 160, 200)
                elif ratio > 0:
                    fill_col  = (186, 117, 23, 255)   # amber
                    out_col   = (220, 160, 50, 200)
                else:
                    fill_col  = (30, 30, 30, 255)     # exhausted — dark
                    out_col   = (80, 80, 80, 180)

                empty_col = (35, 35, 35, 255)
                empty_out = (70, 70, 70, 160)

                if filled:
                    fd.rectangle((cx2, bar_y, cx2+cell_w, bar_y+bar_h),
                                 fill=fill_col, outline=out_col, width=1)
                else:
                    fd.rectangle((cx2, bar_y, cx2+cell_w, bar_y+bar_h),
                                 fill=empty_col, outline=empty_out, width=1)

            # Label to the right of bar
            label_x = bar_x + total_bar_w + 8
            label   = f"{rem_b}/{bud_b} hexes"
            if rem_b == 0:
                label_col = (180, 60, 60, 255)
                label = "EXHAUSTED"
            elif rem_b <= 1:
                label_col = (210, 140, 40, 255)
            else:
                label_col = (80, 210, 170, 255)
            try:
                for dx2, dy2 in [(-1,-1),(1,-1),(-1,1),(1,1)]:
                    fd.text((label_x+dx2, bar_y+dy2), label, font=f_hud, fill=(0,0,0,180))
                fd.text((label_x, bar_y), label, font=f_hud, fill=label_col)
            except Exception:
                pass

    out = io.BytesIO()
    final.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def render_movement_map_for_guild(
    guild_id: int,
    conn,
    from_addr: str,
    to_addr:   str,
    unit_name: str,
    planet_id: int = None,
    remaining: int = None,
    budget:    int = None,
    show_arrow: bool = True,
) -> io.BytesIO:
    from utils.db import get_theme, get_active_planet_id

    if planet_id is None:
        planet_id = await get_active_planet_id(conn, guild_id)

    hex_rows     = await conn.fetch(
        "SELECT address, status FROM hexes WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)
    terrain_rows = await conn.fetch(
        "SELECT address, terrain FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)

    hex_data: dict = {}
    for r in hex_rows:
        hex_data[r["address"]] = {"status": r["status"], "terrain": "plains"}
    for r in terrain_rows:
        if r["address"] in hex_data:
            hex_data[r["address"]]["terrain"] = r["terrain"]
        else:
            hex_data[r["address"]] = {"terrain": r["terrain"], "status": "neutral"}

    sq_rows = await conn.fetch(
        "SELECT hex_address, brigade FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
        guild_id, planet_id)
    en_rows = await conn.fetch(
        "SELECT hex_address FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)

    unit_data: dict = {}
    for r in sq_rows:
        addr    = r["hex_address"]
        brigade = r["brigade"] or "infantry"
        entry   = unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
        entry["brigades"][brigade] = entry["brigades"].get(brigade, 0) + 1
    for r in en_rows:
        addr = r["hex_address"]
        unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
        unit_data[addr]["enemy"] += 1

    theme = await get_theme(conn, guild_id)

    return render_movement_map(
        hex_data   = hex_data,
        unit_data  = unit_data,
        from_addr  = from_addr,
        to_addr    = to_addr,
        unit_name  = unit_name,
        theme      = theme,
        remaining  = remaining,
        budget     = budget,
        show_arrow = show_arrow,
    )


async def render_gm_map_for_guild(guild_id: int, conn, planet_id: int = None,
                                  movement_arrows: list = None) -> io.BytesIO:
    """
    Render a GM-only map that shows ALL unit locations with detailed labels.
    Player units shown in blue with name, enemy units shown in red with ID+type.
    Movement arrows shown for all units (player=cyan, enemy=orange).
    """
    from utils.db import get_theme, get_active_planet_id

    if planet_id is None:
        planet_id = await get_active_planet_id(conn, guild_id)

    planet      = await conn.fetchrow(
        "SELECT * FROM planets WHERE guild_id=$1 AND id=$2", guild_id, planet_id)
    planet_name = planet["name"]       if planet else "Unknown"
    contractor  = planet["contractor"] if planet else "Unknown"
    enemy_type  = planet["enemy_type"] if planet else "Unknown"

    hex_rows     = await conn.fetch(
        "SELECT address, status FROM hexes WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)
    terrain_rows = await conn.fetch(
        "SELECT address, terrain FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)

    hex_data: dict = {}
    for r in hex_rows:
        hex_data[r["address"]] = {"status": r["status"], "terrain": "plains"}
    for r in terrain_rows:
        if r["address"] in hex_data:
            hex_data[r["address"]]["terrain"] = r["terrain"]
        else:
            hex_data[r["address"]] = {"terrain": r["terrain"], "status": "neutral"}

    sq_rows = await conn.fetch(
        "SELECT hex_address, owner_name, name, brigade, in_transit, transit_destination FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)
    en_rows = await conn.fetch(
        "SELECT id, hex_address, unit_type, attack, defense FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)

    unit_data: dict = {}
    for r in sq_rows:
        addr    = r["hex_address"]
        brigade = r["brigade"] or "infantry"
        entry   = unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
        entry["brigades"][brigade] = entry["brigades"].get(brigade, 0) + 1
    for r in en_rows:
        addr = r["hex_address"]
        unit_data.setdefault(addr, {"brigades": {}, "enemy": 0})
        unit_data[addr]["enemy"] += 1

    gm_player_labels: dict = {}
    gm_enemy_labels:  dict = {}

    for r in sq_rows:
        addr  = r["hex_address"]
        label = r["owner_name"][:8]
        if r["in_transit"]:
            label += "->" + (r["transit_destination"] or "?")[:5]
        gm_player_labels.setdefault(addr, []).append(label)

    for r in en_rows:
        addr  = r["hex_address"]
        label = f"#{r['id']}{r['unit_type'][:6]}"
        gm_enemy_labels.setdefault(addr, []).append(label)

    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1", guild_id) or 0
    theme = await get_theme(conn, guild_id)

    buf = render_planet_map(
        planet_name     = f"[GM] {planet_name}",
        contractor      = contractor,
        enemy_type      = enemy_type,
        hex_data        = hex_data,
        unit_data       = unit_data,
        turn_number     = int(turn_count) + 1,
        theme           = theme,
        movement_arrows = movement_arrows or [],
    )

    img  = Image.open(buf).convert("RGBA")
    draw = ImageDraw.Draw(img)
    f_gm = _font(_SANS, 6)

    px_all = [hex_center(gq, gr, HEX_SIZE) for gq, gr in GRID_COORDS]
    xs = [p[0] for p in px_all]; ys = [p[1] for p in px_all]
    grid_h = int(max(ys) - min(ys) + HEX_SIZE*2 + PADDING*2)
    img_w  = img.width

    ox = int(img_w/2 - (max(xs)+min(xs))/2)
    oy = int(TITLE_H + PADDING + grid_h/2 - (max(ys)+min(ys))/2)

    for addr, labels in gm_player_labels.items():
        try:
            gq, gr = map(int, addr.split(","))
            cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
            dot_y  = cy + HEX_SIZE * 0.3
            for j, lbl in enumerate(labels[:3]):
                ty = dot_y + j * 7
                try:
                    bb  = draw.textbbox((0,0), lbl, font=f_gm)
                    lw  = (bb[2]-bb[0])/2
                    for dx2, dy2 in [(-1,0),(1,0),(0,-1),(0,1)]:
                        draw.text((cx-lw+dx2, ty+dy2), lbl, font=f_gm, fill=(0,0,80,220))
                    draw.text((cx-lw, ty), lbl, font=f_gm, fill=(180,210,255,255))
                except Exception:
                    pass
        except Exception:
            pass

    for addr, labels in gm_enemy_labels.items():
        try:
            gq, gr = map(int, addr.split(","))
            cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
            dot_y  = cy + HEX_SIZE * 0.3
            for j, lbl in enumerate(labels[:3]):
                ty = dot_y + j * 7
                try:
                    bb  = draw.textbbox((0,0), lbl, font=f_gm)
                    lw  = (bb[2]-bb[0])/2
                    for dx2, dy2 in [(-1,0),(1,0),(0,-1),(0,1)]:
                        draw.text((cx-lw+dx2, ty+dy2), lbl, font=f_gm, fill=(80,0,0,220))
                    draw.text((cx-lw, ty), lbl, font=f_gm, fill=(255,180,180,255))
                except Exception:
                    pass
        except Exception:
            pass

    final = Image.new("RGB", img.size, (20,20,20))
    final.paste(img, mask=img.split()[3])
    out = io.BytesIO()
    final.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def render_overview_for_guild(guild_id: int, conn) -> io.BytesIO:
    from utils.db import get_theme, get_active_planet_id
    theme     = await get_theme(conn, guild_id)
    active_id = await get_active_planet_id(conn, guild_id)
    planets   = await conn.fetch(
        "SELECT id, name, contractor, enemy_type FROM planets "
        "WHERE guild_id=$1 ORDER BY sort_order, id", guild_id)
    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1", guild_id) or 0
    player_counts = await conn.fetch(
        "SELECT planet_id, COUNT(*) AS count FROM squadrons "
        "WHERE guild_id=$1 AND is_active=TRUE GROUP BY planet_id", guild_id)
    enemy_counts = await conn.fetch(
        "SELECT planet_id, COUNT(*) AS count FROM enemy_units "
        "WHERE guild_id=$1 AND is_active=TRUE GROUP BY planet_id", guild_id)
    players_by_planet = {row["planet_id"]: row["count"] for row in player_counts}
    enemies_by_planet = {row["planet_id"]: row["count"] for row in enemy_counts}
    overview_planets = []
    for planet in planets:
        item = dict(planet)
        item["player_units"] = int(players_by_planet.get(item["id"], 0) or 0)
        item["enemy_units"] = int(enemies_by_planet.get(item["id"], 0) or 0)
        overview_planets.append(item)
    return render_planetary_system_overview(overview_planets, active_id, theme, int(turn_count) + 1)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _badge(draw, cx, cy, text, bg, fg, font):
    try:
        bb     = draw.textbbox((0,0), text, font=font)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        px, py = 4, 2
        draw.rounded_rectangle(
            (cx-tw//2-px, cy-th//2-py, cx+tw//2+px, cy+th//2+py),
            radius=3, fill=bg+(210,))
        draw.text((cx-tw//2, cy-th//2), text, font=font, fill=fg+(255,))
    except Exception:
        pass


def _compass(draw, cx, cy, size, font):
    col = (130,130,130,255)
    draw.line((cx,cy-size,cx,cy+size), fill=col, width=1)
    draw.line((cx-size,cy,cx+size,cy), fill=col, width=1)
    draw.ellipse((cx-3,cy-3,cx+3,cy+3), fill=(180,180,180,255))
    for lbl,dx,dy in [("N",0,-1),("S",0,1),("E",1,0),("W",-1,0)]:
        lx,ly = cx+dx*(size+7), cy+dy*(size+7)
        try:
            bb   = draw.textbbox((0,0), lbl, font=font)
            w, h = bb[2]-bb[0], bb[3]-bb[1]
            draw.text((lx-w//2, ly-h//2), lbl, font=font, fill=col)
        except Exception:
            pass


def _default_theme():
    return {
        "bot_name":       "IRON PACT",
        "player_faction": "Iron Pact PMC",
        "enemy_faction":  "Enemy",
        "player_unit":    "Unit",
        "enemy_unit":     "Enemy Unit",
        "safe_zone":      "Deployment Zone",
        "flavor_text":    "The contract must be fulfilled.",
        "color":          0xAA2222,
    }
