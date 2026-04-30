"""
Discord-native UI views for Contract: ANOMALY.

Design rule:
- Embeds show information.
- Select menus choose objects/screens.
- Buttons execute actions.
- Images support the screen, but do not carry the whole UI.
"""

import discord
from discord.ui import View, Button

from utils.db import get_pool, get_theme, get_active_planet_id
from utils.brigades import BRIGADES

DIVIDER = "━━━━━━━━━━━━━━━━━━"
DEPLOYABLE_STATUSES = ("deployable", "active")
BOARD_STATUSES = ("open", "accepting", "locked", "deployable", "active")


# -----------------------------------------------------------------------------
# Small UI helpers
# -----------------------------------------------------------------------------

def _bot_name(theme: dict) -> str:
    return theme.get("bot_name", "REVENANT")


def _color(theme: dict, fallback: int = 0x2F3542) -> int:
    return int(theme.get("color", fallback) or fallback)


def _dots(value: int, cap: int = 8) -> str:
    value = max(0, int(value or 0))
    if value <= 0:
        return "0"
    return "●" * min(value, cap) + (f" +{value-cap}" if value > cap else "")


def _tempo_state(value: int, threshold: int) -> str:
    if threshold <= 0:
        return "Unknown"
    ratio = value / threshold
    if ratio >= 1:
        return "Critical"
    if ratio >= 0.66:
        return "Escalating"
    if ratio >= 0.33:
        return "Rising"
    return "Stable"


def _status_line(status: str) -> str:
    status = (status or "unknown").replace("_", " ").title()
    return status


def _capacity(contract) -> tuple[int, int, int]:
    fleets = int(contract["fleet_count"] or 0)
    capacity = int(contract["deployment_capacity"] or 0)
    deployed = int(contract["deployed_units"] or 0)
    return fleets, capacity, deployed


def _accepted_count(contract) -> int:
    try:
        return int(contract["accepted_count"] or 0)
    except Exception:
        return 0


def revenant_embed(title: str, theme: dict, description: str = "") -> discord.Embed:
    return discord.Embed(
        title=f"{_bot_name(theme)} | {title}",
        description=description[:4096],
        color=_color(theme),
    )


async def _safe(interaction: discord.Interaction, coro):
    try:
        await coro
    except Exception as e:
        msg = f"Action failed: {e}"
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass


async def _home_embed(guild_id: int, conn, theme: dict = None) -> discord.Embed:
    return await build_menu_embed(guild_id, conn, theme)


async def _edit_home(interaction: discord.Interaction):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, interaction.guild_id)
        embed = await build_menu_embed(interaction.guild_id, conn, theme)
    await interaction.response.edit_message(embed=embed, attachments=[], view=MainMenuView(interaction.guild_id))


# -----------------------------------------------------------------------------
# Home / command hub
# -----------------------------------------------------------------------------

class InterfaceSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Open interface...",
            min_values=1,
            max_values=1,
            custom_id="ca_open_interface",
            options=[
                discord.SelectOption(label="System Overview", value="system", description="Strategic theatre overview"),
                discord.SelectOption(label="Contract Board", value="contracts", description="Browse and accept contracts"),
                discord.SelectOption(label="Deployment", value="deployment", description="Deploy accepted forces"),
                discord.SelectOption(label="Tactical Map", value="map", description="Open the active tactical map"),
                discord.SelectOption(label="Intel Network", value="intel", description="View reports and theatre activity"),
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        if choice == "system":
            await _send_overview(interaction)
        elif choice == "contracts":
            await _send_contract_board(interaction)
        elif choice == "deployment":
            await _send_contract_board(interaction, deployment_hint=True)
        elif choice == "map":
            await _send_map(interaction)
        elif choice == "intel":
            await _send_intel(interaction)


class MainMenuView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(InterfaceSelect())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="ca_home_refresh", row=1)
    async def refresh(self, i: discord.Interaction, b: Button):
        await _safe(i, _edit_home(i))

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="ca_home_help", row=1)
    async def help(self, i: discord.Interaction, b: Button):
        await i.response.send_message(
            "Use the dropdown to open a screen. Use selects to choose contracts, theatres, or units. Use buttons to act.",
            ephemeral=True,
        )


