"""Esport team applications: /esportpanel (admin). Railway variables: ESPORT_CATEGORY_ID, STAFF_ROLE_ID, ESPORT_ROLE_ID, ESPORT_LOG_CHANNEL_ID."""
import asyncio
import io
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

from common import GUILD, banner_file, banner_name, int_env, is_admin

CATEGORY_ID = int_env("ESPORT_CATEGORY_ID")
STAFF_ROLE_ID = int_env("STAFF_ROLE_ID", 1513904136783925380)
ESPORT_ROLE_ID = int_env("ESPORT_ROLE_ID")
LOG_CHANNEL_ID = int_env("ESPORT_LOG_CHANNEL_ID")
COLOR = 0x9B59FF
COOLDOWN_SECONDS = 60
INACTIVE_HOURS = 24
EMOJI_HOW = "<a:562725purplespiral:1559975435658461264>"
EMOJI_LOOK = "<a:88542dancepurple:1559975433368248460>"
EMOJI_CLAIM = discord.PartialEmoji(name="claim", id=1554675609525821531)
EMOJI_ACCEPT = discord.PartialEmoji(name="accept", id=1553555472811040788)
EMOJI_DENY = discord.PartialEmoji(name="deny", id=1559043013811114105)
EMOJI_CLOSE = discord.PartialEmoji(name="close", id=1553472894976393246)
cooldowns: dict[int, float] = {}
clip_seen: set[int] = set()
bot = None


def is_staff(m) -> bool:
    return m.guild_permissions.administrator or any(r.id == STAFF_ROLE_ID for r in m.roles)


def ticket_owner_id(ch):
    t = getattr(ch, "topic", None)
    if t and t.startswith("esport:"):
        try:
            return int(t.split(":")[1])
        except ValueError:
            return None
    return None


async def staff_only(i: discord.Interaction) -> bool:
    if is_staff(i.user):
        return True
    await i.response.send_message("Staff only.", ephemeral=True)
    return False


async def make_transcript(ch) -> discord.File:
    lines = []
    async for m in ch.history(limit=None, oldest_first=True):
        lines.append(f"[{m.created_at.strftime('%Y-%m-%d %H:%M')}] {m.author}: {m.content} {' '.join(a.url for a in m.attachments)}".strip())
    return discord.File(io.BytesIO("\n".join(lines).encode("utf-8")), filename=f"{ch.name}.txt")


async def send_log(guild, embed, file=None):
    ch = guild.get_channel(LOG_CHANNEL_ID)
    if ch:
        try:
            await ch.send(embed=embed, file=file)
        except Exception as e:
            print("[esport] log error:", e)


async def close_ticket(ch, reason):
    owner = ticket_owner_id(ch)
    try:
        await send_log(ch.guild, discord.Embed(title="Esport Ticket Closed", color=COLOR,
                       description=f"**Channel:** #{ch.name}\n**Applicant:** <@{owner}>\n**Reason:** {reason}"), await make_transcript(ch))
    except Exception as e:
        print("[esport] transcript error:", e)
    clip_seen.discard(ch.id)
    await ch.delete()


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def _decide(self, interaction, accepted: bool):
        await interaction.response.defer()
        owner_id = ticket_owner_id(interaction.channel)
        member = interaction.guild.get_member(owner_id) if owner_id else None
        if accepted:
            role = interaction.guild.get_role(ESPORT_ROLE_ID)
            if member and role:
                try:
                    await member.add_roles(role, reason="Esport application accepted")
                except Exception as e:
                    print("[esport] role error:", e)
        for c in self.children:
            if c.custom_id in ("esport_accept", "esport_deny"):
                c.disabled = True
        await interaction.message.edit(view=self)
        if accepted:
            e = discord.Embed(title="APPLICATION ACCEPTED", color=0x2ECC71,
                              description=f"<@{owner_id}> welcome to the **Esport Team**!\nAccepted by {interaction.user.mention}")
            dm, lt = f"Your Esport application in **{interaction.guild.name}** was **accepted**!", "Esport Accepted"
        else:
            e = discord.Embed(title="APPLICATION DENIED", color=0xE74C3C,
                              description=f"<@{owner_id}> your application was not accepted this time.\nDenied by {interaction.user.mention}")
            dm, lt = f"Your Esport application in **{interaction.guild.name}** was **denied**. You can try again later.", "Esport Denied"
        await interaction.channel.send(embed=e)
        if member:
            try:
                await member.send(dm)
            except Exception:
                pass
        await send_log(interaction.guild, discord.Embed(title=lt, color=e.color,
                       description=f"<@{owner_id}> {'accepted' if accepted else 'denied'} by {interaction.user.mention}"))

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.secondary, emoji=EMOJI_CLAIM, custom_id="esport_claim")
    async def claim(self, interaction, button):
        if not await staff_only(interaction):
            return
        await interaction.response.defer()
        button.disabled = True
        button.label = f"Claimed by {interaction.user.display_name}"[:80]
        await interaction.message.edit(view=self)
        await interaction.channel.send(f"{interaction.user.mention} will review this application.")

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.secondary, emoji=EMOJI_ACCEPT, custom_id="esport_accept")
    async def accept(self, interaction, button):
        if await staff_only(interaction):
            await self._decide(interaction, True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.secondary, emoji=EMOJI_DENY, custom_id="esport_deny")
    async def deny(self, interaction, button):
        if await staff_only(interaction):
            await self._decide(interaction, False)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary, emoji=EMOJI_CLOSE, custom_id="esport_close")
    async def close(self, interaction, button):
        if not (interaction.user.id == ticket_owner_id(interaction.channel) or is_staff(interaction.user)):
            return await interaction.response.send_message("You can't close this ticket.", ephemeral=True)
        await interaction.response.send_message("Closing in 5 seconds... transcript is being saved.")
        await asyncio.sleep(5)
        await close_ticket(interaction.channel, f"Closed by {interaction.user}")


class JoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.secondary, custom_id="esport_join")
    async def join(self, interaction: discord.Interaction, button):
        guild, user = interaction.guild, interaction.user
        send = interaction.response.send_message
        left = COOLDOWN_SECONDS - (time.time() - cooldowns.get(user.id, 0))
        if left > 0:
            return await send(f"Please wait {int(left)}s before trying again.", ephemeral=True)
        if ESPORT_ROLE_ID and any(r.id == ESPORT_ROLE_ID for r in user.roles):
            return await send("You are already in the Esport Team.", ephemeral=True)
        cat = guild.get_channel(CATEGORY_ID)
        if not isinstance(cat, discord.CategoryChannel):
            return await send("Esport category is not set up (ESPORT_CATEGORY_ID). Tell an admin.", ephemeral=True)
        ex = discord.utils.find(lambda c: c.topic == f"esport:{user.id}", cat.text_channels)
        if ex:
            return await send(f"You already have an open ticket: {ex.mention}", ephemeral=True)
        if len(cat.channels) >= 50:
            return await send("Applications are full right now. Please try again in a bit.", ephemeral=True)
        cooldowns[user.id] = time.time()
        await interaction.response.defer(ephemeral=True)
        staff = guild.get_role(STAFF_ROLE_ID)
        ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
              user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True, read_message_history=True),
              guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)}
        if staff:
            ow[staff] = discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, read_message_history=True)
        ch = await guild.create_text_channel(name=f"esport-{user.name}", category=cat, topic=f"esport:{user.id}", overwrites=ow)
        e = discord.Embed(title="ESPORT TEAM APPLICATION", color=COLOR,
                          description=f"Welcome {user.mention}!\nSend the following in this ticket and staff will review it soon.")
        e.add_field(name="1.  What do you play?", value="Tell us your game(s), rank and role.", inline=False)
        e.add_field(name="2.  Send a clip", value="Upload a gameplay clip or paste a link (YouTube / Medal / Streamable).", inline=False)
        e.set_thumbnail(url=user.display_avatar.url)
        e.set_footer(text="ELT Esport  •  Good luck!")
        await ch.send(content=f"{user.mention} {staff.mention if staff else ''}", embed=e, view=TicketView())
        await interaction.followup.send(f"Your ticket is ready: {ch.mention}", ephemeral=True)


class Esport(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        self.bot.add_view(JoinView())
        self.bot.add_view(TicketView())
        self.auto_close.start()

    async def cog_unload(self):
        self.auto_close.cancel()

    @app_commands.command(name="esportpanel", description="Post the Esport team application panel here (admin)")
    @app_commands.guild_only()
    async def esportpanel(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("High rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        e = discord.Embed(title="JOIN THE ELT ESPORT TEAM", color=COLOR,
                          description="Think you've got what it takes to **compete and represent the server?**\nOpen a ticket below and show us what you can do.")
        e.add_field(name=f"{EMOJI_HOW}  How to apply", inline=True,
                    value="**1.** Press **Apply Now** below\n**2.** Tell us **what you play**\n**3.** Send **a clip** of your gameplay")
        e.add_field(name=f"{EMOJI_LOOK}  What we look for", value="• Real skill & good aim\n• Teamwork & communication\n• Respect & activity", inline=True)
        e.add_field(name="What happens next", inline=False,
                    value="Staff reviews every application. You'll get a **DM** with the result — accepted or denied.")
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.set_footer(text="ELT Esport  •  Apply now, show your skills")
        f = banner_file("esport")
        if f:
            e.set_image(url=f"attachment://{banner_name('esport')}")
        await interaction.channel.send(embed=e, view=JoinView(), **({"file": f} if f else {}))
        await interaction.followup.send("Panel posted.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return
        if ticket_owner_id(message.channel) == message.author.id and message.channel.id not in clip_seen:
            if message.attachments or "http" in message.content:
                clip_seen.add(message.channel.id)
                staff = message.guild.get_role(STAFF_ROLE_ID)
                await message.channel.send(content=staff.mention if staff else None,
                                           embed=discord.Embed(title="Clip received", description="Staff can now review this application.", color=COLOR))

    @tasks.loop(minutes=30)
    async def auto_close(self):
        for guild in self.bot.guilds:
            cat = guild.get_channel(CATEGORY_ID)
            if not isinstance(cat, discord.CategoryChannel):
                continue
            for ch in list(cat.text_channels):
                if ticket_owner_id(ch) is None:
                    continue
                last = discord.utils.snowflake_time(ch.last_message_id) if ch.last_message_id else ch.created_at
                if discord.utils.utcnow() - last > timedelta(hours=INACTIVE_HOURS):
                    try:
                        await close_ticket(ch, f"Inactive for {INACTIVE_HOURS}h")
                    except Exception as e:
                        print("[esport] auto close error:", e)

    @auto_close.before_loop
    async def _w(self):
        await self.bot.wait_until_ready()


async def setup(b):
    await b.add_cog(Esport(b), guild=GUILD)
