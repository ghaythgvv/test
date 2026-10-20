"""Support / Report tickets (ported from the Node bot): /ticketpanel (admin). Transcripts go to the log channel + the owner's DMs."""
import html
import io
import os
import re

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, banner_file, banner_name, is_admin

TICKET_CATEGORY_ID = int(os.environ.get("TICKET_CATEGORY_ID") or 1539749321048596610)
LOG_CHANNEL_ID = int(os.environ.get("TICKET_LOG_CHANNEL_ID") or 1539787269769269258)
STAFF_ROLE_IDS = [int(x) for x in (os.environ.get("TICKET_STAFF_ROLE_IDS") or "1513904136783925380").replace(" ", "").split(",") if x.isdigit()]
COLOR = 0x9B59B6

CLAIM_EMOJI = discord.PartialEmoji(name="emoji_14", id=1552077450442186862, animated=True)
CLOSE_EMOJI = discord.PartialEmoji(name="close_ticket", id=1553472894976393246)
DELETE_EMOJI = discord.PartialEmoji(name="delete_ticket", id=1553472896398270574)
UNLOCK_EMOJI = discord.PartialEmoji(name="unlock_ticket", id=1553483080998588456)
SUPPORT_E = "<:865560adminroleicon:1554665054312341514>"
REPORT_E = "<a:purplealert1086520955985199115:1554664860476907550>"
PANEL_E = "<:purple_ticket:1554668288947126282>"
ERROR_E = "<:error_purple:1554671367142645770>"
WARN_E = "<:warn_purple:1554671365490212945>"
CLAIMED_E = "<a:ticket_claimed:1554675609525821531>"
DELETE_E = "<:delete_ticket:1553472896398270574>"
CLOSE_E = "<:close_ticket:1553472894976393246>"
UNLOCK_E = "<:unlock_ticket:1553483080998588456>"
TYPES = {"support": ("Support Ticket", SUPPORT_E), "report": ("Report Ticket", REPORT_E)}
bot = None


def is_staff(m: discord.Member) -> bool:
    return m.guild_permissions.administrator or any(r.id in STAFF_ROLE_IDS for r in m.roles)


def parse_topic(ch):
    t = getattr(ch, "topic", None)
    if not t or not t.startswith("ticket|"):
        return None
    d = {}
    for part in t.split("|")[1:]:
        k, _, v = part.partition("=")
        d[k] = v
    return d


def panel_state(msg: discord.Message):
    row = msg.components[0].children if msg.components else []
    return {"claimed": bool(row and getattr(row[0], "disabled", False)),
            "closed": len(row) > 1 and getattr(row[1], "custom_id", "") == "ticket_reopen"}


def claimed_by(msg):
    if msg.embeds:
        for f in msg.embeds[0].fields:
            if f.name == "Claimed by":
                m = re.search(r"<@!?(\d+)>", f.value)
                return int(m.group(1)) if m else None
    return None


def err(text):
    return f"{ERROR_E} {text}"


# ------------------------------------------------------------------ transcript
def _md(t: str) -> str:
    t = html.escape(t or "")
    t = re.sub(r"```(?:\w+\n)?([\s\S]*?)```", r"<pre>\1</pre>", t)
    t = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)
    return t.replace("\n", "<br>")


