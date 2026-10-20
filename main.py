"""
ELT - ONE bot for everything.   Every command is a slash command (/).

Run:  python main.py        (env: DISCORD_TOKEN)
"""
import asyncio
import os
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, GUILD_ID

TOKEN = os.environ.get("DISCORD_TOKEN")

# server_logs.py is your existing file, copied UNCHANGED next to this one (it is a ready-made cog).
EXTENSIONS = [
    "server_logs",
    "cogs.studycard",
    "cogs.welcome",
    "cogs.verification",
    "cogs.verifycheck",
    "cogs.staffapps",
    "cogs.punishments",
    "cogs.xp",
    "cogs.boost",
    "cogs.giveaway",
    "cogs.esport",
    "cogs.tickets",
    "cogs.tempvc",
]

intents = discord.Intents.default()
intents.members = True           # Developer Portal: SERVER MEMBERS INTENT
intents.message_content = True   # Developer Portal: MESSAGE CONTENT INTENT
intents.voice_states = True
intents.invites = True


REQUIRED_FILES = ["server_logs.py", "punishment_card.py", "punishment_gif.py"]
for _f in REQUIRED_FILES:
    if not os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), _f)):
        print(f"[main] MISSING FILE: {_f} - copy it from your old bot next to main.py "
              f"({'logs' if _f.startswith('server') else 'punishment commands'} will not load without it)")


class EliteBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)

    async def setup_hook(self):
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                print(f"[main] loaded {ext}")
            except Exception:
                print(f"[main] FAILED to load {ext} (the other features still run):")
                traceback.print_exc()

        # server_logs.py has its own /warn /warnings /unwarn (global commands). The punishment cog already owns
        # those names, so the duplicates are removed here (the join/leave/ban/role/channel/voice logs keep working).
        for name in ("warn", "warnings", "warns", "unwarn"):
            self.remove_command(name)
            try:
                self.tree.remove_command(name)          # global scope only - the punishment cog's are guild commands
            except Exception:
                pass

        # ONE sync, to the server only (instant). No other bot shares this token any more.
        synced = await self.tree.sync(guild=GUILD)
        print(f"[main] synced {len(synced)} slash commands: {sorted(c.name for c in synced)}")

    async def on_ready(self):
        print(f"[main] logged in as {self.user} ({self.user.id})")


bot = EliteBot()


@bot.tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = str(error) or "You are not allowed to use this command."
    else:
        print(f"[main] /{interaction.command.name if interaction.command else '?'} failed: {error!r}")
        traceback.print_exception(type(error), error, error.__traceback__)
        msg = "Something went wrong running that command."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


if not TOKEN:
    raise SystemExit("DISCORD_TOKEN is not set. Add it in Railway's Variables tab, then redeploy.")
bot.run(TOKEN)
