"""
Admin cog v4 — fully button-driven.

All admin actions are accessible via /admin_panel (one slash command).
All GM actions are accessible via /gm_panel (one slash command).

/admin_panel  → AdminPanelView   (game control, planets, theme, channels, roles, terrain)
/gm_panel     → GmPanelView      (spawn enemy, move enemy, list enemies)
"""

import random
import discord
from discord import app_commands
from discord.ext import commands

from utils.db import get_pool, ensure_guild, get_theme, get_active_planet_id
from utils.hexmap import ensure_hexes, is_valid, GRID_COORDS, hex_key
from utils.map_render import TERRAIN_TYPES, generate_water_bodies
from utils.brigades import BRIGADES
from utils.profiles import cosmetic_key, ensure_commander_profile, grant_default_banner


# ══════════════════════════════════════════════════════════════════════════════
# PERMISSION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _is_admin(bot, interaction: discord.Interaction) -> bool:
    if interaction.user.id == bot.bot_owner_id:
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


def _is_owner_only(interaction: discord.Interaction) -> bool:
    return (interaction.user.guild_permissions.administrator
            or interaction.guild.owner_id == interaction.user.id)


async def _is_gm(interaction: discord.Interaction) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        cfg = await conn.fetchrow(
            "SELECT gamemaster_role_id FROM guild_config WHERE guild_id=$1",
            interaction.guild_id)
    if not cfg or not cfg["gamemaster_role_id"]:
        return False
    role = interaction.guild.get_role(cfg["gamemaster_role_id"])
    return role is not None and role in interaction.user.roles


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN PANEL
# ══════════════════════════════════════════════════════════════════════════════

def _admin_panel_embed(theme: dict) -> discord.Embed:
    return discord.Embed(
        title=f"⚙ {theme.get('bot_name', 'WARBOT')} — Admin Panel",
        color=theme.get("color", 0xAA2222),
        description=(
            "**Game Control**\n"
            "Reset · Status · Turn Interval · **Force Turn**\n\n"
            "**Planets**\n"
            "List · Add · Remove · Set Active · Edit\n\n"
            "**Theme & Appearance**\n"
            "View Theme · Set Theme · Set Color\n\n"
            "**Channels & Roles**\n"
            "Map · Overview · Menu · Enlist · Report · Announcement · Admin Role · Player Role · GM Role\n\n"
            "**Terrain**\n"
            "Set Terrain · Reset Terrain"
        ),
    ).set_footer(text="All actions are ephemeral — only you can see them.")