async def build_menu_embed(guild_id: int, conn, theme: dict = None) -> discord.Embed:
    if theme is None:
        theme = await get_theme(conn, guild_id)
    planet_id = await get_active_planet_id(conn, guild_id)
    planet = await conn.fetchrow(
        "SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
        guild_id,
        planet_id,
    )
    cfg = await conn.fetchrow(
        """
        SELECT game_started, turn_interval_hours, contract_name,
               operational_tempo, tempo_threshold, fleet_pool_available
        FROM guild_config WHERE guild_id=$1
        """,
        guild_id,
    )
    turns = await conn.fetchval(
        "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
        guild_id,
        planet_id,
    ) or 0
    active_contracts = await conn.fetchval(
        "SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status IN ('deployable','active')",
        guild_id,
    ) or 0
    accepting_contracts = await conn.fetchval(
        "SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status IN ('open','accepting','locked')",
        guild_id,
    ) or 0
    tempo = int(cfg["operational_tempo"] if cfg else 0)
    threshold = int(cfg["tempo_threshold"] if cfg else 500)
    fleets = int(cfg["fleet_pool_available"] if cfg else 0)
    desc = (
        f"**Active Theatre:** {planet['name'] if planet else 'Unknown'}\n"
        f"**Enemy:** {planet['enemy_type'] if planet else 'Unknown'}\n"
        f"**Turn:** {turns}\n\n"
        f"{DIVIDER}\n"
        f"**Operational Tempo:** {tempo} / {threshold} ({_tempo_state(tempo, threshold)})\n"
        f"**Fleets Available:** {_dots(fleets)}\n"
        f"**Contracts:** {active_contracts} active · {accepting_contracts} board\n\n"
        f"Select an interface below."
    )
    embed = revenant_embed("Command Hub", theme, desc)
    embed.set_footer(text="Discord-native console: dropdowns choose, buttons act.")
    return embed


# -----------------------------------------------------------------------------
# System overview / tactical map / intel
# -----------------------------------------------------------------------------

async def _send_map(i: discord.Interaction):
    await i.response.defer(ephemeral=True, thinking=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        try:
            from utils.map_render import render_map_for_guild
            buf = await render_map_for_guild(i.guild_id, conn)
            f = discord.File(buf, filename="tactical_map.png")
            embed = revenant_embed("Tactical Map", theme, "Attached image shows the current tactical layer.")
            embed.set_image(url="attachment://tactical_map.png")
            await i.followup.send(embed=embed, file=f, view=BasicNavView(i.guild_id), ephemeral=True)
        except Exception as e:
            await i.followup.send(f"Map render failed: {e}", ephemeral=True)


async def _send_overview(i: discord.Interaction):
    await i.response.defer(ephemeral=True, thinking=True)
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        cfg = await conn.fetchrow(
            "SELECT operational_tempo, tempo_threshold, fleet_pool_available FROM guild_config WHERE guild_id=$1",
            i.guild_id,
        )
        try:
            from utils.map_render import render_overview_for_guild
            buf = await render_overview_for_guild(i.guild_id, conn)
            f = discord.File(buf, filename="system_overview.png")
            tempo = int(cfg["operational_tempo"] if cfg else 0)
            threshold = int(cfg["tempo_threshold"] if cfg else 500)
            fleets = int(cfg["fleet_pool_available"] if cfg else 0)
            desc = (
                f"**Fleets Available:** {_dots(fleets)}\n"
                f"**Operational Tempo:** {tempo} / {threshold} ({_tempo_state(tempo, threshold)})\n\n"
                f"{DIVIDER}\n"
                "Strategic theatre image attached."
            )
            embed = revenant_embed("System Overview", theme, desc)
            embed.set_image(url="attachment://system_overview.png")
            await i.followup.send(embed=embed, file=f, view=SystemOverviewView(i.guild_id), ephemeral=True)
        except Exception as e:
            await i.followup.send(f"Overview render failed: {e}", ephemeral=True)


async def _send_intel(i: discord.Interaction):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        active = await conn.fetchval("SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status='active'", i.guild_id) or 0
        accepting = await conn.fetchval("SELECT COUNT(*) FROM contracts WHERE guild_id=$1 AND status='accepting'", i.guild_id) or 0
    desc = (
        f"**Enemy Activity:** {'High' if active else 'Low'}\n"
        f"**Active Operations:** {active}\n"
        f"**Open Sign-ups:** {accepting}\n\n"
        f"{DIVIDER}\n"
        "Latest Report: Contract board activity is the current command priority."
    )
    embed = revenant_embed("Intel Network", theme, desc)
    await i.response.send_message(embed=embed, view=BasicNavView(i.guild_id), ephemeral=True)


class SystemOverviewView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="View Contracts", style=discord.ButtonStyle.primary, row=0)
    async def view_contracts(self, i: discord.Interaction, b: Button):
        await _send_contract_board(i)

    @discord.ui.button(label="Intel", style=discord.ButtonStyle.secondary, row=0)
    async def intel(self, i: discord.Interaction, b: Button):
        await _send_intel(i)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=1)
    async def home(self, i: discord.Interaction, b: Button):
        await _edit_home(i)


