"""
boost_card.py  -  draws the ELT server-boost card: BLACK + PURPLE, ANIMATED (GIF).

    render_boost_card(name, server_name, avatar_bytes, level, total_boosts,
                      boosts=1, level_up=None) -> bytes   (GIF, or PNG if BOOST_ANIMATED=0)

Files next to this one:
    boost_bg.jpg      the flames / skeletons / ghost artwork (REQUIRED for the full look,
                      a plain black+purple fallback is used if it is missing)
    fonts/            optional - any .ttf/.otf (bold ones are preferred), or set FONT_PATH

Needs only Pillow.

What moves: the flames ripple + flicker, embers rise, the ghost floats, stars twinkle,
the avatar ring has glowing arcs orbiting it, and a shine sweeps over the member's name.

Optional env vars:
    BOOST_ANIMATED=0     send a still PNG instead of the animated GIF
    FONT_PATH=...        force a font file
"""

import io
import math
import os
import random
import unicodedata
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

W, H = 1000, 360
MAGENTA = (255, 90, 210)
PURPLE = (170, 70, 255)
VIOLET = (120, 40, 220)

ANIMATED = os.environ.get("BOOST_ANIMATED", "1") != "0"
CARD_EXT = "gif" if ANIMATED else "png"

N_FRAMES = 24          # one seamless loop
FRAME_MS = 70          # ~14 fps  ->  loop of ~1.7 s
FLAME_Y = 196          # rows below this get the flame ripple

_HERE = os.path.dirname(os.path.abspath(__file__))
BG_PATH = os.path.join(_HERE, "boost_bg.jpg")


# ------------------------------------------------------------------ text helpers
def safe_text(text: str, fallback: str = "") -> str:
    """Keeps only characters a normal font can draw (fancy letters become plain ones)."""
    text = unicodedata.normalize("NFKC", text or "")
    out = "".join(c for c in text if ord(c) < 0x250 and (c.isalnum() or c in " ._-'&!|#@+()"))
    out = " ".join(out.split())
    return out or fallback


@lru_cache(maxsize=None)
def _font_paths():
    paths = [os.environ.get("FONT_PATH")]
    fonts_dir = os.path.join(_HERE, "fonts")
    if os.path.isdir(fonts_dir):
        files = sorted(f for f in os.listdir(fonts_dir) if f.lower().endswith((".ttf", ".otf")))
        files.sort(key=lambda f: 0 if "bold" in f.lower() else 1)
        paths += [os.path.join(fonts_dir, f) for f in files]
    paths += [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    return [p for p in paths if p and os.path.exists(p)]


@lru_cache(maxsize=None)
def font(size: int):
    for p in _font_paths():
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)      # Pillow 10.1+
    except TypeError:
        return ImageFont.load_default()


_scratch = ImageDraw.Draw(Image.new("L", (1, 1)))


def text_w(text, fnt, spacing=0):
    return _scratch.textlength(text, font=fnt) + spacing * max(len(text) - 1, 0)


