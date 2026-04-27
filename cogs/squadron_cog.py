"""
Squadron cog v3 — brigade system, directional move pad, brigade-specific actions.
"""

import random
import discord
from discord import app_commands
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


def _bar(val: int, length: int = 12) -> str:
    filled = max(0, min(length, int((val / 20) * length)))
    return "+" * filled + "-" * (length - filled)


class SquadronCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Enlist ────────────────────────────────────────────────────────────────

    @app_commands.command(name="enlist",
                          description="Enlist in the PMC and choose your brigade.")
    @app_commands.describe(unit_name="Name for your unit (max 32 chars)")
    async def enlist(self, interaction: discord.Interaction, unit_name: str):
        await ensure_guild(interaction.guild_id)
        if len(unit_name) > 32:
            await interaction.response.send_message("Unit name max 32 chars.", ephemeral=True)
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT game_started FROM guild_config WHERE guild_id=$1",
                interaction.guild_id)
            if not cfg or not cfg["game_started"]:
                await interaction.response.send_message(
                    "The war has not started yet. An admin must run `/game_start`.",
                    ephemeral=True)
                return
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            existing  = await conn.fetchrow(
                "SELECT id FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE",
                interaction.guild_id, planet_id, interaction.user.id)
            if existing:
                await interaction.response.send_message(
                    "You already have an active unit on this planet.", ephemeral=True)
                return

        await interaction.response.send_message(
            embed=_brigade_picker_embed(unit_name),
            view=BrigadePickerView(interaction.guild_id, unit_name),
            ephemeral=True)

    # ── Unit status ───────────────────────────────────────────────────────────

    @app_commands.command(name="unit_status", description="View your unit's stats and position.")
    async def unit_status(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT * FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message(
                    "No active unit. Use `/enlist` to join.", ephemeral=True)
                return
            turn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id) or 0
            nearby = await conn.fetch(
                "SELECT hex_address, unit_type FROM enemy_units "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                interaction.guild_id, planet_id)

        brig     = get_brigade(sq["brigade"])
        nbrs     = set(hex_neighbors(sq["hex_address"]))
        near_str = "\n".join(
            f"  `{r['hex_address']}` — {r['unit_type']}"
            for r in nearby if r["hex_address"] in nbrs
        )
        transit_str = (
            f"\n**IN TRANSIT** to `{sq['transit_destination']}` "
            f"({sq['transit_turns_left']} turns left)"
            if sq["in_transit"] else ""
        )
        flags = []
        if sq["is_dug_in"]:       flags.append("Dug In (+4 DEF)")
        if sq["artillery_armed"]: flags.append("Artillery Armed")
        flag_str = "  |  ".join(flags) if flags else ""

        embed = discord.Embed(
            title=f"{brig['emoji']} {sq['owner_name']} — {sq['name']}",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Brigade:** {brig['name']}\n"
                f"**Position:** `{sq['hex_address']}`{transit_str}\n"
                f"{flag_str}\n\n"
                f"**Stats**\n"
                f"  ATK  {_bar(sq['attack'])}  {sq['attack']}\n"
                f"  DEF  {_bar(sq['defense'])}  {sq['defense']}\n"
                f"  SPD  {_bar(sq['speed'])}  {sq['speed']}\n"
                f"  MRL  {_bar(sq['morale'])}  {sq['morale']}\n"
                f"  SUP  {_bar(sq['supply'])}  {sq['supply']}\n"
                f"  RCN  {_bar(sq['recon'])}  {sq['recon']}\n"
                + (f"\n**Adjacent threats:**\n{near_str}" if near_str else "")
            ),
        )
        embed.set_footer(text=f"Turn {turn_count} | {theme.get('flavor_text','')}")
        await interaction.response.send_message(
            embed=embed, view=MoveDirectionView(interaction.guild_id), ephemeral=True)

    # ── Move (directional pad entry point) ───────────────────────────────────

    @app_commands.command(name="move",
                          description="Move your unit — opens a directional control pad.")
    async def move(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT hex_address, brigade, in_transit, name FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit.", ephemeral=True)
            return
        if sq["in_transit"]:
            await interaction.response.send_message(
                "Your unit is already in transit.", ephemeral=True)
            return
        embed = _move_embed(sq["hex_address"], sq["brigade"], sq["name"])
        await interaction.response.send_message(
            embed=embed, view=MoveDirectionView(interaction.guild_id), ephemeral=True)

    # ── Fast travel (long-distance) ───────────────────────────────────────────

    @app_commands.command(name="fast_travel",
                          description="Travel to a distant hex (multi-turn transit).")
    @app_commands.describe(destination="Target hex coord, e.g. 5,-3")
    async def fast_travel(self, interaction: discord.Interaction, destination: str):
        await ensure_guild(interaction.guild_id)
        dest = destination.strip()
        if not is_valid(dest):
            await interaction.response.send_message(
                f"Invalid hex `{dest}`. Use global coord format like `5,-3`.", ephemeral=True)
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT * FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["in_transit"]:
                await interaction.response.send_message("Already in transit.", ephemeral=True)
                return

            dist   = hex_distance(sq["hex_address"], dest)
            turns  = transit_turns(sq["brigade"])
            # Aerial and SpecOps insert directly — always 1 turn regardless of distance
            if can_direct_insert(sq["brigade"]):
                turns = 1
            else:
                # Scale: 1 base transit + 1 extra turn per 3 hexes of distance
                turns = transit_turns(sq["brigade"]) + max(0, (dist - 3) // 3)

            await conn.execute(
                "UPDATE squadrons SET in_transit=TRUE, transit_destination=$1, "
                "transit_turns_left=$2 WHERE id=$3",
                dest, turns, sq["id"])

        brig = get_brigade(sq["brigade"])
        await interaction.response.send_message(
            f"{brig['emoji']} **{sq['name']}** en route to `{dest}`. "
            f"Arrival in **{turns} turn(s)**.", ephemeral=True)

    # ── Scavenge ──────────────────────────────────────────────────────────────

    @app_commands.command(name="scavenge", description="Scavenge for supplies.")
    async def scavenge(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id  = await get_active_planet_id(conn, interaction.guild_id)
            turn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id) or 0
            sq = await conn.fetchrow(
                "SELECT * FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            brig = get_brigade(sq["brigade"])
            if not brig.get("can_scavenge", True):
                await interaction.response.send_message(
                    f"{brig['name']} cannot scavenge — too heavy to forage.", ephemeral=True)
                return
            # Ranger can scavenge twice — track with half-turn floats
            max_scavenges = 2 if can_scavenge_twice(sq["brigade"]) else 1
            times_this_turn = await conn.fetchval(
                "SELECT COUNT(*) FROM combat_log "
                "WHERE guild_id=$1 AND planet_id=$2 AND turn_number=$3 AND attacker=$4",
                interaction.guild_id, planet_id, turn_count, f"scavenge:{sq['id']}")
            # Use last_scavenged_turn as primary guard; for Rangers use a counter
            if sq["last_scavenged_turn"] >= turn_count:
                if not can_scavenge_twice(sq["brigade"]):
                    await interaction.response.send_message(
                        "Already scavenged this turn.", ephemeral=True)
                    return
                # Rangers: check second scavenge
                second_key = f"scavenge2:{sq['id']}:{turn_count}"
                already2   = await conn.fetchval(
                    "SELECT COUNT(*) FROM enemy_gm_moves WHERE guild_id=$1 AND target_address=$2",
                    interaction.guild_id, second_key)
                if already2:
                    await interaction.response.send_message(
                        "Rangers can scavenge twice per turn — already used both.", ephemeral=True)
                    return
                # Record second use
                await conn.execute(
                    "INSERT INTO enemy_gm_moves (guild_id, planet_id, enemy_unit_id, target_address) "
                    "VALUES ($1,$2,-1,$3) ON CONFLICT DO NOTHING",
                    interaction.guild_id, planet_id, second_key)

            gain       = random.randint(2, 6) + (sq["recon"] // 5) + scavenge_bonus(sq["brigade"])
            new_supply = min(20, sq["supply"] + gain)
            await conn.execute(
                "UPDATE squadrons SET supply=$1, last_scavenged_turn=$2 WHERE id=$3",
                new_supply, turn_count, sq["id"])

        brig_def = get_brigade(sq["brigade"])
        await interaction.response.send_message(
            f"Scavenged **+{gain}** supply. "
            f"Supply: `{new_supply}/20`.", ephemeral=True)

    # ── Brigade-specific: Fortify (Engineering) ───────────────────────────────

    @app_commands.command(name="fortify",
                          description="[Engineering] Fortify your current hex permanently.")
    async def fortify(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["brigade"] != "engineering":
                await interaction.response.send_message(
                    "Only Engineering Brigade can fortify hexes.", ephemeral=True)
                return
            await conn.execute("""
                INSERT INTO hex_terrain (guild_id, planet_id, address, terrain)
                VALUES ($1,$2,$3,'fort')
                ON CONFLICT (guild_id, planet_id, address) DO UPDATE SET terrain='fort'
            """, interaction.guild_id, planet_id, sq["hex_address"])

        await interaction.response.send_message(
            f"Hex `{sq['hex_address']}` has been **fortified**. "
            f"Fort terrain is now permanent on this hex.", ephemeral=True)

    # ── Brigade-specific: Repair (Engineering) ────────────────────────────────

    @app_commands.command(name="repair",
                          description="[Engineering] Restore supply to adjacent friendly units.")
    async def repair(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["brigade"] != "engineering":
                await interaction.response.send_message(
                    "Only Engineering Brigade can repair adjacent units.", ephemeral=True)
                return
            nbrs = hex_neighbors(sq["hex_address"])
            repaired = await conn.fetch(
                "SELECT id, name, owner_name, supply FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
                "AND hex_address=ANY($3::text[])",
                interaction.guild_id, planet_id, nbrs)
            count = 0
            for unit in repaired:
                new_sup = min(20, unit["supply"] + 4)
                await conn.execute(
                    "UPDATE squadrons SET supply=$1 WHERE id=$2", new_sup, unit["id"])
                count += 1

        if count == 0:
            await interaction.response.send_message(
                "No friendly units on adjacent hexes to repair.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Repaired **{count}** adjacent unit(s) (+4 supply each).", ephemeral=True)

    # ── Brigade-specific: Recon Sweep (Aerial, Ranger, SpecOps) ──────────────

    @app_commands.command(name="recon_sweep",
                          description="[Aerial/Ranger/SpecOps] Reveal nearby enemy units.")
    async def recon_sweep(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, recon FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["brigade"] not in ("aerial", "ranger", "special_ops"):
                await interaction.response.send_message(
                    "Only Aerial, Ranger, or Special Ops can perform recon sweeps.",
                    ephemeral=True)
                return
            radius = 3 if sq["brigade"] == "ranger" else 2
            from utils.hexmap import hexes_within
            nearby_keys = set(hexes_within(sq["hex_address"], radius))
            enemies = await conn.fetch(
                "SELECT hex_address, unit_type, attack, defense FROM enemy_units "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                interaction.guild_id, planet_id)
            found = [e for e in enemies if e["hex_address"] in nearby_keys]

        if not found:
            await interaction.response.send_message(
                f"Recon sweep complete — no enemy units within {radius} hexes.", ephemeral=True)
        else:
            lines = [f"`{e['hex_address']}` — {e['unit_type']} "
                     f"(ATK:{e['attack']} DEF:{e['defense']})" for e in found]
            await interaction.response.send_message(
                f"**Recon sweep — {len(found)} contact(s) within {radius} hexes:**\n"
                + "\n".join(lines), ephemeral=True)

    # ── Brigade-specific: Dig In (Infantry) ───────────────────────────────────

    @app_commands.command(name="dig_in",
                          description="[Infantry] Dig in for +4 defense bonus until next move.")
    async def dig_in(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["brigade"] != "infantry":
                await interaction.response.send_message(
                    "Only Infantry Brigade can dig in.", ephemeral=True)
                return
            await conn.execute(
                "UPDATE squadrons SET is_dug_in=TRUE WHERE id=$1", sq["id"])
        await interaction.response.send_message(
            f"**{sq['name']}** is dug in. +4 defense until next move.", ephemeral=True)

    # ── Brigade-specific: Artillery Hold ──────────────────────────────────────

    @app_commands.command(name="artillery_hold",
                          description="[Artillery] Hold position and arm for fire next combat.")
    async def artillery_hold(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["brigade"] != "artillery":
                await interaction.response.send_message(
                    "Only Artillery Brigade can use this command.", ephemeral=True)
                return
            await conn.execute(
                "UPDATE squadrons SET artillery_armed=TRUE WHERE id=$1", sq["id"])
        await interaction.response.send_message(
            f"**{sq['name']}** is armed and holding position. "
            f"Will fire with splash damage next turn.", ephemeral=True)

    # ── List units ────────────────────────────────────────────────────────────

    @app_commands.command(name="list_units",
                          description="List all active units on this planet.")
    async def list_units(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            rows      = await conn.fetch(
                "SELECT owner_name, name, brigade, hex_address, in_transit, supply "
                "FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE ORDER BY owner_name",
                interaction.guild_id, planet_id)
        if not rows:
            await interaction.response.send_message("No active units.", ephemeral=True)
            return
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

    # ── Disband ───────────────────────────────────────────────────────────────

    @app_commands.command(name="disband", description="Permanently disband your unit.")
    async def disband(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit to disband.", ephemeral=True)
            return
        view = _ConfirmDisbandView(interaction.user.id, sq["id"], sq["name"])
        await interaction.response.send_message(
            f"Permanently disband **{sq['name']}**?", view=view, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# BRIGADE PICKER
# ══════════════════════════════════════════════════════════════════════════════

def _brigade_picker_embed(unit_name: str) -> discord.Embed:
    lines = []
    for key, b in BRIGADES.items():
        stats = b["stats"]
        lines.append(
            f"**{b['emoji']} {b['name']}**\n"
            f"  {b['description']}\n"
            f"  ATK:{stats['attack']} DEF:{stats['defense']} SPD:{stats['speed']} "
            f"MRL:{stats['morale']} SUP:{stats['supply']} RCN:{stats['recon']}\n"
            f"  Transit: {b['transit_turns']} turn(s)  |  "
            + "  |  ".join(b['specials'][:2])
        )
    embed = discord.Embed(
        title=f"Choose Your Brigade — {unit_name}",
        description="\n\n".join(lines),
        color=0xAA2222,
    )
    embed.set_footer(text="Select your brigade from the dropdown below.")
    return embed


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
        brigade = self.values[0]
        await interaction.response.send_modal(
            DeployModal(self.guild_id, self.unit_name, brigade))


# ── Deploy modal ───────────────────────────────────────────────────────────────

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
                f"Invalid hex `{dest}`. Use format `gq,gr` e.g. `3,-2`.", ephemeral=True)
            return

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
                    "You already have an active unit.", ephemeral=True)
                return

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

            # Update enlist embed counter
            try:
                from views.menu import refresh_enlist_counter
                await refresh_enlist_counter(interaction.client, self.guild_id, conn)
            except Exception:
                pass

        brig = get_brigade(self.brigade)
        embed = discord.Embed(
            title=f"{brig['emoji']} Enlisted — {self.unit_name}",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Brigade:** {brig['name']}\n"
                f"**Deployed at:** `{dest}`\n\n"
                f"**Stats**\n"
                f"  ATK  {_bar(stats['attack'])}  {stats['attack']}\n"
                f"  DEF  {_bar(stats['defense'])}  {stats['defense']}\n"
                f"  SPD  {_bar(stats['speed'])}  {stats['speed']}\n"
                f"  MRL  {_bar(stats['morale'])}  {stats['morale']}\n"
                f"  SUP  {_bar(stats['supply'])}  {stats['supply']}\n"
                f"  RCN  {_bar(stats['recon'])}  {stats['recon']}\n\n"
                + "\n".join(f"  {s}" for s in brig["specials"])
            ),
        )
        await interaction.response.send_message(embed=embed)


# ══════════════════════════════════════════════════════════════════════════════
# DIRECTIONAL MOVE PAD
# ══════════════════════════════════════════════════════════════════════════════

def _move_embed(hex_addr: str, brigade: str, unit_name: str) -> discord.Embed:
    brig  = get_brigade(brigade)
    steps = move_steps(brigade)
    nbrs  = hex_neighbors(hex_addr)
    gq, gr = parse_hex(hex_addr)

    lines = [f"**{brig['emoji']} {unit_name}** at `{hex_addr}`"]
    lines.append(f"Move {steps} hex(es) per step. Use buttons to navigate.")
    lines.append(f"\nAdjacent hexes: {', '.join(f'`{n}`' for n in nbrs) or 'none'}")
    return discord.Embed(
        title="Move Unit",
        description="\n".join(lines),
        color=0x445588,
    )


class MoveDirectionView(discord.ui.View):
    """
    Directional pad for hex movement.
    Flat-top hex directions: E, SE, SW, W, NW, NE
    Layout on buttons:
      Row 0: NW  NE
      Row 1: W   [pos]  E
      Row 2: SW  SE
    Plus a Fast Travel button.
    """

    def __init__(self, guild_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id

    async def _do_move(self, interaction: discord.Interaction, dir_index: int):
        dq, dr = DIRECTIONS[dir_index]
        pool   = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, in_transit, is_dug_in, artillery_armed "
                "FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True)
                return
            if sq["in_transit"]:
                await interaction.response.send_message("Unit is in transit.", ephemeral=True)
                return

            steps      = move_steps(sq["brigade"])
            cur        = sq["hex_address"]
            new_addr   = cur

            for _ in range(steps):
                gq, gr = parse_hex(new_addr)
                candidate = hex_key(gq + dq, gr + dr)
                if (gq + dq, gr + dr) in GRID_SET:
                    new_addr = candidate
                else:
                    break   # hit grid edge, stop

            if new_addr == cur:
                await interaction.response.send_message(
                    "Cannot move that direction — grid edge.", ephemeral=True)
                return

            # Moving disarms artillery and removes dig-in
            await conn.execute(
                "UPDATE squadrons SET hex_address=$1, is_dug_in=FALSE, "
                "artillery_armed=FALSE WHERE id=$2",
                new_addr, sq["id"])

        embed = _move_embed(new_addr, sq["brigade"], sq["name"])
        await interaction.response.edit_message(embed=embed, view=MoveDirectionView(self.guild_id))

    # Row 0: NW (index 5), NE (index 4) — but labeled for visual layout
    # Flat-top: E=0, SE=1, SW=2, W=3, NW=4, NE=5
    @discord.ui.button(label="NW", style=discord.ButtonStyle.secondary, row=0)
    async def nw(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 4)

    @discord.ui.button(label="NE", style=discord.ButtonStyle.secondary, row=0)
    async def ne(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 5)

    @discord.ui.button(label="W",  style=discord.ButtonStyle.primary,   row=1)
    async def west(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 3)

    @discord.ui.button(label="--", style=discord.ButtonStyle.secondary, row=1, disabled=True)
    async def center(self, i: discord.Interaction, b: discord.ui.Button):
        pass

    @discord.ui.button(label="E",  style=discord.ButtonStyle.primary,   row=1)
    async def east(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 0)

    @discord.ui.button(label="SW", style=discord.ButtonStyle.secondary, row=2)
    async def sw(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 2)

    @discord.ui.button(label="SE", style=discord.ButtonStyle.secondary, row=2)
    async def se(self, i: discord.Interaction, b: discord.ui.Button):
        await self._do_move(i, 1)

    @discord.ui.button(label="Fast Travel", style=discord.ButtonStyle.danger, row=3)
    async def fast_travel(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.send_modal(FastTravelModal(self.guild_id))

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success, row=3)
    async def done(self, i: discord.Interaction, b: discord.ui.Button):
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
            await interaction.response.send_message(
                f"Invalid hex `{dest}`.", ephemeral=True)
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq        = await conn.fetchrow(
                "SELECT id, name, brigade, hex_address, in_transit FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, interaction.user.id)
            if not sq or sq["in_transit"]:
                await interaction.response.send_message(
                    "No available unit.", ephemeral=True)
                return
            dist  = hex_distance(sq["hex_address"], dest)
            turns = (1 if can_direct_insert(sq["brigade"])
                     else transit_turns(sq["brigade"]) + max(0, (dist-3)//3))
            await conn.execute(
                "UPDATE squadrons SET in_transit=TRUE, transit_destination=$1, "
                "transit_turns_left=$2, is_dug_in=FALSE, artillery_armed=FALSE WHERE id=$3",
                dest, turns, sq["id"])
        brig = get_brigade(sq["brigade"])
        await interaction.response.send_message(
            f"{brig['emoji']} **{sq['name']}** en route to `{dest}` — "
            f"**{turns} turn(s)**.", ephemeral=True)


# ── Disband confirm ────────────────────────────────────────────────────────────

class _ConfirmDisbandView(discord.ui.View):
    def __init__(self, user_id, sq_id, sq_name):
        super().__init__(timeout=30)
        self.user_id = user_id; self.sq_id = sq_id; self.sq_name = sq_name

    async def interaction_check(self, i): return i.user.id == self.user_id

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


async def setup(bot):
    await bot.add_cog(SquadronCog(bot))
