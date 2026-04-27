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

from utils.hexmap import GRID_COORDS, hex_key, hex_center, hex_corners, GRID_SET

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
    planet_name: str,
    contractor:  str,
    enemy_type:  str,
    hex_data:    dict,   # {"gq,gr": {"terrain": str, "status": str}}
    unit_data:   dict,   # {"gq,gr": {"players": N, "enemy": N}}
    turn_number: int = 1,
    theme:       dict = None,
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

        # Terrain abbr — top center
        abbr = t_def.get("abbr", "")
        if abbr:
            try:
                bb   = draw.textbbox((0,0), abbr, font=f_abbr)
                aw   = (bb[2]-bb[0])/2
                # Use white on dark tiles (mountain), black on everything else
                tc   = (220,220,220,220) if g < 120 else (30,30,30,220)
                draw.text((cx-aw, cy - HEX_SIZE*0.52), abbr, font=f_abbr, fill=tc)
            except Exception:
                pass

        # Global coord label — centered
        lbl = key
        try:
            bb  = draw.textbbox((0,0), lbl, font=f_coord)
            lw  = (bb[2]-bb[0])/2
            lh  = (bb[3]-bb[1])/2
            col = (40,40,40,190) if g > 148 else (185,185,185,190)
            draw.text((cx-lw, cy-lh), lbl, font=f_coord, fill=col)
        except Exception:
            pass

    # ── Draw unit markers in a separate pass so tint compositing can't erase them ──
    draw = ImageDraw.Draw(img)
    for gq, gr in GRID_COORDS:
        key    = hex_key(gq, gr)
        cx, cy = hex_center(gq, gr, HEX_SIZE, ox, oy)
        units  = unit_data.get(key, {})
        p_ct   = units.get("players", 0)
        e_ct   = units.get("enemy",   0)

        if p_ct > 0 or e_ct > 0:
            dot_y = cy + HEX_SIZE * 0.55
            dot_r = 6

            if p_ct > 0 and e_ct > 0:
                draw.rectangle(
                    (cx-dot_r*2-1, dot_y-dot_r, cx-1, dot_y+dot_r),
                    fill=(55,80,200,255), outline=(200,210,255,255), width=1)
                draw.rectangle(
                    (cx+1, dot_y-dot_r, cx+dot_r*2+1, dot_y+dot_r),
                    fill=(190,40,40,255), outline=(255,190,190,255), width=1)
                try:
                    bb = draw.textbbox((0,0), str(p_ct), font=f_pip)
                    pw,ph = (bb[2]-bb[0])/2,(bb[3]-bb[1])/2
                    draw.text((cx-dot_r-pw, dot_y-ph), str(p_ct), font=f_pip,
                              fill=(255,255,255,255))
                    bb = draw.textbbox((0,0), str(e_ct), font=f_pip)
                    ew,eh = (bb[2]-bb[0])/2,(bb[3]-bb[1])/2
                    draw.text((cx+dot_r+1-ew, dot_y-eh), str(e_ct), font=f_pip,
                              fill=(255,255,255,255))
                except Exception:
                    pass
            elif p_ct > 0:
                draw.rectangle(
                    (cx-dot_r, dot_y-dot_r, cx+dot_r, dot_y+dot_r),
                    fill=(55,80,200,255), outline=(200,210,255,255), width=1)
                try:
                    bb = draw.textbbox((0,0), str(p_ct), font=f_pip)
                    pw,ph = (bb[2]-bb[0])/2,(bb[3]-bb[1])/2
                    draw.text((cx-pw, dot_y-ph), str(p_ct), font=f_pip,
                              fill=(255,255,255,255))
                except Exception:
                    pass
            else:
                draw.rectangle(
                    (cx-dot_r, dot_y-dot_r, cx+dot_r, dot_y+dot_r),
                    fill=(190,40,40,255), outline=(255,190,190,255), width=1)
                try:
                    bb = draw.textbbox((0,0), str(e_ct), font=f_pip)
                    ew,eh = (bb[2]-bb[0])/2,(bb[3]-bb[1])/2
                    draw.text((cx-ew, dot_y-eh), str(e_ct), font=f_pip,
                              fill=(255,255,255,255))
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

async def render_map_for_guild(guild_id: int, conn, planet_id: int = None) -> io.BytesIO:
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
        "SELECT hex_address FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
        guild_id, planet_id)
    en_rows = await conn.fetch(
        "SELECT hex_address FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id)

    unit_data: dict = {}
    for r in sq_rows:
        addr = r["hex_address"]
        unit_data.setdefault(addr, {"players": 0, "enemy": 0})
        unit_data[addr]["players"] += 1
    for r in en_rows:
        addr = r["hex_address"]
        unit_data.setdefault(addr, {"players": 0, "enemy": 0})
        unit_data[addr]["enemy"] += 1

    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1", guild_id) or 0
    theme = await get_theme(conn, guild_id)

    return render_planet_map(
        planet_name = planet_name,
        contractor  = contractor,
        enemy_type  = enemy_type,
        hex_data    = hex_data,
        unit_data   = unit_data,
        turn_number = int(turn_count) + 1,
        theme       = theme,
    )


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