class AdminPanelView(discord.ui.View):
    """
    Main admin panel. 5 rows of buttons covering all admin functionality.
    Row 0: Game Control
    Row 1: Planets
    Row 2: Theme
    Row 3: Channels
    Row 4: Roles & Terrain
    """
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot      = bot
        self.guild_id = guild_id

    # ── Row 0: Game Control ───────────────────────────────────────────────────

    @discord.ui.button(label="🔄 Reset War", style=discord.ButtonStyle.danger, row=0)
    async def game_reset(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        view = _ConfirmView(i.user.id)
        await i.response.send_message(
            "⚠ This wipes **all** war data on the active planet. Confirm?",
            view=view, ephemeral=True)
        await view.wait()
        if not view.confirmed:
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            for tbl in ("squadrons", "enemy_units", "combat_log", "turn_history", "enemy_gm_moves"):
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE guild_id=$1 AND planet_id=$2",
                    i.guild_id, planet_id)
            await conn.execute(
                "UPDATE hexes SET controller='neutral', status='neutral' "
                "WHERE guild_id=$1 AND planet_id=$2", i.guild_id, planet_id)
            await conn.execute(
                "UPDATE guild_config SET game_started=FALSE, last_turn_at=NOW() "
                "WHERE guild_id=$1", i.guild_id)
        await i.edit_original_response(content="War data cleared.", view=None)

    @discord.ui.button(label="📊 Status", style=discord.ButtonStyle.secondary, row=0)
    async def game_status(self, i: discord.Interaction, b: discord.ui.Button):
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, i.guild_id)
            cfg       = await conn.fetchrow(
                "SELECT * FROM guild_config WHERE guild_id=$1", i.guild_id)
            planet_id = await get_active_planet_id(conn, i.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                i.guild_id, planet_id)
            p_count   = await conn.fetchval(
                "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                i.guild_id, planet_id) or 0
            e_count   = await conn.fetchval(
                "SELECT COUNT(*) FROM enemy_units "
                "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
                i.guild_id, planet_id) or 0
            turns     = await conn.fetchval(
                "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
                i.guild_id, planet_id) or 0
        embed = discord.Embed(
            title=f"{theme.get('bot_name','WARBOT')} — War Status",
            color=theme.get("color", 0xAA2222))
        embed.add_field(name="State",    value="Active" if cfg["game_started"] else "Paused", inline=True)
        embed.add_field(name="Turn",     value=str(turns), inline=True)
        embed.add_field(name="Interval", value=f"{cfg['turn_interval_hours']}h", inline=True)
        embed.add_field(name="Planet",   value=planet["name"]       if planet else "—", inline=True)
        embed.add_field(name="Contractor", value=planet["contractor"] if planet else "—", inline=True)
        embed.add_field(name="Enemy",    value=planet["enemy_type"] if planet else "—", inline=True)
        embed.add_field(name=theme.get("player_faction","PMC"),  value=f"{p_count} units", inline=True)
        embed.add_field(name=theme.get("enemy_faction","Enemy"), value=f"{e_count} units", inline=True)
        embed.set_footer(text=f"Last advance: {cfg['last_turn_at'].strftime('%Y-%m-%d %H:%M UTC')}")
        await i.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⏱ Turn Interval", style=discord.ButtonStyle.secondary, row=0)
    async def set_turn_interval(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_TurnIntervalModal())

    # ── Row 1: Planets ────────────────────────────────────────────────────────

    @discord.ui.button(label="🪐 List Planets", style=discord.ButtonStyle.secondary, row=1)
    async def planet_list(self, i: discord.Interaction, b: discord.ui.Button):
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, i.guild_id)
            planets   = await conn.fetch(
                "SELECT * FROM planets WHERE guild_id=$1 ORDER BY sort_order, id", i.guild_id)
            active_id = await get_active_planet_id(conn, i.guild_id)
        if not planets:
            await i.response.send_message("No planets configured.", ephemeral=True); return
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
        await i.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="➕ Add Planet", style=discord.ButtonStyle.secondary, row=1)
    async def planet_add(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_PlanetAddModal())

    @discord.ui.button(label="🔀 Set Active Planet", style=discord.ButtonStyle.primary, row=1)
    async def planet_set_active(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_PlanetSetActiveModal(self.bot))

    @discord.ui.button(label="✏ Edit Planet", style=discord.ButtonStyle.secondary, row=1)
    async def planet_edit(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_message(
            "Choose a field to edit:", view=_PlanetEditFieldView(i.guild_id), ephemeral=True)

    @discord.ui.button(label="🗑 Remove Planet", style=discord.ButtonStyle.danger, row=1)
    async def planet_remove(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_PlanetRemoveModal())

    # ── Row 2: Theme ──────────────────────────────────────────────────────────

    @discord.ui.button(label="🎨 View Theme", style=discord.ButtonStyle.secondary, row=2)
    async def theme_view(self, i: discord.Interaction, b: discord.ui.Button):
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, i.guild_id)
        embed = discord.Embed(
            title="Current Theme",
            color=theme.get("color", 0xAA2222),
            description="\n".join(f"**{k}:** {v}" for k, v in theme.items()))
        await i.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="✏ Set Theme", style=discord.ButtonStyle.secondary, row=2)
    async def theme_set(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_message(
            "Choose a theme field:", view=_ThemeSetFieldView(i.guild_id), ephemeral=True)

    @discord.ui.button(label="🖌 Set Color", style=discord.ButtonStyle.secondary, row=2)
    async def theme_color(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_ThemeColorModal())

    @discord.ui.button(label="🗺 Set Terrain", style=discord.ButtonStyle.secondary, row=2)
    async def map_set_terrain(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_message(
            "Choose terrain type:", view=_TerrainTypeView(i.guild_id), ephemeral=True)

    @discord.ui.button(label="🎲 Random Terrain", style=discord.ButtonStyle.secondary, row=2)
    async def map_random_terrain(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.defer(ephemeral=True, thinking=True)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            from utils.hexmap import GRID_COORDS, hex_key
            import random
            # Weighted land distribution plus clustered water bodies.
            weights = {
                "plains":   45,
                "forest":   20,
                "hills":    15,
                "mountain":  8,
                "city":      5,
                "military":  4,
                "fort":      3,
            }
            terrain_pool = [t for t, w in weights.items() for _ in range(w)]
            water_hexes = generate_water_bodies(
                GRID_COORDS,
                water_ratio=0.08,
                body_count=3,
                seed=i.guild_id * 1009 + planet_id,
            )
            await conn.execute(
                "DELETE FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
                i.guild_id, planet_id)
            for gq, gr in GRID_COORDS:
                addr    = hex_key(gq, gr)
                terrain = "water" if (gq, gr) in water_hexes else random.choice(terrain_pool)
                if terrain != "plains":   # skip inserting plains — it's the default
                    await conn.execute("""
                        INSERT INTO hex_terrain (guild_id, planet_id, address, terrain)
                        VALUES ($1,$2,$3,$4)
                        ON CONFLICT (guild_id, planet_id, address) DO UPDATE SET terrain=EXCLUDED.terrain
                    """, i.guild_id, planet_id, addr, terrain)
        await i.followup.send("🎲 Terrain randomised!", ephemeral=True)

    @discord.ui.button(label="🔃 Reset Terrain", style=discord.ButtonStyle.danger, row=0)
    async def map_reset_terrain(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            await conn.execute(
                "DELETE FROM hex_terrain WHERE guild_id=$1 AND planet_id=$2",
                i.guild_id, planet_id)
        await i.response.send_message("Terrain reset to flat.", ephemeral=True)

    # ── Row 3: Channels ───────────────────────────────────────────────────────

    @discord.ui.button(label="📡 Map Channel", style=discord.ButtonStyle.secondary, row=3)
    async def set_map_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_ChannelModal("map_channel_id", "Map Channel ID"))

    @discord.ui.button(label="🌌 Overview Channel", style=discord.ButtonStyle.secondary, row=3)
    async def set_overview_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_ChannelModal("overview_channel_id", "Overview Channel ID"))

    @discord.ui.button(label="📋 Menu Channel", style=discord.ButtonStyle.secondary, row=3)
    async def set_menu_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_MenuChannelModal())

    @discord.ui.button(label="⚔ Enlist Channel", style=discord.ButtonStyle.secondary, row=3)
    async def set_enlist_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_EnlistChannelModal())

    @discord.ui.button(label="📢 Report Channel", style=discord.ButtonStyle.secondary, row=3)
    async def set_report_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_ChannelModal("report_channel_id", "Report Channel ID"))

    @discord.ui.button(label="📣 Announcement Channel", style=discord.ButtonStyle.secondary, row=4)
    async def set_announcement_channel(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_ChannelModal("announcement_channel_id", "Announcement Channel ID"))

    # ── Row 4: Roles ──────────────────────────────────────────────────────────

    @discord.ui.button(label="🛡 Admin Role", style=discord.ButtonStyle.secondary, row=4)
    async def set_admin_role(self, i: discord.Interaction, b: discord.ui.Button):
        if not _is_owner_only(i):
            await i.response.send_message("Server owner only.", ephemeral=True); return
        await i.response.send_modal(_RoleModal("admin_role_id", "Admin Role ID"))

    @discord.ui.button(label="🪖 Player Role", style=discord.ButtonStyle.secondary, row=4)
    async def set_player_role(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_RoleModal("player_role_id", "Player Role ID"))

    @discord.ui.button(label="🎮 GM Role", style=discord.ButtonStyle.secondary, row=4)
    async def set_gm_role(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_modal(_RoleModal("gamemaster_role_id", "GM Role ID"))

    @discord.ui.button(label="Cosmetics", style=discord.ButtonStyle.primary, row=4)
    async def cosmetics(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.send_message(
            "Manage banner and badge definitions.",
            view=_AdminCosmeticView(self.bot, i.guild_id),
            ephemeral=True)

    @discord.ui.button(label="⏩ Force Turn", style=discord.ButtonStyle.danger, row=0)
    async def force_turn(self, i: discord.Interaction, b: discord.ui.Button):
        if not await _is_admin(self.bot, i):
            await i.response.send_message("Admins only.", ephemeral=True); return
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            from utils.db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                cfg = await conn.fetchrow(
                    "SELECT game_started FROM guild_config WHERE guild_id=$1", i.guild_id)
                if not cfg or not cfg["game_started"]:
                    await i.followup.send(
                        "❌ The war hasn't started yet — start it first.", ephemeral=True)
                    return
                await self.bot.turn_engine._resolve(conn, i.guild_id)
            await i.followup.send("✅ Turn forced successfully.", ephemeral=True)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()[-1800:]
            msg = "\u274c Force turn failed: " + str(e) + "\n```\n" + tb + "\n```"
            await i.followup.send(msg, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# GM PANEL
# ══════════════════════════════════════════════════════════════════════════════

def _gm_panel_embed(theme: dict) -> discord.Embed:
    return discord.Embed(
        title=f"🎮 {theme.get('bot_name', 'WARBOT')} — GM Panel",
        color=theme.get("color", 0xAA2222),
        description=(
            "**Contract Control**\n"
            "Start Contract · Pause Contract · Conclude Contract\n\n"
            "**Enemy Management**\n"
            "Spawn, move (single or bulk), list, or remove enemy units.\n\n"
            "**🗺 GM Map** shows ALL unit positions (players + enemies) with labels.\n\n"
            "All actions are ephemeral — only you can see this panel."
        ),
    ).set_footer(text="Game Master controls")


class GmPanelView(discord.ui.View):
    """
    GM panel — contract control + enemy unit management.
    Row 0: Start Contract | Pause Contract | Conclude Contract
    Row 1: Spawn Enemy | Move Enemy | Bulk Move | List Enemies
    Row 2: Remove Enemy | GM Map
    """
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot      = bot
        self.guild_id = guild_id

    async def _check(self, i: discord.Interaction) -> bool:
        if await _is_admin(self.bot, i):
            return True
        if await _is_gm(i):
            return True
        await i.response.send_message("GMs only.", ephemeral=True)
        return False

    # ── Row 0: Contract Control ───────────────────────────────────────────────

    @discord.ui.button(label="▶ Start Contract", style=discord.ButtonStyle.success, row=0)
    async def start_contract(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_StartContractModal(self.bot))

    @discord.ui.button(label="⏸ Pause Contract", style=discord.ButtonStyle.secondary, row=0)
    async def pause_contract(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET game_started=FALSE WHERE guild_id=$1", i.guild_id)
        await i.response.send_message("⏸ Contract paused.", ephemeral=True)

    @discord.ui.button(label="📋 Conclude Contract", style=discord.ButtonStyle.danger, row=0)
    async def conclude_contract(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_ContractOutcomeModal(self.bot))

    # ── Row 1: Enemy Management ───────────────────────────────────────────────

    @discord.ui.button(label="👾 Spawn Enemy", style=discord.ButtonStyle.danger, row=1)
    async def spawn_enemy(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_SpawnEnemyModal())

    @discord.ui.button(label="👾 Bulk Spawn", style=discord.ButtonStyle.danger, row=1)
    async def bulk_spawn_enemy(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BulkSpawnEnemyModal())

    @discord.ui.button(label="➡ Move Enemy", style=discord.ButtonStyle.primary, row=2)
    async def move_enemy(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_MoveEnemyModal())

    @discord.ui.button(label="📦 Bulk Move", style=discord.ButtonStyle.primary, row=2)
    async def bulk_move_enemy(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BulkMoveEnemyModal())

    @discord.ui.button(label="📋 List Enemies", style=discord.ButtonStyle.secondary, row=2)
    async def list_enemies(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, i.guild_id)
            planet_id = await get_active_planet_id(conn, i.guild_id)
            rows      = await conn.fetch(
                "SELECT id, unit_type, hex_address, attack, defense, hp, is_active "
                "FROM enemy_units WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
                "ORDER BY id",
                i.guild_id, planet_id)
            # Also fetch queued moves
            queued = await conn.fetch(
                "SELECT enemy_unit_id, target_address FROM enemy_gm_moves "
                "WHERE guild_id=$1 AND planet_id=$2",
                i.guild_id, planet_id)
        queued_map = {r["enemy_unit_id"]: r["target_address"] for r in queued}
        if not rows:
            await i.response.send_message("No active enemy units.", ephemeral=True); return
        lines = []
        for r in rows:
            move_str = f" → `{queued_map[r['id']]}`" if r["id"] in queued_map else ""
            lines.append(
                f"**ID {r['id']}** `{r['hex_address']}`{move_str} — "
                f"{r['unit_type']} (ATK:{r['attack']} DEF:{r['defense']} HP:{r['hp'] or 100})"
            )
        description = "\n".join(lines)
        embed = discord.Embed(
            title=f"Enemy Units ({len(rows)}) — queued moves shown as →",
            color=theme.get("color", 0xAA2222),
            description=description[:4000])
        await i.response.send_message(embed=embed, ephemeral=True)

    # ── Row 2: Misc ───────────────────────────────────────────────────────────

    @discord.ui.button(label="☠ Remove Enemy", style=discord.ButtonStyle.danger, row=3)
    async def remove_enemy(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_RemoveEnemyModal())

    @discord.ui.button(label="🗺 GM Map", style=discord.ButtonStyle.success, row=3)
    async def gm_map(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                from utils.map_render import render_gm_map_for_guild
                buf = await render_gm_map_for_guild(i.guild_id, conn)
            file = discord.File(buf, filename="gm_map.png")
            embed = discord.Embed(
                title="🗺 GM Map — All Unit Positions",
                description=(
                    "🟦 **Blue labels** = player unit names (→dest if in transit)\n"
                    "🟥 **Red labels** = enemy units (#ID + type)"
                ),
                color=0x226622,
            )
            embed.set_image(url="attachment://gm_map.png")
            await i.followup.send(embed=embed, file=file, ephemeral=True)
        except Exception as e:
            await i.followup.send(f"Error rendering GM map: {e}", ephemeral=True)

    @discord.ui.button(label="Grant Banner", style=discord.ButtonStyle.primary, row=4)
    async def grant_banner(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_GrantCosmeticModal("banner", remove=False))

    @discord.ui.button(label="Remove Banner", style=discord.ButtonStyle.secondary, row=4)
    async def remove_banner(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_GrantCosmeticModal("banner", remove=True))

    @discord.ui.button(label="Grant Badge", style=discord.ButtonStyle.primary, row=4)
    async def grant_badge(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_GrantCosmeticModal("badge", remove=False))

    @discord.ui.button(label="Remove Badge", style=discord.ButtonStyle.secondary, row=4)
    async def remove_badge(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_GrantCosmeticModal("badge", remove=True))


class _AdminCosmeticView(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id

    async def _check(self, i: discord.Interaction) -> bool:
        if await _is_admin(self.bot, i):
            return True
        await i.response.send_message("Admins only.", ephemeral=True)
        return False

    @discord.ui.button(label="Add Banner", style=discord.ButtonStyle.primary, row=0)
    async def add_banner(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BannerAddModal())

    @discord.ui.button(label="Remove Banner", style=discord.ButtonStyle.danger, row=0)
    async def remove_banner(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BannerRemoveModal())

    @discord.ui.button(label="Add Badge", style=discord.ButtonStyle.primary, row=1)
    async def add_badge(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BadgeAddModal())

    @discord.ui.button(label="Remove Badge", style=discord.ButtonStyle.danger, row=1)
    async def remove_badge(self, i: discord.Interaction, b: discord.ui.Button):
        if not await self._check(i): return
        await i.response.send_modal(_BadgeRemoveModal())


class _PagedPanelView(discord.ui.View):
    def __init__(self, bot, guild_id: int, theme: dict, pages: list[dict], legacy):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id
        self.theme = theme
        self.pages = pages
        self.legacy = legacy
        self.page = 0
        self._rebuild()

    def _embed(self) -> discord.Embed:
        data = self.pages[self.page]
        embed = discord.Embed(
            title=f"{self.theme.get('bot_name', 'WARBOT')} - {data['title']}",
            color=self.theme.get("color", 0xAA2222),
            description=data["description"],
        )
        embed.set_footer(text=f"Page {self.page + 1}/{len(self.pages)}")
        return embed

    def _rebuild(self):
        self.clear_items()
        for idx, item in enumerate(self.pages[self.page]["items"]):
            button = discord.ui.Button(
                label=item["label"],
                style=item.get("style", discord.ButtonStyle.secondary),
                row=idx // 4)

            async def callback(i: discord.Interaction, method=item["method"]):
                legacy_button = getattr(self.legacy, method)
                await legacy_button.callback(i)

            button.callback = callback
            self.add_item(button)

        prev_button = discord.ui.Button(label="Previous", style=discord.ButtonStyle.secondary, row=4)
        next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary, row=4)
        prev_button.disabled = self.page == 0
        next_button.disabled = self.page >= len(self.pages) - 1

        async def prev_cb(i: discord.Interaction):
            self.page = max(0, self.page - 1)
            self._rebuild()
            await i.response.edit_message(embed=self._embed(), view=self)

        async def next_cb(i: discord.Interaction):
            self.page = min(len(self.pages) - 1, self.page + 1)
            self._rebuild()
            await i.response.edit_message(embed=self._embed(), view=self)

        prev_button.callback = prev_cb
        next_button.callback = next_cb
        self.add_item(prev_button)
        self.add_item(next_button)


class AdminPanelPagerView(_PagedPanelView):
    def __init__(self, bot, guild_id: int, theme: dict):
        pages = [
            {
                "title": "Admin Panel / Game",
                "description": "Core contract controls and current war status.",
                "items": [
                    {"label": "Status", "method": "game_status"},
                    {"label": "Turn Interval", "method": "set_turn_interval"},
                    {"label": "Force Turn", "method": "force_turn", "style": discord.ButtonStyle.danger},
                    {"label": "Reset War", "method": "game_reset", "style": discord.ButtonStyle.danger},
                ],
            },
            {
                "title": "Admin Panel / Planets",
                "description": "Create, edit, remove, and activate contract theatres.",
                "items": [
                    {"label": "List Planets", "method": "planet_list"},
                    {"label": "Add Planet", "method": "planet_add"},
                    {"label": "Set Active", "method": "planet_set_active", "style": discord.ButtonStyle.primary},
                    {"label": "Edit Planet", "method": "planet_edit"},
                    {"label": "Remove Planet", "method": "planet_remove", "style": discord.ButtonStyle.danger},
                ],
            },
            {
                "title": "Admin Panel / Appearance",
                "description": "Theme, color, terrain, and cosmetic definitions.",
                "items": [
                    {"label": "View Theme", "method": "theme_view"},
                    {"label": "Set Theme", "method": "theme_set"},
                    {"label": "Set Color", "method": "theme_color"},
                    {"label": "Set Terrain", "method": "map_set_terrain"},
                    {"label": "Random Terrain", "method": "map_random_terrain"},
                    {"label": "Reset Terrain", "method": "map_reset_terrain", "style": discord.ButtonStyle.danger},
                    {"label": "Cosmetics", "method": "cosmetics", "style": discord.ButtonStyle.primary},
                ],
            },
            {
                "title": "Admin Panel / Channels & Roles",
                "description": "Route bot output and set staff/player roles.",
                "items": [
                    {"label": "Map Channel", "method": "set_map_channel"},
                    {"label": "Overview Channel", "method": "set_overview_channel"},
                    {"label": "Menu Channel", "method": "set_menu_channel"},
                    {"label": "Enlist Channel", "method": "set_enlist_channel"},
                    {"label": "Report Channel", "method": "set_report_channel"},
                    {"label": "Announcement", "method": "set_announcement_channel"},
                    {"label": "Admin Role", "method": "set_admin_role"},
                    {"label": "Player Role", "method": "set_player_role"},
                    {"label": "GM Role", "method": "set_gm_role"},
                ],
            },
        ]
        super().__init__(bot, guild_id, theme, pages, AdminPanelView(bot, guild_id))


class GmPanelPagerView(_PagedPanelView):
    def __init__(self, bot, guild_id: int, theme: dict):
        pages = [
            {
                "title": "GM Panel / Contract",
                "description": "Start, pause, conclude, and inspect the current theatre.",
                "items": [
                    {"label": "Start Contract", "method": "start_contract", "style": discord.ButtonStyle.success},
                    {"label": "Pause Contract", "method": "pause_contract"},
                    {"label": "Conclude Contract", "method": "conclude_contract", "style": discord.ButtonStyle.danger},
                    {"label": "GM Map", "method": "gm_map", "style": discord.ButtonStyle.success},
                ],
            },
            {
                "title": "GM Panel / Enemies",
                "description": "Spawn, move, list, and remove hostile units.",
                "items": [
                    {"label": "Spawn Enemy", "method": "spawn_enemy", "style": discord.ButtonStyle.danger},
                    {"label": "Bulk Spawn", "method": "bulk_spawn_enemy", "style": discord.ButtonStyle.danger},
                    {"label": "Move Enemy", "method": "move_enemy", "style": discord.ButtonStyle.primary},
                    {"label": "Bulk Move", "method": "bulk_move_enemy", "style": discord.ButtonStyle.primary},
                    {"label": "List Enemies", "method": "list_enemies"},
                    {"label": "Remove Enemy", "method": "remove_enemy", "style": discord.ButtonStyle.danger},
                ],
            },
            {
                "title": "GM Panel / Cosmetics",
                "description": "Grant or revoke player banner and badge cosmetics by Discord ID and cosmetic key.",
                "items": [
                    {"label": "Grant Banner", "method": "grant_banner", "style": discord.ButtonStyle.primary},
                    {"label": "Remove Banner", "method": "remove_banner"},
                    {"label": "Grant Badge", "method": "grant_badge", "style": discord.ButtonStyle.primary},
                    {"label": "Remove Badge", "method": "remove_badge"},
                ],
            },
        ]
        super().__init__(bot, guild_id, theme, pages, GmPanelView(bot, guild_id))


# ══════════════════════════════════════════════════════════════════════════════
# COSMETIC MODALS
# ══════════════════════════════════════════════════════════════════════════════

class _BannerAddModal(discord.ui.Modal, title="Add Banner"):
    name = discord.ui.TextInput(label="Banner Name", max_length=60)
    image_url = discord.ui.TextInput(label="Image URL", max_length=500)

    async def on_submit(self, i: discord.Interaction):
        key = cosmetic_key(str(self.name))
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO cosmetic_banners (guild_id, banner_key, name, image_url, created_by)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (guild_id, banner_key) DO UPDATE
                SET name=EXCLUDED.name, image_url=EXCLUDED.image_url
            """, i.guild_id, key, str(self.name).strip(), str(self.image_url).strip(), i.user.id)
        await i.response.send_message(f"Banner `{key}` saved.", ephemeral=True)


class _BannerRemoveModal(discord.ui.Modal, title="Remove Banner"):
    banner_key = discord.ui.TextInput(label="Banner Key", max_length=40)

    async def on_submit(self, i: discord.Interaction):
        key = cosmetic_key(str(self.banner_key))
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM commander_banners WHERE guild_id=$1 AND banner_key=$2",
                i.guild_id, key)
            await conn.execute(
                "UPDATE commander_profiles SET selected_banner_key=NULL "
                "WHERE guild_id=$1 AND selected_banner_key=$2",
                i.guild_id, key)
            result = await conn.execute(
                "DELETE FROM cosmetic_banners WHERE guild_id=$1 AND banner_key=$2",
                i.guild_id, key)
        await i.response.send_message(f"Removed banner `{key}` ({result}).", ephemeral=True)


class _BadgeAddModal(discord.ui.Modal, title="Add Badge"):
    name = discord.ui.TextInput(label="Badge Name", max_length=60)
    symbol = discord.ui.TextInput(label="Badge Symbol", placeholder="ASCII preferred, e.g. * or ^", max_length=8)

    async def on_submit(self, i: discord.Interaction):
        key = cosmetic_key(str(self.name))
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO cosmetic_badges (guild_id, badge_key, symbol, text, created_by)
                VALUES ($1,$2,$3,$4,$5)
                ON CONFLICT (guild_id, badge_key) DO UPDATE
                SET symbol=EXCLUDED.symbol, text=EXCLUDED.text
            """, i.guild_id, key, str(self.symbol).strip()[:8], str(self.name).strip(), i.user.id)
        await i.response.send_message(f"Badge `{key}` saved.", ephemeral=True)


class _BadgeRemoveModal(discord.ui.Modal, title="Remove Badge"):
    badge_key = discord.ui.TextInput(label="Badge Key", max_length=40)

    async def on_submit(self, i: discord.Interaction):
        key = cosmetic_key(str(self.badge_key))
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM commander_badges WHERE guild_id=$1 AND badge_key=$2",
                i.guild_id, key)
            result = await conn.execute(
                "DELETE FROM cosmetic_badges WHERE guild_id=$1 AND badge_key=$2",
                i.guild_id, key)
        await i.response.send_message(f"Removed badge `{key}` ({result}).", ephemeral=True)


class _GrantCosmeticModal(discord.ui.Modal):
    owner_id = discord.ui.TextInput(label="Player Discord ID", max_length=24)
    key = discord.ui.TextInput(label="Cosmetic Key", max_length=40)

    def __init__(self, kind: str, remove: bool = False):
        super().__init__(title=("Remove " if remove else "Grant ") + kind.title())
        self.kind = kind
        self.remove = remove

    async def on_submit(self, i: discord.Interaction):
        try:
            owner_id = int(str(self.owner_id).strip())
        except ValueError:
            await i.response.send_message("Player Discord ID must be numeric.", ephemeral=True)
            return
        key = cosmetic_key(str(self.key))
        table = "commander_banners" if self.kind == "banner" else "commander_badges"
        key_col = "banner_key" if self.kind == "banner" else "badge_key"
        pool = await get_pool()
        async with pool.acquire() as conn:
            await ensure_commander_profile(conn, i.guild_id, owner_id, f"Commandant {owner_id}")
            if self.kind == "banner":
                await grant_default_banner(conn, i.guild_id, owner_id)
            if self.remove:
                await conn.execute(
                    f"DELETE FROM {table} WHERE guild_id=$1 AND owner_id=$2 AND {key_col}=$3",
                    i.guild_id, owner_id, key)
                if self.kind == "banner":
                    await conn.execute(
                        "UPDATE commander_profiles SET selected_banner_key=NULL "
                        "WHERE guild_id=$1 AND owner_id=$2 AND selected_banner_key=$3",
                        i.guild_id, owner_id, key)
            else:
                await conn.execute(
                    f"INSERT INTO {table} (guild_id, owner_id, {key_col}, granted_by) "
                    "VALUES ($1,$2,$3,$4) ON CONFLICT DO NOTHING",
                    i.guild_id, owner_id, key, i.user.id)
                if self.kind == "banner":
                    await conn.execute(
                        "UPDATE commander_profiles SET selected_banner_key=$3, updated_at=NOW() "
                        "WHERE guild_id=$1 AND owner_id=$2",
                        i.guild_id, owner_id, key)
        action = "Removed" if self.remove else "Granted"
        await i.response.send_message(f"{action} {self.kind} `{key}` for `{owner_id}`.", ephemeral=True)


# MODALS
# ══════════════════════════════════════════════════════════════════════════════

class _TurnIntervalModal(discord.ui.Modal, title="Set Turn Interval"):
    hours = discord.ui.TextInput(
        label="Hours between turns (1–168)",
        placeholder="e.g. 24",
        max_length=3, required=True)

    async def on_submit(self, i: discord.Interaction):
        try:
            h = int(str(self.hours).strip())
            assert 1 <= h <= 168
        except Exception:
            await i.response.send_message("Must be a number between 1 and 168.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET turn_interval_hours=$1 WHERE guild_id=$2",
                h, i.guild_id)
        await i.response.send_message(f"Turn interval set to **{h}h**.", ephemeral=True)


class _PlanetAddModal(discord.ui.Modal, title="Add Planet"):
    planet_name = discord.ui.TextInput(label="Planet Name", max_length=40)
    contractor  = discord.ui.TextInput(label="Contractor", max_length=60)
    enemy_type  = discord.ui.TextInput(label="Enemy Type", max_length=60)

    async def on_submit(self, i: discord.Interaction):
        name = str(self.planet_name).strip()
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT COUNT(*) FROM planets WHERE guild_id=$1", i.guild_id)
            try:
                row = await conn.fetchrow("""
                    INSERT INTO planets (guild_id, name, contractor, enemy_type, sort_order)
                    VALUES ($1,$2,$3,$4,$5) RETURNING id
                """, i.guild_id, name,
                    str(self.contractor).strip(),
                    str(self.enemy_type).strip(),
                    int(existing) + 1)
            except Exception:
                await i.response.send_message(
                    f"Planet `{name}` already exists.", ephemeral=True); return
            await ensure_hexes(i.guild_id, conn, row["id"])
        await i.response.send_message(
            f"Planet **{name}** added (ID {row['id']}).", ephemeral=True)


class _PlanetSetActiveModal(discord.ui.Modal, title="Set Active Planet"):
    planet_name = discord.ui.TextInput(label="Planet Name", max_length=40)

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i: discord.Interaction):
        name = str(self.planet_name).strip()
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet = await conn.fetchrow(
                "SELECT id, name FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                i.guild_id, name)
            if not planet:
                await i.response.send_message(f"Planet `{name}` not found.", ephemeral=True); return
            await ensure_hexes(i.guild_id, conn, planet["id"])
            await conn.execute(
                "UPDATE guild_config SET active_planet_id=$1 WHERE guild_id=$2",
                planet["id"], i.guild_id)
        await i.response.send_message(f"Active theatre: **{planet['name']}**.", ephemeral=True)
        try:
            from cogs.map_cog import auto_update_map, auto_update_overview
            await auto_update_map(self.bot, i.guild_id)
            await auto_update_overview(self.bot, i.guild_id)
        except Exception:
            pass


class _PlanetRemoveModal(discord.ui.Modal, title="Remove Planet"):
    planet_name = discord.ui.TextInput(
        label="Planet Name to Remove", max_length=40)

    async def on_submit(self, i: discord.Interaction):
        name = str(self.planet_name).strip()
        pool = await get_pool()
        async with pool.acquire() as conn:
            active_id = await get_active_planet_id(conn, i.guild_id)
            planet    = await conn.fetchrow(
                "SELECT id FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                i.guild_id, name)
            if not planet:
                await i.response.send_message(f"Planet `{name}` not found.", ephemeral=True); return
            if planet["id"] == active_id:
                await i.response.send_message(
                    "Cannot remove the active planet.", ephemeral=True); return
            pid = planet["id"]
            for tbl in ("hexes","hex_terrain","squadrons","enemy_units",
                        "combat_log","turn_history","enemy_gm_moves"):
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE guild_id=$1 AND planet_id=$2", i.guild_id, pid)
            await conn.execute(
                "DELETE FROM planets WHERE guild_id=$1 AND id=$2", i.guild_id, pid)
        await i.response.send_message(f"Planet **{name}** removed.", ephemeral=True)


# Planet Edit — two-step: pick field then enter value
PLANET_EDIT_FIELDS = {
    "name":        "Planet Name",
    "contractor":  "Contractor",
    "enemy_type":  "Enemy Type",
    "description": "Description",
}


class _PlanetEditFieldView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        select = discord.ui.Select(
            placeholder="Choose field to edit...",
            options=[
                discord.SelectOption(label=label, value=key)
                for key, label in PLANET_EDIT_FIELDS.items()
            ])
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, i: discord.Interaction):
        field = i.data["values"][0]
        await i.response.send_modal(_PlanetEditModal(field, PLANET_EDIT_FIELDS[field]))


class _PlanetEditModal(discord.ui.Modal, title="Edit Planet"):
    planet_name = discord.ui.TextInput(label="Planet Name", max_length=40)
    new_value   = discord.ui.TextInput(label="New Value",   max_length=80)

    def __init__(self, field: str, field_label: str):
        super().__init__(title=f"Edit Planet — {field_label}")
        self.field = field

    async def on_submit(self, i: discord.Interaction):
        name = str(self.planet_name).strip()
        val  = str(self.new_value).strip()
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet = await conn.fetchrow(
                "SELECT id FROM planets WHERE guild_id=$1 AND LOWER(name)=LOWER($2)",
                i.guild_id, name)
            if not planet:
                await i.response.send_message(f"Planet `{name}` not found.", ephemeral=True); return
            await conn.execute(
                f"UPDATE planets SET {self.field}=$1 WHERE guild_id=$2 AND id=$3",
                val, i.guild_id, planet["id"])
        await i.response.send_message(
            f"**{name}** — {PLANET_EDIT_FIELDS[self.field]} updated to `{val}`.", ephemeral=True)


# Theme
THEME_FIELDS = {
    "theme_bot_name":       "Bot Name",
    "theme_player_faction": "Player Faction",
    "theme_enemy_faction":  "Enemy Faction",
    "theme_player_unit":    "Player Unit Name",
    "theme_enemy_unit":     "Enemy Unit Name",
    "theme_flavor_text":    "Flavor Text",
}


class _ThemeSetFieldView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        select = discord.ui.Select(
            placeholder="Choose theme field...",
            options=[
                discord.SelectOption(label=label, value=key)
                for key, label in THEME_FIELDS.items()
            ])
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, i: discord.Interaction):
        field = i.data["values"][0]
        await i.response.send_modal(_ThemeSetModal(field, THEME_FIELDS[field]))


class _ThemeSetModal(discord.ui.Modal, title="Set Theme"):
    value = discord.ui.TextInput(label="New Value", max_length=80)

    def __init__(self, field: str, field_label: str):
        super().__init__(title=f"Set — {field_label}")
        self.field = field

    async def on_submit(self, i: discord.Interaction):
        val = str(self.value).strip()
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_config SET {self.field}=$1 WHERE guild_id=$2",
                val, i.guild_id)
        await i.response.send_message(
            f"**{THEME_FIELDS[self.field]}** set to `{val}`.", ephemeral=True)


class _ThemeColorModal(discord.ui.Modal, title="Set Accent Color"):
    hex_color = discord.ui.TextInput(
        label="Hex color (e.g. AA2222)",
        placeholder="AA2222",
        max_length=7, required=True)

    async def on_submit(self, i: discord.Interaction):
        raw = str(self.hex_color).strip().lstrip("#")
        try:
            color_int = int(raw, 16)
            assert 0 <= color_int <= 0xFFFFFF
        except Exception:
            await i.response.send_message("Invalid hex color.", ephemeral=True); return
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET theme_color=$1 WHERE guild_id=$2",
                color_int, i.guild_id)
        await i.response.send_message(f"Accent color: `#{raw.upper()}`.", ephemeral=True)


# Terrain — two-step: pick terrain type then enter hex
class _TerrainTypeView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=60)
        self.guild_id = guild_id
        select = discord.ui.Select(
            placeholder="Choose terrain type...",
            options=[
                discord.SelectOption(label=t.title(), value=t)
                for t in TERRAIN_TYPES
            ])
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, i: discord.Interaction):
        terrain = i.data["values"][0]
        await i.response.send_modal(_TerrainHexModal(terrain))


