"""Analyse vidéo légère : mouvement et changements de plan sur des images 64x36."""
from __future__ import annotations

import numpy as np

from .. import config


def analyze(frames: np.ndarray, fps: float = config.ANALYSIS_FPS) -> dict:
    if frames.shape[0] < 2:
        return {"fps": fps, "motion": [], "cuts": [], "brightness": []}
    diff = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))  # 0..255
    motion = np.concatenate([[0.0], diff])
    # Changement de plan = pic de différence nettement au-dessus du bruit local.
    med = float(np.median(diff))
    mad = float(np.median(np.abs(diff - med))) + 1e-6
    thr = max(med + 8 * mad, 18.0)
    cuts: list[float] = []
    for i in range(1, len(diff) - 1):
        if diff[i] >= thr and diff[i] >= diff[i - 1] and diff[i] >= diff[i + 1]:
            t = (i + 1) / fps
            if not cuts or t - cuts[-1] > 0.5:
                cuts.append(round(t, 3))
    # On normalise le mouvement hors changements de plan.
    clipped = np.minimum(motion, thr)
    norm = clipped / (thr + 1e-6)
    return {
        "fps": fps,
        "motion": [round(float(x), 3) for x in norm],
        "cuts": cuts,
        "brightness": [round(float(x), 1) for x in frames.mean(axis=(1, 2))],
    }


def red_flashes(frames_rgb: np.ndarray, fps: float = config.ANALYSIS_FPS, min_gap: float = 0.4,
                jump: float = 14.0, floor: float = 10.0) -> list[float]:
    """Instants où l'image rougit d'un coup (dégâts encaissés dans la plupart des jeux : écran teinté de rouge,
    caméra qui tremble). Mesure : rouge moyen moins la moyenne vert/bleu, comparée aux images d'avant."""
    if frames_rgb.ndim != 4 or frames_rgb.shape[0] < 3:
        return []
    f = frames_rgb.astype(np.float32)
    redness = f[..., 0].mean(axis=(1, 2)) - (f[..., 1].mean(axis=(1, 2)) + f[..., 2].mean(axis=(1, 2))) / 2
    out: list[float] = []
    hot = False  # vrai tant que l'image reste rouge : on ne compte que le front montant
    for i in range(1, len(redness)):
        base = float(np.median(redness[max(0, i - 6):i]))
        rising = redness[i] - base >= jump and redness[i] >= floor
        if rising and not hot:
            t = round(i / fps, 3)
            if not out or t - out[-1] > min_gap:
                out.append(t)
        hot = rising
    return out
