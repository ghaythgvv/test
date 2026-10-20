"""Run once:  python make_banners.py   -> animated 900x300 GIF banners (same purple style as the esport banner)."""
import math, os, random
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, N, MS = 900, 300, 24, 70
BANNERS = {
    "studycard": ("ELITE STUDY CARD", "GET YOUR CARD  •  UNLOCK THE STUDY AREA"),
    "staffapps": ("STAFF APPLICATIONS", "JOIN THE TEAM  •  HELP SHAPE THE COMMUNITY"),
    "support":   ("MODERATOR SUPPORT", "DESCRIBE YOUR ISSUE  •  WE WILL HELP"),
    "verify":    ("VERIFICATION", "WAITING FOR MOVE  •  STAFF WILL VERIFY YOU"),
    "welcome":   ("WELCOME", "ELITE LEADERS COMMUNITY"),
    "xp":        ("STAFF LEADERBOARD", "ACTIVITY  •  POINTS  •  RANKS"),
}
font = lambda size: ImageFont.truetype(os.path.join(HERE, "fonts", "Poppins-Bold.ttf"), size)
PURPLE = (190, 110, 255)


def background():
    bg = Image.new("RGB", (W, H))
    px = bg.load()
    for y in range(H):
        for x in range(W):
            k = max(0.0, 1 - math.hypot((x - W / 2) / (W * .6), (y - H / 2) / (H * .9)))
            px[x, y] = (int(8 + 70 * k), int(3 + 20 * k), int(18 + 120 * k))
    return bg.convert("RGBA")


def build(key, title, sub, base, seed):
    rnd = random.Random(seed)
    size = 84
    f = font(size)
    d0 = ImageDraw.Draw(Image.new("L", (1, 1)))
    while d0.textlength(title, font=f) > W - 90:
        size -= 4
        f = font(size)
    tw = d0.textlength(title, font=f)
    pos = ((W - tw) / 2, H / 2 - size * 0.85)
    tmask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(tmask).text(pos, title, font=f, fill=255)
    glow_big, glow_small = tmask.filter(ImageFilter.GaussianBlur(14)), tmask.filter(ImageFilter.GaussianBlur(5))

    static = base.copy()                                     # subtitle sits on the static layer
    sf = font(21)
    sd = ImageDraw.Draw(static)
    sd.text(((W - sd.textlength(sub, font=sf)) / 2, H / 2 + 52), sub, font=sf, fill=(199, 163, 255, 255))

    band = Image.new("L", (160, H), 0)                       # slanted light band that sweeps over the title
    bd = ImageDraw.Draw(band)
    for x in range(160):
        bd.line((x, 0, x, H), fill=int(255 * max(0, 1 - abs(x - 80) / 80) ** 1.6))
    band = band.transform((160 + H // 3, H), Image.AFFINE, (1, 0.33, -H * 0.33, 0, 1, 0), resample=Image.BILINEAR)

    stars = [(rnd.uniform(0, W), rnd.uniform(0, H), rnd.choice((1, 1, 2)), rnd.random(), rnd.choice((1, 2)), rnd.choice((1, 1, 2, 3)))
             for _ in range(46)]                             # x, y, cycles per loop, phase, twinkle speed, size

    frames = []
    for k in range(N):
        t = k / N
        img = static.copy()
        pulse = 0.78 + 0.22 * math.sin(2 * math.pi * t)
        for g, a in ((glow_big, 0.95), (glow_small, 1.0)):   # pulsing glow behind the title
            lay = Image.new("RGBA", (W, H), PURPLE + (0,))
            lay.putalpha(g.point(lambda v, m=a * pulse: min(255, int(v * 1.6 * m))))
            img.alpha_composite(lay)
        white = Image.new("RGBA", (W, H), (255, 255, 255, 0))
        white.putalpha(tmask)
        img.alpha_composite(white)
        sweep = Image.new("L", (W, H), 0)                    # light sweep, only over the letters
        sweep.paste(band, (int(-band.width + (W + band.width) * t), 0))
        lit = Image.new("RGBA", (W, H), (215, 170, 255, 0))
        lit.putalpha(ImageChops.multiply(sweep, tmask).point(lambda v: int(v * 0.9)))
        img.alpha_composite(lit)

        sp = Image.new("RGBA", (W, H), (0, 0, 0, 0))         # drifting, twinkling sparkles
        sd = ImageDraw.Draw(sp)
        for x, y, cyc, ph, tv, s in stars:
            yy = (y - cyc * H * t) % H
            a = int(255 * (0.25 + 0.75 * (0.5 + 0.5 * math.sin(2 * math.pi * (tv * t + ph)))))
            sd.ellipse((x - s, yy - s, x + s, yy + s), fill=(235, 205, 255, a))
        img.alpha_composite(sp.filter(ImageFilter.GaussianBlur(0.9)))
        img.alpha_composite(sp)

        ly = int(H * ((t * 1.0) % 1.0))                      # thin scan line
        scan = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(scan).rectangle((0, ly, W, ly + 1), fill=(200, 140, 255, 38))
        img.alpha_composite(scan)

        br = Image.new("RGBA", (W, H), (0, 0, 0, 0))         # corner brackets, softly pulsing
        bd2 = ImageDraw.Draw(br)
        c, l, m = (181, 107, 255, int(170 + 85 * (0.5 + 0.5 * math.sin(2 * math.pi * t)))), 38, 16
        for cx, cy, sx, sy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
            bd2.line((cx, cy, cx + sx * l, cy), fill=c, width=3)
            bd2.line((cx, cy, cx, cy + sy * l), fill=c, width=3)
        img.alpha_composite(br)
        frames.append(img.convert("RGB"))

    tile = (W // 2, H // 2)                                  # one shared palette -> no colour flicker
    montage = Image.new("RGB", (tile[0] * 2, tile[1] * 2))
    for i, fi in enumerate((0, N // 4, N // 2, 3 * N // 4)):
        montage.paste(frames[fi].resize(tile), ((i % 2) * tile[0], (i // 2) * tile[1]))
    pal = montage.quantize(colors=200, method=Image.Quantize.MEDIANCUT)
    out = [fr.quantize(palette=pal, dither=Image.Dither.NONE) for fr in frames]
    path = os.path.join(HERE, "banners", f"{key}.gif")
    out[0].save(path, save_all=True, append_images=out[1:], duration=MS, loop=0, optimize=False)
    return path


if __name__ == "__main__":
    base = background()
    for i, (k, (t, s)) in enumerate(BANNERS.items()):
        p = build(k, t, s, base, seed=i + 7)
        old = os.path.join(HERE, "banners", f"{k}.png")
        if os.path.exists(old):
            os.remove(old)
        print(f"{k}: {os.path.getsize(p) // 1024} KB")
