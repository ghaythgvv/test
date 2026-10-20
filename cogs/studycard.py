"""Elite Study Card: /cardpanel (admin) posts the panel, /removecard (mods) takes the role away."""
import discord
from discord import app_commands
from discord.ext import commands

from common import GUILD, PURPLE, int_env, is_admin, is_mod, with_banner

CARD_ROLE_ID = int_env("CARD_ROLE_ID", 1547024762444714004)
CARD_NAME = "Elite Study Card"
AREA_NAME = "Elite Study"
APPLY_EMOJI = "\U0001FAAA"


def role_problem(guild: discord.Guild):
    role = guild.get_role(CARD_ROLE_ID)
    if role is None:
        return f"The {CARD_NAME} role wasn't found - check CARD_ROLE_ID."
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "I'm missing the Manage Roles permission."
    if role >= me.top_role:
        return f"The {CARD_NAME} role is above my role - move my role higher."
    return None


def panel_embed(guild: discord.Guild) -> discord.Embed:
    e = discord.Embed(
        title=CARD_NAME,
        description=(
            f"Want access to **{AREA_NAME}**?\nGet your **{CARD_NAME}** and unlock the study-only category.\n\n"
            "### How it works\n"
            "> 1. Press **Get your Card** below\n"
            f"> 2. You instantly get the **{CARD_NAME}** role\n"
            f"> 3. **{AREA_NAME}** opens for you\n\n"
            "**Respect the rules** - the card can be removed at any time.\n\nPress the button below to get your card."
        ),
        color=PURPLE,
    )
    e.set_footer(text=f"ELITE LEADERS COMMUNITY • {CARD_NAME}", icon_url=guild.icon.url if guild.icon else None)
    return e


class CardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Get your Card", emoji=APPLY_EMOJI, style=discord.ButtonStyle.secondary,
                       custom_id="elitestudycard_get")
    async def get_card(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await interaction.response.send_message("Use this in the server.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if role_problem(guild):
            print(f"[card] ERROR: {role_problem(guild)}")
            return await interaction.followup.send("The card isn't working right now. Please tell a staff member.", ephemeral=True)
        role = guild.get_role(CARD_ROLE_ID)
        if role in member.roles:
            return await interaction.followup.send(f"You already have the **{CARD_NAME}**.", ephemeral=True)
        try:
            await member.add_roles(role, reason=f"{CARD_NAME} button")
        except discord.HTTPException as e:
            print(f"[card] ERROR giving the role to {member}: {e}")
            return await interaction.followup.send("I couldn't give you the card. Please tell a staff member.", ephemeral=True)
        await interaction.followup.send(f"You got the **{CARD_NAME}**. **{AREA_NAME}** is now open for you.", ephemeral=True)


class StudyCard(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.bot.add_view(CardView())

    @app_commands.command(name="cardpanel", description="Post the Elite Study Card panel here (high rank only)")
    @app_commands.guild_only()
    async def cardpanel(self, interaction: discord.Interaction):
        if not is_admin(interaction.user):
            return await interaction.response.send_message("This command is for the high rank only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = panel_embed(interaction.guild)
        kw = with_banner(embed, "studycard")
        try:
            await interaction.channel.send(embed=embed, view=CardView(), **kw)
        except discord.HTTPException as e:
            return await interaction.followup.send(f"I couldn't post the panel here: {e}", ephemeral=True)
        note = "Panel posted."
        if role_problem(interaction.guild):
            note += f"\nWarning: {role_problem(interaction.guild)}"
        await interaction.followup.send(note, ephemeral=True)

    @app_commands.command(name="removecard", description="Take the Elite Study Card role away from a member (moderators)")
    @app_commands.describe(member="The member")
    @app_commands.guild_only()
    async def removecard(self, interaction: discord.Interaction, member: discord.Member):
        if not is_mod(interaction.user):
            return await interaction.response.send_message("This command is for moderators or higher.", ephemeral=True)
        role = interaction.guild.get_role(CARD_ROLE_ID)
        if role is None:
            return await interaction.response.send_message(f"The {CARD_NAME} role wasn't found - check CARD_ROLE_ID.", ephemeral=True)
        if role not in member.roles:
            return await interaction.response.send_message(f"{member.mention} doesn't have the {CARD_NAME}.", ephemeral=True,
                                                           allowed_mentions=discord.AllowedMentions.none())
        try:
            await member.remove_roles(role, reason=f"{CARD_NAME} removed by {interaction.user}")
        except discord.HTTPException as e:
            return await interaction.response.send_message(f"Failed to remove the role: {e}", ephemeral=True)
        await interaction.response.send_message(f"Removed the **{CARD_NAME}** from {member.mention}.", ephemeral=True,
                                                allowed_mentions=discord.AllowedMentions.none())


async def setup(bot):
    await bot.add_cog(StudyCard(bot), guild=GUILD)
