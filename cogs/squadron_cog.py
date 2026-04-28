"""
Squadron cog v4 — fully button-driven, no player-facing slash commands.

Players interact via:
  - EnlistView (enlistment board)        → brigade picker → deploy modal
  - UnitPanelView (My Unit button)       → stats + brigade-specific action buttons
  - MoveDirectionView                    → directional pad (speed-capped per turn)
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


def _bar(val: int, length: int = 10, max_val: int = 20) -> str:
    filled = max(0, min(length, int((val / max_val) * length)))
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

    hp     = sq["hp"] if "hp" in sq.keys() else 100
    max_hp = 100
    hp_bar = _bar(hp, max_val=max_hp, length=10)
    embed = discord.Embed(
        title=f"{brig['emoji']} {sq['owner_name']} — {sq['name']}",
        color=theme.get("color", 0xAA2222),
        description=(
            f"**Brigade:** {brig['name']}\n"
            f"**Position:** `{sq['hex_address']}`{transit_str}\n"
            f"{flag_str}\n"
            f"```\n"
            f"   HP  {hp_bar}  {hp}/{max_hp}\n"
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
    Row 0: Move  |  Scavenge
    Row 1: Brigade specials (shown only if applicable)
    Row 2: List Units
    """
    def __init__(self, guild_id: int, brigade: str, in_transit: bool, move_exhausted: bool = False):
        super().__init__(timeout=300)
        self.guild_id = guild_id

        # Row 0 — always present
        move_btn = discord.ui.Button(
            label="📍 Move", style=discord.ButtonStyle.primary, row=0,
            disabled=in_transit or move_exhausted)
        move_btn.callback = self._move
        self.add_item(move_btn)

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

    async def _move(self, interaction: discord.Interaction):
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq = await conn.fetchrow(
                "SELECT hex_address, brigade, in_transit, name, speed, hexes_moved_this_turn "
                "FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True); return
        if sq["in_transit"]:
            await interaction.response.send_message("Unit is already in transit.", ephemeral=True); return
        budget    = sq["speed"] // 2
        remaining = max(0, budget - sq["hexes_moved_this_turn"])
        if remaining == 0:
            await interaction.response.send_message(
                f"⛔ Movement exhausted for this turn (budget: {budget} hexes).", ephemeral=True); return
        max_s = move_steps(sq["brigade"])
        embed = _move_embed(sq["hex_address"], sq["brigade"], sq["name"], remaining=remaining, budget=budget)
        await interaction.response.send_message(
            embed=embed, view=MoveDirectionView(self.guild_id, max_steps=max_s, chosen_steps=min(max_s, remaining)),
            ephemeral=True)

    async def _scavenge(self, interaction: discord.Interaction):
        await _do_scavenge(interaction, self.guild_id)

    async def _list_units(self, interaction: discord.Interaction):
        await _do_list_units(interaction, self.guild_id)


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

    budget        = sq["speed"] // 2 if "speed" in sq.keys() else 5
    move_exhausted = sq["hexes_moved_this_turn"] >= budget if "hexes_moved_this_turn" in sq.keys() else False
    embed = await build_unit_embed(sq, theme, turn_count)
    view  = UnitPanelView(guild_id, sq["brigade"], sq["in_transit"], move_exhausted=move_exhausted)
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
            DeployModal(interaction.guild_id, self.unit_name, self.values[0]))


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

        # Defer immediately — DB work + map renders can exceed Discord's 3-second window
        await interaction.response.defer(ephemeral=True)

        # Guarantee guild_config row exists so active_planet_id is never NULL
        await ensure_guild(self.guild_id)

        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, self.guild_id)
            planet_id = await get_active_planet_id(conn, self.guild_id)
            existing  = await conn.fetchrow(
                "SELECT id FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE",
                self.guild_id, planet_id, interaction.user.id)
            if existing:
                await interaction.followup.send(
                    "You already have an active unit.", ephemeral=True); return

            # Block re-enlist if they have a dead unit (hp=0) this contract
            dead = await conn.fetchrow(
                "SELECT id FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND hp<=0",
                self.guild_id, planet_id, interaction.user.id)
            if dead:
                await interaction.followup.send(
                    "❌ Your unit was **permanently destroyed** this contract. "
                    "You cannot re-enlist until the GM concludes the current contract.",
                    ephemeral=True); return

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

        # Push live map channel so new unit is visible without waiting for next turn
        try:
            from cogs.map_cog import auto_update_map, auto_update_overview
            await auto_update_map(interaction.client, self.guild_id)
            await auto_update_overview(interaction.client, self.guild_id)
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
        await interaction.followup.send(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MOVE PAD
# ══════════════════════════════════════════════════════════════════════════════

def _move_embed(hex_addr: str, brigade: str, unit_name: str,
               chosen_steps: int = None, remaining: int = None, budget: int = None) -> discord.Embed:
    brig  = get_brigade(brigade)
    steps = move_steps(brigade)
    nbrs  = hex_neighbors(hex_addr)
    if chosen_steps is None:
        chosen_steps = steps
    step_note = (
        f"Moving **{chosen_steps}** hex{'es' if chosen_steps > 1 else ''} per press "
        f"(max {steps})"
        if steps > 1 else
        f"Moves **1** hex per step"
    )
    budget_note = (
        f"\n🏃 **{remaining}/{budget}** hexes remaining this turn"
        if remaining is not None and budget is not None else ""
    )
    exhausted_note = "\n⛔ **Movement exhausted** — resets next turn." if remaining == 0 else ""
    return discord.Embed(
        title=f"📍 Move — {brig['emoji']} {unit_name}",
        description=(
            f"At `{hex_addr}` · {step_note}"
            f"{budget_note}{exhausted_note}\n"
            f"Adjacent: {', '.join(f'`{n}`' for n in nbrs) or 'none'}"
        ),
        color=0x445588 if (remaining or 1) > 0 else 0x554444,
    )


class MoveDirectionView(discord.ui.View):
    """
    Hex directional pad for a flat-top hex grid.

    On a flat-top hex grid the six directions map visually to:
        NW  N  NE
        SW  S  SE

    Discord allows max 5 rows of up to 5 components each.

    Layout:
      Row 0: [NW] [N ] [NE]
      Row 1: [SW] [S ] [SE]
      Row 2: Step selector (Select) — only added when move_steps > 1
      Row 3: [🚀 Fast Travel]  [✓ Done]

    When a unit has move_steps > 1 the player can pick how many hexes to
    traverse in a single press (1 … move_steps) via the Select on row 2.
    The selection persists across button presses until they change it.
    """

    # DIRECTIONS index mapping (from hexmap.py):
    # 0=E(1,0), 1=SE(0,1), 2=SW(-1,1), 3=W(-1,0), 4=NW(0,-1), 5=NE(1,-1)
    # Visual mapping on the rendered flat-top map:
    #   NW=(0,-1), N=(1,-1), NE=(1,0), SW=(-1,0), S=(-1,1), SE=(0,1)
    _DIR = {
        "NW": 3,   # (-1, 0) upper-left
        "N":  4,   # (0, -1) straight up
        "NE": 5,   # (1, -1) upper-right
        "SW": 2,   # (-1,+1) lower-left
        "S":  1,   # (0, +1) straight down
        "SE": 0,   # (1,  0) lower-right
    }

    def __init__(self, guild_id: int, max_steps: int = 1, chosen_steps: int = 1, remaining: int = None):
        super().__init__(timeout=120)
        self.guild_id     = guild_id
        self.max_steps    = max_steps
        self.chosen_steps = chosen_steps  # how many hexes the player has chosen to move
        self.remaining    = remaining     # hexes left in turn budget (None = uncapped legacy)

        # Disable all direction buttons immediately if movement is exhausted
        exhausted = remaining is not None and remaining <= 0
        for btn_attr in ("nw", "n", "ne", "sw", "s", "se"):
            btn = getattr(self, btn_attr, None)
            if btn is not None:
                btn.disabled = exhausted

        # ── Step selector (row 2) — only when unit can move more than 1 hex ──
        if max_steps > 1 and not exhausted:
            select = StepSelect(max_steps=max_steps, current=chosen_steps)
            select.row = 2
            self.add_item(select)

    # ── Internal move logic ────────────────────────────────────────────────────

    async def _do_move(self, interaction: discord.Interaction, dir_key: str):
        # Defer immediately — DB work + map render can exceed Discord's 3-second window.
        # ephemeral=True keeps the response visible only to the player.
        await interaction.response.defer(ephemeral=True)

        dir_index = self._DIR[dir_key]
        dq, dr    = DIRECTIONS[dir_index]
        pool      = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, in_transit, speed, hexes_moved_this_turn "
                "FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.followup.send("No active unit.", ephemeral=True)
                return
            if sq["in_transit"]:
                await interaction.followup.send("Unit is in transit.", ephemeral=True)
                return

            budget    = sq["speed"] // 2
            remaining = max(0, budget - sq["hexes_moved_this_turn"])
            if remaining <= 0:
                await interaction.followup.send(
                    f"⛔ Movement exhausted for this turn (budget: {budget} hexes). Resets next turn.",
                    ephemeral=True)
                return

            max_s    = move_steps(sq["brigade"])
            steps    = min(self.chosen_steps, max_s, remaining)  # never exceed remaining budget
            old_addr = sq["hex_address"]
            new_addr = old_addr

            for _ in range(steps):
                gq, gr    = parse_hex(new_addr)
                candidate = hex_key(gq + dq, gr + dr)
                if (gq + dq, gr + dr) in GRID_SET:
                    new_addr = candidate
                else:
                    break

            if new_addr == old_addr:
                await interaction.followup.send(
                    "Cannot move that direction — grid edge.", ephemeral=True)
                return

            actual_steps = hex_distance(old_addr, new_addr) if new_addr != old_addr else steps
            await conn.execute(
                "UPDATE squadrons SET hex_address=$1, is_dug_in=FALSE, "
                "artillery_armed=FALSE, "
                "hexes_moved_this_turn=hexes_moved_this_turn+$2 WHERE id=$3",
                new_addr, actual_steps, sq["id"])
            new_remaining = max(0, remaining - actual_steps)

            # Persist this arrow so it survives across subsequent move actions
            await conn.execute(
                "INSERT INTO movement_arrows "
                "(guild_id, planet_id, from_addr, to_addr, side, owner_id) "
                "VALUES ($1, $2, $3, $4, 'player', $5)",
                interaction.guild_id, planet_id, old_addr, new_addr, interaction.user.id)

            # Load ALL accumulated arrows for this guild this turn
            arrow_rows = await conn.fetch(
                "SELECT from_addr, to_addr, side FROM movement_arrows "
                "WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id)
            all_arrows = [(r["from_addr"], r["to_addr"], r["side"]) for r in arrow_rows]

            map_buf = None
            try:
                from utils.map_render import render_movement_map_for_guild
                map_buf = await render_movement_map_for_guild(
                    guild_id  = interaction.guild_id,
                    conn      = conn,
                    from_addr = old_addr,
                    to_addr   = new_addr,
                    unit_name = sq["name"],
                    planet_id = planet_id,
                    remaining = new_remaining,
                    budget    = budget,
                )
            except Exception:
                pass

        # Update the live global map showing ALL arrows accumulated this turn
        try:
            from cogs.map_cog import auto_update_map
            await auto_update_map(
                interaction.client,
                interaction.guild_id,
                movement_arrows=all_arrows,
            )
        except Exception:
            pass

        embed = _move_embed(new_addr, sq["brigade"], sq["name"],
                          chosen_steps=min(self.chosen_steps, new_remaining),
                          remaining=new_remaining, budget=budget)
        new_view = MoveDirectionView(self.guild_id, max_steps=max_s,
                                     chosen_steps=min(self.chosen_steps, max(1, new_remaining)),
                                     remaining=new_remaining)
        if map_buf:
            file = discord.File(map_buf, filename="movement.png")
            embed.set_image(url="attachment://movement.png")
            await interaction.followup.send(embed=embed, view=new_view, file=file, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=new_view, ephemeral=True)

    # ── Direction buttons ──────────────────────────────────────────────────────
    # Row 0 — NW  N  NE
    @discord.ui.button(label="NW", style=discord.ButtonStyle.secondary, row=0)
    async def nw(self, i, b): await self._do_move(i, "NW")

    @discord.ui.button(label="N",  style=discord.ButtonStyle.primary,   row=0)
    async def n(self, i, b):  await self._do_move(i, "N")

    @discord.ui.button(label="NE", style=discord.ButtonStyle.secondary, row=0)
    async def ne(self, i, b): await self._do_move(i, "NE")

    # Row 1 — SW  S  SE
    @discord.ui.button(label="SW", style=discord.ButtonStyle.secondary, row=1)
    async def sw(self, i, b): await self._do_move(i, "SW")

    @discord.ui.button(label="S",  style=discord.ButtonStyle.primary,   row=1)
    async def s(self, i, b):  await self._do_move(i, "S")

    @discord.ui.button(label="SE", style=discord.ButtonStyle.secondary, row=1)
    async def se(self, i, b): await self._do_move(i, "SE")

    # Row 3 — action buttons (row 2 is reserved for StepSelect when present)
    @discord.ui.button(label="✓ Done", style=discord.ButtonStyle.success, row=3)
    async def done(self, i, b):
        await i.response.edit_message(
            embed=discord.Embed(description="Movement complete.", color=0x446644),
            view=None)


class StepSelect(discord.ui.Select):
    """
    Dropdown that lets the player choose how many hexes to move per button press.
    Appears on row 3 of MoveDirectionView only when the brigade's move_steps > 1.
    """
    def __init__(self, max_steps: int, current: int):
        options = [
            discord.SelectOption(
                label=f"Move {n} hex{'es' if n > 1 else ''}",
                value=str(n),
                default=(n == current),
                description="per direction press",
            )
            for n in range(1, max_steps + 1)
        ]
        super().__init__(
            placeholder=f"Step size: {current} hex{'es' if current > 1 else ''}",
            options=options,
            min_values=1,
            max_values=1,
        )
        self.max_steps = max_steps

    async def callback(self, interaction: discord.Interaction):
        chosen = int(self.values[0])
        new_view = MoveDirectionView(
            guild_id     = self.view.guild_id,
            max_steps    = self.max_steps,
            chosen_steps = chosen,
        )
        await interaction.response.edit_message(view=new_view)



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
