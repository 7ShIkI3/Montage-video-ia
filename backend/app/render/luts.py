"""LUT d'étalonnage (.cube) : quelques LUT intégrées générées à la volée, et celles que l'utilisateur importe."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import config, storage

SIZE = 33


def _smooth(x):
    return x * x * (3 - 2 * x)


def _teal_orange(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    w = _smooth(np.clip(lum, 0, 1))
    # Ombres vers le bleu-vert, hautes lumières et peaux vers l'orange.
    r2 = r + 0.10 * w - 0.06 * (1 - w)
    g2 = g + 0.02 * w + 0.03 * (1 - w)
    b2 = b - 0.10 * w + 0.10 * (1 - w)
    return _contrast(r2, g2, b2, 1.08)


def _contrast(r, g, b, k):
    return tuple(np.clip(0.5 + (c - 0.5) * k, 0, 1) for c in (r, g, b))


def _film_chaud(r, g, b):
    r, g, b = r * 1.04 + 0.03, g * 1.0 + 0.01, b * 0.9
    lift = 0.04
    r, g, b = (lift + c * (1 - lift) for c in (r, g, b))
    return _contrast(r, g, b, 0.95)


def _nuit(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    return np.clip(lum * 0.55 + r * 0.1, 0, 1), np.clip(lum * 0.7, 0, 1), np.clip(lum * 0.95 + 0.05, 0, 1)


def _desature(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    s = 0.55
    return _contrast(*(lum + (c - lum) * s for c in (r, g, b)), 1.12)


def _pop(r, g, b):
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    s = 1.35
    return _contrast(*(lum + (c - lum) * s for c in (r, g, b)), 1.1)


BUILTIN = {
    "teal_orange": ("Teal & orange (blockbuster)", _teal_orange),
    "film_chaud": ("Film chaud", _film_chaud),
    "nuit": ("Nuit américaine", _nuit),
    "desature": ("Désaturé contrasté", _desature),
    "pop": ("Couleurs pop", _pop),
}


def cube_text(fn, title: str, n: int = SIZE) -> str:
    """LUT 3D au format .cube (rouge varie le plus vite)."""
    v = np.linspace(0, 1, n, dtype=np.float64)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    r2, g2, b2 = fn(r.ravel(), g.ravel(), b.ravel())
    rows = np.stack([np.clip(r2, 0, 1), np.clip(g2, 0, 1), np.clip(b2, 0, 1)], axis=1)
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}"]
    lines += [f"{x:.5f} {y:.5f} {z:.5f}" for x, y, z in rows]
    return "\n".join(lines) + "\n"


def builtin_dir() -> Path:
    d = config.DATA_DIR / "luts"
    d.mkdir(parents=True, exist_ok=True)
    for key, (title, fn) in BUILTIN.items():
        p = d / f"{key}.cube"
        if not p.exists():
            p.write_text(cube_text(fn, title), encoding="utf-8")
    return d


def project_dir(project_id: str) -> Path:
    d = storage.project_dir(project_id) / "luts"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve(project_id: str, ref: str | None) -> Path | None:
    """Chemin du fichier .cube désigné par la timeline, ou None."""
    if not ref:
        return None
    kind, _, name = ref.partition(":")
    name = Path(name).name
    if kind == "integre" and name in BUILTIN:
        return builtin_dir() / f"{name}.cube"
    if kind == "projet":
        p = project_dir(project_id) / name
        return p if p.exists() and p.suffix.lower() == ".cube" else None
    return None


def listing(project_id: str) -> list[dict]:
    out = [{"id": f"integre:{k}", "label": v[0], "builtin": True} for k, v in BUILTIN.items()]
    for p in sorted(project_dir(project_id).glob("*.cube")):
        out.append({"id": f"projet:{p.name}", "label": p.stem, "builtin": False})
    return out


def validate_cube(text: str) -> None:
    """Vérifie qu'un fichier ressemble à une LUT 3D .cube lisible par FFmpeg."""
    size = None
    count = 0
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.upper().startswith("LUT_3D_SIZE"):
            size = int(s.split()[1])
        elif s[0].isdigit() or s[0] in "-.":
            count += 1
    if not size or size < 2 or size > 128:
        raise ValueError("Fichier .cube invalide : LUT_3D_SIZE manquant (seules les LUT 3D sont acceptées).")
    if count < size ** 3:
        raise ValueError(f"Fichier .cube incomplet : {count} lignes de couleur au lieu de {size ** 3}.")
