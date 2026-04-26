"""
Warbot — Map Renderer v2
========================
Two render modes:

1. render_planet_map()      — Full tactical map for the ACTIVE planet.
   37 outer hexes (4-ring axial grid), each with 19 mid hexes (2-ring).
   Grayscale terrain, control tints, unit badges.

2. render_galaxy_overview() — Horizontal strip showing all planets.
   Active planet = large stylised sphere with ring + contract details.
   Inactive planets = small dim discs labelled STANDBY.

Terrain types: flat, forest, hill, mountain, fort, city, military
All output is grayscale-tinted PNG.
"""

import io
import math
import random
from PIL import Image, ImageDraw, ImageFont

from utils.hexmap import (
    OUTER_COORDS, MID_OFFSETS,
    hex_center_flat, hex_corners_flat,
    outer_key, mid_key,
    SAFE_HUB, STATUS_PLAYER, STATUS_ENEMY,
)

# ── Font loader ────────────────────────────────────────────────────────────────

def _font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()

_SANS     = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
_SERIF    = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
_MONO     = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
_SANS_REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]

# ── Terrain ────────────────────────────────────────────────────────────────────

TERRAIN_TYPES = ["flat", "forest", "hill", "mountain", "fort", "city", "military"]

TERRAIN_DEFS = {
    "flat":     {"fill": 195, "border": 155, "symbol": None, "count": 0, "label": "Flat"},
    "forest":   {"fill": 138, "border": 102, "symbol": "♣",  "count": 4, "label": "Forest"},
    "hill":     {"fill": 166, "border": 126, "symbol": "∧",  "count": 3, "label": "Hills"},
    "mountain": {"fill": 108, "border":  72, "symbol": "▲",  "count": 3, "label": "Mountain"},
    "fort":     {"fill": 180, "border": 136, "symbol": "⊞",  "count": 1, "label": "Fort"},
    "city":     {"fill": 215, "border": 165, "symbol": "⌂",  "count": 1, "label": "City"},
    "military": {"fill": 155, "border": 110, "symbol": "✦",  "count": 2, "label": "Military"},
}

STATUS_TINTS = {
    "players":        (70,  90, 210, 78),
    "majority_player":(100,120, 195, 52),
    "enemy":          (210,  55, 55,  78),
    "majority_enemy": (190,  90, 90,  52),
    "contested":      (155,  75,155,  68),
    "neutral":        (0,    0,   0,   0),
}

# ── Layout constants ───────────────────────────────────────────────────────────

OUTER_SIZE = 72    # outer hex circumradius in pixels
MID_SCALE  = 0.87  # mid hexes fill this fraction of outer radius
PADDING    = 85
TITLE_H    = 60
LEGEND_H   = 96


# ══════════════════════════════════════════════════════════════════════════════
# PLANET MAP
# ══════════════════════════════════════════════════════════════════════════════

