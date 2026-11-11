"""Moteur 3D logiciel (NumPy + OpenCV) : caméra en perspective, plans texturés, cubes,
textes extrudés et particules.

Les unités du monde sont des pixels : un plan de W x H posé à la distance focale `f`
remplit exactement l'image. L'axe Y monte, l'axe Z part vers l'avant de la caméra.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Courbes d'animation
# ---------------------------------------------------------------------------


def clamp01(u: float) -> float:
    return 0.0 if u < 0 else 1.0 if u > 1 else u


def ease_out_cubic(u: float) -> float:
    u = clamp01(u)
    return 1 - (1 - u) ** 3


def ease_in_out(u: float) -> float:
    u = clamp01(u)
    return 3 * u * u - 2 * u * u * u


def ease_out_back(u: float, s: float = 1.70158) -> float:
    u = clamp01(u) - 1
    return 1 + (s + 1) * u ** 3 + s * u ** 2


def decay(u: float, k: float = 5.0) -> float:
    """1 au moment de l'impact, puis décroissance exponentielle."""
    return math.exp(-k * u) if u >= 0 else 0.0


# ---------------------------------------------------------------------------
# Géométrie
# ---------------------------------------------------------------------------


def rotation(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> np.ndarray:
    """Matrice de rotation (degrés) : tangage X, lacet Y, roulis Z (appliqués Z, puis X, puis Y)."""
    ax, ay, az = (math.radians(a) for a in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Ry @ Rx @ Rz


@dataclass
class Camera:
    W: int
    H: int
    fov: float = 50.0  # champ vertical, degrés

    @property
    def f(self) -> float:
        return (self.H / 2) / math.tan(math.radians(self.fov) / 2)

    def project(self, P: np.ndarray) -> np.ndarray:
        """Points N x 3 (repère caméra) vers pixels N x 2."""
        z = np.maximum(P[:, 2], 1e-3)
        x = self.W / 2 + self.f * P[:, 0] / z
        y = self.H / 2 - self.f * P[:, 1] / z
        return np.stack([x, y], axis=1)


def quad(center, w: float, h: float, R: np.ndarray) -> np.ndarray:
    """Coins (haut-gauche, haut-droit, bas-droit, bas-gauche) d'un rectangle orienté."""
    local = np.array([[-w / 2, h / 2, 0], [w / 2, h / 2, 0], [w / 2, -h / 2, 0], [-w / 2, -h / 2, 0]], dtype=np.float64)
    return local @ R.T + np.asarray(center, dtype=np.float64)


def signed_area(pts: np.ndarray) -> float:
    x, y = pts[:, 0], pts[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def card(cam: Camera, R: np.ndarray, scale: float = 1.0, offset=(0.0, 0.0, 0.0), pivot=(0.0, 0.0)) -> np.ndarray:
    """Plan de la taille de l'image (pleine vue à scale=1), tourné autour de son pivot
    (en fraction de la largeur/hauteur depuis le centre : (-0.5, 0) = bord gauche, comme une porte)."""
    W, H, f = cam.W, cam.H, cam.f
    w, h = W * scale, H * scale
    px, py = pivot[0] * w, pivot[1] * h
    local = np.array([[-w / 2, h / 2, 0], [w / 2, h / 2, 0], [w / 2, -h / 2, 0], [-w / 2, -h / 2, 0]], dtype=np.float64)
    local -= [px, py, 0]
    world = local @ R.T + [px, py, 0] + np.array([offset[0], offset[1], f + offset[2]])
    return cam.project(world)


# ---------------------------------------------------------------------------
# Rastérisation
# ---------------------------------------------------------------------------


def _roi(pts: np.ndarray, W: int, H: int, pad: int = 2):
    x0 = max(0, int(math.floor(pts[:, 0].min())) - pad)
    y0 = max(0, int(math.floor(pts[:, 1].min())) - pad)
    x1 = min(W, int(math.ceil(pts[:, 0].max())) + pad)
    y1 = min(H, int(math.ceil(pts[:, 1].max())) + pad)
    return x0, y0, x1, y1


def draw_image(canvas: np.ndarray, img: np.ndarray, dst: np.ndarray, shade: float = 1.0,
               alpha: np.ndarray | None = None, opacity: float = 1.0, additive: bool = False,
               hard: bool = False) -> None:
    """Plaque `img` (BGR) sur le quadrilatère `dst` du canevas, en perspective, avec un masque
    optionnel `alpha`. Tout est fait par OpenCV dans le rectangle englobant (rapide en 1080p/4K)."""
    H, W = canvas.shape[:2]
    x0, y0, x1, y1 = _roi(dst, W, H)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(src, np.float32(dst - [x0, y0]))
    size = (x1 - x0, y1 - y0)
    if abs(shade - 1.0) > 1e-3:
        img = cv2.convertScaleAbs(img, alpha=shade)
    roi = canvas[y0:y1, x0:x1]
    if alpha is None and opacity >= 0.999 and not additive:
        tmp = roi.copy()
        cv2.warpPerspective(img, M, size, dst=tmp, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
        roi[:] = tmp
        return
    warped = cv2.warpPerspective(img, M, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    a = cv2.warpPerspective(np.full((h, w), 255, np.uint8) if alpha is None else alpha, M, size,
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    if hard:
        tmp = roi.copy()
        cv2.copyTo(warped, (a > 110).astype(np.uint8), tmp)
        roi[:] = tmp
        return
    if additive:
        a3 = cv2.merge([a, a, a])
        roi[:] = cv2.add(roi, cv2.multiply(warped, a3, scale=opacity / 255.0))
        return
    wgt = a.astype(np.float32) * (opacity / 255.0)
    roi[:] = cv2.blendLinear(warped, np.ascontiguousarray(roi), wgt, 1.0 - wgt)


def draw_shadow(canvas: np.ndarray, dst: np.ndarray, strength: float = 0.55, blur: int = 31,
                offset=(0, 18)) -> None:
    """Ombre portée douce sous un plan (calculée en basse résolution)."""
    H, W = canvas.shape[:2]
    k = 4
    small = np.zeros((H // k + 1, W // k + 1), np.uint8)
    pts = ((dst + offset) / k).astype(np.int32)
    cv2.fillConvexPoly(small, pts, 255, lineType=cv2.LINE_AA)
    b = max(3, blur // k | 1)
    small = cv2.GaussianBlur(small, (b, b), 0)
    m = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)[..., None].astype(np.float32) / 255.0
    canvas[:] = (canvas.astype(np.float32) * (1 - strength * m)).astype(np.uint8)


def backdrop(frame: np.ndarray, darken: float = 0.38) -> np.ndarray:
    """Fond flou et assombri tiré de l'image (derrière les plans en 3D)."""
    H, W = frame.shape[:2]
    small = cv2.resize(frame, (max(8, W // 12), max(8, H // 12)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 2.2)
    big = cv2.resize(small, (W, H), interpolation=cv2.INTER_LINEAR)
    return cv2.convertScaleAbs(big, alpha=darken)


def light(R: np.ndarray, base: float = 0.55, amount: float = 0.5) -> float:
    """Éclairage diffus d'une face selon son orientation (lumière en haut à gauche, devant)."""
    n = R @ np.array([0.0, 0.0, -1.0])
    L = np.array([-0.35, 0.4, -1.0])
    L /= np.linalg.norm(L)
    return float(base + amount * max(0.0, float(n @ L)))


def faces_camera(dst: np.ndarray) -> bool:
    """Vrai si la face est vue de devant (coins dans le sens horaire à l'écran)."""
    return signed_area(dst) > 0


# ---------------------------------------------------------------------------
# Texte extrudé
# ---------------------------------------------------------------------------

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
    "/usr/share/fonts/opentype/inter/Inter-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/impact.ttf",
]


@lru_cache(maxsize=16)
def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)


@dataclass
class TextSprite:
    face: np.ndarray  # BGR de la face avant (dégradé)
    side: np.ndarray  # BGR des flancs
    alpha: np.ndarray  # masque des lettres
    glow: np.ndarray  # halo (masque flouté)
    w: int
    h: int


@lru_cache(maxsize=64)
def text_sprite(text: str, size: int, top=(255, 245, 120), bottom=(255, 140, 40), side=(170, 70, 20)) -> TextSprite:
    """Lettres en relief : face avant en dégradé, contour sombre, halo. Couleurs en RGB."""
    from PIL import Image, ImageDraw
    font = _font(size)
    pad = size // 3
    l, t, r, b = font.getbbox(text)
    w, h = r - l + 2 * pad, b - t + 2 * pad
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text((pad - l, pad - t), text, font=font, fill=255,
                            stroke_width=max(2, size // 18), stroke_fill=255)
    alpha = np.asarray(im, dtype=np.uint8).copy()
    inner = Image.new("L", (w, h), 0)
    ImageDraw.Draw(inner).text((pad - l, pad - t), text, font=font, fill=255)
    inner_a = np.asarray(inner, dtype=np.float32)[..., None] / 255.0
    ys = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    top_bgr = np.array(top[::-1], np.float32)
    bot_bgr = np.array(bottom[::-1], np.float32)
    grad = top_bgr * (1 - ys) + bot_bgr * ys
    grad = np.broadcast_to(grad, (h, w, 3))
    outline = np.array([25, 10, 5], np.float32)
    face = (grad * inner_a + outline * (1 - inner_a)).astype(np.uint8)
    side_img = np.empty((h, w, 3), np.uint8)
    side_img[:] = side[::-1]
    glow = cv2.GaussianBlur(alpha, (0, 0), max(2, size / 10))
    return TextSprite(face=face, side=side_img, alpha=alpha, glow=glow, w=w, h=h)


def draw_text3d(canvas: np.ndarray, cam: Camera, sprite: TextSprite, center, R: np.ndarray,
                scale: float = 1.0, depth: float = 0.22, opacity: float = 1.0, glow_color=(40, 160, 255)) -> None:
    """Texte extrudé : tranches empilées de l'arrière vers l'avant (algorithme du peintre)."""
    w, h = sprite.w * scale, sprite.h * scale
    d = depth * h
    n = max(6, min(12, int(d / 3)))
    back = np.array([0.0, 0.0, 1.0]) @ R.T  # direction de l'épaisseur
    glow_img = np.empty((sprite.h, sprite.w, 3), np.uint8)
    glow_img[:] = glow_color[::-1]
    front = cam.project(quad(center, w, h, R))
    draw_image(canvas, glow_img, front, alpha=sprite.glow, opacity=0.8 * opacity, additive=True)
    sh = light(R, 0.35, 0.4)
    for k in range(n, 0, -1):
        c = np.asarray(center) + back * d * k / n
        dst = cam.project(quad(c, w, h, R))
        draw_image(canvas, sprite.side, dst, shade=sh * (0.55 + 0.6 * (1 - k / n)), alpha=sprite.alpha,
                   opacity=opacity, hard=opacity > 0.95)
    draw_image(canvas, sprite.face, front, shade=light(R, 0.8, 0.25), alpha=sprite.alpha, opacity=opacity)


# ---------------------------------------------------------------------------
# Particules
# ---------------------------------------------------------------------------


def burst(seed: int, n: int = 46, speed: float = 900.0) -> dict:
    """Gerbe d'étincelles : positions et vitesses 3D tirées une fois pour toutes (rendu déterministe)."""
    rng = np.random.default_rng(seed)
    v = rng.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
    v *= rng.uniform(0.35, 1.0, size=(n, 1)) * speed
    v[:, 2] *= 0.6
    life = rng.uniform(0.25, 0.55, size=n)
    hue = rng.uniform(0, 1, size=n)
    return {"v": v, "life": life, "hue": hue}


def draw_particles(canvas: np.ndarray, cam: Camera, p: dict, origin_px, t: float,
                   palette=((255, 255, 210), (255, 200, 60), (255, 110, 20))) -> None:
    """Étincelles projetées en perspective, avec traînée et lumière additive. Palette en RGB."""
    if t < 0:
        return
    H, W = canvas.shape[:2]
    f = cam.f
    ox = (origin_px[0] - W / 2) * 1.0
    oy = (H / 2 - origin_px[1]) * 1.0
    g = np.array([0.0, -1400.0, 0.0])
    layer = np.zeros_like(canvas)
    xs: list[float] = []
    ys: list[float] = []
    for v, life, hue in zip(p["v"], p["life"], p["hue"]):
        if t > life:
            continue
        a = 1 - t / life
        pos = np.array([ox, oy, f]) + v * t + 0.5 * g * t * t
        prev = np.array([ox, oy, f]) + v * max(0.0, t - 0.035) + 0.5 * g * max(0.0, t - 0.035) ** 2
        pts = cam.project(np.stack([prev, pos]))
        rgb = palette[min(len(palette) - 1, int(hue * len(palette)))]
        col = tuple(int(c * a) for c in rgb[::-1])
        th = max(1, int(round(3 * a * H / 720)))
        cv2.line(layer, tuple(np.int32(pts[0])), tuple(np.int32(pts[1])), col, th, cv2.LINE_AA)
        xs += [pts[0][0], pts[1][0]]
        ys += [pts[0][1], pts[1][1]]
    if not xs:
        return
    _glow_add(canvas, layer, min(xs), min(ys), max(xs), max(ys), max(1.5, H / 300))


def _glow_add(canvas, layer, x0, y0, x1, y1, sigma):
    """Ajoute un calque lumineux (et son halo) au canevas, limité à la zone utile."""
    H, W = canvas.shape[:2]
    pad = int(4 * sigma) + 8
    x0, y0 = max(0, int(x0) - pad), max(0, int(y0) - pad)
    x1, y1 = min(W, int(x1) + pad), min(H, int(y1) + pad)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    lay = layer[y0:y1, x0:x1]
    roi = canvas[y0:y1, x0:x1]
    roi[:] = cv2.add(cv2.add(roi, cv2.GaussianBlur(lay, (0, 0), sigma)), lay)


def draw_shockwave(canvas: np.ndarray, cam: Camera, origin_px, t: float, life: float = 0.4,
                   color=(255, 240, 200)) -> None:
    """Onde de choc : anneau posé sur un plan incliné qui s'agrandit et s'efface."""
    if not 0 <= t <= life:
        return
    H, W = canvas.shape[:2]
    u = t / life
    r = (0.08 + 0.55 * ease_out_cubic(u)) * H
    ang = np.linspace(0, 2 * np.pi, 72, endpoint=False)
    R = rotation(62, 0, 0)
    ring = np.stack([np.cos(ang) * r, np.sin(ang) * r, np.zeros_like(ang)], axis=1) @ R.T
    ox = origin_px[0] - W / 2
    oy = H / 2 - origin_px[1]
    pts = cam.project(ring + [ox, oy, cam.f]).astype(np.int32)
    layer = np.zeros_like(canvas)
    a = 1 - u
    cv2.polylines(layer, [pts], True, tuple(int(c * a) for c in color[::-1]),
                  max(1, int(6 * a * H / 720)), cv2.LINE_AA)
    _glow_add(canvas, layer, pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max(), max(1.5, H / 260))
