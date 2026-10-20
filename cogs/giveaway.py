"""Giveaways: /gstart /gend /greroll /gcancel /glist (need Manage Server). Entries use the Enter button."""
import json
import os
import random
import re
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import GUILD, banner_file, banner_path, banner_name

DATA_FILE = os.getenv("GIVEAWAY_DATA_FILE") or os.getenv("DATA_FILE") or (
    os.path.join(os.environ["RAILWAY_VOLUME_MOUNT_PATH"], "giveaways.json") if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") else "giveaways.json")
COLOR, END_COLOR, WIN_COLOR = 0x7C3AED, 0x3B1F66, 0x8B5CF6
GIFT_EMOJI = discord.PartialEmoji(name="ELTgift", id=1557224616861106186)
DM_WINNERS = os.getenv("DM_WINNERS", "1") != "0"
KEEP_ENDED_DAYS = 30
MAX_PRIZE_LEN = 200
FOOTER = "ELITE SYSTEM"
giveaways: dict[str, dict] = {}
bot = None


def load():
    global giveaways
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            giveaways = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        giveaways = {}


def save():
    try:
        os.makedirs(os.path.dirname(DATA_FILE) or ".", exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(giveaways, f)
        os.replace(tmp, DATA_FILE)
    except OSError as e:
        print(f"[giveaway] couldn't save {DATA_FILE}: {e}")


def parse_duration(text: str) -> Optional[int]:
    text = text.lower().strip()
    m = re.findall(r"(\d+)([smhdw])", text)
    if not m or re.sub(r"\d+[smhdw]", "", text):
        return None
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = sum(int(n) * mult[u] for n, u in m)
    return total or None


def note(text, color=COLOR):
    return discord.Embed(description=text, color=color)


def build_embed(g: dict) -> discord.Embed:
    ended, n = g["ended"], len(g["entries"])
    e = discord.Embed(title=g["prize"], color=END_COLOR if ended else COLOR)
    if banner_path("giveaway"):
        e.set_image(url=f"attachment://{banner_name('giveaway')}")
    e.set_author(name="GIVEAWAY ENDED" if ended else "GIVEAWAY", icon_url=g.get("host_avatar") or None)
    L = []
    if ended:
        w = g.get("winner_ids", [])
        L += [f"**Hosted by**  <@{g['host_id']}>", f"**Ended**  <t:{g.get('ended_ts', g['end_ts'])}:R>", ""]
        if w:
            L.append(f"**{'Winner' if len(w) == 1 else 'Winners'}**")
            L += [f"<@{i}>" for i in w]
        else:
            L.append("**No valid entries**")
        if g.get("winner_avatars"):
            e.set_thumbnail(url=g["winner_avatars"][0])
    else:
        L += ["Click the button below to enter!", "", f"**Hosted by**  <@{g['host_id']}>", f"**Winners**  {g['winners']}"]
        if g.get("required_role"):
            L.append(f"**Required role**  <@&{g['required_role']}>")
        L.append(f"**Ends**  <t:{g['end_ts']}:R>  (<t:{g['end_ts']}:f>)")
    L += ["", f"## {n:,} {'Entry' if n == 1 else 'Entries'}"]
    e.description = "\n".join(L)
    e.set_footer(text=FOOTER)
    e.timestamp = datetime.fromtimestamp(g.get("ended_ts", g["end_ts"]) if ended else g["end_ts"], tz=timezone.utc)
    return e


def jump_url(g, mid):
    return f"https://discord.com/channels/{g['guild_id']}/{g['channel_id']}/{mid}"


async def resolve_user(guild, uid):
    user = (guild.get_member(uid) if guild else None) or bot.get_user(uid)
    if user is None:
        try:
            user = await bot.fetch_user(uid)
        except discord.HTTPException:
            return None
    return user


avatar_url = lambda u: u.display_avatar.with_size(256).url if u else None


async def still_valid(guild, uid, role_id):
    if guild is None:
        return True
    m = guild.get_member(uid)
    if m is None:
        try:
            m = await guild.fetch_member(uid)
        except discord.NotFound:
            return False
        except discord.HTTPException:
            return True
    return not (role_id and not any(r.id == role_id for r in m.roles))


async def pick_winners(guild, g, count, exclude=()):
    pool = [u for u in g["entries"] if u not in exclude]
    random.shuffle(pool)
    out = []
    for uid in pool:
        if len(out) >= count:
            break
        if await still_valid(guild, uid, g.get("required_role")):
            out.append(uid)
    return out


def winner_embed(user, uid, g, url):
    e = discord.Embed(title="Congratulations", description=f"## <@{uid}>\nYou won **{g['prize']}**!\n\n[View the giveaway]({url})",
                      color=WIN_COLOR, timestamp=discord.utils.utcnow())
    if avatar_url(user):
        e.set_thumbnail(url=avatar_url(user))
    e.set_footer(text=FOOTER)
    return e


async def announce_winners(guild, msg, g, winner_ids, url):
    users = [await resolve_user(guild, uid) for uid in winner_ids]
    for s in range(0, len(winner_ids), 10):
        ids = winner_ids[s:s + 10]
        try:
            await msg.reply(content=" ".join(f"<@{u}>" for u in ids), embeds=[winner_embed(users[s + k], uid, g, url) for k, uid in enumerate(ids)],
                            mention_author=False, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False, replied_user=False))
        except discord.HTTPException as e:
            print(f"[giveaway] couldn't post the winner message: {e}")
    if DM_WINNERS:
        for u in users:
            if u is None or getattr(u, "bot", False):
                continue
            dm = discord.Embed(title="You won a giveaway", color=WIN_COLOR,
                               description=f"You won **{g['prize']}** in **{guild.name if guild else 'the server'}**.\n\n[Go to the giveaway]({url})")
            dm.set_thumbnail(url=avatar_url(u))
            dm.set_footer(text=FOOTER)
            try:
                await u.send(embed=dm)
            except (discord.Forbidden, discord.HTTPException):
                pass


async def get_message(g, mid):
    ch = bot.get_channel(g["channel_id"])
    if ch is None:
        try:
            ch = await bot.fetch_channel(g["channel_id"])
        except discord.HTTPException:
            return None
    try:
        return await ch.fetch_message(int(mid))
    except discord.HTTPException:
        return None


class GiveawayView(discord.ui.View):
    def __init__(self, disabled=False):
        super().__init__(timeout=None)
        self.enter.disabled = disabled
        if disabled:
            self.enter.label = "Ended"

    @discord.ui.button(label="Enter", emoji=GIFT_EMOJI, style=discord.ButtonStyle.secondary, custom_id="giveaway:enter")
    async def enter(self, interaction: discord.Interaction, button: discord.ui.Button):
        g = giveaways.get(str(interaction.message.id))
        if not g or g["ended"]:
            return await interaction.response.send_message(embed=note("This giveaway has ended."), ephemeral=True)
        rid = g.get("required_role")
        if rid and not any(r.id == rid for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message(embed=note(f"You need <@&{rid}> to enter this giveaway."), ephemeral=True)
        uid = interaction.user.id
        if uid in g["entries"]:
            g["entries"].remove(uid)
            text = "### You left the giveaway\nClick **Enter** again if you change your mind."
        else:
            g["entries"].append(uid)
            text = f"### You're in\nGood luck! You are one of **{len(g['entries']):,}** entries.\nClick **Enter** again to leave."
        save()
        await interaction.response.send_message(embed=note(text), ephemeral=True)
        try:
            await interaction.message.edit(embed=build_embed(g))
        except discord.HTTPException as e:
            print(f"[giveaway] couldn't update the entry count: {e}")


async def finish(mid):
    g = giveaways.get(mid)
    if not g or g["ended"]:
        return
    g["ended"], g["ended_ts"] = True, int(time.time())
    guild = bot.get_guild(g["guild_id"])
    g["winner_ids"] = await pick_winners(guild, g, g["winners"])
    users = [await resolve_user(guild, uid) for uid in g["winner_ids"]]
    g["winner_avatars"] = [a for a in (avatar_url(u) for u in users) if a]
    save()
    msg = await get_message(g, mid)
    if msg is None:
        return
    try:
        await msg.edit(embed=build_embed(g), view=GiveawayView(disabled=True))
    except discord.HTTPException as e:
        print(f"[giveaway] couldn't edit the ended giveaway: {e}")
    url = jump_url(g, mid)
    if g["winner_ids"]:
        await announce_winners(guild, msg, g, g["winner_ids"], url)
    else:
        try:
            await msg.reply(embed=discord.Embed(title="No winner", color=END_COLOR,
                                                description=f"There were no valid entries for **{g['prize']}**.\n\n[View the giveaway]({url})").set_footer(text=FOOTER),
                            mention_author=False)
        except discord.HTTPException:
            pass


def manage_guild_only():
    async def pred(interaction: discord.Interaction):
        if isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_guild:
            return True
        raise app_commands.CheckFailure("You need **Manage Server** to use this.")
    return app_commands.check(pred)


class Giveaway(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        load()
        self.bot.add_view(GiveawayView())
        self.checker.start()

    async def cog_unload(self):
        self.checker.cancel()

    @tasks.loop(seconds=10)
    async def checker(self):
        now = int(time.time())
        for mid, g in list(giveaways.items()):
            try:
                if not g["ended"] and g["end_ts"] <= now:
                    await finish(mid)
            except Exception:
                print(f"[giveaway] {mid} failed to finish (will retry):")
                traceback.print_exc()
        changed = False
        for mid, g in list(giveaways.items()):
            if g["ended"]:
                if "ended_ts" not in g:
                    g["ended_ts"], changed = now, True
                elif now - g["ended_ts"] > KEEP_ENDED_DAYS * 86400:
                    del giveaways[mid]
                    changed = True
        if changed:
            save()

    @checker.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="gstart", description="Start a giveaway")
    @app_commands.describe(duration="e.g. 30m, 2h, 1d12h", winners="How many winners (1-20)", prize="The prize",
                           required_role="Optional: role needed to enter")
    @app_commands.guild_only()
    @manage_guild_only()
    async def gstart(self, interaction: discord.Interaction, duration: str, winners: app_commands.Range[int, 1, 20],
                     prize: app_commands.Range[str, 1, MAX_PRIZE_LEN], required_role: Optional[discord.Role] = None):
        secs = parse_duration(duration)
        if not secs:
            return await interaction.response.send_message(embed=note("Invalid duration. Use `30m`, `2h`, `1d12h`..."), ephemeral=True)
        g = {"guild_id": interaction.guild.id, "channel_id": interaction.channel.id, "prize": prize, "host_id": interaction.user.id,
             "host_avatar": interaction.user.display_avatar.with_size(128).url, "end_ts": int(time.time()) + secs, "winners": winners,
             "entries": [], "required_role": required_role.id if required_role else None, "ended": False, "winner_ids": []}
        await interaction.response.send_message(embed=note("Giveaway started."), ephemeral=True)
        f = banner_file("giveaway")
        kw = {"file": f} if f else {}
        msg = await interaction.channel.send(embed=build_embed(g), view=GiveawayView(), **kw)
        giveaways[str(msg.id)] = g
        save()

    @app_commands.command(name="gend", description="End a giveaway now")
    @app_commands.describe(message_id="The giveaway message ID")
    @app_commands.guild_only()
    @manage_guild_only()
    async def gend(self, interaction: discord.Interaction, message_id: str):
        g = giveaways.get(message_id.strip())
        if not g or g["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message(embed=note("Giveaway not found."), ephemeral=True)
        if g["ended"]:
            return await interaction.response.send_message(embed=note("Already ended. Use `/greroll`."), ephemeral=True)
        await interaction.response.send_message(embed=note("Ending it now..."), ephemeral=True)
        await finish(message_id.strip())

    @app_commands.command(name="greroll", description="Pick new winner(s) for an ended giveaway")
    @app_commands.describe(message_id="The giveaway message ID", count="How many new winners")
    @app_commands.guild_only()
    @manage_guild_only()
    async def greroll(self, interaction: discord.Interaction, message_id: str, count: app_commands.Range[int, 1, 20] = 1):
        mid = message_id.strip()
        g = giveaways.get(mid)
        if not g or g["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message(embed=note("Giveaway not found."), ephemeral=True)
        if not g["ended"]:
            return await interaction.response.send_message(embed=note("That giveaway hasn't ended yet."), ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        new = await pick_winners(interaction.guild, g, count, exclude=g["winner_ids"])
        if not new:
            return await interaction.followup.send(embed=note("No more valid entries to pick from."), ephemeral=True)
        g["winner_ids"] += new
        users = [await resolve_user(interaction.guild, u) for u in new]
        g.setdefault("winner_avatars", []).extend(a for a in (avatar_url(u) for u in users) if a)
        save()
        msg = await get_message(g, mid)
        if msg is not None:
            try:
                await msg.edit(embed=build_embed(g))
            except discord.HTTPException:
                pass
            await announce_winners(interaction.guild, msg, g, new, jump_url(g, mid))
        await interaction.followup.send(embed=note(f"Picked **{len(new)}** new winner(s)."), ephemeral=True)

    @app_commands.command(name="gcancel", description="Cancel a giveaway (no winners, message deleted)")
    @app_commands.describe(message_id="The giveaway message ID")
    @app_commands.guild_only()
    @manage_guild_only()
    async def gcancel(self, interaction: discord.Interaction, message_id: str):
        mid = message_id.strip()
        g = giveaways.get(mid)
        if not g or g["guild_id"] != interaction.guild.id:
            return await interaction.response.send_message(embed=note("Giveaway not found."), ephemeral=True)
        del giveaways[mid]
        save()
        msg = await get_message(g, mid)
        if msg is not None:
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
        await interaction.response.send_message(embed=note(f"Giveaway **{g['prize']}** was cancelled."), ephemeral=True)

    @app_commands.command(name="glist", description="Show the active giveaways")
    @app_commands.guild_only()
    @manage_guild_only()
    async def glist(self, interaction: discord.Interaction):
        active = [(m, g) for m, g in giveaways.items() if not g["ended"] and g["guild_id"] == interaction.guild.id]
        if not active:
            return await interaction.response.send_message(embed=note("No active giveaways."), ephemeral=True)
        e = discord.Embed(title="Active giveaways", color=COLOR)
        e.description = "\n\n".join(f"**{g['prize']}**\n<#{g['channel_id']}>  •  ends <t:{g['end_ts']}:R>  •  {len(g['entries']):,} entries\n`{m}`"
                                    for m, g in active)[:4000]
        e.set_footer(text=FOOTER)
        await interaction.response.send_message(embed=e, ephemeral=True)


async def setup(b):
    await b.add_cog(Giveaway(b), guild=GUILD)
