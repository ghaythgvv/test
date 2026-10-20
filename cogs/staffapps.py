"""Staff applications: /staffpanel (admin) and /staffapps open|close (admin)."""
import logging
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, GUILD_ID, banner_file, banner_name, banner_path, is_admin

log = logging.getLogger("staffapps")

PENDING_CHANNEL_ID = 1513904283337101364
ACCEPTED_CHANNEL_ID = 1513904281198137475
DENIED_CHANNEL_ID = 1513904282267811920
APPLY_CHANNEL_ID = 1513904279847702699
STAFF_ROLE_ID = 0        # role given when accepted (0 = none)
REVIEWER_ROLE_ID = 0     # role allowed to review (0 = only Manage Server / Admin)
SERVER_NAME = "ELT | ELITE LEADERS COMMUNITY"
ACCENT = 0x8B3DFF
INTERVIEW_INVITE_URL = "https://discord.gg/VE3Yaje9ED"
MIN_DAYS_IN_SERVER = 7
COOLDOWN_HOURS = 72

state = {"open": True}
pending: set[int] = set()
handled: set[int] = set()
submitting: set[int] = set()
cooldowns: dict[int, float] = {}
drafts: dict[int, dict] = {}
bot = None

# (key, button label, full question for reviewers, placeholder, min, max)
Q = [
    ("age", "Your age", "Your age", "e.g. 19", 1, 3),
    ("why_join", "Why you wanna join the staff team?", "Why you wanna join the staff team?", "Tell us in your own words", 20, 400),
    ("country_tz", "What country / time zone are you from?", "What country/time zone are you from?", "e.g. Tunisia, GMT+1", 2, 100),
    ("activity", "How active can you be each day?", "How active can you be each day?", "e.g. about 4 hours a day", 2, 100),
    ("prev_staff", "Been staff in another community before?", "Have you been staff in another community before?", "Yes or no - and where, if yes", 2, 300),
    ("why_become", "Why do you want to become a staff member?", "Why do you want to become a staff member?", "Tell us in your own words", 20, 400),
    ("good_choice", "What makes you a good choice for staff?", "What makes you a good choice for the staff team?", "What makes you a good choice?", 20, 400),
    ("strengths", "What are your strengths and weaknesses?", "What are your strengths and weaknesses?", "Be honest", 10, 400),
    ("arguing", "What if two members started arguing?", "What would you do if two members started arguing?", "What would you do?", 20, 400),
    ("abuse", "What if another staff member abused powers?", "What would you do if another staff member abused their powers?", "What would you do?", 20, 400),
    ("repeat", "How to handle repeated rule breaking?", "How would you handle someone breaking the rules repeatedly?", "How would you handle it?", 20, 400),
    ("insulted", "What if a member insulted you?", "What would you do if a member insulted or disrespected you?", "What would you do?", 20, 400),
    ("removal", "Abusing staff powers can lead to removal?", "Do you understand that abusing staff powers can result in removal?", "Yes or no", 2, 200),
    ("rules", "Willing to follow rules & respect staff?", "Are you willing to follow the server rules and respect higher-ranking staff?", "Yes or no", 2, 200),
    ("extra", "Anything else you'd like us to know?", "Is there anything else you would like us to know?", 'Write "No" if there\'s nothing', 1, 400),
]
PARTS = [Q[i:i + 5] for i in range(0, len(Q), 5)]


def is_reviewer(m) -> bool:
    if not isinstance(m, discord.Member):
        return False
    p = m.guild_permissions
    return p.administrator or p.manage_guild or bool(REVIEWER_ROLE_ID and m.get_role(REVIEWER_ROLE_ID))


def clip(t, n=1000):
    t = (t or "").strip() or "—"
    return t if len(t) <= n else t[: n - 1] + "…"


def quote(t, n=1000):
    b = clip(t, n).replace("\n", "\n> ")
    return "> " + (b if len(b) <= 1200 else b[:1199] + "…")


