"""Shared helpers: IDs, permission tiers and the per-feature banners."""
import os
import discord

HERE = os.path.dirname(os.path.abspath(__file__))
GUILD_ID = 1410440666747633707          # ELT server
GUILD = discord.Object(id=GUILD_ID)
MOD_ROLE_ID = 1513904125086011402       # moderators
ADMIN_ROLE_ID = 1513904120803889243     # high rank
PURPLE = discord.Color.purple()

# One banner per feature. Swap a file in /banners (same name) to change a banner.
BANNERS = {
    "studycard": "banners/studycard.gif",
    "staffapps": "banners/staffapps.gif",
    "support":   "banners/support.gif",
    "verify":    "banners/verify.gif",
    "welcome":   "banners/welcome.gif",
    "xp":        "banners/xp.gif",
    "esport":    "banners/esport.gif",
    "tickets":   "banners/tickets.gif",
    "giveaway":  "banners/giveaway.gif",
    "tempvc":    "banners/tempvc.gif",
    "punish":    "blacklist_banner.jpg",
    "boost":     "boost_bg.jpg",
}


def banner_path(key: str):
    p = os.path.join(HERE, BANNERS[key])
    return p if os.path.exists(p) else None


def banner_name(key: str) -> str:
    return os.path.basename(BANNERS[key])


def banner_file(key: str):
    """A NEW discord.File each call (a File can only be sent once). None if the image is missing."""
    p = banner_path(key)
    return discord.File(p, filename=os.path.basename(p)) if p else None


def with_banner(embed: discord.Embed, key: str) -> dict:
    """Puts the banner inside the embed. Returns the kwargs to pass to send()/edit_message()."""
    f = banner_file(key)
    if f is None:
        return {}
    embed.set_image(url=f"attachment://{f.filename}")
    return {"file": f}


def is_admin(m) -> bool:
    if not isinstance(m, discord.Member):
        return False
    if m.id == m.guild.owner_id or m.guild_permissions.administrator:
        return True
    r = m.guild.get_role(ADMIN_ROLE_ID)
    return bool(r and m.top_role >= r)


def is_mod(m) -> bool:
    if is_admin(m):
        return True
    if not isinstance(m, discord.Member):
        return False
    r = m.guild.get_role(MOD_ROLE_ID)
    return bool(r and m.top_role >= r)


def int_env(name: str, default: int = 0) -> int:
    v = os.environ.get(name, "").strip()
    return int(v) if v.isdigit() else default
