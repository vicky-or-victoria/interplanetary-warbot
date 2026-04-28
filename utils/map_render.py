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
from utils.brigades import brigade_ascii_icon, BRIGADES

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

_SANS    = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
_SERIF   = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"]
_MONO    = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
_SANSREG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"]

# ── Terrain ────────────────────────────────────────────────────────────────────

TERRAIN_TYPES = ["flat", "forest", "hill", "mountain", "fort", "city", "military"]

TERRAIN_DEFS = {
    "flat":     {"fill": 195, "border": 155, "label": "Flat",     "abbr": ""},
    "forest":   {"fill": 138, "border": 102, "label": "Forest",   "abbr": "Fst"},
    "hill":     {"fill": 166, "border": 126, "label": "Hills",    "abbr": "Hll"},
    "mountain": {"fill": 108, "border":  72, "label": "Mtn",      "abbr": "Mtn"},
    "fort":     {"fill": 180, "border": 136, "label": "Fort",     "abbr": "Frt"},
    "city":     {"fill": 215, "border": 165, "label": "City",     "abbr": "Cty"},
    "military": {"fill": 155, "border": 110, "label": "Military", "abbr": "Mil"},
}

STATUS_TINTS = {
    "players":         (70,  90, 210, 78),
    "majority_player": (100, 120, 195, 52),
    "enemy":           (210,  55,  55, 78),
    "majority_enemy":  (190,  90,  90, 52),
    "contested":       (155,  75, 155, 68),
    "neutral":         (0,     0,   0,  0),
}

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
    f_coord  = _font(_MONO,     7)
    f_pip    = _font(_SANS,     7)
    f_legend = _font(_SANSREG, 11)

    # ── Draw all hexes (terrain + status tint + labels) ───────────────────────
    for gq, gr in GRID_COORDS:
        key     = hex_key(gq, gr)
        cx, cy  = hex_center(gq, gr, HEX_SIZE, ox, oy)
        info    = hex_data.get(key, {})
        terrain = info.get("terrain", "flat")
        status  = info.get("status",  "neutral")
        t_def   = TERRAIN_DEFS.get(terrain, TERRAIN_DEFS["flat"])
        g       = t_def["fill"]
        b       = t_def["border"]
        corners = hex_corners(cx, cy, HEX_SIZE - 0.8)

        draw.polygon(corners, fill=(g, g, g, 255))

        tint = STATUS_TINTS.get(status, (0,0,0,0))
        if tint[3] > 0:
            tl = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
            ImageDraw.Draw(tl).polygon(corners, fill=tint)
            img  = Image.alpha_composite(img, tl)
            draw = ImageDraw.Draw(img)

        draw.polygon(corners, outline=(b, b, b, 255), width=1)

        # Terrain abbr — top center (always black with white outline for visibility)
        abbr = t_def.get("abbr", "")
        if abbr:
            try:
                bb   = draw.textbbox((0,0), abbr, font=f_abbr)
                aw   = (bb[2]-bb[0])/2
                ax   = cx - aw
                ay   = cy - HEX_SIZE*0.52
                # White shadow/outline for contrast on all terrain
                for ox2, oy2 in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
                    draw.text((ax+ox2, ay+oy2), abbr, font=f_abbr, fill=(255,255,255,200))
                draw.text((ax, ay), abbr, font=f_abbr, fill=(0,0,0,255))
            except Exception:
                pass

        # Global coord label — centered (always black with white outline)
        lbl = key
        try:
            bb  = draw.textbbox((0,0), lbl, font=f_coord)
            lw  = (bb[2]-bb[0])/2
            lh  = (bb[3]-bb[1])/2
            lx2 = cx - lw
            ly2 = cy - lh
            # White shadow for contrast on dark tiles
            for ox2, oy2 in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
                draw.text((lx2+ox2, ly2+oy2), lbl, font=f_coord, fill=(255,255,255,200))
            draw.text((lx2, ly2), lbl, font=f_coord, fill=(0,0,0,255))
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
            dot_y = cy + HEX_SIZE * 0.55
            dot_r = 6

            # ── Player units: one small badge per brigade type ────────────────
            if p_ct > 0:
                badge_w = 13   # width of each brigade badge
                badge_h = 12
                gap     = 2
                # Collect (icon, count) pairs in stable order
                badges = [(brigade_ascii_icon(bk), cnt)
                          for bk, cnt in sorted(brigades_map.items())
                          if cnt > 0]
                if not badges:
                    # legacy fallback: plain blue square with count
                    badges = [("##", p_ct)]

                total_badge_w = len(badges) * badge_w + (len(badges) - 1) * gap
                # Centre the row of badges under the hex
                bx_start = int(cx - total_badge_w / 2)

                for bi, (icon, cnt) in enumerate(badges):
                    bx = bx_start + bi * (badge_w + gap)
                    by = int(dot_y - badge_h / 2)
                    # Slightly different blue shade so stacked brigades are legible
                    shade = max(40, 70 - bi * 8)
                    draw.rectangle(
                        (bx, by, bx + badge_w, by + badge_h),
                        fill=(shade, shade + 20, 200, 255),
                        outline=(180, 200, 255, 255), width=1)
                    try:
                        # Icon text (e.g. "##", ">>", "[]")
                        bb  = draw.textbbox((0, 0), icon, font=f_pip)
                        iw  = (bb[2] - bb[0]) / 2
                        ih  = (bb[3] - bb[1]) / 2
                        draw.text((bx + badge_w / 2 - iw, by + badge_h / 2 - ih),
                                  icon, font=f_pip, fill=(220, 235, 255, 255))
                        # Count superscript at top-right of badge
                        if cnt > 1:
                            cnt_str = str(cnt)
                            cb = draw.textbbox((0, 0), cnt_str, font=f_pip)
                            cw = cb[2] - cb[0]
                            # tiny white count in top-right corner
                            draw.text((bx + badge_w - cw - 1, by),
                                      cnt_str, font=f_pip, fill=(255, 255, 180, 255))
                    except Exception:
                        pass

            # ── Enemy units: plain red square with count (unchanged) ──────────
            if e_ct > 0:
                if p_ct > 0:
                    # shift enemy badge to the right so it doesn't overlap
                    ex_l = int(cx + p_ct * (13 + 2) / 2 + 2)
                    ex_r = ex_l + dot_r * 2
                else:
                    ex_l = int(cx - dot_r)
                    ex_r = int(cx + dot_r)
                draw.rectangle(
                    (ex_l, dot_y - dot_r, ex_r, dot_y + dot_r),
                    fill=(190, 40, 40, 255), outline=(255, 190, 190, 255), width=1)
                try:
                    bb = draw.textbbox((0, 0), str(e_ct), font=f_pip)
                    ew, eh = (bb[2] - bb[0]) / 2, (bb[3] - bb[1]) / 2
                    draw.text(((ex_l + ex_r) / 2 - ew, dot_y - eh),
                              str(e_ct), font=f_pip, fill=(255, 255, 255, 255))
                except Exception:
                    pass

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

    t_items = [(TERRAIN_DEFS[t]["fill"], TERRAIN_DEFS[t]["label"]) for t in TERRAIN_TYPES]
    s_items = [
        (72, 90,205, f"{theme.get('player_faction','PMC')} ctrl"),
        (205,52, 52, f"{theme.get('enemy_faction','Enemy')} ctrl"),
        (152,72,152, "Contested"),
        (115,115,115,"Neutral"),
    ]
    lx   = 28
    cw_t = (img_w-56) // len(t_items)
    cw_s = (img_w-56) // len(s_items)

    for i,(g,lbl) in enumerate(t_items):
        x = lx+i*cw_t
        draw.rectangle((x,ly+10,x+14,ly+24), fill=(g,g,g,255), outline=(105,105,105,255))
        try: draw.text((x+18,ly+10), lbl, font=f_legend, fill=(162,162,162,255))
        except Exception: pass

    for i,(ri,gi,bi,lbl) in enumerate(s_items):
        x = lx+i*cw_s
        draw.rectangle((x,ly+46,x+14,ly+60), fill=(ri,gi,bi,255), outline=(105,105,105,255))
        try: draw.text((x+18,ly+46), lbl, font=f_legend, fill=(162,162,162,255))
        except Exception: pass

    r2 = 6
    draw.rectangle((lx,    ly+72-r2, lx+r2*2,    ly+72+r2),
                   fill=(55,80,200,230), outline=(200,210,255,255), width=1)
    draw.rectangle((lx+36, ly+72-r2, lx+36+r2*2, ly+72+r2),
                   fill=(190,40,40,230), outline=(255,190,190,255), width=1)
    try:
        draw.text((lx+r2*2+6,    ly+65),
                  f"= {theme.get('player_unit','PMC')} units",
                  font=f_legend, fill=(162,162,162,255))
        draw.text((lx+36+r2*2+6, ly+65),
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

def render_planetary_system_overview(
    planets:          list,
    active_planet_id: int,
    theme:            dict = None,
) -> io.BytesIO:
    if theme is None:
        theme = _default_theme()

    N    = max(len(planets), 1)
    W    = max(1200, N * 220)
    H    = 360
    TBAR = 46
    img  = Image.new("RGB", (W, H), (10,10,10))
    draw = ImageDraw.Draw(img)

    f_title = _font(_SERIF,   18)
    f_name  = _font(_SANS,    13)
    f_sub   = _font(_SANSREG, 10)
    f_tiny  = _font(_MONO,     8)

    draw.rectangle((0,0,W,TBAR), fill=(16,16,16))
    draw.line((28,TBAR-2,W-28,TBAR-2), fill=(65,65,65), width=1)
    try:
        tt = f"{theme.get('bot_name','WARBOT')}  |  Interplanetary Theatre Overview"
        bb = draw.textbbox((0,0), tt, font=f_title)
        tw,th = bb[2]-bb[0],bb[3]-bb[1]
        draw.text(((W-tw)//2,(TBAR-th)//2), tt, font=f_title, fill=(205,205,205))
    except Exception:
        pass

    col_w     = W // N
    content_h = H - TBAR

    for i, planet in enumerate(planets):
        cx        = col_w*i + col_w//2
        cy        = TBAR + content_h//2 + 8
        is_active = (planet.get("id") == active_planet_id)
        R         = 95 if is_active else 46

        for g in range(14 if is_active else 6, 0, -1):
            lum = int(28 * g / (14 if is_active else 6))
            try:
                draw.ellipse((cx-R-g,cy-R-g,cx+R+g,cy+R+g), outline=(lum,lum,lum+10), width=1)
            except Exception:
                pass

        for band in range(R, 0, -7 if is_active else -5):
            g = 50 + int((R-band)/R*(85 if is_active else 50))
            draw.ellipse((cx-band,cy-band,cx+band,cy+band), fill=(g,g,g))

        if is_active:
            for lat in range(-4, 5):
                y_off = lat*(R//5)
                if abs(y_off) >= R: continue
                hw  = int(math.sqrt(max(0, R*R-y_off*y_off)))
                lum = 72 + abs(lat)*6
                draw.line((cx-hw,cy+y_off,cx+hw,cy+y_off), fill=(lum,lum,lum), width=1)

        ry = 11 if is_active else 5
        rw = 20 if is_active else 10
        rc = (140,140,140) if is_active else (60,60,60)
        draw.ellipse((cx-R-rw,cy-ry,cx+R+rw,cy+ry), outline=rc, width=2)
        draw.ellipse((cx-R-rw,cy-ry,cx+R+rw,cy+ry), outline=(10,10,10), width=6)
        draw.ellipse((cx-R-rw+4,cy-ry+3,cx+R+rw-4,cy+ry-3), outline=rc, width=1)

        try:
            badge = "* ACTIVE THEATRE" if is_active else "- STANDBY"
            col   = (110,210,110) if is_active else (80,80,80)
            bb    = draw.textbbox((0,0), badge, font=f_sub)
            bw    = bb[2]-bb[0]
            draw.text((cx-bw//2, cy-R-22), badge, font=f_sub, fill=col)
        except Exception:
            pass

        try:
            bb  = draw.textbbox((0,0), planet["name"], font=f_name)
            nw  = bb[2]-bb[0]
            col = (218,218,218) if is_active else (95,95,95)
            draw.text((cx-nw//2, cy+R+10), planet["name"], font=f_name, fill=col)
        except Exception:
            pass

        for j, sub in enumerate([
            f"Contract: {planet.get('contractor','---')}",
            f"Enemy: {planet.get('enemy_type','---')}",
        ]):
            try:
                bb  = draw.textbbox((0,0), sub, font=f_tiny)
                sw  = bb[2]-bb[0]
                col = (155,155,155) if is_active else (60,60,60)
                draw.text((cx-sw//2, cy+R+28+j*13), sub, font=f_tiny, fill=col)
            except Exception:
                pass

        if i < N-1:
            draw.line((col_w*(i+1),TBAR+8,col_w*(i+1),H-8), fill=(36,36,36), width=1)

    draw.rectangle((1,1,W-2,H-2), outline=(55,55,55), width=2)
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
        hex_data[r["address"]] = {"status": r["status"], "terrain": "flat"}
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
    f_coord = _font(_MONO,  7)
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
        terrain = info.get("terrain", "flat")
        status  = info.get("status",  "neutral")
        t_def   = TERRAIN_DEFS.get(terrain, TERRAIN_DEFS["flat"])
        g       = t_def["fill"]
        b       = t_def["border"]
        corners = hex_corners(cx, cy, HEX_SIZE - 0.8)

        draw.polygon(corners, fill=(g, g, g, 255))

        tint = STATUS_TINTS.get(status, (0,0,0,0))
        if tint[3] > 0:
            tl = Image.new("RGBA", (img_w, img_h), (0,0,0,0))
            ImageDraw.Draw(tl).polygon(corners, fill=tint)
            img  = Image.alpha_composite(img, tl)
            draw = ImageDraw.Draw(img)

        draw.polygon(corners, outline=(b, b, b, 255), width=1)

        abbr = t_def.get("abbr", "")
        if abbr:
            try:
                bb  = draw.textbbox((0,0), abbr, font=f_abbr)
                aw  = (bb[2]-bb[0])/2
                ax, ay = cx-aw, cy - HEX_SIZE*0.52
                for dx2, dy2 in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
                    draw.text((ax+dx2, ay+dy2), abbr, font=f_abbr, fill=(255,255,255,200))
                draw.text((ax, ay), abbr, font=f_abbr, fill=(0,0,0,255))
            except Exception:
                pass

        lbl = key
        try:
            bb  = draw.textbbox((0,0), lbl, font=f_coord)
            lw2 = (bb[2]-bb[0])/2
            lh2 = (bb[3]-bb[1])/2
            lx2, ly2 = cx-lw2, cy-lh2
            for dx2, dy2 in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-1),(0,1),(-1,0),(1,0)]:
                draw.text((lx2+dx2, ly2+dy2), lbl, font=f_coord, fill=(255,255,255,200))
            draw.text((lx2, ly2), lbl, font=f_coord, fill=(0,0,0,255))
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
            dot_y   = cy + HEX_SIZE * 0.55
            dot_r   = 6
            badge_w = 13
            badge_h = 12
            gap     = 2

            if p_ct > 0:
                badges = [(brigade_ascii_icon(bk), cnt)
                          for bk, cnt in sorted(brigades_map.items()) if cnt > 0]
                if not badges:
                    badges = [("##", p_ct)]
                total_badge_w = len(badges) * badge_w + (len(badges) - 1) * gap
                bx_start = int(cx - total_badge_w / 2)
                for bi, (icon, cnt) in enumerate(badges):
                    bx = bx_start + bi * (badge_w + gap)
                    by = int(dot_y - badge_h / 2)
                    shade = max(40, 70 - bi * 8)
                    draw.rectangle(
                        (bx, by, bx + badge_w, by + badge_h),
                        fill=(shade, shade + 20, 200, 255),
                        outline=(180, 200, 255, 255), width=1)
                    try:
                        bb  = draw.textbbox((0, 0), icon, font=f_pip)
                        iw  = (bb[2] - bb[0]) / 2
                        ih  = (bb[3] - bb[1]) / 2
                        draw.text((bx + badge_w / 2 - iw, by + badge_h / 2 - ih),
                                  icon, font=f_pip, fill=(220, 235, 255, 255))
                        if cnt > 1:
                            cnt_str = str(cnt)
                            cb = draw.textbbox((0, 0), cnt_str, font=f_pip)
                            cw = cb[2] - cb[0]
                            draw.text((bx + badge_w - cw - 1, by),
                                      cnt_str, font=f_pip, fill=(255, 255, 180, 255))
                    except Exception:
                        pass

            if e_ct > 0:
                if p_ct > 0:
                    ex_l = int(cx + p_ct * (badge_w + gap) / 2 + 2)
                    ex_r = ex_l + dot_r * 2
                else:
                    ex_l = int(cx - dot_r)
                    ex_r = int(cx + dot_r)
                draw.rectangle(
                    (ex_l, dot_y - dot_r, ex_r, dot_y + dot_r),
                    fill=(190, 40, 40, 255), outline=(255, 190, 190, 255), width=1)

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
        hex_data[r["address"]] = {"status": r["status"], "terrain": "flat"}
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
        hex_data[r["address"]] = {"status": r["status"], "terrain": "flat"}
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
    return render_planetary_system_overview([dict(p) for p in planets], active_id, theme)


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
