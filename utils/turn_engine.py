"""
Turn engine — resolves one turn per guild per active planet.

Each turn:
  1. Process player transit
  2. Apply queued GM enemy moves
  3. AI moves / spawns enemy units
  4. Resolve combat on shared mid hexes
  5. Apply supply drain (off-FOB units)
  6. Stamp hex controllers for uncontested occupation
  7. Recompute outer hex statuses
  8. Cleanup + record turn
  9. Post after-action report
  10. Auto-update live map embed
"""

import asyncio
import logging
import random
from datetime import datetime, timezone

import discord

from utils.db import get_pool, get_theme, get_active_planet_id
from utils.combat import resolve_combat, CombatUnit
from utils.hexmap import (
    OUTER_COORDS, MID_OFFSETS,
    outer_key, mid_key, parse_mid,
    outer_neighbors, mid_neighbors,
    SAFE_HUB, STATUS_PLAYER, STATUS_ENEMY,
    recompute_hex_statuses,
)

log = logging.getLogger(__name__)

_SUPPLY_DRAIN   = 1
_SUPPLY_MIN     = 0
_MAX_SPAWNS     = 2   # max new enemy units per turn

# Enemy unit roster — drawn based on planet enemy_type
_UNIT_ROSTERS = {
    "AI Legion":        ["Scout-Form", "Heavy-Form", "Shepherd-Form", "Juggernaut", "Specter"],
    "Pirate Fleet":     ["Raider",     "Corsair",    "Marauder",      "Dreadnaught","Ghost Ship"],
    "Civil War Militia":["Infantry",   "Irregular",  "Heavy Militia", "Guerrilla",  "War Chief"],
    "Rogue Syndicate":  ["Enforcer",   "Vanguard",   "Assault Unit",  "Titan",      "Shadow Op"],
    "Xeno Collective":  ["Drone",      "Brood",      "Hunter",        "Titan-Form", "Apex"],
    "Unknown":          ["Unit Alpha", "Unit Beta",  "Unit Gamma",    "Unit Delta", "Unit Omega"],
}


def _get_roster(enemy_type: str) -> list:
    return _UNIT_ROSTERS.get(enemy_type, _UNIT_ROSTERS["Unknown"])


def _rand_stats(aggression: int = 0) -> dict:
    v = lambda: random.randint(-2, 2)
    base = 9 + aggression
    return dict(attack=base+v(), defense=base+v(), speed=base+v(),
                morale=base+v(), supply=base+v(), recon=base+v())


# ── FOB mid-hex helper ────────────────────────────────────────────────────────

def _fob_mid_hexes() -> list:
    """All mid-hex addresses inside the FOB outer hex."""
    oq, or_ = 0, 0
    return [mid_key(oq, or_, mq, mr) for mq, mr in MID_OFFSETS]


# ── Retreat finder ────────────────────────────────────────────────────────────

async def _find_retreat(conn, guild_id: int, planet_id: int, lost_addr: str) -> str | None:
    oq, or_, mq, mr = parse_mid(lost_addr)
    outer = outer_key(oq, or_)

    # 1. Another mid hex in the same outer sector
    candidates = await conn.fetch(
        "SELECT address, controller FROM hexes "
        "WHERE guild_id=$1 AND planet_id=$2 AND level=2 AND parent_address=$3 AND address!=$4",
        guild_id, planet_id, outer, lost_addr)
    safe = [r["address"] for r in candidates if r["controller"] in ("players", "neutral")]
    if safe:
        return random.choice(safe)

    # 2. Adjacent outer sector (not fully enemy)
    for noq, nor_ in outer_neighbors(oq, or_):
        nok = outer_key(noq, nor_)
        mids = await conn.fetch(
            "SELECT address, controller FROM hexes "
            "WHERE guild_id=$1 AND planet_id=$2 AND level=2 AND parent_address=$3",
            guild_id, planet_id, nok)
        safe = [r["address"] for r in mids if r["controller"] in ("players", "neutral")]
        if safe:
            return random.choice(safe)

    # 3. FOB
    fob_mids = await conn.fetch(
        "SELECT address, controller FROM hexes "
        "WHERE guild_id=$1 AND planet_id=$2 AND level=2 AND parent_address=$3",
        guild_id, planet_id, SAFE_HUB)
    safe = [r["address"] for r in fob_mids if r["controller"] in ("players", "neutral")]
    return random.choice(safe) if safe else None


