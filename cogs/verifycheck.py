"""/verifycard - staff post a clean verification card (NAME / AGE / INVITE BY / USER) in VERIFY-CHECK."""
from __future__ import annotations

import asyncio
import re
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, GUILD_ID, with_banner

VERIFY_CHECK_CHANNEL_ID = None
VERIFY_CHECK_NAME = "verify-check"
STAFF_ROLE_ID = 1513904127837736992
ADMIN_ROLE_ID = 1513904120803889243
UT_STAFF_ROLE_NAME = "ut staff"
UT_STAFF_ROLE_ID = None
INVITE_CHANNEL_ID = None
INVITE_CHANNEL_NAME = "invite"
INVITE_HISTORY_LIMIT = 1500
INVITE_SCAN_LIMIT = 50000
EMBED_COLOR = 0x8B2BE2
SERVER_FOOTER = "ELT | ELITE LEADERS COMMUNITY"


def norm(t):
    t = unicodedata.normalize("NFKC", t or "").lower()
    return "".join(c for c in t if c.isalnum())


def clean(t):
    return (t or "").replace("*", "").replace("`", "").replace("\\", "").strip()


def safe_text(t):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(t))


def find_text_channel(guild, forced_id, name, exact):
    if forced_id:
        ch = guild.get_channel(forced_id)
        return ch if isinstance(ch, discord.TextChannel) else None
    key = norm(name)
    for ch in guild.text_channels:
        if norm(ch.name) == key:
            return ch
    if not exact:
        for ch in guild.text_channels:
            if key in norm(ch.name):
                return ch
    return None


find_verify_channel = lambda g: find_text_channel(g, VERIFY_CHECK_CHANNEL_ID, VERIFY_CHECK_NAME, False)
find_invite_channel = lambda g: find_text_channel(g, INVITE_CHANNEL_ID, INVITE_CHANNEL_NAME, True)


def find_ut_staff_role(guild):
    if UT_STAFF_ROLE_ID:
        return guild.get_role(UT_STAFF_ROLE_ID)
    key = norm(UT_STAFF_ROLE_NAME)
    return next((r for r in guild.roles if norm(r.name) == key), None)


def is_staff(m):
    if not isinstance(m, discord.Member):
        return False
    if m.guild_permissions.administrator:
        return True
    ids = {r.id for r in m.roles}
    ut = find_ut_staff_role(m.guild)
    return STAFF_ROLE_ID in ids or ADMIN_ROLE_ID in ids or (ut is not None and ut.id in ids)


INVITED_BY_RE = re.compile(
    r"invited\s+by\s+(.+?)(?=\s+(?:who|and|that)\s+(?:now\s+)?has\b|\s+(?:who|and|that)\s+now\b"
    r"|\s+has\s+now\b|\s+now\s+has\b|\s*\(|\s*\n|\s*$)", re.IGNORECASE | re.DOTALL)
JOINED_TAIL_RE = re.compile(r"\s*(?:just\s+|has\s+)*joined.*$", re.IGNORECASE | re.DOTALL)
MENTION_RE = re.compile(r"<@!?(\d+)>")
BARE_ID_RE = re.compile(r"(?<!\d)(\d{17,20})(?!\d)")
invite_index_id: dict[int, str] = {}
invite_index_name: dict[str, str] = {}
invite_cache: dict[str, int] = {}
join_inviter: dict[int, int] = {}


def message_text(msg):
    parts = [msg.content or ""]
    for e in msg.embeds:
        parts += [e.title or "", e.description or ""]
        if e.author and e.author.name:
            parts.append(e.author.name)
        for f in e.fields:
            parts += [f.name or "", f.value or ""]
        if e.footer and e.footer.text:
            parts.append(e.footer.text)
    return "\n".join(p for p in parts if p)


def split_invite_text(text):
    m = INVITED_BY_RE.search(text)
    if not m:
        return None
    inviter = clean(m.group(1)).strip(" .,:;-")
    if not inviter:
        return None
    return text[:m.start()], text[:m.start()] + "\n" + text[m.end():], inviter


def name_keys(member):
    k = {norm(member.name), norm(member.display_name)}
    if member.global_name:
        k.add(norm(member.global_name))
    k.discard("")
    return k


def joiner_matches_id(joiner, member):
    return any(int(x) == member.id for x in MENTION_RE.findall(joiner)) or re.search(rf"(?<!\d){member.id}(?!\d)", joiner) is not None


def joiner_matches_name(joiner, member):
    lines = [l for l in clean(joiner).splitlines() if l.strip()]
    if not lines:
        return False
    key = norm(JOINED_TAIL_RE.sub("", lines[-1]).strip().lstrip("@"))
    return bool(key) and key in name_keys(member)


def index_message(msg, overwrite):
    p = split_invite_text(message_text(msg))
    if p is None:
        return
    joiner, rest, inviter = p
    for i in {int(x) for x in MENTION_RE.findall(rest)} | {int(x) for x in BARE_ID_RE.findall(rest)}:
        if overwrite or i not in invite_index_id:
            invite_index_id[i] = inviter
    lines = [l for l in clean(joiner).splitlines() if l.strip()]
    if lines:
        key = norm(JOINED_TAIL_RE.sub("", lines[-1]).strip().lstrip("@"))
        if key and (overwrite or key not in invite_index_name):
            invite_index_name[key] = inviter


