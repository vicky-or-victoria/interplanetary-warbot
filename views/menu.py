"""
Views â€” Victoria-style persistent button command panel.
All labels are theme-aware. Planet context is always the active planet.
"""

import random
import discord
from discord.ui import View, Button

from utils.db import get_pool, get_theme, get_active_planet_id
from utils.brigades import BRIGADES
from utils.revenant_ui import (
    build_revenant_embed,
    dot_bar,
    format_section,
    kv,
    progress_bar,
    status_label,
    transmission,
)


def _bar(val: int, length: int = 12) -> str:
    filled = max(0, min(length, int((val / 20) * length)))
    return "█" * filled + "░" * (length - filled)


async def _safe(interaction: discord.Interaction, coro):
    try:
        await coro
    except Exception as e:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"Error: {e}", ephemeral=True)
        except Exception:
            pass


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# MAIN MENU
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

class MainMenuView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="View Map",      style=discord.ButtonStyle.primary,   custom_id="menu_map",        row=0)
    async def view_map(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_map(i))

    @discord.ui.button(label="Intel",        style=discord.ButtonStyle.secondary, custom_id="menu_planetary_system",     row=0)
    async def planetary_system(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_overview(i))

    @discord.ui.button(label="Deployment",       style=discord.ButtonStyle.primary,   custom_id="menu_my_unit",    row=0)
    async def my_unit(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_unit_panel(i))

    @discord.ui.button(label="View Contracts", style=discord.ButtonStyle.secondary, custom_id="menu_status",     row=1)
    async def war_status(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_contract_board(i))

    @discord.ui.button(label="Reports",    style=discord.ButtonStyle.secondary, custom_id="menu_log",        row=1)
    async def combat_log(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_combat_log(i))

    @discord.ui.button(label="Details",   style=discord.ButtonStyle.secondary, custom_id="menu_leaderboard",row=1)
    async def leaderboard(self, i: discord.Interaction, b: Button):
        await _safe(i, _send_leaderboard(i))