# ── Turn engine ───────────────────────────────────────────────────────────────

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
                await self._tick_all_guilds()
            except Exception as e:
                log.error(f"Turn engine tick error: {e}", exc_info=True)
            await asyncio.sleep(60)

    async def _tick_all_guilds(self):
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
                    await self._resolve_turn(conn, g["guild_id"])

    async def _resolve_turn(self, conn, guild_id: int):
        theme     = await get_theme(conn, guild_id)
        planet_id = await get_active_planet_id(conn, guild_id)
        planet    = await conn.fetchrow(
            "SELECT name, enemy_type FROM planets WHERE guild_id=$1 AND id=$2",
            guild_id, planet_id)
        enemy_type   = planet["enemy_type"] if planet else "Unknown"
        planet_name  = planet["name"]       if planet else "Unknown"
        turn_row     = await conn.fetchval(
            "SELECT COUNT(*) FROM turn_history WHERE guild_id=$1 AND planet_id=$2",
            guild_id, planet_id)
        turn_number  = (turn_row or 0) + 1
        summaries    = []

        log.info(f"[Guild {guild_id}] Resolving turn {turn_number} on {planet_name}")

        async with conn.transaction():
            await self._process_transit(conn, guild_id, planet_id, summaries, theme)
            await self._apply_gm_moves(conn, guild_id, planet_id, summaries, theme, enemy_type)
            await self._enemy_ai(conn, guild_id, planet_id, summaries, theme, enemy_type)
            await self._resolve_combat(conn, guild_id, planet_id, turn_number, summaries, theme, enemy_type)
            await self._supply_drain(conn, guild_id, planet_id, summaries, theme)
            await self._apply_occupation(conn, guild_id, planet_id)
            await recompute_hex_statuses(conn, guild_id, planet_id)
            await conn.execute(
                "UPDATE enemy_units SET manually_moved=FALSE WHERE guild_id=$1 AND planet_id=$2",
                guild_id, planet_id)
            await conn.execute(
                "DELETE FROM enemy_gm_moves WHERE guild_id=$1 AND planet_id=$2",
                guild_id, planet_id)
            await conn.execute(
                "DELETE FROM enemy_units WHERE guild_id=$1 AND planet_id=$2 AND is_active=FALSE",
                guild_id, planet_id)
            await conn.execute(
                "INSERT INTO turn_history (guild_id, planet_id, turn_number) VALUES ($1,$2,$3)",
                guild_id, planet_id, turn_number)
            await conn.execute(
                "UPDATE guild_config SET last_turn_at=NOW() WHERE guild_id=$1", guild_id)

        await self._post_summary(guild_id, planet_id, planet_name, turn_number, summaries, theme)
        try:
            from cogs.map_cog import auto_update_map
            await auto_update_map(self.bot, guild_id)
        except Exception as e:
            log.warning(f"auto_update_map failed: {e}")

    # ── Transit ───────────────────────────────────────────────────────────────

    async def _process_transit(self, conn, guild_id, planet_id, summaries, theme):
        rows = await conn.fetch(
            "SELECT id, name, owner_name, hex_address, transit_destination, transit_step "
            "FROM squadrons WHERE guild_id=$1 AND planet_id=$2 AND in_transit=TRUE AND is_active=TRUE",
            guild_id, planet_id)
        ul = theme.get("player_unit", "Unit")
        for sq in rows:
            step = sq["transit_step"]
            dest = sq["transit_destination"]
            if step == 1:
                # Arrive at FOB waypoint
                fob_mid = mid_key(0, 0, 0, 0)
                await conn.execute(
                    "UPDATE squadrons SET hex_address=$1, transit_step=2 WHERE id=$2",
                    fob_mid, sq["id"])
                summaries.append(
                    f"🚶 **{sq['owner_name']}'s {sq['name']}** ({ul}) "
                    f"passed through FOB en route to `{dest}`.")
            elif step == 2:
                dest_outer = dest.split(":")[0]
                await conn.execute(
                    "UPDATE squadrons SET hex_address=$1, in_transit=FALSE, "
                    "transit_destination=NULL, transit_step=0 WHERE id=$2",
                    dest, sq["id"])
                summaries.append(
                    f"✅ **{sq['owner_name']}'s {sq['name']}** ({ul}) "
                    f"deployed to `{dest}`.")

    # ── GM moves ──────────────────────────────────────────────────────────────

    async def _apply_gm_moves(self, conn, guild_id, planet_id, summaries, theme, enemy_type):
        moves = await conn.fetch(
            "SELECT gm.enemy_unit_id, gm.target_address, eu.unit_type "
            "FROM enemy_gm_moves gm "
            "JOIN enemy_units eu ON eu.id = gm.enemy_unit_id "
            "WHERE gm.guild_id=$1 AND gm.planet_id=$2",
            guild_id, planet_id)
        eu = theme.get("enemy_unit", "Enemy Unit")
        for m in moves:
            await conn.execute(
                "UPDATE enemy_units SET hex_address=$1, manually_moved=TRUE WHERE id=$2",
                m["target_address"], m["enemy_unit_id"])
            summaries.append(
                f"🎮 **{eu} [{m['unit_type']}]** moved to `{m['target_address']}` (GM).")

    # ── Enemy AI ──────────────────────────────────────────────────────────────

    async def _enemy_ai(self, conn, guild_id, planet_id, summaries, theme, enemy_type):
        roster      = _get_roster(enemy_type)
        enemy_label = theme.get("enemy_faction", "Enemy")
        eu_label    = theme.get("enemy_unit",    "Enemy Unit")

        # Count existing units to gauge pressure
        total_enemies = await conn.fetchval(
            "SELECT COUNT(*) FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
            guild_id, planet_id) or 0

        # Spawn on outermost ring hexes (ring 3)
        outermost = [(q, r) for q, r in OUTER_COORDS
                     if max(abs(q), abs(r), abs(q+r)) == 3]
        spawn_candidates = []
        for oq, or_ in outermost:
            ok = outer_key(oq, or_)
            row = await conn.fetchrow(
                "SELECT controller FROM hexes "
                "WHERE guild_id=$1 AND planet_id=$2 AND level=1 AND address=$3",
                guild_id, planet_id, ok)
            if not row or row["controller"] != "players":
                spawn_candidates.append((oq, or_))

        spawned = 0
        for oq, or_ in random.sample(spawn_candidates, min(_MAX_SPAWNS, len(spawn_candidates))):
            mq, mr  = random.choice(MID_OFFSETS)
            addr    = mid_key(oq, or_, mq, mr)
            unit_t  = random.choice(roster)
            stats   = _rand_stats(aggression=min(total_enemies // 3, 4))
            await conn.execute(
                "INSERT INTO enemy_units "
                "(guild_id, planet_id, unit_type, hex_address, "
                " attack, defense, speed, morale, supply, recon, manually_moved) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,TRUE)",
                guild_id, planet_id, unit_t, addr,
                stats["attack"], stats["defense"], stats["speed"],
                stats["morale"], stats["supply"], stats["recon"])
            summaries.append(
                f"🔴 **{enemy_label} {eu_label} [{unit_t}]** appeared at `{addr}`.")
            spawned += 1

        # Move existing enemy units inward
        units = await conn.fetch(
            "SELECT id, hex_address, unit_type FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND manually_moved=FALSE",
            guild_id, planet_id)

        for unit in units:
            addr     = unit["hex_address"]
            oq, or_, mq, mr = parse_mid(addr)
            outer    = outer_key(oq, or_)

            # Don't enter FOB unless citadel_besieged
            cfg = await conn.fetchrow(
                "SELECT citadel_besieged FROM guild_config WHERE guild_id=$1", guild_id)
            besieged = cfg["citadel_besieged"] if cfg else False
            if outer == SAFE_HUB and not besieged:
                continue

            # Neighbours within same outer hex
            nbr_mids = mid_neighbors(mq, mr)
            candidates = [mid_key(oq, or_, nmq, nmr) for nmq, nmr in nbr_mids]

            # Also consider moving to an adjacent outer hex (edge mid hexes)
            # Simple heuristic: if unit is on the edge of its sector, try advancing inward
            dist = max(abs(oq), abs(or_), abs(oq + or_))
            if dist > 0:
                for noq, nor_ in outer_neighbors(oq, or_):
                    ndist = max(abs(noq), abs(nor_), abs(noq + nor_))
                    if ndist < dist:   # closer to FOB = inward
                        if outer_key(noq, nor_) == SAFE_HUB and not besieged:
                            continue
                        candidates.append(mid_key(noq, nor_, 0, 0))

            if not candidates:
                continue

            ctrl_rows = await conn.fetch(
                "SELECT address, controller FROM hexes "
                "WHERE guild_id=$1 AND planet_id=$2 AND address=ANY($3::text[])",
                guild_id, planet_id, candidates)
            ctrl_map  = {r["address"]: r["controller"] for r in ctrl_rows}
            # Prefer player hexes > neutral > enemy
            p_hexes  = [c for c in candidates if ctrl_map.get(c) == "players"]
            n_hexes  = [c for c in candidates if ctrl_map.get(c) == "neutral"]
            e_hexes  = [c for c in candidates if ctrl_map.get(c) == "enemy"]
            target   = (random.choice(p_hexes)  if p_hexes  else
                        random.choice(n_hexes)  if n_hexes  else
                        random.choice(e_hexes)  if e_hexes  else
                        random.choice(candidates))
            await conn.execute(
                "UPDATE enemy_units SET hex_address=$1 WHERE id=$2", target, unit["id"])

    # ── Combat ────────────────────────────────────────────────────────────────

    async def _resolve_combat(self, conn, guild_id, planet_id, turn_number,
                               summaries, theme, enemy_type):
        p_rows = await conn.fetch(
            "SELECT hex_address, owner_id, owner_name, name, "
            "attack, defense, speed, morale, supply, recon "
            "FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)
        e_rows = await conn.fetch(
            "SELECT id, hex_address, unit_type, "
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

        contested       = set(p_by_hex) & set(e_by_hex)
        pl              = theme.get("player_faction", "PMC")
        el              = theme.get("enemy_faction",  "Enemy")
        final_def_needed = False

        for hex_addr in contested:
            p_units = p_by_hex[hex_addr]
            e_units = e_by_hex[hex_addr]

            def avg(stat):
                return sum(u[stat] for u in p_units) // len(p_units)

            player_cu = CombatUnit(
                name=f"{pl} ({', '.join(u['name'] for u in p_units)})",
                side="players",
                attack=avg("attack"), defense=avg("defense"), speed=avg("speed"),
                morale=avg("morale"), supply=avg("supply"),  recon=avg("recon"),
            )

            fatigue       = 0
            player_routed = False
            final_ctrl    = "neutral"

            for e in e_units:
                enemy_cu = CombatUnit(
                    name=f"{el} [{e['unit_type']}]",
                    side="enemy",
                    attack=e["attack"], defense=e["defense"], speed=e["speed"],
                    morale=e["morale"], supply=e["supply"],  recon=e["recon"],
                    unit_type=e["unit_type"],
                )
                tired_player = CombatUnit(
                    name=player_cu.name, side="players",
                    attack=max(1, player_cu.attack  - fatigue * 2),
                    defense=player_cu.defense,
                    speed=player_cu.speed,
                    morale=max(1, player_cu.morale  - fatigue),
                    supply=player_cu.supply,
                    recon=player_cu.recon,
                )
                result = resolve_combat(tired_player, enemy_cu)
                await conn.execute(
                    "INSERT INTO combat_log "
                    "(guild_id, planet_id, turn_number, hex_address, "
                    " attacker, defender, attacker_roll, defender_roll, outcome) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    guild_id, planet_id, turn_number, hex_addr,
                    result.attacker, result.defender,
                    result.attacker_roll, result.defender_roll, result.outcome)
                summaries.append(f"⚔️ **{hex_addr}**: {result.narrative}")

                if result.outcome == "attacker_wins":
                    final_ctrl = "players"
                    await conn.execute(
                        "UPDATE enemy_units SET is_active=FALSE WHERE id=$1", e["id"])
                elif result.outcome == "defender_wins":
                    final_ctrl    = "enemy"
                    player_routed = True
                    fatigue      += 1
                    break
                else:
                    final_ctrl = "neutral"
                    fatigue   += 1

            await conn.execute(
                "UPDATE hexes SET controller=$1 "
                "WHERE guild_id=$2 AND planet_id=$3 AND address=$4",
                final_ctrl, guild_id, planet_id, hex_addr)

            if player_routed:
                retreat = await _find_retreat(conn, guild_id, planet_id, hex_addr)
                if retreat is None:
                    final_def_needed = True
                else:
                    sq_ids = await conn.fetch(
                        "SELECT id, name, owner_name FROM squadrons "
                        "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND hex_address=$3",
                        guild_id, planet_id, hex_addr)
                    for sq in sq_ids:
                        await conn.execute(
                            "UPDATE squadrons SET hex_address=$1 WHERE id=$2",
                            retreat, sq["id"])
                    summaries.append(
                        f"🔙 **{pl}** routed from `{hex_addr}` → fell back to `{retreat}`.")

        if not final_def_needed:
            outer_rows = await conn.fetch(
                "SELECT status FROM hexes "
                "WHERE guild_id=$1 AND planet_id=$2 AND level=1 AND address!=$3",
                guild_id, planet_id, SAFE_HUB)
            bad = {"enemy", "majority_enemy"}
            if outer_rows and all(r["status"] in bad for r in outer_rows):
                final_def_needed = True

        if final_def_needed:
            cfg = await conn.fetchrow(
                "SELECT citadel_besieged FROM guild_config WHERE guild_id=$1", guild_id)
            if not (cfg and cfg["citadel_besieged"]):
                await self._trigger_final_defense(conn, guild_id, planet_id, theme, summaries)

    async def _trigger_final_defense(self, conn, guild_id, planet_id, theme, summaries):
        log.warning(f"FINAL DEFENSE triggered guild={guild_id} planet={planet_id}")
        fob_mids = _fob_mid_hexes()
        actives  = await conn.fetch(
            "SELECT id, name, owner_name FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
            guild_id, planet_id)
        for sq in actives:
            dest = random.choice(fob_mids)
            await conn.execute(
                "UPDATE squadrons SET hex_address=$1, in_transit=FALSE, "
                "transit_destination=NULL, transit_step=0 WHERE id=$2",
                dest, sq["id"])
            summaries.append(
                f"🏰 **{sq['owner_name']}'s {sq['name']}** pulled back to FOB `{dest}`.")
        await conn.execute(
            "UPDATE guild_config SET citadel_besieged=TRUE WHERE guild_id=$1", guild_id)
        sz = theme.get("safe_zone", "FOB")
        ef = theme.get("enemy_faction", "Enemy")
        ul = theme.get("player_unit", "Unit")
        summaries.insert(0,
            f"☠️ **FINAL DEFENSE — {sz} UNDER SIEGE** — "
            f"The {ef} has overrun every sector. All {ul}s recalled to base.")

    # ── Supply drain ──────────────────────────────────────────────────────────

    async def _supply_drain(self, conn, guild_id, planet_id, summaries, theme):
        ul   = theme.get("player_unit", "Unit")
        rows = await conn.fetch(
            "SELECT id, name, owner_name, supply, hex_address FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)
        for sq in rows:
            if sq["hex_address"].split(":")[0] == SAFE_HUB:
                continue   # in FOB, no drain
            new_sup = max(_SUPPLY_MIN, sq["supply"] - _SUPPLY_DRAIN)
            await conn.execute(
                "UPDATE squadrons SET supply=$1 WHERE id=$2", new_sup, sq["id"])
            if new_sup <= 4:
                summaries.append(
                    f"⚠️ **{sq['owner_name']}'s {sq['name']}** ({ul}) "
                    f"critically low on supply (`{new_sup}`).")

    # ── Occupation ────────────────────────────────────────────────────────────

    async def _apply_occupation(self, conn, guild_id, planet_id):
        p_hexes = {r["hex_address"] for r in await conn.fetch(
            "SELECT hex_address FROM squadrons "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE AND in_transit=FALSE",
            guild_id, planet_id)}
        e_hexes = {r["hex_address"] for r in await conn.fetch(
            "SELECT hex_address FROM enemy_units "
            "WHERE guild_id=$1 AND planet_id=$2 AND is_active=TRUE",
            guild_id, planet_id)}
        for addr in p_hexes - e_hexes:
            await conn.execute(
                "UPDATE hexes SET controller='players' "
                "WHERE guild_id=$1 AND planet_id=$2 AND address=$3",
                guild_id, planet_id, addr)
        for addr in e_hexes - p_hexes:
            await conn.execute(
                "UPDATE hexes SET controller='enemy' "
                "WHERE guild_id=$1 AND planet_id=$2 AND address=$3",
                guild_id, planet_id, addr)

    # ── Summary post ──────────────────────────────────────────────────────────

    async def _post_summary(self, guild_id, planet_id, planet_name,
                             turn_number, summaries, theme):
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
            title=f"⚔️ Turn {turn_number} — {planet_name} After Action Report",
            color=theme.get("color", 0xAA2222),
            description=("\n".join(summaries) if summaries else "No activity this turn."),
        )
        embed.set_footer(
            text=f"{theme.get('bot_name','WARBOT')} — {theme.get('flavor_text','')}")
        await channel.send(embed=embed)
