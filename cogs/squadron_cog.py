"""
Squadron cog v4 — fully button-driven, no player-facing slash commands.

Players interact via:
  - EnlistView (enlistment board)        → brigade picker → deploy modal
  - UnitPanelView (My Unit button)       → stats + brigade-specific action buttons
  - MoveDirectionView                    → directional pad + fast travel
"""

import random
import discord
from discord.ext import commands

from utils.db import get_pool, ensure_guild, get_theme, get_active_planet_id
from utils.hexmap import (
    hex_key, parse_hex, hex_neighbors, is_valid, GRID_SET,
    hex_distance, DIRECTIONS, DIR_NAMES,
)
from utils.brigades import (
    BRIGADES, BRIGADE_KEYS, get_brigade, brigade_stats,
    transit_turns, move_steps, can_direct_insert,
    can_scavenge_twice, scavenge_bonus, brigade_choices,
)


def _bar(val: int, length: int = 10) -> str:
    filled = max(0, min(length, int((val / 20) * length)))
    return "▓" * filled + "░" * (length - filled)


# ══════════════════════════════════════════════════════════════════════════════
# UNIT PANEL  (opened by "My Unit" button on main menu)
# ══════════════════════════════════════════════════════════════════════════════

async def build_unit_embed(sq, theme: dict, turn_count: int) -> discord.Embed:
    brig = get_brigade(sq["brigade"])
    transit_str = (
        f"\n⚡ **IN TRANSIT** → `{sq['transit_destination']}` "
        f"({sq['transit_turns_left']} turn(s) left)"
        if sq["in_transit"] else ""
    )
    flags = []
    if sq["is_dug_in"]:       flags.append("⛏ Dug In (+4 DEF)")
    if sq["artillery_armed"]: flags.append("🎯 Artillery Armed")
    flag_str = "  ·  ".join(flags) + "\n" if flags else ""

    embed = discord.Embed(
        title=f"{brig['emoji']} {sq['owner_name']} — {sq['name']}",
        color=theme.get("color", 0xAA2222),
        description=(
            f"**Brigade:** {brig['name']}\n"
            f"**Position:** `{sq['hex_address']}`{transit_str}\n"
            f"{flag_str}\n"
            f"```\n"
            f"  ATK  {_bar(sq['attack'])}  {sq['attack']}\n"
            f"  DEF  {_bar(sq['defense'])}  {sq['defense']}\n"
            f"  SPD  {_bar(sq['speed'])}  {sq['speed']}\n"
            f"  MRL  {_bar(sq['morale'])}  {sq['morale']}\n"
            f"  SUP  {_bar(sq['supply'])}  {sq['supply']}\n"
            f"  RCN  {_bar(sq['recon'])}  {sq['recon']}\n"
            f"```"
        ),
    )
    embed.set_footer(text=f"Turn {turn_count} · {theme.get('flavor_text', '')}")
    return embed


