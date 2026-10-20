"""
Verification + moderator support + waiting/report voice rooms  (was: Discord Verification Bot v2.5)
No slash commands here: it runs on voice joins, buttons and the support panel (/supportpanel re-posts the panel).
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, GUILD_ID, PURPLE, MOD_ROLE_ID, banner_file, banner_name, banner_path, is_admin

# =========================== CONFIG ===========================
VERIFICATION_CHANNEL_ID = 1542531178526146670
WELCOME_CHANNEL_ID = None
WAITING_VC_ID = 1513904254535073883
HELP_ALERT_CHANNEL_ID = 1551163762084683866
MEMBER_HELP_PANEL_CHANNEL_ID = 1553786062579699722
ISSUE_REPORTS_CHANNEL_ID = 1553196616146493460
INVITE_LOG_CHANNEL_ID = None
INVITE_LOG_WINDOW_BEFORE = timedelta(minutes=2)
INVITE_LOG_WINDOW_AFTER = timedelta(minutes=20)
INVITE_LOG_WIDE_SCAN_LIMIT = 1500
HELP_VC_CONFIG = {1517941411151085691: {"emoji": "", "send_member_panel": True}}
DEFAULT_HELP_CONFIG = next(iter(HELP_VC_CONFIG.values()))

WAITING_VC_EMOJI = "⏳"
REPORT_VC_EMOJI = "📛"
LEGACY_ALERT_EMOJIS = {"⛔"}
REPORT_VC_IDS = {1517940974125318166, 1554981588880850964}
REPORT_CATEGORY_ID = 1517941029221695760
REPORT_CHANNEL_NAME = "📛┃𝗥𝗘𝗣𝗢𝗥𝗧"
LEGACY_REPORT_CHANNEL_NAMES = {"⛔┃𝗥𝗘𝗣𝗢𝗥𝗧"}
ALERT_VC_EMOJIS = {WAITING_VC_ID: WAITING_VC_EMOJI, **{v: WAITING_VC_EMOJI for v in HELP_VC_CONFIG},
                   **{v: REPORT_VC_EMOJI for v in REPORT_VC_IDS}}
ALERT_VC_IDS = set(ALERT_VC_EMOJIS)
ALERT_EMOJIS = set(ALERT_VC_EMOJIS.values())
_STRIPPABLE = sorted(ALERT_EMOJIS | LEGACY_ALERT_EMOJIS, key=len, reverse=True)

UNVERIFIED_ROLE_ID = 1513904174079934657
VERIFIED_ROLE_ID = 1513904156350353511
EXTRA_ROLES_ON_VERIFY = [1513904151309058159]
GENDER_ROLES = {"boy": ("Boy", 1513904153049436332), "girl": ("Girl", 1545082482855125013),
                "unsure": ("Not sure if it’s a girl", 1513904149085814795)}

VERIFY_EMOJI = discord.PartialEmoji(name="positivo", id=1553555472811040788)
X_EMOJI = discord.PartialEmoji(name="❌")
CLAIM_EMOJI = discord.PartialEmoji(name="crown", id=1554186011809021953)
RESOLVE_EMOJI = discord.PartialEmoji(name="shield_check", id=1553472890601734325)
DESCRIBE_EMOJI = discord.PartialEmoji(name="pencil", id=1553472888906977300)

PS_WAITING = "Waiting for a moderator"
PS_SENT = "Message sent — waiting for a moderator"
PS_DISCONNECTED = "Disconnected — waiting for them to reconnect"
ps_claimed = lambda mention: f"{mention} is handling your request"

NEW_ACCOUNT_WARNING_DAYS = 7
VERIFY_REALERT_COOLDOWN = 5 * 60
REJOIN_PING_COOLDOWN = 5 * 60
REMINDER_DELETE_AFTER = 10 * 60
HELP_LEAVE_GRACE = 20
REPORT_ROOM_COOLDOWN = 10
DM_ON_REJECT = True
REJECT_REASON_MAX = 300
PING_ON_JOIN, PING_DELETE_AFTER, NOTIFY_DELETE_AFTER = True, 8, 12
JOIN_PING_TEXT = "your request is in — check the panel and press **Describe Issue** to tell us what is wrong."
PANEL_TITLE = "Support Request Received"
# ================================================================

bot: commands.Bot = None
invite_cache, member_inviters, report_vc_base_nicknames = {}, {}, {}
inviter_text_cache: dict[int, str] = {}
_nick_locks: dict[int, asyncio.Lock] = {}
_nick_failed: set[int] = set()
help_panels: dict = {}
verify_alerts: dict = {}
_verify_locks, _verify_action_locks, _report_locks = {}, {}, {}
member_report_rooms: dict[int, int] = {}
_report_last_created: dict[int, float] = {}
_leave_tasks: dict[int, asyncio.Task] = {}
report_room_ids: set[int] = set()
shared_panel = None
_panel_lock = asyncio.Lock()
_startup_done = False
_bg_tasks: set = set()

VERIFY_OPEN_FOOTER = "Click Verify to let this member in"
VERIFY_DONE_FOOTERS = ("Verified", "Rejected", "Member left", "Verified ☑️", "Verified ✅", "Rejected ❌", "Member left ❌")
VERIFY_LEFT_FOOTERS = ("Member left", "Member left ❌")
VERIFY_REJECTED_FOOTERS = ("Rejected", "Rejected ❌")
ALERT_DONE_FOOTERS = ("Resolved", "Member left", "Cancelled", "Resolved ✅", "Resolved ☑️", "Member left ❌", "Cancelled ⚪")


def bg(coro):
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


async def cache_invites(guild):
    try:
        inv = await guild.invites()
        invite_cache[guild.id] = {i.code: {"uses": i.uses or 0, "max_uses": i.max_uses, "inviter": i.inviter} for i in inv}
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[verify] couldn't cache invites: {e}")


async def send_with_retry(channel, **kw):
    for attempt, delay in enumerate((1, 2, 4, 8, 16)):
        try:
            return await channel.send(**kw)
        except discord.HTTPException as e:
            if e.status == 429 and attempt < 4:
                await asyncio.sleep(delay)
            else:
                raise


def set_field(embed, name, value, inline=False):
    for i, f in enumerate(embed.fields):
        if f.name == name:
            return embed.set_field_at(i, name=name, value=value, inline=inline)
    if name == "Status":
        return
    embed.add_field(name=name, value=value, inline=inline)


def get_field(embed, name):
    return next((f.value for f in embed.fields if f.name == name), None)


# ====================== nickname emoji (⏳ waiting / 📛 report) ======================
def _is_report_room(ch) -> bool:
    if ch is None or ch.id in REPORT_VC_IDS:
        return False
    return ch.id in report_room_ids or (getattr(ch, "category_id", None) == REPORT_CATEGORY_ID
                                        and (ch.name == REPORT_CHANNEL_NAME or ch.name in LEGACY_REPORT_CHANNEL_NAMES))


def _alert_emoji_for(m):
    ch = m.voice.channel if m.voice else None
    if ch is None:
        return None
    return REPORT_VC_EMOJI if _is_report_room(ch) else ALERT_VC_EMOJIS.get(ch.id)


_ACCESS = dict(view_channel=True, connect=True, speak=True, send_messages=True, read_message_history=True)


def _report_overwrites(guild, base, member=None):
    ow = dict(base)
    mod = guild.get_role(MOD_ROLE_ID)
    for target in (mod, member):
        if target is not None:
            o = ow.get(target, discord.PermissionOverwrite())
            o.update(**_ACCESS)
            ow[target] = o
    return ow


async def ensure_report_room_access(room, member=None):
    mod = room.guild.get_role(MOD_ROLE_ID)
    for target in (mod, member):
        if target is None:
            continue
        o = room.overwrites_for(target)
        o.update(**_ACCESS)
        try:
            await room.set_permissions(target, overwrite=o, reason="Mods/reporter must be able to use the report room")
        except discord.HTTPException as e:
            return print(f"[verify] can't edit report room permissions: {e}")


_EMOJI_CHARS = ("\U0001F000-\U0001FAFF\U000E0020-\U000E007F\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u21AA\u221E\u231A-\u23FF"
                "\u24C2\u25AA-\u25FE\u2600-\u27BF\u2934\u2935\u2B00-\u2BFF\u3030\u303D\u3297\u3299\u200D\uFE0F\u20E3")
_LEADING_EMOJI_RE = re.compile(f"^[{_EMOJI_CHARS}\\s]+")


def _strip_leading_emoji(t):
    return _LEADING_EMOJI_RE.sub("", t).strip() if t else ""


def _strip_alert_prefix(nick):
    had = False
    while nick:
        for e in _STRIPPABLE:
            if nick.startswith(e):
                nick, had = nick[len(e):].lstrip(), True
                break
        else:
            break
    return (nick or None) if had else nick, had


def _has_exact_prefix(nick, emoji):
    if not nick or not nick.startswith(f"{emoji} "):
        return False
    rest = nick[len(emoji) + 1:]
    return bool(rest) and not _LEADING_EMOJI_RE.match(rest)


def get_base_nick(member):
    if member.id not in report_vc_base_nicknames:
        nick, had = _strip_alert_prefix(member.nick)
        if had and (not nick or nick in (member.name, member.global_name)):
            nick = None
        report_vc_base_nicknames[member.id] = nick
    return report_vc_base_nicknames[member.id]


async def set_report_vc_alert(member):
    lock = _nick_locks.setdefault(member.id, asyncio.Lock())
    async with lock:
        fresh = member.guild.get_member(member.id) or member
        emoji = _alert_emoji_for(fresh)
        active = emoji is not None
        if active:
            name = ""
            for c in (get_base_nick(fresh), fresh.global_name, fresh.name):
                name = _strip_leading_emoji(c)
                if name:
                    break
            target = f"{emoji} {name or fresh.name}"[:32]
        else:
            _, had = _strip_alert_prefix(fresh.nick)
            if not had:
                report_vc_base_nicknames.pop(fresh.id, None)
                return
            target = get_base_nick(fresh)
        if fresh.nick == target:
            if not active:
                report_vc_base_nicknames.pop(fresh.id, None)
            return
        try:
            await fresh.edit(nick=target, reason="Waiting/help/report VC occupancy changed")
            if not active:
                report_vc_base_nicknames.pop(fresh.id, None)
        except discord.Forbidden:
            _nick_failed.add(fresh.id)
            print(f"[verify] can't change {fresh}'s nickname (owner, or my role is below theirs / no Manage Nicknames)")
            if not active:
                report_vc_base_nicknames.pop(fresh.id, None)
        except discord.HTTPException as e:
            print(f"[verify] nickname update failed: {e}")


async def create_report_room(member):
    guild = member.guild
    async with _report_locks.setdefault(member.id, asyncio.Lock()):
        def in_trigger():
            m = guild.get_member(member.id)
            return bool(m and m.voice and m.voice.channel and m.voice.channel.id in REPORT_VC_IDS)
        if not in_trigger():
            return
        room, created = None, False
        ex = guild.get_channel(member_report_rooms.get(member.id, 0))
        if isinstance(ex, discord.VoiceChannel) and _is_report_room(ex):
            room = ex
        else:
            wait = REPORT_ROOM_COOLDOWN - (time.monotonic() - _report_last_created.get(member.id, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
                if not in_trigger():
                    return
            cat = guild.get_channel(REPORT_CATEGORY_ID)
            if not isinstance(cat, discord.CategoryChannel):
                return print("[verify] REPORT category not found")
            try:
                room = await guild.create_voice_channel(REPORT_CHANNEL_NAME, category=cat,
                                                        overwrites=_report_overwrites(guild, cat.overwrites, member),
                                                        reason=f"Report room for {member}")
            except discord.HTTPException as e:
                return print(f"[verify] couldn't create the report room: {e}")
            created = True
            report_room_ids.add(room.id)
            member_report_rooms[member.id] = room.id
            _report_last_created[member.id] = time.monotonic()
        if not created:
            await ensure_report_room_access(room, member)
        try:
            await member.move_to(room, reason="Moved into their report room")
            await asyncio.sleep(0.5)
            await set_report_vc_alert(member)
        except discord.HTTPException as e:
            print(f"[verify] couldn't move {member} into the report room: {e}")
            if created:
                try:
                    await room.delete(reason="Couldn't move the member in")
                except discord.HTTPException:
                    pass


async def delete_if_empty_report_room(ch):
    if not _is_report_room(ch) or any(not m.bot for m in ch.members):
        return
    try:
        await ch.delete(reason="Report room is empty")
        report_room_ids.discard(ch.id)
        for mid, rid in list(member_report_rooms.items()):
            if rid == ch.id:
                del member_report_rooms[mid]
    except discord.HTTPException:
        pass


# ====================== verification card ======================
def _already_done(message, footers):
    return bool(message.embeds and message.embeds[0].footer.text in footers)


async def dm_rejected_member(guild, member_id, reason) -> bool:
    m = guild.get_member(member_id)
    if m is None:
        return False
    e = discord.Embed(title="Verification not accepted", color=PURPLE,
                      description=f"A moderator reviewed your request in **{guild.name}** and could not verify you right now.\n"
                                  "-# You are welcome to join the voice channel again once the issue is fixed.")
    e.add_field(name="Reason", value=f">>> {reason}"[:1024], inline=False)
    e.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
    e.timestamp = discord.utils.utcnow()
    try:
        await m.send(embed=e)
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


def verify_view(member_id, disabled=False):
    v = discord.ui.View(timeout=None)
    v.add_item(VerifyButton("accept", member_id, disabled))
    v.add_item(VerifyButton("reject", member_id, disabled))
    return v


class RejectReasonModal(discord.ui.Modal, title="Reject Member"):
    reason = discord.ui.TextInput(label="Why is this member being rejected?", style=discord.TextStyle.paragraph, required=True,
                                  placeholder="The reason is shown on the card and sent to the member.", max_length=REJECT_REASON_MAX)

    def __init__(self, member_id):
        super().__init__()
        self.member_id = member_id

    async def on_submit(self, interaction):
        message = interaction.message
        if message is None or not message.embeds:
            return await interaction.response.send_message("Couldn't find the request message.", ephemeral=True)
        reason = self.reason.value.strip()
        async with _verify_action_locks.setdefault(self.member_id, asyncio.Lock()):
            try:
                fresh = await message.channel.fetch_message(message.id)
            except discord.HTTPException:
                fresh = message
            if _already_done(fresh, VERIFY_DONE_FOOTERS):
                return await interaction.response.send_message("This request was already handled.", ephemeral=True)
            embed = fresh.embeds[0].copy()
            embed.color = PURPLE
            set_field(embed, "Rejected", f"❌ {interaction.user.mention} • {discord.utils.format_dt(discord.utils.utcnow(), 'R')}")
            set_field(embed, "Reason", f">>> {reason}"[:1024])
            embed.set_footer(text="Rejected ❌")
            await interaction.response.edit_message(embed=embed, view=verify_view(self.member_id, True))
        if DM_ON_REJECT and interaction.guild is not None:
            ok = await dm_rejected_member(interaction.guild, self.member_id, reason)
            await interaction.followup.send("Rejected. The member was sent the reason by DM." if ok else
                                            "Rejected. I couldn't DM the member (their DMs are closed or they left).", ephemeral=True)

    async def on_error(self, interaction, error):
        print(f"[verify] reject modal error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


async def finish_verification(interaction, card, member_id, gender_key):
    guild = interaction.guild
    async with _verify_action_locks.setdefault(member_id, asyncio.Lock()):
        try:
            fresh = await card.channel.fetch_message(card.id)
        except discord.HTTPException:
            fresh = card
        if _already_done(fresh, VERIFY_DONE_FOOTERS):
            return "This request was already handled."
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except discord.HTTPException:
                return "That member isn't in the server anymore (they may have left)."
        label, rid = GENDER_ROLES[gender_key]
        grole = guild.get_role(rid)
        if grole is None:
            return "I can't find that role. Check GENDER_ROLES."
        unv, ver = guild.get_role(UNVERIFIED_ROLE_ID), guild.get_role(VERIFIED_ROLE_ID)
        to_add = [r for r in (ver, grole, *[guild.get_role(x) for x in EXTRA_ROLES_ON_VERIFY]) if r is not None and r not in member.roles]
        try:
            if to_add:
                await member.add_roles(*to_add, reason=f"Verified by {interaction.user} ({label})")
            if unv and unv in member.roles:
                await member.remove_roles(unv, reason=f"Verified by {interaction.user}")
        except discord.Forbidden:
            return "I don't have enough permission. Move the bot's role above the roles it gives."
        except discord.HTTPException as e:
            print(f"[verify] role change failed for {member}: {e}")
            return "Something went wrong while changing roles. Try again."
        embed = fresh.embeds[0].copy()
        embed.color = PURPLE
        set_field(embed, "Verified", f"☑️ {interaction.user.mention} • {discord.utils.format_dt(discord.utils.utcnow(), 'R')}")
        set_field(embed, "Gender", label, inline=True)
        embed.set_footer(text="Verified ☑️")
        member_inviters.pop(member_id, None)
        try:
            await fresh.edit(embed=embed, view=verify_view(member_id, True))
        except discord.HTTPException as e:
            print(f"[verify] couldn't update the verification card: {e}")
    if WELCOME_CHANNEL_ID:
        wc = guild.get_channel(WELCOME_CHANNEL_ID)
        if wc:
            try:
                await wc.send(f"Welcome {member.mention}, you're verified — glad to have you in the server!")
            except discord.HTTPException:
                pass
    return None


class GenderChoiceView(discord.ui.View):
    def __init__(self, card, member_id):
        super().__init__(timeout=120)
        self.card, self.member_id = card, member_id

    async def _pick(self, interaction, key):
        await interaction.response.defer()
        err = await finish_verification(interaction, self.card, self.member_id, key)
        await interaction.edit_original_response(
            content=err or f"Verified <@{self.member_id}> as **{GENDER_ROLES[key][0]}**.", view=None)
        self.stop()

    @discord.ui.button(label="Boy", style=discord.ButtonStyle.secondary)
    async def boy(self, i, b):
        await self._pick(i, "boy")

    @discord.ui.button(label="Girl", style=discord.ButtonStyle.secondary)
    async def girl(self, i, b):
        await self._pick(i, "girl")

    @discord.ui.button(label="Not sure if it’s a girl", style=discord.ButtonStyle.secondary)
    async def unsure(self, i, b):
        await self._pick(i, "unsure")


class VerifyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"verify_(?P<action>accept|reject)_(?P<uid>[0-9]+)"):
    def __init__(self, action, member_id, disabled=False):
        label, emoji = ("Verify", VERIFY_EMOJI) if action == "accept" else ("Reject", X_EMOJI)
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"verify_{action}_{member_id}", disabled=disabled))
        self.action, self.member_id = action, member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]))

    async def callback(self, interaction):
        if _already_done(interaction.message, VERIFY_DONE_FOOTERS):
            return await interaction.response.send_message("This request was already handled.", ephemeral=True)
        if self.action == "reject":
            return await interaction.response.send_modal(RejectReasonModal(self.member_id))
        await interaction.response.send_message(f"Is <@{self.member_id}> a boy or a girl?",
                                                view=GenderChoiceView(interaction.message, self.member_id), ephemeral=True)


# ====================== moderator help alerts ======================
def _is_claimed(embed):
    return get_field(embed, "Claimed by") is not None


def _alert_origin(msg):
    if msg.embeds:
        e = msg.embeds[0]
        if "Not in a voice channel" in ((get_field(e, "Voice Channel") or "") + (e.description or "")):
            return "panel"
    return "vc"


def notify_member(member_id, text):
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    m, ch = guild.get_member(member_id), guild.get_channel(MEMBER_HELP_PANEL_CHANNEL_ID)
    if m is not None and isinstance(ch, discord.TextChannel):
        bg(ping_member(ch, m, text, NOTIFY_DELETE_AFTER))


async def ping_member(channel, member, text=None, delete_after=PING_DELETE_AFTER):
    try:
        msg = await channel.send(f"{member.mention} {text or 'your request is in — check the panel.'}",
                                 allowed_mentions=discord.AllowedMentions(users=[member]))
        await asyncio.sleep(delete_after)
        await msg.delete()
    except discord.HTTPException:
        pass


def help_view(member_id, claimed=False, disabled=False):
    v = discord.ui.View(timeout=None)
    v.add_item(HelpButton("claim", member_id, disabled or claimed))
    v.add_item(HelpButton("note", member_id, disabled))
    return v


async def resolve_alert(interaction, member_id, note=None):
    message = interaction.message
    if message is None or not message.embeds:
        return await interaction.response.send_message("Couldn't find the alert message.", ephemeral=True)
    if _already_done(message, ALERT_DONE_FOOTERS):
        return await interaction.response.send_message("This request was already closed.", ephemeral=True)
    embed = message.embeds[0].copy()
    embed.color = PURPLE
    if note:
        set_field(embed, "What happened", f">>> {note}"[:1024])
    set_field(embed, "Resolved by", interaction.user.mention, inline=True)
    embed.set_footer(text="Resolved", icon_url=embed.footer.icon_url)
    await interaction.response.edit_message(embed=embed, view=help_view(member_id, disabled=True))
    await close_help_request(member_id, f"Resolved by {interaction.user.mention}", PURPLE,
                             notify=f"your request was resolved by {interaction.user.mention}. Thank you for your patience!",
                             origin_hint=_alert_origin(message))


class ResolveNoteModal(discord.ui.Modal, title="Resolve Help Request"):
    note = discord.ui.TextInput(label="What was the problem? (optional)", style=discord.TextStyle.paragraph,
                                placeholder="What happened / why they needed help...", required=False, max_length=500)

    def __init__(self, member_id):
        super().__init__()
        self.member_id = member_id

    async def on_submit(self, interaction):
        await resolve_alert(interaction, self.member_id, self.note.value or None)

    async def on_error(self, interaction, error):
        print(f"[verify] resolve modal error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


class HelpButton(discord.ui.DynamicItem[discord.ui.Button], template=r"help_(?P<action>claim|resolved|note)_(?P<uid>[0-9]+)"):
    def __init__(self, action, member_id, disabled=False):
        label, emoji = {"claim": ("Claim", CLAIM_EMOJI), "resolved": ("Mark as Resolved", RESOLVE_EMOJI)}.get(action, ("Resolve", RESOLVE_EMOJI))
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"help_{action}_{member_id}", disabled=disabled))
        self.action, self.member_id = action, member_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]))

    async def callback(self, interaction):
        message = interaction.message
        if message is None or not message.embeds:
            return await interaction.response.send_message("Couldn't find the alert message.", ephemeral=True)
        if _already_done(message, ALERT_DONE_FOOTERS):
            return await interaction.response.send_message("This request was already closed.", ephemeral=True)
        if self.action == "resolved":
            return await resolve_alert(interaction, self.member_id)
        if self.action == "note":
            return await interaction.response.send_modal(ResolveNoteModal(self.member_id))
        embed = message.embeds[0].copy()
        by = get_field(embed, "Claimed by")
        if by:
            return await interaction.response.send_message(f"Already claimed by {by}.", ephemeral=True)
        embed.color = PURPLE
        set_field(embed, "Claimed by", interaction.user.mention, inline=True)
        await interaction.response.edit_message(embed=embed, view=help_view(self.member_id, claimed=True))
        entry = help_panels.get(self.member_id)
        if entry is not None:
            entry["claimed"] = True
        await set_panel_status(self.member_id, ps_claimed(interaction.user.mention))
        origin = entry.get("origin") if entry is not None else _alert_origin(message)
        if origin == "panel":
            notify_member(self.member_id, f"{interaction.user.mention} picked up your request and will contact you shortly.")


class IssueReportView(discord.ui.View):
    def __init__(self, seen=False):
        super().__init__(timeout=None)
        if seen:
            self.mark_seen.label, self.mark_seen.disabled = "Seen", True

    @discord.ui.button(label="Mark as Seen", emoji=VERIFY_EMOJI, style=discord.ButtonStyle.secondary, custom_id="issue_report_seen")
    async def mark_seen(self, interaction, button):
        embed = interaction.message.embeds[0].copy()
        if any(f.name == "Seen by" for f in embed.fields):
            return await interaction.response.send_message("Already marked as seen.", ephemeral=True)
        embed.color = PURPLE
        set_field(embed, "Status", "**Seen**", inline=True)
        set_field(embed, "Seen by", interaction.user.mention, inline=True)
        set_field(embed, "Seen At", discord.utils.format_dt(discord.utils.utcnow(), "R"), inline=True)
        await interaction.response.edit_message(embed=embed, view=IssueReportView(seen=True))


# ====================== member support panel (ONE shared panel) ======================
def build_shared_embed(guild):
    queue = sorted(((m, e) for m, e in help_panels.items() if e.get("show") and e.get("queued")),
                   key=lambda it: it[1].get("queued_at") or it[1]["joined_at"])
    if queue:
        lines = [f"**{i}.** <@{m}> — {e['status']} • {discord.utils.format_dt(e.get('queued_at') or e['joined_at'], 'R')}"
                 for i, (m, e) in enumerate(queue[:15], 1)]
        if len(queue) > 15:
            lines.append(f"*…and {len(queue) - 15} more*")
        qt = "\n".join(lines)
    else:
        qt = "*Nobody is waiting right now.*"
    e = discord.Embed(title=PANEL_TITLE, color=PURPLE, description=(
        "## Moderator Support\nNeed help from a **moderator**? Press **Describe Issue** below and tell us what is wrong — "
        "**you do not need to join a voice channel.** You are added to the queue the moment you send it, and a moderator "
        "will pick it up as soon as one is available.\n\n### How it works\n"
        "**1. Describe** — press **Describe Issue** and tell us what happened, who is involved and when.\n"
        "**2. Wait** — you appear in the **Current queue** below, and get a short ping here when a moderator picks it up.\n"
        "**3. Cancel** — press **Cancel Request** if you no longer need help.\n\n"
        "-# Prefer to talk? You can also wait in the **Waiting for Help** voice channel.\n\n"
        f"### Current queue\n{qt}"))
    if banner_path("support"):
        e.set_image(url=f"attachment://{banner_name('support')}")
    e.set_footer(text="ELITE LEADERS COMMUNITY • Support System", icon_url=guild.icon.url if guild.icon else None)
    e.timestamp = discord.utils.utcnow()
    return e


def panel_view():
    v = discord.ui.View(timeout=None)
    v.add_item(MemberPanelButton("describe"))
    v.add_item(MemberPanelButton("cancel"))
    return v


async def refresh_shared_panel(guild):
    global shared_panel
    ch = guild.get_channel(MEMBER_HELP_PANEL_CHANNEL_ID)
    if not isinstance(ch, discord.TextChannel):
        return print("[verify] #describe-issue channel not found")
    async with _panel_lock:
        embed = build_shared_embed(guild)
        if shared_panel is not None:
            try:
                await shared_panel.edit(content=None, embed=embed, view=panel_view())
                return
            except discord.NotFound:
                shared_panel = None
            except discord.HTTPException as e:
                return print(f"[verify] failed to update the shared panel: {e}")
        try:
            kw = {}
            f = banner_file("support")
            if f:
                kw["file"] = f
            shared_panel = await send_with_retry(ch, embed=embed, view=panel_view(), **kw)
        except Exception as e:
            print(f"[verify] failed to send the shared panel: {e}")


async def ensure_shared_panel(guild):
    global shared_panel
    ch = guild.get_channel(MEMBER_HELP_PANEL_CHANNEL_ID)
    if not isinstance(ch, discord.TextChannel):
        return
    found = []
    try:
        async for m in ch.history(limit=100):
            if m.author.id == bot.user.id and m.embeds and PANEL_TITLE in (m.embeds[0].title or ""):
                found.append(m)
    except discord.HTTPException as e:
        print(f"[verify] couldn't read #describe-issue history: {e}")
    if found:
        shared_panel = found[0]
        for old in found[1:]:
            try:
                await old.delete()
            except discord.HTTPException:
                pass
        if banner_path("support") and not shared_panel.attachments:      # old panel without the banner -> repost it
            try:
                await shared_panel.delete()
            except discord.HTTPException:
                pass
            shared_panel = None
    await refresh_shared_panel(guild)


async def set_panel_status(member_id, status, color=None, note=None):
    entry = help_panels.get(member_id)
    if entry is None:
        return
    entry["status"] = status
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await refresh_shared_panel(guild)


async def close_help_request(member_id, status, color, alert_footer=None, skip_panel=False, note=None, lock_alert=True,
                             notify=None, origin_hint=None):
    entry = help_panels.pop(member_id, None)
    if entry is None:
        if notify and origin_hint == "panel":
            notify_member(member_id, notify)
        return
    alert = entry.get("alert")
    if alert_footer and alert is not None:
        try:
            fresh = await alert.channel.fetch_message(alert.id)
            if fresh.embeds and fresh.embeds[0].footer.text not in ALERT_DONE_FOOTERS:
                embed = fresh.embeds[0].copy()
                embed.color = color
                embed.set_footer(text=alert_footer, icon_url=embed.footer.icon_url)
                view = help_view(member_id, disabled=True) if lock_alert else help_view(member_id, claimed=_is_claimed(embed))
                await fresh.edit(embed=embed, view=view)
        except discord.NotFound:
            pass
        except discord.HTTPException as e:
            print(f"[verify] failed to update the mod alert: {e}")
    if notify and entry.get("origin") == "panel":
        notify_member(member_id, notify)
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await refresh_shared_panel(guild)


def entry_from_alert(msg, config, origin="vc"):
    e = msg.embeds[0]
    claimed_by, issue = get_field(e, "Claimed by"), get_field(e, "Issue")
    described = bool(issue) and "hasn't described" not in issue
    status = ps_claimed(claimed_by) if claimed_by else (PS_SENT if described else PS_WAITING)
    return {"alert": msg, "origin": origin, "described": described, "queued": described or origin == "panel",
            "claimed": bool(claimed_by), "show": config.get("send_member_panel", True), "status": status,
            "joined_at": msg.created_at, "queued_at": msg.created_at}


async def scan_latest_alerts(channel):
    latest = {}
    try:
        async for msg in channel.history(limit=100):
            if msg.author.id != bot.user.id or not msg.embeds:
                continue
            for row in msg.components:
                for c in getattr(row, "children", []):
                    m = re.fullmatch(r"help_(?:claim|resolved|note)_(\d+)", getattr(c, "custom_id", None) or "")
                    if m:
                        latest.setdefault(int(m.group(1)), msg)
    except discord.HTTPException as e:
        print(f"[verify] couldn't read the help alert channel: {e}")
    return latest


def _alert_is_open(msg):
    f = msg.embeds[0].footer.text if msg.embeds else None
    return f not in ALERT_DONE_FOOTERS and f != "Member moved"


async def recover_help_request_for(member):
    ch = member.guild.get_channel(HELP_ALERT_CHANNEL_ID)
    if ch is None:
        return False
    msg = (await scan_latest_alerts(ch)).get(member.id)
    if msg is None or not _alert_is_open(msg):
        return False
    origin = _alert_origin(msg)
    vc = member.voice.channel if member.voice else None
    in_help = vc is not None and vc.id in HELP_VC_CONFIG
    if origin == "vc" and not in_help:
        return False
    config = HELP_VC_CONFIG[vc.id] if in_help else DEFAULT_HELP_CONFIG
    if member.id not in help_panels:
        help_panels[member.id] = entry_from_alert(msg, config, origin)
        await refresh_shared_panel(member.guild)
    return True


async def recover_help_requests(guild):
    alert_channel = guild.get_channel(HELP_ALERT_CHANNEL_ID)
    if alert_channel is None:
        return
    latest = await scan_latest_alerts(alert_channel)
    waiting = {}
    for vc_id in HELP_VC_CONFIG:
        vc = guild.get_channel(vc_id)
        if isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
            for m in vc.members:
                if not m.bot:
                    waiting[m.id] = (m, vc)
    for mid, msg in latest.items():
        if mid in waiting or not _alert_is_open(msg):
            continue
        if _alert_origin(msg) == "panel" and guild.get_member(mid) is not None:
            help_panels[mid] = entry_from_alert(msg, DEFAULT_HELP_CONFIG, "panel")
            continue
        try:
            e = msg.embeds[0].copy()
            e.color = PURPLE
            e.set_footer(text="Member left", icon_url=e.footer.icon_url)
            await msg.edit(embed=e, view=help_view(mid, disabled=True))
        except discord.HTTPException:
            pass
    for mid, (m, vc) in waiting.items():
        msg, config = latest.get(mid), HELP_VC_CONFIG[vc.id]
        if msg is not None and _alert_is_open(msg):
            help_panels[mid] = entry_from_alert(msg, config, _alert_origin(msg))
        elif msg is not None and msg.embeds and msg.embeds[0].footer.text in ("Resolved", "Resolved ✅", "Resolved ☑️"):
            continue
        else:
            await send_help_alert(m, vc, config, ping=False)


async def close_after_grace(member_id):
    try:
        await asyncio.sleep(HELP_LEAVE_GRACE)
    except asyncio.CancelledError:
        return
    _leave_tasks.pop(member_id, None)
    guild = bot.get_guild(GUILD_ID)
    m = guild.get_member(member_id) if guild else None
    if m and m.voice and m.voice.channel and m.voice.channel.id in HELP_VC_CONFIG:
        return
    await close_help_request(member_id, "Left the queue", PURPLE, alert_footer="Member left")


def reserve_help_request(member, config, origin="vc"):
    if member.id in help_panels:
        return False
    help_panels[member.id] = {"alert": None, "origin": origin, "described": False, "queued": False, "claimed": False,
                              "show": config.get("send_member_panel", True), "status": PS_WAITING,
                              "joined_at": discord.utils.utcnow(), "queued_at": None}
    return True


async def send_help_alert(member, voice_channel, config, ping=True):
    if not reserve_help_request(member, config, "vc"):
        return
    await _post_help_alert(member, voice_channel, config, ping)


async def _post_help_alert(member, voice_channel, config, ping=True, issue=None):
    hc = member.guild.get_channel(HELP_ALERT_CHANNEL_ID)
    if hc is None:
        print("[verify] help alert channel not found")
        help_panels.pop(member.id, None)
        return
    where = (f"is waiting in {voice_channel.mention}\n-# Join them to help." if voice_channel is not None else
             "sent a request from the support panel\n-# Not in a voice channel — contact them directly.")
    embed = discord.Embed(title="Help Request", description=f"## {member.mention}\n{where}", color=PURPLE)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
    embed.add_field(name="Waiting Since", value=discord.utils.format_dt(discord.utils.utcnow(), "R"), inline=True)
    if issue or config.get("send_member_panel", True):
        embed.add_field(name="Issue", inline=False,
                        value=f">>> {issue}"[:1024] if issue else "*The member hasn't described the issue yet.*")
    embed.set_footer(text="ELITE LEADERS COMMUNITY • Press Claim first, then Resolve when it's handled",
                     icon_url=member.guild.icon.url if member.guild.icon else None)
    embed.timestamp = discord.utils.utcnow()
    msg = None
    try:
        msg = await send_with_retry(hc, content="@everyone A member needs a moderator", embed=embed, view=help_view(member.id),
                                    allowed_mentions=discord.AllowedMentions(everyone=True))
    except Exception as e:
        print(f"[verify] failed to send the help alert: {e}")
    entry = help_panels.get(member.id)
    if entry is None:
        if msg is not None:
            try:
                left = msg.embeds[0].copy()
                left.set_footer(text="Member left", icon_url=left.footer.icon_url)
                await msg.edit(embed=left, view=help_view(member.id, disabled=True))
            except discord.HTTPException:
                pass
        return
    entry["alert"] = msg
    if entry.get("show") and entry.get("queued"):
        await refresh_shared_panel(member.guild)
    if PING_ON_JOIN and ping and entry.get("show"):
        pc = member.guild.get_channel(MEMBER_HELP_PANEL_CHANNEL_ID)
        if isinstance(pc, discord.TextChannel):
            bg(ping_member(pc, member, JOIN_PING_TEXT))


class DescribeIssueModal(discord.ui.Modal, title="Describe Your Issue"):
    issue = discord.ui.TextInput(label="What do you need help with?", style=discord.TextStyle.paragraph, required=True, max_length=500,
                                 placeholder="What happened, who is involved, and when? A few lines is enough.")

    def __init__(self, member_id):
        super().__init__()
        self.member_id = member_id

    async def on_submit(self, interaction):
        await interaction.response.send_message(
            "Got it, thanks. You are now in the queue — a moderator will pick up your request shortly. "
            "You will get a ping in this channel when someone is on it.", ephemeral=True)
        guild = interaction.guild
        member = guild.get_member(self.member_id) if guild else None
        reports = guild.get_channel(ISSUE_REPORTS_CHANNEL_ID) if guild else None
        text, now = self.issue.value, discord.utils.utcnow()
        entry, created_here = help_panels.get(self.member_id), False
        if entry is None and member is not None and await recover_help_request_for(member):
            entry = help_panels.get(self.member_id)
        if entry is None and member is not None:
            vc = member.voice.channel if member.voice and member.voice.channel and member.voice.channel.id in HELP_VC_CONFIG else None
            config = HELP_VC_CONFIG[vc.id] if vc else DEFAULT_HELP_CONFIG
            if reserve_help_request(member, config, "vc" if vc else "panel"):
                help_panels[self.member_id].update(described=True, queued=True, queued_at=now, status=PS_SENT)
                created_here = True
                await _post_help_alert(member, vc, config, ping=False, issue=text)
            entry = help_panels.get(self.member_id)
        if entry is not None and not created_here:
            for _ in range(10):
                if entry.get("alert") is not None:
                    break
                await asyncio.sleep(0.5)
            entry["described"] = True
            if not entry.get("queued"):
                entry["queued"], entry["queued_at"] = True, now
            alert = entry.get("alert")
            if alert is not None:
                try:
                    fresh = await alert.channel.fetch_message(alert.id)
                    if fresh.embeds and fresh.embeds[0].footer.text not in ALERT_DONE_FOOTERS:
                        e = fresh.embeds[0].copy()
                        set_field(e, "Issue", f">>> {text}"[:1024])
                        await fresh.edit(embed=e, view=help_view(self.member_id, claimed=_is_claimed(e)))
                except discord.HTTPException as e:
                    print(f"[verify] couldn't add the issue to the mod alert: {e}")
            if not entry.get("claimed"):
                await set_panel_status(self.member_id, PS_SENT)
            elif guild:
                await refresh_shared_panel(guild)
        if reports is None:
            return print("[verify] issue reports channel not found")
        e = discord.Embed(title="Issue Report", color=PURPLE,
                          description=f"## New issue from <@{self.member_id}>\n-# Sent from the support panel\n\n### Their message\n>>> {text}")
        if member:
            e.set_author(name=f"{member.display_name} ({member})", icon_url=member.display_avatar.url)
        else:
            e.set_author(name=f"Unknown member ({self.member_id})")
        voice = member.voice.channel if member and member.voice and member.voice.channel else None
        alert_msg = entry.get("alert") if entry else None
        e.add_field(name="Member", value=f"<@{self.member_id}>", inline=True)
        e.add_field(name="User ID", value=f"`{self.member_id}`", inline=True)
        e.add_field(name="Submitted", value=discord.utils.format_dt(now, "R"), inline=True)
        e.add_field(name="Voice Channel", value=voice.mention if voice else "`Not in a voice channel`", inline=True)
        e.add_field(name="Status", value="**Not seen yet**", inline=True)
        if alert_msg is not None:
            e.add_field(name="Help Request", value=f"[Jump to request]({alert_msg.jump_url})", inline=True)
        e.set_footer(text="ELITE LEADERS COMMUNITY • Issue Reports", icon_url=guild.icon.url if guild and guild.icon else None)
        e.timestamp = now
        try:
            await send_with_retry(reports, embed=e, view=IssueReportView())
        except Exception as ex:
            print(f"[verify] failed to send the issue report: {ex}")

    async def on_error(self, interaction, error):
        print(f"[verify] describe-issue modal error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("Something went wrong. Please try again.", ephemeral=True)


class MemberPanelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"member_(?P<action>describe|cancel)(?:_[0-9]+)?"):
    def __init__(self, action):
        label, emoji = ("Describe Issue", DESCRIBE_EMOJI) if action == "describe" else ("Cancel Request", X_EMOJI)
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, custom_id=f"member_{action}"))
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"])

    async def interaction_check(self, interaction):
        if self.action == "describe":
            return True
        entry = help_panels.get(interaction.user.id)
        if entry is None:
            m = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
            if m is not None and await recover_help_request_for(m):
                entry = help_panels.get(interaction.user.id)
        if entry is None:
            await interaction.response.send_message("You don't have an active request. Press **Describe Issue** to open one — no voice channel needed.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction):
        if self.action == "describe":
            return await interaction.response.send_modal(DescribeIssueModal(interaction.user.id))
        await interaction.response.send_message("Your request has been cancelled. Press **Describe Issue** any time you need a **moderator**.", ephemeral=True)
        await close_help_request(interaction.user.id, "Cancelled", PURPLE, alert_footer="Cancelled")


# ====================== inviter lookup + verification alert ======================
def _find_invite_log_channel(guild):
    if INVITE_LOG_CHANNEL_ID:
        ch = guild.get_channel(INVITE_LOG_CHANNEL_ID)
        return ch if isinstance(ch, discord.TextChannel) else None
    return next((c for c in guild.text_channels if c.name.lower().rstrip().endswith("invite")), None)


def _embed_text(e):
    return "\n".join(p for p in [e.title or "", e.description or "", *[f"{f.name} {f.value}" for f in e.fields]] if p)


def _parse_invite_post(guild, member, msg):
    uid = str(member.id)
    names = {n.lower() for n in (member.name, member.display_name, member.global_name) if n and len(n) >= 3}
    for text in [msg.content or ""] + [_embed_text(e) for e in msg.embeds]:
        idx = text.lower().find("invited by")
        if idx == -1:
            continue
        head = text[:idx]
        if uid not in head and not any(n in head.lower() for n in names):
            continue
        rest = text[idx + len("invited by"):].strip()
        rest = re.split(r"\s+who\b|\n|[!]+\s*$", rest, maxsplit=1)[0].strip(" .!*`")
        if not rest:
            continue
        m = re.search(r"<@!?(\d+)>", rest)
        if m:
            im = guild.get_member(int(m.group(1)))
            return f"<@{m.group(1)}>" + (f" (`{im.name}`)" if im else "")
        return f"**{rest}**"
    return None


async def lookup_inviter_from_log(guild, member):
    if inviter_text_cache.get(member.id):
        return inviter_text_cache[member.id]
    ch = _find_invite_log_channel(guild)
    if ch is None:
        return None

    async def scan(**kw):
        async for msg in ch.history(**kw):
            f = _parse_invite_post(guild, member, msg)
            if f:
                return f

    try:
        res = None
        if member.joined_at:
            res = await scan(after=member.joined_at - INVITE_LOG_WINDOW_BEFORE, before=member.joined_at + INVITE_LOG_WINDOW_AFTER,
                             limit=200, oldest_first=False)
        res = res or await scan(limit=INVITE_LOG_WIDE_SCAN_LIMIT)
        if res:
            inviter_text_cache[member.id] = res
        return res
    except discord.HTTPException as e:
        print(f"[verify] couldn't read the -INVITE channel: {e}")
    return None


async def prefetch_inviter(member):
    for delay in (6, 15, 40):
        await asyncio.sleep(delay)
        if await lookup_inviter_from_log(member.guild, member):
            return


async def find_latest_verification(channel, member_id):
    try:
        async for msg in channel.history(limit=100):
            if msg.author.id != bot.user.id or not msg.embeds:
                continue
            ids = [getattr(c, "custom_id", None) or "" for row in msg.components for c in getattr(row, "children", [])]
            if any(c.startswith("verify_") and c.endswith(f"_{member_id}") for c in ids):
                return msg
    except discord.HTTPException as e:
        print(f"[verify] couldn't read the verification channel: {e}")
    return None


async def send_verification_alert(member, voice_channel):
    async with _verify_locks.setdefault(member.id, asyncio.Lock()):
        await _send_verification_alert(member, voice_channel)


async def _send_verification_alert(member, voice_channel):
    unv = member.guild.get_role(UNVERIFIED_ROLE_ID)
    if unv is None or unv not in member.roles:
        return
    vch = member.guild.get_channel(VERIFICATION_CHANNEL_ID)
    if vch is None:
        return print("[verify] verification channel not found")
    existing, entry = None, verify_alerts.get(member.id)
    if entry is not None:
        try:
            existing = await vch.fetch_message(entry["message"].id)
        except discord.NotFound:
            verify_alerts.pop(member.id, None)
            entry = None
        except discord.HTTPException:
            existing = entry["message"]
    if existing is None:
        existing = await find_latest_verification(vch, member.id)
        if existing is not None:
            entry = verify_alerts[member.id] = {"message": existing, "rejoins": 0, "last_edit": 0.0, "last_ping": existing.created_at}
    previous_reason = None
    if existing is not None and existing.embeds:
        footer = existing.embeds[0].footer.text
        if footer == VERIFY_OPEN_FOOTER:
            entry["rejoins"] = entry.get("rejoins", 0) + 1
            if time.monotonic() - entry.get("last_edit", 0.0) >= 5:
                try:
                    e = existing.embeds[0].copy()
                    set_field(e, "Re-joined", f"`{entry['rejoins']}` time(s) since this request")
                    await existing.edit(embed=e)
                    entry["last_edit"] = time.monotonic()
                except discord.HTTPException:
                    pass
            last = entry.get("last_ping") or existing.created_at
            if (discord.utils.utcnow() - last).total_seconds() >= REJOIN_PING_COOLDOWN:
                try:
                    await send_with_retry(vch, content=f"@everyone {member.mention} is **still waiting** to be verified "
                                                       f"(joined the voice channel `{entry['rejoins'] + 1}` times). {existing.jump_url}",
                                          reference=existing.to_reference(fail_if_not_exists=False),
                                          allowed_mentions=discord.AllowedMentions(everyone=True, users=False, replied_user=False),
                                          delete_after=REMINDER_DELETE_AFTER)
                    entry["last_ping"] = discord.utils.utcnow()
                except Exception as e:
                    print(f"[verify] couldn't send the reminder ping: {e}")
            return
        age = (discord.utils.utcnow() - existing.created_at).total_seconds()
        if footer not in VERIFY_LEFT_FOOTERS and age < VERIFY_REALERT_COOLDOWN:
            return
        if footer in VERIFY_REJECTED_FOOTERS:
            previous_reason = get_field(existing.embeds[0], "Reason")

    inv = await lookup_inviter_from_log(member.guild, member)
    if inv is None:
        i = member_inviters.get(member.id)
        inv = f"{i.mention} (`{i.name}`)" if i else "`Unknown` — vanity URL or unknown invite"
    warn = ""
    if NEW_ACCOUNT_WARNING_DAYS:
        age = discord.utils.utcnow() - member.created_at
        if age < timedelta(days=NEW_ACCOUNT_WARNING_DAYS):
            if age.days >= 1:
                lbl = f"{age.days} day{'s' if age.days != 1 else ''} old"
            else:
                h = max(age.seconds // 3600, 0)
                lbl = "less than an hour old" if h < 1 else f"{h} hour{'s' if h != 1 else ''} old"
            warn = f"**New account warning** — only **{lbl}**\n-# Check them carefully before verifying.\n\n"
    joined = discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "`Unknown`"
    embed = discord.Embed(title="Member awaiting verification", color=PURPLE, description=(
        f"{warn}Member: {member.mention}\nID: `{member.id}`\nAccount created: {discord.utils.format_dt(member.created_at, 'R')}\n"
        f"Joined server: {joined}\nInvited by: {inv}\nJoined voice channel: {voice_channel.mention}"))
    embed.set_thumbnail(url=member.display_avatar.url)
    if previous_reason:
        embed.add_field(name="Previous rejection", value=previous_reason[:1024], inline=False)
    kw = {}
    f = banner_file("verify")
    if f:
        embed.set_image(url=f"attachment://{f.filename}")
        kw["file"] = f
    embed.set_footer(text=VERIFY_OPEN_FOOTER)
    try:
        sent = await send_with_retry(vch, content="@everyone A member in the voice channel needs verification", embed=embed,
                                     view=verify_view(member.id), allowed_mentions=discord.AllowedMentions(everyone=True), **kw)
        verify_alerts[member.id] = {"message": sent, "rejoins": 0, "last_edit": 0.0, "last_ping": discord.utils.utcnow()}
    except Exception as e:
        print(f"[verify] failed to send the verification message: {e}")


async def nickname_watchdog():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                for vc in list(guild.channels):
                    if not isinstance(vc, (discord.VoiceChannel, discord.StageChannel)):
                        continue
                    if vc.id not in ALERT_VC_IDS and not _is_report_room(vc):
                        continue
                    for m in list(vc.members):
                        if m.bot or m.id in _nick_failed:
                            continue
                        want = _alert_emoji_for(m)
                        if want and not _has_exact_prefix(m.nick, want):
                            await set_report_vc_alert(m)
        except Exception as e:
            print(f"[verify] nickname watchdog error: {e}")
        await asyncio.sleep(15)


class Verification(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        self.bot.add_dynamic_items(VerifyButton, HelpButton, MemberPanelButton)
        self.bot.add_view(IssueReportView())

    @app_commands.command(name="supportpanel", description="Re-post the moderator support panel (high rank only)")
    @app_commands.guild_only()
    async def supportpanel(self, interaction: discord.Interaction):
        global shared_panel
        if not is_admin(interaction.user):
            return await interaction.response.send_message("High rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if shared_panel is not None:
            try:
                await shared_panel.delete()
            except discord.HTTPException:
                pass
            shared_panel = None
        await refresh_shared_panel(interaction.guild)
        await interaction.followup.send("Support panel posted.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        global _startup_done
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return
        await cache_invites(guild)
        if _startup_done:
            return
        _startup_done = True
        bg(nickname_watchdog())
        for vid in ALERT_VC_IDS:
            ch = guild.get_channel(vid)
            if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
                for m in ch.members:
                    if not m.bot:
                        await set_report_vc_alert(m)
        cat = guild.get_channel(REPORT_CATEGORY_ID)
        if isinstance(cat, discord.CategoryChannel):
            for vc in list(cat.voice_channels):
                if not _is_report_room(vc):
                    continue
                await ensure_report_room_access(vc)
                if vc.members:
                    for m in vc.members:
                        if not m.bot:
                            await set_report_vc_alert(m)
                else:
                    await delete_if_empty_report_room(vc)
        for m in list(guild.members):
            if not m.bot and _alert_emoji_for(m) is None and _strip_alert_prefix(m.nick)[1]:
                await set_report_vc_alert(m)
        await ensure_shared_panel(guild)
        await recover_help_requests(guild)
        await refresh_shared_panel(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        if invite.guild and invite.guild.id == GUILD_ID:
            await cache_invites(invite.guild)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != GUILD_ID:
            return
        await asyncio.sleep(1)
        try:
            cur = await member.guild.invites()
            by_code = {i.code: i for i in cur}
            old = invite_cache.get(member.guild.id, {})
            inviter = None
            for i in cur:
                if (i.uses or 0) > old.get(i.code, {}).get("uses", 0):
                    inviter = i.inviter
                    break
            if inviter is None:
                for code, oi in old.items():
                    if code not in by_code and oi.get("max_uses") and oi.get("uses", 0) == oi["max_uses"] - 1:
                        inviter = oi.get("inviter")
                        break
            member_inviters[member.id] = inviter
            invite_cache[member.guild.id] = {i.code: {"uses": i.uses or 0, "max_uses": i.max_uses, "inviter": i.inviter} for i in cur}
        except (discord.Forbidden, discord.HTTPException) as e:
            print(f"[verify] couldn't read invites: {e}")
        bg(prefetch_inviter(member))

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id != GUILD_ID or member.bot:
            return
        if member.id in help_panels:
            p = _leave_tasks.pop(member.id, None)
            if p is not None:
                p.cancel()
            await close_help_request(member.id, "Left the server", PURPLE, alert_footer="Member left")
        entry = verify_alerts.get(member.id)
        msg = entry["message"] if entry else None
        if msg is None:
            ch = member.guild.get_channel(VERIFICATION_CHANNEL_ID)
            if ch is not None:
                msg = await find_latest_verification(ch, member.id)
        if msg is not None:
            try:
                fresh = await msg.channel.fetch_message(msg.id)
                if fresh.embeds and fresh.embeds[0].footer.text == VERIFY_OPEN_FOOTER:
                    e = fresh.embeds[0].copy()
                    e.color = PURPLE
                    e.set_footer(text="Member left")
                    await fresh.edit(embed=e, view=verify_view(member.id, True))
            except discord.HTTPException as ex:
                print(f"[verify] couldn't close the verification card for {member}: {ex}")
        member_inviters.pop(member.id, None)
        inviter_text_cache.pop(member.id, None)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != GUILD_ID or member.bot:
            return
        bid = before.channel.id if before.channel else None
        aid = after.channel.id if after.channel else None
        if bid == aid:
            return
        _nick_failed.discard(member.id)
        e0 = help_panels.get(member.id)
        if e0 is not None and e0.get("origin", "vc") == "vc" and bid in HELP_VC_CONFIG:
            if aid is None:
                if member.id not in _leave_tasks:
                    _leave_tasks[member.id] = asyncio.create_task(close_after_grace(member.id))
                    e0["status_before"] = e0["status"]
                    await set_panel_status(member.id, PS_DISCONNECTED)
            elif aid != bid:
                await close_help_request(member.id, "Resolved — a moderator moved you", PURPLE, alert_footer="Member moved", lock_alert=False)
        nick_task = None
        if bid in ALERT_VC_IDS or aid in ALERT_VC_IDS or _is_report_room(before.channel) or _is_report_room(after.channel):
            nick_task = asyncio.create_task(set_report_vc_alert(member))
        if before.channel is not None and _is_report_room(before.channel):
            bg(delete_if_empty_report_room(before.channel))
        try:
            if aid in REPORT_VC_IDS:
                await create_report_room(member)
            elif aid == WAITING_VC_ID:
                if isinstance(after.channel, (discord.VoiceChannel, discord.StageChannel)):
                    await send_verification_alert(member, after.channel)
            elif aid in HELP_VC_CONFIG:
                p = _leave_tasks.pop(member.id, None)
                if p is not None:
                    p.cancel()
                    ent = help_panels.get(member.id)
                    if ent is not None:
                        await set_panel_status(member.id, ent.pop("status_before", PS_WAITING))
                if isinstance(after.channel, (discord.VoiceChannel, discord.StageChannel)):
                    await send_help_alert(member, after.channel, HELP_VC_CONFIG[aid])
        finally:
            if nick_task is not None:
                await nick_task


async def setup(b):
    await b.add_cog(Verification(b), guild=GUILD)