async def generate_transcript(channel: discord.TextChannel, meta: dict):
    msgs = [m async for m in channel.history(limit=None, oldest_first=True)]
    rows, text_rows = [], []
    for m in msgs:
        name = getattr(m.author, "display_name", m.author.name)
        t = m.created_at.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        body = f'<div class="content">{_md(m.clean_content)}</div>' if m.content else ""
        for e in m.embeds:
            body += f'<div class="embed"><b>{html.escape(e.title or "")}</b><div>{_md(e.description or "")}</div>' + "".join(
                f'<div class="f"><b>{html.escape(f.name)}</b><br>{_md(f.value)}</div>' for f in e.fields) + "</div>"
        for a in m.attachments:
            body += (f'<div><img class="attach" src="{html.escape(a.url)}"></div>' if (a.content_type or "").startswith("image/")
                     else f'<div>📎 <a href="{html.escape(a.url)}">{html.escape(a.filename)}</a></div>')
        rows.append(f'<div class="msg"><img class="avatar" src="{m.author.display_avatar.replace(size=64, format="png").url}">'
                    f'<div class="body"><span class="name">{html.escape(name)}</span>{" <span class=tag>BOT</span>" if m.author.bot else ""}'
                    f'<span class="time">{t}</span>{body}</div></div>')
        out = [f"[{t[:16]}] {name}{' (BOT)' if m.author.bot else ''}:"]
        if m.clean_content:
            out += ["    " + l for l in m.clean_content.split("\n")]
        for e in m.embeds:
            parts = [p for p in [e.title, e.description, *[f"{f.name}: {f.value}" for f in e.fields]] if p]
            if parts:
                out.append("    [Embed] " + " | ".join(parts).replace("\n", " "))
        for a in m.attachments:
            out.append(f"    [Attachment] {a.filename} - {a.url}")
        text_rows.append("\n".join(out))
    head = "".join(f"<div>{k}: {html.escape(str(v))}</div>" for k, v in meta.items() if v)
    page = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Transcript</title><style>"
            "body{margin:0;background:#313338;color:#dbdee1;font-family:Segoe UI,Arial,sans-serif;font-size:15px}"
            ".top{background:#2b2d31;padding:20px 28px}.top h1{margin:0 0 6px;color:#fff;font-size:20px}.top div{color:#949ba4;font-size:13px}"
            ".wrap{padding:16px 28px 40px;max-width:1000px;margin:auto}.msg{display:flex;gap:14px;padding:8px 0}.avatar{width:40px;height:40px;border-radius:50%}"
            ".name{font-weight:600;color:#fff}.tag{background:#5865f2;color:#fff;font-size:10px;padding:1px 5px;border-radius:4px;margin-left:6px}"
            ".time{color:#949ba4;font-size:12px;margin-left:8px}a{color:#00a8fc}code,pre{background:#1e1f22;padding:2px 5px;border-radius:4px}"
            ".embed{background:#2b2d31;border-left:4px solid #9b59b6;border-radius:4px;padding:10px 14px;margin-top:6px;max-width:520px}"
            ".attach{max-width:400px;max-height:300px;border-radius:6px;margin-top:6px}</style></head><body>"
            f"<div class='top'><h1>#{html.escape(channel.name)}</h1><div>Server: {html.escape(channel.guild.name)}</div>{head}"
            f"<div>Messages: {len(msgs)}</div></div><div class='wrap'>{''.join(rows)}</div></body></html>")
    txt = (f"TICKET TRANSCRIPT - #{channel.name}\nServer: {channel.guild.name}\n" + "".join(f"{k}: {v}\n" for k, v in meta.items() if v)
           + f"Messages: {len(msgs)}\n{'=' * 48}\n\n" + "\n\n".join(text_rows) + "\n")
    return page, txt, len(msgs)


# ------------------------------------------------------------------ buttons
def ticket_buttons(claimed=False, closed=False) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    b1 = discord.ui.Button(custom_id="ticket_claim", label="Claimed" if claimed else "Claim", emoji=CLAIM_EMOJI,
                           style=discord.ButtonStyle.secondary, disabled=claimed)
    b2 = (discord.ui.Button(custom_id="ticket_reopen", label="Reopen", emoji=UNLOCK_EMOJI, style=discord.ButtonStyle.secondary) if closed else
          discord.ui.Button(custom_id="ticket_close", label="Close Ticket", emoji=CLOSE_EMOJI, style=discord.ButtonStyle.secondary))
    b3 = discord.ui.Button(custom_id="ticket_delete", label="Delete Ticket", emoji=DELETE_EMOJI, style=discord.ButtonStyle.secondary)
    b1.callback, b2.callback, b3.callback = handle_claim, (handle_reopen if closed else handle_close), handle_delete
    for b in (b1, b2, b3):
        v.add_item(b)
    return v


async def handle_claim(i: discord.Interaction):
    if not is_staff(i.user):
        return await i.response.send_message(err("Only staff can claim tickets."), ephemeral=True)
    st = panel_state(i.message)
    if st["claimed"]:
        return await i.response.send_message(err("This ticket is already claimed."), ephemeral=True)
    e = i.message.embeds[0].copy()
    fields = [f for f in e.fields if f.name != "Claimed by"]
    e.clear_fields()
    for f in fields:
        e.add_field(name=f.name, value=f.value, inline=f.inline)
    e.add_field(name="Claimed by", value=i.user.mention, inline=True)
    await i.response.edit_message(embed=e, view=ticket_buttons(claimed=True, closed=st["closed"]))
    data = parse_topic(i.channel)
    owner = int(data["owner"]) if data and data.get("owner", "").isdigit() else None
    c = discord.Embed(color=COLOR, description=f"{CLAIMED_E} **Ticket Claimed**\n\n> {i.user.mention} is now handling this ticket.\n"
                                               f"> {f'<@{owner}>' if owner else 'you'}, please share any extra details while they review your request.")
    c.set_author(name=f"{i.user.display_name} claimed this ticket", icon_url=i.user.display_avatar.url)
    c.set_footer(text="ELT | Ticket System")
    c.timestamp = discord.utils.utcnow()
    await i.channel.send(content=f"<@{owner}>" if owner else None, embed=c,
                         allowed_mentions=discord.AllowedMentions(users=[discord.Object(owner)] if owner else False))


