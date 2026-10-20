"""
Punishments with animated cards (skull banner): /warn /unwarn /mute /timeout /kick /ban /blacklist /unblacklist
/blacklisted /warnings /history.   Needs punishment_card.py, punishment_gif.py, fonts/ (copied unchanged next to main.py).
"""
import asyncio
import io
import json
import os
import re
import shutil
import time
import traceback
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import GUILD, GUILD_ID, MOD_ROLE_ID, ADMIN_ROLE_ID
from punishment_card import render_card, TYPE_STYLE, TITLE_WORD
from punishment_gif import render_card_gif, clean_for_card, MAX_BYTES, BANNER_PATH

TYPE_STYLE.setdefault("WARNING CLEARED", ((70, 220, 120), 100))
TYPE_STYLE.setdefault("WARN + TIMEOUT", ((255, 138, 61), 66))
TITLE_WORD.setdefault("WARN + TIMEOUT", "WARNING")

warning_lock, blacklist_lock = asyncio.Lock(), asyncio.Lock()
bot = None

# =========================== CONFIG ===========================
PUNISHMENT_LOG_CHANNEL_ID = None      # None = post in the channel where the command was used
MODS_CAN_BAN = False
MODS_CAN_KICK = False
BLACKLIST_MIN_TIER = "admin"
WARN_1_ROLE_ID = 1513904153900875897
WARN_2_ROLE_ID = 1513904154719027291
BLACKLIST_ROLE_ID = 1554563076681109524
SUPER_MEMBER_ROLE_ID = 1513904142551220366
KEEP_ROLE_IDS = {SUPER_MEMBER_ROLE_ID}
WARNING_EXPIRE_DAYS = 30
MAX_WARNINGS = 3
WARN_TIMEOUT_AT = 2
WARN_TIMEOUT_HOURS = 2
WARNING_CLEAR_CHANNEL_ID = 1540154905644367893
DM_PUNISHED_MEMBERS = True
STAFF_ROLE_IDS = {int(x) for x in os.environ.get("STAFF_ROLE_IDS", "1513904136783925380").replace(" ", "").split(",") if x.isdigit()}
DATA_DIR = os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DATA_DIR, "punishments.json")
# ==============================================================

_UP, _LO, _DG = 0x1D5D4, 0x1D5EE, 0x1D7EC


def bold(text):
    out = []
    for ch in text or "":
        c = ord(ch)
        out.append(chr(_UP + c - 65) if 65 <= c <= 90 else chr(_LO + c - 97) if 97 <= c <= 122 else chr(_DG + c - 48) if 48 <= c <= 57 else ch)
    return "".join(out)


def short(text, n):
    text = (text or "").replace("\n", " ")
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- database
def _read_db_file(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return int(d.get("counter", 0)), {int(k): v for k, v in d.get("records", {}).items()}, {int(k): v for k, v in d.get("blacklist", {}).items()}


def load_db():
    for path in (DB_PATH, DB_PATH + ".bak"):
        try:
            c, r, b = _read_db_file(path)
            return max(c, max(r) if r else 0), r, b
        except FileNotFoundError:
            continue
        except Exception as e:
            print(f"[punish] couldn't read {path}: {e}")
            try:
                shutil.copyfile(path, f"{path}.corrupt-{int(time.time())}")
            except Exception:
                pass
    return 0, {}, {}


def save_db():
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        tmp = DB_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"counter": case_counter, "records": punishment_records, "blacklist": blacklist_records}, f, ensure_ascii=False)
        if os.path.exists(DB_PATH):
            try:
                shutil.copyfile(DB_PATH, DB_PATH + ".bak")
            except Exception:
                pass
        os.replace(tmp, DB_PATH)
    except Exception as e:
        print(f"[punish] couldn't save {DB_PATH}: {e}")


case_counter, punishment_records, blacklist_records = load_db()
print(f"[punish] case data: {DB_PATH} (last case #{case_counter:04d}, {len(punishment_records)} saved, {len(blacklist_records)} blacklisted)")


def next_case():
    global case_counter
    case_counter += 1
    save_db()
    return case_counter


def card_name(member):
    cands = [getattr(member, a, None) for a in ("display_name", "global_name", "name")]
    cleaned = [(clean_for_card(c) or "").strip() for c in cands if c]
    alnum = lambda s: sum(ch.isalnum() for ch in s)
    for need in (2, 1):
        for s in cleaned:
            if alnum(s) >= need:
                return s
    return str(member.id)


audit_reason = lambda reason, action, by: f"{reason} — {action} by {by}"[:512]

