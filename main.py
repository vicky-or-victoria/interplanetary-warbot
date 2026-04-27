"""
Warbot — main entry point.

Required environment variables:
  DISCORD_TOKEN   — bot token
  DATABASE_URL    — asyncpg-compatible PostgreSQL DSN

Optional:
  BOT_OWNER_ID    — your Discord user ID for super-admin access
"""

import asyncio
import logging
import os

import discord
from discord.ext import commands

from utils.db import get_pool, init_schema
from utils.turn_engine import TurnEngine
from views.menu import MainMenuView, EnlistView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("warbot")

COGS = [
    "cogs.admin_cog",
    "cogs.squadron_cog",
    "cogs.map_cog",
]


class Warbot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members          = True
        super().__init__(command_prefix="!", intents=intents)
        self.bot_owner_id = int(os.environ.get("BOT_OWNER_ID", 0))
        self.turn_engine  = TurnEngine(self)

    async def setup_hook(self):
        # Initialise DB schema
        await init_schema()
        log.info("Database schema initialised.")

        # Load cogs
        for cog in COGS:
            try:
                await self.load_extension(cog)
                log.info(f"Loaded: {cog}")
            except Exception as e:
                log.error(f"Failed to load {cog}: {e}", exc_info=True)

        # Sync slash commands globally
        await self.tree.sync()
        log.info("Slash commands synced.")

        # Register persistent views so buttons survive restarts
        # guild_id=0 is a sentinel; the real guild_id is read per-interaction
        self.add_view(MainMenuView(guild_id=0))
        self.add_view(EnlistView(guild_id=0))

        # Start the turn engine background loop
        self.turn_engine.start()
        log.info("Turn engine started.")

    async def on_ready(self):
        log.info(f"Ready — logged in as {self.user} ({self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="the front lines"))

    async def on_guild_join(self, guild: discord.Guild):
        from utils.db import ensure_guild
        await ensure_guild(guild.id)
        log.info(f"Joined guild: {guild.name} ({guild.id})")

    async def close(self):
        self.turn_engine.stop()
        from utils.db import close_pool
        await close_pool()
        await super().close()


async def main():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable not set.")
    bot = Warbot()
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