def panel_embed() -> discord.Embed:
    e = discord.Embed(
        title=f"✦ Join the {SERVER_NAME} Staff Team",
        description=("Want to help shape this community? We're looking for people who are reliable, fair and genuinely "
                     f"invested in the server.\n\n{'Applications are **OPEN**' if state['open'] else 'Applications are **CLOSED**'}"),
        color=ACCENT if state["open"] else 0xE74C3C)
    e.add_field(name="<a:226410surohpurplestar:1554285922651086939> What we look for",
                value="• Maturity and good judgement under pressure\n• Clear communication and teamwork\n"
                      "• Consistent activity in the community\n• A calm, fair approach with members", inline=False)
    e.add_field(name="<:501986darkpurplestaffbadge:1554290940930162790> Requirements",
                value=f"• In the server for **{MIN_DAYS_IN_SERVER}+ days**\n• No staff experience needed\n• Honest, thoughtful answers")
    e.add_field(name="⏱ How it works",
                value="1. Press **Apply**\n2. Fill in the short form\n3. We review it and reply by DM\n*(keep your DMs open)*")
    if banner_path("staffapps"):
        e.set_image(url=f"attachment://{banner_name('staffapps')}")
    e.set_footer(text=f"{SERVER_NAME} • Staff Recruitment")
    return e


async def has_open_application(uid: int) -> bool:
    ch = bot.get_channel(PENDING_CHANNEL_ID)
    if ch is None:
        return False
    try:
        async for m in ch.history(limit=200):
            if m.author.id == bot.user.id and m.embeds and m.embeds[0].footer.text == f"User ID: {uid}" and m.components:
                return True
    except discord.HTTPException:
        pass
    return False


def application_embed(user, answers: dict, limit: int) -> discord.Embed:
    joined = discord.utils.format_dt(user.joined_at, "R") if isinstance(user, discord.Member) and user.joined_at else "—"
    blocks = [f"# ✦ STAFF APPLICATION\n**Applicant:** {user.mention} (`{user.id}`)\n"
              f"**Age:** {clip(answers.get('age', ''), 10)}  •  **Joined:** {joined}"]
    for key, _l, question, *_ in Q[1:]:
        blocks.append(f"## {question}\n{quote(answers.get(key, ''), limit)}")
    e = discord.Embed(description="\n\n".join(blocks), color=ACCENT, timestamp=datetime.now(timezone.utc))
    e.set_thumbnail(url=user.display_avatar.url)
    e.add_field(name="Status", value="🕓 Pending review", inline=False)
    e.set_footer(text=f"User ID: {user.id}")
    return e


async def submit_application(interaction: discord.Interaction, answers: dict):
    user = interaction.user
    uid = user.id
    if not state["open"]:
        return await interaction.followup.send("🔴 Applications are currently closed.", ephemeral=True)
    if uid in submitting:
        return await interaction.followup.send("⏳ You already have an application under review.", ephemeral=True)
    submitting.add(uid)
    if await has_open_application(uid):
        submitting.discard(uid)
        return await interaction.followup.send("⏳ You already have an application under review.", ephemeral=True)
    ch = bot.get_channel(PENDING_CHANNEL_ID)
    if ch is None:
        submitting.discard(uid)
        return await interaction.followup.send("⚠️ Applications are temporarily unavailable. Please tell an admin.", ephemeral=True)
    for limit in (400, 300, 220, 160, 110, 70):
        e = application_embed(user, answers, limit)
        if len(e.description) <= 4000 and len(e) <= 5800:
            break
    try:
        await ch.send(embed=e, view=review_view(uid, interview=True))
    except discord.HTTPException:
        log.exception("Could not post application")
        submitting.discard(uid)
        return await interaction.followup.send("⚠️ Something went wrong sending your application. Please try again later.", ephemeral=True)
    pending.add(uid)
    submitting.discard(uid)
    drafts.pop(uid, None)
    cooldowns[uid] = time.time() + COOLDOWN_HOURS * 3600
    await interaction.followup.send("☑️ Your application was sent! We'll reply by DM — keep your DMs open.", ephemeral=True)