# ---------------------------------------------------------------- permissions
TIER_RANK = {"none": 0, "staff": 1, "mod": 2, "admin": 3}
TIER_LABEL = {"staff": "Staff", "mod": "Moderators", "admin": "the high rank"}
BAN_MIN_TIER = "mod" if MODS_CAN_BAN else "admin"
KICK_MIN_TIER = "mod" if MODS_CAN_KICK else "admin"


def actor_tier(m):
    g = m.guild
    if m.id == g.owner_id or m.guild_permissions.administrator:
        return "admin"
    ar = g.get_role(ADMIN_ROLE_ID)
    if ar and m.top_role >= ar:
        return "admin"
    mr = g.get_role(MOD_ROLE_ID)
    if mr and m.top_role >= mr:
        return "mod"
    sr = [r for r in (g.get_role(i) for i in STAFF_ROLE_IDS) if r]
    if sr and m.top_role >= min(sr):
        return "staff"
    return "none"


def can_ban(m):
    return TIER_RANK[actor_tier(m)] >= TIER_RANK[BAN_MIN_TIER]


def require_tier(min_tier):
    async def pred(i: discord.Interaction):
        if isinstance(i.user, discord.Member) and TIER_RANK[actor_tier(i.user)] >= TIER_RANK[min_tier]:
            return True
        raise app_commands.CheckFailure(f"⚠️ {bold(f'This command is for {TIER_LABEL[min_tier]} or higher.')}")
    return app_commands.check(pred)


def target_problem(i, member, needs_bot_rank=True):
    g = i.guild
    if member.id == i.user.id:
        return "You can't punish yourself."
    if bot.user and member.id == bot.user.id:
        return "I can't punish myself."
    if member.id == g.owner_id:
        return "You can't punish the server owner."
    if member.guild_permissions.administrator:
        return "You can't punish an administrator."
    mr = g.get_role(MOD_ROLE_ID)
    if mr and member.top_role > mr:
        return "That member is protected (their role is above Moderator)."
    if i.user.id != g.owner_id and member.top_role >= i.user.top_role:
        return "That member's highest role is equal to or above yours."
    if needs_bot_rank and g.me and member.top_role >= g.me.top_role:
        return "That member's highest role is equal to or above mine — move my role higher."
    return None


def get_log_channel(i):
    ch = i.guild.get_channel(PUNISHMENT_LOG_CHANNEL_ID) if PUNISHMENT_LOG_CHANNEL_ID else i.channel
    return ch or i.channel


# ---------------------------------------------------------------- sending helpers
async def send_with_retry(channel, **kw):
    delays = [1, 2, 4, 8]
    for a in range(len(delays) + 1):
        try:
            return await channel.send(**kw)
        except discord.HTTPException as e:
            if not (e.status == 429 or e.status >= 500) or a == len(delays):
                raise
            await asyncio.sleep(delays[a])


async def dm_member(user, guild, title, reason, color, note=""):
    if not DM_PUNISHED_MEMBERS or getattr(user, "bot", False):
        return None
    e = discord.Embed(title=bold(title), color=color, timestamp=discord.utils.utcnow())
    e.add_field(name=bold("Reason"), value=short(reason, 1000), inline=False)
    if note:
        e.add_field(name=bold("Info"), value=short(note, 1000), inline=False)
    e.set_footer(text=guild.name)
    try:
        return await user.send(embed=e)
    except (discord.Forbidden, discord.HTTPException):
        return None


async def undo_dm(m):
    if m is not None:
        try:
            await m.delete()
        except discord.HTTPException:
            pass


class PunishmentDetailsButton(discord.ui.DynamicItem[discord.ui.Button], template=r"punishment_details_(?P<case>[0-9]+)"):
    def __init__(self, case_no):
        super().__init__(discord.ui.Button(label=f"🔍 {bold('View Punishment Details')}", style=discord.ButtonStyle.secondary,
                                           custom_id=f"punishment_details_{case_no}"))
        self.case_no = case_no

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["case"]))

    async def callback(self, interaction):
        r = punishment_records.get(self.case_no)
        if r is None:
            return await interaction.response.send_message(bold("Details for this case aren't available anymore."), ephemeral=True)
        dt = r.get("display_type", r["type"])
        style = TYPE_STYLE.get(dt) or TYPE_STYLE.get(r["type"]) or ((150, 150, 150), 0)
        e = discord.Embed(title=bold(f"Case ELT-{self.case_no:04d} — {dt.title()}"), color=discord.Color.from_rgb(*style[0]))
        e.add_field(name=bold("User"), value=f"<@{r['user_id']}> ({bold(r['user_tag'])})", inline=False)
        e.add_field(name=bold("Punisher"), value=f"<@{r['punisher_id']}> ({bold(r['punisher_tag'])})", inline=False)
        e.add_field(name=bold("Reason"), value=bold(short(r["reason"], 300)), inline=False)
        e.add_field(name=bold("Date"), value=bold(r["date_text"]), inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)