class UnitPanelView(discord.ui.View):
    """
    Ephemeral panel shown when a player presses 'My Unit'.
    Row 0: Move  |  Fast Travel  |  Scavenge
    Row 1: Brigade specials (shown only if applicable)
    Row 2: List Units  |  Disband
    """
    def __init__(self, guild_id: int, brigade: str, in_transit: bool):
        super().__init__(timeout=300)
        self.guild_id = guild_id

        # Row 0 — always present
        move_btn = discord.ui.Button(
            label="📍 Move", style=discord.ButtonStyle.primary, row=0,
            disabled=in_transit)
        move_btn.callback = self._move
        self.add_item(move_btn)

        travel_btn = discord.ui.Button(
            label="🚀 Fast Travel", style=discord.ButtonStyle.primary, row=0,
            disabled=in_transit)
        travel_btn.callback = self._fast_travel
        self.add_item(travel_btn)

        scav_btn = discord.ui.Button(
            label="🔍 Scavenge", style=discord.ButtonStyle.secondary, row=0)
        scav_btn.callback = self._scavenge
        self.add_item(scav_btn)

        # Row 1 — brigade specials
        specials = _brigade_special_buttons(guild_id, brigade)
        for btn in specials:
            self.add_item(btn)

        # Row 2 — misc
        list_btn = discord.ui.Button(
            label="📋 List Units", style=discord.ButtonStyle.secondary, row=2)
        list_btn.callback = self._list_units
        self.add_item(list_btn)

        disband_btn = discord.ui.Button(
            label="🗑 Disband", style=discord.ButtonStyle.danger, row=2)
        disband_btn.callback = self._disband
        self.add_item(disband_btn)

    async def _move(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq = await conn.fetchrow(
                "SELECT hex_address, brigade, in_transit, name FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["in_transit"]:
            await interaction.response.send_message("Unit is already in transit.", ephemeral=True); return
        embed = _move_embed(sq["hex_address"], sq["brigade"], sq["name"])
        await interaction.response.send_message(
            embed=embed, view=MoveDirectionView(self.guild_id), ephemeral=True)

    async def _fast_travel(self, interaction: discord.Interaction):
        await interaction.response.send_modal(FastTravelModal(self.guild_id))

    async def _scavenge(self, interaction: discord.Interaction):
        await _do_scavenge(interaction, self.guild_id)

    async def _list_units(self, interaction: discord.Interaction):
        await _do_list_units(interaction, self.guild_id)

    async def _disband(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq = await conn.fetchrow(
                "SELECT id, name FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit to disband.", ephemeral=True); return
        view = _ConfirmDisbandView(interaction.user.id, sq["id"], sq["name"])
        await interaction.response.send_message(
            f"Permanently disband **{sq['name']}**?", view=view, ephemeral=True)


def _brigade_special_buttons(guild_id: int, brigade: str):
    """Return brigade-specific action buttons for row 1."""
    buttons = []

    if brigade == "infantry":
        btn = discord.ui.Button(
            label="⛏ Dig In", style=discord.ButtonStyle.secondary, row=1)
        btn.callback = _make_callback(_do_dig_in, guild_id)
        buttons.append(btn)

    if brigade == "artillery":
        btn = discord.ui.Button(
            label="🎯 Artillery Hold", style=discord.ButtonStyle.secondary, row=1)
        btn.callback = _make_callback(_do_artillery_hold, guild_id)
        buttons.append(btn)

    if brigade == "engineering":
        btn = discord.ui.Button(
            label="🏗 Fortify", style=discord.ButtonStyle.secondary, row=1)
        btn.callback = _make_callback(_do_fortify, guild_id)
        buttons.append(btn)

        btn2 = discord.ui.Button(
            label="🔧 Repair Adjacent", style=discord.ButtonStyle.secondary, row=1)
        btn2.callback = _make_callback(_do_repair, guild_id)
        buttons.append(btn2)

    if brigade in ("aerial", "ranger", "special_ops"):
        btn = discord.ui.Button(
            label="📡 Recon Sweep", style=discord.ButtonStyle.secondary, row=1)
        btn.callback = _make_callback(_do_recon_sweep, guild_id)
        buttons.append(btn)

    return buttons


def _make_callback(fn, guild_id: int):
    """Wrap an async action function so it can be used as a button callback."""
    async def callback(interaction: discord.Interaction):
        await fn(interaction, guild_id)
    return callback


# ── Unit panel builder (called from views/menu.py) ────────────────────────────

async def send_unit_panel(interaction: discord.Interaction, guild_id: int):
    """Build and send the full unit panel as an ephemeral response."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, guild_id)
        planet_id = await get_active_planet_id(conn, guild_id)
        sq        = await conn.fetchrow(
            "SELECT * FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message(
                f"No active {theme.get('player_unit', 'unit')}. "
                f"Head to the enlistment board to join.", ephemeral=True)
            return
        turn_count = await conn.fetchval(
            "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
            guild_id, planet_id) or 0

    embed = await build_unit_embed(sq, theme, turn_count)
    view  = UnitPanelView(guild_id, sq["brigade"], sq["in_transit"])
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BRIGADE-SPECIFIC ACTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def _do_scavenge(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id  = await get_active_planet_id(conn, guild_id)
        turn_count = await conn.fetchval(
            "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
            guild_id, planet_id) or 0
        sq = await conn.fetchrow(
            "SELECT * FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        brig = get_brigade(sq["brigade"])
        if not brig.get("can_scavenge", True):
            await interaction.response.send_message(
                f"{brig['name']} cannot scavenge — too heavy to forage.", ephemeral=True); return
        if sq["last_scavenged_turn"] >= turn_count:
            if not can_scavenge_twice(sq["brigade"]):
                await interaction.response.send_message(
                    "Already scavenged this turn.", ephemeral=True); return
            second_key = f"scavenge2:{sq['id']}:{turn_count}"
            already2   = await conn.fetchval(
                "SELECT COUNT(*) FROM enemy_gm_moves WHERE guild_id=$1 AND target_address=$2",
                guild_id, second_key)
            if already2:
                await interaction.response.send_message(
                    "Rangers can scavenge twice per turn — already used both.", ephemeral=True); return
            await conn.execute(
                "INSERT INTO enemy_gm_moves (guild_id, planet_id, enemy_unit_id, target_address) "
                "VALUES ($1,$2,-1,$3) ON CONFLICT DO NOTHING",
                guild_id, planet_id, second_key)

        gain       = random.randint(2, 6) + (sq["recon"] // 5) + scavenge_bonus(sq["brigade"])
        new_supply = min(20, sq["supply"] + gain)
        await conn.execute(
            "UPDATE squadrons SET supply=$1, last_scavenged_turn=$2 WHERE id=$3",
            new_supply, turn_count, sq["id"])
    await interaction.response.send_message(
        f"🔍 Scavenged **+{gain}** supply → `{new_supply}/20`.", ephemeral=True)


async def _do_dig_in(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id = await get_active_planet_id(conn, guild_id)
        sq = await conn.fetchrow(
            "SELECT id, name, brigade FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["brigade"] != "infantry":
            await interaction.response.send_message(
                "Only Infantry Brigade can dig in.", ephemeral=True); return
        await conn.execute("UPDATE squadrons SET is_dug_in=TRUE WHERE id=$1", sq["id"])
    await interaction.response.send_message(
        f"⛏ **{sq['name']}** is dug in. +4 defense until next move.", ephemeral=True)


async def _do_artillery_hold(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id = await get_active_planet_id(conn, guild_id)
        sq = await conn.fetchrow(
            "SELECT id, name, brigade FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["brigade"] != "artillery":
            await interaction.response.send_message(
                "Only Artillery Brigade can use this.", ephemeral=True); return
        await conn.execute("UPDATE squadrons SET artillery_armed=TRUE WHERE id=$1", sq["id"])
    await interaction.response.send_message(
        f"🎯 **{sq['name']}** armed and holding — splash damage fires next combat.", ephemeral=True)


async def _do_fortify(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id = await get_active_planet_id(conn, guild_id)
        sq = await conn.fetchrow(
            "SELECT id, name, brigade, hex_address FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["brigade"] != "engineering":
            await interaction.response.send_message(
                "Only Engineering Brigade can fortify hexes.", ephemeral=True); return
        await conn.execute("""
            INSERT INTO hex_terrain (guild_id, planet_id, address, terrain)
            VALUES ($1,$2,$3,'fort')
            ON CONFLICT (guild_id, planet_id, address) DO UPDATE SET terrain='fort'
        """, guild_id, planet_id, sq["hex_address"])
    await interaction.response.send_message(
        f"🏗 Hex `{sq['hex_address']}` has been **fortified** permanently.", ephemeral=True)


async def _do_repair(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id = await get_active_planet_id(conn, guild_id)
        sq = await conn.fetchrow(
            "SELECT id, name, brigade, hex_address FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["brigade"] != "engineering":
            await interaction.response.send_message(
                "Only Engineering Brigade can repair adjacent units.", ephemeral=True); return
        nbrs     = hex_neighbors(sq["hex_address"])
        repaired = await conn.fetch(
            "SELECT id, supply FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND hex_address=ANY($3::text[])",
            guild_id, planet_id, nbrs)
        count = 0
        for unit in repaired:
            await conn.execute(
                "UPDATE squadrons SET supply=$1 WHERE id=$2",
                min(20, unit["supply"] + 4), unit["id"])
            count += 1
    if count == 0:
        await interaction.response.send_message(
            "No friendly units on adjacent hexes to repair.", ephemeral=True)
    else:
        await interaction.response.send_message(
            f"🔧 Repaired **{count}** adjacent unit(s) (+4 supply each).", ephemeral=True)


async def _do_recon_sweep(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        planet_id = await get_active_planet_id(conn, guild_id)
        sq = await conn.fetchrow(
            "SELECT id, name, brigade, hex_address, recon FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["brigade"] not in ("aerial", "ranger", "special_ops"):
            await interaction.response.send_message(
                "Only Aerial, Ranger, or Special Ops can perform recon sweeps.", ephemeral=True); return
        radius = 3 if sq["brigade"] == "ranger" else 2
        from utils.hexmap import hexes_within
        nearby_keys = set(hexes_within(sq["hex_address"], radius))
        enemies = await conn.fetch(
            "SELECT hex_address, unit_type, attack, defense FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
            guild_id, planet_id)
        found = [e for e in enemies if e["hex_address"] in nearby_keys]
    if not found:
        await interaction.response.send_message(
            f"📡 Recon sweep complete — no contacts within {radius} hexes.", ephemeral=True)
    else:
        lines = [
            f"`{e['hex_address']}` — {e['unit_type']} (ATK:{e['attack']} DEF:{e['defense']})"
            for e in found
        ]
        await interaction.response.send_message(
            f"**📡 Recon — {len(found)} contact(s) within {radius} hexes:**\n" + "\n".join(lines),
            ephemeral=True)


async def _do_list_units(interaction: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, guild_id)
        planet_id = await get_active_planet_id(conn, guild_id)
        rows      = await conn.fetch(
            "SELECT owner_name, name, brigade, hex_address, in_transit, supply "
            "FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE ORDER BY owner_name",
            guild_id, planet_id)
    if not rows:
        await interaction.response.send_message("No active units on this planet.", ephemeral=True); return
    lines = []
    for r in rows:
        brig = get_brigade(r["brigade"])
        t    = " (transit)" if r["in_transit"] else ""
        lines.append(
            f"{brig['emoji']} **{r['owner_name']}** — {r['name']} "
            f"[{brig['name']}] @ `{r['hex_address']}`{t} SUP:{r['supply']}")
    embed = discord.Embed(
        title=f"Active Units ({len(rows)})",
        color=theme.get("color", 0xAA2222),
        description="\n".join(lines))
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BRIGADE PICKER  (triggered from EnlistView)
# ══════════════════════════════════════════════════════════════════════════════

def brigade_picker_embed(unit_name: str) -> discord.Embed:
    lines = []
    for key, b in BRIGADES.items():
        s = b["stats"]
        lines.append(
            f"**{b['emoji']} {b['name']}**\n"
            f"  {b['description']}\n"
            f"  ATK:{s['attack']} DEF:{s['defense']} SPD:{s['speed']} "
            f"MRL:{s['morale']} SUP:{s['supply']} RCN:{s['recon']}\n"
            f"  Transit: {b['transit_turns']} turn(s)  ·  "
            + "  ·  ".join(b["specials"][:2])
        )
    return discord.Embed(
        title=f"Choose Your Brigade — {unit_name}",
        description="\n\n".join(lines),
        color=0xAA2222,
    ).set_footer(text="Select your brigade from the dropdown below.")


class BrigadePickerView(discord.ui.View):
    def __init__(self, guild_id: int, unit_name: str):
        super().__init__(timeout=120)
        self.guild_id  = guild_id
        self.unit_name = unit_name
        self.add_item(BrigadeSelect(guild_id, unit_name))


class BrigadeSelect(discord.ui.Select):
    def __init__(self, guild_id: int, unit_name: str):
        self.guild_id  = guild_id
        self.unit_name = unit_name
        options = [
            discord.SelectOption(
                label=f"{b['emoji']} {b['name']}",
                value=key,
                description=b["description"][:100],
            )
            for key, b in BRIGADES.items()
        ]
        super().__init__(placeholder="Choose your brigade...", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            DeployModal(self.guild_id, self.unit_name, self.values[0]))


class DeployModal(discord.ui.Modal, title="Deploy Your Unit"):
    destination = discord.ui.TextInput(
        label="Deployment Hex (global coord)",
        placeholder="e.g. 3,-2 or 0,0",
        max_length=12,
        required=True,
    )

    def __init__(self, guild_id: int, unit_name: str, brigade: str):
        super().__init__()
        self.guild_id  = guild_id
        self.unit_name = unit_name
        self.brigade   = brigade

    async def on_submit(self, interaction: discord.Interaction):
        dest = str(self.destination).strip()
        if not is_valid(dest):
            await interaction.response.send_message(
                f"Invalid hex `{dest}`. Use format `gq,gr` e.g. `3,-2`.", ephemeral=True); return

        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, self.guild_id)
            planet_id = await get_active_planet_id(conn, self.guild_id)
            existing  = await conn.fetchrow(
                "SELECT id FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE",
                self.guild_id, planet_id, interaction.user.id)
            if existing:
                await interaction.response.send_message(
                    "You already have an active unit.", ephemeral=True); return

            stats = brigade_stats(self.brigade)
            v     = lambda base: base + random.randint(-1, 2)
            await conn.execute("""
                INSERT INTO squadrons
                  (guild_id, planet_id, owner_id, owner_name, name, brigade, hex_address,
                   attack, defense, speed, morale, supply, recon)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
                self.guild_id, planet_id,
                interaction.user.id, interaction.user.display_name,
                self.unit_name, self.brigade, dest,
                v(stats["attack"]), v(stats["defense"]), v(stats["speed"]),
                v(stats["morale"]), v(stats["supply"]),  v(stats["recon"]))

            cfg = await conn.fetchrow(
                "SELECT player_role_id FROM guild_config WHERE guild_id=$1", self.guild_id)
            if cfg and cfg["player_role_id"]:
                role = interaction.guild.get_role(cfg["player_role_id"])
                if role:
                    try:
                        await interaction.user.add_roles(role)
                    except discord.Forbidden:
                        pass

            try:
                from views.menu import refresh_enlist_counter
                await refresh_enlist_counter(interaction.client, self.guild_id, conn)
            except Exception:
                pass

            # Update hex controller so unit appears on map immediately
            try:
                from utils.hexmap import recompute_statuses
                await recompute_statuses(conn, self.guild_id, planet_id)
            except Exception:
                pass

        brig = get_brigade(self.brigade)
        embed = discord.Embed(
            title=f"{brig['emoji']} Enlisted — {self.unit_name}",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Brigade:** {brig['name']}\n"
                f"**Deployed at:** `{dest}`\n\n"
                f"```\n"
                f"  ATK  {_bar(stats['attack'])}  {stats['attack']}\n"
                f"  DEF  {_bar(stats['defense'])}  {stats['defense']}\n"
                f"  SPD  {_bar(stats['speed'])}  {stats['speed']}\n"
                f"  MRL  {_bar(stats['morale'])}  {stats['morale']}\n"
                f"  SUP  {_bar(stats['supply'])}  {stats['supply']}\n"
                f"  RCN  {_bar(stats['recon'])}  {stats['recon']}\n"
                f"```\n"
                + "\n".join(f"  · {s}" for s in brig["specials"])
            ),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MOVE PAD
# ══════════════════════════════════════════════════════════════════════════════

def _move_embed(hex_addr: str, brigade: str, unit_name: str) -> discord.Embed:
    brig  = get_brigade(brigade)
    steps = move_steps(brigade)
    nbrs  = hex_neighbors(hex_addr)
    return discord.Embed(
        title=f"📍 Move — {brig['emoji']} {unit_name}",
        description=(
            f"At `{hex_addr}` · Moves **{steps}** hex(es) per step\n"
            f"Adjacent: {', '.join(f'`{n}`' for n in nbrs) or 'none'}"
        ),
        color=0x445588,
    )


class MoveDirectionView(discord.ui.View):
    """
    Hex directional pad. Flat-top layout:
      Row 0: [NW]  [NE]
      Row 1: [W ]  [·]  [E]
      Row 2: [SW]  [SE]
      Row 3: [Fast Travel]  [Done]
    """
    def __init__(self, guild_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id

    async def _do_move(self, interaction: discord.Interaction, dir_index: int):
        dq, dr = DIRECTIONS[dir_index]
        pool   = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, in_transit FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True); return
            if sq["in_transit"]:
                await interaction.response.send_message("Unit is in transit.", ephemeral=True); return

            steps    = move_steps(sq["brigade"])
            new_addr = sq["hex_address"]
            for _ in range(steps):
                gq, gr    = parse_hex(new_addr)
                candidate = hex_key(gq + dq, gr + dr)
                if (gq + dq, gr + dr) in GRID_SET:
                    new_addr = candidate
                else:
                    break

            if new_addr == sq["hex_address"]:
                await interaction.response.send_message(
                    "Cannot move that direction — grid edge.", ephemeral=True); return

            await conn.execute(
                "UPDATE squadrons SET hex_address=$1, is_dug_in=FALSE, "
                "artillery_armed=FALSE WHERE id=$2",
                new_addr, sq["id"])

        embed = _move_embed(new_addr, sq["brigade"], sq["name"])
        await interaction.response.edit_message(embed=embed, view=MoveDirectionView(self.guild_id))

    @discord.ui.button(label="NW", style=discord.ButtonStyle.secondary, row=0)
    async def nw(self, i, b): await self._do_move(i, 4)

    @discord.ui.button(label="NE", style=discord.ButtonStyle.secondary, row=0)
    async def ne(self, i, b): await self._do_move(i, 5)

    @discord.ui.button(label="W",  style=discord.ButtonStyle.primary,   row=1)
    async def west(self, i, b): await self._do_move(i, 3)

    @discord.ui.button(label="·",  style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def center(self, i, b): pass

    @discord.ui.button(label="E",  style=discord.ButtonStyle.primary,   row=1)
    async def east(self, i, b): await self._do_move(i, 0)

    @discord.ui.button(label="SW", style=discord.ButtonStyle.secondary, row=2)
    async def sw(self, i, b): await self._do_move(i, 2)

    @discord.ui.button(label="SE", style=discord.ButtonStyle.secondary, row=2)
    async def se(self, i, b): await self._do_move(i, 1)

    @discord.ui.button(label="🚀 Fast Travel", style=discord.ButtonStyle.danger, row=3)
    async def fast_travel(self, i, b):
        await i.response.send_modal(FastTravelModal(self.guild_id))

    @discord.ui.button(label="✓ Done", style=discord.ButtonStyle.success, row=3)
    async def done(self, i, b):
        await i.response.edit_message(
            embed=discord.Embed(description="Movement complete.", color=0x446644),
            view=None)


class FastTravelModal(discord.ui.Modal, title="Fast Travel"):
    destination = discord.ui.TextInput(
        label="Destination Hex",
        placeholder="e.g. 5,-3",
        max_length=12,
        required=True,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        dest = str(self.destination).strip()
        if not is_valid(dest):
            await interaction.response.send_message(f"Invalid hex `{dest}`.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, in_transit FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, interaction.user.id)
            if not sq or sq["in_transit"]:
                await interaction.response.send_message("No available unit.", ephemeral=True); return
            dist  = hex_distance(sq["hex_address"], dest)
            turns = (1 if can_direct_insert(sq["brigade"])
                     else transit_turns(sq["brigade"]) + max(0, (dist - 3) // 3))
            await conn.execute(
                "UPDATE squadrons SET in_transit=TRUE, transit_destination=$1, "
                "transit_turns_left=$2, is_dug_in=FALSE, artillery_armed=FALSE WHERE id=$3",
                dest, turns, sq["id"])
        brig = get_brigade(sq["brigade"])
        await interaction.response.send_message(
            f"{brig['emoji']} **{sq['name']}** en route to `{dest}` — **{turns} turn(s)**.",
            ephemeral=True)


# ── Disband confirm ────────────────────────────────────────────────────────────

class _ConfirmDisbandView(discord.ui.View):
    def __init__(self, user_id, sq_id, sq_name):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.sq_id   = sq_id
        self.sq_name = sq_name

    async def interaction_check(self, i):
        return i.user.id == self.user_id

    @discord.ui.button(label="Yes, Disband", style=discord.ButtonStyle.danger)
    async def confirm(self, i, b):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE squadrons SET is_active=FALSE WHERE id=$1", self.sq_id)
        self.stop()
        await i.response.edit_message(content=f"**{self.sq_name}** disbanded.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, i, b):
        self.stop()
        await i.response.edit_message(content="Cancelled.", view=None)


# ══════════════════════════════════════════════════════════════════════════════
# COG (minimal — only registers persistent views on startup)
# ══════════════════════════════════════════════════════════════════════════════

class SquadronCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(SquadronCog(bot))
    from views.menu import EnlistView
    bot.add_view(EnlistView(guild_id=0))
