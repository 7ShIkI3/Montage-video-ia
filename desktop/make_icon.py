"""Dessine l'icône de l'application (desktop/monteur.ico) : le carré corail et le triangle de lecture de l'interface.

    python desktop/make_icon.py

L'icône est versionnée ; ce script ne sert qu'à la redessiner.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).with_name("monteur.ico")
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
BIG = 1024  # dessin en grand, puis réduction : les bords sont lissés


def draw() -> Image.Image:
    # Dégradé vertical corail (le --accent de l'interface, un peu plus clair en haut).
    top, bottom = (255, 138, 102), (255, 92, 46)
    grad = Image.new("RGB", (1, BIG))
    for y in range(BIG):
        k = y / (BIG - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * k) for a, b in zip(top, bottom)))
    grad = grad.resize((BIG, BIG))
    mask = Image.new("L", (BIG, BIG), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, BIG - 1, BIG - 1), radius=BIG * 0.235, fill=255)
    icon = Image.new("RGBA", (BIG, BIG), (0, 0, 0, 0))
    icon.paste(grad, (0, 0), mask)
    # Triangle de lecture, centré sur son centre de gravité (un triangle centré sur sa boîte paraît décalé).
    w, h = BIG * 0.36, BIG * 0.42
    cx, cy = BIG * 0.5 + w / 6, BIG * 0.5
    tri = [(cx - w / 2, cy - h / 2), (cx + w / 2, cy), (cx - w / 2, cy + h / 2)]
    ImageDraw.Draw(icon).polygon(tri, fill=(255, 255, 255, 255))
    return icon


def main() -> None:
    icon = draw()
    icon.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"{OUT} : {OUT.stat().st_size} octets, tailles {SIZES}")


if __name__ == "__main__":
    main()
