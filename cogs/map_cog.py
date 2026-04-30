"""
Map cog â€” /map, /map_overview commands and auto-update helpers.
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_pool, ensure_guild, get_theme, get_active_planet_id
from utils.revenant_ui import build_revenant_embed, format_section
from cogs.admin_cog import _is_admin, _is_gm


class MapCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="map", description="Render the current tactical map.")
    async def map_cmd(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        await interaction.response.defer(thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            try:
                from utils.map_render import render_map_for_guild
                buf = await render_map_for_guild(interaction.guild_id, conn)
            except Exception as e:
                await interaction.followup.send(f"âŒ Map render failed: {e}")
                return
        f     = discord.File(buf, filename="warmap.png")
        embed = build_revenant_embed(
            "Tactical Map",
            format_section("Tactical Map", ["**Status:** Current theatre map attached."]),
            "info",
            footer=theme.get("flavor_text", ""))
        embed.set_image(url="attachment://warmap.png")
        await interaction.followup.send(embed=embed, file=f)

    @app_commands.command(name="map_overview",
                          description="Show the planetary system overview (all planets).")
    async def map_overview(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        await interaction.response.defer(thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            try:
                from utils.map_render import render_overview_for_guild
                buf = await render_overview_for_guild(interaction.guild_id, conn)
            except Exception as e:
                await interaction.followup.send(f"âŒ Overview render failed: {e}")
                return
        f     = discord.File(buf, filename="overview.png")
        embed = discord.Embed(
            title="REVENANT | System Overview",
            color=0x34495E)
        embed.set_image(url="attachment://overview.png")
        await interaction.followup.send(embed=embed, file=f)

    @app_commands.command(name="map_update",
                          description="[Admin] Force-refresh the live map embed.")
    async def map_update(self, interaction: discord.Interaction):
        await ensure_guild(interaction.guild_id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await auto_update_map(self.bot, interaction.guild_id)
        ok2 = await auto_update_overview(self.bot, interaction.guild_id)
        msg = []
        if ok:  msg.append("âœ… Tactical map updated.")
        if ok2: msg.append("âœ… Planetary system overview updated.")
        if not msg: msg.append("âŒ No map channels configured.")
        await interaction.followup.send("\n".join(msg), ephemeral=True)

    @app_commands.command(
        name="gm_map",
        description="[GM] Full GM map â€” all unit positions revealed, no fog of war.",
    )
    async def gm_map_cmd(self, interaction: discord.Interaction):
        """Ephemeral GM map visible only to the requesting GM/Admin."""
        await ensure_guild(interaction.guild_id)
        # Only GMs and admins may use this command
        if not (await _is_admin(self.bot, interaction) or await _is_gm(interaction)):
            await interaction.response.send_message(
                "ðŸš« This command is restricted to GMs and Admins.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            try:
                from utils.map_render import render_gm_map_for_guild
                buf = await render_gm_map_for_guild(interaction.guild_id, conn)
            except Exception as e:
                await interaction.followup.send(f"âŒ GM map render failed: {e}", ephemeral=True)
                return
        f = discord.File(buf, filename="gm_map.png")
        embed = discord.Embed(
            title="REVENANT | GM Control Panel",
            description=(
                "**Fog of war lifted.** All player and enemy units are shown with their "
                "exact positions. This message is only visible to you."
            ),
            color=0x6C5CE7,
        )
        embed.set_image(url="attachment://gm_map.png")
        embed.set_footer(text="GM eyes only â€” this response is ephemeral.")
        await interaction.followup.send(embed=embed, file=f, ephemeral=True)


# â”€â”€ Auto-update helpers (called by turn engine + admin cog) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def auto_update_map(bot, guild_id: int, movement_arrows: list = None) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        cfg = await conn.fetchrow(
            "SELECT map_channel_id, map_message_id FROM guild_config WHERE guild_id=$1",
            guild_id)
        if not cfg or not cfg["map_channel_id"]:
            return False
        theme = await get_theme(conn, guild_id)
        try:
            from utils.map_render import render_map_for_guild
            buf = await render_map_for_guild(guild_id, conn, movement_arrows=movement_arrows)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Map render error guild {guild_id}: {e}")
            return False

    channel = bot.get_channel(cfg["map_channel_id"])
    if not channel:
        return False

    f     = discord.File(buf, filename="warmap.png")
    embed = discord.Embed(
        title="REVENANT | Tactical Map",
        color=0x34495E)
    embed.set_image(url="attachment://warmap.png")

    try:
        if cfg["map_message_id"]:
            try:
                msg = await channel.fetch_message(cfg["map_message_id"])
                await msg.edit(embed=embed, attachments=[f])
                return True
            except Exception:
                pass
        new_msg = await channel.send(embed=embed, file=f)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET map_message_id=$1 WHERE guild_id=$2",
                new_msg.id, guild_id)
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Map post error guild {guild_id}: {e}")
        return False


async def auto_update_overview(bot, guild_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        cfg = await conn.fetchrow(
            "SELECT overview_channel_id, overview_message_id FROM guild_config WHERE guild_id=$1",
            guild_id)
        if not cfg or not cfg["overview_channel_id"]:
            return False
        theme = await get_theme(conn, guild_id)
        try:
            from utils.map_render import render_overview_for_guild
            buf = await render_overview_for_guild(guild_id, conn)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Overview render error: {e}")
            return False

    channel = bot.get_channel(cfg["overview_channel_id"])
    if not channel:
        return False

    f     = discord.File(buf, filename="overview.png")
    embed = discord.Embed(
        title="REVENANT | System Overview",
        color=0x34495E)
    embed.set_image(url="attachment://overview.png")

    try:
        if cfg["overview_message_id"]:
            try:
                msg = await channel.fetch_message(cfg["overview_message_id"])
                await msg.edit(embed=embed, attachments=[f])
                return True
            except Exception:
                pass
        new_msg = await channel.send(embed=embed, file=f)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET overview_message_id=$1 WHERE guild_id=$2",
                new_msg.id, guild_id)
        return True
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Overview post error: {e}")
        return False


async def setup(bot):
    await bot.add_cog(MapCog(bot))

