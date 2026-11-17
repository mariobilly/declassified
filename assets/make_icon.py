"""Draws the Declassified icon (assets/icon.ico, assets/icon.png, ui/icon.png): a typed sheet whose redaction
bars form a face, with a red stamp."""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
S = 1024


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((32, 32, S - 32, S - 32), radius=200, fill=(24, 24, 26, 255))
    # sheet with a soft shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rectangle((214, 150, 834, 930), fill=(0, 0, 0, 170))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(22)))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = 196, 120, 816, 900
    d.rectangle((x0, y0, x1, y1), fill=(246, 245, 240, 255))
    # typed lines (grey) on a grid, bars (black) shaped like a head: hair cap, eyes, mouth, neck/shoulders
    lh = 34
    rows = [(y0 + 60 + i * lh) for i in range(23)]
    for i, y in enumerate(rows):
        L = x0 + 46
        R = x1 - 46 - (i * 37 % 120)
        d.rectangle((L, y + 10, R, y + 20), fill=(150, 150, 146, 255))
    bars = {
        0: [(330, 690)], 1: [(290, 730)], 2: [(270, 750)], 3: [(262, 420), (600, 758)], 4: [(262, 360), (690, 758)],
        5: [(262, 330), (720, 758)], 6: [(262, 316), (380, 460), (560, 640), (728, 758)],
        7: [(262, 310), (734, 758)], 8: [(262, 306), (488, 520)], 9: [(268, 300), (480, 528)],
        11: [(420, 600)], 13: [(330, 360), (660, 690)], 14: [(360, 400), (620, 660)], 15: [(400, 620)],
        17: [(250, 400), (620, 770)], 18: [(236, 784)], 19: [(236, 784)], 20: [(236, 784)], 21: [(236, 784)],
    }
    for r, segs in bars.items():
        y = rows[r]
        for (a, b) in segs:
            d.rectangle((a, y + 1, b, y + lh - 1), fill=(22, 22, 23, 255))
    # red stamp
    st = Image.new("RGBA", (420, 130), (0, 0, 0, 0))
    sd = ImageDraw.Draw(st)
    sd.rectangle((6, 6, 414, 124), outline=(210, 60, 48, 235), width=12)
    try:
        f = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "arialbd.ttf"), 74)
    except OSError:
        f = ImageFont.load_default()
    tw = sd.textlength("SECRET", font=f)
    sd.text(((420 - tw) / 2, 22), "SECRET", font=f, fill=(210, 60, 48, 235))
    st = st.rotate(-14, resample=Image.BICUBIC, expand=True)
    img.alpha_composite(st, (520, 610))
    out = img.resize((256, 256), Image.LANCZOS)
    out.save(os.path.join(HERE, "icon.png"))
    out.save(os.path.join(ROOT, "ui", "icon.png"))
    img.save(os.path.join(HERE, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon written")


if __name__ == "__main__":
    main()
