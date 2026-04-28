"""
Warbot — Turn Engine v3
Brigade-aware, flat hex system, no FOB concept.

Each turn:
  1. Process transit (brigade-specific turn counts)
  2. Apply GM enemy moves
  3. Enemy AI moves / spawns
  4. Resolve combat (brigade modifiers)
  5. Apply supply drain (brigade-specific rates)
  6. Recompute hex statuses
  7. Reset per-turn flags (dug_in, artillery_armed)
  8. Cleanup + record + post summary
  9. Auto-update map
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

import discord

from utils.db import get_pool, get_theme, get_active_planet_id
from utils.combat import resolve_combat, CombatUnit
from utils.brigades import (
    transit_turns as brigade_transit, supply_drain as brigade_drain,
    move_steps as brigade_steps, can_direct_insert,
)
from utils.hexmap import (
    GRID_COORDS, GRID_SET,
    hex_key, parse_hex, hex_neighbors, hex_distance, is_valid,
    hex_ring_keys, nearest_hex, step_toward,
    outermost_hexes, recompute_statuses,
    STATUS_PLAYER, STATUS_ENEMY,
)

log = logging.getLogger(__name__)

_UNIT_ROSTERS = {
    "AI Legion":         ["Scout-Form", "Heavy-Form", "Shepherd", "Juggernaut", "Specter"],
    "Pirate Fleet":      ["Raider",     "Corsair",    "Marauder",  "Dreadnaught","Ghost Ship"],
    "Civil War Militia": ["Infantry",   "Irregular",  "Heavy Mil", "Guerrilla",  "War Chief"],
    "Rogue Syndicate":   ["Enforcer",   "Vanguard",   "Assault",   "Titan",      "Shadow Op"],
    "Xeno Collective":   ["Drone",      "Brood",      "Hunter",    "Titan-Form", "Apex"],
    "Unknown":           ["Unit-A",     "Unit-B",     "Unit-C",    "Unit-D",     "Unit-E"],
}

_MAX_SPAWNS = 2


def _roster(enemy_type: str) -> list:
    return _UNIT_ROSTERS.get(enemy_type, _UNIT_ROSTERS["Unknown"])


def _rand_stats(aggression: int = 0) -> dict:
    v = lambda: random.randint(-2, 2)
    b = 9 + aggression
    return dict(attack=b+v(), defense=b+v(), speed=b+v(),
                morale=b+v(), supply=b+v(), recon=b+v())


class TurnEngine:
    def __init__(self, bot):
        self.bot   = bot
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while True:
            try:
                await self._tick_all()
            except Exception as e:
                log.error(f"Turn engine error: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def _tick_all(self):
        pool = await get_pool()
        async with pool.acquire() as conn:
            guilds = await conn.fetch(
                "SELECT guild_id, turn_interval_hours, last_turn_at, game_started "
                "FROM guild_config")
        now = datetime.now(timezone.utc)
        for g in guilds:
            if not g["game_started"]:
                continue
            elapsed = (now - g["last_turn_at"].replace(tzinfo=timezone.utc)).total_seconds()
            if elapsed / 3600 >= g["turn_interval_hours"]:
                pool = await get_pool()
                async with pool.acquire() as conn:
                    await self._resolve(conn, g["guild_id"])

    async def _resolve(self, conn, guild_id: int):
        theme     = await get_theme(conn, guild_id)
        planet_id = await get_active_planet_id(conn, guild_id)
        planet    = await conn.fetchrow(
            "SELECT name, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
            guild_id, planet_id)
        enemy_type  = planet["enemy_type"] if planet else "Unknown"
        planet_name = planet["name"]       if planet else "Unknown"
        turn_num    = (await conn.fetchval(
            "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
            guild_id, planet_id) or 0) + 1
        summaries = []
        # movement_arrows: list of (from_addr, to_addr, "player"|"enemy")
        movement_arrows: list = []

        log.info(f"[{guild_id}] Turn {turn_num} on {planet_name}")

        async with conn.transaction():
            await self._transit(conn, guild_id, planet_id, summaries, theme, movement_arrows)
            await self._gm_moves(conn, guild_id, planet_id, summaries, theme)
            await self._enemy_ai(conn, guild_id, planet_id, summaries, theme, enemy_type, movement_arrows)
            await self._combat(conn, guild_id, planet_id, turn_num, summaries, theme, enemy_type, movement_arrows)
            await self._supply(conn, guild_id, planet_id, summaries, theme)
            await recompute_statuses(conn, guild_id, planet_id)

            # Reset per-turn flags
            await conn.execute(
                "UPDATE squadrons SET is_dug_in=FALSE, artillery_armed=FALSE, "
                "hexes_moved_this_turn=0 "
                "WHERE guild_id=$1 AND planet_id=$2",
                guild_id, planet_id)
            await conn.execute(
                "UPDATE enemy_units SET manually_moved=FALSE "
                "WHERE guild_id=$1 AND planet_id=$2", guild_id, planet_id)
            await conn.execute(
                "DELETE FROM enemy_gm_moves WHERE guild_id=$1 AND planet_id=$2",
                guild_id, planet_id)
            await conn.execute(
                "DELETE FROM enemy_units WHERE guild_id=$1 AND planet_id=$2 AND is_active=FALSE",
                guild_id, planet_id)
            await conn.execute(
                "INSERT INTO turn_history (guild_id, planet_id, turn_number) VALUES ($1,$2,$3)",
                guild_id, planet_id, turn_num)
            await conn.execute(
                "UPDATE guild_config SET last_turn_at=NOW() WHERE guild_id=$1", guild_id)

        await self._post(guild_id, planet_name, turn_num, summaries, theme)
        try:
            from cogs.map_cog import auto_update_map
            await auto_update_map(self.bot, guild_id, movement_arrows=movement_arrows)
        except Exception as e:
            log.warning(f"auto_update_map: {e}")

        # Clear persisted player movement arrows — new turn = blank slate
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM movement_arrows WHERE guild_id=$1 AND planet_id=$2",
                    guild_id, planet_id)
        except Exception as e:
            log.warning(f"movement_arrows clear: {e}")

    # ── Transit ───────────────────────────────────────────────────────────────

    async def _transit(self, conn, guild_id, planet_id, summaries, theme, movement_arrows):
        rows = await conn.fetch(
            "SELECT id, name, owner_name, brigade, hex_address, "
            "transit_destination, transit_turns_left "
            "FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND in_transit=TRUE AND is_active=TRUE",
            guild_id, planet_id)
        ul = theme.get("player_unit", "Unit")
        for sq in rows:
            turns_left = sq["transit_turns_left"] - 1
            dest       = sq["transit_destination"]
            if turns_left <= 0:
                movement_arrows.append((sq["hex_address"], dest, "player"))
                await conn.execute(
                    "UPDATE squadrons SET hex_address=$1, in_transit=FALSE, "
                    "transit_destination=NULL, transit_turns_left=0 WHERE id=$2",
                    dest, sq["id"])
                summaries.append(
                    f"✅ **{sq['owner_name']}'s {sq['name']}** ({ul}) "
                    f"arrived at `{dest}`.")
            else:
                # Step one hex closer each turn
                next_hex = step_toward(sq["hex_address"], dest)
                if next_hex != sq["hex_address"]:
                    movement_arrows.append((sq["hex_address"], next_hex, "player"))
                await conn.execute(
                    "UPDATE squadrons SET hex_address=$1, transit_turns_left=$2 WHERE id=$3",
                    next_hex, turns_left, sq["id"])

    # ── GM moves ──────────────────────────────────────────────────────────────

    async def _gm_moves(self, conn, guild_id, planet_id, summaries, theme):
        moves = await conn.fetch(
            "SELECT gm.enemy_unit_id, gm.target_address, eu.unit_type "
            "FROM enemy_gm_moves gm "
            "JOIN enemy_units eu ON eu.id=gm.enemy_unit_id "
            "WHERE gm.guild_id=$1 AND gm.planet_id=$2", guild_id, planet_id)
        for m in moves:
            await conn.execute(
                "UPDATE enemy_units SET hex_address=$1, manually_moved=TRUE WHERE id=$2",
                m["target_address"], m["enemy_unit_id"])
            summaries.append(
                f"🎮 **{theme.get('enemy_unit','Enemy')} [{m['unit_type']}]** "
                f"moved to `{m['target_address']}` (GM).")

    # ── Enemy AI ──────────────────────────────────────────────────────────────

    async def _enemy_ai(self, conn, guild_id, planet_id, summaries, theme, enemy_type, movement_arrows):
        # AI spawning has been removed — only GMs may spawn enemy units.
        # Existing units (not GM-moved this turn) move toward player positions automatically.
        units = await conn.fetch(
            "SELECT id, hex_address FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND manually_moved=FALSE",
            guild_id, planet_id)
        p_hexes = [r["hex_address"] for r in await conn.fetch(
            "SELECT hex_address FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)]

        for unit in units:
            addr  = unit["hex_address"]
            nbrs  = hex_neighbors(addr)
            if not nbrs:
                continue
            if p_hexes:
                target_p = nearest_hex(addr, p_hexes)
                target   = step_toward(addr, target_p)
            else:
                target = step_toward(addr, "0,0")
            if target != addr:
                movement_arrows.append((addr, target, "enemy"))
                await conn.execute(
                    "UPDATE enemy_units SET hex_address=$1 WHERE id=$2",
                    target, unit["id"])

    # ── Combat ────────────────────────────────────────────────────────────────

    async def _combat(self, conn, guild_id, planet_id, turn_num,
                       summaries, theme, enemy_type, movement_arrows):
        p_rows = await conn.fetch(
            "SELECT id, hex_address, owner_id, owner_name, name, brigade, "
            "attack, defense, speed, morale, supply, recon, is_dug_in, artillery_armed, hp "
            "FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)
        e_rows = await conn.fetch(
            "SELECT id, hex_address, unit_type, hp, "
            "attack, defense, speed, morale, supply, recon "
            "FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
            guild_id, planet_id)

        p_by_hex: dict = {}
        for p in p_rows:
            p_by_hex.setdefault(p["hex_address"], []).append(p)
        e_by_hex: dict = {}
        for e in e_rows:
            e_by_hex.setdefault(e["hex_address"], []).append(e)

        pl = theme.get("player_faction", "PMC")
        el = theme.get("enemy_faction",  "Enemy")

        contested = set(p_by_hex) & set(e_by_hex)

        for hex_addr in contested:
            p_units = p_by_hex[hex_addr]
            e_units = e_by_hex[hex_addr]

            def avg(stat):
                return sum(u[stat] for u in p_units) // len(p_units)

            # Representative brigade — use the first unit's brigade
            rep_brigade = p_units[0]["brigade"] if p_units else "infantry"

            # Fetch current HP for all player units fresh from DB
            p_hp_map = {}
            for pu in p_units:
                cur_hp = await conn.fetchval(
                    "SELECT hp FROM squadrons WHERE id=$1", pu["id"]) or 100
                p_hp_map[pu["id"]] = cur_hp

            avg_player_hp = sum(p_hp_map.values()) // max(1, len(p_hp_map))

            player_cu = CombatUnit(
                name=f"{pl} ({', '.join(u['name'] for u in p_units)})",
                side="players",
                attack=avg("attack"), defense=avg("defense"), speed=avg("speed"),
                morale=avg("morale"), supply=avg("supply"),  recon=avg("recon"),
                brigade=rep_brigade,
                is_dug_in=any(u["is_dug_in"] for u in p_units),
                artillery_armed=any(u["artillery_armed"] for u in p_units),
                hp=avg_player_hp,
            )

            fatigue       = 0
            player_routed = False
            final_ctrl    = "neutral"

            for e in e_units:
                # Fetch fresh enemy HP
                cur_enemy_hp = await conn.fetchval(
                    "SELECT hp FROM enemy_units WHERE id=$1", e["id"]) or 100
                if cur_enemy_hp <= 0:
                    # Already dead from earlier in this loop
                    continue

                # Artillery splash: find enemy hexes adjacent to hex_addr
                adj_enemy = []
                if rep_brigade == "artillery" and player_cu.artillery_armed:
                    for nb in hex_neighbors(hex_addr):
                        nb_row = await conn.fetchrow(
                            "SELECT id FROM enemy_units "
                            "WHERE guild_id=$1 AND planet_id=$2 AND hex_address=$3 AND is_active=TRUE",
                            guild_id, planet_id, nb)
                        if nb_row:
                            adj_enemy.append(nb)

                enemy_cu = CombatUnit(
                    name=f"{el} [{e['unit_type']}]",
                    side="enemy",
                    attack=e["attack"], defense=e["defense"], speed=e["speed"],
                    morale=e["morale"], supply=e["supply"],  recon=e["recon"],
                    hp=cur_enemy_hp,
                )
                tired = CombatUnit(
                    name=player_cu.name, side="players",
                    attack=max(1, player_cu.attack  - fatigue*2),
                    defense=player_cu.defense,
                    speed=player_cu.speed,
                    morale=max(1, player_cu.morale  - fatigue),
                    supply=player_cu.supply, recon=player_cu.recon,
                    brigade=rep_brigade,
                    is_dug_in=player_cu.is_dug_in,
                    artillery_armed=player_cu.artillery_armed,
                    hp=avg_player_hp,
                )
                result = resolve_combat(
                    tired, enemy_cu,
                    attacker_hex=hex_addr,
                    adjacent_enemy_hexes=adj_enemy,
                )
                await conn.execute(
                    "INSERT INTO combat_log "
                    "(guild_id, planet_id, turn_number, hex_address, "
                    " attacker, defender, attacker_roll, defender_roll, outcome) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    guild_id, planet_id, turn_num, hex_addr,
                    result.attacker, result.defender,
                    result.attacker_roll, result.defender_roll, result.outcome)
                summaries.append(f"⚔ **{hex_addr}**: {result.narrative}")

                # ── Apply HP damage to enemy ──────────────────────────────────
                if result.defender_damage > 0:
                    new_enemy_hp = max(0, cur_enemy_hp - result.defender_damage)
                    if new_enemy_hp <= 0:
                        await conn.execute(
                            "UPDATE enemy_units SET hp=0, is_active=FALSE WHERE id=$1",
                            e["id"])
                        summaries.append(
                            f"💀 **{el} [{e['unit_type']}]** was **destroyed** at `{hex_addr}`.")
                    else:
                        await conn.execute(
                            "UPDATE enemy_units SET hp=$1 WHERE id=$2",
                            new_enemy_hp, e["id"])
                        summaries.append(
                            f"🩸 **{el} [{e['unit_type']}]** `{new_enemy_hp} HP` remaining.")

                # ── Apply HP damage to player units ───────────────────────────
                if result.attacker_damage > 0:
                    for pu in p_units:
                        cur_hp = p_hp_map[pu["id"]]
                        new_hp = max(0, cur_hp - result.attacker_damage)
                        p_hp_map[pu["id"]] = new_hp
                        # Recalculate avg for next round
                        avg_player_hp = sum(p_hp_map.values()) // max(1, len(p_hp_map))
                        if new_hp <= 0:
                            await conn.execute(
                                "UPDATE squadrons SET hp=0, is_active=FALSE WHERE id=$1",
                                pu["id"])
                            summaries.append(
                                f"💀 **{pu['owner_name']}'s {pu['name']}** was **destroyed** "
                                f"— they can re-enlist next contract.")
                        else:
                            await conn.execute(
                                "UPDATE squadrons SET hp=$1 WHERE id=$2",
                                new_hp, pu["id"])
                            summaries.append(
                                f"💔 **{pu['owner_name']}'s {pu['name']}** took "
                                f"**{result.attacker_damage} damage** (`{new_hp} HP` remaining).")

                # ── Determine hex control and routing ─────────────────────────
                # Routing only triggers when the winning roll is >= 10
                if result.outcome == "attacker_wins":
                    final_ctrl = "players"
                    if result.attacker_roll >= 10:
                        # Enemy routed — mark it (already dead or retreating handled by HP)
                        pass
                elif result.outcome == "defender_wins":
                    final_ctrl = "enemy"
                    fatigue   += 1
                    if result.defender_roll >= 10:
                        player_routed = True
                        break
                else:
                    final_ctrl = "neutral"
                    fatigue   += 1

                # Artillery splash damage — deal fixed 10 HP to splashed enemy units
                if result.splash_hexes:
                    for sh in result.splash_hexes:
                        splash_rows = await conn.fetch(
                            "SELECT id, unit_type, hp FROM enemy_units "
                            "WHERE guild_id=$1 AND planet_id=$2 AND hex_address=$3 AND is_active=TRUE",
                            guild_id, planet_id, sh)
                        for sr in splash_rows:
                            splash_new_hp = max(0, (sr["hp"] or 100) - 10)
                            if splash_new_hp <= 0:
                                await conn.execute(
                                    "UPDATE enemy_units SET hp=0, is_active=FALSE WHERE id=$1",
                                    sr["id"])
                                summaries.append(
                                    f"💥 Artillery splash destroyed **{el} [{sr['unit_type']}]** at `{sh}`.")
                            else:
                                await conn.execute(
                                    "UPDATE enemy_units SET hp=$1 WHERE id=$2",
                                    splash_new_hp, sr["id"])
                                summaries.append(
                                    f"💥 Artillery splash hit **{el} [{sr['unit_type']}]** at `{sh}` "
                                    f"(`{splash_new_hp} HP` remaining).")

            await conn.execute(
                "UPDATE hexes SET controller=$1, status=$1 "
                "WHERE guild_id=$2 AND planet_id=$3 AND address=$4",
                final_ctrl, guild_id, planet_id, hex_addr)

            if player_routed:
                # Retreat to nearest friendly/neutral hex
                all_hexes = await conn.fetch(
                    "SELECT address, controller FROM hexes "
                    "WHERE guild_id=$1 AND planet_id=$2",
                    guild_id, planet_id)
                candidates = [r["address"] for r in all_hexes
                              if r["controller"] in ("players", "neutral")
                              and r["address"] != hex_addr
                              and is_valid(r["address"])]
                retreat = nearest_hex(hex_addr, candidates) if candidates else None
                if retreat:
                    sq_ids = await conn.fetch(
                        "SELECT id, name, owner_name FROM squadrons "
                        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE "
                        "AND hex_address=$3",
                        guild_id, planet_id, hex_addr)
                    for sq in sq_ids:
                        await conn.execute(
                            "UPDATE squadrons SET hex_address=$1 WHERE id=$2",
                            retreat, sq["id"])
                    movement_arrows.append((hex_addr, retreat, "player"))
                    summaries.append(
                        f"🔙 **{pl}** routed from `{hex_addr}` → fell back to `{retreat}`.")

    # ── Supply drain ──────────────────────────────────────────────────────────

    async def _supply(self, conn, guild_id, planet_id, summaries, theme):
        ul   = theme.get("player_unit", "Unit")
        rows = await conn.fetch(
            "SELECT id, name, owner_name, brigade, supply FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)
        for sq in rows:
            drain     = brigade_drain(sq["brigade"])
            new_supply = max(0, sq["supply"] - drain)
            await conn.execute(
                "UPDATE squadrons SET supply=$1 WHERE id=$2", new_supply, sq["id"])
            if new_supply <= 3:
                summaries.append(
                    f"⚠ **{sq['owner_name']}'s {sq['name']}** ({ul}) "
                    f"critically low on supply (`{new_supply}`).")

    # ── Post summary ──────────────────────────────────────────────────────────

    async def _post(self, guild_id, planet_name, turn_num, summaries, theme):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
        pool = await get_pool()
        async with pool.acquire() as conn:
            cfg = await conn.fetchrow(
                "SELECT report_channel_id FROM guild_config WHERE guild_id=$1", guild_id)
        channel = None
        if cfg and cfg["report_channel_id"]:
            channel = guild.get_channel(cfg["report_channel_id"])
        if not channel:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    break
        if not channel:
            return
        embed = discord.Embed(
            title=f"⚔ Turn {turn_num} — {planet_name} After Action Report",
            color=theme.get("color", 0xAA2222),
            description="\n".join(summaries) if summaries else "No activity this turn.",
        )
        embed.set_footer(
            text=f"{theme.get('bot_name','WARBOT')} — {theme.get('flavor_text','')}")
        await channel.send(embed=embed)