async def build_invite_index(guild):
    ch = find_invite_channel(guild)
    if ch is None:
        return
    n = 0
    try:
        async for msg in ch.history(limit=INVITE_SCAN_LIMIT):
            index_message(msg, overwrite=False)
            n += 1
            if n % 50 == 0:
                await asyncio.sleep(0)
    except discord.HTTPException as e:
        print(f"[verifycard] invite scan stopped: {e}")
    print(f"[verifycard] invite index ready: {n} messages, {len(invite_index_id)} members")


async def find_inviter_raw(guild, member):
    if member.id in invite_index_id:
        return invite_index_id[member.id]
    ch = find_invite_channel(guild)
    if ch is None:
        return None
    fallback = None
    try:
        async for msg in ch.history(limit=INVITE_HISTORY_LIMIT):
            p = split_invite_text(message_text(msg))
            if p is None:
                continue
            joiner, rest, inviter = p
            if joiner_matches_id(rest, member):
                return inviter
            if fallback is None and joiner_matches_name(joiner, member):
                fallback = inviter
    except discord.HTTPException:
        return fallback
    if fallback is None:
        for k in name_keys(member):
            if k in invite_index_name:
                return invite_index_name[k]
    return fallback


async def inviter_to_text(guild, raw):
    m = MENTION_RE.search(raw)
    if m:
        return f"<@{m.group(1)}>"
    name = raw.strip().lstrip("@").strip()
    key, low = norm(name), name.lower()
    for mem in guild.members:
        if low in (mem.name.lower(), (mem.global_name or "").lower(), mem.display_name.lower()):
            return mem.mention
    if key:
        for mem in guild.members:
            if key in {norm(mem.name), norm(mem.display_name), norm(mem.global_name or "")}:
                return mem.mention
    try:
        for mem in await guild.query_members(query=name[:100], limit=5):
            if low in (mem.name.lower(), (mem.global_name or "").lower(), mem.display_name.lower()):
                return mem.mention
    except (discord.HTTPException, asyncio.TimeoutError):
        pass
    return f"**{safe_text(name)}**"


async def refresh_invites(guild):
    try:
        inv = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        return []
    invite_cache.clear()
    invite_cache.update({i.code: (i.uses or 0) for i in inv})
    return inv


class VerifyCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            return
        await refresh_invites(guild)
        asyncio.create_task(build_invite_index(guild))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild and message.guild.id == GUILD_ID:
            ch = find_invite_channel(message.guild)
            if ch is not None and message.channel.id == ch.id:
                index_message(message, overwrite=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != GUILD_ID:
            return
        old = dict(invite_cache)
        inv = await refresh_invites(member.guild)
        used = [i for i in inv if (i.uses or 0) > old.get(i.code, 0) and i.inviter]
        if len(used) == 1:
            join_inviter[member.id] = used[0].inviter.id

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild and invite.guild.id == GUILD_ID:
            invite_cache[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        invite_cache.pop(invite.code, None)

    @app_commands.command(name="verifycard", description="Post the verification card in VERIFY-CHECK (staff only)")
    @app_commands.describe(member="The member being verified", name="Their name", age="Their age",
                           invited_by="Only if the bot says Unknown: who invited them")
    @app_commands.guild_only()
    async def verifycard(self, interaction: discord.Interaction, member: discord.Member,
                         name: app_commands.Range[str, 1, 50], age: app_commands.Range[int, 5, 99],
                         invited_by: discord.Member | None = None):
        if not is_staff(interaction.user):
            return await interaction.response.send_message("❌ Only staff can use this command.", ephemeral=True)
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)
        channel = find_verify_channel(guild)
        if channel is None:
            return await interaction.followup.send("❌ I can't find the VERIFY-CHECK channel.", ephemeral=True)
        text = None
        if invited_by is not None:
            text = invited_by.mention
        else:
            raw = await find_inviter_raw(guild, member)
            if raw:
                text = await inviter_to_text(guild, raw)
            elif member.id in join_inviter:
                text = f"<@{join_inviter[member.id]}>"
        found = text is not None
        text = text or "`Unknown`"
        roles = [r for r in (guild.get_role(STAFF_ROLE_ID), find_ut_staff_role(guild)) if r is not None]
        embed = discord.Embed(title="✦ VERIFICATION CARD ✦", color=EMBED_COLOR, timestamp=discord.utils.utcnow())
        embed.description = (f"## NAME : {safe_text(str(name))}\n## AGE : {age}\n## INVITE BY : {text}\n## USER : {member.mention}")
        embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        if member.joined_at:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, "R"), inline=True)
        embed.add_field(name="Checked by", value=interaction.user.mention, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=SERVER_FOOTER)
        kw = with_banner(embed, "verify")
        try:
            await channel.send(content=" ".join(r.mention for r in roles) or None, embed=embed,
                               allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=roles or False), **kw)
        except discord.Forbidden:
            return await interaction.followup.send(f"❌ I can't send messages / embeds in {channel.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            return await interaction.followup.send(f"❌ Discord error: {e}", ephemeral=True)
        note = "" if found else "\n⚠️ I couldn't find them in the -INVITE channel — use the `invited_by` option next time."
        await interaction.followup.send(f"✅ Card posted in {channel.mention}.{note}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(VerifyCheck(bot), guild=GUILD)