async def handle_close(i: discord.Interaction):
    data = parse_topic(i.channel)
    if not data:
        return await i.response.send_message(err("This is not a ticket channel."), ephemeral=True)
    if not is_staff(i.user) and str(i.user.id) != data.get("owner"):
        return await i.response.send_message(err("You cannot close this ticket."), ephemeral=True)
    st = panel_state(i.message)
    owner = i.guild.get_member(int(data["owner"]))
    if owner:
        try:
            await i.channel.set_permissions(owner, overwrite=discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True, attach_files=True, embed_links=True))
        except discord.HTTPException:
            pass
    await i.response.edit_message(view=ticket_buttons(claimed=st["claimed"], closed=True))
    await i.channel.send(embed=discord.Embed(color=COLOR, description=f"{CLOSE_E} Ticket closed by {i.user.mention}. Staff can **Reopen** or **Delete** it."))


async def handle_reopen(i: discord.Interaction):
    data = parse_topic(i.channel)
    if not data:
        return await i.response.send_message(err("This is not a ticket channel."), ephemeral=True)
    if not is_staff(i.user):
        return await i.response.send_message(err("Only staff can reopen tickets."), ephemeral=True)
    st = panel_state(i.message)
    owner = i.guild.get_member(int(data["owner"]))
    if owner:
        try:
            await i.channel.set_permissions(owner, overwrite=discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True))
        except discord.HTTPException:
            pass
    await i.response.edit_message(view=ticket_buttons(claimed=st["claimed"], closed=False))
    await i.channel.send(embed=discord.Embed(color=COLOR, description=f"{UNLOCK_E} Ticket reopened by {i.user.mention}."))


async def handle_delete(i: discord.Interaction):
    data = parse_topic(i.channel)
    if not data:
        return await i.response.send_message(err("This is not a ticket channel."), ephemeral=True)
    if not is_staff(i.user):
        return await i.response.send_message(err("Only staff can delete tickets."), ephemeral=True)
    await i.response.send_message(embed=discord.Embed(color=COLOR, description=f"{DELETE_E} Generating transcript… this channel will be deleted in 5 seconds."))
    ch, cid = i.channel, claimed_by(i.message)
    owner_user = await bot.fetch_user(int(data["owner"])) if data.get("owner", "").isdigit() else None
    claimed_user = await bot.fetch_user(cid) if cid else None
    label = TYPES.get(data.get("type"), (data.get("type", "Unknown"), ""))[0]
    try:
        page, txt, count = await generate_transcript(ch, {"Type": label, "Opened by": str(owner_user) if owner_user else data.get("owner"),
                                                        "Claimed by": str(claimed_user) if claimed_user else None, "Deleted by": str(i.user)})
        files = lambda: [discord.File(io.BytesIO(page.encode()), filename=f"transcript-{ch.name}.html"),
                         discord.File(io.BytesIO(txt.encode()), filename=f"transcript-{ch.name}.txt")]
        log = discord.Embed(color=COLOR, title="📄 Ticket Deleted", timestamp=discord.utils.utcnow())
        for n, v in (("Ticket", f"#{ch.name}"), ("Type", label), ("Messages", str(count)), ("Opened by", f"<@{data['owner']}>"),
                     ("Claimed by", f"<@{cid}>" if cid else "Nobody"), ("Deleted by", i.user.mention)):
            log.add_field(name=n, value=v, inline=True)
        lc = bot.get_channel(LOG_CHANNEL_ID) or await bot.fetch_channel(LOG_CHANNEL_ID)
        await lc.send(embed=log, files=files())
        if owner_user:
            try:
                await owner_user.send(f"Your ticket **#{ch.name}** was closed. Here is your transcript:", files=files())
            except discord.HTTPException:
                pass
    except Exception as e:
        print(f"[tickets] transcript error: {e}")
        return await ch.send(f"{WARN_E} Failed to generate/send the transcript. Deletion cancelled.")
    import asyncio
    await asyncio.sleep(5)
    try:
        await ch.delete(reason="Ticket deleted")
    except discord.HTTPException as e:
        print(f"[tickets] couldn't delete the channel: {e}")