def draw_spaced(draw, xy, text, fnt, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + spacing


def fit_text(text, max_w, start, minimum):
    """Largest font size (start -> minimum) that fits; ellipsis if even the smallest is too wide."""
    for size in range(start, minimum - 1, -2):
        f = font(size)
        if text_w(text, f) <= max_w:
            return text, f
    f = font(minimum)
    while len(text) > 1 and text_w(text + "…", f) > max_w:
        text = text[:-1]
    return text.rstrip() + "…", f


def text_layer(xy, text, fnt, fill, spacing=0, shadow=True):
    """Returns (RGBA layer, L mask) with the text drawn cleanly (soft dark shadow underneath)."""
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    if spacing:
        draw_spaced(md, xy, text, fnt, 255, spacing)
    else:
        md.text(xy, text, font=fnt, fill=255)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if shadow:
        sh = ImageChops.offset(mask, 2, 3).filter(ImageFilter.GaussianBlur(3)).point(lambda v: int(v * 0.85))
        black = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        black.putalpha(sh)
        layer = Image.alpha_composite(layer, black)
    col = Image.new("RGBA", (W, H), tuple(fill[:3]) + (0,))
    col.putalpha(mask)
    return Image.alpha_composite(layer, col), mask


# ------------------------------------------------------------------ shapes
def star(draw, cx, cy, r, fill):
    k = r * 0.28
    draw.polygon([(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
                  (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)], fill=fill)


def gem(draw, cx, cy, r, fill, outline=None):
    pts = [(cx, cy - r), (cx + r * 0.85, cy - r * 0.15), (cx, cy + r), (cx - r * 0.85, cy - r * 0.15)]
    draw.polygon(pts, fill=fill, outline=outline)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ background art
@lru_cache(maxsize=1)
def _load_art():
    """-> (background RGB W x H with the ghost removed, ghost RGBA cut-out or None)"""
    if not os.path.exists(BG_PATH):
        return _fallback_bg(), None
    try:
        src = Image.open(BG_PATH).convert("RGB")
    except Exception:
        return _fallback_bg(), None

    # clean, pure blacks (kills JPEG noise in the dark areas)
    src = src.point(lambda v: max(0, v - 14) * 255 // 241)

    sw, sh = src.size
    # cut the ghost out (it sits alone on black, top-middle of the artwork) ...
    gbox = (int(sw * 0.440), int(sh * 0.255), int(sw * 0.567), int(sh * 0.495))
    ghost = src.crop(gbox).convert("RGBA")
    ghost.putalpha(ghost.split()[2].point(lambda v: 0 if v < 50 else min(255, (v - 50) * 255 // 110)))
    ghost = ghost.crop(ghost.getbbox() or (0, 0, *ghost.size))
    ImageDraw.Draw(src).rectangle(gbox, fill=(0, 0, 0))   # ... and black its old spot out

    # cover-fit, anchored so the flame tips sit around y=215
    scale = max(W / sw, H / sh)
    big = src.resize((int(sw * scale) + 1, int(sh * scale) + 1), Image.LANCZOS)
    flame_top = int(big.height * 0.594)
    top = max(0, min(big.height - H, flame_top - 215))
    left = (big.width - W) // 2
    bg = big.crop((left, top, left + W, top + H))

    ghost = ghost.resize((int(ghost.width * 0.95), int(ghost.height * 0.95)), Image.LANCZOS)
    return bg, ghost


def _fallback_bg():
    bg = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(bg)
    for y in range(H // 2, H):
        t = (y - H // 2) / (H // 2)
        d.line([(0, y), (W, y)], fill=(int(90 * t * t), int(15 * t * t), int(160 * t * t)))
    return bg


def _scrim():
    """Dark veil over the flames behind the text so everything stays readable."""
    hg = Image.new("L", (W, H), 0)
    hd = ImageDraw.Draw(hg)
    for x in range(W):
        hd.line([(x, 0), (x, H)], fill=int(255 * min(1, max(0, (x - 250) / 150))))
    vg = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vg)
    for y in range(H):
        vd.line([(0, y), (W, y)], fill=int(255 * min(1, max(0, (y - 165) / 70))))
    alpha = ImageChops.multiply(hg, vg).point(lambda v: int(v * 125 / 255))
    veil = Image.new("RGBA", (W, H), (4, 0, 10, 0))
    veil.putalpha(alpha)
    return veil


def warp_flames(bg, phase):
    out = bg.copy()
    span = H - FLAME_Y
    for y in range(FLAME_Y, H, 2):
        k = (y - FLAME_Y) / span
        amp = 7.5 * (1 - 0.65 * k)
        dx = amp * math.sin(phase + y * 0.05) + 0.45 * amp * math.sin(2 * phase + y * 0.13 + 1.3)
        dx = int(round(dx))
        if dx:
            out.paste(ImageChops.offset(bg.crop((0, y, W, y + 2)), dx, 0), (0, y))
    box = (0, FLAME_Y - 25, W, H)
    region = ImageEnhance.Brightness(out.crop(box)).enhance(1 + 0.13 * math.sin(2 * phase + 0.7))
    out.paste(region, box[:2])
    return out


# ------------------------------------------------------------------ the card
def render_boost_card(name, server_name, avatar_bytes, level, total_boosts, boosts=1, level_up=None) -> bytes:
    name = safe_text(name, "Booster")
    server_name = safe_text(server_name, "the server")

    bg, ghost = _load_art()
    veil = _scrim()

    cx, cy = 175, 180

    # ---------- static: avatar block ----------
    av_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ad = ImageDraw.Draw(av_layer)
    ad.ellipse([cx - 122, cy - 122, cx + 122, cy + 122], fill=MAGENTA + (255,))
    ad.ellipse([cx - 115, cy - 115, cx + 115, cy + 115], fill=(14, 4, 28, 255))
    try:
        av = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((222, 222), Image.LANCZOS)
    except Exception:
        av = Image.new("RGBA", (222, 222), (40, 14, 80, 255))
        gem(ImageDraw.Draw(av), 111, 111, 60, MAGENTA + (255,))
    big_mask = Image.new("L", (888, 888), 0)
    ImageDraw.Draw(big_mask).ellipse([0, 0, 887, 887], fill=255)
    av_layer.paste(av, (cx - 111, cy - 111), big_mask.resize((222, 222), Image.LANCZOS))
    bx, by = cx + 84, cy + 88
    ad.ellipse([bx - 34, by - 34, bx + 34, by + 34], fill=(14, 4, 28, 255))
    ad.ellipse([bx - 29, by - 29, bx + 29, by + 29], fill=MAGENTA + (255,))
    gem(ad, bx, by, 17, (255, 255, 255, 255))

    # ---------- static: glow masks ----------
    glow_av = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow_av).ellipse([cx - 150, cy - 150, cx + 150, cy + 150], fill=210)
    glow_av = glow_av.filter(ImageFilter.GaussianBlur(42))
    ghost_glow = None
    if ghost is not None:
        gl = Image.new("L", (W, H), 0)
        gl.paste(ghost.split()[3], (0, 0))
        ghost_glow = gl  # re-positioned per frame below

    # ---------- static: text ----------
    x0 = 340
    max_w = W - x0 - 50
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if level_up:
        tag = f"LEVEL {level_up} UNLOCKED"
    elif boosts > 1:
        tag = f"{boosts}X SERVER BOOST"
    else:
        tag = "NEW SERVER BOOST"
    lay, _ = text_layer((x0, 52), tag, font(24), MAGENTA, spacing=5)
    top = Image.alpha_composite(top, lay)
    ImageDraw.Draw(top).line([(x0, 90), (x0 + 70, 90)], fill=PURPLE + (255,), width=4)

    shown, f = fit_text(name, max_w, 68, 34)
    lay, name_mask = text_layer((x0, 104), shown, f, (255, 255, 255))
    top = Image.alpha_composite(top, lay)

    if level_up:
        line = f"unlocked Level {level_up} for {server_name}!"
    elif boosts > 1:
        line = f"boosted {server_name} {boosts} times!"
    else:
        line = f"just boosted {server_name}!"
    shown, f = fit_text(line, max_w, 30, 18)
    lay, _ = text_layer((x0, 190), shown, f, (232, 212, 252))
    top = Image.alpha_composite(top, lay)

    f2 = font(22)
    pills = [(f"LEVEL {level}", True), (f"{total_boosts} BOOST{'S' if total_boosts != 1 else ''}", False)]
    px = x0
    pd = ImageDraw.Draw(top)
    pill_texts = []
    for text, filled in pills:
        w = int(text_w(text, f2, 2)) + 44
        pd.rounded_rectangle([px, 248, px + w, 296], radius=24,
                             fill=(78, 16, 120, 255) if filled else (18, 6, 34, 255),
                             outline=MAGENTA + (255,), width=2)
        pill_texts.append((px + 22, text))
        px += w + 16
    for tx, text in pill_texts:
        lay, _ = text_layer((tx, 258), text, f2, (255, 255, 255), spacing=2, shadow=False)
        top = Image.alpha_composite(top, lay)

    shown, f = fit_text(server_name.upper(), max_w, 18, 12)
    lay, _ = text_layer((x0, 316), shown, f, (196, 160, 236))
    top = Image.alpha_composite(top, lay)

    # ---------- dynamic helpers ----------
    rnd = random.Random(sum(map(ord, name)) + level)
    embers = [dict(x=rnd.uniform(20, W - 20), y=rnd.uniform(0, H), k=rnd.choice([1, 1, 2]),
                   r=rnd.choice([1.6, 2, 2.4, 3, 3.6]), off=rnd.uniform(0, 6.28),
                   col=rnd.choice([MAGENTA, PURPLE, (230, 190, 255)])) for _ in range(38)]
    stars = [dict(x=rnd.randint(660, 840), y=rnd.randint(14, 110), r=rnd.choice([4, 5, 7, 9]),
                  m=rnd.choice([1, 2]), off=rnd.uniform(0, 6.28)) for _ in range(9)]
    band = Image.new("L", (220, H), 0)
    bd = ImageDraw.Draw(band)
    for i in range(220):
        bd.line([(i, 0), (i, H)], fill=int(255 * max(0, 1 - abs(i - 110) / 110) ** 1.5))

    gx, gy0 = W - 128, 10

    def frame(i):
        t = i / N_FRAMES
        phase = 2 * math.pi * t
        pulse = 0.5 + 0.5 * math.sin(phase)

        img = warp_flames(bg, phase).convert("RGBA")
        img = Image.alpha_composite(img, veil)

        # glows
        g = Image.new("RGBA", (W, H), (190, 60, 255, 0))
        g.putalpha(glow_av.point(lambda v, k=0.40 + 0.30 * pulse: int(v * k)))
        img = Image.alpha_composite(img, g)

        # twinkling stars + rising embers
        fx = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fx)
        for s in stars:
            a = int(30 + 190 * (0.5 + 0.5 * math.sin(phase * s["m"] + s["off"])) ** 2)
            star(fd, s["x"], s["y"], s["r"], (255, 205, 245, a))
        for e in embers:
            y = (e["y"] - e["k"] * H * t) % H
            x = e["x"] + 7 * math.sin(phase * e["k"] + e["off"])
            a = int(235 * (y / H) ** 0.8)
            r = e["r"]
            fd.ellipse([x - r * 2, y - r * 2, x + r * 2, y + r * 2], fill=e["col"] + (a // 6,))
            fd.ellipse([x - r, y - r, x + r, y + r], fill=e["col"] + (a,))
        img = Image.alpha_composite(img, fx)

        # avatar block
        img = Image.alpha_composite(img, av_layer)

        # orbiting arcs around the avatar
        R, size, ss = 138, 292, 3
        arc = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        ad2 = ImageDraw.Draw(arc)
        c = size * ss // 2
        rr = R * ss
        box = [c - rr, c - rr, c + rr, c + rr]
        ad2.ellipse(box, outline=PURPLE + (60,), width=2 * ss)
        base = 360 * t
        for off, col, ln in ((0, MAGENTA, 70), (120, PURPLE, 70), (240, (255, 225, 250), 36)):
            ad2.arc(box, base + off, base + off + ln, fill=col + (255,), width=5 * ss)
        arc = arc.resize((size, size), Image.LANCZOS)
        img.alpha_composite(arc, (cx - size // 2, cy - size // 2))

        # floating ghost
        if ghost is not None:
            bob = int(round(5 * math.sin(phase)))
            pos = (gx - ghost.width // 2, gy0 + bob)
            halo = Image.new("L", (W, H), 0)
            halo.paste(ghost.split()[3], pos)
            halo = halo.filter(ImageFilter.GaussianBlur(14)).point(lambda v, k=0.55 + 0.45 * pulse: int(v * k))
            hl = Image.new("RGBA", (W, H), (200, 60, 255, 0))
            hl.putalpha(halo)
            img = Image.alpha_composite(img, hl)
            img.alpha_composite(ghost, pos)
        else:
            d = ImageDraw.Draw(img)
            gem(d, gx, gy0 + 45, 34, MAGENTA + (255,))

        # text
        img = Image.alpha_composite(img, top)

        # shine sweeping over the name
        prog = min(1.0, t / 0.7)
        sx = int(-220 + (W + 120) * prog)
        sweep = Image.new("L", (W, H), 0)
        sweep.paste(band, (sx, 0))
        sm = ImageChops.multiply(sweep, name_mask)
        if sm.getbbox():
            shine = Image.new("RGBA", (W, H), (255, 175, 245, 0))
            shine.putalpha(sm.point(lambda v: int(v * 0.85)))
            img = Image.alpha_composite(img, shine)

        # pulsing border
        ImageDraw.Draw(img).rectangle([1, 1, W - 2, H - 2], outline=lerp(PURPLE, MAGENTA, pulse) + (255,), width=3)
        return img.convert("RGB")

    out = io.BytesIO()
    if not ANIMATED:
        frame(0).save(out, "PNG", optimize=True)
        return out.getvalue()

    frames = [frame(i) for i in range(N_FRAMES)]

    # one shared palette for every frame (no colour flicker, smaller file)
    montage = Image.new("RGB", (W, H * 4))
    for j, idx in enumerate((0, N_FRAMES // 4, N_FRAMES // 2, 3 * N_FRAMES // 4)):
        montage.paste(frames[idx], (0, H * j))
    pal = montage.quantize(colors=255, method=Image.MEDIANCUT, dither=Image.NONE)
    pframes = [fr.quantize(palette=pal, dither=Image.NONE) for fr in frames]
    pframes[0].save(out, "GIF", save_all=True, append_images=pframes[1:],
                    duration=FRAME_MS, loop=0, optimize=False)
    return out.getvalue()
