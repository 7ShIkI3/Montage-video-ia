"""Suivi du visage principal pour le recadrage automatique (16:9 → 9:16 ou 1:1).

Comme un cadreur : on détecte les visages quelques fois par seconde, on garde le
plus gros (en privilégiant la continuité), puis on lisse la trajectoire avec une
zone morte pour que le cadre ne « flotte » pas. La trajectoire repart à zéro à
chaque changement de plan.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

FACE_FPS = 3.0
DEAD_ZONE = 0.04  # le cadre ne bouge pas tant que le visage reste dans ±4 % de la largeur
SMOOTH = 0.5     # lissage exponentiel une fois la zone morte franchie

try:  # OpenCV est optionnel : sans lui, on recadre au centre.
    import cv2

    _CASCADES = [cv2.CascadeClassifier(cv2.data.haarcascades + n)
                 for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml")]
    AVAILABLE = all(not c.empty() for c in _CASCADES)
except Exception:  # pragma: no cover - dépend de l'installation
    cv2 = None
    _CASCADES = []
    AVAILABLE = False


def detect(frames: np.ndarray) -> list[tuple[float, float, float] | None]:
    """Pour chaque image (niveaux de gris uint8), (cx, cy, taille) normalisés du visage principal."""
    out: list[tuple[float, float, float] | None] = []
    prev: tuple[float, float, float] | None = None
    for f in frames:
        h, w = f.shape
        img = np.ascontiguousarray(f)
        boxes: list[tuple[int, int, int, int]] = []
        mins = max(18, int(min(w, h) * 0.06))
        for k, casc in enumerate(_CASCADES):
            found = casc.detectMultiScale(img, scaleFactor=1.1, minNeighbors=4, minSize=(mins, mins))
            boxes += [tuple(b) for b in found]
            if k == 0 and len(found):
                break
            if k == 1 and not len(found):  # profil tourné de l'autre côté
                flip = casc.detectMultiScale(img[:, ::-1].copy(), 1.1, 4, minSize=(mins, mins))
                boxes += [(w - x - bw, y, bw, bh) for (x, y, bw, bh) in flip]
        if not boxes:
            out.append(None)
            continue

        def score(b):
            x, y, bw, bh = b
            cx, cy, s = (x + bw / 2) / w, (y + bh / 2) / h, bw / w
            near = 0.0 if prev is None else abs(cx - prev[0])
            return s - 0.6 * near

        x, y, bw, bh = max(boxes, key=score)
        prev = ((x + bw / 2) / w, (y + bh / 2) / h, bw / w)
        out.append(prev)
    return out


def _follow(arr) -> list[float]:
    """Cadreur virtuel : immobile tant que le visage reste dans la zone morte, puis le rattrape en douceur."""
    vals = [float(v) for v in arr]
    cam = float(np.median(vals[: min(len(vals), 6)]))
    out = []
    for v in vals:
        if abs(v - cam) > DEAD_ZONE:
            target = v - np.sign(v - cam) * DEAD_ZONE
            cam += (target - cam) * SMOOTH
        out.append(float(cam))
    return out


def smooth_track(raw: list[float | None], hop: float, cuts: list[float]) -> list[float]:
    """Trajectoire horizontale stable (0..1), avec zone morte et remise à zéro aux changements de plan."""
    n = len(raw)
    if not n:
        return []
    cut_idx = sorted({min(n - 1, max(0, int(round(c / hop)))) for c in cuts})
    bounds = [0] + [i for i in cut_idx if i > 0] + [n]
    out: list[float] = []
    for a, b in zip(bounds, bounds[1:]):
        seg = raw[a:b]
        vals = [v for v in seg if v is not None]
        if not vals:
            out += [0.5] * len(seg)
            continue
        # Trous comblés par la dernière position connue (la première connue au début).
        filled, last = [], vals[0]
        for v in seg:
            last = v if v is not None else last
            filled.append(last)
        arr = np.array(filled)
        if len(arr) >= 5:  # médiane glissante : supprime les fausses détections isolées
            pad = np.pad(arr, 2, mode="edge")
            arr = np.median(np.lib.stride_tricks.sliding_window_view(pad, 5), axis=1)
        # Passe avant + passe arrière puis moyenne : le cadre anticipe au lieu de traîner derrière le visage
        # (possible car on monte après coup, pas en direct).
        fwd = _follow(arr)
        bwd = _follow(arr[::-1])[::-1]
        out += [round((a + b) / 2, 4) for a, b in zip(fwd, bwd)]
    return out


def analyze(frames: np.ndarray, cuts: list[float], fps: float = FACE_FPS) -> dict:
    if not AVAILABLE or not len(frames):
        return {"hop": 1 / fps, "cx": [], "found": 0.0}
    raw = detect(frames)
    found = sum(r is not None for r in raw) / len(raw)
    if found < 0.15:  # trop peu de visages : pas de suivi, recadrage centré
        return {"hop": 1 / fps, "cx": [], "found": round(found, 3)}
    cx = smooth_track([r[0] if r else None for r in raw], 1 / fps, cuts)
    return {"hop": 1 / fps, "cx": cx, "found": round(found, 3)}


def keyframes(faces: dict | None, src_in: float, src_out: float, speed: float = 1.0,
              eps: float = 0.012) -> list[tuple[float, float]]:
    """Points (t local du clip, cx) simplifiés, utilisés pour l'expression FFmpeg du recadrage."""
    if not faces or not faces.get("cx"):
        return []
    hop, cx = faces["hop"], faces["cx"]
    i0, i1 = max(0, int(src_in / hop)), min(len(cx) - 1, int(np.ceil(src_out / hop)))
    if i1 < i0:
        return []
    pts = [(max(0.0, (i * hop - src_in) / speed), cx[i]) for i in range(i0, i1 + 1)]
    pts[0] = (0.0, pts[0][1])
    # Simplification de Ramer-Douglas-Peucker : peu de points, même trajectoire.
    def rdp(p):
        if len(p) < 3:
            return p
        (t0, x0), (t1, x1) = p[0], p[-1]
        d = [abs(x - (x0 + (x1 - x0) * (t - t0) / max(1e-6, t1 - t0))) for t, x in p[1:-1]]
        k = int(np.argmax(d))
        if d[k] > eps:
            return rdp(p[: k + 2])[:-1] + rdp(p[k + 1:])
        return [p[0], p[-1]]
    out = rdp(pts)
    if max(x for _, x in out) - min(x for _, x in out) < 0.01:
        return [(0.0, round(float(np.mean([x for _, x in out])), 4))]
    return [(round(t, 3), round(x, 4)) for t, x in out]


def ffmpeg_expr(kf: list[tuple[float, float]]) -> str:
    """Expression d'interpolation linéaire en fonction de t pour les points donnés."""
    if not kf:
        return "0.5"
    if len(kf) == 1:
        return f"{kf[0][1]:.4f}"
    expr = f"{kf[-1][1]:.4f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(kf, kf[1:]))):
        slope = (x1 - x0) / max(1e-6, t1 - t0)
        expr = f"if(lt(t,{t1:.3f}),{x0:.4f}+{slope:.5f}*(t-{t0:.3f}),{expr})"
    return expr
