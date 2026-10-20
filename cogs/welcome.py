"""Welcome message with banner. /testwelcome (admin) shows a private preview."""
import os
import time

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, GUILD_ID, HERE, banner_path, is_admin

SERVER_NAME = "ELT | ELITE LEADERS COMMUNITY"
WELCOME_CHANNEL_ID = 1513904242891427930
RULES_CHANNEL_ID = 1410440667401814052
ANNOUNCEMENTS_CHANNEL_ID = 1513930984456716458
ROLES_CHANNEL_ID = None
CHAT_CHANNEL_ID = 1513904263502499870
VERIFY_VC_ID = 1513904254535073883
WELCOME_TRIGGER = "join"            # "join" = when they join | "verified" = when they get the Verified role
VERIFIED_ROLE_ID = 1513904156350353511
EMBED_COLOR = 0x9B30FF
WELCOME_COOLDOWN = 10 * 60
E = "<a:emoji_8128:1556021990324969592>"

_last: dict[int, float] = {}


def ordinal(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n:,}{suf}"


def banner():
    """Your own Liebe_Roxo.gif if it sits next to main.py, otherwise the generated welcome banner."""
    own = os.path.join(HERE, "Liebe_Roxo.gif")
    return own if os.path.exists(own) else banner_path("welcome")


def build(member: discord.Member):
    guild = member.guild
    count = guild.member_count or len(guild.members)
    links = [f"{E} **{n}** ➜ <#{cid}>" for n, cid in (("Rules", RULES_CHANNEL_ID), ("Announcements", ANNOUNCEMENTS_CHANNEL_ID),
                                                     ("Roles", ROLES_CHANNEL_ID), ("Chat", CHAT_CHANNEL_ID)) if cid]
    d = ("# ✦ WELCOME TO ELITE LEADERS COMMUNITY ✦\n"
         f"## {member.mention}\n*Step inside… and remain as long as you dare.*\n\n"
         f"> You are our `{ordinal(count)}` member.\n> The community just grew **stronger**.\n")
    if links:
        d += "\n### Begin your journey\n" + "\n".join(links) + "\n"
    if VERIFY_VC_ID:
        d += ("\n### How to verify\n"
              f"> **1.** Join the voice channel <#{VERIFY_VC_ID}>\n"
              "> **2.** Wait a moment — a **staff member** will verify you\n"
              "> **3.** Once verified, the **whole server opens** for you\n")
    e = discord.Embed(description=d, color=EMBED_COLOR, timestamp=discord.utils.utcnow())
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.set_thumbnail(url=member.display_avatar.url)
    e.add_field(name="Member", value=f"`{member.display_name}`")
    e.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"))
    e.add_field(name="Member Number", value=f"`#{count:,}`")
    e.set_footer(text=f"{SERVER_NAME} • Welcome aboard", icon_url=guild.icon.url if guild.icon else None)
    path = banner()
    file = None
    if path:
        name = "welcome" + os.path.splitext(path)[1]
        file = discord.File(path, filename=name)
        e.set_image(url=f"attachment://{name}")
    return e, file


class Welcome(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_welcome(self, member: discord.Member, force=False):
        if member.bot or member.guild.id != GUILD_ID:
            return
        now = time.monotonic()
        if not force and now - _last.get(member.id, -1e9) < WELCOME_COOLDOWN:
            return
        _last[member.id] = now
        ch = member.guild.get_channel(WELCOME_CHANNEL_ID)
        if not isinstance(ch, discord.abc.Messageable):
            return print("[welcome] can't see the welcome channel")
        embed, file = build(member)
        try:
            await ch.send(content=member.mention, embed=embed, file=file or discord.utils.MISSING,
                          allowed_mentions=discord.AllowedMentions(users=[member]))
        except discord.HTTPException as e:
            print(f"[welcome] failed: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if WELCOME_TRIGGER == "join":
            await self.send_welcome(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if WELCOME_TRIGGER != "verified":
            return
        role = after.guild.get_role(VERIFIED_ROLE_ID)
        if role and role not in before.roles and role in after.roles:
            await self.send_welcome(after)

    @app_commands.command(name="testwelcome", description="Preview the welcome message (admins only, pings nobody)")
    @app_commands.guild_only()
    async def testwelcome(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("Admins only.", ephemeral=True)
        embed, file = build(interaction.user)
        await interaction.response.send_message(embed=embed, file=file or discord.utils.MISSING, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Welcome(bot), guild=GUILD)