class BasicNavView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary)
    async def home(self, i: discord.Interaction, b: Button):
        await _edit_home(i)


# -----------------------------------------------------------------------------
# Contract Board
# -----------------------------------------------------------------------------

async def fetch_contract(conn, guild_id: int, contract_id: int):
    return await conn.fetchrow("SELECT * FROM contracts WHERE guild_id=$1 AND id=$2", guild_id, contract_id)


async def fetch_board_contracts(conn, guild_id: int, limit: int = 25):
    return await conn.fetch(
        """
        SELECT c.*, COUNT(ca.player_id)::INT AS accepted_count
        FROM contracts c
        LEFT JOIN contract_acceptances ca
          ON ca.guild_id=c.guild_id AND ca.contract_id=c.id
        WHERE c.guild_id=$1 AND c.status = ANY($2::text[])
        GROUP BY c.id
        ORDER BY c.created_at DESC, c.id DESC
        LIMIT $3
        """,
        guild_id,
        list(BOARD_STATUSES),
        limit,
    )


def build_contract_board_embed(theme: dict, rows, selected_id: int = None) -> discord.Embed:
    selected = None
    for row in rows or []:
        if selected_id == row["id"]:
            selected = row
            break
    if selected is None and rows:
        selected = rows[0]
        selected_id = selected["id"]

    if not selected:
        desc = "No contracts are currently posted on the board."
    else:
        fleets, capacity, deployed = _capacity(selected)
        accepted = _accepted_count(selected)
        desc = (
            f"**Currently Viewing**\n"
            f"**#{selected['id']:03d} — {selected['title']}**\n"
            f"**Theatre:** {selected['planet_system']}\n"
            f"**Enemy:** {selected['enemy']}\n"
            f"**Threat:** {selected['difficulty']}\n"
            f"**Status:** {_status_line(selected['status'])}\n\n"
            f"{DIVIDER}\n"
            f"**Fleets Assigned:** {_dots(fleets)}\n"
            f"**Deployment Capacity:** {deployed} / {capacity} units\n"
            f"**Accepted Players:** {accepted}\n\n"
            "Select a contract from the dropdown, then choose an action."
        )
    embed = revenant_embed("Contract Board", theme, desc)
    embed.set_footer(text="Contracts are chosen from the dropdown. Buttons perform actions on the selected contract.")
    return embed


