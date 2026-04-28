DEFAULT_BANNER_KEY = "standard"
DEFAULT_BANNER_NAME = "Standard Issue Dossier"
DEFAULT_BANNER_URL = "https://dummyimage.com/900x240/1b1f2a/e8edf7.png&text=COMMAND+DOSSIER"
RECOVERY_STATUS = "is recovering from total loss of unit cohesion."


def cosmetic_key(value: str) -> str:
    key = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    key = "_".join(part for part in key.split("_") if part)
    return key[:40] or "standard"


async def ensure_commander_profile(conn, guild_id: int, user_id: int, display_name: str):
    await conn.execute("""
        INSERT INTO commander_profiles (guild_id, owner_id, display_name)
        VALUES ($1,$2,$3)
        ON CONFLICT (guild_id, owner_id) DO UPDATE
        SET display_name=EXCLUDED.display_name, updated_at=NOW()
    """, guild_id, user_id, display_name)


async def ensure_default_banner(conn, guild_id: int):
    await conn.execute("""
        INSERT INTO cosmetic_banners (guild_id, banner_key, name, image_url, created_by)
        VALUES ($1,$2,$3,$4,0)
        ON CONFLICT (guild_id, banner_key) DO NOTHING
    """, guild_id, DEFAULT_BANNER_KEY, DEFAULT_BANNER_NAME, DEFAULT_BANNER_URL)


async def grant_default_banner(conn, guild_id: int, user_id: int):
    await ensure_default_banner(conn, guild_id)
    await conn.execute("""
        INSERT INTO commander_banners (guild_id, owner_id, banner_key, granted_by)
        VALUES ($1,$2,$3,0)
        ON CONFLICT (guild_id, owner_id, banner_key) DO NOTHING
    """, guild_id, user_id, DEFAULT_BANNER_KEY)
    await conn.execute("""
        UPDATE commander_profiles
        SET selected_banner_key=COALESCE(selected_banner_key, $3), updated_at=NOW()
        WHERE guild_id=$1 AND owner_id=$2
    """, guild_id, user_id, DEFAULT_BANNER_KEY)


async def mark_recovering(conn, guild_id: int, owner_id: int, display_name: str):
    await ensure_commander_profile(conn, guild_id, owner_id, display_name)
    await conn.execute("""
        UPDATE commander_profiles
        SET recovery_status=$3, updated_at=NOW()
        WHERE guild_id=$1 AND owner_id=$2
    """, guild_id, owner_id, RECOVERY_STATUS)


async def clear_recovery(conn, guild_id: int, owner_id: int):
    await conn.execute("""
        UPDATE commander_profiles
        SET recovery_status=NULL, updated_at=NOW()
        WHERE guild_id=$1 AND owner_id=$2
    """, guild_id, owner_id)
