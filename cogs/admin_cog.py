"""
Admin cog v3 — flat hex system, enlist embed, planet management.
"""

import random
import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_pool, ensure_guild, get_theme, get_active_planet_id
from utils.hexmap import ensure_hexes, is_valid, GRID_COORDS, hex_key
from utils.map_render import TERRAIN_TYPES
from utils.brigades import BRIGADES


ENEMY_TYPES = [
    "AI Legion", "Pirate Fleet", "Civil War Militia",
    "Rogue Syndicate", "Xeno Collective",
    "Mercenary Company", "Separatist Army", "Corporate Security",
    "Cultist Swarm", "Unknown",
]


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Permissions ───────────────────────────────────────────────────────────

    async def _is_admin(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.bot.bot_owner_id:
            return True
        if interaction.user.guild_permissions.administrator:
            return True
        if interaction.guild.owner_id == interaction.user.id:
            return True
        pool = await get_pool()
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT admin_role_id FROM guild_config WHERE guild_id=$1",
                interaction.guild_id)
        if cfg and cfg["admin_role_id"]:
            role = interaction.guild.get_role(cfg["admin_role_id"])
            if role and role in interaction.user.roles:
                return True
        return False

    def _is_owner_only(self, interaction: discord.Interaction) -> bool:
        return (interaction.user.id == self.bot.bot_owner_id
                or interaction.guild.owner_id == interaction.user.id
                or interaction.user.guild_permissions.administrator)

    def _is_gm(self, interaction: discord.Interaction, gm_role_id) -> bool:
        if not gm_role_id:
            return False
        role = interaction.guild.get_role(gm_role_id)
        return role is not None and role in interaction.user.roles

    # ══════════════════════════════════════════════════════════════════════════
    # GAME CONTROL
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="game_start",
                          description="[Admin] Start the war on the active planet.")
    async def game_start(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                interaction.guild_id, planet_id)
            await ensure_hexes(interaction.guild_id, conn, planet_id)
            await conn.execute(
                "UPDATE guild_config SET game_started=TRUE WHERE guild_id=$1",
                interaction.guild_id)
            theme = await get_theme(conn, interaction.guild_id)

        embed = discord.Embed(
            title=f"{theme.get('bot_name','WARBOT')} — War Begins",
            color=theme.get("color", 0xAA2222),
            description=(
                f"**Planet:** {planet['name'] if planet else 'Unknown'}\n"
                f"**Contractor:** {planet['contractor'] if planet else '---'}\n"
                f"**Enemy:** {planet['enemy_type'] if planet else '---'}\n\n"
                f"Operatives may now enlist and deploy anywhere on the map.\n"
                f"Use `/set_menu_channel` and `/set_enlist_channel` to set up boards."
            ),
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="game_stop", description="[Admin] Pause the war.")
    async def game_stop(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET game_started=FALSE WHERE guild_id=$1",
                interaction.guild_id)
        await interaction.response.send_message("War paused.", ephemeral=True)

    @app_commands.command(name="game_reset",
                          description="[Admin] Wipe all war data on the active planet.")
    async def game_reset(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        view = _ConfirmView(interaction.user.id)
        await interaction.response.send_message(
            "This wipes ALL war data on the active planet. Confirm?",
            view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            for tbl in ("squadrons", "enemy_units", "combat_log",
                        "turn_history", "enemy_gm_moves"):
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE guild_id=$1 AND planet_id=$2",
                    interaction.guild_id, planet_id)
            await conn.execute(
                "UPDATE hexes SET controller='neutral', status='neutral' "
                "WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id)
            await conn.execute(
                "UPDATE guild_config SET game_started=FALSE, last_turn_at=NOW() "
                "WHERE guild_id=$1", interaction.guild_id)
        await interaction.edit_original_response(
            content="War data cleared.", view=None)

    @app_commands.command(name="game_status", description="View current war status.")
    async def game_status(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            cfg       = await conn.fetchrow(
                "SELECT * FROM guild_config WHERE guild_id=$1", interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                interaction.guild_id, planet_id)
            p_count   = await conn.fetchval(
                "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                interaction.guild_id, planet_id) or 0
            e_count   = await conn.fetchval(
                "SELECT COUNT(*) FROM enemy_units "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                interaction.guild_id, planet_id) or 0
            turns     = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history "
                "WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id) or 0

        embed = discord.Embed(
            title=f"{theme.get('bot_name','WARBOT')} — War Status",
            color=theme.get("color", 0xAA2222))
        embed.add_field(name="State",   value="Active" if cfg["game_started"] else "Paused", inline=True)
        embed.add_field(name="Turn",    value=str(turns), inline=True)
        embed.add_field(name="Interval",value=f"{cfg['turn_interval_hours']}h", inline=True)
        embed.add_field(name="Planet",  value=planet["name"]       if planet else "—", inline=True)
        embed.add_field(name="Contractor",value=planet["contractor"] if planet else "—", inline=True)
        embed.add_field(name="Enemy",   value=planet["enemy_type"] if planet else "—", inline=True)
        embed.add_field(name=theme.get("player_faction","PMC"),  value=f"{p_count} units", inline=True)
        embed.add_field(name=theme.get("enemy_faction","Enemy"), value=f"{e_count} units", inline=True)
        embed.add_field(name="Admin Role",
                        value=f"<@&{cfg['admin_role_id']}>" if cfg["admin_role_id"] else "Not set",
                        inline=True)
        embed.set_footer(text=f"Last advance: {cfg['last_turn_at'].strftime('%Y-%m-%d %H:%M UTC')}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="set_turn_interval",
                          description="[Admin] Hours between AI advance turns (1-168).")
    async def set_turn_interval(self, interaction: discord.Interaction, hours: int):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        if not (1 <= hours <= 168):
            await interaction.response.send_message("Must be 1-168.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET turn_interval_hours=$1 WHERE guild_id=$2",
                hours, interaction.guild_id)
        await interaction.response.send_message(f"Turn interval: **{hours}h**.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    # PLANET MANAGEMENT
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="planet_list", description="List all planets.")
    async def planet_list(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planets   = await conn.fetch(
                "SELECT * FROM planets WHERE guild_id=$1 ORDER BY sort_order, id",
                interaction.guild_id)
            active_id = await get_active_planet_id(conn, interaction.guild_id)
        if not planets:
            await interaction.response.send_message("No planets configured.", ephemeral=True)
            return
        lines = [
            f"{'>' if p['id']==active_id else ' '} **{p['name']}** (ID {p['id']})\n"
            f"   Contractor: {p['contractor']}  |  Enemy: {p['enemy_type']}"
            for p in planets
        ]
        embed = discord.Embed(
            title=f"{theme.get('bot_name','WARBOT')} — Planetary Theatres",
            color=theme.get("color", 0xAA2222),
            description="\n\n".join(lines))
        embed.set_footer(text="> = Active Theatre")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="planet_set_active",
                          description="[Admin] Switch the active planet.")
    @app_commands.describe(planet_name="Name of the planet to activate")
    async def planet_set_active(self, interaction: discord.Interaction, planet_name: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet = await conn.fetchrow(
                "SELECT id, name FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                interaction.guild_id, planet_name)
            if not planet:
                await interaction.response.send_message(
                    f"Planet `{planet_name}` not found.", ephemeral=True); return
            await ensure_hexes(interaction.guild_id, conn, planet["id"])
            await conn.execute(
                "UPDATE guild_config SET active_planet_id=$1 WHERE guild_id=$2",
                planet["id"], interaction.guild_id)
        await interaction.response.send_message(
            f"Active theatre: **{planet['name']}**.", ephemeral=True)
        try:
            from cogs.map_cog import auto_update_map, auto_update_overview
            await auto_update_map(self.bot, interaction.guild_id)
            await auto_update_overview(self.bot, interaction.guild_id)
        except Exception:
            pass

    @app_commands.command(name="planet_add",
                          description="[Admin] Add a new planet.")
    @app_commands.describe(planet_name="Planet name", contractor="Contractor", enemy_type="Enemy type")
    async def planet_add(self, interaction: discord.Interaction,
                         planet_name: str, contractor: str, enemy_type: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        if len(planet_name) > 40:
            await interaction.response.send_message("Name too long (max 40).", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT COUNT(*) FROM planets WHERE guild_id=$1", interaction.guild_id)
            try:
                row = await conn.fetchrow("""
                    INSERT INTO planets (guild_id, name, contractor, enemy_type, sort_order)
                    VALUES ($1,$2,$3,$4,$5) RETURNING id
                """, interaction.guild_id, planet_name, contractor, enemy_type, int(existing)+1)
            except Exception:
                await interaction.response.send_message(
                    f"Planet `{planet_name}` already exists.", ephemeral=True); return
            await ensure_hexes(interaction.guild_id, conn, row["id"])
        await interaction.response.send_message(
            f"Planet **{planet_name}** added (ID {row['id']}).", ephemeral=True)

    @app_commands.command(name="planet_remove",
                          description="[Admin] Remove a planet (not the active one).")
    async def planet_remove(self, interaction: discord.Interaction, planet_name: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            active_id = await get_active_planet_id(conn, interaction.guild_id)
            planet    = await conn.fetchrow(
                "SELECT id FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                interaction.guild_id, planet_name)
            if not planet:
                await interaction.response.send_message(
                    f"Planet `{planet_name}` not found.", ephemeral=True); return
            if planet["id"] == active_id:
                await interaction.response.send_message(
                    "Cannot remove the active planet.", ephemeral=True); return
            pid = planet["id"]
            for tbl in ("hexes","hex_terrain","squadrons","enemy_units",
                        "combat_log","turn_history","enemy_gm_moves"):
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE guild_id=$1 AND planet_id=$2",
                    interaction.guild_id, pid)
            await conn.execute(
                "DELETE FROM planets WHERE guild_id=$1 AND id=$2",
                interaction.guild_id, pid)
        await interaction.response.send_message(
            f"Planet **{planet_name}** removed.", ephemeral=True)

    @app_commands.command(name="planet_edit",
                          description="[Admin] Edit a planet's details.")
    @app_commands.choices(field=[
        app_commands.Choice(name="Contractor",  value="contractor"),
        app_commands.Choice(name="Enemy Type",  value="enemy_type"),
        app_commands.Choice(name="Description", value="description"),
        app_commands.Choice(name="Planet Name", value="name"),
    ])
    async def planet_edit(self, interaction: discord.Interaction,
                          planet_name: str, field: app_commands.Choice[str], value: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet = await conn.fetchrow(
                "SELECT id FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                interaction.guild_id, planet_name)
            if not planet:
                await interaction.response.send_message(
                    f"Planet `{planet_name}` not found.", ephemeral=True); return
            await conn.execute(
                f"UPDATE planets SET {field.value}=$1 WHERE guild_id=$2 AND id=$3",
                value, interaction.guild_id, planet["id"])
        await interaction.response.send_message(
            f"**{planet_name}** — {field.name} updated.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    # THEME
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="theme_view", description="View current theme settings.")
    async def theme_view(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
        embed = discord.Embed(
            title="Current Theme",
            color=theme.get("color", 0xAA2222),
            description="\n".join(f"**{k}:** {v}" for k, v in theme.items()))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="theme_set", description="[Admin] Change a theme value.")
    @app_commands.choices(field=[
        app_commands.Choice(name="Bot Name",         value="theme_bot_name"),
        app_commands.Choice(name="Player Faction",   value="theme_player_faction"),
        app_commands.Choice(name="Enemy Faction",    value="theme_enemy_faction"),
        app_commands.Choice(name="Player Unit Name", value="theme_player_unit"),
        app_commands.Choice(name="Enemy Unit Name",  value="theme_enemy_unit"),
        app_commands.Choice(name="Flavor Text",      value="theme_flavor_text"),
    ])
    async def theme_set(self, interaction: discord.Interaction,
                        field: app_commands.Choice[str], value: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        if len(value) > 80:
            await interaction.response.send_message("Max 80 chars.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_config SET {field.value}=$1 WHERE guild_id=$2",
                value, interaction.guild_id)
        await interaction.response.send_message(
            f"**{field.name}** set to `{value}`.", ephemeral=True)

    @app_commands.command(name="theme_color",
                          description="[Admin] Set embed accent color (hex, e.g. AA2222).")
    async def theme_color(self, interaction: discord.Interaction, hex_color: str):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        try:
            color_int = int(hex_color.lstrip("#"), 16)
            if not (0 <= color_int <= 0xFFFFFF):
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Invalid hex color.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET theme_color=$1 WHERE guild_id=$2",
                color_int, interaction.guild_id)
        await interaction.response.send_message(
            f"Accent color: `#{hex_color.upper()}`.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    # MAP TERRAIN
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="map_set_terrain",
                          description="[Admin] Set terrain for a hex on the active planet.")
    @app_commands.describe(hex_address="Global hex coord e.g. 3,-2", terrain="Terrain type")
    @app_commands.choices(terrain=[
        app_commands.Choice(name=t.title(), value=t) for t in TERRAIN_TYPES])
    async def map_set_terrain(self, interaction: discord.Interaction,
                              hex_address: str, terrain: app_commands.Choice[str]):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        addr = hex_address.strip()
        if not is_valid(addr):
            await interaction.response.send_message(
                f"Invalid hex `{addr}`. Use format `gq,gr`.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            await conn.execute("""
                INSERT INTO hex_terrain (guild_id, planet_id, address, terrain)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (guild_id, planet_id, address) DO UPDATE SET terrain=EXCLUDED.terrain
            """, interaction.guild_id, planet_id, addr, terrain.value)
        await interaction.response.send_message(
            f"`{addr}` set to **{terrain.name}**.", ephemeral=True)

    @app_commands.command(name="map_reset_terrain",
                          description="[Admin] Reset all terrain to flat on the active planet.")
    async def map_reset_terrain(self, interaction: discord.Interaction):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            await conn.execute(
                "DELETE FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
                interaction.guild_id, planet_id)
        await interaction.response.send_message("Terrain reset to flat.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    # ROLES & CHANNELS
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="set_admin_role",
                          description="[Owner] Set the admin role for this bot.")
    async def set_admin_role(self, interaction: discord.Interaction, role: discord.Role):
        if not self._is_owner_only(interaction):
            await interaction.response.send_message("Server owner only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET admin_role_id=$1 WHERE guild_id=$2",
                role.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Admin role: {role.mention}.", ephemeral=True)

    @app_commands.command(name="set_player_role",
                          description="[Admin] Role assigned on enlistment.")
    async def set_player_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET player_role_id=$1 WHERE guild_id=$2",
                role.id, interaction.guild_id)
        await interaction.response.send_message(f"Player role: {role.mention}.", ephemeral=True)

    @app_commands.command(name="set_gamemaster_role",
                          description="[Admin] Role that can manually move enemy units.")
    async def set_gamemaster_role(self, interaction: discord.Interaction, role: discord.Role):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET gamemaster_role_id=$1 WHERE guild_id=$2",
                role.id, interaction.guild_id)
        await interaction.response.send_message(f"GM role: {role.mention}.", ephemeral=True)

    @app_commands.command(name="set_report_channel",
                          description="[Admin] Channel for after-action turn reports.")
    async def set_report_channel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET report_channel_id=$1 WHERE guild_id=$2",
                channel.id, interaction.guild_id)
        await interaction.response.send_message(f"Reports: {channel.mention}.", ephemeral=True)

    @app_commands.command(name="set_map_channel",
                          description="[Admin] Channel for the live tactical map.")
    async def set_map_channel(self, interaction: discord.Interaction,
                               channel: discord.TextChannel):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET map_channel_id=$1 WHERE guild_id=$2",
                channel.id, interaction.guild_id)
        try:
            from cogs.map_cog import auto_update_map
            await auto_update_map(self.bot, interaction.guild_id)
            await interaction.followup.send(f"Live map: {channel.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Map post failed: {e}", ephemeral=True)

    @app_commands.command(name="set_overview_channel",
                          description="[Admin] Channel for the galaxy overview.")
    async def set_overview_channel(self, interaction: discord.Interaction,
                                    channel: discord.TextChannel):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET overview_channel_id=$1 WHERE guild_id=$2",
                channel.id, interaction.guild_id)
        try:
            from cogs.map_cog import auto_update_overview
            await auto_update_overview(self.bot, interaction.guild_id)
            await interaction.followup.send(f"Overview: {channel.mention}.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Overview post failed: {e}", ephemeral=True)

    @app_commands.command(name="set_menu_channel",
                          description="[Admin] Post the persistent command panel.")
    async def set_menu_channel(self, interaction: discord.Interaction,
                                channel: discord.TextChannel):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            from views.menu import build_menu_embed, MainMenuView
            embed = await build_menu_embed(interaction.guild_id, conn, theme)
        msg = await channel.send(embed=embed, view=MainMenuView(interaction.guild_id))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET reg_channel_id=$1, reg_message_id=$2 WHERE guild_id=$3",
                channel.id, msg.id, interaction.guild_id)
        await interaction.response.send_message(f"Command panel: {channel.mention}.", ephemeral=True)

    @app_commands.command(name="set_enlist_channel",
                          description="[Admin] Post the persistent enlistment board.")
    async def set_enlist_channel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel):
        if not await self._is_admin(interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True); return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, interaction.guild_id)
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                interaction.guild_id, planet_id)
            count     = await conn.fetchval(
                "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                interaction.guild_id, planet_id) or 0
            from views.menu import build_enlist_embed, EnlistView
            embed = build_enlist_embed(
                theme,
                planet["name"]       if planet else "Unknown",
                planet["contractor"] if planet else "---",
                planet["enemy_type"] if planet else "---",
                count,
            )
        msg = await channel.send(embed=embed, view=EnlistView(interaction.guild_id))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET enlist_channel_id=$1, enlist_message_id=$2 "
                "WHERE guild_id=$3",
                channel.id, msg.id, interaction.guild_id)
        await interaction.response.send_message(
            f"Enlistment board: {channel.mention}.", ephemeral=True)

    # ══════════════════════════════════════════════════════════════════════════
    # GM COMMANDS
    # ══════════════════════════════════════════════════════════════════════════

    @app_commands.command(name="spawn_enemy",
                          description="[GM] Spawn an enemy unit at a hex.")
    @app_commands.describe(hex_address="Global hex coord e.g. 6,-3", unit_type="Unit label")
    async def spawn_enemy(self, interaction: discord.Interaction,
                          hex_address: str, unit_type: str = "Scout"):
        pool = await get_pool()
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT gamemaster_role_id FROM guild_config WHERE guild_id=$1",
                interaction.guild_id)
        if not (await self._is_admin(interaction) or
                (cfg and self._is_gm(interaction, cfg["gamemaster_role_id"]))):
            await interaction.response.send_message("GMs only.", ephemeral=True); return
        addr = hex_address.strip()
        if not is_valid(addr):
            await interaction.response.send_message(
                f"Invalid hex `{addr}`.", ephemeral=True); return
        v = lambda: random.randint(-2, 2)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            await conn.execute(
                "INSERT INTO enemy_units "
                "(guild_id, planet_id, unit_type, hex_address, "
                " attack, defense, speed, morale, supply, recon) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                interaction.guild_id, planet_id, unit_type[:40], addr,
                10+v(), 10+v(), 10+v(), 10+v(), 10+v(), 10+v())
        await interaction.response.send_message(
            f"**{unit_type}** spawned at `{addr}`.", ephemeral=True)

    @app_commands.command(name="move_enemy",
                          description="[GM] Queue a move for an enemy unit next turn.")
    async def move_enemy(self, interaction: discord.Interaction,
                         unit_id: int, target_hex: str):
        pool = await get_pool()
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT gamemaster_role_id FROM guild_config WHERE guild_id=$1",
                interaction.guild_id)
        if not (await self._is_admin(interaction) or
                (cfg and self._is_gm(interaction, cfg["gamemaster_role_id"]))):
            await interaction.response.send_message("GMs only.", ephemeral=True); return
        addr = target_hex.strip()
        if not is_valid(addr):
            await interaction.response.send_message(
                f"Invalid hex `{addr}`.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, interaction.guild_id)
            await conn.execute("""
                INSERT INTO enemy_gm_moves (guild_id, planet_id, enemy_unit_id, target_address)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (guild_id, enemy_unit_id)
                DO UPDATE SET target_address=EXCLUDED.target_address
            """, interaction.guild_id, planet_id, unit_id, addr)
        await interaction.response.send_message(
            f"Unit {unit_id} queued to `{addr}` next turn.", ephemeral=True)


# ── Confirm view ───────────────────────────────────────────────────────────────

class _ConfirmView(discord.ui.View):
    def __init__(self, admin_id: int):
        super().__init__(timeout=30)
        self.admin_id  = admin_id
        self.confirmed = False

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.admin_id:
            await i.response.send_message("Not yours.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, i: discord.Interaction, b: discord.ui.Button):
        self.confirmed = True
        self.stop()
        await i.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, i: discord.Interaction, b: discord.ui.Button):
        self.stop()
        await i.response.edit_message(content="Cancelled.", view=None)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