# â”€â”€ Map â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _send_map(i: discord.Interaction):
    await i.response.defer(ephemeral=True, thinking=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        try:
            from utils.map_render import render_map_for_guild
            buf = await render_map_for_guild(i.guild_id, conn)
            f   = discord.File(buf, filename="warmap.png")
            embed = build_revenant_embed(
                "Tactical Map",
                format_section("Map Link", ["**Status:** Live tactical map render attached."]),
                "info",
                footer=theme.get("flavor_text", ""))
            embed.set_image(url="attachment://warmap.png")
            embed.set_footer(text=theme.get("flavor_text",""))
            await i.followup.send(embed=embed, file=f, ephemeral=True)
        except Exception as e:
            await i.followup.send(f"Map render failed: {e}", ephemeral=True)


async def _send_overview(i: discord.Interaction):
    await i.response.defer(ephemeral=True, thinking=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        try:
            from utils.map_render import render_overview_for_guild
            buf = await render_overview_for_guild(i.guild_id, conn)
            f   = discord.File(buf, filename="overview.png")
            embed = build_revenant_embed(
                "System Overview",
                format_section("System Overview", ["**Status:** Theatre overview image attached."]),
                "info")
            embed.set_image(url="attachment://overview.png")
            await i.followup.send(embed=embed, file=f, ephemeral=True)
        except Exception as e:
            await i.followup.send(f"Overview render failed: {e}", ephemeral=True)


# â”€â”€ Unit panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _send_unit_panel(i: discord.Interaction):
    from cogs.squadron_cog import send_unit_panel
    await send_unit_panel(i, i.guild_id)


# â”€â”€ Unit action sub-panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class UnitActionView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="Move",       style=discord.ButtonStyle.primary,   custom_id="unit_action_move")
    async def move_unit(self, i: discord.Interaction, b: Button):
        await i.response.send_modal(MoveModal(self.guild_id))

    @discord.ui.button(label="Intel", style=discord.ButtonStyle.secondary, custom_id="unit_action_scavenge")
    async def scavenge(self, i: discord.Interaction, b: Button):
        await _safe(i, _do_scavenge(i, self.guild_id))

    @discord.ui.button(label="Back",             style=discord.ButtonStyle.secondary, custom_id="unit_action_back")
    async def back(self, i: discord.Interaction, b: Button):
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, self.guild_id)
            embed = await build_menu_embed(self.guild_id, conn, theme)
        await i.response.edit_message(embed=embed, view=MainMenuView(self.guild_id))


# â”€â”€ Move modal â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class MoveModal(discord.ui.Modal, title="Move Unit"):
    destination = discord.ui.TextInput(
        label="Target Hex Address",
        placeholder="e.g. 3,-2",
        max_length=12, required=True)

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, i: discord.Interaction):
        dest = str(self.destination).strip()
        from utils.hexmap import is_valid, hex_distance
        if not is_valid(dest):
            await i.response.send_message(
                "Invalid hex. Use format `gq,gr` e.g. `3,-2`.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme     = await get_theme(conn, self.guild_id)
            planet_id = await get_active_planet_id(conn, self.guild_id)
            sq        = await conn.fetchrow(
                "SELECT * FROM squadrons "
                "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
                self.guild_id, planet_id, i.user.id)
            if not sq:
                await i.response.send_message("No active unit.", ephemeral=True); return
            if sq["in_transit"]:
                await i.response.send_message("Already in transit.", ephemeral=True); return

            dist = hex_distance(sq["hex_address"], dest)
            if dist == 0:
                await i.response.send_message("Already at that hex.", ephemeral=True); return

            budget    = sq["speed"] // 2
            remaining = max(0, budget - sq["hexes_moved_this_turn"])
            if dist > remaining:
                await i.response.send_message(
                    f"That hex is **{dist}** away but you only have "
                    f"**{remaining}/{budget}** hexes remaining this turn.", ephemeral=True); return

            await conn.execute(
                "UPDATE squadrons SET hex_address=$1, is_dug_in=FALSE, "
                "hexes_moved_this_turn=hexes_moved_this_turn+$2 WHERE id=$3",
                dest, dist, sq["id"])
            await i.response.send_message(f"Moved to `{dest}`.", ephemeral=True)


# â”€â”€ Scavenge â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _do_scavenge(i: discord.Interaction, guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, guild_id)
        planet_id = await get_active_planet_id(conn, guild_id)
        turn_count = await conn.fetchval(
            "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
            guild_id, planet_id) or 0
        sq = await conn.fetchrow(
            "SELECT * FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND owner_id=$3 AND is_active=TRUE LIMIT 1",
            guild_id, planet_id, i.user.id)
        if not sq:
            await i.response.send_message("No active unit.", ephemeral=True); return
        if sq["last_scavenged_turn"] >= turn_count:
            await i.response.send_message("Already scavenged this turn.", ephemeral=True); return
        gain = random.randint(1, 5) + (sq["recon"] // 5)
        new_supply = min(20, sq["supply"] + gain)
        await conn.execute(
            "UPDATE squadrons SET supply=$1, last_scavenged_turn=$2 WHERE id=$3",
            new_supply, turn_count, sq["id"])
    await i.response.send_message(
        f"Scavenged **+{gain}** supply -> `{new_supply}/20`.", ephemeral=True)




DEPLOYABLE_STATUSES = ("deployable", "active")
BOARD_STATUSES = ("open", "accepting", "locked", "deployable", "active")


async def fetch_contract(conn, guild_id: int, contract_id: int):
    return await conn.fetchrow(
        "SELECT * FROM contracts WHERE guild_id=$1 AND id=$2",
        guild_id, contract_id)


async def fetch_board_contracts(conn, guild_id: int, limit: int = 25):
    return await conn.fetch(
        """
        SELECT c.*,
               COUNT(ca.player_id)::INT AS accepted_count
        FROM contracts c
        LEFT JOIN contract_acceptances ca
          ON ca.guild_id=c.guild_id AND ca.contract_id=c.id
        WHERE c.guild_id=$1
          AND c.status = ANY($2::text[])
        GROUP BY c.id
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT $3
        """,
        guild_id, list(BOARD_STATUSES), limit)


def build_contract_board_embed(theme: dict, rows, selected_id: int = None) -> discord.Embed:
    selected = None
    if rows:
        selected = next((c for c in rows if c["id"] == selected_id), rows[0])
    if not selected:
        return build_revenant_embed(
            "Contract Board",
            format_section("Contract Board", ["No contracts on the board yet."]),
            "warning")

    try:
        accepted = selected["accepted_count"]
    except (KeyError, IndexError):
        accepted = 0
    capacity = selected["deployment_capacity"] or 0
    deployed = selected["deployed_units"] or 0
    fleet_count = selected["fleet_count"] or 0
    accepted_limit = max(accepted, capacity // 4, 1)
    summary = [
        kv("Contract", f"#{selected['id']:03d} {selected['title']}"),
        kv("Enemy", selected["enemy"]),
        kv("Status", status_label(selected["status"])),
        kv("Difficulty", selected["difficulty"]),
        kv("Fleets Assigned", dot_bar(fleet_count)),
        kv("Deployment Capacity", f"{deployed}/{capacity} units `{progress_bar(deployed, capacity)}`"),
        kv("Accepted Players", f"{accepted}/{accepted_limit}"),
    ]
    queue = [
        f"{'▶' if c['id'] == selected['id'] else '•'} #{c['id']:03d} {c['title']} - {status_label(c['status'])}"
        for c in rows[:10]
    ]
    note = selected["description"] or "Operation file available. Select Details for full briefing."
    if len(note) > 180:
        note = note[:177].rstrip() + "..."
    color_type = "warning" if str(selected["status"]).lower() in {"locked", "pending"} else "active"
    return build_revenant_embed(
        "Contract Board",
        (
            f"{format_section('Available Operations', summary)}\n\n"
            f"{format_section('Board Queue', queue)}\n\n"
            f"{transmission(note)}"
        ),
        color_type)


class ContractSelect(discord.ui.Select):
    def __init__(self, rows, selected_id: int = None):
        options = []
        for c in rows[:25]:
            capacity = c["deployment_capacity"] or 0
            deployed = c["deployed_units"] or 0
            options.append(discord.SelectOption(
                label=f"#{c['id']:03d} {c['title']}"[:100],
                value=str(c["id"]),
                description=f"{c['status']} | fleets {c['fleet_count'] or 0} | units {deployed}/{capacity}"[:100],
                default=(selected_id == c["id"]),
            ))
        super().__init__(placeholder="Select a contract...", options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_id = int(self.values[0])
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            rows = await fetch_board_contracts(conn, interaction.guild_id)
        embed = build_contract_board_embed(theme, rows, selected_id)
        await interaction.response.edit_message(
            embed=embed,
            view=ContractBoardView(interaction.guild_id, rows, selected_id))


class ContractBoardView(View):
    def __init__(self, guild_id:int, rows=None, selected_id: int = None):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.selected_id = selected_id
        if rows:
            self.add_item(ContractSelect(rows, selected_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=2)
    async def view_contract(self,i,b):
        await _send_contract_board(i)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=0)
    async def accept_contract(self,i,b):
        if not self.selected_id:
            await i.response.send_message("Select a contract first.", ephemeral=True); return
        pool=await get_pool()
        async with pool.acquire() as conn:
            c=await fetch_contract(conn,i.guild_id,self.selected_id)
            if not c:
                await i.response.send_message("No contract available.",ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("This contract is not accepting sign-ups.",ephemeral=True); return
            await conn.execute("INSERT INTO contract_acceptances (guild_id, contract_id, player_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", i.guild_id, c['id'], i.user.id)
        await i.response.send_message(f"Accepted contract #{c['id']:03d}.",ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.danger, row=0)
    async def withdraw_contract(self,i,b):
        if not self.selected_id:
            await i.response.send_message("Select a contract first.", ephemeral=True); return
        pool=await get_pool()
        async with pool.acquire() as conn:
            c=await fetch_contract(conn,i.guild_id,self.selected_id)
            if not c:
                await i.response.send_message("No contract available.",ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("Sign-ups are locked for this contract.",ephemeral=True); return
            await conn.execute("DELETE FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 AND player_id=$3", i.guild_id, c['id'], i.user.id)
        await i.response.send_message(f"Withdrawn from contract #{c['id']:03d}.",ephemeral=True)

    @discord.ui.button(label="Deploy", style=discord.ButtonStyle.primary, row=0)
    async def deploy_contract(self,i,b):
        if not self.selected_id:
            await i.response.send_message("Select a contract first.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            accepted = await conn.fetchval(
                "SELECT 1 FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 AND player_id=$3",
                i.guild_id, self.selected_id, i.user.id)
            roster_count = await conn.fetchval(
                "SELECT COUNT(*) FROM squadrons WHERE guild_id=$1 AND owner_id=$2 AND is_active=FALSE",
                i.guild_id, i.user.id) or 0
        if not c or c["status"] not in DEPLOYABLE_STATUSES or not accepted:
            await i.response.send_message("Select an accepted, fleet-assigned deployable contract first.", ephemeral=True); return
        if roster_count == 0:
            await i.response.send_modal(_UnitNameModal(i.guild_id, False, self.selected_id)); return
        from cogs.squadron_cog import open_returning_deploy
        await open_returning_deploy(i, self.selected_id)

    @discord.ui.button(label="Details", style=discord.ButtonStyle.secondary, row=1)
    async def new_unit_contract(self,i,b):
        if not self.selected_id:
            await i.response.send_message("Select a contract first.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, i.guild_id)
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            accepted = await conn.fetchval(
                "SELECT COUNT(*) FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2",
                i.guild_id, self.selected_id) or 0
        if not c:
            await i.response.send_message("Contract not found.", ephemeral=True); return
        desc = format_section("Contract Details", [
            kv("Contract", f"#{c['id']:03d} {c['title']}"),
            kv("Location", c["planet_system"]),
            kv("Enemy", c["enemy"]),
            kv("Difficulty", c["difficulty"]),
            kv("Status", status_label(c["status"])),
            kv("Fleets Assigned", dot_bar(c["fleet_count"] or 0)),
            kv("Deployment Capacity", f"{c['deployed_units'] or 0}/{c['deployment_capacity'] or 0} units `{progress_bar(c['deployed_units'] or 0, c['deployment_capacity'] or 0)}`"),
            kv("Accepted Players", accepted),
            "",
            "**Mission Briefing:**",
            c["description"] or "No briefing filed.",
        ])
        await i.response.send_message(
            embed=build_revenant_embed("Contract Details", desc, "info"),
            view=ContractBoardView(i.guild_id, [c], c["id"]),
            ephemeral=True)

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, row=1)
    async def participants(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("Select a contract first.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT player_id FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 ORDER BY accepted_at",
                i.guild_id, self.selected_id)
        lines = [f"<@{r['player_id']}>" for r in rows] or ["No accepted players yet."]
        await i.response.send_message(
            embed=build_revenant_embed("Contract Details", format_section("Accepted Players", lines), "info"),
            ephemeral=True)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, i: discord.Interaction, b: Button):
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, self.guild_id)
            embed = await build_menu_embed(self.guild_id, conn, theme)
        if i.response.is_done():
            await i.followup.send(embed=embed, view=MainMenuView(self.guild_id), ephemeral=True)
        else:
            await i.response.send_message(embed=embed, view=MainMenuView(self.guild_id), ephemeral=True)


async def _send_contract_board(i: discord.Interaction):
    pool=await get_pool()
    async with pool.acquire() as conn:
        theme=await get_theme(conn,i.guild_id)
        rows=await fetch_board_contracts(conn, i.guild_id)
    if not rows:
        await i.response.send_message("No contracts on the board yet.",ephemeral=True); return
    selected_id = rows[0]["id"]
    embed=build_contract_board_embed(theme, rows, selected_id)
    await i.response.send_message(embed=embed, view=ContractBoardView(i.guild_id, rows, selected_id), ephemeral=True)


# â”€â”€ War status â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _send_war_status(i: discord.Interaction):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, i.guild_id)
        planet_id = await get_active_planet_id(conn, i.guild_id)
        planet    = await conn.fetchrow(
            "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
            i.guild_id, planet_id)
        cfg       = await conn.fetchrow(
            "SELECT game_started, turn_interval_hours, contract_name, operational_tempo, tempo_threshold, fleet_pool_available FROM guild_config WHERE guild_id=$1",
            i.guild_id)
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
        hex_s     = await conn.fetch(
            "SELECT status, COUNT(*) AS cnt FROM hexes "
            "WHERE guild_id=$1 AND planet_id=$2 GROUP BY status",
            i.guild_id, planet_id)

    status_lines = [
        kv("Active Theatre", planet["name"] if planet else "Unknown"),
        kv("Contractor", planet["contractor"] if planet else "Unknown"),
        kv("Enemy", planet["enemy_type"] if planet else "Unknown"),
        kv("Turn", turns),
        kv("Status", "Active" if cfg and cfg["game_started"] else "Paused"),
        kv("Operational Tempo", f"{cfg['operational_tempo'] if cfg else 0}/{cfg['tempo_threshold'] if cfg else 500}"),
        kv("Fleets Available", cfg["fleet_pool_available"] if cfg else 0),
    ]
    sector_lines = [f"`{r['status']}`: {r['cnt']}" for r in hex_s] or ["No data."]
    embed = build_revenant_embed(
        "System Overview",
        f"{format_section('System Overview', status_lines)}\n\n{format_section('Sector Control', sector_lines)}",
        "info")
    await i.response.send_message(embed=embed, ephemeral=True)


# â”€â”€ Combat log â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _send_combat_log(i: discord.Interaction):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, i.guild_id)
        planet_id = await get_active_planet_id(conn, i.guild_id)
        entries   = await conn.fetch(
            "SELECT turn_number, hex_address, attacker, defender, "
            "attacker_roll, defender_roll, outcome "
            "FROM combat_log WHERE guild_id=$1 AND planet_id=$2 "
            "ORDER BY id DESC LIMIT 15",
            i.guild_id, planet_id)
    if not entries:
        await i.response.send_message("No combat recorded yet.", ephemeral=True); return
    lines = []
    for e in entries:
        icon = {"attacker_wins":"WIN", "defender_wins":"LOSS", "draw":"DRAW"}.get(e["outcome"], "INFO")
        lines.append(
            f"{icon} T{e['turn_number']} `{e['hex_address']}` - "
            f"{e['attacker']} vs {e['defender']} ({e['attacker_roll']} vs {e['defender_roll']})")
    embed = build_revenant_embed("Intel Network", format_section("Recent Reports", lines), "warning")
    await i.response.send_message(embed=embed, ephemeral=True)


# â”€â”€ Leaderboard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _send_leaderboard(i: discord.Interaction):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme     = await get_theme(conn, i.guild_id)
        planet_id = await get_active_planet_id(conn, i.guild_id)
        rows      = await conn.fetch(
            "SELECT owner_name, name, attack+defense+speed+morale+supply+recon AS power "
            "FROM squadrons WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
            "ORDER BY power DESC LIMIT 10",
            i.guild_id, planet_id)
    if not rows:
        await i.response.send_message("No units enlisted yet.", ephemeral=True); return
    lines = [f"**{n+1}.** {r['owner_name']} - {r['name']} | Power {r['power']}"
             for n, r in enumerate(rows)]
    embed = build_revenant_embed("Intel Network", format_section("Recent Reports", lines), "info")
    await i.response.send_message(embed=embed, ephemeral=True)


# â”€â”€ Menu embed builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def build_menu_embed(guild_id: int, conn, theme: dict = None) -> discord.Embed:
    if theme is None:
        theme = await get_theme(conn, guild_id)

    planet_id  = await get_active_planet_id(conn, guild_id)
    planet     = await conn.fetchrow(
        "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
        guild_id, planet_id)
    cfg        = await conn.fetchrow(
        "SELECT game_started, turn_interval_hours, contract_name, operational_tempo, tempo_threshold, fleet_pool_available FROM guild_config WHERE guild_id=$1",
        guild_id)
    turn_count = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
        guild_id, planet_id) or 0
    p_count    = await conn.fetchval(
        "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id) or 0
    e_count    = await conn.fetchval(
        "SELECT COUNT(*) FROM enemy_units "
        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
        guild_id, planet_id) or 0
    p_hexes    = await conn.fetchval(
        "SELECT COUNT(*) FROM hexes "
        "WHERE guild_id=$1 AND planet_id=$2 AND controller='players'",
        guild_id, planet_id) or 0
    e_hexes    = await conn.fetchval(
        "SELECT COUNT(*) FROM hexes "
        "WHERE guild_id=$1 AND planet_id=$2 AND controller='enemy'",
        guild_id, planet_id) or 0

    state = "ACTIVE" if cfg and cfg["game_started"] else "PAUSED"
    active_contracts = await conn.fetchval(
        "SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status IN ('deployable','active')",
        guild_id) or 0
    pending_contracts = await conn.fetchval(
        "SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status IN ('open','accepting','locked')",
        guild_id) or 0
    overview_lines = [
        kv("Active Theatre", planet["name"] if planet else "Unknown"),
        kv("Contractor", planet["contractor"] if planet else "Unknown"),
        kv("Enemy", planet["enemy_type"] if planet else "Unknown"),
        kv("Turn", turn_count),
        kv("Operational Tempo", f"{cfg['operational_tempo'] if cfg else 0}/{cfg['tempo_threshold'] if cfg else 500} `{progress_bar(cfg['operational_tempo'] if cfg else 0, cfg['tempo_threshold'] if cfg else 500)}`"),
        kv("Fleets Available", cfg["fleet_pool_available"] if cfg else 0),
        kv("Active Contracts", active_contracts),
        kv("Pending Contracts", pending_contracts),
    ]
    front_lines = [
        kv(theme.get("player_faction", "PMC"), f"{p_count} units | {p_hexes} sectors held"),
        kv(theme.get("enemy_faction", "Enemy"), f"{e_count} units | {e_hexes} sectors held"),
        kv("Status", status_label(state)),
    ]
    desc = (
        f"{format_section('Operational Status', overview_lines)}\n\n"
        f"{format_section('Front Line', front_lines)}\n\n"
        f"{transmission(theme.get('flavor_text', 'Awaiting command input.'))}"
    )
    return build_revenant_embed("System Overview", desc, "info")


async def update_menu_embed(bot, guild_id: int, conn):
    cfg = await conn.fetchrow(
        "SELECT reg_channel_id, reg_message_id FROM guild_config WHERE guild_id=$1", guild_id)
    if not cfg or not cfg["reg_channel_id"] or not cfg["reg_message_id"]:
        return
    channel = bot.get_channel(cfg["reg_channel_id"])
    if not channel:
        return
    try:
        msg   = await channel.fetch_message(cfg["reg_message_id"])
        theme = await get_theme(conn, guild_id)
        embed = await build_menu_embed(guild_id, conn, theme)
        await msg.edit(embed=embed, view=MainMenuView(guild_id))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Menu embed update failed: {e}")


# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
# ENLISTMENT BOARD
# â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

def _brigade_stats_line(stats: dict) -> str:
    return (
        f"ATK {stats['attack']:>2} | DEF {stats['defense']:>2} | "
        f"SPD {stats['speed']:>2} | MRL {stats['morale']:>2} | "
        f"SUP {stats['supply']:>2} | RCN {stats['recon']:>2}"
    )


def _brigade_brief_lines() -> list[str]:
    lines = []
    for data in BRIGADES.values():
        stats = _brigade_stats_line(data["stats"])
        specials = data.get("specials", [])
        hook = specials[0] if specials else "Standard line deployment."
        lines.append(f"**{data['name']}** - `{stats}`\n{hook}")
    return lines


def _build_brigade_dossier_embed(theme: dict) -> discord.Embed:
    embed = build_revenant_embed(
        "Deployment",
        format_section("Brigade Registry", ["Live unit archetypes available for enlistment."]),
        "deployment")
    for data in BRIGADES.values():
        stats = _brigade_stats_line(data["stats"])
        specials = "\n".join(f"- {text}" for text in data.get("specials", []))
        if not specials:
            specials = "- Standard line unit"
        embed.add_field(
            name=f"{data['emoji']} {data['name']}",
            value=f"{data['description']}\n```{stats}```{specials}",
            inline=False)
    return embed


def build_enlist_embed(theme: dict, planet_name: str, contractor: str,
                       enemy_type: str, operative_count: int, contract_name: str = None,
                       contract_status: str = None) -> discord.Embed:
    """Build the persistent enlistment board embed."""
    lines = [
        kv("Active Theatre", planet_name),
        kv("Contractor", contractor),
        kv("Enemy", enemy_type),
        kv("Selected Contract", contract_name or "Unassigned"),
        kv("Deployment Status", status_label(contract_status or "Standby")),
        kv("Accepted Players", f"{operative_count} enlisted"),
    ]
    desc = (
        f"{format_section('Deployment Intake', lines)}\n\n"
        f"{transmission(theme.get('flavor_text', 'Choose a brigade and await deployment orders.'))}"
    )
    return build_revenant_embed("Deployment", desc, "deployment")


class _UnitNameModal(discord.ui.Modal, title="Name Your Unit"):
    unit_name = discord.ui.TextInput(
        label="Unit Name",
        placeholder="e.g. Iron Wolves",
        max_length=40,
        required=True,
    )

    def __init__(self, guild_id: int, returning: bool = False, contract_id: int = None):
        super().__init__()
        self.guild_id = guild_id
        self.returning = returning
        self.contract_id = contract_id

    async def on_submit(self, i: discord.Interaction):
        from cogs.squadron_cog import BrigadePickerView, brigade_picker_embed
        name = str(self.unit_name).strip()
        embed = brigade_picker_embed(name, returning=self.returning)
        await i.response.send_message(embed=embed, view=BrigadePickerView(i.guild_id, name, self.contract_id), ephemeral=True)


class EnlistView(View):
    """Persistent view attached to the enlistment board message."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Enlist Now", style=discord.ButtonStyle.success,
                       custom_id="enlist_board_enlist")
    async def enlist_now(self, i: discord.Interaction, b: Button):
        await _send_contract_board(i)

    @discord.ui.button(label="Deploy", style=discord.ButtonStyle.primary,
                       custom_id="enlist_board_deploy")
    async def deploy_now(self, i: discord.Interaction, b: Button):
        await _send_contract_board(i)

    @discord.ui.button(label="Brigade Info", style=discord.ButtonStyle.secondary,
                       custom_id="enlist_board_brigades")
    async def brigade_info(self, i: discord.Interaction, b: Button):
        try:
            theme = {"color": 0xAA2222, "bot_name": "WARBOT"}
            try:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    theme = await get_theme(conn, i.guild_id)
            except Exception:
                pass
            embed = _build_brigade_dossier_embed(theme)
            await i.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await i.response.send_message(f"Error loading brigades: {e}", ephemeral=True)


async def refresh_enlist_counter(bot, guild_id: int, conn):
    """Update the persistent enlistment board with current theatre and roster data."""
    try:
        cfg = await conn.fetchrow(
            "SELECT enlist_channel_id, enlist_message_id, active_planet_id, "
            "contract_name, game_started "
            "FROM guild_config WHERE guild_id=$1", guild_id)
        if not cfg or not cfg["enlist_channel_id"] or not cfg["enlist_message_id"]:
            return
        channel = bot.get_channel(cfg["enlist_channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["enlist_message_id"])
        planet_id = cfg["active_planet_id"] or await get_active_planet_id(conn, guild_id)
        planet = await conn.fetchrow(
            "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
            guild_id, planet_id)
        count = await conn.fetchval(
            "SELECT COUNT(DISTINCT owner_id) FROM squadrons "
            "WHERE guild_id=$1",
            guild_id) or 0
        theme = await get_theme(conn, guild_id)
        embed = build_enlist_embed(
            theme,
            planet["name"]       if planet else "Unknown",
            planet["contractor"] if planet else "---",
            planet["enemy_type"] if planet else "---",
            count,
            cfg["contract_name"] if cfg and cfg["contract_name"] else "Unassigned",
            "Active" if cfg and cfg["game_started"] else "Standby",
        )
        await msg.edit(embed=embed, view=EnlistView(guild_id))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Enlist counter refresh failed: {e}")


async def refresh_contract_board(bot, guild_id: int, conn):
    """Update the persistent contract board, if configured."""
    try:
        cfg = await conn.fetchrow(
            "SELECT contract_board_channel_id, contract_board_message_id "
            "FROM guild_config WHERE guild_id=$1", guild_id)
        if not cfg or not cfg["contract_board_channel_id"] or not cfg["contract_board_message_id"]:
            return
        channel = bot.get_channel(cfg["contract_board_channel_id"])
        if not channel:
            return
        msg = await channel.fetch_message(cfg["contract_board_message_id"])
        theme = await get_theme(conn, guild_id)
        rows = await fetch_board_contracts(conn, guild_id)
        selected_id = rows[0]["id"] if rows else None
        embed = build_contract_board_embed(theme, rows, selected_id)
        await msg.edit(embed=embed, view=ContractBoardView(guild_id, rows, selected_id))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Contract board refresh failed: {e}")


async def refresh_public_panels(bot, guild_id: int, conn):
    """Refresh persistent public embeds that describe the current theatre."""
    await update_menu_embed(bot, guild_id, conn)
    await refresh_enlist_counter(bot, guild_id, conn)
    await refresh_contract_board(bot, guild_id, conn)