def render_planet_map(
    planet_name: str,
    contractor:  str,
    enemy_type:  str,
    hex_data:    dict,    # {addr: {"terrain": str, "status": str}}
    unit_counts: dict,    # {outer_key_str: {"players": N, "enemy": N}}
    turn_number: int = 1,
    theme:       dict = None,
) -> io.BytesIO:

    if theme is None:
        theme = _default_theme()

    # Canvas size from bounding box of all outer hex centres
    centres = [hex_center_flat(q, r, OUTER_SIZE) for q, r in OUTER_COORDS]
    xs      = [c[0] for c in centres]
    ys      = [c[1] for c in centres]
    grid_w  = int(max(xs) - min(xs) + OUTER_SIZE * 2 + PADDING * 2)
    grid_h  = int(max(ys) - min(ys) + OUTER_SIZE * 2 + PADDING * 2)
    img_w   = grid_w
    img_h   = grid_h + TITLE_H + LEGEND_H

    # Origin so grid is centred
    ox = int(img_w / 2 - (max(xs) + min(xs)) / 2)
    oy = int(TITLE_H + PADDING + grid_h / 2 - (max(ys) + min(ys)) / 2)

    BG   = 20
    img  = Image.new("RGBA", (img_w, img_h), (BG, BG, BG, 255))
    draw = ImageDraw.Draw(img)

    # Noise texture
    try:
        import numpy as np
        arr   = np.array(img)
        noise = np.random.default_rng(99).integers(0, 8, (img_h, img_w), dtype=np.uint8)
        for c in range(3):
            arr[:, :, c] = np.clip(arr[:, :, c].astype(np.int16) + noise, 0, 255).astype(np.uint8)
        img  = Image.fromarray(arr, "RGBA")
        draw = ImageDraw.Draw(img)
    except ImportError:
        pass

    # Fonts
    f_title   = _font(_SERIF,    20)
    f_outer   = _font(_SANS,      9)
    f_sym     = _font(_MONO,      9)
    f_badge   = _font(_SANS,      9)
    f_legend  = _font(_SANS_REG, 10)
    f_coord   = _font(_MONO,      6)

    # ── Per outer hex ─────────────────────────────────────────────────────────
    for oq, or_ in OUTER_COORDS:
        ok_str = outer_key(oq, or_)
        ocx, ocy = hex_center_flat(oq, or_, OUTER_SIZE, ox, oy)

        # ── Mid hexes ─────────────────────────────────────────────────────────
        # The 19 mid offsets span axial radius 2.
        # Their pixel span (radius 2 hex grid) is sqrt(3)*2*MID_SIZE wide.
        # We scale so this fits snugly inside the outer hex.
        mid_size_px = OUTER_SIZE * MID_SCALE / (math.sqrt(3))

        for mq, mr in MID_OFFSETS:
            mk_str = mid_key(oq, or_, mq, mr)

            # Pixel centre of mid hex relative to outer centre
            dx = mid_size_px * (3 / 2 * mq)
            dy = mid_size_px * (math.sqrt(3) / 2 * mq + math.sqrt(3) * mr)
            mcx, mcy = ocx + dx, ocy + dy

            info    = hex_data.get(mk_str) or hex_data.get(ok_str) or {}
            terrain = info.get("terrain", "flat")
            status  = info.get("status",  "neutral")
            t_def   = TERRAIN_DEFS.get(terrain, TERRAIN_DEFS["flat"])

            g        = t_def["fill"]
            b        = t_def["border"]
            corners  = hex_corners_flat(mcx, mcy, mid_size_px - 0.8)

            # Fill
            draw.polygon(corners, fill=(g, g, g, 255))

            # Control tint
            tint = STATUS_TINTS.get(status, (0, 0, 0, 0))
            if tint[3] > 0:
                tl = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
                ImageDraw.Draw(tl).polygon(corners, fill=tint)
                img  = Image.alpha_composite(img, tl)
                draw = ImageDraw.Draw(img)

            # Border
            draw.polygon(corners, outline=(b, b, b, 255), width=1)

            # Terrain symbols
            sym = t_def.get("symbol")
            if sym and t_def["count"] > 0:
                rng  = random.Random(hash(mk_str) & 0xFFFF)
                rmax = mid_size_px * 0.38
                dark = g < 140
                sc   = (225, 225, 225, 175) if dark else (30, 30, 30, 175)
                for _ in range(t_def["count"]):
                    angle = rng.uniform(0, 2 * math.pi)
                    rad   = rng.uniform(0, rmax)
                    sx, sy = mcx + rad * math.cos(angle), mcy + rad * math.sin(angle)
                    try:
                        bb = draw.textbbox((0, 0), sym, font=f_sym)
                        draw.text((sx - (bb[2]-bb[0])/2, sy - (bb[3]-bb[1])/2),
                                  sym, font=f_sym, fill=sc)
                    except Exception:
                        pass

            # Tiny coord label at bottom of mid hex
            lbl = f"{mq},{mr}"
            try:
                bb  = draw.textbbox((0, 0), lbl, font=f_coord)
                lw, lh = (bb[2]-bb[0])/2, bb[3]-bb[1]
                col = (38, 38, 38, 195) if g > 148 else (195, 195, 195, 195)
                draw.text((mcx - lw, mcy + mid_size_px * 0.50 - lh),
                          lbl, font=f_coord, fill=col)
            except Exception:
                pass

        # Outer hex border (drawn over mid hexes)
        outer_corners = hex_corners_flat(ocx, ocy, OUTER_SIZE - 1.5)
        is_hub   = (ok_str == SAFE_HUB)
        bdr_col  = (150, 150, 255, 255) if is_hub else (72, 72, 72, 255)
        bdr_w    = 3 if is_hub else 2
        draw.polygon(outer_corners, outline=bdr_col, width=bdr_w)

        # Outer coord label (centre of hex)
        try:
            bb   = draw.textbbox((0, 0), ok_str, font=f_outer)
            lw, lh = (bb[2]-bb[0])/2, (bb[3]-bb[1])/2
            draw.text((ocx-lw+1, ocy-lh+1), ok_str, font=f_outer, fill=(0,0,0,150))
            lc = (235, 235, 255, 255) if is_hub else (185, 185, 185, 255)
            draw.text((ocx-lw, ocy-lh), ok_str, font=f_outer, fill=lc)
        except Exception:
            pass

        # Unit count badges
        counts = unit_counts.get(ok_str, {})
        p_ct   = counts.get("players", 0)
        e_ct   = counts.get("enemy", 0)
        by     = ocy + OUTER_SIZE * 0.72
        if p_ct > 0:
            _badge(draw, ocx - 20, by, f"P:{p_ct}", (55,75,185), (185,195,240), f_badge)
        if e_ct > 0:
            _badge(draw, ocx + 20, by, f"E:{e_ct}", (145,40,40), (240,165,165), f_badge)

    # ── Title bar ─────────────────────────────────────────────────────────────
    draw.rectangle((0, 0, img_w, TITLE_H), fill=(10, 10, 10, 255))
    draw.line((36, TITLE_H-2, img_w-36, TITLE_H-2), fill=(75, 75, 75, 255), width=2)
    title = (f"{theme.get('bot_name','WARBOT')}  ·  {planet_name}  ·  "
             f"Contractor: {contractor}  ·  Enemy: {enemy_type}  ·  Turn {turn_number}")
    try:
        bb = draw.textbbox((0, 0), title, font=f_title)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        draw.text(((img_w-tw)//2, (TITLE_H-th)//2), title, font=f_title,
                  fill=(210, 210, 210, 255))
    except Exception:
        pass

    # ── Legend ────────────────────────────────────────────────────────────────
    ly = img_h - LEGEND_H
    draw.rectangle((0, ly, img_w, img_h), fill=(10, 10, 10, 255))
    draw.line((36, ly+1, img_w-36, ly+1), fill=(75, 75, 75, 255), width=1)

    t_items = [(TERRAIN_DEFS[t]["fill"], TERRAIN_DEFS[t]["label"]) for t in TERRAIN_TYPES]
    s_items = [
        (72,  90, 205, f"{theme.get('player_faction','PMC')} controlled"),
        (205, 52,  52, f"{theme.get('enemy_faction','Enemy')} controlled"),
        (152, 72, 152, "Contested"),
        (115,115, 115, "Neutral"),
    ]
    lx   = 38
    cw_t = (img_w - 76) // len(t_items)
    cw_s = (img_w - 76) // len(s_items)

    for i, (g, lbl) in enumerate(t_items):
        x = lx + i * cw_t
        draw.rectangle((x, ly+10, x+13, ly+23), fill=(g,g,g,255), outline=(105,105,105,255))
        try:
            draw.text((x+17, ly+10), lbl, font=f_legend, fill=(162, 162, 162, 255))
        except Exception:
            pass

    for i, (ri, gi, bi, lbl) in enumerate(s_items):
        x = lx + i * cw_s
        draw.rectangle((x, ly+48, x+13, ly+61), fill=(ri,gi,bi,255), outline=(105,105,105,255))
        try:
            draw.text((x+17, ly+48), lbl, font=f_legend, fill=(162, 162, 162, 255))
        except Exception:
            pass

    try:
        fl = (f"P:N = {theme.get('player_unit','unit')} count  ·  "
              f"E:N = {theme.get('enemy_unit','enemy')} count  ·  "
              f"FOB = {SAFE_HUB}  ·  {theme.get('flavor_text','')}")
        bb  = draw.textbbox((0, 0), fl, font=f_legend)
        fw  = bb[2]-bb[0]
        draw.text(((img_w-fw)//2, ly+78), fl, font=f_legend, fill=(95, 95, 95, 255))
    except Exception:
        pass

    # Compass
    _compass(draw, img_w - 46, TITLE_H + 42, 16, f_legend)

    # Border
    draw.rectangle((1, 1, img_w-2, img_h-2), outline=(65, 65, 65, 255), width=3)

    # Flatten to RGB
    bg = Image.new("RGB", (img_w, img_h), (BG, BG, BG))
    bg.paste(img, mask=img.split()[3])
    buf = io.BytesIO()
    bg.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════════════════
# GALAXY OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

def render_galaxy_overview(
    planets:          list,   # list of dicts: {id, name, contractor, enemy_type, ...}
    active_planet_id: int,
    theme:            dict = None,
) -> io.BytesIO:

    if theme is None:
        theme = _default_theme()

    N       = max(len(planets), 1)
    W       = max(1200, N * 220)
    H       = 360
    TBAR    = 46
    img     = Image.new("RGB", (W, H), (10, 10, 10))
    draw    = ImageDraw.Draw(img)

    f_title = _font(_SERIF,    18)
    f_name  = _font(_SANS,     13)
    f_sub   = _font(_SANS_REG, 10)
    f_tiny  = _font(_MONO,      8)

    # Title bar
    draw.rectangle((0, 0, W, TBAR), fill=(16, 16, 16))
    draw.line((28, TBAR-2, W-28, TBAR-2), fill=(65, 65, 65), width=1)
    try:
        tt = f"{theme.get('bot_name','WARBOT')}  ·  Interplanetary Theatre Overview"
        bb = draw.textbbox((0, 0), tt, font=f_title)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        draw.text(((W-tw)//2, (TBAR-th)//2), tt, font=f_title, fill=(205, 205, 205))
    except Exception:
        pass

    col_w    = W // N
    content_h = H - TBAR

    for i, planet in enumerate(planets):
        cx       = col_w * i + col_w // 2
        cy       = TBAR + content_h // 2 + 8
        is_active = (planet.get("id") == active_planet_id)
        R        = 95 if is_active else 46

        # Glow rings
        glow_count = 14 if is_active else 6
        for g in range(glow_count, 0, -1):
            lum = int(28 * g / glow_count)
            try:
                draw.ellipse((cx-R-g, cy-R-g, cx+R+g, cy+R+g),
                             outline=(lum, lum, lum+10), width=1)
            except Exception:
                pass

        # Planet body — concentric bands
        for band in range(R, 0, -7 if is_active else -5):
            g = 50 + int((R - band) / R * (85 if is_active else 50))
            draw.ellipse((cx-band, cy-band, cx+band, cy+band), fill=(g, g, g))

        # Latitude lines on active planet
        if is_active:
            for lat in range(-4, 5):
                y_off = lat * (R // 5)
                if abs(y_off) >= R:
                    continue
                hw  = int(math.sqrt(max(0, R*R - y_off*y_off)))
                lum = 72 + abs(lat) * 6
                draw.line((cx-hw, cy+y_off, cx+hw, cy+y_off), fill=(lum,lum,lum), width=1)

        # Planet ring
        ry = 11 if is_active else 5
        rw = 20 if is_active else 10
        ring_col  = (140, 140, 140) if is_active else (60, 60, 60)
        mask_col  = (10, 10, 10)
        draw.ellipse((cx-R-rw, cy-ry, cx+R+rw, cy+ry), outline=ring_col, width=2)
        draw.ellipse((cx-R-rw, cy-ry, cx+R+rw, cy+ry), outline=mask_col, width=6)
        draw.ellipse((cx-R-rw+4, cy-ry+3, cx+R+rw-4, cy+ry-3), outline=ring_col, width=1)

        # Status badge
        try:
            if is_active:
                badge = "◉ ACTIVE THEATRE"
                col   = (110, 210, 110)
            else:
                badge = "○ STANDBY"
                col   = (80, 80, 80)
            bb  = draw.textbbox((0, 0), badge, font=f_sub)
            bw  = bb[2]-bb[0]
            draw.text((cx-bw//2, cy-R-22), badge, font=f_sub, fill=col)
        except Exception:
            pass

        # Planet name
        try:
            bb  = draw.textbbox((0, 0), planet["name"], font=f_name)
            nw  = bb[2]-bb[0]
            col = (218, 218, 218) if is_active else (95, 95, 95)
            draw.text((cx-nw//2, cy+R+10), planet["name"], font=f_name, fill=col)
        except Exception:
            pass

        # Contractor + enemy type
        for j, sub in enumerate([
            f"Contract: {planet.get('contractor','—')}",
            f"Enemy: {planet.get('enemy_type','—')}",
        ]):
            try:
                bb  = draw.textbbox((0, 0), sub, font=f_tiny)
                sw  = bb[2]-bb[0]
                col = (155, 155, 155) if is_active else (60, 60, 60)
                draw.text((cx-sw//2, cy+R+28+j*13), sub, font=f_tiny, fill=col)
            except Exception:
                pass

        # Column divider
        if i < N - 1:
            draw.line((col_w*(i+1), TBAR+8, col_w*(i+1), H-8), fill=(36,36,36), width=1)

    draw.rectangle((1, 1, W-2, H-2), outline=(55, 55, 55), width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf


# ══════════════════════════════════════════════════════════════════════════════
# DB-backed helpers
# ══════════════════════════════════════════════════════════════════════════════

async def render_map_for_guild(guild_id: int, conn, planet_id: int = None) -> io.BytesIO:
    from utils.db import get_theme

    if planet_id is None:
        row       = await conn.fetchrow(
            "SELECT active_planet_id FROM guild_config WHERE guild_id=$1", guild_id)
        planet_id = (row["active_planet_id"] if row and row["active_planet_id"] else 1)

    planet      = await conn.fetchrow(
        "SELECT * FROM planets WHERE guild_id=$1 AND id=$2", guild_id, planet_id)
    planet_name = planet["name"]       if planet else "Unknown"
    contractor  = planet["contractor"] if planet else "Unknown"
    enemy_type  = planet["enemy_type"] if planet else "Unknown"

    hex_rows = await conn.fetch(
        "SELECT address, status FROM hexes WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)
    terrain_rows = await conn.fetch(
        "SELECT address, terrain FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id)

    hex_data: dict = {}
    for r in hex_rows:
        hex_data[r["address"]] = {"status": r["status"], "terrain": "flat"}
    for r in terrain_rows:
        if r["address"] in hex_data:
            hex_data[r["address"]]["terrain"] = r["terrain"]
        else:
            hex_data[r["address"]] = {"terrain": r["terrain"], "status": "neutral"}

    sq_rows = await conn.fetch(
        "SELECT hex_address FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
        guild_id, planet_id)
    en_rows = await conn.fetch(
        "SELECT hex_address FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)

    unit_counts: dict = {}
    for r in sq_rows:
        outer = r["hex_address"].split(":")[0]
        unit_counts.setdefault(outer, {"players": 0, "enemy": 0})
        unit_counts[outer]["players"] += 1
    for r in en_rows:
        outer = r["hex_address"].split(":")[0]
        unit_counts.setdefault(outer, {"players": 0, "enemy": 0})
        unit_counts[outer]["enemy"] += 1

    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1", guild_id) or 0
    theme = await get_theme(conn, guild_id)

    return render_planet_map(
        planet_name = planet_name,
        contractor  = contractor,
        enemy_type  = enemy_type,
        hex_data    = hex_data,
        unit_counts = unit_counts,
        turn_number = int(turn_count) + 1,
        theme       = theme,
    )


async def render_overview_for_guild(guild_id: int, conn) -> io.BytesIO:
    from utils.db import get_theme
    theme     = await get_theme(conn, guild_id)
    cfg       = await conn.fetchrow(
        "SELECT active_planet_id FROM guild_config WHERE guild_id=$1", guild_id)
    active_id = cfg["active_planet_id"] if cfg and cfg["active_planet_id"] else 1
    planets   = await conn.fetch(
        "SELECT id, name, contractor, enemy_type FROM planets "
        "WHERE guild_id=$1 ORDER BY id", guild_id)
    return render_galaxy_overview([dict(p) for p in planets], active_id, theme)


# ── Small helpers ──────────────────────────────────────────────────────────────

def _badge(draw, cx, cy, text, bg, fg, font):
    try:
        bb     = draw.textbbox((0, 0), text, font=font)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        px, py = 4, 2
        draw.rounded_rectangle(
            (cx-tw//2-px, cy-th//2-py, cx+tw//2+px, cy+th//2+py),
            radius=3, fill=bg+(210,))
        draw.text((cx-tw//2, cy-th//2), text, font=font, fill=fg+(255,))
    except Exception:
        pass


def _compass(draw, cx, cy, size, font):
    col = (130, 130, 130, 255)
    draw.line((cx, cy-size, cx, cy+size), fill=col, width=1)
    draw.line((cx-size, cy, cx+size, cy), fill=col, width=1)
    draw.ellipse((cx-3, cy-3, cx+3, cy+3), fill=(180,180,180,255))
    for lbl, dx, dy in [("N",0,-1),("S",0,1),("E",1,0),("W",-1,0)]:
        lx, ly = cx+dx*(size+7), cy+dy*(size+7)
        try:
            bb   = draw.textbbox((0, 0), lbl, font=font)
            w, h = bb[2]-bb[0], bb[3]-bb[1]
            draw.text((lx-w//2, ly-h//2), lbl, font=font, fill=col)
        except Exception:
            pass


def _default_theme():
    return {
        "bot_name":       "WARBOT",
        "player_faction": "PMC",
        "enemy_faction":  "Enemy",
        "player_unit":    "Unit",
        "enemy_unit":     "Enemy Unit",
        "safe_zone":      "FOB",
        "flavor_text":    "The contract must be fulfilled.",
        "color":          0xAA2222,
    }