# ---------------------------------------------------------------- warnings
def _warning_is_active(r):
    return r.get("type") == "WARNING" and r.get("warning_active", True) is not False


def _parse_warning_time(r):
    v = r.get("warning_issued_at")
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
        return (dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def active_warning_records(mid):
    recs = [(c, r) for c, r in punishment_records.items() if r.get("user_id") == mid and _warning_is_active(r)]
    recs.sort(key=lambda it: (it[1].get("warning_issued_at", ""), it[0]))
    return recs


active_warning_count = lambda mid: len(active_warning_records(mid))
warning_percent = lambda n: 0 if n <= 0 else 33 if n == 1 else 66 if n == 2 else 100


def role_warning_level(guild, member):
    w1, w2 = guild.get_role(WARN_1_ROLE_ID), guild.get_role(WARN_2_ROLE_ID)
    return 2 if w2 is not None and w2 in member.roles else 1 if w1 is not None and w1 in member.roles else 0


async def update_warning_roles(guild, member, count):
    if member.id in blacklist_records:
        return
    w1, w2 = guild.get_role(WARN_1_ROLE_ID), guild.get_role(WARN_2_ROLE_ID)
    if w1 is None or w2 is None:
        return print("[punish] Warn role not found — check WARN_1_ROLE_ID / WARN_2_ROLE_ID")
    wanted = [w1] if count == 1 else [w1, w2] if count == 2 else []
    cur = [r for r in member.roles if not r.is_default()]
    new = [r for r in cur if r not in (w1, w2)] + wanted
    if set(new) == set(cur):
        return
    try:
        await member.edit(roles=new, reason="Warning level updated")
    except discord.HTTPException as e:
        print(f"[punish] failed to update warning roles for {member}: {e}")


async def _render(user_name, punisher_name, reason, rtype, case_no, date_text, avatar, max_bytes, severity=None):
    try:
        return await asyncio.to_thread(render_card_gif, user_name, punisher_name, reason, rtype, case_no, date_text, avatar, max_bytes, severity), "gif"
    except Exception as e:
        print(f"[punish] animated card failed ({type(e).__name__}: {e}) — still card instead")
        return await asyncio.to_thread(render_card, user_name, punisher_name, reason, rtype, case_no, date_text, avatar, severity), "png"


async def _avatar(member):
    try:
        return await member.display_avatar.replace(size=256, format="png").read()
    except Exception:
        return None


async def post_warning_cleared_card(guild, member, punisher, reason, cleared_cases=None):
    cleared_cases = list(cleared_cases or [])
    case_no = next_case()
    date_text = discord.utils.utcnow().strftime("%d/%m/%Y")
    try:
        card, ext = await _render(card_name(member), card_name(punisher), reason, "WARNING CLEARED", case_no, date_text,
                                  await _avatar(member), min(MAX_BYTES, int(guild.filesize_limit * 0.9)))
    except Exception as e:
        print(f"[punish] failed to render warning-clear card: {e}")
        return False
    punishment_records[case_no] = {"user_id": member.id, "user_tag": str(member), "punisher_id": punisher.id, "punisher_tag": str(punisher),
                                   "reason": reason, "type": "WARNING CLEARED", "display_type": "WARNING CLEARED", "date_text": date_text,
                                   "cleared_warning_cases": cleared_cases}
    save_db()
    ch = guild.get_channel(WARNING_CLEAR_CHANNEL_ID)
    if ch is None:
        print(f"[punish] warning cleared channel {WARNING_CLEAR_CHANNEL_ID} not found")
        return False
    v = discord.ui.View(timeout=None)
    v.add_item(PunishmentDetailsButton(case_no))
    try:
        await send_with_retry(ch, file=discord.File(io.BytesIO(card), filename=f"warning_cleared_{case_no:04d}.{ext}"), view=v)
        return True
    except Exception as e:
        print(f"[punish] failed to send warning-clear card: {e}")
        return False


async def issue_punishment(i, member, ptype, reason, *, card_ptype=None, record_extra=None, confirm_note="", severity=None):
    case_no = next_case()
    rtype = (card_ptype or ptype).upper()
    date_text = discord.utils.utcnow().strftime("%d/%m/%Y")
    try:
        card, ext = await _render(card_name(member), card_name(i.user), reason, rtype, case_no, date_text, await _avatar(member),
                                  min(MAX_BYTES, int(i.guild.filesize_limit * 0.9)), severity)
    except Exception as e:
        print(f"[punish] failed to render the card: {type(e).__name__}: {e}")
        await i.followup.send(f"⚠️ {bold('Something went wrong generating the punishment card.')}", ephemeral=True)
        return None
    punishment_records[case_no] = {"user_id": member.id, "user_tag": str(member), "punisher_id": i.user.id, "punisher_tag": str(i.user),
                                   "reason": reason, "type": ptype, "display_type": rtype, "date_text": date_text}
    if record_extra:
        punishment_records[case_no].update(record_extra)
    save_db()
    ch = get_log_channel(i)
    v = discord.ui.View(timeout=None)
    v.add_item(PunishmentDetailsButton(case_no))
    try:
        posted = await send_with_retry(ch, file=discord.File(io.BytesIO(card), filename=f"punishment_case_{case_no:04d}.{ext}"), view=v)
        hook = getattr(bot, "xp_award_punish", None)      # staff points (cogs/xp.py)
        if hook and posted is not None:
            hook(i.user.id, posted.id)
    except Exception as e:
        print(f"[punish] failed to send the card: {e}")
        await i.followup.send(f"⚠️ {bold('The case was saved but I could not post the card — check my permissions in that channel.')}", ephemeral=True)
        return case_no
    text = f"✅ {bold(f'{ptype.title()} logged for')} {member.mention} {bold('in')} {ch.mention}."
    await i.followup.send(text + (f" {confirm_note}" if confirm_note else ""), ephemeral=True)
    return case_no


async def reject(i, command, target, msg):
    print(f"[punish] /{command}: {i.user} -> {target} blocked: {msg}")
    await i.followup.send(f"⚠️ {bold(msg)}", ephemeral=True)


# ---------------------------------------------------------------- warning expiry
async def expire_warnings():
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return
    now, limit = datetime.now(timezone.utc), timedelta(days=WARNING_EXPIRE_DAYS)
    expired = {}
    for case_no, r in punishment_records.items():
        if not _warning_is_active(r):
            continue
        issued = _parse_warning_time(r)
        if issued is None or now - issued < limit:
            continue
        expired.setdefault(r["user_id"], []).append((case_no, r))
    for mid, items in expired.items():
        cleared = []
        for case_no, r in items:
            r.update(warning_active=False, warning_cleared_at=now.isoformat(), warning_clear_method="automatic_30_day_expiry")
            cleared.append(case_no)
        save_db()
        member = guild.get_member(mid)
        if member is None:
            continue
        try:
            remaining = active_warning_count(mid)
            await update_warning_roles(guild, member, min(remaining, MAX_WARNINGS - 1))
            n = len(items)
            reason = (f"Warning expired after {WARNING_EXPIRE_DAYS} days. Active warnings left: {remaining}/{MAX_WARNINGS}." if n == 1 else
                      f"{n} warnings expired after {WARNING_EXPIRE_DAYS} days. Active warnings left: {remaining}/{MAX_WARNINGS}.")
            punisher = guild.me or bot.user
            if punisher:
                await post_warning_cleared_card(guild, member, punisher, reason, cleared)
            await dm_member(member, guild, f"A warning expired in {guild.name}", reason, 0x57F287, f"Active warnings left: {remaining}/{MAX_WARNINGS}.")
        except Exception:
            print(f"[punish] expiry follow-up failed for {mid}:")
            traceback.print_exc()


# ---------------------------------------------------------------- blacklist helpers
def removable_roles(m):
    return [r for r in m.roles if not r.is_default() and not r.managed and r.id != BLACKLIST_ROLE_ID and r.id not in KEEP_ROLE_IDS]


def kept_roles(m):
    return [r for r in m.roles if not r.is_default() and (r.managed or r.id in KEEP_ROLE_IDS)]


async def lock_blacklist_roles(member, reason):
    role = member.guild.get_role(BLACKLIST_ROLE_ID)
    if role is None:
        raise RuntimeError("blacklist role not found")
    await member.edit(roles=kept_roles(member) + [role], reason=reason[:512])


async def _apply_timeout(i, member, duration, reason, command, ptype, shown):
    try:
        await member.timeout(duration, reason=audit_reason(reason, ptype.lower(), i.user))
    except discord.Forbidden:
        return await reject(i, command, member, "I do not have permission to time out that member.")
    except discord.HTTPException as e:
        return await i.followup.send(f"⚠️ {bold('Failed to apply the timeout:')} {e}", ephemeral=True)
    await dm_member(member, i.guild, f"You were muted in {i.guild.name}" if ptype == "MUTE" else f"You were timed out in {i.guild.name}", reason, 0x8E6CFF, shown)
    await issue_punishment(i, member, ptype, f"{reason} ({shown})")


class Punishments(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        self.bot.add_dynamic_items(PunishmentDetailsButton)
        self.warning_expiry_loop.start()

    async def cog_unload(self):
        self.warning_expiry_loop.cancel()

    @tasks.loop(hours=1)
    async def warning_expiry_loop(self):
        try:
            await expire_warnings()
        except Exception:
            traceback.print_exc()

    @warning_expiry_loop.before_loop
    async def _w(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.guild.id != GUILD_ID:
            return
        if member.id in blacklist_records:
            try:
                await lock_blacklist_roles(member, "Blacklisted member rejoined")
            except Exception as e:
                print(f"[punish] couldn't re-apply the blacklist role to {member}: {e}")
            return
        c = active_warning_count(member.id)
        if c:
            await update_warning_roles(member.guild, member, min(c, MAX_WARNINGS - 1))

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if after.guild.id != GUILD_ID or after.id not in blacklist_records or before.roles == after.roles:
            return
        role = after.guild.get_role(BLACKLIST_ROLE_ID)
        if role is None:
            return
        extras = [r for r in after.roles if not r.is_default() and not r.managed and r.id != role.id and r.id not in KEEP_ROLE_IDS]
        if not extras and role in after.roles:
            return
        try:
            await lock_blacklist_roles(after, "Blacklisted: roles are locked")
        except Exception as e:
            print(f"[punish] blacklist lock failed for {after}: {e}")

    # ------------------------------------------------------------ /warn
    @app_commands.command(name="warn", description="Give a warning. Each warning expires 30 days after it was given.")
    @app_commands.describe(member="The member being warned", reason="Why they're being warned")
    @app_commands.guild_only()
    @require_tier("staff")
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        p = target_problem(i, member, needs_bot_rank=False)
        if p:
            return await reject(i, "warn", member, p)
        if member.id in blacklist_records:
            return await reject(i, "warn", member, "That member is already blacklisted.")
        guild = i.guild
        async with warning_lock:
            count = max(active_warning_count(member.id), role_warning_level(guild, member)) + 1
            percent = warning_percent(count)
            auto_ban = count >= MAX_WARNINGS and can_ban(i.user)
            card_type, card_reason = ("BAN" if auto_ban else "WARNING"), reason
            if auto_ban:
                card_reason = f"{reason} - {MAX_WARNINGS} active warnings reached. Automatic ban applied."[:300]
            timed_out = False
            if count == WARN_TIMEOUT_AT and not auto_ban:
                if guild.me and member.top_role >= guild.me.top_role:
                    await i.followup.send("⚠️ I can't time that member out: their highest role is equal to or above mine.", ephemeral=True)
                else:
                    try:
                        await member.timeout(timedelta(hours=WARN_TIMEOUT_HOURS),
                                             reason=audit_reason(f"Warning {count}/{MAX_WARNINGS}: {reason}", "auto-timeout", bot.user))
                        timed_out, card_type = True, "WARN + TIMEOUT"
                        card_reason = f"{reason[:270]} (for {WARN_TIMEOUT_HOURS} hours)"
                    except discord.HTTPException as e:
                        await i.followup.send(f"⚠️ The warning is given, but the timeout failed: {e}", ephemeral=True)
            extra = {"warning_active": True, "warning_issued_at": discord.utils.utcnow().isoformat(), "warning_count": count, "warning_percent": percent}
            if auto_ban:
                extra["automatic_ban"] = True
            if timed_out:
                extra["auto_timeout_hours"] = WARN_TIMEOUT_HOURS
            note = ""
            if not auto_ban:
                note = f"{bold('Active warnings:')} **{count}/{MAX_WARNINGS}**."
                if timed_out:
                    note += f" {bold(f'Timed out for {WARN_TIMEOUT_HOURS} hours.')}"
            case_no = await issue_punishment(i, member, "WARNING", card_reason, card_ptype=card_type, record_extra=extra,
                                             confirm_note=note, severity=None if auto_ban else percent)
            if case_no is None:
                if timed_out:
                    try:
                        await member.timeout(None, reason="Warning card failed - timeout undone")
                    except discord.HTTPException:
                        pass
                return
            await update_warning_roles(guild, member, count if auto_ban else min(count, MAX_WARNINGS - 1))
        if auto_ban:
            dm = await dm_member(member, guild, f"You have been banned from {guild.name}", reason, 0xE53935, f"You reached {MAX_WARNINGS} active warnings.")
            try:
                await member.ban(reason=audit_reason(f"{MAX_WARNINGS} active warnings reached: {reason}", "automatically banned", bot.user))
            except discord.HTTPException as e:
                await undo_dm(dm)
                await i.followup.send(f"⚠️ The BAN card was posted, but the automatic ban failed: {e}", ephemeral=True)
            return
        info = (f"Active warnings: {count}/{MAX_WARNINGS}. Each warning is removed automatically {WARNING_EXPIRE_DAYS} days after it was given."
                + (f" You are also timed out for {WARN_TIMEOUT_HOURS} hours." if timed_out else ""))
        await dm_member(member, guild, f"You received warning {count}/{MAX_WARNINGS} in {guild.name}", reason, 0xF5A623, info)
        if count >= MAX_WARNINGS:
            ar = guild.get_role(ADMIN_ROLE_ID)
            try:
                await get_log_channel(i).send(f"{ar.mention if ar else 'High rank'} {member.mention} now has **{count}** active warnings. The high rank needs to decide on a ban.",
                                              allowed_mentions=discord.AllowedMentions(roles=[ar] if ar else False, users=False, everyone=False))
            except discord.HTTPException:
                pass
            await i.followup.send(f"ℹ️ {member.mention} now has **{count}** active warnings. You can't ban, so I pinged the high rank.", ephemeral=True)

    @app_commands.command(name="unwarn", description="Remove the most recent active warning from a member.")
    @app_commands.describe(member="The member", reason="Why the warning is being removed")
    @app_commands.guild_only()
    @require_tier("staff")
    async def unwarn(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        p = target_problem(i, member, needs_bot_rank=False)
        if p:
            return await reject(i, "unwarn", member, p)
        orphan = False
        async with warning_lock:
            active = active_warning_records(member.id)
            if active:
                case_no, r = active[-1]
                r.update(warning_active=False, warning_cleared_at=discord.utils.utcnow().isoformat(), warning_cleared_by_id=i.user.id,
                         warning_cleared_by_tag=str(i.user), warning_clear_method="manual_unwarn", warning_clear_reason=reason)
                save_db()
                remaining = active_warning_count(member.id)
            else:
                level = role_warning_level(i.guild, member)
                if level == 0:
                    return await i.followup.send(f"ℹ️ {member.mention} has no active warnings.", ephemeral=True)
                orphan, case_no, remaining = True, None, level - 1
            await update_warning_roles(i.guild, member, min(remaining, MAX_WARNINGS - 1))
        await post_warning_cleared_card(i.guild, member, i.user, f"Warning removed: {reason[:1].upper() + reason[1:]}"[:300], [case_no] if case_no else [])
        await dm_member(member, i.guild, f"A warning was removed in {i.guild.name}", reason, 0x57F287, f"Active warnings left: {remaining}/{MAX_WARNINGS}.")
        if orphan:
            await i.followup.send(f"🟢 Warning removed from {member.mention}. Active warnings remaining: **{remaining}**.\nℹ️ I had no saved record of that warning, so I only lowered their Warn role.", ephemeral=True)
        else:
            await i.followup.send(f"🟢 Warning ELT-{case_no:04d} removed from {member.mention}. Active warnings remaining: **{remaining}**.", ephemeral=True)

    @app_commands.command(name="mute", description="Time a member out and log a mute card.")
    @app_commands.describe(member="The member", minutes="How long, in minutes", reason="Why")
    @app_commands.guild_only()
    @require_tier("mod")
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: app_commands.Range[str, 1, 300]):
        await interaction.response.defer(ephemeral=True)
        p = target_problem(interaction, member)
        if p:
            return await reject(interaction, "mute", member, p)
        await _apply_timeout(interaction, member, timedelta(minutes=minutes), reason, "mute", "MUTE", f"for {minutes}m")

    @app_commands.command(name="timeout", description="Time a member out (minutes, hours or days) and log a timeout card.")
    @app_commands.describe(member="The member", amount="How long (a number)", unit="Minutes, hours or days", reason="Why")
    @app_commands.choices(unit=[app_commands.Choice(name="Minutes", value="minutes"), app_commands.Choice(name="Hours", value="hours"), app_commands.Choice(name="Days", value="days")])
    @app_commands.guild_only()
    @require_tier("mod")
    async def timeout(self, interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 40320],
                      unit: app_commands.Choice[str], reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        try:
            d = timedelta(**{unit.value: amount})
        except OverflowError:
            d = timedelta(days=9999)
        if d > timedelta(days=28):
            return await reject(i, "timeout", member, "Discord only allows timeouts up to 28 days.")
        p = target_problem(i, member)
        if p:
            return await reject(i, "timeout", member, p)
        await _apply_timeout(i, member, d, reason, "timeout", "TIMEOUT", f"for {amount} {unit.value if amount != 1 else unit.value[:-1]}")

    @app_commands.command(name="kick", description="Kick a member and log a kick card. High rank only.")
    @app_commands.describe(member="The member", reason="Why")
    @app_commands.guild_only()
    @require_tier(KICK_MIN_TIER)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        p = target_problem(i, member)
        if p:
            return await reject(i, "kick", member, p)
        dm = await dm_member(member, i.guild, f"You were kicked from {i.guild.name}", reason, 0xFF7043)
        try:
            await member.kick(reason=audit_reason(reason, "kicked", i.user))
        except discord.Forbidden:
            await undo_dm(dm)
            return await reject(i, "kick", member, "I do not have permission to kick that member.")
        except discord.HTTPException as e:
            await undo_dm(dm)
            return await i.followup.send(f"⚠️ {bold('Failed to kick:')} {e}", ephemeral=True)
        await issue_punishment(i, member, "KICK", reason)

    @app_commands.command(name="ban", description="Ban a member and log a ban card. High rank only.")
    @app_commands.describe(member="Who to ban (paste a user ID if they already left)", reason="Why")
    @app_commands.guild_only()
    @require_tier(BAN_MIN_TIER)
    async def ban(self, interaction: discord.Interaction, member: discord.User, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        g = i.guild
        inside = g.get_member(member.id)
        if inside is not None:
            p = target_problem(i, inside)
        elif member.id == i.user.id:
            p = "You can't punish yourself."
        elif bot.user and member.id == bot.user.id:
            p = "I can't punish myself."
        elif member.id == g.owner_id:
            p = "You can't punish the server owner."
        else:
            p = None
        if p:
            return await reject(i, "ban", member, p)
        dm = await dm_member(member, g, f"You were banned from {g.name}", reason, 0xE53935) if inside else None
        try:
            await g.ban(member, reason=audit_reason(reason, "banned", i.user))
        except discord.Forbidden:
            await undo_dm(dm)
            return await reject(i, "ban", member, "I do not have permission to ban that member.")
        except discord.HTTPException as e:
            await undo_dm(dm)
            return await i.followup.send(f"⚠️ {bold('Failed to ban:')} {e}", ephemeral=True)
        await issue_punishment(i, member, "BAN", reason)

    @app_commands.command(name="blacklist", description="Blacklist a member: remove ALL their roles, give the blacklist role. High rank only.")
    @app_commands.describe(member="The member", reason="Why")
    @app_commands.guild_only()
    @require_tier(BLACKLIST_MIN_TIER)
    async def blacklist(self, interaction: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        g = i.guild
        p = target_problem(i, member)
        if p:
            return await reject(i, "blacklist", member, p)
        role = g.get_role(BLACKLIST_ROLE_ID)
        if role is None:
            return await reject(i, "blacklist", member, "The blacklist role was not found — check BLACKLIST_ROLE_ID.")
        if g.me and role >= g.me.top_role:
            return await reject(i, "blacklist", member, "The blacklist role is above mine — move my role higher.")
        async with blacklist_lock:
            if member.id in blacklist_records:
                return await reject(i, "blacklist", member, "That member is already blacklisted.")
            removed = [r.id for r in removable_roles(member)]
            try:
                await lock_blacklist_roles(member, audit_reason(reason, "blacklisted", i.user))
            except discord.Forbidden:
                return await reject(i, "blacklist", member, "I do not have permission to change that member's roles (check Manage Roles and my role position).")
            except discord.HTTPException as e:
                return await i.followup.send(f"⚠️ {bold('Failed to blacklist:')} {e}", ephemeral=True)
            blacklist_records[member.id] = {"roles": removed, "reason": reason, "by": i.user.id, "by_tag": str(i.user),
                                            "date_text": discord.utils.utcnow().strftime("%d/%m/%Y")}
            save_db()
        await dm_member(member, g, f"You were blacklisted in {g.name}", reason, 0x2B2D31, "All your roles were removed. Only the high rank can lift this.")
        case_no = await issue_punishment(i, member, "BLACKLIST", reason, record_extra={"removed_roles": removed},
                                         confirm_note=f"{bold('Roles removed:')} **{len(removed)}**.")
        if case_no is not None and member.id in blacklist_records:
            blacklist_records[member.id]["case"] = case_no
            save_db()

    @app_commands.command(name="unblacklist", description="Lift a blacklist and give the old roles back. High rank only.")
    @app_commands.describe(member="The member (paste a user ID if they left)", reason="Why")
    @app_commands.guild_only()
    @require_tier(BLACKLIST_MIN_TIER)
    async def unblacklist(self, interaction: discord.Interaction, member: discord.User, reason: app_commands.Range[str, 1, 300]):
        i = interaction
        await i.response.defer(ephemeral=True)
        g = i.guild
        async with blacklist_lock:
            entry = blacklist_records.get(member.id)
            if entry is None:
                return await i.followup.send(f"ℹ️ {member.mention} is not blacklisted.", ephemeral=True)
            target = g.get_member(member.id)
            del blacklist_records[member.id]
            save_db()
            restored = 0
            if target is not None:
                top = g.me.top_role if g.me else None
                restore = [r for r in (g.get_role(x) for x in entry.get("roles", [])) if r and not r.is_default() and not r.managed and (top is None or r < top)]
                try:
                    await target.edit(roles=kept_roles(target) + restore, reason=audit_reason(reason, "unblacklisted", i.user))
                    restored = len(restore)
                except discord.HTTPException as e:
                    blacklist_records[member.id] = entry
                    save_db()
                    return await i.followup.send(f"⚠️ {bold('Failed to give the roles back:')} {e}", ephemeral=True)
                await update_warning_roles(g, target, min(active_warning_count(target.id), MAX_WARNINGS - 1))
        if target is not None:
            await dm_member(target, g, f"Your blacklist in {g.name} was lifted", reason, 0x57F287, "Your roles were given back.")
        e = discord.Embed(title=bold("Blacklist removed"), color=discord.Color.green(), timestamp=discord.utils.utcnow(),
                          description=f"{member.mention} was removed from the blacklist by {i.user.mention}.")
        e.add_field(name=bold("Reason"), value=short(reason, 300), inline=False)
        if target is not None:
            e.add_field(name=bold("Roles restored"), value=str(restored))
        e.set_thumbnail(url=member.display_avatar.url)
        try:
            await get_log_channel(i).send(embed=e, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass
        await i.followup.send(f"🟢 {member.mention} is no longer blacklisted. " + (f"{restored} roles restored." if target is not None else "They are not in the server, so no roles were given."), ephemeral=True)

    @app_commands.command(name="blacklisted", description="Show everyone who is currently blacklisted.")
    @app_commands.guild_only()
    @require_tier("mod")
    async def blacklisted(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not blacklist_records:
            return await interaction.followup.send("ℹ️ Nobody is blacklisted.", ephemeral=True)
        rows = sorted(blacklist_records.items(), key=lambda kv: kv[1].get("case", 0), reverse=True)
        lines = [f"<@{u}> • {e.get('date_text', '?')} • by <@{e.get('by', 0)}>\n{short(e.get('reason', ''), 120)}" for u, e in rows[:15]]
        emb = discord.Embed(title=bold("Blacklisted members"), description="\n\n".join(lines)[:4000], color=discord.Color.dark_grey())
        emb.set_footer(text=f"Showing {min(len(rows), 15)} of {len(rows)}")
        await interaction.followup.send(embed=emb, ephemeral=True)

    @app_commands.command(name="warnings", description="Show a member's active warnings and when they clear.")
    @app_commands.describe(member="The member to check")
    @app_commands.guild_only()
    @require_tier("mod")
    async def warnings(self, interaction: discord.Interaction, member: discord.User):
        await interaction.response.defer(ephemeral=True)
        active = active_warning_records(member.id)
        if not active:
            return await interaction.followup.send(f"ℹ️ {member.mention} has no active warnings.", ephemeral=True)
        lines = [f"**ELT-{c:04d}** • {r['date_text']} • by <@{r['punisher_id']}>\n{short(r['reason'], 150)}" for c, r in active]
        e = discord.Embed(title=bold(f"Active warnings — {card_name(member)}"), description="\n\n".join(lines)[:4000], color=discord.Color.orange())
        times = [t for t in (_parse_warning_time(r) for _, r in active) if t]
        if times:
            e.add_field(name=bold("Next warning expires"), value=discord.utils.format_dt(min(times) + timedelta(days=WARNING_EXPIRE_DAYS), "R"), inline=False)
        e.set_footer(text=f"{len(active)}/{MAX_WARNINGS} active warnings")
        e.set_thumbnail(url=member.display_avatar.url)
        await interaction.followup.send(embed=e, ephemeral=True)

    @app_commands.command(name="history", description="Show a member's last punishment cases.")
    @app_commands.describe(member="The member (paste a user ID if they left)")
    @app_commands.guild_only()
    @require_tier("mod")
    async def history(self, interaction: discord.Interaction, member: discord.User):
        await interaction.response.defer(ephemeral=True)
        cases = sorted(((c, r) for c, r in punishment_records.items() if r.get("user_id") == member.id), key=lambda it: it[0], reverse=True)
        if not cases:
            return await interaction.followup.send(f"ℹ️ No cases found for {member.mention}.", ephemeral=True)
        lines = [f"`ELT-{c:04d}` **{r.get('display_type', r['type']).title()}** • {r['date_text']}\n{short(r['reason'], 100)}" for c, r in cases[:15]]
        e = discord.Embed(title=bold(f"Case history — {card_name(member)}"), description="\n\n".join(lines)[:4000], color=discord.Color.blurple())
        e.set_footer(text=f"Showing {min(len(cases), 15)} of {len(cases)} cases")
        e.set_thumbnail(url=member.display_avatar.url)
        await interaction.followup.send(embed=e, ephemeral=True)


async def setup(b):
    await b.add_cog(Punishments(b), guild=GUILD)