# ------------------------------------------------------------------ panel + creation
class TypeSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(custom_id="ticket_type", placeholder="Select your ticket type…", options=[
            discord.SelectOption(label="General Support", value="support", description="Need help or have a question? Open a support ticket."),
            discord.SelectOption(label="Report a Problem", value="report", description="Report a member, a staff member or a bug.")])

    async def callback(self, i: discord.Interaction):
        kind = self.values[0]
        label, emoji = TYPES[kind]
        await i.response.defer(ephemeral=True)
        guild, user = i.guild, i.user
        for c in guild.text_channels:
            if c.category_id == TICKET_CATEGORY_ID and (parse_topic(c) or {}).get("owner") == str(user.id):
                return await i.followup.send(f"{ERROR_E} You already have an open ticket: {c.mention}", ephemeral=True)
        safe = re.sub(r"[^a-z0-9]", "", user.name.lower())[:20] or str(user.id)
        cat = guild.get_channel(TICKET_CATEGORY_ID)
        base = dict(view_channel=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False), user: discord.PermissionOverwrite(**base),
              guild.me: discord.PermissionOverwrite(**base, manage_channels=True, manage_messages=True)}
        for rid in STAFF_ROLE_IDS:
            r = guild.get_role(rid)
            if r:
                ow[r] = discord.PermissionOverwrite(**base, manage_messages=True)
        ch = await guild.create_text_channel(f"{kind}-{safe}", category=cat, topic=f"ticket|owner={user.id}|type={kind}", overwrites=ow)
        e = discord.Embed(color=COLOR, title=f"{emoji} {label}", timestamp=discord.utils.utcnow(),
                          description=f"Welcome {user.mention}!\nPlease describe your {'report' if kind == 'report' else 'issue'} in detail and a staff member will be with you shortly.")
        e.add_field(name="Opened by", value=user.mention, inline=True)
        e.add_field(name="Type", value=label, inline=True)
        roles = [guild.get_role(r) for r in STAFF_ROLE_IDS if guild.get_role(r)]
        await ch.send(content=f"{user.mention} {' '.join(r.mention for r in roles)}", embed=e, view=ticket_buttons(),
                      allowed_mentions=discord.AllowedMentions(users=[user], roles=roles))
        await i.followup.send(f"☑️ Your ticket has been created: {ch.mention}", ephemeral=True)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TypeSelect())


class Tickets(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        self.bot.add_view(PanelView())
        self.bot.add_view(ticket_buttons())
        self.bot.add_view(ticket_buttons(claimed=True, closed=True))

    @app_commands.command(name="ticketpanel", description="Post the ticket panel here (high rank only)")
    @app_commands.guild_only()
    async def ticketpanel(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("High rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        g = interaction.guild
        icon = g.icon.url if g.icon else None
        e = discord.Embed(color=COLOR, title=f"{PANEL_E} ELT SUPPORT CENTER", description=(
            "```\n  Welcome to the ELT Support Center\n```\n**Need a hand? Pick a category from the menu below and a private channel will open just for you.**"))
        e.set_author(name="ELT | Ticket System", icon_url=icon)
        e.add_field(name=f"{SUPPORT_E} Support Ticket", value="> Questions, help & general issues", inline=True)
        e.add_field(name=f"{REPORT_E} Report Ticket", value="> Report a member, a staff member or a bug", inline=True)
        e.add_field(name="How it works", value="`1` Choose a category below\n`2` Describe your issue in detail\n`3` A staff member will join you shortly", inline=False)
        e.add_field(name="Good to know", value="• One open ticket per member\n• No need to ping staff, we will get to you\n"
                                               "• You get a copy of the transcript in your DMs (if they are open)", inline=False)
        e.set_footer(text=f"{g.name} • Pick the right category for faster help", icon_url=icon)
        if icon:
            e.set_thumbnail(url=icon)
        f = banner_file("tickets")
        if f:
            e.set_image(url=f"attachment://{banner_name('tickets')}")
        await interaction.channel.send(embed=e, view=PanelView(), **({"file": f} if f else {}))
        await interaction.followup.send("Ticket panel posted.", ephemeral=True)


async def setup(b):
    await b.add_cog(Tickets(b), guild=GUILD)