class ContractSelect(discord.ui.Select):
    def __init__(self, rows, selected_id: int = None):
        options = []
        for c in (rows or [])[:25]:
            fleets, capacity, deployed = _capacity(c)
            options.append(
                discord.SelectOption(
                    label=f"#{c['id']:03d} {c['title']}"[:100],
                    value=str(c["id"]),
                    description=f"{_status_line(c['status'])} | fleets {fleets} | units {deployed}/{capacity}"[:100],
                    default=(selected_id == c["id"]),
                )
            )
        if not options:
            options = [discord.SelectOption(label="No contracts available", value="none", description="Ask a GM to post contracts.")]
        super().__init__(placeholder="Select contract...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("No contracts are available.", ephemeral=True)
            return
        selected_id = int(self.values[0])
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            rows = await fetch_board_contracts(conn, interaction.guild_id)
        embed = build_contract_board_embed(theme, rows, selected_id)
        await interaction.response.edit_message(embed=embed, view=ContractBoardView(interaction.guild_id, rows, selected_id))


class ContractBoardView(View):
    def __init__(self, guild_id: int, rows=None, selected_id: int = None):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.rows = rows or []
        self.selected_id = selected_id or (self.rows[0]["id"] if self.rows else None)
        self.add_item(ContractSelect(self.rows, self.selected_id))

    async def _refresh(self, interaction: discord.Interaction, *, message: str = None):
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, interaction.guild_id)
            rows = await fetch_board_contracts(conn, interaction.guild_id)
        embed = build_contract_board_embed(theme, rows, self.selected_id)
        view = ContractBoardView(interaction.guild_id, rows, self.selected_id)
        if interaction.response.is_done():
            if message:
                await interaction.followup.send(message, ephemeral=True)
            try:
                await interaction.edit_original_response(embed=embed, view=view)
            except Exception:
                pass
        else:
            await interaction.response.edit_message(embed=embed, view=view)
            if message:
                await interaction.followup.send(message, ephemeral=True)

    async def _selected_contract(self, interaction: discord.Interaction):
        if not self.selected_id:
            return None
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await fetch_contract(conn, interaction.guild_id, self.selected_id)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=1)
    async def accept_contract(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            if not c:
                await i.response.send_message("Contract not found.", ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("This contract is not accepting sign-ups.", ephemeral=True); return
            await conn.execute(
                "INSERT INTO contract_acceptances (guild_id, contract_id, player_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                i.guild_id, c["id"], i.user.id,
            )
        await i.response.send_message(f"Accepted contract #{self.selected_id:03d}.", ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.danger, row=1)
    async def withdraw_contract(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            if not c:
                await i.response.send_message("Contract not found.", ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("Sign-ups are locked for this contract.", ephemeral=True); return
            await conn.execute(
                "DELETE FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 AND player_id=$3",
                i.guild_id, c["id"], i.user.id,
            )
        await i.response.send_message(f"Withdrawn from contract #{self.selected_id:03d}.", ephemeral=True)

    @discord.ui.button(label="Details", style=discord.ButtonStyle.secondary, row=1)
    async def details(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            theme = await get_theme(conn, i.guild_id)
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            accepted = await conn.fetchval(
                "SELECT COUNT(*) FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2",
                i.guild_id, self.selected_id,
            ) or 0
        if not c:
            await i.response.send_message("Contract not found.", ephemeral=True); return
        fleets, capacity, deployed = _capacity(c)
        desc = (
            f"**#{c['id']:03d} — {c['title']}**\n"
            f"**Theatre:** {c['planet_system']}\n"
            f"**Enemy:** {c['enemy']}\n"
            f"**Difficulty:** {c['difficulty']}\n"
            f"**Status:** {_status_line(c['status'])}\n\n"
            f"{DIVIDER}\n"
            f"**Fleets Assigned:** {_dots(fleets)}\n"
            f"**Deployment Capacity:** {deployed} / {capacity}\n"
            f"**Accepted Players:** {accepted}\n\n"
            f"{DIVIDER}\n"
            f"**Mission Briefing**\n{c['description'] or 'No additional briefing has been filed.'}"
        )
        embed = revenant_embed("Contract Details", theme, desc)
        await i.response.edit_message(embed=embed, view=ContractDetailsView(i.guild_id, self.selected_id))

    @discord.ui.button(label="Participants", style=discord.ButtonStyle.secondary, row=1)
    async def participants(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT player_id FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 ORDER BY accepted_at",
                i.guild_id, self.selected_id,
            )
        if not rows:
            await i.response.send_message("No players have accepted this contract yet.", ephemeral=True); return
        mentions = "\n".join(f"<@{r['player_id']}>" for r in rows[:30])
        await i.response.send_message(f"Accepted players for #{self.selected_id:03d}:\n{mentions}", ephemeral=True)

    @discord.ui.button(label="Deploy", style=discord.ButtonStyle.primary, row=2)
    async def deploy_contract(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        from cogs.squadron_cog import open_returning_deploy
        await open_returning_deploy(i, self.selected_id)

    @discord.ui.button(label="New Unit", style=discord.ButtonStyle.success, row=2)
    async def new_unit_contract(self, i: discord.Interaction, b: Button):
        if not self.selected_id:
            await i.response.send_message("No contract selected.", ephemeral=True); return
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.selected_id)
            accepted = await conn.fetchval(
                "SELECT 1 FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 AND player_id=$3",
                i.guild_id, self.selected_id, i.user.id,
            )
        if not c or c["status"] not in DEPLOYABLE_STATUSES or not accepted:
            await i.response.send_message("You must accept a fleet-assigned deployable contract first.", ephemeral=True); return
        await i.response.send_modal(_UnitNameModal(i.guild_id, False, self.selected_id))

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, i: discord.Interaction, b: Button):
        await self._refresh(i)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=3)
    async def home(self, i: discord.Interaction, b: Button):
        await _edit_home(i)


class ContractDetailsView(View):
    def __init__(self, guild_id: int, contract_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.contract_id = contract_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, row=0)
    async def accept(self, i: discord.Interaction, b: Button):
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.contract_id)
            if not c:
                await i.response.send_message("Contract not found.", ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("This contract is not accepting sign-ups.", ephemeral=True); return
            await conn.execute(
                "INSERT INTO contract_acceptances (guild_id, contract_id, player_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                i.guild_id, self.contract_id, i.user.id,
            )
        await i.response.send_message(f"Accepted contract #{self.contract_id:03d}.", ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.danger, row=0)
    async def withdraw(self, i: discord.Interaction, b: Button):
        pool = await get_pool()
        async with pool.acquire() as conn:
            c = await fetch_contract(conn, i.guild_id, self.contract_id)
            if not c:
                await i.response.send_message("Contract not found.", ephemeral=True); return
            if c["status"] != "accepting":
                await i.response.send_message("Sign-ups are locked for this contract.", ephemeral=True); return
            await conn.execute(
                "DELETE FROM contract_acceptances WHERE guild_id=$1 AND contract_id=$2 AND player_id=$3",
                i.guild_id, self.contract_id, i.user.id,
            )
        await i.response.send_message(f"Withdrawn from contract #{self.contract_id:03d}.", ephemeral=True)

    @discord.ui.button(label="Deploy Forces", style=discord.ButtonStyle.primary, row=1)
    async def deploy(self, i: discord.Interaction, b: Button):
        from cogs.squadron_cog import open_returning_deploy
        await open_returning_deploy(i, self.contract_id)

    @discord.ui.button(label="View Board", style=discord.ButtonStyle.secondary, row=2)
    async def board(self, i: discord.Interaction, b: Button):
        await _send_contract_board(i, selected_id=self.contract_id)

    @discord.ui.button(label="Home", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, i: discord.Interaction, b: Button):
        await _edit_home(i)


async def _send_contract_board(i: discord.Interaction, deployment_hint: bool = False, selected_id: int = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        theme = await get_theme(conn, i.guild_id)
        rows = await fetch_board_contracts(conn, i.guild_id)
    if not rows:
        embed = build_contract_board_embed(theme, [], None)
        await i.response.send_message(embed=embed, view=ContractBoardView(i.guild_id, [], None), ephemeral=True)
        return
    selected_id = selected_id or rows[0]["id"]
    embed = build_contract_board_embed(theme, rows, selected_id)
    if deployment_hint:
        embed.set_footer(text="Select a contract, then use Deploy or New Unit.")
    await i.response.send_message(embed=embed, view=ContractBoardView(i.guild_id, rows, selected_id), ephemeral=True)


# -----------------------------------------------------------------------------
# Enlistment board
# -----------------------------------------------------------------------------

def _brigade_stats_line(stats: dict) -> str:
    return (
        f"ATK {stats['attack']:>2} | DEF {stats['defense']:>2} | "
        f"SPD {stats['speed']:>2} | MRL {stats['morale']:>2} | "
        f"SUP {stats['supply']:>2} | RCN {stats['recon']:>2}"
    )


def _build_brigade_dossier_embed(theme: dict) -> discord.Embed:
    embed = revenant_embed(
        "Brigade Dossier",
        theme,
        "Live brigade registry. Stats are synced with enlistment and deployment.",
    )
    for key, data in BRIGADES.items():
        stats = _brigade_stats_line(data["stats"])
        specials = "\n".join(f"- {text}" for text in data.get("specials", [])) or "- Standard line unit"
        name = data.get("name", key.title())
        embed.add_field(name=name, value=f"{data.get('description','')}\n```{stats}```{specials}", inline=False)
    return embed


def build_enlist_embed(theme: dict, planet_name: str, contractor: str, enemy_type: str,
                       operative_count: int, contract_name: str = None,
                       contract_status: str = None) -> discord.Embed:
    desc = (
        f"**Theatre:** {planet_name}\n"
        f"**Contractor:** {contractor}\n"
        f"**Enemy:** {enemy_type}\n"
        f"**Contract:** {contract_name or 'Unassigned'}\n"
        f"**Status:** {contract_status or 'Standby'}\n\n"
        f"{DIVIDER}\n"
        f"**Commandants Registered:** {operative_count}\n\n"
        "Use the board to accept a contract before deploying."
    )
    embed = revenant_embed("Enlistment Board", theme, desc)
    embed.set_footer(text="Enlistment opens the contract board so deployments target a specific contract.")
    return embed


class _UnitNameModal(discord.ui.Modal, title="Name Your Unit"):
    unit_name = discord.ui.TextInput(label="Unit Name", placeholder="e.g. Iron Wolves", max_length=40, required=True)

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
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="Open Contract Board", style=discord.ButtonStyle.primary, custom_id="enlist_board_contracts", row=0)
    async def enlist_now(self, i: discord.Interaction, b: Button):
        await _send_contract_board(i)

    @discord.ui.button(label="Brigade Info", style=discord.ButtonStyle.secondary, custom_id="enlist_board_brigades", row=0)
    async def brigade_info(self, i: discord.Interaction, b: Button):
        theme = {"color": 0x2F3542, "bot_name": "REVENANT"}
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                theme = await get_theme(conn, i.guild_id)
        except Exception:
            pass
        await i.response.send_message(embed=_build_brigade_dossier_embed(theme), ephemeral=True)


# -----------------------------------------------------------------------------
# Public panel refresh helpers
# -----------------------------------------------------------------------------

async def update_menu_embed(bot, guild_id: int, conn):
    cfg = await conn.fetchrow("SELECT reg_channel_id, reg_message_id FROM guild_config WHERE guild_id=$1", guild_id)
    if not cfg or not cfg["reg_channel_id"] or not cfg["reg_message_id"]:
        return
    channel = bot.get_channel(cfg["reg_channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(cfg["reg_message_id"])
        theme = await get_theme(conn, guild_id)
        embed = await build_menu_embed(guild_id, conn, theme)
        await msg.edit(embed=embed, view=MainMenuView(guild_id))
    except Exception:
        pass


async def refresh_enlist_counter(bot, guild_id: int, conn):
    cfg = await conn.fetchrow(
        "SELECT enlist_channel_id, enlist_message_id, active_planet_id, contract_name, game_started FROM guild_config WHERE guild_id=$1",
        guild_id,
    )
    if not cfg or not cfg["enlist_channel_id"] or not cfg["enlist_message_id"]:
        return
    channel = bot.get_channel(cfg["enlist_channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(cfg["enlist_message_id"])
        planet_id = cfg["active_planet_id"] or await get_active_planet_id(conn, guild_id)
        planet = await conn.fetchrow("SELECT name, contractor, enemy_type FROM planets WHERE guild_id=$1 AND id=$2", guild_id, planet_id)
        count = await conn.fetchval("SELECT COUNT(DISTINCT owner_id) FROM squadrons WHERE guild_id=$1", guild_id) or 0
        theme = await get_theme(conn, guild_id)
        embed = build_enlist_embed(
            theme,
            planet["name"] if planet else "Unknown",
            planet["contractor"] if planet else "---",
            planet["enemy_type"] if planet else "---",
            count,
            cfg["contract_name"] if cfg and cfg["contract_name"] else "Unassigned",
            "Active" if cfg and cfg["game_started"] else "Standby",
        )
        await msg.edit(embed=embed, view=EnlistView(guild_id))
    except Exception:
        pass


async def refresh_contract_board(bot, guild_id: int, conn):
    cfg = await conn.fetchrow(
        "SELECT contract_board_channel_id, contract_board_message_id FROM guild_config WHERE guild_id=$1",
        guild_id,
    )
    if not cfg or not cfg["contract_board_channel_id"] or not cfg["contract_board_message_id"]:
        return
    channel = bot.get_channel(cfg["contract_board_channel_id"])
    if not channel:
        return
    try:
        msg = await channel.fetch_message(cfg["contract_board_message_id"])
        theme = await get_theme(conn, guild_id)
        rows = await fetch_board_contracts(conn, guild_id)
        selected_id = rows[0]["id"] if rows else None
        embed = build_contract_board_embed(theme, rows, selected_id)
        await msg.edit(embed=embed, view=ContractBoardView(guild_id, rows, selected_id))
    except Exception:
        pass


async def refresh_public_panels(bot, guild_id: int, conn):
    await update_menu_embed(bot, guild_id, conn)
    await refresh_enlist_counter(bot, guild_id, conn)
    await refresh_contract_board(bot, guild_id, conn)
