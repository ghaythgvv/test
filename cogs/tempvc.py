"""
Temporary voice channels (ported from the Node bot): join-to-create rooms, control panel in each room's chat,
synced emoji on channel + nicknames, trusted list, auto-delete timer, live stats dashboard.   Command: /tempvc-setup
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import GUILD, GUILD_ID, MOD_ROLE_ID, banner_file, banner_name, banner_path

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
FILE = os.path.join(DATA_DIR, "tempvc.json")
PANEL_COLOR = 0x9B59B6
STATIC_EMOJI_SYNC_CHANNEL_IDS = {1517941337700176003, 1543001094781665370, 1513904253423587451, 1519068432316760286, 1543346189276160241}
NICKNAME_EMOJI_REPLACEMENTS = {"✅": "☑️"}
bot = None

# ------------------------------------------------------------------ storage
store = {"userEmojis": {}, "userSettings": {}, "tempChannels": {}, "guildConfig": {}}
try:
    with open(FILE, encoding="utf-8") as f:
        store.update(json.load(f))
except FileNotFoundError:
    pass
except Exception as e:
    print(f"[tempvc] couldn't read {FILE}: {e}")


def save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(tmp, FILE)
    except OSError as e:
        print(f"[tempvc] couldn't save: {e}")


get_temp = lambda cid: store["tempChannels"].get(str(cid))


def set_temp(cid, data):
    store["tempChannels"][str(cid)] = data
    save()


def del_temp(cid):
    store["tempChannels"].pop(str(cid), None)
    save()


def cfg(gid) -> dict:
    return store["guildConfig"].get(str(gid)) or {}


def set_cfg(gid, **kw):
    store["guildConfig"].setdefault(str(gid), {}).update(kw)
    save()


# ------------------------------------------------------------------ text + emoji helpers
def bold(s):
    out = []
    for ch in str(s or ""):
        c = ord(ch)
        out.append(chr(0x1D5D4 + c - 65) if 65 <= c <= 90 else chr(0x1D5EE + c - 97) if 97 <= c <= 122 else chr(0x1D7EC + c - 48) if 48 <= c <= 57 else ch)
    return "".join(out)


_RAW = ("🦢 Swan|💖 Sparkling Heart|🦋 Butterfly|🫧 Bubbles|🎀 Ribbon|💕 Two Hearts|🌸 Cherry Blossom|🐥 Baby Chick|💌 Love Letter|"
        "🕯️ Candle|✨ Sparkles|🍭 Lollipop|😍 Heart Eyes|😋 Yum Face|🥶 Cold Face|💀 Skull|👾 Alien Monster|👻 Ghost|👽 Alien|🤡 Clown|"
        "🙈 See No Evil|🔥 Fire|🎊 Confetti|😻 Cat Heart Eyes|💋 Kiss Mark|👀 Eyes|🫀 Heart|🫂 Hug|🪷 Lotus|🍁 Maple Leaf|🍄 Mushroom|"
        "🌼 Blossom|☘️ Shamrock|⭐ Star|❄️ Snowflake|🪐 Planet|🌍 Earth|🐉 Dragon|🐈 Cat|🐈‍⬛ Black Cat|🐍 Snake|🪼 Jellyfish|🐙 Octopus|"
        "🐚 Shell|🦂 Scorpion|🕷️ Spider|🕸️ Spider Web|🐾 Paw Prints|🍓 Strawberry|🍬 Candy|🧁 Cupcake|🎧 Headphones|🔋 Battery|👑 Crown|"
        "💭 Thought Bubble|⚜️ Fleur-de-lis|♾️ Infinity")
PALETTE = [tuple(x.split(" ", 1)) for x in _RAW.split("|")]
random_emoji = lambda: random.choice(PALETTE)[0]

_CH = ("\U0001F000-\U0001FAFF\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u21AA\u221E\u231A-\u23FF\u24C2\u25AA-\u25FE\u2600-\u27BF"
       "\u2934\u2935\u2B00-\u2BFF\u3030\u303D\u3297\u3299")
_UNIT = f"[{_CH}](?:\uFE0F|[\U0001F3FB-\U0001F3FF])?(?:\u200D[{_CH}](?:\uFE0F|[\U0001F3FB-\U0001F3FF])?)*"
LEADING_RE = re.compile(f"^{_UNIT}")
PREFIX_RE = re.compile(f"^{_UNIT}\\s*")


def strip_prefixes(name):
    r = str(name or "")
    for _ in range(10):
        n = PREFIX_RE.sub("", r)
        if n == r:
            break
        r = n
    return r


def channel_leading_emoji(ch):
    m = re.match(f"[{_CH}]\uFE0F?", ch.name or "")
    if not m:
        return None
    return NICKNAME_EMOJI_REPLACEMENTS.get(m.group(0)) or NICKNAME_EMOJI_REPLACEMENTS.get(m.group(0).replace("\uFE0F", "")) or m.group(0)


def clip_name(text, n):
    return text if len(text) <= n else text[:n].rstrip()


# ------------------------------------------------------------------ nickname sync (one change per member at a time)
_chains: dict[int, asyncio.Lock] = {}
_recent: dict[str, list] = {}
_norm = lambda s: str(s or "").replace("\uFE0F", "").strip()


async def apply_nickname(member: discord.Member, emoji):
    fallback = member.global_name or member.name
    cur = member.nick
    if emoji:
        base = strip_prefixes(cur if cur is not None else fallback).strip() or fallback
        prefix = f"{emoji} "
        target = prefix + clip_name(base, max(1, 32 - len(prefix)))
        if _norm(target) == _norm(cur):
            return False
    else:
        if not cur:
            return False
        clean = strip_prefixes(cur).strip()
        if clean == cur:
            return False
        target = clean if clean and clean != fallback else None
    me = member.guild.me
    if me is None or not me.guild_permissions.manage_nicknames:
        return False
    if member.id == member.guild.owner_id or member.top_role >= me.top_role:
        return False
    now = time.time()
    lst = [t for t in _recent.get(f"{member.guild.id}:{member.id}", []) if now - t < 60]
    if len(lst) >= 4:
        return False
    lst.append(now)
    _recent[f"{member.guild.id}:{member.id}"] = lst
    try:
        await member.edit(nick=target, reason="TempVC emoji sync")
        return True
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't update {member}: {e}")
        return False


async def sync_nickname(guild, member_id, resolve):
    async with _chains.setdefault(member_id, asyncio.Lock()):
        try:
            member = await guild.fetch_member(member_id)
        except discord.HTTPException:
            return False
        emoji = resolve(member)
        if emoji is ...:
            return False
        return await apply_nickname(member, emoji)


apply_emoji = lambda m, e: sync_nickname(m.guild, m.id, lambda _m: e)
remove_emoji = lambda m: sync_nickname(m.guild, m.id, lambda _m: None)

# ------------------------------------------------------------------ permissions
OWNER_PERMS = dict(view_channel=True, connect=True, speak=True, send_messages=True, read_message_history=True)


async def update_owner_permissions(ch, old_id, new_id):
    try:
        if old_id and old_id != new_id:
            await ch.set_permissions(discord.Object(int(old_id)), overwrite=None)
        if new_id:
            await ch.set_permissions(discord.Object(int(new_id)), overwrite=discord.PermissionOverwrite(**OWNER_PERMS))
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't update the owner overwrite: {e}")


async def setup_permissions(ch: discord.VoiceChannel, guild, owner_id):
    try:
        await ch.edit(sync_permissions=True)
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't sync permissions: {e}")
    try:
        ow = ch.overwrites_for(guild.default_role)
        ow.view_channel = True
        await ch.set_permissions(guild.default_role, overwrite=ow)
        mod = guild.get_role(MOD_ROLE_ID)
        if mod:
            await ch.set_permissions(mod, overwrite=discord.PermissionOverwrite(**OWNER_PERMS))
    except discord.HTTPException as e:
        print(f"[tempvc] permission setup failed: {e}")
    await update_owner_permissions(ch, None, owner_id)


# ------------------------------------------------------------------ panel
I = lambda n: discord.PartialEmoji(name=n[1], id=int(n[0]))
EM = {"lock": ("1553472894976393246", "lock"), "unlock": ("1553483080998588456", "unlock"), "trust": ("1553472890601734325", "trust"),
      "untrust": ("1553472897757224980", "untrust"), "rename": ("1553472888906977300", "rename"), "limit": ("1553472892111560754", "limit"),
      "emoji": ("1553472899589873784", "change_emoji"), "kick": ("1553472901301280870", "kick"), "transfer": ("1553472893583888475", "transfer"),
      "timer": ("1553596332025974864", "timer"), "delete": ("1553472896398270574", "delete"), "check": ("1553555472811040788", "positivo"),
      "owner": ("1554186011809021953", "owner")}
TXT = {k: f"<:{v[1]}:{v[0]}>" for k, v in EM.items()}
TITLE_EMOJI = "<a:156218darkpurplesparklybutterfly:1553826933890879518>"
INTERVALS = [(0, "Off"), (3, "Every 3 minutes"), (5, "Every 5 minutes"), (10, "Every 10 minutes"), (30, "Every 30 minutes"), (60, "Every 1 hour")]


def panel_embed(owner: discord.Member | None, t: dict) -> discord.Embed:
    lock = f"{TXT['lock']} {bold('Locked')}" if t.get("locked") else f"{TXT['unlock']} {bold('Unlocked')}"
    limit = bold(f"Limit {t['limit']}") if t.get("limit") else bold("No limit")
    iv = t.get("cleanupIntervalMinutes")
    if not iv:
        timer = bold("Auto-delete off")
    else:
        nxt = int(((t.get("lastPurgeAt") or t.get("createdAt") or time.time() * 1000) / 1000) + iv * 60)
        timer = f"{bold(f'Auto-delete every {iv}m (next')} <t:{nxt}:R>)"
    owner_line = f"{TXT['owner']} {bold('Owner:')} <@{t['ownerId']}>" if t.get("ownerId") else f"{TXT['owner']} {bold('Owner: unknown')}"
    e = discord.Embed(color=PANEL_COLOR, description="\n".join([
        f"{TITLE_EMOJI} {bold('ELT CONTROL PANEL')}", "",
        "  •  ".join([lock, limit, f"{bold('Emoji')} {t.get('emoji') or bold('none')}", timer]), "", owner_line, "", bold("Owner-only controls below.")]))
    if banner_path("tempvc"):
        e.set_image(url=f"attachment://{banner_name('tempvc')}")
    if owner:
        e.set_thumbnail(url=owner.display_avatar.url)
    elif t.get("ownerId"):
        e.set_footer(text=bold("Owner has left — use Claim Ownership to take over."))
    return e


def panel_view(owner_present=True, locked=False) -> discord.ui.View:
    v = PanelView()
    v.build(owner_present, locked)
    return v


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def build(self, owner_present, locked):
        self.clear_items()

        def btn(cid, label, emoji, row, style=discord.ButtonStyle.secondary, cb=None):
            b = discord.ui.Button(custom_id=f"tempvc:{cid}", label=bold(label) if label else None, emoji=emoji, style=style, row=row)
            b.callback = cb
            self.add_item(b)
        btn("lock", "Lock / Unlock", I(EM["lock" if locked else "unlock"]), 0, cb=h_lock)
        btn("trust", "Trust", I(EM["trust"]), 0, cb=h_trust)
        btn("untrust", "Untrust", I(EM["untrust"]), 0, cb=h_untrust)
        btn("rename", "Rename", I(EM["rename"]), 1, cb=h_rename)
        btn("limit", "Limit", I(EM["limit"]), 1, cb=h_limit)
        btn("emoji", "Change Emoji", I(EM["emoji"]), 1, cb=h_emoji)
        btn("kick", "Kick", I(EM["kick"]), 2, cb=h_kick)
        if owner_present:
            btn("transfer", "Transfer Ownership", I(EM["owner"]), 2, cb=h_transfer)
        else:
            btn("claim", "Claim Ownership", I(EM["owner"]), 2, style=discord.ButtonStyle.primary, cb=h_claim)
        btn("age", "18+", None, 2, cb=h_age)
        btn("timer", "Auto-Delete Timer", I(EM["timer"]), 3, cb=h_timer)
        btn("delete", "Delete", I(EM["delete"]), 3, cb=h_delete)
        return self


def saved(summary):
    return f"{TXT['check']} {bold('Saved')} — {summary}, {bold("and it'll carry over next time your channel gets recreated.")}"


def owned(i: discord.Interaction):
    vc = i.user.voice.channel if isinstance(i.user, discord.Member) and i.user.voice else None
    if vc is None:
        return None, None, bold("You need to be in a temp voice channel to do that.")
    t = get_temp(vc.id)
    if not t:
        return None, None, bold("That voice channel isn't a temp channel.")
    return vc, t, None


def need_owner(i, t, require=True):
    vc, t2, err = owned(i)
    if err:
        return None, None, err
    if require and t2["ownerId"] != str(i.user.id) and t2["ownerId"] != i.user.id:
        return None, None, bold("Only the channel owner can do that.")
    return vc, t2, None


def snapshot(t):
    if not t or not t.get("ownerId") or t.get("type") == "game":
        return
    store["userSettings"].setdefault(str(t["ownerId"]), {}).update(
        customName=t.get("customName"), limit=t.get("limit") or 0, locked=bool(t.get("locked")), trusted=t.get("trusted") or [],
        cleanupIntervalMinutes=t["cleanupIntervalMinutes"] if isinstance(t.get("cleanupIntervalMinutes"), int) else 10)
    save()


async def refresh_panel(ch: discord.VoiceChannel, t: dict):
    if not t or not t.get("panelMessageId"):
        return
    try:
        owner = ch.guild.get_member(int(t["ownerId"])) or await ch.guild.fetch_member(int(t["ownerId"]))
    except (discord.HTTPException, ValueError):
        owner = None
    present = bool(owner and owner in ch.members)
    emb, view = panel_embed(owner, t), panel_view(present, bool(t.get("locked")))
    try:
        msg = await ch.fetch_message(int(t["panelMessageId"]))
        await msg.edit(embed=emb, view=view)
    except discord.NotFound:
        try:
            f = banner_file("tempvc")
            msg = await ch.send(embed=emb, view=view, **({"file": f} if f else {}))
            t["panelMessageId"] = msg.id
            set_temp(ch.id, t)
        except discord.HTTPException as e:
            print(f"[tempvc] couldn't repost the panel: {e}")
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't refresh the panel: {e}")


async def destroy_temp(guild, ch, cid, t):
    snapshot(t)
    del_temp(cid)
    if ch:
        try:
            await ch.delete()
        except discord.HTTPException:
            pass
    await refresh_dashboard(guild)


# ---- handlers (all owner-only unless noted)
EPH = dict(ephemeral=True)


async def h_lock(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)
    ow = vc.overwrites_for(vc.guild.default_role)
    was_locked = ow.connect is False
    ow.connect = None if was_locked else False
    await vc.set_permissions(vc.guild.default_role, overwrite=ow)
    t["locked"] = not was_locked
    set_temp(vc.id, t)
    snapshot(t)
    await refresh_panel(vc, t)
    await i.response.send_message(saved(f"{TXT['lock']} {bold('channel locked')}" if t["locked"] else f"{TXT['unlock']} {bold('channel unlocked')}"), **EPH)


async def h_age(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)
    await i.response.defer(**EPH)
    age = bold("18+")
    cur = strip_prefixes(vc.name)
    if cur == age:
        new = t.get("nameBefore18") or bold("Channel")
    else:
        t["nameBefore18"], new = cur, age
    t["customName"] = new
    final = f"{t['emoji']} {new}"[:100]
    set_temp(vc.id, t)
    snapshot(t)
    asyncio.create_task(_rename(vc, final))
    await i.followup.send(saved(bold(f"channel renamed to {final}")), **EPH)


async def _rename(vc, name):
    try:
        await vc.edit(name=name)
    except discord.HTTPException as e:
        print(f"[tempvc] rename failed: {e}")


class RenameModal(discord.ui.Modal, title=bold("Rename channel")):
    name = discord.ui.TextInput(label=bold("New channel name"), max_length=90, required=True)

    async def on_submit(self, i):
        vc, t, err = need_owner(i, None)
        if err:
            return await i.response.send_message(err, **EPH)
        await i.response.defer(**EPH)
        typed = self.name.value.strip()
        styled = bold(strip_prefixes(typed) or typed)
        final = f"{t['emoji']} {styled}"[:100]
        t["customName"] = styled
        set_temp(vc.id, t)
        snapshot(t)
        asyncio.create_task(_rename(vc, final))
        await i.followup.send(f"{TXT['rename']} {bold(f'Renamed to {final}')}.", **EPH)


class LimitModal(discord.ui.Modal, title=bold("Set member limit")):
    limit = discord.ui.TextInput(label=bold("Member limit"), placeholder="0 = no limit, up to 99", max_length=2, required=True)

    async def on_submit(self, i):
        vc, t, err = need_owner(i, None)
        if err:
            return await i.response.send_message(err, **EPH)
        raw = self.limit.value.strip()
        n = max(0, min(99, int(raw) if raw.isdigit() else 0))
        await vc.edit(user_limit=n)
        t["limit"] = n
        set_temp(vc.id, t)
        snapshot(t)
        await refresh_panel(vc, t)
        await i.response.send_message(saved(bold(f"limit set to {n}") if n else bold("limit removed")), **EPH)


async def h_rename(i):
    vc, t, err = need_owner(i, None)
    await (i.response.send_message(err, **EPH) if err else i.response.send_modal(RenameModal()))


async def h_limit(i):
    vc, t, err = need_owner(i, None)
    await (i.response.send_message(err, **EPH) if err else i.response.send_modal(LimitModal()))


class EmojiPicker(discord.ui.View):
    def __init__(self, page=0):
        super().__init__(timeout=300)
        total = max(1, -(-len(PALETTE) // 25))
        self.page = min(max(0, page), total - 1)
        sel = discord.ui.Select(placeholder=bold(f"Choose an emoji ({self.page + 1}/{total})"), options=[
            discord.SelectOption(label=l, value=e, emoji=e) for e, l in PALETTE[self.page * 25:(self.page + 1) * 25]])
        sel.callback = self.picked
        self.add_item(sel)
        if total > 1:
            for label, delta, dis in (("Back", -1, self.page == 0), ("Next", 1, self.page >= total - 1)):
                b = discord.ui.Button(label=bold(label), style=discord.ButtonStyle.secondary, disabled=dis, row=1)
                b.callback = (lambda d: (lambda ii: self.go(ii, d)))(delta)
                self.add_item(b)

    async def go(self, i, delta):
        vc, t, err = need_owner(i, None)
        if err:
            return await i.response.edit_message(content=err, view=None)
        await i.response.edit_message(content=bold("Choose a new emoji — it'll update the channel and everyone in it:"), view=EmojiPicker(self.page + delta))

    async def picked(self, i):
        vc, t, err = need_owner(i, None)
        if err:
            return await i.response.edit_message(content=err, view=None)
        new = i.data["values"][0]
        if new == t.get("emoji"):
            return await i.response.edit_message(content=f"{bold('Already using')} {new}.", view=None)
        await i.response.defer()
        t["emoji"] = new
        set_temp(vc.id, t)
        store["userEmojis"][str(t["ownerId"])] = new
        save()
        asyncio.create_task(_rename(vc, f"{new} {strip_prefixes(vc.name) or bold('Channel')}"[:100]))
        for m in list(vc.members):
            if not m.bot:
                await apply_emoji(m, new)
        await refresh_panel(vc, t)
        await i.edit_original_response(content=saved(f"{bold('emoji set to')} {new}"), view=None)


async def h_emoji(i):
    vc, t, err = need_owner(i, None)
    await (i.response.send_message(err, **EPH) if err else
           i.response.send_message(bold("Choose a new emoji — it'll update the channel and everyone in it:"), view=EmojiPicker(0), **EPH))


def member_select_view(vc, owner_id, placeholder, on_pick):
    others = [m for m in vc.members if str(m.id) != str(owner_id) and not m.bot][:25]
    if not others:
        return None
    v = discord.ui.View(timeout=300)
    s = discord.ui.Select(placeholder=bold(placeholder), options=[discord.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in others])
    s.callback = on_pick
    v.add_item(s)
    return v


async def h_kick(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)

    async def pick(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        tgt = vc2.guild.get_member(int(ii.data["values"][0]))
        if not tgt or tgt not in vc2.members:
            return await ii.response.edit_message(content=bold("They already left."), view=None)
        try:
            await tgt.move_to(None)
        except discord.HTTPException:
            pass
        await ii.response.edit_message(content=f"✖️ {bold('Disconnected')} **{tgt.display_name}**.", view=None)
    v = member_select_view(vc, t["ownerId"], "Choose who to disconnect", pick)
    await (i.response.send_message(bold("Choose who to disconnect from the channel:"), view=v, **EPH) if v else
           i.response.send_message(bold("There's no one else in the channel to kick."), **EPH))


async def h_trust(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)

    async def pick(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        users = ii.data["values"]
        trusted = set(map(str, t2.get("trusted") or [])) | set(users)
        for u in users:
            try:
                await vc2.set_permissions(discord.Object(int(u)), overwrite=discord.PermissionOverwrite(connect=True))
            except discord.HTTPException:
                pass
        t2["trusted"] = list(trusted)
        set_temp(vc2.id, t2)
        snapshot(t2)
        await refresh_panel(vc2, t2)
        n = len(users)
        await ii.response.edit_message(content=saved(bold("1 member trusted") if n == 1 else bold(f"{n} members trusted")), view=None)
    v = discord.ui.View(timeout=300)
    s = discord.ui.UserSelect(placeholder=bold("Choose who to trust"), min_values=1, max_values=25)
    s.callback = pick
    v.add_item(s)
    await i.response.send_message(bold("Choose who can join even while the channel is locked:"), view=v, **EPH)


async def h_untrust(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)
    ids = [str(x) for x in (t.get("trusted") or [])][:25]
    if not ids:
        return await i.response.send_message(bold("No one's trusted right now."), **EPH)

    async def pick(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        targets = set(ii.data["values"])
        t2["trusted"] = [x for x in (t2.get("trusted") or []) if str(x) not in targets]
        set_temp(vc2.id, t2)
        snapshot(t2)
        for u in targets:
            try:
                await vc2.set_permissions(discord.Object(int(u)), overwrite=None)
            except discord.HTTPException:
                pass
        await refresh_panel(vc2, t2)
        n = len(targets)
        await ii.response.edit_message(content=saved(bold("1 member untrusted") if n == 1 else bold(f"{n} members untrusted")), view=None)
    opts = []
    for x in ids:
        m = vc.guild.get_member(int(x))
        opts.append(discord.SelectOption(label=(m.display_name if m else f"User {x}")[:100], value=x))
    v = discord.ui.View(timeout=300)
    s = discord.ui.Select(placeholder=bold("Choose who to untrust"), min_values=1, max_values=len(opts), options=opts)
    s.callback = pick
    v.add_item(s)
    await i.response.send_message(bold("Choose who to remove from the trusted list:"), view=v, **EPH)


async def h_transfer(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)

    async def pick(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        new = vc2.guild.get_member(int(ii.data["values"][0]))
        if not new or new not in vc2.members:
            return await ii.response.edit_message(content=bold("They already left."), view=None)
        old = t2["ownerId"]
        t2["ownerId"] = t2["originalOwnerId"] = str(new.id)
        set_temp(vc2.id, t2)
        await update_owner_permissions(vc2, old, new.id)
        await refresh_panel(vc2, t2)
        await ii.response.edit_message(content=f"♣️ **{new.display_name}** {bold('is now the channel owner.')}", view=None)
    v = member_select_view(vc, t["ownerId"], "Choose the new owner", pick)
    await (i.response.send_message(bold("Choose who to hand ownership to:"), view=v, **EPH) if v else
           i.response.send_message(bold("There's no one else in the channel to hand it to."), **EPH))


async def h_claim(i):
    vc, t, err = owned(i)
    if err:
        return await i.response.send_message(err, **EPH)
    if any(str(m.id) == str(t["ownerId"]) for m in vc.members):
        return await i.response.send_message(bold("The owner is still in the channel."), **EPH)
    old = t["ownerId"]
    t["ownerId"] = str(i.user.id)
    set_temp(vc.id, t)
    await update_owner_permissions(vc, old, i.user.id)
    await refresh_panel(vc, t)
    await i.response.send_message(f"{TXT['owner']} {bold('You are now the channel owner.')}", **EPH)


async def h_timer(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)
    cur = t["cleanupIntervalMinutes"] if isinstance(t.get("cleanupIntervalMinutes"), int) else 10

    async def pick(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        mins = int(ii.data["values"][0])
        t2["cleanupIntervalMinutes"], t2["lastPurgeAt"] = mins, int(time.time() * 1000)
        set_temp(vc2.id, t2)
        snapshot(t2)
        await refresh_panel(vc2, t2)
        await ii.response.edit_message(content=saved(bold(f"auto-delete set to every {mins}m") if mins else bold("auto-delete turned off")), view=None)
    v = discord.ui.View(timeout=300)
    s = discord.ui.Select(placeholder=bold("Choose how often to auto-delete messages"), options=[
        discord.SelectOption(label=bold(l), value=str(m), default=(m == cur)) for m, l in INTERVALS])
    s.callback = pick
    v.add_item(s)
    await i.response.send_message(bold("Automatically clear this channel's chat on a schedule (the panel is never deleted):"), view=v, **EPH)


async def h_delete(i):
    vc, t, err = need_owner(i, None)
    if err:
        return await i.response.send_message(err, **EPH)

    async def yes(ii):
        vc2, t2, e2 = need_owner(ii, None)
        if e2:
            return await ii.response.edit_message(content=e2, view=None)
        await ii.response.edit_message(content=f"➖ {bold('Channel deleted.')}", view=None)
        await destroy_temp(vc2.guild, vc2, vc2.id, t2)

    async def no(ii):
        await ii.response.edit_message(content=bold("Delete cancelled."), view=None)
    v = discord.ui.View(timeout=60)
    b1 = discord.ui.Button(label=bold("Yes, delete it"), style=discord.ButtonStyle.danger)
    b2 = discord.ui.Button(label=bold("Cancel"), style=discord.ButtonStyle.secondary)
    b1.callback, b2.callback = yes, no
    v.add_item(b1)
    v.add_item(b2)
    await i.response.send_message(f"⚠️ {bold('This will permanently delete the channel. Are you sure?')}", view=v, **EPH)


# ------------------------------------------------------------------ channel creation / cleanup / dashboard
pending_deletions: set[int] = set()


async def create_temp_channel(member: discord.Member, guild: discord.Guild, c: dict):
    emoji = store["userEmojis"].get(str(member.id)) or random_emoji()
    saved_ = store["userSettings"].get(str(member.id))
    base = (saved_ or {}).get("customName") or f"{strip_prefixes(member.display_name)}'s Channel"
    name = re.sub(r"\s+", " ", f"{emoji} {base}").strip()[:100]
    cat = guild.get_channel(c.get("categoryId") or 0)
    limit = (saved_ or {}).get("limit") or 0
    try:
        ch = await guild.create_voice_channel(name, category=cat, user_limit=limit)
    except discord.HTTPException:
        try:
            ch = await guild.create_voice_channel(f"{emoji} Channel", category=cat, user_limit=limit)
        except discord.HTTPException as e:
            return print(f"[tempvc] couldn't create a channel for {member}: {e}")
    t = {"guildId": str(guild.id), "ownerId": str(member.id), "emoji": emoji, "customName": (saved_ or {}).get("customName"), "limit": limit,
         "locked": bool((saved_ or {}).get("locked")), "trusted": (saved_ or {}).get("trusted") or [],
         "cleanupIntervalMinutes": (saved_ or {}).get("cleanupIntervalMinutes") if isinstance((saved_ or {}).get("cleanupIntervalMinutes"), int) else 10,
         "lastPurgeAt": int(time.time() * 1000), "createdAt": int(time.time() * 1000)}
    set_temp(ch.id, t)
    await setup_permissions(ch, guild, member.id)
    if t["locked"]:
        ow = ch.overwrites_for(guild.default_role)
        ow.connect = False
        await ch.set_permissions(guild.default_role, overwrite=ow)
    for u in t["trusted"]:
        try:
            await ch.set_permissions(discord.Object(int(u)), overwrite=discord.PermissionOverwrite(connect=True))
        except discord.HTTPException:
            pass
    try:
        f = banner_file("tempvc")
        msg = await ch.send(embed=panel_embed(member, t), view=panel_view(True, t["locked"]), **({"file": f} if f else {}))
        t["panelMessageId"] = msg.id
        set_temp(ch.id, t)
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't post the panel in {ch.name}: {e}")
    try:
        await member.move_to(ch)
    except discord.HTTPException as e:
        print(f"[tempvc] couldn't move {member}: {e}")
        try:
            await ch.delete()
        except discord.HTTPException:
            pass
        del_temp(ch.id)
    await refresh_dashboard(guild)


def schedule_empty_check(cid, guild):
    if cid in pending_deletions:
        return
    pending_deletions.add(cid)

    async def run():
        await asyncio.sleep(0)
        pending_deletions.discard(cid)
        t = get_temp(cid)
        ch = guild.get_channel(int(cid))
        if ch is None:
            snapshot(t)
            del_temp(cid)
            return await refresh_dashboard(guild)
        if len(ch.members) == 0:
            await destroy_temp(guild, ch, cid, t)
    asyncio.create_task(run())


async def sweep_empty(b):
    for cid, t in list(store["tempChannels"].items()):
        g = b.get_guild(int(t["guildId"]))
        if not g:
            continue
        ch = g.get_channel(int(cid))
        if ch is None:
            snapshot(t)
            del_temp(cid)
        elif len(ch.members) == 0:
            await destroy_temp(g, ch, cid, t)


async def purge_channel(ch, t):
    if not t.get("panelMessageId"):
        return
    try:
        keep = int(t["panelMessageId"])
        await ch.purge(limit=None, check=lambda m: m.id != keep, bulk=True)
    except discord.HTTPException as e:
        print(f"[tempvc] purge failed in {ch.name}: {e}")


def dashboard_embed(guild: discord.Guild) -> discord.Embed:
    rooms = []
    for cid, t in store["tempChannels"].items():
        if t.get("guildId") == str(guild.id):
            ch = guild.get_channel(int(cid))
            if ch:
                rooms.append((ch, t, len(ch.members)))
    rooms.sort(key=lambda r: -r[2])
    live = [r for r in rooms if r[2] > 0]
    users = sum(r[2] for r in live)
    e = discord.Embed(color=0x7C3AED, title=f"{TITLE_EMOJI} ELITE VOICE",
                      description=f"## {len(live)} {'Room' if len(live) == 1 else 'Rooms'}  ·  {users} Online\n"
                                  f"-# {TXT['lock']} {sum(1 for r in rooms if r[1].get('locked'))} locked  ·  {TXT['unlock']} {sum(1 for r in rooms if not r[1].get('locked'))} open")
    top = live[:5]
    if not top:
        e.add_field(name="Leaderboard", value="*Nobody is in voice right now.*\n*Join the create channel and open the first room.*", inline=False)
    mx = top[0][2] if top else 0
    for n, (ch, t, c) in enumerate(top, 1):
        owner = guild.get_member(int(t["ownerId"])) if t.get("ownerId") else None
        filled = max(1, round(c / mx * 10)) if mx else 0
        e.add_field(name=f"0{n}  {re.sub(r'[*_~|`>]', '', strip_prefixes(ch.name)) or f'Room {n}'}  {TXT['lock'] if t.get('locked') else TXT['unlock']}",
                    value=f"{'▰' * filled}{'▱' * (10 - filled)}  **{c}** / {ch.user_limit or '∞'}\n-# {TXT['owner']} **{owner.display_name if owner else '—'}**",
                    inline=False)
    if banner_path("tempvc"):
        e.set_image(url=f"attachment://{banner_name('tempvc')}")
    e.set_footer(text=f"{guild.name}  ·  updates every 20s  ·  ELITE SYSTEM")
    e.timestamp = discord.utils.utcnow()
    return e


_dash_busy: set[int] = set()


async def refresh_dashboard(guild):
    c = cfg(guild.id)
    if not c.get("statsChannelId") or not c.get("statsMessageId") or guild.id in _dash_busy:
        return
    _dash_busy.add(guild.id)
    try:
        ch = guild.get_channel(int(c["statsChannelId"]))
        if ch:
            await ch.get_partial_message(int(c["statsMessageId"])).edit(embed=dashboard_embed(guild))
    except discord.HTTPException:
        pass
    finally:
        _dash_busy.discard(guild.id)


class TempVC(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        for owner_present in (True, False):
            for locked in (False, True):
                self.bot.add_view(panel_view(owner_present, locked))
        self.stats_loop.start()
        self.cleanup_loop.start()
        self.sweep_loop.start()

    async def cog_unload(self):
        for l in (self.stats_loop, self.cleanup_loop, self.sweep_loop):
            l.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await sweep_empty(self.bot)

    @tasks.loop(seconds=20)
    async def stats_loop(self):
        for g in self.bot.guilds:
            await refresh_dashboard(g)

    @tasks.loop(minutes=5)
    async def sweep_loop(self):
        await sweep_empty(self.bot)

    @tasks.loop(minutes=1)
    async def cleanup_loop(self):
        now = int(time.time() * 1000)
        for cid, t in list(store["tempChannels"].items()):
            iv = t.get("cleanupIntervalMinutes")
            if not iv or now < (t.get("lastPurgeAt") or t.get("createdAt") or 0) + iv * 60000:
                continue
            g = self.bot.get_guild(int(t["guildId"]))
            ch = g.get_channel(int(cid)) if g else None
            if ch is None:
                continue
            await purge_channel(ch, t)
            t["lastPurgeAt"] = now
            set_temp(cid, t)
            await refresh_panel(ch, t)

    @stats_loop.before_loop
    @sweep_loop.before_loop
    @cleanup_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="tempvc-setup", description="Set up (or refresh) the temp voice channel system in this server")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup_cmd(self, interaction: discord.Interaction):
        g = interaction.guild
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("You need **Manage Server**.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        c = cfg(g.id)
        cat = g.get_channel(c.get("categoryId") or 0) or await g.create_category("Temp Channels")
        join = g.get_channel(c.get("joinToCreateId") or 0) or await g.create_voice_channel("➕ Join to Create", category=cat)
        stats = g.get_channel(c.get("statsChannelId") or 0) or await g.create_text_channel(
            "📊│temp-vc-stats", category=cat, overwrites={g.default_role: discord.PermissionOverwrite(send_messages=False)})
        msg = None
        if c.get("statsMessageId"):
            try:
                msg = await stats.fetch_message(int(c["statsMessageId"]))
            except discord.HTTPException:
                msg = None
        if msg is None:
            f = banner_file("tempvc")
            msg = await stats.send(embed=dashboard_embed(g), **({"file": f} if f else {}))
        set_cfg(g.id, categoryId=cat.id, joinToCreateId=join.id, statsChannelId=stats.id, statsMessageId=msg.id)
        await interaction.followup.send(f"✅ Temp VC system is ready! Join <#{join.id}> to create your own channel — the panel shows up in its chat, "
                                        f"and live stats are in <#{stats.id}>.", ephemeral=True)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        if member.bot or before.channel == after.channel:
            return
        old_id = before.channel.id if before.channel else None
        new_id = after.channel.id if after.channel else None
        joined_static, left_static = new_id in STATIC_EMOJI_SYNC_CHANNEL_IDS, old_id in STATIC_EMOJI_SYNC_CHANNEL_IDS
        c = cfg(guild.id)
        old_t = get_temp(old_id) if old_id else None
        if old_t:
            await remove_emoji(member)
            schedule_empty_check(str(old_id), guild)
        elif left_static and not joined_static:
            await remove_emoji(member)
        if joined_static:
            e = channel_leading_emoji(after.channel)
            if e:
                await apply_emoji(member, e)
        if not c:
            return
        if new_id and new_id == c.get("joinToCreateId"):
            return await create_temp_channel(member, guild, c)
        if new_id:
            nt = get_temp(new_id)
            if nt:
                await apply_emoji(member, nt["emoji"])
                ch = after.channel
                if str(nt.get("ownerId")) == str(member.id) or nt.get("ownerId"):
                    await refresh_panel(ch, nt)      # keeps Transfer / Claim button correct


async def setup(b):
    await b.add_cog(TempVC(b), guild=GUILD)
