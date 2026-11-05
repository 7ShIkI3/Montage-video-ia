"""Génère le clip de test « gameplay PvP en voxels » (le même que « Essayer sans vidéo » dans l'app).

Le rendu vit dans `backend/app/media/demo.py` ; ce script n'est qu'une ligne de commande.

Usage : python scripts/make_pvp_clip.py sortie.mp4 [--seconds 40] [--fps 60] [--size 1280x720]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.media import demo  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seconds", type=float, default=demo.SECONDS)
    ap.add_argument("--fps", type=int, default=demo.FPS)
    ap.add_argument("--size", default=demo.SIZE)
    a = ap.parse_args()
    last = -1

    def progress(p: float) -> None:
        nonlocal last
        pct = int(p * 100)
        if pct // 5 != last // 5:
            last = pct
            print(f"\r{pct:3d} %", end="", flush=True)
    out = demo.render(Path(a.out), a.seconds, a.fps, a.size, progress=progress)
    print(f"\r{out}")


if __name__ == "__main__":
    main()
