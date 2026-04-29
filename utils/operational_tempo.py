TEMPO_PER_FLEET = 500
DEPLOYMENT_PER_FLEET = 8

TRANSMISSION_VARIANTS = [
    "Additional fleet assets have been authorized.",
    "A fleet has been reassigned to this theatre.",
    "Private naval contractors have been secured.",
    "Emergency escalation protocols are now active.",
    "Funding authorization has unlocked fleet support.",
]


async def add_operational_tempo(conn, guild_id: int, amount: int):
    cfg = await conn.fetchrow(
        "SELECT operational_tempo, tempo_threshold, fleet_pool_available FROM guild_config WHERE guild_id=$1",
        guild_id,
    )
    if not cfg:
        return {"fleets_gained": 0, "tempo": 0, "threshold": TEMPO_PER_FLEET}
    tempo = (cfg["operational_tempo"] or 0) + max(0, amount)
    threshold = cfg["tempo_threshold"] or TEMPO_PER_FLEET
    fleets = cfg["fleet_pool_available"] or 0
    gained = 0
    while tempo >= threshold:
        tempo -= threshold
        fleets += 1
        gained += 1
    await conn.execute(
        "UPDATE guild_config SET operational_tempo=$1, fleet_pool_available=$2 WHERE guild_id=$3",
        tempo, fleets, guild_id,
    )
    return {"fleets_gained": gained, "tempo": tempo, "threshold": threshold, "fleet_pool": fleets}


def capacity_for_fleets(fleet_count: int) -> int:
    return max(0, fleet_count) * DEPLOYMENT_PER_FLEET
