import os
import asyncpg
from typing import Optional

_pool: Optional[asyncpg.Pool] = None

DEFAULT_PLANETS = [
    {"name": "Terra Prime",  "contractor": "United Commonwealth of Terra",  "enemy_type": "AI Legion",        "sort_order": 1},
    {"name": "Urathis",      "contractor": "Sovereign Dominion of Urathis", "enemy_type": "Pirate Fleet",     "sort_order": 2},
    {"name": "Aresia",       "contractor": "Martian Republic of Aresia",    "enemy_type": "Civil War Militia","sort_order": 3},
    {"name": "Kelvinor",     "contractor": "Thermian State of Kelvinor",    "enemy_type": "Rogue Syndicate",  "sort_order": 4},
    {"name": "Veth Prime",   "contractor": "Uncontracted",                  "enemy_type": "Xeno Collective",  "sort_order": 5},
]


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=os.environ["DATABASE_URL"], min_size=2, max_size=10)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def init_schema():
    pool = await get_pool()
    schema_path = os.path.join(os.path.dirname(__file__), "../sql/schema.sql")
    with open(schema_path) as f:
        sql = f.read()
    async with pool.acquire() as conn:
        await conn.execute(sql)


async def ensure_guild(guild_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO guild_config (guild_id) VALUES ($1) ON CONFLICT (guild_id) DO NOTHING",
            guild_id)
        await _seed_planets(conn, guild_id)


async def _seed_planets(conn, guild_id: int):
    """Insert the 5 default planets if this guild has none yet."""
    count = await conn.fetchval(
        "SELECT COUNT(*) FROM planets WHERE guild_id=$1", guild_id)
    if count:
        return
    for p in DEFAULT_PLANETS:
        await conn.execute("""
            INSERT INTO planets (guild_id, name, contractor, enemy_type, sort_order)
            VALUES ($1,$2,$3,$4,$5)
            ON CONFLICT (guild_id, name) DO NOTHING
        """, guild_id, p["name"], p["contractor"], p["enemy_type"], p["sort_order"])


async def get_theme(conn, guild_id: int) -> dict:
    row = await conn.fetchrow("SELECT * FROM guild_config WHERE guild_id=$1", guild_id)
    if not row:
        return _default_theme()
    return {
        "bot_name":       row["theme_bot_name"],
        "player_faction": row["theme_player_faction"],
        "enemy_faction":  row["theme_enemy_faction"],
        "player_unit":    row["theme_player_unit"],
        "enemy_unit":     row["theme_enemy_unit"],
        "safe_zone":      row["theme_safe_zone"],
        "flavor_text":    row["theme_flavor_text"],
        "color":          row["theme_color"],
    }


async def get_active_planet_id(conn, guild_id: int) -> int:
    row = await conn.fetchrow(
        "SELECT active_planet_id FROM guild_config WHERE guild_id=$1", guild_id)
    return (row["active_planet_id"] if row and row["active_planet_id"] else 1)


async def get_planet(conn, guild_id: int, planet_id: int) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        "SELECT * FROM planets WHERE guild_id=$1 AND id=$2", guild_id, planet_id)


def _default_theme() -> dict:
    return {
        "bot_name":       "IRON PACT",
        "player_faction": "Iron Pact PMC",
        "enemy_faction":  "Enemy",
        "player_unit":    "Unit",
        "enemy_unit":     "Enemy Unit",
        "safe_zone":      "FOB Alpha",
        "flavor_text":    "The contract must be fulfilled.",
        "color":          0xAA2222,
    }