class StepModal(discord.ui.Modal):
    def __init__(self, part: int):
        super().__init__(title=f"Staff Application ({part + 1}/{len(PARTS)})")
        self.part = part
        self.inputs = {}
        for key, label, _q, ph, mn, mx in PARTS[part]:
            box = discord.ui.TextInput(label=label, placeholder=ph, style=discord.TextStyle.paragraph,
                                       min_length=mn, max_length=mx, required=True)
            self.inputs[key] = box
            self.add_item(box)

    async def on_submit(self, interaction: discord.Interaction):
        uid = interaction.user.id
        answers = {k: b.value.strip() for k, b in self.inputs.items()}
        if self.part == 0:
            a = answers["age"]
            if not (a.isascii() and a.isdigit() and 13 <= int(a) <= 99):
                return await interaction.response.send_message(
                    "⚠️ Please enter your real age as a number (13 or older). Press **Apply** to try again.", ephemeral=True)
        elif uid not in drafts:
            return await interaction.response.send_message("⚠️ Your draft expired. Press **Apply** to start again.", ephemeral=True)
        drafts.setdefault(uid, {}).update(answers)
        if self.part + 1 < len(PARTS):
            return await interaction.response.send_message(
                f"☑️ Part {self.part + 1}/{len(PARTS)} saved. Press **Continue** for the next part.",
                view=ContinueView(uid, self.part + 1), ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        await submit_application(interaction, drafts.get(uid, answers))

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        log.exception("Application modal error", exc_info=error)
        submitting.discard(interaction.user.id)
        msg = "⚠️ Something went wrong. Please try again."
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


class ContinueView(discord.ui.View):
    def __init__(self, uid: int, part: int):
        super().__init__(timeout=900)
        self.uid, self.part = uid, part
        self.go.label = f"Continue ({part + 1}/{len(PARTS)})"

    @discord.ui.button(label="Continue", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def go(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.uid:
            return await interaction.response.send_message("This isn't your application.", ephemeral=True)
        if self.uid not in drafts:
            return await interaction.response.send_message("⚠️ Your draft expired. Press **Apply** to start again.", ephemeral=True)
        await interaction.response.send_modal(StepModal(self.part))


class ApplyView(discord.ui.View):
    def __init__(self, closed: bool = False):
        super().__init__(timeout=None)
        if closed:
            self.apply.label, self.apply.disabled = "Applications Closed", True

    @discord.ui.button(label="Apply", emoji="📝", style=discord.ButtonStyle.secondary, custom_id="staffapp:apply")
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        send = interaction.response.send_message
        if not state["open"]:
            return await send("🔴 Applications are currently closed.", ephemeral=True)
        if await has_open_application(user.id):
            return await send("⏳ You already have an application under review.", ephemeral=True)
        if STAFF_ROLE_ID and isinstance(user, discord.Member) and user.get_role(STAFF_ROLE_ID):
            return await send("You're already part of the staff team!", ephemeral=True)
        wait = cooldowns.get(user.id, 0)
        if wait > time.time():
            return await send(f"⏳ You can apply again {discord.utils.format_dt(datetime.fromtimestamp(wait, timezone.utc), 'R')}.", ephemeral=True)
        if isinstance(user, discord.Member) and user.joined_at:
            days = (datetime.now(timezone.utc) - user.joined_at).days
            if days < MIN_DAYS_IN_SERVER:
                return await send(f"You need to be in the server for at least **{MIN_DAYS_IN_SERVER} days** to apply (you're at {days}).", ephemeral=True)
        drafts.pop(user.id, None)
        await interaction.response.send_modal(StepModal(0))


class DenyModal(discord.ui.Modal, title="Deny Application"):
    reason = discord.ui.TextInput(label="Reason (sent to the applicant)", style=discord.TextStyle.paragraph, max_length=500, required=True)

    def __init__(self, uid: int, message: discord.Message):
        super().__init__()
        self.uid, self.message = uid, message

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await finalize(interaction, self.message, self.uid, "deny", reason=self.reason.value)


async def finalize(interaction, message: discord.Message, uid: int, action: str, reason: str = ""):
    guild, reviewer, notes = interaction.guild, interaction.user, []
    if action != "interview":
        if message.id in handled:
            return await interaction.followup.send("This application was already handled.", ephemeral=True)
        handled.add(message.id)
    embed = message.embeds[0].copy() if message.embeds else discord.Embed(title="Application")
    idx = next((i for i, f in enumerate(embed.fields) if f.name == "Status"), None)

    if action == "accept":
        color, status = 0x2ECC71, f"☑️ Accepted by {reviewer.mention}"
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except discord.HTTPException:
                member = None
        if member is None:
            notes.append("⚠️ The applicant is no longer in the server.")
        elif STAFF_ROLE_ID:
            role = guild.get_role(STAFF_ROLE_ID)
            if role is None:
                notes.append("⚠️ Staff role not found — check STAFF_ROLE_ID.")
            else:
                try:
                    await member.add_roles(role, reason=f"Staff application accepted by {reviewer}")
                except discord.Forbidden:
                    notes.append("⚠️ I can't give that role — move my bot role above it.")
        dm = discord.Embed(title="🎉 Application accepted!", color=0x2ECC71,
                           description=f"Congratulations! Your staff application in **{guild.name}** was accepted. A team member will contact you soon.")
        pending.discard(uid)
        cooldowns.pop(uid, None)
    elif action == "interview":
        color, status = 0xF1C40F, f"🎤 Interview requested by {reviewer.mention}"
        dm = discord.Embed(title="Interview request", color=0xF1C40F,
                           description=f"Your staff application in **{guild.name}** looks promising, join this server for Interview:\n{INTERVIEW_INVITE_URL}")
    else:
        color, status = 0xE74C3C, f"❌ Denied by {reviewer.mention}\n**Reason:** {clip(reason, 500)}"
        dm = discord.Embed(title="Application update", color=0xE74C3C,
                           description=f"Unfortunately your staff application in **{guild.name}** was not accepted this time.\n\n"
                                       f"**Reason:** {clip(reason, 500)}\n\nYou're welcome to apply again in the future.")
        pending.discard(uid)

    embed.color = color
    if idx is not None:
        embed.set_field_at(idx, name="Status", value=status, inline=False)
    else:
        embed.add_field(name="Status", value=status, inline=False)

    if action == "interview":
        try:
            await message.edit(embed=embed, view=review_view(uid, interview=False))
        except discord.HTTPException:
            notes.append("⚠️ Couldn't update the application message.")
    else:
        dest = interaction.client.get_channel(ACCEPTED_CHANNEL_ID if action == "accept" else DENIED_CHANNEL_ID)
        posted = False
        if dest is None:
            notes.append("⚠️ Result channel not found - check the channel IDs.")
        else:
            try:
                await dest.send(embed=embed)
                posted = True
            except discord.HTTPException:
                notes.append("⚠️ I couldn't post in the result channel - check my permissions there.")
        try:
            if posted:
                await message.delete()
            else:
                await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            notes.append("⚠️ Couldn't clean up the pending message.")

    try:
        user = guild.get_member(uid) or await interaction.client.fetch_user(uid)
        await user.send(embed=dm)
    except (discord.Forbidden, discord.HTTPException):
        notes.append("⚠️ Couldn't DM the applicant (their DMs are closed).")
    await interaction.followup.send("Done. " + " ".join(notes) if notes else "Done ☑️", ephemeral=True)


class ReviewButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"staffapp:(?P<action>accept|interview|deny):(?P<uid>[0-9]+)"):
    STYLES = {"accept": ("Accept", "☑️"), "interview": ("Interview", None), "deny": ("Deny", "❌")}

    def __init__(self, action: str, uid: int):
        label, emoji = self.STYLES[action]
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.secondary,
                                           custom_id=f"staffapp:{action}:{uid}"))
        self.action, self.uid = action, uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(match["action"], int(match["uid"]))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not is_reviewer(interaction.user):
            await interaction.response.send_message("You're not allowed to review applications.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        if self.action == "deny":
            return await interaction.response.send_modal(DenyModal(self.uid, interaction.message))
        await interaction.response.defer(ephemeral=True)
        await finalize(interaction, interaction.message, self.uid, self.action)


def review_view(uid: int, interview: bool = True) -> discord.ui.View:
    v = discord.ui.View(timeout=None)
    v.add_item(ReviewButton("accept", uid))
    if interview:
        v.add_item(ReviewButton("interview", uid))
    v.add_item(ReviewButton("deny", uid))
    return v


async def find_panel(guild: discord.Guild):
    ch = guild.get_channel(APPLY_CHANNEL_ID)
    if ch is None:
        return None
    async for m in ch.history(limit=100):
        if m.author.id == bot.user.id and m.embeds and (m.embeds[0].title or "").startswith("✦ Join the"):
            return m
    return None


class StaffApps(commands.Cog):
    def __init__(self, b):
        global bot
        bot = b
        self.bot = b

    async def cog_load(self):
        self.bot.add_view(ApplyView())
        self.bot.add_dynamic_items(ReviewButton)

    @commands.Cog.listener()
    async def on_ready(self):
        g = self.bot.get_guild(GUILD_ID)
        if g:
            try:
                panel = await find_panel(g)
                if panel and "CLOSED" in (panel.embeds[0].description or ""):
                    state["open"] = False
            except discord.HTTPException:
                pass

    @app_commands.command(name="staffpanel", description="Post the staff application panel (high rank only)")
    @app_commands.guild_only()
    async def staffpanel(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("High rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        ch = interaction.guild.get_channel(APPLY_CHANNEL_ID) or interaction.channel
        try:
            async for m in ch.history(limit=100):
                if m.author.id == self.bot.user.id and m.embeds and (m.embeds[0].title or "").startswith("✦ Join the"):
                    await m.delete()
        except discord.HTTPException:
            pass
        kw = {}
        f = banner_file("staffapps")
        if f:
            kw["file"] = f
        try:
            await ch.send(embed=panel_embed(), view=ApplyView(closed=not state["open"]), **kw)
        except discord.HTTPException:
            return await interaction.followup.send(f"⚠️ I couldn't post the panel in {ch.mention} - check my permissions there.", ephemeral=True)
        await interaction.followup.send(f"Panel posted in {ch.mention} ☑️", ephemeral=True)

    @app_commands.command(name="staffapps", description="Open or close staff applications (high rank only)")
    @app_commands.choices(mode=[app_commands.Choice(name="open", value="open"), app_commands.Choice(name="close", value="close")])
    @app_commands.guild_only()
    async def staffapps(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("High rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        state["open"] = mode.value == "open"
        note = ""
        try:
            panel = await find_panel(interaction.guild)
            if panel:
                kw = {}
                f = banner_file("staffapps")
                if f:
                    kw["attachments"] = [f]
                await panel.edit(embed=panel_embed(), view=ApplyView(closed=not state["open"]), **kw)
            else:
                note = " I couldn't find the panel - run /staffpanel again."
        except discord.HTTPException:
            note = " I couldn't update the panel message."
        await interaction.followup.send(f"Applications are now **{'OPEN' if state['open'] else 'CLOSED'}**.{note}", ephemeral=True)


async def setup(b):
    await b.add_cog(StaffApps(b), guild=GUILD)