class _TerrainHexModal(discord.ui.Modal, title="Set Terrain"):
    hex_address = discord.ui.TextInput(
        label="Hex address (e.g. 3,-2)",
        placeholder="3,-2",
        max_length=12, required=True)

    def __init__(self, terrain: str):
        super().__init__(title=f"Set Terrain — {terrain.title()}")
        self.terrain = terrain

    async def on_submit(self, i: discord.Interaction):
        addr = str(self.hex_address).strip()
        if not is_valid(addr):
            await i.response.send_message(
                f"Invalid hex `{addr}`. Use format `gq,gr`.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            await conn.execute("""
                INSERT INTO hex_terrain (guild_id, planet_id, address, terrain)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (guild_id, planet_id, address) DO UPDATE SET terrain=EXCLUDED.terrain
            """, i.guild_id, planet_id, addr, self.terrain)
        await i.response.send_message(
            f"`{addr}` set to **{self.terrain.title()}**.", ephemeral=True)


# Channels — generic modal that resolves channel by ID or mention
class _ChannelModal(discord.ui.Modal, title="Set Channel"):
    channel_input = discord.ui.TextInput(
        label="Channel ID or #mention",
        placeholder="Paste the channel ID",
        max_length=30, required=True)

    def __init__(self, db_column: str, label: str):
        super().__init__(title=f"Set {label}")
        self.db_column = db_column

    async def on_submit(self, i: discord.Interaction):
        raw = str(self.channel_input).strip().lstrip("<#").rstrip(">")
        try:
            channel_id = int(raw)
        except ValueError:
            await i.response.send_message("Please paste a valid channel ID.", ephemeral=True); return
        channel = i.guild.get_channel(channel_id)
        if not channel:
            await i.response.send_message("Channel not found in this server.", ephemeral=True); return
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_config SET {self.db_column}=$1 WHERE guild_id=$2",
                channel_id, i.guild_id)
        await i.response.send_message(f"Set to {channel.mention}.", ephemeral=True)


class _MenuChannelModal(discord.ui.Modal, title="Set Menu Channel"):
    channel_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="Paste the channel ID",
        max_length=30, required=True)

    async def on_submit(self, i: discord.Interaction):
        raw = str(self.channel_input).strip().lstrip("<#").rstrip(">")
        try:
            channel_id = int(raw)
        except ValueError:
            await i.response.send_message("Please paste a valid channel ID.", ephemeral=True); return
        channel = i.guild.get_channel(channel_id)
        if not channel:
            await i.response.send_message("Channel not found.", ephemeral=True); return
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, i.guild_id)
            from views.menu import build_menu_embed, MainMenuView
            embed = await build_menu_embed(i.guild_id, conn, theme)
        msg = await channel.send(embed=embed, view=MainMenuView(i.guild_id))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET reg_channel_id=$1, reg_message_id=$2 WHERE guild_id=$3",
                channel.id, msg.id, i.guild_id)
        await i.response.send_message(f"Command panel posted in {channel.mention}.", ephemeral=True)


class _EnlistChannelModal(discord.ui.Modal, title="Set Enlist Channel"):
    channel_input = discord.ui.TextInput(
        label="Channel ID",
        placeholder="Paste the channel ID",
        max_length=30, required=True)

    async def on_submit(self, i: discord.Interaction):
        raw = str(self.channel_input).strip().lstrip("<#").rstrip(">")
        try:
            channel_id = int(raw)
        except ValueError:
            await i.response.send_message("Please paste a valid channel ID.", ephemeral=True); return
        channel = i.guild.get_channel(channel_id)
        if not channel:
            await i.response.send_message("Channel not found.", ephemeral=True); return
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, i.guild_id)
            planet_id = await get_active_planet_id(conn, i.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                i.guild_id, planet_id)
            count     = await conn.fetchval(
                "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
                "WHERE guild_id=$1",
                i.guild_id) or 0
            from views.menu import build_enlist_embed, EnlistView
            embed = build_enlist_embed(
                theme,
                planet["name"]       if planet else "Unknown",
                planet["contractor"] if planet else "---",
                planet["enemy_type"] if planet else "---",
                count,
            )
        msg = await channel.send(embed=embed, view=EnlistView(i.guild_id))
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE guild_config SET enlist_channel_id=$1, enlist_message_id=$2 WHERE guild_id=$3",
                channel.id, msg.id, i.guild_id)
        await i.response.send_message(f"Enlistment board posted in {channel.mention}.", ephemeral=True)


class _RoleModal(discord.ui.Modal, title="Set Role"):
    role_input = discord.ui.TextInput(
        label="Role ID or @mention",
        placeholder="Paste the role ID",
        max_length=30, required=True)

    def __init__(self, db_column: str, label: str):
        super().__init__(title=f"Set {label}")
        self.db_column = db_column

    async def on_submit(self, i: discord.Interaction):
        raw = str(self.role_input).strip().lstrip("<@&").rstrip(">")
        try:
            role_id = int(raw)
        except ValueError:
            await i.response.send_message("Please paste a valid role ID.", ephemeral=True); return
        role = i.guild.get_role(role_id)
        if not role:
            await i.response.send_message("Role not found in this server.", ephemeral=True); return
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                f"UPDATE guild_config SET {self.db_column}=$1 WHERE guild_id=$2",
                role_id, i.guild_id)
        await i.response.send_message(f"Set to {role.mention}.", ephemeral=True)


# GM Modals
class _StartContractModal(discord.ui.Modal, title="Start Contract"):
    contract_name = discord.ui.TextInput(
        label="Contract Name",
        placeholder="e.g. Operation Iron Dawn",
        max_length=80,
        required=True,
    )
    rp_description = discord.ui.TextInput(
        label="Roleplay Description / Briefing",
        placeholder="Describe the contract, objectives, and what operatives must accomplish...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i: discord.Interaction):
        name = str(self.contract_name).strip()
        desc = str(self.rp_description).strip()
        await ensure_guild(i.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            planet    = await conn.fetchrow(
                "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
                i.guild_id, planet_id)
            await ensure_hexes(i.guild_id, conn, planet_id)
            for tbl in ("enemy_units", "combat_log",
                        "turn_history", "enemy_gm_moves", "movement_arrows"):
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE guild_id=$1 AND planet_id=$2",
                    i.guild_id, planet_id)
            await conn.execute("""
                UPDATE squadrons
                SET is_active=FALSE,
                    in_transit=FALSE,
                    transit_destination=NULL,
                    transit_turns_left=0,
                    is_dug_in=FALSE,
                    artillery_armed=FALSE,
                    hexes_moved_this_turn=0
                WHERE guild_id=$1 AND planet_id=$2
            """, i.guild_id, planet_id)
            await conn.execute(
                "UPDATE commander_profiles SET recovery_status=NULL, updated_at=NOW() "
                "WHERE guild_id=$1",
                i.guild_id)
            await conn.execute(
                "UPDATE hexes SET controller='neutral', status='neutral' "
                "WHERE guild_id=$1 AND planet_id=$2",
                i.guild_id, planet_id)
            await conn.execute(
                "UPDATE guild_config SET game_started=TRUE, contract_name=$1 WHERE guild_id=$2",
                name, i.guild_id)
            theme = await get_theme(conn, i.guild_id)
            # Post to announcement channel if set
            cfg = await conn.fetchrow(
                "SELECT announcement_channel_id FROM guild_config WHERE guild_id=$1", i.guild_id)
        await i.response.send_message(
            f"✅ **Contract: {name}** has started!", ephemeral=True)
        if cfg and cfg["announcement_channel_id"]:
            channel = i.guild.get_channel(cfg["announcement_channel_id"])
            if channel:
                embed = discord.Embed(
                    title=f"📜 Contract: {name}",
                    color=theme.get("color", 0xAA2222),
                    description=(
                        f"{desc}\n\n"
                        f"**Planet:** {planet['name'] if planet else '—'}\n"
                        f"**Contractor:** {planet['contractor'] if planet else '—'}\n"
                        f"**Enemy:** {planet['enemy_type'] if planet else '—'}\n\n"
                        f"*Commandants may now enlist or deploy. Good luck.*"
                    ),
                )
                embed.set_footer(text=theme.get("flavor_text", "The contract must be fulfilled."))
                await channel.send(embed=embed)


class _ContractOutcomeModal(discord.ui.Modal, title="Conclude Contract"):
    outcome = discord.ui.TextInput(
        label="Outcome",
        placeholder="SUCCESS or FAILURE",
        max_length=10,
        required=True,
    )
    rp_description = discord.ui.TextInput(
        label="Roleplay Outcome Description",
        placeholder="Describe what happened — did the operatives fulfil the contract in time?",
        style=discord.TextStyle.paragraph,
        max_length=1200,
        required=True,
    )

    def __init__(self, bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, i: discord.Interaction):
        outcome_raw = str(self.outcome).strip().upper()
        desc        = str(self.rp_description).strip()
        success     = "SUCCESS" in outcome_raw
        icon        = "✅" if success else "❌"
        label       = "CONTRACT FULFILLED" if success else "CONTRACT FAILED"
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme  = await get_theme(conn, i.guild_id)
            cfg    = await conn.fetchrow(
                "SELECT announcement_channel_id, contract_name FROM guild_config WHERE guild_id=$1",
                i.guild_id)
            planet_id = await get_active_planet_id(conn, i.guild_id)
            # Pause the game and move deployed player units back to their persistent roster.
            await conn.execute(
                "UPDATE guild_config SET game_started=FALSE WHERE guild_id=$1", i.guild_id)
            await conn.execute("""
                UPDATE squadrons
                SET is_active=FALSE,
                    in_transit=FALSE,
                    transit_destination=NULL,
                    transit_turns_left=0,
                    is_dug_in=FALSE,
                    artillery_armed=FALSE,
                    hexes_moved_this_turn=0
                WHERE guild_id=$1 AND planet_id=$2
            """, i.guild_id, planet_id)
        contract_name = (cfg["contract_name"] if cfg and cfg["contract_name"] else "Contract")
        await i.response.send_message(
            f"{icon} **{label}** posted to announcement channel.", ephemeral=True)
        if cfg and cfg["announcement_channel_id"]:
            channel = i.guild.get_channel(cfg["announcement_channel_id"])
            if channel:
                embed = discord.Embed(
                    title=f"{icon} {label} — {contract_name}",
                    color=0x22AA44 if success else 0xAA2222,
                    description=desc,
                )
                embed.set_footer(text=theme.get("flavor_text", "The contract must be fulfilled."))
                await channel.send(embed=embed)
        else:
            await i.followup.send(
                "⚠ No announcement channel set. Use Admin Panel → Announcement Channel to configure one.",
                ephemeral=True)


class _SpawnEnemyModal(discord.ui.Modal, title="Spawn Enemy Unit"):
    unit_type   = discord.ui.TextInput(label="Unit Type", placeholder="e.g. Scout", max_length=40)
    hex_address = discord.ui.TextInput(label="Hex Address", placeholder="e.g. 6,-3", max_length=12)
    hp_input    = discord.ui.TextInput(
        label="HP (default 100)", placeholder="e.g. 100", max_length=5, required=False)

    async def on_submit(self, i: discord.Interaction):
        addr = str(self.hex_address).strip()
        if not is_valid(addr):
            await i.response.send_message(f"Invalid hex `{addr}`.", ephemeral=True); return
        try:
            hp = max(1, int(str(self.hp_input).strip())) if str(self.hp_input).strip() else 100
        except ValueError:
            hp = 100
        v = lambda: random.randint(-2, 2)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            await conn.execute(
                "INSERT INTO enemy_units "
                "(guild_id, planet_id, unit_type, hex_address, "
                " attack, defense, speed, morale, supply, recon, hp) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                i.guild_id, planet_id, str(self.unit_type).strip()[:40], addr,
                10+v(), 10+v(), 10+v(), 10+v(), 10+v(), 10+v(), hp)
        await i.response.send_message(
            f"👾 **{str(self.unit_type).strip()}** spawned at `{addr}` with **{hp} HP**.", ephemeral=True)


class _BulkSpawnEnemyModal(discord.ui.Modal, title="Bulk Spawn Enemy Units"):
    unit_type   = discord.ui.TextInput(label="Unit Type", placeholder="e.g. Scout", max_length=40)
    hex_address = discord.ui.TextInput(
        label="Hex Address", placeholder="e.g. 6,-3", max_length=12)
    count_input = discord.ui.TextInput(
        label="Number of units (1–20)", placeholder="e.g. 5", max_length=3)
    hp_input    = discord.ui.TextInput(
        label="HP per unit (default 100)", placeholder="e.g. 100", max_length=5, required=False)

    async def on_submit(self, i: discord.Interaction):
        addr = str(self.hex_address).strip()
        if not is_valid(addr):
            await i.response.send_message(f"Invalid hex `{addr}`.", ephemeral=True); return
        try:
            count = max(1, min(20, int(str(self.count_input).strip())))
        except ValueError:
            await i.response.send_message("Count must be a number between 1 and 20.", ephemeral=True); return
        try:
            hp = max(1, int(str(self.hp_input).strip())) if str(self.hp_input).strip() else 100
        except ValueError:
            hp = 100
        unit_name = str(self.unit_type).strip()[:40]
        v = lambda: random.randint(-2, 2)
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            for _ in range(count):
                await conn.execute(
                    "INSERT INTO enemy_units "
                    "(guild_id, planet_id, unit_type, hex_address, "
                    " attack, defense, speed, morale, supply, recon, hp) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
                    i.guild_id, planet_id, unit_name, addr,
                    10+v(), 10+v(), 10+v(), 10+v(), 10+v(), 10+v(), hp)
        await i.response.send_message(
            f"👾 **{count}× {unit_name}** spawned at `{addr}` with **{hp} HP** each.",
            ephemeral=True)


class _MoveEnemyModal(discord.ui.Modal, title="Queue Enemy Move"):
    unit_id     = discord.ui.TextInput(label="Enemy Unit ID", max_length=10)
    hex_address = discord.ui.TextInput(label="Target Hex", placeholder="e.g. 4,-2", max_length=12)

    async def on_submit(self, i: discord.Interaction):
        addr = str(self.hex_address).strip()
        if not is_valid(addr):
            await i.response.send_message(f"Invalid hex `{addr}`.", ephemeral=True); return
        try:
            uid = int(str(self.unit_id).strip())
        except ValueError:
            await i.response.send_message("Unit ID must be a number.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            await conn.execute("""
                INSERT INTO enemy_gm_moves (guild_id, planet_id, enemy_unit_id, target_address)
                VALUES ($1,$2,$3,$4)
                ON CONFLICT (guild_id, enemy_unit_id)
                DO UPDATE SET target_address=EXCLUDED.target_address
            """, i.guild_id, planet_id, uid, addr)
        await i.response.send_message(
            f"Unit **{uid}** queued to `{addr}` next turn.", ephemeral=True)


class _BulkMoveEnemyModal(discord.ui.Modal, title="Bulk Queue Enemy Moves"):
    moves_input = discord.ui.TextInput(
        label="Moves (one per line: ID hex)",
        placeholder="1 4,-2\n2 0,5\n3 -3,1",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True,
    )

    async def on_submit(self, i: discord.Interaction):
        raw_lines = str(self.moves_input).strip().splitlines()
        pool = await get_pool()
        successes = []
        errors    = []
        async with pool.acquire() as conn:
            planet_id = await get_active_planet_id(conn, i.guild_id)
            for line in raw_lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 2:
                    errors.append(f"Bad format: `{line}` (expected `ID hex`)")
                    continue
                uid_str, addr = parts
                if not is_valid(addr):
                    errors.append(f"Invalid hex `{addr}` for unit `{uid_str}`")
                    continue
                try:
                    uid = int(uid_str)
                except ValueError:
                    errors.append(f"Non-numeric ID: `{uid_str}`")
                    continue
                unit = await conn.fetchrow(
                    "SELECT id FROM enemy_units WHERE guild_id=$1 AND id=$2 AND is_active=TRUE",
                    i.guild_id, uid)
                if not unit:
                    errors.append(f"Unit ID {uid} not found or inactive")
                    continue
                await conn.execute("""
                    INSERT INTO enemy_gm_moves (guild_id, planet_id, enemy_unit_id, target_address)
                    VALUES ($1,$2,$3,$4)
                    ON CONFLICT (guild_id, enemy_unit_id)
                    DO UPDATE SET target_address=EXCLUDED.target_address
                """, i.guild_id, planet_id, uid, addr)
                successes.append(f"Unit **{uid}** → `{addr}`")

        parts_out = []
        if successes:
            parts_out.append(f"✅ Queued {len(successes)} move(s):\n" + "\n".join(successes))
        if errors:
            parts_out.append(f"⚠ {len(errors)} error(s):\n" + "\n".join(errors))
        msg = "\n\n".join(parts_out) or "Nothing processed."
        await i.response.send_message(msg[:2000], ephemeral=True)


class _RemoveEnemyModal(discord.ui.Modal, title="Remove Enemy Unit"):
    unit_id = discord.ui.TextInput(label="Enemy Unit ID", max_length=10)

    async def on_submit(self, i: discord.Interaction):
        try:
            uid = int(str(self.unit_id).strip())
        except ValueError:
            await i.response.send_message("Unit ID must be a number.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE enemy_units SET is_active=FALSE WHERE guild_id=$1 AND id=$2",
                i.guild_id, uid)
        if result == "UPDATE 0":
            await i.response.send_message(f"Enemy unit {uid} not found.", ephemeral=True)
        else:
            await i.response.send_message(f"Enemy unit **{uid}** removed.", ephemeral=True)


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


# ══════════════════════════════════════════════════════════════════════════════
# COG — only two slash commands remain: /admin_panel and /gm_panel
# ══════════════════════════════════════════════════════════════════════════════

class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="admin_panel",
                          description="Open the admin control panel.")
    async def admin_panel(self, interaction: discord.Interaction):
        if not await _is_admin(self.bot, interaction):
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
        view  = AdminPanelPagerView(self.bot, interaction.guild_id, theme)
        embed = view._embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="gm_panel",
                          description="Open the Game Master control panel.")
    async def gm_panel(self, interaction: discord.Interaction):
        if not (await _is_admin(self.bot, interaction) or await _is_gm(interaction)):
            await interaction.response.send_message("GMs only.", ephemeral=True)
            return
        await ensure_guild(interaction.guild_id)
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
        view  = GmPanelPagerView(self.bot, interaction.guild_id, theme)
        embed = view._embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
