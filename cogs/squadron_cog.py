"""
Squadron cog — player commands for managing their unit.
Units are always enlisted on the active planet.
"""

import random
import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_pool, ensure_guild, get_theme, get_active_planet_id
from utils.hexmap import SAFE_HUB, MID_OFFSETS, mid_key, outer_key, OUTER_COORDS


def _roll_stat() -> int:
    return random.randint(8, 14)


def _bar(val: int, length: int = 12) -> str:
    filled = max(0, min(length, int((val / 20) * length)))
    return "▓" * filled + "░" * (length - filled)


class SquadronCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="enlist",
                          description="Enlist and deploy your unit to the active planet.")
    @app_commands.describe(unit_name="Name for your unit (max 32 chars)")
    async def enlist(self, interaction: discord.Interaction, unit_name: str):
        await ensure_guild(interaction.guild_id)
        if len(unit_name) > 32:
            await interaction.response.send_message("❌ Unit name max 32 chars.", ephemeral=True)
            return

        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            cfg       = await conn.fetchrow(
                "SELECT game_started, player_role_id FROM guild_config WHERE guild_id=$1",
                interaction.guild_id)
            if not cfg or not cfg["game_started"]:
                await interaction.response.send_message(
                    "❌ The war hasn't started yet. An admin must run `/game_start`.",
                    ephemeral=True)
                return

            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            existing  = await conn.fetchrow(
                "SELECT id FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE",
                interaction.guild_id, planet_id, interaction.user.id)
            if existing:
                await interaction.response.send_message(
                    f"❌ You already have an active {theme.get('player_unit','unit')} "
                    f"on this planet.", ephemeral=True)
                return

            # Spawn at random FOB mid hex
            mq, mr    = random.choice(MID_OFFSETS)
            spawn_addr = mid_key(0, 0, mq, mr)
            stats      = {s: _roll_stat() for s in
                          ("attack","defense","speed","morale","supply","recon")}

            await conn.execute("""
                INSERT INTO squadrons
                  (guild_id, planet_id, owner_id, owner_name, name, hex_address,
                   attack, defense, speed, morale, supply, recon)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            """,
                interaction.guild_id, planet_id,
                interaction.user.id, interaction.user.display_name, unit_name, spawn_addr,
                stats["attack"], stats["defense"], stats["speed"],
                stats["morale"], stats["supply"], stats["recon"])

            planet = await conn.fetchrow(
                "SELECT name FROM planets WHERE guild_id=$1 AND id=$2",
                interaction.guild_id, planet_id)

            if cfg["player_role_id"]:
                role = interaction.guild.get_role(cfg["player_role_id"])
                if role:
                    try:
                        await interaction.user.add_roles(role)
                    except discord.Forbidden:
                        pass

        ul    = theme.get("player_unit", "Unit")
        embed = discord.Embed(
            title=f"🪖 {ul} Enlisted — {unit_name}",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Commander:** {interaction.user.mention}\n"
                f"**Planet:** {planet['name'] if planet else 'Unknown'}\n"
                f"**Deployed at:** `{spawn_addr}` (FOB)\n\n"
                f"**Stats**\n"
                f"  ATK  {_bar(stats['attack'])}  {stats['attack']}\n"
                f"  DEF  {_bar(stats['defense'])}  {stats['defense']}\n"
                f"  SPD  {_bar(stats['speed'])}  {stats['speed']}\n"
                f"  MRL  {_bar(stats['morale'])}  {stats['morale']}\n"
                f"  SUP  {_bar(stats['supply'])}  {stats['supply']}\n"
                f"  RCN  {_bar(stats['recon'])}  {stats['recon']}\n\n"
                f"*Use the command panel or `/move` to deploy forward.*"
            ),
        )
        embed.set_footer(text=theme.get("flavor_text",""))
        await interaction.response.send_message(embed=embed)

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
                    f"No active {theme.get('player_unit','unit')} on this planet. "
                    f"Use `/enlist`.", ephemeral=True)
                return
            turn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id) or 0
            nearby = await conn.fetch(
                "SELECT hex_address, unit_type FROM enemy_units "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
                "AND split_part(hex_address,':',1)=split_part($3,':',1)",
                interaction.guild_id, planet_id, sq["hex_address"])

        transit_str = (
            f"\n⚠️ **IN TRANSIT** → `{sq['transit_destination']}` "
            f"(step {sq['transit_step']}/2)"
            if sq["in_transit"] else ""
        )
        enemy_str = (
            "\n\n**Nearby threats:**\n" +
            "\n".join(f"  `{r['hex_address']}` — {r['unit_type']}" for r in nearby)
        ) if nearby else ""

        embed = discord.Embed(
            title=f"🪖 {sq['owner_name']} — {sq['name']}",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Position:** `{sq['hex_address']}`{transit_str}\n\n"
                f"**Stats**\n"
                f"  ATK  {_bar(sq['attack'])}  {sq['attack']}\n"
                f"  DEF  {_bar(sq['defense'])}  {sq['defense']}\n"
                f"  SPD  {_bar(sq['speed'])}  {sq['speed']}\n"
                f"  MRL  {_bar(sq['morale'])}  {sq['morale']}\n"
                f"  SUP  {_bar(sq['supply'])}  {sq['supply']}\n"
                f"  RCN  {_bar(sq['recon'])}  {sq['recon']}\n"
                f"{enemy_str}"
            ),
        )
        embed.set_footer(text=f"Turn {turn_count} · {theme.get('flavor_text','')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="move",
                          description="Move your unit to a hex address.")
    @app_commands.describe(hex_address="Target mid hex e.g. 1,-1:0,1 — cross-sector moves take 2 turns via FOB")
    async def move(self, interaction: discord.Interaction, hex_address: str):
        await ensure_guild(interaction.guild_id)
        dest = hex_address.strip()
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
                    f"No active {theme.get('player_unit','unit')}.", ephemeral=True)
                return
            if sq["in_transit"]:
                await interaction.response.send_message(
                    "Your unit is already in transit.", ephemeral=True)
                return

            # Validate mid-hex format "q,r:mq,mr"
            if ":" not in dest:
                await interaction.response.send_message(
                    "❌ Use mid-hex format: `q,r:mq,mr` e.g. `1,-1:0,1`", ephemeral=True)
                return
            try:
                outer_part, mid_part = dest.split(":")
                oq, or_ = [int(x) for x in outer_part.split(",")]
                mq, mr  = [int(x) for x in mid_part.split(",")]
                from utils.hexmap import OUTER_SET, MID_SET
                if (oq, or_) not in OUTER_SET or (mq, mr) not in MID_SET:
                    raise ValueError
            except Exception:
                await interaction.response.send_message(
                    "❌ Invalid hex address.", ephemeral=True)
                return

            current_outer = sq["hex_address"].split(":")[0]
            dest_outer    = outer_part

            if dest_outer == current_outer:
                await conn.execute(
                    "UPDATE squadrons SET hex_address=$1 WHERE id=$2",
                    dest, sq["id"])
                await interaction.response.send_message(
                    f"✅ **{sq['name']}** moved to `{dest}`.", ephemeral=True)
            else:
                await conn.execute(
                    "UPDATE squadrons SET in_transit=TRUE, transit_destination=$1, "
                    "transit_step=1 WHERE id=$2",
                    dest, sq["id"])
                await interaction.response.send_message(
                    f"🚀 **{sq['name']}** en route to `{dest}`. "
                    f"Arrives in 2 turns via FOB.", ephemeral=True)

    @app_commands.command(name="scavenge",
                          description="Scavenge for supplies at your current position.")
    async def scavenge(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            turn_count = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id) or 0
            sq = await conn.fetchrow(
                "SELECT * FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
            if not sq:
                await interaction.response.send_message("No active unit.", ephemeral=True); return
            if sq["last_scavenged_turn"] >= turn_count:
                await interaction.response.send_message(
                    "Already scavenged this turn.", ephemeral=True); return
            gain = random.randint(1, 5) + (sq["recon"] // 5)
            new_supply = min(20, sq["supply"] + gain)
            await conn.execute(
                "UPDATE squadrons SET supply=$1, last_scavenged_turn=$2 WHERE id=$3",
                new_supply, turn_count, sq["id"])
        await interaction.response.send_message(
            f"🔍 **{sq['name']}** scavenged **+{gain}** supply → `{new_supply}/20`.",
            ephemeral=True)

    @app_commands.command(name="disband", description="Permanently disband your unit.")
    async def disband(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            sq = await conn.fetchrow(
                "SELECT id, name FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                interaction.guild_id, planet_id, interaction.user.id)
        if not sq:
            await interaction.response.send_message("No active unit to disband.", ephemeral=True); return
        view = _DisbandConfirm(interaction.user.id, sq["id"], sq["name"])
        await interaction.response.send_message(
            f"⚠️ Permanently disband **{sq['name']}**?", view=view, ephemeral=True)

    @app_commands.command(name="list_units", description="[Admin] List all active units on this planet.")
    async def list_units(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            rows      = await conn.fetch(
                "SELECT owner_name, name, hex_address, in_transit, supply "
                "FROM squadrons WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
                "ORDER BY owner_name",
                interaction.guild_id, planet_id)
        if not rows:
            await interaction.response.send_message("No active units.", ephemeral=True); return
        lines = []
        for r in rows:
            t = " *(transit)*" if r["in_transit"] else ""
            lines.append(f"**{r['owner_name']}** — {r['name']} @ `{r['hex_address']}`{t} SUP:{r['supply']}")
        embed = discord.Embed(
            title=f"🪖 Active {theme.get('player_unit','Units')} ({len(rows)})",
            color=theme.get("color", 0xAA2222),
            description="\n".join(lines),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class _DisbandConfirm(discord.ui.View):
    def __init__(self, user_id, sq_id, sq_name):
        super().__init__(timeout=30)
        self.user_id = user_id
        self.sq_id   = sq_id
        self.sq_name = sq_name

    async def interaction_check(self, i: discord.Interaction) -> bool:
        return i.user.id == self.user_id

    @discord.ui.button(label="Yes, Disband", style=discord.ButtonStyle.danger)
    async def confirm(self, i: discord.Interaction, b: discord.ui.Button):
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("UPDATE squadrons SET is_active=FALSE WHERE id=$1", self.sq_id)
        self.stop()
        await i.response.edit_message(content=f"✅ **{self.sq_name}** disbanded.", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, i: discord.Interaction, b: discord.ui.Button):
        self.stop()
        await i.response.edit_message(content="Cancelled.", view=None)


async def setup(bot):
    await bot.add_cog(SquadronCog(bot))
